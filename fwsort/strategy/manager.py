"""策略管理器 - 工厂模式 + 自动发现 + 外部信号接入

支持：
    - 自动发现 strategy/providers/ 下所有继承 StrategyBase 的类
    - 按 category 区分：internal（内部）/ external（外部）
    - 支持数据库中动态配置策略
"""
from __future__ import annotations

import importlib
import inspect
import os
import pkgutil
import re
from typing import Any

from fwsort.fwlogs import logger

from fwsort.strategy.base import Signal, StrategyBase

# 策略注册表
_PROVIDERS: dict[str, type[StrategyBase]] = {}
_PROVIDER_INSTANCES: dict[str, StrategyBase] = {}
# 存储每个 provider 对应的 category（internal/external）
_PROVIDER_CATEGORIES: dict[str, str] = {}


def _discover_providers() -> None:
    """自动发现并注册 strategy.providers 包下所有 StrategyBase 子类"""
    providers_dir = os.path.join(os.path.dirname(__file__), "providers")
    if not os.path.isdir(providers_dir):
        logger.warning("[SignalManager] providers directory not found")
        return

    # 导入 strategy.providers 包
    import fwsort.strategy.providers as providers_pkg

    # 获取模块路径列表（可能已失效，使用 providers_dir 作为备选）
    try:
        path_list = providers_pkg.__path__
    except AttributeError:
        path_list = [providers_dir]

    logger.info(f"[SignalManager] _discover_providers: scanning {path_list}")

    for _finder, module_name, _ispkg in pkgutil.iter_modules(path_list):
        logger.info(f"[SignalManager] _discover_providers: checking module {module_name}")
        try:
            module = importlib.import_module(f"fwsort.strategy.providers.{module_name}")
            logger.info(f"[SignalManager] _discover_providers: imported {module_name}, searching for StrategyBase subclasses")
            attr_count = 0
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, StrategyBase)
                    and attr is not StrategyBase
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
            logger.info(f"[SignalManager] _discover_providers: module {module_name} has {attr_count} StrategyBase subclasses")
        except Exception as e:
            logger.warning(f"[SignalManager] failed to import module {module_name}: {e}")
            import traceback
            logger.error(f"[SignalManager] traceback: {traceback.format_exc()}")


def register_provider(name: str, provider_class: type[StrategyBase], category: str = "internal") -> None:
    """手动注册策略"""
    _PROVIDERS[name] = provider_class
    _PROVIDER_CATEGORIES[name] = category
    logger.info(f"[SignalManager] registered provider: {name} ({category})")


def get_provider(name: str, **kwargs) -> StrategyBase:
    """获取策略实例（单例）

    新架构：参数值存储在 .py 文件的类属性默认值中。
    kwargs 会传递给 Provider 的 __init__ 方法。
    如果未提供 config_json，则使用空字典（让 Provider 使用类默认值）。
    """
    if name not in _PROVIDERS:
        raise ValueError(f"Unknown signal provider: {name}, available: {list(_PROVIDERS.keys())}")

    if name not in _PROVIDER_INSTANCES:
        cls = _PROVIDERS[name]

        # 新架构：不再从数据库加载 config_json
        # Provider 的 __init__ 会优先使用 config_json 中的值，
        # 否则使用类属性的默认值
        if "config_json" not in kwargs:
            kwargs["config_json"] = {}

        # 尝试创建实例，兼容不同的构造函数签名
        try:
            _PROVIDER_INSTANCES[name] = cls(**kwargs)
        except TypeError:
            if "config_json" in kwargs:
                try:
                    _PROVIDER_INSTANCES[name] = cls(config_json=kwargs["config_json"])
                except TypeError:
                    _PROVIDER_INSTANCES[name] = cls()
            else:
                _PROVIDER_INSTANCES[name] = cls()
    return _PROVIDER_INSTANCES[name]


def reset_provider_instance(name: str) -> None:
    """重置策略实例（当配置变更时调用）"""
    if name in _PROVIDER_INSTANCES:
        del _PROVIDER_INSTANCES[name]
        logger.info(f"[SignalManager] reset provider instance: {name}")


def list_providers() -> list[str]:
    """列出所有已注册的策略"""
    return list(_PROVIDERS.keys())


def get_provider_info(name: str) -> dict | None:
    """获取策略的详细信息"""
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
    """按类别列出所有策略"""
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
    """热加载：重新扫描 providers/ 目录，重新注册所有策略
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
    import fwsort.strategy.providers as providers_pkg
    providers_dir_list = providers_pkg.__path__

    # 先保存模块名列表，避免删除过程中修改迭代对象
    modules_to_remove = []
    for module_info in pkgutil.iter_modules(providers_dir_list):
        module_name = module_info.name
        full_module_name = f"fwsort.strategy.providers.{module_name}"
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
    """获取策略对应的 .py 文件路径（支持子包，支持文件系统回退）

    按优先级尝试：
    1. 从注册表获取类，用 inspect.getfile() 取路径（验证文件存在）
    2. 用 cls.__module__ 构造真实文件路径
    3. 遍历所有模块查找并取路径
    4. 文件系统直接扫描（不依赖模块导入）
    """
    # 优先从注册表获取类
    cls = _PROVIDERS.get(provider_name)
    if cls is not None:
        # 方法1: inspect.getfile (对单子模块策略有效)
        try:
            path = inspect.getfile(cls)
            if path and os.path.exists(path):
                return path
        except (TypeError, OSError):
            pass

        # 方法2: 从 cls.__module__ 构造路径（解决子包策略 inspect 返回错误路径的问题）
        path = _module_to_path(cls.__module__)
        if path and os.path.exists(path):
            return path

    # 回退1：遍历所有模块查找（需要模块可导入）
    import fwsort.strategy.providers as providers_pkg
    for module_info in pkgutil.iter_modules(providers_pkg.__path__):
        module_name = module_info.name
        try:
            module = importlib.import_module(f"fwsort.strategy.providers.{module_name}")
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, StrategyBase)
                    and attr is not StrategyBase
                ):
                    try:
                        inst = attr()
                        if inst.name == provider_name:
                            # 优先用 __module__ 构造路径
                            path = _module_to_path(attr.__module__)
                            if path and os.path.exists(path):
                                return path
                            # 再试 inspect.getfile
                            try:
                                path = inspect.getfile(attr)
                                if path and os.path.exists(path):
                                    return path
                            except (TypeError, OSError):
                                pass
                    except Exception:
                        pass
        except Exception:
            pass

    # 回退2：文件系统直接扫描（不依赖模块导入，处理因依赖缺失导致的导入失败）
    providers_dir = os.path.join(os.path.dirname(__file__), "providers")
    if os.path.isdir(providers_dir):
        result = _find_provider_file_by_scan(providers_dir, provider_name)
        if result:
            return result

    return None


def _module_to_path(module_name: str) -> str | None:
    """将模块名转换为文件系统路径（处理子包结构）

    例: 'fwsort.strategy.providers.hermes.sftp_strategy'
     → 'fwsort/strategy/providers/hermes/sftp_strategy.py'
    """
    if not module_name:
        return None
    try:
        parts = module_name.split(".")
        # 从当前模块位置推导项目根目录
        # manager.py 位于 {root}/fwsort/strategy/manager.py
        # 所以项目根目录 = manager.py 所在目录的父目录的父目录
        strategy_dir = os.path.dirname(os.path.abspath(__file__))  # {root}/fwsort/strategy
        fwsort_dir = os.path.dirname(strategy_dir)  # {root}/fwsort
        project_root = os.path.dirname(fwsort_dir)  # {root}

        # 从 fwsort 开始拼接相对路径
        try:
            start_idx = parts.index("fwsort")
        except ValueError:
            start_idx = 0
        rel_path = os.path.join(*parts[start_idx:])  # fwsort/strategy/providers/hermes/sftp_strategy
        file_path = os.path.join(project_root, rel_path + ".py")
        if os.path.exists(file_path):
            return file_path
        # 也尝试 __init__.py（包级模块）
        init_path = os.path.join(project_root, rel_path, "__init__.py")
        if os.path.exists(init_path):
            return init_path
    except Exception:
        pass
    return None


def _find_provider_file_by_scan(providers_dir: str, provider_name: str) -> str | None:
    """通过文件系统扫描查找策略文件（不依赖模块导入）

    递归扫描 providers/ 目录下所有 .py 文件，
    读取文件内容查找 name = "{provider_name}" 的类定义。
    """
    # 预编译正则，避免循环内重复编译
    # 模式1: name: str = "value"
    # 模式2: name = "value"
    pattern1 = re.compile(
        r'^\s+name\s*:\s*str\s*=\s*["\']' + re.escape(provider_name) + r'["\']',
        re.MULTILINE
    )
    pattern2 = re.compile(
        r'^\s+name\s*=\s*["\']' + re.escape(provider_name) + r'["\']',
        re.MULTILINE
    )

    for root, dirs, files in os.walk(providers_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fname in files:
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                if pattern1.search(content) or pattern2.search(content):
                    return fpath
            except Exception:
                pass
    return None


# 初始化：自动发现所有策略
_discover_providers()