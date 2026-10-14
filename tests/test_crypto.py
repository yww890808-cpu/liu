"""Api-Key 加密与脱敏。"""
import pytest

from app.config import get_settings
from app.utils import crypto


def test_encrypt_roundtrip():
    token = crypto.encrypt("my-api-key")
    assert token != "my-api-key"
    assert crypto.decrypt(token) == "my-api-key"


def test_missing_key_raises(monkeypatch):
    monkeypatch.setenv("FERNET_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="FERNET_KEY"):
        crypto.encrypt("x")
    get_settings.cache_clear()


def test_mask_value():
    assert crypto.mask("abcd1234efgh") == "abcd****"
    assert crypto.mask("") == ""
