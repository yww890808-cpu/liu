"""外部 HTTP 接口的通用客户端：限流、指数退避重试、统一异常、调用日志（api_logs）。

Ozon 与 MPStats 客户端都基于它，业务代码不直接发 HTTP 请求。
"""
import logging
import time
from collections.abc import Callable

import httpx
from sqlalchemy.orm import Session

from app.db.models import ApiLog
from app.ozon.ratelimit import RateLimiter

logger = logging.getLogger(__name__)

# api_logs.error 最多保留的字符数
_MAX_ERROR_LEN = 2000


class ApiError(Exception):
    """调用外部接口失败。status_code 为 None 表示网络层错误（超时、连接失败等）。"""

    def __init__(self, endpoint: str, message: str, status_code: int | None = None,
                 code: str | int | None = None, body: object = None,
                 retry_after: float | None = None, retryable: bool | None = None):
        self.endpoint = endpoint
        self.status_code = status_code
        self.code = code  # 服务方返回的业务错误码
        self.body = body
        self.retry_after = retry_after  # 服务端建议的等待秒数
        self._retryable = retryable
        super().__init__(f"[{endpoint}] {status_code or '网络错误'}: {message}")

    @property
    def retryable(self) -> bool:
        if self._retryable is not None:
            return self._retryable
        return self.status_code is None or self.status_code == 429 or self.status_code >= 500


class ApiClient:
    """子类提供 base_url、请求头、限流器和异常类型。"""

    error_cls: type[ApiError] = ApiError
    log_prefix = ""  # 写入 api_logs.endpoint 的前缀，用于区分不同服务
    pending_statuses: frozenset[int] = frozenset()  # 表示“结果尚未生成，请稍后重试”的状态码

    def __init__(self, *, base_url: str, headers: dict, limiter: RateLimiter, max_retries: int,
                 timeout: float, shop_id: int | None = None,
                 db_factory: Callable[[], Session] | None = None, http: httpx.Client | None = None,
                 backoff_base: float = 1.0, sleep: Callable[[float], None] = time.sleep,
                 log_name: str = ""):
        self.shop_id = shop_id
        self._db_factory = db_factory
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout)
        self._headers = headers
        self._limiter = limiter
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._log_name = log_name  # 出现在应用日志中的标识（不含密钥明文）

    # ---------- 对外方法 ----------

    def post(self, endpoint: str, payload: dict | None = None, params: dict | None = None) -> dict:
        """POST JSON，返回解析后的 JSON。"""
        return self._json(endpoint, self._request("POST", endpoint, payload or {}, params))

    def get(self, endpoint: str, params: dict | None = None):
        return self._json(endpoint, self._request("GET", endpoint, None, params))

    def post_binary(self, endpoint: str, payload: dict | None = None) -> bytes:
        """POST JSON，返回原始字节（如面单 PDF）。"""
        return self._request("POST", endpoint, payload or {}, None).content

    def close(self) -> None:
        self._http.close()

    # ---------- 内部实现 ----------

    def _json(self, endpoint: str, resp: httpx.Response):
        try:
            return resp.json()
        except ValueError as e:
            raise self.error_cls(endpoint, f"响应不是合法 JSON: {e}", resp.status_code) from e

    def _request(self, method: str, endpoint: str, payload: dict | None,
                 params: dict | None) -> httpx.Response:
        attempt = 0
        while True:
            self._limiter.acquire()
            start = time.monotonic()
            try:
                resp = self._http.request(method, endpoint, json=payload, params=params,
                                          headers=self._headers)
            except httpx.HTTPError as e:
                err = self.error_cls(endpoint, f"{type(e).__name__}: {e}")
                self._log(endpoint, None, start, str(err))
            else:
                if resp.status_code in self.pending_statuses:
                    err = self.error_cls(endpoint, "数据生成中，稍后重试", resp.status_code,
                                         retry_after=_retry_after(resp), retryable=True)
                    self._log(endpoint, resp.status_code, start, str(err))
                elif resp.status_code < 400:
                    self._log(endpoint, resp.status_code, start, None)
                    return resp
                else:
                    err = self._error_from_response(endpoint, resp)
                    self._log(endpoint, resp.status_code, start, str(err))

            if not err.retryable or attempt >= self._max_retries:
                logger.warning("外部接口调用失败 %s %s", self._log_name, err)
                raise err
            delay = self._retry_delay(attempt, err)
            attempt += 1
            logger.info("外部接口重试 %s/%s，%.1fs 后重试：%s", attempt, self._max_retries, delay, err)
            self._sleep(delay)

    def _retry_delay(self, attempt: int, err: ApiError) -> float:
        delay = self._backoff_base * (2 ** attempt)
        # 服务端给了 Retry-After 时取两者较大值
        if err.retry_after:
            delay = max(delay, err.retry_after)
        return delay

    def _error_from_response(self, endpoint: str, resp: httpx.Response) -> ApiError:
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
        return self.error_cls(endpoint, message, resp.status_code, code=code, body=body,
                              retry_after=_retry_after(resp))

    def _log(self, endpoint: str, status_code: int | None, start: float, error: str | None) -> None:
        if self._db_factory is None:
            return
        duration_ms = int((time.monotonic() - start) * 1000)
        # 每条日志独立会话、独立提交，不受业务事务回滚影响
        try:
            with self._db_factory() as db:
                db.add(ApiLog(shop_id=self.shop_id, endpoint=self.log_prefix + endpoint,
                              status_code=status_code, duration_ms=duration_ms,
                              error=error[:_MAX_ERROR_LEN] if error else None))
                db.commit()
        except Exception:  # 日志写入失败不能影响业务调用
            logger.exception("写入 api_logs 失败 endpoint=%s", endpoint)


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return float(resp.headers.get("Retry-After", ""))
    except ValueError:
        return None
