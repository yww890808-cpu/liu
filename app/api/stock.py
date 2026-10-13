"""库存接口：按商品展示各仓库存、批量修改、手动推送 / 读取。"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import current_user, get_shop
from app.db.models import Product, ProductStock, Shop, Warehouse
from app.db.session import get_db
from app.services import stock as stock_service
from app.tasks.stock import push_stock_task, refresh_stock_task

router = APIRouter(prefix="/api/shops/{shop_id}/stocks", tags=["库存"],
                   dependencies=[Depends(current_user)])


class StockCell(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    warehouse_id: int
    local_stock: int | None
    synced_stock: int | None
    ozon_stock: int | None
    ozon_reserved: int | None
    ozon_checked_at: datetime | None
    pushed_at: datetime | None
    push_error: str | None
    pending: bool  # 是否等待推送


class StockProduct(BaseModel):
    product_id: int
    offer_id: str
    sku: int | None
    name: str | None
    stocks: list[StockCell]


class WarehouseBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    is_active: bool


class StockPage(BaseModel):
    warehouses: list[WarehouseBrief]
    total: int
    items: list[StockProduct]


class StockChange(BaseModel):
    product_id: int
    warehouse_id: int
    local_stock: int = Field(ge=0)


def _cell(row: ProductStock) -> StockCell:
    pending = row.local_stock is not None and row.local_stock != row.synced_stock
    return StockCell(**{k: getattr(row, k) for k in StockCell.model_fields if k != "pending"},
                     pending=pending)


@router.get("", response_model=StockPage)
def list_stocks(shop: Shop = Depends(get_shop), db: Session = Depends(get_db),
                q: str | None = Query(default=None, description="按货号或名称搜索"),
                only_pending: bool = Query(default=False, description="只看待推送"),
                page: int = Query(default=1, ge=1), size: int = Query(default=50, ge=1, le=500)):
    stmt = select(Product).where(Product.shop_id == shop.id, Product.archived.is_(False))
    if q:
        stmt = stmt.where(or_(Product.offer_id.ilike(f"%{q}%"), Product.name.ilike(f"%{q}%")))
    if only_pending:
        pending_ids = {r.product_id for r in stock_service.pending_rows(db, shop)}
        stmt = stmt.where(Product.id.in_(pending_ids))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    products = db.scalars(stmt.options(selectinload(Product.stocks)).order_by(Product.offer_id)
                          .offset((page - 1) * size).limit(size)).all()
    warehouses = db.scalars(select(Warehouse).where(Warehouse.shop_id == shop.id).order_by(Warehouse.id)).all()
    return StockPage(
        warehouses=warehouses, total=total,
        items=[StockProduct(product_id=p.id, offer_id=p.offer_id, sku=p.sku, name=p.name,
                            stocks=[_cell(s) for s in sorted(p.stocks, key=lambda s: s.warehouse_id)])
               for p in products])


@router.put("")
def update_stocks(changes: list[StockChange], shop: Shop = Depends(get_shop), db: Session = Depends(get_db)) -> dict:
    """批量修改本地库存（下一次推送周期生效，或调用 push 立即推送）。"""
    try:
        n = stock_service.set_local_stock(db, shop, [c.model_dump() for c in changes])
    except stock_service.StockError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return {"updated": n}


@router.post("/push", status_code=status.HTTP_202_ACCEPTED)
def push_now(shop: Shop = Depends(get_shop)) -> dict:
    """立即推送待同步库存（后台执行，结果见 task_logs）。"""
    return {"task_id": push_stock_task.delay(shop.id).id}


@router.post("/refresh", status_code=status.HTTP_202_ACCEPTED)
def refresh_now(shop: Shop = Depends(get_shop)) -> dict:
    """从 Ozon 读取当前库存（只读）。"""
    return {"task_id": refresh_stock_task.delay(shop.id).id}
