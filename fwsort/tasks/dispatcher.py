"""内置自动任务调度器（不依赖 Celery）

当 FastAPI 启动时，启动一个后台线程：
1. 每秒钟检查所有活跃的自动任务
2. 根据每个任务的 interval 配置，到期后自动执行
3. 使用 Redis 记录上次执行时间戳，实现多任务独立调度
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from loguru import logger

from fwsort.database import get_sync_db
from fwsort.models import AutoTask
from fwsort.redis_client import sync_redis

# 调度器配置
DISPATCHER_KEY = "fwsort:auto_task:last_run"
CHECK_INTERVAL = 1.0  # 检查间隔（秒）：每 1 秒检查一次
MAX_CONCURRENT_EXECUTIONS = 10  # 最大并发执行数
HEARTBEAT_INTERVAL = 30  # 心跳日志间隔（秒）：每 30 秒输出一次状态

# 调度器状态
_dispatcher_thread: Optional[threading.Thread] = None
_dispatcher_stop_event = threading.Event()
_executing_tasks: set[int] = set()  # 正在执行的任务 ID 集合
_executing_lock = threading.Lock()
_last_heartbeat_time = 0  # 上次心跳时间


def _execute_task_in_thread(task_id: int) -> None:
    """在独立线程中执行单个任务"""
    from fwsort.tasks.service import execute_task

    try:
        result = execute_task(task_id)
        status = result.get("status", "unknown")
        logger.info(f"[AutoTaskDispatcher] ✓ task={task_id} executed, status={status}")
    except Exception as e:
        logger.error(f"[AutoTaskDispatcher] ✗ task={task_id} failed with error: {e}")
    finally:
        with _executing_lock:
            _executing_tasks.discard(task_id)
        logger.debug(f"[AutoTaskDispatcher] task={task_id} released from executing set")


def _dispatcher_loop() -> None:
    """调度器主循环：定期检查并触发到期任务"""
    global _last_heartbeat_time
    logger.info("[AutoTaskDispatcher] ✅ 内置自动任务调度器已启动")
    _last_heartbeat_time = time.time()

    while not _dispatcher_stop_event.is_set():
        try:
            _scan_and_dispatch()
        except Exception as e:
            logger.error(f"[AutoTaskDispatcher] dispatcher loop error: {e}")

        _dispatcher_stop_event.wait(CHECK_INTERVAL)

    logger.info("[AutoTaskDispatcher] ⛔ 内置自动任务调度器已停止")


def _scan_and_dispatch() -> None:
    """扫描活跃任务并触发到期的任务"""
    global _last_heartbeat_time
    now = int(time.time())
    current_time = time.time()

    # 心跳日志：每 HEARTBEAT_INTERVAL 秒输出一次
    task_count = 0
    if current_time - _last_heartbeat_time >= HEARTBEAT_INTERVAL:
        _last_heartbeat_time = current_time
        with _executing_lock:
            executing_count = len(_executing_tasks)
        
        try:
            with get_sync_db() as db:
                active_tasks = db.query(AutoTask).filter(
                    AutoTask.is_active == True,
                    AutoTask.deleted_at.is_(None),
                ).all()
                task_count = len(active_tasks)
        except Exception:
            pass
        
        logger.info(
            f"[AutoTaskDispatcher] 💓 心跳: 扫描到 {task_count} 个活跃任务, "
            f"执行中任务数={executing_count}"
        )

    try:
        with get_sync_db() as db:
            active_tasks = db.query(AutoTask).filter(
                AutoTask.is_active == True,
                AutoTask.deleted_at.is_(None),
            ).all()

            for task in active_tasks:
                try:
                    _process_single_task(task, now)
                except Exception as e:
                    logger.error(
                        f"[AutoTaskDispatcher] 处理任务 task={task.id} 时出错: {e}",
                        exc_info=True,
                    )
    except Exception as e:
        logger.error(f"[AutoTaskDispatcher] 扫描任务时发生错误: {e}", exc_info=True)


def _process_single_task(task: AutoTask, now: int) -> None:
    """处理单个任务的调度逻辑（独立异常保护）"""
    task_id = task.id

    # 跳过正在执行的任务（防止并发重复执行）
    with _executing_lock:
        if task_id in _executing_tasks:
            logger.debug(f"[AutoTaskDispatcher] 跳过正在执行的任务 task={task_id}")
            return

    # 读取上次执行时间
    last_run = 0
    try:
        raw = sync_redis.hget(DISPATCHER_KEY, str(task_id))
        if raw is not None:
            last_run = int(raw)
    except Exception as e:
        logger.warning(f"[AutoTaskDispatcher] Redis hget 失败 task={task_id}: {e}")

    interval_seconds = task.interval * 60
    elapsed = now - last_run

    # 首次执行或间隔到期
    if last_run == 0 or elapsed >= interval_seconds:
        reason = "首次执行" if last_run == 0 else f"间隔到期(elapsed={elapsed}s, interval={interval_seconds}s)"
        logger.info(
            f"[AutoTaskDispatcher] 🎯 触发任务 task={task_id} name={task.task_name} "
            f"interval={task.interval}min 原因: {reason}"
        )

        # 触发执行
        with _executing_lock:
            if len(_executing_tasks) >= MAX_CONCURRENT_EXECUTIONS:
                logger.warning(
                    f"[AutoTaskDispatcher] 最大并发执行数({MAX_CONCURRENT_EXECUTIONS})已达，跳过 task={task_id}"
                )
                return
            _executing_tasks.add(task_id)

        # 更新上次执行时间（即使执行失败也更新，防止立即重试风暴）
        try:
            sync_redis.hset(DISPATCHER_KEY, str(task_id), str(now))
            logger.debug(f"[AutoTaskDispatcher] Redis 更新 last_run task={task_id} -> {now}")
        except Exception as e:
            logger.warning(f"[AutoTaskDispatcher] Redis hset 失败 task={task_id}: {e}")

        # 在新线程中执行任务
        thread = threading.Thread(
            target=_execute_task_in_thread,
            args=(task_id,),
            daemon=True,
            name=f"auto-task-{task_id}",
        )
        thread.start()
        logger.info(f"[AutoTaskDispatcher] 🚀 任务 task={task_id} 已提交到执行线程")
    else:
        # 还未到执行时间
        remaining = interval_seconds - elapsed
        logger.debug(
            f"[AutoTaskDispatcher] ⏳ 任务 task={task_id} 未到期，还需 {remaining}s "
            f"(last_run={last_run}, interval={interval_seconds}s)"
        )


def start_dispatcher() -> None:
    """启动内置自动任务调度器（后台守护线程）"""
    global _dispatcher_thread

    if _dispatcher_thread and _dispatcher_thread.is_alive():
        logger.warning("[AutoTaskDispatcher] dispatcher already running")
        return

    _dispatcher_stop_event.clear()
    _dispatcher_thread = threading.Thread(
        target=_dispatcher_loop,
        daemon=True,
        name="auto-task-dispatcher",
    )
    _dispatcher_thread.start()
    logger.info("[AutoTaskDispatcher] dispatcher thread started")


def stop_dispatcher() -> None:
    """停止内置自动任务调度器"""
    if _dispatcher_thread and _dispatcher_thread.is_alive():
        _dispatcher_stop_event.set()
        _dispatcher_thread.join(timeout=5.0)
        logger.info("[AutoTaskDispatcher] dispatcher thread stopped")


def is_dispatcher_running() -> bool:
    """检查调度器是否在运行"""
    return _dispatcher_thread is not None and _dispatcher_thread.is_alive()


def get_dispatcher_status() -> dict:
    """获取调度器状态（供前端查询）"""
    return {
        "running": is_dispatcher_running(),
        "check_interval": CHECK_INTERVAL,
        "max_concurrent": MAX_CONCURRENT_EXECUTIONS,
        "executing_count": len(_executing_tasks),
        "executing_tasks": list(_executing_tasks),
    }
