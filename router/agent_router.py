# 智能体路由：V1.0 多智能体策略-订单执行规则（接入 Hermes MoA + Voting + ExecutionGateway）
import time

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fwsort.agents.hermes_moa import build_hermes_moa
from fwsort.config import settings
from fwsort.database import get_async_db
from fwsort.exceptions import NotFoundError, ParamError, RiskControlError
from fwsort.execution.gateway import ExecutionResult, get_gateway
from fwsort.models import (
    AgentPrediction,
    ExecutionAccount,
    OrderExecutionLog,
    User,
    VoteDecision,
)
from fwsort.response import success
from fwsort.schemas import AgentPredictionItem, AgentPredictionReq, VoteResultResp
from fwsort.voting import vote
from router.auth_router import current_user

router = APIRouter()

# 单例：MoA 聚合器 + 统一执行网关
_moa = build_hermes_moa()
_gateway = get_gateway()


# ========== 接口：触发一轮预测+投票+下单（V1.0 完整闭环）==========
@router.post("/predict-and-vote", response_model=dict)
async def predict_and_vote(
    req: AgentPredictionReq,
    account_id: int,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """V1.0 核心闭环：3 智能体预测（Hermes MoA）→ 投票 → 风控 → 模拟下单"""
    # 1) 校验执行账户
    acc = (
        await db.execute(
            select(ExecutionAccount).where(
                ExecutionAccount.id == account_id,
                ExecutionAccount.owner_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if not acc:
        raise NotFoundError("execution account not found")
    if acc.risk_frozen:
        raise RiskControlError("account is frozen by risk control")
    if acc.status != 0:
        raise ParamError(f"account status={acc.status}, cannot trade")

    # 2) Hermes MoA 聚合
    t0 = time.perf_counter()
    moa_result = await _moa.aggregate(req.symbol, req.timeframe)
    t_predict = (time.perf_counter() - t0) * 1000

    # 3) 落库：每条智能体预测
    db_preds: list[AgentPrediction] = []
    for p in moa_result.layer1_results:
        ap = AgentPrediction(
            agent_name=p.agent_name,
            agent_model=p.agent_model,
            symbol=p.symbol,
            timeframe=p.timeframe,
            direction=p.direction,
            confidence=p.confidence,
            reasoning=p.reasoning,
            raw_payload=p.raw_payload,
            latency_ms=p.latency_ms,
        )
        db.add(ap)
        db_preds.append(ap)
    await db.flush()

    # 4) 投票引擎
    directions = [p.direction for p in moa_result.layer1_results]
    v = vote(
        directions=directions,
        account_balance=float(acc.current_balance),
        daily_pnl=float(acc.daily_pnl),
        initial_balance=float(acc.initial_balance),
    )

    # 若风控冻结，更新账户
    if "risk_freeze" in v.reason:
        acc.risk_frozen = True
        await db.flush()
        raise RiskControlError(v.reason)

    # 5) 落库：投票决策
    vote_row = VoteDecision(
        account_id=acc.id,
        symbol=req.symbol,
        timeframe=req.timeframe,
        up_count=v.up_count,
        down_count=v.down_count,
        flat_count=v.flat_count,
        final_direction=v.final_direction,
        order_amount_usd=v.order_amount_usd,
        order_amount_reason=v.reason,
        prediction_ids=",".join(str(p.id) for p in db_preds),
    )
    db.add(vote_row)
    await db.flush()

    # 6) 下单（V1.0 通过 ExecutionGateway：根据 acc.account_type 自动选模拟/真实）
    order_id: str | None = None
    order_status: int | None = None
    es_doc_id: int | None = None
    if v.final_direction != 0 and v.order_amount_usd > 0:
        result: ExecutionResult = await _gateway.submit(
            account_type=acc.account_type,
            platform=acc.platform,
            symbol=req.symbol,
            side=v.final_direction,
            amount_usd=v.order_amount_usd,
        )
        order_id = result.order_id
        order_status = result.status
        log = OrderExecutionLog(
            uid=acc.uid,
            account_id=acc.id,
            vote_id=vote_row.id,
            order_id=result.order_id or f"FAIL-{int(time.time()*1000)}",
            order_type=2,  # 市价
            side=result.side,
            platform=result.platform,
            symbol=result.symbol,
            expected_price=result.expected_price,
            actual_price=result.actual_price,
            quantity=result.quantity,
            amount_usd=result.amount_usd,
            status=result.status,
            latency_ms=result.latency_ms,
            slippage=result.slippage,
            pnl=0.0,
        )
        db.add(log)
        await db.flush()
        es_doc_id = log.id

        # ES 双写（异步触发，失败不影响主流程）
        from fwsort.execution.es_writer import index_order_log

        await index_order_log(
            order_log_id=log.id,
            uid=acc.uid,
            account_id=acc.id,
            vote_id=vote_row.id,
            order_id=log.order_id,
            order_type=log.order_type,
            side=log.side,
            platform=log.platform,
            symbol=log.symbol,
            expected_price=float(log.expected_price),
            actual_price=float(log.actual_price),
            quantity=float(log.quantity),
            amount_usd=float(log.amount_usd),
            status=log.status,
            latency_ms=log.latency_ms,
            slippage=float(log.slippage),
            created_at=log.created_at,
        )

    # 7) 响应
    return success(
        VoteResultResp(
            vote_id=vote_row.id,
            up_count=v.up_count,
            down_count=v.down_count,
            flat_count=v.flat_count,
            final_direction=v.final_direction,
            order_amount_usd=v.order_amount_usd,
            reason=v.reason,
            predictions=[
                AgentPredictionItem(
                    id=p.id or 0,
                    agent_name=p.agent_name,
                    agent_model=p.agent_model,
                    direction=p.direction,
                    confidence=p.confidence,
                    reasoning=p.reasoning,
                    latency_ms=p.latency_ms,
                    created_at=p.created_at or moa_result.layer1_results[0].created_at,
                )
                for p in db_preds
            ],
            order_id=order_id,
            order_status=order_status,
        ).model_dump(),
        message="vote complete",
    )


# ========== 接口：执行账户列表 ==========
@router.get("/accounts", response_model=dict)
async def list_my_accounts(
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """当前用户的所有执行账户（1对N）"""
    rows = (
        await db.execute(
            select(ExecutionAccount).where(ExecutionAccount.owner_id == user.id)
        )
    ).scalars().all()
    return success(data={"count": len(rows), "accounts": [
        {
            "id": a.id,
            "uid": a.uid,
            "name": a.name,
            "platform": a.platform,
            "account_type": a.account_type,
            "current_balance": float(a.current_balance),
            "daily_pnl": float(a.daily_pnl),
            "risk_frozen": a.risk_frozen,
            "status": a.status,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in rows
    ]})


# ========== 接口：创建执行账户 ==========
@router.post("/accounts", response_model=dict)
async def create_account(
    name: str,
    platform: str,
    initial_balance: float = 1000.0,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """创建执行账户（每个用户可创建 N 个）"""
    import uuid

    if platform not in ("polymarket", "okx"):
        raise ParamError("platform must be polymarket or okx")
    if initial_balance <= 0:
        raise ParamError("initial_balance must > 0")
    acc = ExecutionAccount(
        uid=f"ACC-{uuid.uuid4().hex[:12].upper()}",
        owner_id=user.id,
        name=name,
        platform=platform,
        account_type=0,  # 默认模拟盘
        initial_balance=initial_balance,
        current_balance=initial_balance,
    )
    db.add(acc)
    await db.flush()
    return success(
        {
            "id": acc.id,
            "uid": acc.uid,
            "name": acc.name,
            "platform": acc.platform,
            "current_balance": float(acc.current_balance),
        },
        message="account created",
    )


# ========== 接口：执行账户删除 ==========
@router.delete("/accounts/{account_id}", response_model=dict)
async def delete_account(
    account_id: int,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """删除执行账户（仅本人/管理员；保留历史订单/投票/绩效记录）"""
    from fwsort.models import (
        AgentPrediction,
        OrderExecutionLog,
        StrategyPerformance,
        VoteDecision,
    )

    acc = (
        await db.execute(
            select(ExecutionAccount).where(
                ExecutionAccount.id == account_id,
                ExecutionAccount.owner_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if not acc:
        raise NotFoundError("execution account not found or not owned by you")

    # 先解绑：把相关历史记录的外键置空（保历史/免级联）
    await db.execute(
        AgentPrediction.__table__.update().where(AgentPrediction.account_id == account_id).values(account_id=None)
    ) if False else None  # AgentPrediction 无外键，no-op

    # 删除账户本体；历史记录不级联删除（保留在库中追溯）
    await db.delete(acc)
    await db.flush()
    return success(message="account deleted", data={"id": account_id})


# ========== 接口：执行日志查询 ==========
@router.get("/execution/{uid}", response_model=dict)
async def list_execution_logs(
    uid: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_async_db),
    _user: User = Depends(current_user),
) -> dict:
    """查询某执行账户的订单执行日志（架构文档 5.6）"""
    rows = (
        await db.execute(
            select(OrderExecutionLog)
            .where(OrderExecutionLog.uid == uid)
            .order_by(OrderExecutionLog.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return success(data={"uid": uid, "count": len(rows), "logs": [
        {
            "order_id": r.order_id,
            "platform": r.platform,
            "symbol": r.symbol,
            "side": r.side,
            "amount_usd": float(r.amount_usd),
            "status": r.status,
            "latency_ms": r.latency_ms,
            "slippage": float(r.slippage),
            "pnl": float(r.pnl),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]})


# ========== 接口：订单状态同步（实盘模式：拉取 OKX/Polymarket 最新状态）==========
@router.post("/execution/{uid}/sync", response_model=dict)
async def sync_execution_status(
    uid: str,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """把执行账户的所有未完结订单（status<3）拉取平台最新状态并落库"""
    acc = (
        await db.execute(
            select(ExecutionAccount).where(
                ExecutionAccount.uid == uid,
                ExecutionAccount.owner_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if not acc:
        raise NotFoundError("execution account not found")

    # 取未完结订单
    rows = (
        await db.execute(
            select(OrderExecutionLog)
            .where(
                OrderExecutionLog.uid == uid,
                OrderExecutionLog.status.in_([1, 2]),  # 已提交/部分成交
            )
            .order_by(OrderExecutionLog.created_at.desc())
            .limit(50)
        )
    ).scalars().all()

    if not rows:
        return success(data={"uid": uid, "synced": 0, "details": []})

    # 仅对实盘 OKX 账户做状态同步；模拟盘没有远程状态
    synced: list[dict] = []
    if acc.account_type == 1 and acc.platform == "okx" and _gateway is not None:
        try:
            okx_executor = _gateway._get_okx()  # 访问内部 OKX 客户端
        except Exception:
            okx_executor = None
        if okx_executor and okx_executor.is_ready():
            for r in rows:
                try:
                    inst_id, _ = okx_executor._symbol_to_inst_id(r.symbol)
                    info = await okx_executor.client.get_order(inst_id=inst_id, order_id=r.order_id)
                    payload = (info.get("data") or [{}])[0]
                    if payload:
                        new_state = payload.get("state", "")
                        if new_state == "filled":
                            r.status = 3
                            r.actual_price = float(payload.get("avgPx", r.actual_price))
                            r.quantity = float(payload.get("fillSz", r.quantity))
                        elif new_state == "canceled":
                            r.status = 4
                        elif new_state == "partially_filled":
                            r.status = 2
                            r.quantity = float(payload.get("fillSz", r.quantity))
                        synced.append({"order_id": r.order_id, "new_state": new_state, "status": r.status})
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"sync order {r.order_id} failed: {e}")
                    synced.append({"order_id": r.order_id, "error": str(e)})
            await db.flush()
    else:
        for r in rows:
            synced.append({
                "order_id": r.order_id,
                "status": r.status,
                "note": "simulator mode: no remote sync",
            })

    return success(data={"uid": uid, "synced": len(synced), "details": synced})


# ========== 接口：ES 检索订单日志（高性能筛选）==========
@router.get("/execution/{uid}/es-search", response_model=dict)
async def es_search_logs(
    uid: str,
    platform: str | None = None,
    status: int | None = None,
    size: int = 50,
    _user: User = Depends(current_user),
) -> dict:
    """通过 ES 检索某账户的订单日志（高并发场景用，DB 兜底）"""
    from fwsort.execution.es_writer import search_order_logs

    res = await search_order_logs(uid=uid, platform=platform, status=status, size=size)
    return success(data=res)
