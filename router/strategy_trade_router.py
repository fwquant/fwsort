"""策略交易明细路由"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from fwsort.database import get_db
from fwsort.strategy.strategy_trade_service import (
    list_trades,
    get_strategy_stats,
    get_direction_distribution,
)

router = APIRouter()


@router.get("/api/strategy-trades")
async def list_strategy_trades(
    strategy_name: str | None = None,
    auto_strategy_id: int | None = None,
    status: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """查询策略交易明细列表"""
    trades = list_trades(
        db, strategy_name=strategy_name, auto_strategy_id=auto_strategy_id,
        status=status, date_from=date_from, date_to=date_to,
        limit=limit, offset=offset,
    )
    return {
        "code": 0, "msg": "ok",
        "data": [
            {
                "id": t.id, "trade_uid": t.trade_uid,
                "strategy_name": t.strategy_name,
                "direction": t.direction, "side": t.side,
                "entry_price": t.entry_price, "exit_price": t.exit_price,
                "amount_usd": t.amount_usd,
                "pnl_amount": t.pnl_amount, "pnl_percent": t.pnl_percent,
                "is_profit": t.is_profit, "is_win": t.is_win,
                "status": t.status,
                "entry_at": t.entry_at.isoformat() if t.entry_at else None,
                "exit_at": t.exit_at.isoformat() if t.exit_at else None,
                "market_resolved": t.market_resolved,
            }
            for t in trades
        ],
        "total": len(trades),
    }


@router.get("/api/strategy-stats")
async def get_stats(
    strategy_name: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """获取策略统计：胜率、盈亏比等"""
    stats = get_strategy_stats(db, strategy_name=strategy_name)
    return {"code": 0, "msg": "ok", "data": stats}


@router.get("/api/strategy-direction-distribution")
async def get_direction_stats(
    strategy_name: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """获取投票方向分布"""
    dist = get_direction_distribution(db, strategy_name=strategy_name)
    return {"code": 0, "msg": "ok", "data": dist}
