# Celery 定时任务：榜单刷新 / 日榜快照 / 数据清理（架构文档 4.3.6）
import asyncio
import random
from datetime import datetime, timedelta

from celery import Celery
from celery.schedules import crontab
from loguru import logger

from core.config import settings
from core.database import get_sync_db
from core.models import RankSnapshot, StrategyPerformance
from core.ranking_engine import composite_score, tier_of
from core.redis_client import RankType, rank_key, sync_redis

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
            "task": "core.scheduler.refresh_realtime_rank",
            "schedule": crontab(minute="*"),
        },
        # 日榜快照：每日 00:05
        "daily-snapshot": {
            "task": "core.scheduler.daily_snapshot",
            "schedule": crontab(hour=0, minute=5),
        },
        # 数据清理：每日 03:00
        "daily-cleanup": {
            "task": "core.scheduler.daily_cleanup",
            "schedule": crontab(hour=3, minute=0),
        },
        # 数据归档：每日 03:30（订单日志 90 天热→冷）
        "archive-hot-to-cold": {
            "task": "core.scheduler.archive_hot_to_cold",
            "schedule": crontab(hour=3, minute=30),
        },
        # 跟单自动同步：每 5 分钟
        "follow-auto-copy": {
            "task": "core.scheduler.follow_auto_copy",
            "schedule": crontab(minute="*/5"),
        },
        # 通知扫描：每 10 分钟
        "notify-scan": {
            "task": "core.scheduler.notify_scan",
            "schedule": crontab(minute="*/10"),
        },
    },
)


# ========== 任务 1：实时榜刷新（Redis ZSet）==========
@celery_app.task(name="core.scheduler.refresh_realtime_rank")
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
@celery_app.task(name="core.scheduler.daily_snapshot")
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
@celery_app.task(name="core.scheduler.daily_cleanup")
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
@celery_app.task(name="core.scheduler.archive_hot_to_cold")
def archive_hot_to_cold() -> dict:
    """订单执行日志归档：把超过 ORDER_LOG_HOT_DAYS 的旧数据从 PG 迁到 ES（冷存）

    真实生产环境应写到 S3 / OSS，本 MVP 直接把过期数据 DELETE 之前先批量
    dump 到 ES（冷存），PG 保留近 90 天热数据。
    """
    import json
    from datetime import datetime

    from core.es_client import get_es_client

    cutoff = datetime.now() - timedelta(days=settings.ORDER_LOG_HOT_DAYS)
    archived = 0
    failed = 0
    try:
        es = get_es_client()
        with get_sync_db() as db:
            from core.models import OrderExecutionLog

            old_rows = (
                db.query(OrderExecutionLog).filter(OrderExecutionLog.created_at < cutoff).limit(5000).all()
            )
            for r in old_rows:
                try:
                    es.index(
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
        logger.info(f"[scheduler] archive done: {archived} ok, {failed} failed")
    except Exception as e:  # noqa: BLE001
        logger.error(f"[scheduler] archive failed: {e}")
    return {"archived": archived, "failed": failed}


# ========== 任务 5：跟单自动同步 ==========
@celery_app.task(name="core.scheduler.follow_auto_copy")
def follow_auto_copy() -> int:
    """每 5 分钟扫描所有有效订阅，复用最近一笔 leader 订单信号给粉丝"""
    from core.models import ExecutionAccount, FollowOrder, FollowSubscription, OrderExecutionLog

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


# ========== 任务 6：通知扫描（风控冻结/榜单异动/订阅到期）==========
@celery_app.task(name="core.scheduler.notify_scan")
def notify_scan() -> int:
    """每 10 分钟扫一次系统状态，发现异常推通知"""
    from core.models import ExecutionAccount, FollowSubscription, Notification

    pushed = 0
    now = datetime.now()
    with get_sync_db() as db:
        # 1) 风控冻结通知
        frozen = db.query(ExecutionAccount).filter(ExecutionAccount.risk_frozen == True).all()  # noqa: E712
        for a in frozen:
            # 同一账户 1 天内只推一次
            recent = (
                db.query(Notification)
                .filter(Notification.user_id == a.owner_id, Notification.ntype == 3, Notification.content.like(f"%{a.uid}%"))
                .filter(Notification.created_at > now - timedelta(days=1))
                .first()
            )
            if not recent:
                db.add(Notification(user_id=a.owner_id, ntype=3, title="风控冻结", content=f"账户 {a.uid} 因日亏超限已被风控冻结"))
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


# ========== 工具：MOCK 计算综合分（无真实交易时填充榜单）==========
def mock_fill_rankings(n: int = 30) -> None:
    """开发期填充 MOCK 绩效到数据库，让榜单有数据可看"""
    with get_sync_db() as db:
        from core.models import ExecutionAccount

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
