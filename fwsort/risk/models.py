# ========== 风控模块专用数据表 ==========
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    DECIMAL,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fwsort.database import Base


# 跨方言主键类型
PKType = BigInteger().with_variant(Integer(), "sqlite")
FKType = BigInteger().with_variant(Integer(), "sqlite")


# ========== 风控配置模板表（可复用：用户可创建多个模板分配给不同账户/策略）==========
class RiskProfile(Base):
    """风控配置模板：一组风控参数的命名集合
    - 用户可创建"保守型/激进型/默认"等多种模板
    - 账户级(AccountRiskProfile)和策略级(StrategyRiskProfile)可关联模板
    - 模板字段为 NULL 时表示"未覆盖，取全局默认"
    """

    __tablename__ = "risk_profile"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="模板名称，如 保守型/激进型")
    owner_id: Mapped[int | None] = mapped_column(
        FKType, ForeignKey("user.id"), nullable=True, comment="所属用户，NULL=系统内置模板"
    )
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否为用户默认模板")
    description: Mapped[str] = mapped_column(String(256), default="", comment="模板描述")

    # ===== 风控参数：为 NULL 表示未设置（取全局 settings 默认值）=====
    # 单笔
    risk_single_ratio: Mapped[float | None] = mapped_column(
        DECIMAL(5, 4), nullable=True, comment="单笔下单最大占余额比例（0.20=20%）"
    )
    # 日维度
    risk_daily_loss_ratio: Mapped[float | None] = mapped_column(
        DECIMAL(5, 4), nullable=True, comment="日亏损比例阈值（0.30=亏30%冻结）"
    )
    max_daily_amount: Mapped[float | None] = mapped_column(
        DECIMAL(18, 6), nullable=True, comment="单日最大下单总金额 USDC"
    )
    max_daily_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="单日最大下单次数")
    # 熔断
    max_consecutive_failures: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="连续失败熔断阈值（0表示不启用）"
    )
    max_drawdown_ratio: Mapped[float | None] = mapped_column(
        DECIMAL(5, 4), nullable=True, comment="最大回撤熔断阈值（预留，0.50=50%）"
    )
    # 持仓
    max_open_positions: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="最大同时持仓数（预留）"
    )
    # 止盈止损
    stop_loss_ratio: Mapped[float | None] = mapped_column(
        DECIMAL(5, 4), nullable=True, comment="单笔止损比例（预留）"
    )
    take_profit_ratio: Mapped[float | None] = mapped_column(
        DECIMAL(5, 4), nullable=True, comment="单笔止盈比例（预留）"
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_risk_profile_owner", "owner_id", "is_active"),
    )


# ========== 账户级风控配置（1 ExecutionAccount : 1 AccountRiskProfile）==========
class AccountRiskProfile(Base):
    """账户级风控参数：覆盖模板 + 个性化定制
    - 通过 risk_profile_id 继承模板，本记录字段非空则再覆盖一次
    - 解析优先级：本记录具体值 > 关联模板值 > 全局 settings 默认值
    """

    __tablename__ = "account_risk_profile"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        FKType, ForeignKey("execution_account.id"), nullable=False, unique=True, index=True
    )
    risk_profile_id: Mapped[int | None] = mapped_column(
        FKType, ForeignKey("risk_profile.id"), nullable=True, comment="关联风控模板"
    )

    # 个性化覆盖字段（NULL 表示走模板 / 全局默认）
    risk_single_ratio: Mapped[float | None] = mapped_column(DECIMAL(5, 4), nullable=True)
    risk_daily_loss_ratio: Mapped[float | None] = mapped_column(DECIMAL(5, 4), nullable=True)
    max_daily_amount: Mapped[float | None] = mapped_column(DECIMAL(18, 6), nullable=True)
    max_daily_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_consecutive_failures: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_drawdown_ratio: Mapped[float | None] = mapped_column(DECIMAL(5, 4), nullable=True)
    max_open_positions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stop_loss_ratio: Mapped[float | None] = mapped_column(DECIMAL(5, 4), nullable=True)
    take_profit_ratio: Mapped[float | None] = mapped_column(DECIMAL(5, 4), nullable=True)

    # 运行时状态（非参数）
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, comment="当前连续失败次数")
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False, comment="风控冻结状态（唯一真源，账户表risk_frozen为镜像）")
    frozen_reason: Mapped[str] = mapped_column(String(256), default="", comment="冻结原因")
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="冻结时间")
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最近一次风控检查时间")

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    profile: Mapped["RiskProfile | None"] = relationship(foreign_keys=[risk_profile_id])


# ========== 策略级风控配置（1 AutoStrategy : 1 StrategyRiskProfile）==========
class StrategyRiskProfile(Base):
    """策略级风控参数（自动任务专用）
    - 兼容原来 AutoStrategy 表的 3 个字段，迁移后原字段作兼容镜像
    """

    __tablename__ = "strategy_risk_profile"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    auto_strategy_id: Mapped[int] = mapped_column(
        FKType, ForeignKey("auto_strategy.id"), nullable=False, unique=True, index=True
    )
    risk_profile_id: Mapped[int | None] = mapped_column(
        FKType, ForeignKey("risk_profile.id"), nullable=True, comment="关联风控模板"
    )

    # 个性化覆盖（兼容旧字段）
    max_daily_amount: Mapped[float | None] = mapped_column(DECIMAL(18, 6), nullable=True, comment="单日最大下单金额")
    max_daily_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="单日最大下单次数")
    max_consecutive_failures: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="连续失败熔断阈值")
    risk_single_ratio: Mapped[float | None] = mapped_column(DECIMAL(5, 4), nullable=True)
    risk_daily_loss_ratio: Mapped[float | None] = mapped_column(DECIMAL(5, 4), nullable=True)
    max_drawdown_ratio: Mapped[float | None] = mapped_column(DECIMAL(5, 4), nullable=True)

    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, comment="当前连续失败次数（旧 AutoStrategy.consecutive_failures 迁移至此）")

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    profile: Mapped["RiskProfile | None"] = relationship(foreign_keys=[risk_profile_id])


# ========== 风控事件审计日志（可追溯：谁什么时候触发了什么风控、影响哪些订单）==========
class RiskEventLog(Base):
    """风控事件日志：每次风控规则触发（通过/拦截/冻结）都写一条
    - 独立审计表，不与业务日志混存
    - 按 account_id / strategy_id / rule_name / created_at 多维检索
    """

    __tablename__ = "risk_event_log"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    event_uid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True, comment="事件唯一ID RSK-{yyyymmdd}-{8hex}")

    # 关联维度
    account_id: Mapped[int | None] = mapped_column(
        FKType, ForeignKey("execution_account.id"), nullable=True, index=True
    )
    auto_strategy_id: Mapped[int | None] = mapped_column(
        FKType, ForeignKey("auto_strategy.id"), nullable=True, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        FKType, ForeignKey("user.id"), nullable=True, index=True
    )

    # 事件核心信息
    rule_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="触发的规则类名，如 DailyAmountLimitRule")
    event_type: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, index=True,
        comment="1-检查通过 2-拦截（未通过） 3-触发冻结 4-手动解冻 5-参数变更"
    )
    severity: Mapped[int] = mapped_column(
        SmallInteger, default=1, comment="1-信息 2-警告 3-严重（冻结/大额拦截）"
    )
    stage: Mapped[str] = mapped_column(
        String(32), default="pre_order",
        comment="触发阶段: pre_vote/pre_order/post_settle/manual"
    )

    # 内容
    title: Mapped[str] = mapped_column(String(128), nullable=False, comment="事件标题（用户可读）")
    detail_json: Mapped[str] = mapped_column(Text, default="{}", comment="事件详情 JSON（含拦截阈值、实际值、上下文快照）")
    message: Mapped[str] = mapped_column(String(512), default="", comment="给用户看的简要原因")

    # 快照（便于事后复盘）
    balance_snapshot: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0, comment="触发时账户余额")
    daily_pnl_snapshot: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0, comment="触发时日盈亏")
    order_amount_snapshot: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0, comment="触发时订单金额（若有）")

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    __table_args__ = (
        Index("idx_risk_event_account_time", "account_id", "created_at"),
        Index("idx_risk_event_strategy_time", "auto_strategy_id", "created_at"),
        Index("idx_risk_event_rule_time", "rule_name", "created_at"),
        Index("idx_risk_event_type_time", "event_type", "created_at"),
    )
