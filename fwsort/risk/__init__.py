# fwsort.risk 风控独立模块
# 职责：
#   - 风控参数管理（全局 / 账户级 / 策略级模板）
#   - 可插拔风控规则链（投票前 / 下单前 / 结算后）
#   - 统一风控入口 Facade（供 auto_strategy / voting / 跟单 / 手动下单共用）
#   - 风控事件审计日志（独立表，可追溯）
#   - 风控冻结/解冻唯一入口

from fwsort.risk.rules import (
    BaseRiskRule,
    DailyCountLimitRule,
    DailyAmountLimitRule,
    ConsecutiveFailureRule,
    SingleOrderRatioRule,
    DailyLossRatioRule,
)
from fwsort.risk.service import RiskControlService, RiskCheckResult
from fwsort.risk.manager import RiskProfileManager

__all__ = [
    "BaseRiskRule",
    "DailyCountLimitRule",
    "DailyAmountLimitRule",
    "ConsecutiveFailureRule",
    "SingleOrderRatioRule",
    "DailyLossRatioRule",
    "RiskControlService",
    "RiskCheckResult",
    "RiskProfileManager",
]
