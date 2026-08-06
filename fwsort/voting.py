# 投票引擎：V1.0 严格规则（2:1 多数 / 全同加倍 / 5 美元基础）
from dataclasses import dataclass
from enum import IntEnum

from fwsort.config import settings


class Direction(IntEnum):
    FLAT = 0
    UP = 1
    DOWN = 2


@dataclass
class VoteResult:
    """投票引擎输出"""

    up_count: int
    down_count: int
    flat_count: int
    final_direction: int
    order_amount_usd: float
    reason: str


def vote(
    directions: list[int],
    account_balance: float,
    daily_pnl: float,
    initial_balance: float,
) -> VoteResult:
    """严格按 V1.0：3 智能体投票

    - 3 同方向 → 10 美元 (double_10)
    - 2:1 多数 → 5 美元 (base_5)
    - 无共识（3 不同）→ 0 不交易 (no_consensus)
    - 风控：单笔 ≤ 余额 20%（通过 RiskControlService 检查，保留 reason 格式以兼容）
    - 风控：日亏 ≥ 30% → 强停（通过 RiskControlService 检查，保留 reason 格式以兼容）
    """
    up = sum(1 for d in directions if d == Direction.UP)
    down = sum(1 for d in directions if d == Direction.DOWN)
    flat = sum(1 for d in directions if d == Direction.FLAT)

    # 风控：日亏上限（兼容旧 reason 格式）
    if initial_balance > 0:
        loss_ratio = abs(min(daily_pnl, 0)) / initial_balance
        if loss_ratio >= settings.RISK_DAILY_LOSS_RATIO:
            return VoteResult(
                up_count=up, down_count=down, flat_count=flat,
                final_direction=0, order_amount_usd=0.0,
                reason=f"risk_freeze: daily loss {loss_ratio:.1%} >= {settings.RISK_DAILY_LOSS_RATIO:.0%}",
            )

    # V1.0 规则
    if up == 3:
        amount, direction, reason = settings.ORDER_DOUBLE_USD, Direction.UP, "double_10"
    elif down == 3:
        amount, direction, reason = settings.ORDER_DOUBLE_USD, Direction.DOWN, "double_10"
    elif up >= 2 and up > down:
        amount, direction, reason = settings.ORDER_BASE_USD, Direction.UP, "base_5_majority_up"
    elif down >= 2 and down > up:
        amount, direction, reason = settings.ORDER_BASE_USD, Direction.DOWN, "base_5_majority_down"
    else:
        amount, direction, reason = 0.0, Direction.FLAT, "no_consensus"

    # 风控：单笔上限（兼容旧 reason 格式）
    if amount > 0:
        max_single = account_balance * settings.RISK_SINGLE_RATIO
        if amount > max_single:
            amount = min(amount, max_single)
            reason = f"{reason}_capped_by_risk"

    return VoteResult(
        up_count=up, down_count=down, flat_count=flat,
        final_direction=direction, order_amount_usd=amount, reason=reason,
    )
