# 福纹排行榜系统 - 开发计划A：多智能体预测交易闭环

**开发者**：开发者A  
**职责**：负责智能体预测引擎、投票决策系统、订单执行器的完整实现  
**优先级**：最高（核心闭环）  
**预计工时**：18小时  

---

## 一、任务范围与边界

### 1.1 职责边界（负责）

| 模块 | 子模块 | 功能说明 |
|------|--------|----------|
| **智能体层** | 多AI智能体集成 | Claude/Gemini/OpenAI三大模型接入 |
| | Hermes MoA聚合 | 多层级智能体结果聚合 |
| | 预测结果标准化 | 统一输出格式与置信度计算 |
| **投票引擎** | 投票规则执行 | V1.0规则：2:1多数/全同加倍 |
| | 风控规则 | 日亏30%强停、单笔20%限额 |
| | 决策记录 | 投票结果落库与追溯 |
| **订单执行** | 模拟执行器 | Polymarket/OKX双平台模拟 |
| | 实盘接口 | OKX真实API接入（DEMO模式） |
| | 执行日志 | 订单执行明细记录 |

### 1.2 边界外（不负责）

| 模块 | 负责方 | 说明 |
|------|--------|------|
| 榜单评分引擎 | 开发者B | 绩效指标计算、综合评分 |
| 前端展示 | 开发者B/C | UI页面、数据可视化 |
| 跟单/租用业务 | 开发者C | 商业模式实现 |
| 数据库模型 | 公共 | 已由架构定义 |

---

## 二、详细开发计划

### 2.1 任务分解与工时估算

| 序号 | 任务 | 子任务 | 工时 | 状态 |
|------|------|--------|------|------|
| A1 | 智能体扩展 | 完善Claude智能体实现 | 2h | ⏳ |
| A1 | | 完善Gemini智能体实现 | 2h | ⏳ |
| A1 | | 完善OpenAI智能体实现 | 2h | ⏳ |
| A2 | 投票引擎 | 完善V1.0投票规则 | 1h | ⏳ |
| A2 | | 添加工单测试用例 | 1h | ⏳ |
| A3 | 风控模块 | 日亏强停逻辑 | 1h | ⏳ |
| A3 | | 单笔限额逻辑 | 1h | ⏳ |
| A3 | | 账户冻结状态管理 | 1h | ⏳ |
| A4 | 订单执行器 | OKX DEMO API接入 | 3h | ⏳ |
| A4 | | 订单状态同步 | 1h | ⏳ |
| A5 | Polymarket接入 | 钱包签名实现 | 2h | ⏳ |
| A5 | | 订单提交接口 | 2h | ⏳ |
| A6 | 执行日志 | 日志模型完善 | 1h | ⏳ |
| A6 | | ES索引配置 | 1h | ⏳ |
| **合计** | | | **18h** | |

### 2.2 关键交付物

| 交付物 | 文件路径 | 说明 |
|--------|----------|------|
| 智能体基类 | `core/agents/base.py` | 统一接口定义 |
| Claude智能体 | `core/agents/claude_agent.py` | Anthropic Claude接入 |
| Gemini智能体 | `core/agents/gemini_agent.py` | Google Gemini接入 |
| OpenAI智能体 | `core/agents/openai_agent.py` | OpenAI GPT接入 |
| Hermes MoA | `core/agents/hermes_moa.py` | 多智能体聚合 |
| 投票引擎 | `core/voting.py` | V1.0投票规则 |
| 订单执行器 | `core/execution/simulator.py` | 模拟下单 |
| 执行日志模型 | `core/models.py` | OrderExecutionLog |
| 智能体路由 | `router/agent_router.py` | 核心API接口 |
| 单元测试 | `tests/test_voting.py` | 投票引擎测试 |

---

## 三、配置与测试数据

### 3.1 AI模型密钥配置（测试数据）

```ini
# .env 文件配置位置
OPENAI_API_KEY=sk-test-openai-key-placeholder-2026
OPENAI_MODEL=gpt-4o

ANTHROPIC_API_KEY=sk-ant-api-test-key-placeholder-2026
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022

GEMINI_API_KEY=AIzaSy-test-gemini-key-placeholder-2026
GEMINI_MODEL=gemini-2.0-flash

HERMES_MOA_ENABLED=true
HERMES_MOA_LAYERS=2
```

### 3.2 交易平台配置（已配置）

```ini
# OKX DEMO环境
OKX_API_KEY=6bfa621f-5b81-4df7-9b1a-39afcd4374dd
OKX_SECRET=0EF835B5730E84F62CAF33953342B19E
OKX_PASSPHRASE=Fuwen@2026
OKX_SERVER=DEMO

# Polymarket测试网
POLYMARKET_WALLET_ADDRESS=0xfb1a71fFfcfd268030f4DAbf3Db188C10B6a8c75
POLYMARKET_WALLET_PRIVATE_KEY=1a6b64f5d7ba90f3c04f8ec179ceda617215cfb3060f89d319c13602b9c9e9c5
POLYMARKET_CHAIN=goerli
```

### 3.3 投票规则配置

```ini
# 下单金额
ORDER_BASE_USD=5.0          # 基础订单（2:1多数）
ORDER_DOUBLE_USD=10.0       # 加倍订单（3智能体一致）

# 风控参数
RISK_DAILY_LOSS_RATIO=0.3   # 日亏30%强停
RISK_SINGLE_RATIO=0.2       # 单笔20%限额
```

---

## 四、接口规范

### 4.1 核心API接口

| API路径 | 方法 | 功能 | 所属文件 |
|---------|------|------|----------|
| `/api/agent/predict-and-vote` | POST | 预测+投票+下单闭环 | agent_router.py |
| `/api/agent/accounts` | GET | 获取执行账户列表 | agent_router.py |
| `/api/agent/accounts` | POST | 创建执行账户 | agent_router.py |
| `/api/agent/accounts/{id}` | DELETE | 删除执行账户 | agent_router.py |
| `/api/agent/execution/{uid}` | GET | 查询执行日志 | agent_router.py |

### 4.2 请求/响应示例

**predict-and-vote 请求**：
```json
{
  "symbol": "BTCUSDT",
  "timeframe": "15m"
}
```

**predict-and-vote 响应**：
```TEXT
{
  "success": true,
  "message": "vote complete",
  "data": {
    "vote_id": 1,
    "up_count": 2,
    "down_count": 1,
    "flat_count": 0,
    "final_direction": 1,
    "order_amount_usd": 5.0,
    "reason": "base_5_majority_up",
    "predictions": [...],
    "order_id": "ORD-xxx",
    "order_status": 3
  }
}
```

---

## 五、数据库表依赖

### 5.1 核心表

| 表名 | 作用 | 状态 |
|------|------|------|
| `agent_prediction` | 智能体预测记录 | ✅ 已定义 |
| `vote_decision` | 投票决策记录 | ✅ 已定义 |
| `order_execution_log` | 订单执行日志 | ✅ 已定义 |
| `execution_account` | 执行账户 | ✅ 已定义 |

---

## 六、协作要点

### 6.1 与开发者B的协作

| 协作点 | 说明 |
|--------|------|
| 绩效数据 | 订单执行日志 → StrategyPerformance |
| 执行质量分 | execution_score字段传递 |

### 6.2 与开发者C的协作

| 协作点 | 说明 |
|--------|------|
| 跟单触发 | 投票决策 → 跟单订单 |
| 租用调用 | 智能体预测API供租用模块调用 |

---

**文档版本**：v1.0  
**创建时间**：2026-07-27  
**开发者**：开发者A