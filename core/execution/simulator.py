# 模拟下单执行器：Polymarket / OKX 通用模拟
import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime

from loguru import logger


@dataclass
class SimulatedOrder:
    """模拟订单结果"""

    order_id: str
    platform: str
    symbol: str
    side: int
    amount_usd: float
    status: int  # 1-已提交 2-部分 3-已成交 4-撤销 5-失败
    expected_price: float
    actual_price: float
    quantity: float
    latency_ms: int
    slippage: float
    created_at: datetime


class OrderSimulator:
    """统一模拟下单器（支持 polymarket / okx）"""

    def submit(
        self,
        platform: str,
        symbol: str,
        side: int,
        amount_usd: float,
    ) -> SimulatedOrder:
        """提交一笔模拟订单（按平台类型生成合理价/量/滑点）"""
        t0 = time.perf_counter()

        if amount_usd <= 0:
            return SimulatedOrder(
                order_id="",
                platform=platform, symbol=symbol, side=side,
                amount_usd=0.0, status=5,
                expected_price=0.0, actual_price=0.0, quantity=0.0,
                latency_ms=0, slippage=0.0, created_at=datetime.now(),
            )

        if platform == "polymarket":
            price, qty, slip = self._polymarket_model(symbol)
        elif platform == "okx":
            price, qty, slip = self._okx_model(symbol, amount_usd)
        else:
            price, qty, slip = 0.0, 0.0, 0.0

        # 模拟执行延迟
        latency = int(random.uniform(80, 600))
        time.sleep(latency / 1000)

        order_id = f"ORD-{uuid.uuid4().hex[:16].upper()}"
        # 90% 概率全部成交，10% 部分成交
        status = 3 if random.random() < 0.9 else 2
        actual_qty = qty if status == 3 else qty * random.uniform(0.3, 0.8)

        actual_price = price * (1 + slip * (1 if side == 1 else -1))
        latency_ms = int((time.perf_counter() - t0) * 1000)
        logger.info(
            f"[SIM] {platform} {symbol} {'BUY' if side == 1 else 'SELL'} "
            f"${amount_usd:.2f} → qty={actual_qty:.6f} @ {actual_price:.4f} (slip {slip*100:.3f}%)"
        )
        return SimulatedOrder(
            order_id=order_id,
            platform=platform, symbol=symbol, side=side,
            amount_usd=amount_usd, status=status,
            expected_price=price, actual_price=actual_price, quantity=actual_qty,
            latency_ms=latency_ms, slippage=slip, created_at=datetime.now(),
        )

    @staticmethod
    def _polymarket_model(symbol: str) -> tuple[float, float, float]:
        """Polymarket 二元合约：价格 0~1（涨跌概率），数量=份额"""
        if "BTC" in symbol.upper():
            base = 0.5
        else:
            base = 0.5
        price = round(max(0.01, min(0.99, base + random.uniform(-0.1, 0.1))), 4)
        # 份额 = 金额 / 价格
        return price, round(1.0 / price, 4), round(random.uniform(0.0, 0.01), 6)

    @staticmethod
    def _okx_model(symbol: str, amount_usd: float) -> tuple[float, float, float]:
        """OKX 现货/合约：以 BTC=60000 估算"""
        if "BTC" in symbol.upper():
            price = 60000.0 + random.uniform(-500, 500)
        else:
            price = 100.0
        qty = round(amount_usd / price, 6)
        slip = round(random.uniform(0.0, 0.003), 6)
        return price, qty, slip
