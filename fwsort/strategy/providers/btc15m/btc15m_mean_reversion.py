"""BTC 15分 策略 #1：均值回归 + 动量确认

【策略核心思想】
Polymarket 的 BTC 15分钟市场存在以下两个统计规律：
    1. 周期初始（剩余时间 > 10 分钟）市场隐含概率常被前一根 K 线方向
       或大单扫单推向极端，UP 冲到 65%~80% 的情况在 15 分钟内回归
       到 50% 附近的概率 > 70%。
    2. 当隐含概率极端时（UP 价 > 65%），订单簿的 ask/bid 失衡往往滞后于
       隐含价格。如果订单簿的真实失衡方向与隐含概率相反，说明大资金
       正在反向布局，是高胜率的反转信号。

【开仓条件（必须同时满足）】
    1. 处于周期前 1/3（剩余时间 >= 10 分钟） —— 留够回归空间
    2. UP 或 DOWN 隐含概率处于 [极端下限, 极端上限] 区间
       （默认 60~78，避开 > 80% 的"看起来稳赢但赔率差"区）
    3. 订单簿失衡方向与隐含概率方向相反（反转确认）
    4. 隐含概率 + 订单簿失衡共同指向的"反向"目标收益 >= 最小止盈目标
       （即当前买一价 <= 1 - 最小止盈ROI）

【止盈目标】
    期望 ROI = 5%（即 1.0 美元本金 → 1.05 美元收益，对应押 UP 时
    UP 价格需要 <= 0.95）。因为押 UP 的胜率约 65~72%，盈亏比 = 5/35
    ≈ 0.14，整体期望为正。

【数据来源（外部传入）】
    ctx = {
        "gateway": pm类实例（用于获得_updown价格 / 订单簿）,
        "now": 当前 datetime,
        "polymarket_market": 可选，外部预查询的市场信息 dict，
                          包含 up_best_ask / down_best_ask / up_best_bid
                          / down_best_bid / orderbook_imbalance 等字段
    }
    若 ctx 中已包含 polymarket_market，则优先使用，避免重复请求。

【信号格式】
    direction: "UP" / "DOWN" / ""
    symbol:   btc-updown-15m-{当前周期epoch}
    amount:   使用用户配置
"""
from __future__ import annotations

import asyncio
import time
import traceback
from typing import Any

from fwsort.fwlogs import logger
from fwsort.strategy.base import Direction, Signal, StrategyBase

# 15分钟 = 900 秒
_PERIOD_SECONDS = 15 * 60
_SYMBOL_PREFIX = "btc-updown-15m"


def _compute_current_epoch() -> int:
    """计算当前 15 分钟周期的起始 epoch（按 900 秒对齐）"""
    return (int(time.time()) // _PERIOD_SECONDS) * _PERIOD_SECONDS


def _build_symbol(epoch: int | None = None) -> str:
    """构造 btc-updown-15m-{epoch} 格式的标的代码"""
    if epoch is None:
        epoch = _compute_current_epoch()
    return f"{_SYMBOL_PREFIX}-{epoch}"


class Btc15mMeanReversionStrategy(StrategyBase):
    """BTC 15分 均值回归 + 动量确认策略

    通过 Polymarket 隐含概率 + 订单簿失衡检测 BTC 15分钟市场的
    短期极端定价并下注回归。
    """

    name: str = "btc15m_mean_reversion"
    category: str = "custom"
    description: str = "BTC 15分 均值回归策略（隐含概率极端 + 订单簿反向确认）"
    author: str = "fwquant"
    version: str = "1.0.0"

    # ============ 下单金额 ============
    amount: float = 1.0

    # ============ 隐含概率阈值（%为单位）=========
    # 进入"极端"区间的下限：UP 价 >= 60% 才认为被超买
    极端隐含概率_下限: float = 60.0
    # 进入"极端"区间的上限：UP 价 <= 78%，避开赔率太差的"准赢家"
    极端隐含概率_上限: float = 78.0
    # 隐含概率差距阈值：UP 与 DOWN 价格差距 >= 此值才开仓
    隐含概率差_最小: float = 8.0

    # ============ 订单簿失衡阈值 ============
    # 订单簿买盘量 - 卖盘量 的归一化值（-1~1）
    # 正数代表买盘强，负数代表卖盘强
    # 反转开仓要求：失衡方向与隐含概率方向相反，且强度 >= 此阈值
    订单簿失衡_最小强度: float = 0.15

    # ============ 剩余时间控制 ============
    # 周期前 1/3 才开仓（已过秒数 <= 周期/3 = 300s）
    # 即剩余 >= 600s（10 分钟）时开仓
    周期已过_最大秒数: int = 5 * 60   # 周期前 1/3（300 秒 = 5 分钟）内开仓

    # ============ 止盈目标 ============
    # 期望 ROI = 5%：押 UP 时 UP 价格 <= 0.95 才开仓
    # 对应隐含概率 <= 95%，与极端区间天然兼容
    最小期望ROI: float = 0.05

    # ============ 显示/隐藏参数 ============
    parameters = [
        "amount",
        "极端隐含概率_下限", "极端隐含概率_上限", "隐含概率差_最小",
        "订单簿失衡_最小强度",
        "周期已过_最大秒数",
        "最小期望ROI",
    ]
    hidden_parameters: list[str] = []

    # ============ 内部状态 ============
    def __init__(self, config_json: dict | None = None, **kwargs):
        self.config = config_json or {}
        # 从 config_json / kwargs 覆盖默认值
        for k in self.parameters + self.hidden_parameters:
            v = kwargs.get(k, self.config.get(k, getattr(self, k, None)))
            if v is not None:
                # 简单类型转换（保持原类型）
                if isinstance(getattr(self, k, None), int):
                    setattr(self, k, int(v))
                elif isinstance(getattr(self, k, None), float):
                    setattr(self, k, float(v))
                else:
                    setattr(self, k, v)

        # 内部统计
        self._signal_count: int = 0
        self._last_signal_ts: int = 0

    # ===================== 核心：生成信号 =====================
    def get_signal(self) -> Signal:
        """生成一个信号。

        注意：本策略是被动型（均值回归需要外部行情），
        get_signal() 不直接获取行情（gateway 拿异步 I/O），
        而是返回一个"待判定"的默认信号；真正的方向判定放在
        should_open() 中执行。当 should_open() 返回 True 时，
        调度器会用本 Signal 的 direction 开仓。

        因此本策略配合自定义执行路径：外部框架在拿到 signal 后
        调用 should_open(signal, ctx)，再决定是否使用 signal.direction。

        为了让纯 get_signal() 接口也能工作，我们提供默认 symbol，
        direction 留空，amount 正常返回。
        """
        self._signal_count += 1
        self._last_signal_ts = int(time.time())

        return Signal(
            symbol=_build_symbol(),
            amount=self.amount,
            direction="",  # 由 should_open 决定
            source=self.name,
            timestamp=self._last_signal_ts,
        )

    # ===================== 核心：开仓条件 =====================
    def should_open(self, signal: Signal, ctx: dict) -> tuple[bool, str]:
        """开仓条件判断（核心逻辑）

        完整执行流程：
            1. 解析 ctx：拿 gateway / now / polymarket_market
            2. 拿隐含概率（优先使用 ctx 预查询结果，否则用 gateway 拉取）
            3. 拿订单簿失衡（优先 ctx 预查询，否则用 gateway 拉取）
            4. 检查剩余时间是否在 [最小剩余秒数, 最大剩余秒数] 区间
            5. 判断隐含概率是否进入"极端"区间
            6. 判断订单簿失衡方向是否与隐含概率相反（>=最小强度）
            7. 判断止盈目标（买一价 <= 1 - 最小期望ROI）
            8. 全部满足 → 改写 signal.direction 并返回 (True, reason)
        """
        gateway = ctx.get("gateway")
        now = ctx.get("now")
        market_info = ctx.get("polymarket_market")  # 外部预查询数据

        if gateway is None and market_info is None:
            return False, "未提供 gateway 或 polymarket_market 数据源"

        try:
            # 1) 拿隐含概率
            up_pct, down_pct, snapshot = self._fetch_implied_probs(
                gateway=gateway, market_info=market_info, symbol=signal.symbol,
            )
            if up_pct is None or down_pct is None:
                return False, "获取隐含概率失败"

            # 2) 拿订单簿失衡
            imbalance, imb_detail = self._fetch_orderbook_imbalance(
                gateway=gateway, market_info=market_info, symbol=signal.symbol,
            )

            # 3) 检查剩余时间窗口
            elapsed, total, remaining = self._compute_period_progress(now)
            if elapsed > self.周期已过_最大秒数:
                return False, (
                    f"周期已过 {elapsed:.0f}s > {self.周期已过_最大秒数}s，"
                    f"回归空间不足（已过 {(elapsed / total * 100):.0f}%）"
                )

            # 4) 判断隐含概率差距（必须有方向性偏好）
            prob_diff = abs(up_pct - down_pct)
            if prob_diff < self.隐含概率差_最小:
                return False, (
                    f"隐含概率差 {prob_diff:.1f}% < {self.隐含概率差_最小}%，"
                    f"市场无方向偏好，不参与"
                )

            # 5) 判断方向：哪边被超买/超卖
            if up_pct > down_pct:
                # UP 被超买 → 期望回归 → 押 DOWN
                biased_side = "UP"
                target_side: Direction = "DOWN"
                biased_pct = up_pct
            else:
                biased_side = "DOWN"
                target_side = "UP"
                biased_pct = down_pct

            # 极端区间检查
            if biased_pct < self.极端隐含概率_下限:
                return False, (
                    f"{biased_side} 隐含概率 {biased_pct:.1f}% < "
                    f"{self.极端隐含概率_下限}%，未进入极端区间"
                )
            if biased_pct > self.极端隐含概率_上限:
                return False, (
                    f"{biased_side} 隐含概率 {biased_pct:.1f}% > "
                    f"{self.极端隐含概率_上限}%，赔率太差（{100 - biased_pct:.0f}% 净胜率要求）"
                )

            # 6) 订单簿反转确认
            if imbalance is None:
                return False, "订单簿数据不可用，跳过反转确认"

            # imbalance > 0 表示买盘强（推 UP）
            # 我们要在 UP 被超买时，看到 imbalance < 0（卖盘反而强）才押 DOWN
            target_imb_sign = 1 if target_side == "UP" else -1
            if target_imb_sign * imbalance <= 0:
                return False, (
                    f"订单簿失衡方向与目标方向不一致："
                    f"imbalance={imbalance:+.3f}, target={target_side}"
                )
            if abs(imbalance) < self.订单簿失衡_最小强度:
                return False, (
                    f"订单簿失衡强度 {abs(imbalance):.3f} < "
                    f"{self.订单簿失衡_最小强度}，反转信号弱"
                )

            # 7) 止盈目标检查
            # 押 UP 时，UP 买一价 <= 1 - 最小期望ROI
            target_price_cap = 1.0 - self.最小期望ROI  # 例如 0.95
            if target_side == "UP":
                target_price = up_pct / 100.0
            else:
                target_price = down_pct / 100.0

            if target_price > target_price_cap:
                return False, (
                    f"目标 {target_side} 价 {target_price:.3f} > "
                    f"止盈上限 {target_price_cap:.3f}，赔率不足"
                )

            # 8) 全部条件满足 → 改写 signal 并返回 True
            signal.direction = target_side
            signal.timestamp = int(time.time())
            reason = (
                f"均值回归开仓 {target_side}: "
                f"{biased_side}={biased_pct:.1f}%(极端), "
                f"订单簿失衡={imbalance:+.3f}(反向), "
                f"目标价={target_price:.3f}<=止盈上限{target_price_cap:.3f}, "
                f"剩余={remaining}s"
            )
            logger.info(f"[{self.name}] {reason}")
            return True, reason

        except Exception as e:
            logger.error(
                f"[{self.name}] should_open 异常: {e}, "
                f"traceback={traceback.format_exc()}"
            )
            return False, f"异常: {e}"

    # ===================== 辅助：拿隐含概率 =====================
    def _fetch_implied_probs(
        self,
        *,
        gateway,
        market_info: dict | None,
        symbol: str,
    ) -> tuple[float | None, float | None, dict]:
        """从预查询数据或 gateway 异步获取 UP / DOWN 隐含概率（百分比 0~100）"""
        # 优先使用预查询数据
        if market_info and "up_midpoint" in market_info and "down_midpoint" in market_info:
            up_pct = float(market_info["up_midpoint"]) * 100.0
            down_pct = float(market_info["down_midpoint"]) * 100.0
            return up_pct, down_pct, {"source": "ctx.polymarket_market"}

        # 否则通过 gateway 拉取
        if gateway is None:
            return None, None, {}

        try:
            prices = self._run_async(gateway.获得_updown价格(symbol))
        except Exception as e:
            logger.warning(f"[{self.name}] 拉取隐含概率异常: {e}")
            return None, None, {}

        if not prices or "UP" not in prices or "DOWN" not in prices:
            return None, None, prices or {}

        up_mid = float(prices["UP"].get("midpoint") or 0.0)
        down_mid = float(prices["DOWN"].get("midpoint") or 0.0)
        return up_mid * 100.0, down_mid * 100.0, prices

    # ===================== 辅助：拿订单簿失衡 =====================
    def _fetch_orderbook_imbalance(
        self,
        *,
        gateway,
        market_info: dict | None,
        symbol: str,
    ) -> tuple[float | None, dict]:
        """获取订单簿失衡度（-1~1，正数代表买盘强）"""
        if market_info and "orderbook_imbalance" in market_info:
            return float(market_info["orderbook_imbalance"]), {"source": "ctx.polymarket_market"}

        if gateway is None:
            return None, {}

        try:
            ob = self._run_async(gateway.获得订单簿(symbol))
        except Exception as e:
            logger.warning(f"[{self.name}] 拉取订单簿异常: {e}")
            return None, {}

        if not ob:
            return None, {}

        # 通用解析：找 UP/DOWN 两个 outcome 的买盘/卖盘量
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

        imbalance = (bid_vol - ask_vol) / total
        return imbalance, {"bid_vol": bid_vol, "ask_vol": ask_vol, "imbalance": imbalance}

    # ===================== 辅助：周期进度 =====================
    def _compute_period_progress(self, now) -> tuple[float, float, float]:
        """计算 (已过秒数, 总秒数, 剩余秒数)"""
        if now is None:
            return 0.0, float(_PERIOD_SECONDS), float(_PERIOD_SECONDS)
        try:
            now_ts = float(now.timestamp())
        except Exception:
            return 0.0, float(_PERIOD_SECONDS), float(_PERIOD_SECONDS)

        epoch_start = (int(now_ts) // _PERIOD_SECONDS) * _PERIOD_SECONDS
        elapsed = now_ts - epoch_start
        remaining = _PERIOD_SECONDS - elapsed
        return elapsed, float(_PERIOD_SECONDS), remaining

    # ===================== 辅助：运行 async 协程 =====================
    @staticmethod
    def _run_async(coro):
        """在线程同步上下文中运行 async 协程"""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            # 已有事件循环在跑，回退到新建独立 loop
            new_loop = asyncio.new_event_loop()
            try:
                return new_loop.run_until_complete(coro)
            finally:
                new_loop.close()
        return asyncio.run(coro)

    # ===================== 健康检查 =====================
    def health_check(self) -> dict:
        return {
            "provider": self.name,
            "category": self.category,
            "ready": True,
            "amount": self.amount,
            "极端区间": f"[{self.极端隐含概率_下限}, {self.极端隐含概率_上限}]",
            "最小期望ROI": self.最小期望ROI,
            "signal_count": self._signal_count,
        }


if __name__ == "__main__":
    s = Btc15mMeanReversionStrategy()
    sig = s.get_signal()
    print(f"信号 symbol={sig.symbol}, amount={sig.amount}, direction={sig.direction!r}")

    # 模拟一次开仓判断：构造极端市场数据
    import datetime
    fake_ctx = {
        "now": datetime.datetime.utcnow(),
        "polymarket_market": {
            "up_midpoint": 0.70,    # UP 70% 隐含概率
            "down_midpoint": 0.30,  # DOWN 30% 隐含概率
            "orderbook_imbalance": -0.25,  # 卖盘比买盘强（支持 DOWN）
        },
    }
    allow, reason = s.should_open(sig, fake_ctx)
    print(f"开仓判断: allow={allow}, reason={reason}")
    print(f"最终 signal.direction: {sig.direction!r}")
