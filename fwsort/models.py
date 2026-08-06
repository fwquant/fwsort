# SQLAlchemy 数据模型（对应架构文档第五章数据库表）
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
    # 主账号可见性开关（粒度A：是否参与总榜单）
    share_to_global: Mapped[bool] = mapped_column(Boolean, default=True, comment="主账号公开开关：True-参与总榜单")
    # 主账号可订阅性开关（粒度B：是否允许被订阅跟单）
    allow_follow: Mapped[bool] = mapped_column(Boolean, default=True, comment="允许被订阅：True-可被跟单")
    token_ttl_minutes: Mapped[int] = mapped_column(Integer, default=10080, comment="登录有效期（分钟），默认7天=10080")
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
    # ===== 交易员需求新增字段（20260729）=====
    target_url: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="交易标 URL")
    target_symbol: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="标的简称（由 URL 解析）")
    order_amount_usd: Mapped[float] = mapped_column(DECIMAL(18, 6), default=50.0, comment="每次下单金额 USDT")
    signal: Mapped[str] = mapped_column(String(16), default="NEUTRAL", comment="当前信号 UP/DOWN/NEUTRAL")
    signal_source: Mapped[str] = mapped_column(String(32), default="random", comment="random/gpt-4o/claude/gemini/moa")
    signal_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_order_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    public_enabled: Mapped[bool] = mapped_column(Boolean, default=True, comment="账户级可见性：True-参与总榜单")
    # ====================================
    # WP-05：软删除时间戳（None=未删除；时间=已删除）
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
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
    subscription_id: Mapped[int] = mapped_column(FKType, ForeignKey("follow_subscription.id"), nullable=False,
                                                 index=True)
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


# ========== 16. 登录失败审计表（WP-03 限流配套）==========
class LoginAttempt(Base):
    """登录失败审计：持久化每次失败记录，支持事后追溯与安全分析
    - 限流热路径只查 Redis（rate_limit.py）
    - 本表冷存全部失败/成功事件，供安全审计 / 攻击溯源使用
    """

    __tablename__ = "login_attempt"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    ip: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    user_agent: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    reason: Mapped[str] = mapped_column(String(64), default="", nullable=False,
                                        comment="invalid_credentials/user_disabled/locked")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )


# ========== 17. 订单日志 Outbox 表（WP-09：事务一致性）==========
class OutboxEvent(Base):
    """订单日志异步投递 outbox：先入库再异步写 ES
    - 同一事务内把 OrderExecutionLog + OutboxEvent 一起 commit
    - 后台 flush_outbox Celery 任务每 30s 扫描 status=0 的事件
    - 写 ES 成功后置 status=1；失败 status=2（重试 ≤ 3 次）
    - 进程崩溃后重启也能从 status=0/2 继续消费
    """

    __tablename__ = "outbox_event"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    # 事件类型：当前仅 order_log_index
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, default="order_log_index", index=True)
    # 关联业务实体（订单日志 ID）
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, comment="ES 文档 JSON 序列化")
    # 状态：0-待消费 1-成功 2-失败重试
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False, index=True)
    # 重试次数
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 失败原因（最近一次）
    last_error: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    # 调度：下一次重试时间（失败后递增退避）
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


# ========== 18. 系统配置表（单表：默认值 + 覆盖值）==========
class SystemConfig(Base):
    """系统配置单表：默认值 + 用户覆盖值
    - default_value: 系统出厂默认值（种子写入，不可通过管理接口修改）
    - config_value: 用户覆盖值（NULL 表示未覆盖，读取时取 default_value）
    - 读取逻辑：COALESCE(config_value, default_value)
    - 重置逻辑：SET config_value = NULL
    """

    __tablename__ = "system_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    config_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    default_value: Mapped[str] = mapped_column(Text, nullable=False)
    config_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_type: Mapped[str] = mapped_column(String(16), nullable=False, default="str")
    group: Mapped[str] = mapped_column(String(32), nullable=False, default="general", index=True)
    description: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    readonly: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False,
                                           comment="True 表示不可通过管理接口修改")
    updated_by: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


# ========== 19. 自动策略表（信号管理器 + 自动下单）==========
class AutoStrategy(Base):
    """自动策略：定时获取信号 → 下单 → 记录日志"""

    __tablename__ = "auto_strategy"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    task_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="任务名称")
    signal_source: Mapped[str] = mapped_column(String(32), nullable=False, default="random",
                                               comment="信号来源: random/http")
    gateway: Mapped[str] = mapped_column(String(32), nullable=False, default="polymarket_f3", comment="交易网关")
    interval: Mapped[int] = mapped_column(Integer, default=5, comment="调度间隔（分钟）")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否启用")
    start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True,
                                                        comment="首次执行时间（为空则启用后立即执行）")
    loop_count: Mapped[int] = mapped_column(Integer, default=0, comment="循环次数（0=无限循环，直到手动停止）")
    executed_count: Mapped[int] = mapped_column(Integer, default=0, comment="已执行次数（用于循环计数）")
    config_json: Mapped[str] = mapped_column(Text, default="{}", comment="任务额外配置 JSON")
    max_daily_amount: Mapped[float] = mapped_column(DECIMAL(18, 6), default=50.0, comment="单日最大下单金额 USDC")
    max_daily_count: Mapped[int] = mapped_column(Integer, default=50, comment="单日最大下单次数")
    max_consecutive_failures: Mapped[int] = mapped_column(Integer, default=5, comment="连续失败熔断阈值")
    total_executions: Mapped[int] = mapped_column(Integer, default=0, comment="总执行次数")
    total_success: Mapped[int] = mapped_column(Integer, default=0, comment="成功次数")
    total_failed: Mapped[int] = mapped_column(Integer, default=0, comment="失败次数")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, comment="当前连续失败次数")
    # 关联执行账户（1:1，创建任务时自动创建）
    account_id: Mapped[int | None] = mapped_column(FKType, ForeignKey("execution_account.id"), nullable=True,
                                                   index=True, comment="关联执行账户ID")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    # === 新增：资金与统计字段 ===
    initial_balance: Mapped[DECIMAL] = mapped_column(DECIMAL(18, 6), default=1000.0, comment="初始资金 USDC")
    current_balance: Mapped[DECIMAL] = mapped_column(DECIMAL(18, 6), default=1000.0, comment="当前净值 USDC")
    total_pnl: Mapped[DECIMAL] = mapped_column(DECIMAL(18, 6), default=0.0, comment="累计盈亏 USDC")
    total_trades: Mapped[int] = mapped_column(Integer, default=0, comment="总交易次数")
    win_trades: Mapped[int] = mapped_column(Integer, default=0, comment="盈利次数")
    loss_trades: Mapped[int] = mapped_column(Integer, default=0, comment="亏损次数")
    win_rate: Mapped[float] = mapped_column(Float, default=0.0, comment="胜率 %")
    max_drawdown: Mapped[float] = mapped_column(Float, default=0.0, comment="最大回撤率 %")
    sharpe_ratio: Mapped[float] = mapped_column(Float, default=0.0, comment="夏普比率")
    profit_loss_ratio: Mapped[float] = mapped_column(Float, default=0.0, comment="盈亏比")

    logs: Mapped[list["AutoStrategyLog"]] = relationship(back_populates="task")
    account: Mapped["ExecutionAccount | None"] = relationship(foreign_keys=[account_id])


# ========== 20. 自动策略执行日志表 ==========
class AutoStrategyLog(Base):
    """自动策略日志：执行日志 + 操作日志 + 盈亏追踪"""

    __tablename__ = "auto_strategy_log"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(FKType, ForeignKey("auto_strategy.id"), nullable=False, index=True)
    log_type: Mapped[int] = mapped_column(
        SmallInteger, default=0, index=True, comment="0-执行日志 1-操作日志"
    )
    action_type: Mapped[str] = mapped_column(String(32), default="",
                                             comment="操作类型: start/stop/create/update/delete/execute_manual/init_gateway/fuse_triggered")
    executed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    signal_json: Mapped[str] = mapped_column(Text, default="{}", comment="信号内容 JSON")
    order_result_json: Mapped[str] = mapped_column(Text, default="{}", comment="下单结果 JSON")
    status: Mapped[int] = mapped_column(
        SmallInteger, default=0, comment="0-成功 1-失败 2-已重试成功 3-已熔断 4-无信号"
    )
    error_message: Mapped[str] = mapped_column(String(512), default="", comment="错误信息")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, comment="执行耗时（毫秒）")
    order_id: Mapped[str] = mapped_column(String(128), default="", comment="订单ID")
    detail_json: Mapped[str] = mapped_column(Text, default="{}", comment="操作详情 JSON")
    # 增强：信号详情（含市场信息）
    signal_detail_json: Mapped[str] = mapped_column(Text, default="{}",
                                                    comment="信号详情 JSON(symbol/direction/amount/market_id/market_slug/market_question)")
    # 增强：执行详情（含操作参数）
    execution_detail_json: Mapped[str] = mapped_column(Text, default="{}",
                                                       comment="执行详情 JSON(gateway/side/order_type/market_question/making_amount/taking_amount)")
    # 增强：结果详情（含订单回执）
    result_detail_json: Mapped[str] = mapped_column(Text, default="{}",
                                                    comment="结果详情 JSON(order_id/status/filled_amount/price/tx_hash/market_result)")
    # 盈亏追踪
    pnl_amount: Mapped[float] = mapped_column(default=0.0, comment="盈亏金额(USD)")
    pnl_percent: Mapped[float] = mapped_column(default=0.0, comment="盈亏百分比(%)")
    is_profit: Mapped[bool] = mapped_column(default=False, comment="是否盈利")
    market_resolved: Mapped[bool] = mapped_column(default=False, comment="市场是否已结算")
    # 开平仓价格（为资金曲线/滑点统计服务）
    entry_price: Mapped[float | None] = mapped_column(default=None, comment="开仓价（成本价）")
    exit_price: Mapped[float | None] = mapped_column(default=None, comment="平仓价（结算价）")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    task: Mapped["AutoStrategy"] = relationship(back_populates="logs")


# ========== 21. 策略交易明细表（每笔已成交记录） ==========
class StrategyTrade(Base):
    """策略交易明细表 - 每笔已成交的交易记录（含持仓中与已平仓）"""
    __tablename__ = "strategy_trade"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    trade_uid: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False,
                                           comment="交易唯一ID TRD-{yyyymmdd}-{8hex}")

    # 策略维度
    strategy_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True, comment="策略名")
    auto_strategy_id: Mapped[int | None] = mapped_column(FKType, ForeignKey("auto_strategy.id"), nullable=True,
                                                         index=True, comment="关联自动策略")
    account_id: Mapped[int | None] = mapped_column(FKType, ForeignKey("execution_account.id"), nullable=True,
                                                   index=True, comment="关联账户")
    source_strategy: Mapped[str] = mapped_column(String(64), default="", index=True, comment="引用的策略名")

    # 标的维度
    platform: Mapped[str] = mapped_column(String(32), nullable=False, comment="polymarket/okx")
    symbol: Mapped[str] = mapped_column(String(64), default="", comment="交易对/市场标识")
    market_question: Mapped[str] = mapped_column(String(512), default="", comment="Polymarket 市场问题")
    market_slug: Mapped[str] = mapped_column(String(128), default="", comment="市场 slug")

    # 交易核心字段
    direction: Mapped[str] = mapped_column(String(16), default="", index=True, comment="方向: UP/DOWN/NEUTRAL")
    side: Mapped[int] = mapped_column(SmallInteger, nullable=False, comment="1-买入 2-卖出")
    order_type: Mapped[int] = mapped_column(SmallInteger, default=2, comment="1-限价 2-市价")
    order_id: Mapped[str] = mapped_column(String(128), default="", index=True, comment="交易所订单ID")

    # 价格与数量
    entry_price: Mapped[float] = mapped_column(Float, nullable=False, comment="开仓价")
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True, comment="平仓价")
    quantity: Mapped[float] = mapped_column(Float, default=0.0, comment="数量")
    amount_usd: Mapped[float] = mapped_column(Float, nullable=False, comment="下单金额 USDC")

    # 盈亏字段
    pnl_amount: Mapped[float] = mapped_column(Float, default=0.0, comment="盈亏金额 USD")
    pnl_percent: Mapped[float] = mapped_column(Float, default=0.0, comment="盈亏百分比 %")
    is_profit: Mapped[bool] = mapped_column(Boolean, default=False, index=True, comment="是否盈利")
    is_win: Mapped[bool] = mapped_column(Boolean, default=False, index=True, comment="是否胜利（方向判断正确）")

    # 时间字段
    entry_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True, comment="开仓时间")
    exit_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True, comment="平仓时间")
    hold_duration_seconds: Mapped[int] = mapped_column(Integer, default=0, comment="持仓时长（秒）")

    # 状态字段
    status: Mapped[int] = mapped_column(SmallInteger, default=0, index=True,
                                        comment="0-持仓中 1-已平仓盈利 2-已平仓亏损 3-已平仓持平 4-已撤销 5-失败")
    market_resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True, comment="市场是否已结算")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="结算时间")

    # 风控与执行质量
    slippage: Mapped[float] = mapped_column(Float, default=0.0, comment="滑点")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, comment="下单延迟")
    execution_detail_json: Mapped[str] = mapped_column(Text, default="{}", comment="执行详情")
    result_detail_json: Mapped[str] = mapped_column(Text, default="{}", comment="结果详情")

    # 软删除与时间戳
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_strategy_trade_name_time", "strategy_name", "entry_at"),
        Index("idx_strategy_trade_name_status", "strategy_name", "status"),
        Index("idx_strategy_trade_profit", "strategy_name", "is_profit"),
        Index("idx_strategy_trade_source", "source_strategy", "entry_at"),
    )


# ========== 22. 策略净值曲线表（每日快照） ==========
class StrategyEquityCurve(Base):
    """策略净值曲线 - 每日快照，支撑资金曲线图和回撤曲线图"""
    __tablename__ = "strategy_equity_curve"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)

    # 策略维度
    strategy_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True, comment="策略名")
    auto_strategy_id: Mapped[int | None] = mapped_column(FKType, ForeignKey("auto_strategy.id"), nullable=True,
                                                         index=True)
    account_id: Mapped[int | None] = mapped_column(FKType, ForeignKey("execution_account.id"), nullable=True,
                                                   index=True)

    # 净值数据
    snapshot_date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True, comment="快照时间（每日）")
    equity: Mapped[float] = mapped_column(Float, nullable=False, comment="净值 USDC")
    balance: Mapped[float] = mapped_column(Float, nullable=False, comment="余额 USDC")
    daily_pnl: Mapped[float] = mapped_column(Float, default=0.0, comment="当日盈亏 USDC")
    daily_pnl_percent: Mapped[float] = mapped_column(Float, default=0.0, comment="当日盈亏 %")

    # 回撤数据
    peak_equity: Mapped[float] = mapped_column(Float, default=0.0, comment="历史峰值净值")
    drawdown: Mapped[float] = mapped_column(Float, default=0.0, comment="当前回撤 USDC")
    drawdown_percent: Mapped[float] = mapped_column(Float, default=0.0, comment="当前回撤率 %")
    max_drawdown_percent: Mapped[float] = mapped_column(Float, default=0.0, comment="历史最大回撤率 %")

    # 持仓数据
    position_count: Mapped[int] = mapped_column(Integer, default=0, comment="当日持仓数")
    trade_count: Mapped[int] = mapped_column(Integer, default=0, comment="当日交易数")

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_equity_strategy_date", "strategy_name", "snapshot_date"),
        Index("idx_equity_account_date", "account_id", "snapshot_date"),
    )

# (signal_provider_config 表已移除，信号源配置迁移到 .py 文件驱动架构)
