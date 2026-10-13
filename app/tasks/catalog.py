"""商品 / 仓库同步任务。"""
from sqlalchemy import select

from app.db.models import Shop
from app.db.session import SessionLocal
from app.services import catalog, stock
from app.services.shops import ozon_client_for
from app.services.task_log import run_logged
from app.tasks.celery_app import celery_app


def sync_shop_catalog(shop_id: int) -> str:
    """同步单个店铺的仓库和商品列表。"""
    def job() -> str:
        with SessionLocal() as db:
            shop = db.get(Shop, shop_id)
            if shop is None:
                raise ValueError(f"店铺 {shop_id} 不存在")
            client = ozon_client_for(shop)
            try:
                wh = catalog.sync_warehouses(db, shop, client)
                pr = catalog.sync_products(db, shop, client)
                st = stock.refresh_from_ozon(db, shop, client)
            finally:
                client.close()
            return f"仓库：{wh}；商品：{pr}；库存：{st}"

    return run_logged("sync_catalog", shop_id, job)


@celery_app.task(name="catalog.sync_shop")
def sync_shop_catalog_task(shop_id: int) -> str:
    return sync_shop_catalog(shop_id)


@celery_app.task(name="catalog.sync_all")
def sync_all_catalogs_task() -> int:
    """定时任务：为每个启用的店铺各派发一个同步任务。"""
    with SessionLocal() as db:
        ids = list(db.scalars(select(Shop.id).where(Shop.is_active.is_(True))))
    for shop_id in ids:
        sync_shop_catalog_task.delay(shop_id)
    return len(ids)
