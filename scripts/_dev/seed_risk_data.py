# -*- coding: utf-8 -*-
"""初始化风控模块基础数据：
1. 确保存在系统默认风控模板（RiskProfile）。
2. 为所有现有账户/策略补全风控档案（AccountRiskProfile/StrategyRiskProfile）。
3. （可选）生成一批 Mock 风控事件日志用于前端演示。

运行： python scripts/_dev/seed_risk_data.py
"""
import random
import uuid
from datetime import datetime, timedelta

from fwsort.database import SyncSessionLocal, init_db
from fwsort.fwlogs import logger
from fwsort.models import AutoStrategy, ExecutionAccount
from fwsort.risk.models import (
    AccountRiskProfile,
    RiskEventLog,
    RiskProfile,
    StrategyRiskProfile,
)
from fwsort.risk.service import _gen_event_uid


def seed():
    # 确保表已创建
    init_db()

    with SyncSessionLocal() as db:
        # 1. 创建系统默认风控模板
        profile = db.query(RiskProfile).filter(RiskProfile.owner_id.is_(None), RiskProfile.is_default == True).first()
        if not profile:
            logger.info("未找到系统默认风控模板，正在创建...")
            profile = RiskProfile(
                name="系统默认",
                owner_id=None,
                is_default=True,
                description="系统内置风控模板，包含基础风控参数",
                risk_single_ratio=0.2,
                risk_daily_loss_ratio=0.15,
                max_daily_amount=None,
                max_daily_count=None,
                max_consecutive_failures=5,
                max_drawdown_ratio=None,
                max_open_positions=None,
                stop_loss_ratio=None,
                take_profit_ratio=None,
                is_active=True,
            )
            db.add(profile)
            db.commit()
            logger.info("系统默认风控模板创建成功")
        else:
            logger.info(f"系统默认风控模板已存在 (ID: {profile.id})")

        # 2. 回填账户风控档案
        accounts = db.query(ExecutionAccount).all()
        for acc in accounts:
            existing = db.query(AccountRiskProfile).filter(AccountRiskProfile.account_id == acc.id).first()
            if not existing:
                logger.info(f"为账户 {acc.uid} (ID: {acc.id}) 创建风控档案...")
                existing = AccountRiskProfile(
                    account_id=acc.id,
                    risk_profile_id=profile.id,  # 关联系统默认模板
                    is_frozen=acc.risk_frozen if hasattr(acc, 'risk_frozen') else False,
                    frozen_reason=getattr(acc, 'risk_frozen_reason', '') or "",
                )
                db.add(existing)
            else:
                # 如果没有关联模板，自动关联系统默认
                if not existing.risk_profile_id:
                    existing.risk_profile_id = profile.id
                    logger.info(f"为账户 {acc.uid} 关联系统默认模板")
        
        # 3. 回填策略风控档案
        strategies = db.query(AutoStrategy).all()
        for strat in strategies:
            existing = db.query(StrategyRiskProfile).filter(StrategyRiskProfile.auto_strategy_id == strat.id).first()
            if not existing:
                logger.info(f"为策略 {strat.task_name} (ID: {strat.id}) 创建风控档案...")
                existing = StrategyRiskProfile(
                    auto_strategy_id=strat.id,
                    risk_profile_id=profile.id,
                    max_daily_amount=getattr(strat, 'max_daily_amount', None) or 50.0,
                    max_daily_count=getattr(strat, 'max_daily_count', None) or 50,
                    max_consecutive_failures=getattr(strat, 'max_consecutive_failures', None) or 5,
                    consecutive_failures=getattr(strat, 'consecutive_failures', None) or 0,
                )
                db.add(existing)
            else:
                if not existing.risk_profile_id:
                    existing.risk_profile_id = profile.id

        # 4. 生成 Mock 风控事件日志（仅当无数据时）
        existing_logs = db.query(RiskEventLog).count()
        if existing_logs == 0 and accounts:
            logger.info("生成 Mock 风控事件日志...")
            now = datetime.utcnow()
            log_types = [
                (1, 1, "风控检查通过", "所有风控规则通过"),
                (2, 2, "风控拦截", "DailyAmountLimitRule: 已达每日最大执行金额"),
                (3, 3, "触发风控冻结", "DailyLossRatioRule: 日亏 16.7% ≥ 阈值 15%"),
            ]
            
            for i in range(50):
                acc = random.choice(accounts)
                auto_strat = random.choice(strategies).id if strategies else None
                event_type, severity, title, message = random.choice(log_types)
                
                log = RiskEventLog(
                    event_uid=_gen_event_uid(),
                    account_id=acc.id,
                    auto_strategy_id=auto_strat,
                    user_id=acc.owner_id,
                    rule_name=random.choice(["DailyLossRatioRule", "DailyAmountLimitRule", "DailyCountLimitRule", "ConsecutiveFailureRule", "SingleOrderRatioRule"]),
                    event_type=event_type,
                    severity=severity,
                    stage=random.choice(["pre_vote", "pre_order", "post_settle"]),
                    title=title,
                    detail_json='{"reason": "Mock event", "ratio": 0.15, "threshold": 0.15}',
                    message=message,
                    balance_snapshot=float(acc.current_balance),
                    daily_pnl_snapshot=float(acc.daily_pnl),
                    order_amount_snapshot=random.uniform(10, 100),
                    created_at=now - timedelta(hours=random.randint(1, 720)),
                )
                db.add(log)
            
            logger.info("Mock 风控事件日志生成完成")

        db.commit()
        logger.info("风控基础数据初始化完成！")


if __name__ == "__main__":
    seed()
