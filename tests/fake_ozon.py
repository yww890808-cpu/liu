"""测试用的假 Ozon 服务：按路径路由到处理函数，记录所有请求。"""
import json

import httpx

from app.ozon.client import OzonClient
from app.ozon.ratelimit import RateLimiter


class FakeOzon:
    def __init__(self, routes: dict):
        self.routes = routes
        self.requests: list[tuple[str, dict]] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content or b"{}")
        self.requests.append((req.url.path, body))
        fn = self.routes.get(req.url.path)
        if fn is None:
            return httpx.Response(404, json={"message": f"no route {req.url.path}"})
        result = fn(body)
        return result if isinstance(result, httpx.Response) else httpx.Response(200, json=result)

    def client(self, shop_id=None, db_factory=None) -> OzonClient:
        http = httpx.Client(base_url="https://api-seller.ozon.ru",
                            transport=httpx.MockTransport(self.handler))
        return OzonClient("111", "key-12345678", shop_id=shop_id, db_factory=db_factory, http=http,
                          limiter=RateLimiter(1000), max_retries=0, sleep=lambda s: None)
