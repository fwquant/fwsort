# ========== 风控管理路由（与 strategy / admin 平级）==========
# 权限：
#   - 账户级 / 策略级配置：仅所属用户可读 / 可写
#   - 风控模板：系统内置模板所有人可读；用户自己的模板可写
#   - 风控事件日志：仅本人 / 管理员可见
#   - 冻结 / 解冻：管理员权限 + 本人可申请（但需确认）
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc, or_, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from fwsort.database import get_async_db, get_sync_db
from fwsort.exceptions import NotFoundError, ParamError, PermissionError_
from fwsort.fwlogs import logger
from fwsort.models import (
    AutoStrategy,
    ExecutionAccount,
    User,
)
from fwsort.response import success
from fwsort.risk.manager import RiskProfileManager
from fwsort.risk.models import (
    AccountRiskProfile,
    RiskEventLog,
    RiskProfile,
    StrategyRiskProfile,
)
from fwsort.risk.schemas import (
    AccountRiskPatch,
    AccountRiskProfileOut,
    FreezeAccountReq,
    RiskEventListResp,
    RiskEventLogOut,
    RiskProfileCreate,
    RiskProfileOut,
    RiskProfileUpdate,
    StrategyRiskPatch,
    StrategyRiskProfileOut,
    UnfreezeAccountReq,
)
from fwsort.risk.service import RiskControlService
from router.auth_router import current_user

router = APIRouter(prefix="/api/risk", tags=["risk"])


# ==================== 工具：权限校验 ====================
async def _require_account_owner(
    db: AsyncSession, account_id: int, user: User,
) -> ExecutionAccount:
    acc = (await db.execute(select(ExecutionAccount).where(
        ExecutionAccount.id == account_id,
    ))).scalar_one_or_none()
    if acc is None:
        raise NotFoundError(f"account {account_id} not found")
    if user.role < 3 and acc.owner_id != user.id:
        raise PermissionError_("not your account")
    return acc


async def _require_strategy_owner(
    db: AsyncSession, auto_strategy_id: int, user: User,
) -> AutoStrategy:
    task = (await db.execute(select(AutoStrategy).where(
        AutoStrategy.id == auto_strategy_id,
    ))).scalar_one_or_none()
    if task is None:
        raise NotFoundError(f"auto strategy {auto_strategy_id} not found")
    if user.role >= 3:
        return task
    # 自动任务的 owner 通过关联的 account_id 找
    if task.account_id:
        acc = (await db.execute(select(ExecutionAccount).where(
            ExecutionAccount.id == task.account_id,
        ))).scalar_one_or_none()
        if acc and acc.owner_id == user.id:
            return task
    raise PermissionError_("not your auto strategy")


def _event_to_out(e: RiskEventLog) -> RiskEventLogOut:
    try:
        dj = json.loads(e.detail_json or "{}")
    except Exception:
        dj = {}
    return RiskEventLogOut(
        id=e.id, event_uid=e.event_uid,
        account_id=e.account_id, auto_strategy_id=e.auto_strategy_id, user_id=e.user_id,
        rule_name=e.rule_name, event_type=e.event_type, severity=e.severity, stage=e.stage,
        title=e.title, message=e.message, detail_json=dj,
        balance_snapshot=float(e.balance_snapshot),
        daily_pnl_snapshot=float(e.daily_pnl_snapshot),
        order_amount_snapshot=float(e.order_amount_snapshot),
        created_at=e.created_at,
    )


# ======================================================================
#  一、风控模板（RiskProfile）CRUD
# ======================================================================
@router.get("/profiles", response_model=dict)
async def list_risk_profiles(
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """列出所有可见的风控模板：系统内置（owner_id=NULL）+ 我自己的"""
    rows = (await db.execute(select(RiskProfile).where(or_(
        RiskProfile.owner_id.is_(None),
        RiskProfile.owner_id == user.id,
    )).order_by(desc(RiskProfile.is_default), RiskProfile.name))).scalars().all()
    return success(data={
        "count": len(rows),
        "items": [RiskProfileOut.model_validate(r).model_dump() for r in rows],
    })


@router.post("/profiles", response_model=dict)
async def create_risk_profile(
    req: RiskProfileCreate,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """创建风控模板（用户级）"""
    # 复制已有模板
    base = None
    if req.risk_profile_id:
        base = (await db.execute(select(RiskProfile).where(
            RiskProfile.id == req.risk_profile_id,
            or_(RiskProfile.owner_id.is_(None), RiskProfile.owner_id == user.id),
        ))).scalar_one_or_none()
        if base is None:
            raise NotFoundError(f"base profile {req.risk_profile_id} not found or not accessible")

    row = RiskProfile(
        name=req.name[:64],
        owner_id=user.id,
        is_default=bool(req.is_default),
        description=req.description or "",
    )
    # 先拷贝模板值，再用 req.params 覆盖
    if base:
        for f in (
            "risk_single_ratio", "risk_daily_loss_ratio",
            "max_daily_amount", "max_daily_count", "max_consecutive_failures",
            "max_drawdown_ratio", "max_open_positions",
            "stop_loss_ratio", "take_profit_ratio",
        ):
            setattr(row, f, getattr(base, f, None))
    pd = req.params.model_dump(exclude_unset=True)
    for k, v in pd.items():
        if hasattr(row, k) and v is not None:
            setattr(row, k, v)
    # 若设为默认：取消该用户其他模板的默认
    if row.is_default:
        others = (await db.execute(select(RiskProfile).where(
            RiskProfile.owner_id == user.id, RiskProfile.is_default == True,  # noqa: E712
            RiskProfile.id != row.id,
        ))).scalars().all()
        for o in others:
            o.is_default = False
    db.add(row)
    await db.flush()
    logger.info(f"[risk_router] create profile id={row.id} name={row.name} by user={user.id}")
    return success(data=RiskProfileOut.model_validate(row).model_dump(), message="profile created")


@router.patch("/profiles/{profile_id}", response_model=dict)
async def update_risk_profile(
    profile_id: int,
    req: RiskProfileUpdate,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """更新风控模板（仅创建者 / 管理员）"""
    row = (await db.execute(select(RiskProfile).where(
        RiskProfile.id == profile_id,
    ))).scalar_one_or_none()
    if not row:
        raise NotFoundError("profile not found")
    if user.role < 3 and row.owner_id != user.id:
        raise PermissionError_("not your profile")
    data = req.model_dump(exclude_unset=True)
    params_fields = {
        "risk_single_ratio", "risk_daily_loss_ratio",
        "max_daily_amount", "max_daily_count", "max_consecutive_failures",
        "max_drawdown_ratio", "max_open_positions",
        "stop_loss_ratio", "take_profit_ratio",
    }
    for k, v in data.items():
        if v is None and k not in ("is_default", "is_active", "name", "description"):
            # 允许把参数设为 NULL（恢复到"取全局默认"）
            if k in params_fields:
                setattr(row, k, None)
            continue
        if hasattr(row, k):
            setattr(row, k, v)
    # 设置默认：取消其他默认
    if data.get("is_default"):
        others = (await db.execute(select(RiskProfile).where(
            RiskProfile.owner_id == (row.owner_id or user.id),
            RiskProfile.is_default == True,  # noqa: E712
            RiskProfile.id != row.id,
        ))).scalars().all()
        for o in others:
            o.is_default = False
    await db.flush()
    return success(data=RiskProfileOut.model_validate(row).model_dump(), message="profile updated")


@router.delete("/profiles/{profile_id}", response_model=dict)
async def delete_risk_profile(
    profile_id: int,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """删除风控模板（软：仅置 is_active=False）"""
    row = (await db.execute(select(RiskProfile).where(
        RiskProfile.id == profile_id,
    ))).scalar_one_or_none()
    if not row:
        raise NotFoundError("profile not found")
    if user.role < 3 and row.owner_id != user.id:
        raise PermissionError_("not your profile")
    if row.owner_id is None:
        raise PermissionError_("cannot delete system built-in profile")
    row.is_active = False
    await db.flush()
    return success(message="profile deactivated")


# ======================================================================
#  二、账户级风控配置（AccountRiskProfile）
# ======================================================================
@router.get("/account/{account_id}", response_model=dict)
async def get_account_risk(
    account_id: int,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """查询账户级风控配置（含实际生效参数）"""
    acc = await _require_account_owner(db, account_id, user)
    with get_sync_db() as sdb:
        profile, effective = RiskProfileManager.resolve_account_params(sdb, account_id)
        sdb.commit()
    # 把 profile 同步读到 async session（或直接 dict 化）
    p = (await db.execute(select(AccountRiskProfile).where(
        AccountRiskProfile.account_id == account_id,
    ))).scalar_one_or_none()
    if p is None:
        # 兜底：用同步读出来的 profile 转换
        p = profile
    data = AccountRiskProfileOut(
        id=p.id, account_id=p.account_id, risk_profile_id=p.risk_profile_id,
        risk_single_ratio=p.risk_single_ratio,
        risk_daily_loss_ratio=p.risk_daily_loss_ratio,
        max_daily_amount=p.max_daily_amount,
        max_daily_count=p.max_daily_count,
        max_consecutive_failures=p.max_consecutive_failures,
        max_drawdown_ratio=p.max_drawdown_ratio,
        max_open_positions=p.max_open_positions,
        stop_loss_ratio=p.stop_loss_ratio,
        take_profit_ratio=p.take_profit_ratio,
        consecutive_failures=p.consecutive_failures,
        is_frozen=p.is_frozen, frozen_reason=p.frozen_reason,
        frozen_at=p.frozen_at, last_check_at=p.last_check_at,
        effective_params={k: (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
                           for k, v in effective.items()},
    )
    return success(data=data.model_dump())


@router.patch("/account/{account_id}", response_model=dict)
async def patch_account_risk(
    account_id: int,
    req: AccountRiskPatch,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """修改账户级风控参数（覆盖模板 / 切换模板）"""
    await _require_account_owner(db, account_id, user)
    with get_sync_db() as sdb:
        p = RiskProfileManager.get_or_create_account_profile(sdb, account_id)
        data = req.model_dump(exclude_unset=True)
        if "risk_profile_id" in data:
            p.risk_profile_id = data["risk_profile_id"]
        param_fields = {
            "risk_single_ratio", "risk_daily_loss_ratio",
            "max_daily_amount", "max_daily_count", "max_consecutive_failures",
            "max_drawdown_ratio", "max_open_positions",
            "stop_loss_ratio", "take_profit_ratio",
        }
        for k, v in data.items():
            if k in param_fields:
                setattr(p, k, v)  # 允许设为 None（去掉个性化覆盖）
        sdb.commit()
    # 读回生效参数
    with get_sync_db() as sdb:
        _, effective = RiskProfileManager.resolve_account_params(sdb, account_id)
        sdb.commit()
    return success(message="account risk patched", data={
        "account_id": account_id,
        "effective_params": effective,
    })


# ======================================================================
#  三、策略级风控配置（StrategyRiskProfile）
# ======================================================================
@router.get("/strategy/{auto_strategy_id}", response_model=dict)
async def get_strategy_risk(
    auto_strategy_id: int,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """查询自动任务级风控配置"""
    await _require_strategy_owner(db, auto_strategy_id, user)
    with get_sync_db() as sdb:
        profile, effective = RiskProfileManager.resolve_strategy_params(sdb, auto_strategy_id)
        sdb.commit()
    out = StrategyRiskProfileOut(
        id=profile.id, auto_strategy_id=profile.auto_strategy_id,
        risk_profile_id=profile.risk_profile_id,
        max_daily_amount=profile.max_daily_amount,
        max_daily_count=profile.max_daily_count,
        max_consecutive_failures=profile.max_consecutive_failures,
        risk_single_ratio=profile.risk_single_ratio,
        risk_daily_loss_ratio=profile.risk_daily_loss_ratio,
        max_drawdown_ratio=profile.max_drawdown_ratio,
        max_open_positions=getattr(profile, 'max_open_positions', None),
        stop_loss_ratio=getattr(profile, 'stop_loss_ratio', None),
        take_profit_ratio=getattr(profile, 'take_profit_ratio', None),
        consecutive_failures=profile.consecutive_failures,
        effective_params=effective,
    )
    return success(data=out.model_dump())


@router.patch("/strategy/{auto_strategy_id}", response_model=dict)
async def patch_strategy_risk(
    auto_strategy_id: int,
    req: StrategyRiskPatch,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """修改自动任务级风控参数"""
    await _require_strategy_owner(db, auto_strategy_id, user)
    data = req.model_dump(exclude_unset=True)
    with get_sync_db() as sdb:
        p = RiskProfileManager.get_or_create_strategy_profile(sdb, auto_strategy_id)
        if "risk_profile_id" in data:
            p.risk_profile_id = data["risk_profile_id"]
        for k in (
            "risk_single_ratio", "risk_daily_loss_ratio",
            "max_daily_amount", "max_daily_count", "max_consecutive_failures",
            "max_drawdown_ratio", "max_open_positions",
            "stop_loss_ratio", "take_profit_ratio",
        ):
            if k in data:
                setattr(p, k, data[k])
        # 同步镜像到 AutoStrategy 旧字段（向后兼容）
        task = sdb.query(AutoStrategy).filter(AutoStrategy.id == auto_strategy_id).first()
        if task is not None:
            if p.max_daily_amount is not None:
                task.max_daily_amount = float(p.max_daily_amount)
            if p.max_daily_count is not None:
                task.max_daily_count = int(p.max_daily_count)
            if p.max_consecutive_failures is not None:
                task.max_consecutive_failures = int(p.max_consecutive_failures)
            task.consecutive_failures = p.consecutive_failures
        sdb.commit()
    return success(message="strategy risk patched")


# ======================================================================
#  四、冻结 / 解冻账户（唯一入口）
# ======================================================================
@router.post("/account/freeze", response_model=dict)
async def freeze_account(
    req: FreezeAccountReq,
    user: User = Depends(current_user),
) -> dict:
    """冻结账户（仅管理员 / 未来可支持自冻结）"""
    if user.role < 3:
        raise PermissionError_("admin required to freeze account")
    with get_sync_db() as sdb:
        acc = sdb.query(ExecutionAccount).filter(ExecutionAccount.id == req.account_id).first()
        if acc is None:
            raise NotFoundError("account not found")
        just_frozen = RiskControlService.freeze_account(
            sdb, req.account_id, reason=req.reason,
            operator_user_id=user.id,
        )
        sdb.commit()
    return success(data={
        "account_id": req.account_id,
        "action": "frozen",
        "changed": just_frozen,
        "reason": req.reason,
    }, message="account frozen by admin")


@router.post("/account/unfreeze", response_model=dict)
async def unfreeze_account(
    req: UnfreezeAccountReq,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """解冻账户（管理员或本人）"""
    acc = await _require_account_owner(db, req.account_id, user)
    with get_sync_db() as sdb:
        was = RiskControlService.unfreeze_account(
            sdb, req.account_id,
            reason=req.reason or f"unfrozen by user {user.id}",
            operator_user_id=user.id,
        )
        # 同步镜像
        acc_row = sdb.query(ExecutionAccount).filter(ExecutionAccount.id == req.account_id).first()
        if acc_row is not None:
            acc_row.risk_frozen = False
        sdb.commit()
    return success(data={
        "account_id": req.account_id,
        "action": "unfrozen",
        "changed": was,
    }, message="account unfrozen")


# ======================================================================
#  五、风控事件日志（审计查询）
# ======================================================================
@router.get("/events", response_model=dict)
async def list_risk_events(
    account_id: Optional[int] = Query(None, description="按账户过滤"),
    auto_strategy_id: Optional[int] = Query(None, description="按自动任务过滤"),
    event_type: Optional[int] = Query(None, description="1通过 2拦截 3冻结 4解冻 5参数变更"),
    severity: Optional[int] = Query(None, description="1信息 2警告 3严重"),
    rule_name: Optional[str] = Query(None, description="按规则名过滤"),
    stage: Optional[str] = Query(None, description="pre_vote / pre_order / post_settle"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """查询风控事件日志（本人账户可见所有事件；管理员可见全部）"""
    q = select(RiskEventLog)
    conds = []
    # 权限过滤：普通用户只能看自己账户 / 自己任务 / 自己作为 operator 的事件
    if user.role < 3:
        my_acc_ids = [
            r[0] for r in (await db.execute(select(ExecutionAccount.id).where(
                ExecutionAccount.owner_id == user.id,
            ))).all()
        ]
        my_task_ids = [
            r[0] for r in (await db.execute(select(AutoStrategy.id).join(
                ExecutionAccount, AutoStrategy.account_id == ExecutionAccount.id,
            ).where(ExecutionAccount.owner_id == user.id))).all()
        ]
        conds.append(or_(
            RiskEventLog.user_id == user.id,
            RiskEventLog.account_id.in_(my_acc_ids) if my_acc_ids else False,
            RiskEventLog.auto_strategy_id.in_(my_task_ids) if my_task_ids else False,
        ))
    if account_id:
        # 若传了 account_id，再次鉴权
        await _require_account_owner(db, account_id, user)
        conds.append(RiskEventLog.account_id == account_id)
    if auto_strategy_id:
        await _require_strategy_owner(db, auto_strategy_id, user)
        conds.append(RiskEventLog.auto_strategy_id == auto_strategy_id)
    if event_type is not None:
        conds.append(RiskEventLog.event_type == event_type)
    if severity is not None:
        conds.append(RiskEventLog.severity == severity)
    if rule_name:
        conds.append(RiskEventLog.rule_name.like(f"%{rule_name}%"))
    if stage:
        conds.append(RiskEventLog.stage == stage)

    where = and_(*conds) if conds else True
    total = (await db.execute(
        select(func.count(RiskEventLog.id)).select_from(RiskEventLog).where(where)
    )).scalar_one() or 0
    rows = (await db.execute(
        select(RiskEventLog).where(where)
        .order_by(desc(RiskEventLog.created_at))
        .limit(limit).offset(offset)
    )).scalars().all()
    return success(data=RiskEventListResp(
        total=total, items=[_event_to_out(r) for r in rows],
    ).model_dump())


# ======================================================================
#  六、全局风控参数概览（admin dashboard 用）
# ======================================================================
@router.get("/summary", response_model=dict)
async def risk_summary(
    user: User = Depends(current_user),
) -> dict:
    """风控统计摘要（冻结账户数 / 近 24h 拦截次数 / 冻结次数）"""
    if user.role < 3:
        raise PermissionError_("admin required")
    with get_sync_db() as sdb:
        from sqlalchemy import func as f
        frozen_accounts = sdb.query(f.count(AccountRiskProfile.id)).filter(
            AccountRiskProfile.is_frozen == True,  # noqa: E712
        ).scalar() or 0
        yesterday = datetime.utcnow() - timedelta(days=1)
        last_24h_blocked = sdb.query(f.count(RiskEventLog.id)).filter(
            RiskEventLog.created_at >= yesterday, RiskEventLog.event_type == 2,
        ).scalar() or 0
        last_24h_frozen = sdb.query(f.count(RiskEventLog.id)).filter(
            RiskEventLog.created_at >= yesterday, RiskEventLog.event_type == 3,
        ).scalar() or 0
        last_24h_unfrozen = sdb.query(f.count(RiskEventLog.id)).filter(
            RiskEventLog.created_at >= yesterday, RiskEventLog.event_type == 4,
        ).scalar() or 0
    return success(data={
        "frozen_accounts": frozen_accounts,
        "last_24h": {
            "blocked_events": last_24h_blocked,
            "freeze_events": last_24h_frozen,
            "unfreeze_events": last_24h_unfrozen,
        },
    })
