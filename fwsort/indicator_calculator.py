# 指标计算模块：年化/夏普/卡玛/回撤/胜率/执行质量（架构文档 4.3.2）
import math
from dataclasses import dataclass


@dataclass
class TradeRecord:
    """单笔交易记录（用于指标计算）"""

    pnl: float
    opened_at: int  # 开仓时间戳
    closed_at: int  # 平仓时间戳
    is_win: bool


def annualized_return(initial: float, final: float, days: float) -> float:
    """年化收益率 = (final/initial)^(365/days) - 1"""
    if initial <= 0 or days <= 0:
        return 0.0
    return (final / initial) ** (365.0 / days) - 1


def cumulative_return(initial: float, final: float) -> float:
    """累计收益率 = (final - initial) / initial（开发计划B §2.1 收益类指标）"""
    if initial <= 0:
        return 0.0
    return (final - initial) / initial


def volatility(returns: list[float], annualize: bool = True) -> float:
    """收益波动率 = 标准差；默认年化（开发计划B §2.1 风险类指标）

    日收益序列传入时 annualize=True 会乘 sqrt(252)
    """
    if not returns or len(returns) < 2:
        return 0.0
    n = len(returns)
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(var)
    if annualize:
        return std * math.sqrt(252)
    return std


def max_consecutive_losses(trades: list[TradeRecord]) -> int:
    """最大连续亏损次数（开发计划B §2.1 风险类指标）"""
    if not trades:
        return 0
    max_run = 0
    cur_run = 0
    for t in trades:
        if not t.is_win:
            cur_run += 1
            max_run = max(max_run, cur_run)
        else:
            cur_run = 0
    return max_run


def sharpe_ratio(returns: list[float], risk_free: float = 0.0) -> float:
    """夏普比率 = (平均收益 - 无风险) / 收益波动率"""
    if not returns or len(returns) < 2:
        return 0.0
    n = len(returns)
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return (mean - risk_free) / std * math.sqrt(252)  # 年化


def max_drawdown(nav_series: list[float]) -> tuple[float, int]:
    """最大回撤 + 修复天数；返回 (回撤比例, 修复天数)"""
    if not nav_series:
        return 0.0, 0
    peak = nav_series[0]
    max_dd = 0.0
    dd_start = 0
    recovery_days = 0
    for i, nav in enumerate(nav_series):
        if nav > peak:
            peak = nav
            dd_start = i
        dd = (peak - nav) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
            recovery_days = i - dd_start
    return max_dd, recovery_days


def calmar_ratio(annualized: float, max_dd: float) -> float:
    """卡玛比率 = 年化 / 最大回撤"""
    if max_dd <= 0:
        return 0.0
    return annualized / max_dd


def win_rate(trades: list[TradeRecord]) -> float:
    """胜率"""
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t.is_win)
    return wins / len(trades)


def profit_loss_ratio(trades: list[TradeRecord]) -> float:
    """盈亏比 = 平均盈利 / 平均亏损（绝对值）"""
    wins = [t.pnl for t in trades if t.is_win]
    losses = [t.pnl for t in trades if not t.is_win]
    if not wins or not losses:
        return 0.0
    avg_win = sum(wins) / len(wins)
    avg_loss = abs(sum(losses) / len(losses))
    if avg_loss == 0:
        return 0.0
    return avg_win / avg_loss


def execution_quality_score(
    execution_rate: float,
    slippage_rate: float,
    latency_ms: int,
    cancel_rate: float,
    latency_baseline: int = 500,
) -> float:
    """执行质量分（架构文档 4.3.5）

    score = 0.3*执行率 + 0.3*(1-滑点率) + 0.2*(1-延迟/基准) + 0.2*(1-撤单率)
    """
    latency_score = max(0.0, 1.0 - latency_ms / latency_baseline)
    score = (
        0.3 * max(0.0, min(1.0, execution_rate))
        + 0.3 * max(0.0, 1.0 - slippage_rate)
        + 0.2 * latency_score
        + 0.2 * max(0.0, 1.0 - cancel_rate)
    )
    return round(score, 4)
