"""按店铺维度的令牌桶限流（进程内）。"""
import threading
import time
from collections.abc import Callable


class RateLimiter:
    """令牌桶：每秒补充 rate 个令牌，桶容量 = max(1, rate)。"""

    def __init__(self, rate: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        if rate <= 0:
            raise ValueError("rate 必须大于 0")
        self.rate = rate
        self.capacity = max(1.0, rate)
        self._tokens = self.capacity
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait = (1 - self._tokens) / self.rate
            self._sleep(wait)


_limiters: dict[str, RateLimiter] = {}
_registry_lock = threading.Lock()


def get_limiter(client_id: str, rate: float) -> RateLimiter:
    """同一 Client-Id 在同一进程内共享一个限流器。"""
    with _registry_lock:
        limiter = _limiters.get(client_id)
        if limiter is None or limiter.rate != rate:
            limiter = _limiters[client_id] = RateLimiter(rate)
        return limiter
