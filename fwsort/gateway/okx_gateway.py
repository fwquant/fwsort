# OKX V5 网关（DEMO/LIVE 通用）
# 文档：https://www.okx.com/docs-v5/zh/
# 架构：OkxClient（REST 签名）+ OkxGateway（继承 BaseGateway）整合到同一文件
# 签名：HMAC SHA256 + Base64
# 职责：
#   - OkxClient：API 签名 + 通用 REST 封装（httpx 客户端由调用方注入）
#   - OkxGateway：业务封装（行情→下单→查状态），继承 BaseGateway 纳入统一管理
import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from fwsort.config import settings
from fwsort.gateway.base import BaseGateway


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


# ========== OkxClient：REST 签名客户端（业务层一般不直接使用，由 OkxGateway 组合）==========
class OkxClient:
    """OKX V5 REST 客户端（自实现签名，零依赖）

    职责：
    - API 三要素签名（HMAC-SHA256 + Base64）
    - 统一请求入口（GET/POST）
    - 行情/账户/交易 API 封装

    注：httpx.AsyncClient 由 OkxGateway 注入，避免重复创建
    """

    def __init__(self, demo: bool = True) -> None:
        self.api_key = settings.OKX_API_KEY
        self.secret = settings.OKX_SECRET
        self.passphrase = settings.OKX_PASSPHRASE
        self.host = OKX_HOSTS["DEMO"]
        self.simulated = "1" if demo else "0"

    def is_configured(self) -> bool:
        """API 三要素是否配置完整"""
        return bool(self.api_key and self.secret and self.passphrase)

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
        client: Any,
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
            logger.info(f"[OKX] {method} {path} ok latency={latency}ms")
        return data

    # ========== 公共 API：账户/行情 ==========
    async def get_account_balance(self, client: Any, ccy: str = "USDT") -> dict:
        """查询账户余额（GET /api/v5/account/balance）"""
        path = f"/api/v5/account/balance?ccy={ccy}"
        return await self._request(client, "GET", path)

    async def get_ticker(self, client: Any, inst_id: str) -> dict:
        """查询某个产品的行情（GET /api/v5/market/ticker）"""
        path = f"/api/v5/market/ticker?instId={inst_id}"
        return await self._request(client, "GET", path)

    # ========== 交易 API：下单/撤单/查询 ==========
    async def place_order(
        self,
        client: Any,
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
            client: httpx.AsyncClient 实例
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
        return await self._request(client, "POST", "/api/v5/trade/order", body)

    async def get_order(
        self,
        client: Any,
        inst_id: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict:
        """查询订单状态（GET /api/v5/trade/order）"""
        params = [f"instId={inst_id}"]
        if order_id:
            params.append(f"ordId={order_id}")
        if client_order_id:
            params.append(f"clOrdId={client_order_id}")
        path = "/api/v5/trade/order?" + "&".join(params)
        return await self._request(client, "GET", path)

    async def cancel_order(
        self,
        client: Any,
        inst_id: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict:
        """撤销订单（POST /api/v5/trade/cancel-order）"""
        body: dict[str, Any] = {"instId": inst_id}
        if order_id:
            body["ordId"] = order_id
        if client_order_id:
            body["clOrdId"] = client_order_id
        return await self._request(client, "POST", "/api/v5/trade/cancel-order", body)


# ========== 工具：USD 金额 → OKX 可接受的数量 ==========
def usd_to_size(amount_usd: float, last_price: float, lot_size: float = 0.0001) -> str:
    """根据 USD 金额和最新价换算币数，并按 lot_size 向下取整"""
    if last_price <= 0:
        raise ValueError(f"invalid last_price: {last_price}")
    raw = amount_usd / last_price
    sz = (int(raw / lot_size)) * lot_size
    if sz <= 0:
        sz = lot_size
    return f"{sz:.8f}".rstrip("0").rstrip(".")


# ========== OkxGateway：OKX 网关（继承 BaseGateway）==========
class OkxGateway(BaseGateway):
    """OKX 网关（DEMO/LIVE 通用）

    职责：
    1) 拉行情（获取 lastPx）→ 2) 计算币数 sz → 3) place_order → 4) 查 order 状态

    继承自 BaseGateway：
    - name = "okx"
    - 内部组合 OkxClient（REST 签名客户端）
    - 共享基类 HTTP 客户端，避免重复 httpx 实例
    """

    # 主流币对 lot size（简化：BTC 0.0001, ETH 0.001, 其他 0.01）
    LOT_SIZE_MAP = {
        "BTC": 0.0001,
        "ETH": 0.001,
        "SOL": 0.01,
        "DOGE": 1.0,
        "USDC": 0.01,
    }

    # 基类要求：平台名
    name: str = "okx"

    def __init__(self, demo: bool = True) -> None:
        # 调用基类初始化（共享 HTTP 客户端）
        super().__init__(
            host=OKX_HOSTS["DEMO"],
            chain_id=0,
            http_timeout=10.0,
        )
        # 内部 OkxClient（组合方式）
        self.client = OkxClient(demo=demo)
        # 同步 host（OkxClient 与基类 host 应一致）
        self.host = self.client.host

    # ===== 抽象方法实现（BaseGateway 要求） =====
    def is_configured(self) -> bool:
        """OKX 密钥是否齐备（委托给内部 OkxClient）"""
        return self.client.is_configured()

    def is_ready(self) -> bool:
        """OKX 密钥齐备 + HTTP 客户端已开"""
        return bool(
            self.is_configured()
            and self._http is not None
            and not self._http.is_closed
        )

    async def _do_ping(self) -> dict:
        """OKX 公开端点连通性探测（GET /api/v5/public/time，无需鉴权）"""
        try:
            client = await self._get_http()
            resp = await client.get(
                f"{self.host}/api/v5/public/time",
                headers={"Content-Type": "application/json"},
            )
            return {"ok": resp.status_code == 200, "status": resp.status_code}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def _symbol_to_inst_id(self, symbol: str) -> tuple[str, float]:
        """BTCUSDT → BTC-USDT, 取 lot_size"""
        if "USDT" in symbol.upper():
            base = symbol.upper().replace("USDT", "")
        else:
            base = symbol.upper()
        lot = self.LOT_SIZE_MAP.get(base, 0.01)
        return f"{base}-USDT", lot

    async def submit(
        self,
        symbol: str,
        side: int,
        amount_usd: float,
    ) -> OkxOrderResult:
        """真实下单（市价单）

        Args:
            symbol: BTCUSDT 等
            side: 1=buy 2=sell
            amount_usd: 美元金额
        """
        t0 = time.perf_counter()

        if amount_usd <= 0:
            return OkxOrderResult(
                order_id="", client_order_id="", symbol=symbol,
                side="buy" if side == 1 else "sell", order_type="market",
                amount_usd=0.0, filled_qty=0.0, avg_price=0.0, state="failed",
                latency_ms=0, raw={"reason": "amount_usd <= 0"},
            )

        inst_id, lot_size = self._symbol_to_inst_id(symbol)

        # 1) 拉行情拿 lastPx
        http_client = await self._get_http()
        ticker = await self.client.get_ticker(http_client, inst_id)
        if ticker.get("code") != "0" or not ticker.get("data"):
            raise RuntimeError(f"OKX get_ticker failed: {ticker.get('msg')}")
        last_px = float(ticker["data"][0]["last"])

        # 2) 计算币数
        sz_str = usd_to_size(amount_usd, last_px, lot_size)
        side_str = "buy" if side == 1 else "sell"
        client_oid = f"FW{uuid.uuid4().hex[:20]}"

        # 3) 下单（市价单）
        resp = await self.client.place_order(
            client=http_client,
            inst_id=inst_id,
            side=side_str,
            order_type="market",
            sz=sz_str,
            td_mode="cash",
            client_order_id=client_oid,
        )
        if resp.get("code") != "0" or not resp.get("data"):
            raise RuntimeError(f"OKX place_order failed: {resp.get('msg')}")
        ord_data = resp["data"][0]
        ord_id = ord_data.get("ordId", "")

        # 4) 查订单状态
        order_info = await self.client.get_order(
            client=http_client, inst_id=inst_id, order_id=ord_id
        )
        info = (order_info.get("data") or [{}])[0]
        filled_qty = float(info.get("fillSz", 0))
        avg_px = float(info.get("avgPx", 0))
        state = info.get("state", "")
        latency_ms = int((time.perf_counter() - t0) * 1000)

        logger.info(
            f"[OKX-REAL] {symbol} {side_str} ${amount_usd:.2f} → "
            f"sz={sz_str} px={avg_px} state={state} latency={latency_ms}ms"
        )
        return OkxOrderResult(
            order_id=ord_id,
            client_order_id=client_oid,
            symbol=symbol,
            side=side_str,
            order_type="market",
            amount_usd=amount_usd,
            filled_qty=filled_qty,
            avg_price=avg_px,
            state=state,
            latency_ms=latency_ms,
            raw={"order_resp": ord_data, "info": info, "last_px": last_px},
        )

    # ===== 公共 API 透传（业务层也可直接调用）=====
    async def get_account_balance(self, ccy: str = "USDT") -> dict:
        """查询账户余额（透传 OkxClient）"""
        client = await self._get_http()
        return await self.client.get_account_balance(client, ccy)

    async def cancel_order(
        self, inst_id: str, order_id: str | None = None, client_order_id: str | None = None
    ) -> dict:
        """撤销订单（透传 OkxClient）"""
        client = await self._get_http()
        return await self.client.cancel_order(client, inst_id, order_id, client_order_id)


# ========== 向后兼容：保留旧类名（迁移期使用）==========
OkxExecutor = OkxGateway  # 别名（之前叫 OkxExecutor，重命名后叫 OkxGateway）
