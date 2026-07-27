# OKX V5 REST 客户端（DEMO/LIVE 通用）
# 文档：https://www.okx.com/docs-v5/zh/
# 签名：HMAC SHA256 + Base64
import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from loguru import logger

from fwsort.config import settings


# OKX 主机（DEMO 走 aws-bj，LIVE 走 www）
OKX_HOSTS = {
    "DEMO": "https://www.okx.com",  # DEMO 模式实盘域名（X-Simulated-Trading=1）
    "LIVE": "https://www.okx.com",
}


@dataclass
class OkxOrderResult:
    """OKX 真实下单结果"""

    order_id: str
    client_order_id: str
    symbol: str
    side: str  # buy / sell
    order_type: str  # market / limit
    amount_usd: float
    filled_qty: float
    avg_price: float
    state: str  # filled / partially_filled / canceled
    latency_ms: int
    raw: dict


class OkxClient:
    """OKX V5 REST 客户端（自实现签名，零依赖）"""

    def __init__(self, demo: bool = True) -> None:
        self.api_key = settings.OKX_API_KEY
        self.secret = settings.OKX_SECRET
        self.passphrase = settings.OKX_PASSPHRASE
        # DEMO 模式：OKX 真实域名 + X-Simulated-Trading=1 header
        self.host = OKX_HOSTS["DEMO"]
        self.simulated = "1" if demo else "0"
        self._http: httpx.AsyncClient | None = None

    def is_configured(self) -> bool:
        """API 三要素是否配置完整"""
        return bool(self.api_key and self.secret and self.passphrase)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=10.0)
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()
            self._http = None

    def _sign(self, ts: str, method: str, path: str, body: str) -> str:
        """生成 OKX V5 签名：base64(HMAC-SHA256(secret, ts+method+path+body))"""
        msg = f"{ts}{method.upper()}{path}{body}"
        mac = hmac.new(
            self.secret.encode("utf-8"),
            msg.encode("utf-8"),
            hashlib.sha256,
        )
        return base64.b64encode(mac.digest()).decode("utf-8")

    async def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
    ) -> dict[str, Any]:
        """统一 OKX 请求入口（自动签名）"""
        if not self.is_configured():
            raise RuntimeError("OKX API key/secret/passphrase not configured")

        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        body_str = json.dumps(body, separators=(",", ":")) if body else ""
        sign = self._sign(ts, method, path, body_str)

        headers = {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
            "x-simulated-trading": self.simulated,
        }

        client = await self._get_client()
        url = f"{self.host}{path}"
        t0 = time.perf_counter()
        if method == "GET":
            resp = await client.get(url, headers=headers)
        elif method == "POST":
            resp = await client.post(url, headers=headers, content=body_str)
        else:
            raise ValueError(f"unsupported method: {method}")
        latency = int((time.perf_counter() - t0) * 1000)

        # OKX 错误码：code != "0" 视为失败
        try:
            data = resp.json()
        except Exception:
            data = {"code": str(resp.status_code), "msg": resp.text[:200]}

        if data.get("code") != "0":
            logger.warning(
                f"[OKX] {method} {path} failed: code={data.get('code')} "
                f"msg={data.get('msg')} latency={latency}ms"
            )
        else:
            logger.info(
                f"[OKX] {method} {path} ok latency={latency}ms"
            )
        return data

    # ========== 公共 API：账户/行情 ==========
    async def get_account_balance(self, ccy: str = "USDT") -> dict:
        """查询账户余额（GET /api/v5/account/balance）"""
        path = f"/api/v5/account/balance?ccy={ccy}"
        return await self._request("GET", path)

    async def get_ticker(self, inst_id: str) -> dict:
        """查询某个产品的行情（GET /api/v5/market/ticker）"""
        path = f"/api/v5/market/ticker?instId={inst_id}"
        return await self._request("GET", path)

    # ========== 交易 API：下单/撤单/查询 ==========
    async def place_order(
        self,
        inst_id: str,
        side: str,
        order_type: str,
        sz: str,
        td_mode: str = "cash",
        px: str | None = None,
        client_order_id: str | None = None,
    ) -> dict:
        """下单（POST /api/v5/trade/order）

        Args:
            inst_id: 产品ID，如 BTC-USDT
            side: buy / sell
            order_type: market / limit
            sz: 数量（币数）
            td_mode: cash(现货) / isolated(逐仓) / cross(全仓)
            px: 限价单必填
            client_order_id: 客户端订单ID（幂等）
        """
        body: dict[str, Any] = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": side,
            "ordType": order_type,
            "sz": sz,
        }
        if px:
            body["px"] = px
        if client_order_id:
            body["clOrdId"] = client_order_id
        return await self._request("POST", "/api/v5/trade/order", body)

    async def get_order(self, inst_id: str, order_id: str | None = None, client_order_id: str | None = None) -> dict:
        """查询订单状态（GET /api/v5/trade/order）"""
        params = [f"instId={inst_id}"]
        if order_id:
            params.append(f"ordId={order_id}")
        if client_order_id:
            params.append(f"clOrdId={client_order_id}")
        path = "/api/v5/trade/order?" + "&".join(params)
        return await self._request("GET", path)

    async def cancel_order(self, inst_id: str, order_id: str | None = None, client_order_id: str | None = None) -> dict:
        """撤销订单（POST /api/v5/trade/cancel-order）"""
        body: dict[str, Any] = {"instId": inst_id}
        if order_id:
            body["ordId"] = order_id
        if client_order_id:
            body["clOrdId"] = client_order_id
        return await self._request("POST", "/api/v5/trade/cancel-order", body)


# ========== 工具：把 USD 金额 + 行情价格 转换为 OKX 可接受的数量 ==========
def usd_to_size(amount_usd: float, last_price: float, lot_size: float = 0.0001) -> str:
    """根据 USD 金额和最新价换算币数，并按 lot_size 向下取整"""
    if last_price <= 0:
        raise ValueError(f"invalid last_price: {last_price}")
    raw = amount_usd / last_price
    # 向下取整到 lot_size 倍数
    sz = (int(raw / lot_size)) * lot_size
    # 至少 1 个 lot，避免为 0
    if sz <= 0:
        sz = lot_size
    # 保留 8 位小数（OKX 限制）
    return f"{sz:.8f}".rstrip("0").rstrip(".")
