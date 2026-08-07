# Polymarket 网关（V2 协议 + 统一 SDK 适配层）
# 官方文档：https://docs.polymarket.com/getting-started/migrate-from-previous-sdks
# 迁移要点（V1 → V2）：
#   1) EIP-712 Exchange 域 version 由 "1" → "2"（API auth 不变）
#   2) Order 结构移除：feeRateBps / nonce / taker；新增：timestamp(毫秒) / metadata / builder
#   3) Collateral：USDC.e → pUSD
#   4) SDK 包名：旧 py-clob-client / py-clob-client-v2 → 新 polymarket-client（统一 SDK，合并 CLOB + Relayer + Builder）
#   5) Base URL 不变：https://clob.polymarket.com
# 本网关职责：
#   - 统一管理连接 / 认证 / 订单 / 持仓 / 监控
#   - 优先使用 polymarket-client（若已安装），否则走 V2 HTTP 直连
#   - 业务层不感知底层协议差异
# 架构：
#   - 继承 BaseGateway（统一生命周期 / 状态 / 健康检查）
#   - 实现 _do_ping / is_ready 抽象方法
import os

mName = os.path.basename(__file__)
import asyncio
import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any

import httpx
from eth_account import Account
from eth_account.messages import encode_typed_data

from fwsort.config import settings
from fwsort.fwlogs import logger
from fwsort.gateway.base import BaseGateway

# 尝试导入官方统一 SDK polymarket-client（V2 + Relayer + Builder 三合一）
# 包名：polymarket（新，安装后模块名为 polymarket）/ py-clob-client-v2（旧，已废弃）
try:
    from polymarket import (AsyncSecureClient, ApiKeyCreds, OrderType, OrderSide, )
    _HAS_SDK = True
except ImportError:
    AsyncSecureClient = ApiKeyCreds = OrderType = OrderSide = None
    _HAS_SDK = False


# ========== 统一错误码（唯一数字，便于排查）==========
# 格式：{模块}{功能}{状态}
# 模块：1=网关基础, 2=认证, 3=市场查询, 4=订单管理, 5=账户持仓, 6=风控监控, 7=BTC5M业务
# 功能：00-99
# 状态：0=成功, 1-9=失败原因
class GatewayCode:
    # ===== 基础操作 (1000-1099) =====
    SUCCESS = 1000
    PING_OK = 1010
    CONNECT_OK = 1020
    CLOSE_OK = 1030
    NOT_READY = 1101
    PING_FAILED = 1110
    TIMEOUT = 1120
    CONNECT_ERROR = 1130
    NETWORK_ERROR = 1140
    UNKNOWN_ERROR = 1199

    # ===== 认证 (2000-2099) =====
    AUTH_L1_SIGNED = 2010
    AUTH_L2_READY = 2020
    AUTH_L2_DERIVED = 2030
    WALLET_NOT_CONFIGURED = 2101
    L2_CREDS_MISSING = 2110
    L2_CREATE_FAILED = 2120
    L2_DERIVE_FAILED = 2130
    SIGN_FAILED = 2140

    # ===== 市场查询 (3000-3099) =====
    MARKET_LIST_OK = 3010
    MARKET_DETAIL_OK = 3020
    MIDPOINT_OK = 3030
    PRICE_OK = 3040
    ORDERBOOK_OK = 3050
    SPREAD_OK = 3060
    TICK_SIZE_OK = 3070
    BTC5M_MARKET_FOUND = 3080
    MARKET_NOT_FOUND = 3101
    QUERY_FAILED = 3120
    INVALID_TOKEN_ID = 3130

    # ===== 订单管理 (4000-4099) =====
    ORDER_CREATED = 4010
    ORDER_POSTED = 4020
    ORDER_CANCELLED = 4030
    ORDERS_BATCH_POSTED = 4040
    ORDERS_CANCELLED = 4050
    ORDERS_ALL_CANCELLED = 4060
    ORDER_QUERY_OK = 4070
    OPEN_ORDERS_OK = 4080
    TRADES_QUERY_OK = 4090
    INVALID_ORDER_PARAMS = 4101
    SIGN_ORDER_FAILED = 4110
    POST_ORDER_FAILED = 4120
    CANCEL_ORDER_FAILED = 4130
    ORDER_NOT_FOUND = 4140
    RISK_LIMIT_EXCEEDED = 4150
    AMOUNT_TOO_SMALL = 4160
    PRICE_OUT_OF_RANGE = 4170

    # ===== 账户与持仓 (5000-5099) =====
    BALANCE_OK = 5010
    POSITIONS_OK = 5020
    BALANCE_QUERY_FAILED = 5110
    POSITIONS_QUERY_FAILED = 5120

    # ===== 风控与监控 (6000-6099) =====
    RISK_LIMITS_SET = 6010
    STATUS_OK = 6020
    HEALTH_CHECK_OK = 6030
    RISK_CHECK_FAILED = 6110

    # ===== BTC5M 业务快捷方法 (7000-7099) =====
    BTC5M_ORDER_PLACED = 7010
    QUICK_BUY_OK = 7020
    QUICK_SELL_OK = 7030
    BTC5M_MARKET_NOT_FOUND = 7101
    BTC5M_ORDER_FAILED = 7120


# ========== V2 常量 ==========
# CLOB V2 Exchange 域 version 已升级到 "2"（订单签名）
EXCHANGE_DOMAIN_VERSION_V2 = "2"
# 订单唯一性由毫秒时间戳保证（V1 的 nonce 已废弃）
ORDER_TIMESTAMP_MS_FALLBACK = 0
# 批量下单上限（Polymarket 官方约束）
BATCH_POST_ORDERS_MAX = 15

# ========== REST API 主机地址 ==========
# CLOB API 主机（主网）：查看价格和订单簿，下单和管理订单
POLY_CLOB_HOST_MAINNET = "https://clob.polymarket.com"
# CLOB API 主机（测试环境）：用于开发和测试
POLY_CLOB_HOST_STAGING = "https://clob-staging.polymarket.com"

# Gamma API 主机：发现事件和市场，检索相关元数据
POLY_GAMMA_HOST = "https://gamma-api.polymarket.com"

# Data API 主机：分析仓位、活动和市场参与情况
POLY_DATA_HOST = "https://data-api.polymarket.com"

# Relayer V2 API 主机：提交钱包交易时无需账户持有 POL 作为 gas 费用
POLY_RELAYER_V2_HOST = "https://relayer-v2.polymarket.com"

# ========== 链 ID 配置 ==========
# Polygon 主网链 ID
CHAIN_ID_POLYGON = 137
# Polygon Amoy 测试网链 ID（用于开发测试）
CHAIN_ID_AMOY = 80002

# ========== WebSocket 端点配置 ==========
# CLOB 市场通道：关注公开订单簿、价格和市场生命周期更新
WS_CLOB_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
# CLOB 用户通道：关注账户的已验证订单和交易更新
WS_CLOB_USER_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
# 实时数字信号系统：实时显示公开参考价格、评论和交易活动
WS_LIVE_DATA_URL = "wss://ws-live-data.polymarket.com"
# 体育 WebSocket：关注公开的实时比赛状态和比分
WS_SPORTS_API_URL = "wss://sports-api.polymarket.com/ws"
# CLOB V2 合约地址（主网）
CTF_EXCHANGE_V2 = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
USDC_E_POLYGON = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"


# ========== 数据结构 ==========
# 网关状态数据类（实时健康摘要）
@dataclass
class GatewayStatus:
    """网关状态（实时健康摘要）"""

    name: str = ""
    ready: bool = False
    sdk_available: bool = False
    host: str = ""
    chain_id: int = 0
    wallet_address: str = ""
    l2_creds_configured: bool = False
    http_client_open: bool = False
    last_ping_success: bool = False
    last_ping_at: str = ""
    last_error: str = ""
    sdk_version: str = "n/a"


# 订单簿快照数据类（V2 标准结构）
@dataclass
class OrderBookSnapshot:
    """订单簿快照（V2 标准结构）"""

    token_id: str
    bids: list[dict] = field(default_factory=list)  # [{price, size}, ...]
    asks: list[dict] = field(default_factory=list)  # [{price, size}, ...]
    midpoint: float = 0.0
    spread: float = 0.0
    tick_size: str = "0.01"
    neg_risk: bool = False
    fetched_at: str = ""


# 下单结果数据类（统一封装）
@dataclass
class PlaceOrderResult:
    """下单结果（统一封装）"""

    success: bool
    order_id: str = ""
    status: str = ""  # matched / live / delayed / unmatched
    error_msg: str = ""
    raw: dict = field(default_factory=dict)


# ========== 主类：PolymarketGateway ==========
pass


# Polymarket 网关管理主类（V2 协议 + 业务封装）
class PolymarketGateway(BaseGateway):
    """Polymarket 网关管理类（V2 协议 + 业务封装）

    核心职责：
    - 生命周期：connect / close / ping / is_ready
    - 认证：L1 钱包签名 + L2 API Key 派生 / 注入
    - 市场：列表 / 详情 / 中间价 / 订单簿 / 价差
    - 订单：限价单 / 市价单 / 批量 / 撤单 / 查单
    - 账户：余额 / 持仓 / 成交历史
    - 业务快捷：BTC 5min 一键下单
    - 监控：get_status / health_check

    底层适配：
    - 若安装 polymarket-client（统一 SDK），优先走 SDK
    - 否则走 V2 HTTP 直连（自签 EIP-712 v2 域）

    继承自 BaseGateway：
    - 自动获得 connect/close/get_status/health_check 模板方法
    - 只需实现 name / is_ready / _do_ping
    """

    # 基类要求：平台名
    name: str = "polymarket"

    # 构造函数（初始化网关参数）
    def __init__(
            self,
            host: str | None = None,
            chain_id: int | None = None,
            private_key: str | None = None,
            wallet_address: str | None = None,
            api_key: str | None = None,
            api_secret: str | None = None,
            api_passphrase: str | None = None,
            signature_type: int = 0,  # 0=EOA 1=POLY_PROXY 2=POLY_GNOSIS_SAFE
            funder_address: str | None = None,
            timeout: float = 10.0,
    ) -> None:
        # 调用基类初始化（host / chain_id / http_timeout）
        super().__init__(
            host=host or POLY_CLOB_HOST_MAINNET,
            chain_id=chain_id or CHAIN_ID_POLYGON,
            http_timeout=timeout,
        )
        # 钱包与认证
        self.private_key = private_key or settings.POLYMARKET_PRIVATE_KEY
        self.wallet_address = wallet_address or settings.POLYMARKET_WALLET_ADDRESS
        self.api_key = api_key or settings.POLYMARKET_APIKEY
        self.api_secret = api_secret or ""
        self.api_passphrase = api_passphrase or ""
        self.signature_type = signature_type
        self.funder_address = funder_address or self.wallet_address
        # 内部状态
        self._l2_creds: dict | None = None  # {apiKey, secret, passphrase}
        self._sdk_client: Any = None
        self._account: Account | None = None
        # 风控默认值（业务层可覆盖）
        self._risk_max_amount_usd: float = 50.0
        self._risk_price_floor: float = 0.05
        self._risk_price_cap: float = 0.95
        self._risk_max_open_orders: int = 5
        # 加载钱包
        if self.private_key:
            try:
                self._account = Account.from_key(self.private_key)
                # 若未显式提供 wallet_address，则从私钥推导
                if not self.wallet_address and self._account:
                    self.wallet_address = self._account.address
                    self.funder_address = self.funder_address or self._account.address
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[POLY-GW] wallet load failed:{e}，traceback: {traceback.format_exc()}")

    # ===== 1. 抽象方法实现（BaseGateway 要求） =====
    def is_ready(self) -> bool:
        """判断网关是否已就绪（钱包 + HTTP 客户端）"""
        return bool(
            self._account
            and self.wallet_address
            and self._http is not None
            and not self._http.is_closed
        )

    # 判断是否加载了官方统一 SDK
    def is_sdk_available(self) -> bool:
        """判断是否加载了官方统一 SDK"""
        return _HAS_SDK and self._sdk_client is not None

    # Polymarket 特定连通性探测
    async def _do_ping(self) -> dict:
        """Polymarket 特定连通性探测（GET /markets?limit=1）"""
        try:
            client = await self._get_http()
            url = f"{self.host}/markets"
            logger.debug(f"[POLY-GW] ping → GET {url}?limit=1")
            resp = await client.get(url, params={"limit": 1}, timeout=10)
            if resp.status_code == 200:
                logger.info(f"[POLY-GW] ping OK (HTTP {resp.status_code})")
                return {
                    "success": True,
                    "code": GatewayCode.PING_OK,
                    "msg": "ping successful",
                    "data": {
                        "status": resp.status_code,
                        "at": datetime.utcnow().isoformat(),
                        "host": self.host
                    }
                }
            else:
                err_msg = f"[{mName}]HTTP {resp.status_code}: {resp.text[:200] if hasattr(resp, 'text') else 'no text'}"
                logger.warning(f"[POLY-GW] ping failed: {err_msg}")
                return {
                    "success": False,
                    "code": GatewayCode.PING_FAILED,
                    "msg": err_msg,
                    "data": {
                        "status": resp.status_code,
                        "at": datetime.utcnow().isoformat()
                    }
                }
        except httpx.TimeoutException as e:
            err_msg = f"[{mName}]timeout:{e}，traceback: {traceback.format_exc()}"
            logger.error(f"[POLY-GW] ping {err_msg}")
            return {
                "success": False,
                "code": GatewayCode.TIMEOUT,
                "msg": err_msg,
                "data": {"status": 408, "at": datetime.utcnow().isoformat(), "host": self.host}
            }
        except httpx.ConnectError as e:
            err_msg = f"[{mName}]connect error:{e}，traceback: {traceback.format_exc()}"
            logger.error(f"[POLY-GW] ping {err_msg}")
            return {
                "success": False,
                "code": GatewayCode.CONNECT_ERROR,
                "msg": err_msg,
                "data": {"status": 503, "at": datetime.utcnow().isoformat(), "host": self.host}
            }
        except Exception as e:  # noqa: BLE001
            err_msg = str(e) or type(e).__name__
            logger.error(f"[POLY-GW] ping exception: {err_msg}")
            return {
                "success": False,
                "code": GatewayCode.UNKNOWN_ERROR,
                "msg": err_msg,
                "data": {"status": 500, "at": datetime.utcnow().isoformat(), "host": self.host}
            }

    # 初始化连接（建 HTTP 客户端 + 加载 SDK）
    async def connect(self) -> None:
        """初始化连接（建 HTTP 客户端 + 加载 SDK）"""
        await super().connect()  # 走基类创建 HTTP 客户端
        # 尝试加载 SDK（不强制）
        if _HAS_SDK and self._sdk_client is None and self._account:
            try:
                creds = ApiKeyCreds(
                    apiKey=self.api_key or "",
                    secret=self.api_secret or "",
                    passphrase=self.api_passphrase or "",
                )
                self._sdk_client = AsyncSecureClient(
                    host=self.host,
                    chain_id=self.chain_id,
                    key=self.private_key,
                    creds=creds,
                    signature_type=self.signature_type,
                    funder=self.funder_address,
                )
                logger.info(f"[POLY-GW] SDK loaded, host={self.host} chain={self.chain_id}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[POLY-GW] SDK init failed, fallback to HTTP:{e}，traceback: {traceback.format_exc()}")
                self._sdk_client = None
        logger.info(
            f"[POLY-GW] connected: host={self.host} chain={self.chain_id} "
            f"wallet={self.wallet_address[:10] if self.wallet_address else 'none'} "
            f"sdk={_HAS_SDK}"
        )

    # ===== 2. 认证管理（L1 + L2）=====
    pass

    # L1 EIP-712 签名（用于创建/派生 L2 API Key）
    def _sign_l1(self, nonce: int = 0) -> str:
        """L1 EIP-712 签名（用于创建/派生 L2 API Key）"""
        if not self._account:
            raise RuntimeError("Polymarket wallet not configured")
        ts = int(time.time())
        if nonce == 0:
            nonce = ts
        payload = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                ],
                "ClobAuth": [
                    {"name": "address", "type": "address"},
                    {"name": "timestamp", "type": "string"},
                    {"name": "nonce", "type": "uint256"},
                ],
            },
            "primaryType": "ClobAuth",
            "domain": {"name": "ClobAuthDomain", "version": "1", "chainId": self.chain_id},
            "message": {
                "address": self.wallet_address,
                "timestamp": str(ts),
                "nonce": int(nonce),
            },
        }
        encoded = encode_typed_data(full_message=payload)
        signed = self._account.sign_message(encoded)
        return f"0x{signed.signature.hex()}"

    # 确保 L2 API Key 已就绪（不存在则创建，存在则可派生）
    async def _ensure_l2_keys(self) -> dict:
        """确保 L2 API Key 已就绪（先 derive，再 create）"""
        if self._l2_creds is not None:
            return self._l2_creds
        if not self._account:
            raise RuntimeError("Polymarket wallet not configured")
        client = await self._get_http()
        headers = {
            "POLY_ADDRESS": self.wallet_address,
            "POLY_SIGNATURE": self._sign_l1(),
            "POLY_TIMESTAMP": str(int(time.time())),
            "POLY_NONCE": "0",
        }
        # 1) 尝试 derive（已有则返回）
        try:
            r = await client.get(f"{self.host}/auth/derive-api-key", headers=headers)
            if r.status_code == 200:
                self._l2_creds = r.json()
                return self._l2_creds
        except Exception:  # noqa: BLE001
            pass
        # 2) derive 失败则 create
        r = await client.post(f"{self.host}/auth/api-key", headers=headers)
        if r.status_code == 200:
            self._l2_creds = r.json()
        else:
            # 3) 退化：使用 env 注入的凭据
            self._l2_creds = {
                "apiKey": self.api_key or "",
                "secret": self.api_secret or "",
                "passphrase": self.api_passphrase or "",
            }
        return self._l2_creds

    # 对外暴露：创建或派生 L2 API Key（返回 {apiKey, secret, passphrase}）
    async def create_or_derive_api_key(self) -> dict:
        """对外暴露：创建或派生 L2 API Key（返回 {apiKey, secret, passphrase}）"""
        self._l2_creds = None
        creds = await self._ensure_l2_creds()
        return {
            "apiKey": creds.get("apiKey", "")[:8] + "***",  # 脱敏
            "has_secret": bool(creds.get("secret")),
            "has_passphrase": bool(creds.get("passphrase")),
        }

    # 对外暴露：注入外部 L2 凭据
    def set_api_creds(self, api_key: str, secret: str, passphrase: str) -> None:
        """对外暴露：注入外部 L2 凭据（覆盖已派生的）"""
        self._l2_creds = {"apiKey": api_key, "secret": secret, "passphrase": passphrase}

    def get_api_creds(self) -> dict:
        """对外暴露：返回当前 L2 凭据（脱敏）"""
        if not self._l2_creds:
            return {"configured": False}
        return {
            "configured": True,
            "apiKey": (self._l2_creds.get("apiKey") or "")[:8] + "***",
            "has_secret": bool(self._l2_creds.get("secret")),
            "has_passphrase": bool(self._l2_creds.get("passphrase")),
        }

    # 对外暴露：强制重新派生 L2 API Key
    async def refresh_api_creds(self) -> dict:
        """对外暴露：强制重新派生 L2 API Key"""
        return await self.create_or_derive_api_key()

    # ===== 3. 统一 HTTP 请求 =====
    pass

    # L2 HMAC 签名（Base64 编码的 SHA256）
    def _l2_headers(self, method: str, path: str, body: str = "") -> dict:
        """L2 HMAC 签名（Base64 编码的 SHA256）"""
        ts = str(int(time.time()))
        msg = ts + method.upper() + path + body
        secret = (self._l2_creds or {}).get("secret", "").encode("utf-8")
        sig = ""
        if secret:
            sig = base64.b64encode(
                hmac.new(secret, msg.encode("utf-8"), hashlib.sha256).digest()
            ).decode()
        return {
            "POLY_ADDRESS": self.wallet_address or "",
            "POLY_API_KEY": (self._l2_creds or {}).get("apiKey", ""),
            "POLY_PASSPHRASE": (self._l2_creds or {}).get("passphrase", ""),
            "POLY_TIMESTAMP": ts,
            "POLY_SIGNATURE": sig,
            "Content-Type": "application/json",
        }

    # 统一 Polymarket 请求封装
    async def _request(
            self,
            method: str,
            path: str,
            body: dict | None = None,
            auth_l1: bool = False,
            need_l2: bool = True,
    ) -> dict:
        """统一 Polymarket 请求封装（返回统一格式：success/code/msg/data）"""
        try:
            body_str = json.dumps(body, separators=(",", ":")) if body else ""
            client = await self._get_http()  # 用基类方法
            if auth_l1:
                headers = {
                    "POLY_ADDRESS": self.wallet_address or "",
                    "POLY_SIGNATURE": self._sign_l1() if self._account else "",
                    "POLY_TIMESTAMP": str(int(time.time())),
                    "POLY_NONCE": "0",
                    "Content-Type": "application/json",
                }
            elif need_l2:
                await self._ensure_l2_creds()
                headers = self._l2_headers(method, path, body_str)
            else:
                headers = {"Content-Type": "application/json"}
            url = f"{self.host}{path}"
            if method == "GET":
                resp = await client.get(url, headers=headers, params=body or None)
            elif method == "POST":
                resp = await client.post(url, headers=headers, content=body_str)
            elif method == "DELETE":
                resp = await client.delete(url, headers=headers, params=body or None)
            else:
                return {
                    "success": False,
                    "code": GatewayCode.UNKNOWN_ERROR,
                    "msg": f"unsupported HTTP method: {method}",
                    "data": {"method": method, "path": path}
                }
            try:
                result_data = resp.json()
            except Exception:
                result_data = {"raw_response": resp.text[:300], "status": resp.status_code}

            if resp.status_code >= 400:
                err_msg = f"[{mName}]HTTP {resp.status_code}: {result_data}"
                logger.warning(f"[POLY-GW] {method} {path} {err_msg}")
                self._last_error = err_msg
                return {
                    "success": False,
                    "code": GatewayCode.NETWORK_ERROR + (resp.status_code % 100),
                    "msg": err_msg,
                    "data": {
                        "status": resp.status_code,
                        "response": result_data,
                        "method": method,
                        "path": path
                    }
                }

            return {
                "success": True,
                "code": GatewayCode.SUCCESS,
                "msg": f"{method} {path} successful",
                "data": result_data
            }

        except httpx.TimeoutException as e:
            err_msg = f"[{mName}]timeout: {e}"
            logger.error(f"[POLY-GW] {method} {path} {err_msg}")
            return {
                "success": False,
                "code": GatewayCode.TIMEOUT,
                "msg": err_msg,
                "data": {"method": method, "path": path}
            }
        except httpx.ConnectError as e:
            err_msg = f"[{mName}]connect error: {e}"
            logger.error(f"[POLY-GW] {method} {path} {err_msg}")
            return {
                "success": False,
                "code": GatewayCode.CONNECT_ERROR,
                "msg": err_msg,
                "data": {"method": method, "path": path}
            }
        except Exception as e:  # noqa: BLE001
            err_msg = str(e) or type(e).__name__
            logger.error(f"[POLY-GW] {method} {path} exception: {err_msg}")
            return {
                "success": False,
                "code": GatewayCode.UNKNOWN_ERROR,
                "msg": err_msg,
                "data": {"method": method, "path": path}
            }

    # ===== 4. 市场查询 =====
    pass

    # 分页查询市场列表（GET /markets）
    async def get_markets(
            self,
            next_cursor: str = "",
            limit: int = 50,
            active: bool = True,
            closed: bool = False,
            archived: bool = False,
            tag_slug: str | None = None,
    ) -> dict:
        """分页查询市场列表（GET /markets，支持 keyset 游标分页）"""
        params: dict[str, Any] = {
            "limit": min(limit, 100),  # V2 限制 max=100
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "archived": str(archived).lower(),
        }
        if next_cursor:
            params["next_cursor"] = next_cursor
        if tag_slug:
            params["tag_slug"] = tag_slug
        result = await self._request("GET", "/markets", body=params, need_l2=False)
        if result.get("success"):
            result["code"] = GatewayCode.MARKET_LIST_OK
            result["msg"] = f"got {len(result.get('data', {}).get('data', []))} markets"
        else:
            result["code"] = GatewayCode.QUERY_FAILED
        return result

    # 查询单个市场详情（GET /markets/{condition_id}）
    async def get_market(self, condition_id: str) -> dict:
        """查询单个市场详情（GET /markets/{condition_id}）"""
        result = await self._request("GET", f"/markets/{condition_id}", need_l2=False)
        if result.get("success"):
            result["code"] = GatewayCode.MARKET_DETAIL_OK
            result["msg"] = f"market {condition_id[:12]}... found"
        else:
            result["code"] = GatewayCode.MARKET_NOT_FOUND
        return result

    # 按 slug 查市场（GET /markets?slug=...)
    async def get_market_by_slug(self, slug: str) -> dict:
        """按 slug 查市场（GET /markets?slug=...）"""
        result = await self._request(
            "GET", "/markets", body={"slug": slug}, need_l2=False
        )
        if result.get("success"):
            result["code"] = GatewayCode.MARKET_DETAIL_OK
            result["msg"] = f"market slug '{slug}' found"
        else:
            result["code"] = GatewayCode.MARKET_NOT_FOUND
        return result

    # 查询 token 中间价（GET /midpoint）
    async def get_midpoint(self, token_id: str) -> dict:
        """查询 token 中间价（GET /midpoint）"""
        result = await self._request(
            "GET", "/midpoint", body={"token_id": token_id}, need_l2=False
        )
        if result.get("success"):
            result["code"] = GatewayCode.MIDPOINT_OK
            midpoint = float((result.get("data") or {}).get("mid", 0.0))
            result["data"] = {"token_id": token_id, "midpoint": midpoint}
        else:
            result["code"] = GatewayCode.INVALID_TOKEN_ID
        return result

    # 查询 token 单边价格（GET /price）
    async def get_price(self, token_id: str, side: str = "BUY") -> dict:
        """查询 token 单边价格（GET /price）"""
        result = await self._request(
            "GET",
            "/price",
            body={"token_id": token_id, "side": side.upper()},
            need_l2=False,
        )
        if result.get("success"):
            result["code"] = GatewayCode.PRICE_OK
            price = float((result.get("data") or {}).get("price", 0.0))
            result["data"] = {"token_id": token_id, "side": side.upper(), "price": price}
        else:
            result["code"] = GatewayCode.INVALID_TOKEN_ID
        return result

    # 查询订单簿（GET /book）
    async def get_order_book(self, token_id: str) -> dict:
        """查询订单簿（GET /book，返回统一格式 + OrderBookSnapshot 数据）"""
        result = await self._request(
            "GET", "/book", body={"token_id": token_id}, need_l2=False
        )
        if result.get("success"):
            data = result.get("data") or {}
            snap = OrderBookSnapshot(
                token_id=token_id,
                bids=data.get("bids", []),
                asks=data.get("asks", []),
                midpoint=float(data.get("midpoint", 0)),
                spread=float(data.get("spread", 0)),
                tick_size=str(data.get("tick_size", "0.01")),
                neg_risk=bool(data.get("neg_risk", False)),
                fetched_at=datetime.utcnow().isoformat(),
            )
            result["code"] = GatewayCode.ORDERBOOK_OK
            result["msg"] = f"orderbook for {token_id[:16]}... retrieved"
            result["snapshot"] = asdict(snap)
        else:
            result["code"] = GatewayCode.INVALID_TOKEN_ID
        return result

    # 查询买卖价差（GET /spread）
    async def get_spread(self, token_id: str) -> dict:
        """查询买卖价差（GET /spread）"""
        result = await self._request(
            "GET", "/spread", body={"token_id": token_id}, need_l2=False
        )
        if result.get("success"):
            result["code"] = GatewayCode.SPREAD_OK
            spread = float((result.get("data") or {}).get("spread", 0.0))
            result["data"] = {"token_id": token_id, "spread": spread}
        else:
            result["code"] = GatewayCode.INVALID_TOKEN_ID
        return result

    # 查询 token 最小变动单位（GET /tick-size）
    async def get_tick_size(self, token_id: str) -> dict:
        """查询 token 最小变动单位（GET /tick-size）"""
        result = await self._request(
            "GET", "/tick-size", body={"token_id": token_id}, need_l2=False
        )
        if result.get("success"):
            result["code"] = GatewayCode.TICK_SIZE_OK
            tick_size = str((result.get("data") or {}).get("minimum_tick_size", "0.01"))
            result["data"] = {"token_id": token_id, "tick_size": tick_size}
        else:
            result["code"] = GatewayCode.INVALID_TOKEN_ID
        return result

    # 查询当前活跃的 BTC 5min 涨跌市场（Gamma API，通过 tag 过滤 + slug 前缀二次筛选）
    async def get_active_btc5m_market(
            self,
            slug_prefix: str | None = None,
            target_epoch: int | None = None,
    ) -> dict:
        """查询当前活跃的 BTC 5min 涨跌市场（Gamma API）

        Args:
            slug_prefix: slug 前缀（默认取 settings.POLYMARKET_BTC5M_SLUG_PREFIX）
            target_epoch: 目标 5 分钟窗口的 Unix 时间戳（秒）。
                - None（默认）: 拉取当前最活跃市场（原有行为）
                - 精确时间戳: 用 slug=前缀-时间戳 精确定位该窗口，
                  例如 target_epoch=1785399000 → slug=btc-updown-5m-1785399000
                - 未来时间戳同理（市场尚未开始也可查询到，前提是 Gamma 已收录）
        """
        prefix = slug_prefix or settings.POLYMARKET_BTC5M_SLUG_PREFIX
        if target_epoch:
            # 精确模式：按完整 slug 查询（如 btc-updown-5m-1785399000）
            full_slug = f"{prefix}-{target_epoch}"
            path = (
                f"/markets?slug={full_slug}"
                "&active=true&closed=false&archived=false&limit=5"
            )
        else:
            # 默认模式：按 tag 模糊查询，取最近活跃
            path = (
                f"/markets?tag={prefix}"
                "&active=true&closed=false&archived=false"
                "&order=startDate&ascending=false&limit=50"
            )
        client = await self._get_http()
        try:
            resp = await client.get(POLY_GAMMA_HOST + path, headers={"Content-Type": "application/json"})
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                if not target_epoch:
                    # 默认模式二次筛选：确保 slug 匹配前缀
                    data = [m for m in data if (m.get("slug") or "").startswith(prefix)]
                if not data:
                    return {
                        "success": False,
                        "code": GatewayCode.BTC5M_MARKET_NOT_FOUND,
                        "msg": f"no BTC 5min market found (slug={full_slug if target_epoch else prefix})",
                        "data": {"slug_prefix": prefix, "target_epoch": target_epoch}
                    }
                return {
                    "success": True,
                    "code": GatewayCode.BTC5M_MARKET_FOUND,
                    "msg": f"found {len(data)} BTC 5min market(s) (target_epoch={target_epoch})",
                    "data": data[0] if len(data) == 1 else data,
                    "count": len(data),
                    "target_epoch": target_epoch,
                }
            else:
                return {
                    "success": False,
                    "code": GatewayCode.BTC5M_MARKET_NOT_FOUND,
                    "msg": f"no BTC 5min market found (slug={full_slug if target_epoch else prefix})",
                    "data": {"slug_prefix": prefix, "target_epoch": target_epoch}
                }
        except Exception as e:  # noqa: BLE001
            err_msg = f"[{mName}]get_active_btc5m_market failed: {e}"
            logger.warning(f"[POLY-GW] {err_msg}")
            return {
                "success": False,
                "code": GatewayCode.QUERY_FAILED,
                "msg": err_msg,
                "data": {"slug_prefix": prefix, "target_epoch": target_epoch}
            }

    # ===== 5. 订单管理（V2 风格）=====
    pass

    # V2 订单 EIP-712 签名（Exchange 域 version=2）
    def _sign_order_v2(
            self,
            token_id: str,
            price: float,
            side: str,
            size: float,
            fee_rate_bps: int = 0,
            expiration: int = 0,
            metadata: str = "",
    ) -> dict:
        """V2 订单 EIP-712 签名（Exchange 域 version=2，移除 nonce/feeRateBps/taker）"""
        if not self._account:
            raise RuntimeError("Polymarket wallet not configured")
        # tokenId 转 uint256
        if token_id.startswith("0x"):
            token_uint = int(token_id, 16)
        else:
            token_uint = int(token_id)
        # 毫秒时间戳（V2 唯一性来源）
        timestamp_ms = int(time.time() * 1000)
        # 过期时间（默认 24h）
        if expiration == 0:
            expiration = int(time.time()) + 86400
        # 份额与价格 USDC 6 位精度
        maker_amount = int(size * 1e6)
        taker_amount = int(size * price * 1e6)
        order = {
            "salt": int.from_bytes(uuid.uuid4().bytes, "big") % (2 ** 128),
            "maker": self.funder_address or self.wallet_address,
            "signer": self.wallet_address,
            "taker": "0x0000000000000000000000000000000000000000",
            "tokenId": str(token_uint),
            "makerAmount": maker_amount,
            "takerAmount": taker_amount,
            "expiration": str(expiration),
            "nonce": "0",
            "feeRateBps": str(fee_rate_bps),
            "side": "0" if side.upper() == "BUY" else "1",
            "signatureType": self.signature_type,
            "timestamp": timestamp_ms,  # V2 新增毫秒时间戳
            "metadata": metadata,  # V2 新增（可放策略 ID / 用户备注）
        }
        # V2 域：version = "2"
        domain = {
            "name": "Polymarket CTF Exchange",
            "version": EXCHANGE_DOMAIN_VERSION_V2,
            "chainId": self.chain_id,
            "verifyingContract": CTF_EXCHANGE_V2,
        }
        types = {
            "Order": [
                {"name": "salt", "type": "uint256"},
                {"name": "maker", "type": "address"},
                {"name": "signer", "type": "address"},
                {"name": "taker", "type": "address"},
                {"name": "tokenId", "type": "uint256"},
                {"name": "makerAmount", "type": "uint256"},
                {"name": "takerAmount", "type": "uint256"},
                {"name": "expiration", "type": "uint256"},
                {"name": "nonce", "type": "uint256"},
                {"name": "feeRateBps", "type": "uint256"},
                {"name": "side", "type": "uint8"},
                {"name": "signatureType", "type": "uint8"},
                {"name": "timestamp", "type": "uint256"},
                {"name": "metadata", "type": "string"},
            ],
        }
        payload = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                **types,
            },
            "primaryType": "Order",
            "domain": domain,
            "message": order,
        }
        encoded = encode_typed_data(full_message=payload)
        signed = self._account.sign_message(encoded)
        order["signature"] = f"0x{signed.signature.hex()}"
        return order

    # 创建 V2 已签名订单（不提交，便于上层 pre-check）
    async def create_order(
            self,
            token_id: str,
            price: float,
            size: float,
            side: str = "BUY",
            expiration: int = 0,
            metadata: str = "",
    ) -> dict:
        """创建 V2 已签名订单（不提交，便于上层 pre-check）"""
        try:
            if not self._account:
                return {
                    "success": False,
                    "code": GatewayCode.WALLET_NOT_CONFIGURED,
                    "msg": "wallet not configured for signing"
                }
            signed = self._sign_order_v2(
                token_id=token_id,
                price=price,
                side=side,
                size=size,
                expiration=expiration,
                metadata=metadata,
            )
            return {
                "success": True,
                "code": GatewayCode.ORDER_CREATED,
                "msg": f"order created for {token_id[:16]}... {side} {size}@{price}",
                "data": signed
            }
        except Exception as e:  # noqa: BLE001
            err_msg = f"[{mName}]create_order failed: {e}"
            logger.error(f"[POLY-GW] {err_msg}")
            return {
                "success": False,
                "code": GatewayCode.SIGN_ORDER_FAILED,
                "msg": err_msg,
                "data": {"token_id": token_id, "side": side}
            }

    # 提交单个已签名订单（POST /order）
    async def post_order(self, signed_order: dict, order_type: str = "GTC") -> dict:
        """提交单个已签名订单（POST /order）"""
        body = {"order": signed_order, "owner": self.wallet_address, "orderType": order_type}
        result = await self._request("POST", "/order", body=body)
        if result.get("success"):
            result["code"] = GatewayCode.ORDER_POSTED
            result["msg"] = f"order posted (type={order_type})"
        else:
            result["code"] = GatewayCode.POST_ORDER_FAILED
        return result

    # 批量提交订单（POST /orders，单批上限 15）
    async def post_orders(self, signed_orders: list[dict], order_type: str = "GTC") -> dict:
        """批量提交订单（POST /orders，单批上限 15）"""
        if len(signed_orders) > BATCH_POST_ORDERS_MAX:
            return {
                "success": False,
                "code": GatewayCode.INVALID_ORDER_PARAMS,
                "msg": f"batch size {len(signed_orders)} > max {BATCH_POST_ORDERS_MAX}",
                "data": {"batch_size": len(signed_orders), "max": BATCH_POST_ORDERS_MAX}
            }
        body = {
            "orders": [
                {"order": so, "owner": self.wallet_address, "orderType": order_type}
                for so in signed_orders
            ]
        }
        result = await self._request("POST", "/orders", body=body)
        if result.get("success"):
            result["code"] = GatewayCode.ORDERS_BATCH_POSTED
            result["msg"] = f"{len(signed_orders)} orders posted (type={order_type})"
        else:
            result["code"] = GatewayCode.POST_ORDER_FAILED
        return result

    # 一步限价下单（创建签名 + 提交）
    async def place_limit_order(
            self,
            token_id: str,
            side: str,
            price: float,
            size: float,
            order_type: str = "GTC",
            expiration: int = 0,
            metadata: str = "",
    ) -> dict:
        """一步限价下单（创建签名 + 提交，返回统一格式）"""
        if not self._account:
            return {
                "success": False,
                "code": GatewayCode.WALLET_NOT_CONFIGURED,
                "msg": "wallet not configured",
                "data": {"token_id": token_id}
            }
        try:
            signed = self._sign_order_v2(
                token_id=token_id,
                price=price,
                side=side,
                size=size,
                expiration=expiration,
                metadata=metadata,
            )
            result = await self.post_order(signed, order_type=order_type)
            if result.get("success"):
                result["code"] = GatewayCode.ORDER_POSTED
                result["msg"] = f"limit order placed: {side} {size}@{price}"
            else:
                result["code"] = GatewayCode.POST_ORDER_FAILED
            return result
        except Exception as e:  # noqa: BLE001
            err_msg = f"[{mName}]place_limit_order failed: {e}"
            logger.error(f"[POLY-GW] {err_msg}")
            return {
                "success": False,
                "code": GatewayCode.SIGN_ORDER_FAILED,
                "msg": err_msg,
                "data": {"token_id": token_id, "side": side}
            }

    # 市价下单（FOK=全部成交或撤 / FAK=部分成交）
    async def place_market_order(
            self,
            token_id: str,
            side: str,
            amount: float,
            order_type: str = "FOK",
    ) -> dict:
        """市价下单（FOK=全部成交或撤 / FAK=部分成交，V2 市价单 amount 为 USDC 数量）"""
        return await self.place_limit_order(
            token_id=token_id,
            side=side,
            price=0.99 if side.upper() == "BUY" else 0.01,
            size=amount,
            order_type=order_type,
            metadata=f"market_order:{side}",
        )

    # 撤单（DELETE /order/{order_id}）
    async def cancel_order(self, order_id: str) -> dict:
        """撤单（DELETE /order/{order_id}）"""
        result = await self._request("DELETE", f"/order/{order_id}")
        if result.get("success"):
            result["code"] = GatewayCode.ORDER_CANCELLED
            result["msg"] = f"order {order_id[:12]}... cancelled"
        else:
            result["code"] = GatewayCode.CANCEL_ORDER_FAILED
        return result

    # 批量撤单
    async def cancel_orders(self, order_ids: list[str]) -> dict:
        """批量撤单（DELETE /orders）"""
        result = await self._request("DELETE", "/orders", body={"orderIds": order_ids})
        if result.get("success"):
            result["code"] = GatewayCode.ORDERS_CANCELLED
            result["msg"] = f"{len(order_ids)} orders cancel requested"
        else:
            result["code"] = GatewayCode.CANCEL_ORDER_FAILED
        return result

    # 全撤指定市场的挂单（DELETE /cancel-all）
    async def cancel_all_orders(self, market: str | None = None) -> dict:
        """全撤指定市场的挂单（DELETE /cancel-all）"""
        body = {}
        if market:
            body["market"] = market
        result = await self._request("DELETE", "/cancel-all", body=body or None)
        if result.get("success"):
            result["code"] = GatewayCode.ORDERS_ALL_CANCELLED
            result["msg"] = f"all orders cancelled" + (f" for market {market}" if market else "")
        else:
            result["code"] = GatewayCode.CANCEL_ORDER_FAILED
        return result

    # 查询单笔订单（GET /order/{order_id}）
    async def get_order(self, order_id: str) -> dict:
        """查询单笔订单（GET /order/{order_id}）"""
        result = await self._request("GET", f"/order/{order_id}")
        if result.get("success"):
            result["code"] = GatewayCode.ORDER_QUERY_OK
            result["msg"] = f"order {order_id[:12]}... found"
        else:
            result["code"] = GatewayCode.ORDER_NOT_FOUND
        return result

    # 查询当前挂单（GET /orders）
    async def get_open_orders(self, market: str | None = None) -> dict:
        """查询当前挂单（GET /orders）"""
        params: dict[str, Any] = {}
        if market:
            params["market"] = market
        result = await self._request("GET", "/orders", body=params)
        if result.get("success"):
            data = result.get("data") or []
            orders = data if isinstance(data, list) else [data]
            result["code"] = GatewayCode.OPEN_ORDERS_OK
            result["msg"] = f"{len(orders)} open orders found"
            result["data"] = orders
        else:
            result["code"] = GatewayCode.QUERY_FAILED
        return result

    # 查询成交历史（GET /trades）
    async def get_trades(
            self,
            market: str | None = None,
            limit: int = 100,
    ) -> dict:
        """查询成交历史（GET /trades）"""
        params: dict[str, Any] = {"limit": min(limit, 100)}
        if market:
            params["market"] = market
        result = await self._request("GET", "/trades", body=params)
        if result.get("success"):
            data = result.get("data") or []
            trades = data if isinstance(data, list) else [data]
            result["code"] = GatewayCode.TRADES_QUERY_OK
            result["msg"] = f"{len(trades)} trades found"
            result["data"] = trades
        else:
            result["code"] = GatewayCode.QUERY_FAILED
        return result

    # ===== 5. 账户与持仓 =====
    pass

    # 扫描并赎回所有已结算市场的获胜持仓
    async def redeem_resolved_positions(self, redeem_all: bool = True) -> dict:
        """扫描钱包所有持仓，找到已结算市场并批量赎回

        逻辑：
        1. 扫描钱包所有持仓（data-api /positions）
        2. 对每个持仓查询对应市场状态，判断是否已结算（resolved=true）
        3. 已结算且持仓 > 0 的，调用 redeemPositions 做链上批量赎回
        4. 已关闭但未结算的市场不做任何操作（卖出会失败）
        5. 没有待赎回仓位时不发链上交易

        Args:
            redeem_all: True=赎回所有已结算持仓，False=仅当市场 resolved 时赎回

        Returns:
            dict: {success, redeemed_count, redeemed_positions, errors}
        """
        if not self.wallet_address:
            return {
                "success": False,
                "code": GatewayCode.WALLET_NOT_CONFIGURED,
                "msg": "wallet address not configured",
                "redeemed_count": 0,
                "redeemed_positions": [],
                "errors": [],
            }

        errors = []
        positions_to_redeem = []

        try:
            pos_result = await self.get_positions(size_greater_than=0.001)
            if not pos_result.get("success"):
                return {
                    "success": False,
                    "code": GatewayCode.POSITIONS_QUERY_FAILED,
                    "msg": f"查询持仓失败: {pos_result.get('msg', '')}",
                    "redeemed_count": 0,
                    "redeemed_positions": [],
                    "errors": [pos_result.get('msg', '')],
                }

            all_positions = pos_result.get("data") or []
            if not all_positions:
                return {
                    "success": True,
                    "code": GatewayCode.POSITIONS_OK,
                    "msg": "无持仓，跳过赎回",
                    "redeemed_count": 0,
                    "redeemed_positions": [],
                    "errors": [],
                }

            logger.info(f"[POLY-REDEEM] 扫描到 {len(all_positions)} 个持仓，逐个检查结算状态...")

            for pos in all_positions:
                token_id = pos.get("token_id") or pos.get("tokenId", "")
                market_slug = pos.get("market") or pos.get("market_slug", "")
                size = float(pos.get("size") or 0)
                cur_price = float(pos.get("curPrice") or pos.get("price") or 0)

                if size <= 0:
                    continue

                try:
                    if market_slug:
                        market_resp = await self.get_market_by_slug(market_slug)
                        if market_resp.get("success") and market_resp.get("data"):
                            mkt = market_resp.get("data")
                            state = mkt.get("state") or {}
                            is_resolved = state.get("resolved", False)
                            is_closed = state.get("closed", False)

                            if is_resolved and is_closed:
                                condition_id = mkt.get("conditionId") or mkt.get("condition_id", "")
                                positions_to_redeem.append({
                                    "token_id": token_id,
                                    "market_slug": market_slug,
                                    "condition_id": condition_id,
                                    "size": size,
                                    "cur_price": cur_price,
                                    "is_resolved": True,
                                })
                                logger.info(
                                    f"[POLY-REDEEM] 发现已结算持仓: {market_slug} "
                                    f"token={token_id[:12]}... size={size} price={cur_price}"
                                )
                            elif is_closed and not is_resolved:
                                logger.debug(
                                    f"[POLY-REDEEM] 市场已关闭但未结算: {market_slug}，跳过"
                                )
                            else:
                                logger.debug(
                                    f"[POLY-REDEEM] 市场未结算: {market_slug} "
                                    f"(closed={is_closed}, resolved={is_resolved})，跳过"
                                )
                        else:
                            logger.warning(
                                f"[POLY-REDEEM] 查询市场失败: {market_slug}, "
                                f"将尝试直接赎回 token={token_id}"
                            )
                            if token_id:
                                positions_to_redeem.append({
                                    "token_id": token_id,
                                    "market_slug": market_slug,
                                    "condition_id": "",
                                    "size": size,
                                    "cur_price": cur_price,
                                    "is_resolved": None,
                                })
                    elif token_id:
                        positions_to_redeem.append({
                            "token_id": token_id,
                            "market_slug": "",
                            "condition_id": "",
                            "size": size,
                            "cur_price": cur_price,
                            "is_resolved": None,
                        })
                except Exception as e:
                    err_msg = f"检查持仓异常: {market_slug or token_id[:16]}... {e}"
                    logger.warning(f"[POLY-REDEEM] {err_msg}")
                    errors.append(err_msg)

            if not positions_to_redeem:
                return {
                    "success": True,
                    "code": GatewayCode.POSITIONS_OK,
                    "msg": f"扫描 {len(all_positions)} 个持仓，无已结算持仓需要赎回",
                    "redeemed_count": 0,
                    "redeemed_positions": [],
                    "errors": errors,
                }

            logger.info(
                f"[POLY-REDEEM] 共 {len(positions_to_redeem)} 个持仓可赎回，开始批量赎回..."
            )

            redeemed_results = []

            if self._sdk_client and hasattr(self._sdk_client, 'redeem_positions'):
                try:
                    condition_ids = list(set(
                        p["condition_id"] for p in positions_to_redeem
                        if p.get("condition_id")
                    ))
                    for cid in condition_ids:
                        try:
                            handle = await self._sdk_client.redeem_positions(condition_id=cid)
                            result = await handle.wait()
                            redeemed_results.append({
                                "condition_id": cid,
                                "result": str(result),
                            })
                            logger.info(f"[POLY-REDEEM] 赎回成功 condition_id={cid[:12]}... result={result}")
                        except Exception as e:
                            err_msg = f"赎回 condition_id={cid[:12]}... 失败: {e}"
                            logger.warning(f"[POLY-REDEEM] {err_msg}")
                            errors.append(err_msg)
                            redeemed_results.append({
                                "condition_id": cid,
                                "error": str(e),
                            })

                    for p in positions_to_redeem:
                        if not p.get("condition_id") and p.get("token_id"):
                            try:
                                handle = await self._sdk_client.redeem_positions(token_id=p["token_id"])
                                result = await handle.wait()
                                redeemed_results.append({
                                    "token_id": p["token_id"],
                                    "result": str(result),
                                })
                                logger.info(f"[POLY-REDEEM] 按 token 赎回成功 {p['token_id'][:12]}...")
                            except Exception as e:
                                err_msg = f"赎回 token={p['token_id'][:12]}... 失败: {e}"
                                logger.warning(f"[POLY-REDEEM] {err_msg}")
                                errors.append(err_msg)
                                redeemed_results.append({
                                    "token_id": p["token_id"],
                                    "error": str(e),
                                })
                except Exception as e:
                    err_msg = f"批量赎回异常: {e}"
                    logger.error(f"[POLY-REDEEM] {err_msg}")
                    errors.append(err_msg)
            else:
                for p in positions_to_redeem:
                    try:
                        cid = p.get("condition_id") or ""
                        tid = p.get("token_id") or ""
                        payload = {}
                        if cid:
                            payload["condition_id"] = cid
                        if tid:
                            payload["token_id"] = tid
                        if not payload:
                            continue

                        redeem_result = await self._request(
                            "POST", "/redeem", body=payload, need_l2=True
                        )
                        redeemed_results.append({
                            "market_slug": p.get("market_slug", ""),
                            "success": redeem_result.get("success"),
                            "detail": redeem_result.get("data") or redeem_result.get("msg", ""),
                        })
                        if redeem_result.get("success"):
                            logger.info(
                                f"[POLY-REDEEM] HTTP 赎回成功: {p.get('market_slug', '')} "
                                f"token={tid[:12]}..."
                            )
                        else:
                            err_msg = f"HTTP 赎回失败: {p.get('market_slug', '')} {redeem_result.get('msg', '')}"
                            logger.warning(f"[POLY-REDEEM] {err_msg}")
                            errors.append(err_msg)
                    except Exception as e:
                        err_msg = f"赎回 {p.get('market_slug') or p.get('token_id', '')} 异常: {e}"
                        logger.warning(f"[POLY-REDEEM] {err_msg}")
                        errors.append(err_msg)

            success_count = sum(1 for r in redeemed_results if r.get("success") or r.get("result"))
            total_redeem = len(redeemed_results)

            return {
                "success": True,
                "code": GatewayCode.POSITIONS_OK,
                "msg": f"扫描 {len(all_positions)} 持仓，赎回 {total_redeem} 个，成功 {success_count}",
                "redeemed_count": total_redeem,
                "redeemed_positions": positions_to_redeem,
                "redeem_results": redeemed_results,
                "errors": errors,
            }

        except Exception as e:
            err_msg = f"redeem_resolved_positions 整体异常: {e}"
            logger.error(f"[POLY-REDEEM] {err_msg}")
            return {
                "success": False,
                "code": GatewayCode.POSITIONS_QUERY_FAILED,
                "msg": err_msg,
                "redeemed_count": 0,
                "redeemed_positions": [],
                "errors": [err_msg],
            }

    # 查询钱包余额（V2 兼容：data-api 持仓 + CLOB 旧 /collateral 尝试）
    async def get_balance(self) -> dict:
        """查询钱包余额（V2 兼容：data-api 持仓 + CLOB 旧 /collateral 尝试）"""
        if not self.wallet_address:
            return {
                "success": False,
                "code": GatewayCode.WALLET_NOT_CONFIGURED,
                "msg": "wallet address not configured"
            }
        try:
            # 1) CLOB 旧 /collateral 端点（V1 兼容）
            coll = await self._request(
                "GET", f"/collateral?user={self.wallet_address}"
            )
            # 2) data-api 持仓汇总
            client = await self._get_http()
            try:
                r = await client.get(
                    f"{POLY_DATA_HOST}/positions",
                    params={
                        "user": self.wallet_address,
                        "sizeGreaterThan": 0,
                        "limit": 100,
                    },
                )
                positions = r.json() if r.status_code == 200 else []
            except Exception:  # noqa: BLE001
                positions = []
            # 持仓 USD 价值汇总
            positions_value = 0.0
            if isinstance(positions, list):
                for p in positions:
                    try:
                        size = float(p.get("size") or 0)
                        price = float(p.get("curPrice") or p.get("avgPrice") or 0)
                        positions_value += size * price
                    except Exception:  # noqa: BLE001
                        continue

            return {
                "success": True,
                "code": GatewayCode.BALANCE_OK,
                "msg": f"balance retrieved for {self.wallet_address[:12]}...",
                "data": {
                    "wallet_address": self.wallet_address,
                    "collateral_endpoint": coll,
                    "positions_count": len(positions) if isinstance(positions, list) else 0,
                    "positions_value_usd": round(positions_value, 4),
                }
            }
        except Exception as e:  # noqa: BLE001
            err_msg = f"[{mName}]get_balance failed: {e}"
            logger.error(f"[POLY-GW] {err_msg}")
            return {
                "success": False,
                "code": GatewayCode.BALANCE_QUERY_FAILED,
                "msg": err_msg,
                "data": {"wallet_address": self.wallet_address}
            }

    # 查询钱包持仓（GET data-api/positions，公开端点）
    async def get_positions(
            self,
            market: str | None = None,
            size_greater_than: float = 0.0,
    ) -> dict:
        """查询钱包持仓（GET data-api/positions，公开端点）"""
        if not self.wallet_address:
            return {
                "success": False,
                "code": GatewayCode.WALLET_NOT_CONFIGURED,
                "msg": "wallet address not configured"
            }
        params: dict[str, Any] = {
            "user": self.wallet_address,
            "limit": 100,
        }
        if market:
            params["market"] = market
        if size_greater_than > 0:
            params["sizeGreaterThan"] = size_greater_than
        client = await self._get_http()
        try:
            r = await client.get(f"{POLY_DATA_HOST}/positions", params=params)
            if r.status_code == 200:
                positions = r.json()
                return {
                    "success": True,
                    "code": GatewayCode.POSITIONS_OK,
                    "msg": f"{len(positions)} positions found",
                    "data": positions
                }
            else:
                return {
                    "success": False,
                    "code": GatewayCode.POSITIONS_QUERY_FAILED,
                    "msg": f"HTTP {r.status_code}: {r.text[:200]}",
                    "data": {"status": r.status_code}
                }
        except Exception as e:  # noqa: BLE001
            err_msg = f"[{mName}]get_positions failed: {e}"
            logger.warning(f"[POLY-GW] {err_msg}")
            return {
                "success": False,
                "code": GatewayCode.POSITIONS_QUERY_FAILED,
                "msg": err_msg,
                "data": {"wallet_address": self.wallet_address}
            }

    # ===== 7. 风控与监控 =====
    pass

    # 设置风控上限（None 表示不修改）
    def set_risk_limits(
            self,
            max_amount_usd: float | None = None,
            price_floor: float | None = None,
            price_cap: float | None = None,
            max_open_orders: int | None = None,
    ) -> dict:
        """设置风控上限（None 表示不修改，返回统一格式）"""
        if max_amount_usd is not None:
            self._risk_max_amount_usd = float(max_amount_usd)
        if price_floor is not None:
            self._risk_price_floor = float(price_floor)
        if price_cap is not None:
            self._risk_price_cap = float(price_cap)
        if max_open_orders is not None:
            self._risk_max_open_orders = int(max_open_orders)

        return {
            "success": True,
            "code": GatewayCode.RISK_LIMITS_SET,
            "msg": "risk limits updated",
            "data": {
                "max_amount_usd": self._risk_max_amount_usd,
                "price_floor": self._risk_price_floor,
                "price_cap": self._risk_price_cap,
                "max_open_orders": self._risk_max_open_orders
            }
        }

    # 获取网关状态（健康摘要，Polymarket 特定字段）
    def get_status(self) -> dict:
        """获取网关状态（健康摘要，Polymarket 特定字段，返回统一格式）"""
        st = GatewayStatus(
            name=self.name,
            ready=self.is_ready(),
            sdk_available=self.is_sdk_available(),
            host=self.host,
            chain_id=self.chain_id,
            wallet_address=(self.wallet_address[:10] + "...") if self.wallet_address else "",
            l2_creds_configured=bool(self._l2_creds),
            http_client_open=bool(self._http and not self._http.is_closed),
            last_ping_success=self._last_ping_success,
            last_ping_at=self._last_ping_at,
            last_error=self._last_error,
            sdk_version="polymarket-client" if _HAS_SDK else "n/a",
        )
        status_dict = asdict(st)
        return {
            "success": st.ready,
            "code": GatewayCode.STATUS_OK if st.ready else GatewayCode.NOT_READY,
            "msg": "gateway ready" if st.ready else "gateway not ready",
            "data": status_dict
        }

    # ===== 8. 业务快捷方法 =====
    pass

    # 从市场结构中按方向挑出 token_id + outcome 标签
    @staticmethod
    def _pick_token_for_side(market: Any, side: str) -> tuple[str, str]:
        """从市场结构中按方向挑出 token_id + outcome 标签"""
        side_u = (side or "UP").upper()
        single = market if isinstance(market, dict) and "tokens" in market else None
        if single is None and isinstance(market, list) and market:
            single = market[0]
        if not single:
            raise RuntimeError("no active market found")
        tokens = single.get("tokens") or []
        if not tokens:
            raise RuntimeError("market has no tokens")
        for t in tokens:
            outcome = (t.get("outcome") or t.get("label") or "").upper()
            if outcome == side_u:
                return str(t.get("token_id") or t.get("clobTokenId") or ""), outcome
        if side_u == "UP" and len(tokens) >= 1:
            t = tokens[0]
            return str(t.get("token_id") or t.get("clobTokenId") or ""), "UP"
        if side_u == "DOWN" and len(tokens) >= 2:
            t = tokens[1]
            return str(t.get("token_id") or t.get("clobTokenId") or ""), "DOWN"
        t = tokens[0]
        return str(t.get("token_id") or t.get("clobTokenId") or ""), (t.get("outcome") or "?")

    # 对外暴露：BTC 5min 市场一键下单（自动风控 + 中间价兜底，返回统一格式）
    async def place_btc5m_order(
            self,
            side: str,
            amount_usd: float | None = None,
            token_id: str | None = None,
            price: float | None = None,
            target_epoch: int | None = None,
            metadata: str = "",
    ) -> dict:
        """BTC 5min 市场一键下单（自动风控 + 中间价兜底，返回统一格式）

        Args:
            side: UP / DOWN（买涨/买跌 token）
            amount_usd: 下单金额（USD），默认取 settings.POLYMARKET_BTC5M_DEFAULT_AMOUNT_USD
            token_id: 直接指定 token（跳过市场查询），一般留 None 自动解析
            price: 限价，默认取中间价 midpoint
            target_epoch: 目标 5 分钟窗口 Unix 时间戳（秒）。
                - None（默认）: 当前最活跃市场（即时成交）
                - 精确时间戳: 下注未来某 5 分钟窗口的趋势，
                  例如 target_epoch=1785399000 → 下 btc-updown-5m-1785399000
            metadata: 订单备注
        """
        if not self._account:
            return {
                "success": False,
                "code": GatewayCode.WALLET_NOT_CONFIGURED,
                "msg": "wallet not configured for BTC5M order"
            }
        try:
            # 1) 风控：金额上限
            amt = float(
                amount_usd if amount_usd is not None else settings.POLYMARKET_BTC5M_DEFAULT_AMOUNT_USD
            )
            if amt <= 0:
                return {
                    "success": False,
                    "code": GatewayCode.AMOUNT_TOO_SMALL,
                    "msg": f"invalid amount_usd: {amt}",
                    "data": {"amount_usd": amt}
                }
            if amt > self._risk_max_amount_usd:
                logger.warning(
                    f"[POLY-GW] amount {amt} > MAX {self._risk_max_amount_usd}, clamp"
                )
                amt = self._risk_max_amount_usd

            # 2) 解析 token_id
            if not token_id:
                market_resp = await self.get_active_btc5m_market(target_epoch=target_epoch)
                if not market_resp.get("success"):
                    return {
                        "success": False,
                        "code": GatewayCode.BTC5M_MARKET_NOT_FOUND,
                        "msg": f"failed to get BTC5M market (target_epoch={target_epoch})",
                        "data": market_resp
                    }
                token_id, _ = self._pick_token_for_side(market_resp.get("data"), side)

            # 3) 价格
            if price is None:
                mid_resp = await self.get_midpoint(token_id)
                price = (mid_resp.get("data") or {}).get("midpoint", 0.5)
            price = max(self._risk_price_floor, min(self._risk_price_cap, float(price or 0.5)))
            if price <= 0:
                price = 0.5

            # 4) 份额
            size = round(amt / price, 4)
            side_str = "BUY"  # BTC 5min 买 UP/DOWN token 都视为 BUY

            result = await self.place_limit_order(
                token_id=token_id,
                side=side_str,
                price=price,
                size=size,
                order_type="GTC",
                metadata=metadata or f"btc5m:{side}",
            )

            if result.get("success"):
                result["code"] = GatewayCode.BTC5M_ORDER_PLACED
                result["msg"] = f"BTC5M {side} order placed: {size}@{price}"
            else:
                result["code"] = GatewayCode.BTC5M_ORDER_FAILED

            return result

        except Exception as e:  # noqa: BLE001
            err_msg = f"[{mName}]place_btc5m_order failed: {e}"
            logger.error(f"[POLY-GW] {err_msg}")
            return {
                "success": False,
                "code": GatewayCode.BTC5M_ORDER_FAILED,
                "msg": err_msg,
                "data": {"side": side}
            }

    # 快捷买入（限价 GTC）
    async def quick_buy(
            self,
            token_id: str,
            price: float,
            size: float,
    ) -> dict:
        """快捷买入（限价 GTC）"""
        result = await self.place_limit_order(
            token_id=token_id, side="BUY", price=price, size=size
        )
        if result.get("success"):
            result["code"] = GatewayCode.QUICK_BUY_OK
            result["msg"] = f"quick buy {size}@{price}"
        return result

    # 快捷卖出（限价 GTC）
    async def quick_sell(
            self,
            token_id: str,
            price: float,
            size: float,
    ) -> dict:
        """快捷卖出（限价 GTC）"""
        result = await self.place_limit_order(
            token_id=token_id, side="SELL", price=price, size=size
        )
        if result.get("success"):
            result["code"] = GatewayCode.QUICK_SELL_OK
            result["msg"] = f"quick sell {size}@{price}"
        return result


# ============================================================
pass
#  V1 协议回退（同一平台，不同协议版本）
#  - 当 polymarket-client SDK 未安装时使用 V1 HTTP 客户端
#  - 同时为旧版路由层（polymarket_router.py）提供兼容
#  - 向后兼容：原 PolymarketClient 类的所有方法
# ============================================================

# V1 CLOB 主机（与 V2 主机相同，但订单域 version="1"）
POLY_HOSTS_V1 = {
    "MAINNET": POLY_CLOB_HOST_MAINNET,
    "GOERLI": POLY_CLOB_HOST_STAGING,
    "MOCK": "https://clob-mock.polymarket.com",
}
# V1 兼容：保留旧名
POLY_HOSTS = POLY_HOSTS_V1

# 注：POLY_GAMMA_HOST / POLY_DATA_HOST / USDC_E_POLYGON / CTF_EXCHANGE_V2 已在顶部定义
pass
# CLOB V1 合约地址（mainnet）
CTF_EXCHANGE_V1 = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
CHAIN_ID_V1 = 137  # polygon


# V1 Polymarket 真实下单结果数据类
@dataclass
class PolyOrderResult:
    """V1 Polymarket 真实下单结果"""

    order_id: str
    market: str
    token_id: str
    side: str  # BUY / SELL
    price: float  # 0~1
    size: float  # 份额
    amount_usd: float
    status: str  # live / matched / canceled
    latency_ms: int
    raw: dict


# Polymarket CLOB 客户端 V1 协议（完整 L2 认证 + EIP-712 订单签名 version=1）
class PolymarketV1Client(BaseGateway):
    """Polymarket CLOB 客户端 V1 协议（完整 L2 认证 + EIP-712 订单签名 version=1）

    继承自 BaseGateway：
    - name = "polymarket_v1"（区分 V2 网关的 polymarket）
    - is_ready / _do_ping 满足基类抽象要求
    - connect / close / get_status / health_check 由基类提供

    用途：
    - 作为 polymarket-client SDK 不可用时的回退
    - 为旧版 polymarket_router.py 提供完整 API
    """

    # 基类要求：平台名
    name: str = "polymarket_v1"

    # 构造函数（初始化 V1 客户端参数）
    def __init__(self, host: str | None = None) -> None:
        # 优先显式传入的 host，其次读 settings.POLYMARKET_HOST
        host_name = host or settings.POLYMARKET_HOST or "MAINNET"
        # 调用基类初始化
        super().__init__(
            host=POLY_HOSTS_V1.get(host_name, POLY_HOSTS_V1["MAINNET"]),
            chain_id=CHAIN_ID_V1,
            http_timeout=settings.POLYMARKET_HTTP_TIMEOUT,
        )
        self.private_key = settings.POLYMARKET_PRIVATE_KEY
        self.wallet_address = settings.POLYMARKET_WALLET_ADDRESS
        self.api_key = settings.POLYMARKET_APIKEY
        # EIP-712 派生 L2 API Key（首次调用时初始化）
        self._l2_keys: dict | None = None
        self._account = None
        if self.private_key:
            try:
                self._account = Account.from_key(self.private_key)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Polymarket wallet load failed: {e}")

    # ===== 抽象方法实现（BaseGateway 要求） =====
    # 判断 V1 网关是否已就绪
    def is_ready(self) -> bool:
        """钱包私钥/地址是否配置 + HTTP 客户端已开"""
        return bool(
            self.private_key
            and self.wallet_address
            and self._account
            and self._http is not None
            and not self._http.is_closed
        )

    # 判断钱包私钥/地址是否配置（无网络副作用）
    def is_configured(self) -> bool:
        """钱包私钥/地址是否配置（无网络副作用）"""
        return bool(self.private_key and self.wallet_address and self._account)

    # Polymarket 公开端点连通性探测（GET /markets）
    async def _do_ping(self) -> dict:
        """Polymarket 公开端点连通性探测（GET /markets，公开端点）

        返回统一格式：{"success": bool, "code": int, "msg": str, "data": dict}
        """
        try:
            client = await self._get_http()
            resp = await client.get(f"{self.host}/markets?limit=1")
            ok = resp.status_code == 200
            return {
                "success": ok,
                "code": GatewayCode.PING_OK if ok else GatewayCode.PING_FAILED,
                "msg": "ping successful" if ok else f"HTTP {resp.status_code}",
                "data": {"status": resp.status_code, "host": self.host},
            }
        except Exception as e:  # noqa: BLE001
            return {
                "success": False,
                "code": GatewayCode.UNKNOWN_ERROR,
                "msg": str(e),
                "data": {"host": self.host},
            }

    # L1 认证签名：用于创建/派生 L2 API Key（EIP-712）
    def _sign_l1(self, nonce: int = 0) -> str:
        """L1 认证签名：用于创建/派生 L2 API Key（EIP-712）"""
        timestamp = int(time.time())
        if nonce == 0:
            nonce = timestamp
        payload = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                ],
                "ClobAuth": [
                    {"name": "address", "type": "address"},
                    {"name": "timestamp", "type": "string"},
                    {"name": "nonce", "type": "uint256"},
                ],
            },
            "primaryType": "ClobAuth",
            "domain": {"name": "ClobAuthDomain", "version": "1", "chainId": CHAIN_ID_V1},
            "message": {
                "address": self.wallet_address,
                "timestamp": str(timestamp),
                "nonce": int(nonce),
            },
        }
        encoded = encode_typed_data(full_message=payload)
        signed = self._account.sign_message(encoded)
        return f"0x{signed.signature.hex()}"

    # 确保 L2 API Key 已就绪（不存在则创建，存在则可派生）
    async def _ensure_l2_keys(self) -> dict:
        """确保 L2 API Key 已就绪（不存在则创建，存在则可派生）"""
        if self._l2_keys is not None:
            return self._l2_keys

        headers = {
            "POLY_ADDRESS": self.wallet_address,
            "POLY_SIGNATURE": self._sign_l1(),
            "POLY_TIMESTAMP": str(int(time.time())),
            "POLY_NONCE": "0",
        }
        client = await self._get_http()

        # 1) 尝试 derive
        try:
            resp = await client.get(f"{self.host}/auth/derive-api-key", headers=headers)
            if resp.status_code == 200:
                self._l2_keys = resp.json()
                return self._l2_keys
        except Exception:
            pass

        # 2) 不存在则创建
        resp = await client.post(f"{self.host}/auth/api-key", headers=headers)
        if resp.status_code == 200:
            self._l2_keys = resp.json()
        else:
            # 退化：使用 POLY_API_KEY env
            self._l2_keys = {
                "apiKey": self.api_key or "POLY_API_KEY_PLACEHOLDER",
                "secret": "",
                "passphrase": "",
            }
        return self._l2_keys

    # L2 API 头部签名：HMAC SHA256 Base64
    def _l2_headers(self, method: str, path: str, body: str = "") -> dict:
        """L2 API 头部签名：HMAC SHA256 Base64"""
        ts = str(int(time.time()))
        message = ts + method.upper() + path + body
        secret = (self._l2_keys or {}).get("secret", "").encode("utf-8")
        if not secret:
            sig = ""
        else:
            sig = base64.b64encode(
                hmac.new(secret, message.encode("utf-8"), hashlib.sha256).digest()
            ).decode()
        return {
            "POLY_ADDRESS": self.wallet_address,
            "POLY_API_KEY": (self._l2_keys or {}).get("apiKey", ""),
            "POLY_PASSPHRASE": (self._l2_keys or {}).get("passphrase", ""),
            "POLY_TIMESTAMP": ts,
            "POLY_SIGNATURE": sig,
        }

    # 统一 Polymarket 请求封装
    async def _request(
            self, method: str, path: str, body: dict | None = None, auth_l1: bool = False
    ) -> dict:
        """统一 Polymarket 请求"""
        if not self.is_configured() and not auth_l1:
            raise RuntimeError("Polymarket wallet not configured")
        body_str = json.dumps(body, separators=(",", ":")) if body else ""

        if auth_l1:
            headers = {
                "POLY_ADDRESS": self.wallet_address,
                "POLY_SIGNATURE": self._sign_l1(),
                "POLY_TIMESTAMP": str(int(time.time())),
                "POLY_NONCE": "0",
                "Content-Type": "application/json",
            }
        else:
            await self._ensure_l2_keys()
            base = self._l2_headers(method, path, body_str)
            headers = {**base, "Content-Type": "application/json"}

        client = await self._get_http()
        url = f"{self.host}{path}"
        if method == "GET":
            resp = await client.get(url, headers=headers)
        elif method == "POST":
            resp = await client.post(url, headers=headers, content=body_str)
        elif method == "DELETE":
            resp = await client.delete(url, headers=headers)
        else:
            raise ValueError(f"unsupported method: {method}")

        try:
            data = resp.json()
        except Exception:
            data = {"msg": resp.text[:200]}

        if resp.status_code >= 400:
            logger.warning(f"[POLY] {method} {path} HTTP {resp.status_code}: {data}")
        return data

    # ========== 公共 API：市场/订单 ==========
    # 获取市场列表（V1 客户端，返回统一格式）
    async def get_markets(self) -> dict:
        """获取市场列表"""
        result = await self._request("GET", "/markets", auth_l1=True)
        if result.get("success"):
            result["code"] = GatewayCode.MARKET_LIST_OK
            result["msg"] = "V1 markets retrieved"
        else:
            result["code"] = GatewayCode.QUERY_FAILED
        return result

    # 获取单个市场（V1 客户端）
    async def get_market(self, condition_id: str) -> dict:
        """获取单个市场"""
        result = await self._request("GET", f"/markets/{condition_id}", auth_l1=True)
        if result.get("success"):
            result["code"] = GatewayCode.MARKET_DETAIL_OK
            result["msg"] = f"V1 market {condition_id[:12]}... found"
        else:
            result["code"] = GatewayCode.MARKET_NOT_FOUND
        return result

    # 获取 token 中间价（公开）
    async def get_midpoint(self, token_id: str) -> dict:
        """获取 token 中间价（公开）"""
        result = await self._request("GET", f"/midpoint?token_id={token_id}", auth_l1=True)
        if result.get("success"):
            result["code"] = GatewayCode.MIDPOINT_OK
            midpoint = float((result.get("data") or {}).get("mid", 0.0))
            result["data"] = {"token_id": token_id, "midpoint": midpoint}
        else:
            result["code"] = GatewayCode.INVALID_TOKEN_ID
        return result

    async def get_order_book(self, token_id: str) -> dict:
        """获取订单簿（公开）"""
        result = await self._request("GET", f"/book?token_id={token_id}", auth_l1=True)
        if result.get("success"):
            result["code"] = GatewayCode.ORDERBOOK_OK
            result["msg"] = f"V1 orderbook for {token_id[:16]}... retrieved"
        else:
            result["code"] = GatewayCode.INVALID_TOKEN_ID
        return result

    # ========== 下单 ==========
    # EIP-712 订单签名（V1 域 version="1"）
    def _sign_order(
            self,
            token_id: str,
            price: float,
            side: str,
            size: float,
            fee_rate_bps: int = 0,
            nonce: int = 0,
            expiration: int = 0,
    ) -> dict:
        """EIP-712 订单签名（V1 域 version="1"）"""
        if nonce == 0:
            nonce = int(time.time())
        if expiration == 0:
            expiration = int(time.time()) + 86400  # 24h 过期

        order = {
            "salt": int.from_bytes(uuid.uuid4().bytes, "big") % (2 ** 128),
            "maker": self.wallet_address,
            "signer": self.wallet_address,
            "taker": "0x0000000000000000000000000000000000000000",
            "tokenId": str(
                int(token_id, 16) if token_id.startswith("0x") else int(token_id)
            ),
            "makerAmount": int(size * 1e6),
            "takerAmount": int(size * price * 1e6),
            "expiration": str(expiration),
            "nonce": str(nonce),
            "feeRateBps": str(fee_rate_bps),
            "side": "0" if side.upper() == "BUY" else "1",
        }
        domain = {
            "name": "Polymarket CTF Exchange",
            "version": "1",
            "chainId": CHAIN_ID_V1,
            "verifyingContract": CTF_EXCHANGE_V1,
        }
        types = {
            "Order": [
                {"name": "salt", "type": "uint256"},
                {"name": "maker", "type": "address"},
                {"name": "signer", "type": "address"},
                {"name": "taker", "type": "address"},
                {"name": "tokenId", "type": "uint256"},
                {"name": "makerAmount", "type": "uint256"},
                {"name": "takerAmount", "type": "uint256"},
                {"name": "expiration", "type": "uint256"},
                {"name": "nonce", "type": "uint256"},
                {"name": "feeRateBps", "type": "uint256"},
                {"name": "side", "type": "uint8"},
            ],
        }
        payload = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                **types,
            },
            "primaryType": "Order",
            "domain": domain,
            "message": order,
        }
        encoded = encode_typed_data(full_message=payload)
        signed = self._account.sign_message(encoded)
        order["signature"] = f"0x{signed.signature.hex()}"
        return order

    # 下单（已签名的订单）
    async def place_order(
            self,
            token_id: str,
            side: str,  # BUY / SELL
            price: float,  # 0~1
            size: float,  # 份额
    ) -> dict:
        """下单（已签名的订单）"""
        signed = self._sign_order(token_id, price, side, size)
        body = {"order": signed, "owner": self.wallet_address, "orderType": "GTC"}
        return await self._request("POST", "/order", body)

    # 查询订单状态（V1 客户端）
    async def get_order(self, order_id: str) -> dict:
        """查询订单状态"""
        return await self._request("GET", f"/order/{order_id}")

    # 取消订单（V1 客户端）
    async def cancel_order(self, order_id: str) -> dict:
        """取消订单"""
        return await self._request("DELETE", f"/order/{order_id}")

    # ========== 网关管理 API：连接状态 / 余额 / 持仓 ==========
    # 网关连通性探测（GET /markets，公开端点）
    async def ping(self) -> dict:
        """网关连通性探测（GET /markets，公开端点）"""
        return await self._request("GET", "/markets?limit=1", auth_l1=True)

    # 公开端点统一请求（gamma-api / data-api，无需签名）
    async def _get_public(self, host: str, path: str) -> dict:
        """公开端点统一请求（gamma-api / data-api，无需签名）"""
        client = await self._get_http()
        url = f"{host}{path}"
        try:
            resp = await client.get(url, headers={"Content-Type": "application/json"})
            try:
                return resp.json()
            except Exception:
                return {"msg": resp.text[:200], "status": resp.status_code}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[POLY] public GET {host}{path} failed: {e}")
            return {"msg": str(e)}

    # 查询钱包余额（V1 兼容：data-api 持仓 + CLOB 旧 /collateral 尝试）
    async def get_balance(self) -> dict:
        """查询钱包余额

        V2 后 CLOB 不再提供 /collateral 端点，pUSD 余额需通过 Polygon 链上查询。
        本方法返回：
        - collateral_endpoint: 旧 /collateral 尝试结果（兼容老版本）
        - positions_value: 通过 data-api 持仓汇总（USD 估值）
        """
        if not self.wallet_address:
            raise RuntimeError("Polymarket wallet address not configured")
        coll_path = f"/collateral?user={self.wallet_address}"
        collateral = await self._request("GET", coll_path)
        pos_path = f"/positions?user={self.wallet_address}&sizeGreaterThan=0&limit=100"
        positions = await self._get_public(POLY_DATA_HOST, pos_path)
        positions_value = 0.0
        if isinstance(positions, list):
            for p in positions:
                try:
                    size = float(p.get("size") or 0)
                    price = float(p.get("curPrice") or p.get("avgPrice") or 0)
                    positions_value += size * price
                except Exception:
                    pass
        return {
            "wallet_address": self.wallet_address,
            "collateral_endpoint": collateral,
            "positions": positions if isinstance(positions, list) else [],
            "positions_value_usd": round(positions_value, 4),
            "positions_count": len(positions) if isinstance(positions, list) else 0,
        }

    # 查询钱包持仓（GET data-api/positions，公开端点）
    async def get_positions(
            self, market: str | None = None, size_greater_than: float = 0.0
    ) -> dict:
        """查询钱包持仓（GET data-api/positions，公开端点）"""
        params = [f"user={self.wallet_address}"]
        if market:
            params.append(f"market={market}")
        if size_greater_than > 0:
            params.append(f"sizeGreaterThan={size_greater_than}")
        params.append("limit=100")
        path = "/positions?" + "&".join(params)
        return await self._get_public(POLY_DATA_HOST, path)

    # 查询钱包当前挂单（GET CLOB /orders，需 L2 auth）
    async def get_open_orders(self, market: str | None = None) -> dict:
        """查询钱包当前挂单（GET CLOB /orders，需 L2 auth）"""
        params = [f"user={self.wallet_address}"]
        if market:
            params.append(f"market={market}")
        path = "/orders?" + "&".join(params)
        return await self._request("GET", path)

    # ========== BTC 5min 市场：查询活跃市场 + 一键下单 ==========
    # 查询当前活跃的 BTC 5min 涨跌市场（通过 Gamma tag 过滤 + slug 前缀二次筛选）
    async def get_active_btc_market(
            self,
            slug_prefix: str | None = None,
            target_epoch: int | None = None,
    ) -> dict:
        """查询当前活跃的 BTC 5min 涨跌市场（通过 Gamma tag 过滤 + slug 前缀二次筛选）

        Args:
            slug_prefix: slug 前缀（默认取 settings.POLYMARKET_BTC5M_SLUG_PREFIX）
            target_epoch: 目标 5 分钟窗口 Unix 时间戳（秒）。
                None=当前最活跃，精确时间戳=按 slug 精确定位
        - 走 Gamma API（公开端点，无需签名），返回市场列表
        """
        prefix = slug_prefix or settings.POLYMARKET_BTC5M_SLUG_PREFIX
        if target_epoch:
            full_slug = f"{prefix}-{target_epoch}"
            path = (
                f"/markets?slug={full_slug}"
                "&active=true&closed=false&archived=false&limit=5"
            )
        else:
            path = (
                f"/markets?tag={prefix}"
                "&active=true&closed=false&archived=false"
                "&order=startDate&ascending=false&limit=50"
            )
        raw = await self._get_public(POLY_GAMMA_HOST, path)
        if isinstance(raw, list):
            if not target_epoch:
                filtered = [m for m in raw if (m.get("slug") or "").startswith(prefix)]
                return filtered
            return raw
        return raw

    # 从市场结构中按方向挑出 token_id + outcomes 标签（V1 客户端静态方法）
    @staticmethod
    def _pick_token_for_side(market: dict, side: str) -> tuple[str, str]:
        """从市场结构中按方向挑出 token_id + outcomes 标签"""
        side_u = (side or "UP").upper()
        single = market if isinstance(market, dict) and "tokens" in market else None
        if single is None and isinstance(market, list) and market:
            single = market[0]
        if not single:
            raise RuntimeError("no active BTC 5min market found")
        tokens = single.get("tokens") or []
        if not tokens:
            raise RuntimeError("market has no tokens")
        for t in tokens:
            outcome = (t.get("outcome") or t.get("label") or "").upper()
            if outcome == side_u:
                return str(t.get("token_id") or t.get("clobTokenId") or ""), outcome
        if side_u == "UP" and len(tokens) >= 1:
            t = tokens[0]
            return str(t.get("token_id") or t.get("clobTokenId") or ""), "UP"
        if side_u == "DOWN" and len(tokens) >= 2:
            t = tokens[1]
            return str(t.get("token_id") or t.get("clobTokenId") or ""), "DOWN"
        t = tokens[0]
        return str(t.get("token_id") or t.get("clobTokenId") or ""), (t.get("outcome") or "?")

    # 在 BTC 5min 市场下单（集中应用风控配置）（V1 客户端）
    async def place_btc5m_order(
            self,
            side: str,
            amount_usd: float | None = None,
            token_id: str | None = None,
            price: float | None = None,
            target_epoch: int | None = None,
    ) -> dict:
        """在 BTC 5min 市场下单（集中应用风控配置）

        Args:
            side: UP / DOWN
            amount_usd: 下单金额（USD）
            token_id: 直接指定 token（跳过市场查询）
            price: 限价，默认取中间价
            target_epoch: 目标 5 分钟窗口 Unix 时间戳（秒）。
                None=当前市场，精确时间戳=未来窗口
        """
        if not self.is_configured():
            raise RuntimeError("Polymarket wallet not configured (need PRIVATE_KEY + ADDRESS)")

        # 风控：金额上限
        amt = float(
            amount_usd if amount_usd is not None
            else settings.POLYMARKET_BTC5M_DEFAULT_AMOUNT_USD
        )
        if amt <= 0:
            raise RuntimeError(f"invalid amount_usd: {amt}")
        if amt > settings.POLYMARKET_BTC5M_MAX_AMOUNT_USD:
            logger.warning(
                f"[POLY-BTC5M] amount {amt} > MAX {settings.POLYMARKET_BTC5M_MAX_AMOUNT_USD}, clamp"
            )
            amt = settings.POLYMARKET_BTC5M_MAX_AMOUNT_USD

        # 解析 token_id
        market_meta: dict = {}
        if not token_id:
            market_resp = await self.get_active_btc_market(target_epoch=target_epoch)
            data = (
                market_resp if isinstance(market_resp, list)
                else market_resp.get("data", market_resp)
            )
            token_id, label = self._pick_token_for_side(data, side)
            market_meta = {
                "resolved_token_label": label,
                "market_resp_head": str(data)[:200] if data else "",
                "target_epoch": target_epoch,
            }
        else:
            token_id = str(token_id)

        # 价格
        if price is None:
            mid_resp = await self.get_midpoint(token_id)
            price = float((mid_resp or {}).get("mid", 0.5))
        price = max(
            settings.POLYMARKET_BTC5M_PRICE_FLOOR,
            min(settings.POLYMARKET_BTC5M_PRICE_CAP, float(price)),
        )
        if price <= 0:
            price = 0.5

        # 份额
        size = round(amt / price, 4)
        side_str = "BUY"
        logger.info(
            f"[POLY-BTC5M] order side={side} amount=${amt} price={price} "
            f"size={size} token={token_id[:12]}..."
        )

        # 下单（带重试）
        last_exc: Exception | None = None
        for attempt in range(settings.POLYMARKET_ORDER_RETRY + 1):
            try:
                resp = await self.place_order(
                    token_id=token_id, side=side_str, price=price, size=size
                )
                if isinstance(resp, dict):
                    resp["_meta"] = {
                        "side": side,
                        "amount_usd": amt,
                        "price": price,
                        "size": size,
                        "token_id": token_id,
                        "attempt": attempt + 1,
                        **market_meta,
                    }
                return resp
            except Exception as e:  # noqa: BLE001
                last_exc = e
                logger.warning(f"[POLY-BTC5M] order attempt {attempt + 1} failed: {e}")
        raise RuntimeError(
            f"BTC 5min order failed after {settings.POLYMARKET_ORDER_RETRY + 1} attempts: {last_exc}"
        )


# ========== 向后兼容：旧类名（迁移期使用）==========
PolymarketClient = PolymarketV1Client  # 别名（之前叫 PolymarketClient）

# 注：全局单例 get_polymarket_gateway() 由 gateway.py 的 GatewayHub 统一管理
pass

# 业务层请统一用：from fwsort.gateway import get_hub; hub.polymarket_v2
pass

# ========== 菜单式调试入口 ==========
pass


# 调试菜单 - ping 网关连通性（适配统一返回格式）
async def _menu_ping(gw: PolymarketGateway) -> None:
    """菜单 - ping 网关连通性"""
    result = await gw.ping()
    print(f"  result={result}")


# 调试菜单 - 显示网关状态（适配统一返回格式）
async def _menu_status(gw: PolymarketGateway) -> None:
    """菜单 - 显示网关状态"""
    result = gw.get_status()
    print(f"  result={result}")


# 调试菜单 - 主动健康检查
async def _menu_health(gw: PolymarketGateway) -> None:
    """菜单 - 主动健康检查"""
    result = await gw.health_check()
    print(f"  result={result}")


# 调试菜单 - 创建或派生 L2 API Key
async def _menu_l2(gw: PolymarketGateway) -> None:
    """菜单 - 创建或派生 L2 API Key"""
    result = await gw.create_or_derive_api_key()
    print(f"  result={result}")


# 调试菜单 - 列出市场（前 5 条，适配统一返回格式）
async def _menu_markets(gw: PolymarketGateway) -> None:
    """菜单 - 列出市场（前 5 条）"""
    result = await gw.get_markets(limit=5)
    print(f"  result={result}")


# 调试菜单 - 查 token 中间价（适配统一返回格式）
async def _menu_midpoint(gw: PolymarketGateway) -> None:
    """菜单 - 查 token 中间价"""
    tid = input("  token_id: ").strip()
    if not tid:
        print("  已取消")
        return
    mid_res = await gw.get_midpoint(tid)
    if isinstance(mid_res, dict) and mid_res.get("success"):
        data = mid_res.get("data", {})
        print(f"  midpoint({tid[:16]}...) = {data.get('midpoint')} (code={mid_res.get('code')})")
    else:
        print(
            f"  midpoint failed → code={mid_res.get('code') if isinstance(mid_res, dict) else 'unknown'} msg={mid_res.get('msg') if isinstance(mid_res, dict) else str(mid_res)}")


# 调试菜单 - 查订单簿（适配统一返回格式）
async def _menu_book(gw: PolymarketGateway) -> None:
    """菜单 - 查订单簿"""
    tid = input("  token_id: ").strip()
    if not tid:
        print("  已取消")
        return
    book_res = await gw.get_order_book(tid)
    if isinstance(book_res, dict) and book_res.get("success"):
        snapshot = book_res.get("snapshot", {})
        bids = snapshot.get("bids", [])
        asks = snapshot.get("asks", [])
        print(
            f"  orderbook → bids={len(bids)} asks={len(asks)} midpoint={snapshot.get('midpoint')} spread={snapshot.get('spread')}")
        if bids:
            print(f"           best bid: {bids[-1]}")
        if asks:
            print(f"           best ask: {asks[0]}")
    else:
        print(f"  orderbook failed → code={book_res.get('code') if isinstance(book_res, dict) else 'unknown'}")


# 调试菜单 - 查当前活跃 BTC 5min 市场（适配统一返回格式）
async def _menu_btc5m_market(gw: PolymarketGateway) -> None:
    """菜单 - 查当前活跃 BTC 5min 市场"""
    res = await gw.get_active_btc5m_market()
    if isinstance(res, dict) and res.get("success"):
        data = res.get("data")
        if isinstance(data, list) and data:
            m = data[0]
            print(f"  第一个: question={m.get('question', '?')[:80]}")
            print(f"          slug={m.get('slug', '?')}")
            print(f"          condition_id={m.get('condition_id', '?')[:20]}...")
            print(f"          tokens={len(m.get('tokens', []))}")
        elif isinstance(data, dict):
            print(f"  market: question={data.get('question', '?')[:80]}")
        else:
            print(f"  resp data: {str(data)[:200]}")
    else:
        print(f"  resp: success={res.get('success') if isinstance(res, dict) else False} "
              f"code={res.get('code') if isinstance(res, dict) else 'unknown'} "
              f"msg={res.get('msg') if isinstance(res, dict) else str(res)[:200]}")


# 调试菜单 - BTC 5min 一键下单（适配统一返回格式）
async def _menu_btc5m_order(gw: PolymarketGateway) -> None:
    """菜单 - BTC 5min 一键下单"""
    side = input("  side (UP/DOWN) [默认 UP]: ").strip().upper() or "UP"
    amt = input("  amount_usd [默认 5]: ").strip() or "5"
    epoch_str = input("  target_epoch (回车=当前市场, 或未来5min窗口时间戳): ").strip()
    target_epoch = int(epoch_str) if epoch_str else None
    result = await gw.place_btc5m_order(side=side, amount_usd=float(amt), target_epoch=target_epoch)
    print(f"  result={result}")


# 调试菜单 - 手动限价下单（适配统一返回格式）
async def _menu_place_order(gw: PolymarketGateway) -> None:
    """菜单 - 手动限价下单"""
    tid = input("  token_id: ").strip()
    if not tid:
        print("  已取消")
        return
    side = input("  side (BUY/SELL) [默认 BUY]: ").strip().upper() or "BUY"
    price = input("  price (0~1) [默认 0.5]: ").strip() or "0.5"
    size = input("  size (份额) [默认 10]: ").strip() or "10"
    res = await gw.place_limit_order(
        token_id=tid, side=side, price=float(price), size=float(size)
    )
    if isinstance(res, dict):
        print(f"  result → success={res.get('success')} code={res.get('code')} msg={res.get('msg')}")
        if res.get("success"):
            data = res.get("data", {})
            print(f"          order_id={data.get('orderID', data.get('id', 'n/a'))}")
        else:
            print(f"          error: {json.dumps(res.get('data'), ensure_ascii=False)[:200]}")
    else:
        print(f"  result: {res}")


# 调试菜单 - 撤单（适配统一返回格式）
async def _menu_cancel(gw: PolymarketGateway) -> None:
    """菜单 - 撤单"""
    oid = input("  order_id: ").strip()
    if not oid:
        print("  已取消")
        return
    res = await gw.cancel_order(oid)
    if isinstance(res, dict):
        print(f"  cancel → success={res.get('success')} code={res.get('code')} msg={res.get('msg')}")
    else:
        print(f"  cancel: {str(res)[:300]}")


# 调试菜单 - 查挂单（适配统一返回格式）
async def _menu_open_orders(gw: PolymarketGateway) -> None:
    """菜单 - 查挂单"""
    res = await gw.get_open_orders()
    if isinstance(res, dict) and res.get("success"):
        orders = res.get("data", [])
        if isinstance(orders, list):
            print(f"  挂单数: {len(orders)}")
            for o in orders[:5]:
                print(f"    - {o.get('id', '?')[:20]} {o.get('side', '?')} {o.get('price', '?')}x{o.get('size', '?')}")
        else:
            print(f"  {str(orders)[:300]}")
    elif isinstance(res, dict):
        print(f"  failed → code={res.get('code')} msg={res.get('msg')}")
    else:
        print(f"  {str(res)[:300]}")


# 调试菜单 - 查余额（适配统一返回格式）
async def _menu_balance(gw: PolymarketGateway) -> None:
    """菜单 - 查余额"""
    res = await gw.get_balance()
    if isinstance(res, dict):
        print(f"  balance → success={res.get('success')} code={res.get('code')} msg={res.get('msg')}")
        data = res.get("data", {})
        if data:
            print(f"           positions_count={data.get('positions_count')}")
            print(f"           positions_value_usd=${data.get('positions_value_usd')}")
    else:
        print("  balance:", json.dumps(res, ensure_ascii=False, indent=2))


# 调试菜单 - 查持仓（适配统一返回格式）
async def _menu_positions(gw: PolymarketGateway) -> None:
    """菜单 - 查持仓"""
    res = await gw.get_positions()
    if isinstance(res, dict) and res.get("success"):
        positions = res.get("data", [])
        if isinstance(positions, list):
            print(f"  持仓数: {len(positions)}")
            for p in positions[:5]:
                try:
                    size = float(p.get("size", 0))
                    price = float(p.get("curPrice", 0))
                    print(
                        f"    - market={p.get('market', '?')[:30]} size={size} price={price} value=${size * price:.2f}")
                except Exception:  # noqa: BLE001
                    pass
        else:
            print(f"  positions → {str(positions)[:300]}")
    elif isinstance(res, dict):
        print(f"  positions failed → code={res.get('code')} msg={res.get('msg')}")
    else:
        print(f"  positions: {str(res)[:300]}")


# 调试菜单 - 设置风控上限（适配统一返回格式）
async def _menu_risk(gw: PolymarketGateway) -> None:
    """菜单 - 设置风控上限"""
    print("  当前风控:", json.dumps({
        "max_amount_usd": gw._risk_max_amount_usd,
        "price_floor": gw._risk_price_floor,
        "price_cap": gw._risk_price_cap,
        "max_open_orders": gw._risk_max_open_orders,
    }, ensure_ascii=False))
    amt = input("  新 max_amount_usd (回车跳过): ").strip()
    fl = input("  新 price_floor (回车跳过): ").strip()
    cap = input("  新 price_cap (回车跳过): ").strip()
    moo = input("  新 max_open_orders (回车跳过): ").strip()
    res = gw.set_risk_limits(
        max_amount_usd=float(amt) if amt else None,
        price_floor=float(fl) if fl else None,
        price_cap=float(cap) if cap else None,
        max_open_orders=int(moo) if moo else None,
    )
    if isinstance(res, dict):
        print(f"  risk limits updated → success={res.get('success')} code={res.get('code')} msg={res.get('msg')}")
        data = res.get("data", {})
        if data:
            print(f"                       max_amount_usd={data.get('max_amount_usd')}")
            print(f"                       price_range=[{data.get('price_floor')}, {data.get('price_cap')}]")
            print(f"                       max_open_orders={data.get('max_open_orders')}")
    else:
        print("  ✅ 已更新风控")


# 调试菜单 - 菜单列表
_MENU = [
    ("1", "ping 网关连通性", _menu_ping),
    ("2", "显示网关状态", _menu_status),
    ("3", "主动健康检查", _menu_health),
    ("4", "创建/派生 L2 API Key", _menu_l2),
    ("5", "列出市场 (前 5 条)", _menu_markets),
    ("6", "查 token 中间价", _menu_midpoint),
    ("7", "查 token 订单簿", _menu_book),
    ("8", "查当前活跃 BTC 5min 市场", _menu_btc5m_market),
    ("9", "BTC5min一键下单", _menu_btc5m_order),
    ("10", "手动限价下单", _menu_place_order),
    ("11", "撤单", _menu_cancel),
    ("12", "查挂单", _menu_open_orders),
    ("13", "查余额", _menu_balance),
    ("14", "查持仓", _menu_positions),
    ("15", "设置风控上限", _menu_risk),
    ("0", "退出", None),
]


# 运行调试菜单主循环
async def _run_menu() -> None:
    """运行交互式菜单"""
    print("=" * 60)
    print("PolymarketGateway 调试菜单")
    print("=" * 60)
    chain = (settings.POLYMARKET_CHAIN or "polygon").lower()
    host = POLY_CLOB_HOST_STAGING if chain == "goerli" else POLY_CLOB_HOST_MAINNET
    chain_id = CHAIN_ID_AMOY if chain == "amoy" else CHAIN_ID_POLYGON
    gw = PolymarketGateway(host=host, chain_id=chain_id)
    print(f"[init] host={gw.host} chain_id={gw.chain_id} sdk_available={_HAS_SDK}")
    print(f"[init] wallet_address={gw.wallet_address or '(未配置)'}")
    print(f"[init] api_key={(gw.api_key[:8] + '***') if gw.api_key else '(未配置)'}")
    await gw.connect()
    if not gw.is_ready():
        print("[warn] 网关未完全就绪（钱包未配置或 HTTP 异常），只读接口可用")
    try:
        while True:
            print()
            print("-" * 60)
            for k, label, _ in _MENU:
                print(f"  {k:>3}  {label}")
            print("-" * 60)
            choice = input("选择 > ").strip()
            match = next((m for m in _MENU if m[0] == choice), None)
            if not match:
                print("  无效选择")
                continue
            if choice == "0":
                print("  bye.")
                break
            try:
                await match[2](gw)
            except Exception as e:  # noqa: BLE001
                print(f"  [err] {e}")
            input("  按任意键继续...")
    finally:
        await gw.close()


# 运行调试菜单主循环
if __name__ == "__main__":
    asyncio.run(_run_menu())