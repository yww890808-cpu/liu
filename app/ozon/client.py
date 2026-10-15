"""Ozon Seller API 客户端。业务代码只能通过这里访问 Ozon。

- 鉴权：请求头 Client-Id / Api-Key
- 限流：按店铺（Client-Id）令牌桶
- 重试：网络错误、429、5xx 指数退避重试；其余 4xx 业务错误直接抛出
- 日志：每次 HTTP 尝试写一条 api_logs
（通用逻辑见 app/utils/api_client.py）
"""
import time
from collections.abc import Callable

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.ozon.errors import OzonAPIError
from app.ozon.ratelimit import RateLimiter, get_limiter
from app.utils.api_client import ApiClient
from app.utils.crypto import mask


class OzonClient(ApiClient):
    error_cls = OzonAPIError

    def __init__(
        self,
        client_id: str,
        api_key: str,
        *,
        shop_id: int | None = None,
        db_factory: Callable[[], Session] | None = None,
        http: httpx.Client | None = None,
        limiter: RateLimiter | None = None,
        max_retries: int | None = None,
        backoff_base: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        settings = get_settings()
        self.client_id = client_id
        super().__init__(
            base_url=settings.ozon_base_url,
            headers={"Client-Id": client_id, "Api-Key": api_key},
            limiter=limiter or get_limiter(client_id, settings.ozon_rate_limit_per_sec),
            max_retries=settings.ozon_max_retries if max_retries is None else max_retries,
            timeout=settings.ozon_timeout_sec,
            shop_id=shop_id, db_factory=db_factory, http=http,
            backoff_base=backoff_base, sleep=sleep,
            log_name=f"client_id={client_id} key={mask(api_key)}",
        )
