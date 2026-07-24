# SQLAlchemy 数据模型（对应架构文档第五章数据库表）
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    DECIMAL,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base


# 跨方言主键类型：PostgreSQL 用 BigInteger，SQLite 用 Integer（走 ROWID 自动递增）
PKType = BigInteger().with_variant(Integer(), "sqlite")
FKType = BigInteger().with_variant(Integer(), "sqlite")


# ========== 1. 用户表（认证用）==========
class User(Base):
    """用户表（邮箱+密码+JWT 认证）"""

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nickname: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[int] = mapped_column(SmallInteger, default=0, comment="0-访客 1-策略所有者 2-组合管理者 3-管理员")
    status: Mapped[int] = mapped_column(SmallInteger, default=0, comment="0-正常 1-禁用")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # 关系
    execution_accounts: Mapped[list["ExecutionAccount"]] = relationship(back_populates="owner")


# ========== 2. 执行账户表（解耦模型：1用户对N执行账户）==========
class ExecutionAccount(Base):
    """执行账户：每个账户独立排榜（智能体只出信号，执行账户下注）"""

    __tablename__ = "execution_account"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    owner_id: Mapped[int] = mapped_column(FKType, ForeignKey("user.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, comment="polymarket/okx")
    account_type: Mapped[int] = mapped_column(SmallInteger, default=0, comment="0-模拟 1-实盘")
    initial_balance: Mapped[float] = mapped_column(DECIMAL(18, 6), default=1000.0)
    current_balance: Mapped[float] = mapped_column(DECIMAL(18, 6), default=1000.0)
    daily_pnl: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0, comment="日盈亏")
    risk_frozen: Mapped[bool] = mapped_column(Boolean, default=False, comment="风控冻结")
    status: Mapped[int] = mapped_column(SmallInteger, default=0, comment="0-启用 1-黑名单 2-暂停")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # 关系
    owner: Mapped["User"] = relationship(back_populates="execution_accounts")
    performances: Mapped[list["StrategyPerformance"]] = relationship(back_populates="account")


# ========== 3. 策略绩效表（架构文档 5.2）==========
class StrategyPerformance(Base):
    """绩效指标明细（按周期统计）"""

    __tablename__ = "strategy_performance"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(FKType, ForeignKey("execution_account.id"), nullable=False)
    uid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    period_type: Mapped[int] = mapped_column(SmallInteger, nullable=False, comment="1-日 2-周 3-月 4-总")
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # 收益类
    total_return: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    annualized_return: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    sharpe_ratio: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    sortino_ratio: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    calmar_ratio: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    profit_factor: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)

    # 风险类
    max_drawdown: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    drawdown_recovery_days: Mapped[int] = mapped_column(Integer, default=0)
    volatility: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    max_consecutive_loss: Mapped[int] = mapped_column(Integer, default=0)

    # 交易质量
    win_rate: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    profit_loss_ratio: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    avg_hold_duration: Mapped[float] = mapped_column(DECIMAL(18, 2), default=0.0)
    trade_count: Mapped[int] = mapped_column(Integer, default=0)

    # 订单执行质量（V1.0 新增）
    execution_rate: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    avg_slippage: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    avg_latency: Mapped[int] = mapped_column(Integer, default=0)
    cancel_rate: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    execution_score: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)

    # 综合分（福纹综合分）
    composite_score: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0, index=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    account: Mapped["ExecutionAccount"] = relationship(back_populates="performances")


# ========== 4. 榜单快照表（架构文档 5.3）==========
class RankSnapshot(Base):
    """榜单快照（每日/周/月固化）"""

    __tablename__ = "rank_snapshot"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    rank_type: Mapped[int] = mapped_column(SmallInteger, nullable=False, index=True)
    period_end_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    uid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(DECIMAL(18, 6), nullable=False)
    execution_score: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    annualized_return: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    max_drawdown: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    trade_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ========== 5. 智能体组合表（架构文档 5.4）==========
class AgentPortfolio(Base):
    """智能体组合（多智能体协作）"""

    __tablename__ = "agent_portfolio"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_uids: Mapped[str] = mapped_column(Text, nullable=False, comment="JSON数组: 智能体UID")
    collaboration_mode: Mapped[int] = mapped_column(
        SmallInteger, default=1, comment="1-并行 2-串行 3-条件触发 4-动态分配"
    )
    status: Mapped[int] = mapped_column(SmallInteger, default=0, comment="0-运行 1-暂停")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


# ========== 6. 智能体协作日志表（架构文档 5.5）==========
class AgentCollaboration(Base):
    """智能体协作日志"""

    __tablename__ = "agent_collaboration"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(FKType, ForeignKey("agent_portfolio.id"))
    from_uid: Mapped[str] = mapped_column(String(64))
    to_uid: Mapped[str] = mapped_column(String(64))
    message_type: Mapped[int] = mapped_column(
        SmallInteger, comment="1-信号 2-资源请求 3-状态同步"
    )
    message_content: Mapped[str] = mapped_column(Text, comment="JSON")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


# ========== 7. 智能体预测记录表（V1.0 新增：3智能体投票）==========
class AgentPrediction(Base):
    """单个智能体一次预测的结果（投票引擎输入）"""

    __tablename__ = "agent_prediction"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    agent_model: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, comment="BTCUSDT等")
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False, comment="15m/1h")
    direction: Mapped[int] = mapped_column(SmallInteger, nullable=False, comment="1-涨 2-跌 0-平")
    confidence: Mapped[float] = mapped_column(DECIMAL(5, 4), default=0.0, comment="置信度 0~1")
    reasoning: Mapped[str] = mapped_column(Text, comment="智能体推理摘要")
    raw_payload: Mapped[str] = mapped_column(Text, comment="智能体原始返回")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


# ========== 8. 投票决策表（V1.0 核心：2:1多数/全同加倍）==========
class VoteDecision(Base):
    """一次投票决策（3智能体→最终下单动作）"""

    __tablename__ = "vote_decision"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(FKType, ForeignKey("execution_account.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    up_count: Mapped[int] = mapped_column(SmallInteger, default=0)
    down_count: Mapped[int] = mapped_column(SmallInteger, default=0)
    flat_count: Mapped[int] = mapped_column(SmallInteger, default=0)
    final_direction: Mapped[int] = mapped_column(SmallInteger, comment="0-不交易 1-涨 2-跌")
    order_amount_usd: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    order_amount_reason: Mapped[str] = mapped_column(
        String(64), comment="base_5/double_10/risk_skip/no_consensus"
    )
    prediction_ids: Mapped[str] = mapped_column(Text, comment="JSON数组")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


# ========== 9. 订单执行日志表（架构文档 5.6）==========
class OrderExecutionLog(Base):
    """订单执行日志（同时落 PostgreSQL + Elasticsearch）"""

    __tablename__ = "order_execution_log"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(FKType, ForeignKey("execution_account.id"))
    vote_id: Mapped[int] = mapped_column(FKType, ForeignKey("vote_decision.id"))
    order_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    order_type: Mapped[int] = mapped_column(SmallInteger, comment="1-限价 2-市价 3-止损 4-止盈")
    side: Mapped[int] = mapped_column(SmallInteger, comment="1-买入 2-卖出")
    platform: Mapped[str] = mapped_column(String(32), nullable=False, comment="polymarket/okx")
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_price: Mapped[float] = mapped_column(DECIMAL(18, 8), default=0.0)
    actual_price: Mapped[float] = mapped_column(DECIMAL(18, 8), default=0.0)
    quantity: Mapped[float] = mapped_column(DECIMAL(18, 8), nullable=False)
    amount_usd: Mapped[float] = mapped_column(DECIMAL(18, 6), nullable=False)
    status: Mapped[int] = mapped_column(
        SmallInteger, default=1, comment="1-已提交 2-部分成交 3-全部成交 4-已撤销 5-失败"
    )
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    slippage: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    pnl: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0, comment="平仓盈亏")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


# ========== 11. 跟单订阅表（V2.0 跟单模块）==========
class FollowSubscription(Base):
    """跟单订阅：粉丝订阅某个 leader_uid，按月扣费 + 利润分成"""

    __tablename__ = "follow_subscription"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    subscriber_id: Mapped[int] = mapped_column(FKType, ForeignKey("user.id"), nullable=False, index=True)
    leader_uid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    leader_name: Mapped[str] = mapped_column(String(128), default="")
    mode: Mapped[int] = mapped_column(SmallInteger, default=3, comment="1-纯订阅 2-纯分成 3-订阅+分成")
    subscription_fee_usd: Mapped[float] = mapped_column(DECIMAL(18, 6), default=9.9)
    profit_share_ratio: Mapped[float] = mapped_column(DECIMAL(5, 4), default=0.20, comment="盈利抽成比例 0~1")
    follow_amount_usd: Mapped[float] = mapped_column(DECIMAL(18, 6), default=50.0, comment="每笔跟单金额")
    total_followed: Mapped[int] = mapped_column(Integer, default=0)
    total_pnl: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    total_fee_paid: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    total_share_paid: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    status: Mapped[int] = mapped_column(SmallInteger, default=1, comment="0-取消 1-订阅中 2-过期")
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


# ========== 12. 跟单成交记录表（每笔跟单）==========
class FollowOrder(Base):
    """跟单成交记录：粉丝跟单 leader 某笔订单的实际执行结果"""

    __tablename__ = "follow_order"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    subscription_id: Mapped[int] = mapped_column(FKType, ForeignKey("follow_subscription.id"), nullable=False, index=True)
    leader_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[int] = mapped_column(SmallInteger, nullable=False, comment="1-买 2-卖")
    amount_usd: Mapped[float] = mapped_column(DECIMAL(18, 6), nullable=False)
    expected_price: Mapped[float] = mapped_column(DECIMAL(18, 8), default=0.0)
    actual_price: Mapped[float] = mapped_column(DECIMAL(18, 8), default=0.0)
    pnl: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    share_paid: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0, comment="本次利润分成")
    status: Mapped[int] = mapped_column(SmallInteger, default=3, comment="1-提交 2-部分 3-成交 4-撤销 5-失败")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ========== 13. 智能体租用品类表（V2.0 租用模块）==========
class RentalAgent(Base):
    """可租用的智能体（按次 + 包时段）"""

    __tablename__ = "rental_agent"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False, comment="gpt-4o / claude-3-5 / gemini-2.0")
    description: Mapped[str] = mapped_column(Text, default="")
    agent_type: Mapped[str] = mapped_column(String(32), default="general", comment="general/trend/sentiment/onchain")
    price_per_call_usd: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.10)
    price_per_hour_usd: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.50)
    max_concurrent: Mapped[int] = mapped_column(Integer, default=10, comment="包时段最大并发数")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ========== 14. 智能体租用订单表（按次 / 包时段）==========
class RentalOrder(Base):
    """租用订单：粉丝购买某个智能体的调用权或时段独占权"""

    __tablename__ = "rental_order"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    renter_id: Mapped[int] = mapped_column(FKType, ForeignKey("user.id"), nullable=False, index=True)
    agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("rental_agent.id"), nullable=False)
    rental_type: Mapped[int] = mapped_column(SmallInteger, nullable=False, comment="1-按次 2-包时段")
    hours: Mapped[int] = mapped_column(Integer, default=0, comment="包时段时长(小时)，按次为 0")
    used_calls: Mapped[int] = mapped_column(Integer, default=0, comment="按次已用次数")
    total_paid_usd: Mapped[float] = mapped_column(DECIMAL(18, 6), default=0.0)
    status: Mapped[int] = mapped_column(SmallInteger, default=1, comment="0-结束 1-有效 2-过期 3-退款")
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ========== 15. 通知消息表（V2.0 通知中心）==========
class Notification(Base):
    """系统通知：订阅/跟单/风控/榜单变更等"""

    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(FKType, ForeignKey("user.id"), nullable=False, index=True)
    ntype: Mapped[int] = mapped_column(SmallInteger, nullable=False, comment="1-系统 2-跟单 3-风控 4-榜单 5-租用")
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ========== 10. 权重配置表（架构文档 5.7）==========
class WeightConfig(Base):
    """榜单权重配置（管理员可调）"""

    __tablename__ = "weight_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rank_type: Mapped[int] = mapped_column(SmallInteger, nullable=False, unique=True)
    weight_annualized: Mapped[float] = mapped_column(DECIMAL(18, 4), default=0.30)
    weight_drawdown: Mapped[float] = mapped_column(DECIMAL(18, 4), default=0.20)
    weight_sharpe: Mapped[float] = mapped_column(DECIMAL(18, 4), default=0.20)
    weight_profit_loss: Mapped[float] = mapped_column(DECIMAL(18, 4), default=0.15)
    weight_execution: Mapped[float] = mapped_column(DECIMAL(18, 4), default=0.15)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
