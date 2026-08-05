# 数据库轻量级迁移（无需 alembic，幂等补列/补表）
from loguru import logger
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
    # ===== 20260803 自动任务日志增强 =====
    ("auto_task_log", "log_type", "SMALLINT", "0"),
    ("auto_task_log", "action_type", "VARCHAR(32)", "''"),
    ("auto_task_log", "detail_json", "TEXT", "'{}'"),
    # ===== 20260805 自动任务：开始时间 + 循环次数 =====
    ("auto_task", "start_time", "DATETIME", None),
    ("auto_task", "loop_count", "INTEGER", "0"),
    ("auto_task", "executed_count", "INTEGER", "0"),
    # ===== 20260805 自动任务日志增强：信号/执行/结果详情 + 盈亏追踪 =====
    ("auto_task_log", "signal_detail_json", "TEXT", "'{}'"),
    ("auto_task_log", "execution_detail_json", "TEXT", "'{}'"),
    ("auto_task_log", "result_detail_json", "TEXT", "'{}'"),
    ("auto_task_log", "pnl_amount", "NUMERIC(18,6)", "0"),
    ("auto_task_log", "pnl_percent", "NUMERIC(10,4)", "0"),
    ("auto_task_log", "is_profit", "BOOLEAN", "0"),
    ("auto_task_log", "market_resolved", "BOOLEAN", "0"),
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
    # auto_task_log: 任务日志按 (task_id, log_type, created_at DESC)
    ("idx_auto_task_log_task_type", "CREATE INDEX IF NOT EXISTS idx_auto_task_log_task_type ON auto_task_log (task_id, log_type, created_at DESC)"),
]


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

    # 2) 索引（login_attempt + WP-10 关键索引）
    try:
        with engine.begin() as conn:
            for name, sql in _INDEX_DDL:
                try:
                    conn.execute(text(sql))
                    applied.append(name)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"create index {name} failed (skip): {e}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"create indexes failed: {e}")

    # 3) 补列
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
    return applied
