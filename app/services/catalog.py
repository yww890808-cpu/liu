"""商品与仓库同步：从 Ozon 拉取，按 (shop_id, offer_id) / (shop_id, warehouse_id) 更新或新增。"""
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Product, Shop, Warehouse, utcnow
from app.ozon import endpoints
from app.ozon.client import OzonClient

PRODUCT_LIST_PAGE = 1000  # /v3/product/list 单页上限
PRODUCT_INFO_BATCH = 100  # /v3/product/info/list 每批查询的商品数


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0

    def __str__(self) -> str:
        return f"新增 {self.created}，更新 {self.updated}"


def sync_warehouses(db: Session, shop: Shop, client: OzonClient) -> SyncResult:
    data = client.post(endpoints.WAREHOUSE_LIST, {})
    existing = {w.ozon_warehouse_id: w for w in
                db.scalars(select(Warehouse).where(Warehouse.shop_id == shop.id))}
    result = SyncResult()
    for item in data.get("result") or []:
        wid = item["warehouse_id"]
        wh = existing.get(wid)
        if wh is None:
            wh = Warehouse(shop_id=shop.id, ozon_warehouse_id=wid)
            db.add(wh)
            result.created += 1
        else:
            result.updated += 1
        wh.name = item.get("name") or str(wid)
        wh.status = item.get("status")
    db.commit()
    return result


def _iter_product_ids(client: OzonClient) -> Iterator[list[dict]]:
    """分页遍历 /v3/product/list（含已归档商品），每次产出一页。"""
    last_id = ""
    while True:
        data = client.post(endpoints.PRODUCT_LIST, {
            "filter": {"visibility": "ALL"}, "last_id": last_id, "limit": PRODUCT_LIST_PAGE,
        })
        result = data.get("result") or {}
        items = result.get("items") or []
        if items:
            yield items
        last_id = result.get("last_id") or ""
        if not items or not last_id or len(items) < PRODUCT_LIST_PAGE:
            return


def sync_products(db: Session, shop: Shop, client: OzonClient) -> SyncResult:
    existing = {p.offer_id: p for p in
                db.scalars(select(Product).where(Product.shop_id == shop.id))}
    result = SyncResult()
    for page in _iter_product_ids(client):
        archived = {i["product_id"]: bool(i.get("archived")) for i in page}
        ids = list(archived)
        for start in range(0, len(ids), PRODUCT_INFO_BATCH):
            batch = ids[start:start + PRODUCT_INFO_BATCH]
            info = client.post(endpoints.PRODUCT_INFO_LIST, {"product_id": batch})
            for item in info.get("items") or []:
                offer_id = item["offer_id"]
                product = existing.get(offer_id)
                if product is None:
                    product = existing[offer_id] = Product(shop_id=shop.id, offer_id=offer_id)
                    db.add(product)
                    result.created += 1
                else:
                    result.updated += 1
                product.ozon_product_id = item["id"]
                product.sku = item.get("sku") or None
                product.name = item.get("name")
                product.archived = archived.get(item["id"], False)
        db.commit()  # 每页提交一次，中途失败时已处理的页不丢
    shop.last_sync_at = utcnow()
    db.commit()
    return result
