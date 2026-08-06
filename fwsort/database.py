# 数据库连接：SQLAlchemy 2.0 同步/异步双引擎
# WP-06：演示模式数据层物理隔离（独立 SQLite + 独立 session 工厂）
import logging
import shutil
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator

from fastapi import Request
from fwsort.fwlogs import logger



from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from fwsort.config import settings


# 控制 SQLAlchemy 日志级别：默认降低到 WARNING 以减少日志噪音
# 如需调试 SQL，可设置为 logging.INFO 或 logging.DEBUG
_SQL_ALCHEMY_LOG_LEVEL = logging.WARNING
logging.getLogger('sqlalchemy.engine').setLevel(_SQL_ALCHEMY_LOG_LEVEL)
logging.getLogger('sqlalchemy.engine.Engine').setLevel(_SQL_ALCHEMY_LOG_LEVEL)


# 声明式基类
class Base(DeclarativeBase):
    """所有 ORM 模型的基类"""

    pass


# ========== 引擎构造（SQLite 走文件，PostgreSQL 走连接池）==========
# 注意：将 echo 设为 False，避免 SQLAlchemy 直接输出到 stderr
# SQL 调试可通过设置 logging 级别实现
_USE_SQL_ECHO = settings.APP_DEBUG and False  # 默认关闭 SQL echo，避免日志噪音

if settings.USE_SQLITE:
    sync_engine = create_engine(
        settings.sync_dsn,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
        echo=_USE_SQL_ECHO,
    )

    # SQLite 必须开启外键约束
    @event.listens_for(sync_engine, "connect")
    def _fk_on(dbapi_conn, _):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async_engine = create_async_engine(
        settings.async_dsn,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
        echo=_USE_SQL_ECHO,
    )
else:
    sync_engine = create_engine(
        settings.postgres_dsn,
        pool_size=settings.POSTGRES_POOL_SIZE,
        max_overflow=settings.POSTGRES_MAX_OVERFLOW,
        pool_pre_ping=True,
        echo=_USE_SQL_ECHO,
    )
    async_engine = create_async_engine(
        settings.postgres_async_dsn,
        pool_size=settings.POSTGRES_POOL_SIZE,
        max_overflow=settings.POSTGRES_MAX_OVERFLOW,
        pool_pre_ping=True,
        echo=_USE_SQL_ECHO,
    )

# 清理 SQLAlchemy 日志 handler，确保日志只通过我们的日志系统输出
for _logger_name in ['sqlalchemy.engine', 'sqlalchemy.engine.Engine']:
    _sa_logger = logging.getLogger(_logger_name)
    _sa_logger.handlers.clear()
    _sa_logger.setLevel(logging.WARNING)
    _sa_logger.propagate = True

# 会话工厂（生产）
SyncSessionLocal = sessionmaker(bind=sync_engine, autoflush=False, autocommit=False)
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine, class_=AsyncSession, autoflush=False, autocommit=False
)


# ========== WP-06：演示模式独立引擎（独立 SQLite 文件）==========
_demo_sync_engine = create_engine(
    f"sqlite:///{settings.APP_DEMO_SQLITE_PATH}",
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
    echo=False,  # demo 不刷 SQL 日志
)
_demo_async_engine = create_async_engine(
    f"sqlite+aiosqlite:///{settings.APP_DEMO_SQLITE_PATH}",
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
    echo=False,
)


@event.listens_for(_demo_sync_engine, "connect")
def _demo_fk_on(dbapi_conn, _):  # noqa: ANN001
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


DemoSyncSessionLocal = sessionmaker(
    bind=_demo_sync_engine, autoflush=False, autocommit=False
)
DemoAsyncSessionLocal = async_sessionmaker(
    bind=_demo_async_engine, class_=AsyncSession, autoflush=False, autocommit=False
)


def _is_demo_request(request: Request | None) -> bool:
    """判断当前请求是否走演示数据通道
    规则：URL path 以 /api/demo/ 开头 → demo
    """
    if request is None:
        return False
    return request.url.path.startswith("/api/demo/")


@contextmanager
def get_sync_db(request: Request | None = None) -> Generator[Session, None, None]:
    """同步会话上下文（Celery 任务、初始化脚本；WP-06 可选接受 request 判定 demo）"""
    if _is_demo_request(request):
        db = DemoSyncSessionLocal()
    else:
        db = SyncSessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def get_async_db(request: Request) -> AsyncSession:
    """异步会话生成器（FastAPI Depends 注入；WP-06 根据 path 自动分流 demo/prod）"""
    if _is_demo_request(request):
        factory = DemoAsyncSessionLocal
    else:
        factory = AsyncSessionLocal
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_db(request: Request) -> Generator[Session, None, None]:
    """同步会话生成器（FastAPI Depends 注入，运行于线程池；WP-06 根据 path 自动分流 demo/prod）

    供使用同步 SQLAlchemy ORM（db.query / db.add / db.flush）的路由与服务调用。
    """
    if _is_demo_request(request):
        db = DemoSyncSessionLocal()
    else:
        db = SyncSessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """初始化数据库表结构（首次启动或迁移后调用）"""
    # 导入所有模型以注册到 Base.metadata
    from fwsort import models  # noqa: F401

    Base.metadata.create_all(bind=sync_engine)
    # 幂等补列（老库平滑升级，无需 alembic）
    from fwsort.migrations import run_migrations

    run_migrations()


def init_demo_db() -> None:
    """WP-06：初始化演示模式数据库表结构（独立 SQLite 文件）"""
    from fwsort import models  # noqa: F401

    Base.metadata.create_all(bind=_demo_sync_engine)
    # 幂等补表：演示库是独立文件，需要也跑一遍迁移脚本（outbox_event 等新增表）
    try:
        from fwsort.migrations import run_migrations_for_engine

        run_migrations_for_engine(_demo_sync_engine)
    except Exception:  # noqa: BLE001
        # 若迁移函数尚未适配新引擎，回退到 prod 引擎迁移
        # （演示库与 prod 库结构最终一致；只在 prod 端补表也能 work，因为 _demo_sync_engine 也是 SQLite 变体）
        from fwsort.migrations import run_migrations

        run_migrations()


# ========== 数据库重置：清空交易数据，保留配置 ==========
# 需要清空的表（交易/运行时数据）
_TRANSACTIONAL_TABLES = [
    "auto_strategy_log",
    "auto_strategy",
    "strategy_performance",
    "execution_account",
    "order_execution_log",
    "vote_decision",
    "agent_prediction",
    "agent_collaboration",
    "agent_portfolio",
    "rank_snapshot",
    "follow_order",
    "rental_order",
    "outbox_event",
]

# 保留的表（配置/基础数据）
_PROTECTED_TABLES = [
    "user",
    "weight_config",
    "system_config",
    "rental_agent",
    "notification",
    "login_attempt",
    "follow_subscription",
]


def _backup_database(engine) -> str | None:
    """备份数据库文件到 data/backups/ 目录

    Args:
        engine: SQLAlchemy 引擎

    Returns:
        str | None: 备份文件路径，备份失败返回 None
    """
    try:
        # 从引擎 URL 获取数据库文件路径
        db_url = engine.url
        if db_url.drivername == "sqlite":
            db_path = str(db_url.database)
            src_path = Path(db_path)
        else:
            # PostgreSQL 等其他数据库暂不支持自动备份
            logger.warning("[reset_db] Auto backup only supports SQLite. Skipping backup.")
            return None

        if not src_path.exists():
            logger.warning(f"[reset_db] Source database not found: {src_path}")
            return None

        # 创建备份目录
        backup_dir = src_path.parent / "backups"
        backup_dir.mkdir(exist_ok=True)

        # 生成备份文件名（带时间戳）
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = backup_dir / f"{src_path.stem}_{timestamp}{src_path.suffix}"

        # 复制文件
        shutil.copy2(src_path, backup_file)
        logger.info(f"[reset_db] Database backup created: {backup_file}")

        # 如果有 WAL 文件也一并备份
        wal_file = src_path.parent / f"{src_path.name}-wal"
        if wal_file.exists():
            shutil.copy2(wal_file, backup_file.with_suffix(backup_file.suffix + "-wal"))

        return str(backup_file)
    except Exception as e:  # noqa: BLE001
        logger.error(f"[reset_db] Database backup failed: {e}")
        return None


def reset_db(engine=None, confirm_token: str = "") -> dict:
    """重置数据库：清空所有交易数据，保留配置

    Args:
        engine: SQLAlchemy 引擎，默认使用 sync_engine（主数据库）
        confirm_token: 确认令牌，需要传入 "RESET_DB" 才能执行

    Returns:
        dict: {"status": "ok"/"error", "cleared_tables": [...], "message": "..."}
    """
    if confirm_token not in ("RESET_DB", "RESET_DEMO_DB"):
        return {"status": "error", "message": "Invalid confirm token. Use 'RESET_DB' or 'RESET_DEMO_DB' to confirm."}

    if engine is None:
        engine = sync_engine

    # ===== 1. 自动备份数据库 =====
    backup_path = _backup_database(engine)
    if backup_path:
        logger.info(f"[reset_db] Database backed up to: {backup_path}")
    else:
        logger.warning("[reset_db] Failed to backup database, proceeding without backup")

    cleared_tables = []
    errors = []

    # 按依赖关系排序：先清子表，再清父表
    # auto_strategy_log -> auto_strategy -> execution_account
    # 其他表按字母顺序
    ordered_tables = [
        "auto_strategy_log",
        "auto_strategy",
        "strategy_performance",
        "order_execution_log",
        "vote_decision",
        "agent_prediction",
        "agent_collaboration",
        "agent_portfolio",
        "rank_snapshot",
        "follow_order",
        "rental_order",
        "outbox_event",
        "execution_account",
    ]

    with engine.begin() as conn:
        # 关闭外键约束（SQLite 需要）
        from sqlalchemy import text
        conn.execute(text("PRAGMA foreign_keys=OFF"))

        for table in ordered_tables:
            if table not in _TRANSACTIONAL_TABLES:
                continue
            try:
                conn.execute(text(f"DELETE FROM {table}"))
                cleared_tables.append(table)
                logger.info(f"[reset_db] Cleared table: {table}")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{table}: {e}")
                logger.warning(f"[reset_db] Failed to clear {table}: {e}")

        # 重新开启外键约束
        conn.execute(text("PRAGMA foreign_keys=ON"))

    # 清空 Redis 缓存
    try:
        from fwsort.redis_client import sync_redis
        # 删除所有排行榜相关的 key（兼容 FakeRedis 与真实 Redis）
        if hasattr(sync_redis, "keys"):
            keys_to_delete = list(sync_redis.keys("fwsort:rank:*"))
        else:
            keys_to_delete = [k for k in sync_redis.scan_iter(match="fwsort:rank:*")]
        if keys_to_delete:
            sync_redis.delete(*keys_to_delete)
            logger.info(f"[reset_db] Cleared {len(keys_to_delete)} Redis keys")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[reset_db] Failed to clear Redis: {e}")

    result = {
        "status": "ok",
        "cleared_tables": cleared_tables,
        "errors": errors,
        "backup_path": backup_path,
        "message": f"Successfully reset database. Cleared {len(cleared_tables)} tables. Redis cache cleared. Backup: {backup_path or 'failed'}",
    }
    if errors:
        result["message"] += f" ({len(errors)} errors)"

    logger.info(f"[reset_db] {result['message']}")
    return result


def _seed_demo_data(engine) -> None:
    """播种演示数据到指定引擎：1 管理员 + 20 执行账户 + 绩效 + 50 投票 + 订单

    用于重置演示库后自动恢复演示数据，使演示库始终可访问。
    """
    import random
    import uuid
    from datetime import datetime, timedelta

    from fwsort.models import (
        AgentPrediction,
        ExecutionAccount,
        OrderExecutionLog,
        StrategyPerformance,
        User,
        VoteDecision,
    )
    from fwsort.ranking_engine import composite_score
    from fwsort.security import hash_password
    from fwsort.voting import vote

    Base.metadata.create_all(bind=engine)
    try:
        from fwsort.migrations import run_migrations_for_engine

        run_migrations_for_engine(engine)
    except Exception:
        from fwsort.migrations import run_migrations

        run_migrations()

    session = DemoSyncSessionLocal()
    try:
        admin = User(
            email="admin@fwquant.com",
            password_hash=hash_password("admin123456"),
            nickname="管理员",
            role=3,
            status=0,
        )
        session.add(admin)
        session.flush()
        admin_id = admin.id
        logger.info(f"[seed_demo] admin created: id={admin_id}")

        names = ["Alpha猎手", "Beta套利", "Gamma网格", "Delta波段", "Epsilon趋势", "Zeta对冲", "Eta高频", "Theta事件"]
        platforms = ["polymarket", "okx"]
        accounts = []

        for i in range(20):
            acc = ExecutionAccount(
                uid=f"ACC-{uuid.uuid4().hex[:10].upper()}",
                owner_id=admin_id,
                name=f"{names[i % len(names)]}-{i+1:02d}",
                platform=platforms[i % 2],
                account_type=0,
                initial_balance=1000.0,
                current_balance=round(1000.0 + random.uniform(-50, 300), 2),
                daily_pnl=round(random.uniform(-100, 100), 2),
            )
            session.add(acc)
            accounts.append(acc)
        session.flush()
        logger.info(f"[seed_demo] {len(accounts)} accounts created")

        for acc in accounts:
            ann = round(random.uniform(-0.1, 1.2), 4)
            dd = round(random.uniform(0.02, 0.3), 4)
            sharpe = round(random.uniform(0.3, 3.0), 2)
            plr = round(random.uniform(0.8, 3.0), 2)
            ex = round(random.uniform(0.6, 0.95), 4)
            score = composite_score(ann, dd, sharpe, plr, ex)

            sp = StrategyPerformance(
                account_id=acc.id,
                uid=acc.uid,
                period_type=4,
                start_time=datetime.now() - timedelta(days=30),
                end_time=datetime.now(),
                annualized_return=ann,
                max_drawdown=dd,
                sharpe_ratio=sharpe,
                sortino_ratio=sharpe * 1.2,
                calmar_ratio=ann / max(dd, 0.01),
                profit_factor=plr,
                win_rate=round(random.uniform(0.45, 0.75), 4),
                profit_loss_ratio=plr,
                trade_count=random.randint(100, 1500),
                execution_rate=round(random.uniform(0.85, 0.99), 4),
                avg_slippage=round(random.uniform(0.0001, 0.005), 6),
                avg_latency=random.randint(150, 800),
                cancel_rate=round(random.uniform(0.01, 0.15), 4),
                execution_score=ex,
                composite_score=score,
            )
            session.add(sp)
        session.flush()
        logger.info("[seed_demo] performances created")

        for _ in range(50):
            acc = random.choice(accounts)
            directions = [random.choice([1, 1, 1, 2, 0]) for _ in range(3)]
            preds = []

            for d in directions:
                ap = AgentPrediction(
                    agent_name=random.choice(["GPT-4o", "Claude", "Gemini"]),
                    agent_model=random.choice(["gpt-4o", "claude-3-5-sonnet", "gemini-2.0-flash"]),
                    symbol="BTCUSDT",
                    timeframe="15m",
                    direction=d,
                    confidence=round(random.uniform(0.55, 0.95), 4),
                    reasoning="[MOCK seed] 模拟历史预测",
                    raw_payload='{"mock": true}',
                    latency_ms=random.randint(100, 600),
                )
                session.add(ap)
                preds.append(ap)
            session.flush()

            v = vote(
                directions=directions,
                account_balance=float(acc.current_balance),
                daily_pnl=float(acc.daily_pnl),
                initial_balance=float(acc.initial_balance),
            )

            v_row = VoteDecision(
                account_id=acc.id,
                symbol="BTCUSDT",
                timeframe="15m",
                up_count=v.up_count,
                down_count=v.down_count,
                flat_count=v.flat_count,
                final_direction=v.final_direction,
                order_amount_usd=v.order_amount_usd,
                order_amount_reason=v.reason,
                prediction_ids=",".join(str(p.id) for p in preds),
            )
            session.add(v_row)
            session.flush()

            if v.final_direction != 0 and v.order_amount_usd > 0:
                order_id = f"ORD-{uuid.uuid4().hex[:16].upper()}"
                if acc.platform == "polymarket":
                    price = round(max(0.01, min(0.99, 0.5 + random.uniform(-0.1, 0.1))), 4)
                    qty = round(1.0 / price, 4)
                    slip = round(random.uniform(0.0, 0.01), 6)
                elif acc.platform == "okx":
                    price = 60000.0 + random.uniform(-500, 500)
                    qty = round(v.order_amount_usd / price, 6)
                    slip = round(random.uniform(0.0, 0.003), 6)
                else:
                    price, qty, slip = 0.0, 0.0, 0.0

                status = 3 if random.random() < 0.9 else 2
                actual_price = price * (1 + slip * (1 if v.final_direction == 1 else -1))

                order_log = OrderExecutionLog(
                    uid=acc.uid,
                    account_id=acc.id,
                    vote_id=v_row.id,
                    order_id=order_id,
                    order_type=2,
                    side=v.final_direction,
                    platform=acc.platform,
                    symbol="BTCUSDT",
                    expected_price=price,
                    actual_price=actual_price,
                    quantity=qty,
                    amount_usd=v.order_amount_usd,
                    status=status,
                    latency_ms=random.randint(80, 600),
                    slippage=slip,
                    pnl=round(random.uniform(-5, 15), 2),
                )
                session.add(order_log)

        session.commit()
        logger.info("[seed_demo] demo data seeded successfully (20 accounts, 50 votes)")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_demo_db(confirm_token: str = "") -> dict:
    """重置演示数据库：清空所有数据后自动播种演示数据

    Args:
        confirm_token: 确认令牌，需要传入 "RESET_DEMO_DB" 才能执行

    Returns:
        dict: 同 reset_db，附加 seeded 字段
    """
    if confirm_token != "RESET_DEMO_DB":
        return {"status": "error", "message": "Invalid confirm token. Use 'RESET_DEMO_DB' to confirm."}

    result = reset_db(engine=_demo_sync_engine, confirm_token="RESET_DB")

    if result["status"] == "ok":
        try:
            _seed_demo_data(_demo_sync_engine)
            result["seeded"] = True
            result["message"] += " | 演示数据已自动播种"
            logger.info("[reset_demo_db] demo data seeded after reset")
        except Exception as e:
            logger.error(f"[reset_demo_db] Failed to seed demo data: {e}")
            result["seeded"] = False
            result["message"] += f" | 播种失败: {e}"

    return result