# 跟单路由：订阅/取消/我的订阅/跟单成交
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_async_db
from core.exceptions import NotFoundError, ParamError
from core.models import ExecutionAccount, FollowOrder, FollowSubscription, StrategyPerformance, User
from core.response import success
from router.auth_router import current_user

router = APIRouter()


# ========== 1. 跟单市场：拉榜单 Top 20 当候选 leader ==========
@router.get("/market", response_model=dict)
async def follow_market(
    rank_type: str = "all_time",
    limit: int = 20,
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    """跟单市场：返回 Top N 候选 leader（按综合分倒序）"""
    from sqlalchemy import desc

    rows = (
        await db.execute(
            select(StrategyPerformance, ExecutionAccount)
            .join(ExecutionAccount, ExecutionAccount.id == StrategyPerformance.account_id)
            .where(StrategyPerformance.period_type == 4)  # 总榜
            .order_by(desc(StrategyPerformance.composite_score))
            .limit(limit)
        )
    ).all()
    items = []
    for idx, (sp, acc) in enumerate(rows, start=1):
        items.append({
            "rank": idx,
            "uid": acc.uid,
            "leader_uid": acc.uid,
            "name": acc.name,
            "leader_name": acc.name,
            "platform": acc.platform,
            "composite_score": float(sp.composite_score),
            "annualized_return": float(sp.annualized_return),
            "max_drawdown": float(sp.max_drawdown),
            "sharpe_ratio": float(sp.sharpe_ratio),
            "win_rate": float(sp.win_rate),
            "trade_count": sp.trade_count,
            "execution_score": float(sp.execution_score),
            "subscription_fee_usd": 9.9,
            "profit_share_ratio": 0.20,
        })
    return success(data={"rank_type": rank_type, "count": len(items), "items": items})


# ========== 2. 订阅 ==========
@router.post("/subscribe", response_model=dict)
async def subscribe(
    leader_uid: str,
    mode: int = 3,
    amount: float = 50.0,
    months: int = 1,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """订阅 leader

    - mode: 1-纯订阅 2-纯分成 3-订阅+分成
    - amount: 每笔跟单金额(USDT)
    - months: 订阅月数
    """
    if mode not in (1, 2, 3):
        raise ParamError("mode must be 1/2/3")
    if amount <= 0:
        raise ParamError("amount must > 0")
    if months <= 0 or months > 12:
        raise ParamError("months must in 1..12")

    # 校验 leader 存在
    leader_acc = (await db.execute(select(ExecutionAccount).where(ExecutionAccount.uid == leader_uid))).scalar_one_or_none()
    if not leader_acc:
        raise NotFoundError(f"leader uid {leader_uid} not found")

    # 已存在有效订阅则续期
    existing = (
        await db.execute(
            select(FollowSubscription).where(
                FollowSubscription.subscriber_id == user.id,
                FollowSubscription.leader_uid == leader_uid,
                FollowSubscription.status == 1,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.expires_at = (existing.expires_at or datetime.now()) + timedelta(days=30 * months)
        existing.follow_amount_usd = amount
        existing.mode = mode
        await db.flush()
        return success({"id": existing.id, "expires_at": existing.expires_at.isoformat()}, message="renewed")

    sub = FollowSubscription(
        subscriber_id=user.id,
        leader_uid=leader_uid,
        leader_name=leader_acc.name,
        mode=mode,
        follow_amount_usd=amount,
        subscription_fee_usd=9.9 * months,
        profit_share_ratio=0.20,
        status=1,
        expires_at=datetime.now() + timedelta(days=30 * months),
    )
    db.add(sub)
    await db.flush()
    return success(
        {
            "id": sub.id,
            "leader_uid": leader_uid,
            "leader_name": leader_acc.name,
            "mode": mode,
            "follow_amount_usd": amount,
            "subscription_fee_usd": float(sub.subscription_fee_usd),
            "expires_at": sub.expires_at.isoformat(),
        },
        message="subscribed",
    )


# ========== 3. 我的订阅 ==========
@router.get("/my", response_model=dict)
async def my_subscriptions(
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """当前用户的所有订阅（含历史）"""
    rows = (
        await db.execute(
            select(FollowSubscription)
            .where(FollowSubscription.subscriber_id == user.id)
            .order_by(FollowSubscription.created_at.desc())
        )
    ).scalars().all()
    return success(data={"count": len(rows), "subscriptions": [
        {
            "id": s.id,
            "leader_uid": s.leader_uid,
            "leader_name": s.leader_name,
            "mode": s.mode,
            "subscription_fee_usd": float(s.subscription_fee_usd),
            "profit_share_ratio": float(s.profit_share_ratio),
            "follow_amount_usd": float(s.follow_amount_usd),
            "total_followed": s.total_followed,
            "total_pnl": float(s.total_pnl),
            "total_fee_paid": float(s.total_fee_paid),
            "total_share_paid": float(s.total_share_paid),
            "status": s.status,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "expires_at": s.expires_at.isoformat() if s.expires_at else None,
        }
        for s in rows
    ]})


# ========== 4. 取消订阅 ==========
@router.delete("/{sub_id}", response_model=dict)
async def cancel_subscription(
    sub_id: int,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """取消订阅（软取消：status=0）"""
    sub = (
        await db.execute(
            select(FollowSubscription).where(
                FollowSubscription.id == sub_id,
                FollowSubscription.subscriber_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if not sub:
        raise NotFoundError("subscription not found")
    sub.status = 0
    await db.flush()
    return success(message="cancelled")


# ========== 5. 跟单成交记录 ==========
@router.get("/orders/{sub_id}", response_model=dict)
async def follow_orders(
    sub_id: int,
    limit: int = 50,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """某订阅的跟单成交记录"""
    sub = (
        await db.execute(
            select(FollowSubscription).where(
                FollowSubscription.id == sub_id,
                FollowSubscription.subscriber_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if not sub:
        raise NotFoundError("subscription not found")
    rows = (
        await db.execute(
            select(FollowOrder)
            .where(FollowOrder.subscription_id == sub_id)
            .order_by(FollowOrder.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return success(data={"sub_id": sub_id, "count": len(rows), "orders": [
        {
            "id": o.id,
            "leader_order_id": o.leader_order_id,
            "symbol": o.symbol,
            "side": o.side,
            "amount_usd": float(o.amount_usd),
            "actual_price": float(o.actual_price),
            "pnl": float(o.pnl),
            "share_paid": float(o.share_paid),
            "status": o.status,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
        for o in rows
    ]})


# ========== 6. 服务层：触发跟单（被 Celery 定时任务调用）==========
async def execute_follow_copy(
    db: AsyncSession,
    subscription: FollowSubscription,
    leader_order_id: str,
    symbol: str,
    side: int,
    expected_price: float,
    actual_price: float,
) -> FollowOrder:
    """执行一笔跟单：写入 FollowOrder + 累计订阅统计"""
    pnl = (actual_price - expected_price) * (subscription.follow_amount_usd / max(expected_price, 0.0001))
    if side == 2:  # 卖出
        pnl = -pnl
    share_paid = max(pnl, 0) * float(subscription.profit_share_ratio) if subscription.mode in (2, 3) else 0.0

    order = FollowOrder(
        subscription_id=subscription.id,
        leader_order_id=leader_order_id,
        symbol=symbol,
        side=side,
        amount_usd=subscription.follow_amount_usd,
        expected_price=expected_price,
        actual_price=actual_price,
        pnl=pnl,
        share_paid=share_paid,
        status=3,
    )
    db.add(order)
    subscription.total_followed += 1
    subscription.total_pnl = float(subscription.total_pnl) + pnl
    subscription.total_share_paid = float(subscription.total_share_paid) + share_paid
    return order
