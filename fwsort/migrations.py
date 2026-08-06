# 数据库轻量级迁移（无需 alembic，幂等补列/补表）
from fwsort.fwlogs import logger
from sqlalchemy import inspect, text

from fwsort.database import sync_engine


# 本期新增字段：(table, column, sql_type, default_sql)
# default_sql 为 NULL 时表示"无默认值，nullable=True"
_PATCHES = [
    # ===== 20260729 交易员需求 =====
    ("user", "share_to_global", "BOOLEAN", "1"),
    ("user", "allow_follow", "BOOLEAN", "1"),
    ("user", "token_ttl_minutes", "INTEGER", "10080"),
    ("execution_account", "target_url", "VARCHAR(512)", None),
    ("execution_account", "target_symbol", "VARCHAR(64)", None),
    ("execution_account", "order_amount_usd", "NUMERIC(18,6)", "50.0"),
    ("execution_account", "signal", "VARCHAR(16)", "'NEUTRAL'"),
    ("execution_account", "signal_source", "VARCHAR(32)", "'random'"),
    ("execution_account", "signal_updated_at", "DATETIME", None),
    ("execution_account", "last_order_at", "DATETIME", None),
    ("execution_account", "public_enabled", "BOOLEAN", "1"),
    # ===== WP-05 软删除字段补列（老库升级）=====
    ("execution_account", "deleted_at", "DATETIME", None),
    # ===== 20260803 自动策略日志增强 =====
    ("auto_strategy_log", "log_type", "SMALLINT", "0"),
    ("auto_strategy_log", "action_type", "VARCHAR(32)", "''"),
    ("auto_strategy_log", "detail_json", "TEXT", "'{}'"),
    # ===== 20260805 自动策略：开始时间 + 循环次数 =====
    ("auto_strategy", "start_time", "DATETIME", None),
    ("auto_strategy", "loop_count", "INTEGER", "0"),
    ("auto_strategy", "executed_count", "INTEGER", "0"),
    # ===== 20260805 自动策略日志增强：信号/执行/结果详情 + 盈亏追踪 =====
    ("auto_strategy_log", "signal_detail_json", "TEXT", "'{}'"),
    ("auto_strategy_log", "execution_detail_json", "TEXT", "'{}'"),
    ("auto_strategy_log", "result_detail_json", "TEXT", "'{}'"),
    ("auto_strategy_log", "pnl_amount", "NUMERIC(18,6)", "0"),
    ("auto_strategy_log", "pnl_percent", "NUMERIC(10,4)", "0"),
    ("auto_strategy_log", "is_profit", "BOOLEAN", "0"),
    ("auto_strategy_log", "market_resolved", "BOOLEAN", "0"),
    # ===== 20260806 AutoStrategy 关联 ExecutionAccount + AutoStrategyLog 开平仓价格 =====
    ("auto_strategy", "account_id", "INTEGER", None),
    ("auto_strategy_log", "entry_price", "FLOAT", None),
    ("auto_strategy_log", "exit_price", "FLOAT", None),
    # ===== 20260806 表名重命名 + 新增资金统计字段 =====
    ("auto_strategy", "initial_balance", "NUMERIC(18,6)", "1000.0"),
    ("auto_strategy", "current_balance", "NUMERIC(18,6)", "1000.0"),
    ("auto_strategy", "total_pnl", "NUMERIC(18,6)", "0"),
    ("auto_strategy", "total_trades", "INTEGER", "0"),
    ("auto_strategy", "win_trades", "INTEGER", "0"),
    ("auto_strategy", "loss_trades", "INTEGER", "0"),
    ("auto_strategy", "win_rate", "FLOAT", "0"),
    ("auto_strategy", "max_drawdown", "FLOAT", "0"),
    ("auto_strategy", "sharpe_ratio", "FLOAT", "0"),
    ("auto_strategy", "profit_loss_ratio", "FLOAT", "0"),
]


# WP-03：本次新增的整张表（无法用 ADD COLUMN 表达，独立处理）
_NEW_TABLES_DDL: list[tuple[str, str]] = [
    # (table_name, create_ddl) - 第一条为 SQLite 语法，第二条为 PostgreSQL 语法
    (
        "login_attempt",
        """
        CREATE TABLE IF NOT EXISTS login_attempt (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email VARCHAR(128) NOT NULL,
            ip VARCHAR(64) NOT NULL,
            success BOOLEAN NOT NULL DEFAULT 0,
            user_agent VARCHAR(256) NOT NULL DEFAULT '',
            reason VARCHAR(64) NOT NULL DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    (
        "login_attempt",
        # PostgreSQL 变体
        """
        CREATE TABLE IF NOT EXISTS login_attempt (
            id BIGSERIAL PRIMARY KEY,
            email VARCHAR(128) NOT NULL,
            ip VARCHAR(64) NOT NULL,
            success BOOLEAN NOT NULL DEFAULT FALSE,
            user_agent VARCHAR(256) NOT NULL DEFAULT '',
            reason VARCHAR(64) NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    # ===== WP-09：outbox_event 表（事务一致性 + 异步投递 ES）=====
    (
        "outbox_event",
        """
        CREATE TABLE IF NOT EXISTS outbox_event (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type VARCHAR(64) NOT NULL DEFAULT 'order_log_index',
            payload_json TEXT NOT NULL,
            status SMALLINT NOT NULL DEFAULT 0,
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_error VARCHAR(512) NOT NULL DEFAULT '',
            next_retry_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    (
        "outbox_event",
        # PostgreSQL 变体
        """
        CREATE TABLE IF NOT EXISTS outbox_event (
            id BIGSERIAL PRIMARY KEY,
            event_type VARCHAR(64) NOT NULL DEFAULT 'order_log_index',
            payload_json TEXT NOT NULL,
            status SMALLINT NOT NULL DEFAULT 0,
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_error VARCHAR(512) NOT NULL DEFAULT '',
            next_retry_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    # ===== 20260806 策略交易明细表 =====
    (
        "strategy_trade",
        """
        CREATE TABLE IF NOT EXISTS strategy_trade (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_uid VARCHAR(64) NOT NULL UNIQUE,
            strategy_name VARCHAR(128) NOT NULL,
            auto_strategy_id INTEGER,
            account_id INTEGER,
            source_strategy VARCHAR(64) DEFAULT '',
            platform VARCHAR(32) NOT NULL,
            symbol VARCHAR(64) DEFAULT '',
            market_question VARCHAR(512) DEFAULT '',
            market_slug VARCHAR(128) DEFAULT '',
            direction VARCHAR(16) DEFAULT '',
            side SMALLINT NOT NULL,
            order_type SMALLINT DEFAULT 2,
            order_id VARCHAR(128) DEFAULT '',
            entry_price FLOAT NOT NULL,
            exit_price FLOAT,
            quantity FLOAT DEFAULT 0,
            amount_usd FLOAT NOT NULL,
            pnl_amount FLOAT DEFAULT 0,
            pnl_percent FLOAT DEFAULT 0,
            is_profit BOOLEAN DEFAULT 0,
            is_win BOOLEAN DEFAULT 0,
            entry_at DATETIME NOT NULL,
            exit_at DATETIME,
            hold_duration_seconds INTEGER DEFAULT 0,
            status SMALLINT DEFAULT 0,
            market_resolved BOOLEAN DEFAULT 0,
            resolved_at DATETIME,
            slippage FLOAT DEFAULT 0,
            latency_ms INTEGER DEFAULT 0,
            execution_detail_json TEXT DEFAULT '{}',
            result_detail_json TEXT DEFAULT '{}',
            deleted_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    (
        "strategy_trade",
        # PostgreSQL 变体
        """
        CREATE TABLE IF NOT EXISTS strategy_trade (
            id BIGSERIAL PRIMARY KEY,
            trade_uid VARCHAR(64) NOT NULL UNIQUE,
            strategy_name VARCHAR(128) NOT NULL,
            auto_strategy_id BIGINT,
            account_id BIGINT,
            source_strategy VARCHAR(64) DEFAULT '',
            platform VARCHAR(32) NOT NULL,
            symbol VARCHAR(64) DEFAULT '',
            market_question VARCHAR(512) DEFAULT '',
            market_slug VARCHAR(128) DEFAULT '',
            direction VARCHAR(16) DEFAULT '',
            side SMALLINT NOT NULL,
            order_type SMALLINT DEFAULT 2,
            order_id VARCHAR(128) DEFAULT '',
            entry_price FLOAT NOT NULL,
            exit_price FLOAT,
            quantity FLOAT DEFAULT 0,
            amount_usd FLOAT NOT NULL,
            pnl_amount FLOAT DEFAULT 0,
            pnl_percent FLOAT DEFAULT 0,
            is_profit BOOLEAN DEFAULT FALSE,
            is_win BOOLEAN DEFAULT FALSE,
            entry_at TIMESTAMP NOT NULL,
            exit_at TIMESTAMP,
            hold_duration_seconds INTEGER DEFAULT 0,
            status SMALLINT DEFAULT 0,
            market_resolved BOOLEAN DEFAULT FALSE,
            resolved_at TIMESTAMP,
            slippage FLOAT DEFAULT 0,
            latency_ms INTEGER DEFAULT 0,
            execution_detail_json TEXT DEFAULT '{}',
            result_detail_json TEXT DEFAULT '{}',
            deleted_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    # ===== 20260806 策略净值曲线表 =====
    (
        "strategy_equity_curve",
        """
        CREATE TABLE IF NOT EXISTS strategy_equity_curve (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_name VARCHAR(128) NOT NULL,
            auto_strategy_id INTEGER,
            account_id INTEGER,
            snapshot_date DATETIME NOT NULL,
            equity FLOAT NOT NULL,
            balance FLOAT NOT NULL,
            daily_pnl FLOAT DEFAULT 0,
            daily_pnl_percent FLOAT DEFAULT 0,
            peak_equity FLOAT DEFAULT 0,
            drawdown FLOAT DEFAULT 0,
            drawdown_percent FLOAT DEFAULT 0,
            max_drawdown_percent FLOAT DEFAULT 0,
            position_count INTEGER DEFAULT 0,
            trade_count INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    # ===== 20260807 风控模块独立（4 张新表）=====
    (
        "risk_profile",
        """
        CREATE TABLE IF NOT EXISTS risk_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(64) NOT NULL,
            owner_id INTEGER,
            is_default BOOLEAN DEFAULT FALSE,
            description VARCHAR(512) DEFAULT '',
            is_active BOOLEAN DEFAULT TRUE,
            risk_single_ratio NUMERIC(10,4),
            risk_daily_loss_ratio NUMERIC(10,4),
            max_daily_amount NUMERIC(18,6),
            max_daily_count INTEGER,
            max_consecutive_failures INTEGER,
            max_drawdown_ratio NUMERIC(10,4),
            max_open_positions INTEGER,
            stop_loss_ratio NUMERIC(10,4),
            take_profit_ratio NUMERIC(10,4),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    (
        "risk_profile",
        # PostgreSQL 变体
        """
        CREATE TABLE IF NOT EXISTS risk_profile (
            id BIGSERIAL PRIMARY KEY,
            name VARCHAR(64) NOT NULL,
            owner_id BIGINT,
            is_default BOOLEAN DEFAULT FALSE,
            description VARCHAR(512) DEFAULT '',
            is_active BOOLEAN DEFAULT TRUE,
            risk_single_ratio NUMERIC(10,4),
            risk_daily_loss_ratio NUMERIC(10,4),
            max_daily_amount NUMERIC(18,6),
            max_daily_count INTEGER,
            max_consecutive_failures INTEGER,
            max_drawdown_ratio NUMERIC(10,4),
            max_open_positions INTEGER,
            stop_loss_ratio NUMERIC(10,4),
            take_profit_ratio NUMERIC(10,4),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    (
        "account_risk_profile",
        """
        CREATE TABLE IF NOT EXISTS account_risk_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL UNIQUE,
            risk_profile_id INTEGER,
            risk_single_ratio NUMERIC(10,4),
            risk_daily_loss_ratio NUMERIC(10,4),
            max_daily_amount NUMERIC(18,6),
            max_daily_count INTEGER,
            max_consecutive_failures INTEGER,
            max_drawdown_ratio NUMERIC(10,4),
            max_open_positions INTEGER,
            stop_loss_ratio NUMERIC(10,4),
            take_profit_ratio NUMERIC(10,4),
            consecutive_failures INTEGER DEFAULT 0,
            is_frozen BOOLEAN DEFAULT FALSE,
            frozen_reason VARCHAR(256),
            frozen_at DATETIME,
            last_check_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    (
        "account_risk_profile",
        """
        CREATE TABLE IF NOT EXISTS account_risk_profile (
            id BIGSERIAL PRIMARY KEY,
            account_id BIGINT NOT NULL UNIQUE,
            risk_profile_id BIGINT,
            risk_single_ratio NUMERIC(10,4),
            risk_daily_loss_ratio NUMERIC(10,4),
            max_daily_amount NUMERIC(18,6),
            max_daily_count INTEGER,
            max_consecutive_failures INTEGER,
            max_drawdown_ratio NUMERIC(10,4),
            max_open_positions INTEGER,
            stop_loss_ratio NUMERIC(10,4),
            take_profit_ratio NUMERIC(10,4),
            consecutive_failures INTEGER DEFAULT 0,
            is_frozen BOOLEAN DEFAULT FALSE,
            frozen_reason VARCHAR(256),
            frozen_at TIMESTAMP,
            last_check_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    (
        "strategy_risk_profile",
        """
        CREATE TABLE IF NOT EXISTS strategy_risk_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            auto_strategy_id INTEGER NOT NULL UNIQUE,
            risk_profile_id INTEGER,
            risk_single_ratio NUMERIC(10,4),
            risk_daily_loss_ratio NUMERIC(10,4),
            max_daily_amount NUMERIC(18,6),
            max_daily_count INTEGER,
            max_consecutive_failures INTEGER,
            max_drawdown_ratio NUMERIC(10,4),
            consecutive_failures INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    (
        "strategy_risk_profile",
        """
        CREATE TABLE IF NOT EXISTS strategy_risk_profile (
            id BIGSERIAL PRIMARY KEY,
            auto_strategy_id BIGINT NOT NULL UNIQUE,
            risk_profile_id BIGINT,
            risk_single_ratio NUMERIC(10,4),
            risk_daily_loss_ratio NUMERIC(10,4),
            max_daily_amount NUMERIC(18,6),
            max_daily_count INTEGER,
            max_consecutive_failures INTEGER,
            max_drawdown_ratio NUMERIC(10,4),
            consecutive_failures INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    (
        "risk_event_log",
        """
        CREATE TABLE IF NOT EXISTS risk_event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_uid VARCHAR(32) NOT NULL UNIQUE,
            account_id INTEGER,
            auto_strategy_id INTEGER,
            user_id INTEGER,
            rule_name VARCHAR(64) DEFAULT '',
            event_type SMALLINT NOT NULL DEFAULT 1,
            severity SMALLINT NOT NULL DEFAULT 1,
            stage VARCHAR(32) DEFAULT '',
            title VARCHAR(128) NOT NULL DEFAULT '',
            message TEXT DEFAULT '',
            detail_json TEXT DEFAULT '{}',
            balance_snapshot NUMERIC(18,6) DEFAULT 0,
            daily_pnl_snapshot NUMERIC(18,6) DEFAULT 0,
            order_amount_snapshot NUMERIC(18,6) DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    (
        "risk_event_log",
        """
        CREATE TABLE IF NOT EXISTS risk_event_log (
            id BIGSERIAL PRIMARY KEY,
            event_uid VARCHAR(32) NOT NULL UNIQUE,
            account_id BIGINT,
            auto_strategy_id BIGINT,
            user_id BIGINT,
            rule_name VARCHAR(64) DEFAULT '',
            event_type SMALLINT NOT NULL DEFAULT 1,
            severity SMALLINT NOT NULL DEFAULT 1,
            stage VARCHAR(32) DEFAULT '',
            title VARCHAR(128) NOT NULL DEFAULT '',
            message TEXT DEFAULT '',
            detail_json TEXT DEFAULT '{}',
            balance_snapshot NUMERIC(18,6) DEFAULT 0,
            daily_pnl_snapshot NUMERIC(18,6) DEFAULT 0,
            order_amount_snapshot NUMERIC(18,6) DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
]


# WP-10：关键查询补索引（性能优化）
# 采用"双语法"：SQLite 用 IF NOT EXISTS，PostgreSQL 用 IF NOT EXISTS（也支持）
_INDEX_DDL: list[tuple[str, str]] = [
    # strategy_performance: 榜单主查询走 (period_type, composite_score DESC)
    ("idx_perf_period_score", "CREATE INDEX IF NOT EXISTS idx_perf_period_score ON strategy_performance (period_type, composite_score DESC)"),
    ("idx_perf_account_period", "CREATE INDEX IF NOT EXISTS idx_perf_account_period ON strategy_performance (account_id, period_type)"),
    ("idx_perf_uid_period", "CREATE INDEX IF NOT EXISTS idx_perf_uid_period ON strategy_performance (uid, period_type)"),
    # order_execution_log: 订单流水按 uid+时间翻页
    ("idx_orderlog_uid_time", "CREATE INDEX IF NOT EXISTS idx_orderlog_uid_time ON order_execution_log (uid, created_at DESC)"),
    ("idx_orderlog_account_time", "CREATE INDEX IF NOT EXISTS idx_orderlog_account_time ON order_execution_log (account_id, created_at DESC)"),
    ("idx_orderlog_vote", "CREATE INDEX IF NOT EXISTS idx_orderlog_vote ON order_execution_log (vote_id)"),
    # follow_subscription: 订阅查询按 (subscriber_id, leader_uid, status)
    ("idx_follow_sub_leader_status", "CREATE INDEX IF NOT EXISTS idx_follow_sub_leader_status ON follow_subscription (subscriber_id, leader_uid, status)"),
    ("idx_follow_leader_status", "CREATE INDEX IF NOT EXISTS idx_follow_leader_status ON follow_subscription (leader_uid, status)"),
    # follow_order: 跟单成交按 (subscription_id, created_at DESC)
    ("idx_follow_order_sub_time", "CREATE INDEX IF NOT EXISTS idx_follow_order_sub_time ON follow_order (subscription_id, created_at DESC)"),
    # notification: 未读查询按 (user_id, is_read, created_at DESC)
    ("idx_notify_user_unread", "CREATE INDEX IF NOT EXISTS idx_notify_user_unread ON notification (user_id, is_read, created_at DESC)"),
    # rank_snapshot: 榜单快照按 (rank_type, period_end_time DESC)
    ("idx_snapshot_rank_type_time", "CREATE INDEX IF NOT EXISTS idx_snapshot_rank_type_time ON rank_snapshot (rank_type, period_end_time DESC, rank)"),
    # execution_account: 软删除过滤 + owner 查询
    ("idx_acc_owner_deleted", "CREATE INDEX IF NOT EXISTS idx_acc_owner_deleted ON execution_account (owner_id, deleted_at)"),
    ("idx_acc_platform_deleted", "CREATE INDEX IF NOT EXISTS idx_acc_platform_deleted ON execution_account (platform, deleted_at)"),
    # vote_decision: 投票按 (account_id, created_at DESC)
    ("idx_vote_account_time", "CREATE INDEX IF NOT EXISTS idx_vote_account_time ON vote_decision (account_id, created_at DESC)"),
    # agent_prediction: 智能体预测按 (symbol, timeframe, created_at DESC)
    ("idx_prediction_symbol_time", "CREATE INDEX IF NOT EXISTS idx_prediction_symbol_time ON agent_prediction (symbol, timeframe, created_at DESC)"),
    # auto_strategy_log: 策略日志按 (task_id, log_type, created_at DESC)
    ("idx_auto_strategy_log_task_type", "CREATE INDEX IF NOT EXISTS idx_auto_strategy_log_task_type ON auto_strategy_log (task_id, log_type, created_at DESC)"),
    # auto_strategy: 按关联账户查询
    ("idx_auto_strategy_account_id", "CREATE INDEX IF NOT EXISTS idx_auto_strategy_account_id ON auto_strategy (account_id)"),
    # strategy_trade: 策略交易明细查询
    ("idx_strategy_trade_name_time", "CREATE INDEX IF NOT EXISTS idx_strategy_trade_name_time ON strategy_trade (strategy_name, entry_at)"),
    ("idx_strategy_trade_name_status", "CREATE INDEX IF NOT EXISTS idx_strategy_trade_name_status ON strategy_trade (strategy_name, status)"),
    ("idx_strategy_trade_profit", "CREATE INDEX IF NOT EXISTS idx_strategy_trade_profit ON strategy_trade (strategy_name, is_profit)"),
    ("idx_strategy_trade_source", "CREATE INDEX IF NOT EXISTS idx_strategy_trade_source ON strategy_trade (source_strategy, entry_at)"),
    # strategy_equity_curve: 净值曲线查询
    ("idx_equity_strategy_date", "CREATE INDEX IF NOT EXISTS idx_equity_strategy_date ON strategy_equity_curve (strategy_name, snapshot_date)"),
    ("idx_equity_account_date", "CREATE INDEX IF NOT EXISTS idx_equity_account_date ON strategy_equity_curve (account_id, snapshot_date)"),
    # ===== 20260807 风控模块独立：新表索引 =====
    ("idx_risk_profile_owner", "CREATE INDEX IF NOT EXISTS idx_risk_profile_owner ON risk_profile (owner_id, is_active)"),
    ("idx_account_risk_frozen", "CREATE INDEX IF NOT EXISTS idx_account_risk_frozen ON account_risk_profile (is_frozen, account_id)"),
    ("idx_strategy_risk_auto", "CREATE INDEX IF NOT EXISTS idx_strategy_risk_auto ON strategy_risk_profile (auto_strategy_id)"),
    ("idx_risk_event_account", "CREATE INDEX IF NOT EXISTS idx_risk_event_account ON risk_event_log (account_id, created_at DESC)"),
    ("idx_risk_event_strategy", "CREATE INDEX IF NOT EXISTS idx_risk_event_strategy ON risk_event_log (auto_strategy_id, created_at DESC)"),
    ("idx_risk_event_user", "CREATE INDEX IF NOT EXISTS idx_risk_event_user ON risk_event_log (user_id, created_at DESC)"),
    ("idx_risk_event_type_time", "CREATE INDEX IF NOT EXISTS idx_risk_event_type_time ON risk_event_log (event_type, created_at DESC)"),
]


# 20260806 表名重命名后遗留的旧索引（迁移时先 DROP 再重建为新名）
_DROP_INDEXES: list[str] = [
    "idx_auto_task_log_task_type",
    "idx_auto_task_account_id",
]


def _rename_table_if_exists(conn, old_name: str, new_name: str) -> bool:
    """幂等重命名表（仅在旧表存在时执行）
    - 旧表不存在：跳过（全新库或已完成迁移）
    - 旧表存在且新表不存在：直接 ALTER TABLE RENAME
    - 旧表存在且新表已存在（create_all 预创建的空表）：先 DROP 新表再 RENAME，保留旧表数据
    """
    insp = inspect(conn)
    if not insp.has_table(old_name):
        return False
    if insp.has_table(new_name):
        conn.execute(text(f"DROP TABLE IF EXISTS {new_name}"))
        logger.warning(f"migration: drop pre-created {new_name} before rename from {old_name}")
    conn.execute(text(f"ALTER TABLE {old_name} RENAME TO {new_name}"))
    logger.info(f"migration: renamed table {old_name} -> {new_name}")
    return True


def _sqlite_type(sql_type: str) -> str:
    """SQLite 类型映射（SQLite 弱类型，列类型仅作 hint）"""
    upper = sql_type.upper()
    if upper.startswith("VARCHAR"):
        return "TEXT"
    if upper.startswith("NUMERIC") or upper.startswith("DECIMAL"):
        return "NUMERIC"
    if upper.startswith("BOOLEAN"):
        return "BOOLEAN"
    if "DATETIME" in upper or "TIMESTAMP" in upper:
        return "DATETIME"
    return upper


def _is_sqlite() -> bool:
    """判断 sync_engine 走的是 SQLite 还是 PostgreSQL"""
    return str(sync_engine.url).startswith("sqlite")


def _create_login_attempt_indexes(insp) -> list[str]:
    """为 login_attempt 表补索引（幂等）"""
    created: list[str] = []
    idx_sql = [
        ("idx_login_attempt_email", "CREATE INDEX IF NOT EXISTS idx_login_attempt_email ON login_attempt (email)"),
        ("idx_login_attempt_ip", "CREATE INDEX IF NOT EXISTS idx_login_attempt_ip ON login_attempt (ip)"),
        ("idx_login_attempt_created_at", "CREATE INDEX IF NOT EXISTS idx_login_attempt_created_at ON login_attempt (created_at)"),
    ]
    # SQLite / PostgreSQL 两者都支持 IF NOT EXISTS
    with sync_engine.begin() as conn:
        for name, sql in idx_sql:
            try:
                conn.execute(text(sql))
                created.append(name)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"create index {name} failed (skip): {e}")
    return created


def run_migrations() -> list[str]:
    """运行所有幂等迁移（默认作用于 sync_engine）"""
    return run_migrations_for_engine(sync_engine)


def run_migrations_for_engine(engine) -> list[str]:
    """运行所有幂等迁移，作用于指定 engine（用于演示库独立补表）
    - 入参：SQLAlchemy Engine 实例
    - 返回：变更摘要列表
    """
    applied: list[str] = []
    insp = inspect(engine)

    # 0) 表名重命名（幂等）：auto_task -> auto_strategy, auto_task_log -> auto_strategy_log
    #    必须在补列/建索引之前执行，以保留旧表数据
    with engine.begin() as conn:
        if _rename_table_if_exists(conn, "auto_task", "auto_strategy"):
            applied.append("rename auto_task -> auto_strategy")
        if _rename_table_if_exists(conn, "auto_task_log", "auto_strategy_log"):
            applied.append("rename auto_task_log -> auto_strategy_log")
    # 重命名后刷新 inspector（确保后续 has_table/get_columns 看到最新状态）
    insp = inspect(engine)

    # 1) 先建新增表（WP-03：login_attempt / WP-09：outbox_event）
    table_ddls: dict[str, list[str]] = {}
    for tbl, ddl in _NEW_TABLES_DDL:
        table_ddls.setdefault(tbl, []).append(ddl)
    is_sqlite = str(engine.url).startswith("sqlite")
    for table_name, ddls in table_ddls.items():
        if insp.has_table(table_name):
            continue
        try:
            with engine.begin() as conn:
                if is_sqlite:
                    conn.execute(text(ddls[0]))
                else:
                    pg_ddl = next(
                        (d for d in ddls if "BIGSERIAL" in d or "TIMESTAMP" in d), ddls[-1]
                    )
                    conn.execute(text(pg_ddl))
            applied.append(f"{table_name} (CREATE TABLE)")
            logger.info(f"migration applied: CREATE TABLE {table_name}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"create {table_name} failed (skip): {e}")

    # 2) 补列（先补列，后建索引——部分索引依赖新列）
    for table, column, sql_type, default_sql in _PATCHES:
        if not insp.has_table(table):
            logger.debug(f"migration skip: table {table} not exists")
            continue
        cols = {c["name"] for c in insp.get_columns(table)}
        if column in cols:
            continue
        actual_type = _sqlite_type(sql_type)
        if default_sql is not None:
            ddl = f"ALTER TABLE {table} ADD COLUMN {column} {actual_type} DEFAULT {default_sql}"
        else:
            ddl = f"ALTER TABLE {table} ADD COLUMN {column} {actual_type}"
        with engine.begin() as conn:
            try:
                conn.execute(text(ddl))
                applied.append(f"{table}.{column} ({actual_type})")
                logger.info(f"migration applied: {ddl}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"alter table {table} add {column} failed: {e}")

    # 3) 索引（先删除旧表名遗留索引，再创建新索引）
    try:
        with engine.begin() as conn:
            for old_idx in _DROP_INDEXES:
                try:
                    conn.execute(text(f"DROP INDEX IF EXISTS {old_idx}"))
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"drop index {old_idx} failed (skip): {e}")
            for name, sql in _INDEX_DDL:
                try:
                    conn.execute(text(sql))
                    applied.append(name)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"create index {name} failed (skip): {e}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"create indexes failed: {e}")

    # 4) migrate_030：把旧表中的风控参数 / 冻结状态回填到新风控表
    try:
        migrated = _migrate_030_risk_backfill(engine)
        applied.extend(migrated)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"migrate_030 risk backfill failed (skip): {e}")

    return applied


# ========= migrate_030：风控旧数据回填（幂等：仅在空行时回填）=========
def _migrate_030_risk_backfill(engine) -> list[str]:
    """
    把存储在 ExecutionAccount / AutoStrategy 旧字段里的风控参数回填到
    AccountRiskProfile / StrategyRiskProfile，并为所有没有风险档案的账户创建默认行。
    - 幂等：已经存在的 account_id / auto_strategy_id 不覆盖（保留用户后来手动设置的个性化值）
    """
    from datetime import datetime
    from sqlalchemy import inspect, text
    from sqlalchemy.orm import sessionmaker, Session

    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    applied: list[str] = []
    db: Session
    with SessionLocal() as db:
        # 1. 检查是否建了 risk_profile 空表？先插入 1 个系统内置默认模板（幂等）
        system_default = db.execute(
            text("SELECT 1 FROM risk_profile WHERE owner_id IS NULL AND is_default = TRUE LIMIT 1")
        ).first()
        if system_default is None:
            try:
                db.execute(text("""
                INSERT INTO risk_profile (
                    name, owner_id, is_default, description, is_active,
                    risk_single_ratio, risk_daily_loss_ratio, max_daily_amount,
                    max_daily_count, max_consecutive_failures, max_drawdown_ratio,
                    max_open_positions, stop_loss_ratio, take_profit_ratio,
                    created_at, updated_at
                ) VALUES (
                    :name, NULL, :is_default, :description, :is_active,
                    :risk_single_ratio, :risk_daily_loss_ratio, :max_daily_amount,
                    :max_daily_count, :max_consecutive_failures, :max_drawdown_ratio,
                    :max_open_positions, :stop_loss_ratio, :take_profit_ratio,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """), {
                    "name": "系统默认风控（保守）",
                    "is_default": True,
                    "description": "全局默认：日亏 5%、单日单量上限 2k USD、连续失败 8 次",
                    "is_active": True,
                    "risk_single_ratio": 0.05,
                    "risk_daily_loss_ratio": 0.05,
                    "max_daily_amount": 2000.0,
                    "max_daily_count": 10,
                    "max_consecutive_failures": 8,
                    "max_drawdown_ratio": 0.15,
                    "max_open_positions": 3,
                    "stop_loss_ratio": 0.05,
                    "take_profit_ratio": 0.10,
                })
                applied.append("risk_profile: system default inserted")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"insert system risk_profile failed: {e}")

        # 2. 给所有 execution_account 补一个 account_risk_profile（幂等）
        #    - 同时把 ExecutionAccount.risk_frozen 镜像过来
        #    - 注意：某些老库可能没有 risk_frozen 列，先查列名再拼接 SQL
        insp_cols = {c["name"] for c in inspect(engine).get_columns("execution_account")}
        frozen_reason_col = "risk_frozen_reason" if "risk_frozen_reason" in insp_cols else "NULL AS risk_frozen_reason"
        sql_acc_all = f"""
            SELECT a.id, a.risk_frozen, {frozen_reason_col}
            FROM execution_account a
            LEFT JOIN account_risk_profile p ON p.account_id = a.id
            WHERE p.id IS NULL
        """
        all_acc = db.execute(text(sql_acc_all)).mappings().all()
        n_acc = 0
        for row in all_acc:
            try:
                frozen = bool(row["risk_frozen"])
                db.execute(text("""
                    INSERT INTO account_risk_profile (
                        account_id, consecutive_failures, is_frozen,
                        frozen_reason, frozen_at, last_check_at,
                        created_at, updated_at
                    ) VALUES (
                        :account_id, 0, :is_frozen,
                        :frozen_reason, :frozen_at, NULL,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                """), {
                    "account_id": row["id"],
                    "is_frozen": frozen,
                    "frozen_reason": (row.get("risk_frozen_reason") or
                                      ("日亏超限(旧数据迁移)" if frozen else None)),
                    "frozen_at": datetime.utcnow() if frozen else None,
                })
                n_acc += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(f"insert account_risk_profile failed acc={row.get('id')}: {e}")
        if n_acc:
            applied.append(f"account_risk_profile: backfilled {n_acc} rows")

        # 3. 给所有 auto_strategy 补一个 strategy_risk_profile（幂等）
        #    - 把 max_daily_amount / max_daily_count / max_consecutive_failures / consecutive_failures 复制
        all_tasks = db.execute(
            text("""
            SELECT t.id, t.max_daily_amount, t.max_daily_count,
                   t.max_consecutive_failures, t.consecutive_failures
            FROM auto_strategy t
            LEFT JOIN strategy_risk_profile p ON p.auto_strategy_id = t.id
            WHERE p.id IS NULL
            """)
        ).mappings().all()
        n_tasks = 0
        for row in all_tasks:
            try:
                db.execute(text("""
                    INSERT INTO strategy_risk_profile (
                        auto_strategy_id, max_daily_amount, max_daily_count,
                        max_consecutive_failures, consecutive_failures,
                        created_at, updated_at
                    ) VALUES (
                        :auto_strategy_id, :max_daily_amount, :max_daily_count,
                        :max_consecutive_failures, :consecutive_failures,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                """), {
                    "auto_strategy_id": row["id"],
                    "max_daily_amount": row["max_daily_amount"],
                    "max_daily_count": row["max_daily_count"],
                    "max_consecutive_failures": row["max_consecutive_failures"],
                    "consecutive_failures": row.get("consecutive_failures") or 0,
                })
                n_tasks += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(f"insert strategy_risk_profile failed task={row.get('id')}: {e}")
        if n_tasks:
            applied.append(f"strategy_risk_profile: backfilled {n_tasks} rows")

        db.commit()
    return applied
