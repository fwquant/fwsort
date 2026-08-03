"""自动任务路由"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from router.admin_router import require_admin
from fwsort.tasks.service import (
    create_task,
    delete_task,
    execute_task,
    get_task,
    get_task_log_count,
    get_task_logs,
    list_tasks,
    start_task,
    start_task_async,
    stop_task,
    update_task,
)
from fwsort.tasks.dispatcher import get_dispatcher_status, start_dispatcher, stop_dispatcher

router = APIRouter(tags=["tasks"])


@router.get("")
async def list_all_tasks(include_deleted: bool = Query(default=False)):
    """查询任务列表"""
    tasks = list_tasks(include_deleted=include_deleted)
    return {"success": True, "data": tasks, "message": "ok"}


@router.get("/dispatcher/status")
async def get_dispatcher_info():
    """查询自动任务调度器状态"""
    status = get_dispatcher_status()
    return {"success": True, "data": status, "message": "ok"}


@router.post("/dispatcher/start")
async def start_dispatcher_endpoint(_=Depends(require_admin)):
    """启动自动任务调度器"""
    start_dispatcher()
    status = get_dispatcher_status()
    return {"success": True, "data": status, "message": "调度器已启动"}


@router.post("/dispatcher/stop")
async def stop_dispatcher_endpoint(_=Depends(require_admin)):
    """停止自动任务调度器"""
    stop_dispatcher()
    status = get_dispatcher_status()
    return {"success": True, "data": status, "message": "调度器已停止"}


@router.post("")
async def create_new_task(data: dict, _=Depends(require_admin)):
    """创建任务"""
    task = create_task(data)
    return {"success": True, "data": task, "message": "任务创建成功"}


@router.get("/{task_id}")
async def get_single_task(task_id: int):
    """查询单个任务"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"success": True, "data": task, "message": "ok"}


@router.put("/{task_id}")
async def update_existing_task(task_id: int, data: dict, _=Depends(require_admin)):
    """更新任务"""
    task = update_task(task_id, data)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"success": True, "data": task, "message": "任务更新成功"}


@router.delete("/{task_id}")
async def delete_existing_task(task_id: int, _=Depends(require_admin)):
    """删除任务（软删除）"""
    try:
        ok = delete_task(task_id)
        if not ok:
            raise HTTPException(status_code=404, detail="任务不存在")
        return {"success": True, "message": "任务已删除"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{task_id}/start")
async def start_existing_task(task_id: int, _=Depends(require_admin)):
    """启用任务"""
    try:
        result = start_task(task_id)
        return {"success": True, "data": result, "message": result["message"]}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{task_id}/start-async")
async def start_existing_task_async(task_id: int, _=Depends(require_admin)):
    """启用任务（异步版本，返回详细进度）"""
    try:
        result = await start_task_async(task_id)
        return {"success": True, "data": result, "message": result["message"]}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{task_id}/stop")
async def stop_existing_task(task_id: int, _=Depends(require_admin)):
    """停止任务"""
    try:
        result = stop_task(task_id)
        return {"success": True, "data": result, "message": result["message"]}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{task_id}/execute")
async def execute_existing_task(task_id: int):
    """手动执行一次任务"""
    result = execute_task(task_id, manual=True)
    return {"success": True, "data": result, "message": "ok"}


@router.get("/{task_id}/logs")
async def get_task_execution_logs(
    task_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    log_type: int = Query(default=None, description="日志类型: None-全部 0-执行日志 1-操作日志"),
):
    """查询任务执行日志"""
    logs = get_task_logs(task_id, limit=limit, offset=offset, log_type=log_type)
    total = get_task_log_count(task_id, log_type=log_type)
    return {"success": True, "data": {"logs": logs, "total": total}, "message": "ok"}


@router.get("/signals/test")
async def test_signal_provider(provider: str = Query(default="random")):
    """测试信号生成"""
    from fwsort.signals import get_signal, list_providers

    if provider not in list_providers():
        raise HTTPException(status_code=400, detail=f"未知信号源: {provider}, 可用: {list_providers()}")

    signal = get_signal(provider)
    return {"success": True, "data": signal.to_dict(), "message": "ok"}


@router.post("/signals/push")
async def push_external_signal(data: dict):
    """推送外部信号到 HTTP 信号源"""
    from fwsort.signals import get_provider
    from fwsort.signals.providers.http_provider import HttpSignalProvider

    provider = get_provider("http")
    if not isinstance(provider, HttpSignalProvider):
        raise HTTPException(status_code=500, detail="HTTP 信号源未正确初始化")

    signal = await provider.push_signal(data)
    return {"success": True, "data": signal.to_dict(), "message": "信号推送成功"}