# 配置管理：基于 pydantic-settings 统一加载 .env
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置类（单例）"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # 应用
    APP_NAME: str = "fwsort"
    APP_ENV: Literal["development", "staging", "production"] = "development"
    APP_DEBUG: bool = True
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    APP_SECRET_KEY: str = "change-me"
    APP_JWT_ALGORITHM: str = "HS256"
    APP_JWT_ACCESS_TTL_MIN: int = 30
    APP_JWT_REFRESH_TTL_DAYS: int = 7

    # 轻量模式（无外部服务时使用 SQLite + 内存 Redis）
    USE_SQLITE: bool = False
    SQLITE_PATH: str = "./fwsort.db"
    USE_FAKE_REDIS: bool = False

    # PostgreSQL
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "fwsort"
    POSTGRES_PASSWORD: str = "fwsort_pwd"
    POSTGRES_DB: str = "fwsort"
    POSTGRES_POOL_SIZE: int = 10
    POSTGRES_MAX_OVERFLOW: int = 20

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""

    # Elasticsearch
    ES_HOST: str = "http://localhost:9200"
    ES_INDEX_ORDER_LOG: str = "order_execution_log"

    # AI 智能体（V1.0 多智能体策略）
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-20241022"
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.0-flash"
    HERMES_MOA_ENABLED: bool = True
    HERMES_MOA_LAYERS: int = 2

    # 交易
    TRADE_MODE: Literal["simulator", "live"] = "simulator"
    POLYMARKET_API_KEY: str = ""
    POLYMARKET_WALLET_PRIVATE_KEY: str = ""
    POLYMARKET_CHAIN: str = "polygon"
    OKX_API_KEY: str = ""
    OKX_SECRET: str = ""
    OKX_PASSPHRASE: str = ""
    OKX_FLAG: int = 1  # 0=实盘 1=模拟盘

    # 下单与风控（V1.0 规则）
    ORDER_BASE_USD: float = 5.0
    ORDER_DOUBLE_USD: float = 10.0
    PREDICTION_TIMEFRAME: str = "15m"
    RISK_SINGLE_RATIO: float = 0.20
    RISK_DAILY_LOSS_RATIO: float = 0.30

    # 榜单权重
    WEIGHT_ANNUALIZED: float = 0.30
    WEIGHT_DRAWDOWN: float = 0.20
    WEIGHT_SHARPE: float = 0.20
    WEIGHT_PROFIT_LOSS: float = 0.15
    WEIGHT_EXECUTION: float = 0.15

    # 数据保留
    ORDER_LOG_HOT_DAYS: int = 90
    RANK_SNAPSHOT_KEEP_DAYS: int = 3650

    @property
    def postgres_dsn(self) -> str:
        """同步数据库连接串（用于 SQLAlchemy + Celery）"""
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def postgres_async_dsn(self) -> str:
        """异步数据库连接串（用于 FastAPI async 路由）"""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def sync_dsn(self) -> str:
        """同步 DSN（SQLite 或 PostgreSQL）"""
        if self.USE_SQLITE:
            return f"sqlite:///{self.SQLITE_PATH}"
        return self.postgres_dsn

    @property
    def async_dsn(self) -> str:
        """异步 DSN（SQLite 或 PostgreSQL）"""
        if self.USE_SQLITE:
            return f"sqlite+aiosqlite:///{self.SQLITE_PATH}"
        return self.postgres_async_dsn

    @property
    def is_simulator(self) -> bool:
        """是否模拟盘模式"""
        return self.TRADE_MODE == "simulator"


@lru_cache
def get_settings() -> Settings:
    """配置单例（lru_cache 保证全局唯一）"""
    return Settings()


settings = get_settings()
