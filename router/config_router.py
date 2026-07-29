# 配置管理路由：config_router（榜单权重/黑名单/段位配置）
import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fwsort.database import get_async_db
from fwsort.exceptions import PermissionError_
from fwsort.models import User, WeightConfig
from fwsort.response import success
from fwsort.schemas import WeightConfigReq, WeightConfigResp
from router.auth_router import current_user

router = APIRouter()


# ========== 管理员校验 ==========
async def require_admin(user: User = Depends(current_user)) -> User:
    """仅管理员可访问"""
    if user.role < 3:
        raise PermissionError_("admin required")
    return user


# ========== WP-08：权重重算后台任务 ==========
def _refresh_zset_task(rank_type: int) -> None:
    """WP-08：同步执行权重重算（独立 sync session 避免与 async 冲突）
    失败只记日志，不影响主接口返回
    """
    try:
        from fwsort.database import get_sync_db
        from fwsort.ranking_engine import refresh_redis_zset

        with get_sync_db() as db:
            result = refresh_redis_zset(db, rank_type=rank_type)
        logger.bind(action="refresh_redis_zset", rank_type=rank_type, result=result).info(
            "WP-08: rankings refreshed after weight update"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"WP-08 refresh_redis_zset (rank_type={rank_type}) failed: {type(e).__name__}: {e}")


async def _refresh_zset_async(rank_type: int) -> None:
    """WP-08：异步触发权重重算（在线程池中执行同步阻塞操作）"""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _refresh_zset_task, rank_type)


# ========== 接口：获取权重 ==========
@router.get("/weights", response_model=dict)
async def get_weights(
    rank_type: int = 1,
    db: AsyncSession = Depends(get_async_db),
    _user: User = Depends(current_user),
) -> dict:
    """获取榜单权重配置（默认日榜）"""
    cfg = (
        await db.execute(select(WeightConfig).where(WeightConfig.rank_type == rank_type))
    ).scalar_one_or_none()
    if not cfg:
        # 默认权重（与 .env 一致）
        cfg = WeightConfig(
            rank_type=rank_type,
            weight_annualized=0.30,
            weight_drawdown=0.20,
            weight_sharpe=0.20,
            weight_profit_loss=0.15,
            weight_execution=0.15,
        )
        db.add(cfg)
        await db.flush()
    return success(
        WeightConfigResp(
            rank_type=cfg.rank_type,
            weight_annualized=float(cfg.weight_annualized),
            weight_drawdown=float(cfg.weight_drawdown),
            weight_sharpe=float(cfg.weight_sharpe),
            weight_profit_loss=float(cfg.weight_profit_loss),
            weight_execution=float(cfg.weight_execution),
        ).model_dump()
    )


# ========== 接口：更新权重（仅管理员）==========
@router.put("/weights", response_model=dict)
async def update_weights(
    rank_type: int,
    req: WeightConfigReq,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_async_db),
    _admin: User = Depends(require_admin),
) -> dict:
    """更新榜单权重（仅管理员；WP-08 提交后异步触发榜单重算）"""
    total = (
        req.weight_annualized
        + req.weight_drawdown
        + req.weight_sharpe
        + req.weight_profit_loss
        + req.weight_execution
    )
    if abs(total - 1.0) > 0.001:
        from fwsort.exceptions import ParamError

        raise ParamError(f"weights must sum to 1.0, got {total}")

    cfg = (
        await db.execute(select(WeightConfig).where(WeightConfig.rank_type == rank_type))
    ).scalar_one_or_none()
    if not cfg:
        cfg = WeightConfig(rank_type=rank_type)
        db.add(cfg)
    cfg.weight_annualized = req.weight_annualized
    cfg.weight_drawdown = req.weight_drawdown
    cfg.weight_sharpe = req.weight_sharpe
    cfg.weight_profit_loss = req.weight_profit_loss
    cfg.weight_execution = req.weight_execution
    await db.flush()

    # WP-08：commit 后由后台任务重算 StrategyPerformance.composite_score + 写 Redis ZSet
    # 使用 BackgroundTasks 而非 asyncio.create_task：确保数据库 commit 已完成
    background_tasks.add_task(_refresh_zset_task, rank_type)
    logger.bind(action="update_weights", rank_type=rank_type).info("weights updated, scheduling refresh")

    return success(message="weights updated, ranking will refresh in background")
