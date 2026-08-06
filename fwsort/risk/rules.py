# ========== 可插拔风控规则链 ==========
# 设计原则：
#   - 每条规则独立一个类，单一职责，方便单独测试 / 单独启停用
#   - 规则通过 check(ctx) 返回 RuleResult(passed, reason, amount_cap, freeze)
#   - 调用方（RiskControlService）按顺序串联规则，遇到第一个 freeze=True 直接冻结
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class RuleResult:
    """单条风控规则的执行结果"""
    rule_name: str
    passed: bool                     # True=放行 / False=拦截
    message: str = ""                # 用户可读说明
    amount_cap: float | None = None  # 非 None 表示：允许此单但截断订单金额到此值
    should_freeze: bool = False      # True 表示：需触发账户/策略风控冻结
    freeze_reason: str = ""          # 冻结说明（仅当 should_freeze=True 时填写）
    detail: dict[str, Any] = field(default_factory=dict)  # 用于审计日志的结构化详情

    @property
    def event_type(self) -> int:
        """映射到 RiskEventLog.event_type：1通过 2拦截 3冻结"""
        if self.should_freeze:
            return 3
        if not self.passed:
            return 2
        return 1

    @property
    def severity(self) -> int:
        if self.should_freeze:
            return 3
        if not self.passed:
            return 2
        return 1


@dataclass
class RiskContext:
    """风控上下文：所有规则共享的输入快照
    - 调用方（strategy/service / voting / router）把能提供的信息都填上
    - 规则只读取自己需要的字段，缺失直接放行（不做强校验，避免某路径未填字段导致全部拦截）
    """
    # 基础
    stage: str = "pre_order"  # pre_vote / pre_order / post_settle / manual
    order_amount_usd: float | None = None
    # 账户维度
    account_id: int | None = None
    account_balance: float = 0.0
    account_daily_pnl: float = 0.0
    account_initial_balance: float = 0.0
    account_is_frozen: bool = False
    # 策略（自动任务）维度
    auto_strategy_id: int | None = None
    strategy_max_daily_amount: float | None = None
    strategy_max_daily_count: int | None = None
    strategy_max_consecutive_failures: int | None = None
    strategy_consecutive_failures: int = 0
    # 当日运行时统计（由调用方查询后填入）
    today_total_amount: float = 0.0
    today_total_count: int = 0
    # 模板解析后的有效参数（由 RiskProfileManager.resolve_* 填入）
    effective_params: dict[str, Any] = field(default_factory=dict)
    # 额外上下文（写入审计日志用）
    extra: dict[str, Any] = field(default_factory=dict)


class BaseRiskRule:
    """风控规则基类：所有规则继承它并实现 check()"""
    rule_name: str = "BaseRiskRule"

    def check(self, ctx: RiskContext) -> RuleResult:  # pragma: no cover - 抽象方法
        raise NotImplementedError


# ==========================================================
# 1. DailyCountLimitRule：每日下单次数上限（原 strategy/service _run_risk_control）
# ==========================================================
class DailyCountLimitRule(BaseRiskRule):
    rule_name = "DailyCountLimitRule"

    def check(self, ctx: RiskContext) -> RuleResult:
        # 策略级 > 模板级 > 默认
        limit = (
            ctx.strategy_max_daily_count
            or ctx.effective_params.get("max_daily_count")
        )
        if limit is None or limit <= 0:
            return RuleResult(self.rule_name, True, detail={"limit": None})

        actual = ctx.today_total_count
        if actual >= limit:
            detail = {"limit": limit, "actual": actual}
            return RuleResult(
                self.rule_name, False,
                message=f"已达每日最大执行次数({limit}次)",
                detail=detail,
            )
        return RuleResult(
            self.rule_name, True,
            detail={"limit": limit, "remaining": limit - actual, "actual": actual},
        )


# ==========================================================
# 2. DailyAmountLimitRule：每日下单总金额上限（原 strategy/service _run_risk_control）
# ==========================================================
class DailyAmountLimitRule(BaseRiskRule):
    rule_name = "DailyAmountLimitRule"

    def check(self, ctx: RiskContext) -> RuleResult:
        limit = (
            ctx.strategy_max_daily_amount
            or ctx.effective_params.get("max_daily_amount")
        )
        if limit is None or limit <= 0:
            return RuleResult(self.rule_name, True, detail={"limit": None})

        actual = float(ctx.today_total_amount)
        order_amt = float(ctx.order_amount_usd or 0.0)
        if actual >= limit:
            detail = {"limit": round(limit, 4), "actual": round(actual, 4),
                      "pending_order": round(order_amt, 4)}
            return RuleResult(
                self.rule_name, False,
                message=f"已达每日最大执行金额(${limit:.2f})",
                detail=detail,
            )
        # 若加上此单会超限，也提前拦截（避免一笔大单穿透）
        if order_amt > 0 and actual + order_amt > limit:
            remaining = max(0.0, limit - actual)
            detail = {"limit": round(limit, 4), "actual": round(actual, 4),
                      "pending_order": round(order_amt, 4), "remaining": round(remaining, 4)}
            return RuleResult(
                self.rule_name, False,
                message=f"此单后将超日金额上限(剩余${remaining:.2f})",
                detail=detail,
            )
        return RuleResult(
            self.rule_name, True,
            detail={
                "limit": round(limit, 4), "actual": round(actual, 4),
                "remaining": round(limit - actual, 4),
            },
        )


# ==========================================================
# 3. ConsecutiveFailureRule：连续失败熔断（原 strategy/service _check_circuit_breaker）
# ==========================================================
class ConsecutiveFailureRule(BaseRiskRule):
    rule_name = "ConsecutiveFailureRule"

    def check(self, ctx: RiskContext) -> RuleResult:
        limit = (
            ctx.strategy_max_consecutive_failures
            or ctx.effective_params.get("max_consecutive_failures")
        )
        if limit is None or limit <= 0:
            return RuleResult(self.rule_name, True, detail={"limit": None})

        actual = ctx.strategy_consecutive_failures
        detail = {"limit": limit, "actual": actual}
        if actual >= limit:
            return RuleResult(
                self.rule_name, False,
                message=f"连续失败 {actual} 次，触发熔断",
                should_freeze=True,
                freeze_reason=f"连续失败 {actual} 次 ≥ 阈值 {limit}",
                detail=detail,
            )
        return RuleResult(
            self.rule_name, True,
            detail={"limit": limit, "actual": actual, "remaining": limit - actual},
        )


# ==========================================================
# 4. SingleOrderRatioRule：单笔金额 ≤ 余额 * ratio（原 voting.py 风控2）
# ==========================================================
class SingleOrderRatioRule(BaseRiskRule):
    rule_name = "SingleOrderRatioRule"

    def check(self, ctx: RiskContext) -> RuleResult:
        if ctx.order_amount_usd is None or ctx.order_amount_usd <= 0:
            return RuleResult(self.rule_name, True, detail={"skip": "no_order_amount"})
        ratio = ctx.effective_params.get("risk_single_ratio")
        if ratio is None or ratio <= 0:
            return RuleResult(self.rule_name, True, detail={"skip": "no_ratio"})

        balance = max(float(ctx.account_balance), 0.0)
        if balance <= 0:
            return RuleResult(
                self.rule_name, False,
                message="账户余额为0，无法下单",
                detail={"balance": balance},
            )

        max_single = balance * float(ratio)
        order_amt = float(ctx.order_amount_usd)
        detail = {
            "ratio": round(ratio, 4), "balance": round(balance, 4),
            "max_single": round(max_single, 4), "order_amount": round(order_amt, 4),
        }
        if order_amt <= max_single:
            return RuleResult(self.rule_name, True, detail=detail)

        # 截断式放行：金额截断到 max_single，不直接拦截（原 voting 逻辑）
        detail["capped_to"] = round(max_single, 4)
        return RuleResult(
            self.rule_name, True,
            message=f"单笔金额按风控比例截断至 ${max_single:.2f}",
            amount_cap=max_single,
            detail=detail,
        )


# ==========================================================
# 5. DailyLossRatioRule：日亏损比例 ≥ 阈值 → 冻结账户（原 voting.py 风控1）
# ==========================================================
class DailyLossRatioRule(BaseRiskRule):
    rule_name = "DailyLossRatioRule"

    def check(self, ctx: RiskContext) -> RuleResult:
        ratio = ctx.effective_params.get("risk_daily_loss_ratio")
        if ratio is None or ratio <= 0:
            return RuleResult(self.rule_name, True, detail={"skip": "no_ratio"})

        initial = max(float(ctx.account_initial_balance), 0.0)
        if initial <= 0:
            return RuleResult(self.rule_name, True, detail={"skip": "no_initial_balance"})

        daily_loss = abs(min(float(ctx.account_daily_pnl), 0.0))
        loss_ratio = daily_loss / initial
        detail = {
            "ratio_threshold": round(ratio, 4),
            "initial_balance": round(initial, 4),
            "daily_pnl": round(float(ctx.account_daily_pnl), 4),
            "daily_loss_abs": round(daily_loss, 4),
            "loss_ratio": round(loss_ratio, 4),
        }
        if loss_ratio >= ratio:
            return RuleResult(
                self.rule_name, False,
                message=f"日亏 {loss_ratio:.1%} ≥ 阈值 {ratio:.0%}，触发冻结",
                should_freeze=True,
                freeze_reason=f"risk_freeze: daily loss {loss_ratio:.1%} >= {ratio:.0%}",
                detail=detail,
            )
        return RuleResult(self.rule_name, True, detail=detail)


# ==========================================================
# 规则集合：按场景分组（不同阶段跑不同子集）
# ==========================================================
PRE_VOTE_RULES: list[type[BaseRiskRule]] = [
    DailyLossRatioRule,       # 投票前先看有没有日亏冻结
    SingleOrderRatioRule,     # 给 voting.py 用的"金额截断"逻辑
]

PRE_ORDER_AUTO_STRATEGY_RULES: list[type[BaseRiskRule]] = [
    DailyCountLimitRule,
    DailyAmountLimitRule,
    ConsecutiveFailureRule,
    SingleOrderRatioRule,
    DailyLossRatioRule,
]

POST_SETTLE_RULES: list[type[BaseRiskRule]] = [
    DailyLossRatioRule,       # 结算后检查日亏，触发冻结
]
