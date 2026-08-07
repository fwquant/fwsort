"""结算数据同步服务：结算回查后更新所有相关表

职责：
1. 更新 StrategyTrade 表（交易明细）
2. 更新 AutoStrategy 统计数据（累计盈亏、胜率等）
3. 重算 StrategyPerformance 绩效指标
4. 触发 Redis 榜单重算
5. 更新 StrategyEquityCurve 净值曲线
"""
from __future__ import annotations

import json
from datetime import datetime, date
from decimal import Decimal

from fwsort.database import get_sync_db
from fwsort.fwlogs import logger
from fwsort.models import (
    AutoStrategy,
    StrategyPerformance,
    StrategyTrade,
    StrategyEquityCurve,
    RankSnapshot,
)


def _safe_dumps(obj) -> str:
    """安全 JSON 序列化"""
    def _json_default(obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        if hasattr(obj, "__str__"):
            return str(obj)
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
    return json.dumps(obj, ensure_ascii=False, default=_json_default)


def update_trade_on_settlement(
    db,
    task: AutoStrategy,
    prev_log,
    pnl_amount: float,
    pnl_percent: float,
    is_profit: bool,
    we_won: bool,
    market_slug: str,
    direction: str,
) -> None:
    """结算回查后更新 StrategyTrade 表

    Args:
        db: SQLAlchemy 会话
        task: 任务实例
        prev_log: 执行日志实例
        pnl_amount: 盈亏金额
        pnl_percent: 盈亏百分比
        is_profit: 是否盈利
        we_won: 是否获胜（方向正确）
        market_slug: 市场 slug
        direction: 下注方向
    """
    try:
        # 解析日志中的 order_id 和 trade_uid
        result_detail = json.loads(prev_log.result_detail_json or "{}")
        order_id = result_detail.get("order_id", "") or prev_log.order_id

        if not order_id:
            logger.warning(f"[SettlementSync] 日志 {prev_log.id} 无 order_id，跳过 StrategyTrade 更新")
            return

        # 查找对应的 StrategyTrade 记录
        trade = (
            db.query(StrategyTrade)
            .filter(
                StrategyTrade.auto_strategy_id == task.id,
                StrategyTrade.order_id == order_id,
                StrategyTrade.market_resolved == False,
            )
            .first()
        )

        if not trade:
            logger.debug(
                f"[SettlementSync] 未找到对应的 StrategyTrade: "
                f"auto_strategy_id={task.id}, order_id={order_id}"
            )
            return

        # 获取 entry_price
        exec_detail = json.loads(prev_log.execution_detail_json or "{}")
        making_amount = float(exec_detail.get("making_amount", 0) or 0)

        # 计算 exit_price（结算后的价格，赢方为 1.0，输方为 0.0）
        exit_price = 1.0 if we_won else 0.0

        # 更新交易记录
        trade.market_resolved = True
        trade.resolved_at = datetime.utcnow()
        trade.exit_price = exit_price
        trade.pnl_amount = pnl_amount
        trade.pnl_percent = pnl_percent
        trade.is_profit = is_profit
        trade.is_win = we_won

        # 更新状态
        if is_profit:
            trade.status = 1  # 已平仓盈利
        elif pnl_amount < 0:
            trade.status = 2  # 已平仓亏损
        else:
            trade.status = 3  # 已平仓持平

        # 更新退出时间
        if trade.entry_at:
            duration = datetime.utcnow() - trade.entry_at
            trade.exit_at = datetime.utcnow()
            trade.hold_duration_seconds = int(duration.total_seconds())

        logger.info(
            f"[SettlementSync] ✅ StrategyTrade 更新: trade_uid={trade.trade_uid} "
            f"pnl={pnl_amount:.4f} won={we_won} slug={market_slug}"
        )

    except Exception as e:
        logger.error(f"[SettlementSync] StrategyTrade 更新失败: {e}")


def update_auto_strategy_stats(
    db,
    task: AutoStrategy,
    pnl_amount: float,
    is_profit: bool,
) -> None:
    """结算回查后更新 AutoStrategy 累计统计

    Args:
        db: SQLAlchemy 会话
        task: 任务实例
        pnl_amount: 盈亏金额
        is_profit: 是否盈利
    """
    try:
        # 更新累计数据
        task.total_pnl = (task.total_pnl or 0) + Decimal(str(pnl_amount))
        task.total_trades = (task.total_trades or 0) + 1

        if is_profit:
            task.win_trades = (task.win_trades or 0) + 1
        else:
            task.loss_trades = (task.loss_trades or 0) + 1

        # 重新计算胜率
        if task.total_trades > 0:
            task.win_rate = float(task.win_trades or 0) / float(task.total_trades) * 100

        # 更新当前余额
        task.current_balance = (task.current_balance or task.initial_balance or 0) + Decimal(str(pnl_amount))

        # 重新计算盈亏比
        if task.loss_trades and task.loss_trades > 0:
            task.profit_loss_ratio = float(task.win_trades or 0) / float(task.loss_trades)

        logger.info(
            f"[SettlementSync] 📊 AutoStrategy 统计更新: "
            f"total_pnl={float(task.total_pnl):.4f} "
            f"win_rate={float(task.win_rate or 0):.2f}% "
            f"balance={float(task.current_balance):.4f}"
        )

    except Exception as e:
        logger.error(f"[SettlementSync] AutoStrategy 统计更新失败: {e}")


def recalculate_performance(db, task: AutoStrategy) -> dict:
    """重算 StrategyPerformance 绩效指标

    Args:
        db: SQLAlchemy 会话
        task: 任务实例

    Returns:
        dict: 重算结果摘要
    """
    try:
        # 获取该任务所有已结算交易
        resolved_trades = (
            db.query(StrategyTrade)
            .filter(
                StrategyTrade.auto_strategy_id == task.id,
                StrategyTrade.market_resolved == True,
            )
            .order_by(StrategyTrade.entry_at.asc())
            .all()
        )

        if not resolved_trades:
            return {"status": "skipped", "reason": "无已结算交易"}

        trades_count = len(resolved_trades)
        win_trades = [t for t in resolved_trades if t.is_profit]
        loss_trades = [t for t in resolved_trades if not t.is_profit]

        total_pnl = sum(float(t.pnl_amount or 0) for t in resolved_trades)
        total_profit = sum(float(t.pnl_amount or 0) for t in win_trades)
        total_loss = abs(sum(float(t.pnl_amount or 0) for t in loss_trades))

        # 计算各项指标
        win_rate = len(win_trades) / trades_count * 100 if trades_count > 0 else 0
        profit_loss_ratio = total_profit / total_loss if total_loss > 0 else float('inf')

        # 计算收益
        initial_balance = float(task.initial_balance or 1000)
        total_return = total_pnl / initial_balance if initial_balance > 0 else 0

        # 简单年化（按交易天数估算）
        if len(resolved_trades) >= 2:
            first_trade = resolved_trades[0]
            last_trade = resolved_trades[-1]
            days = (last_trade.entry_at - first_trade.entry_at).days + 1
            annualized_return = total_return / days * 365 if days > 0 else 0
        else:
            annualized_return = total_return

        # 计算最大回撤
        current_equity = initial_balance
        peak_equity = initial_balance
        max_drawdown = 0

        for trade in resolved_trades:
            current_equity += float(trade.pnl_amount or 0)
            peak_equity = max(peak_equity, current_equity)
            drawdown = peak_equity - current_equity
            max_drawdown = max(max_drawdown, drawdown / peak_equity if peak_equity > 0 else 0)

        # 更新/创建 Performance 记录（按周期）
        now = datetime.utcnow()
        period_end = now.replace(hour=0, minute=0, second=0, microsecond=0)

        for period_type, days in [(1, 1), (2, 7), (3, 30), (4, 36500)]:
            period_start = datetime(now.year, now.month, 1) if period_type in (1, 2, 3) else datetime(2000, 1, 1)
            if period_type == 1:
                period_start = period_end - (datetime.utcnow() - datetime.utcnow()).__class__(days=1)
            elif period_type == 2:
                from datetime import timedelta
                period_start = period_end - timedelta(days=7)
            elif period_type == 3:
                period_start = datetime(now.year, now.month, 1)
            else:
                period_start = datetime(2000, 1, 1)

            # 获取该周期内的交易
            period_trades = [
                t for t in resolved_trades
                if t.entry_at >= period_start
            ]

            if not period_trades:
                continue

            # 计算该周期指标
            period_pnl = sum(float(t.pnl_amount or 0) for t in period_trades)
            period_win = [t for t in period_trades if t.is_profit]
            period_loss = [t for t in period_trades if not t.is_profit]

            perf = (
                db.query(StrategyPerformance)
                .filter(
                    StrategyPerformance.account_id == task.account_id,
                    StrategyPerformance.uid == task.task_name,
                    StrategyPerformance.period_type == period_type,
                )
                .first()
            )

            if perf is None:
                perf = StrategyPerformance(
                    account_id=task.account_id,
                    uid=task.task_name,
                    period_type=period_type,
                    start_time=period_start,
                    end_time=period_end,
                )
                db.add(perf)

            # 更新指标
            perf.total_return = period_pnl / initial_balance if initial_balance > 0 else 0
            perf.annualized_return = period_pnl / initial_balance * 365 if period_type != 4 and period_start < period_end else 0
            perf.win_rate = len(period_win) / len(period_trades) * 100 if period_trades else 0
            perf.profit_loss_ratio = (
                sum(float(t.pnl_amount or 0) for t in period_win) /
                max(abs(sum(float(t.pnl_amount or 0) for t in period_loss)), 0.001)
            )
            perf.trade_count = len(period_trades)
            perf.max_drawdown = max_drawdown
            perf.updated_at = now

        logger.info(
            f"[SettlementSync] 📈 Performance 重算: "
            f"trades={trades_count} win_rate={win_rate:.2f}% "
            f"total_return={total_return:.4f}"
        )

        return {
            "status": "success",
            "trades_count": trades_count,
            "win_rate": win_rate,
            "total_return": total_return,
        }

    except Exception as e:
        logger.error(f"[SettlementSync] Performance 重算失败: {e}")
        return {"status": "failed", "error": str(e)}


def trigger_ranking_update(db, task: AutoStrategy) -> dict:
    """触发 Redis 榜单重算（包括账户榜单和策略榜单）

    Args:
        db: SQLAlchemy 会话
        task: 任务实例

    Returns:
        dict: 重算结果
    """
    try:
        from fwsort.ranking_engine import refresh_redis_zset
        from fwsort.strategy.strategy_ranking import refresh_strategy_redis_zset

        # 刷新账户榜单
        account_result = refresh_redis_zset(db)
        
        # 刷新策略榜单
        strategy_result = refresh_strategy_redis_zset()
        
        logger.info(
            f"[SettlementSync] 🏆 榜单重算触发: "
            f"账户榜单 updated={account_result.get('updated', 0)} "
            f"策略榜单 updated={strategy_result.get('updated', 0)}"
        )
        
        return {
            "account_ranking": account_result,
            "strategy_ranking": strategy_result,
        }

    except Exception as e:
        logger.error(f"[SettlementSync] 榜单重算触发失败: {e}")
        return {"error": str(e)}


def update_equity_curve(db, task: AutoStrategy) -> dict:
    """更新净值曲线（每日快照）

    Args:
        db: SQLAlchemy 会话
        task: 任务实例

    Returns:
        dict: 更新结果
    """
    try:
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        # 获取当前最新数据
        resolved_trades = (
            db.query(StrategyTrade)
            .filter(
                StrategyTrade.auto_strategy_id == task.id,
                StrategyTrade.market_resolved == True,
            )
            .order_by(StrategyTrade.entry_at.asc())
            .all()
        )

        initial_balance = float(task.initial_balance or 1000)
        current_balance = float(task.current_balance or initial_balance)
        total_pnl = current_balance - initial_balance

        # 计算当日盈亏
        today_start = today
        today_trades = [t for t in resolved_trades if t.entry_at >= today_start]
        today_pnl = sum(float(t.pnl_amount or 0) for t in today_trades)

        # 计算回撤
        current_equity = current_balance
        peak_equity = max(initial_balance, current_balance)
        drawdown = peak_equity - current_equity
        drawdown_percent = drawdown / peak_equity if peak_equity > 0 else 0

        # 查找或创建今日快照
        curve = (
            db.query(StrategyEquityCurve)
            .filter(
                StrategyEquityCurve.strategy_name == task.task_name,
                StrategyEquityCurve.snapshot_date == today,
            )
            .first()
        )

        if curve is None:
            curve = StrategyEquityCurve(
                strategy_name=task.task_name,
                auto_strategy_id=task.id,
                account_id=task.account_id,
                snapshot_date=today,
            )
            db.add(curve)

        # 更新快照
        curve.equity = current_equity
        curve.balance = current_balance
        curve.daily_pnl = today_pnl
        curve.daily_pnl_percent = today_pnl / initial_balance * 100 if initial_balance > 0 else 0
        curve.peak_equity = max(curve.peak_equity or initial_balance, peak_equity)
        curve.drawdown = drawdown
        curve.drawdown_percent = drawdown_percent
        curve.max_drawdown_percent = max(curve.max_drawdown_percent or 0, drawdown_percent)
        curve.position_count = len(
            [t for t in resolved_trades if not t.market_resolved]
        )
        curve.trade_count = len(today_trades)

        logger.info(
            f"[SettlementSync] 📈 EquityCurve 更新: "
            f"equity={current_equity:.4f} "
            f"daily_pnl={today_pnl:.4f} "
            f"max_dd={curve.max_drawdown_percent:.4f}"
        )

        return {
            "status": "success",
            "date": today.isoformat(),
            "equity": current_equity,
            "daily_pnl": today_pnl,
        }

    except Exception as e:
        logger.error(f"[SettlementSync] EquityCurve 更新失败: {e}")
        return {"status": "failed", "error": str(e)}


def sync_all_on_settlement(
    db,
    task: AutoStrategy,
    prev_log,
    pnl_amount: float,
    pnl_percent: float,
    is_profit: bool,
    we_won: bool,
    market_slug: str,
    direction: str,
) -> dict:
    """结算回查后同步所有相关数据

    这是主入口函数，在结算成功后调用。

    Args:
        db: SQLAlchemy 会话
        task: 任务实例
        prev_log: 执行日志实例
        pnl_amount: 盈亏金额
        pnl_percent: 盈亏百分比
        is_profit: 是否盈利
        we_won: 是否获胜
        market_slug: 市场 slug
        direction: 下注方向

    Returns:
        dict: 所有更新结果
    """
    results = {}

    # 1. 更新 StrategyTrade
    update_trade_on_settlement(
        db, task, prev_log, pnl_amount, pnl_percent,
        is_profit, we_won, market_slug, direction
    )
    results["strategy_trade"] = "ok"

    # 2. 更新 AutoStrategy 统计
    update_auto_strategy_stats(db, task, pnl_amount, is_profit)
    results["auto_strategy_stats"] = "ok"

    # 3. 重算 Performance（单次结算不触发，但批量结算后可触发）
    # 为了性能，这里只更新最近的周期
    perf_result = recalculate_performance(db, task)
    results["performance"] = perf_result

    # 4. 更新净值曲线
    curve_result = update_equity_curve(db, task)
    results["equity_curve"] = curve_result

    # 5. 触发策略榜单刷新（轻量操作）
    try:
        from fwsort.strategy.strategy_ranking import refresh_strategy_redis_zset
        strategy_ranking_result = refresh_strategy_redis_zset()
        results["strategy_ranking"] = strategy_ranking_result
    except Exception as ranking_err:
        logger.warning(f"[SettlementSync] 策略榜单刷新失败(不影响主流程): {ranking_err}")
        results["strategy_ranking"] = {"error": str(ranking_err)}

    logger.info(
        f"[SettlementSync] 🎯 结算数据同步完成: "
        f"trade={market_slug} pnl={pnl_amount:.4f} won={we_won}"
    )

    return results


def batch_sync_after_resolution(db, task: AutoStrategy) -> dict:
    """批量结算后触发全量重算和榜单更新

    应该在 stop_task 或定时任务中调用。

    Args:
        db: SQLAlchemy 会话
        task: 任务实例

    Returns:
        dict: 重算结果
    """
    results = {}

    # 1. 全量重算 Performance
    perf_result = recalculate_performance(db, task)
    results["performance"] = perf_result

    # 2. 全量更新净值曲线
    curve_result = update_equity_curve(db, task)
    results["equity_curve"] = curve_result

    # 3. 触发榜单重算
    ranking_result = trigger_ranking_update(db, task)
    results["ranking"] = ranking_result

    # 4. 刷新策略榜单
    try:
        from fwsort.strategy.strategy_ranking import refresh_strategy_redis_zset
        strategy_ranking_result = refresh_strategy_redis_zset()
        results["strategy_ranking"] = strategy_ranking_result
    except Exception as ranking_err:
        logger.warning(f"[SettlementSync] 策略榜单刷新失败(不影响主流程): {ranking_err}")
        results["strategy_ranking"] = {"error": str(ranking_err)}

    logger.info(
        f"[SettlementSync] 🚀 批量结算后全量重算完成: "
        f"perf={perf_result.get('status', 'unknown')} "
        f"ranking_updated={ranking_result.get('updated', 0)}"
    )

    return results
