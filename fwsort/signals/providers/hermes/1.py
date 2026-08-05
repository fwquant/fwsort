"""随机信号提供者

生成随机方向的交易信号，标的代码使用 F3 最简类的获得当前时间值逻辑：
    - 格式: btc-updown-4h-{epoch}
    - 时间戳使用 fwsort.gateway.polymarket.F3.最简类_下单代码.获得当前时间值(周期=4h)
    - 下单金额固定为 1 USDC
    - 下单方向随机 UP/DOWN
"""
from __future__ import annotations

import random

from fwsort.signals.base import Direction, Signal, SignalProvider


class RandomSignalProvider(SignalProvider):
    """随机信号提供者

    标的代码格式: btc-updown-4h-{epoch}
    使用 4 小时周期对齐的时间戳
    """

    name: str = "random"
    category: str = "internal"  # 内部信号

    def __init__(self, interval_seconds: int = 4 * 60 * 60, base_symbol: str = "btc-updown-4h", config_json: dict | None = None):
        self.interval_seconds = interval_seconds
        self.base_symbol = base_symbol
        self.config = config_json or {}

    def get_signal(self) -> Signal:
        """生成一个随机信号

        时间戳对齐到 interval_seconds 窗口（与 F3 pm类._get周期 逻辑一致）
        """
        from fwsort.gateway.polymarket.F3.最简类_下单代码 import 获得当前时间值

        epoch = 获得当前时间值(周期=self.interval_seconds)
        direction: Direction = random.choice(["UP", "DOWN"])
        symbol = f"{self.base_symbol}-{epoch}"

        return Signal(
            symbol=symbol,
            amount=1.0,
            direction=direction,
            source=self.name,
            timestamp=int(epoch),
        )

    def health_check(self) -> dict:
        return {
            "provider": self.name,
            "category": self.category,
            "ready": True,
            "base_symbol": self.base_symbol,
            "interval_seconds": self.interval_seconds,
        }