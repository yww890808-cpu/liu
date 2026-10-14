"""阶段 5：备货（含拆包）、试运行、面单。"""
import base64

import httpx
import pytest

from app.config import get_settings
from app.db.models import Order, Shipment, Shop, TaskLog
from app.ozon import endpoints
from app.ozon.errors import OzonAPIError
from app.services import orders as order_service
from app.services import shipping
from app.services.shops import create_shop
from tests.fake_ozon import FakeOzon
from tests.test_orders import posting

PDF = b"%PDF-1.4 fake label"


@pytest.fixture
def live(monkeypatch, tmp_path):
    """关闭试运行，面单写到临时目录。"""
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("LABEL_DIR", str(tmp_path))
    get_settings.cache_clear()


@pytest.fixture
def shop(db):
    return create_shop(db, "店铺A", "111", "key-12345678")


def two_item_posting(number="700-1-1", **kw):
    return posting(number, products=[
        {"sku": 11, "offer_id": "A", "name": "a", "quantity": 2, "price": "1"},
        {"sku": 22, "offer_id": "B", "name": "b", "quantity": 1, "price": "1"},
    ], **kw)


def make_order(db, shop, p):
    order_service.upsert_posting(db, shop, p)
    db.commit()
    return db.query(Order).filter_by(posting_number=p["posting_number"]).one()


class FakeOzonState:
    """模拟备货后 Ozon 的状态变化：货件变为 awaiting_deliver，拆包时生成子货件号。"""

    def __init__(self, base):
        self.postings = {base["posting_number"]: base}
        self.label_ready = True

    def ship(self, body):
        number = body["posting_number"]
        base = self.postings[number]
        result = []
        for i, pkg in enumerate(body["packages"]):
            child = number if i == 0 else f"{number.rsplit('-', 1)[0]}-{i + 1}"
            self.postings[child] = {**base, "posting_number": child, "status": "awaiting_deliver",
                                    "tracking_number": f"OZ-{child}",
                                    "parent_posting_number": number if i else "",
                                    "products": [{"sku": l["product_id"], "quantity": l["quantity"],
                                                  "offer_id": "", "name": "", "price": "1"}
                                                 for l in pkg["products"]]}
            result.append(child)
        return {"result": result}

    def get(self, body):
        return {"result": self.postings[body["posting_number"]]}

    def label(self, body):
        if not self.label_ready:
            return httpx.Response(400, json={"code": 3, "message": "The next postings are not ready: 700-1-1"})
        return httpx.Response(200, content=PDF, headers={"Content-Type": "application/pdf"})

    def fake(self):
        return FakeOzon({endpoints.FBS_SHIP: self.ship, endpoints.FBS_POSTING_GET: self.get,
                         endpoints.FBS_PACKAGE_LABEL: self.label})


# ---------- 校验 ----------

def test_default_single_package(db, shop):
    order = make_order(db, shop, two_item_posting())
    payload = shipping.build_ship_payload(order, shipping.default_packages(order))
    assert payload["packages"] == [{"products": [{"product_id": 11, "quantity": 2},
                                                 {"product_id": 22, "quantity": 1}]}]


@pytest.mark.parametrize("packages, msg", [
    ([[{"sku": 11, "quantity": 2}]], "不一致"),                                   # 少了商品
    ([[{"sku": 11, "quantity": 3}, {"sku": 22, "quantity": 1}]], "不一致"),        # 数量多了
    ([[{"sku": 11, "quantity": 2}, {"sku": 22, "quantity": 1}], []], "不能为空"),
    ([[{"sku": 11, "quantity": 0}, {"sku": 11, "quantity": 2}, {"sku": 22, "quantity": 1}]], "大于 0"),
])
def test_invalid_packages_rejected(db, shop, packages, msg):
    order = make_order(db, shop, two_item_posting())
    with pytest.raises(shipping.ShippingError, match=msg):
        shipping.build_ship_payload(order, packages)


def test_wrong_status_rejected(db, shop):
    order = make_order(db, shop, two_item_posting(status="awaiting_deliver"))
    with pytest.raises(shipping.ShippingError, match="待备货"):
        shipping.build_ship_payload(order, shipping.default_packages(order))


def test_special_requirements_rejected(db, shop):
    order = make_order(db, shop, two_item_posting(requirements={"products_requiring_gtd": [11]}))
    with pytest.raises(shipping.ShippingError, match="手动备货"):
        shipping.build_ship_payload(order, shipping.default_packages(order))


# ---------- 试运行 / 实际提交 ----------

def test_dry_run_does_not_call_ozon(db, shop):
    assert get_settings().dry_run is True
    order = make_order(db, shop, two_item_posting())
    fake = FakeOzon({})
    r = shipping.ship_order(db, shop, fake.client(), order)
    assert r.dry_run and fake.requests == []
    log = db.query(TaskLog).filter_by(task_name="ship_dry_run").one()
    assert '"posting_number": "700-1-1"' in log.message
    assert db.query(Shipment).count() == 0


def test_ship_single_package(db, shop, live):
    state = FakeOzonState(two_item_posting())
    order = make_order(db, shop, state.postings["700-1-1"])
    r = shipping.ship_order(db, shop, state.fake().client(), order)
    assert not r.dry_run and r.posting_numbers == ["700-1-1"]
    db.expire_all()
    o = db.query(Order).filter_by(posting_number="700-1-1").one()
    assert o.status == "awaiting_deliver" and o.tracking_number == "OZ-700-1-1"
    assert o.shipment.shipped_at is not None and o.shipment.tracking_number == "OZ-700-1-1"


def test_ship_split_packages(db, shop, live):
    state = FakeOzonState(two_item_posting())
    order = make_order(db, shop, state.postings["700-1-1"])
    packages = [[{"sku": 11, "quantity": 1}], [{"sku": 11, "quantity": 1}, {"sku": 22, "quantity": 1}]]
    r = shipping.ship_order(db, shop, state.fake().client(), order, packages)
    assert r.posting_numbers == ["700-1-1", "700-1-2"]
    assert r.payload["packages"][1] == {"products": [{"product_id": 11, "quantity": 1},
                                                      {"product_id": 22, "quantity": 1}]}
    child = db.query(Order).filter_by(posting_number="700-1-2").one()
    assert child.parent_posting_number == "700-1-1" and child.shipment.shipped_at is not None
    assert db.query(Shipment).count() == 2


# ---------- 面单 ----------

def test_fetch_label_saves_pdf(db, shop, live, tmp_path):
    state = FakeOzonState(two_item_posting(status="awaiting_deliver"))
    order = make_order(db, shop, state.postings["700-1-1"])
    path = shipping.fetch_and_store_label(db, state.fake().client(), order)
    assert path == tmp_path / str(shop.id) / "700-1-1.pdf"
    assert path.read_bytes() == PDF and order.shipment.label_path == str(path)


def test_label_json_base64_format(db, shop, live):
    body = {"result": {"file_content": base64.b64encode(PDF).decode(), "file_name": "x.pdf"}}
    fake = FakeOzon({endpoints.FBS_PACKAGE_LABEL: lambda b: body})
    assert shipping.fetch_labels_pdf(fake.client(), ["1"]) == PDF


def test_label_not_ready_recorded(db, shop, live):
    state = FakeOzonState(two_item_posting(status="awaiting_deliver"))
    state.label_ready = False
    order = make_order(db, shop, state.postings["700-1-1"])
    with pytest.raises(OzonAPIError) as ei:
        shipping.fetch_and_store_label(db, state.fake().client(), order)
    assert shipping.is_label_not_ready(ei.value)
    assert "not ready" in order.shipment.label_error


def test_label_batch_limit():
    with pytest.raises(shipping.ShippingError, match="20"):
        shipping.fetch_labels_pdf(FakeOzon({}).client(), [str(i) for i in range(21)])


# ---------- 接口 ----------

@pytest.fixture
def api_state(api, db, monkeypatch, tmp_path):
    from app.tasks.celery_app import celery_app

    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr("app.tasks.shipping.SessionLocal", db.factory)
    monkeypatch.setenv("LABEL_DIR", str(tmp_path))
    get_settings.cache_clear()
    api.post("/api/shops", json={"name": "A", "client_id": "111", "api_key": "k-1234567"})
    shop = db.query(Shop).one()
    state = FakeOzonState(two_item_posting())
    make_order(db, shop, state.postings["700-1-1"])
    for target in ("app.api.orders.ozon_client_for", "app.tasks.shipping.ozon_client_for"):
        monkeypatch.setattr(target, lambda s: state.fake().client())
    return state


def test_api_ship_dry_run(api, db, api_state):
    oid = db.query(Order).one().id
    r = api.post(f"/api/orders/{oid}/ship", json={}).json()
    assert r["ok"] and r["dry_run"] and r["payload"]["posting_number"] == "700-1-1"
    assert db.query(Order).one().status == "awaiting_packaging"


def test_api_ship_live_then_label_fetched(api, db, api_state, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    get_settings.cache_clear()
    oid = db.query(Order).one().id
    r = api.post(f"/api/orders/{oid}/ship", json={
        "packages": [[{"sku": 11, "quantity": 2}], [{"sku": 22, "quantity": 1}]]}).json()
    assert r["posting_numbers"] == ["700-1-1", "700-1-2"]
    # eager 模式下面单任务已执行
    logs = db.query(TaskLog).filter_by(task_name="fetch_label").all()
    assert len(logs) == 2 and all(l.status == "success" for l in logs)

    child = db.query(Order).filter_by(posting_number="700-1-2").one()
    resp = api.get(f"/api/orders/{child.id}/label")
    assert resp.status_code == 200 and resp.content == PDF
    detail = api.get(f"/api/orders/{child.id}").json()
    assert detail["shipment"]["label_path"].endswith("700-1-2.pdf")


def test_api_ship_invalid_returns_400(api, db, api_state):
    oid = db.query(Order).one().id
    r = api.post(f"/api/orders/{oid}/ship", json={"packages": [[{"sku": 11, "quantity": 1}]]})
    assert r.status_code == 400 and "不一致" in r.json()["detail"]


def test_api_label_not_ready_409(api, db, api_state):
    api_state.label_ready = False
    oid = db.query(Order).one().id
    r = api.get(f"/api/orders/{oid}/label")
    assert r.status_code == 409 and "稍后" in r.json()["detail"]


def test_api_batch_ship_and_labels(api, db, api_state):
    shop = db.query(Shop).one()
    make_order(db, shop, two_item_posting("800-1-1", status="delivered"))
    ids = [o.id for o in db.query(Order).order_by(Order.id)]
    res = api.post("/api/orders/ship-batch", json={"order_ids": ids}).json()
    assert [x["ok"] for x in res] == [True, False]  # 已签收的订单不能备货，不影响第一单
    assert "待备货" in res[1]["error"]

    r = api.post("/api/orders/labels", json={"order_ids": ids})
    assert r.status_code == 200 and r.content == PDF


def test_label_task_retries_until_ready(api, db, api_state, monkeypatch):
    from app.tasks.shipping import fetch_label_task

    calls = []
    orig = api_state.label

    def flaky(body):
        calls.append(1)
        api_state.label_ready = len(calls) >= 3  # 第 3 次才就绪
        return orig(body)

    api_state.label = flaky
    oid = db.query(Order).one().id
    fetch_label_task.apply_async((oid,))
    assert len(calls) == 3
    statuses = [l.status for l in db.query(TaskLog).filter_by(task_name="fetch_label").order_by(TaskLog.id)]
    assert statuses == ["failed", "failed", "success"]


def test_label_task_gives_up_after_max_tries(api, db, api_state, monkeypatch):
    from app.tasks.celery_app import celery_app
    from app.tasks.shipping import fetch_label_task

    monkeypatch.setenv("LABEL_FETCH_MAX_TRIES", "2")
    monkeypatch.setattr(celery_app.conf, "task_eager_propagates", False)
    get_settings.cache_clear()
    api_state.label_ready = False
    oid = db.query(Order).one().id
    fetch_label_task.apply_async((oid,))
    assert db.query(TaskLog).filter_by(task_name="fetch_label").count() == 2
