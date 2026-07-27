# JWT 与密码哈希：用户认证（邮箱+密码+JWT）
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from fwsort.config import settings


def hash_password(plain: str) -> str:
    """密码哈希（bcrypt）"""
    # bcrypt 仅支持 ≤72 字节密码，超出截断
    pwd = plain.encode("utf-8")[:72]
    return bcrypt.hashpw(pwd, bcrypt.gensalt(rounds=10)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """验证明文密码与哈希是否匹配"""
    if not hashed:
        return False
    try:
        pwd = plain.encode("utf-8")[:72]
        return bcrypt.checkpw(pwd, hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str | int, extra: dict[str, Any] | None = None) -> str:
    """签发访问令牌（30 分钟）"""
    expire = datetime.now(tz=timezone.utc) + timedelta(
        minutes=settings.APP_JWT_ACCESS_TTL_MIN
    )
    payload: dict[str, Any] = {"sub": str(subject), "exp": expire, "type": "access"}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.APP_SECRET_KEY, algorithm=settings.APP_JWT_ALGORITHM)


def create_refresh_token(subject: str | int) -> str:
    """签发刷新令牌（7 天）"""
    expire = datetime.now(tz=timezone.utc) + timedelta(
        days=settings.APP_JWT_REFRESH_TTL_DAYS
    )
    payload = {"sub": str(subject), "exp": expire, "type": "refresh"}
    return jwt.encode(payload, settings.APP_SECRET_KEY, algorithm=settings.APP_JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """解码并校验令牌，无效则抛 JWTError"""
    try:
        return jwt.decode(token, settings.APP_SECRET_KEY, algorithms=[settings.APP_JWT_ALGORITHM])
    except JWTError as exc:
        raise JWTError(f"invalid token: {exc}") from exc
