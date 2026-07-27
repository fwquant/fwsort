# OKX 真实下单执行器（封装 OkxClient，提供与 OrderSimulator 一致的接口）
import time
import uuid
from datetime import datetime

from loguru import logger

from fwsort.execution.okx_client import OkxClient, OkxOrderResult, usd_to_size


class OkxExecutor:
    """OKX 真实下单执行器（DEMO/LIVE 通用）

    职责：
    1) 拉行情（获取 lastPx）→ 2) 计算币数 sz → 3) place_order → 4) 查 order 状态
    """

    # 主流币对 lot size（简化：BTC 0.0001, ETH 0.001, 其他 0.01）
    LOT_SIZE_MAP = {
        "BTC": 0.0001,
        "ETH": 0.001,
        "SOL": 0.01,
        "DOGE": 1.0,
        "USDC": 0.01,
    }

    def __init__(self, demo: bool = True) -> None:
        self.client = OkxClient(demo=demo)

    def is_ready(self) -> bool:
        """OKX 是否配置完整可用"""
        return self.client.is_configured()

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
        ticker = await self.client.get_ticker(inst_id)
        if ticker.get("code") != "0" or not ticker.get("data"):
            raise RuntimeError(f"OKX get_ticker failed: {ticker.get('msg')}")
        last_px = float(ticker["data"][0]["last"])

        # 2) 计算币数
        sz_str = usd_to_size(amount_usd, last_px, lot_size)
        side_str = "buy" if side == 1 else "sell"
        client_oid = f"FW{uuid.uuid4().hex[:20]}"

        # 3) 下单（市价单）
        resp = await self.client.place_order(
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
        order_info = await self.client.get_order(inst_id=inst_id, order_id=ord_id)
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

    async def close(self) -> None:
        await self.client.close()
