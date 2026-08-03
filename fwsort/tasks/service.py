"""自动任务服务层：CRUD + 业务逻辑

职责：
    - 任务的增删改查
    - 任务启停（初始化/销毁网关）
    - 风控检查
    - 日志记录
"""
from __future__ import annotations

import json
import time
import traceback
from datetime import datetime, timedelta
from decimal import Decimal



from fwsort.database import get_sync_db
from fwsort.fwlogs import logger
from fwsort.models import AutoTask, AutoTaskLog
from fwsort.redis_client import sync_redis
from fwsort.signals import get_signal

# 调度器 Redis Key
DISPATCHER_KEY = "fwsort:auto_task:last_run"

# 内存中存储已初始化的网关客户端
_gateway_instances: dict[int, object] = {}


def list_tasks(include_deleted: bool = False) -> list[dict]:
    """查询任务列表"""
    with get_sync_db() as db:
        query = db.query(AutoTask)
        if not include_deleted:
            query = query.filter(AutoTask.deleted_at.is_(None))
        tasks = query.order_by(AutoTask.id.desc()).all()
        return [_task_to_dict(t) for t in tasks]


def get_task(task_id: int) -> dict | None:
    """查询单个任务"""
    with get_sync_db() as db:
        task = db.query(AutoTask).filter(AutoTask.id == task_id).first()
        if not task:
            return None
        return _task_to_dict(task)


def create_task(data: dict) -> dict:
    """创建任务"""
    with get_sync_db() as db:
        task = AutoTask(
            task_name=data["task_name"],
            signal_source=data.get("signal_source", "random"),
            gateway=data.get("gateway", "polymarket_f3"),
            interval=data.get("interval", 5),
            is_active=False,
            max_daily_amount=data.get("max_daily_amount", 50.0),
            max_daily_count=data.get("max_daily_count", 50),
            max_consecutive_failures=data.get("max_consecutive_failures", 5),
            config_json=json.dumps(data.get("config_json", {})),
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        
        # 记录操作日志
        _add_operation_log(db, task.id, "create", 0, detail={
            "task_name": task.task_name,
            "signal_source": task.signal_source,
            "gateway": task.gateway,
            "interval": task.interval,
        })
        
        logger.info(f"[AutoTask] created task: {task.id} - {task.task_name}")
        return _task_to_dict(task)


def update_task(task_id: int, data: dict) -> dict | None:
    """更新任务"""
    with get_sync_db() as db:
        task = db.query(AutoTask).filter(AutoTask.id == task_id).first()
        if not task:
            return None

        updatable_fields = [
            "task_name", "signal_source", "gateway", "interval",
            "max_daily_amount", "max_daily_count", "max_consecutive_failures",
        ]
        changes = {}
        for field in updatable_fields:
            if field in data and data[field] is not None:
                old_val = getattr(task, field)
                new_val = data[field]
                if old_val != new_val:
                    changes[field] = {"old": old_val, "new": new_val}
                setattr(task, field, data[field])

        if "config_json" in data and data["config_json"] is not None:
            task.config_json = json.dumps(data["config_json"])
            changes["config_json"] = True

        db.commit()
        db.refresh(task)
        
        # 记录操作日志
        _add_operation_log(db, task.id, "update", 0, detail={
            "changes": changes,
        })
        
        logger.info(f"[AutoTask] updated task: {task.id}")
        return _task_to_dict(task)


def delete_task(task_id: int) -> bool:
    """软删除任务"""
    with get_sync_db() as db:
        task = db.query(AutoTask).filter(AutoTask.id == task_id).first()
        if not task:
            return False
        if task.is_active:
            raise ValueError("任务正在运行，请先停止再删除")
        task.deleted_at = datetime.utcnow()
        
        # 记录操作日志
        _add_operation_log(db, task_id, "delete", 0, detail={
            "task_name": task.task_name,
        })
        
        db.commit()
        logger.info(f"[AutoTask] deleted task: {task.id}")
        return True

# 启用 任务
def start_task(task_id: int) -> dict:
    """启用任务：初始化网关 + 标记为活跃（同步版本，保留向后兼容）"""
    import asyncio
    return asyncio.run(_start_task_internal(task_id))


async def start_task_async(task_id: int) -> dict:
    """启用任务：异步版本，支持实时进度回调"""
    return await _start_task_internal(task_id)


async def _start_task_internal(task_id: int) -> dict:
    """内部实现：初始化网关 + 标记为活跃"""
    progress_steps = []
    
    def add_progress(step: str, status: str = "pending"):
        progress_steps.append({"step": step, "status": status})
        logger.info(f"[AutoTask] [进度] {task_id}: {step} - {status}")

    add_progress("查询任务信息")
    
    with get_sync_db() as db:
        task = db.query(AutoTask).filter(AutoTask.id == task_id).first()
        if not task:
            raise ValueError(f"任务不存在: {task_id}")
        if task.is_active:
            raise ValueError("任务已在运行中")

        add_progress("准备初始化网关", "running")
        gateway_initialized = False
        error_msg = ""

        # 尝试初始化网关
        try:
            if task.gateway == "polymarket_f3":
                add_progress("导入 Polymarket F3 网关模块")
                from fwsort.gateway.polymarket.F3.最简类_下单代码 import pm类
                pm = pm类()
                
                add_progress("正在连接 Polymarket F3 网关...", "running")
                try:
                    await pm.初始化()
                    _gateway_instances[task.id] = pm
                    gateway_initialized = True
                    add_progress("Polymarket F3 网关连接成功！", "completed")
                except Exception as e1:
                    error_msg = str(e1)
                    add_progress(f"网关初始化失败: {error_msg}", "error")
                    logger.error(f"[AutoTask] 任务网关初始化失败 {task_id}: {e1},traceback={traceback.format_exc()}")
            else:
                add_progress(f"网关类型 '{task.gateway}' 不需要额外初始化", "completed")
                gateway_initialized = True
        except Exception as e:
            error_msg = str(e)
            add_progress(f"网关初始化异常: {error_msg}", "error")
            logger.error(f"[AutoTask] 任务网关初始化失败 {task_id}: {e},traceback={traceback.format_exc()}")

        add_progress("标记任务为活跃状态", "running")
        task.is_active = True
        db.commit()
        db.refresh(task)
        
        # 初始化 Redis 中的上次执行时间为当前时间，避免启动后立即触发
        try:
            sync_redis.hset(DISPATCHER_KEY, str(task.id), str(int(time.time())))
            logger.info(f"[AutoTask] 初始化 Redis last_run 为 task={task.id}")
        except Exception as e:
            logger.warning(f"[AutoTask] 初始化 Redis last_run 失败: {e}")
        
        add_progress("任务已启动", "completed")

        # 记录操作日志
        _add_operation_log(db, task.id, "start", 0 if gateway_initialized else 1, detail={
            "gateway_initialized": gateway_initialized,
            "error_message": error_msg if not gateway_initialized else "",
            "progress_steps": progress_steps,
        })

        result = {
            "task_id": task.id,
            "task_name": task.task_name,
            "is_active": task.is_active,
            "gateway_initialized": gateway_initialized,
            "message": "任务已启动" + ("，但网关初始化失败: " + error_msg if not gateway_initialized else ""),
            "progress": progress_steps,
        }
        logger.info(f"[AutoTask] started task: {task.id} gateway_ok={gateway_initialized}")
        return result


def stop_task(task_id: int) -> dict:
    """停止任务：释放网关 + 标记为不活跃 + 清理 Redis 记录"""
    with get_sync_db() as db:
        task = db.query(AutoTask).filter(AutoTask.id == task_id).first()
        if not task:
            raise ValueError(f"任务不存在: {task_id}")

        # 释放网关
        gateway_cleanup_ok = True
        gateway_error = ""
        if task.id in _gateway_instances:
            try:
                gateway = _gateway_instances.pop(task.id)
                if hasattr(gateway, '关闭对象'):
                    gateway.关闭对象()
            except Exception as e:
                gateway_cleanup_ok = False
                gateway_error = str(e)
                logger.warning(f"[AutoTask] gateway cleanup failed for task {task_id}: {e}")

        task.is_active = False
        db.commit()
        db.refresh(task)
        
        # 清理 Redis 中的调度记录
        try:
            sync_redis.hdel(DISPATCHER_KEY, str(task_id))
            logger.info(f"[AutoTask] 清理 Redis last_run 为 task={task_id}")
        except Exception as e:
            logger.warning(f"[AutoTask] 清理 Redis last_run 失败: {e}")

        # 记录操作日志
        _add_operation_log(db, task.id, "stop", 0 if gateway_cleanup_ok else 1, detail={
            "gateway_cleanup_ok": gateway_cleanup_ok,
            "gateway_error": gateway_error,
        })

        logger.info(f"[AutoTask] stopped task: {task.id}")
        return {
            "task_id": task.id,
            "task_name": task.task_name,
            "is_active": task.is_active,
            "message": "任务已停止",
        }

# 执行 任务（立即执行）
def execute_task(task_id: int, manual: bool = False) -> dict:
    """执行一次任务：获取信号 → 风控检查 → 下单 → 记录日志

    Args:
        task_id: 任务ID
        manual: 是否为手动触发（True时会额外记录操作日志）

    返回执行结果 dict。
    """
    start_time = time.time()
    signal = None
    order_result = None
    status = 0  # 成功
    error_message = ""
    order_id = ""
    retried = False

    with get_sync_db() as db:
        task = db.query(AutoTask).filter(AutoTask.id == task_id).first()
        if not task:
            # 记录操作日志（手动执行失败）
            if manual:
                _add_operation_log(db, task_id, "execute_manual", 1, detail={
                    "error": "任务不存在",
                })
            return {"status": "skipped", "message": "任务不存在"}
        
        # 手动执行允许未启用的任务（用于调试），自动调度必须是活跃任务
        if not task.is_active and not manual:
            return {"status": "skipped", "message": "任务已停止"}

        # ===== 风控检查 =====
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_logs = (
            db.query(AutoTaskLog)
            .filter(AutoTaskLog.task_id == task_id, AutoTaskLog.created_at >= today_start)
            .all()
        )

        # 检查连续失败熔断
        if task.consecutive_failures >= task.max_consecutive_failures:
            log_entry = AutoTaskLog(
                task_id=task_id,
                log_type=0,  # 执行日志
                executed_at=datetime.utcnow(),
                signal_json="{}",
                order_result_json=json.dumps({"error": "熔断触发"}),
                status=3,
                error_message=f"连续失败 {task.consecutive_failures} 次，触发熔断",
                duration_ms=int((time.time() - start_time) * 1000),
                order_id="",
                detail_json=json.dumps({"manual": manual}, ensure_ascii=False),
            )
            db.add(log_entry)
            task.consecutive_failures = 0  # 熔断后重置
            task.is_active = False  # 自动停止
            task.total_failed += 1
            task.total_executions += 1
            
            # 记录熔断操作日志
            _add_operation_log(db, task_id, "fuse_triggered", 1, detail={
                "consecutive_failures": task.consecutive_failures,
                "max_consecutive_failures": task.max_consecutive_failures,
            })
            
            db.commit()
            
            # 手动执行时记录操作日志
            if manual:
                _add_operation_log(db, task_id, "execute_manual", 1, detail={
                    "error": "熔断触发",
                })
            
            return {"status": "fuse_triggered", "message": "已触发熔断，任务自动停止"}

        # ===== 获取信号 =====
        try:
            signal = get_signal(task.signal_source)
        except Exception as e:
            status = 1
            error_message = f"信号获取失败: {e}"

        # ===== 下单（含重试一次）=====
        if status == 0 and signal:
            order_result, order_id, error_message, retried = _execute_order_with_retry(
                task=task,
                signal=signal,
                start_time=start_time,
            )
            if error_message:
                status = 1

        # ===== 更新统计 =====
        duration_ms = int((time.time() - start_time) * 1000)

        signal_json = signal.to_dict() if signal else {}
        order_result_json = order_result if order_result else {}

        if retried and status == 0:
            status = 2  # 已重试成功

        log_entry = AutoTaskLog(
            task_id=task_id,
            log_type=0,  # 执行日志
            executed_at=datetime.utcnow(),
            signal_json=json.dumps(signal_json, ensure_ascii=False),
            order_result_json=json.dumps(order_result_json, ensure_ascii=False) if isinstance(order_result_json, dict) else str(order_result_json),
            status=status,
            error_message=error_message or "",
            duration_ms=duration_ms,
            order_id=order_id,
            detail_json=json.dumps({"manual": manual}, ensure_ascii=False),
        )
        db.add(log_entry)

        task.total_executions += 1
        if status == 0 or status == 2:
            task.total_success += 1
            task.consecutive_failures = 0
        else:
            task.total_failed += 1
            task.consecutive_failures += 1

        db.commit()

        # 手动执行时记录操作日志
        if manual:
            _add_operation_log(db, task_id, "execute_manual", status, detail={
                "execution_status": status,
                "duration_ms": duration_ms,
                "order_id": order_id,
                "error_message": error_message,
            })

        result = {
            "task_id": task_id,
            "signal": signal_json,
            "order_result": order_result_json,
            "status": ["成功", "失败", "重试成功", "熔断"][status],
            "error": error_message,
            "duration_ms": duration_ms,
            "order_id": order_id,
        }
        logger.info(f"[AutoTask] executed task {task_id}: status={result['status']} duration={duration_ms}ms")
        return result


def _execute_order_with_retry(task: AutoTask, signal, start_time: float) -> tuple:
    """执行下单，失败重试一次

    Returns:
        tuple: (order_result, order_id, error_message, was_retried)
    """
    import asyncio

    order_result = None
    order_id = ""
    error_message = ""
    retried = False

    # 获取或初始化网关
    pm = _gateway_instances.get(task.id)
    if pm is None:
        from fwsort.gateway.polymarket.F3.最简类_下单代码 import pm类
        pm = pm类()
        _gateway_instances[task.id] = pm
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(pm.初始化())
        finally:
            loop.close()

    loop = asyncio.new_event_loop()
    try:
        # 第一次尝试
        direction = signal.direction
        amount = Decimal(str(signal.amount))
        result = loop.run_until_complete(
            pm.下单(
                标的代码=signal.symbol,
                outcome=direction,
                amount=amount,
            )
        )

        if result is None:
            error_message = "下单返回空结果"
            return order_result, order_id, error_message, retried

        # 检查是否被拒绝
        if hasattr(result, 'ok') and not result.ok:
            error_message = f"订单被拒绝: code={getattr(result, 'code', 'N/A')} msg={getattr(result, 'message', 'N/A')}"
            # 重试一次
            retried = True
            logger.warning(f"[AutoTask] order rejected, retrying once for task {task.id}")
            result = loop.run_until_complete(
                pm.下单(
                    标的代码=signal.symbol,
                    outcome=direction,
                    amount=amount,
                )
            )
            if result is None or (hasattr(result, 'ok') and not result.ok):
                error_message = f"重试后仍然失败: {error_message}"
                return order_result, order_id, error_message, retried

        order_result = {
            "ok": getattr(result, 'ok', True),
            "order_id": getattr(result, 'order_id', ''),
            "status": getattr(result, 'status', ''),
            "making_amount": str(getattr(result, 'making_amount', '')),
            "raw": str(result),
        }
        order_id = getattr(result, 'order_id', '')

    except Exception as e:
        error_message = f"下单异常: {e},traceback={traceback.format_exc()}"
        logger.error(f"[AutoTask] order execution error for task {task.id}: {error_message}")
    finally:
        loop.close()

    return order_result, order_id, error_message, retried


def get_task_logs(task_id: int, limit: int = 50, offset: int = 0, log_type: int | None = None) -> list[dict]:
    """查询任务执行日志"""
    with get_sync_db() as db:
        query = (
            db.query(AutoTaskLog)
            .filter(AutoTaskLog.task_id == task_id)
        )
        if log_type is not None:
            query = query.filter(AutoTaskLog.log_type == log_type)
        logs = (
            query
            .order_by(AutoTaskLog.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [_log_to_dict(l) for l in logs]


def get_task_log_count(task_id: int, log_type: int | None = None) -> int:
    """查询任务日志数量"""
    with get_sync_db() as db:
        query = db.query(AutoTaskLog).filter(AutoTaskLog.task_id == task_id)
        if log_type is not None:
            query = query.filter(AutoTaskLog.log_type == log_type)
        return query.count()


def _task_to_dict(task: AutoTask) -> dict:
    return {
        "id": task.id,
        "task_name": task.task_name,
        "signal_source": task.signal_source,
        "gateway": task.gateway,
        "interval": task.interval,
        "is_active": task.is_active,
        "max_daily_amount": float(task.max_daily_amount),
        "max_daily_count": task.max_daily_count,
        "max_consecutive_failures": task.max_consecutive_failures,
        "total_executions": task.total_executions,
        "total_success": task.total_success,
        "total_failed": task.total_failed,
        "consecutive_failures": task.consecutive_failures,
        "config_json": json.loads(task.config_json or "{}"),
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


def _log_to_dict(log: AutoTaskLog) -> dict:
    return {
        "id": log.id,
        "task_id": log.task_id,
        "log_type": log.log_type,
        "action_type": log.action_type,
        "executed_at": log.executed_at.isoformat() if log.executed_at else None,
        "signal_json": log.signal_json,
        "order_result_json": log.order_result_json,
        "status": log.status,
        "error_message": log.error_message,
        "duration_ms": log.duration_ms,
        "order_id": log.order_id,
        "detail_json": log.detail_json,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


def _add_operation_log(db, task_id: int, action_type: str, status: int, detail: dict | None = None):
    """添加操作日志

    Args:
        db: 数据库会话
        task_id: 任务ID
        action_type: 操作类型 (create/update/delete/start/stop/execute_manual/fuse_triggered/init_gateway)
        status: 状态 (0-成功 1-失败)
        detail: 操作详情
    """
    try:
        log_entry = AutoTaskLog(
            task_id=task_id,
            log_type=1,  # 操作日志
            action_type=action_type,
            executed_at=datetime.utcnow(),
            signal_json="{}",
            order_result_json="{}",
            status=status,
            error_message="",
            duration_ms=0,
            order_id="",
            detail_json=json.dumps(detail or {}, ensure_ascii=False),
        )
        db.add(log_entry)
        db.flush()  # 确保ID生成，但不提交事务
    except Exception as e:
        logger.error(f"[AutoTask] 记录操作日志失败: {e}, traceback={traceback.format_exc()}")