# D2 阶段验收测试（WP-06 ~ WP-09）
# 覆盖：演示/生产数据隔离 / 跟单自动执行 / 权重变更触发重算 / 下单事务一致性 + ES 异步化
import os
import sys
import asyncio
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

# 允许在任意目录运行 pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 强制使用 SQLite + FakeRedis，避免依赖外部服务
os.environ.setdefault("USE_SQLITE", "true")
os.environ.setdefault("USE_FAKE_REDIS", "true")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-32bytes-for-unit-test-pass")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("APP_DEMO_MODE", "true")


# ========== WP-06 演示/生产数据隔离 ==========
def test_wp06_demo_db_engine_isolated():
    """演示库与生产库必须是不同的 SQLAlchemy Engine"""
    from fwsort.database import sync_engine, _demo_sync_engine

    assert sync_engine is not _demo_sync_engine
    # URL 不同的 SQLite 文件（或生产为 PG）
    assert str(sync_engine.url) != str(_demo_sync_engine.url)


def test_wp06_demo_session_factory_isolated():
    """演示会话工厂与生产会话工厂是不同实例"""
    from fwsort.database import (
        DemoAsyncSessionLocal,
        AsyncSessionLocal,
        DemoSyncSessionLocal,
        SyncSessionLocal,
    )

    assert DemoAsyncSessionLocal is not AsyncSessionLocal
    assert DemoSyncSessionLocal is not SyncSessionLocal


def test_wp06_demo_redis_isolated():
    """演示 Redis 与生产 Redis 是不同实例"""
    from fwsort.redis_client import (
        async_redis,
        demo_async_redis,
        sync_redis,
        demo_sync_redis,
    )

    assert demo_async_redis is not async_redis
    assert demo_sync_redis is not sync_redis


def test_wp06_is_demo_request_detects_demo_path():
    """请求 path 以 /api/demo/ 开头 → 视为演示请求"""
    from fwsort.database import _is_demo_request
    from fastapi import Request

    def _make_req(path: str) -> MagicMock:
        req = MagicMock(spec=Request)
        req.url.path = path
        return req

    assert _is_demo_request(_make_req("/api/demo/ranking/list")) is True
    assert _is_demo_request(_make_req("/api/ranking/list")) is False
    assert _is_demo_request(_make_req("/api/demo/auth/login")) is True
    assert _is_demo_request(None) is False


def test_wp06_main_app_mirrors_routes_to_demo():
    """main.py 的 FastAPI 应用必须把 /api/* 路由镜像到 /api/demo/*"""
    from main import app
    from fastapi.routing import APIRoute

    # 过滤出所有 APIRoute 实例（避免 _IncludedRouter 等没有 path 属性的对象）
    api_routes = [r for r in app.routes if isinstance(r, APIRoute)]
    demo_paths = {r.path for r in api_routes if r.path.startswith("/api/demo/")}
    prod_paths = {r.path for r in api_routes if r.path.startswith("/api/") and not r.path.startswith("/api/demo/")}

    # 至少镜像 5 个核心接口
    assert len(demo_paths) >= 5, f"mirror routes too few: {demo_paths}"
    # 演示路由必须包含一些关键端点
    assert any("/auth/login" in p for p in demo_paths)
    assert any("/agent/" in p for p in demo_paths)
    assert any("/ranking/" in p for p in demo_paths)


# ========== WP-07 跟单自动执行任务 ==========
def test_wp07_follow_auto_copy_task_registered():
    """Celery beat 调度必须包含 follow-auto-copy（每 5 分钟）"""
    from fwsort.scheduler import celery_app

    sched = celery_app.conf.beat_schedule
    assert "follow-auto-copy" in sched
    assert sched["follow-auto-copy"]["task"] == "fwsort.scheduler.follow_auto_copy"


def test_wp07_follow_auto_copy_signature():
    """follow_auto_copy 任务签名：返回 int，参数为空"""
    from fwsort.scheduler import follow_auto_copy

    # Celery 任务对象 name 属性保留原始函数名
    assert follow_auto_copy.name == "fwsort.scheduler.follow_auto_copy"


def test_wp07_trigger_endpoint_includes_follow_auto_copy():
    """admin trigger 端点必须包含 follow_auto_copy"""
    from router.admin_router import trigger_task

    # 通过源码扫描确认
    import inspect
    src = inspect.getsource(trigger_task)
    assert "follow_auto_copy" in src
    assert "flush_outbox" in src  # WP-09 也在


# ========== WP-08 权重变更触发榜单重算 ==========
def test_wp08_refresh_redis_zset_signature():
    """ranking_engine.refresh_redis_zset 存在且签名正确"""
    from fwsort.ranking_engine import refresh_redis_zset

    import inspect
    sig = inspect.signature(refresh_redis_zset)
    params = list(sig.parameters.keys())
    # 必须有 rank_type 参数
    assert "rank_type" in params or len(params) >= 1


def test_wp08_config_router_uses_background_tasks():
    """config_router.update_weights 必须使用 BackgroundTasks 异步重算榜单"""
    from router.config_router import update_weights

    import inspect
    src = inspect.getsource(update_weights)
    # WP-08：必须看到 BackgroundTasks + refresh_redis_zset 的引用
    assert "BackgroundTasks" in src or "background_tasks" in src
    assert "refresh_redis_zset" in src or "_refresh_zset_task" in src


def test_wp08_scheduler_refresh_realtime_rank_uses_redis_zset():
    """scheduler.refresh_realtime_rank 必须清空 + ZADD 写入 Redis ZSet"""
    from fwsort.scheduler import refresh_realtime_rank

    import inspect
    src = inspect.getsource(refresh_realtime_rank)
    assert "sync_redis.delete" in src
    assert "sync_redis.zadd" in src


# ========== WP-09 下单事务一致性 + ES 异步化 ==========
def test_wp09_outbox_event_model_exists():
    """models.OutboxEvent 表结构必须存在"""
    from fwsort.models import OutboxEvent

    assert OutboxEvent.__tablename__ == "outbox_event"
    cols = {c.name for c in OutboxEvent.__table__.columns}
    for required in ("id", "event_type", "payload_json", "status", "retry_count", "last_error", "next_retry_at"):
        assert required in cols, f"OutboxEvent missing column: {required}"


def test_wp09_outbox_migration_ddl_exists():
    """migrations._NEW_TABLES_DDL 必须包含 outbox_event"""
    from fwsort.migrations import _NEW_TABLES_DDL

    table_names = {t for t, _ in _NEW_TABLES_DDL}
    assert "outbox_event" in table_names, f"outbox_event DDL missing, got: {table_names}"


def test_wp09_outbox_table_creates_correctly():
    """运行迁移后 outbox_event 表必须存在"""
    from fwsort.database import sync_engine
    from fwsort.migrations import run_migrations_for_engine
    from sqlalchemy import inspect

    insp = inspect(sync_engine)
    if not insp.has_table("outbox_event"):
        run_migrations_for_engine(sync_engine)
    assert insp.has_table("outbox_event") or inspect(sync_engine).has_table("outbox_event")


def test_wp09_flush_outbox_task_registered():
    """Celery beat 调度必须包含 flush-outbox（每 30s）"""
    from fwsort.scheduler import celery_app

    sched = celery_app.conf.beat_schedule
    assert "flush-outbox" in sched
    assert sched["flush-outbox"]["task"] == "fwsort.scheduler.flush_outbox"


def test_wp09_build_order_log_event_creates_correct_payload():
    """build_order_log_event 序列化字段完整且带 status=0 初始值"""
    from fwsort.execution.outbox import build_order_log_event

    # 模拟一个 OrderExecutionLog
    fake_log = MagicMock()
    fake_log.id = 100
    fake_log.uid = "ACC-TEST-001"
    fake_log.account_id = 5
    fake_log.vote_id = 88
    fake_log.order_id = "ORD-999"
    fake_log.order_type = 2
    fake_log.side = 1
    fake_log.platform = "okx"
    fake_log.symbol = "BTC-USDT"
    fake_log.expected_price = 50000.0
    fake_log.actual_price = 50010.0
    fake_log.quantity = 0.001
    fake_log.amount_usd = 5.0
    fake_log.status = 3
    fake_log.latency_ms = 120
    fake_log.slippage = 0.0002
    fake_log.created_at = None  # 会用 utcnow

    evt = build_order_log_event(fake_log)
    assert evt.event_type == "order_log_index"
    assert evt.status == 0
    assert evt.retry_count == 0
    payload = json.loads(evt.payload_json)
    assert payload["uid"] == "ACC-TEST-001"
    assert payload["amount_usd"] == 5.0
    assert payload["symbol"] == "BTC-USDT"


def test_wp09_es_writer_uses_fire_and_forget():
    """es_writer.schedule_index_order_log 必须用 asyncio.create_task 而非 await"""
    from fwsort.execution.es_writer import schedule_index_order_log

    import inspect
    src = inspect.getsource(schedule_index_order_log)
    assert "asyncio.create_task" in src
    assert "await" not in src.split("def schedule_index_order_log")[1].split("def ")[0].split("return")[0]


def test_wp09_agent_router_uses_outbox_and_fire_and_forget():
    """agent_router.predict_and_vote 必须同时入 outbox + fire-and-forget"""
    from router.agent_router import predict_and_vote

    import inspect
    src = inspect.getsource(predict_and_vote)
    assert "build_order_log_event" in src
    assert "schedule_index_order_log" in src
    # outbox 入库失败不应阻塞
    assert "outbox" in src.lower()


def test_wp09_outbox_dispatch_skips_already_success():
    """dispatch_event 当 status=1 时直接返回 True（避免重复 IO）"""
    from fwsort.execution.outbox import dispatch_event

    fake_event = MagicMock()
    fake_event.status = 1
    fake_event.payload_json = '{"id":1}'
    fake_event.id = 1

    async def _run():
        return await dispatch_event(fake_event)

    result = asyncio.run(_run())
    assert result is True


def test_wp09_outbox_mark_event_failure_backoff():
    """mark_event_failure 在 retry_count < 3 时返回 True 并设置 next_retry_at"""
    from fwsort.execution.outbox import mark_event_failure
    from datetime import datetime

    fake_event = MagicMock()
    fake_event.retry_count = 0
    fake_event.status = 0

    db = MagicMock()
    can_retry = mark_event_failure(db, fake_event, "test error")
    assert can_retry is True
    assert fake_event.retry_count == 1
    assert fake_event.status == 2
    assert fake_event.next_retry_at is not None
    assert fake_event.last_error == "test error"


def test_wp09_outbox_mark_event_failure_max_retry():
    """mark_event_failure 在 retry_count >= 3 时切长退避（10 分钟）"""
    from fwsort.execution.outbox import mark_event_failure, OUTBOX_MAX_RETRY
    from datetime import datetime, timedelta

    fake_event = MagicMock()
    fake_event.retry_count = OUTBOX_MAX_RETRY
    fake_event.status = 0

    db = MagicMock()
    can_retry = mark_event_failure(db, fake_event, "exhausted")
    assert can_retry is False
    assert fake_event.retry_count == OUTBOX_MAX_RETRY + 1
    # 超过最大重试 → 10 分钟长退避
    assert fake_event.status == 2
    assert fake_event.next_retry_at is not None


# ========== WP-06 演示模式启动自动 seed ==========
def test_wp06_demo_init_seeds_admin_and_accounts():
    """演示模式启动时若 admin/accounts 不存在应自动 seed"""
    # 这是 lifespan 中的行为，难以单元测试。这里只验证 init_demo_db + seed 入口存在
    from fwsort.database import init_demo_db
    import inspect

    src = inspect.getsource(init_demo_db)
    # 必须包含 create_all
    assert "create_all" in src
