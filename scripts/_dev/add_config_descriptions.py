"""为所有配置添加中文描述"""
import sqlite3

DB = "./data/fwsort.db"
c = sqlite3.connect(DB).cursor()

DESCRIPTIONS = {
    # AI 模型
    "OPENAI_MODEL": "GPT 模型名称",
    "ANTHROPIC_MODEL": "Claude 模型名称",
    "GEMINI_MODEL": "Gemini 模型名称",
    "HERMES_MOA_ENABLED": "是否启用 Hermes MoA 聚合层",
    "HERMES_MOA_LAYERS": "Hermes MoA 聚合层数",

    # 应用
    "APP_ALLOW_INIT": "是否允许首次无管理员时初始化",
    "APP_CORS_ORIGINS": "CORS 允许来源（逗号分隔）",
    "APP_DEBUG": "调试模式开关",
    "APP_DEMO_MODE": "演示模式开关",
    "APP_DEMO_REDIS_PREFIX": "演示模式 Redis 前缀",
    "APP_DEMO_SQLITE_PATH": "演示模式 SQLite 文件路径",
    "APP_ENV": "运行环境（development/production）",
    "APP_HOST": "服务监听地址",
    "APP_JWT_ACCESS_TTL_MIN": "JWT Access Token 有效期（分钟）",
    "APP_JWT_ALGORITHM": "JWT 签名算法",
    "APP_JWT_REFRESH_TTL_DAYS": "JWT Refresh Token 有效期（天）",
    "APP_LOGIN_LOCK_MINUTES": "登录失败锁定时长（分钟），0 表示关闭",
    "APP_LOGIN_RATE_LIMIT": "登录失败计数阈值",
    "APP_NAME": "应用名称",
    "APP_PORT": "服务端口",
    "APP_REGISTER_RATE_LIMIT": "注册频率限制（次/小时）",
    "APP_RELOAD": "是否启用自动重载",
    "APP_SECRET_KEY": "JWT 签名密钥",
    "SQLITE_PATH": "SQLite 数据库文件路径",
    "USE_FAKE_REDIS": "是否使用内存模拟 Redis",
    "USE_SQLITE": "是否使用 SQLite",

    # 数据库
    "ES_HOST": "Elasticsearch 主机地址",
    "ES_INDEX_ORDER_LOG": "订单日志 ES 索引名",
    "POSTGRES_DB": "PostgreSQL 数据库名",
    "POSTGRES_HOST": "PostgreSQL 主机",
    "POSTGRES_MAX_OVERFLOW": "连接池最大溢出连接数",
    "POSTGRES_POOL_SIZE": "连接池大小",
    "POSTGRES_PORT": "PostgreSQL 端口",
    "POSTGRES_USER": "PostgreSQL 用户名",
    "REDIS_DB": "Redis 数据库编号",
    "REDIS_HOST": "Redis 主机",
    "REDIS_PORT": "Redis 端口",

    # 交易所
    "OKX_FLAG": "OKX 交易对标志位",
    "OKX_SERVER": "OKX 服务器环境（DEMO/LIVE）",
    "POLYMARKET_APIKEY": "Polymarket API Key",
    "POLYMARKET_BTC5M_AUTO_ORDER": "BTC 5 分钟自动下单开关",
    "POLYMARKET_BTC5M_DEFAULT_AMOUNT_USD": "BTC 5 分钟默认下单金额（美元）",
    "POLYMARKET_BTC5M_DEFAULT_SIDE": "BTC 5 分钟默认方向",
    "POLYMARKET_BTC5M_ENABLED": "BTC 5 分钟轮询开关",
    "POLYMARKET_BTC5M_MAX_AMOUNT_USD": "BTC 5 分钟单笔最大金额（美元）",
    "POLYMARKET_BTC5M_MAX_OPEN_ORDERS": "BTC 5 分钟最大持仓订单数",
    "POLYMARKET_BTC5M_ORDER_TTL_SECONDS": "BTC 5 分钟订单有效期（秒）",
    "POLYMARKET_BTC5M_POLL_SECONDS": "BTC 5 分钟轮询间隔（秒）",
    "POLYMARKET_BTC5M_PRICE_CAP": "BTC 5 分钟价格上限",
    "POLYMARKET_BTC5M_PRICE_FLOOR": "BTC 5 分钟价格下限",
    "POLYMARKET_BTC5M_SLUG_PREFIX": "BTC 5 分钟市场 slug 前缀",
    "POLYMARKET_CHAIN": "Polymarket 区块链网络",
    "POLYMARKET_HOST": "Polymarket 环境（MAINNET/TESTNET）",
    "POLYMARKET_HTTP_TIMEOUT": "Polymarket HTTP 超时（秒）",
    "POLYMARKET_ORDER_RETRY": "Polymarket 下单重试次数",
    "POLYMARKET_RELAYER_API_KEY_ADDRESS": "Polymarket Relayer API Key 地址",
    "POLYMARKET_WALLET_ADDRESS": "Polymarket 代理钱包地址",

    # 交易
    "ORDER_BASE_USD": "基础下单金额（美元）",
    "ORDER_DOUBLE_USD": "双智能体同方向下单金额（美元）",
    "ORDER_LOG_HOT_DAYS": "订单日志热存保留天数",
    "PREDICTION_TIMEFRAME": "预测时间周期",
    "RISK_DAILY_LOSS_RATIO": "日亏损风控阈值（余额比例）",
    "RISK_SINGLE_RATIO": "单笔下单上限（余额比例）",
    "TRADE_MODE": "交易模式（live/simulator）",

    # 权重
    "RANK_SNAPSHOT_KEEP_DAYS": "榜单快照保留天数",
    "WEIGHT_ANNUALIZED": "年化收益率权重",
    "WEIGHT_DRAWDOWN": "回撤权重",
    "WEIGHT_EXECUTION": "执行质量权重",
    "WEIGHT_PROFIT_LOSS": "盈亏比权重",
    "WEIGHT_SHARPE": "夏普比率权重",
}

updated = 0
for key, desc in DESCRIPTIONS.items():
    r = c.execute("UPDATE system_config SET description=? WHERE config_key=?", (desc, key))
    if r.rowcount > 0:
        updated += 1

DB2 = "./data/fwsort_demo.db"
c2 = sqlite3.connect(DB2).cursor()
for key, desc in DESCRIPTIONS.items():
    c2.execute("UPDATE system_config SET description=? WHERE config_key=?", (desc, key))
c2.connection.commit()

c.connection.commit()
print(f"已更新 {updated} 条配置的中文描述")
print(f"演示库也已同步更新")