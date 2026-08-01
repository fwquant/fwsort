# 系统管理路由：初始化、播种 MOCK、Celery 触发（仅管理员）
from datetime import datetime, timedelta
import random

from fastapi import APIRouter, Depends
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fwsort.agents.hermes_moa import build_hermes_moa
from fwsort.config import settings
from fwsort.database import get_async_db, init_db
from fwsort.models import (
    AgentPrediction,
    ExecutionAccount,
    StrategyPerformance,
    User,
    VoteDecision,
)
from fwsort.ranking_engine import composite_score
from fwsort.response import success
from fwsort.security import hash_password
from fwsort.gateway.simulator_gateway import SimulatorGateway
from router.auth_router import current_user, current_user_optional

router = APIRouter()

_moa = build_hermes_moa()
_simulator = SimulatorGateway()


async def require_admin(user: User = Depends(current_user)) -> User:
    if user.role < 3:
        from fwsort.exceptions import PermissionError_

        raise PermissionError_("admin required")
    return user


async def _has_any_admin(db: AsyncSession) -> bool:
    """检查是否已经存在管理员（首次启动判定）"""
    row = (
        await db.execute(select(func.count(User.id)).where(User.role == 3))
    ).scalar_one()
    return (row or 0) > 0


async def _bootstrap_or_admin(db: AsyncSession, user: User | None) -> None:
    """WP-04：首次启动放行 / 已有 admin 必须鉴权
    - 若无 admin 且 APP_ALLOW_INIT=True → 放行（首次部署引导）
    - 否则 → 必须有 admin token
    """
    if settings.APP_ALLOW_INIT and not await _has_any_admin(db):
        logger.warning("⚠️  bootstrap mode: init endpoint open until first admin is created")
        return
    if not user or user.role < 3:
        from fwsort.exceptions import AuthError, PermissionError_

        if not user:
            raise AuthError("admin token required for init endpoints")
        raise PermissionError_("admin role required")


# ========== 1. 初始化数据库表 ==========
@router.post("/init-db", response_model=dict)
async def init_database(
    db: AsyncSession = Depends(get_async_db),
    user: User | None = Depends(current_user_optional),
) -> dict:
    """初始化所有表结构（首次启动时调用；WP-04 仅 admin/首次启动放行）"""
    await _bootstrap_or_admin(db, user)
    init_db()
    logger.bind(action="init_db").info("database tables initialized")
    return success(message="database tables initialized")


# ========== 2. 播种管理员账户 ==========
@router.post("/seed-admin", response_model=dict)
async def seed_admin(
    email: str = "admin@fwquant.com",
    password: str = "admin123456",
    nickname: str = "管理员",
    db: AsyncSession = Depends(get_async_db),
    user: User | None = Depends(current_user_optional),
) -> dict:
    """创建初始管理员账户（仅当无 admin 时生效；WP-04）"""
    await _bootstrap_or_admin(db, user)
    exists = (await db.execute(select(User).where(User.role == 3))).scalar_one_or_none()
    if exists:
        return success(message="admin already exists", data={"user_id": exists.id})
    admin = User(
        email=email,
        password_hash=hash_password(password),
        nickname=nickname,
        role=3,
        status=0,
    )
    db.add(admin)
    await db.flush()
    logger.bind(action="seed_admin", user_id=admin.id).warning("admin account created (bootstrap)")
    return success({"user_id": admin.id, "email": email, "role": 3}, message="admin created")


# ========== 3. 播种 MOCK 数据（执行账户+绩效+投票+订单）==========
@router.post("/seed-mock", response_model=dict)
async def seed_mock(
    n_accounts: int = 20,
    n_votes: int = 50,
    db: AsyncSession = Depends(get_async_db),
    user: User | None = Depends(current_user_optional),
) -> dict:
    """播种：N 个执行账户 + 50 笔模拟投票+订单 + 综合分（WP-04 鉴权）"""
    await _bootstrap_or_admin(db, user)
    # 找一个用户作为所有者（没有就用 admin 邮箱匹配）
    owner = (await db.execute(select(User).where(User.role == 3))).scalar_one_or_none()
    if not owner:
        return success(message="please seed-admin first")
    import uuid

    # 创建执行账户
    names = ["Alpha猎手", "Beta套利", "Gamma网格", "Delta波段", "Epsilon趋势", "Zeta对冲", "Eta高频", "Theta事件"]
    platforms = ["polymarket", "okx"]
    accounts: list[ExecutionAccount] = []
    for i in range(n_accounts):
        acc = ExecutionAccount(
            uid=f"ACC-{uuid.uuid4().hex[:10].upper()}",
            owner_id=owner.id,
            name=f"{names[i % len(names)]}-{i+1:02d}",
            platform=platforms[i % 2],
            account_type=0,
            initial_balance=1000.0,
            current_balance=1000.0 + random.uniform(-50, 300),
            daily_pnl=random.uniform(-100, 100),
        )
        db.add(acc)
        accounts.append(acc)
    await db.flush()

    # 创建绩效
    for acc in accounts:
        ann = round(random.uniform(-0.1, 1.2), 4)
        dd = round(random.uniform(0.02, 0.3), 4)
        sharpe = round(random.uniform(0.3, 3.0), 2)
        plr = round(random.uniform(0.8, 3.0), 2)
        ex = round(random.uniform(0.6, 0.95), 4)
        score = composite_score(ann, dd, sharpe, plr, ex)
        db.add(
            StrategyPerformance(
                account_id=acc.id,
                uid=acc.uid,
                period_type=4,  # 总榜
                start_time=datetime.now() - timedelta(days=30),
                end_time=datetime.now(),
                annualized_return=ann,
                max_drawdown=dd,
                sharpe_ratio=sharpe,
                sortino_ratio=sharpe * 1.2,
                calmar_ratio=ann / max(dd, 0.01),
                profit_factor=plr,
                win_rate=round(random.uniform(0.45, 0.75), 4),
                profit_loss_ratio=plr,
                trade_count=random.randint(100, 1500),
                execution_rate=round(random.uniform(0.85, 0.99), 4),
                avg_slippage=round(random.uniform(0.0001, 0.005), 6),
                avg_latency=random.randint(150, 800),
                cancel_rate=round(random.uniform(0.01, 0.15), 4),
                execution_score=ex,
                composite_score=score,
            )
        )
    await db.flush()

    # 创建投票+订单（用模拟器）
    from fwsort.models import OrderExecutionLog
    from fwsort.voting import vote

    for _ in range(n_votes):
        acc = random.choice(accounts)
        # 3 智能体伪预测
        directions = [random.choice([1, 1, 1, 2, 0]) for _ in range(3)]
        # 落库预测
        preds = []
        for d in directions:
            ap = AgentPrediction(
                agent_name=random.choice(["GPT-4o", "Claude", "Gemini"]),
                agent_model=random.choice([settings.OPENAI_MODEL, settings.ANTHROPIC_MODEL, settings.GEMINI_MODEL]),
                symbol="BTCUSDT",
                timeframe="15m",
                direction=d,
                confidence=round(random.uniform(0.55, 0.95), 4),
                reasoning="[MOCK seed] 模拟历史预测",
                raw_payload='{"mock": true}',
                latency_ms=random.randint(100, 600),
            )
            db.add(ap)
            preds.append(ap)
        await db.flush()
        # 投票
        v = vote(
            directions=directions,
            account_balance=float(acc.current_balance),
            daily_pnl=float(acc.daily_pnl),
            initial_balance=float(acc.initial_balance),
        )
        v_row = VoteDecision(
            account_id=acc.id,
            symbol="BTCUSDT",
            timeframe="15m",
            up_count=v.up_count,
            down_count=v.down_count,
            flat_count=v.flat_count,
            final_direction=v.final_direction,
            order_amount_usd=v.order_amount_usd,
            order_amount_reason=v.reason,
            prediction_ids=",".join(str(p.id) for p in preds),
        )
        db.add(v_row)
        await db.flush()
        # 模拟下单
        if v.final_direction != 0 and v.order_amount_usd > 0:
            sim = await _simulator.submit(
                platform=acc.platform, symbol="BTCUSDT",
                side=v.final_direction, amount_usd=v.order_amount_usd,
            )
            db.add(
                OrderExecutionLog(
                    uid=acc.uid, account_id=acc.id, vote_id=v_row.id,
                    order_id=sim.order_id, order_type=2, side=sim.side,
                    platform=sim.platform, symbol=sim.symbol,
                    expected_price=sim.expected_price, actual_price=sim.actual_price,
                    quantity=sim.quantity, amount_usd=sim.amount_usd,
                    status=sim.status, latency_ms=sim.latency_ms,
                    slippage=sim.slippage, pnl=random.uniform(-5, 15),
                )
            )
    await db.flush()

    return success(
        {"accounts": len(accounts), "votes": n_votes},
        message="mock data seeded",
    )


# ========== 4. 触发 Celery 任务（手动）==========
@router.post("/trigger/{task_name}", response_model=dict)
async def trigger_task(task_name: str, _admin: User = Depends(require_admin)) -> dict:
    """手动触发 Celery 任务（仅管理员）"""
    from fwsort.scheduler import (
        archive_hot_to_cold,
        daily_cleanup,
        daily_snapshot,
        flush_outbox,  # WP-09：outbox 消费任务
        follow_auto_copy,  # WP-07：跟单自动执行任务（修复 F-3 路径缺失）
        refresh_realtime_rank,
    )

    tasks = {
        "refresh_realtime_rank": refresh_realtime_rank,
        "daily_snapshot": daily_snapshot,
        "daily_cleanup": daily_cleanup,
        "archive_hot_to_cold": archive_hot_to_cold,
        "follow_auto_copy": follow_auto_copy,  # WP-07：管理员可手动触发跟单自动同步
        "flush_outbox": flush_outbox,  # WP-09：手动触发 outbox 消费（无需等 30s）
    }
    if task_name not in tasks:
        from fwsort.exceptions import ParamError

        raise ParamError(f"unknown task: {task_name}, options: {list(tasks.keys())}")

    # WP-07：USE_FAKE_REDIS 时 Celery broker 不可用 → 降级为本地同步执行
    # 生产环境 (USE_FAKE_REDIS=false) 走标准 Celery .delay() 异步队列
    if settings.USE_FAKE_REDIS:
        try:
            result_value = tasks[task_name].apply().get(timeout=10)
            return success(
                {"task_id": f"local-{task_name}", "task": task_name, "result": result_value, "mode": "sync-fallback"},
                message="task executed synchronously (Celery broker unavailable in dev mode)",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"sync fallback for {task_name} failed: {e}")
            return success(
                {"task_id": f"local-{task_name}", "task": task_name, "error": str(e), "mode": "sync-fallback-failed"},
                message="task sync fallback failed",
            )

    result = tasks[task_name].delay()
    return success({"task_id": result.id, "task": task_name})


# ========== 5. 播种租用品类（智能体）==========
@router.post("/seed-rental-agents", response_model=dict)
async def seed_rental_agents(
    db: AsyncSession = Depends(get_async_db),
    _admin: User = Depends(require_admin),
) -> dict:
    """播种：6 个可租用的智能体品类（按次 + 包时段双轨）"""
    from fwsort.models import RentalAgent

    catalog = [
        {"name": "GPT-4o 趋势猎手", "model": "gpt-4o", "agent_type": "trend",
         "description": "基于 GPT-4o 的多周期趋势识别智能体，适合 BTC/ETH 主线行情。",
         "price_per_call_usd": 0.10, "price_per_hour_usd": 0.50, "max_concurrent": 20},
        {"name": "Claude 3.5 风控官", "model": "claude-3-5-sonnet", "agent_type": "risk",
         "description": "Claude 3.5 Sonnet 风控智能体：识别极端行情、提示减仓。",
         "price_per_call_usd": 0.12, "price_per_hour_usd": 0.60, "max_concurrent": 15},
        {"name": "Gemini 2.0 链上分析师", "model": "gemini-2.0-flash", "agent_type": "onchain",
         "description": "Gemini 2.0 链上数据分析师，专注资金流向与持仓变化。",
         "price_per_call_usd": 0.08, "price_per_hour_usd": 0.40, "max_concurrent": 25},
        {"name": "GPT-4o 通用分析师", "model": "gpt-4o-mini", "agent_type": "general",
         "description": "GPT-4o-mini 通用分析智能体，价格亲民，适合大批量试算。",
         "price_per_call_usd": 0.03, "price_per_hour_usd": 0.20, "max_concurrent": 50},
        {"name": "Claude 情绪解读", "model": "claude-3-5-haiku", "agent_type": "sentiment",
         "description": "Claude 3.5 Haiku 情绪解读智能体，分析新闻/社媒情绪。",
         "price_per_call_usd": 0.05, "price_per_hour_usd": 0.30, "max_concurrent": 30},
        {"name": "Gemini 多模态信号", "model": "gemini-2.0-flash", "agent_type": "general",
         "description": "Gemini 2.0 Flash 多模态信号智能体，融合 K 线 + 文本。",
         "price_per_call_usd": 0.08, "price_per_hour_usd": 0.40, "max_concurrent": 25},
    ]
    added = 0
    for c in catalog:
        exists = (await db.execute(
            select(RentalAgent).where(RentalAgent.name == c["name"])
        )).scalar_one_or_none()
        if exists:
            continue
        db.add(RentalAgent(**c, is_active=True))
        added += 1
    await db.flush()
    return success({"added": added, "total": len(catalog)}, message="rental agents seeded")