# Polymarket 网关路由：连接/余额/持仓/挂单 + BTC 5min 市场查询与下单
# 所有接口需要 admin 鉴权（避免泄露钱包信息或误下单）
from fastapi import APIRouter, Depends
from loguru import logger

from fwsort.config import settings
from fwsort.gateway.polymarket_client import PolymarketClient
from fwsort.models import User
from fwsort.response import fail, success
from router.auth_router import current_user

router = APIRouter()

# 全局单例（避免每次请求都重建 httpx 客户端 + L2 key）
_client: PolymarketClient | None = None


def get_polymarket_client() -> PolymarketClient:
    """获取 Polymarket 客户端单例"""
    global _client
    if _client is None:
        _client = PolymarketClient()
    return _client


async def require_admin(user: User = Depends(current_user)) -> User:
    """管理员鉴权（下单/查余额等敏感操作必须 admin）"""
    if user.role < 3:
        from fwsort.exceptions import PermissionError_

        raise PermissionError_("admin required for polymarket gateway")
    return user


def _ensure_configured(client: PolymarketClient) -> dict | None:
    """检查密钥是否配置；未配置返回失败响应（路由直接 return）"""
    if not client.is_configured():
        missing = settings.polymarket_missing_keys
        return fail(
            f"Polymarket 网关未配置密钥，无法执行该操作。请在 .env 填入: {', '.join(missing)}",
            code=400,
            data={"missing_keys": missing},
        )
    return None


# ========== 1. 网关连接状态 ==========
@router.get("/status", response_model=dict)
async def gateway_status(_: User = Depends(require_admin)) -> dict:
    """查询 Polymarket 网关连接状态（不发起真实请求，仅本地配置检查）"""
    client = get_polymarket_client()
    return success({
        "configured": client.is_configured(),
        "missing_keys": settings.polymarket_missing_keys,
        "host": client.host,
        "wallet_address": client.wallet_address or "(未配置)",
        "chain": settings.POLYMARKET_CHAIN,
        "trade_mode": settings.TRADE_MODE,
        "btc5m_enabled": settings.POLYMARKET_BTC5M_ENABLED,
        "btc5m_auto_order": settings.POLYMARKET_BTC5M_AUTO_ORDER,
        "btc5m_effective": settings.btc5m_enabled_effective,
    }, message="polymarket gateway status")


@router.get("/ping", response_model=dict)
async def gateway_ping(_: User = Depends(require_admin)) -> dict:
    """真实探测 Polymarket 网关连通性（GET /markets 公开端点）"""
    client = get_polymarket_client()
    bad = _ensure_configured(client)
    if bad is not None:
        return bad
    try:
        resp = await client.ping()
        return success({"reachable": True, "sample": str(resp)[:200]}, message="ping ok")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[POLY] ping failed: {e}")
        return fail(f"网关连通失败: {e}", code=502)


# ========== 2. 查询用户余额 ==========
@router.get("/balance", response_model=dict)
async def gateway_balance(_: User = Depends(require_admin)) -> dict:
    """查询钱包 USDC 抵押品余额"""
    client = get_polymarket_client()
    bad = _ensure_configured(client)
    if bad is not None:
        return bad
    try:
        resp = await client.get_balance()
        # 标准化余额字段（兼容不同响应格式）
        balance = None
        if isinstance(resp, dict):
            balance = resp.get("balance") or resp.get("collateral")
        return success({
            "wallet_address": client.wallet_address,
            "raw": resp,
            "balance": balance,
        }, message="balance fetched")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[POLY] get_balance failed: {e}")
        return fail(f"查询余额失败: {e}", code=502)


# ========== 3. 查询持仓 + 当前挂单 ==========
@router.get("/positions", response_model=dict)
async def gateway_positions(
    market: str | None = None,
    size_greater_than: float = 0.0,
    _: User = Depends(require_admin),
) -> dict:
    """查询钱包持仓（可选按 market 过滤）"""
    client = get_polymarket_client()
    bad = _ensure_configured(client)
    if bad is not None:
        return bad
    try:
        resp = await client.get_positions(market=market, size_greater_than=size_greater_than)
        return success({"raw": resp}, message="positions fetched")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[POLY] get_positions failed: {e}")
        return fail(f"查询持仓失败: {e}", code=502)


@router.get("/orders", response_model=dict)
async def gateway_open_orders(
    market: str | None = None,
    _: User = Depends(require_admin),
) -> dict:
    """查询当前挂单"""
    client = get_polymarket_client()
    bad = _ensure_configured(client)
    if bad is not None:
        return bad
    try:
        resp = await client.get_open_orders(market=market)
        return success({"raw": resp}, message="open orders fetched")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[POLY] get_open_orders failed: {e}")
        return fail(f"查询挂单失败: {e}", code=502)


# ========== 4. BTC 5min 市场：查询活跃市场 ==========
@router.get("/btc5m/market", response_model=dict)
async def btc5m_active_market(
    slug_prefix: str | None = None,
    _: User = Depends(require_admin),
) -> dict:
    """查询当前活跃的 BTC 5min 涨跌市场"""
    client = get_polymarket_client()
    # 公开端点，不需要密钥；但未配置也允许查询
    try:
        resp = await client.get_active_btc_market(slug_prefix=slug_prefix)
        data = resp if isinstance(resp, list) else resp.get("data", resp)
        return success({
            "slug_prefix": slug_prefix or settings.POLYMARKET_BTC5M_SLUG_PREFIX,
            "count": len(data) if isinstance(data, list) else 0,
            "markets": data,
        }, message="active BTC 5min market fetched")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[POLY] get_active_btc_market failed: {e}")
        return fail(f"查询活跃市场失败: {e}", code=502)


# ========== 5. BTC 5min 市场：下单 ==========
@router.post("/btc5m/order", response_model=dict)
async def btc5m_place_order(
    side: str = "UP",
    amount_usd: float | None = None,
    token_id: str | None = None,
    price: float | None = None,
    _: User = Depends(require_admin),
) -> dict:
    """在 BTC 5min 市场下单

    - side: UP / DOWN
    - amount_usd: 不传则用配置默认值
    - token_id: 不传则按 side 自动挑选当前活跃市场 token
    - price: 不传则取中间价
    """
    # 模拟盘模式禁止实盘下单
    if settings.is_simulator:
        return fail(
            f"当前为模拟盘模式 (TRADE_MODE=simulator)，禁止实盘下单。"
            f"请在 .env 设置 TRADE_MODE=live 并填好 Polymarket 密钥",
            code=400,
        )

    client = get_polymarket_client()
    bad = _ensure_configured(client)
    if bad is not None:
        return bad

    side_u = (side or "UP").upper()
    if side_u not in ("UP", "DOWN"):
        return fail(f"side 只支持 UP / DOWN，收到: {side}", code=400)

    try:
        resp = await client.place_btc5m_order(
            side=side_u,
            amount_usd=amount_usd,
            token_id=token_id,
            price=price,
        )
        return success({
            "raw": resp,
            "meta": resp.get("_meta") if isinstance(resp, dict) else None,
        }, message="BTC 5min order placed")
    except Exception as e:  # noqa: BLE001
        logger.error(f"[POLY] btc5m order failed: {e}")
        return fail(f"BTC 5min 下单失败: {e}", code=502)


# ========== 6. BTC 5min 模块配置查询（脱敏，方便运维核对）==========
@router.get("/config", response_model=dict)
async def btc5m_config(_: User = Depends(require_admin)) -> dict:
    """查询 Polymarket 网关 + BTC 5min 模块当前配置（密钥脱敏）"""
    return success({
        "gateway": {
            "host": settings.POLYMARKET_HOST,
            "chain": settings.POLYMARKET_CHAIN,
            "trade_mode": settings.TRADE_MODE,
            "http_timeout": settings.POLYMARKET_HTTP_TIMEOUT,
            "order_retry": settings.POLYMARKET_ORDER_RETRY,
            "wallet_configured": bool(settings.POLYMARKET_WALLET_ADDRESS),
            "private_key_configured": bool(settings.POLYMARKET_PRIVATE_KEY),
            "api_key_configured": bool(settings.POLYMARKET_APIKEY),
            "missing_keys": settings.polymarket_missing_keys,
        },
        "btc5m": {
            "enabled": settings.POLYMARKET_BTC5M_ENABLED,
            "auto_order": settings.POLYMARKET_BTC5M_AUTO_ORDER,
            "effective": settings.btc5m_enabled_effective,
            "slug_prefix": settings.POLYMARKET_BTC5M_SLUG_PREFIX,
            "poll_seconds": settings.POLYMARKET_BTC5M_POLL_SECONDS,
            "default_side": settings.POLYMARKET_BTC5M_DEFAULT_SIDE,
            "default_amount_usd": settings.POLYMARKET_BTC5M_DEFAULT_AMOUNT_USD,
            "price_floor": settings.POLYMARKET_BTC5M_PRICE_FLOOR,
            "price_cap": settings.POLYMARKET_BTC5M_PRICE_CAP,
            "max_amount_usd": settings.POLYMARKET_BTC5M_MAX_AMOUNT_USD,
            "max_open_orders": settings.POLYMARKET_BTC5M_MAX_OPEN_ORDERS,
            "order_ttl_seconds": settings.POLYMARKET_BTC5M_ORDER_TTL_SECONDS,
        },
    }, message="polymarket config (sanitized)")


@router.delete("/order/{order_id}", response_model=dict)
async def cancel_order(
    order_id: str,
    _: User = Depends(require_admin),
) -> dict:
    """取消指定订单"""
    if settings.is_simulator:
        return fail("当前为模拟盘模式，禁止实盘撤单", code=400)
    client = get_polymarket_client()
    bad = _ensure_configured(client)
    if bad is not None:
        return bad
    try:
        resp = await client.cancel_order(order_id)
        return success({"raw": resp, "order_id": order_id}, message="cancel requested")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[POLY] cancel_order failed: {e}")
        return fail(f"撤单失败: {e}", code=502)
