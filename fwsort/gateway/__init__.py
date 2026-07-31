# 网关包统一导出
# 架构：
#   BaseGateway              抽象基类（所有网关必须继承）
#   PolymarketGateway        Polymarket V2 协议 + polymarket-client SDK 网关（业务推荐）
#   PolymarketClient         Polymarket V1 HTTP 客户端（路由层兼容）
#   OkxClient                OKX V5 REST 签名客户端
#   OkxExecutor              OKX 下单执行器
#   ExecutionGateway         统一执行网关（按 account_type 路由）
#   GatewayHub               总接口工厂（推荐：get_hub()）
#
# 用法（新）：
#   from fwsort.gateway import get_hub
#   hub = get_hub()
#   pm = hub.polymarket_v2        # 业务层推荐
#   okx = hub.okx                 # OKX
#   exec_gw = hub.execution       # 统一路由
#
# 用法（旧兼容）：
#   from fwsort.gateway import get_gateway, get_polymarket_client, get_polymarket_gateway
from fwsort.gateway.base import (
    BaseGateway,
    GatewayHealth,
    GatewayNotConfiguredError,
    GatewayNotReadyError,
    assert_subclass_ready,
)
from fwsort.gateway.gateway import (
    ExecutionGateway,
    ExecutionResult,
    GatewayHub,
    get_gateway,
    get_hub,
    get_polymarket_client,
    get_polymarket_gateway,
)
from fwsort.gateway.okx_gateway import (
    OkxClient,
    OkxGateway,
    OkxOrderResult,
    OkxExecutor,  # 别名（兼容旧路由调用）
    usd_to_size,
)
from fwsort.gateway.polymarket.polymarket_gateway import (
    POLY_HOSTS,
    OrderBookSnapshot,
    PlaceOrderResult,
    PolyOrderResult,
    PolymarketClient,  # 别名（兼容旧路由调用，等于 PolymarketV1Client）
    PolymarketGateway,
    PolymarketV1Client,
)
from fwsort.gateway.simulator_gateway import (
    OrderSimulator,  # 别名（兼容旧 execution.simulator 调用）
    SimulatedOrder,
    SimulatorGateway,
)

__all__ = [
    # 基类
    "BaseGateway",
    "GatewayHealth",
    "GatewayNotConfiguredError",
    "GatewayNotReadyError",
    "assert_subclass_ready",
    # Polymarket V2（业务推荐）
    "PolymarketGateway",
    "OrderBookSnapshot",
    "PlaceOrderResult",
    # Polymarket V1（兼容）
    "PolymarketV1Client",
    "PolymarketClient",
    "PolyOrderResult",
    "POLY_HOSTS",
    # OKX
    "OkxClient",
    "OkxGateway",
    "OkxExecutor",
    "OkxOrderResult",
    "usd_to_size",
    # 模拟盘
    "SimulatorGateway",
    "OrderSimulator",
    "SimulatedOrder",
    # 总接口
    "ExecutionGateway",
    "ExecutionResult",
    "GatewayHub",
    "get_gateway",
    "get_hub",
    "get_polymarket_client",
    "get_polymarket_gateway",
]
