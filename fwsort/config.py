# 配置管理：基于 pydantic-settings 统一加载 .env
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# 解析项目根目录（fwsort 包的上一级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """全局配置类（单例）"""

    # 加载环境变量 使用了 SettingsConfigDict 来解析 .env 文件中的配置
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用
    APP_NAME: str = "fwsort"
    APP_ENV: Literal["development", "staging", "production"] = "development"
    APP_DEBUG: bool = True
    APP_RELOAD: bool = False
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8002
    APP_SECRET_KEY: str = "change-me"
    APP_JWT_ALGORITHM: str = "HS256"
    APP_JWT_ACCESS_TTL_MIN: int = 10080
    APP_JWT_REFRESH_TTL_DAYS: int = 7

    # CORS 收敛：生产环境必须显式白名单（避免与 allow_credentials 冲突导致 CSRF）
    # 配置示例：APP_CORS_ORIGINS="https://app.fwquant.com,https://admin.fwquant.com"
    # 留空或 "*" 表示完全开放（仅开发模式使用）
    APP_CORS_ORIGINS: str = ""

    # 管理员初始化放行开关：仅在首次启动无 admin 时允许未授权调用 init/seed
    APP_ALLOW_INIT: bool = True

    # WP-06：演示模式数据层隔离
    # APP_DEMO_MODE=True 时，所有 /api/demo/* 走独立 SQLite + 内存 Redis（与生产物理隔离）
    APP_DEMO_MODE: bool = True
    APP_DEMO_SQLITE_PATH: str = "./data/fwsort_demo.db"
    APP_DEMO_REDIS_PREFIX: str = "fwsort_demo:"  # 隔离 key 命名空间（共享 Redis 时使用）

    # 限流配置（WP-03）
    APP_LOGIN_RATE_LIMIT: int = 5           # 5 次失败
    APP_LOGIN_LOCK_MINUTES: int = 0         # 0 = 不锁定（默认关闭登录锁定）
    APP_REGISTER_RATE_LIMIT: int = 3        # 同 IP 3 次/小时

    # 轻量模式（无外部服务时使用 SQLite + 内存 Redis）
    USE_SQLITE: bool = True
    SQLITE_PATH: str = "./data/fwsort.db"
    USE_FAKE_REDIS: bool = True

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
    TRADE_MODE: Literal["simulator", "live"] = "live"
    POLYMARKET_APIKEY: str = ""
    POLYMARKET_PRIVATE_KEY: str = ""
    POLYMARKET_WALLET_ADDRESS: str = ""
    POLYMARKET_CHAIN: str = "polygon"
    OKX_API_KEY: str = ""
    OKX_SECRET: str = ""
    OKX_PASSPHRASE: str = ""
    OKX_FLAG: int = 1  # 0=实盘 1=模拟盘
    OKX_SERVER: str = "DEMO"  # DEMO=演示, LIVE=实盘

    # Polymarket F3 Relayer Gasless 密钥（⚠️ 敏感信息，仅在 .env 中配置）
    POLYMARKET_RELAYER_API_KEY_ADDRESS: str = ""
    POLYMARKET_RELAYER_API_KEY: str = ""
    POLYMARKET_RELAYER_PRIVATE_KEY: str = ""

    # ====== Polymarket 网关模块（BTC 5min 短期涨跌市场）集中配置 ======
    # 说明：所有 Polymarket 网关相关参数集中在此区段，便于统一管理
    # ⚠️ 密钥（POLYMARKET_WALLET_PRIVATE_KEY / POLYMARKET_API_KEY / POLYMARKET_WALLET_ADDRESS）
    #     请务必填入 .env，未填则网关仅能查询公开行情，无法下单/查余额
    # 网关主机：MAINNET(主网) / GOERLI(测试) / MOCK(模拟)
    POLYMARKET_HOST: str = "MAINNET"
    # HTTP 超时（秒）
    POLYMARKET_HTTP_TIMEOUT: float = 10.0
    # 下单重试次数（网络瞬时错误时）
    POLYMARKET_ORDER_RETRY: int = 2

    # ---- BTC 5min 子模块（每 5 分钟到期的 BTC 涨跌预测市场）----
    # 是否启用 BTC 5min 自动轮询下单
    POLYMARKET_BTC5M_ENABLED: bool = False
    # BTC 5min 市场 tag 前缀（用于在 Gamma /markets 接口通过 tag 参数过滤活跃市场）
    # Polymarket 实际市场 slug 形如 "btc-updown-5m-1785480300"（末尾为 unix 时间戳）
    POLYMARKET_BTC5M_SLUG_PREFIX: str = "btc-updown-5m"
    # 轮询/自动下单节奏（秒），300=5分钟
    POLYMARKET_BTC5M_POLL_SECONDS: int = 300
    # 默认下单方向：UP / DOWN
    POLYMARKET_BTC5M_DEFAULT_SIDE: str = "UP"
    # 默认下单金额（USD）
    POLYMARKET_BTC5M_DEFAULT_AMOUNT_USD: float = 5.0
    # 是否启用自动下单（关闭则仅轮询查询不下单）
    POLYMARKET_BTC5M_AUTO_ORDER: bool = False
    # 价格边界（避免极端价格成交）
    POLYMARKET_BTC5M_PRICE_FLOOR: float = 0.05
    POLYMARKET_BTC5M_PRICE_CAP: float = 0.95
    # 单笔最大金额（USD）风控上限
    POLYMARKET_BTC5M_MAX_AMOUNT_USD: float = 50.0
    # 同时最大持仓订单数
    POLYMARKET_BTC5M_MAX_OPEN_ORDERS: int = 5
    # 订单过期秒数（默认 24h，5min 市场可设短）
    POLYMARKET_BTC5M_ORDER_TTL_SECONDS: int = 86400

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

    @property
    def polymarket_configured(self) -> bool:
        """Polymarket 网关密钥是否齐备（私钥 + 钱包地址）"""
        return bool(self.POLYMARKET_PRIVATE_KEY and self.POLYMARKET_WALLET_ADDRESS)

    @property
    def polymarket_missing_keys(self) -> list[str]:
        """Polymarket 网关缺失的密钥列表（用于启动时醒目提醒）"""
        missing: list[str] = []
        if not self.POLYMARKET_PRIVATE_KEY:
            missing.append("POLYMARKET_WALLET_PRIVATE_KEY")
        if not self.POLYMARKET_WALLET_ADDRESS:
            missing.append("POLYMARKET_WALLET_ADDRESS")
        if not self.POLYMARKET_APIKEY:
            missing.append("POLYMARKET_API_KEY")
        return missing

    @property
    def btc5m_enabled_effective(self) -> bool:
        """BTC 5min 自动下单实际是否生效（启用 + 密钥齐备 + 非模拟盘）"""
        return (
            self.POLYMARKET_BTC5M_ENABLED
            and self.polymarket_configured
            and not self.is_simulator
        )


@lru_cache
def get_settings() -> Settings:
    """配置单例（lru_cache 保证全局唯一）"""
    return Settings()


settings = get_settings()

if __name__ == '__main__':
    print(f"SQLITE_PATH: {settings.SQLITE_PATH}")

    pass