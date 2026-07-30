# 网关抽象基类
# 职责：
#   - 统一所有外部交易/数据网关（Polymarket / OKX / ...）的接口与生命周期
#   - 子类只需实现：name、is_ready、_do_ping，零成本接入 GatewayHub
#   - 提供统一的 HTTP 客户端管理、状态摘要、健康检查
# 设计原则：
#   - 单一职责：基类只管"网关身份 + 连接 + 状态"，不做具体业务
#   - 业务扩展：子类按平台添加业务方法（如下单/撤单/查持仓）
#   - 兼容性：与旧 PolymarketClient / OkxClient 保持向后兼容
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any

import httpx
from loguru import logger


# ========== 网关异常 ==========
class GatewayNotConfiguredError(RuntimeError):
    """网关未配置（缺密钥/钱包）时抛出"""


# ========== 网关状态 ==========
class GatewayNotReadyError(RuntimeError):
    """网关尚未 connect() 时调用业务方法抛出"""


# ========== 网关健康摘要 ==========
@dataclass
class GatewayHealth:
    """网关健康摘要（统一格式，便于上层聚合展示）"""

    name: str = ""
    ready: bool = False
    configured: bool = False
    host: str = ""
    chain_id: int = 0
    http_open: bool = False
    last_ping_ok: bool = False
    last_ping_at: str = ""
    last_error: str = ""
    extra: dict = field(default_factory=dict)


# ========== 网关抽象基类 ==========
class BaseGateway(ABC):
    """所有外部网关的抽象基类（Polymarket / OKX / ...）

    抽象方法（子类必须实现）：
        name        : 平台名（如 "polymarket" / "okx"）
        is_ready()  : 业务可用性（密钥 + HTTP 客户端）
        _do_ping()  : 平台特定的连通性探测

    模板方法（基类已实现，子类可重写）：
        connect()   : 初始化 HTTP 客户端
        close()     : 释放资源
        ping()      : 调用 _do_ping 并包装返回值
        get_status(): 状态摘要 dict
        health_check(): 主动健康检查
    """

    # 子类可覆盖的默认属性
    name: str = "base"

    def __init__(
            self,
            host: str | None = None,
            chain_id: int = 0,
            http_timeout: float = 10.0,
    ) -> None:
        # 身份与连接配置
        self.host: str = host or ""
        self.chain_id: int = chain_id
        self._http_timeout: float = http_timeout
        # HTTP 客户端（懒加载）
        self._http: httpx.AsyncClient | None = None
        # 状态
        self._initialized: bool = False
        self._last_ping_ok: bool = False
        self._last_ping_at: str = ""
        self._last_error: str = ""

    # ========== 抽象方法（子类必须实现） ==========
    pass

    #  网关状态
    @abstractmethod
    def is_ready(self) -> bool:
        """判断网关是否完全就绪（密钥 + HTTP 客户端 + L2 凭据）"""

    #  网关连通性探测
    @abstractmethod
    async def _do_ping(self) -> dict:
        """平台特定的连通性探测；返回 {ok: bool, ...}"""

    #  网关配置状态
    def is_configured(self) -> bool:
        """是否完成密钥/钱包配置（无网络副作用）"""
        return self.is_ready()

    # ========== 模板方法（基类统一实现） ==========
    pass

    #  网关连接
    async def connect(self) -> None:
        """建立连接（初始化 HTTP 客户端）"""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=self._http_timeout)
        self._initialized = True
        logger.info(f"[{self.name}-GW] connected host={self.host} chain={self.chain_id}")

    #  网关关闭
    async def close(self) -> None:
        """释放资源（HTTP 客户端）"""
        if self._http and not self._http.is_closed:
            await self._http.aclose()
        self._http = None
        self._initialized = False
        self._last_ping_ok = False
        logger.info(f"[{self.name}-GW] closed")

    #  网关获取 HTTP 客户端
    async def _get_http(self) -> httpx.AsyncClient:
        """获取或懒创建 HTTP 客户端"""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=self._http_timeout)
        return self._http

    #  网关连通性探测
    async def ping(self) -> dict:
        """连通性探测（包装 _do_ping）"""
        try:
            res = await self._do_ping()
            self._last_ping_ok = bool(res.get("ok", False))
            self._last_ping_at = datetime.utcnow().isoformat()
            if not self._last_ping_ok:
                self._last_error = res.get("error") or res.get("status", "unknown")
            return res
        except Exception as e:  # noqa: BLE001
            self._last_ping_ok = False
            self._last_error = str(e)
            self._last_ping_at = datetime.utcnow().isoformat()
            return {"ok": False, "error": str(e)}

    #  网关状态摘要
    def get_status(self) -> dict:
        """获取网关状态摘要（字典形式，便于 JSON 返回）"""
        return asdict(self._build_health(extra={}))

    #  网关主动检查健康状态
    async def health_check(self) -> dict:
        """主动健康检查（ping + 配置 + HTTP 状态）"""
        ping_res = await self.ping()
        return {
            "name": self.name,
            "ready": self.is_ready(),
            "configured": self.is_configured(),
            "ping": ping_res,
            "host": self.host,
            "chain_id": self.chain_id,
            "http_open": bool(self._http and not self._http.is_closed),
        }

    # ========== 工具方法 ==========
    pass

    #  网关状态摘要
    def _build_health(self, extra: dict[str, Any] | None = None) -> GatewayHealth:
        """构造健康摘要对象（供子类在 get_status 中复用）"""
        return GatewayHealth(
            name=self.name,
            ready=self.is_ready(),
            configured=self.is_configured(),
            host=self.host,
            chain_id=self.chain_id,
            http_open=bool(self._http and not self._http.is_closed),
            last_ping_ok=self._last_ping_ok,
            last_ping_at=self._last_ping_at,
            last_error=self._last_error,
            extra=extra or {},
        )

    #  网关状态摘要
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name} host={self.host} ready={self.is_ready()}>"


# ========== 工具：确保子类正确实现抽象方法 ==========
def assert_subclass_ready(gw: BaseGateway) -> None:
    """装饰器/工具：断言 gateway 是 BaseGateway 的具体实现（开发期自检用）"""
    if not isinstance(gw, BaseGateway):
        raise TypeError(f"{gw!r} is not a BaseGateway subclass")
    if gw.name == "base":
        logger.warning(f"gateway {gw!r} uses default name 'base', please override")
