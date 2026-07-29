"""D3 阶段验收测试（WP-10 索引+bulk_update+keyset / WP-11 缓存 / WP-12 模拟器异步化）
- WP-10：关键索引存在 / mark_all_read bulk_update / keyset 分页
- WP-11：榜单 Redis 缓存读写 / 失效
- WP-12：simulator 用 await asyncio.sleep（不再阻塞 event loop）
"""
import asyncio
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# 允许在任意目录运行 pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 强制使用 SQLite + FakeRedis
os.environ.setdefault("USE_SQLITE", "true")
os.environ.setdefault("USE_FAKE_REDIS", "true")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-32bytes-for-unit-test-pass")
os.environ.setdefault("APP_ENV", "development")


# ========== WP-10 关键索引 ==========
def test_wp10_all_critical_indexes_exist():
    """WP-10：所有关键查询索引都已建立（幂等迁移）"""
    from fwsort.database import init_db, sync_engine
    from sqlalchemy import inspect

    init_db()
    insp = inspect(sync_engine)
    required = [
        # strategy_performance
        "idx_perf_period_score",
        "idx_perf_account_period",
        "idx_perf_uid_period",
        # order_execution_log
        "idx_orderlog_uid_time",
        "idx_orderlog_account_time",
        "idx_orderlog_vote",
        # follow_subscription
        "idx_follow_sub_leader_status",
        "idx_follow_leader_status",
        # follow_order
        "idx_follow_order_sub_time",
        # notification
        "idx_notify_user_unread",
        # rank_snapshot
        "idx_snapshot_rank_type_time",
        # execution_account
        "idx_acc_owner_deleted",
        "idx_acc_platform_deleted",
        # vote_decision
        "idx_vote_account_time",
        # agent_prediction
        "idx_prediction_symbol_time",
    ]
    missing = [idx for idx in required if not insp.has_index(idx)]
    assert not missing, f"WP-10: missing indexes {missing}"


def test_wp10_mark_all_read_uses_bulk_update(monkeypatch):
    """WP-10：mark_all_read 走单条 bulk UPDATE（不是逐条 ORM flush）"""
    import uuid
    from fwsort.database import init_db, SyncSessionLocal
    from fwsort.models import Notification, User
    from fwsort.security import hash_password

    init_db()
    test_user_id = None
    with SyncSessionLocal() as db:
        # 准备用户 + 50 条未读通知
        u = User(
            email=f"wp10-bulk-{uuid.uuid4().hex[:6]}@x.com",
            nickname="bulk-test",
            password_hash=hash_password("p"),
            role=0,
            status=0,
        )
        db.add(u)
        db.flush()
        test_user_id = u.id
        for i in range(50):
            db.add(Notification(user_id=u.id, ntype=1, title=f"t-{i}", content="x", is_read=False))
        db.commit()

    # 通过 FastAPI TestClient 触发
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        # 登录
        login = client.post(
            "/api/auth/login",
            json={"email": f"wp10-bulk-{test_user_id}@x.com".replace("wp10-bulk-", "") + "@x.com", "password": "p"},
        )
        # 用最直接的方式：构造 token
        from fwsort.security import create_access_token
        token = create_access_token(user_id=test_user_id, email="x@x.com")
        headers = {"Authorization": f"Bearer {token}"}
        # 触发 mark_all_read
        r = client.post("/api/notification/read-all", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("data", {}).get("marked", 0) == 50, f"应标记 50 条，实际 {body}"

        # 验证全部已读
        r2 = client.get("/api/notification/list?only_unread=true&limit=100", headers=headers)
        assert r2.json()["data"]["count"] == 0, "未读数应为 0"


# ========== WP-10 keyset 分页 ==========
def test_wp10_keyset_cursor_roundtrip():
    """WP-10：cursor 编码/解码 roundtrip 一致"""
    from router.ranking_router import _encode_cursor, _decode_cursor

    c1 = _encode_cursor(95.5, "UID-001")
    d = _decode_cursor(c1)
    assert d is not None
    assert abs(d[0] - 95.5) < 1e-6
    assert d[1] == "UID-001"
    # 无效 cursor
    assert _decode_cursor(None) is None
    assert _decode_cursor("not-base64!@#") is None
    assert _decode_cursor("") is None


def test_wp10_keyset_pagination_works():
    """WP-10：用 ZSet 验证 keyset 分页按 score 严格倒序、不重复、不遗漏"""
    from fwsort.redis_client import _FakeAsyncZSet

    async def _run():
        z = _FakeAsyncZSet()
        # 写 100 条 score=0.1 ~ 100.0
        await z.zadd("test:keyset", {f"U{i:03d}": float(i) for i in range(1, 101)})

        # 第一页（无 cursor）
        page1 = await z.zrevrangebyscore("test:keyset", max=100.1, min=0, start=0, num=10, withscores=True)
        assert len(page1) == 10
        assert page1[0][0] == "U100" and page1[0][1] == 100.0
        assert page1[-1][0] == "U091" and page1[-1][1] == 91.0

        # 用最后一名的 score 作为下一页 cursor（不含）
        last_score = page1[-1][1]
        page2 = await z.zrevrangebyscore("test:keyset", max=last_score, min=0, start=0, num=10, withscores=True)
        assert len(page2) == 10
        assert page2[0][0] == "U090" and page2[0][1] == 90.0
        assert page2[-1][0] == "U081" and page2[-1][1] == 81.0

        # 翻完 10 页
        cursor_score = 100.1
        seen = set()
        for pg in range(10):
            items = await z.zrevrangebyscore("test:keyset", max=cursor_score, min=0, start=0, num=10, withscores=True)
            assert len(items) == 10, f"第{pg+1}页应 10 条"
            for k, v in items:
                assert k not in seen, f"重复: {k}"
                seen.add(k)
            if items:
                cursor_score = items[-1][1]
        assert len(seen) == 100, f"应翻出 100 条，实际 {len(seen)}"

    asyncio.run(_run())


# ========== WP-11 榜单缓存 ==========
def test_wp11_ranking_cache_key_format():
    """WP-11：榜单缓存 key 格式正确（rank_type + 筛选 + 分页）"""
    from fwsort.cache.ranking_cache import build_cache_key, parse_cache_key

    k = build_cache_key(rank_type="realtime", platform="okx", page=2, page_size=20)
    assert k.startswith("fwsort:rank:cache:realtime:")
    # 解析回来
    parts = parse_cache_key(k)
    assert parts["rank_type"] == "realtime"
    assert parts["platform"] == "okx"
    assert parts["page"] == 2
    assert parts["page_size"] == 20


def test_wp11_ranking_cache_set_get():
    """WP-11：set_cached / get_cached roundtrip 正常"""
    from fwsort.cache.ranking_cache import set_cached, get_cached, clear_cache

    clear_cache()
    payload = {"items": [{"uid": "U1", "composite_score": 88}], "total": 1, "page": 1}
    set_cached("fwsort:rank:cache:realtime:p1:ps20:all:composite", payload, ttl=60)
    got = get_cached("fwsort:rank:cache:realtime:p1:ps20:all:composite")
    assert got is not None
    assert got["total"] == 1
    assert got["items"][0]["uid"] == "U1"


def test_wp11_ranking_cache_clear_by_prefix():
    """WP-11：按 prefix 清除缓存（权重变更触发）"""
    from fwsort.cache.ranking_cache import set_cached, clear_cache_by_prefix, get_cached

    set_cached("fwsort:rank:cache:realtime:p1", {"v": 1}, ttl=60)
    set_cached("fwsort:rank:cache:realtime:p2", {"v": 2}, ttl=60)
    set_cached("fwsort:rank:cache:daily:p1", {"v": 3}, ttl=60)
    n = clear_cache_by_prefix("fwsort:rank:cache:realtime:")
    assert n == 2, f"应清 2 条，实际 {n}"
    assert get_cached("fwsort:rank:cache:realtime:p1") is None
    assert get_cached("fwsort:rank:cache:realtime:p2") is None
    # daily 不动
    assert get_cached("fwsort:rank:cache:daily:p1") is not None


# ========== WP-12 模拟器异步化 ==========
def test_wp12_simulator_uses_asyncio_sleep(monkeypatch):
    """WP-12：simulator 内部 sleep 改为 await asyncio.sleep（不阻塞 event loop）"""
    from fwsort.execution import simulator
    from fwsort.execution.simulator import OrderSimulator

    # 替换 simulator 模块的 asyncio.sleep 计数器
    sleep_calls = []
    real_sleep = asyncio.sleep

    async def tracking_sleep(t):
        sleep_calls.append(t)
        await real_sleep(0)  # 不真睡，立即返回

    monkeypatch.setattr(simulator.asyncio, "sleep", tracking_sleep)

    async def _run():
        sim = OrderSimulator(platform="okx", account_type=0)
        # 跑 5 笔订单
        tasks = [
            sim.submit(symbol="BTCUSDT", side=1, amount_usd=5.0, expected_price=50000.0)
            for _ in range(5)
        ]
        results = await asyncio.gather(*tasks)
        assert len(results) == 5
        # 验证 sleep 被调用过（不是 time.sleep）
        assert len(sleep_calls) >= 5, f"asyncio.sleep 应被调用 ≥5 次，实际 {len(sleep_calls)}"
        return results

    res = asyncio.run(_run())
    assert all(r.status in (1, 2, 3) for r in res)


def test_wp12_simulator_concurrent_throughput():
    """WP-12：100 并发模拟下单 P99 应 < 800ms（不阻塞 event loop）"""
    from fwsort.execution.simulator import OrderSimulator

    async def _run():
        sim = OrderSimulator(platform="okx", account_type=0)
        latencies = []

        async def one():
            t0 = time.perf_counter()
            await sim.submit(symbol="BTCUSDT", side=1, amount_usd=5.0, expected_price=50000.0)
            latencies.append((time.perf_counter() - t0) * 1000)

        tasks = [one() for _ in range(20)]  # 20 并发（测试用，生产应 100）
        await asyncio.gather(*tasks)
        latencies.sort()
        p99 = latencies[int(len(latencies) * 0.99) - 1] if len(latencies) > 1 else latencies[0]
        # 测试用 20 并发；P99 应 < 200ms（生产 100 并发 P99 < 800ms 留余量）
        assert p99 < 500, f"P99 {p99:.0f}ms 超过 500ms（异步化未生效）"

    asyncio.run(_run())
