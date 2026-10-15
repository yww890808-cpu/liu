"""Celery 应用与定时任务表。"""
from celery import Celery
from celery.schedules import crontab

from app.config import check_secrets, get_settings

check_secrets()
settings = get_settings()

celery_app = Celery("ozon", broker=settings.redis_url, backend=settings.redis_url,
                    include=["app.tasks.catalog", "app.tasks.orders", "app.tasks.shipping", "app.tasks.stock", "app.tasks.market"])
celery_app.conf.update(timezone="UTC", task_acks_late=True, worker_prefetch_multiplier=1)

celery_app.conf.beat_schedule = {
    "sync-orders": {
        "task": "orders.sync_all",
        "schedule": settings.order_sync_interval_minutes * 60,
    },
    "push-stock": {
        "task": "stock.push_all",
        "schedule": settings.stock_push_interval_minutes * 60,
    },
    "market-fetch-daily": {
        "task": "market.fetch_all",
        "schedule": crontab(hour=settings.market_fetch_hour, minute=10),
    },
    "sync-catalog-daily": {
        "task": "catalog.sync_all",
        "schedule": crontab(hour=settings.product_sync_hour, minute=0),
    },
}
