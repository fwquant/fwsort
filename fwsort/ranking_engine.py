# 榜单评分引擎：福纹综合分（架构文档 4.3.3）
from dataclasses import dataclass

from fwsort.config import settings


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
