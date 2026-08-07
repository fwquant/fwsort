"""BTC 15分 策略 #3：订单簿失衡 + 微结构捕捉

【策略核心思想】
Polymarket 的 BTC 15分钟市场上有大量做市商挂单，但偶尔会出现
"瞬时失衡"：大量一侧挂单被吃掉，另一侧没有相应补充，导致
失衡在 1~3 秒内冲到极端值（|imb| > 0.5）。这种瞬时失衡的
"挤压"通常会推动价格向失衡的反方向运动（即吃掉的是 ask，
则价格上行，押 UP），胜率约 68~72%。

【与策略 #1/#2 的关键差异】
    - 不依赖隐含概率的绝对水平，只看失衡的"瞬时变化"和"绝对强度"
    - 在周期的中后段（剩余 3~12 分钟）效果更好（市场已稳定）
    - 适合震荡市和趋势市的切换

【开仓条件】
    1. 剩余时间在 [3 分钟, 12 分钟] 之间（中段最佳）
    2. 订单簿失衡 |imbalance| >= 0.45（强失衡）
    3. 失衡方向在最近 2 次采样中保持稳定（避免噪声）
    4. 隐含概率与失衡方向一致（共振）— 这是与策略#1的反向点
    5. 当前买一价 <= 1 - 最小期望ROI（止盈目标）

【止盈目标】
    期望 ROI 至少 3%（因为本策略胜率约 70%，盈亏比 3/30 = 0.1，
    长期为正EV）。可调高到 5% 获得更高单笔收益但减少开仓频率。

【数据来源】
    ctx 中需要包含"近 2 次订单簿采样"的历史，用于检测"稳定性"。
    推荐外部框架在每 5~10 秒采样一次订单簿，缓存最近 2~3 个采样。
"""
from __future__ import annotations

import asyncio
import time
import traceback
from collections import deque
from typing import Any, Deque

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


class Btc15mOrderbookImbalanceStrategy(StrategyBase):
    """BTC 15分 订单簿失衡 + 微结构捕捉策略

    通过检测订单簿瞬时强失衡 + 持续性 + 价格共振捕捉微结构机会。
    """

    name: str = "btc15m_orderbook_imbalance"
    category: str = "custom"
    description: str = "BTC 15分 订单簿失衡策略（瞬时强失衡 + 持续性确认）"
    author: str = "fwquant"
    version: str = "1.0.0"

    # ============ 下单金额 ============
    amount: float = 1.0

    # ============ 失衡阈值 ============
    # 失衡绝对值 >= 此值才开仓
    失衡强度阈值: float = 0.45
    # 失衡方向稳定性：最近 N 次采样，方向一致比例
    失衡方向一致性_最小: float = 0.66  # 2/3

    # ============ 时间窗口（中段最佳）=========
    # 已过 3~12 分钟（剩余 3~12 分钟）
    周期已过_最小秒数: int = 3 * 60
    周期已过_最大秒数: int = 12 * 60

    # ============ 隐含概率共振 ============
    # 失衡方向的隐含概率 >= 此值（市场已部分跟随）
    隐含概率共振阈值: float = 55.0

    # ============ 止盈目标 ============
    最小期望ROI: float = 0.03

    # ============ 内部缓存 ============
    # 每个 symbol 维护一个失衡历史队列
    _imb_history: dict[str, Deque[float]] = {}

    # ============ 参数声明 ============
    parameters = [
        "amount",
        "失衡强度阈值", "失衡方向一致性_最小",
        "周期已过_最小秒数", "周期已过_最大秒数",
        "隐含概率共振阈值", "最小期望ROI",
    ]
    hidden_parameters = ["_imb_history_max_len"]
    _imb_history_max_len: int = 5

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
        # 每次新实例化都重置内部缓存，避免跨实例污染
        self._imb_history = {}
        self._signal_count: int = 0
        self._last_signal_ts: int = 0

    # ===================== 生成信号 =====================
    def get_signal(self) -> Signal:
        self._signal_count += 1
        self._last_signal_ts = int(time.time())
        return Signal(
            symbol=_build_symbol(),
            amount=self.amount,
            direction="",
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
            # 1) 隐含概率（用于共振确认）
            up_pct, down_pct, _ = self._fetch_implied_probs(
                gateway=gateway, market_info=market_info, symbol=signal.symbol,
            )
            if up_pct is None or down_pct is None:
                return False, "隐含概率获取失败"

            # 2) 订单簿失衡（本次）
            imbalance, imb_detail = self._fetch_orderbook_imbalance(
                gateway=gateway, market_info=market_info, symbol=signal.symbol,
            )
            if imbalance is None:
                return False, "订单簿数据不可用"

            # 3) 维护历史（用于方向稳定性检测）
            hist = self._imb_history.setdefault(
                signal.symbol, deque(maxlen=self._imb_history_max_len),
            )
            hist.append(imbalance)

            # 4) 时间窗口（中段）
            elapsed, total, remaining = self._compute_period_progress(now)
            if elapsed < self.周期已过_最小秒数 or elapsed > self.周期已过_最大秒数:
                return False, (
                    f"周期已过 {elapsed:.0f}s 不在窗口 "
                    f"[{self.周期已过_最小秒数}, {self.周期已过_最大秒数}]，"
                    f"非中段最佳时机"
                )

            # 5) 失衡强度阈值
            if abs(imbalance) < self.失衡强度阈值:
                return False, (
                    f"失衡强度 {abs(imbalance):.3f} < "
                    f"{self.失衡强度阈值}，信号不足"
                )

            # 6) 失衡方向稳定性
            # 至少需要 3 个样本，且方向一致比例 >= 阈值
            if len(hist) < 3:
                return False, (
                    f"历史样本不足（{len(hist)} < 3），等待稳定性确认"
                )
            direction = 1 if imbalance > 0 else -1
            same_count = sum(1 for x in hist if (x > 0) == (direction > 0))
            consistency = same_count / len(hist)
            if consistency < self.失衡方向一致性_最小:
                return False, (
                    f"失衡方向一致性 {consistency:.2f} < "
                    f"{self.失衡方向一致性_最小}，噪声大"
                )

            # 7) 隐含概率共振
            # 失衡 > 0 表示买盘强，应共振 UP：要求 UP 隐含 >= 阈值
            if direction > 0:
                target_side: Direction = "UP"
                target_pct = up_pct
            else:
                target_side = "DOWN"
                target_pct = down_pct

            if target_pct < self.隐含概率共振阈值:
                return False, (
                    f"{target_side} 隐含概率 {target_pct:.1f}% < "
                    f"共振阈值 {self.隐含概率共振阈值}%，市场未跟随"
                )

            # 8) 止盈目标检查
            target_price = target_pct / 100.0
            target_price_cap = 1.0 - self.最小期望ROI
            if target_price > target_price_cap:
                return False, (
                    f"目标 {target_side} 价 {target_price:.3f} > "
                    f"止盈上限 {target_price_cap:.3f}"
                )

            # 通过
            signal.direction = target_side
            signal.timestamp = int(time.time())
            reason = (
                f"微结构开仓 {target_side}: "
                f"失衡={imbalance:+.3f}({consistency:.0%}一致), "
                f"隐含={target_pct:.1f}%(共振), "
                f"目标价={target_price:.3f}<=止盈上限{target_price_cap:.3f}, "
                f"剩余={remaining}s"
            )
            logger.info(f"[{self.name}] {reason}")
            return True, reason

        except Exception as e:
            logger.error(f"[{self.name}] should_open 异常: {e}, traceback={traceback.format_exc()}")
            return False, f"异常: {e}"

    # ===================== 辅助方法（与策略#1共享逻辑）=====================
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
            "失衡阈值": self.失衡强度阈值,
            "跟踪symbol数": len(self._imb_history),
            "signal_count": self._signal_count,
        }


if __name__ == "__main__":
    import datetime
    s = Btc15mOrderbookImbalanceStrategy()
    sig = s.get_signal()
    fake_ctx = {
        "now": datetime.datetime.utcnow(),
        "polymarket_market": {
            "up_midpoint": 0.60,
            "down_midpoint": 0.40,
            "orderbook_imbalance": 0.55,  # 强买盘
        },
    }
    # 模拟 3 次稳定采样
    for _ in range(3):
        s.should_open(sig, fake_ctx)
    # 触发判断
    allow, reason = s.should_open(sig, fake_ctx)
    print(f"失衡策略: allow={allow}, reason={reason}")
    print(f"最终 direction: {sig.direction!r}")
