"""测试公共夹具：每个测试使用独立的内存 SQLite，密钥使用临时值。"""
import os

from cryptography.fernet import Fernet

# 必须在导入 app 之前设置：应用启动时会校验密钥
_FERNET_KEY = Fernet.generate_key().decode()
_JWT_SECRET = "test-secret-test-secret-test-secret"
os.environ.update(FERNET_KEY=_FERNET_KEY, JWT_SECRET=_JWT_SECRET, DATABASE_URL="sqlite://")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db.models import Base
from app.db.session import get_db


@pytest.fixture(autouse=True)
def _test_settings(monkeypatch):
    monkeypatch.setenv("FERNET_KEY", _FERNET_KEY)
    monkeypatch.setenv("JWT_SECRET", _JWT_SECRET)
    # 已有测试按“需要登录”运行，免登录模式另有专门测试
    monkeypatch.setenv("LOGIN_REQUIRED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    # SQLite 默认不校验外键，开启以贴近 PostgreSQL 行为
    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    session.factory = factory  # 供需要独立会话的代码（如 api_logs 写入）使用
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def api(db, monkeypatch):
    """已登录的测试客户端；所有数据库访问都指向测试库。"""
    from app.main import app
    from app.services.auth import create_user, login_throttle

    monkeypatch.setattr("app.services.shops.SessionLocal", db.factory)
    monkeypatch.setattr("app.services.task_log.SessionLocal", db.factory)
    monkeypatch.setattr("app.tasks.catalog.SessionLocal", db.factory)
    monkeypatch.setattr("app.tasks.orders.SessionLocal", db.factory)
    monkeypatch.setattr("app.tasks.stock.SessionLocal", db.factory)
    monkeypatch.setattr("app.tasks.market.SessionLocal", db.factory)
    monkeypatch.setattr("app.api.market.SessionLocal", db.factory)

    def _get_db():
        s = db.factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db
    create_user(db, "admin", "password123")
    client = TestClient(app)
    token = client.post("/api/auth/login",
                        data={"username": "admin", "password": "password123"}).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    yield client
    app.dependency_overrides.clear()
    login_throttle._failures.clear()
