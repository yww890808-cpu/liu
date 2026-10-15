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

    # 选品：第三方数据服务 MPStats（https://mpstats.io/userpanel 的 API token）
    mpstats_token: str = ""
    mpstats_base_url: str = "https://mpstats.io/api/"
    mpstats_rate_limit_per_sec: float = 1.0
    mpstats_include_fbs: bool = True  # 统计中包含 FBS 卖家（跨境卖家多为 FBS）
    market_fetch_hour: int = 5  # 每天几点（UTC）抓取前一天的数据
    market_backfill_days: int = 14  # 新增类目时回补的天数
    # 备货后 Ozon 需要一段时间生成面单：首次获取延迟与最多尝试次数
    label_fetch_delay_sec: int = 60
    label_fetch_max_tries: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()


def check_secrets(settings: Settings | None = None) -> None:
    """启动时校验必填密钥，配置错误时给出明确提示而不是运行到一半才报错。"""
    from cryptography.fernet import Fernet

    settings = settings or get_settings()
    problems = []
    if not settings.fernet_key:
        problems.append("FERNET_KEY 未设置")
    else:
        try:
            Fernet(settings.fernet_key.encode())
        except (ValueError, TypeError):
            problems.append("FERNET_KEY 格式不正确（应为 44 位 base64 字符串，按 .env.example 中的命令生成）")
    if len(settings.jwt_secret) < 16:
        problems.append("JWT_SECRET 未设置或太短（至少 16 位）")
    if problems:
        raise RuntimeError("配置错误：" + "；".join(problems) + "。请检查 .env 文件。")
