# Pydantic Schemas：API 入参/出参校验
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field


# ========== 通用 ==========
class ApiResponse(BaseModel):
    success: bool = True
    message: str = "success"
    data: Any = None
    code: int = 200
    timestamp: int = 0


# ========== 认证 ==========
class RegisterReq(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=64)
    nickname: str = Field(min_length=2, max_length=64)


class LoginReq(BaseModel):
    email: EmailStr
    password: str


class TokenResp(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    user_id: int
    nickname: str
    role: int


# ========== 执行账户 ==========
class CreateAccountReq(BaseModel):
    name: str = Field(min_length=2, max_length=128)
    platform: Literal["polymarket", "okx"]
    account_type: Literal[0, 1] = 0  # 0-模拟 1-实盘
    initial_balance: float = Field(default=1000.0, gt=0)


class AccountResp(BaseModel):
    id: int
    uid: str
    name: str
    platform: str
    account_type: int
    current_balance: float
    daily_pnl: float
    risk_frozen: bool
    status: int
    created_at: datetime

    class Config:
        from_attributes = True


# ========== 榜单 ==========
class RankItem(BaseModel):
    rank: int
    uid: str
    name: str
    platform: str
    composite_score: float
    annualized_return: float
    max_drawdown: float
    calmar_ratio: float
    sharpe_ratio: float
    win_rate: float
    trade_count: int
    execution_score: float
    tier: str  # 段位


class RankListResp(BaseModel):
    rank_type: str
    items: list[RankItem]
    total: int
    page: int
    page_size: int


# ========== 智能体预测（V1.0 多智能体策略）==========
class AgentPredictionReq(BaseModel):
    symbol: str = "BTCUSDT"
    timeframe: str = "15m"


class AgentPredictionItem(BaseModel):
    id: int
    agent_name: str
    agent_model: str
    direction: int
    confidence: float
    reasoning: str
    latency_ms: int
    created_at: datetime


class VoteResultResp(BaseModel):
    vote_id: int
    up_count: int
    down_count: int
    flat_count: int
    final_direction: int
    order_amount_usd: float
    reason: str
    predictions: list[AgentPredictionItem]
    order_id: str | None = None
    order_status: int | None = None


# ========== 权重配置 ==========
class WeightConfigReq(BaseModel):
    weight_annualized: float = Field(ge=0, le=1)
    weight_drawdown: float = Field(ge=0, le=1)
    weight_sharpe: float = Field(ge=0, le=1)
    weight_profit_loss: float = Field(ge=0, le=1)
    weight_execution: float = Field(ge=0, le=1)


class WeightConfigResp(BaseModel):
    rank_type: int
    weight_annualized: float
    weight_drawdown: float
    weight_sharpe: float
    weight_profit_loss: float
    weight_execution: float
