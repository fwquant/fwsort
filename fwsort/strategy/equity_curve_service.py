"""策略净值曲线服务 - 每日快照 + 资金曲线/回撤曲线查询"""
from __future__ import annotations

from datetime import datetime, date
from typing import Any

from fwsort.fwlogs import logger
from sqlalchemy import desc
from sqlalchemy.orm import Session

from fwsort.models import StrategyEquityCurve, AutoStrategy, StrategyTrade
from fwsort.strategy.strategy_trade_service import get_strategy_stats


def create_snapshot(
    db: Session,
    strategy_name: str,
    auto_strategy_id: int | None = None,
    account_id: int | None = None,
    equity: float = 0.0,
    balance: float = 0.0,
    daily_pnl: float = 0.0,
    daily_pnl_percent: float = 0.0,
    peak_equity: float = 0.0,
    drawdown: float = 0.0,
    drawdown_percent: float = 0.0,
    max_drawdown_percent: float = 0.0,
    position_count: int = 0,
    trade_count: int = 0,
    snapshot_date: datetime | None = None,
) -> StrategyEquityCurve:
    """创建净值快照"""
    snapshot = StrategyEquityCurve(
        strategy_name=strategy_name,
        auto_strategy_id=auto_strategy_id,
        account_id=account_id,
        snapshot_date=snapshot_date or datetime.utcnow(),
        equity=equity,
        balance=balance,
        daily_pnl=daily_pnl,
        daily_pnl_percent=daily_pnl_percent,
        peak_equity=peak_equity,
        drawdown=drawdown,
        drawdown_percent=drawdown_percent,
        max_drawdown_percent=max_drawdown_percent,
        position_count=position_count,
        trade_count=trade_count,
    )
    db.add(snapshot)
    db.flush()
    logger.info(f"[EquityCurve] 快照: strategy={strategy_name} equity={equity} drawdown={drawdown_percent}%")
    return snapshot


def get_equity_curve(
    db: Session,
    strategy_name: str | None = None,
    account_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[StrategyEquityCurve]:
    """查询净值曲线"""
    query = db.query(StrategyEquityCurve)
    if strategy_name:
        query = query.filter(StrategyEquityCurve.strategy_name == strategy_name)
    if account_id:
        query = query.filter(StrategyEquityCurve.account_id == account_id)
    if date_from:
        query = query.filter(StrategyEquityCurve.snapshot_date >= date_from)
    if date_to:
        query = query.filter(StrategyEquityCurve.snapshot_date <= date_to)
    return query.order_by(StrategyEquityCurve.snapshot_date.asc()).all()


def get_latest_snapshot(db: Session, strategy_name: str) -> StrategyEquityCurve | None:
    """获取最新快照"""
    return (
        db.query(StrategyEquityCurve)
        .filter(StrategyEquityCurve.strategy_name == strategy_name)
        .order_by(desc(StrategyEquityCurve.snapshot_date))
        .first()
    )


def calc_drawdown(equity: float, peak_equity: float) -> tuple[float, float]:
    """计算回撤金额和回撤率"""
    if peak_equity <= 0:
        return 0.0, 0.0
    dd = peak_equity - equity
    dd_pct = (dd / peak_equity * 100) if peak_equity > 0 else 0.0
    return round(dd, 2), round(dd_pct, 2)
