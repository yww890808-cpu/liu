"""阶段 1：骨架冒烟测试。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.db.models import Order, OrderItem, Shop
from app.main import app


def test_health():
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_dry_run_defaults_on(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("DRY_RUN", raising=False)
    assert Settings(_env_file=None).dry_run is True


def test_order_posting_number_unique(db):
    shop = Shop(name="测试店", client_id="123", api_key_encrypted="x")
    db.add(shop)
    db.flush()
    db.add(Order(shop_id=shop.id, posting_number="P-1", status="awaiting_packaging"))
    db.commit()

    db.add(Order(shop_id=shop.id, posting_number="P-1", status="awaiting_deliver"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_order_items_cascade(db):
    shop = Shop(name="测试店", client_id="123", api_key_encrypted="x")
    db.add(shop)
    db.flush()
    order = Order(shop_id=shop.id, posting_number="P-2", status="awaiting_packaging",
                  items=[OrderItem(sku=3000000001, offer_id="A1", quantity=2)])
    db.add(order)
    db.commit()
    assert db.query(OrderItem).count() == 1

    db.delete(order)
    db.commit()
    assert db.query(OrderItem).count() == 0
