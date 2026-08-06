"""策略配置服务层 - 已迁移到文件驱动架构

本模块保留用于向后兼容，实际逻辑已迁移到 fwsort.strategy.file_manager。
所有函数都是 file_manager 对应函数的薄封装。

新架构：
    - 策略配置存储在 providers/ 目录下的 .py 文件类属性默认值中
    - 参数声明通过 parameters 列表在 StrategyBase 子类中定义
    - Web 界面编辑参数时直接修改 .py 文件并触发热加载
"""
from __future__ import annotations

from fwsort.strategy.file_manager import (
    check_provider_references,
    create_provider,
    delete_provider,
    hot_reload,
    list_all_providers as list_signal_providers,
    run_health_check,
    test_provider,
    toggle_provider,
    update_provider_values as update_signal_provider,
    open_provider_file,
    get_provider_detail as get_signal_provider,
)
from fwsort.strategy.base import SignalCategory


def sync_builtin_providers() -> int:
    """已废弃：新架构自动发现 providers/ 目录下所有策略"""
    return 0


def list_active_signal_providers() -> list[dict]:
    """获取所有启用的策略"""
    providers = list_signal_providers()
    return [p for p in providers if p.get("is_active", True)]


def get_available_categories() -> list[dict]:
    """获取所有可用的策略类别枚举"""
    result = []
    for cat in SignalCategory:
        result.append({
            "value": cat.value,
            "display": SignalCategory.display_names().get(cat.value, cat.value),
        })
    return result
