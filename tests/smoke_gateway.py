"""Test ExecutionGateway 真实 OKX 路径"""
import asyncio
from fwsort.execution.gateway import get_gateway


async def main():
    g = get_gateway()
    print("test gateway.submit(account_type=1, platform=okx):")
    r = await g.submit(account_type=1, platform="okx", symbol="BTCUSDT", side=1, amount_usd=5.0)
    print(f"  order_id: {r.order_id}")
    print(f"  platform: {r.platform}")
    print(f"  is_live: {r.is_live}")
    print(f"  status: {r.status}")
    print(f"  actual_price: {r.actual_price}")
    print(f"  quantity: {r.quantity}")
    print(f"  latency_ms: {r.latency_ms}")
    if r.is_live:
        okx_info = (r.extra or {}).get("okx", {})
        print(f"  okx.order_resp keys: {list(okx_info.get('order_resp', {}).keys())}")
    else:
        print(f"  extra: {r.extra}")
    await g.close()


asyncio.run(main())
