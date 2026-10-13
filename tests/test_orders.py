"""阶段 4：订单拉取、去重、筛选。"""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.db.models import Order, OrderItem, TaskLog
from app.ozon import endpoints
from app.services import orders
from app.services.shops import create_shop
from tests.fake_ozon import FakeOzon

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


def posting(number, status="awaiting_packaging", **kw):
    p = {
        "posting_number": number,
        "order_number": number.rsplit("-", 1)[0],
        "status": status,
        "substatus": "posting_created",
        "in_process_at": "2026-10-12T08:30:00Z",
        "shipment_date": "2026-10-14T10:00:00Z",
        "tracking_number": "",
        "parent_posting_number": "",
        "delivery_method": {"id": 1, "name": "Ozon Логистика", "warehouse_id": 1001, "warehouse": "FBS-Moscow"},
        "products": [{"sku": 3000000001, "offer_id": "CUP-1", "name": "杯子", "quantity": 2, "price": "199.5000"}],
    }
    p.update(kw)
    return p


def list_route(all_postings):
    """模拟 /v3/posting/fbs/list 的 offset 分页。"""
    def handler(body):
        off, lim = body.get("offset", 0), body["limit"]
        page = all_postings[off:off + lim]
        return {"result": {"postings": page, "has_next": off + lim < len(all_postings)}}
    return handler


@pytest.fixture
def shop(db):
    return create_shop(db, "店铺A", "111", "key-12345678")


def test_sync_creates_orders_with_items(db, shop):
    fake = FakeOzon({endpoints.FBS_POSTING_LIST: list_route([posting("100-1-1")])})
    r = orders.sync_orders(db, shop, fake.client(), now=NOW)
    assert (r.created, r.updated, r.unchanged) == (1, 0, 0)

    o = db.query(Order).one()
    assert o.status == "awaiting_packaging" and o.substatus == "posting_created"
    assert (o.ozon_warehouse_id, o.warehouse_name) == (1001, "FBS-Moscow")
    assert o.ozon_created_at.replace(tzinfo=timezone.utc) == datetime(2026, 10, 12, 8, 30, tzinfo=timezone.utc)
    assert o.tracking_number is None and o.parent_posting_number is None
    assert o.raw_json["order_number"] == "100-1"
    item = o.items[0]
    assert (item.sku, item.offer_id, item.quantity, item.price) == (3000000001, "CUP-1", 2, Decimal("199.5"))


def test_request_filter_uses_status_change_window(db, shop, monkeypatch):
    monkeypatch.setenv("ORDER_SYNC_LOOKBACK_DAYS", "3")
    monkeypatch.setenv("ORDER_SYNC_CREATED_WINDOW_DAYS", "90")
    from app.config import get_settings
    get_settings.cache_clear()

    fake = FakeOzon({endpoints.FBS_POSTING_LIST: list_route([])})
    orders.sync_orders(db, shop, fake.client(), now=NOW)
    body = fake.requests[0][1]
    assert body["filter"]["last_changed_status_date"] == {"from": "2026-10-10T12:00:00Z", "to": "2026-10-13T12:00:00Z"}
    assert body["filter"]["since"] == "2026-07-15T12:00:00Z"
    assert body["limit"] == 50 and body["offset"] == 0


def test_dedup_by_posting_number(db, shop):
    data = [posting("100-1-1")]
    fake = FakeOzon({endpoints.FBS_POSTING_LIST: list_route(data)})
    orders.sync_orders(db, shop, fake.client(), now=NOW)

    # 原样再拉一次：无变化
    r = orders.sync_orders(db, shop, fake.client(), now=NOW)
    assert (r.created, r.updated, r.unchanged) == (0, 0, 1)

    # 状态变化 + 商品数量变化：更新而不是新增，明细整体替换
    data[0] = posting("100-1-1", status="awaiting_deliver", tracking_number="OZ123",
                      products=[{"sku": 3000000001, "offer_id": "CUP-1", "name": "杯子", "quantity": 1, "price": "199.5"}])
    r = orders.sync_orders(db, shop, fake.client(), now=NOW)
    assert (r.created, r.updated) == (0, 1)
    assert db.query(Order).count() == 1
    o = db.query(Order).one()
    assert o.status == "awaiting_deliver" and o.tracking_number == "OZ123"
    assert db.query(OrderItem).count() == 1 and o.items[0].quantity == 1


def test_pagination(db, shop, monkeypatch):
    monkeypatch.setattr(orders, "POSTING_LIST_LIMIT", 2)
    data = [posting(f"200-{i}-1") for i in range(5)]
    fake = FakeOzon({endpoints.FBS_POSTING_LIST: list_route(data)})
    r = orders.sync_orders(db, shop, fake.client(), now=NOW)
    assert r.created == 5
    assert [b["offset"] for _, b in fake.requests] == [0, 2, 4]


def test_split_package_postings(db, shop):
    """拆包后 Ozon 生成新货件号，记录原货件号。"""
    data = [posting("300-1-1", status="awaiting_deliver"),
            posting("300-1-2", status="awaiting_deliver", parent_posting_number="300-1-1")]
    fake = FakeOzon({endpoints.FBS_POSTING_LIST: list_route(data)})
    orders.sync_orders(db, shop, fake.client(), now=NOW)
    child = db.query(Order).filter_by(posting_number="300-1-2").one()
    assert child.parent_posting_number == "300-1-1"


def test_zero_dates_treated_as_empty(db, shop):
    fake = FakeOzon({endpoints.FBS_POSTING_LIST: list_route(
        [posting("400-1-1", shipment_date="0001-01-01T00:00:00Z")])})
    orders.sync_orders(db, shop, fake.client(), now=NOW)
    assert db.query(Order).one().shipment_date is None


def test_refresh_single_order(db, shop):
    fake = FakeOzon({endpoints.FBS_POSTING_GET: lambda b: {"result": posting(b["posting_number"], status="delivering")}})
    o = orders.refresh_order(db, shop, fake.client(), "500-1-1")
    assert o.status == "delivering"
    assert fake.requests == [(endpoints.FBS_POSTING_GET, {"posting_number": "500-1-1"})]


# ---------- 接口 ----------

def _seed(api, db, monkeypatch):
    from app.tasks.celery_app import celery_app

    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    sid = api.post("/api/shops", json={"name": "A", "client_id": "111", "api_key": "k-1234567"}).json()["id"]
    data = [
        posting("600-1-1", in_process_at="2026-10-10T00:00:00Z"),
        posting("600-2-1", status="awaiting_deliver", in_process_at="2026-10-11T00:00:00Z",
                delivery_method={"warehouse_id": 1002, "warehouse": "FBS-SPB"},
                products=[{"sku": 1, "offer_id": "BAG-9", "name": "背包", "quantity": 1, "price": "10"}]),
        posting("600-3-1", status="delivered", in_process_at="2026-10-12T00:00:00Z"),
    ]
    fake = FakeOzon({endpoints.FBS_POSTING_LIST: list_route(data),
                     endpoints.FBS_POSTING_GET: lambda b: {"result": posting(b["posting_number"], status="cancelled")}})
    monkeypatch.setattr("app.tasks.orders.ozon_client_for", lambda shop: fake.client())
    monkeypatch.setattr("app.api.orders.ozon_client_for", lambda shop: fake.client())
    assert api.post(f"/api/shops/{sid}/sync-orders").status_code == 202
    return sid


def test_api_sync_and_filters(api, db, monkeypatch):
    sid = _seed(api, db, monkeypatch)
    log = db.query(TaskLog).filter_by(task_name="sync_orders").one()
    assert log.status == "success" and "新增 3" in log.message

    def nums(**params):
        r = api.get("/api/orders", params=params).json()
        return [o["posting_number"] for o in r["items"]]

    assert nums() == ["600-3-1", "600-2-1", "600-1-1"]  # 按创建时间倒序
    assert nums(status=["awaiting_packaging", "awaiting_deliver"]) == ["600-2-1", "600-1-1"]
    assert nums(warehouse_id=1002) == ["600-2-1"]
    assert nums(q="背包") == ["600-2-1"]
    assert nums(q="600-1") == ["600-1-1"]
    assert nums(created_from="2026-10-11T00:00:00Z") == ["600-3-1", "600-2-1"]
    assert nums(shop_id=sid + 1) == []
    assert api.get("/api/orders/statuses").json() == {
        "awaiting_packaging": 1, "awaiting_deliver": 1, "delivered": 1}


def test_api_detail_and_refresh(api, db, monkeypatch):
    _seed(api, db, monkeypatch)
    oid = db.query(Order).filter_by(posting_number="600-1-1").one().id
    d = api.get(f"/api/orders/{oid}").json()
    assert d["items"][0]["offer_id"] == "CUP-1" and d["raw_json"]["posting_number"] == "600-1-1"
    assert api.post(f"/api/orders/{oid}/refresh").json()["status"] == "cancelled"
    assert api.get("/api/orders/99999").status_code == 404
