# Polymarket 网关路由：连接/余额/持仓/挂单 + BTC 5min 市场查询与下单 + F3 Relayer Gasless 调试
# —— 鉴权分级 ——
# 1) 写操作 + 钱包敏感读（余额/持仓/订单）：require_admin（role>=3）
# 2) 公开只读（配置状态/连通性/公开市场列表/单个市场详情）：_bootstrap_or_readonly
#    - 若系统尚未存在 admin 且 APP_ALLOW_INIT=True → 放行（首次启动/演示模式方便调试）
#    - 若已存在 admin → 至少登录（不需要 admin role）

import time

from fastapi import APIRouter, Depends, Request
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fwsort.config import settings, reload_env
from fwsort.database import get_async_db
from fwsort.models import User
from fwsort.response import fail, success
from router.auth_router import current_user, current_user_optional

try:
    from fwsort.gateway.polymarket_client import PolymarketClient
except ImportError:
    PolymarketClient = None

router = APIRouter()

_client = None


def get_polymarket_client():
    """获取 Polymarket 客户端单例（若模块不存在返回 None）"""
    global _client
    if PolymarketClient is None:
        return None
    if _client is None:
        _client = PolymarketClient()
    return _client


async def require_admin(user: User = Depends(current_user)) -> User:
    """管理员鉴权（下单/查余额/持仓等敏感操作必须 admin）"""
    if user.role < 3:
        from fwsort.exceptions import PermissionError_

        raise PermissionError_("admin required for polymarket gateway")
    return user


async def _has_any_admin(db: AsyncSession) -> bool:
    """检查系统中是否已经存在管理员（首次启动判定）"""
    cnt = (await db.execute(select(func.count(User.id)).where(User.role >= 3))).scalar_one() or 0
    return cnt > 0


async def _bootstrap_or_readonly(
    db: AsyncSession = Depends(get_async_db),
    user: User | None = Depends(current_user_optional),
) -> None:
    """WP-04 风格：公开只读接口权限
    - APP_ALLOW_INIT=True 且无 admin → 放行（首次部署引导 / 演示模式）
    - 已存在 admin → 至少登录（普通用户即可，不要求 admin role）
    """
    if settings.APP_ALLOW_INIT and not await _has_any_admin(db):
        return
    if user is None:
        from fwsort.exceptions import AuthError
        raise AuthError("login required for polymarket readonly endpoints")


def _ensure_configured(client) -> dict | None:
    """检查密钥是否配置；未配置返回失败响应（路由直接 return）"""
    if client is None:
        return fail("PolymarketClient 模块未安装，旧版网关接口不可用，请使用 F3 接口", code=400)
    if not client.is_configured():
        missing = settings.polymarket_missing_keys
        return fail(
            f"Polymarket 网关未配置密钥，无法执行该操作。请在 .env 填入: {', '.join(missing)}",
            code=400,
            data={"missing_keys": missing},
        )
    return None


def _is_demo_request(request: Request) -> bool:
    """检测请求是否来自 /api/demo/* 演示通道"""
    return request.url.path.startswith("/api/demo/")


_DEMO_ORDER_COUNTER = 0


def _demo_order_response(slug: str, outcome: str, amount: float, side: str) -> dict:
    global _DEMO_ORDER_COUNTER
    _DEMO_ORDER_COUNTER += 1
    import random
    order_id = f"DEMO-ORDER-{int(time.time())}-{_DEMO_ORDER_COUNTER}"
    filled = round(amount * random.uniform(0.95, 1.05), 4)
    price = round(random.uniform(0.45, 0.55), 4)
    return success({
        "response": {
            "order_id": order_id,
            "status": "FILLED",
            "making_amount": str(filled),
            "taking_amount": str(round(filled * price, 4)),
            "ok": True,
            "code": None,
            "message": None,
        },
        "raw": f"DEMO: order_id={order_id}, filled={filled}@{price}",
        "slug": slug,
        "outcome": outcome,
        "amount": amount,
        "side": side,
        "market_url": f"https://polymarket.com/zh/event/demo-{slug}",
        "demo": True,
    }, message="DEMO mode: order placed (simulated)")


def _demo_close_response(slug: str | None) -> dict:
    return success({
        "results": [
            {"type": "LIMIT_ORDER", "response": "DEMO: 平仓成功 (模拟)", "price": "0.5234"},
            {"type": "MARKET", "response": "DEMO: 市价平仓成功 (模拟)"},
        ],
        "slug": slug,
        "count": 2,
        "summary": {"MARKET": 1, "LIMIT_ORDER": 1, "REDEEM": 0, "FAILED": 0, "SKIPPED": 0},
        "failed_count": 0,
        "demo": True,
    }, message="DEMO mode: close completed (simulated)")


def _demo_positions_response(slug: str | None) -> dict:
    import random
    positions = []
    base = slug or "btc-updown-4h-{epoch}"
    for i, outcome in enumerate(["UP", "DOWN"]):
        size = round(random.uniform(1, 20), 4)
        cur_price = round(random.uniform(0.40, 0.60), 4)
        positions.append({
            "title": f"DEMO BTC {outcome} Position #{i+1}",
            "outcome": outcome,
            "size": str(size),
            "cur_price": str(cur_price),
            "current_value": str(round(size * cur_price, 4)),
            "token_id": f"DEMO-TOKEN-{outcome}-{i}",
        })
    return success({
        "positions": positions,
        "count": len(positions),
        "demo": True,
    })


def _demo_market_response(slug: str) -> dict:
    epoch = str(((int(time.time()) // (4 * 3600)) * (4 * 3600)))
    return success({
        "slug": slug.replace("{epoch}", epoch),
        "question": "DEMO: BTC Up/Down 4h",
        "condition_id": "DEMO-CONDITION-0001",
        "accepting_orders": True,
        "closed": False,
        "yes_token_id": "DEMO-TOKEN-YES",
        "no_token_id": "DEMO-TOKEN-NO",
        "url": f"https://polymarket.com/zh/event/{slug.replace('{epoch}', epoch)}",
        "demo": True,
    })


def _demo_init_response() -> dict:
    return success({
        "initialized": True,
        "market": {
            "slug": "btc-updown-4h-demo",
            "question": "DEMO: BTC Up/Down 4h",
            "condition_id": "DEMO-CONDITION-0001",
            "accepting_orders": True,
            "closed": False,
            "yes_token_id": "DEMO-TOKEN-YES",
            "no_token_id": "DEMO-TOKEN-NO",
        },
        "demo": True,
    }, message="DEMO mode: client initialized (simulated)")


def _demo_status_response() -> dict:
    return success({
        "initialized": True,
        "default_slug": "btc-updown-4h-{epoch}",
        "market": {
            "slug": "btc-updown-4h-demo",
            "question": "DEMO: BTC Up/Down 4h",
            "condition_id": "DEMO-CONDITION-0001",
            "accepting_orders": True,
            "closed": False,
        },
        "relayer_key_configured": True,
        "relayer_addr_configured": True,
        "private_key_configured": True,
        "demo": True,
    }, message="DEMO mode: F3 status (simulated)")


def _demo_liquidity_response(token_ids: list[str]) -> dict:
    import random
    liquidity = []
    for tid in token_ids:
        is_yes = "YES" in tid.upper() or "UP" in tid.upper()
        best_bid = round(random.uniform(0.40, 0.50), 4)
        best_ask = round(best_bid + random.uniform(0.01, 0.05), 4)
        liquidity.append({
            "token_id": tid,
            "best_bid_price": str(best_bid),
            "best_bid_size": str(round(random.uniform(5, 50), 2)),
            "best_ask_price": str(best_ask),
            "best_ask_size": str(round(random.uniform(5, 50), 2)),
            "spread": str(round(best_ask - best_bid, 4)),
            "midpoint": str(round((best_bid + best_ask) / 2, 4)),
            "last_trade_price": str(round(random.uniform(best_bid, best_ask), 4)),
            "min_order_size": "5.0",
            "tick_size": "0.001",
            "bids_count": random.randint(3, 10),
            "asks_count": random.randint(3, 10),
            "has_bid_liquidity": True,
            "demo": True,
        })
    return success({"liquidity": liquidity, "count": len(liquidity)})


# ========== 1. 网关连接状态 ==========
@router.get("/status", response_model=dict)
async def gateway_status(__=Depends(_bootstrap_or_readonly)) -> dict:
    """查询 Polymarket 网关连接状态（不发起真实请求，仅本地配置检查；首次启动无 admin 时放行）"""
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
async def gateway_ping(__=Depends(_bootstrap_or_readonly)) -> dict:
    """真实探测 Polymarket 网关连通性（GET /markets 公开端点；首次启动无 admin 时放行）"""
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
    __=Depends(_bootstrap_or_readonly),
) -> dict:
    """查询当前活跃的 BTC 5min 涨跌市场（公开市场信息，首次启动无 admin 时放行）"""
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
    request: Request,
    side: str = "UP",
    amount_usd: float | None = None,
    token_id: str | None = None,
    price: float | None = None,
    _: User = Depends(require_admin),
) -> dict:
    """在 BTC 5min 市场下单"""
    if _is_demo_request(request):
        return success({
            "side": side,
            "amount_usd": amount_usd,
            "token_id": token_id,
            "price": price,
            "demo": True,
            "order_id": "demo-" + str(int(time.time())),
            "status": "live",
        }, message="demo btc5m order ok")
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
async def btc5m_config(__=Depends(_bootstrap_or_readonly)) -> dict:
    """查询 Polymarket 网关 + BTC 5min 模块当前配置（密钥脱敏；首次启动无 admin 时放行）"""
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
    request: Request,
    _: User = Depends(require_admin),
) -> dict:
    """取消指定订单"""
    if _is_demo_request(request):
        return success({"order_id": order_id, "cancelled": True, "demo": True}, message="demo cancel ok")
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


# ========== F3 Relayer Gasless 下单调试接口 ==========
import asyncio
from decimal import Decimal
from pydantic import BaseModel

_pm_instance = None
_pm_lock = asyncio.Lock()


class F3OrderRequest(BaseModel):
    slug: str = "btc-updown-5m-{epoch}"
    outcome: str = "UP"
    amount: float = 1.0
    side: str = "BUY"


class F3CloseRequest(BaseModel):
    slug: str | None = "btc-updown-4h-{epoch}"


class F3LiquidityRequest(BaseModel):
    token_ids: list[str] = []


def _get_pm():
    global _pm_instance
    if _pm_instance is None:
        from fwsort.gateway.polymarket.F3.最简类_下单代码 import pm类
        _pm_instance = pm类()
    return _pm_instance


@router.post("/f3/init", response_model=dict)
async def f3_init(request: Request, _: User = Depends(require_admin)) -> dict:
    """初始化 F3 Relayer Gasless 客户端"""
    if _is_demo_request(request):
        return _demo_init_response()

    reload_env()

    pm = _get_pm()
    try:
        async with _pm_lock:
            await pm.初始化()
        market_info = {}
        if pm.market:
            m = pm.market
            market_info = {
                "slug": getattr(m, "slug", ""),
                "question": getattr(m, "question", ""),
                "condition_id": getattr(m, "condition_id", ""),
                "accepting_orders": getattr(m.state, "accepting_orders", None) if hasattr(m, "state") else None,
                "closed": getattr(m.state, "closed", None) if hasattr(m, "state") else None,
                "yes_token_id": getattr(m.outcomes.yes, "token_id", "") if hasattr(m, "outcomes") else "",
                "no_token_id": getattr(m.outcomes.no, "token_id", "") if hasattr(m, "outcomes") else "",
            }
        return success({
            "initialized": pm._initialized,
            "market": market_info,
        }, message="F3 client initialized")
    except Exception as e:  # noqa: BLE001
        logger.error(f"[POLY] F3 init failed: {e}")
        return fail(f"F3 初始化失败: {e}", code=502)


@router.get("/f3/status", response_model=dict)
async def f3_status(request: Request, __=Depends(_bootstrap_or_readonly)) -> dict:
    """查询 F3 客户端状态"""
    if _is_demo_request(request):
        return _demo_status_response()
    pm = _get_pm()
    market_info = {}
    if pm.market:
        m = pm.market
        market_info = {
            "slug": getattr(m, "slug", ""),
            "question": getattr(m, "question", ""),
            "condition_id": getattr(m, "condition_id", ""),
            "accepting_orders": getattr(m.state, "accepting_orders", None) if hasattr(m, "state") else None,
            "closed": getattr(m.state, "closed", None) if hasattr(m, "state") else None,
        }
    return success({
        "initialized": pm._initialized,
        "default_slug": pm.标的代码,
        "market": market_info,
        "relayer_key_configured": bool(settings.POLYMARKET_RELAYER_API_KEY),
        "relayer_addr_configured": bool(settings.POLYMARKET_RELAYER_API_KEY_ADDRESS),
        "private_key_configured": bool(settings.POLYMARKET_RELAYER_PRIVATE_KEY),
    }, message="F3 status")


def _mask_secret(val: str, name: str, prefix_len: int = 6, suffix_len: int = 4) -> str:
    """脱敏处理：有值显示 前缀***后缀，无值显示 [变量名]"""
    if not val:
        return f"[{name}]"
    if len(val) <= prefix_len + suffix_len:
        return "*" * len(val)
    return val[:prefix_len] + "*" * 8 + val[-suffix_len:]


@router.get("/f3/init-status", response_model=dict)
async def f3_init_status(request: Request, __=Depends(_bootstrap_or_readonly)) -> dict:
    """初始化进展详情（脱敏，非敏感数据直接显示，密钥显示星号或[变量名]）

    自动重新加载 .env 文件，修改 .env 后无需重启服务。
    """
    if _is_demo_request(request):
        return success({
            "demo": True,
            "trade_mode": "simulator",
            "config_items": [],
            "steps": [],
        }, message="DEMO mode: init-status (simulated)")

    # --- 重新加载 .env（修改后无需重启）---
    env_changes = reload_env()

    # --- .env 非敏感值 → 数据库同步（仅当 DB 为空时）---
    try:
        from fwsort.config_service import save_config, get_all_configs
        _all_db = await get_all_configs()
        for _ek in ("POLYMARKET_CHAIN", "POLYMARKET_HOST", "POLYMARKET_BTC5M_ENABLED",
                     "POLYMARKET_BTC5M_SLUG_PREFIX", "TRADE_MODE"):
            _ev = getattr(settings, _ek, "")
            if _ek.lower() not in _all_db and _ev:
                _vt = "bool" if isinstance(_ev, bool) else "int" if isinstance(_ev, int) else "str"
                await save_config(_ek, str(_ev), value_type=_vt, group="polymarket", updated_by="env-sync")
    except Exception:
        pass

    # --- 配置项详情（脱敏）---
    config_items = [
        {
            "key": "POLYMARKET_RELAYER_API_KEY_ADDRESS",
            "label": "Relayer 签名者地址",
            "configured": bool(settings.POLYMARKET_RELAYER_API_KEY_ADDRESS),
            "value": settings.POLYMARKET_RELAYER_API_KEY_ADDRESS or "[POLYMARKET_RELAYER_API_KEY_ADDRESS]",
        },
        {
            "key": "POLYMARKET_RELAYER_API_KEY",
            "label": "Relayer API Key",
            "configured": bool(settings.POLYMARKET_RELAYER_API_KEY),
            "value": _mask_secret(settings.POLYMARKET_RELAYER_API_KEY, "POLYMARKET_RELAYER_API_KEY"),
        },
        {
            "key": "POLYMARKET_RELAYER_PRIVATE_KEY",
            "label": "钱包私钥(POLYGON)",
            "configured": bool(settings.POLYMARKET_RELAYER_PRIVATE_KEY),
            "value": _mask_secret(settings.POLYMARKET_RELAYER_PRIVATE_KEY, "POLYMARKET_RELAYER_PRIVATE_KEY"),
        },
    ]

    # --- 非敏感配置 ---
    runtime_config = {
        "POLYMARKET_CHAIN": settings.POLYMARKET_CHAIN,
        "POLYMARKET_HOST": settings.POLYMARKET_HOST,
        "TRADE_MODE": settings.TRADE_MODE,
        "env_reloaded": env_changes,
    }

    # --- 初始化步骤进度 ---
    pm = _get_pm()
    pm_ready = pm is not None and getattr(pm, "_initialized", False)

    # 检查 SDK 可用性
    sdk_ok = False
    sdk_error = ""
    try:
        from polymarket import AsyncSecureClient  # noqa: F401
        sdk_ok = True
    except ImportError as e:
        sdk_error = str(e)

    # 检查 pm类 可用性
    pm_ok = False
    pm_error = ""
    try:
        from fwsort.gateway.polymarket.F3.最简类_下单代码 import pm类  # noqa: F401
        pm_ok = True
    except Exception as e:
        pm_error = str(e)

    # 构造步骤列表
    f3_ready = bool(settings.POLYMARKET_RELAYER_API_KEY and settings.POLYMARKET_RELAYER_API_KEY_ADDRESS and settings.POLYMARKET_RELAYER_PRIVATE_KEY)

    steps = [
        {
            "step": 1,
            "name": "SDK 加载",
            "status": "ok" if sdk_ok else "error",
            "detail": "polymarket-client SDK 已加载" if sdk_ok else f"SDK 导入失败: {sdk_error}",
        },
        {
            "step": 2,
            "name": "pm类 加载",
            "status": "ok" if pm_ok else "error",
            "detail": "fwsort.gateway.polymarket.最简类_下单代码.pm类 已加载" if pm_ok else f"pm类 导入失败: {pm_error}",
        },
        {
            "step": 3,
            "name": "F3 Relayer 凭据",
            "status": "ok" if f3_ready else "warn",
            "detail": "RELAYER_API_KEY + ADDRESS + PRIVATE_KEY 齐全" if f3_ready else "[POLYMARKET_RELAYER_*] 未配置",
        },
        {
            "step": 4,
            "name": "客户端初始化",
            "status": "ok" if pm_ready else "idle",
            "detail": "AsyncSecureClient 已创建并验证" if pm_ready else "未初始化（点击「初始化连接」按钮）",
        },
    ]

    # --- 可用下单方式 ---
    available_modes = []
    if f3_ready:
        available_modes.append("F3")
    if not available_modes:
        available_modes.append("(无可用方式)")

    return success({
        "config_items": config_items,
        "runtime_config": runtime_config,
        "steps": steps,
        "available_modes": available_modes,
        "pm_initialized": pm_ready,
        "total_steps": len(steps),
        "ok_steps": sum(1 for s in steps if s["status"] == "ok"),
    }, message="F3 init-status (sanitized)")


@router.get("/f3/market", response_model=dict)
async def f3_market(
    request: Request,
    slug: str = "btc-updown-5m-{epoch}",
    __=Depends(_bootstrap_or_readonly),
) -> dict:
    """查询指定 slug 的市场信息（需登录，已初始化 admin 后要求认证）"""
    if _is_demo_request(request):
        return _demo_market_response(slug)
    pm = _get_pm()
    try:
        await pm._ensure_initialized()
        market = await pm.获得市场(标的代码=slug)
        m = market
        market_info = {
            "slug": getattr(m, "slug", ""),
            "question": getattr(m, "question", ""),
            "condition_id": getattr(m, "condition_id", ""),
            "accepting_orders": getattr(m.state, "accepting_orders", None) if hasattr(m, "state") else None,
            "closed": getattr(m.state, "closed", None) if hasattr(m, "state") else None,
            "yes_token_id": getattr(m.outcomes.yes, "token_id", "") if hasattr(m, "outcomes") else "",
            "no_token_id": getattr(m.outcomes.no, "token_id", "") if hasattr(m, "outcomes") else "",
            "url": "https://polymarket.com/zh/event/" + getattr(m, "slug", ""),
        }
        return success(market_info, message="market fetched")
    except Exception as e:  # noqa: BLE001
        logger.error(f"[POLY] F3 get_market failed: {e}")
        return fail(f"获取市场失败: {e}", code=502)


@router.post("/f3/order", response_model=dict)
async def f3_order(req: F3OrderRequest, request: Request, _: User = Depends(require_admin)) -> dict:
    """F3 下市价单"""
    if _is_demo_request(request):
        return _demo_order_response(req.slug, req.outcome, req.amount, req.side)
    if settings.is_simulator:
        return fail("当前为模拟盘模式，禁止实盘下单", code=400)

    pm = _get_pm()
    try:
        async with _pm_lock:
            await pm._ensure_initialized()
            response = await pm.下单(
                标的代码=req.slug,
                outcome=req.outcome,
                amount=Decimal(str(req.amount)),
                side=req.side,
            )
            if response is None:
                return fail("下单失败：市场已结算或无法下单", code=400)
            resp_data = {}
            for attr in ("order_id", "status", "making_amount", "taking_amount", "ok", "code", "message"):
                if hasattr(response, attr):
                    resp_data[attr] = getattr(response, attr)
            market_url = "https://polymarket.com/zh/event/" + getattr(pm.market, "slug", "") if pm.market else ""
            return success({
                "response": resp_data,
                "raw": str(response),
                "slug": req.slug,
                "outcome": req.outcome,
                "amount": req.amount,
                "side": req.side,
                "market_url": market_url,
            }, message="F3 order placed")
    except Exception as e:  # noqa: BLE001
        logger.error(f"[POLY] F3 order failed: {e}")
        return fail(f"F3 下单失败: {e}", code=502)


@router.post("/f3/close", response_model=dict)
async def f3_close(req: F3CloseRequest, request: Request, _: User = Depends(require_admin)) -> dict:
    """F3 平仓"""
    if _is_demo_request(request):
        return _demo_close_response(req.slug)
    if settings.is_simulator:
        return fail("当前为模拟盘模式，禁止实盘平仓", code=400)

    pm = _get_pm()
    try:
        async with _pm_lock:
            await pm._ensure_initialized()
            results = await pm.平仓(标的代码=req.slug)
            summary = {"MARKET": 0, "LIMIT_ORDER": 0, "REDEEM": 0, "FAILED": 0, "SKIPPED": 0}
            for r in (results or []):
                t = r.get("type", "UNKNOWN") if isinstance(r, dict) else "MARKET"
                summary[t] = summary.get(t, 0) + 1
            failed = [r for r in (results or []) if isinstance(r, dict) and r.get("type") == "FAILED"]
            return success({
                "results": results or [],
                "slug": req.slug,
                "count": len(results or []),
                "summary": summary,
                "failed_count": len(failed),
            }, message="F3 close completed")
    except Exception as e:  # noqa: BLE001
        logger.error(f"[POLY] F3 close failed: {e}")
        return fail(f"F3 平仓失败: {e}", code=502)


@router.post("/f3/liquidity", response_model=dict)
async def f3_liquidity(req: F3LiquidityRequest, request: Request, _: User = Depends(require_admin)) -> dict:
    """F3 查询 token 盘口流动性"""
    if _is_demo_request(request):
        return _demo_liquidity_response(req.token_ids)
    pm = _get_pm()
    try:
        async with _pm_lock:
            await pm._ensure_initialized()
            results = []
            for tid in req.token_ids:
                liq = await pm.查询流动性(tid)
                results.append(liq)
            return success({"liquidity": results, "count": len(results)})
    except Exception as e:  # noqa: BLE001
        logger.error(f"[POLY] F3 liquidity failed: {e}")
        return fail(f"查询流动性失败: {e}", code=502)


@router.get("/f3/positions", response_model=dict)
async def f3_positions(
    request: Request,
    slug: str | None = None,
    _: User = Depends(require_admin),
) -> dict:
    """F3 查询持仓"""
    if _is_demo_request(request):
        return _demo_positions_response(slug)
    pm = _get_pm()
    try:
        async with _pm_lock:
            await pm._ensure_initialized()
            if slug:
                market = await pm.获得市场(标的代码=slug)
                paginator = pm.client.list_positions(market=[market.condition_id])
            else:
                paginator = pm.client.list_positions()
            positions = []
            async for pos in paginator.iter_items():
                positions.append({
                    "title": getattr(pos, "title", "") or getattr(pos, "slug", ""),
                    "outcome": getattr(pos, "outcome", ""),
                    "size": str(getattr(pos, "size", "")),
                    "cur_price": str(getattr(pos, "cur_price", "")),
                    "current_value": str(getattr(pos, "current_value", "")),
                    "token_id": getattr(pos, "token_id", ""),
                })
            return success({
                "positions": positions,
                "count": len(positions),
            }, message="F3 positions fetched")
    except Exception as e:  # noqa: BLE001
        logger.error(f"[POLY] F3 positions failed: {e}")
        return fail(f"F3 查询持仓失败: {e}", code=502)