"""日志查看：任务执行记录与 Ozon 接口调用记录。"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.db.models import ApiLog, TaskLog
from app.db.session import get_db

router = APIRouter(prefix="/api/logs", tags=["日志"], dependencies=[Depends(current_user)])


class TaskLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_name: str
    shop_id: int | None
    status: str
    message: str | None
    started_at: datetime
    finished_at: datetime | None


class ApiLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    shop_id: int | None
    endpoint: str
    status_code: int | None
    duration_ms: int
    error: str | None
    created_at: datetime


@router.get("/tasks")
def task_logs(db: Session = Depends(get_db), shop_id: int | None = None, status: str | None = None,
              task_name: str | None = None,
              page: int = Query(default=1, ge=1), size: int = Query(default=50, ge=1, le=200)) -> dict:
    stmt = select(TaskLog)
    if shop_id is not None:
        stmt = stmt.where(TaskLog.shop_id == shop_id)
    if status:
        stmt = stmt.where(TaskLog.status == status)
    if task_name:
        stmt = stmt.where(TaskLog.task_name == task_name)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(TaskLog.id.desc()).offset((page - 1) * size).limit(size))
    return {"total": total, "items": [TaskLogOut.model_validate(r) for r in rows]}


@router.get("/api")
def api_logs(db: Session = Depends(get_db), shop_id: int | None = None,
             only_errors: bool = False, endpoint: str | None = None,
             page: int = Query(default=1, ge=1), size: int = Query(default=50, ge=1, le=200)) -> dict:
    stmt = select(ApiLog)
    if shop_id is not None:
        stmt = stmt.where(ApiLog.shop_id == shop_id)
    if only_errors:
        stmt = stmt.where(ApiLog.error.is_not(None))
    if endpoint:
        stmt = stmt.where(ApiLog.endpoint.ilike(f"%{endpoint}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(ApiLog.id.desc()).offset((page - 1) * size).limit(size))
    return {"total": total, "items": [ApiLogOut.model_validate(r) for r in rows]}
