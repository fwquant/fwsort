# WP-11：榜单 Redis 缓存（统一 key 命名空间 + 失效策略）
import json
from typing import Any

from fwsort.redis_client import get_async_redis_for, get_sync_redis_for, sync_redis


# ========== 缓存 Key 规范 ==========
# fwsort:rank:cache:{rank_type}:{page}:{ps}:{platform}:{sort_by}
_RANK_CACHE_PREFIX = "fwsort:rank:cache:"


def build_cache_key(
    rank_type: str,
    platform: str | None = None,
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "composite",
) -> str:
    """构造榜单缓存 key（参数缺失用 'all' 占位）"""
    return f"{_RANK_CACHE_PREFIX}{rank_type}:p{page}:ps{page_size}:{platform or 'all'}:{sort_by}"


def parse_cache_key(key: str) -> dict[str, Any]:
    """解析 key → 字段（用于失效审计）"""
    body = key.replace(_RANK_CACHE_PREFIX, "")
    parts = body.split(":")
    out: dict[str, Any] = {}
    if len(parts) >= 5:
        out["rank_type"] = parts[0]
        out["page"] = int(parts[1].lstrip("p")) if parts[1].startswith("p") else None
        out["page_size"] = int(parts[2].lstrip("ps")) if parts[2].startswith("ps") else None
        out["platform"] = None if parts[3] == "all" else parts[3]
        out["sort_by"] = parts[4]
    return out


# ========== 同步 Redis（Celery 任务 / 初始化脚本）==========
def set_cached(key: str, payload: Any, ttl: int = 60) -> None:
    """同步写缓存（JSON 序列化 + TTL）"""
    try:
        sync_redis.setex(key, ttl, json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001
        pass


def get_cached(key: str) -> Any | None:
    """同步读缓存（返回反序列化对象）"""
    try:
        raw = sync_redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


def clear_cache() -> int:
    """清空所有榜单缓存（慎用）"""
    return clear_cache_by_prefix(_RANK_CACHE_PREFIX)


def clear_cache_by_prefix(prefix: str) -> int:
    """按前缀清缓存（权重变更 / 新订单触发）"""
    n = 0
    try:
        for k in sync_redis.scan_iter(match=f"{prefix}*", count=100):
            sync_redis.delete(k)
            n += 1
    except Exception:  # noqa: BLE001
        pass
    return n


def clear_rank_cache(rank_type: str | None = None) -> int:
    """清榜单缓存：传 rank_type 只清该榜；不传全清
    - WP-08：权重重算后调用
    - WP-12：新订单完成后调用（确保榜单反映新成交）
    """
    if rank_type:
        return clear_cache_by_prefix(f"{_RANK_CACHE_PREFIX}{rank_type}:")
    return clear_cache()
