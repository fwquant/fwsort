# 统一执行网关：根据执行账户类型选择模拟/真实执行器
from dataclasses import dataclass
from typing import Any

from loguru import logger

from fwsort.config import settings
from fwsort.execution.okx_executor import OkxExecutor
from fwsort.execution.polymarket_client import PolymarketClient
from fwsort.execution.simulator import OrderSimulator, SimulatedOrder


@dataclass
class ExecutionResult:
    """统一执行结果（屏蔽不同执行器差异）"""

    order_id: str
    platform: str  # okx / polymarket / simulator
    symbol: str
    side: int  # 1=buy 2=sell
    amount_usd: float
    status: int  # 1-已提交 2-部分 3-已成交 4-撤销 5-失败
    expected_price: float
    actual_price: float
    quantity: float
    latency_ms: int
    slippage: float
    is_live: bool  # True=实盘 False=模拟
    extra: dict  # 执行器特有信息


class ExecutionGateway:
    """统一执行网关（根据 ExecutionAccount 选路由）

    路由规则：
    - account_type == 0 → simulator（模拟盘）
    - account_type == 1 && platform == 'okx' → OkxExecutor
    - account_type == 1 && platform == 'polymarket' → PolymarketClient
    - 未配置 key 时降级 simulator（保证主流程不挂）
    """

    def __init__(self) -> None:
        self._simulator = OrderSimulator()
        self._okx: OkxExecutor | None = None
        self._polymarket: PolymarketClient | None = None

    def _get_okx(self) -> OkxExecutor:
        if self._okx is None:
            self._okx = OkxExecutor(demo=settings.OKX_SERVER != "LIVE")
        return self._okx

    def _get_polymarket(self) -> PolymarketClient:
        if self._polymarket is None:
            # chain: goerli→STAGING 主机；polygon→MAINNET
            chain = settings.POLYMARKET_CHAIN.lower()
            host = "GOERLI" if chain == "goerli" else "MAINNET"
            self._polymarket = PolymarketClient(host=host)
        return self._polymarket

    async def close(self) -> None:
        if self._okx:
            await self._okx.close()
        if self._polymarket:
            await self._polymarket.close()

    async def submit(
        self,
        *,
        account_type: int,  # 0-模拟 1-实盘
        platform: str,      # okx / polymarket
        symbol: str,
        side: int,          # 1=buy 2=sell
        amount_usd: float,
    ) -> ExecutionResult:
        """根据 account_type 路由到合适的执行器

        任何执行器异常 → 降级 simulator（不阻塞投票闭环）
        """
        if amount_usd <= 0:
            return ExecutionResult(
                order_id="", platform=platform, symbol=symbol, side=side,
                amount_usd=0.0, status=5, expected_price=0.0, actual_price=0.0,
                quantity=0.0, latency_ms=0, slippage=0.0, is_live=False,
                extra={"reason": "amount_usd <= 0"},
            )

        # 实盘模式
        if account_type == 1:
            try:
                if platform == "okx":
                    return await self._submit_okx(symbol, side, amount_usd)
                if platform == "polymarket":
                    return await self._submit_polymarket(symbol, side, amount_usd)
            except Exception as e:  # noqa: BLE001
                logger.error(f"[GATEWAY] {platform} live order failed, fallback to simulator: {e}")
                # 实盘失败 → 降级模拟（保证主流程不挂）

        # 默认：模拟盘
        return self._submit_simulator(platform, symbol, side, amount_usd)

    async def _submit_okx(self, symbol: str, side: int, amount_usd: float) -> ExecutionResult:
        """OKX 实盘下单"""
        client = self._get_okx()
        if not client.is_ready():
            logger.warning("[GATEWAY] OKX not configured, fallback to simulator")
            return self._submit_simulator("okx", symbol, side, amount_usd)
        result = await client.submit(symbol=symbol, side=side, amount_usd=amount_usd)
        # 状态映射
        state_map = {"filled": 3, "live": 1, "partially_filled": 2, "canceled": 4, "expired": 4}
        status = state_map.get(result.state, 5)
        return ExecutionResult(
            order_id=result.order_id,
            platform="okx",
            symbol=symbol,
            side=side,
            amount_usd=amount_usd,
            status=status,
            expected_price=0.0,
            actual_price=result.avg_price,
            quantity=result.filled_qty,
            latency_ms=result.latency_ms,
            slippage=0.0,
            is_live=True,
            extra={"okx": result.raw},
        )

    async def _submit_polymarket(self, symbol: str, side: int, amount_usd: float) -> ExecutionResult:
        """Polymarket 实盘下单（需 token_id，先查 midpoint 估价）"""
        client = self._get_polymarket()
        if not client.is_configured():
            logger.warning("[GATEWAY] Polymarket not configured, fallback to simulator")
            return self._submit_simulator("polymarket", symbol, side, amount_usd)
        # 注：实际生产需要把 symbol 映射到 condition_id/token_id
        # 这里走中间价查询 → 计算份额 → 下单
        try:
            token_id = symbol  # 简化：symbol 当作 token_id
            mid = await client.get_midpoint(token_id)
            price = float((mid or {}).get("mid", 0.5))
            if price <= 0:
                price = 0.5
            size = round(amount_usd / price, 4)
            side_str = "BUY" if side == 1 else "SELL"
            resp = await client.place_order(token_id=token_id, side=side_str, price=price, size=size)
            order_id = (resp or {}).get("id") or (resp or {}).get("orderID", "")
            return ExecutionResult(
                order_id=order_id,
                platform="polymarket",
                symbol=symbol,
                side=side,
                amount_usd=amount_usd,
                status=1,  # live
                expected_price=price,
                actual_price=price,
                quantity=size,
                latency_ms=0,
                slippage=0.0,
                is_live=True,
                extra={"poly": resp},
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"[GATEWAY] polymarket place_order error: {e}")
            return self._submit_simulator("polymarket", symbol, side, amount_usd)

    def _submit_simulator(self, platform: str, symbol: str, side: int, amount_usd: float) -> ExecutionResult:
        """模拟下单（同步）"""
        sim: SimulatedOrder = self._simulator.submit(platform, symbol, side, amount_usd)
        return ExecutionResult(
            order_id=sim.order_id,
            platform=sim.platform,
            symbol=sim.symbol,
            side=sim.side,
            amount_usd=sim.amount_usd,
            status=sim.status,
            expected_price=sim.expected_price,
            actual_price=sim.actual_price,
            quantity=sim.quantity,
            latency_ms=sim.latency_ms,
            slippage=sim.slippage,
            is_live=False,
            extra={"sim": True},
        )


# 全局单例
_gateway: ExecutionGateway | None = None


def get_gateway() -> ExecutionGateway:
    global _gateway
    if _gateway is None:
        _gateway = ExecutionGateway()
    return _gateway
