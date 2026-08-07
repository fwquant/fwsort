# ========== 统一风控入口 Facade ==========
# 所有交易路径（auto_strategy / voting / 跟单 / 手动下单）必须走这些入口，
# 不得直接调用具体规则类，以确保：
#   1. 所有规则执行顺序一致
#   2. 风控审计日志完整写入
#   3. 冻结状态唯一设置点（避免 3 处地方都改 risk_frozen）
from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from fwsort.fwlogs import logger
from fwsort.models import AutoStrategy, AutoStrategyLog, ExecutionAccount, Notification
from fwsort.risk.manager import RiskProfileManager
from fwsort.risk.models import (
    AccountRiskProfile,
    RiskEventLog,
    StrategyRiskProfile,
)
from fwsort.risk.rules import (
    PRE_ORDER_AUTO_STRATEGY_RULES,
    PRE_VOTE_RULES,
    POST_SETTLE_RULES,
    RiskContext,
    RuleResult,
)


@dataclass
class RiskCheckResult:
    """统一风控检查结果（返回给调用方）"""
    passed: bool                        # 是否通过（冻结也算不通过）
    blocked: bool                       # True=规则明确拦截（没到冻结地步）
    should_freeze: bool                 # True=要求调用方执行冻结动作
    freeze_reason: str                  # 冻结原因（should_freeze=True 时有）
    amount_cap: float | None            # 非 None：订单金额截断到此值
    message: str                        # 给用户看的原因
    rule_results: list[RuleResult]      # 每条规则执行结果（调试/审计用）
    event_log_id: int | None = None     # 已写入的风险事件日志ID

    @property
    def first_block_reason(self) -> str:
        for r in self.rule_results:
            if not r.passed:
                return r.message or r.rule_name
        return self.message


def _gen_event_uid() -> str:
    date = datetime.utcnow().strftime("%Y%m%d")
    rand = secrets.token_hex(4)
    return f"RSK-{date}-{rand.upper()}"


def _write_event_log(
    db: Session,
    *,
    ctx: RiskContext,
    rule_results: list[RuleResult],
    user_id: int | None = None,
) -> RiskEventLog | None:
    """把本次风控检查中：非"通过"的规则各写一条事件日志（严重的冻结事件单独一条）
    - 全部通过也写一条 summary（severity=1），方便查"最后一次检查时间"
    """
    if not rule_results:
        return None

    # 先记录最终状态（通过 / 拦截 / 冻结）的汇总日志
    any_block = any(not r.passed for r in rule_results)
    any_freeze = any(r.should_freeze for r in rule_results)

    final_type = 1 if (not any_block and not any_freeze) else (3 if any_freeze else 2)
    final_severity = 1 if final_type == 1 else (3 if final_type == 3 else 2)
    title_map = {1: "风控检查通过", 2: "风控拦截", 3: "触发风控冻结"}
    msg_map = {
        1: "所有风控规则通过",
        2: "; ".join(r.message for r in rule_results if not r.passed) or "风控规则未通过",
        3: "; ".join(r.freeze_reason or r.message for r in rule_results if r.should_freeze) or "风控冻结",
    }

    # 快照上下文
    balance = float(ctx.account_balance)
    daily_pnl = float(ctx.account_daily_pnl)
    order_amt = float(ctx.order_amount_usd or 0.0)

    detail = {
        "stage": ctx.stage,
        "rule_results": [
            {
                "rule": r.rule_name,
                "passed": r.passed,
                "message": r.message,
                "freeze": r.should_freeze,
                "freeze_reason": r.freeze_reason,
                "amount_cap": r.amount_cap,
                "detail": r.detail,
            }
            for r in rule_results
        ],
        "effective_params": ctx.effective_params,
        "today_stats": {
            "total_amount": round(ctx.today_total_amount, 4),
            "total_count": ctx.today_total_count,
            "consecutive_failures": ctx.strategy_consecutive_failures,
        },
    }
    if ctx.extra:
        detail["extra"] = ctx.extra

    row = RiskEventLog(
        event_uid=_gen_event_uid(),
        account_id=ctx.account_id,
        auto_strategy_id=ctx.auto_strategy_id,
        user_id=user_id,
        rule_name="RiskCheckSummary",
        event_type=final_type,
        severity=final_severity,
        stage=ctx.stage,
        title=title_map[final_type],
        detail_json=json.dumps(detail, ensure_ascii=False),
        message=msg_map[final_type],
        balance_snapshot=balance,
        daily_pnl_snapshot=daily_pnl,
        order_amount_snapshot=order_amt,
    )
    db.add(row)
    db.flush()
    return row


# ==========================================================
# RiskControlService：对外 Facade 入口
# ==========================================================
class RiskControlService:
    """风控统一入口。所有方法幂等、无状态，可多线程调用。"""

    # ---------------------------------------------------------
    #  冻结 / 解冻：唯一真源入口（严禁其他模块直接改 risk_frozen）
    # ---------------------------------------------------------
    @staticmethod
    def freeze_account(
        db: Session,
        account_id: int,
        *,
        reason: str,
        rule_name: str = "ManualFreeze",
        operator_user_id: int | None = None,
    ) -> bool:
        """冻结账户：写 AccountRiskProfile.is_frozen + 镜像到 ExecutionAccount.risk_frozen + 事件日志 + 通知
        - 返回 True：冻结操作生效（之前未冻结）
        - 返回 False：已处于冻结状态，无需重复
        """
        profile = RiskProfileManager.get_or_create_account_profile(db, account_id)
        already = bool(profile.is_frozen)
        profile.is_frozen = True
        profile.frozen_reason = reason
        profile.frozen_at = datetime.utcnow()
        profile.last_check_at = datetime.utcnow()

        # 镜像到 ExecutionAccount（向后兼容，其他模块还在读取 risk_frozen）
        acc = db.query(ExecutionAccount).filter(ExecutionAccount.id == account_id).first()
        if acc is not None:
            acc.risk_frozen = True

        # 事件日志
        ev = RiskEventLog(
            event_uid=_gen_event_uid(),
            account_id=account_id,
            user_id=operator_user_id,
            rule_name=rule_name,
            event_type=3, severity=3, stage="manual",
            title="账户风控冻结",
            detail_json=json.dumps({"reason": reason, "operator": operator_user_id}, ensure_ascii=False),
            message=reason,
            balance_snapshot=float(acc.current_balance) if acc else 0.0,
            daily_pnl_snapshot=float(acc.daily_pnl) if acc else 0.0,
        )
        db.add(ev)

        # 通知（不重复 1 天内）
        if acc is not None and not already:
            now = datetime.utcnow()
            recent = (
                db.query(Notification)
                .filter(
                    Notification.user_id == acc.owner_id,
                    Notification.ntype == 3,
                    Notification.content.like(f"%{acc.uid}%"),
                )
                .filter(Notification.created_at > now - __import__("datetime").timedelta(days=1))
                .first()
            )
            if not recent:
                db.add(Notification(
                    user_id=acc.owner_id, ntype=3,
                    title="风控冻结",
                    content=f"账户 {acc.uid} 已被风控冻结：{reason}",
                ))
        db.flush()
        logger.warning(
            f"[RiskControl] 冻结账户 account_id={account_id} reason={reason} "
            f"already_frozen={already}"
        )
        return not already

    @staticmethod
    def unfreeze_account(
        db: Session,
        account_id: int,
        *,
        reason: str = "人工复核通过，解除冻结",
        operator_user_id: int | None = None,
        reset_consecutive_failures: bool = True,
    ) -> bool:
        """解冻账户：同时清零策略级 consecutive_failures（可选）"""
        profile = RiskProfileManager.get_or_create_account_profile(db, account_id)
        was_frozen = bool(profile.is_frozen)
        profile.is_frozen = False
        profile.frozen_reason = ""
        profile.frozen_at = None
        profile.last_check_at = datetime.utcnow()
        if reset_consecutive_failures:
            profile.consecutive_failures = 0

        # 镜像
        acc = db.query(ExecutionAccount).filter(ExecutionAccount.id == account_id).first()
        if acc is not None:
            acc.risk_frozen = False

        # 清零该账户下所有自动任务的连续失败计数
        if reset_consecutive_failures:
            strat_rows = (
                db.query(StrategyRiskProfile)
                .join(AutoStrategy, StrategyRiskProfile.auto_strategy_id == AutoStrategy.id)
                .filter(AutoStrategy.account_id == account_id)
                .all()
            )
            for sr in strat_rows:
                sr.consecutive_failures = 0
            tasks = db.query(AutoStrategy).filter(AutoStrategy.account_id == account_id).all()
            for t in tasks:
                t.consecutive_failures = 0

        ev = RiskEventLog(
            event_uid=_gen_event_uid(),
            account_id=account_id,
            user_id=operator_user_id,
            rule_name="ManualUnfreeze",
            event_type=4, severity=1, stage="manual",
            title="解除风控冻结",
            detail_json=json.dumps({
                "reason": reason, "operator": operator_user_id,
                "reset_consecutive_failures": reset_consecutive_failures,
            }, ensure_ascii=False),
            message=reason,
            balance_snapshot=float(acc.current_balance) if acc else 0.0,
            daily_pnl_snapshot=float(acc.daily_pnl) if acc else 0.0,
        )
        db.add(ev)
        db.flush()
        logger.info(
            f"[RiskControl] 解冻账户 account_id={account_id} was_frozen={was_frozen}"
        )
        return was_frozen

    @staticmethod
    def is_account_frozen(db: Session, account_id: int) -> tuple[bool, str]:
        """读取账户冻结状态（唯一真源：AccountRiskProfile，镜像字段备用）"""
        profile = RiskProfileManager.get_or_create_account_profile(db, account_id)
        if profile.is_frozen:
            return True, profile.frozen_reason or "账户已风控冻结"
        return False, ""

    #  自动任务（AutoStrategy）下单前检查：原 _run_risk_control + _check_circuit_breaker
    @staticmethod
    def check_before_auto_strategy_order(
        db: Session,
        *,
        auto_strategy_id: int,
        order_amount_usd: float | None = None,
        manual: bool = False,
        user_id: int | None = None,
    ) -> RiskCheckResult:
        """自动任务下单前风控检查：
        - 查询当日累计次数/金额 → 跑 PRE_ORDER_AUTO_STRATEGY_RULES
        - 若规则要求冻结，自动执行 freeze_account + 停止任务
        - 返回结果供调用方决定：拦截(return) / 截断金额 / 放行
        """
        # 1. 加载策略（含关联账户）
        task = db.query(AutoStrategy).filter(AutoStrategy.id == auto_strategy_id).first()
        if task is None:
            return RiskCheckResult(
                passed=False, blocked=True, should_freeze=False,
                freeze_reason="", amount_cap=None,
                message=f"自动任务不存在: id={auto_strategy_id}",
                rule_results=[], event_log_id=None,
            )

        # 2. 解析策略级 + 账户级参数
        strat_profile, effective = RiskProfileManager.resolve_strategy_params(db, auto_strategy_id)
        account_effective = effective
        if task.account_id:
            _, account_effective = RiskProfileManager.resolve_account_params(db, task.account_id)
        # 合并：策略级特有的 3 个字段直接从 AutoStrategy 表读取（用户编辑的源头，确保热加载）
        merged_effective = dict(account_effective)
        merged_effective["max_daily_amount"] = (
            float(task.max_daily_amount)
            if task.max_daily_amount is not None
            else merged_effective.get("max_daily_amount")
        )
        merged_effective["max_daily_count"] = (
            int(task.max_daily_count)
            if task.max_daily_count is not None
            else merged_effective.get("max_daily_count")
        )
        merged_effective["max_consecutive_failures"] = (
            int(task.max_consecutive_failures)
            if task.max_consecutive_failures is not None
            else merged_effective.get("max_consecutive_failures")
        )

        # 3. 当日统计：累计下单次数/金额（来自 auto_strategy_log）
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_logs = (
            db.query(AutoStrategyLog)
            .filter(
                AutoStrategyLog.task_id == auto_strategy_id,
                AutoStrategyLog.created_at >= today_start,
            )
            .all()
        )
        today_total_count = 0
        today_total_amount = 0.0
        for log in today_logs:
            try:
                exec_detail = json.loads(log.execution_detail_json or "{}")
            except Exception:
                exec_detail = {}
            making = float(exec_detail.get("making_amount", 0) or 0)
            if making > 0:
                today_total_count += 1
                today_total_amount += making

        # 4. 构建上下文
        acc_balance = 0.0
        acc_daily_pnl = 0.0
        acc_initial = 0.0
        acc_id = task.account_id
        acc_frozen = False
        if task.account_id:
            acc = db.query(ExecutionAccount).filter(ExecutionAccount.id == task.account_id).first()
            if acc is not None:
                acc_balance = float(acc.current_balance)
                acc_daily_pnl = float(acc.daily_pnl)
                acc_initial = float(acc.initial_balance)
                acc_frozen, _ = RiskControlService.is_account_frozen(db, task.account_id)

        ctx = RiskContext(
            stage="pre_order",
            order_amount_usd=float(order_amount_usd) if order_amount_usd else None,
            account_id=acc_id,
            account_balance=acc_balance,
            account_daily_pnl=acc_daily_pnl,
            account_initial_balance=acc_initial,
            account_is_frozen=acc_frozen,
            auto_strategy_id=auto_strategy_id,
            strategy_max_daily_amount=merged_effective.get("max_daily_amount"),
            strategy_max_daily_count=merged_effective.get("max_daily_count"),
            strategy_max_consecutive_failures=merged_effective.get("max_consecutive_failures"),
            strategy_consecutive_failures=int(strat_profile.consecutive_failures),
            today_total_amount=today_total_amount,
            today_total_count=today_total_count,
            effective_params=merged_effective,
            extra={"manual": manual},
        )

        # 账户已冻结：直接短路（显示具体冻结原因）
        if acc_frozen:
            _, frozen_reason = RiskControlService.is_account_frozen(db, task.account_id)
            reason_detail = f"（原因：{frozen_reason}）" if frozen_reason else ""
            result = RiskCheckResult(
                passed=False, blocked=True, should_freeze=True,
                freeze_reason=frozen_reason or "账户已处于风控冻结状态",
                amount_cap=None,
                message=f"账户已处于风控冻结状态{reason_detail}，请先联系管理员或手动解冻",
                rule_results=[],
            )
            _write_event_log(db, ctx=ctx, rule_results=[], user_id=user_id)
            return result

        # 5. 依次执行规则链
        rule_results: list[RuleResult] = []
        for cls in PRE_ORDER_AUTO_STRATEGY_RULES:
            rule = cls()
            r = rule.check(ctx)
            rule_results.append(r)

        # 6. 汇总结果
        any_block = any(not r.passed and not r.should_freeze for r in rule_results)
        any_freeze = any(r.should_freeze for r in rule_results)
        amount_caps = [r.amount_cap for r in rule_results if r.amount_cap is not None]
        amount_cap = min(amount_caps) if amount_caps else None

        messages = []
        freeze_reason = ""
        for r in rule_results:
            if r.should_freeze and r.freeze_reason:
                freeze_reason = r.freeze_reason
                messages.append(f"❌ {r.rule_name}: {r.freeze_reason}")
            elif not r.passed:
                messages.append(f"🛑 {r.rule_name}: {r.message}")
            elif r.amount_cap is not None:
                messages.append(f"⚠️ {r.rule_name}: {r.message}")
        msg = "\n".join(messages) if messages else "风控检查通过"

        result = RiskCheckResult(
            passed=not (any_block or any_freeze),
            blocked=any_block and not any_freeze,
            should_freeze=any_freeze,
            freeze_reason=freeze_reason,
            amount_cap=amount_cap,
            message=msg,
            rule_results=rule_results,
        )

        # 7. 写事件日志
        ev = _write_event_log(db, ctx=ctx, rule_results=rule_results, user_id=user_id)
        if ev is not None:
            result.event_log_id = ev.id

        # 8. 触发冻结 → 自动执行 + 停止任务（原熔断逻辑）
        if any_freeze and freeze_reason:
            if task.account_id:
                RiskControlService.freeze_account(
                    db, task.account_id, reason=freeze_reason,
                    rule_name=next(
                        (r.rule_name for r in rule_results if r.should_freeze),
                        "AutoStrategyPreOrder",
                    ),
                    operator_user_id=user_id,
                )
            # 策略级连续失败熔断：停止任务
            if any(r.rule_name == "ConsecutiveFailureRule" for r in rule_results):
                task.is_active = False
                task.total_failed += 1
                task.total_executions += 1

        return result

    # ---------------------------------------------------------
    #  投票前检查（给 voting.py / agent_router.py 用）：返回 (amount_cap, freeze_reason)
    # ---------------------------------------------------------
    @staticmethod
    def check_before_vote(
        db: Session,
        *,
        account_id: int,
        account_balance: float,
        daily_pnl: float,
        initial_balance: float,
        proposed_amount: float = 0.0,
        user_id: int | None = None,
    ) -> tuple[RiskCheckResult, dict[str, Any]]:
        """投票前风控检查：
        - 返回 (result, extra)
        - extra 中包含 amount_cap、final_direction_override（预留）
        - voting.py 若需要冻结，可以依赖 result.should_freeze 直接处理
        """
        profile, effective = RiskProfileManager.resolve_account_params(db, account_id)
        is_frozen, frozen_reason = RiskControlService.is_account_frozen(db, account_id)

        ctx = RiskContext(
            stage="pre_vote",
            order_amount_usd=proposed_amount,
            account_id=account_id,
            account_balance=float(account_balance),
            account_daily_pnl=float(daily_pnl),
            account_initial_balance=float(initial_balance),
            account_is_frozen=is_frozen,
            effective_params=effective,
        )

        rule_results: list[RuleResult] = []
        if is_frozen:
            # 已冻结直接短路
            result = RiskCheckResult(
                passed=False, blocked=False, should_freeze=True,
                freeze_reason=frozen_reason, amount_cap=None,
                message=f"账户已冻结：{frozen_reason}",
                rule_results=[],
            )
            _write_event_log(db, ctx=ctx, rule_results=[], user_id=user_id)
            return result, {"amount_cap": None}

        for cls in PRE_VOTE_RULES:
            rule = cls()
            rule_results.append(rule.check(ctx))

        any_block = any(not r.passed and not r.should_freeze for r in rule_results)
        any_freeze = any(r.should_freeze for r in rule_results)
        amount_caps = [r.amount_cap for r in rule_results if r.amount_cap is not None]
        amount_cap = min(amount_caps) if amount_caps else None
        freeze_reason = next(
            (r.freeze_reason for r in rule_results if r.should_freeze and r.freeze_reason), ""
        )
        messages = []
        for r in rule_results:
            if r.should_freeze:
                messages.append(f"❌ {r.freeze_reason or r.message}")
            elif not r.passed:
                messages.append(f"🛑 {r.message}")
        msg = "\n".join(messages) if messages else "投票前风控通过"

        result = RiskCheckResult(
            passed=not (any_block or any_freeze),
            blocked=any_block and not any_freeze,
            should_freeze=any_freeze,
            freeze_reason=freeze_reason,
            amount_cap=amount_cap,
            message=msg,
            rule_results=rule_results,
        )
        ev = _write_event_log(db, ctx=ctx, rule_results=rule_results, user_id=user_id)
        if ev is not None:
            result.event_log_id = ev.id

        if any_freeze and freeze_reason:
            RiskControlService.freeze_account(
                db, account_id, reason=freeze_reason,
                rule_name=next((r.rule_name for r in rule_results if r.should_freeze), "PreVote"),
                operator_user_id=user_id,
            )

        return result, {"amount_cap": amount_cap}

    # ---------------------------------------------------------
    #  结算后检查（每笔订单结算完，扫日亏）
    # ---------------------------------------------------------
    @staticmethod
    def post_settle_check(
        db: Session,
        *,
        account_id: int,
        user_id: int | None = None,
    ) -> RiskCheckResult:
        acc = db.query(ExecutionAccount).filter(ExecutionAccount.id == account_id).first()
        if acc is None:
            return RiskCheckResult(
                passed=True, blocked=False, should_freeze=False,
                freeze_reason="", amount_cap=None,
                message="账户不存在，跳过结算后风控",
                rule_results=[],
            )
        profile, effective = RiskProfileManager.resolve_account_params(db, account_id)
        ctx = RiskContext(
            stage="post_settle",
            account_id=account_id,
            account_balance=float(acc.current_balance),
            account_daily_pnl=float(acc.daily_pnl),
            account_initial_balance=float(acc.initial_balance),
            effective_params=effective,
        )
        rule_results = [cls().check(ctx) for cls in POST_SETTLE_RULES]

        any_freeze = any(r.should_freeze for r in rule_results)
        freeze_reason = next((r.freeze_reason for r in rule_results if r.should_freeze), "")
        passed = not any_freeze
        msg = "; ".join(r.message for r in rule_results if not r.passed or r.should_freeze) or "结算后风控通过"

        result = RiskCheckResult(
            passed=passed, blocked=False, should_freeze=any_freeze,
            freeze_reason=freeze_reason, amount_cap=None,
            message=msg, rule_results=rule_results,
        )
        ev = _write_event_log(db, ctx=ctx, rule_results=rule_results, user_id=user_id)
        if ev is not None:
            result.event_log_id = ev.id
        if any_freeze and freeze_reason:
            RiskControlService.freeze_account(
                db, account_id, reason=freeze_reason,
                rule_name="PostSettle",
                operator_user_id=user_id,
            )
        return result

    # ---------------------------------------------------------
    #  策略级连续失败状态更新（代替原来直接改 AutoStrategy.consecutive_failures）
    # ---------------------------------------------------------
    @staticmethod
    def update_strategy_consecutive_failures(
        db: Session,
        auto_strategy_id: int,
        *,
        success: bool,
    ) -> None:
        """每次任务执行完调用：成功清零、失败 +1
        - 同步写 AutoStrategy（兼容旧字段）+ StrategyRiskProfile（新真源）
        """
        profile = RiskProfileManager.get_or_create_strategy_profile(db, auto_strategy_id)
        if success:
            profile.consecutive_failures = 0
        else:
            profile.consecutive_failures += 1
        # 镜像旧字段
        task = db.query(AutoStrategy).filter(AutoStrategy.id == auto_strategy_id).first()
        if task is not None:
            task.consecutive_failures = profile.consecutive_failures
        db.flush()
