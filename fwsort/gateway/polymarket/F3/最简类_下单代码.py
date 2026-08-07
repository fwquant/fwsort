import asyncio
import json
import time
import traceback
from decimal import Decimal
from typing import Literal, TypeAlias

import httpx
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


def 获取周期(self, 标的代码: str) -> int:
    if '-5m-' in 标的代码.lower():
        return 5 * 60  # 5分钟 300 秒
    elif '-15m-' in 标的代码.lower():
        return 15 * 60  # 15分钟 900 秒
    elif '-4h-' in 标的代码.lower():
        return 4 * 60 * 60  # 4小时 14400 秒
    return 4 * 60 * 60  # 默认4小时 14400 秒


def 获得当前区间时间戳(标的代码: str = ""):
    if '-5m-' in 标的代码.lower():
        周期 = 5 * 60  # 5分钟 300 秒
    elif '-15m-' in 标的代码.lower():
        周期 = 15 * 60  # 15分钟 900 秒
    elif '-4h-' in 标的代码.lower():
        周期 = 4 * 60 * 60  # 4小时 14400 秒
    else:
        周期 = 1  # 当前时间戳 1秒周期对齐
    当前区间时间戳 = str(((int(time.time()) // 周期)) * (周期))
    return 当前区间时间戳, 周期


#
class pm类():

    def __init__(self, 标的代码: str = "btc-updown-4h-{epoch}"):
        self.market = None
        self.client = None
        self.标的代码 = 标的代码
        self._initialized = False
        self.当前区间时间戳 = None

    async def _ensure_initialized(self):
        if not self._initialized:
            await self.初始化()

    # 初始化
    async def 初始化(self):
        if self._initialized:
            return
        await self.connect()
        result = await self.获得市场(标的代码=self.标的代码)
        print(f"获得市场={result}")
        self._initialized = True

    # 连接
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
        self.当前区间时间戳, self._周期 = 获得当前区间时间戳(标的代码=标的代码)
        slug = 标的代码
        slug = slug.replace("{epoch}", self.当前区间时间戳)
        slug = slug.replace("{时间值}", self.当前区间时间戳)
        slug = slug.replace("{时间戳}", self.当前区间时间戳)

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
        current_slug = 标的代码.replace("{epoch}", self.当前区间时间戳)
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

    async def 获得_updown价格(self, 标的代码: str = "btc-updown-4h-{epoch}") -> dict:
        """获得当前市场 UP 和 DOWN 的最新价格(以 USDC 计价, 范围 0-1)"""
        await self._ensure_initialized()
        current_slug = 标的代码.replace("{epoch}", self.当前区间时间戳)
        if self.market is None or getattr(self.market, "slug", "") != current_slug:
            self.market = await self.获得市场(标的代码=标的代码)

        yes_token_id = self.market.outcomes.yes.token_id
        no_token_id = self.market.outcomes.no.token_id

        async def _fetch_price(token_id, label):
            try:
                midpoint = await _retry_call(self.client.get_midpoint, token_id=token_id)
                order_book = await _retry_call(self.client.get_order_book, token_id=token_id)
                best_bid = order_book.bids[0].price if order_book.bids else None
                best_ask = order_book.asks[0].price if order_book.asks else None
                last_trade = order_book.last_trade_price if order_book.last_trade_price else None
                return {
                    "label": label,
                    "token_id": token_id,
                    "midpoint": str(midpoint) if midpoint else None,
                    "best_bid": str(best_bid) if best_bid is not None else None,
                    "best_ask": str(best_ask) if best_ask is not None else None,
                    "last_trade_price": str(last_trade) if last_trade is not None else None,
                }
            except Exception as e:
                return {"label": label, "token_id": token_id, "error": str(e)}

        up_info, down_info = await asyncio.gather(
            _fetch_price(yes_token_id, "UP"),
            _fetch_price(no_token_id, "DOWN"),
        )

        print(
            f"【UP/DOWN 价格】 UP(mid={up_info.get('midpoint')}, bid={up_info.get('best_bid')}, ask={up_info.get('best_ask')}) | DOWN(mid={down_info.get('midpoint')}, bid={down_info.get('best_bid')}, ask={down_info.get('best_ask')})")
        return {"UP": up_info, "DOWN": down_info}

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

    async def 赎回(self, 标的代码: str | None = None) -> list:
        """赎回持仓。不传参数时赎回所有持仓，传参时赎回指定市场"""
        await self._ensure_initialized()
        results = []

        if 标的代码 is not None:
            m = await self.获得市场(标的代码=标的代码)
            print(f"【赎回】指定市场={m.slug if hasattr(m, 'slug') else 'N/A'}, condition_id={m.condition_id}")

            if not m.state.closed:
                print(f"【赎回】⚠️ 市场尚未结算 (closed={m.state.closed}), 赎回可能不生效")
            if m.state.accepting_orders:
                print(f"【赎回】⚠️ 市场仍接受下单, 通常应在市场结算后赎回")

            handle = await _retry_call(self.client.redeem_positions, condition_id=m.condition_id)
            result = await handle.wait()
            print(f"【赎回】结果 = {result}")
            results.append({"type": "REDEEM", "condition_id": m.condition_id, "result": str(result)})
        else:
            print(f"【赎回】扫描账号所有持仓...")
            paginator = self.client.list_positions()
            positions = []
            async for pos in paginator.iter_items():
                positions.append(pos)

            if not positions:
                print(f"【赎回】无持仓，无需赎回")
                return []

            print(f"【赎回】共找到 {len(positions)} 个持仓，开始逐个赎回...")
            redeemed_ids = set()
            for i, pos in enumerate(positions, 1):
                if pos.size is None or pos.size <= 0:
                    print(f"  [{i}] 跳过零持仓: outcome={pos.outcome}, size={pos.size}")
                    continue
                condition_id = pos.condition_id
                if condition_id in redeemed_ids:
                    continue
                redeemed_ids.add(condition_id)
                print(f"  [{i}] 赎回 condition_id={condition_id}, outcome={pos.outcome}, size={pos.size}")
                try:
                    handle = await _retry_call(self.client.redeem_positions, condition_id=condition_id)
                    result = await handle.wait()
                    print(f"  [{i}] 赎回结果 = {result}")
                    results.append({"type": "REDEEM", "condition_id": condition_id, "result": str(result)})
                except Exception as e:
                    print(f"  [{i}] 赎回失败:{e}，traceback: {traceback.format_exc()}")
                    results.append({"type": "FAILED", "condition_id": condition_id, "error": str(e)})

        return results

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
            print(
                f"  [{i}] {pos.title or pos.slug or '未知市场'} | outcome={pos.outcome} | size={pos.size} | cur_price={pos.cur_price} | value={pos.current_value}")

        results = []
        dust_positions = []
        for pos in positions:
            if pos.size is None or pos.size <= 0:
                print(f".0、【平仓】跳过零持仓: outcome={pos.outcome}, size={pos.size}")
                continue

            token_id = pos.token_id
            liq = await self.查询流动性(token_id)
            print(
                f".1、【流动性】 token={token_id[:12]}... | best_bid={liq.get('best_bid_price')}({liq.get('best_bid_size')}) | best_ask={liq.get('best_ask_price')}({liq.get('best_ask_size')}) | spread={liq.get('spread')} | mid={liq.get('midpoint')}")

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
                    print(f".X、【平仓】限价单也失败:{e}，traceback: {traceback.format_exc()}")
                    results.append({"type": "FAILED", "reason": str(e), "size": str(pos.size)})
                continue

            if 标的代码 is not None and not self.market.state.accepting_orders:
                print(f".2、【平仓】市场已结算，调用赎回函数...")
                redeem_results = await self.赎回(标的代码=标的代码)
                results.extend(redeem_results)
                break
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
                                results.append(
                                    {"type": "LIMIT_ORDER", "response": str(response), "price": str(limit_price)})
                            else:
                                print(f".3、【平仓】best_bid=0, 无法挂限价单")
                                results.append({"type": "FAILED", "reason": err_str, "size": str(pos.size)})
                        except Exception as e2:
                            print(f".4、【平仓】限价单也失败: {e2}")
                            results.append({"type": "FAILED", "reason": f"市价失败:{err_str} | 限价也失败:{e2}",
                                            "size": str(pos.size)})
                    else:
                        print(f".3、【平仓】市价单失败:{e}，traceback: {traceback.format_exc()}")
                        results.append({"type": "FAILED", "reason": err_str, "size": str(pos.size)})
        if dust_positions:
            total_dust_value = sum(d[2] for d in dust_positions)
            print(
                f"\n【平仓】⚠️ 共 {len(dust_positions)} 个灰尘持仓，总估值≈{total_dust_value} USDC (低于最低下单金额，可忽略)")
        return results

    async def 查看连接信息(self, 标的代码: str = "btc-updown-4h-{epoch}") -> dict:
        """查看当前连接状态、市场信息、盘口数据"""
        result = {
            "连接状态": {
                "已初始化": self._initialized,
                "客户端存在": self.client is not None,
                "市场存在": self.market is not None,
            }
        }

        if not self._initialized or self.client is None:
            print(f"【连接信息】 ❌ 未连接，请先初始化！")
            return result

        try:
            current_slug = 标的代码.replace("{epoch}", self.当前区间时间戳)
            if self.market is None or getattr(self.market, "slug", "") != current_slug:
                print(f"【连接信息】 市场已过期，重新获取...")
                self.market = await self.获得市场(标的代码=标的代码)

            m = self.market
            print(f"\n══════════════════════════════════════")
            print(f"【连接信息】 ✅ 已连接")
            print(f"══════════════════════════════════════")
            print(f"  客户端: {self.client}")
            print(f"  市场标题: {m.title if hasattr(m, 'title') else 'N/A'}")
            print(f"  市场Slug: {m.slug if hasattr(m, 'slug') else 'N/A'}")
            print(f"  市场ID: {m.id if hasattr(m, 'id') else 'N/A'}")
            print(f"  ConditionID: {m.condition_id if hasattr(m, 'condition_id') else 'N/A'}")
            print(f"  是否接受下单: {m.state.accepting_orders if hasattr(m.state, 'accepting_orders') else 'N/A'}")
            print(f"  是否已结算: {m.state.closed if hasattr(m.state, 'closed') else 'N/A'}")
            print(f"  结果数量: {len(m.outcomes) if hasattr(m, 'outcomes') else 'N/A'}")

            yes_token_id = m.outcomes.yes.token_id if hasattr(m.outcomes, 'yes') else None
            no_token_id = m.outcomes.no.token_id if hasattr(m.outcomes, 'no') else None
            print(f"  YES/UP TokenID: {yes_token_id[:20] if yes_token_id else 'N/A'}...")
            print(f"  NO/DOWN TokenID: {no_token_id[:20] if no_token_id else 'N/A'}...")

            result["市场信息"] = {
                "title": m.title if hasattr(m, 'title') else None,
                "slug": m.slug if hasattr(m, 'slug') else None,
                "condition_id": m.condition_id if hasattr(m, 'condition_id') else None,
                "accepting_orders": m.state.accepting_orders if hasattr(m.state, 'accepting_orders') else None,
                "closed": m.state.closed if hasattr(m.state, 'closed') else None,
            }

            print(f"\n【盘口数据】")

            async def _显示盘口(token_id, label):
                if not token_id:
                    return
                try:
                    order_book = await _retry_call(self.client.get_order_book, token_id=token_id)
                    midpoint = await _retry_call(self.client.get_midpoint, token_id=token_id)
                    bids = order_book.bids[:5] if order_book.bids else []
                    asks = order_book.asks[:5] if order_book.asks else []

                    print(f"  ── {label} ({token_id[:16]}...) ──")
                    print(f"    midpoint: {midpoint}")
                    print(f"    last_trade: {order_book.last_trade_price}")
                    print(f"    买盘 (前{len(bids)}档):")
                    for i, b in enumerate(reversed(bids), 1):
                        print(f"      {i}. price={b.price}, size={b.size}")
                    print(f"    卖盘 (前{len(asks)}档):")
                    for i, a in enumerate(asks, 1):
                        print(f"      {i}. price={a.price}, size={a.size}")
                    print(f"    min_order_size: {order_book.min_order_size}, tick_size: {order_book.tick_size}")
                    print()

                    return {
                        "midpoint": str(midpoint) if midpoint else None,
                        "last_trade": str(order_book.last_trade_price) if order_book.last_trade_price else None,
                        "best_bid": str(bids[-1].price) if bids else None,
                        "best_ask": str(asks[0].price) if asks else None,
                        "bids_count": len(order_book.bids),
                        "asks_count": len(order_book.asks),
                    }
                except Exception as e:
                    print(f"  ── {label} 查询失败:{e}，traceback: {traceback.format_exc()}")
                    return {"error": str(e)}

            up盘口, down盘口 = await asyncio.gather(
                _显示盘口(yes_token_id, "UP/YES"),
                _显示盘口(no_token_id, "DOWN/NO"),
            )
            result["盘口"] = {"UP": up盘口, "DOWN": down盘口}

            print(f"══════════════════════════════════════")
        except Exception as e:
            print(f"【连接信息】 ❌ 查询异常:{e}，traceback: {traceback.format_exc()}")
            result["错误"] = str(e)

        return result

    async def 查询持仓(self, 标的代码: str | None = None) -> list:
        """查询持仓。传参查指定市场，不传查全部"""
        await self._ensure_initialized()
        if 标的代码 is not None:
            m = await self.获得市场(标的代码=标的代码)
            paginator = self.client.list_positions(market=[m.condition_id])
            print(f"【查询持仓】指定市场={m.slug if hasattr(m, 'slug') else 'N/A'}")
        else:
            paginator = self.client.list_positions()
            print(f"【查询持仓】扫描账号全部持仓...")

        positions = []
        async for pos in paginator.iter_items():
            positions.append(pos)

        if not positions:
            print(f"【查询持仓】无持仓")
            return []

        print(f"【查询持仓】共 {len(positions)} 个持仓:")
        for i, pos in enumerate(positions, 1):
            title = pos.title or pos.slug or '未知市场'
            print(
                f"  [{i}] {title} | outcome={pos.outcome} | size={pos.size} | cur_price={pos.cur_price} | value={pos.current_value}")
        return positions

    async def 关闭对象(self):
        if self.client:
            await self.client.close()

    @staticmethod
    def _解析市场结算数据(m: dict) -> dict:
        """从 Gamma API 返回的 market dict 中解析结算结果。

        Gamma API 字段说明：
        - outcomes: JSON字符串, 如 '["UP","DOWN"]' 或 '["Yes","No"]'
        - outcomePrices: JSON字符串, 如 '["1.0","0.0"]' (已结算), '["0.65","0.35"]' (未结算)
        - result: 结算获胜方向, 如 "UP" / "Yes" (已结算才有)
        - closed: 是否已结算
        - umaResolutionStatus: "resolved" 表示已通过 UMA 预言机结算
        """
        outcomes_str = m.get("outcomes") or '["Yes","No"]'
        prices_str = m.get("outcomePrices") or '["0.5","0.5"]'
        result = m.get("result")

        try:
            outcomes = json.loads(outcomes_str)
        except (json.JSONDecodeError, TypeError):
            outcomes = ["Yes", "No"]

        try:
            prices = json.loads(prices_str)
        except (json.JSONDecodeError, TypeError):
            prices = [0.5, 0.5]

        if len(prices) < 2:
            prices = prices + ["0.5"] * (2 - len(prices))

        closed = m.get("closed", False)
        uma_status = m.get("umaResolutionStatus", "")

        parsed = {
            "closed": closed,
            "uma_resolved": uma_status == "resolved",
            "outcomes": outcomes,
            "prices": [float(prices[0]), float(prices[1])],
            "result_raw": result,
        }

        if closed or uma_status == "resolved":
            parsed["结算状态"] = "已结算"
            if result:
                parsed["获胜方向"] = result
            else:
                parsed["获胜方向"] = outcomes[0] if parsed["prices"][0] >= parsed["prices"][1] else outcomes[1]

            winning_idx = 0 if parsed["获胜方向"] == outcomes[0] else 1
            losing_idx = 1 - winning_idx
            parsed["获胜价格"] = str(prices[winning_idx])
            parsed["失败价格"] = str(prices[losing_idx])
            parsed["摘要"] = (
                f"已结算，{parsed['获胜方向']} 获胜 (价格≈{parsed['获胜价格']})，"
                f"{outcomes[losing_idx]} 失败 (价格≈{parsed['失败价格']})"
            )
        else:
            parsed["结算状态"] = "未结算"
            parsed["获胜方向"] = None
            parsed["获胜价格"] = None
            parsed["失败价格"] = None
            parsed["摘要"] = f"未结算，当前 {outcomes[0]}≈{prices[0]}, {outcomes[1]}≈{prices[1]}"

        return parsed

    @staticmethod
    def _从标的代码提取事件slug(标的代码: str) -> str:
        """从标的代码模板提取事件 slug，如 'btc-updown-15m-{epoch}' → 'btc-updown-15m'"""
        return 标的代码.replace("-{epoch}", "")

    async def 查询结算方向历史(self, 标的代码: str = "btc-updown-15m-{epoch}", 数量: int = 20) -> list:
        """查询历史结算记录（公开数据 Gamma API）。

        实现说明（已修复 2026-08-06）：
        - Polymarket 的 Gamma API 中，每个 epoch 对应一个独立的 event（slug 形如 btc-updown-15m-1786024800）
        - 原实现用前缀 slug（如 btc-updown-15m）查询 /events，会返回空列表
        - 修复：对每个目标 epoch 单独构造完整 slug，分别请求 /events?slug=<完整slug>
        - 每个 event 只包含一个 market，直接取 markets[0] 解析结算数据
        """
        当前区间时间戳, 周期 = 获得当前区间时间戳(标的代码=标的代码)

        # 构造目标 epoch 列表（从新到旧）
        target_epochs = [int(当前区间时间戳) - i * 周期 for i in range(数量)]

        results = []
        not_found_slugs = []
        async with httpx.AsyncClient(timeout=15) as client:
            for epoch in target_epochs:
                slug = 标的代码.replace("{epoch}", str(epoch))
                try:
                    resp = await client.get(
                        "https://gamma-api.polymarket.com/events",
                        params={"slug": slug},
                    )
                    data = resp.json()
                    events_list = (
                        data.get("events", data.get("data", []))
                        if isinstance(data, dict)
                        else data
                    )
                    if not events_list:
                        not_found_slugs.append(slug)
                        continue

                    event = events_list[0]
                    all_markets = event.get("markets", [])
                    if not all_markets:
                        continue

                    # 一个 event 只有一个 market
                    m = all_markets[0]
                    parsed = self._解析市场结算数据(m)
                    row = {
                        "slug": slug,
                        "epoch": epoch,
                        "title": m.get("question") or m.get("title"),
                        "outcomes": parsed["outcomes"],
                        "prices": parsed["prices"],
                        "结算状态": parsed["结算状态"],
                    }
                    if parsed["获胜方向"]:
                        row["获胜方向"] = parsed["获胜方向"]
                        row["获胜价格"] = parsed["获胜价格"]
                        row["失败价格"] = parsed["失败价格"]
                    row["摘要"] = f"epoch={epoch} {parsed['摘要']}"
                    results.append(row)
                except Exception as e:
                    print(f"【结算历史】epoch={epoch} 查询异常:{e}，traceback: {traceback.format_exc()}")

        results.sort(key=lambda x: x["epoch"], reverse=True)

        if not results:
            item = {"事件slug": 标的代码, "结算状态": "无匹配市场",
                    "摘要": f"查询 {len(target_epochs)} 个 epoch 均未命中"}
            return [item]

        已结算 = sum(1 for r in results if r.get("结算状态") == "已结算")
        未结算 = sum(1 for r in results if r.get("结算状态") == "未结算")
        未找到 = len(not_found_slugs)
        print(
            f"【结算历史】共 {len(results)} 条 | 已结算={已结算} | 未结算={未结算}"
            + (f" | 未开放={未找到}" if 未找到 else "")
        )
        return results

    async def 本人持仓结算方向(self, 数量: int = 10) -> list:
        """查询本人持仓对应市场的结算方向（最近N个）。
        按事件分组批量请求，一次事件请求拿该事件下所有市场。
        """
        await self._ensure_initialized()

        paginator = self.client.list_positions()
        positions = []
        async for pos in paginator.iter_items():
            positions.append(pos)

        if not positions:
            print("【本人持仓结算方向】无持仓")
            return []

        market_map = {}
        for pos in positions:
            slug = getattr(pos, 'slug', None)
            if slug and slug not in market_map:
                market_map[slug] = pos

        recent_items = list(market_map.items())[-数量:]

        event_slugs_needed = set()
        for slug, _ in recent_items:
            try:
                prefix = slug.rsplit("-", 1)[0]
                event_slugs_needed.add(prefix)
            except (ValueError, IndexError):
                pass

        market_data_map = {}
        async with httpx.AsyncClient(timeout=15) as client:
            # 先尝试用前缀（如 btc-updown-15m）查询，若无结果再用完整 slug
            for event_slug in event_slugs_needed:
                found = False
                # 1) 尝试按前缀查 events（可能返回空，因为每个 epoch 是独立 event）
                try:
                    resp = await client.get(
                        "https://gamma-api.polymarket.com/events",
                        params={"slug": event_slug},
                    )
                    data = resp.json()
                    events_list = data.get("events", data.get("data", [])) if isinstance(data, dict) else data
                    if events_list:
                        for m in events_list[0].get("markets", []):
                            mslug = m.get("slug", "")
                            if mslug:
                                market_data_map[mslug] = self._解析市场结算数据(m)
                                found = True
                except Exception:
                    pass

                # 2) 若前缀查询为空（正常情况），用完整 slug 逐个查
                if not found:
                    # 遍历持仓中属于此前缀的 slug，用完整 slug 单独查询
                    for slug, pos in recent_items:
                        try:
                            prefix = slug.rsplit("-", 1)[0]
                        except (ValueError, IndexError):
                            continue
                        if prefix != event_slug:
                            continue
                        try:
                            resp = await client.get(
                                "https://gamma-api.polymarket.com/events",
                                params={"slug": slug},
                            )
                            data = resp.json()
                            events_list = data.get("events", data.get("data", [])) if isinstance(data, dict) else data
                            if events_list:
                                for m in events_list[0].get("markets", []):
                                    mslug = m.get("slug", "")
                                    if mslug:
                                        market_data_map[mslug] = self._解析市场结算数据(m)
                        except Exception:
                            pass

        results = []
        for slug, pos in recent_items:
            item = {
                "slug": slug,
                "outcome": pos.outcome,
                "size": str(pos.size),
                "cur_price": str(pos.cur_price) if pos.cur_price else None,
                "title": pos.title,
            }

            parsed = market_data_map.get(slug)
            if not parsed:
                item["结算状态"] = "市场不存在"
                item["我方胜负"] = "未知"
                item["摘要"] = f"{slug} 未在事件数据中找到"
                results.append(item)
                continue

            item["结算状态"] = parsed["结算状态"]
            if parsed["获胜方向"]:
                item["获胜方向"] = parsed["获胜方向"]
                item["获胜价格"] = parsed["获胜价格"]
                item["我方胜负"] = "胜利" if pos.outcome.upper() == parsed["获胜方向"].upper() else "失败"
                item["摘要"] = (
                    f"{slug} | 我方={pos.outcome} | 获胜方={parsed['获胜方向']}"
                    f"({parsed['获胜价格']}) | {item['我方胜负']}"
                )
            else:
                item["我方胜负"] = "待定"
                item["摘要"] = f"{slug} | 我方={pos.outcome} | {parsed['摘要']}"

            results.append(item)

        print(f"【本人持仓结算方向】共 {len(results)} 条记录")
        return results


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
        print("2.3. 赎回  btc-updown-4h (指定市场)")
        print("2.4. 一键赎回 (所有持仓)")
        print("--------------------------------------")

        # 查询
        print("3.1. 查询持仓 (指定市场)")
        print("3.2. 查询持仓 (全部)")
        print("3.3. 查询 UP/DOWN 最新价格")
        print("3.4. 查看连接信息 & 盘口")
        print("3.5. 查询结算方向历史 (公开数据)")
        print("3.6. 本人持仓结算方向")
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
        elif choice == "2.3":
            result = await pm.赎回(标的代码="btc-updown-4h-{epoch}")
            print(f"指定市场赎回结果 result = {result}")
        elif choice == "2.4":
            result = await pm.赎回()
            print(f"一键赎回所有持仓结果 result = {result}")

        # 查询
        elif choice == "3.1":
            result = await pm.查询持仓(标的代码="btc-updown-4h-{epoch}")
            print(f"指定市场持仓 result = {len(result)} 条")
        elif choice == "3.2":
            result = await pm.查询持仓()
            print(f"全部持仓 result = {len(result)} 条")
        elif choice == "3.3":
            result = await pm.获得_updown价格(标的代码="btc-updown-4h-{epoch}")
            print(f"UP/DOWN 价格 result = {result}")
        elif choice == "3.4":
            result = await pm.查看连接信息(标的代码="btc-updown-4h-{epoch}")
            print(f"连接信息 result = {result}")
        elif choice == "3.5":
            result = await pm.查询结算方向历史(标的代码="btc-updown-15m-{epoch}", 数量=5, )
            print(f"结算历史 result = {result}")
        elif choice == "3.6":
            result = await pm.本人持仓结算方向(数量=3)
            print(f"本人持仓结算方向 result = {result} 条")

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