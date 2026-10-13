"""Celery 应用与定时任务表。"""
from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

celery_app = Celery("ozon", broker=settings.redis_url, backend=settings.redis_url,
                    include=["app.tasks.catalog"])
celery_app.conf.update(timezone="UTC", task_acks_late=True, worker_prefetch_multiplier=1)

celery_app.conf.beat_schedule = {
    "sync-catalog-daily": {
        "task": "catalog.sync_all",
        "schedule": crontab(hour=settings.product_sync_hour, minute=0),
    },
}
