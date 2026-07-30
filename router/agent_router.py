# 智能体路由：V1.0 多智能体策略-订单执行规则（接入 Hermes MoA + Voting + ExecutionGateway）
import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fwsort.agents.hermes_moa import build_hermes_moa
from fwsort.database import get_async_db
from fwsort.exceptions import NotFoundError, ParamError, RiskControlError
from fwsort.gateway.gateway import ExecutionResult, get_gateway
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


# ========== 请求模型 ==========
class UpdateAccountReq(BaseModel):
    """更新执行账户（JSON body）"""
    name: str | None = None
    target_url: str | None = None
    order_amount_usd: float | None = None
    public_enabled: bool | None = None
    status: int | None = None

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

        # WP-09：双重保障写入 ES
        # 1) outbox：同事务入库，进程崩溃也能恢复
        # 2) fire-and-forget：健康情况下立即投递，outbox flush 时会跳过 status=1 的事件
        from fwsort.execution.es_writer import schedule_index_order_log
        from fwsort.execution.outbox import build_order_log_event

        try:
            db.add(build_order_log_event(log))
            await db.flush()
        except Exception as outbox_err:  # noqa: BLE001
            # outbox 入库失败不应阻塞主流程
            from loguru import logger as _lg

            _lg.warning(f"[outbox] enqueue failed: {outbox_err}")

        es_doc_id = log.id
        schedule_index_order_log(
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
    """当前用户的所有执行账户（1对N；WP-05 过滤已软删）"""
    rows = (
        await db.execute(
            select(ExecutionAccount)
            .where(ExecutionAccount.owner_id == user.id)
            .where(ExecutionAccount.deleted_at.is_(None))
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
            "target_url": a.target_url,
            "target_symbol": a.target_symbol,
            "order_amount_usd": float(a.order_amount_usd),
            "signal": a.signal,
            "signal_source": a.signal_source,
            "signal_updated_at": a.signal_updated_at.isoformat() if a.signal_updated_at else None,
            "last_order_at": a.last_order_at.isoformat() if a.last_order_at else None,
            "public_enabled": a.public_enabled,
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
    target_url: str | None = None,
    order_amount_usd: float = 50.0,
    public_enabled: bool = True,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """创建执行账户（每个用户可创建 N 个）"""
    import uuid

    from fwsort.signals.parser import parse_target_url

    if platform not in ("polymarket", "okx"):
        raise ParamError("platform must be polymarket or okx")
    if initial_balance <= 0:
        raise ParamError("initial_balance must > 0")
    if order_amount_usd < 1 or order_amount_usd > 10000:
        raise ParamError("order_amount_usd must in 1..10000")
    if target_url and len(target_url) > 512:
        raise ParamError("target_url too long (max 512)")
    if target_url and not target_url.startswith("https://"):
        raise ParamError("target_url must start with https://")
    acc = ExecutionAccount(
        uid=f"ACC-{uuid.uuid4().hex[:12].upper()}",
        owner_id=user.id,
        name=name,
        platform=platform,
        account_type=0,  # 默认模拟盘
        initial_balance=initial_balance,
        current_balance=initial_balance,
        target_url=target_url,
        target_symbol=parse_target_url(target_url) if target_url else None,
        order_amount_usd=order_amount_usd,
        public_enabled=public_enabled,
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
            "target_url": acc.target_url,
            "order_amount_usd": float(acc.order_amount_usd),
            "public_enabled": acc.public_enabled,
        },
        message="account created",
    )


# ========== 接口：更新执行账户（编辑标的/金额/启停/公开）==========
@router.put("/accounts/{account_id}", response_model=dict)
async def update_account(
    account_id: int,
    req: UpdateAccountReq,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """更新执行账户（仅 owner 可改）
    - 支持 JSON body 局部更新
    - target_url 修改时自动解析 target_symbol
    """
    from fwsort.signals.parser import parse_target_url

    acc = (
        await db.execute(
            select(ExecutionAccount).where(
                ExecutionAccount.id == account_id,
                ExecutionAccount.owner_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if not acc:
        raise NotFoundError("account not found or not owned by you")
    if req.name is not None:
        if not req.name.strip():
            raise ParamError("name cannot be empty")
        acc.name = req.name.strip()
    if req.target_url is not None:
        if req.target_url and not req.target_url.startswith("https://"):
            raise ParamError("target_url must start with https://")
        if req.target_url and len(req.target_url) > 512:
            raise ParamError("target_url too long (max 512)")
        acc.target_url = req.target_url or None
        # 同时解析 target_symbol
        if req.target_url:
            parsed = parse_target_url(req.target_url)
            acc.target_symbol = parsed
        else:
            acc.target_symbol = None
    if req.order_amount_usd is not None:
        if req.order_amount_usd < 1 or req.order_amount_usd > 10000:
            raise ParamError("order_amount_usd must in 1..10000")
        acc.order_amount_usd = req.order_amount_usd
    if req.public_enabled is not None:
        acc.public_enabled = bool(req.public_enabled)
    if req.status is not None:
        if req.status not in (0, 1, 2):
            raise ParamError("status must be 0/1/2")
        acc.status = req.status
    await db.flush()
    return success(
        {
            "id": acc.id,
            "uid": acc.uid,
            "name": acc.name,
            "target_url": acc.target_url,
            "target_symbol": acc.target_symbol,
            "order_amount_usd": float(acc.order_amount_usd),
            "public_enabled": acc.public_enabled,
            "status": acc.status,
        },
        message="account updated",
    )


# ========== 接口：刷新信号（生成一次信号，可选 source）==========
@router.post("/accounts/{account_id}/signal/refresh", response_model=dict)
async def refresh_account_signal(
    account_id: int,
    source: str = "random",
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """立即生成一次信号（写入账户.signal 字段）"""
    from datetime import datetime

    from fwsort.signals.generator import generate_signal

    if source not in ("random", "gpt-4o", "claude", "gemini", "moa"):
        raise ParamError("source must be random/gpt-4o/claude/gemini/moa")
    acc = (
        await db.execute(
            select(ExecutionAccount).where(
                ExecutionAccount.id == account_id,
                ExecutionAccount.owner_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if not acc:
        raise NotFoundError("account not found or not owned by you")
    sig = generate_signal(source=source)
    acc.signal = sig
    acc.signal_source = source
    acc.signal_updated_at = datetime.now()
    await db.flush()
    return success(
        {
            "id": acc.id,
            "uid": acc.uid,
            "signal": acc.signal,
            "signal_source": acc.signal_source,
            "signal_updated_at": acc.signal_updated_at.isoformat(),
        },
        message="signal refreshed",
    )


# ========== 接口：执行账户删除 ==========
@router.delete("/accounts/{account_id}", response_model=dict)
async def delete_account(
    account_id: int,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """软删除执行账户（WP-05）：保留历史订单/投票/绩效；仅标记 deleted_at"""
    from datetime import datetime, timezone

    acc = (
        await db.execute(
            select(ExecutionAccount).where(
                ExecutionAccount.id == account_id,
                ExecutionAccount.owner_id == user.id,
                ExecutionAccount.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if not acc:
        raise NotFoundError("execution account not found or not owned by you")

    # 软删除：写 deleted_at，关闭订阅与跟单绑定
    acc.deleted_at = datetime.now(tz=timezone.utc)
    acc.status = 2  # 2=已停用（前端列表不显示但保留数据）
    await db.flush()
    return success(
        message="account soft-deleted (history preserved)",
        data={"id": account_id, "deleted_at": acc.deleted_at.isoformat()},
    )


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


# ========== 接口：任务状态查询（前端 /accounts/tasks 用）==========
@router.get("/tasks", response_model=dict)
async def list_task_status(
    _user: User = Depends(current_user),
) -> dict:
    """列出所有 Celery 定时任务最近一次执行状态（last_run_at / status / result）"""
    from fwsort.scheduler import get_all_task_status

    items = get_all_task_status()
    return success(data={"count": len(items), "tasks": items})


# ========== 接口：手动触发某个任务（演示/调试用）==========
@router.post("/tasks/{task_name}/trigger", response_model=dict)
async def trigger_task(
    task_name: str,
    user: User = Depends(current_user),
) -> dict:
    """手动触发一个 Celery 任务（异步 .delay()，不阻塞 API）
    仅允许触发：refresh_account_signals / auto_predict_vote_trade / follow_auto_copy / flush_outbox
    """
    from fwsort.scheduler import celery_app

    allowed = {
        "refresh_account_signals",
        "auto_predict_vote_trade",
        "follow_auto_copy",
        "flush_outbox",
    }
    if task_name not in allowed:
        raise ParamError(f"task {task_name} not allowed to trigger manually")
    # WP-09 / WP-07：USE_FAKE_REDIS 时 Celery broker 不可用 → 降级为本地同步执行
    # 复用 admin_router 同样的兜底逻辑，保证演示模式可手动触发
    from fwsort.config import settings as _settings

    if _settings.USE_FAKE_REDIS:
        try:
            from fwsort.scheduler import (
                follow_auto_copy,
                refresh_account_signals,
                flush_outbox,
            )

            local_tasks = {
                "refresh_account_signals": refresh_account_signals,
                "follow_auto_copy": follow_auto_copy,
                "flush_outbox": flush_outbox,
            }
            if task_name in local_tasks:
                result_value = local_tasks[task_name].apply().get(timeout=10)
                return success(
                    {"task": task_name, "task_id": f"local-{task_name}", "result": result_value, "mode": "sync-fallback"},
                    message="task executed synchronously (Celery broker unavailable in dev mode)",
                )
        except Exception as e:  # noqa: BLE001
            return success(
                {"task": task_name, "task_id": f"local-{task_name}", "error": str(e), "mode": "sync-fallback-failed"},
                message="task sync fallback failed",
            )
    try:
        async_result = celery_app.send_task(f"fwsort.scheduler.{task_name}")
    except Exception as e:  # noqa: BLE001
        raise ParamError(f"send task failed: {e}")
    return success(
        {"task": task_name, "task_id": async_result.id, "triggered_by": user.id},
        message="task triggered",
    )
