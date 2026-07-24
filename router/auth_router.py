# 认证路由：auth_router（邮箱+密码+JWT）
from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_async_db
from core.exceptions import AuthError, NotFoundError
from core.models import User
from core.response import success
from core.schemas import LoginReq, RegisterReq, TokenResp
from core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

router = APIRouter()

# OAuth2 令牌 URL（用于 Swagger）
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


# ========== 工具：当前用户解析 ==========
async def current_user(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_async_db),
) -> User:
    """FastAPI 依赖：从 Bearer Token 解析当前用户"""
    if not token:
        raise AuthError("missing token")
    try:
        payload = decode_token(token)
    except JWTError as exc:
        raise AuthError(str(exc)) from exc

    if payload.get("type") != "access":
        raise AuthError("token type mismatch")

    user_id = int(payload.get("sub", 0))
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or user.status != 0:
        raise AuthError("user disabled or not found")
    return user


# ========== 接口：注册 ==========
@router.post("/register", response_model=dict)
async def register(req: RegisterReq, db: AsyncSession = Depends(get_async_db)) -> dict:
    """用户注册（邮箱唯一）"""
    exists = (await db.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
    if exists:
        raise AuthError("email already registered")
    user = User(
        email=req.email,
        password_hash=hash_password(req.password),
        nickname=req.nickname,
        role=0,
        status=0,
    )
    db.add(user)
    await db.flush()
    return success({"user_id": user.id}, message="register success")


# ========== 接口：登录 ==========
@router.post("/login", response_model=dict)
async def login(req: LoginReq, db: AsyncSession = Depends(get_async_db)) -> dict:
    """邮箱+密码登录，签发 JWT"""
    user = (await db.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
    if not user or not verify_password(req.password, user.password_hash):
        raise AuthError("invalid email or password")
    if user.status != 0:
        raise AuthError("user disabled")

    token = TokenResp(
        access_token=create_access_token(user.id, {"role": user.role}),
        refresh_token=create_refresh_token(user.id),
        user_id=user.id,
        nickname=user.nickname,
        role=user.role,
    )
    return success(token.model_dump(), message="login success")


# ========== 接口：当前用户信息 ==========
@router.get("/me", response_model=dict)
async def me(user: User = Depends(current_user)) -> dict:
    """获取当前登录用户信息"""
    return success(
        {
            "id": user.id,
            "email": user.email,
            "nickname": user.nickname,
            "role": user.role,
        }
    )


# ========== 接口：刷新令牌 ==========
@router.post("/refresh", response_model=dict)
async def refresh_token(
    refresh_token: str,
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    """用 refresh_token 换取新 access_token"""
    try:
        payload = decode_token(refresh_token)
    except JWTError as exc:
        raise AuthError(str(exc)) from exc
    if payload.get("type") != "refresh":
        raise AuthError("token type mismatch")
    user_id = int(payload.get("sub", 0))
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or user.status != 0:
        raise NotFoundError("user not found")
    return success(
        {
            "access_token": create_access_token(user.id, {"role": user.role}),
            "token_type": "bearer",
        }
    )
