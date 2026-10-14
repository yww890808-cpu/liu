"""阶段 9：选品数据抓取与分析（MPStats 全部 mock）。"""
import json
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest

from app.config import get_settings
from app.db.models import MarketCategory, MarketCategoryDaily, MarketProduct, MarketProductDaily, ApiLog
from app.market import mpstats
from app.market.mpstats import MPStatsClient, MPStatsError, MPStatsNotConfigured
from app.ozon.ratelimit import RateLimiter
from app.services import market

TODAY = date(2026, 10, 14)


class FakeMPStats:
    """按 (类目, 日期) 返回商品；可设置某天“未出数”或返回 202。"""

    def __init__(self):
        self.items: dict[tuple[str, str], list[dict]] = {}
        self.by_date: list[dict] = []
        self.calls: list[tuple[str, str, dict, dict]] = []
        self.pending_once: set[str] = set()

    def handler(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/api/")
        params = {k: v[0] for k, v in parse_qs(req.url.query.decode()).items()}
        body = json.loads(req.content or b"{}")
        self.calls.append((req.method, path, params, body))
        assert req.headers["X-Mpstats-TOKEN"] == "tok"
        if path in self.pending_once:
            self.pending_once.discard(path)
            return httpx.Response(202, json={})
        if path == mpstats.CATEGORY_ITEMS:
            assert params["d1"] == params["d2"]
            rows = self.items.get((params["path"], params["d1"]), [])
            page = rows[body["startRow"]:body["endRow"]]
            return httpx.Response(200, json={"total": len(rows), "data": page})
        if path == mpstats.CATEGORY_BY_DATE:
            return httpx.Response(200, json=self.by_date)
        if path == mpstats.CATEGORY_LIST:
            return httpx.Response(200, json=[{"path": "Дом и сад/Посуда/Кружки", "name": "Кружки", "url": "/c/1"},
                                             {"path": "Электроника/Наушники", "name": "Наушники", "url": "/c/2"}])
        if path == mpstats.API_LIMIT:
            return httpx.Response(200, json={"available": 2500, "use": 38})
        return httpx.Response(404, json={"message": "no route"})

    def client(self, db=None) -> MPStatsClient:
        http = httpx.Client(base_url="https://mpstats.io/api/", transport=httpx.MockTransport(self.handler))
        return MPStatsClient("tok", http=http, limiter=RateLimiter(1000), sleep=lambda s: None,
                             db_factory=db.factory if db is not None else None)


def item(sku, sales, price=500, revenue=None, comments=10, **kw):
    return {"id": sku, "name": f"商品{sku}", "brand": "B", "seller": "S", "final_price": price,
            "sales": sales, "revenue": revenue if revenue is not None else sales * price,
            "balance": 100, "rating": 4.8, "comments": comments, "thumb": f"https://img/{sku}.jpg", **kw}


@pytest.fixture
def backfill(monkeypatch):
    monkeypatch.setenv("MARKET_BACKFILL_DAYS", "3")
    get_settings.cache_clear()


@pytest.fixture
def cat(db):
    return market.add_category(db, "Дом и сад/Посуда/Кружки", "马克杯", top_n=100)


def test_requires_token(monkeypatch):
    monkeypatch.setenv("MPSTATS_TOKEN", "")
    get_settings.cache_clear()
    with pytest.raises(MPStatsNotConfigured):
        MPStatsClient()


def test_add_category_validation(db, cat):
    with pytest.raises(market.MarketError, match="已在关注"):
        market.add_category(db, "/Дом и сад/Посуда/Кружки/")
    assert market.add_category(db, "Электроника/Наушники").name == "Наушники"


def test_backfill_then_incremental(db, cat, backfill):
    fake = FakeMPStats()
    for d in ("2026-10-11", "2026-10-12", "2026-10-13"):
        fake.items[(cat.path, d)] = [item(1001, 5), item(1002, 2)]
    r = market.fetch_category(db, fake.client(), cat, TODAY)
    assert (r.days, r.products, r.pending) == (3, 6, False)
    assert cat.last_fetched_date == date(2026, 10, 13)
    assert db.query(MarketProduct).count() == 2 and db.query(MarketProductDaily).count() == 6

    # 请求参数：按销售额倒序、包含 FBS、每天单独请求
    m, path, params, body = [c for c in fake.calls if c[1] == mpstats.CATEGORY_ITEMS][0]
    assert m == "POST" and params["fbs"] == "1" and params["d1"] == "2026-10-11"
    assert body["sortModel"] == [{"colId": "revenue", "sort": "desc"}] and body["endRow"] == 100

    # 第二天只抓新的一天
    fake.calls.clear()
    fake.items[(cat.path, "2026-10-14")] = [item(1001, 7), item(1003, 1)]
    r = market.fetch_category(db, fake.client(), cat, TODAY + timedelta(days=1))
    assert r.days == 1
    assert [c[2]["d1"] for c in fake.calls if c[1] == mpstats.CATEGORY_ITEMS] == ["2026-10-14"]
    p = db.query(MarketProduct).filter_by(ozon_sku=1001).one()
    assert (p.first_seen, p.last_seen, p.name, p.thumb) == (date(2026, 10, 11), date(2026, 10, 14), "商品1001", "https://img/1001.jpg")


def test_empty_day_is_pending_not_skipped(db, cat, backfill):
    fake = FakeMPStats()
    fake.items[(cat.path, "2026-10-11")] = [item(1, 1)]
    # 10-12 未出数
    fake.items[(cat.path, "2026-10-13")] = [item(1, 1)]
    r = market.fetch_category(db, fake.client(), cat, TODAY)
    assert (r.days, r.pending) == (1, True)
    assert cat.last_fetched_date == date(2026, 10, 11)  # 下次从 10-12 重试


def test_202_retried(db, cat, backfill):
    fake = FakeMPStats()
    fake.pending_once.add(mpstats.CATEGORY_ITEMS)
    for d in ("2026-10-11", "2026-10-12", "2026-10-13"):
        fake.items[(cat.path, d)] = [item(1, 1)]
    r = market.fetch_category(db, fake.client(db), cat, TODAY)
    assert r.days == 3
    logs = db.query(ApiLog).filter(ApiLog.endpoint.like("mpstats:%")).all()
    assert logs[0].status_code == 202 and "数据生成中" in logs[0].error


def test_pagination_over_5000(monkeypatch):
    monkeypatch.setattr(mpstats, "MAX_ROWS", 2)
    fake = FakeMPStats()
    fake.items[("P", "2026-10-13")] = [item(i, 1) for i in range(5)]
    d = date(2026, 10, 13)
    rows = fake.client().category_items("P", d, d, limit=4)
    assert [r["id"] for r in rows] == [0, 1, 2, 3]
    assert [(c[3]["startRow"], c[3]["endRow"]) for c in fake.calls] == [(0, 2), (2, 4)]


def test_category_daily_totals_saved(db, cat, backfill):
    fake = FakeMPStats()
    for d in ("2026-10-11", "2026-10-12", "2026-10-13"):
        fake.items[(cat.path, d)] = [item(1, 1)]
    fake.by_date = [{"period": "2026-10-12", "sales": 900, "revenue": 450000, "items": 3000,
                     "items_with_sells": 800, "sellers": 120, "avg_price": 610.5}]
    market.fetch_category(db, fake.client(), cat, TODAY)
    row = db.query(MarketCategoryDaily).one()
    assert (row.day, row.sales, row.sellers, row.avg_price) == (date(2026, 10, 12), 900, 120, Decimal("610.50"))


def test_category_daily_failure_does_not_lose_products(db, cat, backfill):
    fake = FakeMPStats()
    for d in ("2026-10-11", "2026-10-12", "2026-10-13"):
        fake.items[(cat.path, d)] = [item(1, 1)]
    fake.by_date = None  # 让 by_date 返回非法结构
    orig = fake.handler
    fake.handler = lambda req: (httpx.Response(400, json={"message": "bad"})
                                if req.url.path.endswith("by_date") else orig(req))
    r = market.fetch_category(db, fake.client(), cat, TODAY)
    assert r.days == 3 and db.query(MarketProductDaily).count() == 3


# ---------- 分析 ----------

def seed_history(db, cat):
    """A：稳定热卖；B：近 7 天爆发；C：下滑；D：新品（前 7 天无数据）。"""
    as_of = date(2026, 10, 13)
    for i in range(30):
        d = as_of - timedelta(days=i)
        recent = i < 7
        rows = [item(1, 10, price=300, comments=100 + (29 - i)),
                item(2, 20 if recent else 2, price=800, comments=5),
                item(3, 1 if recent else 6, price=150, comments=50)]
        if i < 5:
            rows.append(item(4, 3, price=1200, comments=1))
        market.save_day(db, cat, d, rows)
    db.commit()


def test_ranking_metrics(db, cat):
    seed_history(db, cat)
    r = market.ranking(db)
    assert r["as_of"] == date(2026, 10, 13) and r["total"] == 4
    by_sku = {i["ozon_sku"]: i for i in r["items"]}
    a, b, c, d = by_sku[1], by_sku[2], by_sku[3], by_sku[4]
    assert (a["sales_7d"], a["sales_prev_7d"], a["sales_30d"]) == (70, 70, 300)
    assert a["growth_pct"] == 0.0 and a["reviews_growth"] == 29 and a["days_with_sales"] == 30
    assert b["sales_7d"] == 140 and b["sales_prev_7d"] == 14 and b["growth_pct"] == 900.0
    assert c["growth_pct"] == pytest.approx(-83.3, abs=0.1)
    assert d["growth_pct"] is None and d["sales_30d"] == 15  # 新品没有对比基数
    # 默认按 30 天销售额倒序：B 186×800 > A 300×300 > C 145×150 > D 15×1200
    assert [i["ozon_sku"] for i in r["items"]] == [2, 1, 3, 4]


def test_ranking_sort_and_filters(db, cat):
    seed_history(db, cat)
    skus = lambda **kw: [i["ozon_sku"] for i in market.ranking(db, **kw)["items"]]
    assert skus(sort="growth_pct") == [2, 1, 3, 4]          # 无对比基数的排最后
    assert skus(sort="price", desc=False) == [3, 1, 2, 4]
    assert skus(price_min=200, price_max=900) == [2, 1]
    assert skus(min_growth=100) == [2]
    assert skus(min_sales_30d=150) == [2, 1]  # A 300、B 186、C 145、D 15
    assert skus(q="商品4") == [4]
    market_product = db.query(MarketProduct).filter_by(ozon_sku=3).one()
    market_product.is_favorite = True
    db.commit()
    assert skus(favorites=True) == [3]
    page = market.ranking(db, size=2, page=2)
    assert page["total"] == 4 and len(page["items"]) == 2


def test_ranking_empty(db):
    assert market.ranking(db) == {"as_of": None, "total": 0, "items": []}


def test_category_history_tracked_totals(db, cat):
    seed_history(db, cat)
    hist = market.category_history(db, cat.id, days=60)
    last = hist[-1]
    assert last["day"] == date(2026, 10, 13)
    assert last["tracked_sales"] == 10 + 20 + 1 + 3 and last["tracked_products"] == 4
    assert last["sales"] is None  # 没有数据服务的全类目汇总时为空


# ---------- 接口 ----------

@pytest.fixture
def market_api(api, db, monkeypatch):
    from app.tasks.celery_app import celery_app

    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setenv("MPSTATS_TOKEN", "tok")
    monkeypatch.setenv("MARKET_BACKFILL_DAYS", "2")
    get_settings.cache_clear()
    fake = FakeMPStats()
    for target in ("app.api.market.MPStatsClient", "app.tasks.market.MPStatsClient"):
        monkeypatch.setattr(target, lambda *a, **kw: fake.client(db))
    from app.api import market as market_api_mod
    market_api_mod._catalog_cache.update(at=0.0, items=[])
    return fake


def test_api_flow(api, db, market_api, monkeypatch):
    fake = market_api
    import app.tasks.market as tm
    monkeypatch.setattr(tm, "utcnow", lambda: __import__("datetime").datetime(2026, 10, 14, 6, 0))
    for d in ("2026-10-12", "2026-10-13"):
        fake.items[("Дом и сад/Посуда/Кружки", d)] = [item(1, 5), item(2, 1)]

    assert api.get("/api/market/status").json() == {"configured": True, "limit": {"available": 2500, "use": 38}}
    found = api.get("/api/market/catalog", params={"q": "круж"}).json()
    assert [c["name"] for c in found] == ["Кружки"]

    r = api.post("/api/market/categories", json={"path": found[0]["path"], "name": "马克杯", "top_n": 200})
    assert r.status_code == 201
    cats = api.get("/api/market/categories").json()
    assert cats[0]["products"] == 2 and cats[0]["last_fetched_date"] == "2026-10-13"

    page = api.get("/api/market/products", params={"sort": "sales_30d"}).json()
    assert page["as_of"] == "2026-10-13" and page["items"][0]["ozon_sku"] == 1
    pid = page["items"][0]["id"]

    assert api.patch(f"/api/market/products/{pid}", json={"is_favorite": True, "note": "可以做"}).json()["is_favorite"]
    assert api.get("/api/market/products", params={"favorites": True}).json()["total"] == 1
    detail = api.get(f"/api/market/products/{pid}").json()
    assert [h["sales"] for h in detail["history"]] == [5, 5] and detail["note"] == "可以做"
    assert detail["url"] == "https://www.ozon.ru/product/1/"
    hist = api.get(f"/api/market/categories/{cats[0]['id']}/history").json()
    assert hist[-1]["tracked_sales"] == 6

    assert api.post("/api/market/categories", json={"path": found[0]["path"]}).status_code == 409
    assert api.delete(f"/api/market/categories/{cats[0]['id']}").status_code == 204
    assert api.get("/api/market/products").json()["total"] == 2  # 历史数据保留


def test_api_without_token(api, monkeypatch):
    from app.api import market as market_api_mod

    market_api_mod._catalog_cache.update(at=0.0, items=[])
    monkeypatch.setenv("MPSTATS_TOKEN", "")
    get_settings.cache_clear()
    assert api.get("/api/market/status").json() == {"configured": False}
    r = api.get("/api/market/catalog", params={"q": "x"})
    assert r.status_code == 400 and "MPSTATS_TOKEN" in r.json()["detail"]
    # 没有 token 也能先添加类目，配置后再抓取
    assert api.post("/api/market/categories", json={"path": "A/B"}).status_code == 201
