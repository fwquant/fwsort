# -*- coding: utf-8 -*-
"""
PM 网关实际连通性测试（公开端点 + 真实 V2 SDK 探测）
- 公开端点不需要钱包密钥
- 验证 PolymarketGateway(V2) / PolymarketV1Client(V1) 均能联通 Polymarket 真实 API
- 验证 SDK 加载状态
执行：python scripts/_dev/debug_pm_gateway.py
"""
import asyncio
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
os.environ.setdefault("APP_ENV", "development")


def banner(t):
    print("\n" + "=" * 70)
    print(f" {t}")
    print("=" * 70)


async def main():
    from fwsort.config import settings
    from fwsort.gateway import (
        get_hub, )
    from fwsort.gateway.polymarket.polymarket_gateway import _HAS_SDK

    banner("0. SDK 与基础配置")
    print(f"  polymarket-client SDK 加载: {_HAS_SDK}")
    print(f"  POLYMARKET_CHAIN = {settings.POLYMARKET_CHAIN}")
    print(f"  POLYMARKET_HOST  = {settings.POLYMARKET_HOST}")
    print(f"  钱包配置: "
          f"pk={'有' if settings.POLYMARKET_PRIVATE_KEY else '无'} "
          f"addr={'有' if settings.POLYMARKET_WALLET_ADDRESS else '无'} "
          f"api_key={'有' if settings.POLYMARKET_APIKEY else '无'}")

    hub = get_hub()

    # ========== V2 网关（PolymarketGateway，业务推荐）==========
    banner("1. PolymarketGateway(V2) — 公开端点连通性")
    pm_v2 = hub.polymarket_v2
    print(f"  host      = {pm_v2.host}")
    print(f"  chain_id  = {pm_v2.chain_id}")
    print(f"  sdk_avail = {pm_v2.is_sdk_available()}")
    print(f"  is_ready  = {pm_v2.is_ready()}  (未配置钱包 → 预期 False)")

    # 1.1 ping（基类 _do_ping 走 /markets?limit=1）
    try:
        ping_res = await pm_v2.ping()
        print(f"  ping      = {ping_res}")
    except Exception as e:  # noqa: BLE001
        print(f"  ping 失败: {type(e).__name__}: {e},traceback={traceback.format_exc()}")

    # 1.2 get_status
    print(f"  status    = {json.dumps(pm_v2.get_status(), ensure_ascii=False, indent=4)[:600]}")

    # 1.3 health_check
    try:
        health = await pm_v2.health_check()
        print(f"  health    = {json.dumps(health, ensure_ascii=False, indent=4)[:600]}")
    except Exception as e:  # noqa: BLE001
        print(f"  health 失败: {type(e).__name__}: {e},traceback={traceback.format_exc()}")

    # 1.4 get_markets 真实拉取（前 3 条）
    try:
        markets = await pm_v2.get_markets(limit=3)
        if isinstance(markets, list):
            print(f"  markets   = {len(markets)} 条（公开 markets）")
            for i, m in enumerate(markets[:3]):
                q = (m.get("question") or "?")[:60]
                cid = (m.get("condition_id") or m.get("conditionId") or "?")[:20]
                print(f"    [{i}] {q} | cid={cid}...")
        else:
            print(f"  markets   = {str(markets)[:300]}")
    except Exception as e:  # noqa: BLE001
        print(f"  get_markets 失败: {type(e).__name__}: {e},traceback={traceback.format_exc()}")

    # 1.5 get_active_btc5m_market（Gamma 公开 API）
    try:
        btc5m = await pm_v2.get_active_btc5m_market()
        if isinstance(btc5m, list):
            print(f"  btc5m     = {len(btc5m)} 个活跃 BTC 5min 市场")
            if btc5m:
                m = btc5m[0]
                print(f"             head: {m.get('question','?')[:80]}")
                print(f"             slug: {m.get('slug','?')}")
                print(f"             tokens: {len(m.get('tokens', []))}")
        else:
            data = btc5m.get("data", btc5m) if isinstance(btc5m, dict) else btc5m
            n = len(data) if isinstance(data, list) else 0
            print(f"  btc5m     = {n} 个（dict 包装）")
    except Exception as e:  # noqa: BLE001
        print(f"  get_active_btc5m_market 失败: {type(e).__name__}: {e},traceback={traceback.format_exc()}")

    # ========== V1 网关（PolymarketV1Client，路由层兼容）==========
    banner("2. PolymarketV1Client(V1) — 公开端点连通性")
    pm_v1 = hub.polymarket_v1
    print(f"  host     = {pm_v1.host}")
    print(f"  is_ready = {pm_v1.is_ready()}  (未配置钱包 → 预期 False)")
    print(f"  is_configured = {pm_v1.is_configured()}")

    # 2.1 ping
    try:
        ping_v1 = await pm_v1.ping()
        sample = json.dumps(ping_v1, ensure_ascii=False)[:200]
        print(f"  ping     = {sample}...")
    except Exception as e:  # noqa: BLE001
        print(f"  ping 失败: {type(e).__name__}: {e},traceback={traceback.format_exc()}")

    # 2.2 get_markets
    try:
        m1 = await pm_v1.get_markets()
        if isinstance(m1, dict):
            data = m1.get("data", m1)
            if isinstance(data, list):
                print(f"  markets  = {len(data)} 条（公开）")
            else:
                print(f"  markets  = {str(m1)[:200]}")
        else:
            print(f"  markets  = {str(m1)[:200]}")
    except Exception as e:  # noqa: BLE001
        print(f"  get_markets 失败: {type(e).__name__}: {e},traceback={traceback.format_exc()}")

    # 2.3 get_active_btc_market
    try:
        b5m = await pm_v1.get_active_btc_market()
        data = b5m if isinstance(b5m, list) else b5m.get("data", b5m) if isinstance(b5m, dict) else []
        n = len(data) if isinstance(data, list) else 0
        print(f"  btc5m    = {n} 个活跃 BTC 5min 市场（V1 路径）")
    except Exception as e:  # noqa: BLE001
        print(f"  get_active_btc_market 失败: {type(e).__name__}: {e},traceback={traceback.format_exc()}")

    # ========== 关闭清理 ==========
    banner("3. 清理")
    await hub.close_all()
    print("  ✅ hub.close_all() 完成")

    banner("RESULT")
    print("  ✅ PM 网关（V1 + V2 + 模拟盘）连通性调试通过")


if __name__ == "__main__":
    asyncio.run(main())
