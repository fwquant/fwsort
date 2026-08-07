"""策略管理路由 - 基于 .py 文件的纯文件驱动架构

所有 CRUD 操作直接操作 providers/ 目录下的 .py 文件，
不再依赖数据库中的 signal_provider_config 表。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from router.admin_router import require_admin
from fwsort.strategy.file_manager import (
    check_provider_references,
    create_provider,
    delete_provider,
    get_provider_detail,
    get_provider_parameters,
    hot_reload,
    list_all_providers,
    open_provider_file,
    run_health_check,
    test_provider,
    toggle_provider,
    update_provider_values,
)

router = APIRouter(tags=["signal-providers"])


@router.get("/api/signal-providers")
async def list_all(
    category: str | None = Query(default=None, description="internal / external / custom"),
):
    """查询策略列表（从文件扫描）"""
    providers = list_all_providers()
    if category:
        providers = [p for p in providers if p["category"] == category]
    return {"success": True, "data": providers, "message": "ok"}


@router.get("/api/signal-providers/active")
async def list_active():
    """获取所有启用的策略"""
    providers = list_all_providers()
    active = [p for p in providers if p.get("is_active", True)]
    return {"success": True, "data": active, "message": "ok"}


@router.get("/api/signal-providers/{provider_name}")
async def get_one(provider_name: str):
    """查询单个策略详情"""
    provider = get_provider_detail(provider_name)
    if not provider:
        raise HTTPException(status_code=404, detail="信号源不存在")
    return {"success": True, "data": provider, "message": "ok"}


@router.get("/api/signal-providers/{provider_name}/parameters")
async def get_parameters(provider_name: str):
    """查询策略的可见参数列表（供前端表单渲染）"""
    params = get_provider_parameters(provider_name)
    return {"success": True, "data": params, "message": "ok"}


@router.get("/api/signal-providers/{provider_name}/references")
async def get_references(provider_name: str):
    """检查策略被哪些任务引用"""
    refs = check_provider_references(provider_name)
    return {"success": True, "data": refs, "message": "ok"}


@router.post("/api/signal-providers/reload")
async def reload(_=Depends(require_admin)):
    """热加载：重新扫描 providers/ 目录"""
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


@router.post("/api/signal-providers")
async def create_one(data: dict, _=Depends(require_admin)):
    """创建策略（生成 .py 文件）

    Args:
        data: {
            provider_name, class_name?, category?, description?,
            source_type: "python" | "http_url",
            http_url?, config_json?
        }
    """
    try:
        source_type = data.get("source_type", "python")
        http_url = data.get("http_url") if source_type == "http_url" else None
        provider = create_provider(
            provider_name=data.get("provider_name", "").strip(),
            class_name=data.get("class_name", "").strip() or None,
            category=data.get("category", "custom"),
            description=data.get("description", ""),
            http_url=http_url,
            config_template=data.get("config_json"),
        )
        return {"success": True, "data": provider, "message": "信号源创建成功，已生成 .py 文件"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/api/signal-providers/{provider_name}")
async def update_one(provider_name: str, data: dict, _=Depends(require_admin)):
    """更新策略参数值（修改 .py 文件中的类属性默认值）

    Args:
        data: {values: {param_name: new_value, ...}}
    """
    values = data.get("values", {})
    if not values:
        raise HTTPException(status_code=400, detail="未提供参数值")
    try:
        updated = update_provider_values(provider_name, values)
        provider = get_provider_detail(provider_name)
        return {"success": True, "data": provider, "updated": list(updated.keys()), "message": "信号源参数已更新"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/signal-providers/{provider_name}/toggle")
async def toggle_active(provider_name: str, data: dict | None = None, _=Depends(require_admin)):
    """切换策略启用/停用状态（自动取反）
    
    如果请求体包含 is_active 字段则使用该值，否则自动取反当前状态。
    """
    data = data or {}
    is_active = data.get("is_active", None)
    try:
        result = toggle_provider(provider_name, is_active)
        new_state = result.get("is_active", False)
        return {"success": True, "data": result, "message": f"已{'启用' if new_state else '停用'}"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/signal-providers/{provider_name}")
async def delete_one(provider_name: str, _=Depends(require_admin)):
    """删除策略（删除 .py 文件）

    仅 custom 类别可删除；有任务引用时不可删除。
    """
    try:
        refs = check_provider_references(provider_name)
        if refs:
            task_names = ", ".join([f'"{r["task_name"]}"' for r in refs])
            raise HTTPException(
                status_code=400,
                detail=f"该信号源正在被 {len(refs)} 个任务使用: {task_names}。请先删除相关任务后再删除信号源",
            )
        ok = delete_provider(provider_name)
        if not ok:
            raise HTTPException(status_code=404, detail="信号源不存在")
        return {"success": True, "message": "信号源及 .py 文件已删除"}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/signal-providers/{provider_name}/health")
async def run_health(provider_name: str):
    """执行策略健康检查"""
    result = run_health_check(provider_name)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return {"success": True, "data": result, "message": "ok"}


@router.post("/api/signal-providers/{provider_name}/test")
async def test_provider_signal(provider_name: str):
    """测试信号生成"""
    result = test_provider(provider_name)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "测试失败"))
    return {"success": True, "data": result, "message": "ok"}


@router.post("/api/signal-providers/{provider_name}/open-file")
async def open_file(provider_name: str, _=Depends(require_admin)):
    """用默认 IDE 打开策略的 .py 文件"""
    result = open_provider_file(provider_name)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "打开失败"))
    return {"success": True, "data": result, "message": "已在 IDE 中打开文件"}
