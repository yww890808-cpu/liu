"""订单接口：列表筛选、详情、手动拉取。"""
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import current_user, get_shop
from app.db.models import Order, OrderItem, Shop
from app.db.session import get_db
from app.ozon.errors import OzonAPIError
from app.services import orders as order_service
from app.services.shops import ozon_client_for
from app.tasks.orders import sync_shop_orders_task

router = APIRouter(tags=["订单"], dependencies=[Depends(current_user)])


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    sku: int | None
    offer_id: str | None
    name: str | None
    quantity: int
    price: Decimal | None


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
    rows = db.scalars(stmt.options(selectinload(Order.items))
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
