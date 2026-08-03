"""自定义信号源: signal_202608031908

类别: 自定义信号 (custom)
创建时间: 2026-08-03 19:08:48

使用说明：
    1. 实现 get_signal() 方法返回 Signal 对象
    2. 可选覆盖 health_check() 方法
    3. 修改后在 Admin 面板点击"🔄 刷新"热加载

标的代码格式: btc-updown-4h-{epoch}
下单金额: 固定 1 USDC
下单方向: UP / DOWN
"""
from __future__ import annotations

import random
import time

from fwsort.signals.base import Direction, Signal, SignalProvider


class Signal202608031908Provider(SignalProvider):
    """signal_202608031908 信号提供者

    继承 SignalProvider，实现自定义信号逻辑。
    """

    name: str = "signal_202608031908"
    category: str = "custom"  # 自定义信号

    def __init__(self, config_json: dict | None = None):
        self.config = config_json or {}

    def get_signal(self) -> Signal:
        """获取信号

        在这里实现你的信号生成逻辑。
        示例：随机方向 + 基于时间的标的代码
        """
        epoch = str(((int(time.time()) // (4 * 60 * 60)) * (4 * 60 * 60)))
        direction: Direction = random.choice(["UP", "DOWN"])
        symbol = f"btc-updown-4h-{epoch}"

        return Signal(
            symbol=symbol,
            amount=1.0,
            direction=direction,
            source=self.name,
            timestamp=int(epoch),
        )

    def health_check(self) -> dict:
        """健康检查（可选覆盖）"""
        return {
            "provider": self.name,
            "category": self.category,
            "ready": True,
        }
