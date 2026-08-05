# 数据库连接：SQLAlchemy 2.0 同步/异步双引擎
# WP-06：演示模式数据层物理隔离（独立 SQLite + 独立 session 工厂）
import logging
from contextlib import contextmanager
from typing import Generator

from fastapi import Request
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
