# router 包：API 路由层
from router import (
    admin_router,
    agent_router,
    auth_router,
    auto_strategy_router,
    compare_router,
    config_router,
    equity_curve_router,
    follow_router,
    notification_router,
    polymarket_router,
    ranking_router,
    rental_router,
    risk_router,
    strategy_router,
    strategy_trade_router,
)

__all__ = [
    "admin_router",
    "agent_router",
    "auth_router",
    "auto_strategy_router",
    "compare_router",
    "config_router",
    "equity_curve_router",
    "follow_router",
    "notification_router",
    "polymarket_router",
    "ranking_router",
    "rental_router",
    "risk_router",
    "strategy_router",
    "strategy_trade_router",
]
__version__ = "1.0.0"
