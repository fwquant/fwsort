# 智能体租用路由：双轨计费（按次试算 + 包时段独占）
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fwsort.agents.hermes_moa import build_hermes_moa
from fwsort.database import get_async_db
from fwsort.exceptions import NotFoundError, ParamError
from fwsort.models import AgentPrediction, RentalAgent, RentalOrder, User
from fwsort.response import success
from router.auth_router import current_user

router = APIRouter()
_moa = build_hermes_moa()


# ========== 1. 智能体清单（公开）==========
@router.get("/agents", response_model=dict)
async def list_agents(db: AsyncSession = Depends(get_async_db)) -> dict:
    """所有可租用的智能体清单（公开接口）"""
    rows = (
        await db.execute(select(RentalAgent).where(RentalAgent.is_active == True).order_by(RentalAgent.id))  # noqa: E712
    ).scalars().all()
    return success(data={"count": len(rows), "agents": [
        {
            "id": a.id,
            "name": a.name,
            "model": a.model,
            "description": a.description,
            "agent_type": a.agent_type,
            "price_per_call": float(a.price_per_call_usd),
            "price_per_hour": float(a.price_per_hour_usd),
        }
        for a in rows
    ]})


# ========== 2. 按次调用（试算）==========
@router.post("/call", response_model=dict)
async def rent_by_call(
    agent_id: int,
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """按次调用智能体：扣一次费用，返回该智能体的预测结果"""
    agent = (await db.execute(select(RentalAgent).where(RentalAgent.id == agent_id))).scalar_one_or_none()
    if not agent or not agent.is_active:
        raise NotFoundError("agent not found or inactive")

    # 查找有效订单（按次）
    valid = (
        await db.execute(
            select(RentalOrder).where(
                RentalOrder.renter_id == user.id,
                RentalOrder.agent_id == agent_id,
                RentalOrder.rental_type == 1,
                RentalOrder.status == 1,
            )
        )
    ).scalars().all()
    used = sum(v.used_calls for v in valid)
    if not valid:
        # 没有订单则现场开一笔
        order = RentalOrder(
            renter_id=user.id,
            agent_id=agent_id,
            rental_type=1,
            used_calls=0,
            total_paid_usd=float(agent.price_per_call_usd),
            status=1,
            started_at=datetime.now(),
            expires_at=datetime.now() + timedelta(hours=24),
        )
        db.add(order)
        await db.flush()
        valid = [order]

    # 调一个具体智能体（从 MoA 中匹配 model）
    target = next((p for p in _moa.layer1_agents if agent.model in p.agent_model or p.agent_model.startswith(agent.model.split("-")[0])), None)
    if target is None:
        raise NotFoundError(f"no live agent matched for model {agent.model}")

    res = await target.predict(symbol, timeframe)
    valid[0].used_calls += 1
    # 落库预测
    ap = AgentPrediction(
        agent_name=target.agent_name,
        agent_model=target.agent_model,
        symbol=symbol,
        timeframe=timeframe,
        direction=res.direction,
        confidence=res.confidence,
        reasoning=res.reasoning,
        raw_payload=res.raw_payload,
        latency_ms=res.latency_ms,
    )
    db.add(ap)
    await db.flush()

    return success(
        {
            "agent": {"id": agent.id, "name": agent.name, "model": agent.model},
            "order_id": valid[0].id,
            "remaining_calls": 1,  # 按次模式：单次调用
            "direction": res.direction,
            "confidence": res.confidence,
            "reasoning": res.reasoning,
            "latency_ms": res.latency_ms,
            "prediction_id": ap.id,
        },
        message="call success",
    )


# ========== 3. 包时段租用 ==========
@router.post("/rent", response_model=dict)
async def rent_by_package(
    agent_id: int,
    hours: int = 24,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """包时段：购买 N 小时独占调用权（折扣：1d=20h, 7d=120h）"""
    if hours not in (1, 24, 168):
        raise ParamError("hours must be 1, 24 or 168")
    agent = (await db.execute(select(RentalAgent).where(RentalAgent.id == agent_id))).scalar_one_or_none()
    if not agent or not agent.is_active:
        raise NotFoundError("agent not found or inactive")

    # 折扣
    if hours == 24:
        pay = agent.price_per_hour_usd * 20
    elif hours == 168:
        pay = agent.price_per_hour_usd * 120
    else:
        pay = agent.price_per_hour_usd * hours

    order = RentalOrder(
        renter_id=user.id,
        agent_id=agent_id,
        rental_type=2,
        hours=hours,
        used_calls=0,
        total_paid_usd=pay,
        status=1,
        started_at=datetime.now(),
        expires_at=datetime.now() + timedelta(hours=hours),
    )
    db.add(order)
    await db.flush()
    return success(
        {
            "id": order.id,
            "agent_id": agent_id,
            "agent_name": agent.name,
            "rental_type": 2,
            "hours": hours,
            "total_paid_usd": float(pay),
            "expires_at": order.expires_at.isoformat(),
        },
        message="rented",
    )


# ========== 4. 我的租用 ==========
@router.get("/my", response_model=dict)
async def my_rentals(
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """当前用户的所有租用记录"""
    rows = (
        await db.execute(
            select(RentalOrder, RentalAgent)
            .join(RentalAgent, RentalAgent.id == RentalOrder.agent_id)
            .where(RentalOrder.renter_id == user.id)
            .order_by(RentalOrder.created_at.desc())
        )
    ).all()
    return success(data={"count": len(rows), "rentals": [
        {
            "id": o.id,
            "agent_id": o.agent_id,
            "agent_name": a.name,
            "rental_type": "per_call" if o.rental_type == 1 else "package",
            "hours": o.hours,
            "used_calls": o.used_calls,
            "total_paid_usd": float(o.total_paid_usd),
            "status": o.status,
            "started_at": o.started_at.isoformat() if o.started_at else None,
            "expires_at": o.expires_at.isoformat() if o.expires_at else None,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
        for o, a in rows
    ]})


# ========== 5. 取消/结束租用 ==========
@router.post("/{order_id}/cancel", response_model=dict)
async def cancel_rental(
    order_id: int,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """主动结束租用（仅包时段可取消，按次不退）"""
    order = (
        await db.execute(
            select(RentalOrder).where(
                RentalOrder.id == order_id,
                RentalOrder.renter_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if not order:
        raise NotFoundError("rental order not found")
    if order.rental_type == 1:
        raise ParamError("per_call order cannot cancel")
    order.status = 0
    await db.flush()
    return success(message="cancelled")
