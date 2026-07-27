"""OKX DEMO 链路 smoke test"""
import asyncio
from fwsort.execution.okx_client import OkxClient
from fwsort.execution.okx_executor import OkxExecutor


async def main():
    c = OkxClient()
    print(f"[OKX] configured={c.is_configured()}")
    if not c.is_configured():
        print("skip: no API key")
        return

    # 1) 拉行情
    print("[1] ticker BTC-USDT ...")
    t = await c.get_ticker("BTC-USDT")
    if t.get("code") == "0":
        last = t["data"][0]["last"]
        print(f"  last price: {last}")
    else:
        msg = t.get("msg")
        print(f"  ticker failed: {msg}")
        await c.close()
        return

    # 2) 拉账户余额
    print("[2] balance ...")
    b = await c.get_account_balance("USDT")
    if b.get("code") == "0":
        data = b["data"][0]
        details = data.get("details", [])
        if details:
            eq = details[0].get("eq", "0")
            print(f"  USDT balance: {eq}")
        else:
            total = data.get("totalEq", "?")
            print(f"  no USDT detail (DEMO default empty acc): totalEq={total}")
    else:
        msg = b.get("msg")
        print(f"  balance failed: {msg}")

    # 3) 极小金额测试下单（5 USD）
    print("[3] test place_order (skip if balance=0) ...")
    details = b.get("data", [{}])[0].get("details", [])
    if details and float(details[0].get("eq", 0)) > 5:
        executor = OkxExecutor(demo=True)
        try:
            res = await executor.submit("BTCUSDT", 1, 5.0)
            print(f"  order_id={res.order_id} avg_px={res.avg_price} state={res.state}")
        except RuntimeError as e:
            # DEMO 账户无交易权限时，gateway 会自动降级 simulator
            print(f"  place_order failed (expected for DEMO without trade permission): {e}")
        await executor.close()
    else:
        print("  skip: DEMO acc balance < $5")

    await c.close()
    print("=== OKX DEMO live test done ===")


if __name__ == "__main__":
    asyncio.run(main())
