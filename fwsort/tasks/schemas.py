"""自动任务 Pydantic 校验模型"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AutoTaskCreate(BaseModel):
    """创建任务请求"""

    task_name: str = Field(..., min_length=1, max_length=128, description="任务名称")
    signal_source: Literal["random", "http"] = Field(default="random", description="信号来源")
    gateway: str = Field(default="polymarket_f3", description="交易网关")
    interval: int = Field(default=5, ge=1, le=1440, description="调度间隔（分钟）")
    max_daily_amount: float = Field(default=50.0, gt=0, description="单日最大下单金额 USDC")
    max_daily_count: int = Field(default=50, ge=1, le=1000, description="单日最大下单次数")
    max_consecutive_failures: int = Field(default=5, ge=1, le=100, description="连续失败熔断阈值")
    config_json: dict = Field(default_factory=dict, description="任务额外配置")


class AutoTaskUpdate(BaseModel):
    """更新任务请求"""

    task_name: str | None = Field(default=None, min_length=1, max_length=128)
    signal_source: Literal["random", "http"] | None = None
    gateway: str | None = None
    interval: int | None = Field(default=None, ge=1, le=1440)
    max_daily_amount: float | None = Field(default=None, gt=0)
    max_daily_count: int | None = Field(default=None, ge=1, le=1000)
    max_consecutive_failures: int | None = Field(default=None, ge=1, le=100)
    config_json: dict | None = None


class AutoTaskResponse(BaseModel):
    """任务响应"""

    id: int
    task_name: str
    signal_source: str
    gateway: str
    interval: int
    is_active: bool
    max_daily_amount: float
    max_daily_count: int
    max_consecutive_failures: int
    total_executions: int
    total_success: int
    total_failed: int
    consecutive_failures: int
    config_json: dict
    created_at: datetime
    updated_at: datetime


class AutoTaskLogResponse(BaseModel):
    """任务日志响应"""

    id: int
    task_id: int
    executed_at: datetime
    signal_json: str
    order_result_json: str
    status: int
    error_message: str
    duration_ms: int
    order_id: str
    created_at: datetime


class AutoTaskStartResponse(BaseModel):
    """任务启动响应"""

    task_id: int
    task_name: str
    is_active: bool
    gateway_initialized: bool
    message: str