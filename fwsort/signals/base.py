"""信号提供者抽象基类 & 统一信号格式定义

所有信号源（随机、外部HTTP、Webhook等）都必须实现 SignalProvider 接口。
统一信号格式：
    - symbol: 标的代码 (如 btc-updown-4h-1785744000)
    - amount: 下单金额 (USDC)
    - direction: 下单方向 (UP/DOWN)
    - source: 信号来源标识
    - timestamp: 信号时间戳

参数声明机制：
    - parameters: 显示参数名列表（Web 界面可编辑）
    - hidden_parameters: 隐藏参数名列表（有默认值但 Web 不显示）
    - 支持类型: int, float, str, bool
    - 参数值存储在 .py 文件的类属性默认值中
"""
from __future__ import annotations

import inspect
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Literal


Direction = Literal["UP", "DOWN", ""]

# 支持的参数类型
SUPPORTED_PARAM_TYPES = (int, float, str, bool)

# 类型名称映射
PARAM_TYPE_NAMES = {
    "int": "整数",
    "float": "小数",
    "str": "字符串",
    "bool": "布尔",
}


class SignalCategory(str, Enum):
    """信号源类别枚举

    - internal: 内置信号（系统自带）
    - external: 外部信号（系统自动发现）
    - custom: 自定义信号（用户创建）
    """
    INTERNAL = "internal"
    EXTERNAL = "external"
    CUSTOM = "custom"

    @classmethod
    def display_names(cls) -> dict[str, str]:
        return {
            "internal": "内部信号",
            "external": "外部信号",
            "custom": "自定义信号",
        }


@dataclass
class Signal:
    """统一信号对象"""

    symbol: str
    amount: float = 1.0
    direction: Direction = ""
    source: str = "unknown"
    timestamp: int = field(default_factory=lambda: int(time.time()))

    @property
    def is_valid(self) -> bool:
        """是否为有效交易信号（direction 为 UP 或 DOWN）"""
        return self.direction in ("UP", "DOWN")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Signal:
        return cls(
            symbol=data.get("symbol", ""),
            amount=float(data.get("amount", 1.0)),
            direction=data.get("direction", "UP"),
            source=data.get("source", "unknown"),
            timestamp=int(data.get("timestamp", int(time.time()))),
        )


class SignalProvider(ABC):
    """信号提供者抽象基类

    所有信号源必须实现 get_signal() 方法。

    元信息（可被子类覆盖）：
        name: 信号源唯一标识
        category: 信号源类别 (internal / external / custom)
        description: 信号源描述
        author: 作者
        version: 版本号

    参数声明：
        parameters: 显示参数名列表（Web 界面生成表单让用户编辑）
        hidden_parameters: 隐藏参数名列表（有默认值但不在 Web 显示）

    参数类型：
        仅支持 int, float, str, bool 四种类型。
        在 Web 界面编辑时会自动根据类型生成输入控件。
    """

    # === 元信息 ===
    name: str = "base"
    category: str = SignalCategory.CUSTOM.value
    description: str = ""
    author: str = ""
    version: str = "1.0.0"

    # === 参数声明 ===
    parameters: list[str] = []
    hidden_parameters: list[str] = []

    @abstractmethod
    def get_signal(self) -> Signal:
        """获取一个信号

        Returns:
            Signal: 统一格式的信号对象
        """

    def health_check(self) -> dict:
        """信号源健康检查（可选覆盖）"""
        return {"provider": self.name, "category": self.category, "ready": True}

    # ========== 参数元信息查询 ==========

    @classmethod
    def get_all_parameter_names(cls) -> list[str]:
        """返回所有参数名（显示 + 隐藏）"""
        return cls.parameters + cls.hidden_parameters

    @classmethod
    def get_visible_parameter_names(cls) -> list[str]:
        """返回显示参数名列表"""
        return cls.parameters.copy()

    @classmethod
    def get_parameter_info(cls) -> list[dict[str, Any]]:
        """返回所有参数的元信息

        Returns:
            list of {name, value, type, type_name, editable}
        """
        result = []
        all_names = cls.parameters + cls.hidden_parameters
        for name in all_names:
            if hasattr(cls, name):
                value = getattr(cls, name)
                type_obj = type(value)
                type_name = type_obj.__name__
                editable = name in cls.parameters
                if type_obj not in SUPPORTED_PARAM_TYPES:
                    continue
                result.append({
                    "name": name,
                    "value": value,
                    "type": type_name,
                    "type_name": PARAM_TYPE_NAMES.get(type_name, type_name),
                    "editable": editable,
                })
        return result

    @classmethod
    def get_visible_parameter_info(cls) -> list[dict[str, Any]]:
        """仅返回显示参数的元信息"""
        return [p for p in cls.get_parameter_info() if p["editable"]]

    # ========== 参数值类型验证 ==========

    @staticmethod
    def validate_param_value(value: Any) -> tuple[bool, str]:
        """验证参数值是否为支持的类型

        Returns:
            (is_valid, error_message)
        """
        if isinstance(value, bool):
            return True, ""
        if isinstance(value, (int, float, str)):
            return True, ""
        return False, f"不支持的参数类型: {type(value).__name__}，仅支持 int/float/str/bool"

    @staticmethod
    def coerce_param_value(value: Any, target_type: str) -> Any:
        """将值转换为目标类型

        Args:
            value: 原始值（通常来自前端表单的字符串）
            target_type: 目标类型名 (int/float/str/bool)

        Returns:
            转换后的值

        Raises:
            ValueError: 转换失败
        """
        if target_type == "int":
            return int(value)
        elif target_type == "float":
            return float(value)
        elif target_type == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes", "on")
            return bool(value)
        elif target_type == "str":
            return str(value)
        else:
            raise ValueError(f"未知的参数类型: {target_type}")

    # ========== 类元数据（供前端查询） ==========

    @classmethod
    def get_provider_metadata(cls) -> dict[str, Any]:
        """返回供前端展示的完整元数据

        Returns:
            {
                name, category, description, author, version,
                parameters: [{name, value, type, type_name, editable}],
                all_parameter_names: [...]
            }
        """
        return {
            "name": cls.name,
            "category": cls.category,
            "description": cls.description,
            "author": cls.author,
            "version": cls.version,
            "parameters": cls.get_parameter_info(),
            "visible_parameters": cls.get_visible_parameter_info(),
            "all_parameter_names": cls.get_all_parameter_names(),
            "has_visible_params": len(cls.parameters) > 0,
        }