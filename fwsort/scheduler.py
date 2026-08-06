# Celery 定时任务：榜单刷新 / 日榜快照 / 数据清理（架构文档 4.3.6）
import random
import time
from datetime import datetime, timedelta

from celery import Celery
from celery.schedules import crontab
from fwsort.fwlogs import logger

from fwsort.config import settings
from fwsort.database import get_sync_db
from fwsort.models import RankSnapshot, StrategyPerformance
from fwsort.ranking_engine import composite_score
from fwsort.redis_client import RankType, rank_key, sync_redis
from fwsort.risk.service import RiskControlService

# ========== Celery 实例 ==========
celery_app = Celery(
    "fwsort",
    broker=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}",
    backend=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}",
)
celery_app.conf.update(
    timezone="Asia/Shanghai",
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    beat_schedule={
        # 实时榜：每分钟刷新
        "refresh-realtime-rank": {
            "task": "fwsort.scheduler.refresh_realtime_rank",
            "schedule": crontab(minute="*"),
        },
        # 日榜快照：每日 00:05
        "daily-snapshot": {
            "task": "fwsort.scheduler.daily_snapshot",
            "schedule": crontab(hour=0, minute=5),
        },
        # 数据清理：每日 03:00
        "daily-cleanup": {
            "task": "fwsort.scheduler.daily_cleanup",
            "schedule": crontab(hour=3, minute=0),
        },
        # 数据归档：每日 03:30（订单日志 90 天热→冷）
        "archive-hot-to-cold": {
            "task": "fwsort.scheduler.archive_hot_to_cold",
            "schedule": crontab(hour=3, minute=30),
        },
        # 跟单自动同步：每 5 分钟
        "follow-auto-copy": {
            "task": "fwsort.scheduler.follow_auto_copy",
            "schedule": crontab(minute="*/5"),
        },
        # 通知扫描：每 10 分钟
        "notify-scan": {
            "task": "fwsort.scheduler.notify_scan",
            "schedule": crontab(minute="*/10"),
        },
        # 账户信号刷新：每 5 分钟
        "refresh-account-signals": {
            "task": "fwsort.scheduler.refresh_account_signals",
            "schedule": crontab(minute="*/5"),
        },
        # 全账户预测-投票-下单：每 1 分钟（V1.0 自动化流水线）
        "auto-predict-vote-trade": {
            "task": "fwsort.scheduler.auto_predict_vote_trade",
            "schedule": crontab(minute="*"),
        },
        # WP-09：outbox 消费：每 1 分钟（订单日志投递到 ES）
        # Celery crontab 不支持 second 字段；分钟级足够覆盖 30s 内的延迟
        "flush-outbox": {
            "task": "fwsort.scheduler.flush_outbox",
            "schedule": crontab(minute="*"),
        },
        # 自动任务调度器：每分钟扫描一次，根据任务 interval 触发
        "auto-task-dispatcher": {
            "task": "fwsort.scheduler.auto_strategy_dispatcher",
            "schedule": crontab(minute="*"),
        },
        # 绩效聚合：每 5 分钟从 AutoStrategyLog 聚合到 StrategyPerformance
        "aggregate-performance": {
            "task": "fwsort.scheduler.aggregate_performance",
            "schedule": crontab(minute="*/5"),
        },
    },
)

# 任务状态键：记录最近一次执行时间和结果（用 Redis 持久）
TASK_STATUS_KEY = "fwsort:task:status"


# ========== 任务 1：实时榜刷新（Redis ZSet）==========
@celery_app.task(name="fwsort.scheduler.refresh_realtime_rank")
def refresh_realtime_rank() -> int:
    """每分钟从数据库读取所有执行账户的 composite_score，写入 Redis ZSet"""
    count = 0
    with get_sync_db() as db:
        perfs = db.query(StrategyPerformance).filter(StrategyPerformance.period_type == 4).all()
        key = rank_key(RankType.REALTIME)
        # 清空再写入（也可增量 ZADD，但周期短直接重置更简单）
        sync_redis.delete(key)
        for p in perfs:
            sync_redis.zadd(key, {p.uid: float(p.composite_score)})
            count += 1
    logger.info(f"[scheduler] realtime rank refreshed: {count} entries")
    return count


# ========== 任务 2：日榜快照 ==========
@celery_app.task(name="fwsort.scheduler.daily_snapshot")
def daily_snapshot() -> int:
    """每日 00:05 把当期榜单快照固化到 PostgreSQL"""
    count = 0
    period_end = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    with get_sync_db() as db:
        perfs = (
            db.query(StrategyPerformance)
            .filter(StrategyPerformance.period_type == 1)
            .order_by(StrategyPerformance.composite_score.desc())
            .all()
        )
        for rank_idx, p in enumerate(perfs, start=1):
            snap = RankSnapshot(
                rank_type=1,  # 日榜
                period_end_time=period_end,
                uid=p.uid,
                rank=rank_idx,
                score=float(p.composite_score),
                execution_score=float(p.execution_score),
                annualized_return=float(p.annualized_return),
                max_drawdown=float(p.max_drawdown),
                trade_count=p.trade_count,
            )
            db.add(snap)
            count += 1
    logger.info(f"[scheduler] daily snapshot saved: {count} rows")
    return count


# ========== 任务 3：每日数据清理 ==========
@celery_app.task(name="fwsort.scheduler.daily_cleanup")
def daily_cleanup() -> int:
    """清理过期缓存（订单日志归档在阶段 3 做）"""
    removed = 0
    # 清理临时缓存键（fwsort:tmp:* 命名空间）
    for key in sync_redis.scan_iter(match="fwsort:tmp:*", count=100):
        sync_redis.delete(key)
        removed += 1
    logger.info(f"[scheduler] cleanup removed {removed} temp keys")
    return removed


# ========== 任务 4：数据归档（90 天热→冷）==========
@celery_app.task(name="fwsort.scheduler.archive_hot_to_cold")
def archive_hot_to_cold() -> dict:
    """订单执行日志归档：把超过 ORDER_LOG_HOT_DAYS 的旧数据从 PG 迁到 ES（冷存）"""
    import asyncio
    from datetime import datetime

    from fwsort.es_client import get_es_client

    es = get_es_client()
    if es is None:
        return {"archived": 0, "failed": 0, "error": "ES client not initialized"}

    cutoff = datetime.now() - timedelta(days=settings.ORDER_LOG_HOT_DAYS)

    async def _run():
        archived = 0
        failed = 0
        with get_sync_db() as db:
            from fwsort.models import OrderExecutionLog

            old_rows = (
                db.query(OrderExecutionLog)
                .filter(OrderExecutionLog.created_at < cutoff)
                .limit(5000)
                .all()
            )
            for r in old_rows:
                try:
                    await es.index(
                        index=f"{settings.ES_INDEX_ORDER_LOG}_archive",
                        id=r.order_id,
                        document={
                            "uid": r.uid,
                            "order_id": r.order_id,
                            "platform": r.platform,
                            "symbol": r.symbol,
                            "side": r.side,
                            "amount_usd": float(r.amount_usd),
                            "actual_price": float(r.actual_price),
                            "pnl": float(r.pnl),
                            "status": r.status,
                            "created_at": r.created_at.isoformat() if r.created_at else None,
                            "archived_at": datetime.now().isoformat(),
                        },
                    )
                    db.delete(r)
                    archived += 1
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    logger.warning(f"archive row {r.order_id} failed: {e}")
            db.commit()
        return archived, failed

    try:
        archived, failed = asyncio.run(_run())
        logger.info(f"[scheduler] archive done: {archived} ok, {failed} failed")
    except Exception as e:  # noqa: BLE001
        logger.error(f"[scheduler] archive failed: {e}")
        return {"archived": 0, "failed": 0, "error": str(e)}

    return {"archived": archived, "failed": failed}


# ========== 任务 5：跟单自动同步 ==========
@celery_app.task(name="fwsort.scheduler.follow_auto_copy")
def follow_auto_copy() -> int:
    """每 5 分钟扫描所有有效订阅，复用最近一笔 leader 订单信号给粉丝"""
    from fwsort.models import ExecutionAccount, FollowOrder, FollowSubscription, OrderExecutionLog

    # 单元测试/CI 场景下表结构可能未初始化，容错兜底
    try:
        copied = 0
        with get_sync_db() as db:
            subs = db.query(FollowSubscription).filter(FollowSubscription.status == 1).all()
            for s in subs:
                # 找 leader 账户
                leader_acc = db.query(ExecutionAccount).filter(ExecutionAccount.uid == s.leader_uid).first()
                if not leader_acc:
                    continue
                # 最近 5 分钟的 leader 订单
                recent = (
                    db.query(OrderExecutionLog)
                    .filter(
                        OrderExecutionLog.account_id == leader_acc.id,
                        OrderExecutionLog.status == 3,
                        OrderExecutionLog.created_at > datetime.now() - timedelta(minutes=5),
                    )
                    .order_by(OrderExecutionLog.created_at.desc())
                    .first()
                )
                if not recent:
                    continue
                # 是否已跟单过
                dup = (
                    db.query(FollowOrder)
                    .filter(FollowOrder.subscription_id == s.id, FollowOrder.leader_order_id == recent.order_id)
                    .first()
                )
                if dup:
                    continue
                # 算粉丝 pnl（按比例缩放）
                scale = float(s.follow_amount_usd) / float(recent.amount_usd) if recent.amount_usd else 1
                pnl = float(recent.pnl) * scale
                share = max(pnl, 0) * float(s.profit_share_ratio) if s.mode in (2, 3) else 0
                db.add(
                    FollowOrder(
                        subscription_id=s.id,
                        leader_order_id=recent.order_id,
                        symbol=recent.symbol,
                        side=recent.side,
                        amount_usd=s.follow_amount_usd,
                        expected_price=float(recent.expected_price),
                        actual_price=float(recent.actual_price),
                        pnl=pnl,
                        share_paid=share,
                        status=3,
                    )
                )
                s.total_followed += 1
                s.total_pnl = float(s.total_pnl) + pnl
                s.total_share_paid = float(s.total_share_paid) + share
                copied += 1
        logger.info(f"[scheduler] follow auto copy: {copied} orders")
        return copied
    except Exception as e:  # noqa: BLE001
        # 表结构未初始化/数据库异常 → 安全降级（不影响主流程）
        msg = f"{type(e).__name__}: {str(e)[:120]}"
        if "no such table" in msg or "relation" in msg or "UndefinedTableError" in msg:
            logger.warning(f"[scheduler] follow_auto_copy: table not ready, skip (init_db first): {msg}")
        else:
            logger.warning(f"[scheduler] follow_auto_copy error: {msg}")
        return 0


# ========== 任务 6：通知扫描（风控冻结/榜单异动/订阅到期）==========
@celery_app.task(name="fwsort.scheduler.notify_scan")
def notify_scan() -> int:
    """每 10 分钟扫一次系统状态，发现异常推通知"""
    from fwsort.models import ExecutionAccount, FollowSubscription, Notification
    from fwsort.risk.models import AccountRiskProfile

    pushed = 0
    now = datetime.now()
    with get_sync_db() as db:
        # 1) 风控冻结通知（从真源 AccountRiskProfile 查，镜像字段也能查到以兼容）
        frozen_rows = (
            db.query(AccountRiskProfile, ExecutionAccount)
            .outerjoin(ExecutionAccount, AccountRiskProfile.account_id == ExecutionAccount.id)
            .filter(AccountRiskProfile.is_frozen == True)  # noqa: E712
            .all()
        )
        if not frozen_rows:
            # 兜底：兼容老数据（直接从 ExecutionAccount.risk_frozen 查）
            acc_frozen = db.query(ExecutionAccount).filter(ExecutionAccount.risk_frozen == True).all()  # noqa: E712
            for a in acc_frozen:
                recent = (
                    db.query(Notification)
                    .filter(Notification.user_id == a.owner_id, Notification.ntype == 3, Notification.content.like(f"%{a.uid}%"))
                    .filter(Notification.created_at > now - timedelta(days=1))
                    .first()
                )
                if not recent:
                    db.add(Notification(user_id=a.owner_id, ntype=3, title="风控冻结", content=f"账户 {a.uid} 已被风控冻结（兼容旧字段）"))
                    pushed += 1
        for rp, a in frozen_rows:
            if a is None:
                continue
            recent = (
                db.query(Notification)
                .filter(Notification.user_id == a.owner_id, Notification.ntype == 3, Notification.content.like(f"%{a.uid}%"))
                .filter(Notification.created_at > now - timedelta(days=1))
                .first()
            )
            if not recent:
                reason = rp.frozen_reason or "风控自动冻结"
                db.add(Notification(
                    user_id=a.owner_id, ntype=3,
                    title="风控冻结",
                    content=f"账户 {a.uid} 因风控触发已被冻结：{reason}",
                ))
                pushed += 1
        # 2) 订阅 7 天内到期
        soon = db.query(FollowSubscription).filter(
            FollowSubscription.status == 1,
            FollowSubscription.expires_at != None,  # noqa: E711
            FollowSubscription.expires_at < now + timedelta(days=7),
            FollowSubscription.expires_at > now,
        ).all()
        for s in soon:
            db.add(Notification(user_id=s.subscriber_id, ntype=2, title="订阅即将到期", content=f"对 {s.leader_uid} 的订阅将在 {s.expires_at.strftime('%Y-%m-%d')} 到期，请续订"))
            pushed += 1
    logger.info(f"[scheduler] notify scan pushed: {pushed}")
    return pushed


# ========== 任务 7：账户信号刷新（每 5 分钟）==========
@celery_app.task(name="fwsort.scheduler.refresh_account_signals")
def refresh_account_signals() -> dict:
    """给所有 status=0 的执行账户生成一次信号，更新 account.signal 等字段"""
    from datetime import datetime

    from fwsort.models import ExecutionAccount
    from fwsort.strategy.generator import generate_signal

    updated = 0
    failed = 0
    started = datetime.now()
    with get_sync_db() as db:
        accounts = db.query(ExecutionAccount).filter(ExecutionAccount.status == 0).all()
        for a in accounts:
            try:
                source = a.signal_source if a.signal_source in ("random", "gpt-4o", "claude", "gemini", "moa") else "random"
                a.signal = generate_signal(source=source)
                a.signal_source = source
                a.signal_updated_at = datetime.now()
                updated += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                logger.warning(f"refresh signal for {a.uid} failed: {e}")
    result = {"updated": updated, "failed": failed, "started_at": started.isoformat()}
    _record_task_status("refresh_account_signals", "ok", result)
    logger.info(f"[scheduler] refresh_account_signals: {result}")
    return result


# ========== 任务 8：全账户预测-投票-下单（V1.0 流水线，每 1 分钟）==========
@celery_app.task(name="fwsort.scheduler.auto_predict_vote_trade")
def auto_predict_vote_trade() -> dict:
    """对所有激活执行账户跑一次 V1.0 流水线：
    signal→MoA 预测→投票→ExecutionGateway 下单
    """
    import asyncio
    from datetime import datetime

    from fwsort.agents.hermes_moa import build_hermes_moa
    from fwsort.gateway.gateway import get_gateway
    from fwsort.models import (
        AgentPrediction,
        ExecutionAccount,
        OrderExecutionLog,
        VoteDecision,
    )
    from fwsort.strategy.generator import signal_to_direction
    from fwsort.voting import vote

    started = datetime.now()
    moa = build_hermes_moa()
    gateway = get_gateway()

    success_count = 0
    skip_count = 0
    fail_count = 0
    with get_sync_db() as db:
        accounts = db.query(ExecutionAccount).filter(
            ExecutionAccount.status == 0,
        ).all()
        # 过滤：从统一风控真源排除冻结账户（同时镜像 ExecutionAccount.risk_frozen 以兼容）
        active_accounts = []
        for a in accounts:
            frozen, _ = RiskControlService.is_account_frozen(db, a.id)
            if frozen:
                # 确保镜像字段一致
                if not a.risk_frozen:
                    a.risk_frozen = True
                fail_count += 1
                continue
            # 反向同步：若风控表未冻结但 ExecutionAccount.risk_frozen=True，解除镜像
            if a.risk_frozen:
                a.risk_frozen = False
            active_accounts.append(a)
        db.flush()

        for acc in active_accounts:
            try:
                symbol = acc.target_symbol or "BTC-USDT"
                # 已无信号则跳过
                direction = signal_to_direction(acc.signal or "NEUTRAL")
                if direction == 0:
                    skip_count += 1
                    continue
                # 跑 MoA 异步
                moa_result = asyncio.run(moa.aggregate(symbol, settings.PREDICTION_TIMEFRAME))
                db_preds = []
                for p in moa_result.layer1_results:
                    ap = AgentPrediction(
                        agent_name=p.agent_name,
                        agent_model=p.agent_model,
                        symbol=p.symbol,
                        timeframe=p.timeframe,
                        direction=p.direction,
                        confidence=p.confidence,
                        reasoning=p.reasoning,
                        raw_payload=p.raw_payload,
                        latency_ms=p.latency_ms,
                    )
                    db.add(ap)
                    db_preds.append(ap)
                db.flush()
                directions = [p.direction for p in moa_result.layer1_results]
                # 投票前先统一跑风控
                pre_risk, _ = RiskControlService.check_before_vote(
                    db,
                    account_id=acc.id,
                    account_balance=float(acc.current_balance),
                    daily_pnl=float(acc.daily_pnl),
                    initial_balance=float(acc.initial_balance),
                    proposed_amount=settings.ORDER_DOUBLE_USD,
                    user_id=acc.owner_id,
                )
                if pre_risk.should_freeze:
                    fail_count += 1
                    continue

                v = vote(
                    directions=directions,
                    account_balance=float(acc.current_balance),
                    daily_pnl=float(acc.daily_pnl),
                    initial_balance=float(acc.initial_balance),
                )
                if "risk_freeze" in v.reason:
                    # 兼容旧 reason 字符串：走统一冻结入口
                    RiskControlService.freeze_account(
                        db, acc.id, reason=v.reason,
                        rule_name="SchedulerVoteDailyLoss",
                        operator_user_id=acc.owner_id,
                    )
                    fail_count += 1
                    continue
                vote_row = VoteDecision(
                    account_id=acc.id,
                    symbol=symbol,
                    timeframe=settings.PREDICTION_TIMEFRAME,
                    up_count=v.up_count,
                    down_count=v.down_count,
                    flat_count=v.flat_count,
                    final_direction=v.final_direction,
                    order_amount_usd=v.order_amount_usd,
                    order_amount_reason=v.reason,
                    prediction_ids=",".join(str(p.id) for p in db_preds),
                )
                db.add(vote_row)
                db.flush()
                # 下单
                if v.final_direction != 0 and v.order_amount_usd > 0:
                    res = asyncio.run(gateway.submit(
                        account_type=acc.account_type,
                        platform=acc.platform,
                        symbol=symbol,
                        side=v.final_direction,
                        amount_usd=v.order_amount_usd,
                    ))
                    log = OrderExecutionLog(
                        uid=acc.uid,
                        account_id=acc.id,
                        vote_id=vote_row.id,
                        order_id=res.order_id or f"FAIL-{int(time.time()*1000)}",
                        order_type=2,
                        side=res.side,
                        platform=res.platform,
                        symbol=res.symbol,
                        expected_price=res.expected_price,
                        actual_price=res.actual_price,
                        quantity=res.quantity,
                        amount_usd=res.amount_usd,
                        status=res.status,
                        latency_ms=res.latency_ms,
                        slippage=res.slippage,
                        pnl=0.0,
                    )
                    db.add(log)
                    acc.last_order_at = datetime.now()
                success_count += 1
            except Exception as e:  # noqa: BLE001
                fail_count += 1
                logger.warning(f"auto_predict_vote_trade for {acc.uid} failed: {e}")
    result = {
        "success": success_count,
        "skipped": skip_count,
        "failed": fail_count,
        "started_at": started.isoformat(),
    }
    _record_task_status("auto_predict_vote_trade", "ok", result)
    logger.info(f"[scheduler] auto_predict_vote_trade: {result}")
    return result


# ========== 任务 9：Outbox 消费（WP-09：订单日志投递 ES）==========
@celery_app.task(name="fwsort.scheduler.flush_outbox")
def flush_outbox() -> dict:
    """WP-09：把 outbox_event 表中 status=0/2 的事件投递到 Elasticsearch
    - 失败重试：指数退避（1 / 2 / 4 分钟），最多 3 次后转长退避
    - 进程崩溃恢复：重启后从 status=0/2 继续消费
    """
    from fwsort.order_log.outbox import flush_outbox_sync

    result = flush_outbox_sync()
    # flush_outbox_sync 内部已写 Redis HASH，这里再调用一次 _record_task_status 保持统一格式
    _record_task_status("flush_outbox", "ok", result)
    return result


# ========== 任务 11：绩效聚合（AutoStrategyLog → StrategyPerformance）==========
@celery_app.task(name="fwsort.scheduler.aggregate_performance")
def aggregate_performance() -> dict:
    """每 5 分钟从 AutoStrategyLog 聚合到 StrategyPerformance，重算 composite_score"""
    try:
        from fwsort.strategy.performance_aggregator import aggregate_all_accounts
        result = aggregate_all_accounts()
        _record_task_status("aggregate_performance", "ok", result)
        return result
    except Exception as e:  # noqa: BLE001
        logger.error(f"[scheduler] aggregate_performance failed: {e}")
        _record_task_status("aggregate_performance", "error", {"error": str(e)})
        return {"updated": 0, "failed": 0, "error": str(e)}


# ========== 工具：记录任务状态到 Redis（供 /api/agent/tasks 查询）==========
def _record_task_status(task_name: str, status: str, payload: dict) -> None:
    """把任务最近一次执行状态写入 Redis HASH"""
    import json as _json
    from datetime import datetime

    field_map = {
        "status": status,
        "last_run_at": datetime.now().isoformat(),
        "last_result": _json.dumps(payload, ensure_ascii=False),
    }
    try:
        sync_redis.hset(TASK_STATUS_KEY, task_name, _json.dumps(field_map, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"record task status {task_name} failed: {e}")


def get_all_task_status() -> list[dict]:
    """读取所有任务最近一次执行状态（前端 /accounts/tasks 用）"""
    import json as _json

    task_names = [
        "refresh_realtime_rank",
        "daily_snapshot",
        "daily_cleanup",
        "archive_hot_to_cold",
        "follow_auto_copy",
        "notify_scan",
        "refresh_account_signals",
        "auto_predict_vote_trade",
        "flush_outbox",  # WP-09
        "auto_strategy_dispatcher",  # 自动任务调度器
        "aggregate_performance",  # 绩效聚合
    ]
    result: list[dict] = []
    try:
        raw = sync_redis.hgetall(TASK_STATUS_KEY) or {}
    except Exception:
        raw = {}
    for name in task_names:
        rec: dict = {"task": name, "status": "unknown", "last_run_at": None, "last_result": None}
        if isinstance(raw, dict) and name in raw:
            try:
                rec = _json.loads(raw[name])
                rec["task"] = name
            except Exception:
                pass
        result.append(rec)
    return result


# ========== 工具：MOCK 计算综合分（无真实交易时填充榜单）==========
def mock_fill_rankings(n: int = 30) -> None:
    """开发期填充 MOCK 绩效到数据库，让榜单有数据可看"""
    with get_sync_db() as db:
        from fwsort.models import ExecutionAccount

        # 找出现有账户
        accounts = db.query(ExecutionAccount).all()
        if not accounts:
            logger.info("[mock] no execution accounts, skip")
            return
        for acc in accounts:
            ann = round(random.uniform(-0.1, 1.2), 4)
            dd = round(random.uniform(0.02, 0.3), 4)
            sharpe = round(random.uniform(0.3, 3.0), 2)
            plr = round(random.uniform(0.8, 3.0), 2)
            ex = round(random.uniform(0.6, 0.95), 4)
            score = composite_score(ann, dd, sharpe, plr, ex)
            # upsert
            existing = (
                db.query(StrategyPerformance)
                .filter(StrategyPerformance.account_id == acc.id, StrategyPerformance.period_type == 4)
                .first()
            )
            if existing:
                existing.annualized_return = ann
                existing.max_drawdown = dd
                existing.sharpe_ratio = sharpe
                existing.profit_loss_ratio = plr
                existing.execution_score = ex
                existing.composite_score = score
                existing.trade_count = random.randint(100, 1500)
            else:
                db.add(
                    StrategyPerformance(
                        account_id=acc.id,
                        uid=acc.uid,
                        period_type=4,
                        start_time=datetime.now() - timedelta(days=30),
                        end_time=datetime.now(),
                        annualized_return=ann,
                        max_drawdown=dd,
                        sharpe_ratio=sharpe,
                        profit_loss_ratio=plr,
                        execution_score=ex,
                        composite_score=score,
                        trade_count=random.randint(100, 1500),
                    )
                )
    logger.info(f"[mock] filled rankings for {len(accounts)} accounts")


# ========== 任务 10：自动任务调度器（信号管理器 + 自动下单）==========
@celery_app.task(name="fwsort.scheduler.auto_strategy_dispatcher")
def auto_strategy_dispatcher() -> dict:
    """每分钟扫描所有活跃的自动任务，根据 interval 触发执行

    使用 Redis 记录每个任务的上次执行时间戳，实现多任务独立调度。
    """
    import json as _json

    from fwsort.models import AutoStrategy

    dispatcher_key = "fwsort:auto_strategy:last_run"
    now = int(time.time())
    triggered: list[int] = []
    skipped: list[int] = []

    with get_sync_db() as db:
        active_tasks = db.query(AutoStrategy).filter(
            AutoStrategy.is_active == True,
            AutoStrategy.deleted_at.is_(None),
        ).all()

        for task in active_tasks:
            # 检查循环次数是否已达到
            if task.loop_count > 0 and task.executed_count >= task.loop_count:
                # 循环已完成，停止任务
                task.is_active = False
                db.commit()
                logger.info(f"[auto_strategy_dispatcher] task {task.id} loop completed ({task.executed_count}/{task.loop_count}), stopped")
                skipped.append(task.id)
                continue

            # 检查开始时间（如果设置了 start_time 且还未到时间，则跳过）
            if task.start_time and task.start_time > datetime.utcnow():
                skipped.append(task.id)
                continue

            # 读取上次执行时间
            last_run = 0
            try:
                raw = sync_redis.hget(dispatcher_key, str(task.id))
                if raw:
                    last_run = int(raw)
            except Exception:
                pass

            interval_seconds = task.interval * 60
            if last_run == 0 or (now - last_run) >= interval_seconds:
                # 触发执行
                sync_redis.hset(dispatcher_key, str(task.id), str(now))
                triggered.append(task.id)

                # 异步投递到独立的执行任务（多任务多进程并发）
                try:
                    execute_auto_strategy.delay(task.id)
                except Exception as e:
                    logger.error(f"[auto_strategy_dispatcher] failed to dispatch task {task.id}: {e}")
            else:
                skipped.append(task.id)

    result = {
        "triggered": triggered,
        "skipped": skipped,
        "active_count": len(active_tasks) if 'active_tasks' in dir() else 0,
    }
    _record_task_status("auto_strategy_dispatcher", "ok", result)
    return result


@celery_app.task(name="fwsort.scheduler.execute_auto_strategy", bind=True, max_retries=0)
def execute_auto_strategy(self, task_id: int) -> dict:
    """执行单个自动任务（独立 Celery 任务，多进程并发）

    流程：获取信号 → 风控检查 → 下单 → 记录日志
    """
    from fwsort.strategy.service import execute_task

    try:
        result = execute_task(task_id)
        logger.info(f"[execute_auto_strategy] task={task_id} result={result.get('status')}")
        return result
    except Exception as e:
        logger.error(f"[execute_auto_strategy] task={task_id} failed: {e}")
        return {"task_id": task_id, "status": "error", "error": str(e)}