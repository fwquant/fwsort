# router 包：API 路由层
from router import (
    admin_router,
    agent_router,
    auth_router,
    compare_router,
    config_router,
    follow_router,
    notification_router,
    polymarket_router,
    ranking_router,
    rental_router,
    signal_provider_router,
    task_router,
)

__all__ = [
    "admin_router",
    "agent_router",
    "auth_router",
    "compare_router",
    "config_router",
    "follow_router",
    "notification_router",
    "polymarket_router",
    "ranking_router",
    "rental_router",
    "signal_provider_router",
    "task_router",
]
__version__ = "1.0.0"
