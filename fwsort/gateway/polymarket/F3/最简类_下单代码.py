import asyncio
import time
import traceback
from decimal import Decimal
from typing import Literal, TypeAlias

from dotenv import load_dotenv
from polymarket import AsyncSecureClient, OrderSide
from polymarket.auth import RelayerApiKey

from fwsort.config import settings
from fwsort.gateway import BaseGateway

load_dotenv()

_MAX_RETRIES = 3
_RETRY_DELAY = 2


async def _retry_call(func, *args, **kwargs):
    last_err = None
    for attempt in range(_MAX_RETRIES):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_err = e
            if attempt < _MAX_RETRIES - 1:
                print(f"  [RETRY] attempt {attempt + 1} failed: {e}, retrying in {_RETRY_DELAY}s...")
                await asyncio.sleep(_RETRY_DELAY * (attempt + 1))
            else:
                raise last_err


def 获得当前时间值(周期: int = 4 * 60 * 60):
    result = str(((int(time.time()) // 周期)) * (周期))
    return result


class pm类():

    def __init__(self, 标的代码: str = "btc-updown-4h-{epoch}"):
        self.market = None
        self.client = None
        self.标的代码 = 标的代码
        self._initialized = False

    async def _ensure_initialized(self):
        if not self._initialized:
            await self.初始化()

    async def 初始化(self):
        if self._initialized:
            return
        await self.connect()
        result = await self.获得市场(标的代码=self.标的代码)
        print(f"获得市场={result}")
        self._initialized = True

    async def connect(self):
        POLYMARKET_RELAYER_API_KEY_ADDRESS = settings.POLYMARKET_RELAYER_API_KEY_ADDRESS
        POLYMARKET_RELAYER_PRIVATE_KEY = settings.POLYMARKET_RELAYER_PRIVATE_KEY
        POLYMARKET_RELAYER_API_KEY = settings.POLYMARKET_RELAYER_API_KEY
        print(f"\n【准备配置】完毕！")

        relayer_api_key = RelayerApiKey(key=POLYMARKET_RELAYER_API_KEY, address=POLYMARKET_RELAYER_API_KEY_ADDRESS, )
        print(f"1、 【构建RelayerApiKey】,  relayer_api_key = {relayer_api_key}")

        client = await _retry_call(
            AsyncSecureClient.create,
            private_key=POLYMARKET_RELAYER_PRIVATE_KEY,
            api_key=relayer_api_key,
        )
        print(f"2、【创建客户端】(Relayer Gasless), client = {client}")
        self.client = client

    async def 获得市场(self, 标的代码: str = "btc-updown-4h-{epoch}"):
        周期 = self._get周期(标的代码)
        slug = 标的代码.replace("{epoch}", 获得当前时间值(周期=周期))
        market = await _retry_call(self.client.get_market, slug=slug)
        print(f"3、【获取市场】 ，market={market}")
        self.market = market
        return self.market

    async def 下单(self, 标的代码: str = "btc-updown-4h-{epoch}"
                   , outcome: Literal["UP", "DOWN", "YES", "NO", "Y", "N", "U", "D"] = "UP"
                   , amount: Decimal = Decimal(1)
                   , shares: Decimal = Decimal(1)
                   , side: OrderSide = "BUY"):
        await self._ensure_initialized()
        current_slug = 标的代码.replace("{epoch}", 获得当前时间值(周期=self._get周期(标的代码)))
        if self.market is None or getattr(self.market, "slug", "") != current_slug:
            self.market = await self.获得市场(标的代码=标的代码)

        if not self.market.state.accepting_orders:
            print(f"4、【下市价单】市场已结算(closed={self.market.state.closed})，无法下单，请使用赎回")
            return None

        if outcome.upper() in ("UP", "YES", "Y", "U"):
            selected_token_id = self.market.outcomes.yes.token_id
        else:
            selected_token_id = self.market.outcomes.no.token_id
        if side == "BUY":
            response = await _retry_call(
                self.client.place_market_order,
                token_id=selected_token_id, side=side, amount=amount,
            )
        else:
            response = await _retry_call(
                self.client.place_market_order,
                token_id=selected_token_id, side=side, shares=shares,
            )
        print(f"4、【下市价单】 side={side}, response = {response}")
        return response

    def _get周期(self, 标的代码: str) -> int:
        if '-5m-' in 标的代码.lower():
            return 5 * 60
        elif '-15m-' in 标的代码.lower():
            return 15 * 60
        elif '-4h-' in 标的代码.lower():
            return 4 * 60 * 60
        return 4 * 60 * 60

    async def 查询流动性(self, token_id: str) -> dict:
        """查询指定 token 的盘口流动性"""
        try:
            order_book = await _retry_call(self.client.get_order_book, token_id=token_id)
            best_bid = order_book.bids[0] if order_book.bids else None
            best_ask = order_book.asks[0] if order_book.asks else None
            spread = None
            if best_bid and best_ask:
                spread = best_ask.price - best_bid.price
            midpoint = await _retry_call(self.client.get_midpoint, token_id=token_id)
            return {
                "token_id": token_id,
                "best_bid_price": str(best_bid.price) if best_bid else None,
                "best_bid_size": str(best_bid.size) if best_bid else None,
                "best_ask_price": str(best_ask.price) if best_ask else None,
                "best_ask_size": str(best_ask.size) if best_ask else None,
                "spread": str(spread) if spread is not None else None,
                "midpoint": str(midpoint) if midpoint else None,
                "last_trade_price": str(order_book.last_trade_price) if order_book.last_trade_price else None,
                "min_order_size": str(order_book.min_order_size),
                "tick_size": str(order_book.tick_size),
                "bids_count": len(order_book.bids),
                "asks_count": len(order_book.asks),
                "has_bid_liquidity": best_bid is not None and best_bid.size > 0,
            }
        except Exception as e:
            return {"token_id": token_id, "error": str(e)}

    async def 平仓(self, 标的代码: str | None = "btc-updown-4h-{epoch}"):
        await self._ensure_initialized()
        if 标的代码 is not None:
            self.market = await self.获得市场(标的代码=标的代码)
            paginator = self.client.list_positions(market=[self.market.condition_id])
        else:
            paginator = self.client.list_positions()
        positions = []
        async for pos in paginator.iter_items():
            positions.append(pos)
        if not positions:
            print(f"【平仓】{'当前市场' if 标的代码 else '账号'}无持仓，无需平仓")
            return None
        print(f"【平仓】共找到 {len(positions)} 个持仓:")
        for i, pos in enumerate(positions, 1):
            print(f"  [{i}] {pos.title or pos.slug or '未知市场'} | outcome={pos.outcome} | size={pos.size} | cur_price={pos.cur_price} | value={pos.current_value}")

        results = []
        dust_positions = []
        for pos in positions:
            if pos.size is None or pos.size <= 0:
                print(f".0、【平仓】跳过零持仓: outcome={pos.outcome}, size={pos.size}")
                continue

            token_id = pos.token_id
            liq = await self.查询流动性(token_id)
            print(f".1、【流动性】 token={token_id[:12]}... | best_bid={liq.get('best_bid_price')}({liq.get('best_bid_size')}) | best_ask={liq.get('best_ask_price')}({liq.get('best_ask_size')}) | spread={liq.get('spread')} | mid={liq.get('midpoint')}")

            if not liq.get("has_bid_liquidity"):
                msg = f"⚠️ 无买盘流动性！best_bid={liq.get('best_bid_price')}, 尝试限价单..."
                print(f".X、【平仓】{msg}")
                try:
                    midpoint = Decimal(str(liq.get('midpoint') or '0'))
                    if midpoint > 0:
                        limit_price = midpoint
                        print(f".X、【平仓】挂限价单 price={limit_price}, size={pos.size}")
                        response = await _retry_call(
                            self.client.place_limit_order,
                            token_id=token_id, price=limit_price, size=pos.size, side="SELL",
                        )
                        print(f".X、【平仓】限价单结果 response = {response}")
                        results.append({"type": "LIMIT_ORDER", "response": str(response), "price": str(limit_price)})
                    else:
                        print(f".X、【平仓】无法确定限价，跳过此持仓")
                        results.append({"type": "SKIPPED", "reason": "无流动性且无法确定限价", "size": str(pos.size)})
                except Exception as e:
                    print(f".X、【平仓】限价单也失败: {e}")
                    results.append({"type": "FAILED", "reason": str(e), "size": str(pos.size)})
                continue

            if 标的代码 is not None and not self.market.state.accepting_orders:
                print(f".2、【平仓】市场已结算，赎回 outcome={pos.outcome}, token_id={token_id}, shares={pos.size}")
                handle = await _retry_call(self.client.redeem_positions, condition_id=self.market.condition_id)
                result = await handle.wait()
                print(f".3、【平仓】赎回结果 result = {result}")
                results.append({"type": "REDEEM", "result": str(result)})
            else:
                print(f".2、【平仓】市价卖出 outcome={pos.outcome}, shares={pos.size}")
                try:
                    response = await _retry_call(
                        self.client.place_market_order,
                        token_id=token_id, side="SELL", shares=pos.size)
                    print(f".3、【平仓】结果 response = {response}")
                    results.append({"type": "MARKET", "response": str(response)})
                    if hasattr(response, 'making_amount') and response.making_amount is not None:
                        sold = response.making_amount
                        dust = pos.size - sold
                        if dust > 0:
                            dust_value = dust * (pos.cur_price or Decimal('0'))
                            dust_positions.append((pos, dust, dust_value))
                            print(f".4、【平仓】⚠️ 灰尘持仓: 实卖={sold}, 剩余={dust}, 估值≈{dust_value} USDC")
                except Exception as e:
                    err_str = str(e)
                    if "No resting liquidity" in err_str or "no resting" in err_str.lower():
                        best_bid_price = liq.get('best_bid_price')
                        print(f".3、【平仓】⚠️ 市价单无对手盘，尝试限价单 @ best_bid={best_bid_price}")
                        try:
                            bb = Decimal(str(best_bid_price)) if best_bid_price else Decimal('0')
                            if bb > 0:
                                limit_price = bb
                                response = await _retry_call(
                                    self.client.place_limit_order,
                                    token_id=token_id, price=limit_price, size=pos.size, side="SELL",
                                )
                                print(f".4、【平仓】限价单结果 = {response}")
                                results.append({"type": "LIMIT_ORDER", "response": str(response), "price": str(limit_price)})
                            else:
                                print(f".3、【平仓】best_bid=0, 无法挂限价单")
                                results.append({"type": "FAILED", "reason": err_str, "size": str(pos.size)})
                        except Exception as e2:
                            print(f".4、【平仓】限价单也失败: {e2}")
                            results.append({"type": "FAILED", "reason": f"市价失败:{err_str} | 限价也失败:{e2}", "size": str(pos.size)})
                    else:
                        print(f".3、【平仓】市价单失败: {e}")
                        results.append({"type": "FAILED", "reason": err_str, "size": str(pos.size)})
        if dust_positions:
            total_dust_value = sum(d[2] for d in dust_positions)
            print(f"\n【平仓】⚠️ 共 {len(dust_positions)} 个灰尘持仓，总估值≈{total_dust_value} USDC (低于最低下单金额，可忽略)")
        return results

    async def 查询持仓验证(self):
        await self._ensure_initialized()
        page = await self.client.list_positions(market=[self.market.condition_id]).first_page()
        print(f"5、【查询持仓验证】 page = {page}")

    def 关闭对象(self):
        self.client.close()


async def 显示菜单():
    pm = pm类()
    await pm.初始化()
    while (True):
        # 初始化
        print("=====================================")
        print("\n0.1. 初始化")
        print("--------------------------------------")

        # 下单
        print("1.1. 下单 F3_市单 btc5分钟 1U买UP")
        print("1.2. 下单 F3_市单 btc5分钟 1U买DOWN")
        print("1.3. 下单 F3_市单 btc15分钟 1U买UP")
        print("1.4. 下单 F3_市单 btc15分钟 1U买DOWN")
        print("1.5. 下单 F3_市单 btc4h分钟 1U买UP")
        print("1.6. 下单 F3_市单 btc4h分钟 1U买DOWN")
        print("--------------------------------------")

        # 平仓
        print("2.1. 平仓  btc-updown-4h(SELL 当前市场全部持仓)")
        print("2.2. 一键平仓 (SELL 账号所有持仓)")
        print("--------------------------------------")

        # 查询
        print("3.1. 查询持仓验证")
        print("--------------------------------------")

        # 退出
        print("886. 退出")
        print("=====================================")

        # 输入
        choice = input("请输入你的输入：")

        if choice == "0.1":
            await pm.初始化()
            print("初始化完成")
        # 下单
        elif choice == "1.1":
            result = await pm.下单(标的代码="btc-updown-5m-{epoch}", amount=Decimal(1), outcome="up")
            print(
                f"下单结果 F3_市单 btc5分钟 1U买UP, "
                f"result = {result}，URL：https://polymarket.com/zh/event/{pm.market.condition_id} 查看订单详情")
        elif choice == "1.2":
            result = await pm.下单(标的代码="btc-updown-5m-{epoch}", amount=Decimal(1), outcome="down")
            print(
                f"下单结果 F3_市单 btc5分钟 1U买DOWN, "
                f"result = {result}，URL：https://polymarket.com/zh/event/{pm.market.condition_id} 查看订单详情")
        elif choice == "1.3":
            result = await pm.下单(标的代码="btc-updown-15m-{epoch}", amount=Decimal(1), outcome="up")
            print(
                f"下单结果 F3_市单 btc15分钟 1U买UP, "
                f"result = {result}，URL：https://polymarket.com/zh/event/{pm.market.condition_id} 查看订单详情")
        elif choice == "1.4":
            result = await pm.下单(标的代码="btc-updown-15m-{epoch}", amount=Decimal(1), outcome="down")
            print(
                f"下单结果 F3_市单 btc15分钟 1U买DOWN, "
                f"result = {result}，URL：https://polymarket.com/zh/event/{pm.market.condition_id} 查看订单详情")
        elif choice == "1.5":
            result = await pm.下单(标的代码="btc-updown-4h-{epoch}", amount=Decimal(1), outcome="up")
            print(
                f"下单结果 F3_市单 btc4h分钟 1U买UP, "
                f"result = {result}，URL：https://polymarket.com/zh/event/{pm.market.condition_id} 查看订单详情")
        elif choice == "1.6":
            result = await pm.下单(标的代码="btc-updown-4h-{epoch}", amount=Decimal(1), outcome="down")
            print(
                f"下单结果 F3_市单 btc4h分钟 1U买DOWN, "
                f"result = {result}，URL：https://polymarket.com/zh/event/{pm.market.condition_id} 查看订单详情")


        # 平仓
        elif choice == "2.1":
            result = await pm.平仓(标的代码="btc-updown-4h-{epoch}")
            print(f"平仓结果 result = {result}")

        elif choice == "2.2":
            result = await pm.平仓(标的代码=None)
            print(f"一键平仓结果 result = {result}")

        # 查询
        elif choice == "3.1":
            result = await pm.查询持仓验证()
            print(f"查询持仓验证 result = {result}")

        # 退出
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