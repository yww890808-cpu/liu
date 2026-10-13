"""Ozon API 统一异常。"""


class OzonAPIError(Exception):
    """调用 Ozon API 失败。

    status_code 为 None 表示网络层错误（超时、连接失败等）。
    """

    def __init__(self, endpoint: str, message: str, status_code: int | None = None,
                 code: str | int | None = None, body: object = None,
                 retry_after: float | None = None):
        self.endpoint = endpoint
        self.retry_after = retry_after  # 429 时服务端建议的等待秒数
        self.status_code = status_code
        self.code = code  # Ozon 返回的业务错误码
        self.body = body
        super().__init__(f"[{endpoint}] {status_code or '网络错误'}: {message}")

    @property
    def retryable(self) -> bool:
        return self.status_code is None or self.status_code == 429 or self.status_code >= 500
