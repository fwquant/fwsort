"""信号提供者管理路由（Signal Provider CRUD + 热加载 + IDE打开 + 启用开关）"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from router.admin_router import require_admin
from fwsort.signals.config_service import (
    check_provider_references,
    create_signal_provider,
    delete_signal_provider,
    get_available_categories,
    get_signal_provider,
    hot_reload,
    list_active_signal_providers,
    list_signal_providers,
    open_provider_file,
    run_health_check,
    sync_builtin_providers,
    test_signal_provider,
    update_signal_provider,
)

router = APIRouter(tags=["signal-providers"])


@router.get("/api/signal-providers")
async def list_all(
    category: str | None = Query(default=None, description="internal / external / custom"),
    include_inactive: bool = Query(default=True),
):
    """查询信号提供者列表"""
    providers = list_signal_providers(category=category, include_inactive=include_inactive)
    return {"success": True, "data": providers, "message": "ok"}


@router.get("/api/signal-providers/active")
async def list_active():
    """获取所有启用的信号源（供任务选择信号来源）"""
    providers = list_active_signal_providers()
    return {"success": True, "data": providers, "message": "ok"}


@router.get("/api/signal-providers/categories")
async def list_categories():
    """获取所有可用的信号源类别枚举"""
    categories = get_available_categories()
    return {"success": True, "data": categories, "message": "ok"}


@router.get("/api/signal-providers/{provider_id}/references")
async def get_references(provider_id: int):
    """检查信号源被哪些任务引用"""
    refs = check_provider_references(provider_id)
    return {"success": True, "data": refs, "message": "ok"}


@router.post("/api/signal-providers/reload")
async def reload(_=Depends(require_admin)):
    """热加载：重新扫描 providers/ 目录，同步内置 + 检测新增/删除的 .py 文件"""
    result = hot_reload()
    added = len(result.get("new", []))
    removed = len(result.get("removed", []))
    total = result.get("total", 0)
    msg = f"热加载完成：共 {total} 个信号源"
    if added > 0:
        msg += f"，新增 {added} 个"
    if removed > 0:
        msg += f"，移除 {removed} 个"
    return {"success": True, "data": result, "message": msg}


@router.get("/api/signal-providers/{provider_id}")
async def get_one(provider_id: int):
    """查询单个信号提供者"""
    provider = get_signal_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="信号源不存在")
    return {"success": True, "data": provider, "message": "ok"}


@router.post("/api/signal-providers")
async def create_one(data: dict, _=Depends(require_admin)):
    """创建信号源（自动生成 .py 文件 + 注册到系统）"""
    try:
        provider = create_signal_provider(data)
        return {"success": True, "data": provider, "message": "信号源创建成功，已生成 .py 文件"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/api/signal-providers/{provider_id}")
async def update_one(provider_id: int, data: dict, _=Depends(require_admin)):
    """更新信号提供者"""
    provider = update_signal_provider(provider_id, data)
    if not provider:
        raise HTTPException(status_code=404, detail="信号源不存在")
    return {"success": True, "data": provider, "message": "信号源更新成功"}


@router.post("/api/signal-providers/{provider_id}/toggle")
async def toggle_active(provider_id: int, _=Depends(require_admin)):
    """切换信号源启用/停用状态"""
    from fwsort.database import get_sync_db
    from fwsort.models import SignalProviderConfig

    with get_sync_db() as db:
        config = db.query(SignalProviderConfig).filter(
            SignalProviderConfig.id == provider_id
        ).first()
        if not config:
            raise HTTPException(status_code=404, detail="信号源不存在")
        new_state = not config.is_active
        config.is_active = new_state
        provider_name = config.provider_name
        db.commit()

    # 如果停用，重置实例
    if not new_state:
        from fwsort.signals.manager import reset_provider_instance
        reset_provider_instance(provider_name)

    return {"success": True, "data": {"is_active": new_state}, "message": f"已{'启用' if new_state else '停用'}"}


@router.delete("/api/signal-providers/{provider_id}")
async def delete_one(provider_id: int, _=Depends(require_admin)):
    """删除信号源（同时删除 .py 文件）

    仅 custom 类别可删除；有任务引用时不可删除。
    """
    try:
        # 先检查引用
        refs = check_provider_references(provider_id)
        if refs:
            task_names = ", ".join([f'"{r["task_name"]}"' for r in refs])
            raise HTTPException(
                status_code=400,
                detail=f"该信号源正在被 {len(refs)} 个任务使用: {task_names}。请先删除相关任务后再删除信号源"
            )
        ok = delete_signal_provider(provider_id)
        if not ok:
            raise HTTPException(status_code=404, detail="信号源不存在")
        return {"success": True, "message": "信号源及 .py 文件已删除"}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/signal-providers/{provider_id}/health")
async def run_health(provider_id: int):
    """执行信号源健康检查"""
    result = run_health_check(provider_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return {"success": True, "data": result, "message": "ok"}


@router.post("/api/signal-providers/{provider_id}/test")
async def test_provider(provider_id: int):
    """测试信号生成"""
    result = test_signal_provider(provider_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return {"success": True, "data": result, "message": "ok"}


@router.post("/api/signal-providers/{provider_id}/open-file")
async def open_file(provider_id: int, _=Depends(require_admin)):
    """用默认 IDE 打开信号源的 .py 文件"""
    result = open_provider_file(provider_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "打开失败"))
    return {"success": True, "data": result, "message": "已在 IDE 中打开文件"}