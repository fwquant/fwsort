# 福纹排行榜系统 - 开发计划B：榜单评分与排名展示

**开发者**：开发者B  
**职责**：负责绩效指标计算、榜单评分引擎、实时榜单刷新、前端展示  
**优先级**：高（核心展示层）  
**预计工时**：19小时  

---

## 一、任务范围与边界

### 1.1 职责边界（负责）

| 模块 | 子模块 | 功能说明 |
|------|--------|----------|
| **指标计算** | 收益指标 | 累计收益、年化收益、夏普比率、卡玛比率 |
| | 风险指标 | 最大回撤、波动率、连续亏损次数 |
| | 执行质量 | 执行率、滑点、延迟、撤单率 |
| **评分引擎** | 福纹综合分 | 加权评分计算、极值截断 |
| | 段位系统 | 青铜/白银/黄金/铂金/钻石 |
| **榜单管理** | Redis缓存 | ZSet实时榜单、分钟级刷新 |
| | 快照存储 | PostgreSQL历史快照 |
| | 定时任务 | Celery调度任务 |
| **前端展示** | 榜单列表 | 周期切换、筛选、排序 |
| | 策略详情 | 净值曲线、回撤曲线、执行质量 |
| | 响应式布局 | PC/移动端自适应、列表/卡片切换 |

### 1.2 边界外（不负责）

| 模块 | 负责方 | 说明 |
|------|--------|------|
| 智能体预测 | 开发者A | 投票决策、订单执行 |
| 跟单/租用业务 | 开发者C | 商业模式实现 |
| 后端API认证 | 公共 | JWT认证已实现 |

---

## 二、详细开发计划

### 2.1 任务分解与工时估算

| 序号 | 任务 | 子任务 | 工时 | 状态 |
|------|------|--------|------|------|
| B1 | 绩效指标计算 | 收益类指标 | 1.5h | ⏳ |
| B1 | | 风险类指标 | 1.5h | ⏳ |
| B2 | 综合评分引擎 | 福纹综合分计算 | 1h | ⏳ |
| B2 | | 段位判定逻辑 | 1h | ⏳ |
| B3 | Redis榜单 | ZSet存储结构设计 | 1.5h | ⏳ |
| B3 | | 实时榜单刷新逻辑 | 1.5h | ⏳ |
| B4 | 定时任务 | 分钟级刷新任务 | 1h | ⏳ |
| B4 | | 每日快照任务 | 1h | ⏳ |
| B5 | 榜单API | 列表接口完善 | 1.5h | ⏳ |
| B5 | | 详情接口完善 | 1.5h | ⏳ |
| B6 | 前端榜单页 | 列表组件 | 2h | ⏳ |
| B6 | | 详情组件 | 2h | ⏳ |
| B7 | 响应式布局 | PC/移动端适配 | 2h | ⏳ |
| **合计** | | | **19h** | |

### 2.2 关键交付物

| 交付物 | 文件路径 | 说明 |
|--------|----------|------|
| 指标计算器 | `core/indicator_calculator.py` | 绩效指标计算 |
| 评分引擎 | `core/ranking_engine.py` | 福纹综合分 |
| Redis客户端 | `core/redis_client.py` | 榜单缓存管理 |
| 调度器 | `core/scheduler.py` | 定时任务 |
| 榜单路由 | `router/ranking_router.py` | API接口 |
| 前端列表 | `web/static/js/ranking_list.js` | 榜单列表组件 |
| 前端详情 | `web/static/js/ranking_detail.js` | 策略详情组件 |
| 单元测试 | `tests/test_ranking_engine.py` | 评分引擎测试 |

---

## 三、配置与测试数据

### 3.1 榜单权重配置

```ini
# .env 文件配置
WEIGHT_ANNUALIZED=0.30      # 年化收益权重
WEIGHT_DRAWDOWN=0.20        # 回撤权重
WEIGHT_SHARPE=0.20          # 夏普比率权重
WEIGHT_PROFIT_LOSS=0.15     # 盈亏比权重
WEIGHT_EXECUTION=0.15       # 执行质量权重
```

### 3.2 Redis配置

```ini
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
USE_FAKE_REDIS=true  # 开发模式使用内存Redis
```

### 3.3 模拟数据生成规则

| 指标 | 范围 | 说明 |
|------|------|------|
| 年化收益 | -20% ~ 150% | 随机分布 |
| 最大回撤 | 2% ~ 35% | 随机分布 |
| 卡玛比率 | 0.5 ~ 4.0 | 随机分布 |
| 胜率 | 45% ~ 75% | 随机分布 |
| 交易笔数 | 120 ~ 1500 | 随机整数 |
| 执行质量分 | 0.6 ~ 0.95 | 随机分布 |

---

## 四、接口规范

### 4.1 榜单API接口

| API路径 | 方法 | 功能 | 所属文件 |
|---------|------|------|----------|
| `/api/ranking/list` | GET | 获取榜单列表（分页+筛选） | ranking_router.py |
| `/api/ranking/detail/{uid}` | GET | 获取策略详情 | ranking_router.py |
| `/api/ranking/history` | GET | 获取历史快照 | ranking_router.py |
| `/api/ranking/change/{uid}` | GET | 获取排名变动 | ranking_router.py |
| `/api/ranking/export` | GET | 导出CSV | ranking_router.py |

### 4.2 请求/响应示例

**list 请求**：
```json
{
  "rank_type": "realtime",
  "page": 1,
  "page_size": 20,
  "platform": "polymarket",
  "sort_by": "composite"
}
```

**list 响应**：
```json
{
  "success": true,
  "data": {
    "rank_type": "realtime",
    "items": [
      {
        "rank": 1,
        "uid": "ACC-0001",
        "name": "Alpha猎手",
        "composite_score": 89.5,
        "tier": "钻石",
        "annualized_return": 0.85,
        "max_drawdown": 0.12,
        "execution_score": 0.92
      }
    ],
    "total": 100,
    "page": 1,
    "page_size": 20
  }
}
```

---

## 五、数据库表依赖

### 5.1 核心表

| 表名 | 作用 | 状态 |
|------|------|------|
| `strategy_performance` | 绩效指标明细 | ✅ 已定义 |
| `rank_snapshot` | 榜单快照 | ✅ 已定义 |
| `execution_account` | 执行账户 | ✅ 已定义 |
| `weight_config` | 权重配置 | ✅ 已定义 |

---

## 六、协作要点

### 6.1 与开发者A的协作

| 协作点 | 说明 |
|--------|------|
| 执行日志 | OrderExecutionLog → 执行质量指标计算 |
| 账户数据 | ExecutionAccount → 绩效关联 |

### 6.2 与开发者C的协作

| 协作点 | 说明 |
|--------|------|
| 榜单数据 | 供跟单/租用模块展示策略排行 |
| 评分数据 | 作为租用定价参考 |

---

**文档版本**：v1.0  
**创建时间**：2026-07-27  
**开发者**：开发者B