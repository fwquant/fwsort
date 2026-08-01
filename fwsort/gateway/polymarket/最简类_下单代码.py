import asyncio
import os
import time
import traceback
from decimal import Decimal
from typing import Literal, TypeAlias

from dotenv import load_dotenv
from polymarket import AsyncSecureClient, OrderSide
from polymarket.auth import RelayerApiKey

from fwsort.gateway import BaseGateway

load_dotenv()


def 获得当前时间值(周期: int = 4 * 60 * 60):
    result = str(((int(time.time()) // 周期)) * (周期))
    return result


class pm类():

    def __init__(self, 标的代码: str = "btc-updown-4h-{epoch}"):
        self.market = None
        self.client = None
        self.标的代码 = 标的代码

    async def 初始化(self):
        await self.connect()
        result = await self.获得市场(标的代码=self.标的代码)
        print(f"获得市场={result}")

    async def connect(self):
        POLYMARKET_RELAYER_API_KEY_ADDRESS = os.environ.get("POLYMARKET_RELAYER_API_KEY_ADDRESS")
        POLYMARKET_RELAYER_PRIVATE_KEY = os.environ.get("POLYMARKET_RELAYER_PRIVATE_KEY")
        POLYMARKET_RELAYER_API_KEY = os.environ.get("POLYMARKET_RELAYER_API_KEY")
        print(f"\n【准备配置】完毕！")

        relayer_api_key = RelayerApiKey(key=POLYMARKET_RELAYER_API_KEY, address=POLYMARKET_RELAYER_API_KEY_ADDRESS, )
        print(f"1、 【构建RelayerApiKey】,  relayer_api_key = {relayer_api_key}")

        client = await AsyncSecureClient.create(private_key=POLYMARKET_RELAYER_PRIVATE_KEY, api_key=relayer_api_key, )
        print(f"2、【创建客户端】(Relayer Gasless), client = {client}")
        self.client = client

    async def 获得市场(self, 标的代码: str = "btc-updown-4h-{epoch}"):
        # 获取当前 标的代码对应的盘口 涨跌市场   btc-updown-4h-1785470400
        market = await self.client.get_market(
            slug=标的代码.replace("{epoch}", 获得当前时间值(周期=4 * 60 * 60)))  # 标的代码对应的盘口
        print(f"3、【获取市场】 ，market={market}")
        self.market = market
        return self.market

    async def 下单(self, 标的代码: str = "btc-updown-4h-{epoch}"
                   , outcome: Literal["YES", "NO", "Y", "N"] = "YES"
                   , amount: Decimal = 1
                   , side: OrderSide = "BUY"):
        self.market = await self.获得市场(标的代码=标的代码)

        # 下市价单（$1 USDC 买 YES/UP 或 NO/DOWN）
        selected_token_id = self.market.outcomes.yes.token_id if outcome.upper() in ("YES",
                                                                                     "Y") else self.market.outcomes.no.token_id
        response = self.client.place_market_order(token_id=selected_token_id, side=side, amount=amount)
        print(f"4、【下市价单】 response = {response}")

    async def 查询持仓验证(self):
        page = await self.client.list_positions(market=[self.market.condition_id]).first_page()
        print(f"5、【查询持仓验证】 page = {page}")

    def 关闭对象(self):
        self.client.close()


async def 显示菜单():
    pm = pm类()
    await pm.初始化()
    while (True):
        print("1. 下单 F3_市单")
        print("2. 查询持仓验证")
        print("0. 退出")
        choice = input("请输入你的输入：")
        if choice == "1":
            result = await pm.下单(标的代码="btc-updown-4h-{epoch}", amount=Decimal(1), outcome="no")
            print(
                f"下单结果 F3_市单 result = {result}，URL：https://polymarket.com/zh/event/{result.market.condition_id} 查看订单详情")
        elif choice == "2":
            result = await pm.查询持仓验证()
            print(f"查询持仓验证 result = {result}")
        elif choice == "0":
            break
        else:
            print("输入错误，请重新输入！")

        input(f"按任意键继续...")
    return True


if __name__ == "__main__":
    result = asyncio.run(显示菜单())
    print(f"显示菜单 result = {result}")
    pass