# ========== Pydantic 请求/响应模型 ==========
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ========== 风控参数公共部分 ==========
class RiskParamsPatch(BaseModel):
    """可部分更新的风控参数（PATCH 请求体，所有字段可选）"""
    risk_single_ratio: Optional[float] = Field(None, ge=0, le=1, description="单笔最大占余额比例 0~1")
    risk_daily_loss_ratio: Optional[float] = Field(None, ge=0, le=1, description="日亏损比例冻结阈值 0~1")
    max_daily_amount: Optional[float] = Field(None, ge=0, description="单日最大下单总金额")
    max_daily_count: Optional[int] = Field(None, ge=0, description="单日最大下单次数")
    max_consecutive_failures: Optional[int] = Field(None, ge=0, description="连续失败熔断阈值")
    max_drawdown_ratio: Optional[float] = Field(None, ge=0, le=1, description="最大回撤冻结阈值（预留）")
    max_open_positions: Optional[int] = Field(None, ge=0, description="最大持仓数（预留）")
    stop_loss_ratio: Optional[float] = Field(None, ge=0, le=1, description="单笔止损比例（预留）")
    take_profit_ratio: Optional[float] = Field(None, ge=0, le=1, description="单笔止盈比例（预留）")


# ========== 风控模板（RiskProfile）==========
class RiskProfileCreate(BaseModel):
    name: str = Field(..., max_length=64)
    description: Optional[str] = Field("", max_length=256)
    is_default: bool = False
    risk_profile_id: Optional[int] = None  # 可基于已有模板复制
    params: RiskParamsPatch = Field(default_factory=RiskParamsPatch)


class RiskProfileUpdate(RiskParamsPatch):
    name: Optional[str] = Field(None, max_length=64)
    description: Optional[str] = Field(None, max_length=256)
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None


class RiskProfileOut(BaseModel):
    id: int
    name: str
    owner_id: Optional[int]
    is_default: bool
    is_active: bool
    description: str
    risk_single_ratio: Optional[float]
    risk_daily_loss_ratio: Optional[float]
    max_daily_amount: Optional[float]
    max_daily_count: Optional[int]
    max_consecutive_failures: Optional[int]
    max_drawdown_ratio: Optional[float]
    max_open_positions: Optional[int]
    stop_loss_ratio: Optional[float]
    take_profit_ratio: Optional[float]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ========== 账户级风控配置 ==========
class AccountRiskProfileOut(BaseModel):
    id: int
    account_id: int
    risk_profile_id: Optional[int]
    # 参数
    risk_single_ratio: Optional[float]
    risk_daily_loss_ratio: Optional[float]
    max_daily_amount: Optional[float]
    max_daily_count: Optional[int]
    max_consecutive_failures: Optional[int]
    max_drawdown_ratio: Optional[float]
    max_open_positions: Optional[int]
    stop_loss_ratio: Optional[float]
    take_profit_ratio: Optional[float]
    # 运行状态
    consecutive_failures: int
    is_frozen: bool
    frozen_reason: str
    frozen_at: Optional[datetime]
    last_check_at: Optional[datetime]
    # 关联模板解析后的实际生效参数
    effective_params: dict = Field(default_factory=dict)

    class Config:
        from_attributes = True


class AccountRiskPatch(RiskParamsPatch):
    risk_profile_id: Optional[int] = Field(None, description="切换关联风控模板；None=解绑走默认")


# ========== 策略级风控配置 ==========
class StrategyRiskProfileOut(BaseModel):
    id: int
    auto_strategy_id: int
    risk_profile_id: Optional[int]
    max_daily_amount: Optional[float]
    max_daily_count: Optional[int]
    max_consecutive_failures: Optional[int]
    risk_single_ratio: Optional[float]
    risk_daily_loss_ratio: Optional[float]
    max_drawdown_ratio: Optional[float]
    consecutive_failures: int
    effective_params: dict = Field(default_factory=dict)

    class Config:
        from_attributes = True


class StrategyRiskPatch(RiskParamsPatch):
    risk_profile_id: Optional[int] = None


# ========== 风控事件日志 ==========
class RiskEventLogOut(BaseModel):
    id: int
    event_uid: str
    account_id: Optional[int]
    auto_strategy_id: Optional[int]
    user_id: Optional[int]
    rule_name: str
    event_type: int  # 1通过 2拦截 3冻结 4解冻 5参数变更
    severity: int
    stage: str
    title: str
    message: str
    detail_json: dict = Field(default_factory=dict)
    balance_snapshot: float
    daily_pnl_snapshot: float
    order_amount_snapshot: float
    created_at: datetime

    class Config:
        from_attributes = True


class RiskEventListResp(BaseModel):
    total: int
    items: list[RiskEventLogOut]


# ========== 冻结 / 解冻 请求 ==========
class FreezeAccountReq(BaseModel):
    account_id: int
    reason: str = Field(..., max_length=256, description="冻结原因")


class UnfreezeAccountReq(BaseModel):
    account_id: int
    reason: str = Field("", max_length=256, description="解冻说明（如'人工复核通过'）")


# ========== 执行风控检查的响应 ==========
class RiskCheckResp(BaseModel):
    passed: bool
    blocked: bool                      # True = 规则明确拦截
    should_freeze: bool                # True = 触发冻结
    amount_cap: Optional[float]        # 非 None：订单金额截断到此值
    message: str
    rule_results: list[dict]           # 每条规则执行结果（调试/审计用）
    event_log_id: Optional[int] = None
