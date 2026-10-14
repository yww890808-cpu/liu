"""店铺、仓库、商品接口。"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_shop
from app.db.models import Product, Shop, Warehouse
from app.db.session import get_db
from app.ozon import endpoints
from app.ozon.errors import OzonAPIError
from app.services import shops as shop_service
from app.tasks.catalog import sync_shop_catalog_task
from app.utils.crypto import decrypt, mask

router = APIRouter(prefix="/api/shops", tags=["店铺"], dependencies=[Depends(current_user)])


class ShopIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    client_id: str = Field(min_length=1, max_length=64)
    api_key: str = Field(min_length=1)


class ShopPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    api_key: str | None = None
    is_active: bool | None = None


class ShopOut(BaseModel):
    id: int
    name: str
    client_id: str
    api_key_masked: str
    is_active: bool
    last_sync_at: datetime | None


class WarehouseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ozon_warehouse_id: int
    name: str
    status: str | None
    is_active: bool


class WarehousePatch(BaseModel):
    is_active: bool


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    offer_id: str
    ozon_product_id: int | None
    sku: int | None
    name: str | None
    archived: bool


class ProductPage(BaseModel):
    total: int
    items: list[ProductOut]


def _out(shop: Shop) -> ShopOut:
    # 只返回脱敏后的 Api-Key
    return ShopOut(id=shop.id, name=shop.name, client_id=shop.client_id,
                   api_key_masked=mask(decrypt(shop.api_key_encrypted)),
                   is_active=shop.is_active, last_sync_at=shop.last_sync_at)


@router.get("", response_model=list[ShopOut])
def list_shops(db: Session = Depends(get_db)):
    return [_out(s) for s in db.scalars(select(Shop).order_by(Shop.id))]


@router.post("", response_model=ShopOut, status_code=status.HTTP_201_CREATED)
def create_shop(body: ShopIn, db: Session = Depends(get_db)):
    try:
        shop = shop_service.create_shop(db, body.name, body.client_id, body.api_key)
    except shop_service.ShopError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return _out(shop)


@router.patch("/{shop_id}", response_model=ShopOut)
def update_shop(body: ShopPatch, shop: Shop = Depends(get_shop), db: Session = Depends(get_db)):
    return _out(shop_service.update_shop(db, shop, **body.model_dump(exclude_unset=True)))


@router.delete("/{shop_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shop(shop: Shop = Depends(get_shop), db: Session = Depends(get_db)):
    shop_service.delete_shop(db, shop)


@router.post("/{shop_id}/test")
def test_connection(shop: Shop = Depends(get_shop)) -> dict:
    """用仓库列表接口（只读）验证 Client-Id / Api-Key 是否可用。"""
    client = shop_service.ozon_client_for(shop)
    try:
        data = client.post(endpoints.WAREHOUSE_LIST, {})
    except OzonAPIError as e:
        return {"ok": False, "error": str(e)}
    finally:
        client.close()
    return {"ok": True, "warehouses": len(data.get("result") or [])}


@router.post("/{shop_id}/sync", status_code=status.HTTP_202_ACCEPTED)
def sync_catalog(shop: Shop = Depends(get_shop)) -> dict:
    """后台同步仓库与商品列表，结果见 task_logs。"""
    task = sync_shop_catalog_task.delay(shop.id)
    return {"task_id": task.id}


@router.get("/{shop_id}/warehouses", response_model=list[WarehouseOut])
def list_warehouses(shop: Shop = Depends(get_shop), db: Session = Depends(get_db)):
    return db.scalars(select(Warehouse).where(Warehouse.shop_id == shop.id).order_by(Warehouse.id)).all()


@router.patch("/{shop_id}/warehouses/{warehouse_id}", response_model=WarehouseOut)
def update_warehouse(warehouse_id: int, body: WarehousePatch, shop: Shop = Depends(get_shop),
                     db: Session = Depends(get_db)):
    wh = db.get(Warehouse, warehouse_id)
    if wh is None or wh.shop_id != shop.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "仓库不存在")
    wh.is_active = body.is_active
    db.commit()
    return wh


@router.get("/{shop_id}/products", response_model=ProductPage)
def list_products(shop: Shop = Depends(get_shop), db: Session = Depends(get_db),
                  q: str | None = Query(default=None, description="按货号或名称搜索"),
                  include_archived: bool = False,
                  page: int = Query(default=1, ge=1), size: int = Query(default=50, ge=1, le=500)):
    stmt = select(Product).where(Product.shop_id == shop.id)
    if not include_archived:
        stmt = stmt.where(Product.archived.is_(False))
    if q:
        stmt = stmt.where(or_(Product.offer_id.ilike(f"%{q}%"), Product.name.ilike(f"%{q}%")))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(stmt.order_by(Product.offer_id).offset((page - 1) * size).limit(size)).all()
    return ProductPage(total=total, items=items)
