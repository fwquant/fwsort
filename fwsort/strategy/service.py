"""自动任务服务层：CRUD + 业务逻辑

职责：
    - 任务的增删改查
    - 任务启停（初始化/销毁网关）
    - 风控检查
    - 日志记录
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime, timedelta, date
from decimal import Decimal

from fwsort.database import get_sync_db
from fwsort.fwlogs import logger
from fwsort.models import AutoStrategy, AutoStrategyLog, ExecutionAccount, StrategyTrade
from fwsort.redis_client import sync_redis
from fwsort.risk.manager import RiskProfileManager
from fwsort.risk.models import StrategyRiskProfile
from fwsort.risk.service import RiskControlService
from fwsort.strategy import get_signal

# 调度器 Redis Key
DISPATCHER_KEY = "fwsort:auto_strategy:last_run"

# 内存中存储已初始化的网关客户端
_gateway_instances: dict[int, object] = {}


def _extract_missing_module(error_msg: str) -> str | None:
    """从 ImportError 消息中提取缺失的模块名。
    支持多种 Python 错误格式，如:
    - No module named 'paramiko'
    - No module named 'paramiko.ssh_exception'
    - cannot import name 'xxx' from 'yyy'
    """
    import re
    m = re.search(r"No module named ['\"]([\w\.]+)['\"]", error_msg)
    if m:
        full = m.group(1)
        top = full.split(".")[0]
        return top
    m = re.search(r"cannot import name ['\"][^'\"]+['\"] from ['\"]([\w\.]+)['\"]", error_msg)
    if m:
        full = m.group(1)
        top = full.split(".")[0]
        return top
    return None


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
        query = db.query(AutoStrategy)
        if not include_deleted:
            query = query.filter(AutoStrategy.deleted_at.is_(None))
        tasks = query.order_by(AutoStrategy.id.desc()).all()
        result = []
        for t in tasks:
            d = _task_to_dict(t)
            _enrich_countdown(d)
            result.append(d)
        return result


def get_task(task_id: int) -> dict | None:
    """查询单个任务"""
    with get_sync_db() as db:
        task = db.query(AutoStrategy).filter(AutoStrategy.id == task_id).first()
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
        gateway_name = data.get("gateway", "polymarket_f3")
        # 如果指定了现有账户，使用关联账户；否则自动创建（1:1）
        account_id = data.get("account_id")
        if account_id:
            account = db.query(ExecutionAccount).filter(ExecutionAccount.id == account_id).first()
            if not account:
                raise ValueError(f"指定的账户 #{account_id} 不存在")
        else:
            platform = "polymarket" if "polymarket" in gateway_name else "okx"
            account = ExecutionAccount(
                uid=f"ACC-TASK-{uuid.uuid4().hex[:8].upper()}",
                owner_id=1,  # admin
                name=f"策略: {data['task_name']}",
                platform=platform,
                account_type=1,  # 实盘
                initial_balance=0.0,
                current_balance=0.0,
                daily_pnl=0.0,
                order_amount_usd=float(data.get("max_daily_amount", 50.0)),
                signal_source=data.get("signal_source", "random"),
            )
            db.add(account)
            db.flush()  # 拿到 account.id

        initial_balance = float(data.get("initial_balance", 1000.0))
        task = AutoStrategy(
            task_name=data["task_name"],
            signal_source=data.get("signal_source", "random"),
            gateway=gateway_name,
            interval=data.get("interval", 5),
            is_active=False,
            start_time=start_time,
            loop_count=data.get("loop_count", 0),
            executed_count=0,
            max_daily_amount=data.get("max_daily_amount", 50.0),
            max_daily_count=data.get("max_daily_count", 50),
            max_consecutive_failures=data.get("max_consecutive_failures", 5),
            config_json=json.dumps(data.get("config_json", {})),
            account_id=account.id,
            initial_balance=initial_balance,
            current_balance=initial_balance,
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

        logger.info(f"[AutoStrategy] created task: {task.id} - {task.task_name}")
        return _task_to_dict(task)


def update_task(task_id: int, data: dict) -> dict | None:
    """更新任务"""
    with get_sync_db() as db:
        task = db.query(AutoStrategy).filter(AutoStrategy.id == task_id).first()
        if not task:
            return None

        updatable_fields = [
            "task_name", "signal_source", "gateway", "interval",
            "max_daily_amount", "max_daily_count", "max_consecutive_failures",
            "loop_count", "account_id",
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

        # 同步风控参数到 StrategyRiskProfile（热加载：修改后立即生效）
        risk_fields = ["max_daily_amount", "max_daily_count", "max_consecutive_failures"]
        risk_changed = any(f in changes for f in risk_fields)
        if risk_changed:
            try:
                profile = db.query(StrategyRiskProfile).filter(
                    StrategyRiskProfile.auto_strategy_id == task_id
                ).first()
                if profile:
                    if "max_daily_amount" in changes:
                        profile.max_daily_amount = float(task.max_daily_amount) if task.max_daily_amount is not None else None
                    if "max_daily_count" in changes:
                        profile.max_daily_count = int(task.max_daily_count) if task.max_daily_count is not None else None
                    if "max_consecutive_failures" in changes:
                        profile.max_consecutive_failures = int(task.max_consecutive_failures) if task.max_consecutive_failures is not None else None
                    db.commit()
                    logger.info(
                        f"[AutoStrategy] 同步风控参数到 StrategyRiskProfile: task={task_id} "
                        f"changed={[f for f in risk_fields if f in changes]}"
                    )
                else:
                    RiskProfileManager.get_or_create_strategy_profile(db, task_id)
                    logger.info(f"[AutoStrategy] StrategyRiskProfile 不存在，懒创建: task={task_id}")
            except Exception as e:
                logger.warning(f"[AutoStrategy] 同步风控参数失败(不影响主流程): {e},traceback={traceback.format_exc()}")
                db.rollback()

            # 账户级冻结重评估：若账户因"连续失败"被冻结，且新阈值 > 当前失败次数，则自动解冻
            try:
                acc_id = task.account_id
                if acc_id:
                    acc_profile = RiskProfileManager.get_or_create_account_profile(db, acc_id)
                    if acc_profile.is_frozen and "连续失败" in (acc_profile.frozen_reason or ""):
                        cur_consec = int(profile.consecutive_failures) if profile else 0
                        new_threshold = int(task.max_consecutive_failures) if task.max_consecutive_failures is not None else 0
                        if new_threshold > 0 and cur_consec < new_threshold:
                            RiskControlService.unfreeze_account(
                                db, acc_id,
                                reason=f"风控阈值调整为{new_threshold}，当前连续失败{cur_consec}次低于阈值，自动解冻",
                            )
                            # 同步重置策略级 consecutive_failures
                            if profile:
                                profile.consecutive_failures = 0
                                db.commit()
                            logger.info(
                                f"[AutoStrategy] 账户级自动解冻: account={acc_id} "
                                f"cur_consec={cur_consec} < new_threshold={new_threshold}"
                            )
            except Exception as e:
                logger.warning(f"[AutoStrategy] 账户级冻结重评估失败(不影响主流程): {e},traceback={traceback.format_exc()}")
                db.rollback()

        # 记录操作日志
        _add_operation_log(db, task.id, "update", 0, detail={
            "changes": changes,
        })

        logger.info(f"[AutoStrategy] updated task: {task.id}")
        return _task_to_dict(task)


def delete_task(task_id: int) -> bool:
    """软删除任务"""
    with get_sync_db() as db:
        task = db.query(AutoStrategy).filter(AutoStrategy.id == task_id).first()
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
        logger.info(f"[AutoStrategy] deleted task: {task.id}")
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
        logger.info(f"[AutoStrategy] [进度] {task_id}: {step} - {status}")

    add_progress("查询任务信息")

    with get_sync_db() as db:
        task = db.query(AutoStrategy).filter(AutoStrategy.id == task_id).first()
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
                logger.info(f"[AutoStrategy] 重新加载 .env 配置完成")

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
                    logger.error(f"[AutoStrategy] 任务启动失败 {task_id}: {error_msg}")
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
                    logger.error(
                        f"[AutoStrategy] 任务网关初始化失败 {task_id}: {e1},traceback={traceback.format_exc()}")
                    raise ValueError(f"Polymarket F3 网关初始化失败: {error_msg}")
            else:
                add_progress(f"网关类型 '{task.gateway}' 不需要额外初始化", "completed")
                gateway_initialized = True
        except ValueError:
            raise
        except Exception as e:
            error_msg = str(e)
            add_progress(f"网关初始化异常: {error_msg}", "error")
            logger.error(f"[AutoStrategy] 任务网关初始化失败 {task_id}: {e},traceback={traceback.format_exc()}")
            raise ValueError(f"网关初始化异常: {error_msg}")

        # 网关初始化成功，标记任务为活跃
        add_progress("标记任务为活跃状态", "running")
        task.is_active = True
        task.executed_count = 0  # 重置执行次数
        task.consecutive_failures = 0  # 重置连续失败次数（用户手动启用，视为确认重置熔断状态）
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
            logger.info(f"[AutoStrategy] 初始化 Redis last_run 为 task={task.id}, initial={initial_last_run}")
        except Exception as e:
            logger.warning(f"[AutoStrategy] 初始化 Redis last_run 失败: {e},traceback={traceback.format_exc()}")

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
        logger.info(f"[AutoStrategy] started task: {task.id} gateway_ok=True")
        return result


def stop_task(task_id: int) -> dict:
    """停止任务：释放网关 + 标记为不活跃 + 清理 Redis 记录 + 结算回查 + 全量重算"""
    with get_sync_db() as db:
        task = db.query(AutoStrategy).filter(AutoStrategy.id == task_id).first()
        if not task:
            raise ValueError(f"任务不存在: {task_id}")

        # ===== 结算回查：任务停止时先回查所有未结算交易 =====
        settlement_result = {}
        try:
            updated = _check_and_update_previous_pnl(db, task)
            settlement_result = {
                "updated_count": len(updated),
                "updated_results": updated,
                "message": f"结算回查更新 {len(updated)} 条",
            }
            if updated:
                logger.info(
                    f"[AutoStrategy] 任务停止结算回查: {len(updated)} 条交易已更新结算"
                )
                
                # ===== 结算回查后触发全量重算 =====
                try:
                    from fwsort.strategy.settlement_service import batch_sync_after_resolution
                    batch_result = batch_sync_after_resolution(db, task)
                    settlement_result["batch_sync"] = batch_result
                    logger.info(
                        f"[AutoStrategy] 任务停止全量重算完成: {batch_result}"
                    )
                except Exception as batch_err:
                    logger.warning(f"[AutoStrategy] 全量重算异常(不影响停止): {batch_err}")
                    settlement_result["batch_sync_error"] = str(batch_err)
                    
        except Exception as e:
            logger.warning(f"[AutoStrategy] 任务停止结算回查异常(不影响停止): {e},traceback={traceback.format_exc()}")
            settlement_result = {"message": f"结算回查异常: {e},traceback={traceback.format_exc()}"}

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
                logger.warning(f"[AutoStrategy] gateway cleanup failed for task {task_id}: {e},traceback={traceback.format_exc()}")

        task.is_active = False
        db.commit()
        db.refresh(task)

        # 清理 Redis 中的调度记录
        try:
            sync_redis.hdel(DISPATCHER_KEY, str(task_id))
            logger.info(f"[AutoStrategy] 清理 Redis last_run 为 task={task_id}")
        except Exception as e:
            logger.warning(f"[AutoStrategy] 清理 Redis last_run 失败: {e},traceback={traceback.format_exc()}")

        # 记录操作日志
        _add_operation_log(db, task.id, "stop", 0 if gateway_cleanup_ok else 1, detail={
            "gateway_cleanup_ok": gateway_cleanup_ok,
            "gateway_error": gateway_error,
            "settlement": settlement_result,
        })

        logger.info(f"[AutoStrategy] stopped task: {task.id}")
        return {
            "task_id": task.id,
            "task_name": task.task_name,
            "is_active": task.is_active,
            "settlement_result": settlement_result,
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
    pnl_check_result = None  # 上一笔交易盈亏回查结果（取第一条）
    pnl_check_results = []   # 所有未结算交易回查结果列表

    with get_sync_db() as db:
        task = db.query(AutoStrategy).filter(AutoStrategy.id == task_id).first()
        if not task:
            if manual:
                _add_operation_log(db, task_id, "execute_manual", 1, detail={
                    "error": "任务不存在",
                })
            return {"status": "skipped", "message": "任务不存在"}

        if not task.is_active and not manual:
            return {"status": "skipped", "message": "任务已停止"}

        # ===== 回查所有未结算交易的盈亏 =====
        try:
            pnl_check_results = _check_and_update_previous_pnl(db, task)
            if pnl_check_results:
                for r in pnl_check_results:
                    logger.info(
                        f"[AutoStrategy] 💰 任务 {task_id} 结算回查: "
                        f"log_id={r.get('log_id')} "
                        f"{'盈利' if r.get('is_profit') else '亏损'} "
                        f"${r.get('pnl_amount', 0):.4f} "
                        f"({r.get('pnl_percent', 0):.2f}%)"
                    )
                pnl_check_result = pnl_check_results[0] if pnl_check_results else None
        except Exception as e:
            logger.warning(f"[AutoStrategy] 任务 {task_id} 盈亏回查异常(不影响主流程): {e},traceback={traceback.format_exc()}")
            pnl_check_results = []
            pnl_check_result = None

        # ===== 自动赎回已结算持仓 =====
        redeem_result = None
        if task.gateway == "polymarket_f3":
            try:
                redeem_result = _auto_redeem_resolved_positions(task)
                if redeem_result and redeem_result.get("redeemed_count", 0) > 0:
                    logger.info(
                        f"[AutoStrategy] 🔄 任务 {task_id} 自动赎回: "
                        f"{redeem_result.get('redeemed_count')} 个持仓已赎回"
                    )
                elif redeem_result:
                    logger.debug(
                        f"[AutoStrategy] 任务 {task_id} 自动赎回: 无待赎回持仓"
                    )
            except Exception as e:
                logger.warning(
                    f"[AutoStrategy] 任务 {task_id} 自动赎回异常(不影响主流程): {e},traceback={traceback.format_exc()}"
                )
                redeem_result = None

        # ===== 风控检查（统一入口：DailyCount / DailyAmount / ConsecutiveFailure / SingleRatio / DailyLoss）=====
        risk_result = RiskControlService.check_before_auto_strategy_order(
            db, auto_strategy_id=task_id, manual=manual,
            order_amount_usd=None,  # 具体金额在投票后确定；此处先跑次数/金额/熔断
        )
        if risk_result.should_freeze:
            # 连续失败熔断 → 自动停止任务 + 返回 fuse 状态
            msg = risk_result.freeze_reason or risk_result.message
            logger.warning(f"[AutoStrategy] 任务 {task_id} 触发风控冻结/熔断: {msg}")
            # 写一条熔断日志
            db.add(AutoStrategyLog(
                task_id=task_id, log_type=0, executed_at=datetime.utcnow(),
                signal_json="{}", order_result_json=_safe_dumps({"error": "风控熔断触发"}),
                status=3, error_message=msg,
                duration_ms=int((time.time() - start_time) * 1000), order_id="",
                detail_json=_safe_dumps({"manual": manual}),
                signal_detail_json="{}", execution_detail_json=_safe_dumps({"stage": "risk_fuse_check"}),
                result_detail_json="{}", pnl_amount=0.0, pnl_percent=0.0,
                is_profit=False, market_resolved=False,
            ))
            db.commit()
            return {"status": "fuse_triggered", "message": msg, "risk_event_log_id": risk_result.event_log_id}
        if not risk_result.passed:
            status = 6  # 风控拦截（黄色警告，非程序错误）
            error_message = risk_result.first_block_reason or risk_result.message

        # ===== 获取信号 =====
        signal = None
        signal_dict = {}
        signal_detail = {}
        if status == 0:
            _install_attempted = False
            while True:
                try:
                    signal = get_signal(task.signal_source)
                    break
                except ImportError as e:
                    if _install_attempted:
                        raise
                    _install_attempted = True
                    missing_module = _extract_missing_module(str(e))
                    if missing_module:
                        logger.warning(
                            f"[AutoStrategy] 检测到缺失依赖 '{missing_module}'，正在自动安装..."
                        )
                        try:
                            subprocess.check_call(
                                [sys.executable, "-m", "pip", "install", missing_module],
                                stdout=sys.stdout,
                                stderr=sys.stderr,
                            )
                            logger.info(f"[AutoStrategy] '{missing_module}' 安装成功，重试信号获取...")
                            continue
                        except Exception as install_err:
                            raise ImportError(
                                f"自动安装 '{missing_module}' 失败: {install_err}"
                            ) from e
                    else:
                        raise
                except Exception as e:
                    status = 1
                    error_message = f"信号获取失败: {e}，traceback: {traceback.format_exc()}"
                    logger.error(f"[AutoStrategy] ❌ 任务 {task_id} 信号获取失败: {e},traceback={traceback.format_exc()}")
                    break

            if status == 0 and signal:
                signal_dict = signal.to_dict() if signal else {}
                symbol = signal_dict.get("symbol", "")
                signal_detail = {
                    "symbol": symbol,
                    "direction": signal_dict.get("direction", ""),
                    "amount": signal_dict.get("amount", 0),
                    "source": signal_dict.get("source", task.signal_source),
                    "market_id": signal_dict.get("market_id", ""),
                    "market_slug": signal_dict.get("market_slug") or symbol,
                    "market_question": signal_dict.get("market_question", ""),
                    "timestamp": datetime.utcnow().isoformat(),
                }
                logger.info(
                    f"[AutoStrategy] 📡 任务 {task_id} 信号获取成功: "
                    f"symbol={signal_detail['symbol']} direction={signal_detail['direction']} "
                    f"amount={signal_detail['amount']} source={signal_detail['source']}"
                )

        # ===== 无有效信号跳过下单 =====
        if status == 0 and signal and not signal.is_valid:
            status = 4
            error_message = "无有效交易信号（direction 为空），跳过下单"
            logger.warning(
                f"[AutoStrategy] ⏭️ 任务 {task_id} 无有效交易信号: "
                f"symbol={signal_detail.get('symbol')} direction={signal_detail.get('direction')}"
            )

        # ===== 策略层判断（开仓条件）=====
        if status == 0 and signal and signal.is_valid:
            try:
                from fwsort.strategy.manager import get_provider
                provider = get_provider(task.signal_source)
                ctx = {
                    "task": task,
                    "now": datetime.utcnow(),
                    "gateway": _gateway_instances.get(task.id),
                }
                allow, reason = provider.should_open(signal, ctx)
                if not allow:
                    status = 5  # 策略拦截
                    error_message = f"策略拦截: {reason}"
                    logger.info(f"[AutoStrategy] 🚫 任务 {task_id} 策略拦截: {reason}")
            except Exception as e:
                logger.warning(f"[AutoStrategy] 任务 {task_id} 策略判断异常(默认放行): {e},traceback={traceback.format_exc()}")

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
                        f"[AutoStrategy] {'🔄 重试成功' if retried else '✅ 下单成功'} "
                        f"task={task_id} order_id={order_id} "
                        f"status={result_detail['status']} "
                        f"making={execution_detail.get('making_amount', 'N/A')} "
                        f"taking={execution_detail.get('taking_amount', 'N/A')}"
                    )
                else:
                    result_detail = {"error": error_message}
                    logger.error(f"[AutoStrategy] ❌ 任务 {task_id} 下单失败: {error_message}")

                if error_message:
                    status = 1
            except Exception as e:
                status = 1
                error_message = f"下单异常: {e},traceback={traceback.format_exc()}"
                logger.error(f"[AutoStrategy] ❌ 任务 {task_id} 下单异常: {e},traceback={traceback.format_exc()}")

        # ===== 更新统计 =====
        duration_ms = int((time.time() - start_time) * 1000)

        signal_json = signal.to_dict() if signal else {}
        order_result_json = order_result if order_result else {}

        if retried and status == 0:
            status = 2

        log_entry = AutoStrategyLog(
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

        # ===== 写入策略交易明细表（仅下单成功时）=====
        if status in (0, 2) and signal and order_id:
            try:
                trade_uid = f"TRD-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
                amount_usd = float(
                    execution_detail.get("making_amount", 0) or execution_detail.get("taking_amount", 0) or 0)
                trade_entry = StrategyTrade(
                    trade_uid=trade_uid,
                    strategy_name=task.task_name,
                    auto_strategy_id=task.id,
                    account_id=task.account_id,
                    source_strategy=task.signal_source,
                    platform=task.gateway or "",
                    symbol=signal.symbol or "",
                    market_question=getattr(signal, "market_question", "") or "",
                    market_slug=getattr(signal, "market_slug", "") or "",
                    direction=signal.direction.value if hasattr(signal.direction, "value") else str(
                        signal.direction or ""),
                    side=1,  # 买入
                    order_type=2,  # 市价
                    order_id=order_id,
                    entry_price=float(getattr(signal, "price", 0) or 0),
                    amount_usd=amount_usd,
                    status=0,  # 持仓中
                    entry_at=datetime.utcnow(),
                    execution_detail_json=_safe_dumps(execution_detail),
                    result_detail_json=_safe_dumps(result_detail),
                )
                db.add(trade_entry)
                logger.info(f"[AutoStrategy] 📝 交易明细已记录: {trade_uid} strategy={task.task_name}")
            except Exception as trade_err:
                logger.warning(f"[AutoStrategy] 交易明细写入失败(不影响主流程): {trade_err}")

        # 策略拦截（status=5）、风控拦截（status=6）不计入执行、不触发熔断
        if status not in (5, 6):
            task.total_executions += 1
            task.executed_count += 1
            if status == 0 or status == 2:
                task.total_success += 1
                RiskControlService.update_strategy_consecutive_failures(db, task_id, success=True)
            elif status == 4:
                pass  # 无信号跳过，不计入失败
            elif status == 1:
                # 程序错误：计入失败 + 可能触发自动停止
                task.total_failed += 1
                RiskControlService.update_strategy_consecutive_failures(db, task_id, success=False)
                # 严重程序错误自动停止任务
                cf = task.consecutive_failures + 1
                if cf >= (task.max_consecutive_failures or 5):
                    task.is_active = False
                    _add_operation_log(db, task_id, "auto_stopped", 1, detail={
                        "reason": "程序连续错误过多，自动停止",
                        "consecutive_failures": cf,
                        "last_error": error_message,
                    })
                    logger.warning(
                        f"[AutoStrategy] ⛔ 任务 {task_id} 因连续程序错误自动停止 "
                        f"(连续{cf}次失败), 最后错误: {error_message}"
                    )
            else:
                task.total_failed += 1
                RiskControlService.update_strategy_consecutive_failures(db, task_id, success=False)

        loop_auto_stopped = False
        if task.loop_count > 0 and task.executed_count >= task.loop_count:
            task.is_active = False
            loop_auto_stopped = True
            _add_operation_log(db, task_id, "loop_completed", 0, detail={
                "executed_count": task.executed_count,
                "loop_count": task.loop_count,
            })
            logger.info(
                f"[AutoStrategy] task {task_id} loop completed ({task.executed_count}/{task.loop_count}), auto-stopped")

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
            "status": ["成功", "失败", "重试成功", "熔断", "无信号", "策略拦截", "风控拦截"][status],
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
            f"[AutoStrategy] 🏁 executed task {task_id}: status={result['status']} "
            f"duration={duration_ms}ms signal={signal_detail.get('symbol', 'N/A')} "
            f"dir={signal_detail.get('direction', 'N/A')} amt={signal_detail.get('amount', 'N/A')}"
        )
        return result


def _run_risk_control(db, task: AutoStrategy, task_id: int) -> dict:
    """每日风控检查：执行次数 + 执行金额

    Args:
        db: SQLAlchemy 同步 session
        task: AutoStrategy ORM 对象
        task_id: 任务 ID

    Returns:
        dict: {"passed": bool, "message": str}
    """
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_logs = (
        db.query(AutoStrategyLog)
        .filter(AutoStrategyLog.task_id == task_id, AutoStrategyLog.created_at >= today_start)
        .all()
    )

    today_total_amount = 0.0
    today_total_count = len(today_logs)
    for log in today_logs:
        try:
            # 修复 bug：making_amount 存储在 execution_detail_json，不是 detail_json
            exec_detail = json.loads(log.execution_detail_json or "{}")
            today_total_amount += float(exec_detail.get("making_amount", 0))
        except Exception:
            pass

    if today_total_count >= task.max_daily_count:
        msg = f"已达每日最大执行次数({task.max_daily_count}次)"
        logger.warning(f"[AutoStrategy] 任务 {task_id} 风控: {msg}")
        return {"passed": False, "message": msg}

    if today_total_amount >= task.max_daily_amount:
        msg = f"已达每日最大执行金额(${task.max_daily_amount:.2f})"
        logger.warning(f"[AutoStrategy] 任务 {task_id} 风控: {msg}")
        return {"passed": False, "message": msg}

    return {"passed": True, "message": ""}


def _check_circuit_breaker(db, task: AutoStrategy, task_id: int, start_time: float, manual: bool) -> bool:
    """连续失败熔断检查：触发后自动停止任务

    Args:
        db: SQLAlchemy 同步 session
        task: AutoStrategy ORM 对象
        task_id: 任务 ID
        start_time: 任务开始时间戳
        manual: 是否为手动触发

    Returns:
        bool: True 表示已触发熔断（调用方应直接 return）
    """
    if task.consecutive_failures < task.max_consecutive_failures:
        return False

    failed_count = task.consecutive_failures
    log_entry = AutoStrategyLog(
        task_id=task_id,
        log_type=0,
        executed_at=datetime.utcnow(),
        signal_json="{}",
        order_result_json=json.dumps({"error": "熔断触发"}),
        status=3,
        error_message=f"连续失败 {failed_count} 次，触发熔断",
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
        "consecutive_failures": failed_count,
        "max_consecutive_failures": task.max_consecutive_failures,
    })

    db.commit()

    if manual:
        _add_operation_log(db, task_id, "execute_manual", 1, detail={
            "error": "熔断触发",
        })

    logger.warning(f"[AutoStrategy] 任务 {task_id} 触发熔断，自动停止")
    return True


def _execute_order_with_retry(task: AutoStrategy, signal, start_time: float) -> tuple:
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
        logger.info(f"[AutoStrategy] 执行前重新加载 .env 配置完成")

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
            f"[AutoStrategy] 📤 准备下单: task={task.id} symbol={signal.symbol} "
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
            logger.warning(f"[AutoStrategy] ⚠️ 订单被拒绝，重试一次: {error_message}")
            result = loop.run_until_complete(
                pm.下单(
                    标的代码=signal.symbol,
                    outcome=direction,
                    amount=amount,
                )
            )
            if result is None or (hasattr(result, 'ok') and not result.ok):
                error_message = f"重试后仍然失败: {error_message}"
                logger.error(f"[AutoStrategy] ❌ 重试仍然失败: {error_message}")
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
        logger.error(f"[AutoStrategy] ❌ order execution error for task {task.id}: {error_message}")
    finally:
        loop.close()

    return order_result, order_id, error_message, retried, pm_result_trimmed, signal


def get_task_logs(task_id: int, limit: int = 50, offset: int = 0, log_type: int | None = None) -> list[dict]:
    """查询任务执行日志"""
    with get_sync_db() as db:
        query = (
            db.query(AutoStrategyLog)
            .filter(AutoStrategyLog.task_id == task_id)
        )
        if log_type is not None:
            query = query.filter(AutoStrategyLog.log_type == log_type)
        logs = (
            query
            .order_by(AutoStrategyLog.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [_log_to_dict(l) for l in logs]


def get_task_log_count(task_id: int, log_type: int | None = None) -> int:
    """查询任务日志数量"""
    with get_sync_db() as db:
        query = db.query(AutoStrategyLog).filter(AutoStrategyLog.task_id == task_id)
        if log_type is not None:
            query = query.filter(AutoStrategyLog.log_type == log_type)
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
        query = db.query(AutoStrategyLog)

        # 搜索
        if search:
            like = f"%{search}%"
            query = query.outerjoin(AutoStrategy, AutoStrategyLog.task_id == AutoStrategy.id).filter(
                sa_or_(
                    AutoStrategyLog.error_message.ilike(like),
                    AutoStrategyLog.order_id.ilike(like),
                    AutoStrategyLog.action_type.ilike(like),
                    AutoStrategy.task_name.ilike(like),
                )
            )

        # 筛选
        if status is not None:
            query = query.filter(AutoStrategyLog.status == status)
        if log_type is not None:
            query = query.filter(AutoStrategyLog.log_type == log_type)
        if action_type:
            query = query.filter(AutoStrategyLog.action_type == action_type)
        if task_id is not None:
            query = query.filter(AutoStrategyLog.task_id == task_id)
        if pnl_only:
            query = query.filter(AutoStrategyLog.pnl_amount > 0)
        if date_from:
            query = query.filter(AutoStrategyLog.executed_at >= date_from)
        if date_to:
            query = query.filter(AutoStrategyLog.executed_at <= date_to)

        total = query.count()

        # 排序
        sort_map = {
            "id": AutoStrategyLog.id,
            "executed_at": AutoStrategyLog.executed_at,
            "duration_ms": AutoStrategyLog.duration_ms,
            "status": AutoStrategyLog.status,
            "pnl_amount": AutoStrategyLog.pnl_amount,
            "task_id": AutoStrategyLog.task_id,
        }
        sort_col = sort_map.get(sort_by, AutoStrategyLog.id)
        if sort_dir == "asc":
            query = query.order_by(sort_col.asc(), AutoStrategyLog.id.desc())
        else:
            query = query.order_by(sort_col.desc(), AutoStrategyLog.id.desc())

        # 分页
        logs = query.offset(offset).limit(limit).all()

        return [_log_to_dict(log, db) for log in logs], total


def _task_to_dict(task: AutoStrategy) -> dict:
    is_frozen = task.consecutive_failures >= (task.max_consecutive_failures or 5)
    return {
        "id": task.id,
        "task_name": task.task_name,
        "signal_source": task.signal_source,
        "gateway": task.gateway,
        "interval": task.interval,
        "is_active": task.is_active,
        "is_frozen": is_frozen,
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
        "account_id": task.account_id,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        # 新增资金与统计字段
        "initial_balance": float(task.initial_balance) if task.initial_balance is not None else 1000.0,
        "current_balance": float(task.current_balance) if task.current_balance is not None else 1000.0,
        "total_pnl": float(task.total_pnl) if task.total_pnl is not None else 0.0,
        "total_trades": task.total_trades or 0,
        "win_trades": task.win_trades or 0,
        "loss_trades": task.loss_trades or 0,
        "win_rate": float(task.win_rate) if task.win_rate is not None else 0.0,
        "max_drawdown": float(task.max_drawdown) if task.max_drawdown is not None else 0.0,
        "sharpe_ratio": float(task.sharpe_ratio) if task.sharpe_ratio is not None else 0.0,
        "profit_loss_ratio": float(task.profit_loss_ratio) if task.profit_loss_ratio is not None else 0.0,
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


def _log_to_dict(log: AutoStrategyLog, db=None) -> dict:
    task_name = ""
    task = log.task
    if task:
        task_name = task.task_name
    elif db is not None:
        t = db.query(AutoStrategy).filter(AutoStrategy.id == log.task_id).first()
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
        "entry_price": float(log.entry_price) if (hasattr(log, 'entry_price') and log.entry_price is not None) else None,
        "exit_price": float(log.exit_price) if (hasattr(log, 'exit_price') and log.exit_price is not None) else None,
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
        log_entry = AutoStrategyLog(
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
        logger.error(f"[AutoStrategy] 记录操作日志失败: {e}, traceback={traceback.format_exc()}")


def _auto_redeem_resolved_positions(task: AutoStrategy) -> dict | None:
    """在任务执行前自动扫描并赎回已结算市场的持仓

    Args:
        task: AutoStrategy 模型实例

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
                logger.warning(f"[AutoStrategy-Redeem] list_positions 失败: {e}, 尝试回退方式")
                try:
                    fallback = loop.run_until_complete(
                        client.get_positions(size_greater_than=0.001)
                    )
                    if isinstance(fallback, list):
                        positions_list = fallback
                    elif isinstance(fallback, dict):
                        positions_list = fallback.get('data', []) or []
                except Exception as e2:
                    logger.warning(f"[AutoStrategy-Redeem] 回退方式也失败: {e2}")
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

            logger.info(f"[AutoStrategy-Redeem] 扫描到 {len(positions_list)} 个持仓")

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
                            f"[AutoStrategy-Redeem] 发现已结算持仓: {market_title or market_slug} "
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
                                    f"[AutoStrategy-Redeem] 市场查询失败: {market_slug}, 跳过"
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
                                    f"[AutoStrategy-Redeem] 发现已结算持仓: {market_title or market_slug} "
                                    f"token={token_id[:12]}... size={size}"
                                )
                            elif is_closed and not is_resolved:
                                logger.debug(
                                    f"[AutoStrategy-Redeem] 市场已关闭但未结算: {market_slug}，跳过"
                                )
                            else:
                                logger.debug(
                                    f"[AutoStrategy-Redeem] 市场未结算: {market_slug} "
                                    f"(closed={is_closed}, resolved={is_resolved})，跳过"
                                )
                        except Exception as inner_e:
                            logger.warning(
                                f"[AutoStrategy-Redeem] 查询市场 {market_slug} 失败: {inner_e}"
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
                    err_msg = f"检查持仓异常: {market_slug or token_id[:16]}... {e},traceback={traceback.format_exc()}"
                    logger.warning(f"[AutoStrategy-Redeem] {err_msg}")
                    errors.append(err_msg)

            if not to_redeem:
                return {
                    "redeemed_count": 0,
                    "msg": f"扫描 {len(positions_list)} 持仓，无已结算持仓需要赎回",
                    "errors": errors,
                }

            logger.info(
                f"[AutoStrategy-Redeem] 共 {len(to_redeem)} 个持仓可赎回，开始批量赎回..."
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
                            f"[AutoStrategy-Redeem] 赎回成功 condition_id={condition_id[:12]}..."
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
                            f"[AutoStrategy-Redeem] 按 token 赎回成功 {token_id[:12]}..."
                        )
                    else:
                        logger.debug(
                            f"[AutoStrategy-Redeem] 无可赎回条件: cid={condition_id[:12] if condition_id else 'N/A'} "
                            f"tid={token_id[:12] if token_id else 'N/A'}，跳过"
                        )
                except Exception as e:
                    err_msg = f"赎回失败 {p.get('market_slug') or token_id[:12]}...: {e},traceback={traceback.format_exc()}"
                    logger.warning(f"[AutoStrategy-Redeem] {err_msg}")
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
        logger.error(f"[AutoStrategy-Redeem] 整体异常: {e}, traceback={traceback.format_exc()}")
        return {
            "redeemed_count": 0,
            "msg": f"异常: {e},traceback={traceback.format_exc()}",
            "errors": [str(e)],
        }


# 回查所有未结算交易的市场结果并更新盈亏
def _check_and_update_previous_pnl(db, task: AutoStrategy) -> list[dict]:
    """回查所有未结算交易的市场结果并更新盈亏

    逻辑：
    1. 查找该任务所有 status=0/2 且 market_resolved=False 的执行日志
    2. 对每条日志，从 signal_detail_json 获取 market_slug 和 direction
    3. 通过 Polymarket API 查询该市场是否已结算
    4. 若已结算，判断结果并计算盈亏，更新日志

    Args:
        db: 数据库会话
        task: AutoStrategy 模型实例

    Returns:
        list[dict]: 所有已更新的结算结果列表
    """
    from sqlalchemy import and_

    # 查找所有未结算的执行日志（不限数量，遍历全部）
    unresolved_logs = (
        db.query(AutoStrategyLog)
        .filter(
            AutoStrategyLog.task_id == task.id,
            AutoStrategyLog.log_type == 0,
            AutoStrategyLog.status.in_([0, 2]),
            AutoStrategyLog.market_resolved == False,
        )
        .order_by(AutoStrategyLog.created_at.asc())
        .all()
    )

    if not unresolved_logs:
        return []

    # 只有 Polymarket 网关的任务才能回查
    if task.gateway != "polymarket_f3":
        return []

    logger.info(
        f"[AutoStrategy] 任务 {task.id} 发现 {len(unresolved_logs)} 条未结算日志，开始回查结算..."
    )

    # 查询市场状态（创建一次 client，遍历所有日志）
    updated_results = []
    pm_client = None
    event_loop = None

    try:
        import asyncio
        from fwsort.config import reload_env
        reload_env()

        from fwsort.gateway.polymarket.F3.最简类_下单代码 import pm类

        # 创建 client（复用已有的网关实例）
        pm = _gateway_instances.get(task.id)
        if pm is None:
            pm = pm类()
            _gateway_instances[task.id] = pm

        # 使用同一个事件循环来初始化 client 和查询市场
        event_loop = asyncio.new_event_loop()
        try:
            if pm.client is None:
                event_loop.run_until_complete(pm.初始化())

            pm_client = pm.client
            if pm_client is None:
                logger.warning(f"[AutoStrategy] 任务 {task.id} client 未初始化，跳过结算回查")
                return []

            for prev_log in unresolved_logs:
                try:
                    result = _try_resolve_single_log(event_loop, pm_client, prev_log, task, db)
                    if result is not None:
                        updated_results.append(result)
                except Exception as e:
                    logger.warning(
                        f"[AutoStrategy] 任务 {task.id} 结算回查日志 {prev_log.id} 异常: {e},traceback={traceback.format_exc()}"
                    )
                    continue

        finally:
            event_loop.close()

    except Exception as e:
        logger.warning(f"[AutoStrategy] 任务 {task.id} 盈亏回查异常: {e},traceback={traceback.format_exc()}")
        return updated_results

    # 提交所有更新
    if updated_results:
        db.commit()
        logger.info(
            f"[AutoStrategy] 💰 任务 {task.id} 结算回查完成: "
            f"成功更新 {len(updated_results)}/{len(unresolved_logs)} 条日志"
        )
    else:
        # 如果没有任何更新（市场均未结算），也提交可能的状态变化
        db.commit()

    return updated_results


def _try_resolve_single_log(event_loop, pm_client, prev_log, task, db) -> dict | None:
    """尝试对单条日志进行结算回查

    Args:
        event_loop: 已创建的事件循环
        pm_client: 已初始化的 Polymarket client
        prev_log: AutoStrategyLog 实例
        task: AutoStrategy 实例
        db: 数据库会话

    Returns:
        dict | None: 结算结果或 None
    """
    import asyncio

    # 跳过已结算的日志（防御性检查）
    if prev_log.market_resolved:
        return None

    # 解析信号详情获取市场信息
    try:
        signal_detail = json.loads(prev_log.signal_detail_json or "{}")
    except Exception:
        signal_detail = {}

    market_slug = signal_detail.get("market_slug", "")
    direction = signal_detail.get("direction", "")

    # 解析执行详情获取下单信息
    try:
        exec_detail = json.loads(prev_log.execution_detail_json or "{}")
    except Exception:
        exec_detail = {}

    making_amount = float(exec_detail.get("making_amount", 0) or 0)
    taking_amount = float(exec_detail.get("taking_amount", 0) or 0)

    if not market_slug:
        logger.debug(f"[AutoStrategy] 日志 {prev_log.id} 无 market_slug，跳过")
        return None

    # 查询市场
    try:
        market = event_loop.run_until_complete(pm_client.get_market(slug=market_slug))
    except Exception as e:
        logger.warning(f"[AutoStrategy] 日志 {prev_log.id} 市场查询异常: slug={market_slug} err={e},traceback={traceback.format_exc()}")
        return None

    if market is None:
        logger.warning(f"[AutoStrategy] 日志 {prev_log.id} 市场查询失败: slug={market_slug}")
        return None

    # 检查市场状态
    state = getattr(market, 'state', None)
    is_closed = getattr(state, 'closed', False) if state else False
    is_resolved = getattr(state, 'resolved', False) if state else False

    # 必须已结算（closed=True 且 resolved=True）才能计算盈亏
    if not is_closed or not is_resolved:
        return None

    # 市场已结算，判断结果
    outcomes = getattr(market, 'outcomes', None)
    if outcomes is None:
        return None

    yes_price = float(getattr(outcomes.yes, 'price', 0)) if outcomes.yes else 0
    no_price = float(getattr(outcomes.no, 'price', 0)) if outcomes.no else 0
    yes_won = yes_price >= 0.5

    is_buy_yes = direction.upper() in ("UP", "YES", "Y", "U", "BUY")
    we_won = (is_buy_yes and yes_won) or (not is_buy_yes and not yes_won)

    # 计算盈亏
    if making_amount > 0:
        if we_won:
            pnl_amount = taking_amount - making_amount
        else:
            pnl_amount = -making_amount
    else:
        pnl_amount = 0.0

    pnl_percent = (pnl_amount / making_amount * 100) if making_amount > 0 else 0.0
    is_profit = pnl_amount > 0

    # 更新日志
    prev_log.market_resolved = True
    prev_log.pnl_amount = round(pnl_amount, 6)
    prev_log.pnl_percent = round(pnl_percent, 4)
    prev_log.is_profit = is_profit

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

    # 回写账户
    _update_account_on_resolution(db, task, prev_log, pnl_amount)

    # 结算数据同步：更新 StrategyTrade、AutoStrategy 统计、净值曲线等
    try:
        from fwsort.strategy.settlement_service import sync_all_on_settlement
        sync_all_on_settlement(
            db=db,
            task=task,
            prev_log=prev_log,
            pnl_amount=pnl_amount,
            pnl_percent=pnl_percent,
            is_profit=is_profit,
            we_won=we_won,
            market_slug=market_slug,
            direction=direction,
        )
    except Exception as sync_err:
        logger.warning(f"[SettlementSync] 结算数据同步失败(不影响主流程): {sync_err}")

    resolution_result = {
        "log_id": prev_log.id,
        "pnl_amount": pnl_amount,
        "pnl_percent": pnl_percent,
        "is_profit": is_profit,
        "market_slug": market_slug,
        "yes_won": yes_won,
        "we_bet_direction": direction,
        "we_won": we_won,
    }

    logger.info(
        f"[AutoStrategy] 💰 日志 {prev_log.id} 结算: "
        f"market={market_slug} yes={yes_price:.3f} no={no_price:.3f} "
        f"dir={direction} won={we_won} pnl=${pnl_amount:.4f} ({pnl_percent:.2f}%)"
    )

    return resolution_result


def _update_account_on_resolution(db, task: AutoStrategy, prev_log, pnl_amount: float):
    """结算回写：更新 ExecutionAccount 余额和盈亏"""
    try:
        if not task.account_id:
            return
        account = db.query(ExecutionAccount).filter(ExecutionAccount.id == task.account_id).first()
        if not account:
            return
        pnl_decimal = Decimal(str(pnl_amount))
        account.current_balance = (account.current_balance or 0) + pnl_decimal
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        if prev_log.executed_at and prev_log.executed_at >= today:
            account.daily_pnl = (account.daily_pnl or 0) + pnl_decimal
        if prev_log.executed_at and not account.last_order_at:
            account.last_order_at = prev_log.executed_at
        logger.info(
            f"[AutoStrategy] 💰 账户 {account.uid} 结算回写: "
            f"pnl={pnl_amount:+.4f} balance={float(account.current_balance):.4f}"
        )
    except Exception as e:
        logger.warning(f"[AutoStrategy] 账户结算回写失败: {e},traceback={traceback.format_exc()}")


def update_settlement_for_task(task_id: int) -> dict:
    """手动触发任务的结算回查（供 API 和 stop_task 调用）

    Args:
        task_id: 任务ID

    Returns:
        dict: 更新结果摘要
    """
    with get_sync_db() as db:
        task = db.query(AutoStrategy).filter(AutoStrategy.id == task_id).first()
        if not task:
            return {"success": False, "message": f"任务不存在: {task_id}", "updated_count": 0}

        updated = _check_and_update_previous_pnl(db, task)

        total_unresolved = (
            db.query(AutoStrategyLog)
            .filter(
                AutoStrategyLog.task_id == task_id,
                AutoStrategyLog.log_type == 0,
                AutoStrategyLog.status.in_([0, 2]),
                AutoStrategyLog.market_resolved == False,
            )
            .count()
        )

        return {
            "success": True,
            "task_id": task_id,
            "task_name": task.task_name,
            "updated_count": len(updated),
            "remaining_unresolved": total_unresolved,
            "updated_results": updated,
            "message": f"结算回查完成: 更新 {len(updated)} 条，剩余 {total_unresolved} 条未结算",
        }


def get_strategy_leaderboard(sort_by: str = "win_rate", sort_dir: str = "desc") -> list[dict]:
    """策略排行榜：按策略名称(signal_source)为基准，合并文件夹策略与数据库交易数据

    数据来源：
    1. 文件夹策略：strategy/providers/ 下所有继承 StrategyBase 的策略 (list_providers())
    2. 数据库策略：auto_strategy 表中已配置的任务
    3. 交易数据：auto_strategy_log 表中的执行记录

    状态标识：
    - active:   文件夹存在 + 数据库有交易数据
    - no_data:  文件夹存在 + 数据库无交易数据（尚未开单）
    - removed:  文件夹不存在 + 数据库有交易数据（策略已移除）

    统计规则：
    - 总下单次数：所有成功执行的订单（status in [0,2]）
    - 胜负判定：仅统计已结算（market_resolved=True）的订单
    - 胜率 = 胜利次数 / (胜利 + 亏损) * 100

    Args:
        sort_by: 排序字段 (win_rate/win_count/loss_count/total_trades/total_pnl/profit_loss_ratio)
        sort_dir: asc / desc

    Returns:
        list[dict]: 排行榜列表
    """
    from sqlalchemy import func, case

    from fwsort.strategy.manager import list_providers, get_provider_info

    # 获取文件夹中所有策略 provider
    provider_names = set(list_providers())
    provider_info_map = {}
    for name in provider_names:
        info = get_provider_info(name)
        if info:
            provider_info_map[name] = info

    with get_sync_db() as db:
        # ===== 1. 获取数据库中所有策略配置（按 signal_source 分组）=====
        db_strategies = (
            db.query(AutoStrategy)
            .filter(AutoStrategy.deleted_at.is_(None))
            .all()
        )

        # 按 signal_source 分组，聚合任务基本信息
        db_source_map: dict[str, dict] = {}
        for s in db_strategies:
            src = s.signal_source
            if src not in db_source_map:
                db_source_map[src] = {
                    "signal_source": src,
                    "task_ids": [],
                    "task_names": [],
                    "gateways": set(),
                    "is_active_any": False,
                    "total_executions": 0,
                }
            db_source_map[src]["task_ids"].append(s.id)
            db_source_map[src]["task_names"].append(s.task_name)
            db_source_map[src]["gateways"].add(s.gateway)
            if s.is_active:
                db_source_map[src]["is_active_any"] = True
            db_source_map[src]["total_executions"] += s.total_executions or 0

        # ===== 2. 构建 task_id → signal_source 映射（用于日志统计）=====
        task_to_source: dict[int, str] = {}
        for s in db_strategies:
            task_to_source[s.id] = s.signal_source

        # ===== 3. 总下单次数（按 signal_source 分组）=====
        total_trades_rows = (
            db.query(
                AutoStrategyLog.task_id,
                func.count(AutoStrategyLog.id).label("total_trades"),
                func.max(AutoStrategyLog.executed_at).label("last_trade_at"),
                func.min(AutoStrategyLog.executed_at).label("first_trade_at"),
            )
            .filter(
                AutoStrategyLog.log_type == 0,
                AutoStrategyLog.status.in_([0, 2]),
            )
            .group_by(AutoStrategyLog.task_id)
            .all()
        )
        # 按 signal_source 汇总
        source_trades_map: dict[str, dict] = {}
        for row in total_trades_rows:
            src = task_to_source.get(row.task_id)
            if not src:
                continue
            if src not in source_trades_map:
                source_trades_map[src] = {
                    "total_trades": 0,
                    "last_trade_at": None,
                    "first_trade_at": None,
                }
            source_trades_map[src]["total_trades"] += row.total_trades or 0
            if row.last_trade_at:
                if not source_trades_map[src]["last_trade_at"] or row.last_trade_at > source_trades_map[src]["last_trade_at"]:
                    source_trades_map[src]["last_trade_at"] = row.last_trade_at
            if row.first_trade_at:
                if not source_trades_map[src]["first_trade_at"] or row.first_trade_at < source_trades_map[src]["first_trade_at"]:
                    source_trades_map[src]["first_trade_at"] = row.first_trade_at

        # ===== 4. 已结算交易统计（按 signal_source 分组）=====
        resolved_rows = (
            db.query(
                AutoStrategyLog.task_id,
                func.sum(case((AutoStrategyLog.is_profit == True, 1), else_=0)).label("win_count"),
                func.sum(case((AutoStrategyLog.is_profit == False, 1), else_=0)).label("loss_count"),
                func.sum(AutoStrategyLog.pnl_amount).label("total_pnl"),
                func.avg(AutoStrategyLog.pnl_amount).label("avg_pnl"),
            )
            .filter(
                AutoStrategyLog.log_type == 0,
                AutoStrategyLog.status.in_([0, 2]),
                AutoStrategyLog.market_resolved == True,
            )
            .group_by(AutoStrategyLog.task_id)
            .all()
        )
        source_resolved_map: dict[str, dict] = {}
        for row in resolved_rows:
            src = task_to_source.get(row.task_id)
            if not src:
                continue
            if src not in source_resolved_map:
                source_resolved_map[src] = {"win_count": 0, "loss_count": 0, "total_pnl": 0.0, "avg_pnl_sum": 0.0, "resolved_count": 0}
            source_resolved_map[src]["win_count"] += row.win_count or 0
            source_resolved_map[src]["loss_count"] += row.loss_count or 0
            source_resolved_map[src]["total_pnl"] += float(row.total_pnl) if row.total_pnl else 0.0
            cnt = (row.win_count or 0) + (row.loss_count or 0)
            source_resolved_map[src]["resolved_count"] += cnt

        # ===== 5. 盈亏比统计（按 signal_source 分组）=====
        pnl_rows = (
            db.query(
                AutoStrategyLog.task_id,
                func.sum(case((AutoStrategyLog.pnl_amount > 0, AutoStrategyLog.pnl_amount), else_=0)).label("gross_profit"),
                func.sum(case((AutoStrategyLog.pnl_amount < 0, func.abs(AutoStrategyLog.pnl_amount)), else_=0)).label("gross_loss"),
            )
            .filter(
                AutoStrategyLog.log_type == 0,
                AutoStrategyLog.status.in_([0, 2]),
                AutoStrategyLog.market_resolved == True,
            )
            .group_by(AutoStrategyLog.task_id)
            .all()
        )
        source_pnl_map: dict[str, dict] = {}
        for row in pnl_rows:
            src = task_to_source.get(row.task_id)
            if not src:
                continue
            if src not in source_pnl_map:
                source_pnl_map[src] = {"gross_profit": 0.0, "gross_loss": 0.0}
            source_pnl_map[src]["gross_profit"] += float(row.gross_profit) if row.gross_profit else 0.0
            source_pnl_map[src]["gross_loss"] += float(row.gross_loss) if row.gross_loss else 0.0

    # ===== 6. 合并：以文件夹策略为基准，加入数据库策略 =====
    all_sources = provider_names | set(db_source_map.keys())

    result = []
    for source_name in sorted(all_sources):
        tt = source_trades_map.get(source_name, {})
        resolved = source_resolved_map.get(source_name, {})
        pnl = source_pnl_map.get(source_name, {})
        db_info = db_source_map.get(source_name, {})
        prov_info = provider_info_map.get(source_name, {})

        total = tt.get("total_trades", 0)
        wins = resolved.get("win_count", 0)
        losses = resolved.get("loss_count", 0)
        resolved_count = wins + losses
        total_pnl_val = resolved.get("total_pnl", 0.0)
        avg_pnl_val = total_pnl_val / resolved_count if resolved_count > 0 else 0.0

        win_rate = round(wins / resolved_count * 100, 2) if resolved_count > 0 else 0.0

        gross_profit = pnl.get("gross_profit", 0.0)
        gross_loss = pnl.get("gross_loss", 0.0)
        profit_loss_ratio = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (float('inf') if gross_profit > 0 else 0.0)

        last_trade = tt.get("last_trade_at")
        first_trade = tt.get("first_trade_at")

        # 判定策略状态
        is_in_folder = source_name in provider_names
        is_in_db = source_name in db_source_map

        if is_in_folder and is_in_db:
            provider_status = "active"  # 正常使用中
        elif is_in_folder and not is_in_db:
            provider_status = "no_data"  # 文件夹有但未建自动任务
        elif not is_in_folder and is_in_db:
            provider_status = "past"  # 过去的策略（文件夹已删除）
        else:
            provider_status = "unknown"

        # 显示名称：策略名 (source_name)
        display_name = source_name
        if prov_info:
            display_name = prov_info.get("name", source_name)

        # 类别
        category = prov_info.get("category", "custom") if prov_info else "unknown"

        result.append({
            "strategy_name": source_name,
            "display_name": display_name,
            "category": category,
            "provider_status": provider_status,  # active/no_data/removed
            "status_label": {
                "active": "正常",
                "no_data": "尚未开单",
                "past": "过去策略",
                "unknown": "未知",
            }.get(provider_status, provider_status),
            "is_active": db_info.get("is_active_any", False),
            "task_count": len(db_info.get("task_ids", [])),
            "task_names": db_info.get("task_names", []),
            "gateways": sorted(db_info.get("gateways", set())),
            "total_trades": total,
            "win_count": wins,
            "loss_count": losses,
            "win_rate": win_rate,
            "total_pnl": round(total_pnl_val, 2),
            "avg_pnl": round(avg_pnl_val, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "profit_loss_ratio": profit_loss_ratio if profit_loss_ratio != float('inf') else 999.99,
            "first_trade_at": first_trade.isoformat() if first_trade else None,
            "last_trade_at": last_trade.isoformat() if last_trade else None,
            "open_trades": total - resolved_count,
        })

    # 排序
    sort_map = {
        "win_rate": "win_rate",
        "win_count": "win_count",
        "loss_count": "loss_count",
        "total_trades": "total_trades",
        "total_pnl": "total_pnl",
        "avg_pnl": "avg_pnl",
        "profit_loss_ratio": "profit_loss_ratio",
        "strategy_name": "strategy_name",
    }
    sort_field = sort_map.get(sort_by, "win_rate")
    result.sort(key=lambda x: x.get(sort_field, 0), reverse=(sort_dir != "asc"))

    return result