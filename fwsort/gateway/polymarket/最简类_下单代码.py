import asyncio
import os
import time
from decimal import Decimal

from celery.bin.result import result
from dotenv import load_dotenv
from eth_abi.utils.padding import fpad32
from polymarket import AsyncSecureClient, ApiKeyCreds, OrderSide
from polymarket.auth import RelayerApiKey
from prompt_toolkit.key_binding.bindings.named_commands import self_insert

load_dotenv()


class F3_市单类:
    def __init__(self, 标的代码: str = "btc-updown-5m-{epoch}", side: OrderSide = "BUY", amount: Decimal = 1):
        self._relayer_api_key = None
        self._client:AsyncSecureClient = None
        self._market = None

        self.标的代码 = 标的代码
        self.side = side
        self.amount = amount

        self.init()

    async def init(self):
        # 这三个值 当前在 .env，获取步骤：POLYMARKET 首页 登陆，右上头像,设置，Relayer API 密钥，新建。
        private_key = os.environ.get("POLYMARKET_PRIVATE_KEY")
        relayer_key = os.environ.get("POLYMARKET_RELAYER_API_KEY")
        relayer_addr = os.environ.get("POLYMARKET_RELAYER_API_KEY_ADDRESS")

        # 构建RelayerApiKey对象（SDK 要求 key + address 两项）
        relayer_api_key = RelayerApiKey(key=relayer_key, address=relayer_addr, )
        print(f"1、 构建RelayerApiKey对象,  relayer_api_key = {relayer_api_key}")

        # 创建客户端（F3 模式：私钥 + api_key=RelayerApiKey，免 Gas 下单）
        client = await AsyncSecureClient.create(private_key=private_key, api_key=relayer_api_key, )
        print(f"2、连接钱包(Relayer Gasless), client = {client}")
        self._client = client
        self._relayer_api_key = relayer_api_key
        return True

    async def setmarket(self, 标的代码):
        # 获取当前 标的代码对应的盘口 涨跌市场
        self._market = await self._client.get_market(slug=标的代码)  # 标的代码对应的盘口
        print(f"3、获取市场 slug={标的代码}，market={self._market}")
        return self._market

    async def 下市价单(self, 标的代码: str = "btc-updown-5m-{epoch}", side: OrderSide = "BUY", amount: Decimal = 1):
        # 下市价单（$1 USDC 买 YES/UP）
        response = await self._client.place_market_order(token_id=self._market.outcomes.yes.token_id, side=side,
                                                         amount=amount)
        print(f"4、下市价单 response = {response}")


def 获得当前_时间值(间隔秒: 300 | 1440 = 300):
    now = int(time.time())
    epoch = now - (now % 间隔秒) + 间隔秒
    return epoch




if __name__ == "__main__":
    print("\n选择了: F3 - Relayer Gasless\n")
    f3=F3_市单类()
    result=f3.下市价单()

    pass
