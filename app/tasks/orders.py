"""订单拉取任务。"""
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Shop
from app.db.session import SessionLocal
from app.services import orders
from app.services.shops import ozon_client_for
from app.services.task_log import run_logged
from app.tasks.celery_app import celery_app


def sync_shop_orders(shop_id: int) -> str:
    def job() -> str:
        with SessionLocal() as db:
            shop = db.get(Shop, shop_id)
            if shop is None:
                raise ValueError(f"店铺 {shop_id} 不存在")
            client = ozon_client_for(shop)
            try:
                return str(orders.sync_orders(db, shop, client))
            finally:
                client.close()

    return run_logged("sync_orders", shop_id, job)


@celery_app.task(name="orders.sync_shop")
def sync_shop_orders_task(shop_id: int) -> str:
    return sync_shop_orders(shop_id)


@celery_app.task(name="orders.sync_all")
def sync_all_orders_task() -> int:
    """定时任务：为每个启用的店铺派发拉单任务。

    设置过期时间为一个周期：worker 积压时旧任务直接丢弃，避免同一店铺任务堆积。
    """
    expires = get_settings().order_sync_interval_minutes * 60
    with SessionLocal() as db:
        ids = list(db.scalars(select(Shop.id).where(Shop.is_active.is_(True))))
    for shop_id in ids:
        sync_shop_orders_task.apply_async((shop_id,), expires=expires)
    return len(ids)
