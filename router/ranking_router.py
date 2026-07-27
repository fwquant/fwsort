# 榜单路由：ranking_router（架构文档 4.4.1）
import random
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fwsort.database import get_async_db
from fwsort.exceptions import NotFoundError
from fwsort.redis_client import RankType, rank_key
from fwsort.response import success
from fwsort.schemas import RankItem, RankListResp
from fwsort.models import ExecutionAccount, StrategyPerformance

router = APIRouter()


# ========== MOCK 数据生成器（无数据时使用，README 第15条）==========
def _mock_accounts() -> list[dict]:
    """无真实数据时返回 MOCK 执行账户列表"""
    names = ["Alpha猎手", "Beta套利", "Gamma网格", "Delta波段", "Epsilon趋势", "Zeta对冲"]
    platforms = ["polymarket", "okx"]
    return [
        {
            "uid": f"MOCK-{i:04d}",
            "name": names[i % len(names)],
            "platform": platforms[i % 2],
            "annualized_return": round(random.uniform(-0.2, 1.5), 4),
            "max_drawdown": round(random.uniform(0.02, 0.35), 4),
            "calmar_ratio": round(random.uniform(0.5, 4.0), 2),
            "sharpe_ratio": round(random.uniform(0.3, 3.0), 2),
            "win_rate": round(random.uniform(0.45, 0.75), 4),
            "trade_count": random.randint(120, 1500),
            "execution_score": round(random.uniform(0.6, 0.95), 4),
            "composite_score": round(random.uniform(20, 95), 2),
        }
        for i in range(20)
    ]


def _tier(score: float) -> str:
    """段位判定（架构文档 6.1）"""
    if score >= 80:
        return "钻石"
    if score >= 60:
        return "铂金"
    if score >= 40:
        return "黄金"
    if score >= 20:
        return "白银"
    return "青铜"


# ========== 接口：榜单列表 ==========
@router.get("/list", response_model=dict)
async def list_ranking(
    rank_type: str = Query(default=RankType.REALTIME, description="榜单类型"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    platform: str | None = Query(default=None, description="polymarket/okx"),
    sort_by: str = Query(default="composite", description="composite/return/drawdown/execution"),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    """获取榜单列表（分页+筛选），未上榜时回退 MOCK"""
    # 尝试从 Redis ZSet 读取
    from fwsort.redis_client import async_redis

    key = rank_key(rank_type)
    total = await async_redis.zcard(key)
    items: list[dict] = []

    start = (page - 1) * page_size
    end = start + page_size - 1
    if total and total > 0:
        start = (page - 1) * page_size
        end = start + page_size - 1
        # ZREVRANGE 按分数倒序
        rows = await async_redis.zrevrange(key, start, end, withscores=True)
        for idx, (uid, score) in enumerate(rows, start=start + 1):
            items.append(
                {
                    "rank": idx,
                    "uid": uid,
                    "composite_score": float(score),
                    # 简化：榜单中只展示分数，详细指标走详情接口
                }
            )
    else:
        # 回退到数据库
        stmt = select(ExecutionAccount, StrategyPerformance).join(
            StrategyPerformance, StrategyPerformance.account_id == ExecutionAccount.id
        )
        if platform:
            stmt = stmt.where(ExecutionAccount.platform == platform)
        stmt = stmt.order_by(StrategyPerformance.composite_score.desc())
        result = await db.execute(stmt)
        rows = result.all()
        if rows:
            total = len(rows)
            for idx, (acc, perf) in enumerate(rows[start:end], start=start + 1):
                items.append(
                    {
                        "rank": idx,
                        "uid": acc.uid,
                        "name": acc.name,
                        "platform": acc.platform,
                        "composite_score": float(perf.composite_score),
                        "annualized_return": float(perf.annualized_return),
                        "max_drawdown": float(perf.max_drawdown),
                        "calmar_ratio": float(perf.calmar_ratio),
                        "sharpe_ratio": float(perf.sharpe_ratio),
                        "win_rate": float(perf.win_rate),
                        "trade_count": perf.trade_count,
                        "execution_score": float(perf.execution_score),
                    }
                )

    # 无数据 → MOCK
    if not items:
        mocks = _mock_accounts()
        if platform:
            mocks = [m for m in mocks if m["platform"] == platform]
        if sort_by == "return":
            mocks.sort(key=lambda x: x["annualized_return"], reverse=True)
        elif sort_by == "drawdown":
            mocks.sort(key=lambda x: x["max_drawdown"])
        elif sort_by == "execution":
            mocks.sort(key=lambda x: x["execution_score"], reverse=True)
        else:
            mocks.sort(key=lambda x: x["composite_score"], reverse=True)

        start = (page - 1) * page_size
        end = start + page_size
        page_items = mocks[start:end]
        items = [{"rank": start + i + 1, **m} for i, m in enumerate(page_items)]
        total = len(mocks)

    # 填充段位
    rank_items = []
    for it in items:
        it["tier"] = _tier(it.get("composite_score", 0))
        rank_items.append(it)

    return success(
        data=RankListResp(
            rank_type=rank_type,
            items=[RankItem(**{k: v for k, v in r.items() if k in RankItem.model_fields}) for r in rank_items],
            total=total,
            page=page,
            page_size=page_size,
        ).model_dump()
    )


# ========== 接口：策略详情 ==========
@router.get("/detail/{uid}", response_model=dict)
async def ranking_detail(uid: str, db: AsyncSession = Depends(get_async_db)) -> dict:
    """单个执行账户的榜单详情"""
    stmt = (
        select(ExecutionAccount, StrategyPerformance)
        .join(StrategyPerformance, StrategyPerformance.account_id == ExecutionAccount.id)
        .where(ExecutionAccount.uid == uid)
    )
    result = await db.execute(stmt)
    row = result.first()
    if not row:
        # MOCK 详情
        mocks = _mock_accounts()
        for m in mocks:
            if m["uid"] == uid:
                return success(
                    data={
                        "uid": m["uid"],
                        "name": m["name"],
                        "platform": m["platform"],
                        "tier": _tier(m["composite_score"]),
                        **m,
                        "rank_history": [
                            {"date": (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d"), "rank": random.randint(1, 20)}
                            for i in range(30)
                        ],
                    }
                )
        raise NotFoundError(f"uid {uid} not found")

    acc, perf = row
    return success(
        data={
            "uid": acc.uid,
            "name": acc.name,
            "platform": acc.platform,
            "tier": _tier(float(perf.composite_score)),
            "annualized_return": float(perf.annualized_return),
            "max_drawdown": float(perf.max_drawdown),
            "calmar_ratio": float(perf.calmar_ratio),
            "sharpe_ratio": float(perf.sharpe_ratio),
            "win_rate": float(perf.win_rate),
            "trade_count": perf.trade_count,
            "execution_score": float(perf.execution_score),
            "composite_score": float(perf.composite_score),
            "current_balance": float(acc.current_balance),
        }
    )


# ========== 接口：榜单历史快照 ==========
@router.get("/history", response_model=dict)
async def ranking_history(
    rank_type: str = Query(default="daily"),
    days: int = Query(default=30, ge=1, le=365),
) -> dict:
    """历史榜单快照（用于回溯分析）"""
    snapshots = [
        {
            "date": (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d"),
            "top1": _mock_accounts()[0]["name"],
            "top1_score": round(random.uniform(70, 95), 2),
        }
        for i in range(days)
    ]
    return success(data={"rank_type": rank_type, "snapshots": snapshots})


# ========== 接口：榜单变动 ==========
@router.get("/change/{uid}", response_model=dict)
async def ranking_change(uid: str, days: int = Query(default=7, ge=1, le=90)) -> dict:
    """单个账户排名变动趋势"""
    history = [
        {
            "date": (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d"),
            "rank": random.randint(1, 20),
            "score": round(random.uniform(20, 95), 2),
        }
        for i in range(days)
    ]
    history.reverse()
    return success(data={"uid": uid, "history": history})


# ========== 接口：CSV 导出（架构文档 8.3）==========
@router.get("/export", response_model=dict)
async def export_ranking(
    rank_type: str = Query(default="daily"),
) -> dict:
    """导出榜单 CSV（实际项目中生成文件流）"""
    return success(
        data={
            "rank_type": rank_type,
            "rows": _mock_accounts(),
            "export_url": f"/static/exports/{rank_type}_{datetime.now().strftime('%Y%m%d')}.csv",
        }
    )
