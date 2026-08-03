"""信号提供者抽象基类 & 统一信号格式定义

所有信号源（随机、外部HTTP、Webhook等）都必须实现 SignalProvider 接口。
统一信号格式：
    - symbol: 标的代码 (如 btc-updown-4h-1785744000)
    - amount: 下单金额 (USDC)
    - direction: 下单方向 (UP/DOWN)
    - source: 信号来源标识
    - timestamp: 信号时间戳
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Literal


Direction = Literal["UP", "DOWN"]


class SignalCategory(str, Enum):
    """信号源类别枚举

    - internal: 内置信号（系统自带，不可删除）
    - external: 外部信号（系统自动发现，不可删除）
    - custom: 自定义信号（用户创建，可删除）
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
    direction: Direction = "UP"
    source: str = "unknown"
    timestamp: int = field(default_factory=lambda: int(time.time()))

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

    Attributes:
        name: 信号源唯一标识
        category: 信号源类别 (internal / external / custom)，默认 custom
    """

    name: str = "base"
    category: str = SignalCategory.CUSTOM.value

    @abstractmethod
    def get_signal(self) -> Signal:
        """获取一个信号

        Returns:
            Signal: 统一格式的信号对象
        """

    def health_check(self) -> dict:
        """信号源健康检查（可选覆盖）"""
        return {"provider": self.name, "category": self.category, "ready": True}