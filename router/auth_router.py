# 认证路由：auth_router（邮箱+密码+JWT）
from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fwsort.config import settings
from fwsort.database import get_async_db
from fwsort.exceptions import AuthError, NotFoundError, ParamError
from fwsort.models import LoginAttempt, User
from fwsort.response import success
from fwsort.schemas import LoginReq, RegisterReq, TokenResp
from fwsort.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

from fwsort.rate_limit import (  # WP-03
    check_login_rate_limit,
    check_register_rate_limit,
    clear_login_failures,
    record_login_failure,
    record_register_attempt,
)


router = APIRouter()

# OAuth2 令牌 URL（用于 Swagger）
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


# ========== 请求模型 ==========
class ChangePasswordReq(BaseModel):
    """修改密码请求"""
    old_password: str
    new_password: str


class UpdateNicknameReq(BaseModel):
    """更新昵称请求"""
    nickname: str


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


async def current_user_optional(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_async_db),
) -> User | None:
    """WP-04：可选当前用户解析（无 token / 非法 token → 返回 None，不抛错）
    仅用于"首次启动放行 / 已有 admin 必须鉴权"的混合场景
    """
    if not token:
        return None
    try:
        payload = decode_token(token)
    except JWTError:
        return None
    if payload.get("type") != "access":
        return None
    user_id = int(payload.get("sub", 0))
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or user.status != 0:
        return None
    return user


# ========== 接口：是否已有管理员（公开端点，方便前端首次启动引导）==========
@router.get("/has-admin", response_model=dict)
async def has_admin(db: AsyncSession = Depends(get_async_db)) -> dict:
    """公开：查询系统是否已经存在管理员（role>=3）
    - 用于前端登录弹窗：如果没有管理员，提示"请先去 /admin 播种"或"进入演示模式"
    - 无鉴权、无副作用，仅返回 bool
    """
    from sqlalchemy import select, func
    cnt = (await db.execute(select(func.count(User.id)).where(User.role >= 3))).scalar_one() or 0
    return success(
        {"has_admin": cnt > 0, "count": int(cnt)},
        message="has-admin check ok",
    )


# ========== 接口：注册 ==========
@router.post("/register", response_model=dict)
async def register(
    req: RegisterReq,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    """用户注册（邮箱唯一；WP-03 同 IP 限流）"""
    # 限流前置
    await check_register_rate_limit(request)
    await record_register_attempt(request)

    exists = (await db.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
    if exists:
        logger.bind(action="register_fail", email=req.email).info("email already registered")
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
    logger.bind(action="register_success", user_id=user.id, email=req.email).info("user registered")
    return success({"user_id": user.id}, message="register success")


# ========== 工具：登录审计持久化（WP-03）==========
async def _record_login_audit(
    db: AsyncSession,
    email: str,
    request: Request,
    success: bool,
    reason: str = "",
) -> None:
    """把每次登录尝试（成功/失败）写入 login_attempt 表，供安全审计
    - 失败原因：invalid_credentials / user_disabled / locked
    - 成功原因：success
    - 不抛错：审计失败不影响登录主流程
    """
    try:
        ip = (
            request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "")
        )
        ua = request.headers.get("user-agent", "")[:256]
        db.add(LoginAttempt(
            email=email,
            ip=ip or "unknown",
            success=success,
            user_agent=ua,
            reason=reason,
        ))
        await db.flush()
    except Exception as e:  # noqa: BLE001
        # 审计失败只记日志，不影响主流程
        logger.warning(f"login audit persist failed (skip): {e}")


# ========== 接口：登录 ==========
@router.post("/login", response_model=dict)
async def login(
    req: LoginReq,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    """邮箱+密码登录，签发 JWT（WP-03 失败计数 + 锁定 + 审计）"""
    # 限流前置
    await check_login_rate_limit(req.email, request)

    user = (await db.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
    if not user or not verify_password(req.password, user.password_hash):
        # 记录失败
        n = await record_login_failure(req.email, request)
        await _record_login_audit(db, req.email, request, success=False, reason="invalid_credentials")
        logger.bind(action="login_fail", email=req.email, failures=n).warning("login failed")
        raise AuthError("invalid email or password")
    if user.status != 0:
        await _record_login_audit(db, req.email, request, success=False, reason="user_disabled")
        raise AuthError("user disabled")

    # 成功：清除计数 + 审计
    await clear_login_failures(req.email, request)
    await _record_login_audit(db, req.email, request, success=True, reason="success")
    logger.bind(action="login_success", user_id=user.id, email=req.email).info("user logged in")

    token = TokenResp(
        access_token=create_access_token(user.id, {"role": user.role}, ttl_minutes=user.token_ttl_minutes),
        refresh_token=create_refresh_token(user.id),
        user_id=user.id,
        nickname=user.nickname,
        role=user.role,
    )
    return success(token.model_dump(), message="login success")


# ========== 接口：演示模式自动登录（无需密码）==========
@router.post("/demo-login", response_model=dict)
async def demo_login(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    """演示模式自动登录：用默认 admin 账号签发 token，无需密码。
    - 仅当 APP_DEMO_MODE=True 时开放
    - 演示模式下前端页面自动调用，无需用户输入
    - 失败时返回 503（提示需先 seed-admin）
    """
    if not settings.APP_DEMO_MODE:
        from fwsort.exceptions import PermissionError_
        raise PermissionError_("demo mode disabled")

    # 1) 找默认管理员（没有就尝试 bootstrap 创建）
    admin = (await db.execute(select(User).where(User.role == 3))).scalar_one_or_none()
    if not admin:
        # 演示模式自动 bootstrap 一个 admin 账号，避免演示前需手动 seed
        try:
            admin = User(
                email="admin@fwquant.com",
                password_hash=hash_password("admin123456"),
                nickname="演示管理员",
                role=3,
                status=0,
            )
            db.add(admin)
            await db.flush()
            logger.bind(action="demo_bootstrap_admin", user_id=admin.id).warning(
                "demo mode: auto-created admin account"
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"demo-login bootstrap admin failed: {e}")
            from fwsort.exceptions import NotFoundError
            raise NotFoundError("admin not found, please seed-admin first")

    if admin.status != 0:
        from fwsort.exceptions import AuthError
        raise AuthError("demo admin disabled")

    # 2) 签发 token
    token = TokenResp(
        access_token=create_access_token(admin.id, {"role": admin.role, "demo": True}, ttl_minutes=admin.token_ttl_minutes),
        refresh_token=create_refresh_token(admin.id),
        user_id=admin.id,
        nickname=admin.nickname,
        role=admin.role,
    )
    logger.bind(action="demo_login", user_id=admin.id).info("demo mode auto login")
    return success(token.model_dump(), message="demo login success")


# ========== 接口：当前用户信息 ==========
@router.get("/me", response_model=dict)
async def me(user: User = Depends(current_user)) -> dict:
    """获取当前登录用户信息（含注册时间）"""
    return success(
        {
            "id": user.id,
            "email": user.email,
            "nickname": user.nickname,
            "role": user.role,
            "role_name": {0: "访客", 1: "策略所有者", 2: "组合管理者", 3: "管理员"}.get(user.role, "未知"),
            "status": user.status,
            "status_name": {0: "正常", 1: "禁用"}.get(user.status, "未知"),
            "share_to_global": bool(user.share_to_global),
            "allow_follow": bool(user.allow_follow),
            "token_ttl_minutes": user.token_ttl_minutes,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "updated_at": user.updated_at.isoformat() if user.updated_at else None,
        }
    )


# ========== 接口：修改密码 ==========
@router.post("/change-password", response_model=dict)
async def change_password(
    req: ChangePasswordReq,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """修改当前用户密码"""
    # 验证旧密码
    if not verify_password(req.old_password, user.password_hash):
        raise AuthError("旧密码错误")

    # 验证新密码长度
    if len(req.new_password) < 1:
        raise ParamError("新密码长度至少1位")

    # 更新密码
    user.password_hash = hash_password(req.new_password)
    await db.flush()
    return success(message="密码修改成功")


# ========== 接口：更新昵称 ==========
@router.post("/update-nickname", response_model=dict)
async def update_nickname(
    req: UpdateNicknameReq,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """更新当前用户昵称"""
    nickname = req.nickname.strip()
    if not nickname:
        raise ParamError("昵称不能为空")
    if len(nickname) > 32:
        raise ParamError("昵称最长32个字符")

    user.nickname = nickname
    await db.flush()
    return success({"nickname": nickname}, message="昵称已更新")


# ========== 请求模型（续）==========
class UpdatePrivacyReq(BaseModel):
    share_to_global: bool | None = None
    allow_follow: bool | None = None
    token_ttl_minutes: int | None = None


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
            "access_token": create_access_token(user.id, {"role": user.role}, ttl_minutes=user.token_ttl_minutes),
            "token_type": "bearer",
        }
    )


# ========== 接口：主账号可见性设置（toggle）==========
@router.get("/privacy", response_model=dict)
async def get_privacy(
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """获取当前用户的主账号可见性设置（用于前端 toggle 初始值）"""
    return success(
        {
            "share_to_global": bool(user.share_to_global),
            "allow_follow": bool(user.allow_follow),
            "token_ttl_minutes": user.token_ttl_minutes,
        }
    )


@router.post("/privacy", response_model=dict)
async def update_privacy(
    req: UpdatePrivacyReq,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """更新主账号可见性设置（toggle）
    - share_to_global: True=参与总榜单聚合
    - allow_follow: True=允许被订阅（跟单）
    """
    if req.share_to_global is not None:
        user.share_to_global = bool(req.share_to_global)
    if req.allow_follow is not None:
        user.allow_follow = bool(req.allow_follow)
    if req.token_ttl_minutes is not None:
        if req.token_ttl_minutes not in (30, 60, 180, 1440, 10080):
            from fwsort.exceptions import ParamError
            raise ParamError("token_ttl_minutes must be one of: 30, 60, 180, 1440, 10080")
        user.token_ttl_minutes = req.token_ttl_minutes
    await db.flush()
    logger.bind(
        action="update_privacy",
        user_id=user.id,
        share_to_global=user.share_to_global,
        allow_follow=user.allow_follow,
    ).info("privacy updated")
    return success(
        {
            "share_to_global": bool(user.share_to_global),
            "allow_follow": bool(user.allow_follow),
            "token_ttl_minutes": user.token_ttl_minutes,
        },
        message="privacy updated",
    )