# 榜单评分引擎：福纹综合分（架构文档 4.3.3）
from dataclasses import dataclass

from fwsort.config import settings
from fwsort.redis_client import RankType, rank_key, sync_redis  # WP-08


@dataclass
class WeightTuple:
    """榜单权重"""

    annualized: float
    drawdown: float
    sharpe: float
    profit_loss: float
    execution: float

    def total(self) -> float:
        return self.annualized + self.drawdown + self.sharpe + self.profit_loss + self.execution


def default_weights() -> WeightTuple:
    """默认权重（与 .env / 架构文档一致）"""
    return WeightTuple(
        annualized=settings.WEIGHT_ANNUALIZED,
        drawdown=settings.WEIGHT_DRAWDOWN,
        sharpe=settings.WEIGHT_SHARPE,
        profit_loss=settings.WEIGHT_PROFIT_LOSS,
        execution=settings.WEIGHT_EXECUTION,
    )


def composite_score(
    annualized: float,
    max_drawdown: float,
    sharpe: float,
    profit_loss: float,
    execution_score: float,
    weights: WeightTuple | None = None,
) -> float:
    """福纹综合分（满分 100）

    score = 100 * (
        W1 * clip(annualized, 0, 2)
      + W2 * (1 - max_drawdown)
      + W3 * clip((sharpe + 1) / 4, 0, 1)   # sharpe 归一化到 0~1
      + W4 * clip(profit_loss / 3, 0, 1)    # 盈亏比归一化
      + W5 * execution_score
    )
    """
    w = weights or default_weights()

    # 极值截断
    a_clip = max(0.0, min(annualized, 2.0)) / 2.0          # 0~1
    d_clip = max(0.0, min(max_drawdown, 1.0))               # 0~1
    dd_score = 1.0 - d_clip
    s_clip = max(0.0, min((sharpe + 1.0) / 4.0, 1.0))       # 0~1
    pl_clip = max(0.0, min(profit_loss / 3.0, 1.0))         # 0~1
    e_clip = max(0.0, min(execution_score, 1.0))            # 0~1

    raw = (
        w.annualized * a_clip
        + w.drawdown * dd_score
        + w.sharpe * s_clip
        + w.profit_loss * pl_clip
        + w.execution * e_clip
    )
    return round(raw * 100, 4)


def tier_of(score: float) -> str:
    """段位判定（架构文档 6.1）"""
    if score >= 80:
        return "钻石"
    if score >= 60:
        return "铂金"
    if score >= 40:
        return "黄金"
    if score >= 20:
        return "白银"
    return "青铜"


# ========== WP-08：权重变更触发榜单重算 ==========
# period_type → Redis ZSet 名称映射
_PERIOD_TO_RANK = {
    1: RankType.DAILY,
    2: RankType.WEEKLY,
    3: RankType.MONTHLY,
    4: RankType.ALL_TIME,
}


def refresh_redis_zset(db, rank_type: int | None = None) -> dict:
    """WP-08：权重重算 → 写回 DB → 写回 Redis ZSet

    - 读 WeightConfig 表中指定（或全部）rank_type 的权重
    - 对每个 period_type，重算 StrategyPerformance.composite_score
    - 写回 DB（commit 由调用方负责）和 Redis ZSet
    - 返回 {updated, rank_types}

    用法：
      from fwsort.database import get_sync_db
      with get_sync_db() as db:
          result = refresh_redis_zset(db, rank_type=1)
    """
    from fwsort.models import StrategyPerformance, WeightConfig

    if rank_type is not None:
        rank_types = [rank_type]
    else:
        # 全量：所有已配置的 rank_type
        cfgs = db.query(WeightConfig).all()
        rank_types = [c.rank_type for c in cfgs] or [1, 2, 3, 4]

    updated = 0
    failed = 0
    for rt in rank_types:
        cfg = db.query(WeightConfig).filter(WeightConfig.rank_type == rt).first()
        if not cfg:
            # 没有配置 → 用默认权重
            w = default_weights()
        else:
            w = WeightTuple(
                annualized=float(cfg.weight_annualized),
                drawdown=float(cfg.weight_drawdown),
                sharpe=float(cfg.weight_sharpe),
                profit_loss=float(cfg.weight_profit_loss),
                execution=float(cfg.weight_execution),
            )
        # 校验权重和
        if abs(w.total() - 1.0) > 0.01:
            # 权重和偏差过大时仍用默认权重重算，避免极端值
            w = default_weights()

        perfs = (
            db.query(StrategyPerformance)
            .filter(StrategyPerformance.period_type == rt)
            .all()
        )
        # 写 Redis ZSet：先清空再 ZADD（与 refresh_realtime_rank 一致）
        zkey = rank_key(_PERIOD_TO_RANK.get(rt, RankType.REALTIME))
        try:
            sync_redis.delete(zkey)
        except Exception as e:  # noqa: BLE001
            from fwsort.fwlogs import logger

            logger.warning(f"redis delete {zkey} failed: {e}")

        for p in perfs:
            try:
                new_score = composite_score(
                    annualized=float(p.annualized_return),
                    max_drawdown=float(p.max_drawdown),
                    sharpe=float(p.sharpe_ratio),
                    profit_loss=float(p.profit_loss_ratio),
                    execution_score=float(p.execution_score),
                    weights=w,
                )
                p.composite_score = new_score
                try:
                    sync_redis.zadd(zkey, {p.uid: new_score})
                except Exception as e:  # noqa: BLE001
                    from fwsort.fwlogs import logger

                    logger.warning(f"redis zadd {p.uid} failed: {e}")
                updated += 1
            except Exception as e:  # noqa: BLE001
                from fwsort.fwlogs import logger

                logger.warning(f"refresh_redis_zset uid={p.uid} failed: {e}")
                failed += 1

    return {"updated": updated, "failed": failed, "rank_types": rank_types}
