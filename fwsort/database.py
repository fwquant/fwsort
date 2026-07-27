# 数据库连接：SQLAlchemy 2.0 同步/异步双引擎
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from fwsort.config import settings


# 声明式基类
class Base(DeclarativeBase):
    """所有 ORM 模型的基类"""

    pass


# ========== 引擎构造（SQLite 走文件，PostgreSQL 走连接池）==========
if settings.USE_SQLITE:
    sync_engine = create_engine(
        settings.sync_dsn,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
        echo=settings.APP_DEBUG,
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
        echo=settings.APP_DEBUG,
    )
else:
    sync_engine = create_engine(
        settings.postgres_dsn,
        pool_size=settings.POSTGRES_POOL_SIZE,
        max_overflow=settings.POSTGRES_MAX_OVERFLOW,
        pool_pre_ping=True,
        echo=settings.APP_DEBUG,
    )
    async_engine = create_async_engine(
        settings.postgres_async_dsn,
        pool_size=settings.POSTGRES_POOL_SIZE,
        max_overflow=settings.POSTGRES_MAX_OVERFLOW,
        pool_pre_ping=True,
        echo=settings.APP_DEBUG,
    )

# 会话工厂
SyncSessionLocal = sessionmaker(bind=sync_engine, autoflush=False, autocommit=False)
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine, class_=AsyncSession, autoflush=False, autocommit=False
)


@contextmanager
def get_sync_db() -> Generator[Session, None, None]:
    """同步会话上下文（Celery 任务、初始化脚本）"""
    db = SyncSessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def get_async_db() -> AsyncSession:
    """异步会话生成器（FastAPI Depends 注入）"""
    async with AsyncSessionLocal() as session:
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
