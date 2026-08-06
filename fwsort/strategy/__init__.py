"""策略模块（合并原 signals + tasks）

导出：
    - 策略基类：StrategyBase（原 SignalProvider）
    - 信号对象：Signal
    - 策略管理器：get_signal / get_provider / list_providers / reload_providers
    - 自动策略服务：execute_strategy / create_strategy / start_strategy / stop_strategy
    - 调度器：start_dispatcher / stop_dispatcher / get_dispatcher_status
"""
from fwsort.strategy.base import Signal, StrategyBase, Direction, SignalCategory
from fwsort.strategy.manager import (
    get_signal,
    get_provider,
    list_providers,
    reload_providers,
    register_provider,
)

__all__ = [
    # 基类与数据结构
    "Signal",
    "StrategyBase",
    "Direction",
    "SignalCategory",
    # 策略管理器
    "get_signal",
    "get_provider",
    "list_providers",
    "reload_providers",
    "register_provider",
]
