"""Polymarket CLOB 链路 smoke test（网关连接 / 余额 / 持仓 / BTC 5min 市场 / 下单）"""
import asyncio
import os
import sys
import traceback

# 让脚本能直接从项目根目录运行
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fwsort.config import settings
from fwsort.gateway.polymarket_client import PolymarketClient


async def main():
    p = PolymarketClient()
    print(f"[POLY] host={p.host} configured={p.is_configured()}")
    print(f"[POLY] missing_keys={settings.polymarket_missing_keys}")
    print(f"[POLY] btc5m_enabled={settings.POLYMARKET_BTC5M_ENABLED} "
          f"effective={settings.btc5m_enabled_effective}")

    # ---- 0) 网关连通性（公开端点，无需密钥） ----
    print("\n[0] ping /markets (public) ...")
    try:
        ping_resp = await p.ping()
        print(f"  reachable=True, sample={str(ping_resp)[:120]}...")
    except Exception as e:
        print(f"  ping failed:{e}，traceback: {traceback.format_exc()}")

    # ---- 1) BTC 5min 活跃市场（公开端点，无需密钥） ----
    print("\n[1] get_active_btc_market (public) ...")
    try:
        mkt = await p.get_active_btc_market()
        data = mkt if isinstance(mkt, list) else mkt.get("data", mkt)
        count = len(data) if isinstance(data, list) else 0
        print(f"  active markets count={count}")
        if isinstance(data, list) and data:
            head = data[0]
            print(f"  head slug={head.get('slug') or head.get('market_slug')}")
            print(f"  head condition_id={head.get('condition_id')}")
            tokens = head.get("tokens") or []
            for i, t in enumerate(tokens):
                print(f"  token[{i}] outcome={t.get('outcome')} token_id={str(t.get('token_id') or t.get('clobTokenId'))[:18]}...")
    except Exception as e:
        print(f"  get_active_btc_market failed:{e}，traceback: {traceback.format_exc()}")

    # ---- 后续测试需要密钥 ----
    if not p.is_configured():
        print("\n[skip] wallet not configured, skip balance/positions/order tests")
        print("  → 请在 .env 填入 POLYMARKET_WALLET_PRIVATE_KEY / POLYMARKET_WALLET_ADDRESS 后重试")
        await p.close()
        print("\n=== Polymarket smoke test (no-key path) done ===")
        return

    # ---- 2) 派生 L2 API Key ----
    print("\n[2] ensure L2 keys ...")
    try:
        keys = await p._ensure_l2_keys()
        print(f"  apiKey: {keys.get('apiKey', '?')[:24]}...")
        print(f"  has secret: {bool(keys.get('secret'))}")
    except Exception as e:
        print(f"  ensure L2 keys failed:{e}，traceback: {traceback.format_exc()}")

    # ---- 3) 余额 ----
    print("\n[3] get_balance (wallet) ...")
    try:
        bal = await p.get_balance()
        print(f"  raw: {bal}")
    except Exception as e:
        print(f"  get_balance failed:{e}，traceback: {traceback.format_exc()}")

    # ---- 4) 持仓 ----
    print("\n[4] get_positions ...")
    try:
        pos = await p.get_positions()
        print(f"  raw (head): {str(pos)[:200]}...")
    except Exception as e:
        print(f"  get_positions failed:{e}，traceback: {traceback.format_exc()}")

    # ---- 5) 当前挂单 ----
    print("\n[5] get_open_orders ...")
    try:
        orders = await p.get_open_orders()
        print(f"  raw (head): {str(orders)[:200]}...")
    except Exception as e:
        print(f"  get_open_orders failed:{e}，traceback: {traceback.format_exc()}")

    # ---- 6) EIP-712 签名测试（不下单） ----
    print("\n[6] EIP-712 sign test (no submit) ...")
    try:
        signed = p._sign_order(token_id="12345", price=0.5, side="BUY", size=10.0)
        print(f"  salt: {signed['salt']}")
        print(f"  maker: {signed['maker'][:18]}...")
        print(f"  signature: {signed['signature'][:24]}...")
    except Exception as e:
        print(f"  sign test failed:{e}，traceback: {traceback.format_exc()}")

    # ---- 7) BTC 5min 真实下单（默认 skip，需显式环境变量打开） ----
    if os.environ.get("POLY_BTC5M_LIVE_ORDER") == "1":
        print("\n[7] place_btc5m_order (LIVE, side=UP, $5) ...")
        try:
            resp = await p.place_btc5m_order(side="UP", amount_usd=5.0)
            print(f"  resp: {resp}")
        except Exception as e:
            print(f"  place_btc5m_order failed:{e}，traceback: {traceback.format_exc()}")
    else:
        print("\n[7] skip place_btc5m_order (set POLY_BTC5M_LIVE_ORDER=1 to enable)")

    await p.close()
    print("\n=== Polymarket CLOB smoke test done ===")


if __name__ == "__main__":
    asyncio.run(main())
