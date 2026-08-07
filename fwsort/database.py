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
    "strategy_trade",
    "strategy_equity_curve",
    "execution_account",
    "order_execution_log",
    "vote_decision",
    "agent_prediction",
    "agent_collaboration",
    "agent_portfolio",
    "rank_snapshot",
    "follow_order",
    "follow_subscription",
    "rental_order",
    "rental_agent",
    "notification",
    "outbox_event",
    "user",
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
        logger.error(f"[reset_db] Database backup failed:{e}，traceback: {traceback.format_exc()}")
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
    # auto_strategy_log -> auto_strategy -> execution_account -> user
    # 其他表按字母顺序
    ordered_tables = [
        "auto_strategy_log",
        "strategy_trade",
        "strategy_equity_curve",
        "auto_strategy",
        "strategy_performance",
        "order_execution_log",
        "vote_decision",
        "agent_prediction",
        "agent_collaboration",
        "agent_portfolio",
        "rank_snapshot",
        "follow_order",
        "follow_subscription",
        "rental_order",
        "rental_agent",
        "notification",
        "outbox_event",
        "execution_account",
        "user",
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
                errors.append(f"{table}:{e}，traceback: {traceback.format_exc()}")
                logger.warning(f"[reset_db] Failed to clear {table}:{e}，traceback: {traceback.format_exc()}")

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
        logger.warning(f"[reset_db] Failed to clear Redis:{e}，traceback: {traceback.format_exc()}")

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
    """播种丰富的演示数据：20 用户 + 200 执行账户 + 绩效 + 500 投票 + 30 自动策略 + 日志 + 跟单 + 租用

    用于重置演示库后自动恢复演示数据，使演示库始终有充足的数据展示。
    """
    import random
    import uuid
    from datetime import datetime, timedelta

    from fwsort.models import (
        AgentCollaboration,
        AgentPortfolio,
        AgentPrediction,
        AutoStrategy,
        AutoStrategyLog,
        ExecutionAccount,
        FollowOrder,
        FollowSubscription,
        Notification,
        OrderExecutionLog,
        RankSnapshot,
        RentalAgent,
        RentalOrder,
        StrategyEquityCurve,
        StrategyPerformance,
        StrategyTrade,
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
    rng = random.Random(42)  # 固定种子保证可重现

    try:
        # ===== 1. 创建用户（1 管理员 + 19 普通用户）=====
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

        normal_users = []
        user_configs = [
            ("trader@fwquant.com", "trader123", "趋势猎人", 2),
            ("quant@fwquant.com", "quant123", "量化工匠", 2),
            ("beta@fwquant.com", "beta123", "Beta套利者", 2),
            ("gamma@fwquant.com", "gamma123", "Gamma网客", 2),
            ("delta@fwquant.com", "delta123", "Delta波段王", 2),
            ("epsilon@fwquant.com", "epsilon123", "Epsilon趋势", 2),
            ("zeta@fwquant.com", "zeta123", "Zeta对冲", 2),
            ("eta@fwquant.com", "eta123", "Eta高频", 2),
            ("theta@fwquant.com", "theta123", "Theta事件", 2),
            ("iota@fwquant.com", "iota123", "Iota突破", 2),
            ("kappa@fwquant.com", "kappa123", "Kappa反转", 2),
            ("lambda@fwquant.com", "lambda123", "Lambda做市", 2),
            ("mu@fwquant.com", "mu123", "Mu量化", 2),
            ("nu@fwquant.com", "nu123", "Nu套利", 2),
            ("xi@fwquant.com", "xi123", "Xi网格", 2),
            ("omicron@fwquant.com", "omicron123", "Omicron波段", 2),
            ("pi@fwquant.com", "pi123", "Pi趋势", 2),
            ("rho@fwquant.com", "rho123", "Rho对冲", 2),
            ("sigma@fwquant.com", "sigma123", "Sigma猎手", 2),
        ]
        for email, pwd, nick, role in user_configs:
            u = User(
                email=email,
                password_hash=hash_password(pwd),
                nickname=nick,
                role=role,
                status=0,
            )
            session.add(u)
            normal_users.append(u)
        session.flush()
        user_ids = [admin_id] + [u.id for u in normal_users]
        logger.info(f"[seed_demo] {len(user_ids)} users created")

        # ===== 2. 创建执行账户（200 个，跨多个用户）=====
        account_names = [
            "Alpha猎手", "Beta套利", "Gamma网格", "Delta波段", "Epsilon趋势",
            "Zeta对冲", "Eta高频", "Theta事件", "Iota突破", "Kappa反转",
            "Lambda做市", "GammaScalper", "DeltaArb", "EpsilonSwing", "ZetaHFT",
            "ETH猎手", "SOL趋势", "DOGE波段", "AVAX套利", "MATIC网格",
            "BTC趋势跟随", "ETH均值回归", "SOL突破", "BNB区间", "XRP事件驱动",
            "ADA逆势", "DOT套利", "LINK趋势", "ATOM波段", "LTC高频",
            "狗狗币狙击", "屎币短线", "小币轮动", "山寨币猎手", "DeFiAlpha",
            "稳定币套利", "跨市套利", "三角套利", "期现套利", "统计套利",
        ]
        platforms_list = ["polymarket", "okx", "polymarket", "okx"]
        symbols_map = {
            "polymarket": ["BTC", "ETH", "SOL", "USDC", "POLY"],
            "okx": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "AVAXUSDT"],
        }
        accounts = []
        for i in range(200):
            owner_id = user_ids[i % len(user_ids)]
            platform = platforms_list[(i // len(user_ids)) % len(platforms_list)]
            name = f"{account_names[i % len(account_names)]}-{i+1:03d}"
            acc = ExecutionAccount(
                uid=f"ACC-{uuid.uuid4().hex[:10].upper()}",
                owner_id=owner_id,
                name=name,
                platform=platform,
                account_type=rng.choice([0, 1, 2]),
                initial_balance=1000.0,
                current_balance=round(1000.0 + rng.uniform(-100, 500), 2),
                daily_pnl=round(rng.uniform(-150, 200), 2),
            )
            session.add(acc)
            accounts.append(acc)
        session.flush()
        logger.info(f"[seed_demo] {len(accounts)} accounts created")

        # ===== 3. 绩效记录 =====
        for acc in accounts:
            ann = round(rng.uniform(-0.15, 1.5), 4)
            dd = round(rng.uniform(0.02, 0.35), 4)
            sharpe = round(rng.uniform(0.2, 3.5), 2)
            plr = round(rng.uniform(0.7, 3.5), 2)
            ex = round(rng.uniform(0.55, 0.98), 4)
            score = composite_score(ann, dd, sharpe, plr, ex)
            sp = StrategyPerformance(
                account_id=acc.id,
                uid=acc.uid,
                period_type=4 if rng.random() < 0.8 else rng.choice([1, 2, 3]),
                start_time=datetime.now() - timedelta(days=rng.choice([7, 30, 90])),
                end_time=datetime.now(),
                annualized_return=ann,
                max_drawdown=dd,
                sharpe_ratio=sharpe,
                sortino_ratio=sharpe * rng.uniform(1.1, 1.4),
                calmar_ratio=ann / max(dd, 0.01),
                profit_factor=plr,
                win_rate=round(rng.uniform(0.40, 0.80), 4),
                profit_loss_ratio=plr,
                trade_count=rng.randint(50, 2000),
                execution_rate=round(rng.uniform(0.80, 0.99), 4),
                avg_slippage=round(rng.uniform(0.0001, 0.008), 6),
                avg_latency=rng.randint(100, 1000),
                cancel_rate=round(rng.uniform(0.005, 0.18), 4),
                execution_score=ex,
                composite_score=score,
            )
            session.add(sp)
        session.flush()
        logger.info("[seed_demo] performances created")

        # ===== 4. 排名快照 =====
        rank_types = [1, 2, 3, 4]  # 日/周/月/总
        for day in range(30, 0, -1):
            snap_time = datetime.now() - timedelta(days=day)
            rtype = rank_types[day % len(rank_types)]
            for acc in rng.sample(accounts, min(20, len(accounts))):
                score_val = round(rng.uniform(30, 95), 2)
                rs = RankSnapshot(
                    rank_type=rtype,
                    period_end_time=snap_time,
                    uid=acc.uid,
                    rank=0,
                    score=score_val,
                    execution_score=round(rng.uniform(0.5, 1.0), 4),
                    annualized_return=round(rng.uniform(-0.1, 1.2), 4),
                    max_drawdown=round(rng.uniform(0.02, 0.25), 4),
                    trade_count=rng.randint(10, 500),
                )
                session.add(rs)
        session.flush()
        logger.info("[seed_demo] rank snapshots created")

        # ===== 5. Agent 组合和协作 =====
        portfolios = []
        for i in range(10):
            pf = AgentPortfolio(
                name=f"Portfolio-{i+1}",
                strategy_uids=f'["ACC-{uuid.uuid4().hex[:8].upper()}","ACC-{uuid.uuid4().hex[:8].upper()}","ACC-{uuid.uuid4().hex[:8].upper()}"]',
                collaboration_mode=rng.choice([1, 2, 3, 4]),
                status=rng.choice([0, 0, 1]),
            )
            session.add(pf)
            portfolios.append(pf)
        session.flush()

        for pf in portfolios:
            for _ in range(rng.randint(3, 10)):
                collab = AgentCollaboration(
                    portfolio_id=pf.id,
                    from_uid=f"Agent-{rng.randint(1, 10):02d}",
                    to_uid=f"Agent-{rng.randint(1, 10):02d}",
                    message_type=rng.choice([1, 2, 3]),
                    message_content=f'{{"action":"{rng.choice(["signal","request","sync"])}","data":"mock"}}',
                )
                session.add(collab)
        session.flush()
        logger.info("[seed_demo] agent portfolios & collaborations created")

        # ===== 6. 投票决策 + 订单（300 条）=====
        symbols_list = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "AVAXUSDT"]
        timeframe_list = ["5m", "15m", "30m", "1h", "4h"]
        agents_list = [
            ("GPT-4o", "gpt-4o"),
            ("Claude", "claude-3-5-sonnet"),
            ("Gemini", "gemini-2.0-flash"),
            ("Deepseek", "deepseek-v3"),
            ("Ollama", "qwen2.5-7b"),
        ]
        for vi in range(500):
            acc = rng.choice(accounts)
            symbols = symbols_list if acc.platform == "okx" else ["BTC", "ETH", "SOL"]
            symbol = rng.choice(symbols)
            tf = rng.choice(timeframe_list)
            directions = [rng.choice([1, 1, 1, 2, 0]) for _ in range(3)]
            preds = []
            for d in directions:
                agent_name, agent_model = rng.choice(agents_list)
                ap = AgentPrediction(
                    agent_name=agent_name,
                    agent_model=agent_model,
                    symbol=symbol,
                    timeframe=tf,
                    direction=d,
                    confidence=round(rng.uniform(0.55, 0.95), 4),
                    reasoning="[MOCK seed] 模拟历史预测",
                    raw_payload='{"mock": true}',
                    latency_ms=rng.randint(80, 800),
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
                symbol=symbol,
                timeframe=tf,
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
                    price = round(max(0.01, min(0.99, 0.5 + rng.uniform(-0.15, 0.15))), 4)
                    qty = round(1.0 / price, 4)
                    slip = round(rng.uniform(0.0, 0.015), 6)
                elif acc.platform == "okx":
                    base_prices = {"BTCUSDT": 60000, "ETHUSDT": 3000, "SOLUSDT": 150, "DOGEUSDT": 0.15, "AVAXUSDT": 35}
                    base = base_prices.get(symbol, 100)
                    price = base + rng.uniform(-base * 0.02, base * 0.02)
                    qty = round(v.order_amount_usd / price, 6)
                    slip = round(rng.uniform(0.0, 0.005), 6)
                else:
                    price, qty, slip = 0.0, 0.0, 0.0

                status = rng.choice([2, 3, 3, 3, 3])  # 多数成功
                actual_price = price * (1 + slip * (1 if v.final_direction == 1 else -1))
                order_log = OrderExecutionLog(
                    uid=acc.uid,
                    account_id=acc.id,
                    vote_id=v_row.id,
                    order_id=order_id,
                    order_type=2,
                    side=v.final_direction,
                    platform=acc.platform,
                    symbol=symbol,
                    expected_price=price,
                    actual_price=actual_price,
                    quantity=qty,
                    amount_usd=v.order_amount_usd,
                    status=status,
                    latency_ms=rng.randint(50, 800),
                    slippage=slip,
                    pnl=round(rng.uniform(-10, 25), 2),
                )
                session.add(order_log)

        session.flush()
        logger.info(f"[seed_demo] 300 votes with orders created")

        # ===== 7. 自动策略任务（30 个）=====
        auto_task_configs = [
            ("BTC趋势追踪", "random", "polymarket_f3", 5, True, 0),
            ("ETH网格策略", "random", "okx", 10, True, 0),
            ("SOL高频交易", "random", "polymarket_f3", 2, True, 0),
            ("DOGE波段", "random", "okx", 15, True, 0),
            ("AVAX套利", "random", "polymarket_f3", 3, False, 0),
            ("MATIC做市", "random", "okx", 5, True, 0),
            ("跨市场套利", "http", "polymarket_f3", 1, True, 0),
            ("新闻事件驱动", "http", "okx", 30, True, 100),
            ("BTC熊市对冲", "random", "polymarket_f3", 10, False, 0),
            ("ETH牛市趋势", "random", "okx", 5, True, 0),
            ("DeFi脉冲交易", "http", "polymarket_f3", 2, True, 50),
            ("稳定币网格", "random", "okx", 20, True, 0),
            ("小币狙击", "http", "polymarket_f3", 1, True, 200),
            ("大盘指数轮动", "random", "okx", 15, False, 0),
            ("高胜率组合", "random", "polymarket_f3", 5, True, 0),
            ("山寨币Alpha", "http", "okx", 3, True, 50),
            ("BTC突破策略", "random", "polymarket_f3", 5, True, 0),
            ("ETH回调买入", "random", "okx", 10, True, 0),
            ("SOL日内交易", "random", "polymarket_f3", 2, True, 0),
            ("BNB区间震荡", "random", "okx", 15, True, 0),
            ("XRP事件驱动", "http", "polymarket_f3", 3, False, 0),
            ("ADA波段操作", "random", "okx", 5, True, 0),
            ("DOT跨链套利", "http", "polymarket_f3", 1, True, 0),
            ("LINK趋势跟随", "random", "okx", 30, True, 150),
            ("ATOM网格交易", "random", "polymarket_f3", 10, False, 0),
            ("LTC高频套利", "random", "okx", 5, True, 0),
            ("DOGE麦理论", "http", "polymarket_f3", 2, True, 80),
            ("AVAX突破反转", "random", "okx", 20, True, 0),
            ("MATIC做市策略", "random", "polymarket_f3", 1, True, 0),
            ("稳定币收益", "http", "okx", 15, False, 0),
        ]
        auto_tasks = []
        for i, (name, source, gateway, interval, active, loop) in enumerate(auto_task_configs):
            acc = accounts[i % len(accounts)]
            total = rng.randint(10, 200) if active else rng.randint(0, 5)
            success_cnt = int(total * rng.uniform(0.6, 0.95))
            fail_cnt = total - success_cnt
            task = AutoStrategy(
                task_name=name,
                signal_source=source,
                gateway=gateway,
                interval=interval,
                is_active=active,
                start_time=datetime.now() - timedelta(hours=rng.randint(1, 72)),
                loop_count=loop,
                executed_count=success_cnt + fail_cnt,
                config_json='{"max_amount": 50, "symbols": ["BTC", "ETH"]}',
                max_daily_amount=round(rng.uniform(20, 200), 2),
                max_daily_count=rng.randint(10, 100),
                max_consecutive_failures=rng.choice([3, 5, 10]),
                total_executions=total,
                total_success=success_cnt,
                total_failed=fail_cnt,
                consecutive_failures=0 if active else rng.randint(0, 5),
                account_id=acc.id,
                initial_balance=acc.initial_balance,
                current_balance=round(acc.current_balance + rng.uniform(-30, 80), 2),
                total_pnl=round(rng.uniform(-50, 200), 2),
                total_trades=total,
                win_trades=success_cnt,
                loss_trades=fail_cnt,
                win_rate=round(success_cnt / max(total, 1) * 100, 2),
                max_drawdown=round(rng.uniform(0.02, 0.2), 4),
                sharpe_ratio=round(rng.uniform(0.3, 2.5), 2),
                profit_loss_ratio=round(rng.uniform(0.8, 3.0), 2),
            )
            session.add(task)
            auto_tasks.append(task)
        session.flush()
        logger.info(f"[seed_demo] {len(auto_tasks)} auto strategies created")

        # ===== 8. 自动策略执行日志（每个任务多条日志）=====
        log_action_types = ["execute_manual", "init_gateway", "start", "stop", "fuse_triggered", "update"]
        for task in auto_tasks:
            num_logs = rng.randint(5, 25)
            for li in range(num_logs):
                log_type = rng.choice([0, 0, 0, 1])  # 多数执行日志
                action = rng.choice(log_action_types)
                status = rng.choice([0, 0, 0, 0, 1, 2, 3, 4])
                direction = rng.choice([1, 1, 2, 0])
                sym = rng.choice(symbols_list if task.gateway == "okx" else ["BTC", "ETH", "SOL"])
                is_profit = rng.random() < 0.65
                pnl_amt = round(rng.uniform(-5, 20), 2) if is_profit else round(rng.uniform(-15, -1), 2)
                entry_p = round(rng.uniform(0.01, 100), 4)
                exit_p = round(entry_p * (1 + rng.uniform(-0.1, 0.15)), 4)
                log_entry = AutoStrategyLog(
                    task_id=task.id,
                    log_type=log_type,
                    action_type=action,
                    executed_at=datetime.now() - timedelta(minutes=rng.randint(1, 4320)),
                    signal_json=f'{{"symbol":"{sym}","direction":"{"UP" if direction == 1 else "DOWN" if direction == 2 else "FLAT"}","confidence":{round(rng.uniform(0.5, 0.95), 2)}}}',
                    order_result_json=f'{{"order_id":"ORD-{uuid.uuid4().hex[:12].upper()}","status":"{"filled" if status == 0 else "failed"}"}}',
                    status=status,
                    error_message="" if status == 0 else f"模拟错误: 网关超时",
                    duration_ms=rng.randint(100, 3000),
                    order_id=f"ORD-{uuid.uuid4().hex[:16].upper()}",
                    detail_json='{"mock": true}',
                    signal_detail_json=f'{{"symbol":"{sym}","direction":{direction},"amount":{round(rng.uniform(10, 100), 2)}}}',
                    execution_detail_json='{"gateway":"f3","side":"buy","order_type":"market"}',
                    result_detail_json='{"filled_amount":1.0,"price":50.0}',
                    pnl_amount=pnl_amt,
                    pnl_percent=round(pnl_amt / max(entry_p, 0.001) * 100, 2),
                    is_profit=is_profit,
                    market_resolved=rng.random() < 0.3,
                    entry_price=entry_p,
                    exit_price=exit_p,
                )
                session.add(log_entry)
        session.flush()
        logger.info("[seed_demo] auto strategy logs created")

        # ===== 9. 策略交易明细（100 条）=====
        for ti in range(100):
            task = rng.choice(auto_tasks)
            acc = task.account or rng.choice(accounts)
            direction = rng.choice(["UP", "DOWN", "NEUTRAL"])
            side = 1 if direction == "UP" else 2
            is_profit = rng.random() < 0.6
            entry_p = round(rng.uniform(0.01, 80000), 4)
            if is_profit:
                exit_p = round(entry_p * (1 + rng.uniform(0.01, 0.2)), 4)
                pnl = round(rng.uniform(1, 30), 2)
            else:
                exit_p = round(entry_p * (1 - rng.uniform(0.01, 0.15)), 4)
                pnl = round(rng.uniform(-20, -0.5), 2)
            trade = StrategyTrade(
                trade_uid=f"TRD-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}",
                strategy_name=task.task_name,
                auto_strategy_id=task.id,
                account_id=acc.id,
                source_strategy=task.signal_source,
                platform=acc.platform,
                symbol=rng.choice(symbols_list if acc.platform == "okx" else ["BTC", "ETH", "SOL"]),
                market_question="Will BTC close above $60000?",
                market_slug=f"btc-{rng.choice(['bull', 'bear'])}-{ti}",
                direction=direction,
                side=side,
                order_type=2,
                order_id=f"ORD-{uuid.uuid4().hex[:16].upper()}",
                entry_price=entry_p,
                exit_price=exit_p,
                quantity=round(rng.uniform(0.1, 5.0), 4),
                amount_usd=round(rng.uniform(10, 200), 2),
                pnl_amount=pnl,
                pnl_percent=round(pnl / max(entry_p, 0.001) * 100, 2),
                is_profit=is_profit,
                is_win=is_profit,
                entry_at=datetime.now() - timedelta(hours=rng.randint(1, 720)),
                exit_at=datetime.now() - timedelta(hours=rng.randint(0, 700)),
                hold_duration_seconds=rng.randint(60, 86400),
                status=1 if is_profit else 2,
                market_resolved=True,
                slippage=round(rng.uniform(0.001, 0.02), 4),
                latency_ms=rng.randint(100, 500),
            )
            session.add(trade)
        session.flush()
        logger.info("[seed_demo] strategy trades created")

        # ===== 10. 资金曲线 =====
        for task in auto_tasks:
            curve_entries = []
            peak = 1000.0
            for d in range(30):
                ts = datetime.now() - timedelta(days=29 - d)
                daily_pnl = round(rng.uniform(-5, 20), 2)
                equity = round(peak + daily_pnl, 2)
                if equity > peak:
                    peak = equity
                dd = round((peak - equity) / max(peak, 0.01) * 100, 2)
                curve_entries.append(StrategyEquityCurve(
                    strategy_name=task.task_name,
                    account_id=task.account_id,
                    snapshot_date=ts,
                    equity=equity,
                    balance=equity,
                    daily_pnl=daily_pnl,
                    daily_pnl_percent=round(daily_pnl / max(equity - daily_pnl, 0.01) * 100, 2),
                    peak_equity=peak,
                    drawdown=round(peak - equity, 2),
                    drawdown_percent=dd,
                    max_drawdown_percent=round(rng.uniform(0.02, 0.2), 4),
                    position_count=rng.randint(0, 5),
                    trade_count=rng.randint(0, 20),
                ))
            session.add_all(curve_entries)
        session.flush()
        logger.info("[seed_demo] equity curves created")

        # ===== 11. 租用智能体 + 订单 =====
        rental_agents = []
        agent_configs = [
            ("趋势大师", "gpt-4o", "trend", 0.15, 0.80),
            ("套利专家", "claude-3-5-sonnet", "arbitrage", 0.20, 1.00),
            ("情绪猎手", "gemini-2.0-flash", "sentiment", 0.10, 0.60),
            ("链上分析师", "deepseek-v3", "onchain", 0.25, 1.20),
            ("高频交易员", "gpt-4o", "hft", 0.30, 1.50),
            ("事件驱动", "claude-3-5-sonnet", "event", 0.12, 0.70),
            ("风险管理师", "qwen2.5-7b", "risk", 0.08, 0.50),
            ("组合优化器", "gpt-4o", "portfolio", 0.18, 0.90),
            ("信号聚合器", "gemini-2.0-flash", "aggregator", 0.10, 0.55),
            ("智能跟单", "claude-3-5-sonnet", "copytrade", 0.14, 0.75),
            ("网格大师", "gpt-4o", "grid", 0.16, 0.85),
            ("波段之王", "claude-3-5-sonnet", "swing", 0.18, 0.95),
            ("Alpha挖掘", "deepseek-v3", "alpha", 0.22, 1.10),
            ("稳定币收益", "qwen2.5-7b", "stablecoin", 0.08, 0.45),
            ("跨期套利", "gpt-4o", "crossperiod", 0.20, 1.05),
            ("期权策略", "claude-3-5-sonnet", "options", 0.25, 1.30),
            ("DeFi研究员", "gemini-2.0-flash", "defi", 0.15, 0.80),
            ("NFT猎手", "deepseek-v3", "nft", 0.12, 0.65),
            ("元宇宙策略", "qwen2.5-7b", "metaverse", 0.10, 0.55),
            ("宏观对冲", "gpt-4o", "macro", 0.30, 1.60),
        ]
        for name, model, atype, ppc, pph in agent_configs:
            ra = RentalAgent(
                name=name,
                model=model,
                description=f"模拟租用智能体：{name}，擅长 {atype} 策略",
                agent_type=atype,
                price_per_call_usd=ppc,
                price_per_hour_usd=pph,
                max_concurrent=rng.randint(5, 30),
                is_active=True,
            )
            session.add(ra)
            rental_agents.append(ra)
        session.flush()

        for _ in range(30):
            renter = rng.choice(normal_users)
            agent = rng.choice(rental_agents)
            rtype = rng.choice([1, 2])
            hours = rng.randint(1, 72) if rtype == 2 else 0
            order = RentalOrder(
                renter_id=renter.id,
                agent_id=agent.id,
                rental_type=rtype,
                hours=hours,
                used_calls=rng.randint(0, 200) if rtype == 1 else 0,
                total_paid_usd=round(rng.uniform(10, 200), 2),
                status=rng.choice([0, 1, 2]),
                started_at=datetime.now() - timedelta(hours=rng.randint(1, 500)),
            )
            session.add(order)
        session.flush()
        logger.info("[seed_demo] rental agents and orders created")

        # ===== 12. 跟单订阅 + 订单 =====
        leaders = rng.sample(accounts, min(20, len(accounts)))
        for leader in leaders:
            for sub_idx in range(rng.randint(1, 4)):
                subscriber = rng.choice(normal_users)
                sub = FollowSubscription(
                    subscriber_id=subscriber.id,
                    leader_uid=leader.uid,
                    leader_name=leader.name,
                    mode=rng.choice([1, 2, 3]),
                    subscription_fee_usd=round(rng.uniform(5, 30), 2),
                    profit_share_ratio=round(rng.uniform(0.10, 0.30), 4),
                    follow_amount_usd=round(rng.uniform(20, 100), 2),
                    total_followed=rng.randint(5, 100),
                    total_pnl=round(rng.uniform(-100, 300), 2),
                    total_fee_paid=round(rng.uniform(20, 100), 2),
                    total_share_paid=round(rng.uniform(-30, 50), 2),
                    status=1,
                    expires_at=datetime.now() + timedelta(days=rng.randint(7, 90)),
                )
                session.add(sub)
                session.flush()

                for _ in range(rng.randint(3, 15)):
                    fo = FollowOrder(
                        subscription_id=sub.id,
                        leader_order_id=f"ORD-{uuid.uuid4().hex[:16].upper()}",
                        symbol=rng.choice(symbols_list),
                        side=rng.choice([1, 2]),
                        amount_usd=round(rng.uniform(10, 100), 2),
                        expected_price=round(rng.uniform(0.01, 80000), 4),
                        actual_price=round(rng.uniform(0.01, 80000), 4),
                        pnl=round(rng.uniform(-10, 20), 2),
                        share_paid=round(rng.uniform(-5, 10), 2),
                        status=3,
                    )
                    session.add(fo)
        session.flush()
        logger.info("[seed_demo] follow subscriptions and orders created")

        # ===== 13. 通知 =====
        for _ in range(50):
            target_user = rng.choice(user_ids)
            notif = Notification(
                user_id=target_user,
                ntype=rng.choice([1, 2, 3, 4, 5]),
                title=rng.choice([
                    "策略执行成功", "风控预警", "系统更新", "订单已成交",
                    "自动策略熔断", "新跟单订阅", "租用即将过期", "每日盈亏报告",
                ]),
                content=rng.choice([
                    "您的策略 BTC趋势追踪 执行成功，盈利 +$12.50",
                    "账户波动超过阈值，请检查风控设置",
                    "系统已更新至最新版本，新增多个功能",
                    "订单已成交：BTC 买入 0.5 手 @ $60,000",
                    "自动策略 ETH网格策略 触发熔断，已暂停执行",
                    "用户 quant@fwquant.com 订阅了您的跟单",
                    "您租用的智能体 趋势大师 即将过期（剩余 2 小时）",
                    "今日盈亏：+$85.30，胜率 68%",
                ]),
                is_read=rng.random() < 0.5,
            )
            session.add(notif)
        session.flush()
        logger.info("[seed_demo] notifications created")

        session.commit()
        total_counts = {
            "users": len(user_ids),
            "accounts": len(accounts),
            "performances": len(accounts),
            "rank_snapshots": "~600",
            "votes": 300,
            "auto_tasks": len(auto_tasks),
            "auto_logs": "~200",
            "trades": 30,
            "rental_agents": len(rental_agents),
            "subscriptions": "~20",
            "notifications": 20,
        }
        logger.info(f"[seed_demo] demo data seeded successfully: {total_counts}")
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
            logger.error(f"[reset_demo_db] Failed to seed demo data:{e}，traceback: {traceback.format_exc()}")
            result["seeded"] = False
            result["message"] += f" | 播种失败:{e}，traceback: {traceback.format_exc()}"

    return result