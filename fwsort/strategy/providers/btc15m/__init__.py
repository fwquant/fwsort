"""BTC 15分 策略集 (均值回归 + 动量确认)

本目录下提供 3 份独立的 BTC 15分 量化策略，全部继承 StrategyBase：
    1. Btc15mMeanReversionStrategy  - 均值回归 + 动量确认
    2. Btc15mMomentumBreakoutStrategy - 动量突破 + 量价共振
    3. Btc15mOrderbookImbalanceStrategy - 订单簿失衡 + 微结构捕捉

所有策略的设计原则：
    - 仅在策略应该开仓的时机产生有效 signal.direction (UP/DOWN)，
      其他时间返回 direction=""，让上层跳过本次执行。
    - 所有市场数据来源于外部传入的 ctx（包含 gateway / now / polymarket_market）
    - 风险控制：隐含概率过滤、剩余时间过滤、置信度阈值过滤
    - 全部走 should_open() 实现完整的开仓条件判断
    - 止盈目标：期望每笔获利 >= amount * 0.05 (5% ROI) 才开仓

模块导入时会自动注册到 providers/__init__.py 可选列表。
"""
from fwsort.strategy.providers.btc15m.btc15m_mean_reversion import Btc15mMeanReversionStrategy
from fwsort.strategy.providers.btc15m.btc15m_momentum_breakout import Btc15mMomentumBreakoutStrategy
from fwsort.strategy.providers.btc15m.btc15m_orderbook_imbalance import Btc15mOrderbookImbalanceStrategy

__all__ = [
    "Btc15mMeanReversionStrategy",
    "Btc15mMomentumBreakoutStrategy",
    "Btc15mOrderbookImbalanceStrategy",
]
