# FastAPI 入口：福纹排行榜（fwsort）V1.0
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from fwsort.config import settings
from fwsort.es_client import ensure_order_log_index, close_es_client
from fwsort.exceptions import FwsortError
from fwsort.response import fail
from router import (
    admin_router,
    agent_router,
    auth_router,
    compare_router,
    config_router,
    follow_router,
    notification_router,
    polymarket_router,
    ranking_router,
    rental_router,
)


# ========== 日志配置（安全 > 稳定 > 性能 > 功能 > 界面）==========
class InterceptHandler(logging.Handler):
    """把标准 logging 路由到 loguru，统一格式"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())


logging.basicConfig(handlers=[InterceptHandler()], level=0)


# ========== 应用生命周期 ==========
def _validate_production_secret() -> None:
    """WP-02：生产环境启动前强制校验密钥
    - 拒绝默认密钥 change-me
    - 拒绝长度 < 32 的弱密钥
    - 拒绝纯字母 / 纯数字
    """
    if settings.APP_ENV != "production":
        return
    key = settings.APP_SECRET_KEY or ""
    issues: list[str] = []
    if key == "change-me":
        issues.append("APP_SECRET_KEY 仍为默认值 'change-me'")
    if len(key) < 32:
        issues.append(f"APP_SECRET_KEY 长度 {len(key)} < 32，建议 ≥ 32 字节随机串")
    if key.isalpha() or key.isdigit():
        issues.append("APP_SECRET_KEY 强度不足：不能是纯字母或纯数字")
    if issues:
        msg = "❌ 生产环境启动失败，安全基线未通过:\n  - " + "\n  - ".join(issues)
        msg += "\n  生成方式：python -c \"import secrets;print(secrets.token_urlsafe(32))\""
        raise RuntimeError(msg)


def _warn_polymarket_keys() -> None:
    """启动时醒目提醒 Polymarket 网关密钥配置状态

    - 密钥未填：警告无法连接/查余额/下单
    - BTC 5min 已启用但密钥未填：警告自动下单不会生效
    - 模拟盘 + BTC 5min 已启用：警告实盘不会触发
    """
    missing = settings.polymarket_missing_keys
    if missing:
        logger.warning(
            "⚠️⚠️⚠️  Polymarket 网关密钥未配置: "
            + ", ".join(missing)
            + "  → 仅能查询公开行情，无法连接/查余额/下单。"
            "请编辑 .env 填入对应密钥后重启服务。"
        )
    if settings.POLYMARKET_BTC5M_ENABLED:
        if not settings.polymarket_configured:
            logger.warning(
                "⚠️  POLYMARKET_BTC5M_ENABLED=true 但 Polymarket 密钥未填，"
                "BTC 5min 自动下单不会生效（请先填好 POLYMARKET_WALLET_PRIVATE_KEY / _ADDRESS）"
            )
        if settings.is_simulator:
            logger.warning(
                "⚠️  POLYMARKET_BTC5M_ENABLED=true 但 TRADE_MODE=simulator，"
                "BTC 5min 自动下单不会触发实盘（请设 TRADE_MODE=live 启用实盘）"
            )
        if settings.btc5m_enabled_effective:
            logger.info(
                f"✅ BTC 5min 自动下单已生效：每 {settings.POLYMARKET_BTC5M_POLL_SECONDS}s 轮询，"
                f"auto_order={settings.POLYMARKET_BTC5M_AUTO_ORDER}"
            )


def _init_demo_db_sync() -> None:
    """WP-06: 演示模式后台初始化（同步版本，在线程池中运行）
     seeding 内容：User + ExecutionAccount + StrategyPerformance + RentalAgent
    + FollowSubscription + Notification + WeightConfig
    """
    try:
        from datetime import datetime, timedelta, timezone

        from fwsort.database import DemoSyncSessionLocal, init_demo_db
        from fwsort.models import (
            ExecutionAccount,
            FollowSubscription,
            Notification,
            RentalAgent,
            StrategyPerformance,
            User,
            WeightConfig,
        )
        from fwsort.ranking_engine import composite_score
        from fwsort.security import hash_password
        import random

        init_demo_db()
        logger.info(f"WP-06: demo DB ready at {settings.APP_DEMO_SQLITE_PATH}")

        with DemoSyncSessionLocal() as db:
            has_admin = db.query(User).filter(User.role >= 3).first() is not None
            has_accounts = db.query(ExecutionAccount).first() is not None
            if not has_admin:
                db.add(User(
                    email="demo@fwquant.com",
                    nickname="演示用户",
                    password_hash=hash_password("demo123456"),
                    role=3,
                    status=0,
                ))
                db.commit()
                logger.info("WP-06: seeded demo admin (demo@fwquant.com / demo123456)")

            demo_user = db.query(User).filter(User.email == "demo@fwquant.com").first()
            owner_id = demo_user.id if demo_user else None

            # ---- 1. 执行账户 ----
            if not has_accounts:
                names = ["量化王", "趋势猎手", "波段大师", "套利先锋", "价值捕手",
                         "日内高手", "加密游侠", "AI猎人", "链上先锋", "套利大师"]
                platforms = ["polymarket", "okx"]
                sigs = ["UP", "DOWN", "NEUTRAL"]
                for i in range(15):
                    db.add(ExecutionAccount(
                        uid=f"ACC-DEMO{1000+i}",
                        owner_id=owner_id,
                        name=f"{names[i%len(names)]}（演示）",
                        platform=platforms[i%2],
                        account_type=0,
                        initial_balance=10000.0,
                        current_balance=10000.0 + random.uniform(-500, 1500),
                        daily_pnl=random.uniform(-100, 200),
                        public_enabled=True,
                        status=0,
                        order_amount_usd=5.0,
                        signal=sigs[i%3],
                        target_url=f"https://demo-{i}.fwquant.com",
                        target_symbol="BTCUSDT",
                    ))
                db.commit()
                logger.info("WP-06: seeded 15 demo execution accounts")

            # ---- 2. 策略绩效（每个账户 4 个周期：日/周/月/总） ----
            has_perf = db.query(StrategyPerformance).first() is not None
            if not has_perf:
                accounts = db.query(ExecutionAccount).all()
                for acc in accounts:
                    for period_type in (1, 2, 3, 4):
                        ann = round(random.uniform(-0.05, 0.8), 4)
                        dd = round(random.uniform(0.02, 0.25), 4)
                        sharpe = round(random.uniform(0.3, 2.5), 2)
                        plr = round(random.uniform(0.8, 2.5), 2)
                        ex = round(random.uniform(0.65, 0.95), 4)
                        score = composite_score(ann, dd, sharpe, plr, ex)
                        now = datetime.now(tz=timezone.utc)
                        if period_type == 1:
                            start = now - timedelta(days=1)
                        elif period_type == 2:
                            start = now - timedelta(days=7)
                        elif period_type == 3:
                            start = now - timedelta(days=30)
                        else:
                            start = now - timedelta(days=365)
                        db.add(StrategyPerformance(
                            account_id=acc.id,
                            uid=acc.uid,
                            period_type=period_type,
                            start_time=start,
                            end_time=now,
                            annualized_return=ann,
                            max_drawdown=dd,
                            sharpe_ratio=sharpe,
                            sortino_ratio=round(sharpe * 1.2, 2),
                            calmar_ratio=round(ann / max(dd, 0.01), 4),
                            profit_factor=plr,
                            win_rate=round(random.uniform(0.45, 0.72), 4),
                            profit_loss_ratio=plr,
                            trade_count=random.randint(50, 800),
                            execution_rate=round(random.uniform(0.88, 0.99), 4),
                            avg_slippage=round(random.uniform(0.0001, 0.003), 6),
                            avg_latency=random.randint(150, 600),
                            cancel_rate=round(random.uniform(0.01, 0.12), 4),
                            execution_score=ex,
                            composite_score=score,
                            total_return=round(random.uniform(-0.1, 1.5), 4),
                            volatility=round(random.uniform(0.05, 0.35), 4),
                            max_consecutive_loss=random.randint(1, 8),
                        ))
                db.commit()
                logger.info("WP-06: seeded StrategyPerformance for all demo accounts")

            # ---- 3. 租用品类（6 个智能体） ----
            has_rental = db.query(RentalAgent).first() is not None
            if not has_rental:
                catalog = [
                    {"name": "GPT-4o 趋势猎手", "model": "gpt-4o", "agent_type": "trend",
                     "description": "基于 GPT-4o 的多周期趋势识别智能体，适合 BTC/ETH 主线行情。",
                     "price_per_call_usd": 0.10, "price_per_hour_usd": 0.50, "max_concurrent": 20},
                    {"name": "Claude 3.5 风控官", "model": "claude-3-5-sonnet", "agent_type": "risk",
                     "description": "Claude 3.5 Sonnet 风控智能体：识别极端行情、提示减仓。",
                     "price_per_call_usd": 0.12, "price_per_hour_usd": 0.60, "max_concurrent": 15},
                    {"name": "Gemini 2.0 链上分析师", "model": "gemini-2.0-flash", "agent_type": "onchain",
                     "description": "Gemini 2.0 链上数据分析师，专注资金流向与持仓变化。",
                     "price_per_call_usd": 0.08, "price_per_hour_usd": 0.40, "max_concurrent": 25},
                    {"name": "GPT-4o 通用分析师", "model": "gpt-4o-mini", "agent_type": "general",
                     "description": "GPT-4o-mini 通用分析智能体，价格亲民，适合大批量试算。",
                     "price_per_call_usd": 0.03, "price_per_hour_usd": 0.20, "max_concurrent": 50},
                    {"name": "Claude 情绪解读", "model": "claude-3-5-haiku", "agent_type": "sentiment",
                     "description": "Claude 3.5 Haiku 情绪解读智能体，分析新闻/社媒情绪。",
                     "price_per_call_usd": 0.05, "price_per_hour_usd": 0.30, "max_concurrent": 30},
                    {"name": "Gemini 多模态信号", "model": "gemini-2.0-flash", "agent_type": "general",
                     "description": "Gemini 2.0 Flash 多模态信号智能体，融合 K 线 + 文本。",
                     "price_per_call_usd": 0.08, "price_per_hour_usd": 0.40, "max_concurrent": 25},
                ]
                for c in catalog:
                    db.add(RentalAgent(**c, is_active=True))
                db.commit()
                logger.info("WP-06: seeded 6 rental agents")

            # ---- 4. 跟单订阅（演示用户订阅前 3 个账户） ----
            has_follow = db.query(FollowSubscription).first() is not None
            if not has_follow and owner_id:
                accounts = db.query(ExecutionAccount).limit(3).all()
                for acc in accounts:
                    db.add(FollowSubscription(
                        subscriber_id=owner_id,
                        leader_uid=acc.uid,
                        leader_name=acc.name,
                        mode=3,
                        subscription_fee_usd=9.9,
                        profit_share_ratio=0.20,
                        follow_amount_usd=50.0,
                        total_followed=random.randint(5, 30),
                        total_pnl=round(random.uniform(-20, 80), 2),
                        total_fee_paid=9.9,
                        total_share_paid=round(random.uniform(0, 10), 2),
                        status=1,
                        expires_at=datetime.now(tz=timezone.utc) + timedelta(days=30),
                    ))
                db.commit()
                logger.info("WP-06: seeded 3 follow subscriptions")

            # ---- 5. 通知消息 ----
            has_notify = db.query(Notification).first() is not None
            if not has_notify and owner_id:
                notifications = [
                    (1, "系统通知", "欢迎使用福纹排行榜演示模式！所有数据均为模拟数据。"),
                    (4, "榜单更新", "量化王（演示）综合分上升至 82.5，当前排名第 1。"),
                    (2, "跟单提醒", "趋势猎手（演示）发出 UP 信号，已自动跟单。"),
                    (3, "风控提示", "波段大师（演示）日回撤达到 3.2%，请注意风险。"),
                    (5, "租用到期", "GPT-4o 趋势猎手包时段租用将在 24 小时后到期。"),
                ]
                for ntype, title, content in notifications:
                    db.add(Notification(
                        user_id=owner_id,
                        ntype=ntype,
                        title=title,
                        content=content,
                        is_read=False,
                    ))
                db.commit()
                logger.info("WP-06: seeded 5 notifications")

            # ---- 6. 权重配置 ----
            has_weight = db.query(WeightConfig).first() is not None
            if not has_weight:
                for rank_type in (1, 2, 3, 4, 5):
                    db.add(WeightConfig(
                        rank_type=rank_type,
                        weight_annualized=0.30,
                        weight_drawdown=0.20,
                        weight_sharpe=0.20,
                        weight_profit_loss=0.15,
                        weight_execution=0.15,
                    ))
                db.commit()
                logger.info("WP-06: seeded 5 weight configs")

            # ---- 7. 租用订单（演示用户租用前 2 个智能体） ----
            from fwsort.models import RentalAgent, RentalOrder
            has_rental_order = db.query(RentalOrder).first() is not None
            if not has_rental_order and owner_id:
                agents = db.query(RentalAgent).limit(2).all()
                for idx, agent in enumerate(agents):
                    rental_type = 1 if idx == 0 else 2  # 第一个按次，第二个包时段
                    hours = 0 if rental_type == 1 else random.choice([24, 48, 72])
                    used_calls = random.randint(5, 30) if rental_type == 1 else 0
                    price = agent.price_per_call_usd * used_calls if rental_type == 1 else agent.price_per_hour_usd * hours
                    status = random.choice([1, 1, 1, 2])  # 大部分有效，少数过期
                    expires = datetime.now(tz=timezone.utc) + timedelta(hours=random.randint(-12, 72))
                    
                    db.add(RentalOrder(
                        renter_id=owner_id,
                        agent_id=agent.id,
                        rental_type=rental_type,
                        hours=hours,
                        used_calls=used_calls,
                        total_paid_usd=round(price, 2),
                        status=status,
                        started_at=datetime.now(tz=timezone.utc) - timedelta(days=random.randint(1, 7)),
                        expires_at=expires if status == 1 else None,
                    ))
                db.commit()
                logger.info(f"WP-06: seeded {len(agents)} rental orders")

    except Exception as e:  # noqa: BLE001
        logger.warning(f"WP-06 demo sync init failed: {type(e).__name__}: {e}")


async def _seed_demo_redis_zset() -> None:
    """WP-06: 异步 seeding Redis ZSet 榜单（ranking router 读的是全局 async_redis）
    注意：需要等待 demo DB 初始化完成后才执行，否则读不到数据
    """
    import asyncio as _asyncio

    # 等待 2 秒让 demo DB 初始化完成
    await _asyncio.sleep(2)

    try:
        from fwsort.database import DemoSyncSessionLocal
        from fwsort.models import ExecutionAccount, StrategyPerformance
        from fwsort.redis_client import RankType, async_redis as _global_redis, rank_key

        with DemoSyncSessionLocal() as db:
            perfs = (
                db.query(StrategyPerformance, ExecutionAccount)
                .join(ExecutionAccount, ExecutionAccount.id == StrategyPerformance.account_id)
                .filter(StrategyPerformance.period_type == 4)
                .filter(ExecutionAccount.deleted_at.is_(None))
                .all()
            )
            for sp, acc in perfs:
                score = float(sp.composite_score)
                for rt_name in (RankType.REALTIME, RankType.DAILY,
                                RankType.WEEKLY, RankType.MONTHLY, RankType.ALL_TIME):
                    key = rank_key(rt_name)
                    await _global_redis.zadd(key, {acc.uid: score})
            logger.info(f"WP-06: seeded Redis ZSet with {len(perfs)} accounts x 5 rank types")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"WP-06: failed to seed Redis ZSet: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动：初始化 ES 索引 + 演示数据库；关闭：清理资源"""
    _validate_production_secret()
    _warn_polymarket_keys()

    # ES 初始化：放到后台，不阻塞启动
    async def _init_es() -> None:
        try:
            await ensure_order_log_index()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ES init failed (ignored, will run without ES): {type(e).__name__}: {e}")

    if settings.APP_DEMO_MODE:
        # 演示模式：数据库初始化放到后台线程池（含 bcrypt 同步阻塞操作）
        asyncio.create_task(asyncio.to_thread(_init_demo_db_sync))
        # Redis ZSet seeding 是异步的，直接 create_task
        asyncio.create_task(_seed_demo_redis_zset())

    # ES 连接超时较长（~2s），放到后台不阻塞 server start
    asyncio.create_task(_init_es())

    logger.info(f"fwsort started | env={settings.APP_ENV} | mode={settings.TRADE_MODE} | demo={settings.APP_DEMO_MODE}")
    yield
    try:
        await close_es_client()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"ES close error: {type(e).__name__}: {e}")
    # 清理 Polymarket 网关 httpx 连接
    try:
        if polymarket_router._client is not None:
            await polymarket_router._client.close()
            polymarket_router._client = None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Polymarket client close error: {type(e).__name__}: {e}")
    logger.info("fwsort shutting down")


# ========== FastAPI 实例 ==========
app = FastAPI(
    title="FWQuant Ranking System",
    version="1.0.0",
    description="福纹排行榜：多智能体策略-订单执行规则 V1.0",
    lifespan=lifespan,
)

# CORS（前端跨域）：按环境收敛（WP-01）
def _parse_cors_origins() -> list[str]:
    """解析 APP_CORS_ORIGINS 配置
    - 空字符串 / "*" → 返回 ["*"]（仅开发模式）
    - 逗号分隔的 URL 列表 → 返回列表（生产环境）
    """
    raw = (settings.APP_CORS_ORIGINS or "").strip()
    if not raw or raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


_cors_origins = _parse_cors_origins()
# 安全提示：生产环境 + 通配符 + 凭据 是不安全组合，启动时检查
if settings.APP_ENV == "production" and "*" in _cors_origins:
    from loguru import logger as _lg

    _lg.warning(
        "⚠️  CORS is wildcard in production env! "
        "Set APP_CORS_ORIGINS explicitly. Falling back to deny-credentials mode."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    # 通配符模式下不能开启凭据（浏览器拒绝且存在 CSRF 风险）
    allow_credentials=("*" not in _cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========== 统一 JSON 响应 Content-Type 头（避免 PowerShell/某些客户端按 GBK 解码导致中文乱码）==========
class _UTF8JSONHeaderMiddleware:
    """为 application/json 响应补上 charset=utf-8（不影响其他资源）"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                raw_headers = list(message.get("headers", []))
                new_headers = []
                for k, v in raw_headers:
                    # ASGI 规范严格要求 headers 为 bytes, 统一转换确保类型安全
                    if isinstance(k, str):
                        k_bytes = k.encode("latin1")
                    else:
                        k_bytes = bytes(k)
                    if isinstance(v, str):
                        v_bytes = v.encode("latin1")
                    else:
                        v_bytes = bytes(v)
                    # 判断 content-type
                    try:
                        k_name = k_bytes.decode("latin1").lower()
                    except Exception:
                        k_name = ""
                    if k_name == "content-type":
                        try:
                            v_str = v_bytes.decode("latin1")
                        except Exception:
                            v_str = ""
                        # 仅给 application/json 补 charset，其它资源（CSS/JS/图片/HTML 等）原样保留
                        if v_str.startswith("application/json") and "charset" not in v_str.lower():
                            v_str = v_str + "; charset=utf-8"
                            v_bytes = v_str.encode("latin1")
                    new_headers.append((k_bytes, v_bytes))
                message["headers"] = new_headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(_UTF8JSONHeaderMiddleware)


# ========== 全局异常处理（业务异常）==========
@app.exception_handler(FwsortError)
async def fwsort_error_handler(_: Request, exc: FwsortError) -> JSONResponse:
    return JSONResponse(status_code=exc.code, content=fail(exc.message, code=exc.code))


# ========== 全局异常处理（Pydantic 参数校验 422）==========
# 把 Pydantic 校验错误类型映射为中文友好提示
# 注意：基础消息是「不带主语」的描述，会自动拼上字段名（"邮箱"+ "格式不正确" = "邮箱格式不正确"）
_VALIDATION_MSG_ZH: dict[str, str] = {
    "missing": "不能为空",
    "string_too_short": "长度不足，至少需 {n} 个字符",
    "string_too_long": "长度超出，最多 {n} 个字符",
    "value_error.email": "格式不正确",
    "value_error": "无效",
    "int_parsing": "需要整数",
    "float_parsing": "需要数字",
    "bool_parsing": "需要布尔值",
    "json_invalid": "JSON 格式错误",
    "type_error": "类型错误",
    "enum": "取值不在允许范围",
    "literal_error": "取值不在允许范围",
    "extra_forbidden": "不允许的额外字段",
    "greater_than": "数值过小",
    "greater_than_equal": "数值过小",
    "less_than": "数值过大",
    "less_than_equal": "数值过大",
}

# 把常见字段名翻译为中文
_FIELD_NAME_ZH: dict[str, str] = {
    "email": "邮箱",
    "password": "密码",
    "nickname": "昵称",
    "name": "名称",
    "platform": "平台",
    "account_type": "账户类型",
    "initial_balance": "初始资金",
    "old_password": "旧密码",
    "new_password": "新密码",
}


def _zh_validation_message(field: str, msg: str, type_: str) -> str:
    """生成中文友好错误消息（保留原始信息以便排查）"""
    import re
    f_zh = _FIELD_NAME_ZH.get(field, field)
    base = ""
    # 1) string_too_short: 优先从 msg 中提取 "at least N" 数字
    if "at least" in msg.lower() or type_ == "string_too_short":
        m = re.search(r"(\d+)", msg)
        n = m.group(1) if m else ""
        base = f"长度不足，至少需 {n} 个字符" if n else "长度不足"
    elif "at most" in msg.lower() or type_ == "string_too_long":
        m = re.search(r"(\d+)", msg)
        n = m.group(1) if m else ""
        base = f"长度超出，最多 {n} 个字符" if n else "长度超出"
    elif "valid email" in msg.lower() or type_ == "value_error.email":
        base = "格式不正确"
    elif "field required" in msg.lower() or type_ == "missing":
        base = "不能为空"
    else:
        # 2) 查表
        base = _VALIDATION_MSG_ZH.get(type_, "")
        if not base and "." in type_:
            base = _VALIDATION_MSG_ZH.get(type_.split(".")[-1], "")
        # 处理 string_too_short / string_too_long 占位符
        if "{n}" in base:
            m = re.search(r"(\d+)", msg)
            n = m.group(1) if m else ""
            base = base.replace("{n}", n)

    if base:
        return f"{f_zh}{base}"
    # 兜底：原始英文消息
    return f"{f_zh}：{msg}"


def _flatten_validation_errors(errors: list[dict]) -> list[dict]:
    """把 FastAPI 的嵌套错误数组展平成 [{field, message, type}, ...]"""
    out = []
    for e in errors or []:
        loc = list(e.get("loc", []))
        # 去掉 "body"/"query"/"path"/"header" 顶层
        if loc and loc[0] in ("body", "query", "path", "header"):
            loc = loc[1:]
        field = ".".join(str(x) for x in loc) if loc else "<root>"
        type_ = e.get("type", "")
        raw_msg = e.get("msg", "invalid value")
        zh_msg = _zh_validation_message(field, raw_msg, type_)
        out.append({
            "field": field,
            "message": zh_msg,
            "raw_message": raw_msg,
            "type": type_,
        })
    return out


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """把 Pydantic 校验失败的 422 转为统一格式，前端可读取 message"""
    items = _flatten_validation_errors(exc.errors())
    if items:
        first = items[0]
        msg = first["message"] if first["field"] == "<root>" else first["message"]
    else:
        msg = "参数校验失败"
    return JSONResponse(
        status_code=422,
        content=fail(msg, code=422, data={"errors": items}),
    )


# ========== 路由注册 ==========
app.include_router(ranking_router.router, prefix="/api/ranking", tags=["ranking"])
app.include_router(config_router.router, prefix="/api/ranking/config", tags=["config"])
app.include_router(compare_router.router, prefix="/api/ranking/compare", tags=["compare"])
app.include_router(auth_router.router, prefix="/api/auth", tags=["auth"])
app.include_router(agent_router.router, prefix="/api/agent", tags=["agent"])
app.include_router(admin_router.router, prefix="/api/admin", tags=["admin"])
app.include_router(follow_router.router, prefix="/api/follow", tags=["follow"])
app.include_router(rental_router.router, prefix="/api/rental", tags=["rental"])
app.include_router(notification_router.router, prefix="/api/notify", tags=["notify"])
app.include_router(polymarket_router.router, prefix="/api/polymarket", tags=["polymarket"])


# ========== WP-06：把所有 /api/* 路由镜像到 /api/demo/*（独立数据通道）==========
# 注意：app.include_router(prefix=...) 之后，route.path 是相对路径（不带 prefix），
# 因此需要根据"最近 include_router 的 prefix"反推完整路径。直接做法：
# 在每个 include_router 后立即镜像该 router 的所有路由到 /api/demo/*。
def _mirror_router_to_demo(router_obj, prefix: str) -> int:
    """把一个 APIRouter 下的所有路由镜像到 /api/demo{prefix}/* 路径
    - prefix 形如 '/api/ranking'（已含 /api）
    - new_path = '/api/demo' + prefix[4:] + r.path  →  '/api/demo/ranking/xxx'
    """
    from fastapi.routing import APIRoute

    demo_prefix = "/api/demo" + prefix[len("/api"):]  # 去掉 prefix 中的 /api 段
    count = 0
    for r in list(router_obj.routes):
        if not isinstance(r, APIRoute):
            continue
        new_path = f"{demo_prefix}{r.path}"
        try:
            app.add_api_route(
                new_path,
                r.endpoint,
                methods=list(r.methods or []),
                tags=["demo"],
                dependencies=r.dependencies,
                response_model=r.response_model,
            )
            count += 1
        except ValueError:
            # 重复添加（重复 import）跳过
            pass
    return count


_total_mirrored = 0
_total_mirrored += _mirror_router_to_demo(ranking_router.router, "/api/ranking")
_total_mirrored += _mirror_router_to_demo(config_router.router, "/api/ranking/config")
_total_mirrored += _mirror_router_to_demo(compare_router.router, "/api/ranking/compare")
_total_mirrored += _mirror_router_to_demo(auth_router.router, "/api/auth")
_total_mirrored += _mirror_router_to_demo(agent_router.router, "/api/agent")
_total_mirrored += _mirror_router_to_demo(admin_router.router, "/api/admin")
_total_mirrored += _mirror_router_to_demo(follow_router.router, "/api/follow")
_total_mirrored += _mirror_router_to_demo(rental_router.router, "/api/rental")
_total_mirrored += _mirror_router_to_demo(notification_router.router, "/api/notify")
_total_mirrored += _mirror_router_to_demo(polymarket_router.router, "/api/polymarket")
logger.info(f"WP-06: mirrored {_total_mirrored} /api/* routes to /api/demo/*")


# ========== 静态资源与前端页面 ==========
app.mount("/static", StaticFiles(directory="web/static"), name="static")


# ========== Favicon 图标路由 ==========
@app.get("/favicon.ico")
async def favicon():
    """浏览器默认请求 /favicon.ico，返回静态目录下的图标"""
    from fastapi.responses import FileResponse
    import os
    favicon_path = os.path.join("web", "static", "favicon.ico")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path, media_type="image/x-icon")
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="favicon not found")


# ========== 演示模式注入工具 ==========
_DEMO_INJECT_HEAD = '<script>window.__FW_DEMO_MODE__=true;</script>'

_DEMO_INJECT_BODY = """
<style>
  /* 演示模式右上角固定徽章 */
  .fw-demo-badge{position:fixed;top:56px;right:24px;z-index:999;background:linear-gradient(135deg,#9b6dff,#6df0ff);color:#fff;padding:8px 18px;border-radius:0 0 10px 10px;font-size:13px;font-weight:700;letter-spacing:1px;box-shadow:0 3px 12px rgba(155,109,255,0.4);animation:fw-demo-pulse 2s ease-in-out infinite;}
  .fw-demo-badge::before{content:"🎬 ";}
  @keyframes fw-demo-pulse{0%,100%{opacity:1}50%{opacity:.75}}
  /* 演示模式底部固定提示条 */
  .fw-demo-bar{position:fixed;bottom:0;left:0;right:0;z-index:998;background:linear-gradient(90deg,rgba(155,109,255,.92),rgba(109,240,255,.92));color:#fff;text-align:center;padding:8px 0;font-size:13px;font-weight:600;letter-spacing:.5px;backdrop-filter:blur(6px);}
  .fw-demo-bar a{color:#fff;text-decoration:underline;margin-left:12px;font-weight:700;}
  /* 为底部提示条留出空间 */
  body.fw-demo-body{padding-bottom:42px;}
</style>
<div class="fw-demo-badge">演示模式</div>
<div class="fw-demo-bar">当前为演示模式 · 数据均为模拟 · 不涉及真实资金 <a href="/">返回生产模式 →</a></div>
<script>
  // 演示模式下：导航链接自动重写为 /demo/ 前缀
  document.body.classList.add("fw-demo-body");
  var _demoNavMap={"/":"demo","/detail":"demo/detail","/accounts":"demo/accounts","/accounts/tasks":"demo/accounts/tasks","/accounts/execution":"demo/accounts/execution","/follow":"demo/follow","/follow/my":"demo/follow/my","/rental":"demo/rental","/admin":"demo/admin","/profile":"demo/profile"};
  document.querySelectorAll(".fwui-nav__link").forEach(function(a){
    var h=a.getAttribute("href");
    if(h&&_demoNavMap[h]) a.setAttribute("href","/"+_demoNavMap[h]);
  });
  // 演示模式下：JS 跳转也自动加 /demo 前缀
  window.__FW_DEMO_PREFIX__="/api/demo";
</script>
"""

def _inject_demo(html: str) -> str:
    """为 HTML 注入演示模式标记、徽章、底部提示条和导航重写"""
    html = html.replace("</head>", f"{_DEMO_INJECT_HEAD}</head>", 1)
    html = html.replace("</body>", f"{_DEMO_INJECT_BODY}</body>", 1)
    return html


# ========== 生产模式底部入口 ==========
_PROD_FOOTER = """
<style>
  .fw-prod-footer{text-align:center;padding:28px 0 20px;color:var(--fwui-text-muted);font-size:12px;}
  .fw-prod-footer a{color:var(--fwui-primary);font-weight:600;text-decoration:none;margin-left:4px;}
  .fw-prod-footer a:hover{text-decoration:underline;}
</style>
<div class="fw-prod-footer">🎬 想先体验？<a href="/demo">进入演示模式</a></div>
"""

def _inject_prod_footer(html: str) -> str:
    """为生产模式页面注入底部演示模式入口"""
    html = html.replace("</main>", f"{_PROD_FOOTER}</main>", 1)
    return html


# ========== 页面路由 ==========
_PAGE_TEMPLATES = {
    "index": "web/templates/index.html",
    "detail": "web/templates/detail.html",
    "accounts": "web/templates/accounts.html",
    "accounts_tasks": "web/templates/accounts_tasks.html",
    "accounts_execution": "web/templates/accounts_execution.html",
    "follow": "web/templates/follow.html",
    "rental": "web/templates/rental.html",
    "admin": "web/templates/admin.html",
    "guide": "web/templates/guide.html",
    "profile": "web/templates/profile.html",
}

def _render_page_sync(name: str, demo: bool = False, initial_tab: str = "global") -> HTMLResponse:
    """同步读取模板并注入演示/生产模式标记（在线程池中调用）"""
    with open(_PAGE_TEMPLATES[name], encoding="utf-8") as f:
        html = f.read()
    if demo:
        html = _inject_demo(html)
    else:
        html = _inject_prod_footer(html)
    html = _inject_initial_tab(html, initial_tab)
    return HTMLResponse(html)


def _inject_initial_tab(html: str, initial_tab: str) -> str:
    """注入初始 Tab 状态脚本变量，供页面 JS 读取"""
    script = f"<script>window.__FW_INITIAL_TAB__='{initial_tab}'</script>"
    return html.replace("</head>", f"{script}</head>", 1)


async def _render_page(name: str, demo: bool = False, initial_tab: str = "global") -> HTMLResponse:
    """异步渲染页面：把同步文件 I/O 放到线程池中执行，避免阻塞事件循环"""
    import asyncio
    return await asyncio.to_thread(_render_page_sync, name, demo, initial_tab)


# --- 生产模式路由 ---
@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return await _render_page("index")

@app.get("/global", response_class=HTMLResponse)
async def global_ranking_page() -> HTMLResponse:
    """总榜单（独立 URL）"""
    return await _render_page("index", initial_tab="global")

@app.get("/my", response_class=HTMLResponse)
async def my_ranking_page() -> HTMLResponse:
    """我的榜单（独立 URL）"""
    return await _render_page("index", initial_tab="my")

@app.get("/detail", response_class=HTMLResponse)
async def detail_page() -> HTMLResponse:
    return await _render_page("detail")

@app.get("/accounts", response_class=HTMLResponse)
async def accounts_page() -> HTMLResponse:
    return await _render_page("accounts")

@app.get("/accounts/tasks", response_class=HTMLResponse)
async def accounts_tasks_page() -> HTMLResponse:
    """任务状态可视化页（独立路由）"""
    return await _render_page("accounts_tasks")

@app.get("/accounts/execution", response_class=HTMLResponse)
async def accounts_execution_page() -> HTMLResponse:
    """执行账号任务执行页（独立路由）"""
    return await _render_page("accounts_execution")

@app.get("/follow", response_class=HTMLResponse)
async def follow_page() -> HTMLResponse:
    return await _render_page("follow")

@app.get("/follow/my", response_class=HTMLResponse)
async def follow_my_page() -> HTMLResponse:
    """我的订阅（独立 URL）"""
    return await _render_page("follow", initial_tab="my")

@app.get("/rental", response_class=HTMLResponse)
async def rental_page() -> HTMLResponse:
    return await _render_page("rental")

@app.get("/admin", response_class=HTMLResponse)
async def admin_page() -> HTMLResponse:
    return await _render_page("admin")

@app.get("/profile", response_class=HTMLResponse)
async def profile_page() -> HTMLResponse:
    return await _render_page("profile")


# --- 演示模式路由（/demo/ 前缀，独立路由地址）---
@app.get("/demo", response_class=HTMLResponse)
async def demo_index() -> HTMLResponse:
    return await _render_page("index", demo=True)

@app.get("/demo/global", response_class=HTMLResponse)
async def demo_global_ranking_page() -> HTMLResponse:
    """演示模式总榜单（独立 URL）"""
    return await _render_page("index", demo=True, initial_tab="global")

@app.get("/demo/my", response_class=HTMLResponse)
async def demo_my_ranking_page() -> HTMLResponse:
    """演示模式我的榜单（独立 URL）"""
    return await _render_page("index", demo=True, initial_tab="my")

@app.get("/demo/detail", response_class=HTMLResponse)
async def demo_detail_page() -> HTMLResponse:
    return await _render_page("detail", demo=True)

@app.get("/demo/accounts", response_class=HTMLResponse)
async def demo_accounts_page() -> HTMLResponse:
    return await _render_page("accounts", demo=True)

@app.get("/demo/accounts/tasks", response_class=HTMLResponse)
async def demo_accounts_tasks_page() -> HTMLResponse:
    """演示模式任务状态页（独立路由）"""
    return await _render_page("accounts_tasks", demo=True)

@app.get("/demo/accounts/execution", response_class=HTMLResponse)
async def demo_accounts_execution_page() -> HTMLResponse:
    """演示模式执行账号任务执行页（独立路由）"""
    return await _render_page("accounts_execution", demo=True)

@app.get("/demo/follow", response_class=HTMLResponse)
async def demo_follow_page() -> HTMLResponse:
    return await _render_page("follow", demo=True)

@app.get("/demo/follow/my", response_class=HTMLResponse)
async def demo_follow_my_page() -> HTMLResponse:
    """演示模式我的订阅（独立 URL）"""
    return await _render_page("follow", demo=True, initial_tab="my")

@app.get("/demo/rental", response_class=HTMLResponse)
async def demo_rental_page() -> HTMLResponse:
    return await _render_page("rental", demo=True)

@app.get("/demo/admin", response_class=HTMLResponse)
async def demo_admin_page() -> HTMLResponse:
    return await _render_page("admin", demo=True)

@app.get("/demo/profile", response_class=HTMLResponse)
async def demo_profile_page() -> HTMLResponse:
    return await _render_page("profile", demo=True)

@app.get("/demo/guide", response_class=HTMLResponse)
async def demo_guide_page() -> HTMLResponse:
    return await _render_page("guide", demo=True)


@app.get("/api/info", response_model=dict)
async def info() -> dict:
    """API 信息（健康检查）"""
    return {
        "app": settings.APP_NAME,
        "version": "1.0.0",
        "status": "running",
        "is_demo": False,
        "trade_mode": settings.TRADE_MODE,
    }


@app.get("/api/demo/info", response_model=dict)
async def demo_info() -> dict:
    """演示模式 API 信息（WP-06：演示数据通道健康检查）"""
    return {
        "app": settings.APP_NAME,
        "version": "1.0.0",
        "status": "running",
        "is_demo": True,
        "trade_mode": settings.TRADE_MODE,
        "demo_db": settings.APP_DEMO_SQLITE_PATH,
    }


@app.get("/health")
async def health() -> dict:
    """健康检查端点（用于 Docker/K8s 探针）"""
    return {"status": "ok"}


@app.get("/api/demo/health")
async def demo_health() -> dict:
    """演示模式健康检查"""
    return {"status": "ok", "demo": True}


if __name__ == "__main__":
    import threading
    import time
    import socket
    import uvicorn
    import webbrowser

    def is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
        """检查端口是否已开放（服务是否启动）"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                result = s.connect_ex((host, port))
                return result == 0
        except Exception:
            return False

    def open_browser():
        """等待服务启动完成后打开浏览器"""
        host = "localhost" if settings.APP_HOST == "0.0.0.0" else settings.APP_HOST
        port = settings.APP_PORT
        max_wait = 30  # 最大等待30秒
        check_interval = 0.5  # 每0.5秒检查一次
        elapsed = 0

        while elapsed < max_wait:
            if is_port_open(host, port):
                url = f"http://{host}:{port}"
                webbrowser.open(url)
                return
            time.sleep(check_interval)
            elapsed += check_interval

    # 启动浏览器线程（仅在开发模式下）
    if settings.APP_DEBUG:
        threading.Thread(target=open_browser, daemon=True).start()

    uvicorn.run(
        "main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=settings.APP_DEBUG,
    )