# Redis 客户端：榜单 ZSet / 缓存 / 限流
# 轻量模式：USE_FAKE_REDIS=True 时使用进程内纯 Python 内存版（无外部依赖）
# WP-06：演示模式独立 Redis 实例（独立内存版 + 独立 key 命名空间）
import datetime as _dt
from datetime import timezone
from typing import Any

from fastapi import Request
from loguru._datetime import datetime

from fwsort.config import settings


# ========== Fake Redis（纯 Python 内存版）==========
class _FakeAsyncZSet:
    """最小 ZSet 实现：覆盖 zadd/zcard/zrevrange/zremrangebyscore/delete/expire/scan_iter"""

    def __init__(self) -> None:
        self._data: dict[str, float] = {}
        self._ttls: dict[str, int] = {}  # 过期时间（unix 时间戳）

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self._evict_if_expired(key)
        self._data.update(mapping)
        return len(mapping)

    async def zcard(self, key: str) -> int:
        self._evict_if_expired(key)
        return len(self._data)

    async def zrevrange(self, key: str, start: int, end: int, withscores: bool = False) -> list:
        self._evict_if_expired(key)
        sorted_items = sorted(self._data.items(), key=lambda x: x[1], reverse=True)
        if end == -1:
            sl = sorted_items[start:]
        else:
            sl = sorted_items[start : end + 1]
        if withscores:
            return [(k, v) for k, v in sl]
        return [k for k, _ in sl]

    async def zrevrangebyscore(
        self, key: str, max: float, min: float = "-inf",
        start: int = 0, num: int = 100, withscores: bool = False,
    ) -> list:
        """WP-10：keyset 分页——按 score 区间倒序拉取
        - max: 上界（不包含，调用方传上一页最后一条的 score）
        - min: 下界（包含）
        - start/num: 区间内分页
        """
        self._evict_if_expired(key)
        items = [(k, v) for k, v in self._data.items() if v < max and v >= min]
        items.sort(key=lambda x: x[1], reverse=True)
        page = items[start : start + num]
        if withscores:
            return [(k, v) for k, v in page]
        return [k for k, _ in page]

    async def zremrangebyscore(self, key: str, min_: float, max_: float) -> int:
        """删除 score 在 [min_, max_] 区间的成员"""
        self._evict_if_expired(key)
        to_del = [k for k, v in self._data.items() if min_ <= v <= max_]
        for k in to_del:
            del self._data[k]
        return len(to_del)

    async def expire(self, key: str, seconds: int) -> bool:
        self._ttls[key] = int(datetime.now(tz=timezone.utc).timestamp()) + seconds
        return True

    async def delete(self, *keys: str) -> int:
        # Fake 模式只有 1 个 key，不区分
        n = len(self._data)
        self._data.clear()
        for k in keys:
            self._ttls.pop(k, None)
        return n

    async def scan_iter(self, match: str | None = None, count: int = 100):
        for k in list(self._data.keys()):
            if match is None or match.replace("*", "") in k:
                yield k

    def _evict_if_expired(self, key: str) -> None:
        exp = self._ttls.get(key)
        if exp and _dt.datetime.now(tz=timezone.utc).timestamp() > exp:
            self._data.clear()
            self._ttls.pop(key, None)


class _FakeAsyncRedis:
    """最小化 async Redis 客户端：实现 zadd/zcard/zrevrange/zremrangebyscore/expire/delete/get/setex/scan_iter + hash ops"""

    def __init__(self) -> None:
        self._zsets: dict[str, _FakeAsyncZSet] = {}
        self._kv: dict[str, str] = {}
        self._kv_ttls: dict[str, int] = {}
        self._hashes: dict[str, dict[str, str]] = {}

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

    async def zrevrangebyscore(
        self, key: str, max: float, min: float = "-inf",
        start: int = 0, num: int = 100, withscores: bool = False,
    ) -> list:
        return await self.zset(key).zrevrangebyscore(key, max, min, start, num, withscores)

    async def zremrangebyscore(self, key: str, min_: float, max_: float) -> int:
        return await self.zset(key).zremrangebyscore(key, min_, max_)

    async def expire(self, key: str, seconds: int) -> bool:
        if key in self._zsets:
            return await self._zsets[key].expire(key, seconds)
        if key in self._kv:
            self._kv_ttls[key] = int(_dt.datetime.now(tz=timezone.utc).timestamp()) + seconds
            return True
        return False

    async def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if k in self._zsets:
                await self._zsets[k].delete()
                self._zsets.pop(k, None)
                n += 1
            if k in self._kv:
                self._kv.pop(k, None)
                self._kv_ttls.pop(k, None)
                n += 1
        return n

    def _evict_kv_if_expired(self, key: str) -> None:
        exp = self._kv_ttls.get(key)
        if exp and _dt.datetime.now(tz=timezone.utc).timestamp() > exp:
            self._kv.pop(key, None)
            self._kv_ttls.pop(key, None)

    async def get(self, key: str) -> str | None:
        self._evict_kv_if_expired(key)
        return self._kv.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._kv[key] = value
        self._kv_ttls[key] = int(_dt.datetime.now(tz=timezone.utc).timestamp()) + ttl

    async def scan_iter(self, match: str | None = None, count: int = 100):
        for k in list(self._kv.keys()):
            if match is None or match.replace("*", "") in k:
                yield k

    async def hget(self, key: str, field: str) -> str | None:
        """获取 hash 中的字段值"""
        hash_data = self._hashes.get(key, {})
        return hash_data.get(field)

    async def hset(self, key: str, field: str, value: str) -> int:
        """设置 hash 中的字段值"""
        if key not in self._hashes:
            self._hashes[key] = {}
        self._hashes[key][field] = value
        return 1

    async def hdel(self, key: str, *fields: str) -> int:
        """删除 hash 中的字段"""
        hash_data = self._hashes.get(key, {})
        count = 0
        for field in fields:
            if field in hash_data:
                del hash_data[field]
                count += 1
        return count

    async def hgetall(self, key: str) -> dict[str, str]:
        """获取 hash 中的所有字段"""
        return self._hashes.get(key, {})


class _FakeSyncRedis:
    """同步版 fake redis（给 Celery 任务使用）"""

    def __init__(self) -> None:
        self._zsets: dict[str, dict[str, float]] = {}
        self._kv: dict[str, str] = {}
        self._hashes: dict[str, dict[str, str]] = {}

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
            if k in self._hashes:
                self._hashes.pop(k, None)
                n += 1
        return n

    def get(self, key: str) -> str | None:
        return self._kv.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self._kv[key] = value

    def hget(self, key: str, field: str) -> str | None:
        """获取 hash 中的字段值"""
        hash_data = self._hashes.get(key, {})
        return hash_data.get(field)

    def hset(self, key: str, field: str, value: str) -> int:
        """设置 hash 中的字段值"""
        if key not in self._hashes:
            self._hashes[key] = {}
        self._hashes[key][field] = value
        return 1

    def hdel(self, key: str, *fields: str) -> int:
        """删除 hash 中的字段"""
        hash_data = self._hashes.get(key, {})
        count = 0
        for field in fields:
            if field in hash_data:
                del hash_data[field]
                count += 1
        return count

    def hgetall(self, key: str) -> dict[str, str]:
        """获取 hash 中的所有字段"""
        return self._hashes.get(key, {})

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


# ========== WP-06：演示模式独立 Redis（独立内存实例 + 隔离 key 命名空间）==========
# 演示模式用 _FakeAsyncRedis（无需外部 Redis 即可完全隔离）
demo_async_redis = _FakeAsyncRedis()
demo_sync_redis = _FakeSyncRedis()


def _is_demo_request(request: Request | None) -> bool:
    """判断当前请求是否走演示数据通道（与 database.py 保持一致）"""
    if request is None:
        return False
    return request.url.path.startswith("/api/demo/")


def get_async_redis_for(request: Request | None = None) -> Any:
    """WP-06：根据请求路径返回 prod 或 demo redis 客户端"""
    if _is_demo_request(request):
        return demo_async_redis
    return async_redis


def get_sync_redis_for(request: Request | None = None) -> Any:
    """WP-06：根据请求路径返回 prod 或 demo 同步 redis 客户端"""
    if _is_demo_request(request):
        return demo_sync_redis
    return sync_redis


def demo_redis_key(raw_key: str) -> str:
    """WP-06：demo redis key 加命名空间前缀（与 prod 完全隔离）"""
    if not raw_key.startswith(settings.APP_DEMO_REDIS_PREFIX):
        return f"{settings.APP_DEMO_REDIS_PREFIX}{raw_key}"
    return raw_key


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
