import asyncio
import os
import time

from dotenv import load_dotenv
from polymarket import AsyncSecureClient, ApiKeyCreds
from polymarket.auth import RelayerApiKey

load_dotenv()


# 辅助：从环境变量读取必填项，缺失时给出清晰提示
def _env(key: str, required: bool = True) -> str:
    val = os.environ.get(key, "")
    if required and not val:
        raise RuntimeError(f"环境变量 {key} 未设置，请检查 .env 文件")
    return val


# 辅助：计算当前 5min 窗口的 slug 时间戳
def _current_btc5m_slug() -> str:
    now = int(time.time())
    # Polymarket BTC 5min 市场 slug 用窗口结束时间戳
    # 对齐到 5 分钟边界 + 300 = 当前窗口结束
    epoch = now - (now % 300) + 300
    return f"btc-updown-5m-{epoch}"


# 辅助：下单后统一处理（检查是否被拒 + 等待上链 + 查持仓）
async def _post_order(client: AsyncSecureClient, response, market) -> None:
    """下单后统一处理：检查是否被拒 → 等待上链 → 查持仓"""
    # place_market_order 返回 AcceptedOrder | RejectedOrder
    if not response.ok:
        print(f"  ❌ 订单被拒: code={response.code} message={response.message}")
        return

    print(f"  ✅ 订单已接受: order_id={response.order_id} status={response.status}")
    print(f"     making_amount={response.making_amount} taking_amount={response.taking_amount}")

    # 等待交易上链
    tx_hashes = await client.wait_for_order_fill_settlement(response)
    print(f"  ⛓️ 交易上链哈希: {tx_hashes}")

    # 查询持仓验证
    page = await client.list_positions(market=[market.condition_id]).first_page()
    position = next(
        (item for item in page.items if item.token_id == market.outcomes.yes.token_id),
        None
    )
    print(f"  📊 当前持仓: {position}")


# F1认证 L1钱包签名 (Relayer API)
async def F1_市单():
    print("=" * 50)
    print("F1: L1钱包签名 (Relayer API)")
    print("=" * 50)
    print("说明: 使用钱包私钥直接签名，每次交易需自己付Gas")
    print("环境变量: POLYMARKET_PRIVATE_KEY")
    print("-" * 50)

    private_key = _env("POLYMARKET_PRIVATE_KEY")

    # 创建客户端（L1 模式：仅私钥，wallet 由 SDK 自动推导为 signer 的 Deposit Wallet）
    # 注：不传 wallet=，因为 .env 中的 POLYMARKET_WALLET_ADDRESS 可能是 Polymarket 代理钱包地址
    # （与 EOA 签名者地址不同），SDK 会从私钥自动推导正确的 deposit wallet
    client = await AsyncSecureClient.create(
        private_key=private_key,
    )
    print(f"1、连接钱包, client = {client}")

    # 获取当前 5min BTC 涨跌市场
    slug = _current_btc5m_slug()
    market = await client.get_market(slug=slug)
    print(f"2、获取市场 slug={slug}")
    print(f"   question={market.question}")
    print(f"   condition_id={market.condition_id}")
    print(f"   yes token={market.outcomes.yes.token_id}")

    # 下市价单（$1 USDC 买 YES/UP）
    response = await client.place_market_order(
        token_id=market.outcomes.yes.token_id,
        side="BUY",
        amount="1"
    )
    print(f"3、下市价单 response = {response}")

    # 下单后处理
    await _post_order(client, response, market)

    await client.close()
    print(f"4、关闭对象，清空实例")


# F2认证 (HMAC API密钥)，更适合程序化交易
async def F2_市单():
    print("=" * 50)
    print("F2: L2认证 (HMAC API密钥)")
    print("=" * 50)
    print("说明: 使用API Key/Secret/Passphrase认证，更适合程序化交易")
    print("环境变量: POLYMARKET_PRIVATE_KEY, POLYMARKET_APIKEY, POLYMARKET_SECRET, POLYMARKET_PASSPHRASE")
    print("-" * 50)

    private_key = _env("POLYMARKET_PRIVATE_KEY")

    # 构建 L2 API 凭据
    creds = ApiKeyCreds(
        apiKey=_env("POLYMARKET_APIKEY"),
        secret=_env("POLYMARKET_SECRET"),
        passphrase=_env("POLYMARKET_PASSPHRASE")
    )
    print(f"1、L2认证凭证, creds = key={creds.key[:8]}***")

    # 创建客户端（L2 模式：私钥 + 预置 credentials）
    client = await AsyncSecureClient.create(
        private_key=private_key,
        credentials=creds,
    )
    print(f"2、连接钱包, client = {client}")

    # 获取当前 5min BTC 涨跌市场
    slug = _current_btc5m_slug()
    market = await client.get_market(slug=slug)
    print(f"3、获取市场 slug={slug}")
    print(f"   question={market.question}")
    print(f"   condition_id={market.condition_id}")
    print(f"   yes token={market.outcomes.yes.token_id}")

    # 下市价单（$1 USDC 买 YES/UP）
    response = await client.place_market_order(
        token_id=market.outcomes.yes.token_id,
        side="BUY",
        amount="1"
    )
    print(f"4、下市价单 response = {response}")

    # 下单后处理
    await _post_order(client, response, market)

    await client.close()
    print(f"5、关闭对象，清空实例")


# F3认证: Relayer Gasless (免Gas费)，无需持有POL
async def F3_市单():
    print("=" * 50)
    print("F3: Relayer Gasless (免Gas费)")
    print("=" * 50)
    print("说明: Polymarket官方Relayer代付Gas，无需持有POL")
    print("环境变量: POLYMARKET_PRIVATE_KEY, POLYMARKET_RELAYER_API_KEY, POLYMARKET_RELAYER_API_KEY_ADDRESS")
    print("-" * 50)

    private_key = _env("POLYMARKET_PRIVATE_KEY")
    relayer_key = _env("POLYMARKET_RELAYER_API_KEY")
    # RelayerApiKey 需要 key + address（钱包地址）
    relayer_addr = _env("POLYMARKET_RELAYER_API_KEY_ADDRESS")

    print(f"1、Relayer API Key: {relayer_key[:10]}... address={relayer_addr[:10]}...")

    # 构建 RelayerApiKey 对象（SDK 要求 key + address 两项）
    relayer_api_key = RelayerApiKey(
        key=relayer_key,
        address=relayer_addr,
    )

    # 创建客户端（F3 模式：私钥 + api_key=RelayerApiKey，免 Gas 下单）
    client = await AsyncSecureClient.create(
        private_key=private_key,
        api_key=relayer_api_key,
    )
    print(f"2、连接钱包(Relayer Gasless), client = {client}")

    # 获取当前 5min BTC 涨跌市场
    slug = _current_btc5m_slug()
    market = await client.get_market(slug=slug)
    print(f"3、获取市场 slug={slug}")
    print(f"   question={market.question}")
    print(f"   condition_id={market.condition_id}")
    print(f"   yes token={market.outcomes.yes.token_id}")

    # 下市价单（$1 USDC 买 YES/UP）
    response = await client.place_market_order(
        token_id=market.outcomes.yes.token_id,
        side="BUY",
        amount="1"
    )
    print(f"4、下市价单 response = {response}")

    # 下单后处理（Relayer 代付 Gas）
    await _post_order(client, response, market)

    await client.close()
    print(f"5、关闭对象，清空实例")


def 显示菜单():
    print("\n" + "=" * 60)
    print("Polymarket 下单方式选择菜单")
    print("=" * 60)
    print("1、F1: L1钱包签名 (Relayer API)")
    print("    - 使用钱包私钥直接签名，每次交易需自己付Gas (POL)")
    print("")
    print("2、F2: L2认证 (HMAC API密钥)")
    print("    - 使用API Key/Secret/Passphrase认证，适合程序化交易")
    print("    - 每次交易需自己付Gas (POL)")
    print("")
    print("3、F3: Relayer Gasless (免Gas费)")
    print("    - Polymarket官方Relayer代付Gas，无需持有POL")
    print("    - 需要先申请Relayer API Key")
    print("")
    print("0: 退出")
    print("=" * 60)


async def main():
    while True:
        显示菜单()
        choice = input("请选择下单方式 (0/F1/F2/F3): ").strip().upper()

        if choice == "0":
            print("退出程序")
            break
        elif choice == "F1" or choice == "1":
            print("\n选择了: F1 - L1钱包签名\n")
            await F1_市单()
            break
        elif choice == "F2" or choice == "2":
            print("\n选择了: F2 - L2认证(HMAC)\n")
            await F2_市单()
            break
        elif choice == "F3" or choice == "3":
            print("\n选择了: F3 - Relayer Gasless\n")
            await F3_市单()
            break
        else:
            print("\n无效选择，请重新输入 (0/F1/F2/F3)")


if __name__ == "__main__":
    asyncio.run(main())
