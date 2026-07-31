# 网关总接口（统一管理 + 路由 + 单例）
# 架构：
#   base.py                 → BaseGateway 抽象基类（PM/OKX 等继承）
#   polymarket_gateway.py   → Polymarket V1+V2 网关（同一平台同文件）
#   okx_gateway.py          → OKX 网关（client+executor 整合到同文件）
#   simulator_gateway.py    → 模拟盘网关（继承 BaseGateway）
#   gateway.py（本文件）    → GatewayHub 工厂 + ExecutionGateway 统一执行路由
# 设计目标：
#   1) 业务层只引一个 from fwsort.gateway import get_hub / get_execution_gateway
#   2) 路由逻辑集中，OKX/PM 切换不污染调用方
#   3) 单例 + 懒加载 + 模拟盘降级（保证主流程不挂）
from dataclasses import dataclass
from typing import Any

from loguru import logger

from fwsort.config import settings
from fwsort.gateway.base import BaseGateway
from fwsort.gateway.okx_gateway import OkxGateway  # OkxExecutor 别名（兼容）
from fwsort.gateway.polymarket.polymarket_gateway import (
    # 别名（兼容旧路由）
    PolymarketGateway,
    PolymarketV1Client,
)
from fwsort.gateway.simulator_gateway import SimulatedOrder, SimulatorGateway


# ========== 统一执行结果 ==========
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


# ========== 执行网关（统一路由）==========
class ExecutionGateway(BaseGateway):
    """统一执行网关（根据 ExecutionAccount 选路由）

    继承自 BaseGateway：
    - name = "execution_hub"
    - 内部聚合 PolymarketGateway / PolymarketV1Client / OkxGateway / SimulatorGateway
    - 统一对外暴露 submit() / close() / get_status() / health_check()

    路由规则：
    - account_type == 0 → simulator（模拟盘）
    - account_type == 1 && platform == 'okx' → OkxGateway
    - account_type == 1 && platform == 'polymarket' → PolymarketV1Client（V1 HTTP，兼容路由层）
    - 未配置 key 时降级 simulator（保证主流程不挂）
    """

    # 基类要求：平台名
    name: str = "execution_hub"

    def __init__(self) -> None:
        # 调用基类初始化（无具体 host，聚合型）
        super().__init__(host="hub://execution", chain_id=0, http_timeout=10.0)
        self._simulator = SimulatorGateway()
        self._okx: OkxGateway | None = None
        self._polymarket: PolymarketV1Client | None = None
        self._polymarket_v2: PolymarketGateway | None = None

    # ===== 抽象方法实现（BaseGateway 要求） =====
    def is_ready(self) -> bool:
        """Hub 是否就绪（始终返回 True，因为降级到 simulator 即可）"""
        return True  # 模拟盘兜底，Hub 永远可用

    def is_configured(self) -> bool:
        """Hub 是否配置了任何实盘网关（用于状态展示）"""
        return bool(
            self._okx and self._okx.is_configured()
            or self._polymarket and self._polymarket.is_configured()
        )

    async def _do_ping(self) -> dict:
        """聚合探测：依次 ping 已初始化的实盘网关"""
        results: dict[str, Any] = {}
        overall_success = True
        if self._okx:
            try:
                results["okx"] = await self._okx.ping()
                if not results["okx"].get("success"):
                    overall_success = False
            except Exception as e:  # noqa: BLE001
                results["okx"] = {"success": False, "msg": str(e)}
                overall_success = False
        if self._polymarket:
            try:
                results["polymarket_v1"] = await self._polymarket.ping()
                if not results["polymarket_v1"].get("success"):
                    overall_success = False
            except Exception as e:  # noqa: BLE001
                results["polymarket_v1"] = {"success": False, "msg": str(e)}
                overall_success = False
        return {"success": overall_success, "children": results, "simulator": True}

    def get_status(self) -> dict:
        """Hub 状态摘要（聚合所有子网关）"""
        return {
            "name": self.name,
            "ready": self.is_ready(),
            "configured": self.is_configured(),
            "okx": self._okx.get_status() if self._okx else {"configured": False},
            "polymarket_v1": self._polymarket.get_status() if self._polymarket else {"configured": False},
            "polymarket_v2": self._polymarket_v2.get_status() if self._polymarket_v2 else {"configured": False},
            "simulator": self._simulator.get_status(),
            "last_ping_success": self._last_ping_success,
            "last_ping_at": self._last_ping_at,
            "last_error": self._last_error,
        }

    # ===== 内部工厂 =====
    def _get_okx(self) -> OkxGateway:
        """懒加载 OkxGateway（DEMO 走 X-Simulated-Trading=1）"""
        if self._okx is None:
            self._okx = OkxGateway(demo=settings.OKX_SERVER != "LIVE")
        return self._okx

    def _get_polymarket(self) -> PolymarketV1Client:
        """懒加载 PolymarketV1Client（V1 HTTP，路由层仍使用）"""
        if self._polymarket is None:
            chain = settings.POLYMARKET_CHAIN.lower()
            host = "GOERLI" if chain == "goerli" else "MAINNET"
            self._polymarket = PolymarketV1Client(host=host)
        return self._polymarket

    def _get_polymarket_v2(self) -> PolymarketGateway:
        """懒加载 PolymarketGateway（V2 + polymarket-client SDK，业务层推荐）"""
        if self._polymarket_v2 is None:
            self._polymarket_v2 = PolymarketGateway()
        return self._polymarket_v2

    # ===== 路由 =====
    async def close(self) -> None:
        """关闭所有子网关"""
        if self._okx:
            await self._okx.close()
        if self._polymarket:
            await self._polymarket.close()
        if self._polymarket_v2:
            await self._polymarket_v2.close()
        await self._simulator.close()
        await super().close()

    async def submit(
            self,
            *,
            account_type: int,  # 0-模拟 1-实盘
            platform: str,  # okx / polymarket
            symbol: str,
            side: int,  # 1=buy 2=sell
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
        return await self._submit_simulator(platform, symbol, side, amount_usd)

    async def _submit_okx(self, symbol: str, side: int, amount_usd: float) -> ExecutionResult:
        """OKX 实盘下单（委托给 OkxGateway）"""
        client = self._get_okx()
        if not client.is_ready():
            logger.warning("[GATEWAY] OKX not configured, fallback to simulator")
            return await self._submit_simulator("okx", symbol, side, amount_usd)
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
            return await self._submit_simulator("polymarket", symbol, side, amount_usd)
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
            return await self._submit_simulator("polymarket", symbol, side, amount_usd)

    async def _submit_simulator(self, platform: str, symbol: str, side: int, amount_usd: float) -> ExecutionResult:
        """WP-12：模拟下单（异步）"""
        sim: SimulatedOrder = await self._simulator.submit(platform, symbol, side, amount_usd)
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


# ========== 总接口：GatewayHub 统一工厂 ==========
class GatewayHub:
    """网关总接口（统一管理 PM V1/V2 / OKX / 模拟盘 / 执行网关）

    设计目的：
    - 业务层只引一个 from fwsort.gateway import get_hub
    - 统一获取单例，避免到处 new + close
    - 提供聚合状态 / 健康检查

    用法：
        hub = get_hub()
        pm = hub.polymarket_v2        # PolymarketGateway（业务推荐）
        pm_v1 = hub.polymarket_v1     # PolymarketV1Client（路由层兼容）
        okx = hub.okx                 # OkxGateway
        sim = hub.simulator           # SimulatorGateway
        exec_gw = hub.execution       # ExecutionGateway
        # ... 业务操作
        await hub.close_all()         # 退出时统一释放
    """

    def __init__(self) -> None:
        self._execution: ExecutionGateway | None = None
        self._okx: OkxGateway | None = None
        self._polymarket_v1: PolymarketV1Client | None = None
        self._polymarket_v2: PolymarketGateway | None = None
        self._simulator: SimulatorGateway | None = None

    @property
    def execution(self) -> ExecutionGateway:
        """统一执行网关（按 account_type 路由）"""
        if self._execution is None:
            self._execution = ExecutionGateway()
        return self._execution

    @property
    def okx(self) -> OkxGateway:
        """OKX 网关（懒加载）"""
        if self._okx is None:
            self._okx = OkxGateway(demo=settings.OKX_SERVER != "LIVE")
        return self._okx

    @property
    def polymarket_v1(self) -> PolymarketV1Client:
        """Polymarket V1 HTTP 客户端（路由层兼容）"""
        if self._polymarket_v1 is None:
            chain = settings.POLYMARKET_CHAIN.lower()
            host = "GOERLI" if chain == "goerli" else "MAINNET"
            self._polymarket_v1 = PolymarketV1Client(host=host)
        return self._polymarket_v1

    @property
    def polymarket_v2(self) -> PolymarketGateway:
        """Polymarket V2 协议 + polymarket-client SDK 网关（业务层推荐）"""
        if self._polymarket_v2 is None:
            self._polymarket_v2 = PolymarketGateway()
        return self._polymarket_v2

    @property
    def simulator(self) -> SimulatorGateway:
        """模拟盘网关（永远可用）"""
        if self._simulator is None:
            self._simulator = SimulatorGateway()
        return self._simulator

    def list_gateways(self) -> list[BaseGateway]:
        """列出所有已初始化的网关（用于状态展示）"""
        items: list[BaseGateway] = []
        if self._execution:
            items.append(self._execution)
        if self._okx:
            items.append(self._okx)
        if self._polymarket_v1:
            items.append(self._polymarket_v1)
        if self._polymarket_v2:
            items.append(self._polymarket_v2)
        if self._simulator:
            items.append(self._simulator)
        return items

    async def close_all(self) -> None:
        """关闭所有已初始化的网关（退出时调用）"""
        if self._execution:
            await self._execution.close()
        if self._okx:
            await self._okx.close()
        if self._polymarket_v1:
            await self._polymarket_v1.close()
        if self._polymarket_v2:
            await self._polymarket_v2.close()
        if self._simulator:
            await self._simulator.close()


# ========== 全局单例（向后兼容 + 统一入口）==========
_hub: GatewayHub | None = None


def get_hub() -> GatewayHub:
    """获取全局 GatewayHub 单例（新统一入口，推荐使用）"""
    global _hub
    if _hub is None:
        _hub = GatewayHub()
    return _hub


def get_gateway() -> ExecutionGateway:
    """获取 ExecutionGateway（统一走 Hub，保证与 hub.execution 是同一实例）"""
    return get_hub().execution


# ========== 兼容旧函数名（polymarket_router 仍使用）==========
def get_polymarket_client() -> PolymarketV1Client:
    """兼容旧调用：返回 PolymarketV1Client 单例（由 Hub 统一管理）"""
    return get_hub().polymarket_v1


def get_polymarket_gateway() -> PolymarketGateway:
    """兼容旧调用：返回 PolymarketGateway 单例（V2 SDK 适配层）"""
    return get_hub().polymarket_v2


# 重导出（兼容旧 import）
__all__ = [
    "ExecutionGateway",
    "ExecutionResult",
    "GatewayHub",
    "get_gateway",
    "get_hub",
    "get_polymarket_client",
    "get_polymarket_gateway",
]