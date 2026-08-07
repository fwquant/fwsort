"""策略榜单引擎：基于 AutoStrategy 维度的榜单评分与 Redis 缓存

职责：
1. 从 AutoStrategy 聚合策略维度的绩效指标
2. 计算策略综合分（福纹综合分）
3. 将策略榜单写入 Redis ZSet 供前端查询
4. 提供策略榜单查询接口

调用时机：
- 定时任务（scheduler.py 每 5 分钟）
- 结算回查后实时触发
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from fwsort.database import get_sync_db
from fwsort.fwlogs import logger
from fwsort.models import (
    AutoStrategy,
    AutoStrategyLog,
    StrategyPerformance,
)
from fwsort.ranking_engine import composite_score
from fwsort.redis_client import RankType, rank_key, sync_redis

# ========== 策略榜单 Redis Key ==========
STRATEGY_RANK_KEY_PREFIX = "fwsort:rank:strategy"


def strategy_rank_key(rank_type: str = RankType.REALTIME) -> str:
    """策略榜单 ZSet key 工厂"""
    return f"{STRATEGY_RANK_KEY_PREFIX}:{rank_type}"


# ========== 策略绩效聚合 ==========
def _aggregate_strategy_performance(db, strategy: AutoStrategy) -> dict | None:
    """聚合单个策略的绩效指标

    Args:
        db: SQLAlchemy 同步会话
        strategy: AutoStrategy ORM 对象

    Returns:
        dict: 绩效指标字典，无数据返回 None
    """
    # 查询所有已结算的执行日志
    logs = (
        db.query(AutoStrategyLog)
        .filter(
            AutoStrategyLog.task_id == strategy.id,
            AutoStrategyLog.log_type == 0,
            AutoStrategyLog.market_resolved == True,
            AutoStrategyLog.pnl_amount != 0,
        )
        .order_by(AutoStrategyLog.executed_at.asc())
        .all()
    )

    if not logs:
        return None

    # 基础统计
    trade_count = len(logs)
    pnl_list = [float(log.pnl_amount) for log in logs]
    wins = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p < 0]

    total_pnl = sum(pnl_list)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = win_count / trade_count if trade_count > 0 else 0.0

    # 盈亏比
    avg_win = sum(wins) / win_count if win_count > 0 else 0.0
    avg_loss = abs(sum(losses) / loss_count) if loss_count > 0 else 0.0
    profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else (999.0 if avg_win > 0 else 0.0)

    # 收益率
    initial_balance = float(strategy.initial_balance or 1000)
    total_return = total_pnl / initial_balance if initial_balance > 0 else 0.0

    # 年化收益率
    if len(logs) >= 2:
        span_seconds = (logs[-1].executed_at - logs[0].executed_at).total_seconds()
        avg_interval = span_seconds / (len(logs) - 1) if span_seconds > 0 else 300
        trades_per_year = (365 * 24 * 3600) / avg_interval if avg_interval > 0 else 0
        annualized_return = total_return * (trades_per_year / max(trade_count, 1)) * 50
    else:
        annualized_return = total_return * 50

    # 最大回撤
    cumulative = []
    running = 0.0
    for p in pnl_list:
        running += p
        cumulative.append(running)

    max_drawdown = 0.0
    peak = cumulative[0] if cumulative else 0.0
    for val in cumulative:
        if val > peak:
            peak = val
        dd = (peak - val) / peak if peak > 0 else 0.0
        if dd > max_drawdown:
            max_drawdown = dd

    # 夏普率
    if len(pnl_list) >= 2:
        mean_pnl = sum(pnl_list) / len(pnl_list)
        variance = sum((p - mean_pnl) ** 2 for p in pnl_list) / (len(pnl_list) - 1)
        volatility = variance ** 0.5
    else:
        volatility = 0.0

    sharpe_ratio = (sum(pnl_list) / len(pnl_list)) / volatility if volatility > 0 else 0.0

    # 最大连续亏损
    max_consecutive_loss = 0
    current_streak = 0
    for p in pnl_list:
        if p < 0:
            current_streak += 1
            if current_streak > max_consecutive_loss:
                max_consecutive_loss = current_streak
        else:
            current_streak = 0

    # 执行分
    success_count = sum(1 for log in logs if log.status in (0, 2))
    execution_rate = success_count / trade_count if trade_count > 0 else 0.0
    execution_score = execution_rate

    # 盈利因子
    total_win = sum(wins)
    total_loss = abs(sum(losses))
    profit_factor = total_win / total_loss if total_loss > 0 else (999.0 if total_win > 0 else 0.0)

    return {
        "strategy_id": strategy.id,
        "strategy_name": strategy.task_name,
        "uid": f"STR-{strategy.id:04d}",
        "account_id": strategy.account_id,
        "trade_count": trade_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "total_pnl": round(total_pnl, 6),
        "win_rate": round(win_rate, 6),
        "profit_loss_ratio": round(profit_loss_ratio, 6),
        "total_return": round(total_return, 6),
        "annualized_return": round(annualized_return, 6),
        "max_drawdown": round(max_drawdown, 6),
        "sharpe_ratio": round(sharpe_ratio, 6),
        "volatility": round(volatility, 6),
        "max_consecutive_loss": max_consecutive_loss,
        "execution_score": round(execution_score, 6),
        "profit_factor": round(profit_factor, 6),
        "composite_score": 0.0,  # 先置 0，后面计算
        "current_balance": float(strategy.current_balance or initial_balance),
        "is_active": strategy.is_active,
        "gateway": strategy.gateway,
    }


# ========== 策略榜单刷新 ==========
def refresh_strategy_redis_zset() -> dict:
    """从 AutoStrategy 聚合绩效到 Redis 策略榜单

    Returns:
        dict: {"updated": N, "failed": N, "strategies": [...]}
    """
    updated = 0
    failed = 0
    strategy_details = []

    with get_sync_db() as db:
        # 获取所有未删除的策略
        strategies = (
            db.query(AutoStrategy)
            .filter(AutoStrategy.deleted_at.is_(None))
            .all()
        )

        logger.info(f"[StrategyRank] 开始刷新策略榜单: 共 {len(strategies)} 个策略")

        # 准备 Redis 数据
        zset_key = strategy_rank_key(RankType.REALTIME)
        rank_mapping: dict[str, float] = {}

        for strategy in strategies:
            try:
                perf = _aggregate_strategy_performance(db, strategy)
                if perf is None:
                    continue

                # 计算综合分
                score = composite_score(
                    annualized=perf["annualized_return"],
                    max_drawdown=perf["max_drawdown"],
                    sharpe=perf["sharpe_ratio"],
                    profit_loss=perf["profit_loss_ratio"],
                    execution_score=perf["execution_score"],
                )
                perf["composite_score"] = round(score, 4)

                # 写入 Redis ZSet（使用策略ID作为标识）
                strategy_uid = f"STR-{strategy.id:04d}"
                rank_mapping[strategy_uid] = score

                strategy_details.append({
                    "strategy_id": strategy.id,
                    "strategy_name": strategy.task_name,
                    "uid": strategy_uid,
                    "score": score,
                    "trade_count": perf["trade_count"],
                    "total_pnl": perf["total_pnl"],
                    "win_rate": perf["win_rate"],
                })

                updated += 1

            except Exception as e:
                failed += 1
                logger.warning(
                    f"[StrategyRank] 策略 {strategy.id} ({strategy.task_name}) 聚合失败: {e},traceback={traceback.format_exc()}"
                )
                continue

        # 批量写入 Redis
        if rank_mapping:
            try:
                # 先清空再写入
                sync_redis.delete(zset_key)
                sync_redis.zadd(zset_key, rank_mapping)
                logger.info(
                    f"[StrategyRank] 策略榜单已写入 Redis: {len(rank_mapping)} 个策略"
                )
            except Exception as e:
                logger.error(f"[StrategyRank] Redis 写入失败: {e},traceback={traceback.format_exc()}")
                failed += len(rank_mapping)
                updated -= len(rank_mapping)

    result = {
        "updated": updated,
        "failed": failed,
        "total_strategies": len(strategies),
        "details": strategy_details[:10],  # 只返回前10条摘要
    }

    logger.info(f"[StrategyRank] 策略榜单刷新完成: {result}")
    return result


# ========== 策略榜单查询 ==========
def get_strategy_ranking(
    rank_type: str = RankType.REALTIME,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """获取策略榜单

    Args:
        rank_type: 榜单类型
        limit: 返回数量
        offset: 偏移量

    Returns:
        dict: {"items": [...], "total": N}
    """
    zset_key = strategy_rank_key(rank_type)

    try:
        total = sync_redis.zcard(zset_key)
        rows = sync_redis.zrevrange(zset_key, offset, offset + limit - 1, withscores=True)
    except Exception as e:
        logger.warning(f"[StrategyRank] Redis 查询失败: {e},traceback={traceback.format_exc()}")
        total = 0
        rows = []

    items = []
    with get_sync_db() as db:
        for idx, (strategy_uid, score) in enumerate(rows, start=offset + 1):
            strategy_id = int(strategy_uid.replace("STR-", ""))

            # 查询策略详情
            strategy = db.query(AutoStrategy).filter(AutoStrategy.id == strategy_id).first()
            if not strategy:
                continue

            # 聚合完整绩效
            perf = _aggregate_strategy_performance(db, strategy)
            if perf is None:
                perf = {
                    "strategy_id": strategy.id,
                    "strategy_name": strategy.task_name,
                    "uid": strategy_uid,
                    "trade_count": 0,
                    "total_pnl": 0,
                    "win_rate": 0,
                    "annualized_return": 0,
                    "max_drawdown": 0,
                    "sharpe_ratio": 0,
                    "composite_score": score,
                }

            items.append({
                "rank": idx,
                "uid": strategy_uid,
                "strategy_id": strategy.id,
                "strategy_name": strategy.task_name,
                "platform": strategy.gateway,
                "composite_score": score,
                "annualized_return": perf.get("annualized_return", 0),
                "max_drawdown": perf.get("max_drawdown", 0),
                "sharpe_ratio": perf.get("sharpe_ratio", 0),
                "win_rate": perf.get("win_rate", 0),
                "trade_count": perf.get("trade_count", 0),
                "total_pnl": perf.get("total_pnl", 0),
                "execution_score": perf.get("execution_score", 0),
                "tier": _strategy_tier(score),
            })

    return {
        "rank_type": rank_type,
        "items": items,
        "total": total,
    }


def _strategy_tier(score: float) -> str:
    """策略段位判定"""
    if score >= 80:
        return "钻石策略"
    if score >= 60:
        return "铂金策略"
    if score >= 40:
        return "黄金策略"
    if score >= 20:
        return "白银策略"
    return "青铜策略"


# ========== 单策略详情查询 ==========
def get_strategy_detail(strategy_id: int) -> dict | None:
    """获取单个策略的榜单详情

    Args:
        strategy_id: 策略 ID

    Returns:
        dict | None: 策略详情
    """
    with get_sync_db() as db:
        strategy = db.query(AutoStrategy).filter(AutoStrategy.id == strategy_id).first()
        if not strategy:
            return None

        perf = _aggregate_strategy_performance(db, strategy)
        if perf is None:
            perf = {
                "strategy_id": strategy.id,
                "strategy_name": strategy.task_name,
                "uid": f"STR-{strategy.id:04d}",
                "trade_count": 0,
                "total_pnl": 0,
                "win_rate": 0,
            }

        # 计算综合分
        score = composite_score(
            annualized=perf.get("annualized_return", 0),
            max_drawdown=perf.get("max_drawdown", 0),
            sharpe=perf.get("sharpe_ratio", 0),
            profit_loss=perf.get("profit_loss_ratio", 0),
            execution_score=perf.get("execution_score", 0),
        )

        return {
            "uid": f"STR-{strategy.id:04d}",
            "strategy_id": strategy.id,
            "strategy_name": strategy.task_name,
            "platform": strategy.gateway,
            "tier": _strategy_tier(score),
            "composite_score": score,
            "annualized_return": perf.get("annualized_return", 0),
            "max_drawdown": perf.get("max_drawdown", 0),
            "sharpe_ratio": perf.get("sharpe_ratio", 0),
            "win_rate": perf.get("win_rate", 0),
            "profit_loss_ratio": perf.get("profit_loss_ratio", 0),
            "trade_count": perf.get("trade_count", 0),
            "total_pnl": perf.get("total_pnl", 0),
            "current_balance": float(strategy.current_balance or strategy.initial_balance or 0),
            "is_active": strategy.is_active,
            "interval": strategy.interval,
            "total_executions": strategy.total_executions or 0,
            "total_success": strategy.total_success or 0,
            "total_failed": strategy.total_failed or 0,
        }
