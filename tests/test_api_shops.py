"""阶段 3：登录与店铺接口。"""
from fastapi.testclient import TestClient

from app.db.models import Product, Shop, TaskLog, Warehouse
from app.main import app
from app.ozon import endpoints
from app.utils.crypto import decrypt
from tests.fake_ozon import FakeOzon

API_KEY = "ozon-api-key-very-secret"


def add_shop(api, client_id="111"):
    r = api.post("/api/shops", json={"name": "店铺A", "client_id": client_id, "api_key": API_KEY})
    assert r.status_code == 201, r.text
    return r.json()


def test_requires_login(api):
    anon = TestClient(app)
    assert anon.get("/api/shops").status_code == 401
    assert anon.post("/api/auth/login", data={"username": "admin", "password": "wrong"}).status_code == 401
    assert api.get("/api/auth/me").json()["username"] == "admin"


def test_invalid_token_rejected(api):
    anon = TestClient(app, headers={"Authorization": "Bearer not-a-token"})
    assert anon.get("/api/shops").status_code == 401


def test_create_shop_encrypts_and_masks(api, db):
    out = add_shop(api)
    assert out["api_key_masked"] == "ozon****"
    assert API_KEY not in str(out)

    shop = db.get(Shop, out["id"])
    assert shop.api_key_encrypted != API_KEY
    assert decrypt(shop.api_key_encrypted) == API_KEY
    assert API_KEY not in api.get("/api/shops").text


def test_duplicate_client_id_conflict(api):
    add_shop(api)
    r = api.post("/api/shops", json={"name": "B", "client_id": "111", "api_key": "k"})
    assert r.status_code == 409


def test_update_and_delete_shop(api, db):
    sid = add_shop(api)["id"]
    r = api.patch(f"/api/shops/{sid}", json={"name": "新名字", "is_active": False, "api_key": "new-key-123"})
    assert r.json()["name"] == "新名字" and r.json()["is_active"] is False
    db.expire_all()
    assert decrypt(db.get(Shop, sid).api_key_encrypted) == "new-key-123"

    db.add(Product(shop_id=sid, offer_id="X"))
    db.commit()
    assert api.delete(f"/api/shops/{sid}").status_code == 204
    assert api.get(f"/api/shops/{sid}/products").status_code == 404
    assert db.query(Product).count() == 0  # 级联删除


def test_connection_check(api, monkeypatch):
    sid = add_shop(api)["id"]
    fake = FakeOzon({endpoints.WAREHOUSE_LIST: lambda b: {"result": [{"warehouse_id": 1, "name": "W"}]}})
    monkeypatch.setattr("app.api.shops.shop_service.ozon_client_for", lambda shop: fake.client())
    assert api.post(f"/api/shops/{sid}/test").json() == {"ok": True, "warehouses": 1}

    bad = FakeOzon({})  # 所有路径 404
    monkeypatch.setattr("app.api.shops.shop_service.ozon_client_for", lambda shop: bad.client())
    r = api.post(f"/api/shops/{sid}/test").json()
    assert r["ok"] is False and "404" in r["error"]


def test_sync_task_end_to_end(api, db, monkeypatch):
    """同步接口 → Celery 任务（eager 模式）→ 写入仓库、商品与 task_logs。"""
    from app.tasks.celery_app import celery_app

    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    sid = add_shop(api)["id"]
    fake = FakeOzon({
        endpoints.WAREHOUSE_LIST: lambda b: {"result": [{"warehouse_id": 77, "name": "FBS-1"}]},
        endpoints.PRODUCT_LIST: lambda b: {"result": {"items": [{"product_id": 5, "offer_id": "SKU-5"}],
                                                      "last_id": ""}},
        endpoints.PRODUCT_INFO_LIST: lambda b: {"items": [{"id": 5, "offer_id": "SKU-5",
                                                           "sku": 999, "name": "杯子"}]},
        endpoints.FBS_STOCKS_BY_WAREHOUSE: lambda b: {"result": [
            {"sku": 999, "warehouse_id": 77, "present": 12, "reserved": 2}]},
    })
    monkeypatch.setattr("app.tasks.catalog.ozon_client_for", lambda shop: fake.client())

    assert api.post(f"/api/shops/{sid}/sync").status_code == 202
    assert [w["name"] for w in api.get(f"/api/shops/{sid}/warehouses").json()] == ["FBS-1"]
    page = api.get(f"/api/shops/{sid}/products", params={"q": "杯"}).json()
    assert page["total"] == 1 and page["items"][0]["sku"] == 999

    log = db.query(TaskLog).one()
    assert log.task_name == "sync_catalog" and log.status == "success"
    assert "新增 1" in log.message and "首次初始化 1" in log.message


def test_sync_failure_logged(api, db, monkeypatch):
    from app.tasks.celery_app import celery_app

    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_eager_propagates", False)
    sid = add_shop(api)["id"]
    monkeypatch.setattr("app.tasks.catalog.ozon_client_for", lambda shop: FakeOzon({}).client())

    api.post(f"/api/shops/{sid}/sync")
    log = db.query(TaskLog).one()
    assert log.status == "failed" and "OzonAPIError" in log.message


def test_toggle_warehouse(api, db):
    sid = add_shop(api)["id"]
    other = add_shop(api, client_id="222")["id"]
    wh = Warehouse(shop_id=sid, ozon_warehouse_id=1, name="W")
    db.add(wh)
    db.commit()
    r = api.patch(f"/api/shops/{sid}/warehouses/{wh.id}", json={"is_active": False})
    assert r.json()["is_active"] is False
    # 不能跨店铺修改
    assert api.patch(f"/api/shops/{other}/warehouses/{wh.id}", json={"is_active": True}).status_code == 404


def test_login_throttled_after_repeated_failures(api):
    anon = TestClient(app)
    for _ in range(5):
        assert anon.post("/api/auth/login", data={"username": "admin", "password": "bad"}).status_code == 401
    r = anon.post("/api/auth/login", data={"username": "admin", "password": "password123"})
    assert r.status_code == 429 and "15 分钟" in r.json()["detail"]


def test_login_throttle_window_expires():
    from app.services.auth import LoginThrottle

    now = [0.0]
    t = LoginThrottle(max_failures=2, window_sec=60, clock=lambda: now[0])
    t.fail("u"); t.fail("u")
    assert t.blocked("u")
    now[0] = 61
    assert not t.blocked("u")
    t.fail("u"); t.success("u")
    assert not t.blocked("u")


# ---------- 免登录模式 ----------

def test_no_login_mode_allows_access_without_token(db, monkeypatch):
    from app.config import get_settings
    from app.db.session import get_db

    monkeypatch.setenv("LOGIN_REQUIRED", "false")
    get_settings.cache_clear()

    def _get_db():
        s = db.factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db
    try:
        anon = TestClient(app)
        assert anon.get("/health").json()["login_required"] is False
        assert anon.get("/api/shops").status_code == 200
        r = anon.post("/api/shops", json={"name": "A", "client_id": "1", "api_key": "k-123456"})
        assert r.status_code == 201
        assert anon.get("/api/auth/me").json()["username"] == "本机"
        # 带了无效凭证也不影响
        assert TestClient(app, headers={"Authorization": "Bearer junk"}).get("/api/shops").status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_login_mode_reported_in_health(api):
    assert TestClient(app).get("/health").json()["login_required"] is True


def test_login_required_by_default_is_off(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("LOGIN_REQUIRED", raising=False)
    assert Settings(_env_file=None).login_required is False
