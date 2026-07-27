# Polymarket CLOB 客户端（完整 EIP-712 签名 + L2 API Key 认证）
# 文档：https://docs.polymarket.com/#clob-api
# 流程：钱包私钥 → EIP-712 创建/派生 L2 API Key → 签名订单 → POST /order
import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime

import httpx
from eth_account import Account
from eth_account.messages import encode_typed_data
from loguru import logger

from fwsort.config import settings


# Polymarket CLOB 主机（主网；测试可走 mock host）
POLY_HOSTS = {
    "MAINNET": "https://clob.polymarket.com",
    "GOERLI": "https://clob-staging.polymarket.com",
    "MOCK": "https://clob-mock.polymarket.com",
}

# CLOB 合约地址（mainnet 已知）
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # polygon USDC.e
CHAIN_ID = 137  # polygon


@dataclass
class PolyOrderResult:
    """Polymarket 真实下单结果"""

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


class PolymarketClient:
    """Polymarket CLOB 客户端（完整 L2 认证 + EIP-712 订单签名）"""

    def __init__(self, host: str = "MAINNET") -> None:
        self.host = POLY_HOSTS.get(host, POLY_HOSTS["MAINNET"])
        self.private_key = settings.POLYMARKET_WALLET_PRIVATE_KEY
        self.wallet_address = settings.POLYMARKET_WALLET_ADDRESS
        self.api_key = settings.POLYMARKET_API_KEY
        self._http: httpx.AsyncClient | None = None
        # EIP-712 派生 L2 API Key（首次调用时初始化）
        self._l2_keys: dict | None = None
        self._account = None
        if self.private_key:
            try:
                self._account = Account.from_key(self.private_key)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Polymarket wallet load failed: {e}")

    def is_configured(self) -> bool:
        """钱包私钥/地址是否配置"""
        return bool(self.private_key and self.wallet_address and self._account)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=10.0)
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

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
            "domain": {"name": "ClobAuthDomain", "version": "1", "chainId": CHAIN_ID},
            "message": {
                "address": self.wallet_address,
                "timestamp": str(timestamp),
                "nonce": int(nonce),
            },
        }
        encoded = encode_typed_data(full_message=payload)
        signed = self._account.sign_message(encoded)
        return f"0x{signed.signature.hex()}"

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
        client = await self._get_client()

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

    def _l2_headers(self, method: str, path: str, body: str = "") -> dict:
        """L2 API 头部签名：HMAC SHA256 Base64"""
        ts = str(int(time.time()))
        message = ts + method.upper() + path + body
        secret = (self._l2_keys or {}).get("secret", "").encode("utf-8")
        if not secret:
            # 没有 secret 时跳过 HMAC 头
            sig = ""
        else:
            sig = base64.b64encode(hmac.new(secret, message.encode("utf-8"), hashlib.sha256).digest()).decode()
        return {
            "POLY_ADDRESS": self.wallet_address,
            "POLY_API_KEY": (self._l2_keys or {}).get("apiKey", ""),
            "POLY_PASSPHRASE": (self._l2_keys or {}).get("passphrase", ""),
            "POLY_TIMESTAMP": ts,
            "POLY_SIGNATURE": sig,
        }

    async def _request(self, method: str, path: str, body: dict | None = None, auth_l1: bool = False) -> dict:
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

        client = await self._get_client()
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
            data = {"error": resp.text[:200]}

        if resp.status_code >= 400:
            logger.warning(f"[POLY] {method} {path} HTTP {resp.status_code}: {data}")
        return data

    # ========== 公共 API：市场/订单 ==========
    async def get_markets(self) -> dict:
        """获取市场列表"""
        return await self._request("GET", "/markets", auth_l1=True)

    async def get_market(self, condition_id: str) -> dict:
        """获取单个市场"""
        return await self._request("GET", f"/markets/{condition_id}", auth_l1=True)

    async def get_midpoint(self, token_id: str) -> dict:
        """获取 token 中间价（公开）"""
        return await self._request("GET", f"/midpoint?token_id={token_id}", auth_l1=True)

    async def get_order_book(self, token_id: str) -> dict:
        """获取订单簿（公开）"""
        return await self._request("GET", f"/book?token_id={token_id}", auth_l1=True)

    # ========== 下单 ==========
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
        """EIP-712 订单签名"""
        if nonce == 0:
            nonce = int(time.time())
        if expiration == 0:
            expiration = int(time.time()) + 86400  # 24h 过期

        order = {
            "salt": int.from_bytes(uuid.uuid4().bytes, "big") % (2**128),
            "maker": self.wallet_address,
            "signer": self.wallet_address,
            "taker": "0x0000000000000000000000000000000000000000",
            "tokenId": str(int(token_id, 16) if token_id.startswith("0x") else int(token_id)),
            "makerAmount": int(size * 1e6),  # USDC 6 位精度
            "takerAmount": int(size * price * 1e6),
            "expiration": str(expiration),
            "nonce": str(nonce),
            "feeRateBps": str(fee_rate_bps),
            "side": "0" if side.upper() == "BUY" else "1",
        }
        # EIP-712 domain
        domain = {
            "name": "Polymarket CTF Exchange",
            "version": "1",
            "chainId": CHAIN_ID,
            "verifyingContract": CTF_EXCHANGE,
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
            "types": {"EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ], **types},
            "primaryType": "Order",
            "domain": domain,
            "message": order,
        }
        encoded = encode_typed_data(full_message=payload)
        signed = self._account.sign_message(encoded)
        order["signature"] = f"0x{signed.signature.hex()}"
        return order

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

    async def get_order(self, order_id: str) -> dict:
        """查询订单状态"""
        return await self._request("GET", f"/order/{order_id}")

    async def cancel_order(self, order_id: str) -> dict:
        """取消订单"""
        return await self._request("DELETE", f"/order/{order_id}")
