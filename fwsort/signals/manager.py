"""信号管理器 - 工厂模式 + 自动发现 + 外部信号接入

支持：
    - 自动发现 signals/providers/ 下所有继承 SignalProvider 的类
    - 按 category 区分：internal（内部）/ external（外部）
    - 支持数据库中动态配置信号源
"""
from __future__ import annotations

import importlib
import inspect
import os
import pkgutil
from typing import Any

from loguru import logger

from fwsort.signals.base import Signal, SignalProvider

# 信号提供者注册表
_PROVIDERS: dict[str, type[SignalProvider]] = {}
_PROVIDER_INSTANCES: dict[str, SignalProvider] = {}
# 存储每个 provider 对应的 category（internal/external）
_PROVIDER_CATEGORIES: dict[str, str] = {}


def _discover_providers() -> None:
    """自动发现并注册 signals.providers 包下所有 SignalProvider 子类"""
    providers_dir = os.path.join(os.path.dirname(__file__), "providers")
    if not os.path.isdir(providers_dir):
        logger.warning("[SignalManager] providers directory not found")
        return

    # 导入 signals.providers 包
    import fwsort.signals.providers as providers_pkg

    # 获取模块路径列表（可能已失效，使用 providers_dir 作为备选）
    try:
        path_list = providers_pkg.__path__
    except AttributeError:
        path_list = [providers_dir]

    logger.info(f"[SignalManager] _discover_providers: scanning {path_list}")

    for _finder, module_name, _ispkg in pkgutil.iter_modules(path_list):
        logger.info(f"[SignalManager] _discover_providers: checking module {module_name}")
        try:
            module = importlib.import_module(f"fwsort.signals.providers.{module_name}")
            logger.info(f"[SignalManager] _discover_providers: imported {module_name}, searching for SignalProvider subclasses")
            attr_count = 0
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, SignalProvider)
                    and attr is not SignalProvider
                ):
                    attr_count += 1
                    # 创建实例获取 name
                    try:
                        inst = attr()
                        name = inst.name
                        if name not in _PROVIDERS:
                            _PROVIDERS[name] = attr
                            # 读取 category
                            category = getattr(attr, "category", "internal")
                            _PROVIDER_CATEGORIES[name] = category
                            logger.info(f"[SignalManager] auto-discovered provider: {name} ({category})")
                    except Exception as e:
                        logger.warning(f"[SignalManager] failed to instantiate {attr_name}: {e}")
            logger.info(f"[SignalManager] _discover_providers: module {module_name} has {attr_count} SignalProvider subclasses")
        except Exception as e:
            logger.warning(f"[SignalManager] failed to import module {module_name}: {e}")
            import traceback
            logger.error(f"[SignalManager] traceback: {traceback.format_exc()}")


def register_provider(name: str, provider_class: type[SignalProvider], category: str = "internal") -> None:
    """手动注册信号提供者"""
    _PROVIDERS[name] = provider_class
    _PROVIDER_CATEGORIES[name] = category
    logger.info(f"[SignalManager] registered provider: {name} ({category})")


def get_provider(name: str, **kwargs) -> SignalProvider:
    """获取信号提供者实例（单例）

    kwargs 会传递给 Provider 的 __init__ 方法。
    如果未提供 config_json，则自动从数据库加载配置。
    对于已有实例，不会重新创建（单例模式）。
    """
    if name not in _PROVIDERS:
        raise ValueError(f"Unknown signal provider: {name}, available: {list(_PROVIDERS.keys())}")

    if name not in _PROVIDER_INSTANCES:
        cls = _PROVIDERS[name]

        # 如果未提供 config_json，尝试从数据库加载
        if "config_json" not in kwargs:
            try:
                from fwsort.database import get_sync_db
                from fwsort.models import SignalProviderConfig
                with get_sync_db() as db:
                    db_cfg = db.query(SignalProviderConfig).filter(
                        SignalProviderConfig.provider_name == name
                    ).first()
                    if db_cfg and db_cfg.config_json:
                        import json as _json
                        kwargs["config_json"] = _json.loads(db_cfg.config_json)
            except Exception:
                pass

        # 尝试创建实例，兼容不同的构造函数签名
        try:
            _PROVIDER_INSTANCES[name] = cls(**kwargs)
        except TypeError:
            # 如果传递的参数不兼容，尝试仅传 config_json
            if "config_json" in kwargs:
                try:
                    _PROVIDER_INSTANCES[name] = cls(config_json=kwargs["config_json"])
                except TypeError:
                    # 最后兜底：无参数创建
                    _PROVIDER_INSTANCES[name] = cls()
            else:
                _PROVIDER_INSTANCES[name] = cls()
    return _PROVIDER_INSTANCES[name]


def reset_provider_instance(name: str) -> None:
    """重置信号提供者实例（当配置变更时调用）"""
    if name in _PROVIDER_INSTANCES:
        del _PROVIDER_INSTANCES[name]
        logger.info(f"[SignalManager] reset provider instance: {name}")


def list_providers() -> list[str]:
    """列出所有已注册的信号提供者"""
    return list(_PROVIDERS.keys())


def get_provider_info(name: str) -> dict | None:
    """获取信号提供者的详细信息"""
    if name not in _PROVIDERS:
        return None
    cls = _PROVIDERS[name]
    return {
        "name": name,
        "class_name": cls.__name__,
        "category": _PROVIDER_CATEGORIES.get(name, "internal"),
        "module": cls.__module__,
    }


def list_providers_by_category() -> dict[str, list[dict]]:
    """按类别列出所有信号提供者"""
    result = {"internal": [], "external": []}
    for name in _PROVIDERS:
        info = get_provider_info(name)
        if info:
            cat = _PROVIDER_CATEGORIES.get(name, "internal")
            result.setdefault(cat, []).append(info)
    return result


def get_signal(provider_name: str = "random", **kwargs) -> Signal:
    """便捷方法：获取一个信号"""
    provider = get_provider(provider_name, **kwargs)
    return provider.get_signal()


def reload_providers() -> dict:
    """热加载：重新扫描 providers/ 目录，重新注册所有信号源
    强制删除并重新导入模块，确保代码修改立即生效。

    Returns:
        dict: {"new": [...], "updated": [...], "removed": [...], "total": int}
    """
    import sys
    import importlib

    # 保存旧状态
    old_providers = set(_PROVIDERS.keys())

    # 1. 先删除 providers 包下的所有子模块（从 sys.modules 中移除）
    # 注意：不删除 base 模块，因为 base 是基类，不会频繁修改
    import fwsort.signals.providers as providers_pkg
    providers_dir_list = providers_pkg.__path__
    
    # 先保存模块名列表，避免删除过程中修改迭代对象
    modules_to_remove = []
    for module_info in pkgutil.iter_modules(providers_dir_list):
        module_name = module_info.name
        full_module_name = f"fwsort.signals.providers.{module_name}"
        if full_module_name in sys.modules:
            modules_to_remove.append(full_module_name)
    
    # 批量删除模块
    for full_module_name in modules_to_remove:
        del sys.modules[full_module_name]
        logger.info(f"[SignalManager] removed module from cache: {full_module_name}")

    # 2. 清空注册表和实例（让旧引用失效）
    _PROVIDERS.clear()
    _PROVIDER_INSTANCES.clear()
    _PROVIDER_CATEGORIES.clear()

    # 3. 重新发现（会自动重新导入所有模块）
    _discover_providers()

    # 4. 计算差异
    new_providers = set(_PROVIDERS.keys())
    added = list(new_providers - old_providers)
    removed = list(old_providers - new_providers)
    common = list(new_providers & old_providers)

    result = {
        "new": added,
        "removed": removed,
        "reloaded": len(common),
        "total": len(_PROVIDERS),
    }
    logger.info(f"[SignalManager] reload done: new={len(added)}, removed={len(removed)}, reloaded={len(common)}, total={len(_PROVIDERS)}")
    return result


def get_provider_file_path(provider_name: str) -> str | None:
    """获取信号源对应的 .py 文件路径"""
    import fwsort.signals.providers as providers_pkg
    providers_dir = os.path.dirname(providers_pkg.__file__)
    for module_info in pkgutil.iter_modules(providers_pkg.__path__):
        module_name = module_info.name
        try:
            module = importlib.import_module(f"fwsort.signals.providers.{module_name}")
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, SignalProvider)
                    and attr is not SignalProvider
                ):
                    try:
                        inst = attr()
                        if inst.name == provider_name:
                            return os.path.join(providers_dir, f"{module_name}.py")
                    except Exception:
                        pass
        except Exception:
            pass
    return None


# 初始化：自动发现所有信号提供者
_discover_providers()