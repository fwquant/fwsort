# 模拟盘网关（继承 BaseGateway，作为"虚拟平台"网关纳入统一管理）
# 架构：
#   - gateway/base.py          → BaseGateway 抽象基类
#   - gateway/simulator_gateway.py → 模拟盘网关（本文件）
#   - gateway/polymarket_gateway.py → Polymarket 网关（V1+V2 协议同文件）
#   - gateway/okx_gateway.py   → OKX 网关（client+executor 同文件）
#   - gateway/gateway.py       → GatewayHub + ExecutionGateway 总接口
# 设计：
#   - 模拟盘永远可用（不依赖密钥/网络），is_ready() 永远返回 True
#   - 继承 BaseGateway 后被 GatewayHub 统一管理
#   - submit() 返回 SimulatedOrder（向后兼容旧 OrderSimulator 调用）
import asyncio
import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loguru import logger

from fwsort.gateway.base import BaseGateway


@dataclass
class SimulatedOrder:
    """模拟订单结果（统一数据结构）"""

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


class SimulatorGateway(BaseGateway):
    """模拟盘网关（虚拟平台，无密钥/网络依赖）

    继承自 BaseGateway：
    - name = "simulator"
    - is_ready 永远 True（不依赖任何配置）
    - _do_ping 永远 OK（无网络）
    - submit() 异步模拟下单（不阻塞 event loop）
    """

    # 基类要求：平台名
    name: str = "simulator"

    def __init__(
        self,
        platform: str | None = None,
        account_type: int | None = None,
        **_: Any,  # 吸收其他旧参数（seed_latency / tick_size 等）
    ) -> None:
        # 调用基类初始化（模拟盘无 host/chain 概念）
        super().__init__(host="sim://local", chain_id=0, http_timeout=1.0)
        # 兼容旧 OrderSimulator(platform=, account_type=) 接口
        # 新版通过 hub.execution.submit(account_type=, platform=) 路由，
        # 此处仅记录供外部读取，不参与下单逻辑
        self.platform: str = platform or "simulator"
        self.account_type: int = int(account_type) if account_type is not None else 0

    # ===== 抽象方法实现（BaseGateway 要求） =====
    def is_ready(self) -> bool:
        """模拟盘永远可用（无密钥/网络依赖）"""
        return True

    def is_configured(self) -> bool:
        """模拟盘永远已配置"""
        return True

    async def _do_ping(self) -> dict:
        """模拟盘连通性永远 OK"""
        return {"success": True, "latency_ms": 0, "note": "simulator always healthy"}

    # ===== 业务：模拟下单 =====
    async def submit(
        self,
        platform: str | None = None,
        symbol: str = "",
        side: int = 1,
        amount_usd: float = 0.0,
        expected_price: float | None = None,
        **_: Any,  # 吸收其他未知参数
    ) -> SimulatedOrder:
        """提交一笔模拟订单（按平台类型生成合理价/量/滑点）

        兼容两种调用风格：
        - 旧：sim.submit(platform=..., symbol=..., side=..., amount_usd=...)
              sim.submit(symbol=..., side=..., amount_usd=..., expected_price=...)
              （platform 缺省取 self.platform，expected_price 缺省按模型计算）
        - 新（hub.execution 路由）：
              sim.submit(platform, symbol, side, amount_usd)

        Args:
            platform: 目标平台（polymarket / okx）— 仅用于标记
            symbol: 交易对
            side: 1=buy 2=sell
            amount_usd: 美元金额
            expected_price: 可选，外部传入的预期价（不传则按模型生成）

        Returns:
            SimulatedOrder
        """
        # 兼容旧 OrderSimulator 调用（platform 缺省 → self.platform）
        if platform is None:
            platform = getattr(self, "platform", "simulator") or "simulator"
        t0 = time.perf_counter()

        if amount_usd <= 0:
            return SimulatedOrder(
                order_id="",
                platform=platform, symbol=symbol, side=side,
                amount_usd=0.0, status=5,
                expected_price=0.0, actual_price=0.0, quantity=0.0,
                latency_ms=0, slippage=0.0, created_at=datetime.now(),
            )

        # 按平台类型分派价格/数量/滑点模型
        if platform == "polymarket":
            price, qty, slip = self._polymarket_model(symbol)
        elif platform == "okx":
            price, qty, slip = self._okx_model(symbol, amount_usd)
        else:
            price, qty, slip = 0.0, 0.0, 0.0

        # 兼容旧 OrderSimulator 接口：expected_price 外部覆盖模型价
        if expected_price is not None and float(expected_price) > 0:
            price = float(expected_price)

        # 异步等待（不阻塞 event loop）
        latency = int(random.uniform(80, 600))
        await asyncio.sleep(latency / 1000)

        order_id = f"ORD-{uuid.uuid4().hex[:16].upper()}"
        # 90% 全部成交，10% 部分成交
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
        base = 0.5
        if "BTC" in symbol.upper():
            base = 0.5
        price = round(max(0.01, min(0.99, base + random.uniform(-0.1, 0.1))), 4)
        # 份额 = 1 / 价格（占位 1 USD 当量）
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


# ========== 向后兼容：保留 OrderSimulator 别名（迁移期使用）==========
# 旧调用：from fwsort.execution.simulator import OrderSimulator
# 新调用：from fwsort.gateway.simulator_gateway import SimulatorGateway
# 旧 OrderSimulator().submit(platform=, symbol=, side=, amount_usd=) 完全兼容
OrderSimulator = SimulatorGateway
