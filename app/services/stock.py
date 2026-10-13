"""库存同步：本地按“商品 × 仓库”维护可售库存，只推送与上次推送值不一致的记录。

规则：
- local_stock 为空（从未设置）的记录永不推送，避免把 Ozon 库存清零；
- 首次从 Ozon 读取库存时，用 Ozon 当前可售数（总数 - 预留）初始化 local_stock 与 synced_stock；
- 推送的是可售数量，Ozon 会在此基础上自行扣减新订单的预留。
"""
import json
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db.models import Product, ProductStock, Shop, TaskLog, Warehouse, utcnow
from app.ozon import endpoints
from app.ozon.client import OzonClient

READ_BATCH = 100   # 按 SKU 查询库存每批数量
PUSH_BATCH = 100   # /v2/products/stocks 每次最多 100 条


class StockError(ValueError):
    pass


@dataclass
class RefreshResult:
    read: int = 0
    initialized: int = 0

    def __str__(self) -> str:
        return f"读取 {self.read} 条，首次初始化 {self.initialized} 条"


@dataclass
class PushResult:
    dry_run: bool = False
    sent: int = 0
    updated: int = 0
    failed: int = 0

    def __str__(self) -> str:
        if self.dry_run:
            return f"试运行：待推送 {self.sent} 条，未提交"
        return f"推送 {self.sent} 条，成功 {self.updated}，失败 {self.failed}"


def _stock_row(db: Session, product_id: int, warehouse_id: int) -> ProductStock:
    row = db.scalar(select(ProductStock).where(ProductStock.product_id == product_id,
                                               ProductStock.warehouse_id == warehouse_id))
    if row is None:
        row = ProductStock(product_id=product_id, warehouse_id=warehouse_id)
        db.add(row)
    return row


def refresh_from_ozon(db: Session, shop: Shop, client: OzonClient) -> RefreshResult:
    """读取 Ozon 各仓库存，更新展示值；从未设置过本地库存的记录用 Ozon 当前值初始化。"""
    products = {p.sku: p for p in db.scalars(
        select(Product).where(Product.shop_id == shop.id, Product.sku.is_not(None)))}
    warehouses = {w.ozon_warehouse_id: w for w in db.scalars(
        select(Warehouse).where(Warehouse.shop_id == shop.id))}
    result = RefreshResult()
    now = utcnow()
    skus = list(products)
    for start in range(0, len(skus), READ_BATCH):
        batch = skus[start:start + READ_BATCH]
        data = client.post(endpoints.FBS_STOCKS_BY_WAREHOUSE, {"sku": [str(s) for s in batch]})
        for item in data.get("result") or []:
            product = products.get(item.get("sku"))
            warehouse = warehouses.get(item.get("warehouse_id"))
            if product is None or warehouse is None:
                continue  # 未同步的商品或仓库，先跑一次商品同步
            reserved = item.get("reserved") or 0
            available = max((item.get("present") or 0) - reserved, 0)
            row = _stock_row(db, product.id, warehouse.id)
            row.ozon_stock, row.ozon_reserved, row.ozon_checked_at = available, reserved, now
            if row.local_stock is None:
                row.local_stock = row.synced_stock = available
                result.initialized += 1
            result.read += 1
        db.commit()
    return result


def set_local_stock(db: Session, shop: Shop, changes: list[dict]) -> int:
    """批量修改本地库存。changes: [{product_id, warehouse_id, local_stock}]（本地 ID）。"""
    for ch in changes:
        if ch["local_stock"] < 0:
            raise StockError("库存不能为负数")
        product = db.get(Product, ch["product_id"])
        warehouse = db.get(Warehouse, ch["warehouse_id"])
        if product is None or product.shop_id != shop.id or warehouse is None or warehouse.shop_id != shop.id:
            raise StockError(f"商品 {ch['product_id']} 或仓库 {ch['warehouse_id']} 不属于该店铺")
        _stock_row(db, product.id, warehouse.id).local_stock = ch["local_stock"]
    db.commit()
    return len(changes)


def pending_rows(db: Session, shop: Shop) -> list[ProductStock]:
    """需要推送的记录：本地已设置、与上次推送值不同、仓库启用、商品未归档且已有 Ozon ID。"""
    return list(db.scalars(
        select(ProductStock)
        .join(Product, ProductStock.product_id == Product.id)
        .join(Warehouse, ProductStock.warehouse_id == Warehouse.id)
        .where(Product.shop_id == shop.id,
               Product.archived.is_(False),
               Product.ozon_product_id.is_not(None),
               Warehouse.is_active.is_(True),
               ProductStock.local_stock.is_not(None),
               or_(ProductStock.synced_stock.is_(None),
                   ProductStock.synced_stock != ProductStock.local_stock))
        .options(joinedload(ProductStock.product), joinedload(ProductStock.warehouse))
        .order_by(ProductStock.id)
    ).unique())


def push_stocks(db: Session, shop: Shop, client: OzonClient) -> PushResult:
    rows = pending_rows(db, shop)
    result = PushResult(dry_run=get_settings().dry_run, sent=len(rows))
    for start in range(0, len(rows), PUSH_BATCH):
        batch = rows[start:start + PUSH_BATCH]
        # 记下本次发送的值：推送期间本地若又被修改，下一轮会再次推送
        sent = {(r.product.ozon_product_id, r.warehouse.ozon_warehouse_id): (r, r.local_stock) for r in batch}
        payload = {"stocks": [{"offer_id": r.product.offer_id, "product_id": r.product.ozon_product_id,
                               "stock": value, "warehouse_id": r.warehouse.ozon_warehouse_id}
                              for r, value in sent.values()]}
        if result.dry_run:
            db.add(TaskLog(task_name="stock_push_dry_run", shop_id=shop.id, status="success",
                           message=json.dumps(payload, ensure_ascii=False), finished_at=utcnow()))
            db.commit()
            continue

        data = client.post(endpoints.STOCKS_UPDATE, payload)
        now = utcnow()
        for item in data.get("result") or []:
            entry = sent.get((item.get("product_id"), item.get("warehouse_id")))
            if entry is None:
                continue
            row, value = entry
            if item.get("updated"):
                row.synced_stock, row.pushed_at, row.push_error = value, now, None
                result.updated += 1
            else:
                errors = item.get("errors") or []
                row.push_error = "; ".join(f"{e.get('code')}: {e.get('message')}" for e in errors) or "未更新"
                result.failed += 1
        db.commit()
    return result
