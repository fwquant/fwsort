"""
统一配置入口：所有非敏感配置从数据库读取

架构：
  .env                  → 仅存敏感/启动必需项（API密钥、密码、DB连接）
  config/*.yaml         → 默认值种子（首次写入数据库后不再使用）
  代码 fallback         → 最后兜底

  运行时配置流：
  Settings 初始化 → 从 .env 加载敏感项 → 启动后从数据库覆盖所有非敏感项
"""

import os
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"
load_dotenv()


def _env_or(key: str, default: Any = None) -> Any:
    val = os.environ.get(key)
    if val is not None and val != "":
        return val
    return default


class Settings(BaseSettings):
    """全局配置类

    非敏感配置（端口、模式、交易参数等）运行时从数据库读取。
    敏感配置（密钥、密码）仅从 .env 读取，不走数据库。
    """

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ====== 应用基础（默认值 → 数据库覆盖）======
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
    APP_CORS_ORIGINS: str = ""
    APP_ALLOW_INIT: bool = True
    APP_DEMO_MODE: bool = True
    APP_DEMO_SQLITE_PATH: str = "./data/fwsort_demo.db"
    APP_DEMO_REDIS_PREFIX: str = "fwsort_demo:"
    APP_LOGIN_RATE_LIMIT: int = 5
    APP_LOGIN_LOCK_MINUTES: int = 0
    APP_REGISTER_RATE_LIMIT: int = 3

    # ====== 数据库（敏感 → .env；路径/模式 → 数据库覆盖）======
    USE_SQLITE: bool = True
    SQLITE_PATH: str = "./data/fwsort.db"
    USE_FAKE_REDIS: bool = True

    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "fwsort"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = "fwsort"
    POSTGRES_POOL_SIZE: int = 10
    POSTGRES_MAX_OVERFLOW: int = 20

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""

    ES_HOST: str = "http://localhost:9200"
    ES_INDEX_ORDER_LOG: str = "order_execution_log"

    # ====== AI 智能体（密钥 → .env；模型名 → 数据库覆盖）======
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-20241022"
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.0-flash"
    HERMES_MOA_ENABLED: bool = True
    HERMES_MOA_LAYERS: int = 2

    # ====== 交易（密钥 → .env；模式/参数 → 数据库覆盖）======
    TRADE_MODE: Literal["simulator", "live"] = "live"

    OKX_API_KEY: str = ""
    OKX_SECRET: str = ""
    OKX_PASSPHRASE: str = ""
    OKX_FLAG: int = 1
    OKX_SERVER: str = "DEMO"

    POLYMARKET_APIKEY: str = ""
    POLYMARKET_SECRET: str = ""
    POLYMARKET_PASSPHRASE: str = ""
    POLYMARKET_PRIVATE_KEY: str = ""
    POLYMARKET_WALLET_ADDRESS: str = ""

    # ====== RELAyer polymarket 配置======
    POLYMARKET_RELAYER_API_KEY_ADDRESS: str = os.getenv("POLYMARKET_RELAYER_API_KEY_ADDRESS", "")
    POLYMARKET_RELAYER_API_KEY: str = os.getenv("POLYMARKET_RELAYER_API_KEY", "")
    POLYMARKET_RELAYER_PRIVATE_KEY: str = os.getenv("POLYMARKET_RELAYER_PRIVATE_KEY", "")

    POLYMARKET_HOST: str = "MAINNET"
    POLYMARKET_CHAIN: str = "polygon"
    POLYMARKET_HTTP_TIMEOUT: float = 10.0
    POLYMARKET_ORDER_RETRY: int = 2

    POLYMARKET_BTC5M_ENABLED: bool = False
    POLYMARKET_BTC5M_SLUG_PREFIX: str = "btc-updown-5m"
    POLYMARKET_BTC5M_POLL_SECONDS: int = 300
    POLYMARKET_BTC5M_DEFAULT_SIDE: str = "UP"
    POLYMARKET_BTC5M_DEFAULT_AMOUNT_USD: float = 5.0
    POLYMARKET_BTC5M_AUTO_ORDER: bool = False
    POLYMARKET_BTC5M_PRICE_FLOOR: float = 0.05
    POLYMARKET_BTC5M_PRICE_CAP: float = 0.95
    POLYMARKET_BTC5M_MAX_AMOUNT_USD: float = 50.0
    POLYMARKET_BTC5M_MAX_OPEN_ORDERS: int = 5
    POLYMARKET_BTC5M_ORDER_TTL_SECONDS: int = 86400

    # ====== 下单与风控 → 数据库覆盖 ======
    ORDER_BASE_USD: float = 5.0
    ORDER_DOUBLE_USD: float = 10.0
    PREDICTION_TIMEFRAME: str = "15m"
    RISK_SINGLE_RATIO: float = 0.20
    RISK_DAILY_LOSS_RATIO: float = 0.30

    # ====== 榜单权重 → 数据库覆盖 ======
    WEIGHT_ANNUALIZED: float = 0.30
    WEIGHT_DRAWDOWN: float = 0.20
    WEIGHT_SHARPE: float = 0.20
    WEIGHT_PROFIT_LOSS: float = 0.15
    WEIGHT_EXECUTION: float = 0.15

    # ====== 数据保留 → 数据库覆盖 ======
    ORDER_LOG_HOT_DAYS: int = 90
    RANK_SNAPSHOT_KEEP_DAYS: int = 3650

    # ====== 数据库动态覆盖 ======
    def override_from_db(self, configs: dict[str, Any]) -> None:
        for key, value in configs.items():
            key_upper = key.upper()
            if not hasattr(self, key_upper):
                continue
            try:
                current = getattr(self, key_upper)
                if isinstance(current, bool):
                    setattr(self, key_upper, bool(value))
                elif isinstance(current, int):
                    setattr(self, key_upper, int(value))
                elif isinstance(current, float):
                    setattr(self, key_upper, float(value))
                else:
                    setattr(self, key_upper, str(value))
            except (ValueError, TypeError):
                pass

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def postgres_async_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def sync_dsn(self) -> str:
        if self.USE_SQLITE:
            return f"sqlite:///{self.SQLITE_PATH}"
        return self.postgres_dsn

    @property
    def async_dsn(self) -> str:
        if self.USE_SQLITE:
            return f"sqlite+aiosqlite:///{self.SQLITE_PATH}"
        return self.postgres_async_dsn

    @property
    def is_simulator(self) -> bool:
        return self.TRADE_MODE == "simulator"

    @property
    def polymarket_configured(self) -> bool:
        return bool(self.POLYMARKET_PRIVATE_KEY and self.POLYMARKET_WALLET_ADDRESS)

    @property
    def polymarket_missing_keys(self) -> list[str]:
        missing: list[str] = []
        if not self.POLYMARKET_PRIVATE_KEY:
            missing.append("POLYMARKET_WALLET_PRIVATE_KEY")
        if not self.POLYMARKET_WALLET_ADDRESS:
            missing.append("POLYMARKET_WALLET_ADDRESS")
        if not self.POLYMARKET_APIKEY:
            missing.append("POLYMARKET_API_KEY")
        if not self.POLYMARKET_SECRET:
            missing.append("POLYMARKET_SECRET")
        if not self.POLYMARKET_PASSPHRASE:
            missing.append("POLYMARKET_PASSPHRASE")
        return missing

    @property
    def btc5m_enabled_effective(self) -> bool:
        return (
                self.POLYMARKET_BTC5M_ENABLED
                and self.polymarket_configured
                and not self.is_simulator
        )


settings = Settings()


def reload_env() -> int:
    """重新加载 .env 文件到 settings 单例，返回变更数量"""
    import os as _os
    from dotenv import load_dotenv
    changes = 0
    try:
        load_dotenv(str(_ENV_FILE), override=True)
    except Exception:
        pass
    for key in dir(settings):
        if key.startswith("_") or key.upper() != key:
            continue
        if key in _SENSITIVE_KEYS:
            continue
        env_val = _os.environ.get(key)
        if env_val is None or env_val == "":
            continue
        try:
            current = getattr(settings, key)
            if isinstance(current, bool):
                new_val = env_val.lower() in ("1", "true", "yes", "on")
            elif isinstance(current, int):
                new_val = int(env_val)
            elif isinstance(current, float):
                new_val = float(env_val)
            else:
                new_val = env_val
            if str(new_val) != str(current):
                setattr(settings, key, new_val)
                changes += 1
        except (ValueError, TypeError):
            pass
    for skey in _SENSITIVE_KEYS:
        env_val = _os.environ.get(skey)
        if env_val and env_val != getattr(settings, skey, ""):
            setattr(settings, skey, env_val)
            changes += 1
    return changes


_SENSITIVE_KEYS = {
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
    "POSTGRES_PASSWORD", "REDIS_PASSWORD",
    "OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE",
    "POLYMARKET_PRIVATE_KEY", "POLYMARKET_SECRET", "POLYMARKET_PASSPHRASE",
    "POLYMARKET_RELAYER_API_KEY", "POLYMARKET_RELAYER_PRIVATE_KEY",
    "POLYMARKET_RELAYER_API_KEY_ADDRESS",
}


def _build_default_seed() -> dict[str, dict]:
    """构建默认值种子：代码中所有非敏感字段的默认值"""
    seed = {}
    for key in dir(settings):
        if key.startswith("_") or key.upper() != key:
            continue
        if key in _SENSITIVE_KEYS:
            continue
        val = getattr(settings, key)
        if callable(val):
            continue
        vtype = "str"
        if isinstance(val, bool):
            vtype = "bool"
        elif isinstance(val, int):
            vtype = "int"
        elif isinstance(val, float):
            vtype = "float"
        elif isinstance(val, (list, dict)):
            vtype = "json"
            val = str(val) if not isinstance(val, str) else val
        seed[key] = {
            "default_value": str(val),
            "value_type": vtype,
            "group": _guess_group(key),
            "description": "",
        }
    return seed


def _guess_group(key: str) -> str:
    k = key.upper()
    groups = []
    if k.startswith("APP_") or k in ("USE_SQLITE", "SQLITE_PATH", "USE_FAKE_REDIS"):
        groups.append("app")
    if k.startswith("POSTGRES") or k.startswith("REDIS") or k.startswith("ES_"):
        groups.append("database")
    if k.startswith("OPENAI") or k.startswith("ANTHROPIC") or k.startswith("GEMINI") or k.startswith("HERMES"):
        groups.append("ai")
    if k.startswith("TRADE") or k.startswith("ORDER_") or k.startswith("RISK_") or k.startswith("PREDICTION_"):
        groups.append("trading")
    if k.startswith("OKX"):
        groups.append("exchange")
    if k.startswith("POLYMARKET"):
        groups.append("exchange")
        groups.append("polymarket")
    if k.startswith("WEIGHT") or k.startswith("RANK"):
        groups.append("weights")
    if k.startswith("POLYMARKET_BTC5M"):
        if "polymarket" not in groups:
            groups.append("polymarket")
    if k in ("ORDER_LOG_HOT_DAYS", "RANK_SNAPSHOT_KEEP_DAYS"):
        groups.append("retention")
    if not groups:
        groups.append("general")
    return ",".join(groups)


async def init_config_from_db() -> None:
    """启动时：种子默认值到数据库 → 加载数据库配置覆盖 settings → 同步 ENV 到 DB

    规则：
        1. 代码默认值 → 种子写入数据库（幂等）
        2. 数据库 config_value 非空 → 覆盖 settings 运行时值
        3. 对于敏感键（API Key/地址等）：
           - 若 .env 有值 且 数据库 config_value 为空  → 自动同步 ENV → DB
           - 保证：用户只需配置 .env，首次启动后数据库即有记录
    """
    try:
        from fwsort.config_service import seed_defaults, load_all_from_db, sync_env_to_db
        from loguru import logger

        defaults = _build_default_seed()
        seeded = await seed_defaults(defaults)
        if seeded:
            logger.info(f"📊 已种子 {seeded} 条默认配置到数据库")

        # 同步 ENV 中敏感键到数据库（DB 为空时自动写入）
        synced = await sync_env_to_db(_SENSITIVE_KEYS)
        if synced:
            logger.info(f"📊 ENV → DB 自动同步 {synced} 条敏感配置（如 POLYMARKET_RELAYER_API_KEY_ADDRESS 等）")

        db_configs = await load_all_from_db()
        if db_configs:
            settings.override_from_db(db_configs)
            logger.info(
                f"📊 数据库配置已加载: {len(db_configs)} 项 | APP_PORT={settings.APP_PORT} | TRADE_MODE={settings.TRADE_MODE}")
    except Exception as e:
        from loguru import logger
        logger.warning(f"从数据库加载配置失败（将使用代码默认值）: {e}")


if __name__ == '__main__':
    print(f"SQLITE_PATH: {settings.SQLITE_PATH}")
    print(f"APP_PORT: {settings.APP_PORT}")
    print(f"TRADE_MODE: {settings.TRADE_MODE}")
