"""任务执行记录：写入 task_logs，失败时记录错误并重新抛出。"""
import logging
from collections.abc import Callable
from typing import TypeVar

from app.db.models import TaskLog, utcnow
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)
T = TypeVar("T")


def run_logged(task_name: str, shop_id: int | None, fn: Callable[[], T],
               db_factory=None) -> T:
    with (db_factory or SessionLocal)() as db:
        log = TaskLog(task_name=task_name, shop_id=shop_id, status="running")
        db.add(log)
        db.commit()
        try:
            result = fn()
        except Exception as e:
            log.status, log.message, log.finished_at = "failed", f"{type(e).__name__}: {e}", utcnow()
            db.commit()
            logger.error("任务失败 %s shop=%s：%s", task_name, shop_id, e)
            raise
        log.status, log.message, log.finished_at = "success", str(result), utcnow()
        db.commit()
        return result
