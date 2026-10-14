"""订单拉取：按 posting_number 去重，重复拉取时更新而不是新增。"""
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.db.models import Order, OrderItem, Shop, utcnow
from app.ozon import endpoints
from app.ozon.client import OzonClient

POSTING_LIST_LIMIT = 50  # /v3/posting/fbs/list 单页上限


@dataclass
class OrderSyncResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0

    def __str__(self) -> str:
        return f"新增 {self.created}，更新 {self.updated}，无变化 {self.unchanged}"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(value: str | None) -> datetime | None:
    """解析 Ozon 时间；空值和 0001-01-01 这类占位值视为空。"""
    if not value or value.startswith("0001-01-01"):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_price(value) -> Decimal | None:
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except InvalidOperation:
        return None


def iter_postings(client: OzonClient, *, changed_since: datetime, now: datetime,
                  created_since: datetime) -> Iterator[dict]:
    """遍历指定时间窗口内状态有变化的 FBS 货件（offset 分页）。"""
    offset = 0
    while True:
        data = client.post(endpoints.FBS_POSTING_LIST, {
            "dir": "ASC",
            "filter": {
                "since": _iso(created_since),
                "to": _iso(now),
                "last_changed_status_date": {"from": _iso(changed_since), "to": _iso(now)},
            },
            "limit": POSTING_LIST_LIMIT,
            "offset": offset,
        })
        result = data.get("result") or {}
        postings = result.get("postings") or []
        yield from postings
        if not result.get("has_next") or not postings:
            return
        offset += len(postings)


def upsert_posting(db: Session, shop: Shop, posting: dict) -> str:
    """写入一个货件，返回 created / updated / unchanged。调用方负责提交。"""
    number = posting["posting_number"]
    order = db.scalar(select(Order).where(Order.posting_number == number)
                      .options(selectinload(Order.items)))
    if order is not None and order.raw_json == posting:
        return "unchanged"

    outcome = "updated"
    if order is None:
        order = Order(shop_id=shop.id, posting_number=number)
        db.add(order)
        outcome = "created"

    delivery = posting.get("delivery_method") or {}
    order.parent_posting_number = posting.get("parent_posting_number") or None
    order.status = posting.get("status") or "unknown"
    order.substatus = posting.get("substatus") or None
    order.ozon_warehouse_id = delivery.get("warehouse_id") or None
    order.warehouse_name = delivery.get("warehouse") or None
    order.tracking_number = posting.get("tracking_number") or None
    order.ozon_created_at = _parse_dt(posting.get("in_process_at"))
    order.shipment_date = _parse_dt(posting.get("shipment_date"))
    order.raw_json = posting
    # 商品明细整体替换，保证与 Ozon 一致
    order.items = [
        OrderItem(sku=p.get("sku"), offer_id=p.get("offer_id"), name=p.get("name"),
                  quantity=p.get("quantity") or 0, price=_parse_price(p.get("price")))
        for p in posting.get("products") or []
    ]
    return outcome


def sync_orders(db: Session, shop: Shop, client: OzonClient, *, now: datetime | None = None) -> OrderSyncResult:
    settings = get_settings()
    now = now or utcnow()
    result = OrderSyncResult()
    for i, posting in enumerate(iter_postings(
        client,
        changed_since=now - timedelta(days=settings.order_sync_lookback_days),
        created_since=now - timedelta(days=settings.order_sync_created_window_days),
        now=now,
    ), start=1):
        outcome = upsert_posting(db, shop, posting)
        setattr(result, outcome, getattr(result, outcome) + 1)
        if i % POSTING_LIST_LIMIT == 0:
            db.commit()
    db.commit()
    return result


def refresh_order(db: Session, shop: Shop, client: OzonClient, posting_number: str) -> Order:
    """从 Ozon 重新获取单个货件详情并更新本地。"""
    data = client.post(endpoints.FBS_POSTING_GET, {"posting_number": posting_number})
    upsert_posting(db, shop, data["result"])
    db.commit()
    return db.scalar(select(Order).where(Order.posting_number == posting_number))
