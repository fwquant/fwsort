import asyncio
import os
import time
from decimal import Decimal
from typing import TypeAlias

from dotenv import load_dotenv
from polymarket import AsyncSecureClient,  OrderSide
from polymarket.auth import RelayerApiKey


load_dotenv()
def 获得当前时间值 (周期: int = 4*60*60):
    result=str(((int(time.time()) // 周期) ) *( 周期 ))
    return result

# F3认证: Relayer Gasless (免Gas费)，无需持有POL  标的代码就是 最后斜线后面的值 ，比如： https://polymarket.com/zh/event/btc-updown-4h-1785456000  值 为：btc-updown-4h-1785456000
async def F3_市单(标的代码: str = "btc-updown-4h-{epoch}", side:OrderSide = "BUY", amount: Decimal = 1):
    # 这三个值 当前在 .env，获取步骤：POLYMARKET 首页 登陆，右上头像,设置，Relayer API 密钥，新建。
    relayer_private_key = os.environ.get("POLYMARKET_RELAYER_PRIVATE_KEY")
    relayer_key = os.environ.get("POLYMARKET_RELAYER_API_KEY")
    relayer_addr = os.environ.get("POLYMARKET_RELAYER_API_KEY_ADDRESS")

    # 构建RelayerApiKey对象（SDK 要求 key + address 两项）
    relayer_api_key = RelayerApiKey(key=relayer_key, address=relayer_addr, )
    print(f"1、 构建RelayerApiKey对象,  relayer_api_key = {relayer_api_key}")

    # 创建客户端（F3 模式：私钥 + api_key=RelayerApiKey，免 Gas 下单）
    client = await AsyncSecureClient.create(private_key=relayer_private_key, api_key=relayer_api_key, )
    print(f"2、连接钱包(Relayer Gasless), client = {client}")

    # 获取当前 标的代码对应的盘口 涨跌市场   btc-updown-4h-1785470400
    market = await client.get_market(slug=标的代码.replace("{epoch}", 获得当前时间值(周期=4*60*60)))  # 标的代码对应的盘口
    print(f"3、获取市场 ，market={market}")

    # 下市价单（$1 USDC 买 YES/UP）
    response = await client.place_market_order(token_id=market.outcomes.yes.token_id, side=side, amount=amount)
    print(f"4、下市价单 response = {response}")

    # 查询持仓验证
    page = await client.list_positions(market=[market.condition_id]).first_page()
    print(f"5、查询持仓验证 page = {page}")

    await client.close()
    print(f"6、关闭对象，清空实例")
    return  True

if __name__ == "__main__":
    result = asyncio.run(F3_市单())
    print(f"下单结果 result = {result}")
    pass