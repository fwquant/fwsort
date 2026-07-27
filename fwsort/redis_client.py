# Redis 客户端：榜单 ZSet / 缓存 / 限流
# 轻量模式：USE_FAKE_REDIS=True 时使用进程内纯 Python 内存版（无外部依赖）
from typing import Any

from fwsort.config import settings


# ========== Fake Redis（纯 Python 内存版）==========
class _FakeAsyncZSet:
    """最小 ZSet 实现：仅覆盖 zadd/zcard/zrevrange/delete/scan_iter"""

    def __init__(self) -> None:
        self._data: dict[str, float] = {}

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self._data.update(mapping)
        return len(mapping)

    async def zcard(self, key: str) -> int:
        return len(self._data)

    async def zrevrange(self, key: str, start: int, end: int, withscores: bool = False) -> list:
        sorted_items = sorted(self._data.items(), key=lambda x: x[1], reverse=True)
        if end == -1:
            sl = sorted_items[start:]
        else:
            sl = sorted_items[start : end + 1]
        if withscores:
            return [(k, v) for k, v in sl]
        return [k for k, _ in sl]

    async def delete(self, *keys: str) -> int:
        # Fake 模式只有 1 个 key，不区分
        n = len(self._data)
        self._data.clear()
        return n

    async def scan_iter(self, match: str | None = None, count: int = 100):
        for k in list(self._data.keys()):
            if match is None or match.replace("*", "") in k:
                yield k


class _FakeAsyncRedis:
    """最小化 async Redis 客户端：仅实现 zadd/zcard/zrevrange/delete/get/setex/scan_iter"""

    def __init__(self) -> None:
        self._zsets: dict[str, _FakeAsyncZSet] = {}
        self._kv: dict[str, str] = {}

    def zset(self, key: str) -> _FakeAsyncZSet:
        if key not in self._zsets:
            self._zsets[key] = _FakeAsyncZSet()
        return self._zsets[key]

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        return await self.zset(key).zadd(key, mapping)

    async def zcard(self, key: str) -> int:
        return await self.zset(key).zcard(key)

    async def zrevrange(self, key: str, start: int, end: int, withscores: bool = False) -> list:
        return await self.zset(key).zrevrange(key, start, end, withscores)

    async def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if k in self._zsets:
                n += await self._zsets[k].delete()
                self._zsets.pop(k, None)
            if k in self._kv:
                self._kv.pop(k, None)
                n += 1
        return n

    async def get(self, key: str) -> str | None:
        return self._kv.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._kv[key] = value

    async def scan_iter(self, match: str | None = None, count: int = 100):
        for k in list(self._kv.keys()):
            if match is None or match.replace("*", "") in k:
                yield k


class _FakeSyncRedis:
    """同步版 fake redis（给 Celery 任务使用）"""

    def __init__(self) -> None:
        self._zsets: dict[str, dict[str, float]] = {}
        self._kv: dict[str, str] = {}

    def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self._zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    def zcard(self, key: str) -> int:
        return len(self._zsets.get(key, {}))

    def zrevrange(self, key: str, start: int, end: int, withscores: bool = False) -> list:
        sorted_items = sorted(self._zsets.get(key, {}).items(), key=lambda x: x[1], reverse=True)
        if end == -1:
            sl = sorted_items[start:]
        else:
            sl = sorted_items[start : end + 1]
        if withscores:
            return [(k, v) for k, v in sl]
        return [k for k, _ in sl]

    def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if k in self._zsets:
                n += len(self._zsets.pop(k, {}))
            if k in self._kv:
                self._kv.pop(k, None)
                n += 1
        return n

    def get(self, key: str) -> str | None:
        return self._kv.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self._kv[key] = value

    def scan_iter(self, match: str | None = None, count: int = 100):
        for k in list(self._kv.keys()):
            if match is None or match.replace("*", "") in k:
                yield k


# ========== 真实 Redis（连接失败时降级到 fake）==========
def _build_sync() -> Any:
    if settings.USE_FAKE_REDIS:
        return _FakeSyncRedis()
    import redis

    return redis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD or None,
        decode_responses=True,
        socket_timeout=5,
    )


def _build_async() -> Any:
    if settings.USE_FAKE_REDIS:
        return _FakeAsyncRedis()
    from redis.asyncio import Redis as AsyncRedis

    return AsyncRedis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD or None,
        decode_responses=True,
        socket_timeout=5,
    )


# 同步客户端（Celery 任务、初始化脚本）
sync_redis = _build_sync()

# 异步客户端（FastAPI 路由）
async_redis = _build_async()


# ========== 榜单 ZSet 命名空间 ==========
# 按榜单类型分桶：实时榜、日榜、周榜、月榜、总榜
RANK_KEY_PREFIX = "fwsort:rank"


def rank_key(rank_type: str) -> str:
    """榜单 ZSet key 工厂"""
    return f"{RANK_KEY_PREFIX}:{rank_type}"


# ========== 榜单类型常量（与架构文档一致）==========
class RankType:
    REALTIME = "realtime"  # 实时榜
    DAILY = "daily"        # 日榜
    WEEKLY = "weekly"      # 周榜
    MONTHLY = "monthly"    # 月榜
    ALL_TIME = "all_time"  # 总榜


# ========== 通用缓存工具 ==========
def get_cached(key: str) -> str | None:
    """读取缓存字符串"""
    try:
        return sync_redis.get(key)
    except Exception:
        return None


def set_cached(key: str, value: str, ttl: int = 300) -> None:
    """写入缓存（默认 5 分钟）"""
    try:
        sync_redis.setex(key, ttl, value)
    except Exception:
        pass


def delete_cached(*keys: str) -> None:
    """批量删除缓存"""
    if not keys:
        return
    try:
        sync_redis.delete(*keys)
    except Exception:
        pass
