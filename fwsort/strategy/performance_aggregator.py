"""绩效聚合器：从 AutoStrategyLog 聚合到 StrategyPerformance

职责：
    - 按 account_id 聚合已结算的 AutoStrategyLog
    - 计算胜率、盈亏比、总盈亏、最大回撤、夏普率、资金曲线
    - 写入 StrategyPerformance 表（period_type=4 总周期）
    - 调用 ranking_engine 重算 composite_score

调用时机：
    - 定时任务（scheduler.py 每 5 分钟）
    - 盈亏结算后实时触发（可选）
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func

from fwsort.database import get_sync_db
from fwsort.fwlogs import logger
from fwsort.models import AutoStrategy, AutoStrategyLog, ExecutionAccount, StrategyPerformance


def aggregate_account_performance(db, account: ExecutionAccount) -> dict | None:
    """聚合单个账户的绩效，写入 StrategyPerformance（period_type=4 总周期）

    Args:
        db: SQLAlchemy 同步 session
        account: ExecutionAccount ORM 对象

    Returns:
        dict: 聚合结果（胜率/盈亏比/总盈亏等），无数据返回 None
    """
    # 找到关联的 AutoStrategy
    task = db.query(AutoStrategy).filter(AutoStrategy.account_id == account.id).first()
    if not task:
        return None

    # 查询所有已结算的执行日志（按时间排序）
    logs = (
        db.query(AutoStrategyLog)
        .filter(
            AutoStrategyLog.task_id == task.id,
            AutoStrategyLog.log_type == 0,
            AutoStrategyLog.market_resolved == True,  # noqa: E712
            AutoStrategyLog.pnl_amount != 0,
        )
        .order_by(AutoStrategyLog.executed_at.asc())
        .all()
    )

    if not logs:
        return None

    # ===== 基础统计 =====
    trade_count = len(logs)
    pnl_list = [float(log.pnl_amount) for log in logs]
    wins = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p < 0]

    total_pnl = sum(pnl_list)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = win_count / trade_count if trade_count > 0 else 0.0

    # 盈亏比 = 平均盈利 / 平均亏损绝对值
    avg_win = sum(wins) / win_count if win_count > 0 else 0.0
    avg_loss = abs(sum(losses) / loss_count) if loss_count > 0 else 0.0
    profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else (999.0 if avg_win > 0 else 0.0)

    # 总收益率 = 总盈亏 / 初始资金（用首笔 making_amount 近似）
    first_log = logs[0]
    try:
        exec_detail = json.loads(first_log.execution_detail_json or "{}")
        initial_capital = float(exec_detail.get("making_amount", 0))
    except Exception:
        initial_capital = 0.0
    total_return = (total_pnl / initial_capital) if initial_capital > 0 else 0.0

    # 年化收益率（简化：按交易频率估算，假设每笔间隔 = 5 分钟）
    if len(logs) >= 2:
        span_seconds = (logs[-1].executed_at - logs[0].executed_at).total_seconds()
        avg_interval = span_seconds / (len(logs) - 1) if span_seconds > 0 else 300
        trades_per_year = (365 * 24 * 3600) / avg_interval if avg_interval > 0 else 0
        annualized_return = total_return * (trades_per_year / max(trade_count, 1)) * 50  # 衰减因子
    else:
        annualized_return = total_return * 50

    # ===== 风险指标 =====
    # 资金曲线：累计 PnL 序列
    cumulative = []
    running = 0.0
    for p in pnl_list:
        running += p
        cumulative.append(running)

    # 最大回撤
    max_drawdown = 0.0
    peak = cumulative[0] if cumulative else 0.0
    for val in cumulative:
        if val > peak:
            peak = val
        dd = (peak - val) / peak if peak > 0 else 0.0
        if dd > max_drawdown:
            max_drawdown = dd

    # 波动率（PnL 标准差）
    if len(pnl_list) >= 2:
        mean_pnl = sum(pnl_list) / len(pnl_list)
        variance = sum((p - mean_pnl) ** 2 for p in pnl_list) / (len(pnl_list) - 1)
        volatility = variance ** 0.5
    else:
        volatility = 0.0

    # 夏普率（简化：mean_pnl / volatility，假设无风险利率=0）
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

    # 执行分（简化：基于成功率）
    success_count = sum(1 for log in logs if log.status in (0, 2))
    execution_rate = success_count / trade_count if trade_count > 0 else 0.0
    execution_score = execution_rate  # 简化映射

    # ===== 写入 StrategyPerformance（period_type=4 总周期）=====
    perf = (
        db.query(StrategyPerformance)
        .filter(
            StrategyPerformance.account_id == account.id,
            StrategyPerformance.period_type == 4,
        )
        .first()
    )

    if not perf:
        perf = StrategyPerformance(
            account_id=account.id,
            uid=account.uid,
            period_type=4,
            start_time=logs[0].executed_at,
            end_time=datetime.utcnow(),
        )
        db.add(perf)
        db.flush()

    # 更新所有字段
    perf.total_return = round(total_return, 6)
    perf.annualized_return = round(annualized_return, 6)
    perf.sharpe_ratio = round(sharpe_ratio, 6)
    perf.max_drawdown = round(max_drawdown, 6)
    perf.volatility = round(volatility, 6)
    perf.win_rate = round(win_rate, 6)
    perf.profit_loss_ratio = round(profit_loss_ratio, 6)
    perf.trade_count = trade_count
    perf.max_consecutive_loss = max_consecutive_loss
    perf.execution_rate = round(execution_rate, 6)
    perf.execution_score = round(execution_score, 6)
    perf.end_time = datetime.utcnow()

    # 盈利因子 = 总盈利 / 总亏损绝对值
    total_win = sum(wins)
    total_loss = abs(sum(losses))
    perf.profit_factor = round(total_win / total_loss, 6) if total_loss > 0 else (999.0 if total_win > 0 else 0.0)

    result = {
        "uid": account.uid,
        "trade_count": trade_count,
        "win_rate": round(win_rate * 100, 2),
        "total_pnl": round(total_pnl, 4),
        "profit_loss_ratio": round(profit_loss_ratio, 4),
        "max_drawdown": round(max_drawdown * 100, 2),
        "sharpe_ratio": round(sharpe_ratio, 4),
        "cumulative_curve": cumulative,
    }

    logger.info(
        f"[Aggregator] 📊 账户 {account.uid} 聚合完成: "
        f"trades={trade_count} win_rate={win_rate*100:.1f}% "
        f"pnl={total_pnl:+.4f} sharpe={sharpe_ratio:.4f} dd={max_drawdown*100:.1f}%"
    )

    return result


def aggregate_all_accounts() -> dict:
    """聚合所有有 AutoStrategy 关联的 ExecutionAccount

    Returns:
        dict: {"updated": N, "failed": N, "details": [...]}
    """
    updated = 0
    failed = 0
    details = []

    with get_sync_db() as db:
        # 查找所有有 account_id 的 AutoStrategy
        tasks = db.query(AutoStrategy).filter(AutoStrategy.account_id.isnot(None)).all()
        account_ids = list({t.account_id for t in tasks})

        for acc_id in account_ids:
            account = db.query(ExecutionAccount).filter(ExecutionAccount.id == acc_id).first()
            if not account:
                continue
            try:
                result = aggregate_account_performance(db, account)
                if result:
                    updated += 1
                    details.append(result)
                else:
                    logger.debug(f"[Aggregator] 账户 {account.uid} 无已结算日志，跳过")
            except Exception as e:
                failed += 1
                logger.warning(f"[Aggregator] 账户 {account.uid} 聚合失败: {e}")

        # 重算 composite_score + 写 Redis
        if updated > 0:
            try:
                from fwsort.ranking_engine import refresh_redis_zset
                refresh_redis_zset(db, rank_type=4)
                logger.info(f"[Aggregator] 🏆 composite_score 已重算并写入 Redis")
            except Exception as e:
                logger.warning(f"[Aggregator] 重算 composite_score 失败: {e}")

        db.commit()

    logger.info(f"[Aggregator] 聚合完成: updated={updated}, failed={failed}")
    return {"updated": updated, "failed": failed, "details": details}


def get_account_equity_curve(account_id: int, limit: int = 100) -> list[dict]:
    """获取账户资金曲线数据（用于前端 sparkline）

    Args:
        account_id: ExecutionAccount.id
        limit: 最多返回点数

    Returns:
        [{"time": iso, "pnl": float, "cumulative": float}, ...]
    """
    with get_sync_db() as db:
        task = db.query(AutoStrategy).filter(AutoStrategy.account_id == account_id).first()
        if not task:
            return []

        logs = (
            db.query(AutoStrategyLog)
            .filter(
                AutoStrategyLog.task_id == task.id,
                AutoStrategyLog.log_type == 0,
                AutoStrategyLog.market_resolved == True,  # noqa: E712
                AutoStrategyLog.pnl_amount != 0,
            )
            .order_by(AutoStrategyLog.executed_at.asc())
            .limit(limit)
            .all()
        )

        curve = []
        running = 0.0
        for log in logs:
            pnl = float(log.pnl_amount)
            running += pnl
            curve.append({
                "time": log.executed_at.isoformat() if log.executed_at else None,
                "pnl": round(pnl, 4),
                "cumulative": round(running, 4),
            })
        return curve
