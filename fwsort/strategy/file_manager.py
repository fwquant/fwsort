"""策略文件管理器 - 基于 .py 文件的策略 CRUD

职责：
    - 扫描 providers/ 目录，获取所有策略
    - 读取单个策略的参数元信息
    - 更新策略的参数值（直接修改 .py 文件中的类属性赋值行）
    - 创建新策略（生成 .py 文件）
    - 删除策略（删除 .py 文件）

设计原则：
    - 参数值存储在 .py 文件的类属性默认值中
    - 仅支持 int/float/str/bool 四种参数类型
    - 通过正则替换类属性的赋值行实现参数值更新
"""
from __future__ import annotations

import importlib
import inspect
import os
import re
import sys
from datetime import datetime
from typing import Any

from fwsort.fwlogs import logger

from fwsort.strategy.base import (
    PARAM_TYPE_NAMES,
    SUPPORTED_PARAM_TYPES,
    Signal,
    SignalCategory,
    StrategyBase,
)
from fwsort.strategy.manager import (
    _PROVIDERS,
    _PROVIDER_CATEGORIES,
    get_provider_file_path,
    list_providers,
    register_provider,
    reload_providers,
    reset_provider_instance,
)

# providers 目录路径
_PROVIDERS_DIR = os.path.join(os.path.dirname(__file__), "providers")


def list_all_providers() -> list[dict[str, Any]]:
    """扫描 providers/ 目录，返回所有信号源的元数据列表

    Returns:
        [{
            name, category, class_name, module_path, file_path,
            description, author, version,
            parameters: [...], visible_parameters: [...],
            is_active, can_delete, ...
        }]
    """
    result = []

    # 从运行时注册表获取
    for name, cls in _PROVIDERS.items():
        try:
            provider_info = _build_provider_info(name, cls)
            result.append(provider_info)
        except Exception as e:
            logger.warning(f"[FileManager] failed to build info for {name}: {e}")

    # 按 ID 排序（如果有）
    result.sort(key=lambda x: x.get("name", ""))
    return result


def _build_provider_info(name: str, cls: type[StrategyBase]) -> dict[str, Any]:
    """构建单个信号源的元数据"""
    file_path = get_provider_file_path(name) or ""
    category = _PROVIDER_CATEGORIES.get(name, getattr(cls, "category", "custom"))

    # 获取参数信息
    all_param_info = cls.get_parameter_info()
    visible_param_info = [p for p in all_param_info if p["editable"]]
    visible_param_names = [p["name"] for p in visible_param_info]
    hidden_param_names = [p["name"] for p in all_param_info if not p["editable"]]

    # 构建 parameter_meta: {param_name: {value, type, type_name}}
    parameter_meta = {}
    for p in all_param_info:
        parameter_meta[p["name"]] = {
            "value": p["value"],
            "type": p["type"],
            "type_name": p["type_name"],
        }

    # 判断是否可删除：custom 类别可删除
    can_delete = category == SignalCategory.CUSTOM.value

    display_name = getattr(cls, "display_name", "") or ""

    return {
        "provider_name": name,
        "name": name,  # 兼容旧字段
        "display_name": display_name or name,
        "category": category,
        "category_display": SignalCategory.display_names().get(category, category),
        "class_name": cls.__name__,
        "module_path": cls.__module__,
        "file_path": file_path,
        "description": getattr(cls, "description", ""),
        "author": getattr(cls, "author", ""),
        "version": getattr(cls, "version", "1.0.0"),
        "parameters": visible_param_names,
        "hidden_parameters": hidden_param_names,
        "parameter_meta": parameter_meta,
        "is_active": True,
        "can_delete": can_delete,
        "is_builtin": not can_delete,
        "health_status": "unknown",
    }


def get_provider_detail(provider_name: str) -> dict[str, Any] | None:
    """获取单个信号源的详细信息"""
    cls = _PROVIDERS.get(provider_name)
    if not cls:
        return None
    return _build_provider_info(provider_name, cls)


def get_provider_parameters(provider_name: str) -> list[dict[str, Any]]:
    """获取信号源的可见参数列表（供前端表单渲染）"""
    cls = _PROVIDERS.get(provider_name)
    if not cls:
        return []
    return cls.get_visible_parameter_info()


def update_provider_values(provider_name: str, values: dict[str, Any]) -> dict[str, Any]:
    """更新信号源的参数值（修改 .py 文件中的类属性默认值）

    Args:
        provider_name: 信号源名称
        values: {param_name: new_value} 字典

    Returns:
        更新后的参数值字典

    Raises:
        ValueError: 参数不存在、类型不匹配、文件不存在等
    """
    cls = _PROVIDERS.get(provider_name)
    if not cls:
        raise ValueError(f"信号源不存在: {provider_name}")

    file_path = get_provider_file_path(provider_name)
    if not file_path or not os.path.exists(file_path):
        raise ValueError(f"信号源文件不存在: {file_path}")

    # 验证参数名和类型
    valid_names = set(cls.get_all_parameter_names())
    validated_values = {}
    for param_name, new_value in values.items():
        if param_name not in valid_names:
            raise ValueError(f"未知参数: {param_name}，有效参数: {list(valid_names)}")
        old_value = getattr(cls, param_name)
        target_type = type(old_value).__name__
        try:
            coerced = StrategyBase.coerce_param_value(new_value, target_type)
            is_valid, err = StrategyBase.validate_param_value(coerced)
            if not is_valid:
                raise ValueError(err)
            validated_values[param_name] = coerced
        except (ValueError, TypeError) as e:
            raise ValueError(f"参数 {param_name} 类型错误: {e}，期望 {target_type}")

    # 读取文件内容
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 替换类属性赋值行
    for param_name, new_value in validated_values.items():
        old_value = getattr(cls, param_name)
        content = _replace_class_attr(content, cls.__name__, param_name, old_value, new_value)

    # 写回文件
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info(f"[FileManager] updated provider {provider_name} values: {list(validated_values.keys())}")

    # 重置实例，让新值生效
    reset_provider_instance(provider_name)

    return validated_values


def _replace_class_attr(content: str, class_name: str, attr_name: str, old_value: Any, new_value: Any) -> str:
    """替换 .py 文件中类属性的赋值行

    匹配模式：
        class ClassName(...):
            ...
            attr_name: type = old_value
            attr_name: type = "old_value"
            attr_name: type = 'old_value'
            attr_name = old_value

    Args:
        content: 文件内容
        class_name: 类名
        attr_name: 属性名
        old_value: 旧值
        new_value: 新值

    Returns:
        修改后的文件内容
    """
    # 构建匹配模式：类属性定义行
    # 支持: attr_name: type = value  或  attr_name = value
    # 值可以是: 数字, 字符串(单引号/双引号), 布尔, 列表等

    def _format_value(value: Any) -> str:
        """将值格式化为 Python 字面量"""
        if isinstance(value, bool):
            return "True" if value else "False"
        elif isinstance(value, int):
            return str(value)
        elif isinstance(value, float):
            return str(value)
        elif isinstance(value, str):
            # 转义字符串中的特殊字符
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'
        elif isinstance(value, list):
            return "[" + ", ".join(_format_value(v) for v in value) + "]"
        else:
            return repr(value)

    def _get_type_annotation(value: Any) -> str:
        """获取类型注解"""
        if isinstance(value, bool):
            return "bool"
        elif isinstance(value, int):
            return "int"
        elif isinstance(value, float):
            return "float"
        elif isinstance(value, str):
            return "str"
        elif isinstance(value, list):
            return "list[str]"
        return ""

    def _match_and_replace(match: re.Match) -> str:
        indent = match.group(1)
        attr = match.group(2)
        type_annotation = match.group(4)
        # 保留原有的类型注解，如果没有则根据新值推断
        if not type_annotation:
            type_annotation = _get_type_annotation(new_value)
        new_val_str = _format_value(new_value)
        if type_annotation:
            return f"{indent}{attr}: {type_annotation} = {new_val_str}"
        else:
            return f"{indent}{attr} = {new_val_str}"

    # 匹配模式：缩进 + 属性名 + 可选类型注解 + = + 值
    # 捕获组: (缩进)(属性名)(空格)(类型注解)
    pattern = re.compile(
        r'^(\s+)(' + re.escape(attr_name) + r')\s*(?::\s*(\w+(?:\[.*?\])?))?\s*=\s*.+$',
        re.MULTILINE
    )

    # 只替换第一个匹配（类属性定义）
    new_content = pattern.sub(_match_and_replace, content, count=1)

    if new_content == content:
        logger.warning(f"[FileManager] no match for class attr {attr_name} in {class_name}")

    return new_content


def create_provider(
    provider_name: str,
    class_name: str | None = None,
    category: str = "custom",
    description: str = "",
    http_url: str | None = None,
    config_template: dict | None = None,
) -> dict[str, Any]:
    """创建新信号源（生成 .py 文件）

    Args:
        provider_name: 信号源名称
        class_name: Python 类名（可选，自动生成）
        category: 类别 (custom/internal/external)
        description: 描述
        http_url: HTTP URL（HTTP 类型信号源）
        config_template: 配置模板

    Returns:
        创建的信号源信息
    """
    from datetime import timedelta

    # 生成名称和类名
    if not provider_name:
        provider_name = f"signal_{datetime.now().strftime('%Y%m%d%H%M')}"
    if not class_name:
        parts = provider_name.split("_")
        class_name = "".join(p.capitalize() for p in parts) + "Provider"

    # 检查是否已存在
    if provider_name in _PROVIDERS:
        raise ValueError(f"信号源已存在: {provider_name}")

    # 生成文件
    file_stem = re.sub(r'[^a-zA-Z0-9_]', '_', provider_name.lower())
    file_name = f"{file_stem}.py"
    file_path = os.path.join(_PROVIDERS_DIR, file_name)

    if os.path.exists(file_path):
        raise ValueError(f"文件已存在: {file_path}")

    if http_url:
        code = _build_http_provider_code(provider_name, class_name, category, description or "", http_url)
    else:
        code = _build_python_provider_code(provider_name, class_name, category, description or "", config_template)

    os.makedirs(_PROVIDERS_DIR, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(code)

    module_path = f"fwsort.strategy.providers.{file_stem}"
    logger.info(f"[FileManager] created provider file: {file_path}")

    # 注册到系统
    try:
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        if isinstance(cls, type) and issubclass(cls, StrategyBase):
            register_provider(provider_name, cls, category=category)
    except Exception as e:
        logger.warning(f"[FileManager] failed to register new provider: {e}")

    return {
        "name": provider_name,
        "class_name": class_name,
        "module_path": module_path,
        "file_path": file_path,
        "category": category,
    }


def delete_provider(provider_name: str) -> bool:
    """删除信号源（删除 .py 文件）

    仅 custom 类别可删除。

    Returns:
        是否删除成功
    """
    cls = _PROVIDERS.get(provider_name)
    if not cls:
        raise ValueError(f"信号源不存在: {provider_name}")

    category = _PROVIDER_CATEGORIES.get(provider_name, getattr(cls, "category", "custom"))
    if category != SignalCategory.CUSTOM.value:
        raise ValueError(f"{SignalCategory.display_names().get(category, category)}信号源不可删除")

    file_path = get_provider_file_path(provider_name)
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
            logger.info(f"[FileManager] deleted provider file: {file_path}")
        except Exception as e:
            logger.warning(f"[FileManager] failed to delete file: {e}")
            raise

    reset_provider_instance(provider_name)
    # 从注册表移除
    if provider_name in _PROVIDERS:
        del _PROVIDERS[provider_name]
    if provider_name in _PROVIDER_CATEGORIES:
        del _PROVIDER_CATEGORIES[provider_name]

    return True


def toggle_provider(provider_name: str, is_active: bool) -> dict[str, Any]:
    """切换信号源启用状态

    由于新架构中文件存在即代表活跃，停用意味着从注册表移除。
    启用则重新加载。
    """
    if is_active:
        # 重新加载
        reload_providers()
    else:
        # 从注册表移除（文件保留）
        if provider_name in _PROVIDERS:
            del _PROVIDERS[provider_name]
        if provider_name in _PROVIDER_CATEGORIES:
            del _PROVIDER_CATEGORIES[provider_name]
        reset_provider_instance(provider_name)

    return {"is_active": is_active}


def run_health_check(provider_name: str) -> dict:
    """执行信号源健康检查"""
    cls = _PROVIDERS.get(provider_name)
    if not cls:
        return {"error": "not found"}

    try:
        provider = cls()
        health = provider.health_check()
        return health
    except Exception as e:
        return {"ready": False, "error": str(e)}


def test_provider(provider_name: str) -> dict:
    """测试信号生成"""
    cls = _PROVIDERS.get(provider_name)
    if not cls:
        return {"error": "not found"}

    try:
        provider = cls()
        signal = provider.get_signal()
        return {"success": True, "signal": signal.to_dict()}
    except Exception as e:
        return {"success": False, "error": str(e)}


def open_provider_file(provider_name: str) -> dict:
    """用默认 IDE 打开信号源文件"""
    file_path = get_provider_file_path(provider_name)
    if not file_path or not os.path.exists(file_path):
        return {"success": False, "error": f"未找到文件: {file_path}"}

    try:
        if sys.platform == "win32":
            os.startfile(file_path)
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", file_path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", file_path])
        return {"success": True, "file_path": file_path}
    except Exception as e:
        return {"success": False, "error": str(e)}


def hot_reload() -> dict:
    """热加载：重新扫描 providers/ 目录

    Returns:
        reload_providers() 的结果
    """
    result = reload_providers()
    logger.info(f"[FileManager] hot reload: {result}")
    return result


def check_provider_references(provider_name: str) -> list[dict]:
    """检查策略被哪些任务引用"""
    from fwsort.database import get_sync_db
    from fwsort.models import AutoStrategy

    with get_sync_db() as db:
        tasks = db.query(AutoStrategy).filter(
            AutoStrategy.signal_source == provider_name,
            AutoStrategy.deleted_at.is_(None),
        ).all()
        return [{"task_id": t.id, "task_name": t.task_name, "is_active": t.is_active} for t in tasks]


# ========== 代码生成模板 ==========

def _build_python_provider_code(
    provider_name: str,
    class_name: str,
    category: str,
    description: str,
    config_template: dict | None = None,
) -> str:
    """生成 Python 自定义策略代码"""
    category_cn = SignalCategory.display_names().get(category, category)
    config_default = config_template or {}

    return f'''"""自定义策略: {provider_name}

类别: {category_cn} ({category})
创建时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

使用说明：
    1. 在 parameters 列表中声明显示参数名
    2. 在类属性中定义参数默认值
    3. Web 界面会自动根据 parameters 列表生成编辑表单
    4. 修改参数值会直接修改本文件中的默认值
"""
from __future__ import annotations

import random
import time

from fwsort.strategy.base import Direction, Signal, StrategyBase


class {class_name}(StrategyBase):
    """{provider_name} 策略

    继承 StrategyBase，实现自定义信号逻辑。
    """

    name: str = "{provider_name}"
    category: str = "{category}"
    description: str = "{description}"

    # 显示参数（Web 可编辑，添加你需要的参数名）
    parameters = ["amount"]
    # 隐藏参数
    hidden_parameters: list[str] = []

    # 参数默认值（会被 Web 界面修改）
    amount: float = 1.0

    def get_signal(self) -> Signal:
        """获取信号

        在这里实现你的信号生成逻辑。
        示例：随机方向 + 基于时间的标的代码
        """
        epoch = str(((int(time.time()) // (4 * 60 * 60)) * (4 * 60 * 60)))
        direction: Direction = random.choice(["UP", "DOWN"])
        symbol = f"btc-updown-4h-{{epoch}}"

        return Signal(
            symbol=symbol,
            amount=self.amount,
            direction=direction,
            source=self.name,
            timestamp=int(epoch),
        )

    def health_check(self) -> dict:
        """健康检查（可选覆盖）"""
        return {{
            "provider": self.name,
            "category": self.category,
            "ready": True,
        }}
'''


def _build_http_provider_code(
    provider_name: str,
    class_name: str,
    category: str,
    description: str,
    http_url: str,
) -> str:
    """生成 HTTP URL 策略代码"""
    category_cn = SignalCategory.display_names().get(category, category)

    return f'''"""HTTP URL 策略: {provider_name}

类别: {category_cn} ({category})
创建时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
HTTP URL: {http_url}

使用说明：
    1. 该策略会请求配置的 HTTP URL
    2. URL 返回 JSON，需包含: symbol, direction, amount, timestamp
    3. 如果返回格式不符，会使用默认值
    4. 如需自定义解析逻辑，可编辑此文件
"""
from __future__ import annotations

import json
import time
from urllib.request import urlopen, Request

from fwsort.strategy.base import Direction, Signal, StrategyBase


class {class_name}(StrategyBase):
    """HTTP URL 策略

    请求 HTTP URL 获取 JSON 信号数据。

    参数：
        - http_url: HTTP 请求地址
    """

    name: str = "{provider_name}"
    category: str = "{category}"
    description: str = "{description}"

    # 显示参数
    parameters = ["http_url"]
    # 隐藏参数
    hidden_parameters = ["timeout"]

    # 参数默认值
    http_url: str = "{http_url}"
    timeout: int = 10

    def get_signal(self) -> Signal:
        """从 HTTP URL 获取信号

        请求配置的 URL，解析 JSON 返回 Signal 对象。
        """
        try:
            req = Request(
                self.http_url,
                headers={{"User-Agent": "fwsort-signal/1.0"}}
            )
            with urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            symbol = data.get("symbol", f"btc-updown-4h-{{int(time.time())}}")
            direction = data.get("direction", "UP")
            if direction not in ("UP", "DOWN"):
                direction = "UP"
            amount = float(data.get("amount", 1.0))
            timestamp = int(data.get("timestamp", int(time.time())))

            return Signal(
                symbol=symbol,
                amount=amount,
                direction=direction,
                source=self.name,
                timestamp=timestamp,
            )
        except Exception as e:
            from fwsort.fwlogs import logger
            logger.warning(f"[{{self.name}}] HTTP request failed: {{e}}, returning default signal")
            return Signal(
                symbol=f"btc-updown-4h-{{int(time.time())}}",
                amount=1.0,
                direction="UP",
                source=self.name,
            )

    def health_check(self) -> dict:
        """健康检查 - 测试 URL 是否可达"""
        try:
            req = Request(
                self.http_url,
                headers={{"User-Agent": "fwsort-health-check/1.0"}}
            )
            with urlopen(req, timeout=self.timeout) as resp:
                return {{
                    "provider": self.name,
                    "category": self.category,
                    "ready": True,
                    "http_url": self.http_url,
                    "status_code": resp.status,
                }}
        except Exception as e:
            return {{
                "provider": self.name,
                "category": self.category,
                "ready": False,
                "http_url": self.http_url,
                "error": str(e),
            }}
'''
