# 榜单路由：ranking_router（架构文档 4.4.1）
import base64
import json
import random
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import and_, asc, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from fwsort.database import get_async_db
from fwsort.exceptions import NotFoundError
from fwsort.redis_client import RankType, rank_key
from fwsort.response import success
from fwsort.schemas import RankItem, RankListResp
from fwsort.models import ExecutionAccount, StrategyPerformance, User
from router.auth_router import current_user

router = APIRouter()


def _is_demo_request(request: Request) -> bool:
    """检测请求是否来自 /api/demo/* 演示通道（仅演示模式使用 Mock 数据）"""
    return request.url.path.startswith("/api/demo/")


# WP-10：keyset 分页游标编解码（base64 包装 JSON 避免泄露排序字段细节）
def _encode_cursor(score: float, identifier: int | str) -> str:
    """把 (score, id/uid) 编码成 base64 cursor"""
    payload = json.dumps({"s": float(score), "i": str(identifier)}, ensure_ascii=False)
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str | None) -> tuple[float, str] | None:
    """解码 cursor；失败返回 None（兼容旧 offset 分页）"""
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        d = json.loads(raw)
        return (float(d.get("s", 0)), str(d.get("i", "")))
    except Exception:  # noqa: BLE001
        return None


# ========== MOCK 数据生成器（无数据时使用，README 第15条）==========
# 使用缓存确保同一次服务器生命周期内 Mock 数据固定，排序效果可感知
_MOCK_ACCOUNTS_CACHE: list[dict] | None = None
_MOCK_GLOBAL_USERS_CACHE: list[dict] | None = None


def _mock_accounts() -> list[dict]:
    """无真实数据时返回 MOCK 执行账户列表"""
    global _MOCK_ACCOUNTS_CACHE
    if _MOCK_ACCOUNTS_CACHE is not None:
        return [dict(m) for m in _MOCK_ACCOUNTS_CACHE]
    names = ["Alpha猎手", "Beta套利", "Gamma网格", "Delta波段", "Epsilon趋势", "Zeta对冲"]
    platforms = ["polymarket", "okx"]
    _MOCK_ACCOUNTS_CACHE = [
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
    return [dict(m) for m in _MOCK_ACCOUNTS_CACHE]


def _mock_global_users() -> list[dict]:
    """总榜单 Mock 数据（用户级）"""
    global _MOCK_GLOBAL_USERS_CACHE
    if _MOCK_GLOBAL_USERS_CACHE is not None:
        return [dict(m) for m in _MOCK_GLOBAL_USERS_CACHE]
    names = ["量化王", "趋势猎手", "波段达人", "套利大师", "网格战士", "对冲先锋", "Alpha牛人", "Beta玩家"]
    platforms = ["polymarket", "okx"]
    _MOCK_GLOBAL_USERS_CACHE = [
        {
            "rank": i + 1,
            "uid": f"USER-{i+1:04d}",
            "user_name": names[i % len(names)],
            "platform": platforms[i % 2],
            "avg_return": round(random.uniform(-0.2, 1.5), 4),
            "total_capital": round(random.uniform(500, 50000), 2),
            "avg_score": round(random.uniform(20, 95), 2),
            "account_count": random.randint(1, 5),
            "trade_count": random.randint(50, 3000),
        }
        for i in range(20)
    ]
    return [dict(m) for m in _MOCK_GLOBAL_USERS_CACHE]


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


# ========== 接口：总榜单（用户级，无需登录）==========
@router.get("/global", response_model=dict)
async def global_ranking(
    request: Request,
    platform: str | None = Query(default=None, description="polymarket/okx"),
    sort_by: str = Query(default="composite", description="composite/return/capital"),
    sort_dir: str = Query(default="desc", description="asc/desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    """总榜单：按用户+平台聚合，返回用户级别排名（无需登录）"""
    items: list[dict] = []

    # 尝试从数据库查询真实数据
    stmt = (
        select(
            User.id.label("user_id"),
            User.nickname.label("user_name"),
            ExecutionAccount.platform,
            func.avg(StrategyPerformance.annualized_return).label("avg_return"),
            func.sum(ExecutionAccount.current_balance).label("total_capital"),
            func.avg(StrategyPerformance.composite_score).label("avg_score"),
            func.count(ExecutionAccount.id).label("account_count"),
            func.sum(StrategyPerformance.trade_count).label("trade_count"),
        )
        .join(ExecutionAccount, ExecutionAccount.owner_id == User.id)
        .join(StrategyPerformance, StrategyPerformance.account_id == ExecutionAccount.id)
        .where(StrategyPerformance.period_type == 4)
        .where(ExecutionAccount.deleted_at.is_(None))
        .group_by(User.id, ExecutionAccount.platform)
    )
    if platform:
        stmt = stmt.where(ExecutionAccount.platform == platform)

    # 排序（支持升序/降序）
    order_fn = asc if sort_dir == "asc" else desc
    if sort_by == "return":
        stmt = stmt.order_by(order_fn(func.avg(StrategyPerformance.annualized_return)))
    elif sort_by == "capital":
        stmt = stmt.order_by(order_fn(func.sum(ExecutionAccount.current_balance)))
    elif sort_by == "trades":
        stmt = stmt.order_by(order_fn(func.sum(StrategyPerformance.trade_count)))
    else:
        stmt = stmt.order_by(order_fn(func.avg(StrategyPerformance.composite_score)))

    result = await db.execute(stmt)
    rows = result.all()

    if rows:
        total = len(rows)
        start = (page - 1) * page_size
        end = start + page_size
        for idx, row in enumerate(rows[start:end], start=start + 1):
            score = float(row.avg_score) if row.avg_score else 0.0
            items.append(
                {
                    "rank": idx,
                    "uid": f"USER-{row.user_id:04d}",
                    "user_id": row.user_id,
                    "user_name": row.user_name,
                    "platform": row.platform,
                    "avg_return": float(row.avg_return) if row.avg_return else 0.0,
                    "total_capital": float(row.total_capital) if row.total_capital else 0.0,
                    "avg_score": score,
                    "account_count": row.account_count,
                    "trade_count": row.trade_count or 0,
                    "tier": _tier(score),
                }
            )
    else:
        # 仅演示模式回退 Mock 数据，真实环境返回空
        if _is_demo_request(request):
            mocks = _mock_global_users()
            if platform:
                mocks = [m for m in mocks if m["platform"] == platform]
            reverse = sort_dir != "asc"
            if sort_by == "return":
                mocks.sort(key=lambda x: x["avg_return"], reverse=reverse)
            elif sort_by == "capital":
                mocks.sort(key=lambda x: x["total_capital"], reverse=reverse)
            elif sort_by == "trades":
                mocks.sort(key=lambda x: x.get("trade_count", 0), reverse=reverse)
            else:
                mocks.sort(key=lambda x: x["avg_score"], reverse=reverse)

            total = len(mocks)
            start = (page - 1) * page_size
            end = start + page_size
            page_items = mocks[start:end]
            items = [
                {
                    **m,
                    "rank": start + i + 1,
                    "tier": _tier(m["avg_score"]),
                }
                for i, m in enumerate(page_items)
            ]
        else:
            total = 0

    return success(
        data={
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


# ========== 接口：我的榜单（需登录）==========
@router.get("/my", response_model=dict)
async def my_ranking(
    rank_type: str = Query(default="all_time"),
    platform: str | None = Query(default=None),
    sort_by: str = Query(default="composite"),
    sort_dir: str = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """我的榜单：仅返回当前用户的执行账户排名（需登录）"""
    # Step 1: 获取用户所有账户（不含 JOIN，确保无绩效数据的账户也能返回）
    acc_stmt = (
        select(ExecutionAccount)
        .where(ExecutionAccount.owner_id == user.id)
        .where(ExecutionAccount.deleted_at.is_(None))
    )
    if platform:
        acc_stmt = acc_stmt.where(ExecutionAccount.platform == platform)
    acc_rows = (await db.execute(acc_stmt)).scalars().all()

    # Step 2: 批量加载绩效数据
    account_ids = [a.id for a in acc_rows]
    perf_map: dict[int, StrategyPerformance] = {}
    if account_ids:
        perfs = (
            await db.execute(
                select(StrategyPerformance)
                .where(StrategyPerformance.account_id.in_(account_ids))
                .where(StrategyPerformance.period_type == 4)
            )
        ).scalars().all()
        perf_map = {p.account_id: p for p in perfs}

    # Step 3: 组装数据 + 内存排序
    items: list[dict] = []
    for acc in acc_rows:
        perf = perf_map.get(acc.id)
        if perf:
            items.append({
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
                "total_return": float(perf.total_return),
                "volatility": float(perf.volatility),
                "max_consecutive_loss": perf.max_consecutive_loss,
                "tier": _tier(float(perf.composite_score)),
            })
        else:
            items.append({
                "uid": acc.uid,
                "name": acc.name,
                "platform": acc.platform,
                "composite_score": 0.0,
                "annualized_return": 0.0,
                "max_drawdown": 0.0,
                "calmar_ratio": 0.0,
                "sharpe_ratio": 0.0,
                "win_rate": 0.0,
                "trade_count": 0,
                "execution_score": 0.0,
                "total_return": 0.0,
                "volatility": 0.0,
                "max_consecutive_loss": 0,
                "tier": _tier(0),
            })

    # 排序（支持升序/降序）
    reverse = sort_dir != "asc"
    if sort_by == "return":
        items.sort(key=lambda x: x["annualized_return"], reverse=reverse)
    elif sort_by == "drawdown":
        items.sort(key=lambda x: x["max_drawdown"], reverse=reverse)
    elif sort_by == "sharpe":
        items.sort(key=lambda x: x["sharpe_ratio"], reverse=reverse)
    elif sort_by == "trades":
        items.sort(key=lambda x: x["trade_count"], reverse=reverse)
    elif sort_by == "execution":
        items.sort(key=lambda x: x["execution_score"], reverse=reverse)
    else:
        items.sort(key=lambda x: x["composite_score"], reverse=reverse)

    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    paged_items = items[start:end]
    for idx, item in enumerate(paged_items, start=start + 1):
        item["rank"] = idx

    return success(
        data={
            "rank_type": rank_type,
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "user_id": user.id,
            "user_name": user.nickname,
        }
    )


# ========== 接口：榜单列表 ==========
@router.get("/list", response_model=dict)
async def list_ranking(
    request: Request,
    rank_type: str = Query(default=RankType.REALTIME, description="榜单类型"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    platform: str | None = Query(default=None, description="polymarket/okx"),
    sort_by: str = Query(default="composite", description="composite/return/drawdown/execution"),
    cursor: str | None = Query(default=None, description="WP-10：keyset 分页游标"),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    """获取榜单列表（分页+筛选），未上榜时回退 MOCK
    WP-10：支持 keyset 分页（cursor）替代 offset，深翻页不再线性变慢
    """
    from fwsort.redis_client import async_redis

    key = rank_key(rank_type)
    total = await async_redis.zcard(key)
    items: list[dict] = []
    next_cursor: str | None = None
    use_keyset = bool(cursor)

    if use_keyset:
        # WP-10：Redis ZSet keyset 分页（ZREVRANGEBYSCORE + LIMIT）
        cur = _decode_cursor(cursor)
        if cur is None:
            return success(data={"error": "invalid cursor", "items": [], "total": total})
        max_score = cur[0]  # 不包含
        # 拉取比 max_score 小的下一页
        rows = await async_redis.zrevrangebyscore(
            key,
            max=max_score,
            min="-inf",
            start=0,
            num=page_size,
            withscores=True,
        )
        if rows:
            uid_list = [uid for uid, _ in rows]
            uid_score_map = {uid: float(score) for uid, score in rows}
            stmt = select(ExecutionAccount, StrategyPerformance).join(
                StrategyPerformance, StrategyPerformance.account_id == ExecutionAccount.id
            ).where(ExecutionAccount.uid.in_(uid_list)).where(ExecutionAccount.deleted_at.is_(None))
            if platform:
                stmt = stmt.where(ExecutionAccount.platform == platform)
            db_rows = (await db.execute(stmt)).all()
            perf_map = {acc.uid: (acc, perf) for acc, perf in db_rows}
            for idx, uid in enumerate(uid_list, start=1):
                score = uid_score_map.get(uid, 0.0)
                if uid in perf_map:
                    acc, perf = perf_map[uid]
                    items.append(
                        {
                            "rank": idx,
                            "uid": uid,
                            "name": acc.name,
                            "platform": acc.platform,
                            "composite_score": score,
                            "annualized_return": float(perf.annualized_return),
                            "max_drawdown": float(perf.max_drawdown),
                            "calmar_ratio": float(perf.calmar_ratio),
                            "sharpe_ratio": float(perf.sharpe_ratio),
                            "win_rate": float(perf.win_rate),
                            "trade_count": perf.trade_count,
                            "execution_score": float(perf.execution_score),
                            "total_return": float(perf.total_return),
                            "volatility": float(perf.volatility),
                            "max_consecutive_loss": perf.max_consecutive_loss,
                        }
                    )
                else:
                    items.append(
                        {
                            "rank": idx,
                            "uid": uid,
                            "name": uid,
                            "platform": "",
                            "composite_score": score,
                            "annualized_return": 0.0,
                            "max_drawdown": 0.0,
                            "calmar_ratio": 0.0,
                            "sharpe_ratio": 0.0,
                            "win_rate": 0.0,
                            "trade_count": 0,
                            "execution_score": 0.0,
                            "total_return": 0.0,
                            "volatility": 0.0,
                            "max_consecutive_loss": 0,
                        }
                    )
            # 生成下一页游标
            last_uid = uid_list[-1]
            last_score = uid_score_map[last_uid]
            next_cursor = _encode_cursor(last_score, last_uid)
        # keyset 模式下不再用 total（数量大时不准）
        total = None
    else:
        # 兼容老 offset 分页
        start = (page - 1) * page_size
        end = start + page_size - 1
        if total and total > 0:
            rows = await async_redis.zrevrange(key, start, end, withscores=True)
            uid_list = [uid for uid, _ in rows]
            uid_score_map = {uid: float(score) for uid, score in rows}

            if uid_list:
                stmt = select(ExecutionAccount, StrategyPerformance).join(
                    StrategyPerformance, StrategyPerformance.account_id == ExecutionAccount.id
                ).where(ExecutionAccount.uid.in_(uid_list)).where(ExecutionAccount.deleted_at.is_(None))
                if platform:
                    stmt = stmt.where(ExecutionAccount.platform == platform)
                result = await db.execute(stmt)
                db_rows = result.all()

                perf_map = {}
                for acc, perf in db_rows:
                    perf_map[acc.uid] = (acc, perf)

                for idx, uid in enumerate(uid_list, start=start + 1):
                    if uid in perf_map:
                        acc, perf = perf_map[uid]
                        items.append(
                            {
                                "rank": idx,
                                "uid": uid,
                                "name": acc.name,
                                "platform": acc.platform,
                                "composite_score": uid_score_map.get(uid, float(perf.composite_score)),
                                "annualized_return": float(perf.annualized_return),
                                "max_drawdown": float(perf.max_drawdown),
                                "calmar_ratio": float(perf.calmar_ratio),
                                "sharpe_ratio": float(perf.sharpe_ratio),
                                "win_rate": float(perf.win_rate),
                                "trade_count": perf.trade_count,
                                "execution_score": float(perf.execution_score),
                                "total_return": float(perf.total_return),
                                "volatility": float(perf.volatility),
                                "max_consecutive_loss": perf.max_consecutive_loss,
                            }
                        )
                    else:
                        items.append(
                            {
                                "rank": idx,
                                "uid": uid,
                                "name": uid,
                                "platform": "",
                                "composite_score": uid_score_map.get(uid, 0.0),
                                "annualized_return": 0.0,
                                "max_drawdown": 0.0,
                                "calmar_ratio": 0.0,
                                "sharpe_ratio": 0.0,
                                "win_rate": 0.0,
                                "trade_count": 0,
                                "execution_score": 0.0,
                                "total_return": 0.0,
                                "volatility": 0.0,
                                "max_consecutive_loss": 0,
                            }
                        )
        else:
            # 回退到数据库
            stmt = select(ExecutionAccount, StrategyPerformance).join(
                StrategyPerformance, StrategyPerformance.account_id == ExecutionAccount.id
            ).where(ExecutionAccount.deleted_at.is_(None))
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
                            "total_return": float(perf.total_return),
                            "volatility": float(perf.volatility),
                            "max_consecutive_loss": perf.max_consecutive_loss,
                        }
                    )

        # 仅演示模式回退 Mock 数据，真实环境返回空
        if not items and _is_demo_request(request):
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
        elif not items:
            total = total or 0

    # 填充段位
    rank_items = []
    for it in items:
        it["tier"] = _tier(it.get("composite_score", 0))
        rank_items.append(it)

    payload = RankListResp(
        rank_type=rank_type,
        items=[RankItem(**{k: v for k, v in r.items() if k in RankItem.model_fields}) for r in rank_items],
        total=total,
        page=page,
        page_size=page_size,
    ).model_dump()
    if next_cursor:
        payload["next_cursor"] = next_cursor
    return success(data=payload)


# ========== 接口：策略详情 ==========
@router.get("/detail/{uid}", response_model=dict)
async def ranking_detail(request: Request, uid: str, db: AsyncSession = Depends(get_async_db)) -> dict:
    """单个执行账户或用户的榜单详情
    - uid 支持两种格式：账户级 (ACC-DEMO1000) / 用户级 (USER-0001)
    - 用户级：聚合该用户所有账户的绩效
    """
    is_demo = _is_demo_request(request)

    # 用户级（USER-XXXX）→ 聚合查询
    if uid.startswith("USER-"):
        try:
            user_id = int(uid[5:])
        except ValueError:
            raise NotFoundError(f"uid {uid} not found")

        # 查用户
        user_stmt = select(User).where(User.id == user_id)
        user_row = (await db.execute(user_stmt)).first()
        if not user_row:
            # 仅演示模式回退 MOCK
            if is_demo:
                mocks = _mock_global_users()
                for m in mocks:
                    if m["uid"] == uid:
                        return success(data={
                            **m,
                            "rank_history": [
                                {"date": (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d"), "rank": random.randint(1, 20)}
                                for i in range(30)
                            ],
                        })
            raise NotFoundError(f"uid {uid} not found")
        user = user_row[0]

        # 聚合账户+绩效
        stmt = (
            select(
                ExecutionAccount.platform,
                func.avg(StrategyPerformance.annualized_return).label("avg_return"),
                func.sum(ExecutionAccount.current_balance).label("total_capital"),
                func.avg(StrategyPerformance.composite_score).label("avg_score"),
                func.count(ExecutionAccount.id).label("account_count"),
                func.sum(StrategyPerformance.trade_count).label("trade_count"),
                func.avg(StrategyPerformance.max_drawdown).label("avg_drawdown"),
                func.avg(StrategyPerformance.sharpe_ratio).label("avg_sharpe"),
                func.avg(StrategyPerformance.profit_loss_ratio).label("avg_plr"),
                func.avg(StrategyPerformance.execution_score).label("avg_execution"),
            )
            .join(StrategyPerformance, StrategyPerformance.account_id == ExecutionAccount.id)
            .where(ExecutionAccount.owner_id == user_id)
            .where(ExecutionAccount.deleted_at.is_(None))
            .where(StrategyPerformance.period_type == 4)
            .group_by(ExecutionAccount.platform)
        )
        result = await db.execute(stmt)
        rows = result.all()
        if not rows:
            raise NotFoundError(f"uid {uid} has no performance data")

        # 取第一行（榜单数据通常只展示一个平台代表）
        row = rows[0]
        score = float(row.avg_score) if row.avg_score else 0.0
        return success(
            data={
                "uid": uid,
                "user_id": user_id,
                "user_name": user.nickname,
                "name": user.nickname,
                "platform": row.platform,
                "tier": _tier(score),
                "annualized_return": float(row.avg_return) if row.avg_return else 0.0,
                "max_drawdown": float(row.avg_drawdown) if row.avg_drawdown else 0.0,
                "calmar_ratio": round(float(row.avg_return) / max(float(row.avg_drawdown) or 0.01, 0.01), 4),
                "sharpe_ratio": float(row.avg_sharpe) if row.avg_sharpe else 0.0,
                "win_rate": 0.0,
                "trade_count": int(row.trade_count or 0),
                "execution_score": float(row.avg_execution) if row.avg_execution else 0.0,
                "composite_score": score,
                "current_balance": float(row.total_capital) if row.total_capital else 0.0,
                "total_return": float(row.avg_return) if row.avg_return else 0.0,
                "volatility": 0.0,
                "max_consecutive_loss": 0,
                "account_count": int(row.account_count),
                "rank_history": [
                    {"date": (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d"), "rank": random.randint(1, 20)}
                    for i in range(30)
                ],
            }
        )

    # 账户级（ACC-XXXX / MOCK-XXXX）
    # 先单独查账户（不依赖绩效表），确保无绩效数据时也能返回基本信息
    acc_stmt = (
        select(ExecutionAccount)
        .where(ExecutionAccount.uid == uid)
        .where(ExecutionAccount.deleted_at.is_(None))
    )
    acc_result = await db.execute(acc_stmt)
    acc = acc_result.scalar_one_or_none()

    if not acc:
        # 仅演示模式回退 MOCK
        if is_demo:
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

    # 查绩效（可能不存在，不存在则返回默认值）
    perf_stmt = (
        select(StrategyPerformance)
        .where(StrategyPerformance.account_id == acc.id)
        .where(StrategyPerformance.period_type == 4)
    )
    perf_result = await db.execute(perf_stmt)
    perf = perf_result.scalar_one_or_none()

    if perf:
        tier_score = float(perf.composite_score)
        return success(
            data={
                "uid": acc.uid,
                "name": acc.name,
                "platform": acc.platform,
                "tier": _tier(tier_score),
                "annualized_return": float(perf.annualized_return),
                "max_drawdown": float(perf.max_drawdown),
                "calmar_ratio": float(perf.calmar_ratio),
                "sharpe_ratio": float(perf.sharpe_ratio),
                "win_rate": float(perf.win_rate),
                "trade_count": perf.trade_count,
                "execution_score": float(perf.execution_score),
                "composite_score": tier_score,
                "current_balance": float(acc.current_balance),
                "total_return": float(perf.total_return),
                "volatility": float(perf.volatility),
                "max_consecutive_loss": perf.max_consecutive_loss,
            }
        )
    else:
        # 无绩效数据时返回账户基本信息 + 默认值
        return success(
            data={
                "uid": acc.uid,
                "name": acc.name,
                "platform": acc.platform,
                "tier": _tier(0),
                "annualized_return": 0.0,
                "max_drawdown": 0.0,
                "calmar_ratio": 0.0,
                "sharpe_ratio": 0.0,
                "win_rate": 0.0,
                "trade_count": 0,
                "execution_score": 0.0,
                "composite_score": 0.0,
                "current_balance": float(acc.current_balance),
                "total_return": 0.0,
                "volatility": 0.0,
                "max_consecutive_loss": 0,
                "note": "该账户暂无绩效数据，请先执行交易以生成排名。",
            }
        )


# ========== 接口：榜单历史快照 ==========
@router.get("/history", response_model=dict)
async def ranking_history(
    request: Request,
    rank_type: str = Query(default="daily"),
    days: int = Query(default=30, ge=1, le=365),
) -> dict:
    """历史榜单快照（用于回溯分析）"""
    # 仅演示模式生成 Mock 快照数据
    if _is_demo_request(request):
        snapshots = [
            {
                "date": (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d"),
                "top1": _mock_accounts()[0]["name"],
                "top1_score": round(random.uniform(70, 95), 2),
            }
            for i in range(days)
        ]
    else:
        snapshots = []
    return success(data={"rank_type": rank_type, "snapshots": snapshots})


# ========== 接口：榜单变动 ==========
@router.get("/change/{uid}", response_model=dict)
async def ranking_change(request: Request, uid: str, days: int = Query(default=7, ge=1, le=90)) -> dict:
    """单个账户排名变动趋势"""
    # 仅演示模式生成 Mock 历史数据
    if _is_demo_request(request):
        history = [
            {
                "date": (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d"),
                "rank": random.randint(1, 20),
                "score": round(random.uniform(20, 95), 2),
            }
            for i in range(days)
        ]
        history.reverse()
    else:
        history = []
    return success(data={"uid": uid, "history": history})


# ========== 接口：CSV 导出（架构文档 8.3）==========
@router.get("/export", response_model=dict)
async def export_ranking(
    request: Request,
    rank_type: str = Query(default="daily"),
) -> dict:
    """导出榜单 CSV（实际项目中生成文件流）"""
    # 仅演示模式使用 Mock 数据
    if _is_demo_request(request):
        rows = _mock_accounts()
    else:
        rows = []
    return success(
        data={
            "rank_type": rank_type,
            "rows": rows,
            "export_url": f"/static/exports/{rank_type}_{datetime.now().strftime('%Y%m%d')}.csv",
        }
    )


# ========== 接口：策略榜单列表 ==========
@router.get("/strategy/list", response_model=dict)
async def strategy_ranking_list(
    request: Request,
    rank_type: str = Query(default="realtime", description="榜单类型: realtime"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    platform: str | None = Query(default=None, description="polymarket/okx"),
) -> dict:
    """获取策略榜单列表（基于 AutoStrategy 维度）"""
    from fwsort.strategy.strategy_ranking import get_strategy_ranking

    offset = (page - 1) * page_size
    result = get_strategy_ranking(
        rank_type=rank_type,
        limit=page_size,
        offset=offset,
    )

    # 按平台筛选（如果指定）
    if platform and result["items"]:
        result["items"] = [
            item for item in result["items"]
            if item.get("platform") == platform
        ]
        result["total"] = len(result["items"])

    return success(data=result)


# ========== 接口：策略详情 ==========
@router.get("/strategy/detail/{strategy_id}", response_model=dict)
async def strategy_ranking_detail(
    request: Request,
    strategy_id: int,
) -> dict:
    """获取单个策略的榜单详情"""
    from fwsort.strategy.strategy_ranking import get_strategy_detail

    detail = get_strategy_detail(strategy_id)
    if detail is None:
        raise NotFoundError(f"策略 {strategy_id} 不存在")

    return success(data=detail)


# ========== 接口：刷新策略榜单（含结算回查）==========
@router.post("/strategy/refresh", response_model=dict)
async def refresh_strategy_ranking() -> dict:
    """手动触发策略榜单刷新：先结算回查未结算交易，再重算绩效，最后刷新榜单"""
    from fwsort.database import get_sync_db
    from fwsort.models import AutoStrategy, AutoStrategyLog
    from fwsort.strategy.service import _check_and_update_previous_pnl
    from fwsort.strategy.settlement_service import batch_sync_after_resolution
    from fwsort.strategy.strategy_ranking import refresh_strategy_redis_zset
    from datetime import datetime

    settlement_result = {"checked": 0, "updated": 0, "failed": 0, "skipped": 0}
    performance_result = {"updated": 0, "failed": 0}
    ranking_result = {"updated": 0, "failed": 0}

    with get_sync_db() as db:
        active_tasks = (
            db.query(AutoStrategy)
            .filter(
                AutoStrategy.is_active == True,
                AutoStrategy.deleted_at.is_(None),
            )
            .all()
        )

        for task in active_tasks:
            settlement_result["checked"] += 1
            try:
                unresolved_count = (
                    db.query(AutoStrategyLog)
                    .filter(
                        AutoStrategyLog.task_id == task.id,
                        AutoStrategyLog.log_type == 0,
                        AutoStrategyLog.status.in_([0, 2]),
                        AutoStrategyLog.market_resolved == False,
                    )
                    .count()
                )

                task_updated = 0
                if unresolved_count > 0 and task.gateway == "polymarket_f3":
                    updated = _check_and_update_previous_pnl(db, task)
                    task_updated = len(updated)
                    settlement_result["updated"] += task_updated

                if task_updated > 0:
                    perf = batch_sync_after_resolution(db, task)
                    if isinstance(perf, dict):
                        ranking_count = perf.get("ranking", {}).get("strategy_ranking", {}).get("updated", 0)
                        performance_result["updated"] += 1
                    else:
                        performance_result["failed"] += 1
                else:
                    settlement_result["skipped"] += 1

            except Exception as e:
                settlement_result["failed"] += 1
                import logging
                logging.getLogger(__name__).warning(f"[refresh] 任务 {task.id} 刷新失败: {e},traceback={traceback.format_exc()}")

    ranking_result = refresh_strategy_redis_zset()

    last_update = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    return success(data={
        "settlement": settlement_result,
        "performance": performance_result,
        "ranking": ranking_result,
        "last_update": last_update,
    })


# ========== 接口：策略榜单对比 ==========
@router.get("/strategy/compare", response_model=dict)
async def compare_strategies(
    request: Request,
    strategy_ids: str = Query(description="逗号分隔的策略ID列表, e.g. 1,2,3"),
) -> dict:
    """对比多个策略的绩效指标"""
    from fwsort.strategy.strategy_ranking import get_strategy_detail

    ids = [int(sid.strip()) for sid in strategy_ids.split(",") if sid.strip().isdigit()]
    if len(ids) > 10:
        ids = ids[:10]  # 最多对比10个

    comparisons = []
    for sid in ids:
        detail = get_strategy_detail(sid)
        if detail:
            comparisons.append(detail)

    if not comparisons:
        return success(data={"error": "未找到有效的策略", "comparisons": []})

    # 计算排名
    comparisons.sort(key=lambda x: x.get("composite_score", 0), reverse=True)
    for idx, comp in enumerate(comparisons, start=1):
        comp["rank"] = idx

    return success(data={
        "count": len(comparisons),
        "comparisons": comparisons,
    })
