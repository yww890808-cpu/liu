"""数据模型。"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Shop(Base):
    __tablename__ = "shops"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    client_id: Mapped[str] = mapped_column(String(64), unique=True)
    # Api-Key 以 Fernet 密文存储，明文不落库
    api_key_encrypted: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("shop_id", "offer_id", name="uq_products_shop_offer"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    offer_id: Mapped[str] = mapped_column(String(128))  # 卖家货号
    ozon_product_id: Mapped[int | None] = mapped_column(BigInteger)
    sku: Mapped[int | None] = mapped_column(BigInteger, index=True)
    name: Mapped[str | None] = mapped_column(String(512))
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    stocks: Mapped[list["ProductStock"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class Warehouse(Base):
    """店铺的 FBS 仓库（从 Ozon 同步）。库存按仓库维护。"""

    __tablename__ = "warehouses"
    __table_args__ = (UniqueConstraint("shop_id", "ozon_warehouse_id", name="uq_warehouses_shop_wh"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    ozon_warehouse_id: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str | None] = mapped_column(String(64))  # Ozon 侧状态，如 created / disabled
    # 本地开关：关闭后不向该仓库推送库存
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ProductStock(Base):
    """商品在某个仓库的库存。"""

    __tablename__ = "product_stocks"
    __table_args__ = (UniqueConstraint("product_id", "warehouse_id", name="uq_stocks_product_wh"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id", ondelete="CASCADE"), index=True)
    # 本地维护的可售库存；为空表示从未设置，此时不会推送
    local_stock: Mapped[int | None] = mapped_column(Integer)
    # 最近一次成功推送到 Ozon 的值；local_stock 与它不同时才推送
    synced_stock: Mapped[int | None] = mapped_column(Integer)
    # 最近一次从 Ozon 读到的可售库存（总数 - 预留）与预留数，仅用于展示和首次导入
    ozon_stock: Mapped[int | None] = mapped_column(Integer)
    ozon_reserved: Mapped[int | None] = mapped_column(Integer)
    ozon_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    push_error: Mapped[str | None] = mapped_column(Text)  # 最近一次推送失败原因
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    product: Mapped[Product] = relationship(back_populates="stocks")
    warehouse: Mapped[Warehouse] = relationship()


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    posting_number: Mapped[str] = mapped_column(String(64), unique=True)  # 去重键
    # 拆包发货后 Ozon 会生成新的货件号，这里记录原货件号
    parent_posting_number: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(64), index=True)
    substatus: Mapped[str | None] = mapped_column(String(64))
    ozon_warehouse_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    warehouse_name: Mapped[str | None] = mapped_column(String(255))
    tracking_number: Mapped[str | None] = mapped_column(String(128))  # Ozon 物流生成的单号
    ozon_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    shipment_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_json: Mapped[dict | None] = mapped_column(JSON)  # 保留原始数据便于排查
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    shipment: Mapped["Shipment | None"] = relationship(back_populates="order", uselist=False)


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    sku: Mapped[int | None] = mapped_column(BigInteger)
    offer_id: Mapped[str | None] = mapped_column(String(128))
    name: Mapped[str | None] = mapped_column(String(512))
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))

    order: Mapped[Order] = relationship(back_populates="items")


class Shipment(Base):
    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), unique=True)
    tracking_number: Mapped[str | None] = mapped_column(String(128))
    label_path: Mapped[str | None] = mapped_column(String(512))
    label_error: Mapped[str | None] = mapped_column(Text)  # 最近一次获取面单失败的原因
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 备货确认时间

    order: Mapped[Order] = relationship(back_populates="shipment")


class ApiLog(Base):
    __tablename__ = "api_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int | None] = mapped_column(ForeignKey("shops.id", ondelete="SET NULL"), index=True)
    endpoint: Mapped[str] = mapped_column(String(255))
    status_code: Mapped[int | None] = mapped_column(Integer)  # 网络错误时为空
    duration_ms: Mapped[int] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class TaskLog(Base):
    __tablename__ = "task_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_name: Mapped[str] = mapped_column(String(128), index=True)
    shop_id: Mapped[int | None] = mapped_column(ForeignKey("shops.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(16))  # running / success / failed
    message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
