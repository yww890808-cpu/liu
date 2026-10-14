"""全局配置：从环境变量 / .env 读取。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./dev.db"
    redis_url: str = "redis://localhost:6379/0"

    # 密钥：没有默认值的生产必填项，开发时为空会在使用处报错
    fernet_key: str = ""
    jwt_secret: str = ""
    jwt_expire_minutes: int = 720

    # Ozon API
    ozon_base_url: str = "https://api-seller.ozon.ru"
    ozon_rate_limit_per_sec: float = 5.0
    ozon_max_retries: int = 3
    ozon_timeout_sec: float = 30.0

    # 试运行模式（默认开启）：写操作只记录不提交
    dry_run: bool = True

    # 定时任务
    order_sync_interval_minutes: int = 10
    order_sync_lookback_days: int = 3  # 拉取最近 N 天内状态有变化的订单
    order_sync_created_window_days: int = 90  # 只看最近 N 天内创建的订单（Ozon 要求必须给创建时间范围）
    stock_push_interval_minutes: int = 30
    product_sync_hour: int = 3

    label_dir: str = "./data/labels"
    static_dir: str = "./frontend/dist"  # 前端构建产物目录
    # 备货后 Ozon 需要一段时间生成面单：首次获取延迟与最多尝试次数
    label_fetch_delay_sec: int = 60
    label_fetch_max_tries: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
