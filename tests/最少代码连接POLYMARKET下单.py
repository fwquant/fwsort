import asyncio
import os
import time

from celery.bin.result import result
from dotenv import load_dotenv
from polymarket import AsyncSecureClient

load_dotenv()


async def main():
    # 1）连接钱包
    client = await AsyncSecureClient.create(
        private_key=os.environ["POLYMARKET_PRIVATE_KEY"],
        wallet=os.environ["POLYMARKET_WALLET_ADDRESS"]
    )
    print(f"连接钱包,polymarket连接WEB3钱包,client = {client}")

    # 2）获取市场
    market = await client.get_market(slug=f"btc-updown-5m-{int(time.time()) - (int(time.time()) % 300)}")
    print(f"获取市场 market = {market}")

    # 3）下市价单：最多花费 1 USDC(pUSD)
    response = await client.place_market_order(
        token_id=market.outcomes.yes.token_id,  # 选择 YES 结果标的
        side="BUY",  # 买方 一般情况固定为 BUY
        amount="1"  # 下单数量 单位为 USDC
    )
    print(f"下市价单 response = {response}")

    # 4）等待链上结算完成
    tx_hashes = await client.wait_for_order_fill_settlement(response)
    print("等待交易上链哈希：", tx_hashes)

    # 5）查询持仓验证
    page = await client.list_positions(market=[market.condition_id]).first_page()
    position = next(
        (item for item in page.items if item.token_id == market.outcomes.yes.token_id),
        None
    )
    print(f"查询持仓验证,market.condition_id = {market.condition_id},持有份额 position = {position}")

    # 6）关闭对象，清空实例
    await client.close()
    print("关闭对象，清空实例")


if __name__ == "__main__":
    result=asyncio.run(main())
    print((f"result = {result}"))
