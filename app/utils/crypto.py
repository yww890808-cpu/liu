"""Api-Key 加解密与脱敏。"""
from cryptography.fernet import Fernet

from app.config import get_settings


def _fernet() -> Fernet:
    key = get_settings().fernet_key
    if not key:
        raise RuntimeError("未配置 FERNET_KEY，无法加解密 Api-Key")
    return Fernet(key.encode())


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def mask(secret: str | None) -> str:
    """日志里只显示前 4 位，避免泄露完整 Api-Key。"""
    if not secret:
        return ""
    return secret[:4] + "****"
