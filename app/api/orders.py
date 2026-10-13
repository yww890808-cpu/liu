"""订单接口：列表筛选、详情、手动拉取。"""
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import current_user, get_shop
from app.db.models import Order, OrderItem, Shop
from app.db.session import get_db
from app.ozon.errors import OzonAPIError
from app.services import orders as order_service
from app.services import shipping
from app.services.shops import ozon_client_for
from app.tasks.orders import sync_shop_orders_task
from app.tasks.shipping import schedule_label_fetch

router = APIRouter(tags=["订单"], dependencies=[Depends(current_user)])


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    sku: int | None
    offer_id: str | None
    name: str | None
    quantity: int
    price: Decimal | None


class ShipmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    shipped_at: datetime | None
    label_path: str | None
    label_error: str | None


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    shop_id: int
    posting_number: str
    parent_posting_number: str | None
    status: str
    substatus: str | None
    ozon_warehouse_id: int | None
    warehouse_name: str | None
    tracking_number: str | None
    ozon_created_at: datetime | None
    shipment_date: datetime | None
    updated_at: datetime
    items: list[OrderItemOut]
    shipment: ShipmentOut | None


class OrderDetail(OrderOut):
    raw_json: dict | None


class OrderPage(BaseModel):
    total: int
    items: list[OrderOut]


def _get_order(order_id: int, db: Session) -> Order:
    order = db.scalar(select(Order).where(Order.id == order_id).options(selectinload(Order.items)))
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")
    return order


@router.get("/api/orders", response_model=OrderPage)
def list_orders(
    db: Session = Depends(get_db),
    shop_id: int | None = None,
    status_: list[str] | None = Query(default=None, alias="status", description="可多选"),
    warehouse_id: int | None = Query(default=None, description="Ozon 仓库 ID"),
    q: str | None = Query(default=None, description="货件号 / 货号 / 商品名"),
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
):
    stmt = select(Order)
    if shop_id is not None:
        stmt = stmt.where(Order.shop_id == shop_id)
    if status_:
        stmt = stmt.where(Order.status.in_(status_))
    if warehouse_id is not None:
        stmt = stmt.where(Order.ozon_warehouse_id == warehouse_id)
    if created_from is not None:
        stmt = stmt.where(Order.ozon_created_at >= created_from)
    if created_to is not None:
        stmt = stmt.where(Order.ozon_created_at <= created_to)
    if q:
        like = f"%{q}%"
        item_match = select(OrderItem.order_id).where(
            or_(OrderItem.offer_id.ilike(like), OrderItem.name.ilike(like)))
        stmt = stmt.where(or_(Order.posting_number.ilike(like), Order.id.in_(item_match)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.options(selectinload(Order.items), selectinload(Order.shipment))
                      .order_by(Order.ozon_created_at.desc(), Order.id.desc())
                      .offset((page - 1) * size).limit(size)).all()
    return OrderPage(total=total, items=rows)


@router.get("/api/orders/statuses")
def status_counts(db: Session = Depends(get_db), shop_id: int | None = None) -> dict[str, int]:
    """各状态订单数，供前端筛选使用。"""
    stmt = select(Order.status, func.count()).group_by(Order.status)
    if shop_id is not None:
        stmt = stmt.where(Order.shop_id == shop_id)
    return dict(db.execute(stmt).all())


@router.get("/api/orders/{order_id}", response_model=OrderDetail)
def get_order(order_id: int, db: Session = Depends(get_db)):
    return _get_order(order_id, db)


@router.post("/api/orders/{order_id}/refresh", response_model=OrderDetail)
def refresh_order(order_id: int, db: Session = Depends(get_db)):
    """从 Ozon 重新获取该订单最新状态。"""
    order = _get_order(order_id, db)
    shop = db.get(Shop, order.shop_id)
    client = ozon_client_for(shop)
    try:
        order_service.refresh_order(db, shop, client, order.posting_number)
    except OzonAPIError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
    finally:
        client.close()
    db.expire_all()
    return _get_order(order_id, db)


@router.post("/api/shops/{shop_id}/sync-orders", status_code=status.HTTP_202_ACCEPTED)
def sync_orders_now(shop: Shop = Depends(get_shop)) -> dict:
    """立即拉取该店铺订单（后台执行，结果见 task_logs）。"""
    return {"task_id": sync_shop_orders_task.delay(shop.id).id}


# ---------------- 发货 ----------------

class PackageLine(BaseModel):
    sku: int
    quantity: int = Field(gt=0)


class ShipIn(BaseModel):
    # 不传则整单一个包裹；拆包时每个元素是一个包裹的商品列表
    packages: list[list[PackageLine]] | None = None


class ShipOut(BaseModel):
    order_id: int
    posting_number: str
    ok: bool
    dry_run: bool = False
    posting_numbers: list[str] = []
    payload: dict | None = None
    error: str | None = None


class OrderIdsIn(BaseModel):
    order_ids: list[int] = Field(min_length=1, max_length=200)


def _ship_one(db: Session, order: Order, packages: list[list[dict]] | None) -> ShipOut:
    shop = db.get(Shop, order.shop_id)
    client = ozon_client_for(shop)
    try:
        r = shipping.ship_order(db, shop, client, order, packages)
    except (shipping.ShippingError, OzonAPIError) as e:
        return ShipOut(order_id=order.id, posting_number=order.posting_number, ok=False, error=str(e))
    finally:
        client.close()
    if not r.dry_run:
        for number in r.posting_numbers:
            shipped = db.scalar(select(Order).where(Order.posting_number == number))
            schedule_label_fetch(shipped.id)
    return ShipOut(order_id=order.id, posting_number=order.posting_number, ok=True,
                   dry_run=r.dry_run, posting_numbers=r.posting_numbers, payload=r.payload)


@router.post("/api/orders/{order_id}/ship", response_model=ShipOut)
def ship_order(order_id: int, body: ShipIn, db: Session = Depends(get_db)):
    """备货确认。试运行模式下只返回将要提交的内容，不调用 Ozon。"""
    order = _get_order(order_id, db)
    packages = [[line.model_dump() for line in pkg] for pkg in body.packages] if body.packages else None
    out = _ship_one(db, order, packages)
    if not out.ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, out.error)
    return out


@router.post("/api/orders/ship-batch", response_model=list[ShipOut])
def ship_batch(body: OrderIdsIn, db: Session = Depends(get_db)):
    """批量备货（每单一个包裹），逐单返回结果，单笔失败不影响其他订单。"""
    try:
        orders = shipping.orders_by_ids(db, body.order_ids)
    except shipping.ShippingError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    return [_ship_one(db, o, None) for o in orders]


@router.get("/api/orders/{order_id}/label")
def download_label(order_id: int, db: Session = Depends(get_db)):
    """下载面单：本地已有则直接返回，否则立即向 Ozon 获取。"""
    order = _get_order(order_id, db)
    path = shipping.label_file(order)
    if not path.exists():
        client = ozon_client_for(db.get(Shop, order.shop_id))
        try:
            path = shipping.fetch_and_store_label(db, client, order)
        except OzonAPIError as e:
            if shipping.is_label_not_ready(e):
                raise HTTPException(status.HTTP_409_CONFLICT, "面单尚未生成，请稍后再试") from e
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
        except ValueError as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
        finally:
            client.close()
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.post("/api/orders/labels")
def batch_labels(body: OrderIdsIn, db: Session = Depends(get_db)):
    """批量打印面单：同一店铺最多 20 个货件，合并为一个 PDF。"""
    try:
        orders = shipping.orders_by_ids(db, body.order_ids)
    except shipping.ShippingError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    if len({o.shop_id for o in orders}) > 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "批量面单只能选择同一店铺的订单")
    client = ozon_client_for(db.get(Shop, orders[0].shop_id))
    try:
        pdf = shipping.fetch_labels_pdf(client, [o.posting_number for o in orders])
    except shipping.ShippingError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    except OzonAPIError as e:
        code = status.HTTP_409_CONFLICT if shipping.is_label_not_ready(e) else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(code, str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
    finally:
        client.close()
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="labels.pdf"'})
