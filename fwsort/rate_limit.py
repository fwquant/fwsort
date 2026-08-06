# 限流：基于 Redis 滑动窗口的失败计数 + 临时锁定（WP-03）
from datetime import datetime, timedelta, timezone

from fastapi import Request
from fwsort.fwlogs import logger

from fwsort.config import settings
from fwsort.exceptions import AuthError
from fwsort.redis_client import async_redis


# ========== 内部：滑动窗口失败计数 ==========
async def _recent_failures(scope: str, key: str, window_min: int = 30) -> int:
    """统计最近 window_min 分钟内失败次数（基于 ZSet score=时间戳）"""
    rk = f"fwsort:rl:{scope}:{key}"
    now = datetime.now(tz=timezone.utc).timestamp()
    cutoff = now - window_min * 60
    try:
        # 删除窗口外
        await async_redis.zremrangebyscore(rk, 0, cutoff)
        # 统计窗口内
        return await async_redis.zcard(rk)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"rate limit read failed (skip): {e}")
        return 0


async def _record_failure(scope: str, key: str, lock_minutes: int) -> int:
    """记录一次失败，并返回当前窗口内累计次数"""
    rk = f"fwsort:rl:{scope}:{key}"
    now = datetime.now(tz=timezone.utc).timestamp()
    try:
        await async_redis.zadd(rk, {f"{now}": now})
        # 锁定时长（SETEX：用作"被锁"标记）
        await async_redis.setex(f"fwsort:rl:lock:{scope}:{key}", lock_minutes * 60, "1")
        # 自身 key 也设过期，避免泄漏
        await async_redis.expire(rk, 3600)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"rate limit record failed (skip): {e}")
    return await _recent_failures(scope, key)


async def _is_locked(scope: str, key: str) -> bool:
    """检查是否处于锁定状态"""
    rk = f"fwsort:rl:lock:{scope}:{key}"
    try:
        v = await async_redis.get(rk)
        return v == "1"
    except Exception:  # noqa: BLE001
        return False


async def _clear_failures(scope: str, key: str) -> None:
    """成功登录后清除计数"""
    rk = f"fwsort:rl:{scope}:{key}"
    try:
        await async_redis.delete(rk, f"fwsort:rl:lock:{scope}:{key}")
    except Exception:  # noqa: BLE001
        pass


# ========== 对外：FastAPI 依赖 ==========
def _client_ip(request: Request) -> str:
    """获取客户端 IP（优先取 X-Forwarded-For）"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def check_login_rate_limit(email: str, request: Request) -> None:
    """登录限流：失败 N 次锁 M 分钟（IP + 邮箱双维度）
    - APP_LOGIN_LOCK_MINUTES=0 时直接跳过锁定检查（仅保留失败计数以便排错）
    - 开发模式下 localhost 自动绕过锁定（无需手动加 bypass_lock 参数）
    - 生产/非本地请求仍支持 ?bypass_lock=1 或 X-FWSORT-BYPASS-LOCK: 1
    """
    # APP_LOGIN_LOCK_MINUTES=0 → 完全关闭锁定（仅用于开发/演示环境）
    if settings.APP_LOGIN_LOCK_MINUTES <= 0:
        return

    ip = _client_ip(request)
    is_local = ip in ("127.0.0.1", "::1", "localhost")
    # 开发模式 + 本地 IP → 自动清除锁定并放行（避免开发调试被限流卡住）
    if settings.APP_ENV == "development" and is_local:
        try:
            await _clear_failures("login:ip", ip)
            await _clear_failures("login:email", email)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"failed to auto-clear localhost lock (skip): {e}")
        return
    # 非本地 / 生产环境：支持手动绕过
    try:
        bypass_q = request.query_params.get("bypass_lock") == "1"
        bypass_h = request.headers.get("x-fwsort-bypass-lock") == "1"
    except Exception:
        bypass_q = False
        bypass_h = False
    if (bypass_q or bypass_h) and is_local:
        try:
            await _clear_failures("login:ip", ip)
            await _clear_failures("login:email", email)
            logger.warning(f"login lock bypass used for ip={ip} email={email}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"failed to clear locks during bypass (skip): {e}")
        return
    # 任一维度被锁，则拒绝
    if await _is_locked("login:ip", ip):
        raise AuthError(f"登录失败次数过多，IP {ip} 已被临时锁定 {settings.APP_LOGIN_LOCK_MINUTES} 分钟")
    if await _is_locked("login:email", email):
        raise AuthError(f"登录失败次数过多，邮箱 {email} 已被临时锁定 {settings.APP_LOGIN_LOCK_MINUTES} 分钟")


async def record_login_failure(email: str, request: Request) -> int:
    """记录登录失败（返回当前累计次数）"""
    ip = _client_ip(request)
    n_ip = await _record_failure("login:ip", ip, settings.APP_LOGIN_LOCK_MINUTES)
    n_email = await _record_failure("login:email", email, settings.APP_LOGIN_LOCK_MINUTES)
    return max(n_ip, n_email)


async def clear_login_failures(email: str, request: Request) -> None:
    """登录成功：清除计数"""
    ip = _client_ip(request)
    await _clear_failures("login:ip", ip)
    await _clear_failures("login:email", email)


async def check_register_rate_limit(request: Request) -> None:
    """注册限流：同 IP 3 次/小时"""
    ip = _client_ip(request)
    n = await _recent_failures("register:ip", ip, window_min=60)
    if n >= settings.APP_REGISTER_RATE_LIMIT:
        raise AuthError(f"注册过于频繁，IP {ip} 1 小时内最多 {settings.APP_REGISTER_RATE_LIMIT} 次")


async def record_register_attempt(request: Request) -> None:
    """记录一次注册尝试（不论成败）"""
    ip = _client_ip(request)
    await _record_failure("register:ip", ip, lock_minutes=60)
