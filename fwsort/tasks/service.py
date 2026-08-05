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
import uuid
from datetime import datetime, timedelta, date
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


def _json_default(obj):
    """JSON 序列化兜底：处理 Decimal / datetime / UUID / ORM 标量等不可序列化类型"""
    if isinstance(obj, Decimal):
        # 保留精度优先用 str；如需数值用 float
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, set):
        return list(obj)
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8")
        except UnicodeDecodeError:
            return obj.hex()
    # SQLAlchemy 标量属性兜底
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "__str__"):
        try:
            return str(obj)
        except Exception:  # noqa: BLE001
            pass
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def _safe_dumps(obj) -> str:
    """安全 json.dumps：处理 Decimal/datetime 等不可序列化类型"""
    return json.dumps(obj, ensure_ascii=False, default=_json_default)


def list_tasks(include_deleted: bool = False) -> list[dict]:
    """查询任务列表"""
    with get_sync_db() as db:
        query = db.query(AutoTask)
        if not include_deleted:
            query = query.filter(AutoTask.deleted_at.is_(None))
        tasks = query.order_by(AutoTask.id.desc()).all()
        result = []
        for t in tasks:
            d = _task_to_dict(t)
            _enrich_countdown(d)
            result.append(d)
        return result


def get_task(task_id: int) -> dict | None:
    """查询单个任务"""
    with get_sync_db() as db:
        task = db.query(AutoTask).filter(AutoTask.id == task_id).first()
        if not task:
            return None
        d = _task_to_dict(task)
        _enrich_countdown(d)
        return d


def create_task(data: dict) -> dict:
    """创建任务"""
    start_time = data.get("start_time")
    if start_time and isinstance(start_time, str):
        from datetime import datetime as _dt
        try:
            start_time = _dt.fromisoformat(start_time.replace("Z", "+00:00"))
        except Exception:
            start_time = None

    with get_sync_db() as db:
        task = AutoTask(
            task_name=data["task_name"],
            signal_source=data.get("signal_source", "random"),
            gateway=data.get("gateway", "polymarket_f3"),
            interval=data.get("interval", 5),
            is_active=False,
            start_time=start_time,
            loop_count=data.get("loop_count", 0),
            executed_count=0,
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
            "loop_count",
        ]
        changes = {}
        for field in updatable_fields:
            if field in data and data[field] is not None:
                old_val = getattr(task, field)
                new_val = data[field]
                if old_val != new_val:
                    changes[field] = {"old": old_val, "new": new_val}
                setattr(task, field, data[field])

        # 处理 start_time（特殊处理 datetime 类型）
        if "start_time" in data:
            st = data["start_time"]
            if not st:
                if task.start_time is not None:
                    changes["start_time"] = {"old": str(task.start_time), "new": None}
                task.start_time = None
            elif isinstance(st, str):
                from datetime import datetime as _dt
                try:
                    new_st = _dt.fromisoformat(st.replace("Z", "+00:00"))
                    if task.start_time != new_st:
                        changes["start_time"] = {"old": str(task.start_time), "new": str(new_st)}
                    task.start_time = new_st
                except Exception:
                    pass
            elif hasattr(st, 'year'):
                if task.start_time != st:
                    changes["start_time"] = {"old": str(task.start_time), "new": str(st)}
                task.start_time = st

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
    """内部实现：初始化网关 + 标记为活跃

    核心逻辑：网关初始化失败则阻止任务启动，避免任务处于活跃但无法执行的状态。
    这相当于自动执行了 /polymarket?tab=status 的"初始化连接"功能。
    """
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

                # 先重新加载 .env 配置，确保最新的密钥被加载
                from fwsort.config import reload_env
                reload_env()
                logger.info(f"[AutoTask] 重新加载 .env 配置完成")

                # 检查必要配置是否已就绪
                from fwsort.config import settings
                missing_configs = []
                if not settings.POLYMARKET_RELAYER_API_KEY:
                    missing_configs.append("POLYMARKET_RELAYER_API_KEY")
                if not settings.POLYMARKET_RELAYER_API_KEY_ADDRESS:
                    missing_configs.append("POLYMARKET_RELAYER_API_KEY_ADDRESS")
                if not settings.POLYMARKET_RELAYER_PRIVATE_KEY:
                    missing_configs.append("POLYMARKET_RELAYER_PRIVATE_KEY")

                if missing_configs:
                    error_msg = f"缺少必要配置: {', '.join(missing_configs)}，请在 .env 文件中配置"
                    add_progress(error_msg, "error")
                    logger.error(f"[AutoTask] 任务启动失败 {task_id}: {error_msg}")
                    raise ValueError(error_msg)

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
                    raise ValueError(f"Polymarket F3 网关初始化失败: {error_msg}")
            else:
                add_progress(f"网关类型 '{task.gateway}' 不需要额外初始化", "completed")
                gateway_initialized = True
        except ValueError:
            raise
        except Exception as e:
            error_msg = str(e)
            add_progress(f"网关初始化异常: {error_msg}", "error")
            logger.error(f"[AutoTask] 任务网关初始化失败 {task_id}: {e},traceback={traceback.format_exc()}")
            raise ValueError(f"网关初始化异常: {error_msg}")

        # 网关初始化成功，标记任务为活跃
        add_progress("标记任务为活跃状态", "running")
        task.is_active = True
        task.executed_count = 0  # 重置执行次数
        db.commit()
        db.refresh(task)

        # 初始化 Redis 中的上次执行时间
        now_ts = int(time.time())
        if task.start_time:
            # 如果设置了开始时间，将 last_run 设为 start_time 对应的时间戳
            # 这样 dispatcher 会在 start_time 之后才触发首次执行
            start_ts = int(task.start_time.timestamp())
            initial_last_run = start_ts - task.interval * 60  # 让首次触发在 start_time
            add_progress(f"首次执行时间: {task.start_time.strftime('%Y-%m-%d %H:%M:%S')}", "completed")
        else:
            initial_last_run = now_ts
            add_progress("启用后立即开始执行", "completed")

        try:
            sync_redis.hset(DISPATCHER_KEY, str(task.id), str(initial_last_run))
            logger.info(f"[AutoTask] 初始化 Redis last_run 为 task={task.id}, initial={initial_last_run}")
        except Exception as e:
            logger.warning(f"[AutoTask] 初始化 Redis last_run 失败: {e}")

        add_progress("任务已启动", "completed")

        # 记录操作日志（网关初始化成功，status=0）
        _add_operation_log(db, task.id, "start", 0, detail={
            "gateway_initialized": True,
            "progress_steps": progress_steps,
        })

        result = {
            "task_id": task.id,
            "task_name": task.task_name,
            "is_active": task.is_active,
            "gateway_initialized": True,
            "message": "任务已启动，Polymarket F3 网关连接成功",
            "progress": progress_steps,
        }
        logger.info(f"[AutoTask] started task: {task.id} gateway_ok=True")
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
    """执行一次任务：回查上笔盈亏 → 获取信号 → 风控检查 → 下单 → 记录日志

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
    signal_detail = {}
    execution_detail = {}
    result_detail = {}
    pnl_amount = 0.0
    pnl_percent = 0.0
    is_profit = False
    market_resolved = False
    pnl_check_result = None  # 上一笔交易盈亏回查结果

    with get_sync_db() as db:
        task = db.query(AutoTask).filter(AutoTask.id == task_id).first()
        if not task:
            if manual:
                _add_operation_log(db, task_id, "execute_manual", 1, detail={
                    "error": "任务不存在",
                })
            return {"status": "skipped", "message": "任务不存在"}

        if not task.is_active and not manual:
            return {"status": "skipped", "message": "任务已停止"}

        # ===== 回查上一笔未结算交易的盈亏 =====
        try:
            pnl_check_result = _check_and_update_previous_pnl(db, task)
            if pnl_check_result:
                logger.info(
                    f"[AutoTask] 💰 任务 {task_id} 回查上一笔交易: "
                    f"{'盈利' if pnl_check_result.get('is_profit') else '亏损'} "
                    f"${pnl_check_result.get('pnl_amount', 0):.4f} "
                    f"({pnl_check_result.get('pnl_percent', 0):.2f}%)"
                )
        except Exception as e:
            logger.warning(f"[AutoTask] 任务 {task_id} 盈亏回查异常(不影响主流程): {e}")
            pnl_check_result = None

        # ===== 自动赎回已结算持仓 =====
        redeem_result = None
        if task.gateway == "polymarket_f3":
            try:
                redeem_result = _auto_redeem_resolved_positions(task)
                if redeem_result and redeem_result.get("redeemed_count", 0) > 0:
                    logger.info(
                        f"[AutoTask] 🔄 任务 {task_id} 自动赎回: "
                        f"{redeem_result.get('redeemed_count')} 个持仓已赎回"
                    )
                elif redeem_result:
                    logger.debug(
                        f"[AutoTask] 任务 {task_id} 自动赎回: 无待赎回持仓"
                    )
            except Exception as e:
                logger.warning(
                    f"[AutoTask] 任务 {task_id} 自动赎回异常(不影响主流程): {e}"
                )
                redeem_result = None

        # ===== 风控检查 =====
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_logs = (
            db.query(AutoTaskLog)
            .filter(AutoTaskLog.task_id == task_id, AutoTaskLog.created_at >= today_start)
            .all()
        )

        today_total_amount = 0.0
        today_total_count = len(today_logs)
        for log in today_logs:
            try:
                detail = json.loads(log.detail_json or "{}")
                today_total_amount += float(detail.get("making_amount", 0))
            except Exception:
                pass

        if today_total_count >= task.max_daily_count:
            status = 1
            error_message = f"已达每日最大执行次数({task.max_daily_count}次)"
            logger.warning(f"[AutoTask] 任务 {task_id} 风控: {error_message}")

        if status == 0 and today_total_amount >= task.max_daily_amount:
            status = 1
            error_message = f"已达每日最大执行金额(${task.max_daily_amount:.2f})"
            logger.warning(f"[AutoTask] 任务 {task_id} 风控: {error_message}")

        # ===== 连续失败熔断检查 =====
        if task.consecutive_failures >= task.max_consecutive_failures:
            log_entry = AutoTaskLog(
                task_id=task_id,
                log_type=0,
                executed_at=datetime.utcnow(),
                signal_json="{}",
                order_result_json=json.dumps({"error": "熔断触发"}),
                status=3,
                error_message=f"连续失败 {task.consecutive_failures} 次，触发熔断",
                duration_ms=int((time.time() - start_time) * 1000),
                order_id="",
                detail_json=_safe_dumps({"manual": manual}),
                signal_detail_json="{}",
                execution_detail_json=_safe_dumps({"stage": "fuse_check"}),
                result_detail_json="{}",
                pnl_amount=0.0,
                pnl_percent=0.0,
                is_profit=False,
                market_resolved=False,
            )
            db.add(log_entry)
            task.consecutive_failures = 0
            task.is_active = False
            task.total_failed += 1
            task.total_executions += 1

            _add_operation_log(db, task_id, "fuse_triggered", 1, detail={
                "consecutive_failures": task.consecutive_failures,
                "max_consecutive_failures": task.max_consecutive_failures,
            })

            db.commit()

            if manual:
                _add_operation_log(db, task_id, "execute_manual", 1, detail={
                    "error": "熔断触发",
                })

            return {"status": "fuse_triggered", "message": "已触发熔断，任务自动停止"}

        # ===== 获取信号 =====
        signal = None
        if status == 0:
            try:
                signal = get_signal(task.signal_source)
                signal_dict = signal.to_dict() if signal else {}
                # symbol 即 Polymarket 的 market_slug
                symbol = signal_dict.get("symbol", "")
                signal_detail = {
                    "symbol": symbol,
                    "direction": signal_dict.get("direction", ""),
                    "amount": signal_dict.get("amount", 0),
                    "source": signal_dict.get("source", task.signal_source),
                    "market_id": signal_dict.get("market_id", ""),
                    "market_slug": signal_dict.get("market_slug") or symbol,  # symbol 即 market_slug
                    "market_question": signal_dict.get("market_question", ""),
                    "timestamp": datetime.utcnow().isoformat(),
                }
                logger.info(
                    f"[AutoTask] 📡 任务 {task_id} 信号获取成功: "
                    f"symbol={signal_detail['symbol']} direction={signal_detail['direction']} "
                    f"amount={signal_detail['amount']} source={signal_detail['source']}"
                )
            except Exception as e:
                status = 1
                error_message = f"信号获取失败: {e}"
                logger.error(f"[AutoTask] ❌ 任务 {task_id} 信号获取失败: {e}")

        # ===== 下单（含重试一次）=====
        if status == 0 and signal:
            try:
                order_result, order_id, error_message, retried, pm_result, signal = _execute_order_with_retry(
                    task=task,
                    signal=signal,
                    start_time=start_time,
                )

                if pm_result:
                    execution_detail = {
                        "gateway": task.gateway,
                        "side": getattr(pm_result, 'side', 'BUY'),
                        "order_type": getattr(pm_result, 'order_type', 'market'),
                        "market_question": getattr(pm_result, 'market_question', ''),
                        "making_amount": str(getattr(pm_result, 'making_amount', '')),
                        "taking_amount": str(getattr(pm_result, 'taking_amount', '')),
                        "retried": retried,
                    }

                    result_detail = {
                        "order_id": getattr(pm_result, 'order_id', ''),
                        "status": getattr(pm_result, 'status', ''),
                        "ok": getattr(pm_result, 'ok', True),
                        "trade_ids": list(getattr(pm_result, 'trade_ids', ())),
                        "transactions_hashes": list(getattr(pm_result, 'transactions_hashes', ())),
                        "raw": str(pm_result),
                    }

                    logger.info(
                        f"[AutoTask] {'🔄 重试成功' if retried else '✅ 下单成功'} "
                        f"task={task_id} order_id={order_id} "
                        f"status={result_detail['status']} "
                        f"making={execution_detail.get('making_amount', 'N/A')} "
                        f"taking={execution_detail.get('taking_amount', 'N/A')}"
                    )
                else:
                    result_detail = {"error": error_message}
                    logger.error(f"[AutoTask] ❌ 任务 {task_id} 下单失败: {error_message}")

                if error_message:
                    status = 1
            except Exception as e:
                status = 1
                error_message = f"下单异常: {e}"
                logger.error(f"[AutoTask] ❌ 任务 {task_id} 下单异常: {e}")

        # ===== 更新统计 =====
        duration_ms = int((time.time() - start_time) * 1000)

        signal_json = signal.to_dict() if signal else {}
        order_result_json = order_result if order_result else {}

        if retried and status == 0:
            status = 2

        log_entry = AutoTaskLog(
            task_id=task_id,
            log_type=0,
            executed_at=datetime.utcnow(),
            signal_json=_safe_dumps(signal_json),
            order_result_json=_safe_dumps(order_result_json) if isinstance(order_result_json,
                                                                              dict) else str(
                order_result_json),
            status=status,
            error_message=error_message or "",
            duration_ms=duration_ms,
            order_id=order_id,
            detail_json=_safe_dumps({"manual": manual}),
            signal_detail_json=_safe_dumps(signal_detail),
            execution_detail_json=_safe_dumps(execution_detail),
            result_detail_json=_safe_dumps(result_detail),
            pnl_amount=pnl_amount,
            pnl_percent=pnl_percent,
            is_profit=is_profit,
            market_resolved=market_resolved,
        )
        db.add(log_entry)

        task.total_executions += 1
        task.executed_count += 1
        if status == 0 or status == 2:
            task.total_success += 1
            task.consecutive_failures = 0
        else:
            task.total_failed += 1
            task.consecutive_failures += 1

        loop_auto_stopped = False
        if task.loop_count > 0 and task.executed_count >= task.loop_count:
            task.is_active = False
            loop_auto_stopped = True
            _add_operation_log(db, task_id, "loop_completed", 0, detail={
                "executed_count": task.executed_count,
                "loop_count": task.loop_count,
            })
            logger.info(
                f"[AutoTask] task {task_id} loop completed ({task.executed_count}/{task.loop_count}), auto-stopped")

        db.commit()

        if manual:
            _add_operation_log(db, task_id, "execute_manual", status, detail={
                "execution_status": status,
                "duration_ms": duration_ms,
                "order_id": order_id,
                "error_message": error_message,
                "signal_detail": signal_detail,
                "execution_detail": execution_detail,
            })

        result = {
            "task_id": task_id,
            "signal": signal_json,
            "signal_detail": signal_detail,
            "execution_detail": execution_detail,
            "result_detail": result_detail,
            "order_result": order_result_json,
            "status": ["成功", "失败", "重试成功", "熔断"][status],
            "error": error_message,
            "duration_ms": duration_ms,
            "order_id": order_id,
            "executed_count": task.executed_count,
            "loop_completed": loop_auto_stopped,
            "pnl_amount": pnl_amount,
            "pnl_percent": pnl_percent,
            "is_profit": is_profit,
        }
        logger.info(
            f"[AutoTask] 🏁 executed task {task_id}: status={result['status']} "
            f"duration={duration_ms}ms signal={signal_detail.get('symbol', 'N/A')} "
            f"dir={signal_detail.get('direction', 'N/A')} amt={signal_detail.get('amount', 'N/A')}"
        )
        return result


def _execute_order_with_retry(task: AutoTask, signal, start_time: float) -> tuple:
    """执行下单，失败重试一次

    Returns:
        tuple: (order_result, order_id, error_message, was_retried, pm_result_trimmed, updated_signal)
    """
    import asyncio

    order_result = None
    order_id = ""
    error_message = ""
    retried = False
    pm_result_trimmed = None

    pm = _gateway_instances.get(task.id)
    if pm is None:
        from fwsort.config import reload_env
        reload_env()
        logger.info(f"[AutoTask] 执行前重新加载 .env 配置完成")

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
        direction = signal.direction
        amount = Decimal(str(signal.amount))

        logger.info(
            f"[AutoTask] 📤 准备下单: task={task.id} symbol={signal.symbol} "
            f"direction={direction} amount={amount} gateway={task.gateway}"
        )

        result = loop.run_until_complete(
            pm.下单(
                标的代码=signal.symbol,
                outcome=direction,
                amount=amount,
            )
        )

        if result is None:
            error_message = "下单返回空结果"
            return order_result, order_id, error_message, retried, pm_result_trimmed, signal

        if hasattr(result, 'ok') and not result.ok:
            error_message = f"订单被拒绝: code={getattr(result, 'code', 'N/A')} msg={getattr(result, 'message', 'N/A')}"
            retried = True
            logger.warning(f"[AutoTask] ⚠️ 订单被拒绝，重试一次: {error_message}")
            result = loop.run_until_complete(
                pm.下单(
                    标的代码=signal.symbol,
                    outcome=direction,
                    amount=amount,
                )
            )
            if result is None or (hasattr(result, 'ok') and not result.ok):
                error_message = f"重试后仍然失败: {error_message}"
                logger.error(f"[AutoTask] ❌ 重试仍然失败: {error_message}")
                return order_result, order_id, error_message, retried, pm_result_trimmed, signal

        order_result = {
            "ok": getattr(result, 'ok', True),
            "order_id": getattr(result, 'order_id', ''),
            "status": getattr(result, 'status', ''),
            "making_amount": str(getattr(result, 'making_amount', '')),
            "raw": str(result),
        }
        order_id = getattr(result, 'order_id', '')

        # 提取关键信息到 pm_result_trimmed（避免存入过多数据）
        pm_result_trimmed = type('TrimmedResult', (), {
            'ok': getattr(result, 'ok', True),
            'order_id': getattr(result, 'order_id', ''),
            'status': getattr(result, 'status', ''),
            'making_amount': getattr(result, 'making_amount', Decimal('0')),
            'taking_amount': getattr(result, 'taking_amount', Decimal('0')),
            'trade_ids': getattr(result, 'trade_ids', ()),
            'transactions_hashes': getattr(result, 'transactions_hashes', ()),
            'side': getattr(result, 'side', 'BUY'),
            'order_type': getattr(result, 'order_type', 'market'),
            'market_question': getattr(result, 'market_question', ''),
        })()

    except Exception as e:
        error_message = f"下单异常: {e},traceback={traceback.format_exc()}"
        logger.error(f"[AutoTask] ❌ order execution error for task {task.id}: {error_message}")
    finally:
        loop.close()

    return order_result, order_id, error_message, retried, pm_result_trimmed, signal


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


def list_all_task_logs(
        search: str = "",
        status: int | None = None,
        log_type: int | None = None,
        action_type: str | None = None,
        task_id: int | None = None,
        pnl_only: bool = False,
        date_from: str | None = None,
        date_to: str | None = None,
        sort_by: str = "id",
        sort_dir: str = "desc",
        limit: int = 50,
        offset: int = 0,
) -> tuple[list[dict], int]:
    """查询全部任务日志（支持搜索/筛选/排序/分页）

    Returns:
        (logs_list, total_count)
    """
    from sqlalchemy import or_ as sa_or_

    with get_sync_db() as db:
        query = db.query(AutoTaskLog)

        # 搜索
        if search:
            like = f"%{search}%"
            query = query.outerjoin(AutoTask, AutoTaskLog.task_id == AutoTask.id).filter(
                sa_or_(
                    AutoTaskLog.error_message.ilike(like),
                    AutoTaskLog.order_id.ilike(like),
                    AutoTaskLog.action_type.ilike(like),
                    AutoTask.task_name.ilike(like),
                )
            )

        # 筛选
        if status is not None:
            query = query.filter(AutoTaskLog.status == status)
        if log_type is not None:
            query = query.filter(AutoTaskLog.log_type == log_type)
        if action_type:
            query = query.filter(AutoTaskLog.action_type == action_type)
        if task_id is not None:
            query = query.filter(AutoTaskLog.task_id == task_id)
        if pnl_only:
            query = query.filter(AutoTaskLog.pnl_amount > 0)
        if date_from:
            query = query.filter(AutoTaskLog.executed_at >= date_from)
        if date_to:
            query = query.filter(AutoTaskLog.executed_at <= date_to)

        total = query.count()

        # 排序
        sort_map = {
            "id": AutoTaskLog.id,
            "executed_at": AutoTaskLog.executed_at,
            "duration_ms": AutoTaskLog.duration_ms,
            "status": AutoTaskLog.status,
            "pnl_amount": AutoTaskLog.pnl_amount,
            "task_id": AutoTaskLog.task_id,
        }
        sort_col = sort_map.get(sort_by, AutoTaskLog.id)
        if sort_dir == "asc":
            query = query.order_by(sort_col.asc(), AutoTaskLog.id.desc())
        else:
            query = query.order_by(sort_col.desc(), AutoTaskLog.id.desc())

        # 分页
        logs = query.offset(offset).limit(limit).all()

        return [_log_to_dict(log, db) for log in logs], total


def _task_to_dict(task: AutoTask) -> dict:
    return {
        "id": task.id,
        "task_name": task.task_name,
        "signal_source": task.signal_source,
        "gateway": task.gateway,
        "interval": task.interval,
        "is_active": task.is_active,
        "start_time": task.start_time.isoformat() if task.start_time else None,
        "loop_count": task.loop_count,
        "executed_count": task.executed_count,
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


def _enrich_countdown(d: dict) -> None:
    """为任务 dict 注入倒计时信息（原地修改）

    支持：
    - start_time: 首次执行时间倒计时
    - loop_count: 循环次数显示（0=无限）
    - 最后30秒标记为即将执行
    """
    d["next_run_at"] = None
    d["countdown_seconds"] = None
    d["countdown_text"] = "—"
    d["countdown_urgent"] = False  # 最后30秒醒目提示

    if not d.get("is_active"):
        d["countdown_text"] = "已停止"
        return

    # 显示循环次数信息
    loop_count = d.get("loop_count", 0)
    executed_count = d.get("executed_count", 0)
    if loop_count > 0:
        remaining = loop_count - executed_count
        if remaining <= 0:
            d["countdown_text"] = "已完成"
            d["countdown_urgent"] = False
            return
        d["loop_remaining"] = remaining

    # 如果有 start_time 且还未到首次执行时间，显示首次倒计时
    start_time_str = d.get("start_time")
    if start_time_str:
        try:
            if isinstance(start_time_str, str):
                from datetime import datetime as _dt
                start_dt = _dt.fromisoformat(start_time_str.replace("Z", "+00:00"))
            else:
                start_dt = start_time_str
            now = datetime.utcnow()
            if start_dt > now:
                delta = int((start_dt - now).total_seconds())
                d["countdown_seconds"] = delta
                d["next_run_at"] = start_dt.isoformat()
                m, s = divmod(delta, 60)
                if m >= 60:
                    h, m = divmod(m, 60)
                    d["countdown_text"] = f"首次: {h}h{m:02d}m"
                elif m > 0:
                    d["countdown_text"] = f"首次: {m}m{s:02d}s"
                else:
                    d["countdown_text"] = f"首次: {s}s"
                    d["countdown_urgent"] = True
                return
        except Exception:
            pass

    # 常规倒计时（基于 Redis 中的 last_run）
    try:
        raw = sync_redis.hget(DISPATCHER_KEY, str(d["id"]))
    except Exception:
        raw = None

    if raw is None:
        d["countdown_text"] = "待执行"
        return

    last_run = int(raw)
    interval_seconds = d["interval"] * 60
    now_ts = int(time.time())
    elapsed = now_ts - last_run
    remaining = interval_seconds - elapsed

    if remaining <= 0:
        d["countdown_text"] = "即将执行"
        d["countdown_seconds"] = 0
        d["next_run_at"] = datetime.utcfromtimestamp(last_run + interval_seconds).isoformat()
        d["countdown_urgent"] = True
        return

    d["countdown_seconds"] = remaining
    d["next_run_at"] = datetime.utcfromtimestamp(last_run + interval_seconds).isoformat()
    m, s = divmod(remaining, 60)
    if remaining <= 30:
        # 最后30秒醒目显示
        d["countdown_urgent"] = True
        if m > 0:
            d["countdown_text"] = f"{m}m{s:02d}s ⚠️"
        else:
            d["countdown_text"] = f"{s}s ⚠️"
    elif m >= 60:
        h, m = divmod(m, 60)
        d["countdown_text"] = f"{h}h{m:02d}m"
    elif m > 0:
        d["countdown_text"] = f"{m}m{s:02d}s"
    else:
        d["countdown_text"] = f"{s}s"


def _log_to_dict(log: AutoTaskLog, db=None) -> dict:
    task_name = ""
    task = log.task
    if task:
        task_name = task.task_name
    elif db is not None:
        t = db.query(AutoTask).filter(AutoTask.id == log.task_id).first()
        if t:
            task_name = t.task_name

    return {
        "id": log.id,
        "task_id": log.task_id,
        "task_name": task_name,
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
        "signal_detail_json": log.signal_detail_json if hasattr(log, 'signal_detail_json') else '{}',
        "execution_detail_json": log.execution_detail_json if hasattr(log, 'execution_detail_json') else '{}',
        "result_detail_json": log.result_detail_json if hasattr(log, 'result_detail_json') else '{}',
        "pnl_amount": float(log.pnl_amount) if (hasattr(log, 'pnl_amount') and log.pnl_amount is not None) else 0.0,
        "pnl_percent": float(log.pnl_percent) if (hasattr(log, 'pnl_percent') and log.pnl_percent is not None) else 0.0,
        "is_profit": log.is_profit if hasattr(log, 'is_profit') else False,
        "market_resolved": log.market_resolved if hasattr(log, 'market_resolved') else False,
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
            detail_json=_safe_dumps(detail or {}),
        )
        db.add(log_entry)
        db.flush()  # 确保ID生成，但不提交事务
    except Exception as e:
        logger.error(f"[AutoTask] 记录操作日志失败: {e}, traceback={traceback.format_exc()}")


def _auto_redeem_resolved_positions(task: AutoTask) -> dict | None:
    """在任务执行前自动扫描并赎回已结算市场的持仓

    Args:
        task: AutoTask 模型实例

    Returns:
        dict | None: 赎回结果摘要，包含 redeemed_count / errors 等
    """
    try:
        import asyncio
        from fwsort.config import reload_env
        reload_env()

        from fwsort.gateway.polymarket.F3.最简类_下单代码 import pm类

        pm = _gateway_instances.get(task.id)
        if pm is None:
            pm = pm类()
            _gateway_instances[task.id] = pm
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(pm.初始化())
            finally:
                loop.close()

        loop = asyncio.new_event_loop()
        try:
            client = pm.client
            if client is None:
                return {
                    "redeemed_count": 0,
                    "msg": "client 未初始化，跳过赎回",
                    "errors": ["client not initialized"],
                }

            positions_list = []
            try:
                async def _fetch_positions():
                    result = []
                    paginator = client.list_positions()
                    async for pos in paginator.iter_items():
                        result.append(pos)
                    return result

                positions_list = loop.run_until_complete(_fetch_positions())
            except Exception as e:
                logger.warning(f"[AutoTask-Redeem] list_positions 失败: {e}, 尝试回退方式")
                try:
                    fallback = loop.run_until_complete(
                        client.get_positions(size_greater_than=0.001)
                    )
                    if isinstance(fallback, list):
                        positions_list = fallback
                    elif isinstance(fallback, dict):
                        positions_list = fallback.get('data', []) or []
                except Exception as e2:
                    logger.warning(f"[AutoTask-Redeem] 回退方式也失败: {e2}")
                    return {
                        "redeemed_count": 0,
                        "msg": f"获取持仓失败: {e}, {e2}",
                        "errors": [str(e), str(e2)],
                    }

            if not positions_list:
                return {
                    "redeemed_count": 0,
                    "msg": "无持仓",
                    "errors": [],
                }

            logger.info(f"[AutoTask-Redeem] 扫描到 {len(positions_list)} 个持仓")

            to_redeem = []
            errors = []

            for pos in positions_list:
                # 兼容不同类型的持仓对象
                if isinstance(pos, dict):
                    token_id = str(pos.get('token_id') or pos.get('tokenId') or '')
                    market_slug = str(pos.get('market') or pos.get('market_slug') or '')
                    size = float(pos.get('size') or 0)
                    cur_price = float(pos.get('curPrice') or pos.get('price') or 0)
                    market_title = str(pos.get('title') or pos.get('market_title') or '')
                else:
                    token_id = str(getattr(pos, 'token_id', '') or '')
                    market_slug = str(getattr(pos, 'slug', '') or getattr(pos, 'market_slug', '') or '')
                    size_val = getattr(pos, 'size', 0)
                    size = float(size_val) if size_val is not None else 0
                    cur_price_val = getattr(pos, 'cur_price', 0) or getattr(pos, 'price', 0)
                    cur_price = float(cur_price_val) if cur_price_val is not None else 0
                    market_title = str(getattr(pos, 'title', '') or '')

                if size <= 0:
                    continue

                try:
                    # 尝试从持仓对象本身获取市场状态信息
                    state_obj = getattr(pos, 'state', None)
                    if state_obj is not None:
                        is_resolved = bool(getattr(state_obj, 'resolved', False))
                        is_closed = bool(getattr(state_obj, 'closed', False))
                    else:
                        is_resolved = False
                        is_closed = False

                    # 如果持仓对象已直接标记为已结算，直接赎回
                    if is_resolved and is_closed:
                        condition_id = str(
                            getattr(pos, 'condition_id', '') or
                            getattr(pos, 'conditionId', '') or
                            getattr(pos, 'conditionId', '')
                        )
                        to_redeem.append({
                            'token_id': token_id,
                            'market_slug': market_slug,
                            'condition_id': condition_id,
                            'size': size,
                            'cur_price': cur_price,
                            'market_title': market_title,
                        })
                        logger.info(
                            f"[AutoTask-Redeem] 发现已结算持仓: {market_title or market_slug} "
                            f"token={token_id[:12]}... size={size}"
                        )
                        continue

                    # 如果需要查询市场状态
                    if market_slug:
                        try:
                            market = loop.run_until_complete(
                                client.get_market(slug=market_slug)
                            )
                            if market is None:
                                logger.warning(
                                    f"[AutoTask-Redeem] 市场查询失败: {market_slug}, 跳过"
                                )
                                continue

                            state = getattr(market, 'state', None)
                            is_closed = bool(getattr(state, 'closed', False)) if state else False
                            is_resolved = bool(getattr(state, 'resolved', False)) if state else False

                            if is_resolved and is_closed:
                                condition_id = str(
                                    getattr(market, 'condition_id', '') or
                                    getattr(market, 'conditionId', '') or ''
                                )
                                to_redeem.append({
                                    'token_id': token_id,
                                    'market_slug': market_slug,
                                    'condition_id': condition_id,
                                    'size': size,
                                    'cur_price': cur_price,
                                    'market_title': market_title,
                                })
                                logger.info(
                                    f"[AutoTask-Redeem] 发现已结算持仓: {market_title or market_slug} "
                                    f"token={token_id[:12]}... size={size}"
                                )
                            elif is_closed and not is_resolved:
                                logger.debug(
                                    f"[AutoTask-Redeem] 市场已关闭但未结算: {market_slug}，跳过"
                                )
                            else:
                                logger.debug(
                                    f"[AutoTask-Redeem] 市场未结算: {market_slug} "
                                    f"(closed={is_closed}, resolved={is_resolved})，跳过"
                                )
                        except Exception as inner_e:
                            logger.warning(
                                f"[AutoTask-Redeem] 查询市场 {market_slug} 失败: {inner_e}"
                            )
                    elif token_id:
                        # 没有市场slug，只有token_id，直接尝试赎回
                        to_redeem.append({
                            'token_id': token_id,
                            'market_slug': '',
                            'condition_id': '',
                            'size': size,
                            'cur_price': cur_price,
                            'market_title': market_title,
                        })
                except Exception as e:
                    err_msg = f"检查持仓异常: {market_slug or token_id[:16]}... {e}"
                    logger.warning(f"[AutoTask-Redeem] {err_msg}")
                    errors.append(err_msg)

            if not to_redeem:
                return {
                    "redeemed_count": 0,
                    "msg": f"扫描 {len(positions_list)} 持仓，无已结算持仓需要赎回",
                    "errors": errors,
                }

            logger.info(
                f"[AutoTask-Redeem] 共 {len(to_redeem)} 个持仓可赎回，开始批量赎回..."
            )

            redeem_results = []
            redeemed_count = 0

            for p in to_redeem:
                condition_id = p.get('condition_id') or ''
                token_id = p.get('token_id') or ''
                try:
                    if condition_id and hasattr(client, 'redeem_positions'):
                        handle = loop.run_until_complete(
                            client.redeem_positions(condition_id=condition_id)
                        )
                        result = loop.run_until_complete(handle.wait())
                        redeem_results.append({
                            'condition_id': condition_id,
                            'result': str(result),
                        })
                        redeemed_count += 1
                        logger.info(
                            f"[AutoTask-Redeem] 赎回成功 condition_id={condition_id[:12]}..."
                        )
                    elif token_id and hasattr(client, 'redeem_positions'):
                        handle = loop.run_until_complete(
                            client.redeem_positions(token_id=token_id)
                        )
                        result = loop.run_until_complete(handle.wait())
                        redeem_results.append({
                            'token_id': token_id,
                            'result': str(result),
                        })
                        redeemed_count += 1
                        logger.info(
                            f"[AutoTask-Redeem] 按 token 赎回成功 {token_id[:12]}..."
                        )
                    else:
                        logger.debug(
                            f"[AutoTask-Redeem] 无可赎回条件: cid={condition_id[:12] if condition_id else 'N/A'} "
                            f"tid={token_id[:12] if token_id else 'N/A'}，跳过"
                        )
                except Exception as e:
                    err_msg = f"赎回失败 {p.get('market_slug') or token_id[:12]}...: {e}"
                    logger.warning(f"[AutoTask-Redeem] {err_msg}")
                    errors.append(err_msg)
                    redeem_results.append({
                        'market_slug': p.get('market_slug', ''),
                        'error': str(e),
                    })

            return {
                "redeemed_count": redeemed_count,
                "total_count": len(to_redeem),
                "msg": f"赎回 {len(to_redeem)} 个持仓，成功 {redeemed_count}",
                "errors": errors,
                "redeem_results": redeem_results,
            }

        finally:
            loop.close()

    except Exception as e:
        logger.error(f"[AutoTask-Redeem] 整体异常: {e}, traceback={traceback.format_exc()}")
        return {
            "redeemed_count": 0,
            "msg": f"异常: {e}",
            "errors": [str(e)],
        }


def _check_and_update_previous_pnl(db, task: AutoTask) -> dict | None:
    """回查上一笔未结算交易的市场结果并更新盈亏

    逻辑：
    1. 查找该任务最近一条 status=0/2 且 market_resolved=False 的执行日志
    2. 从日志的 signal_detail_json 中获取 market_slug（市场标识）和 direction
    3. 通过 Polymarket API 查询该市场是否已结算
    4. 若已结算，判断结果并计算盈亏
    5. 更新该条日志的 pnl_amount / pnl_percent / is_profit / market_resolved

    Args:
        db: 数据库会话
        task: AutoTask 模型实例

    Returns:
        dict | None: 盈亏结果 {"pnl_amount": x, "pnl_percent": y, "is_profit": z} 或 None
    """
    from sqlalchemy import and_

    # 查找最近一条成功但未结算的执行日志
    prev_log = (
        db.query(AutoTaskLog)
        .filter(
            AutoTaskLog.task_id == task.id,
            AutoTaskLog.log_type == 0,
            AutoTaskLog.status.in_([0, 2]),
            AutoTaskLog.market_resolved == False,
        )
        .order_by(AutoTaskLog.created_at.desc())
        .first()
    )

    if not prev_log:
        return None

    # 解析信号详情获取市场信息
    try:
        signal_detail = json.loads(prev_log.signal_detail_json or "{}")
    except Exception:
        signal_detail = {}

    market_slug = signal_detail.get("market_slug", "")
    direction = signal_detail.get("direction", "")
    signal_amount = float(signal_detail.get("amount", 0))

    # 解析执行详情获取下单信息
    try:
        exec_detail = json.loads(prev_log.execution_detail_json or "{}")
    except Exception:
        exec_detail = {}

    making_amount = float(exec_detail.get("making_amount", 0) or 0)
    taking_amount = float(exec_detail.get("taking_amount", 0) or 0)

    if not market_slug:
        return None

    # 只有 Polymarket 网关的任务才能回查
    if task.gateway != "polymarket_f3":
        return None

    # 查询市场状态
    try:
        import asyncio
        from fwsort.config import reload_env
        reload_env()

        from fwsort.gateway.polymarket.F3.最简类_下单代码 import pm类

        # 使用已有的网关实例或创建新的
        pm = _gateway_instances.get(task.id)
        if pm is None:
            pm = pm类()
            _gateway_instances[task.id] = pm
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(pm.初始化())
            finally:
                loop.close()

        loop = asyncio.new_event_loop()
        try:
            market = loop.run_until_complete(pm.client.get_market(slug=market_slug))
        finally:
            loop.close()

        if market is None:
            logger.warning(f"[AutoTask] 任务 {task.id} 市场查询失败: slug={market_slug}")
            return None

        # 检查市场状态
        state = getattr(market, 'state', None)
        is_closed = getattr(state, 'closed', False) if state else False
        is_resolved = getattr(state, 'resolved', False) if state else False
        accepting_orders = getattr(state, 'accepting_orders', True) if state else True

        if not is_closed and not is_resolved:
            # 市场尚未结算，无法计算盈亏
            logger.debug(
                f"[AutoTask] 任务 {task.id} 市场 {market_slug} 尚未结算 "
                f"(closed={is_closed}, resolved={is_resolved})"
            )
            return None

        # 市场已结算，判断结果
        outcomes = getattr(market, 'outcomes', None)
        if outcomes is None:
            return None

        # 获取 YES/NO 的结算价格
        yes_price = float(getattr(outcomes.yes, 'price', 0)) if outcomes.yes else 0
        no_price = float(getattr(outcomes.no, 'price', 0)) if outcomes.no else 0

        # 结算规则：winning outcome 的价格接近 1.0
        # 如果 YES 价格 >= 0.5，视为 YES 赢；否则 NO 赢
        yes_won = yes_price >= 0.5

        # 判断我们的方向是否正确
        # direction UP/YES 对应买 YES (token_id = outcomes.yes.token_id)
        # direction DOWN/NO 对应买 NO (token_id = outcomes.no.token_id)
        is_buy_yes = direction.upper() in ("UP", "YES", "Y", "U", "BUY")
        we_won = (is_buy_yes and yes_won) or (not is_buy_yes and not yes_won)

        # 计算盈亏
        # 赢：收回 1.0 * shares (付出约 0.5 * shares)
        # 输：收回 0
        if making_amount > 0:
            if we_won:
                # 盈利 = (1.0 - cost_price) * shares
                # 近似：盈利 = making_amount * (1/cost_ratio - 1)
                # 简化：盈利 = making_amount * (winning_price - 1) / losing_price... 
                # 更简单：盈利 = taking_amount (赢方收回全部) - making_amount (付出)
                pnl_amount = taking_amount - making_amount
            else:
                pnl_amount = -making_amount
        else:
            pnl_amount = 0.0

        pnl_percent = (pnl_amount / making_amount * 100) if making_amount > 0 else 0.0
        is_profit = pnl_amount > 0

        # 更新日志记录
        prev_log.market_resolved = True
        prev_log.pnl_amount = round(pnl_amount, 6)
        prev_log.pnl_percent = round(pnl_percent, 4)
        prev_log.is_profit = is_profit

        # 在 result_detail_json 中记录结算信息
        try:
            result_detail = json.loads(prev_log.result_detail_json or "{}")
        except Exception:
            result_detail = {}
        result_detail["market_resolution"] = {
            "market_slug": market_slug,
            "yes_price": yes_price,
            "no_price": no_price,
            "yes_won": yes_won,
            "we_bet_direction": direction,
            "we_won": we_won,
            "resolved_at": datetime.utcnow().isoformat(),
        }
        prev_log.result_detail_json = _safe_dumps(result_detail)

        db.commit()

        resolution_result = {
            "pnl_amount": pnl_amount,
            "pnl_percent": pnl_percent,
            "is_profit": is_profit,
            "market_slug": market_slug,
            "yes_won": yes_won,
            "we_bet_direction": direction,
            "prev_log_id": prev_log.id,
        }

        logger.info(
            f"[AutoTask] 💰 任务 {task.id} 盈亏回查: "
            f"market={market_slug} yes_price={yes_price:.3f} no_price={no_price:.3f} "
            f"direction={direction} we_won={we_won} "
            f"pnl=${pnl_amount:.4f} ({pnl_percent:.2f}%)"
        )

        return resolution_result

    except Exception as e:
        logger.warning(f"[AutoTask] 任务 {task.id} 盈亏回查异常: {e}")
        return None
