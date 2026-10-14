"""阶段 2：Ozon 客户端封装测试（全部 mock，不调用真实接口）。"""
import logging

import httpx
import pytest

from app.db.models import ApiLog
from app.ozon.client import OzonClient
from app.ozon.errors import OzonAPIError
from app.ozon.ratelimit import RateLimiter

API_KEY = "abcd1234-secret-key-value"


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(handler, db=None, **kw):
    """用 MockTransport 构造客户端；sleeps 记录退避等待时间。"""
    sleeps: list[float] = []
    http = httpx.Client(base_url="https://api-seller.ozon.ru", transport=httpx.MockTransport(handler))
    client = OzonClient(
        "111", API_KEY, shop_id=None, http=http,
        db_factory=db.factory if db is not None else None,
        limiter=RateLimiter(1000), max_retries=3, sleep=sleeps.append, **kw,
    )
    return client, sleeps


def test_sends_auth_headers_and_json():
    seen = {}

    def handler(req: httpx.Request):
        seen["headers"] = req.headers
        seen["path"] = req.url.path
        seen["body"] = req.content
        return httpx.Response(200, json={"result": {"ok": True}})

    client, _ = make_client(handler)
    assert client.post("/v3/posting/fbs/list", {"limit": 1}) == {"result": {"ok": True}}
    assert seen["headers"]["Client-Id"] == "111"
    assert seen["headers"]["Api-Key"] == API_KEY
    assert seen["path"] == "/v3/posting/fbs/list"
    assert seen["body"] == b'{"limit":1}'


def test_retries_429_then_succeeds_with_backoff():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, json={"code": 8, "message": "too many requests"})
        return httpx.Response(200, json={"ok": 1})

    client, sleeps = make_client(handler)
    assert client.post("/x") == {"ok": 1}
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]  # 指数退避


def test_retry_after_header_respected():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, json={})
        return httpx.Response(200, json={})

    client, sleeps = make_client(handler)
    client.post("/x")
    assert sleeps == [7.0]


def test_retries_5xx_and_network_errors_until_exhausted():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) % 2:
            raise httpx.ConnectError("boom")
        return httpx.Response(503, text="unavailable")

    client, sleeps = make_client(handler)
    with pytest.raises(OzonAPIError) as ei:
        client.post("/x")
    assert len(calls) == 4  # 1 次 + 3 次重试
    assert sleeps == [1.0, 2.0, 4.0]
    assert ei.value.status_code == 503


def test_4xx_business_error_not_retried():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(400, json={"code": 3, "message": "invalid posting_number"})

    client, sleeps = make_client(handler)
    with pytest.raises(OzonAPIError) as ei:
        client.post("/v4/posting/fbs/ship")
    assert len(calls) == 1 and sleeps == []
    err = ei.value
    assert err.status_code == 400 and err.code == 3
    assert "invalid posting_number" in str(err)
    assert not err.retryable


def test_api_logs_written_for_each_attempt(db):
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(500, json={"message": "internal"})
        return httpx.Response(200, json={})

    client, _ = make_client(handler, db=db)
    client.post("/v2/posting/fbs/package-label")
    logs = db.query(ApiLog).order_by(ApiLog.id).all()
    assert [(l.endpoint, l.status_code) for l in logs] == [
        ("/v2/posting/fbs/package-label", 500),
        ("/v2/posting/fbs/package-label", 200),
    ]
    assert "internal" in logs[0].error and logs[1].error is None
    assert all(l.duration_ms >= 0 for l in logs)


def test_network_error_logged_without_status(db):
    def handler(req):
        raise httpx.ReadTimeout("slow")

    client, _ = make_client(handler, db=db)
    with pytest.raises(OzonAPIError):
        client.post("/x")
    logs = db.query(ApiLog).all()
    assert len(logs) == 4
    assert all(l.status_code is None and "ReadTimeout" in l.error for l in logs)


def test_api_key_never_logged(db, caplog):
    def handler(req):
        return httpx.Response(403, json={"message": "forbidden"})

    client, _ = make_client(handler, db=db)
    with caplog.at_level(logging.DEBUG), pytest.raises(OzonAPIError):
        client.post("/x")
    assert API_KEY not in caplog.text
    assert "abcd****" in caplog.text
    assert all(API_KEY not in (l.error or "") for l in db.query(ApiLog))


def test_post_binary_returns_pdf_bytes():
    client, _ = make_client(lambda req: httpx.Response(
        200, content=b"%PDF-1.4 fake", headers={"Content-Type": "application/pdf"}))
    assert client.post_binary("/v2/posting/fbs/package-label").startswith(b"%PDF")


def test_invalid_json_raises_ozon_error():
    client, _ = make_client(lambda req: httpx.Response(200, text="<html>"))
    with pytest.raises(OzonAPIError, match="JSON"):
        client.post("/x")


def test_rate_limiter_throttles_to_rate():
    clock = FakeClock()
    limiter = RateLimiter(2, clock=clock, sleep=clock.sleep)
    for _ in range(6):
        limiter.acquire()
    # 桶容量 2：前 2 次立即通过，后 4 次每次等待 0.5s
    assert clock.now == pytest.approx(2.0)


def test_rate_limiter_fractional_rate():
    clock = FakeClock()
    limiter = RateLimiter(0.5, clock=clock, sleep=clock.sleep)
    limiter.acquire()
    limiter.acquire()
    assert clock.now == pytest.approx(2.0)
