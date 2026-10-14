"""登录鉴权：bcrypt 密码哈希 + JWT。"""
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
