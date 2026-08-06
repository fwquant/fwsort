"""策略交易明细服务 - 交易记录 CRUD + 胜率/盈亏比/夏普等聚合统计"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fwsort.fwlogs import logger
from sqlalchemy import desc, asc, func, and_
from sqlalchemy.orm import Session

from fwsort.models import StrategyTrade, AutoStrategy


def create_trade(db: Session, **kwargs) -> StrategyTrade:
    """创建交易记录"""
    trade = StrategyTrade(**kwargs)
    db.add(trade)
    db.flush()
    logger.info(f"[StrategyTrade] 创建交易记录: {trade.trade_uid} strategy={trade.strategy_name}")
    return trade


def get_trade(db: Session, trade_id: int) -> StrategyTrade | None:
    """获取单条交易记录"""
    return db.query(StrategyTrade).filter(StrategyTrade.id == trade_id).first()


def get_trade_by_uid(db: Session, trade_uid: str) -> StrategyTrade | None:
    """按 UID 获取交易记录"""
    return db.query(StrategyTrade).filter(StrategyTrade.trade_uid == trade_uid).first()


def update_trade_pnl(
    db: Session,
    trade_id: int,
    exit_price: float,
    pnl_amount: float,
    pnl_percent: float,
    is_profit: bool,
    is_win: bool,
    status: int = 1,
    market_resolved: bool = True,
    resolved_at: datetime | None = None,
) -> StrategyTrade | None:
    """更新交易盈亏（市场结算后调用）"""
    trade = db.query(StrategyTrade).filter(StrategyTrade.id == trade_id).first()
    if not trade:
        return None

    trade.exit_price = exit_price
    trade.pnl_amount = pnl_amount
    trade.pnl_percent = pnl_percent
    trade.is_profit = is_profit
    trade.is_win = is_win
    trade.status = status
    trade.market_resolved = market_resolved
    trade.resolved_at = resolved_at or datetime.utcnow()
    if trade.exit_at and trade.entry_at:
        trade.hold_duration_seconds = int((trade.exit_at - trade.entry_at).total_seconds())
    db.flush()
    logger.info(f"[StrategyTrade] 更新盈亏: trade={trade_id} pnl={pnl_amount}")
    return trade


def list_trades(
    db: Session,
    strategy_name: str | None = None,
    auto_strategy_id: int | None = None,
    status: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[StrategyTrade]:
    """查询交易列表"""
    query = db.query(StrategyTrade).filter(StrategyTrade.deleted_at.is_(None))
    if strategy_name:
        query = query.filter(StrategyTrade.strategy_name == strategy_name)
    if auto_strategy_id:
        query = query.filter(StrategyTrade.auto_strategy_id == auto_strategy_id)
    if status is not None:
        query = query.filter(StrategyTrade.status == status)
    if date_from:
        query = query.filter(StrategyTrade.entry_at >= date_from)
    if date_to:
        query = query.filter(StrategyTrade.entry_at <= date_to)
    return query.order_by(desc(StrategyTrade.entry_at)).limit(limit).offset(offset).all()


def get_strategy_stats(db: Session, strategy_name: str | None = None) -> dict[str, Any]:
    """聚合统计：胜率、盈亏比、总盈亏等
    
    Args:
        strategy_name: 策略名，为 None 时统计全部
    Returns:
        dict: {total_trades, win_count, loss_count, win_rate, total_pnl, avg_win, avg_loss, profit_loss_ratio}
    """
    query = db.query(StrategyTrade).filter(
        StrategyTrade.status.in_([1, 2]),  # 已平仓盈利或亏损
        StrategyTrade.deleted_at.is_(None),
    )
    if strategy_name:
        query = query.filter(StrategyTrade.strategy_name == strategy_name)

    trades = query.all()
    if not trades:
        return {
            "total_trades": 0, "win_count": 0, "loss_count": 0,
            "win_rate": 0.0, "total_pnl": 0.0, "avg_win": 0.0,
            "avg_loss": 0.0, "profit_loss_ratio": 0.0,
        }

    total = len(trades)
    wins = [t for t in trades if t.is_profit]
    losses = [t for t in trades if not t.is_profit]
    win_count = len(wins)
    loss_count = len(losses)
    total_pnl = sum(t.pnl_amount for t in trades)
    avg_win = sum(t.pnl_amount for t in wins) / win_count if win_count else 0.0
    avg_loss = sum(t.pnl_amount for t in losses) / loss_count if loss_count else 0.0
    win_rate = (win_count / total * 100) if total else 0.0
    profit_loss_ratio = (avg_win / abs(avg_loss)) if avg_loss != 0 else 0.0

    return {
        "total_trades": total,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": round(win_rate, 2),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_loss_ratio": round(profit_loss_ratio, 2),
    }


def get_direction_distribution(db: Session, strategy_name: str | None = None) -> dict[str, int]:
    """投票方向分布统计（UP/DOWN）"""
    query = db.query(
        StrategyTrade.direction,
        func.count(StrategyTrade.id).label("count"),
    ).filter(
        StrategyTrade.deleted_at.is_(None),
        StrategyTrade.direction != "",
    )
    if strategy_name:
        query = query.filter(StrategyTrade.strategy_name == strategy_name)
    query = query.group_by(StrategyTrade.direction)

    return {row.direction: row.count for row in query.all()}
