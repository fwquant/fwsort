"""自动策略路由"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query

from router.admin_router import require_admin
from fwsort.strategy.service import (
    create_task,
    delete_task,
    execute_task,
    get_strategy_leaderboard,
    get_task,
    get_task_log_count,
    get_task_logs,
    list_all_task_logs,
    list_tasks,
    start_task,
    start_task_async,
    stop_task,
    update_settlement_for_task,
    update_task,
)
from fwsort.strategy.dispatcher import get_dispatcher_status, start_dispatcher, stop_dispatcher

router = APIRouter(tags=["tasks"])


# ========== 通用接口（无路径参数） ==========

@router.get("")
async def list_all_tasks(include_deleted: bool = Query(default=False)):
    """查询策略列表"""
    tasks = list_tasks(include_deleted=include_deleted)
    return {"success": True, "data": tasks, "message": "ok"}


@router.get("/leaderboard")
async def strategy_leaderboard(
    sort_by: str = Query(default="win_rate"),
    sort_dir: str = Query(default="desc"),
):
    """策略排行榜：按策略ID统计开仓次数、胜负、胜率"""
    data = get_strategy_leaderboard(sort_by=sort_by, sort_dir=sort_dir)
    return {"success": True, "data": data, "message": "ok"}


@router.get("/dispatcher/status")
async def get_dispatcher_info():
    """查询自动策略调度器状态"""
    status = get_dispatcher_status()
    return {"success": True, "data": status, "message": "ok"}


@router.post("/dispatcher/start")
async def start_dispatcher_endpoint(_=Depends(require_admin)):
    """启动自动策略调度器"""
    start_dispatcher()
    status = get_dispatcher_status()
    return {"success": True, "data": status, "message": "调度器已启动"}


@router.post("/dispatcher/stop")
async def stop_dispatcher_endpoint(_=Depends(require_admin)):
    """停止自动策略调度器"""
    stop_dispatcher()
    status = get_dispatcher_status()
    return {"success": True, "data": status, "message": "调度器已停止"}


# ========== 自动策略日志（全量查询，必须在 {task_id} 路由之前） ==========

@router.get("/logs", response_model=dict)
async def list_task_logs(
    search: str = Query(default="", description="搜索关键字（任务名/订单ID/错误信息/操作类型）"),
    status: int | None = Query(default=None, description="状态: 0-成功 1-失败 2-重试成功 3-熔断 4-无信号"),
    log_type: int | None = Query(default=None, description="日志类型: 0-执行日志 1-操作日志"),
    action_type: str | None = Query(default=None, description="操作类型: start/stop/create/update/delete/execute_manual等"),
    task_id: int | None = Query(default=None, description="按任务ID筛选"),
    pnl_only: bool = Query(default=False, description="只显示盈利日志"),
    date_from: str | None = Query(default=None, description="起始日期 YYYY-MM-DD"),
    date_to: str | None = Query(default=None, description="结束日期 YYYY-MM-DD"),
    sort_by: str = Query(default="executed_at", description="排序字段: id/executed_at/duration_ms/status/pnl_amount/task_id"),
    sort_dir: str = Query(default="desc", description="排序方向: asc/desc"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _admin=Depends(require_admin),
) -> dict:
    """查询全部自动策略日志（支持搜索/筛选/排序/分页）"""
    logs, total = list_all_task_logs(
        search=search,
        status=status,
        log_type=log_type,
        action_type=action_type,
        task_id=task_id,
        pnl_only=pnl_only,
        date_from=date_from,
        date_to=date_to,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )
    return {
        "success": True,
        "data": {
            "logs": logs,
            "total": total,
            "limit": limit,
            "offset": offset,
        },
        "message": "ok",
    }


@router.post("")
async def create_new_task(data: dict, _=Depends(require_admin)):
    """创建策略"""
    task = create_task(data)
    return {"success": True, "data": task, "message": "任务创建成功"}


@router.get("/signals/test")
async def test_signal_provider(provider: str = Query(default="random")):
    """测试信号生成"""
    from fwsort.strategy import get_signal, list_providers

    if provider not in list_providers():
        raise HTTPException(status_code=400, detail=f"未知信号源: {provider}, 可用: {list_providers()}")

    signal = get_signal(provider)
    return {"success": True, "data": signal.to_dict(), "message": "ok"}


@router.post("/signals/push")
async def push_external_signal(data: dict):
    """推送外部信号到 HTTP 信号源"""
    from fwsort.strategy import get_provider
    from fwsort.strategy.providers.http_strategy import HttpStrategy

    provider = get_provider("http")
    if not isinstance(provider, HttpStrategy):
        raise HTTPException(status_code=500, detail="HTTP 信号源未正确初始化")

    signal = await provider.push_signal(data)
    return {"success": True, "data": signal.to_dict(), "message": "信号推送成功"}


@router.get("/log-info")
async def get_log_info():
    """获取日志目录信息和日志文件列表"""
    from fwsort.fwlogs import logger as fw_logger
    if fw_logger is None:
        return {"success": True, "data": {"log_dir": None, "log_files": [], "message": "日志系统未初始化"}, "message": "ok"}

    log_dir = ""
    log_files = []
    current_log_path = ""
    error_log_path = ""

    try:
        log_dir = str(fw_logger._get_log_dir())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取日志目录失败: {e}")

    try:
        if hasattr(fw_logger, 'log_path'):
            current_log_path = str(fw_logger.log_path)
        if hasattr(fw_logger, 'error_log_path'):
            error_log_path = str(fw_logger.error_log_path)
    except Exception:
        pass

    try:
        if log_dir and os.path.isdir(log_dir):
            for fname in sorted(os.listdir(log_dir)):
                fpath = os.path.join(log_dir, fname)
                if os.path.isfile(fpath) and (fname.endswith('.log') or fname.endswith('.log.1')):
                    stat = os.stat(fpath)
                    log_files.append({
                        "name": fname,
                        "path": fpath,
                        "size": stat.st_size,
                        "size_human": _format_file_size(stat.st_size),
                        "mtime": int(stat.st_mtime),
                    })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取日志目录失败: {e}")

    return {
        "success": True,
        "data": {
            "log_dir": log_dir,
            "log_files": log_files,
            "current_log_path": current_log_path,
            "error_log_path": error_log_path,
            "db_table": "auto_strategy_log",
            "db_table_desc": "数据库策略日志表(auto_strategy_log)存储所有策略的执行日志和操作日志，可直接查询数据库查看完整历史",
        },
        "message": "ok",
    }


@router.post("/open-log-dir")
async def open_log_dir(_=Depends(require_admin)):
    """在系统默认文件管理器中打开日志目录（仅管理员）"""
    from fwsort.fwlogs import logger as fw_logger
    if fw_logger is None:
        raise HTTPException(status_code=400, detail="日志系统未初始化")

    log_dir = str(fw_logger._get_log_dir())
    if not os.path.isdir(log_dir):
        raise HTTPException(status_code=400, detail=f"日志目录不存在: {log_dir}")

    try:
        if os.name == 'nt':
            os.startfile(log_dir)  # type: ignore[attr-defined]
        elif os.uname().sysname == 'Darwin':  # type: ignore[attr-defined]
            os.system(f'open "{log_dir}"')
        else:
            os.system(f'xdg-open "{log_dir}"')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"打开目录失败: {e}")

    return {"success": True, "message": f"已打开日志目录: {log_dir}"}


# ========== 带路径参数接口（必须在通用接口之后定义，否则 /log-info 等被 {task_id} 拦截） ==========

@router.get("/{task_id}")
async def get_single_task(task_id: int):
    """查询单个策略"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"success": True, "data": task, "message": "ok"}


@router.put("/{task_id}")
async def update_existing_task(task_id: int, data: dict, _=Depends(require_admin)):
    """更新策略"""
    task = update_task(task_id, data)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"success": True, "data": task, "message": "任务更新成功"}


@router.delete("/{task_id}")
async def delete_existing_task(task_id: int, _=Depends(require_admin)):
    """删除策略（软删除）"""
    try:
        ok = delete_task(task_id)
        if not ok:
            raise HTTPException(status_code=404, detail="任务不存在")
        return {"success": True, "message": "任务已删除"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{task_id}/start")
async def start_existing_task(task_id: int, _=Depends(require_admin)):
    """启用策略"""
    try:
        result = await start_task_async(task_id)
        return {"success": True, "data": result, "message": result["message"]}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{task_id}/start-async")
async def start_existing_task_async(task_id: int, _=Depends(require_admin)):
    """启用策略（异步版本，返回详细进度）"""
    try:
        result = await start_task_async(task_id)
        return {"success": True, "data": result, "message": result["message"]}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{task_id}/stop")
async def stop_existing_task(task_id: int, _=Depends(require_admin)):
    """停止策略"""
    try:
        result = stop_task(task_id)
        return {"success": True, "data": result, "message": result["message"]}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{task_id}/execute")
async def execute_existing_task(task_id: int):
    """手动执行一次策略"""
    result = execute_task(task_id, manual=True)
    return {"success": True, "data": result, "message": "ok"}


@router.get("/{task_id}/logs")
async def get_task_execution_logs(
    task_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    log_type: int = Query(default=None, description="日志类型: None-全部 0-执行日志 1-操作日志"),
):
    """查询策略执行日志"""
    logs = get_task_logs(task_id, limit=limit, offset=offset, log_type=log_type)
    total = get_task_log_count(task_id, log_type=log_type)
    return {"success": True, "data": {"logs": logs, "total": total}, "message": "ok"}


@router.post("/{task_id}/update-settlement")
async def update_task_settlement(
    task_id: int,
    _admin=Depends(require_admin),
) -> dict:
    """手动更新任务的结算方向（回查所有未结算交易）

    扫描该任务所有 market_resolved=False 的执行日志，
    逐个查询 Polymarket 市场结算状态，若已结算则更新盈亏和结算方向。
    """
    try:
        result = update_settlement_for_task(task_id)
        return {"success": True, "data": result, "message": result.get("message", "ok")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"结算回查失败: {e}")


def _format_file_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    return f"{size / 1024 / 1024 / 1024:.2f} GB"
