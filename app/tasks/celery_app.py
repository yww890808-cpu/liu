"""Celery 应用与定时任务表。具体任务在后续阶段实现。"""
from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery("ozon", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(timezone="UTC", task_acks_late=True, worker_prefetch_multiplier=1)

# 定时任务（beat_schedule）将在订单 / 库存 / 商品同步阶段注册
celery_app.conf.beat_schedule = {}
