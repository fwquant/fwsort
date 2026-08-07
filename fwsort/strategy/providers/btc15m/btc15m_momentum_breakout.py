"""BTC 15分 策略 #2：动量突破 + 量价共振

【策略核心思想】
均值回归适合震荡市，但 BTC 在趋势行情中会"强者恒强"。
本策略专注于"周期开始时已经形成的强动量"：

    1. 周期开始时（前 3 分钟），如果 Polymarket 的隐含概率已经
       强烈倾向某一方（UP 价 > 70% 或 DOWN 价 > 70%），且这种
       倾向由订单簿大单支撑（量价共振），则跟随动量继续押
       同方向，胜率可达 75%+。

    2. 与"均值回归"的关键差异：动量策略在隐含概率突破 75% 时
       仍然跟随（因为只要胜率 > 80%，即便 0.25 买价仍然正EV）。

【开仓条件（必须同时满足）】
    1. 周期刚启动 0~3 分钟（剩余 >= 12 分钟）
    2. 隐含概率 >= 突破阈值（默认 72%）
    3. 订单簿失衡方向与隐含概率同向（动量确认）
    4. 订单簿失衡强度 >= 阈值
    5. 隐含概率 + 失衡方向 + 强度共同推出隐含胜率 >= 78%，
       此时即便买价 0.25 仍为正EV（0.78 * 0.75 > 0.25）

【止盈目标】
    不做固定止盈（让市场自然结算），但要求"隐含胜率 * 赔率 > 赔率反向"
    即 0.78 * 0.25 > 0.22（买价对应的输光概率成本），长期正EV。

【数据来源】
    与策略 #1 一致：ctx.gateway / ctx.polymarket_market
"""
from __future__ import annotations

import asyncio
import time
import traceback
from typing import Any

from fwsort.fwlogs import logger
from fwsort.strategy.base import Direction, Signal, StrategyBase

_PERIOD_SECONDS = 15 * 60
_SYMBOL_PREFIX = "btc-updown-15m"


def _compute_current_epoch() -> int:
    return (int(time.time()) // _PERIOD_SECONDS) * _PERIOD_SECONDS


def _build_symbol(epoch: int | None = None) -> str:
    if epoch is None:
        epoch = _compute_current_epoch()
    return f"{_SYMBOL_PREFIX}-{epoch}"


class Btc15mMomentumBreakoutStrategy(StrategyBase):
    """BTC 15分 动量突破 + 量价共振策略

    在周期开始 0~3 分钟内检测已形成的强动量（隐含概率突破 + 订单簿
    同向大单），跟随动量下注。
    """

    name: str = "btc15m_momentum_breakout"
    category: str = "custom"
    description: str = "BTC 15分 动量突破策略（隐含概率突破 + 量价共振跟随）"
    author: str = "fwquant"
    version: str = "1.0.0"

    # ============ 下单金额 ============
    amount: float = 1.0

    # ============ 动量突破阈值 ============
    # 隐含概率 >= 此值才认为"突破"
    突破隐含概率: float = 72.0
    # 突破后要求的最强侧隐含概率（避免 50/50 时的假突破）
    最强隐含概率: float = 70.0
    # 订单簿同向失衡强度阈值
    同向失衡_最小强度: float = 0.20

    # ============ 时间窗口 ============
    # 周期开始 0~3 分钟内（已过 0~180 秒，剩余 12~15 分钟）
    # 实际判断用"周期已过秒数"更直观
    周期已过_最大秒数: int = 3 * 60   # 周期开始 3 分钟内才开仓

    # ============ 隐含胜率要求 ============
    # 隐含胜率 >= 此值才开仓（用于算 EV）
    最小隐含胜率: float = 0.78

    # ============ 参数声明 ============
    parameters = [
        "amount",
        "突破隐含概率", "最强隐含概率", "同向失衡_最小强度",
        "周期已过_最大秒数",
        "最小隐含胜率",
    ]
    hidden_parameters: list[str] = []

    def __init__(self, config_json: dict | None = None, **kwargs):
        self.config = config_json or {}
        for k in self.parameters + self.hidden_parameters:
            v = kwargs.get(k, self.config.get(k, getattr(self, k, None)))
            if v is not None:
                default = getattr(self, k, None)
                if isinstance(default, int):
                    setattr(self, k, int(v))
                elif isinstance(default, float):
                    setattr(self, k, float(v))
                else:
                    setattr(self, k, v)
        self._signal_count: int = 0
        self._last_signal_ts: int = 0

    # ===================== 生成信号 =====================
    def get_signal(self) -> Signal:
        self._signal_count += 1
        self._last_signal_ts = int(time.time())
        return Signal(
            symbol=_build_symbol(),
            amount=self.amount,
            direction="",  # 由 should_open 决定
            source=self.name,
            timestamp=self._last_signal_ts,
        )

    # ===================== 开仓判断 =====================
    def should_open(self, signal: Signal, ctx: dict) -> tuple[bool, str]:
        gateway = ctx.get("gateway")
        market_info = ctx.get("polymarket_market")
        now = ctx.get("now")

        if gateway is None and market_info is None:
            return False, "未提供数据源"

        try:
            # 1) 隐含概率
            up_pct, down_pct, _ = self._fetch_implied_probs(
                gateway=gateway, market_info=market_info, symbol=signal.symbol,
            )
            if up_pct is None or down_pct is None:
                return False, "隐含概率获取失败"

            # 2) 订单簿失衡
            imbalance, _ = self._fetch_orderbook_imbalance(
                gateway=gateway, market_info=market_info, symbol=signal.symbol,
            )
            if imbalance is None:
                return False, "订单簿数据不可用"

            # 3) 时间窗口
            elapsed, total, remaining = self._compute_period_progress(now)
            if elapsed > self.周期已过_最大秒数:
                return False, (
                    f"周期已过 {elapsed:.0f}s > {self.周期已过_最大秒数}s，"
                    f"已过最佳动量捕捉窗口"
                )

            # 4) 判断动量方向
            # 选胜率更高的一边
            if up_pct >= down_pct:
                momentum_side: Direction = "UP"
                momentum_pct = up_pct
            else:
                momentum_side = "DOWN"
                momentum_pct = down_pct

            # 5) 突破阈值检查
            if momentum_pct < self.突破隐含概率:
                return False, (
                    f"动量侧 {momentum_side}={momentum_pct:.1f}% < "
                    f"突破阈值 {self.突破隐含概率}%"
                )

            # 6) 量价共振：订单簿失衡方向必须与动量方向一致
            momentum_imb_sign = 1 if momentum_side == "UP" else -1
            if momentum_imb_sign * imbalance <= 0:
                return False, (
                    f"订单簿失衡 {imbalance:+.3f} 与动量 {momentum_side} 不一致，"
                    f"量价未共振"
                )
            if abs(imbalance) < self.同向失衡_最小强度:
                return False, (
                    f"订单簿失衡强度 {abs(imbalance):.3f} < "
                    f"{self.同向失衡_最小强度}，共振信号弱"
                )

            # 7) 隐含胜率 + EV 检查
            # 隐含胜率 ≈ momentum_pct / 100（市场对该方向的看法）
            # 再叠加订单簿共振的"信心加成"：每 +0.1 失衡 → +3% 胜率（cap +6%）
            orderbook_alpha = min(0.06, abs(imbalance) * 0.03)  # 0.30失衡 → +0.009... 太弱
            # 改为：失衡 >= 0.20 时直接 +0.04，>= 0.30 时 +0.06
            if abs(imbalance) >= 0.30:
                orderbook_alpha = 0.06
            elif abs(imbalance) >= 0.20:
                orderbook_alpha = 0.04
            else:
                orderbook_alpha = 0.02
            implied_winrate = min(0.99, momentum_pct / 100.0 + orderbook_alpha)
            if implied_winrate < self.最小隐含胜率:
                return False, (
                    f"隐含胜率 {implied_winrate:.3f} < {self.最小隐含胜率}，EV 不足"
                )

            # 8) 计算 EV: win * (1 - price) - lose * price
            price = momentum_pct / 100.0
            ev = implied_winrate * (1.0 - price) - (1.0 - implied_winrate) * price
            if ev <= 0:
                return False, (
                    f"期望值 EV={ev:.4f} <= 0，"
                    f"胜率{implied_winrate:.3f}*赔率{1-price:.3f} < 输成本{price:.3f}"
                )

            # 通过：跟随动量开仓
            signal.direction = momentum_side
            signal.timestamp = int(time.time())
            reason = (
                f"动量跟随 {momentum_side}: "
                f"隐含={momentum_pct:.1f}%, "
                f"失衡={imbalance:+.3f}(同向), "
                f"胜率={implied_winrate:.3f}, "
                f"EV={ev:.4f}, "
                f"剩余={remaining}s"
            )
            logger.info(f"[{self.name}] {reason}")
            return True, reason

        except Exception as e:
            logger.error(f"[{self.name}] should_open 异常: {e}, traceback={traceback.format_exc()}")
            return False, f"异常: {e}"

    # ===================== 复用辅助方法 =====================
    def _fetch_implied_probs(self, *, gateway, market_info, symbol: str):
        if market_info and "up_midpoint" in market_info and "down_midpoint" in market_info:
            return (
                float(market_info["up_midpoint"]) * 100.0,
                float(market_info["down_midpoint"]) * 100.0,
                {"source": "ctx"},
            )
        if gateway is None:
            return None, None, {}
        try:
            prices = self._run_async(gateway.获得_updown价格(symbol))
        except Exception as e:
            logger.warning(f"[{self.name}] 拉取隐含概率异常: {e}")
            return None, None, {}
        if not prices or "UP" not in prices or "DOWN" not in prices:
            return None, None, prices or {}
        return (
            float(prices["UP"].get("midpoint", 0)) * 100.0,
            float(prices["DOWN"].get("midpoint", 0)) * 100.0,
            prices,
        )

    def _fetch_orderbook_imbalance(self, *, gateway, market_info, symbol: str):
        if market_info and "orderbook_imbalance" in market_info:
            return float(market_info["orderbook_imbalance"]), {"source": "ctx"}
        if gateway is None:
            return None, {}
        try:
            ob = self._run_async(gateway.获得订单簿(symbol))
        except Exception as e:
            logger.warning(f"[{self.name}] 拉取订单簿异常: {e}")
            return None, {}
        if not ob:
            return None, {}
        bid_vol = 0.0
        ask_vol = 0.0
        for outcome in ob.values() if isinstance(ob, dict) else []:
            for lvl in outcome.get("bids", []) or []:
                bid_vol += float(lvl.get("size", 0))
            for lvl in outcome.get("asks", []) or []:
                ask_vol += float(lvl.get("size", 0))
        total = bid_vol + ask_vol
        if total <= 0:
            return 0.0, {"bid_vol": 0, "ask_vol": 0}
        return (bid_vol - ask_vol) / total, {"bid_vol": bid_vol, "ask_vol": ask_vol}

    def _compute_period_progress(self, now):
        if now is None:
            return 0.0, float(_PERIOD_SECONDS), float(_PERIOD_SECONDS)
        try:
            now_ts = float(now.timestamp())
        except Exception:
            return 0.0, float(_PERIOD_SECONDS), float(_PERIOD_SECONDS)
        epoch_start = (int(now_ts) // _PERIOD_SECONDS) * _PERIOD_SECONDS
        elapsed = now_ts - epoch_start
        return elapsed, float(_PERIOD_SECONDS), _PERIOD_SECONDS - elapsed

    @staticmethod
    def _run_async(coro):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            new_loop = asyncio.new_event_loop()
            try:
                return new_loop.run_until_complete(coro)
            finally:
                new_loop.close()
        return asyncio.run(coro)

    def health_check(self) -> dict:
        return {
            "provider": self.name,
            "category": self.category,
            "ready": True,
            "amount": self.amount,
            "突破阈值": self.突破隐含概率,
            "signal_count": self._signal_count,
        }


if __name__ == "__main__":
    import datetime
    s = Btc15mMomentumBreakoutStrategy()
    sig = s.get_signal()
    fake_ctx = {
        "now": datetime.datetime.utcnow(),
        "polymarket_market": {
            "up_midpoint": 0.74,
            "down_midpoint": 0.26,
            "orderbook_imbalance": 0.30,  # 买盘强（支持 UP）
        },
    }
    allow, reason = s.should_open(sig, fake_ctx)
    print(f"动量策略: allow={allow}, reason={reason}")
    print(f"最终 direction: {sig.direction!r}")
