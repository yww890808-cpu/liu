"""发货流程：备货确认（支持拆包）→ 获取面单 PDF。

使用 Ozon 物流，物流单号由 Ozon 生成，随订单同步写入 orders.tracking_number，无需回传。
"""
import base64
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Order, Shipment, Shop, TaskLog, utcnow
from app.ozon import endpoints
from app.ozon.client import OzonClient
from app.ozon.errors import OzonAPIError
from app.services import orders as order_service

logger = logging.getLogger(__name__)

SHIPPABLE_STATUS = "awaiting_packaging"
LABEL_BATCH_MAX = 20  # 面单接口一次最多 20 个货件


class ShippingError(ValueError):
    """业务校验失败（不调用 Ozon）。"""


@dataclass
class ShipResult:
    dry_run: bool
    payload: dict
    posting_numbers: list[str] = field(default_factory=list)  # Ozon 返回的货件号（拆包后为多个）


def default_packages(order: Order) -> list[list[dict]]:
    """默认整单一个包裹。"""
    return [[{"sku": i.sku, "quantity": i.quantity} for i in order.items]]


def build_ship_payload(order: Order, packages: list[list[dict]]) -> dict:
    """校验拆包方案并生成请求体：所有包裹的商品数量合计必须与订单完全一致。"""
    if order.status != SHIPPABLE_STATUS:
        raise ShippingError(f"订单状态为 {order.status}，只有待备货（{SHIPPABLE_STATUS}）的订单可以备货")
    if not packages or any(not pkg for pkg in packages):
        raise ShippingError("至少需要一个包裹，且每个包裹不能为空")
    _check_requirements(order)

    expected = Counter()
    for item in order.items:
        expected[item.sku] += item.quantity
    packed = Counter()
    for pkg in packages:
        for line in pkg:
            if line.get("quantity", 0) <= 0:
                raise ShippingError("包裹中的商品数量必须大于 0")
            packed[line["sku"]] += line["quantity"]
    if packed != expected:
        raise ShippingError(f"包裹商品与订单不一致：订单 {dict(expected)}，包裹合计 {dict(packed)}")

    return {
        "posting_number": order.posting_number,
        "packages": [{"products": [{"product_id": line["sku"], "quantity": line["quantity"]}
                                   for line in pkg]} for pkg in packages],
        "with": {"additional_data": True},
    }


def _check_requirements(order: Order) -> None:
    """需要报关单号 / 商品标识码的订单暂不支持自动备货，提示到 Ozon 后台处理。"""
    req = (order.raw_json or {}).get("requirements") or {}
    needs = [k for k, v in req.items() if isinstance(v, list) and v]
    if needs:
        raise ShippingError(f"该订单有附加要求（{', '.join(needs)}），请在 Ozon 后台手动备货")


def ship_order(db: Session, shop: Shop, client: OzonClient, order: Order,
               packages: list[list[dict]] | None = None) -> ShipResult:
    payload = build_ship_payload(order, packages or default_packages(order))

    if get_settings().dry_run:
        # 试运行：只记录不提交
        db.add(TaskLog(task_name="ship_dry_run", shop_id=shop.id, status="success",
                       message=json.dumps(payload, ensure_ascii=False), finished_at=utcnow()))
        db.commit()
        logger.info("试运行：未提交备货 %s", order.posting_number)
        return ShipResult(dry_run=True, payload=payload)

    data = client.post(endpoints.FBS_SHIP, payload)
    numbers = [n for n in data.get("result") or [] if n] or [order.posting_number]

    # 拆包后 Ozon 会生成新货件号：逐个拉取最新详情，并为每个货件建发货记录
    now = utcnow()
    for number in numbers:
        shipped = order_service.refresh_order(db, shop, client, number)
        shipment = shipped.shipment or Shipment(order=shipped)
        shipment.shipped_at = now
        shipment.tracking_number = shipped.tracking_number
        db.add(shipment)
    db.commit()
    return ShipResult(dry_run=False, payload=payload, posting_numbers=numbers)


# ---------------- 面单 ----------------

def is_label_not_ready(err: OzonAPIError) -> bool:
    """备货后面单尚未生成时 Ozon 返回 4xx，错误信息含 not ready。"""
    text = f"{err.code or ''} {err}".lower()
    return err.status_code is not None and 400 <= err.status_code < 500 and (
        "not ready" in text or "not_ready" in text)


def _extract_pdf(raw: bytes) -> bytes:
    """面单接口可能直接返回 PDF，也可能返回 JSON（file_content 为 base64）。"""
    if raw.startswith(b"%PDF"):
        return raw
    try:
        data = json.loads(raw)
        pdf = base64.b64decode((data.get("result") or data)["file_content"])
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError(f"无法识别面单响应：{raw[:100]!r}") from e
    if not pdf.startswith(b"%PDF"):
        raise ValueError("面单内容不是 PDF")
    return pdf


def fetch_labels_pdf(client: OzonClient, posting_numbers: list[str]) -> bytes:
    if not 1 <= len(posting_numbers) <= LABEL_BATCH_MAX:
        raise ShippingError(f"一次最多获取 {LABEL_BATCH_MAX} 个面单")
    raw = client.post_binary(endpoints.FBS_PACKAGE_LABEL, {"posting_number": posting_numbers})
    return _extract_pdf(raw)


def label_file(order: Order) -> Path:
    return Path(get_settings().label_dir) / str(order.shop_id) / f"{order.posting_number}.pdf"


def fetch_and_store_label(db: Session, client: OzonClient, order: Order) -> Path:
    """获取单个货件面单并保存到本地；失败原因记录在 shipments.label_error。"""
    shipment = order.shipment or Shipment(order=order)
    db.add(shipment)
    try:
        pdf = fetch_labels_pdf(client, [order.posting_number])
    except (OzonAPIError, ValueError) as e:
        shipment.label_error = str(e)
        db.commit()
        raise
    path = label_file(order)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf)
    shipment.label_path = str(path)
    shipment.label_error = None
    db.commit()
    return path


def orders_by_ids(db: Session, ids: list[int]) -> list[Order]:
    found = {o.id: o for o in db.scalars(select(Order).where(Order.id.in_(ids)))}
    missing = [i for i in ids if i not in found]
    if missing:
        raise ShippingError(f"订单不存在：{missing}")
    return [found[i] for i in ids]
