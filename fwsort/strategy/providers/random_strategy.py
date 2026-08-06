"""随机策略

生成随机方向的交易信号，标的代码使用 F3 最简类的获得当前时间值逻辑：
    - 格式: btc-updown-4h-{epoch}
    - 时间戳使用 fwsort.gateway.polymarket.F3.最简类_下单代码.获得当前时间值(周期=4h)
    - 下单金额默认 1 USDC（可在 Web 界面修改）
    - 下单方向随机 UP/DOWN

参数说明：
    - interval_seconds (int): 周期秒数，默认 14400 (4小时)
    - amount (float): 下单金额，默认 1.0
    - base_symbol (str): 标的前缀（隐藏参数）
"""
from __future__ import annotations

import random

from fwsort.strategy.base import Direction, Signal, StrategyBase


class RandomStrategy(StrategyBase):
    """随机策略

    标的代码格式: btc-updown-4h-{epoch}
    使用 4 小时周期对齐的时间戳

    参数：
        - interval_seconds: 周期秒数
        - amount: 下单金额
    """

    name: str = "random"
    category: str = "internal"
    description: str = "随机方向信号源，用于测试"

    # 显示参数（Web 可编辑）
    parameters = ["interval_seconds", "amount"]
    # 隐藏参数
    hidden_parameters = ["base_symbol"]

    # 参数默认值（会被 Web 界面修改）
    interval_seconds: int = 14400
    amount: float = 1.0
    base_symbol: str = "btc-updown-4h"

    def __init__(self, config_json: dict | None = None):
        self.config = config_json or {}
        if self.config:
            self.interval_seconds = self.config.get("interval_seconds", self.interval_seconds)
            self.amount = self.config.get("amount", self.amount)
            self.base_symbol = self.config.get("base_symbol", self.base_symbol)

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
            amount=self.amount,
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
            "amount": self.amount,
        }
