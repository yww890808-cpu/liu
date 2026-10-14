"""阶段 3：仓库与商品同步。"""
import pytest

from app.db.models import Product, Warehouse
from app.ozon import endpoints
from app.ozon.errors import OzonAPIError
from app.services import catalog
from app.services.shops import create_shop
from tests.fake_ozon import FakeOzon


@pytest.fixture
def shop(db):
    return create_shop(db, "店铺A", "111", "key-12345678")


def product_routes(products: list[dict], page_size: int):
    """模拟 /v3/product/list 游标分页 + /v3/product/info/list。"""
    by_id = {p["id"]: p for p in products}

    def product_list(body):
        start = int(body["last_id"] or 0)
        page = products[start:start + page_size]
        nxt = start + len(page)
        return {"result": {"items": [{"product_id": p["id"], "offer_id": p["offer_id"],
                                      "archived": p.get("archived", False)} for p in page],
                           "total": len(products),
                           "last_id": str(nxt) if nxt < len(products) else ""}}

    def info_list(body):
        return {"items": [{"id": i, "offer_id": by_id[i]["offer_id"], "sku": by_id[i]["sku"],
                           "name": by_id[i]["name"]} for i in body["product_id"]]}

    return {endpoints.PRODUCT_LIST: product_list, endpoints.PRODUCT_INFO_LIST: info_list}


def test_sync_warehouses_upsert(db, shop):
    data = {"result": [{"warehouse_id": 1001, "name": "Moscow FBS", "status": "created"},
                       {"warehouse_id": 1002, "name": "SPB FBS", "status": "created"}]}
    fake = FakeOzon({endpoints.WAREHOUSE_LIST: lambda b: data})
    r = catalog.sync_warehouses(db, shop, fake.client())
    assert (r.created, r.updated) == (2, 0)

    data["result"][0]["name"] = "Moscow FBS 2"
    r = catalog.sync_warehouses(db, shop, fake.client())
    assert (r.created, r.updated) == (0, 2)
    names = sorted(w.name for w in db.query(Warehouse))
    assert names == ["Moscow FBS 2", "SPB FBS"]


def test_sync_products_paginates_and_upserts(db, shop, monkeypatch):
    monkeypatch.setattr(catalog, "PRODUCT_LIST_PAGE", 2)
    monkeypatch.setattr(catalog, "PRODUCT_INFO_BATCH", 1)
    products = [{"id": 10 + i, "offer_id": f"A{i}", "sku": 3000000000 + i, "name": f"商品{i}"}
                for i in range(5)]
    products[4]["archived"] = True
    fake = FakeOzon(product_routes(products, page_size=2))

    r = catalog.sync_products(db, shop, fake.client())
    assert (r.created, r.updated) == (5, 0)
    list_calls = [b for p, b in fake.requests if p == endpoints.PRODUCT_LIST]
    assert [c["last_id"] for c in list_calls] == ["", "2", "4"]
    assert all(c["filter"] == {"visibility": "ALL"} for c in list_calls)

    p = db.query(Product).filter_by(offer_id="A1").one()
    assert (p.ozon_product_id, p.sku, p.name, p.archived) == (11, 3000000001, "商品1", False)
    assert db.query(Product).filter_by(offer_id="A4").one().archived is True
    assert shop.last_sync_at is not None

    # 再次同步：按 offer_id 更新而不是新增
    products[1]["name"] = "改名"
    r = catalog.sync_products(db, shop, fake.client())
    assert (r.created, r.updated) == (0, 5)
    assert db.query(Product).count() == 5
    assert db.query(Product).filter_by(offer_id="A1").one().name == "改名"


def test_sync_products_error_keeps_committed_pages(db, shop, monkeypatch):
    monkeypatch.setattr(catalog, "PRODUCT_LIST_PAGE", 2)
    products = [{"id": i, "offer_id": f"B{i}", "sku": i, "name": "x"} for i in range(4)]
    routes = product_routes(products, page_size=2)
    ok_list = routes[endpoints.PRODUCT_LIST]

    def flaky_list(body):
        if body["last_id"]:
            import httpx
            return httpx.Response(400, json={"code": 3, "message": "bad"})
        return ok_list(body)

    routes[endpoints.PRODUCT_LIST] = flaky_list
    with pytest.raises(OzonAPIError):
        catalog.sync_products(db, shop, FakeOzon(routes).client())
    assert db.query(Product).count() == 2  # 第一页已提交
