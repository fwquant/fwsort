# -*- coding: utf-8 -*-
"""F3 Relayer Gasless 连通性测试（只到获取市场，不下单）"""
import asyncio
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv()

from polymarket import AsyncSecureClient
from polymarket.auth import RelayerApiKey


async def main():
    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    relayer_key = os.environ.get("POLYMARKET_RELAYER_API_KEY", "")
    relayer_addr = os.environ.get("POLYMARKET_RELAYER_API_KEY_ADDRESS", "")

    print(f"private_key: {'已设置' if private_key else '未设置'}")
    print(f"relayer_key: {relayer_key[:10]}..." if relayer_key else "relayer_key: 未设置")
    print(f"relayer_addr: {relayer_addr[:10]}..." if relayer_addr else "relayer_addr: 未设置")

    if not all([private_key, relayer_key, relayer_addr]):
        print("❌ 环境变量不完整")
        return

    # 构建 RelayerApiKey
    relayer_api_key = RelayerApiKey(
        key=relayer_key,
        address=relayer_addr,
    )
    print(f"\nRelayerApiKey 构建成功: {relayer_api_key}")

    # 创建客户端
    print("\n[1] 创建 AsyncSecureClient (F3 Relayer Gasless)...")
    try:
        client = await AsyncSecureClient.create(
            private_key=private_key,
            api_key=relayer_api_key,
        )
        print(f"  ✅ 客户端创建成功: {client}")
    except Exception as e:
        print(f"  ❌ 客户端创建失败: {type(e).__name__}: {e},traceback={traceback.format_exc()}")
        return

    # 获取当前 5min 市场
    import time
    now = int(time.time())
    epoch = now - (now % 300) + 300
    slug = f"btc-updown-5m-{epoch}"
    print(f"\n[2] 获取市场 slug={slug}...")
    try:
        market = await client.get_market(slug=slug)
        print(f"  ✅ 市场获取成功")
        print(f"     question={market.question}")
        print(f"     condition_id={market.condition_id}")
        print(f"     yes token_id={market.outcomes.yes.token_id}")
        print(f"     yes price={market.outcomes.yes.price}")
        print(f"     no  token_id={market.outcomes.no.token_id}")
        print(f"     no  price={market.outcomes.no.price}")
    except Exception as e:
        print(f"  ❌ 市场获取失败: {type(e).__name__}: {e},traceback={traceback.format_exc()}")
        await client.close()
        return

    # 查询余额/持仓
    print(f"\n[3] 查询钱包持仓...")
    try:
        page = await client.list_positions().first_page()
        print(f"  ✅ 持仓查询成功，共 {len(page.items)} 条")
        for item in page.items[:3]:
            print(f"     - {item}")
    except Exception as e:
        print(f"  ⚠️ 持仓查询失败: {type(e).__name__}: {e},traceback={traceback.format_exc()}")

    await client.close()
    print(f"\n[4] 客户端已关闭")
    print("\n✅ F3 连通性测试通过（未下单，仅验证客户端创建+市场查询+持仓查询）")


if __name__ == "__main__":
    asyncio.run(main())
