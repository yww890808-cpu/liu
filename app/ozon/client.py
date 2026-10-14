"""Ozon Seller API 客户端。业务代码只能通过这里访问 Ozon。

- 鉴权：请求头 Client-Id / Api-Key
- 限流：按店铺（Client-Id）令牌桶
- 重试：网络错误、429、5xx 指数退避重试；其余 4xx 业务错误直接抛出
- 日志：每次 HTTP 尝试写一条 api_logs
"""
import logging
import time
from collections.abc import Callable

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ApiLog
from app.ozon.errors import OzonAPIError
from app.ozon.ratelimit import RateLimiter, get_limiter
from app.utils.crypto import mask

logger = logging.getLogger(__name__)

# api_logs.error 最多保留的字符数
_MAX_ERROR_LEN = 2000


class OzonClient:
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
        self.shop_id = shop_id
        self._db_factory = db_factory
        self._http = http or httpx.Client(base_url=settings.ozon_base_url,
                                          timeout=settings.ozon_timeout_sec)
        self._headers = {"Client-Id": client_id, "Api-Key": api_key}
        self._limiter = limiter or get_limiter(client_id, settings.ozon_rate_limit_per_sec)
        self._max_retries = settings.ozon_max_retries if max_retries is None else max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._key_masked = mask(api_key)

    # ---------- 对外方法 ----------

    def post(self, endpoint: str, payload: dict | None = None) -> dict:
        """POST JSON，返回解析后的 JSON。"""
        resp = self._request(endpoint, payload or {})
        try:
            return resp.json()
        except ValueError as e:
            raise OzonAPIError(endpoint, f"响应不是合法 JSON: {e}", resp.status_code) from e

    def post_binary(self, endpoint: str, payload: dict | None = None) -> bytes:
        """POST JSON，返回原始字节（如面单 PDF）。"""
        return self._request(endpoint, payload or {}).content

    def close(self) -> None:
        self._http.close()

    # ---------- 内部实现 ----------

    def _request(self, endpoint: str, payload: dict) -> httpx.Response:
        attempt = 0
        while True:
            self._limiter.acquire()
            start = time.monotonic()
            try:
                resp = self._http.post(endpoint, json=payload, headers=self._headers)
            except httpx.HTTPError as e:
                err = OzonAPIError(endpoint, f"{type(e).__name__}: {e}")
                self._log(endpoint, None, start, str(err))
            else:
                if resp.status_code < 400:
                    self._log(endpoint, resp.status_code, start, None)
                    return resp
                err = self._error_from_response(endpoint, resp)
                self._log(endpoint, resp.status_code, start, str(err))

            if not err.retryable or attempt >= self._max_retries:
                logger.warning("Ozon 调用失败 client_id=%s key=%s %s",
                               self.client_id, self._key_masked, err)
                raise err
            delay = self._retry_delay(attempt, err)
            attempt += 1
            logger.info("Ozon 调用重试 %s/%s，%.1fs 后重试：%s",
                        attempt, self._max_retries, delay, err)
            self._sleep(delay)

    def _retry_delay(self, attempt: int, err: OzonAPIError) -> float:
        delay = self._backoff_base * (2 ** attempt)
        # 服务端给了 Retry-After 时取两者较大值
        if err.retry_after:
            delay = max(delay, err.retry_after)
        return delay

    @staticmethod
    def _error_from_response(endpoint: str, resp: httpx.Response) -> OzonAPIError:
        code = None
        message = resp.text[:500]
        body: object = resp.text
        try:
            body = resp.json()
            if isinstance(body, dict):
                code = body.get("code")
                message = body.get("message") or message
        except ValueError:
            pass
        retry_after = None
        try:
            retry_after = float(resp.headers.get("Retry-After", ""))
        except ValueError:
            pass
        return OzonAPIError(endpoint, message, resp.status_code, code=code, body=body,
                            retry_after=retry_after)

    def _log(self, endpoint: str, status_code: int | None, start: float, error: str | None) -> None:
        if self._db_factory is None:
            return
        duration_ms = int((time.monotonic() - start) * 1000)
        # 每条日志独立会话、独立提交，不受业务事务回滚影响
        try:
            with self._db_factory() as db:
                db.add(ApiLog(shop_id=self.shop_id, endpoint=endpoint, status_code=status_code,
                              duration_ms=duration_ms,
                              error=error[:_MAX_ERROR_LEN] if error else None))
                db.commit()
        except Exception:  # 日志写入失败不能影响业务调用
            logger.exception("写入 api_logs 失败 endpoint=%s", endpoint)
