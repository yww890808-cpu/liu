"""登录鉴权：bcrypt 密码哈希 + JWT。"""
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

import bcrypt
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import User, utcnow

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def _secret() -> str:
    secret = get_settings().jwt_secret
    if not secret:
        raise RuntimeError("未配置 JWT_SECRET")
    return secret


def create_token(user: User) -> str:
    exp = utcnow() + timedelta(minutes=get_settings().jwt_expire_minutes)
    return jwt.encode({"sub": str(user.id), "exp": exp}, _secret(), algorithm=ALGORITHM)


def user_from_token(db: Session, token: str) -> User | None:
    try:
        payload = jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    return db.get(User, int(payload["sub"]))


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username))
    if user and verify_password(password, user.password_hash):
        return user
    return None


def create_user(db: Session, username: str, password: str) -> User:
    user = User(username=username, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    return user


class LoginThrottle:
    """登录失败限流（进程内）：同一用户名或同一 IP 在窗口期内失败过多次后暂时禁止登录。"""

    def __init__(self, max_failures: int = 5, window_sec: int = 900, clock=time.monotonic):
        self.max_failures = max_failures
        self.window_sec = window_sec
        self._clock = clock
        self._failures: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _recent(self, key: str) -> deque:
        q = self._failures[key]
        cutoff = self._clock() - self.window_sec
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def blocked(self, *keys: str) -> bool:
        with self._lock:
            return any(len(self._recent(k)) >= self.max_failures for k in keys)

    def fail(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._recent(k).append(self._clock())

    def success(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._failures.pop(k, None)


login_throttle = LoginThrottle()
