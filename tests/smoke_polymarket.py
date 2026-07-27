"""Polymarket CLOB 链路 smoke test"""
import asyncio
from fwsort.execution.polymarket_client import PolymarketClient


async def main():
    p = PolymarketClient(host="MAINNET")
    print(f"[POLY] configured={p.is_configured()}")
    if not p.is_configured():
        print("skip: no wallet key")
        return

    # 1) 拉中间价（公开）
    print("[1] midpoint (test token 0x) ...")
    # 选一个真实可用的 token_id（BTC 价格走势市场 token）
    # 注：实际生产需要把 symbol 映射到 condition_id + token_id
    test_token = "0x0000000000000000000000000000000000000000"  # 占位
    mid = await p.get_midpoint(test_token)
    print(f"  midpoint: {mid}")

    # 2) 派生 L2 API Key
    print("[2] ensure L2 keys ...")
    keys = await p._ensure_l2_keys()
    print(f"  apiKey: {keys.get('apiKey', '?')[:24]}...")
    print(f"  has secret: {bool(keys.get('secret'))}")

    # 3) 签名测试（不实际下单）
    print("[3] EIP-712 sign test ...")
    signed = p._sign_order(token_id="12345", price=0.5, side="BUY", size=10.0)
    print(f"  salt: {signed['salt']}")
    print(f"  maker: {signed['maker'][:18]}...")
    print(f"  signature: {signed['signature'][:24]}...")

    await p.close()
    print("=== Polymarket CLOB live test done ===")


if __name__ == "__main__":
    asyncio.run(main())
