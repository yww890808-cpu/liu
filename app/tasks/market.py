"""选品数据抓取任务。"""
from sqlalchemy import select

from app.db.models import MarketCategory, utcnow
from app.db.session import SessionLocal
from app.market.mpstats import MPStatsClient
from app.services import market
from app.services.task_log import run_logged
from app.tasks.celery_app import celery_app


def fetch_market_category(category_id: int) -> str:
    def job() -> str:
        with SessionLocal() as db:
            cat = db.get(MarketCategory, category_id)
            if cat is None:
                raise ValueError(f"类目 {category_id} 不存在")
            client = MPStatsClient(db_factory=SessionLocal)
            try:
                return f"{cat.name}：{market.fetch_category(db, client, cat, utcnow().date())}"
            finally:
                client.close()

    return run_logged("market_fetch", None, job)


@celery_app.task(name="market.fetch_category")
def fetch_market_category_task(category_id: int) -> str:
    return fetch_market_category(category_id)


@celery_app.task(name="market.fetch_all")
def fetch_all_market_task() -> int:
    """每日定时：逐个抓取启用的关注类目（未配置 MPSTATS_TOKEN 时跳过）。"""
    from app.config import get_settings

    if not get_settings().mpstats_token:
        return 0
    with SessionLocal() as db:
        ids = list(db.scalars(select(MarketCategory.id).where(MarketCategory.is_active.is_(True))))
    for cid in ids:
        fetch_market_category_task.delay(cid)
    return len(ids)
