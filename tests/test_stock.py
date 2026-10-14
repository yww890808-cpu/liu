"""阶段 6：库存读取、修改、差异推送。"""
import pytest

from app.config import get_settings
from app.db.models import Product, ProductStock, Shop, TaskLog, Warehouse
from app.ozon import endpoints
from app.services import stock
from app.services.shops import create_shop
from tests.fake_ozon import FakeOzon


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    get_settings.cache_clear()


@pytest.fixture
def setup(db):
    """店铺 + 2 个仓库 + 3 个商品（其中 1 个已归档）。"""
    shop = create_shop(db, "店铺A", "111", "key-12345678")
    w1 = Warehouse(shop_id=shop.id, ozon_warehouse_id=1001, name="莫斯科")
    w2 = Warehouse(shop_id=shop.id, ozon_warehouse_id=1002, name="圣彼得堡")
    ps = [Product(shop_id=shop.id, offer_id=f"A{i}", ozon_product_id=100 + i, sku=9000 + i, name=f"商品{i}")
          for i in range(3)]
    ps[2].archived = True
    db.add_all([w1, w2, *ps])
    db.commit()
    return shop, (w1, w2), ps


class FakeStockOzon:
    def __init__(self, ozon_stock: dict):
        self.ozon_stock = ozon_stock  # {(sku, wh): (present, reserved)}
        self.reject: set = set()       # 拒绝更新的 (product_id, wh)

    def read(self, body):
        skus = {int(s) for s in body["sku"]}
        return {"result": [{"sku": sku, "warehouse_id": wh, "present": p, "reserved": r}
                           for (sku, wh), (p, r) in self.ozon_stock.items() if sku in skus]}

    def update(self, body):
        out = []
        for s in body["stocks"]:
            key = (s["product_id"], s["warehouse_id"])
            if key in self.reject:
                out.append({**key_dict(key), "offer_id": s["offer_id"], "updated": False,
                            "errors": [{"code": "TOO_MANY_REQUESTS", "message": "wait 2 min"}]})
            else:
                out.append({**key_dict(key), "offer_id": s["offer_id"], "updated": True, "errors": []})
        return {"result": out}

    def fake(self):
        return FakeOzon({endpoints.FBS_STOCKS_BY_WAREHOUSE: self.read, endpoints.STOCKS_UPDATE: self.update})


def key_dict(key):
    return {"product_id": key[0], "warehouse_id": key[1]}


def rows(db):
    return {(r.product.offer_id, r.warehouse.ozon_warehouse_id): r for r in db.query(ProductStock)}


def test_refresh_initializes_only_unset(db, setup):
    shop, (w1, w2), ps = setup
    ozon = FakeStockOzon({(9000, 1001): (10, 3), (9001, 1002): (5, 0)})
    r = stock.refresh_from_ozon(db, shop, ozon.fake().client())
    assert (r.read, r.initialized) == (2, 2)
    row = rows(db)[("A0", 1001)]
    # 可售 = 总数 - 预留
    assert (row.local_stock, row.synced_stock, row.ozon_stock, row.ozon_reserved) == (7, 7, 7, 3)

    # 用户改过本地库存后再次读取：只更新展示值，不覆盖本地
    stock.set_local_stock(db, shop, [{"product_id": ps[0].id, "warehouse_id": w1.id, "local_stock": 50}])
    ozon.ozon_stock[(9000, 1001)] = (8, 3)
    r = stock.refresh_from_ozon(db, shop, ozon.fake().client())
    assert r.initialized == 0
    db.refresh(row)
    assert (row.local_stock, row.ozon_stock) == (50, 5)


def test_nothing_pushed_without_local_values(db, setup, live):
    shop, *_ = setup
    ozon = FakeStockOzon({})
    r = stock.push_stocks(db, shop, ozon.fake().client())
    assert r.sent == 0 and ozon.fake().requests == []


def test_push_only_changed(db, setup, live):
    shop, (w1, w2), ps = setup
    ozon = FakeStockOzon({(9000, 1001): (10, 0), (9001, 1001): (4, 0)})
    stock.refresh_from_ozon(db, shop, ozon.fake().client())  # 初始化后无差异

    fake = ozon.fake()
    assert stock.push_stocks(db, shop, fake.client()).sent == 0

    stock.set_local_stock(db, shop, [
        {"product_id": ps[0].id, "warehouse_id": w1.id, "local_stock": 20},  # 修改
        {"product_id": ps[1].id, "warehouse_id": w2.id, "local_stock": 3},   # 新仓库
        {"product_id": ps[2].id, "warehouse_id": w1.id, "local_stock": 9},   # 已归档，不推
    ])
    r = stock.push_stocks(db, shop, fake.client())
    assert (r.sent, r.updated, r.failed) == (2, 2, 0)
    body = [b for p, b in fake.requests if p == endpoints.STOCKS_UPDATE][0]
    assert sorted(body["stocks"], key=lambda s: s["offer_id"]) == [
        {"offer_id": "A0", "product_id": 100, "stock": 20, "warehouse_id": 1001},
        {"offer_id": "A1", "product_id": 101, "stock": 3, "warehouse_id": 1002},
    ]
    assert rows(db)[("A0", 1001)].synced_stock == 20

    # 再推一次：没有差异
    assert stock.push_stocks(db, shop, fake.client()).sent == 0


def test_inactive_warehouse_skipped(db, setup, live):
    shop, (w1, w2), ps = setup
    w2.is_active = False
    db.commit()
    stock.set_local_stock(db, shop, [{"product_id": ps[0].id, "warehouse_id": w2.id, "local_stock": 5}])
    assert stock.push_stocks(db, shop, FakeStockOzon({}).fake().client()).sent == 0


def test_partial_failure_recorded_and_retried(db, setup, live):
    shop, (w1, w2), ps = setup
    ozon = FakeStockOzon({})
    ozon.reject.add((101, 1001))
    stock.set_local_stock(db, shop, [
        {"product_id": ps[0].id, "warehouse_id": w1.id, "local_stock": 1},
        {"product_id": ps[1].id, "warehouse_id": w1.id, "local_stock": 2},
    ])
    r = stock.push_stocks(db, shop, ozon.fake().client())
    assert (r.updated, r.failed) == (1, 1)
    bad = rows(db)[("A1", 1001)]
    assert bad.synced_stock is None and "TOO_MANY_REQUESTS" in bad.push_error

    ozon.reject.clear()
    r = stock.push_stocks(db, shop, ozon.fake().client())  # 下一轮只重推失败的那条
    assert (r.sent, r.updated) == (1, 1)
    db.refresh(bad)
    assert bad.synced_stock == 2 and bad.push_error is None


def test_push_batches_of_100(db, setup, live, monkeypatch):
    monkeypatch.setattr(stock, "PUSH_BATCH", 1)
    shop, (w1, w2), ps = setup
    stock.set_local_stock(db, shop, [{"product_id": p.id, "warehouse_id": w1.id, "local_stock": 1} for p in ps[:2]])
    fake = FakeStockOzon({}).fake()
    stock.push_stocks(db, shop, fake.client())
    assert len(fake.requests) == 2


def test_dry_run_logs_payload_only(db, setup):
    shop, (w1, w2), ps = setup
    stock.set_local_stock(db, shop, [{"product_id": ps[0].id, "warehouse_id": w1.id, "local_stock": 7}])
    fake = FakeStockOzon({}).fake()
    r = stock.push_stocks(db, shop, fake.client())
    assert r.dry_run and r.sent == 1 and fake.requests == []
    assert rows(db)[("A0", 1001)].synced_stock is None  # 未真正推送，仍待推送
    assert '"stock": 7' in db.query(TaskLog).filter_by(task_name="stock_push_dry_run").one().message


def test_validation(db, setup):
    shop, (w1, w2), ps = setup
    other = create_shop(db, "店铺B", "222", "k")
    with pytest.raises(stock.StockError, match="负数"):
        stock.set_local_stock(db, shop, [{"product_id": ps[0].id, "warehouse_id": w1.id, "local_stock": -1}])
    with pytest.raises(stock.StockError, match="不属于"):
        stock.set_local_stock(db, other, [{"product_id": ps[0].id, "warehouse_id": w1.id, "local_stock": 1}])


# ---------- 接口 ----------

def test_api_edit_list_and_push(api, db, monkeypatch):
    from app.tasks.celery_app import celery_app

    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setenv("DRY_RUN", "false")
    get_settings.cache_clear()
    sid = api.post("/api/shops", json={"name": "A", "client_id": "111", "api_key": "k-1234567"}).json()["id"]
    wh = Warehouse(shop_id=sid, ozon_warehouse_id=1001, name="莫斯科")
    p = Product(shop_id=sid, offer_id="CUP", ozon_product_id=5, sku=55, name="杯子")
    db.add_all([wh, p])
    db.commit()
    ozon = FakeStockOzon({(55, 1001): (10, 1)})
    monkeypatch.setattr("app.tasks.stock.ozon_client_for", lambda s: ozon.fake().client())

    api.post(f"/api/shops/{sid}/stocks/refresh")
    cell = api.get(f"/api/shops/{sid}/stocks").json()["items"][0]["stocks"][0]
    assert (cell["local_stock"], cell["ozon_stock"], cell["pending"]) == (9, 9, False)

    r = api.put(f"/api/shops/{sid}/stocks", json=[{"product_id": p.id, "warehouse_id": wh.id, "local_stock": 30}])
    assert r.json() == {"updated": 1}
    page = api.get(f"/api/shops/{sid}/stocks", params={"only_pending": True}).json()
    assert page["total"] == 1 and page["items"][0]["stocks"][0]["pending"] is True
    assert page["warehouses"][0]["name"] == "莫斯科"

    api.post(f"/api/shops/{sid}/stocks/push")
    assert api.get(f"/api/shops/{sid}/stocks", params={"only_pending": True}).json()["total"] == 0
    log = db.query(TaskLog).filter_by(task_name="push_stock").one()
    assert log.status == "success" and "成功 1" in log.message

    bad = api.put(f"/api/shops/{sid}/stocks", json=[{"product_id": p.id, "warehouse_id": wh.id, "local_stock": -5}])
    assert bad.status_code == 422
