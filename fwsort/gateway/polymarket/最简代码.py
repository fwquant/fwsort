import asyncio
import os
import time
import traceback
from decimal import Decimal
from typing import Literal, TypeAlias

from dotenv import load_dotenv
from polymarket import AsyncSecureClient, OrderSide
from polymarket.auth import RelayerApiKey

load_dotenv()


def 获得当前时间值(周期: int = 4 * 60 * 60):
    result = str(((int(time.time()) // 周期)) * (周期))
    return result


# F3认证: Relayer Gasless (免Gas费)，无需持有POL  标的代码就是 最后斜线后面的值 ，比如： https://polymarket.com/zh/event/btc-updown-4h-1785456000  值 为：btc-updown-4h-1785456000
async def F3_市单(标的代码: str = "btc-updown-4h-{epoch}", amount: Decimal = 1,
                  outcome: Literal["YES", "NO", "Y", "N"] = "YES", side: OrderSide = "BUY"):
    try:
        POLYMARKET_RELAYER_API_KEY_ADDRESS = os.environ.get("POLYMARKET_RELAYER_API_KEY_ADDRESS")
        POLYMARKET_RELAYER_PRIVATE_KEY = os.environ.get("POLYMARKET_RELAYER_PRIVATE_KEY")
        POLYMARKET_RELAYER_API_KEY = os.environ.get("POLYMARKET_RELAYER_API_KEY")
        print(f"\n【准备配置】完毕！")

        # 构建RelayerApiKey对象（SDK 要求 key + address 两项）
        relayer_api_key = RelayerApiKey(key=POLYMARKET_RELAYER_API_KEY, address=POLYMARKET_RELAYER_API_KEY_ADDRESS, )
        print(f"1、 【构建RelayerApiKey】,  relayer_api_key = {relayer_api_key}")

        # 创建客户端（F3 模式：私钥 + api_key=RelayerApiKey，免 Gas 下单）
        client = await AsyncSecureClient.create(private_key=POLYMARKET_RELAYER_PRIVATE_KEY, api_key=relayer_api_key, )
        print(f"2、【创建客户端】(Relayer Gasless), client = {client}")

        # 获取当前 标的代码对应的盘口 涨跌市场   btc-updown-4h-1785470400
        market = await client.get_market(
            slug=标的代码.replace("{epoch}", 获得当前时间值(周期=4 * 60 * 60)))  # 标的代码对应的盘口
        print(f"3、【获取市场】 ，market={market}")

        # 下市价单（$1 USDC 买 YES/UP 或 NO/DOWN）
        selected_token_id = market.outcomes.yes.token_id if outcome.upper() in ("YES",
                                                                                "Y") else market.outcomes.no.token_id
        response = await client.place_market_order(token_id=selected_token_id, side=side, amount=amount)
        print(f"4、【下市价单】 response = {response}")

        # 查询持仓验证
        page = await client.list_positions(market=[market.condition_id]).first_page()
        print(f"5、【查询持仓验证】 page = {page}")

        await client.close()
        print(f"6、【关闭对象】，清空实例！")
        return {"success": True, "market": market, "response": response, "page": page}
    except Exception as e:
        return {"success": False, "error": f"下单失败：{str(e)},traceback={traceback.format_exc()}"}


if __name__ == "__main__":
    result = asyncio.run(F3_市单(标的代码="btc-updown-4h-{epoch}", amount=Decimal(1), outcome="YES"))
    print(
        f"下单结果 result = {result}，你可以在浏览器打开URL：https://polymarket.com/zh/event/{result.market.condition_id} 查看订单详情")
    pass
