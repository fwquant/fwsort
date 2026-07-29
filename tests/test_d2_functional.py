"""D2 阶段验收测试（WP-06/07/08/09）
覆盖：
- WP-06 物理隔离（独立 SQLite + 独立 Redis）
- WP-07 跟单自动执行（follow_auto_copy）
- WP-08 权重重算（refresh_redis_zset）
- WP-09 ES 异步化（schedule_index_order_log）+ outbox（flush_outbox_sync）
"""
import os
import sys
import asyncio
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

# 允许在任意目录运行 pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 强制使用 SQLite + FakeRedis，避免依赖外部服务
os.environ.setdefault("USE_SQLITE", "true")
os.environ.setdefault("USE_FAKE_REDIS", "true")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-32bytes-for-unit-test-pass")
os.environ.setdefault("APP_ENV", "development")


# ========== WP-06 物理隔离 ==========
def test_wp06_demo_db_is_separate_file():
    """WP-06：演示模式使用独立 SQLite 文件，与生产完全分离"""
    from fwsort.config import settings
    from fwsort.database import _demo_sync_engine, sync_engine

    # 不同 engine 实例
    assert _demo_sync_engine is not sync_engine, "demo engine must be different from prod engine"
    # 不同 DSN
    demo_url = str(_demo_sync_engine.url)
    prod_url = str(sync_engine.url)
    assert demo_url != prod_url, f"demo and prod must use different DSN, got demo={demo_url} prod={prod_url}"
    # 演示模式开启
    assert settings.APP_DEMO_MODE is True


def test_wp06_demo_session_local_uses_demo_engine():
    """WP-06：DemoSyncSessionLocal 绑定到演示引擎"""
    from fwsort.database import DemoSyncSessionLocal, _demo_sync_engine

    sess = DemoSyncSessionLocal()
    assert sess.bind is _demo_sync_engine
    sess.close()


def test_wp06_demo_redis_is_isolated():
    """WP-06：demo_async_redis / demo_sync_redis 与 prod 内存实例分离"""
    from fwsort.redis_client import demo_async_redis, demo_sync_redis, async_redis, sync_redis

    # 不同对象
    assert demo_async_redis is not async_redis
    assert demo_sync_redis is not sync_redis
    # 类型校验
    assert hasattr(demo_async_redis, "zadd")
    assert hasattr(demo_sync_redis, "zadd")


def test_wp06_demo_redis_key_namespacing():
    """WP-06：demo_redis_key 加命名空间前缀（共享 Redis 时隔离）"""
    from fwsort.redis_client import demo_redis_key

    assert demo_redis_key("fwsort:rank:realtime") == "fwsort_demo:fwsort:rank:realtime"
    # 已经有前缀的不重复加
    prefixed = "fwsort_demo:already_prefixed"
    assert demo_redis_key(prefixed) == prefixed


# ========== WP-07 跟单自动执行 ==========
def test_wp07_follow_auto_copy_runs_without_error():
    """WP-07：follow_auto_copy 任务可同步执行不抛错（即使无订阅）"""
    from fwsort.scheduler import follow_auto_copy

    result = follow_auto_copy.apply().get(timeout=10)
    assert isinstance(result, int)
    assert result >= 0


def test_wp07_execute_follow_copy_creates_record():
    """WP-07：execute_follow_copy 写入 FollowOrder 并更新订阅统计"""
    import uuid
    from fwsort.database import init_db, init_demo_db, AsyncSessionLocal
    from fwsort.models import (
        ExecutionAccount,
        FollowOrder,
        FollowSubscription,
        User,
    )
    from fwsort.security import hash_password
    from router.follow_router import execute_follow_copy
    from datetime import datetime, timedelta

    init_db()
    # 使用唯一 email 避免 UNIQUE 冲突（不依赖 db.delete 顺序）
    u_l = f"leader-{uuid.uuid4().hex[:8]}@x.com"
    u_f = f"follower-{uuid.uuid4().hex[:8]}@x.com"
    async def _run():
        async with AsyncSessionLocal() as db:
            leader_user = User(
                email=u_l,
                nickname="leader",
                password_hash=hash_password("p"),
                role=0,
                status=0,
                allow_follow=True,
            )
            follower_user = User(
                email=u_f,
                nickname="follower",
                password_hash=hash_password("p"),
                role=0,
                status=0,
            )
            db.add(leader_user)
            db.add(follower_user)
            await db.flush()
            leader_acc = ExecutionAccount(
                uid=f"L-{uuid.uuid4().hex[:8]}",
                owner_id=leader_user.id,
                name="L",
                platform="okx",
                account_type=0,
                initial_balance=10000,
                current_balance=10000,
                order_amount_usd=5,
                public_enabled=True,
                status=0,
            )
            db.add(leader_acc)
            await db.flush()
            sub = FollowSubscription(
                subscriber_id=follower_user.id,
                leader_uid=leader_acc.uid,
                mode=3,
                follow_amount_usd=10,
                subscription_fee_usd=10,
                profit_share_ratio=0.20,
                status=1,
                expires_at=datetime.now() + timedelta(days=30),
            )
            db.add(sub)
            await db.flush()

            order = await execute_follow_copy(
                db, sub,
                leader_order_id="ORD-1",
                symbol="BTCUSDT",
                side=1,  # UP
                expected_price=50000.0,
                actual_price=50500.0,  # 涨了 1%
            )
            await db.commit()
            await db.refresh(order)
            await db.refresh(sub)

            # 验证：FollowOrder 已创建
            assert order.id is not None, "FollowOrder id 缺失"
            assert order.amount_usd == 10, f"amount_usd 期望 10, 实际 {order.amount_usd}"
            assert order.pnl > 0, f"pnl 应为正, 实际 {order.pnl}"
            assert order.share_paid > 0, f"mode=3 利润分成应 >0, 实际 {order.share_paid}"
            # 订阅统计累加
            assert sub.total_followed == 1
            assert sub.total_pnl > 0
            assert sub.total_share_paid > 0

    asyncio.run(_run())


# ========== WP-08 权重重算 ==========
def test_wp08_refresh_redis_zset_updates_scores():
    """WP-08：refresh_redis_zset 重算 composite_score 并写回 Redis ZSet"""
    import uuid
    from fwsort.database import init_db, SyncSessionLocal
    from fwsort.models import ExecutionAccount, StrategyPerformance, WeightConfig
    from fwsort.ranking_engine import refresh_redis_zset, composite_score
    from fwsort.redis_client import sync_redis, rank_key, RankType
    from datetime import datetime, timedelta

    init_db()
    test_tag = f"WP08A-{uuid.uuid4().hex[:6]}"
    with SyncSessionLocal() as db:
        from fwsort.models import User
        from fwsort.security import hash_password
        # 准备 owner（FK：ExecutionAccount.owner_id -> user.id）
        owner = User(
            email=f"{test_tag}@owner.com",
            nickname=test_tag,
            password_hash=hash_password("p"),
            role=0,
            status=0,
        )
        db.add(owner)
        db.flush()
        # 清理本 tag 残留的 StrategyPerformance
        db.query(StrategyPerformance).filter(StrategyPerformance.uid.like(f"{test_tag}%")).delete()
        # 同时清理可能孤立的 ExecutionAccount（account 字段可能约束）
        db.commit()
        # 先创建 ExecutionAccount（FK 依赖）
        acc_ids = []
        for i in range(3):
            acc = ExecutionAccount(
                uid=f"{test_tag}-ACC-{i}",
                owner_id=owner.id,
                name=f"ACC-{i}",
                platform="okx",
                account_type=0,
                initial_balance=10000,
                current_balance=10000,
                order_amount_usd=5,
                public_enabled=True,
                status=0,
            )
            db.add(acc)
            db.flush()
            acc_ids.append(acc.id)
        # 再创建 StrategyPerformance（带真实 account_id）
        for i, aid in enumerate(acc_ids):
            sp = StrategyPerformance(
                uid=f"{test_tag}-{i}",
                account_id=aid,
                period_type=4,  # ALL_TIME
                start_time=datetime.now() - timedelta(days=30),
                end_time=datetime.now(),
                annualized_return=0.5 + i * 0.1,
                max_drawdown=0.1 + i * 0.05,
                sharpe_ratio=1.0 + i * 0.5,
                profit_loss_ratio=1.5,
                execution_score=0.8,
                composite_score=0,
                trade_count=100,
            )
            db.add(sp)
        db.commit()
        # 写一个 weight config（rank_type=4）
        cfg = db.query(WeightConfig).filter(WeightConfig.rank_type == 4).first()
        if cfg is None:
            cfg = WeightConfig(
                rank_type=4,
                weight_annualized=0.30,
                weight_drawdown=0.20,
                weight_sharpe=0.20,
                weight_profit_loss=0.15,
                weight_execution=0.15,
            )
            db.add(cfg)
            db.commit()
        # 重算（覆盖式：weight 变化后 ZSet 会被新分数刷新）
        result = refresh_redis_zset(db, rank_type=4)
        db.commit()
        # 验证：本次新增的 3 条都进 ZSet 且分数被更新
        assert result["updated"] >= 3, f"updated 期望 ≥3, 实际 {result['updated']}"
        # 验证 ZSet 写入（≥3 是本次新增的）
        from fwsort.redis_client import rank_key, RankType
        zkey = rank_key(RankType.ALL_TIME)
        count = sync_redis.zcard(zkey)
        assert count >= 3, f"ZSet 成员数 ≥3, 实际 {count}"
        # 验证 DB 分数更新
        for i in range(3):
            sp = db.query(StrategyPerformance).filter(
                StrategyPerformance.uid == f"{test_tag}-{i}",
                StrategyPerformance.period_type == 4,
            ).first()
            assert sp is not None and sp.composite_score > 0


def test_wp08_refresh_redis_zset_uses_defaults_when_no_config():
    """WP-08：没有 WeightConfig 时使用默认权重（不抛错）"""
    import uuid
    from fwsort.database import init_db, SyncSessionLocal
    from fwsort.models import ExecutionAccount, StrategyPerformance, WeightConfig
    from fwsort.ranking_engine import refresh_redis_zset
    from datetime import datetime, timedelta

    init_db()
    tag = f"WP08B-{uuid.uuid4().hex[:6]}"
    with SyncSessionLocal() as db:
        from fwsort.models import User
        from fwsort.security import hash_password
        # 准备 owner（FK：ExecutionAccount.owner_id -> user.id）
        owner = User(
            email=f"{tag}@owner.com",
            nickname=tag,
            password_hash=hash_password("p"),
            role=0,
            status=0,
        )
        db.add(owner)
        db.flush()
        # 删掉 rank_type=2 的配置
        db.query(WeightConfig).filter(WeightConfig.rank_type == 2).delete()
        # 清理本 tag 残留
        db.query(StrategyPerformance).filter(StrategyPerformance.uid.like(f"{tag}%")).delete()
        db.commit()
        # 准备 ExecutionAccount + 一条 perf
        acc = ExecutionAccount(
            uid=f"{tag}-ACC",
            owner_id=owner.id,
            name="ACC",
            platform="okx",
            account_type=0,
            initial_balance=10000,
            current_balance=10000,
            order_amount_usd=5,
            public_enabled=True,
            status=0,
        )
        db.add(acc)
        db.flush()
        sp = StrategyPerformance(
            uid=tag,
            account_id=acc.id,
            period_type=2,
            start_time=datetime.now() - timedelta(days=7),
            end_time=datetime.now(),
            annualized_return=0.3,
            max_drawdown=0.05,
            sharpe_ratio=1.5,
            profit_loss_ratio=2.0,
            execution_score=0.9,
            composite_score=0,
            trade_count=200,
        )
        db.add(sp)
        db.commit()
        # 不传 rank_type → 走默认
        result = refresh_redis_zset(db, rank_type=2)
        db.commit()
        assert result["updated"] >= 1, f"updated 期望 ≥1, 实际 {result['updated']}"


# ========== WP-09 ES 异步化 ==========
def test_wp09_schedule_index_order_log_returns_none_when_es_unavailable(monkeypatch):
    """WP-09：ES 不可用时 schedule_index_order_log 返回 None（不抛错）"""
    from fwsort.execution import es_writer

    monkeypatch.setattr(es_writer, "es_available", False)
    monkeypatch.setattr(es_writer, "async_es", None)

    task = es_writer.schedule_index_order_log(
        order_log_id=1,
        uid="U1",
        account_id=1,
        vote_id=1,
        order_id="O1",
        order_type=2,
        side=1,
        platform="okx",
        symbol="BTCUSDT",
        expected_price=50000.0,
        actual_price=50000.0,
        quantity=0.001,
        amount_usd=50.0,
        status=3,
        latency_ms=100,
        slippage=0.0,
    )
    assert task is None


def test_wp09_build_order_log_event_creates_outbox_record():
    """WP-09：build_order_log_event 构造 OutboxEvent 对象（不入库）"""
    from fwsort.execution.outbox import build_order_log_event
    from fwsort.models import OutboxEvent
    from datetime import datetime

    # mock OrderExecutionLog
    log = MagicMock()
    log.id = 42
    log.uid = "U-DEMO"
    log.account_id = 1
    log.vote_id = 1
    log.order_id = "ORD-42"
    log.order_type = 2
    log.side = 1
    log.platform = "okx"
    log.symbol = "BTCUSDT"
    log.expected_price = 50000.0
    log.actual_price = 50000.0
    log.quantity = 0.001
    log.amount_usd = 50.0
    log.status = 3
    log.latency_ms = 100
    log.slippage = 0.0001
    log.created_at = datetime(2026, 7, 29, 12, 0, 0)

    evt = build_order_log_event(log)
    assert isinstance(evt, OutboxEvent)
    assert evt.event_type == "order_log_index"
    assert evt.status == 0
    assert evt.retry_count == 0
    assert '"uid": "U-DEMO"' in evt.payload_json
    assert '"id": 42' in evt.payload_json


def test_wp09_flush_outbox_sync_runs_cleanly():
    """WP-09：flush_outbox_sync 即使无事件也不抛错"""
    from fwsort.execution.outbox import flush_outbox_sync
    from fwsort.database import init_db

    init_db()
    # 同步执行
    result = flush_outbox_sync()
    assert "success" in result
    assert "failed" in result
    assert "total" in result
    assert result["total"] == 0  # 无事件


def test_wp09_outbox_event_table_exists():
    """WP-09：outbox_event 表必须存在（迁移已建）"""
    from fwsort.database import init_db, sync_engine
    from sqlalchemy import inspect

    init_db()
    insp = inspect(sync_engine)
    assert insp.has_table("outbox_event")


# ========== 综合：核心 API 调用链 ==========
def test_d2_health_endpoints():
    """D2：/api/info 与 /api/demo/info 都返回 200（健康检查通过）"""
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        r = client.get("/api/info")
        assert r.status_code == 200
        assert r.json()["status"] == "running"
        r = client.get("/api/demo/info")
        assert r.status_code == 200
        d = r.json()
        assert d["is_demo"] is True
        assert "demo_db" in d
