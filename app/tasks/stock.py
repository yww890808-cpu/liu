"""库存任务：读取 Ozon 库存、推送本地库存。"""
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Shop
from app.db.session import SessionLocal
from app.services import stock
from app.services.shops import ozon_client_for
from app.services.task_log import run_logged
from app.tasks.celery_app import celery_app


def _with_shop(task_name: str, shop_id: int, fn) -> str:
    def job() -> str:
        with SessionLocal() as db:
            shop = db.get(Shop, shop_id)
            if shop is None:
                raise ValueError(f"店铺 {shop_id} 不存在")
            client = ozon_client_for(shop)
            try:
                return str(fn(db, shop, client))
            finally:
                client.close()

    return run_logged(task_name, shop_id, job)


@celery_app.task(name="stock.refresh_shop")
def refresh_stock_task(shop_id: int) -> str:
    return _with_shop("refresh_stock", shop_id, stock.refresh_from_ozon)


@celery_app.task(name="stock.push_shop")
def push_stock_task(shop_id: int) -> str:
    return _with_shop("push_stock", shop_id, stock.push_stocks)


@celery_app.task(name="stock.push_all")
def push_all_stock_task() -> int:
    expires = get_settings().stock_push_interval_minutes * 60
    with SessionLocal() as db:
        ids = list(db.scalars(select(Shop.id).where(Shop.is_active.is_(True))))
    for shop_id in ids:
        push_stock_task.apply_async((shop_id,), expires=expires)
    return len(ids)
