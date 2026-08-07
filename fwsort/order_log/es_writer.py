# 订单日志双写器：写完 PostgreSQL 后异步落 ES（无 ES 时降级）
import asyncio
from datetime import datetime
from typing import Any

from fwsort.fwlogs import logger

from fwsort.es_client import async_es, es_available


async def index_order_log(
    *,
    order_log_id: int,
    uid: str,
    account_id: int,
    vote_id: int,
    order_id: str,
    order_type: int,
    side: int,
    platform: str,
    symbol: str,
    expected_price: float,
    actual_price: float,
    quantity: float,
    amount_usd: float,
    status: int,
    latency_ms: int,
    slippage: float,
    created_at: datetime | None = None,
) -> bool:
    """落库后调用本方法把订单日志同步写 ES

    失败不抛异常，仅日志告警（保证主流程不挂）
    """
    if not es_available or async_es is None:
        # ES 不可用 → 静默降级（不影响主流程）
        return False
    try:
        from fwsort.config import settings

        doc = {
            "id": order_log_id,
            "uid": uid,
            "account_id": account_id,
            "vote_id": vote_id,
            "order_id": order_id,
            "order_type": order_type,
            "side": side,
            "platform": platform,
            "symbol": symbol,
            "expected_price": expected_price,
            "actual_price": actual_price,
            "quantity": quantity,
            "amount_usd": amount_usd,
            "status": status,
            "latency_ms": latency_ms,
            "slippage": slippage,
            "created_at": (created_at or datetime.utcnow()).isoformat(),
        }
        await async_es.index(
            index=settings.ES_INDEX_ORDER_LOG,
            id=str(order_log_id),
            document=doc,
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ES] order_log index failed (id={order_log_id}):{e}，traceback: {traceback.format_exc()}")
        return False


# ========== WP-09：Fire-and-Forget 异步包装（含重试）==========
async def _index_with_retry(**kwargs: Any) -> bool:
    """WP-09：内部包装，含 3 次重试（指数退避）
    - 失败仍返回 False，由 schedule_index_order_log 决定如何处理
    """
    last_err: str = ""
    for attempt in range(3):
        try:
            ok = await index_order_log(**kwargs)
            if ok:
                return True
            last_err = "index_order_log returned False (ES unavailable?)"
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}:{e}，traceback: {traceback.format_exc()}"
        if attempt < 2:
            # 指数退避：0.2s, 0.4s
            await asyncio.sleep(0.2 * (2 ** attempt))
    logger.warning(f"[ES] index_order_log failed after 3 attempts: {last_err}")
    return False


def schedule_index_order_log(**kwargs: Any) -> asyncio.Task | None:
    """WP-09：把 ES 索引写入调度为后台异步任务（不阻塞主流程）
    - 返回 asyncio.Task 便于调用方跟踪 / 测试；调用方通常忽略
    - 任务内部已 try/except + 3 次重试，单次失败仅记日志
    - ES 不可用时立即返回 None
    """
    if not es_available or async_es is None:
        return None
    try:
        task = asyncio.create_task(_index_with_retry(**kwargs))
        # 回调：若任务异常则捕获（避免 'Task exception was never retrieved' 警告）
        task.add_done_callback(_log_es_task_result)
        return task
    except RuntimeError:
        # 没有 event loop（同步上下文）→ 静默跳过
        return None


def _log_es_task_result(task: asyncio.Task) -> None:
    """WP-09：异步任务完成回调，捕获异常仅记日志"""
    try:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.warning(f"[ES] async index task failed: {type(exc).__name__}: {exc}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ES] task result inspection failed: {e},traceback={traceback.format_exc()}")


async def search_order_logs(
    *,
    uid: str | None = None,
    platform: str | None = None,
    status: int | None = None,
    size: int = 50,
) -> dict[str, Any]:
    """ES 检索订单日志（用于排行页/详情页高性能筛选）

    任意参数为 None 表示不参与过滤
    """
    if not es_available or async_es is None:
        return {"available": False, "total": 0, "hits": []}

    from fwsort.config import settings

    must: list[dict] = []
    if uid:
        must.append({"term": {"uid": uid}})
    if platform:
        must.append({"term": {"platform": platform}})
    if status is not None:
        must.append({"term": {"status": status}})

    query: dict = {"match_all": {}} if not must else {"bool": {"must": must}}
    try:
        resp = await async_es.search(
            index=settings.ES_INDEX_ORDER_LOG,
            query=query,
            size=size,
            sort=[{"created_at": {"order": "desc"}}],
        )
        hits = [h["_source"] for h in resp.get("hits", {}).get("hits", [])]
        total = resp.get("hits", {}).get("total", {}).get("value", 0)
        return {"available": True, "total": total, "hits": hits}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ES] search failed: {e},traceback={traceback.format_exc()}")
        return {"available": False, "total": 0, "hits": [], "error": str(e)}
