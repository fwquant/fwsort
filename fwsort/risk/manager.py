# ========== 风控参数解析与加载管理器 ==========
# 职责：
#   1. 三级参数合并：实例个性化覆盖 > 关联模板 > 全局 settings 默认
#   2. 数据库不存在 AccountRiskProfile/StrategyRiskProfile 行时自动懒创建（写入镜像）
#   3. 读取 AutoStrategy / ExecutionAccount 旧字段做"向后兼容"
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from fwsort.config import settings
from fwsort.fwlogs import logger
from fwsort.models import AutoStrategy, ExecutionAccount
from fwsort.risk.models import (
    AccountRiskProfile,
    RiskProfile,
    StrategyRiskProfile,
)


class RiskProfileManager:
    """风控参数解析管理器（轻量无状态，所有方法可独立调用）"""

    # ---------- 全局默认值（对应 settings） ----------
    @staticmethod
    def global_defaults() -> dict[str, Any]:
        """settings 里的风控默认值（兜底用）"""
        return {
            "risk_single_ratio": float(settings.RISK_SINGLE_RATIO),
            "risk_daily_loss_ratio": float(settings.RISK_DAILY_LOSS_RATIO),
            "max_daily_amount": 2000.0,
            "max_daily_count": 10,
            "max_consecutive_failures": 8,
            "max_drawdown_ratio": 0.15,
            "max_open_positions": 3,
            "stop_loss_ratio": 0.05,
            "take_profit_ratio": 0.10,
        }

    # ---------- 参数合并：三级覆盖 ----------
    @staticmethod
    def _merge(
        instance: Any,          # AccountRiskProfile 或 StrategyRiskProfile 实例
        template: RiskProfile | None,
    ) -> dict[str, Any]:
        """三级合并：instance != NULL > template != NULL > global_defaults"""
        merged: dict[str, Any] = RiskProfileManager.global_defaults()
        param_fields = [
            "risk_single_ratio", "risk_daily_loss_ratio",
            "max_daily_amount", "max_daily_count", "max_consecutive_failures",
            "max_drawdown_ratio", "max_open_positions",
            "stop_loss_ratio", "take_profit_ratio",
        ]
        # 模板层覆盖
        if template:
            for f in param_fields:
                v = getattr(template, f, None)
                if v is not None:
                    merged[f] = float(v) if isinstance(v, (int, float)) and not isinstance(f, int) else v
        # 实例层再覆盖
        if instance:
            for f in param_fields:
                v = getattr(instance, f, None)
                if v is not None:
                    merged[f] = float(v) if isinstance(v, (int, float)) and not isinstance(f, int) else v
        return merged

    # ---------- 账户级风控：懒创建 + 兼容旧字段 ----------
    @staticmethod
    def get_or_create_account_profile(
        db: Session, account_id: int,
    ) -> AccountRiskProfile:
        """获取账户级风控配置，不存在则懒创建（并镜像旧 execution_account.risk_frozen 字段）"""
        row = (
            db.query(AccountRiskProfile)
            .filter(AccountRiskProfile.account_id == account_id)
            .first()
        )
        if row is not None:
            return row
        # 懒创建
        acc = db.query(ExecutionAccount).filter(ExecutionAccount.id == account_id).first()
        row = AccountRiskProfile(
            account_id=account_id,
            risk_profile_id=None,
            consecutive_failures=0,
            is_frozen=bool(acc.risk_frozen) if acc else False,
            frozen_reason="迁移自旧字段 execution_account.risk_frozen" if (acc and acc.risk_frozen) else "",
            frozen_at=None,
        )
        db.add(row)
        db.flush()
        logger.info(f"[RiskProfileManager] 懒创建账户级风控配置: account_id={account_id}")
        return row

    @staticmethod
    def resolve_account_params(
        db: Session, account_id: int,
    ) -> tuple[AccountRiskProfile, dict[str, Any]]:
        """解析账户级实际生效参数；返回 (profile_row, merged_params)"""
        profile = RiskProfileManager.get_or_create_account_profile(db, account_id)
        template = (
            db.query(RiskProfile).filter(RiskProfile.id == profile.risk_profile_id).first()
            if profile.risk_profile_id else None
        )
        effective = RiskProfileManager._merge(profile, template)
        return profile, effective

    # ---------- 策略（自动任务）级风控：懒创建 + 兼容旧字段 ----------
    @staticmethod
    def get_or_create_strategy_profile(
        db: Session, auto_strategy_id: int,
    ) -> StrategyRiskProfile:
        """获取策略级风控配置，不存在则懒创建（并镜像旧 AutoStrategy 三字段）"""
        row = (
            db.query(StrategyRiskProfile)
            .filter(StrategyRiskProfile.auto_strategy_id == auto_strategy_id)
            .first()
        )
        if row is not None:
            return row
        task = db.query(AutoStrategy).filter(AutoStrategy.id == auto_strategy_id).first()
        row = StrategyRiskProfile(
            auto_strategy_id=auto_strategy_id,
            risk_profile_id=None,
            max_daily_amount=float(task.max_daily_amount) if task and task.max_daily_amount is not None else 50.0,
            max_daily_count=int(task.max_daily_count) if task and task.max_daily_count is not None else 50,
            max_consecutive_failures=(
                int(task.max_consecutive_failures)
                if task and task.max_consecutive_failures is not None else 5
            ),
            consecutive_failures=int(task.consecutive_failures) if task else 0,
        )
        db.add(row)
        db.flush()
        logger.info(
            f"[RiskProfileManager] 懒创建策略级风控配置: auto_strategy_id={auto_strategy_id} "
            f"daily_amt={row.max_daily_amount} daily_cnt={row.max_daily_count} "
            f"max_fail={row.max_consecutive_failures}"
        )
        return row

    @staticmethod
    def resolve_strategy_params(
        db: Session, auto_strategy_id: int,
    ) -> tuple[StrategyRiskProfile, dict[str, Any]]:
        profile = RiskProfileManager.get_or_create_strategy_profile(db, auto_strategy_id)
        template = (
            db.query(RiskProfile).filter(RiskProfile.id == profile.risk_profile_id).first()
            if profile.risk_profile_id else None
        )
        effective = RiskProfileManager._merge(profile, template)
        # 策略级特有的 3 个字段：优先从 AutoStrategy 表读取（用户编辑的源头，确保热加载）
        task = db.query(AutoStrategy).filter(AutoStrategy.id == auto_strategy_id).first()
        if task:
            for f in ("max_daily_amount", "max_daily_count", "max_consecutive_failures"):
                v = getattr(task, f, None)
                if v is not None:
                    effective[f] = float(v) if f == "max_daily_amount" else int(v)
        return profile, effective
