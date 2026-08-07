# -*- coding: utf-8 -*-
"""F1 + F2 连通性测试（只到客户端创建+市场查询，不下单）"""
import asyncio
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv()

from polymarket import AsyncSecureClient, ApiKeyCreds


async def test_f1():
    print("=" * 60)
    print("F1: L1钱包签名 连通性测试")
    print("=" * 60)
    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    if not private_key:
        print("❌ 缺少 POLYMARKET_PRIVATE_KEY")
        return False

    try:
        # 不传 wallet=，让 SDK 自动推导 deposit wallet
        client = await AsyncSecureClient.create(
            private_key=private_key,
        )
        print(f"  ✅ F1 客户端创建成功: {client}")
        await client.close()
        return True
    except Exception as e:
        print(f"  ❌ F1 客户端创建失败: {type(e).__name__}: {e},traceback={traceback.format_exc()}")
        return False


async def test_f2():
    print("\n" + "=" * 60)
    print("F2: L2 HMAC 认证 连通性测试")
    print("=" * 60)
    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    api_key = os.environ.get("POLYMARKET_APIKEY", "")
    secret = os.environ.get("POLYMARKET_SECRET", "")
    passphrase = os.environ.get("POLYMARKET_PASSPHRASE", "")
    if not all([private_key, api_key, secret, passphrase]):
        print("❌ 缺少 L2 凭据（POLYMARKET_APIKEY/SECRET/PASSPHRASE）")
        return False

    try:
        creds = ApiKeyCreds(apiKey=api_key, secret=secret, passphrase=passphrase)
        print(f"  L2 凭据: key={creds.key[:8]}***")
        # 不传 wallet=，让 SDK 自动推导
        client = await AsyncSecureClient.create(
            private_key=private_key,
            credentials=creds,
        )
        print(f"  ✅ F2 客户端创建成功: {client}")
        await client.close()
        return True
    except Exception as e:
        print(f"  ❌ F2 客户端创建失败: {type(e).__name__}: {e},traceback={traceback.format_exc()}")
        return False


async def main():
    ok1 = await test_f1()
    ok2 = await test_f2()
    print("\n" + "=" * 60)
    print(f"结果: F1={'✅' if ok1 else '❌'} F2={'✅' if ok2 else '❌'}")


if __name__ == "__main__":
    asyncio.run(main())
