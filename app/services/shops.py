"""店铺管理：Api-Key 加密存储，按店铺构造 Ozon 客户端。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Shop
from app.db.session import SessionLocal
from app.ozon.client import OzonClient
from app.utils.crypto import decrypt, encrypt


class ShopError(ValueError):
    pass


def create_shop(db: Session, name: str, client_id: str, api_key: str) -> Shop:
    client_id = client_id.strip()
    if db.scalar(select(Shop).where(Shop.client_id == client_id)):
        raise ShopError(f"Client-Id {client_id} 已存在")
    shop = Shop(name=name.strip(), client_id=client_id, api_key_encrypted=encrypt(api_key.strip()))
    db.add(shop)
    db.commit()
    return shop


def update_shop(db: Session, shop: Shop, *, name: str | None = None, api_key: str | None = None,
                is_active: bool | None = None) -> Shop:
    if name is not None:
        shop.name = name.strip()
    if api_key:
        shop.api_key_encrypted = encrypt(api_key.strip())
    if is_active is not None:
        shop.is_active = is_active
    db.commit()
    return shop


def delete_shop(db: Session, shop: Shop) -> None:
    """删除店铺及其商品、仓库、订单（数据库级联）。"""
    db.delete(shop)
    db.commit()


def ozon_client_for(shop: Shop, db_factory=None) -> OzonClient:
    return OzonClient(shop.client_id, decrypt(shop.api_key_encrypted),
                      shop_id=shop.id, db_factory=db_factory or SessionLocal)
