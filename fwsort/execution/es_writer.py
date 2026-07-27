# 订单日志双写器：写完 PostgreSQL 后异步落 ES（无 ES 时降级）
from datetime import datetime
from typing import Any

from loguru import logger

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
        logger.warning(f"[ES] order_log index failed (id={order_log_id}): {e}")
        return False


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
        logger.warning(f"[ES] search failed: {e}")
        return {"available": False, "total": 0, "hits": [], "error": str(e)}
