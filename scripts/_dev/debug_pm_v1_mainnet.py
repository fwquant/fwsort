# -*- coding: utf-8 -*-
"""
PM V1 客户端实际连通性（强制 MAINNET host，绕开 goerli 配置）
"""
import asyncio
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
os.environ.setdefault("APP_ENV", "development")


async def main():
    from fwsort.gateway import PolymarketV1Client

    print("=" * 70)
    print(" PM V1 客户端（强制 MAINNET host）连通性测试")
    print("=" * 70)

    # 强制 MAINNET（绕开 settings.POLYMARKET_CHAIN=goerli）
    pm = PolymarketV1Client(host="MAINNET")
    print(f"\n  host = {pm.host}")
    print(f"  is_configured = {pm.is_configured()}")
    print(f"  is_ready      = {pm.is_ready()}")
    print(f"  status        = {pm.get_status()}")

    # ping
    try:
        r = await pm.ping()
        print(f"\n  ping ok: ok={r.get('ok')} status={r.get('status', r.get('data', '?'))[:200]}")
    except Exception as e:  # noqa: BLE001
        print(f"\n  ping FAILED: {type(e).__name__}: {e},traceback={traceback.format_exc()}")

    # get_markets
    try:
        m = await pm.get_markets()
        if isinstance(m, dict) and "data" in m:
            data = m["data"]
            n = len(data) if isinstance(data, list) else 0
            print(f"  get_markets: {n} 条（public）")
            if n:
                head = data[0]
                print(f"    head: {head.get('question','?')[:60]}")
        else:
            print(f"  get_markets: {str(m)[:200]}")
    except Exception as e:  # noqa: BLE001
        print(f"  get_markets FAILED: {type(e).__name__}: {e},traceback={traceback.format_exc()}")

    # get_active_btc_market
    try:
        b = await pm.get_active_btc_market()
        if isinstance(b, list):
            print(f"  btc5m: {len(b)} 个")
            if b:
                m0 = b[0]
                print(f"    head slug: {m0.get('slug','?')}")
                print(f"    head condition_id: {(m0.get('condition_id','?'))[:20]}...")
        else:
            data = b.get("data", b) if isinstance(b, dict) else b
            n = len(data) if isinstance(data, list) else 0
            print(f"  btc5m: {n} 个（dict 包装）")
    except Exception as e:  # noqa: BLE001
        print(f"  btc5m FAILED: {type(e).__name__}: {e},traceback={traceback.format_exc()}")

    await pm.close()
    print("\n  ✅ V1 客户端（MAINNET）调试完成")


if __name__ == "__main__":
    asyncio.run(main())
