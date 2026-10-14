"""MPStats 数据服务客户端（Ozon 全平台商品与类目数据）。

接口说明来自 MPStats 官方文档（github.com/mpstats-io/claude-code-skills）：
- 鉴权：请求头 X-Mpstats-TOKEN
- 202 表示结果尚未生成需稍后重试；429 按 Retry-After 等待
- 列表接口请求体为 {startRow, endRow, filterModel, sortModel}，单次最多 5000 行
"""
import time
from collections.abc import Callable
from datetime import date

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.ozon.ratelimit import RateLimiter, get_limiter
from app.utils.api_client import ApiClient, ApiError

OZ = "analytics/v1/oz/"
CATEGORY_LIST = OZ + "category/list"
CATEGORY_ITEMS = OZ + "category/items"
CATEGORY_BY_DATE = OZ + "category/by_date"
API_LIMIT = "user/report_api_limit"

MAX_ROWS = 5000


class MPStatsError(ApiError):
    pass


class MPStatsNotConfigured(RuntimeError):
    pass


class MPStatsClient(ApiClient):
    error_cls = MPStatsError
    log_prefix = "mpstats:"
    pending_statuses = frozenset({202})

    def __init__(self, token: str | None = None, *, db_factory: Callable[[], Session] | None = None,
                 http: httpx.Client | None = None, limiter: RateLimiter | None = None,
                 max_retries: int = 4, backoff_base: float = 2.0,
                 sleep: Callable[[float], None] = time.sleep):
        settings = get_settings()
        token = token if token is not None else settings.mpstats_token
        if not token:
            raise MPStatsNotConfigured("未配置 MPSTATS_TOKEN，请在 .env 中填写 MPStats 的 API token")
        super().__init__(
            base_url=settings.mpstats_base_url,
            headers={"X-Mpstats-TOKEN": token, "Content-Type": "application/json"},
            limiter=limiter or get_limiter("mpstats", settings.mpstats_rate_limit_per_sec),
            max_retries=max_retries, timeout=120, db_factory=db_factory, http=http,
            backoff_base=backoff_base, sleep=sleep, log_name="mpstats",
        )
        self._fbs = 1 if settings.mpstats_include_fbs else 0

    def _period(self, path: str, d1: date, d2: date) -> dict:
        return {"d1": d1.isoformat(), "d2": d2.isoformat(), "path": path, "fbs": self._fbs}

    def categories(self) -> list[dict]:
        """Ozon 类目树：[{url, path, name}]。"""
        data = self.post(CATEGORY_LIST, {}, params={"fbs": "true" if self._fbs else "false"})
        return data if isinstance(data, list) else data.get("data") or []

    def category_items(self, path: str, d1: date, d2: date, limit: int) -> list[dict]:
        """类目内商品，按销售额从高到低取前 limit 个。"""
        rows: list[dict] = []
        while len(rows) < limit:
            start = len(rows)
            end = min(limit, start + MAX_ROWS)
            data = self.post(CATEGORY_ITEMS, {
                "startRow": start, "endRow": end, "filterModel": {},
                "sortModel": [{"colId": "revenue", "sort": "desc"}],
            }, params=self._period(path, d1, d2))
            page = data.get("data") or []
            rows.extend(page)
            if len(page) < end - start or len(rows) >= (data.get("total") or 0):
                break
        return rows[:limit]

    def category_by_date(self, path: str, d1: date, d2: date) -> list[dict]:
        data = self.post(CATEGORY_BY_DATE, {"startRow": 0, "endRow": MAX_ROWS, "filterModel": {}, "sortModel": []},
                         params=self._period(path, d1, d2) | {"groupBy": "day"})
        return data if isinstance(data, list) else data.get("data") or []

    def api_limit(self) -> dict:
        """剩余调用额度：{available, use}。"""
        return self.get(API_LIMIT)
