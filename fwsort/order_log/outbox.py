# 订单日志 Outbox 工具：把 ES 写入转为 outbox 模式
# 同一事务内：写 OrderExecutionLog + 写 OutboxEvent(status=0)
# 后台 Celery 任务 flush_outbox 每 30s 扫描并消费
import json
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from fwsort.es_client import async_es, es_available
from fwsort.redis_client import sync_redis


# ========== 入队：构造 OutboxEvent 对象（不 commit）==========
def build_order_log_event(order_log: Any) -> Any:
    """WP-09：构造 OutboxEvent（不入库），由调用方 db.add() + commit()
    - 返回 OutboxEvent 实例，调用方应负责 add + flush + commit
    - 用于在同步/异步 session 中复用同一事务
    """
    from fwsort.models import OutboxEvent

    doc = {
        "id": order_log.id,
        "uid": order_log.uid,
        "account_id": order_log.account_id,
        "vote_id": order_log.vote_id,
        "order_id": order_log.order_id,
        "order_type": order_log.order_type,
        "side": order_log.side,
        "platform": order_log.platform,
        "symbol": order_log.symbol,
        "expected_price": float(order_log.expected_price or 0),
        "actual_price": float(order_log.actual_price or 0),
        "quantity": float(order_log.quantity or 0),
        "amount_usd": float(order_log.amount_usd or 0),
        "status": order_log.status,
        "latency_ms": order_log.latency_ms or 0,
        "slippage": float(order_log.slippage or 0),
        "created_at": (order_log.created_at or datetime.utcnow()).isoformat(),
    }
    return OutboxEvent(
        event_type="order_log_index",
        payload_json=json.dumps(doc, ensure_ascii=False),
        status=0,
        retry_count=0,
        next_retry_at=datetime.utcnow(),
    )


def enqueue_order_log_event(db, order_log: Any) -> int | None:
    """WP-09：把订单日志的 ES 文档序列化后写入 outbox_event 表
    - 调用方须在同事务内 commit（落库后异步消费）
    - 返回 OutboxEvent.id；失败返回 None
    - 同步 session 版本：传入 sync Session
    """
    try:
        evt = build_order_log_event(order_log)
        db.add(evt)
        db.flush()
        return evt.id
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[outbox] enqueue failed: {e}")
        return None


# ========== 出队：flush_outbox Celery 任务调用 ==========
OUTBOX_BATCH_SIZE = 50
OUTBOX_MAX_RETRY = 3
OUTBOX_RETRY_BACKOFF_MIN = 1  # 分钟


def fetch_pending_events(db) -> list[Any]:
    """拉取一批待消费事件：status=0 且 next_retry_at <= now
    - 按 created_at 升序（FIFO 避免饥饿）
    - 单批上限 OUTBOX_BATCH_SIZE
    """
    from fwsort.models import OutboxEvent

    now = datetime.utcnow()
    return (
        db.query(OutboxEvent)
        .filter(OutboxEvent.status.in_([0, 2]))
        .filter((OutboxEvent.next_retry_at == None) | (OutboxEvent.next_retry_at <= now))  # noqa: E711
        .order_by(OutboxEvent.created_at.asc())
        .limit(OUTBOX_BATCH_SIZE)
        .all()
    )


async def dispatch_event(event: Any) -> bool:
    """单条事件投递到 ES（异步）
    - 成功返回 True，失败返回 False
    - 文档已存在时 ES 返回 success=True（幂等）
    """
    from fwsort.config import settings

    # WP-09：若事件已标记 success（说明 fire-and-forget 已写入），跳过避免重复 IO
    # 这一步必须在 es_available 之前，避免 ES 不可用时仍然返回 True（应当返回成功但跳过）
    if event.status == 1:
        return True
    if not es_available or async_es is None:
        return False
    try:
        doc = json.loads(event.payload_json)
        await async_es.index(
            index=settings.ES_INDEX_ORDER_LOG,
            id=str(doc.get("id", event.id)),
            document=doc,
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[outbox] dispatch event {event.id} failed: {e}")
        return False


def mark_event_success(db, event: Any) -> None:
    """事件投递成功 → status=1"""
    event.status = 1
    event.last_error = ""
    event.next_retry_at = None


def mark_event_failure(db, event: Any, err: str) -> bool:
    """事件投递失败 → 退避重试
    - retry_count + 1
    - 超过 OUTBOX_MAX_RETRY → 标 status=2（持续重试但延长间隔）
    - 返回 True 表示仍可重试
    """
    event.retry_count += 1
    event.last_error = err[:500]
    if event.retry_count >= OUTBOX_MAX_RETRY:
        # 超过最大重试 → 长退避（10 分钟），保留 status=2 让运维感知
        event.next_retry_at = datetime.utcnow() + timedelta(minutes=10)
        event.status = 2
    else:
        # 指数退避：1, 2, 4 分钟
        event.next_retry_at = datetime.utcnow() + timedelta(
            minutes=OUTBOX_RETRY_BACKOFF_MIN * (2 ** (event.retry_count - 1))
        )
        event.status = 2
    return event.retry_count < OUTBOX_MAX_RETRY


def flush_outbox_sync() -> dict:
    """WP-09：Celery 同步入口：拉一批 outbox 事件并投递到 ES
    - 由于 index_order_log 是 async，这里在事件循环中执行
    - 使用 asyncio.run 启动新循环
    - 返回处理摘要 {success, failed, skipped, total}
    """
    import asyncio

    from fwsort.database import get_sync_db

    success = 0
    failed = 0
    skipped = 0
    try:
        with get_sync_db() as db:
            events = fetch_pending_events(db)
            if not events:
                return {"success": 0, "failed": 0, "skipped": 0, "total": 0}

            async def _run() -> tuple[int, int]:
                s = 0
                f = 0
                for ev in events:
                    ok = await dispatch_event(ev)
                    if ok:
                        mark_event_success(db, ev)
                        s += 1
                    else:
                        mark_event_failure(db, ev, "dispatch_event returned False")
                        f += 1
                return s, f

            success, failed = asyncio.run(_run())
            skipped = len(events) - success - failed
            db.commit()
    except Exception as e:  # noqa: BLE001
        logger.error(f"[outbox] flush_outbox_sync error: {e}")
        return {"success": 0, "failed": 0, "skipped": 0, "total": 0, "error": str(e)}
    summary = {"success": success, "failed": failed, "skipped": skipped, "total": len(events)}
    logger.info(f"[outbox] flush: {summary}")
    # 记录任务状态
    try:
        import json as _json

        sync_redis.hset(
            "fwsort:task:status",
            "flush_outbox",
            _json.dumps(
                {
                    "status": "ok",
                    "last_run_at": datetime.utcnow().isoformat(),
                    "last_result": _json.dumps(summary, ensure_ascii=False),
                },
                ensure_ascii=False,
            ),
        )
    except Exception:  # noqa: BLE001
        pass
    return summary
