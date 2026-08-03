"""自动任务直接执行器（非 Celery 模式，用于开发/测试）"""
from __future__ import annotations

from loguru import logger

from fwsort.tasks.service import execute_task


def run_task_sync(task_id: int) -> dict:
    """同步执行一次任务（直接调用，不走 Celery）"""
    logger.info(f"[executor] running task {task_id} synchronously")
    return execute_task(task_id)