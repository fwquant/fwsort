# 福纹排行榜系统 - 开发计划C：跟单交易与智能体租用

**开发者**：开发者C  
**职责**：负责用户系统、跟单交易、智能体租用、支付结算、通知系统  
**优先级**：中（商业模式层）  
**预计工时**：23小时  

---

## 一、任务范围与边界

### 1.1 职责边界（负责）

| 模块 | 子模块 | 功能说明 |
|------|--------|----------|
| **用户系统** | 用户注册 | 邮箱+密码+JWT认证 |
| | 权限管理 | 角色权限控制 |
| | 用户资料 | 个人信息管理 |
| **跟单模块** | 订阅管理 | 订阅leader策略 |
| | 收益分成 | 盈利抽成计算 |
| | 跟单执行 | 自动跟单下单 |
| **租用模块** | 智能体市场 | 可租用智能体列表 |
| | 租用合约 | 按次/包时段 |
| | 计费结算 | 费用计算与扣除 |
| **支付集成** | 订单支付 | USD支付处理 |
| | 分成结算 | leader收益结算 |
| **通知系统** | 消息推送 | 收益/到期/风控通知 |
| | 消息中心 | 用户消息管理 |

### 1.2 边界外（不负责）

| 模块 | 负责方 | 说明 |
|------|--------|------|
| 智能体预测 | 开发者A | 预测引擎、投票决策 |
| 榜单评分 | 开发者B | 绩效计算、排名展示 |
| 数据库模型 | 公共 | 已由架构定义 |

---

## 二、详细开发计划

### 2.1 任务分解与工时估算

| 序号 | 任务 | 子任务 | 工时 | 状态 |
|------|------|--------|------|------|
| C1 | 用户系统 | 用户注册登录 | 1.5h | ⏳ |
| C1 | | JWT认证中间件 | 1.5h | ⏳ |
| C2 | 跟单模块 | 订阅创建/取消 | 2h | ⏳ |
| C2 | | 收益分成计算 | 2h | ⏳ |
| C3 | 租用模块 | 智能体品类管理 | 2h | ⏳ |
| C3 | | 租用合约创建 | 2h | ⏳ |
| C4 | 支付集成 | 支付订单处理 | 2h | ⏳ |
| C4 | | 分成结算逻辑 | 1h | ⏳ |
| C5 | API接口 | 跟单路由完善 | 1.5h | ⏳ |
| C5 | | 租用路由完善 | 1.5h | ⏳ |
| C6 | 前端页面 | 跟单列表页 | 2h | ⏳ |
| C6 | | 租用市场页 | 2h | ⏳ |
| C7 | 通知系统 | 通知模型 | 1h | ⏳ |
| C7 | | 通知推送逻辑 | 1h | ⏳ |
| **合计** | | | **23h** | |

### 2.2 关键交付物

| 交付物 | 文件路径 | 说明 |
|--------|----------|------|
| 用户认证路由 | `router/auth_router.py` | 注册/登录/刷新 |
| 跟单路由 | `router/follow_router.py` | 跟单订阅接口 |
| 租用路由 | `router/rental_router.py` | 租用合约接口 |
| 通知路由 | `router/notification_router.py` | 消息通知接口 |
| 前端跟单 | `web/static/js/follow.js` | 跟单页面组件 |
| 前端租用 | `web/static/js/rental.js` | 租用市场组件 |
| 前端账户 | `web/static/js/accounts.js` | 用户账户组件 |

---

## 三、配置与测试数据

### 3.1 跟单配置

```ini
# .env 文件配置
FOLLOW_DEFAULT_FEE_USD=9.9       # 默认订阅费/月
FOLLOW_DEFAULT_SHARE_RATIO=0.20  # 默认分成比例20%
FOLLOW_MIN_AMOUNT_USD=10.0       # 最小跟单金额
```

### 3.2 租用配置

```ini
# 智能体租用定价
RENTAL_PRICE_PER_CALL_USD=0.10   # 按次调用价格
RENTAL_PRICE_PER_HOUR_USD=0.50   # 包时段每小时价格
RENTAL_MAX_CONCURRENT=10         # 最大并发数
```

### 3.3 模拟智能体数据

| 智能体名称 | 模型 | 类型 | 单次价格(USD) | 小时价格(USD) |
|------------|------|------|--------------|--------------|
| TrendGPT | gpt-4o | 趋势预测 | 0.10 | 0.50 |
| SentimentClaude | claude-3-5 | 情绪分析 | 0.12 | 0.60 |
| ChainGemini | gemini-2.0 | 链上分析 | 0.08 | 0.40 |

---

## 四、接口规范

### 4.1 跟单API接口

| API路径 | 方法 | 功能 | 所属文件 |
|---------|------|------|----------|
| `/api/follow/subscribe` | POST | 订阅跟单 | follow_router.py |
| `/api/follow/unsubscribe` | POST | 取消订阅 | follow_router.py |
| `/api/follow/list` | GET | 获取订阅列表 | follow_router.py |
| `/api/follow/orders` | GET | 获取跟单订单 | follow_router.py |

### 4.2 租用API接口

| API路径 | 方法 | 功能 | 所属文件 |
|---------|------|------|----------|
| `/api/rental/agents` | GET | 获取可租用智能体列表 | rental_router.py |
| `/api/rental/order` | POST | 创建租用订单 | rental_router.py |
| `/api/rental/my-orders` | GET | 获取我的租用订单 | rental_router.py |
| `/api/rental/call` | POST | 调用租用智能体 | rental_router.py |

### 4.3 请求/响应示例

**subscribe 请求**：
```json
{
  "leader_uid": "ACC-0001",
  "mode": 3,
  "follow_amount_usd": 50.0
}
```

**subscribe 响应**：
```json
{
  "success": true,
  "message": "subscription created",
  "data": {
    "id": 1,
    "leader_uid": "ACC-0001",
    "leader_name": "Alpha猎手",
    "mode": 3,
    "profit_share_ratio": 0.2,
    "follow_amount_usd": 50.0,
    "expires_at": "2026-08-27T00:00:00Z"
  }
}
```

---

## 五、数据库表依赖

### 5.1 核心表

| 表名 | 作用 | 状态 |
|------|------|------|
| `user` | 用户表 | ✅ 已定义 |
| `follow_subscription` | 跟单订阅 | ✅ 已定义 |
| `follow_order` | 跟单订单 | ✅ 已定义 |
| `rental_agent` | 可租用智能体 | ✅ 已定义 |
| `rental_order` | 租用合约 | ✅ 已定义 |
| `notification` | 通知消息 | ✅ 已定义 |

---

## 六、协作要点

### 6.1 与开发者A的协作

| 协作点 | 说明 |
|--------|------|
| 智能体调用 | 租用模块调用预测API |
| 订单同步 | 跟单订单与执行订单关联 |

### 6.2 与开发者B的协作

| 协作点 | 说明 |
|--------|------|
| 榜单数据 | 获取leader排行供用户选择 |
| 绩效数据 | 展示leader历史绩效 |

---

**文档版本**：v1.0  
**创建时间**：2026-07-27  
**开发者**：开发者C