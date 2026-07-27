# FastAPI 入口：福纹排行榜（fwsort）V1.0
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
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
@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动：初始化 ES 索引；关闭：清理资源（ES 不可用时优雅降级）"""
    try:
        await ensure_order_log_index()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"ES init failed (ignored, will run without ES): {type(e).__name__}: {e}")
    logger.info(f"fwsort started | env={settings.APP_ENV} | mode={settings.TRADE_MODE}")
    yield
    try:
        await close_es_client()
    except Exception:  # noqa: BLE001
        pass
    logger.info("fwsort shutting down")


# ========== FastAPI 实例 ==========
app = FastAPI(
    title="FWQuant Ranking System",
    version="1.0.0",
    description="福纹排行榜：多智能体策略-订单执行规则 V1.0",
    lifespan=lifespan,
)

# CORS（前端跨域）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境需收敛
    allow_credentials=True,
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
                headers = list(message.get("headers", []))
                new_headers = []
                for k, v in headers:
                    k_str = k.decode("latin1") if isinstance(k, bytes) else k
                    if k_str.lower() == "content-type":
                        v_str = v.decode("latin1") if isinstance(v, bytes) else v
                        # 仅给 application/json 补 charset，其它资源（CSS/JS/图片等）原样保留
                        if v_str.startswith("application/json") and "charset" not in v_str.lower():
                            v_str = v_str + "; charset=utf-8"
                            v = v_str.encode("latin1") if isinstance(v, bytes) else v_str
                    new_headers.append((k, v))
                message["headers"] = new_headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(_UTF8JSONHeaderMiddleware)


# ========== 全局异常处理（业务异常）==========
@app.exception_handler(FwsortError)
async def fwsort_error_handler(_: Request, exc: FwsortError) -> JSONResponse:
    return JSONResponse(status_code=exc.code, content=fail(exc.message, code=exc.code))


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


# ========== 静态资源与前端页面 ==========
app.mount("/static", StaticFiles(directory="web/static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """首页：榜单列表"""
    with open("web/templates/index.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/detail", response_class=HTMLResponse)
async def detail_page() -> HTMLResponse:
    """策略详情页"""
    with open("web/templates/detail.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/accounts", response_class=HTMLResponse)
async def accounts_page() -> HTMLResponse:
    """我的执行账户页"""
    with open("web/templates/accounts.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/follow", response_class=HTMLResponse)
async def follow_page() -> HTMLResponse:
    """跟单管理页"""
    with open("web/templates/follow.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/rental", response_class=HTMLResponse)
async def rental_page() -> HTMLResponse:
    """智能体租用页"""
    with open("web/templates/rental.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/admin", response_class=HTMLResponse)
async def admin_page() -> HTMLResponse:
    """管理员控制台页"""
    with open("web/templates/admin.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/api/info", response_model=dict)
async def info() -> dict:
    """API 信息（健康检查）"""
    return {"app": settings.APP_NAME, "version": "1.0.0", "status": "running"}


@app.get("/health")
async def health() -> dict:
    """健康检查端点（用于 Docker/K8s 探针）"""
    return {"status": "ok"}


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