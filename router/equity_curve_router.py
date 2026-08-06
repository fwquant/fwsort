"""策略净值曲线路由"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from fwsort.database import get_db
from fwsort.strategy.equity_curve_service import get_equity_curve

router = APIRouter()


@router.get("/api/equity-curves")
async def list_equity_curves(
    strategy_name: str | None = None,
    account_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """查询净值曲线"""
    curves = get_equity_curve(
        db, strategy_name=strategy_name, account_id=account_id,
        date_from=date_from, date_to=date_to,
    )
    return {
        "code": 0, "msg": "ok",
        "data": [
            {
                "snapshot_date": c.snapshot_date.isoformat() if c.snapshot_date else None,
                "equity": c.equity,
                "balance": c.balance,
                "daily_pnl": c.daily_pnl,
                "daily_pnl_percent": c.daily_pnl_percent,
                "drawdown": c.drawdown,
                "drawdown_percent": c.drawdown_percent,
                "max_drawdown_percent": c.max_drawdown_percent,
                "position_count": c.position_count,
                "trade_count": c.trade_count,
            }
            for c in curves
        ],
        "total": len(curves),
    }
