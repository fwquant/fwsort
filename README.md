# 福纹排行榜（FWQuant Ranking System） · V1.0

> 多智能体策略-订单执行模块（V1.0）+ 福纹排行榜完整系统
> 3 个 AI 智能体（GPT-4o / Claude 3.5+ / Gemini 2.0+）→ Hermes MoA 聚合 → 投票引擎 → 模拟下单
> 多周期榜单 + 跟单订阅 + 智能体租用 + 数据归档

---

## ✨ 核心特性

### 1. V1.0 多智能体策略（严格规则）

| 情况 | 智能体方向 | 下单金额 | 原因码 |
|---|---|---|---|
| 3 智能体同方向 | 看涨×3 / 看跌×3 | **$10** | `double_10` |
| 2:1 多数 | 2 看涨 1 看跌 / 2 看跌 1 看涨 | **$5** | `base_5_majority_up/down` |
| 无共识 | 3 方向各不同 | 不下单 | `no_consensus` |
| 风控冻结 | 日亏 ≥ 30% 初始余额 | 强停 | `risk_freeze` |
| 单笔上限 | 单笔 ≤ 余额 20% | 自动截断 | `*_capped_by_risk` |

### 2. 架构分层

- **后端**：Python 3.10+ / FastAPI 0.139 / SQLAlchemy 2.0（asyncpg+psycopg2）
- **前端**：原生 HTML + ES6 模块化 JS（FWUI 组件库），可平滑迁移 Vue 3
- **数据库**：PostgreSQL 15（热存）+ Redis 7（榜单 ZSet）+ Elasticsearch 8（订单执行日志 + 冷存）
- **任务**：Celery 5 + Redis Broker（每分钟实时榜 / 每日快照 / 每 5 分钟跟单同步 / 每日 03:30 归档）
- **智能体**：OpenAI / Anthropic / Google GenAI 官方 SDK；无 key 时降级 MOCK
- **下单**：Polymarket 二元合约 + OKX 现货定价模型（统一 OrderSimulator）

### 3. 福纹排行榜

- 5 个周期：实时 / 日 / 周 / 月 / 总
- 段位系统：青铜 < 20 < 白银 < 40 < 黄金 < 60 < 铂金 < 80 ≤ 钻石
- 综合分 = 0.30×年化 + 0.20×(1-回撤) + 0.20×夏普 + 0.15×盈亏比 + 0.15×执行质量
- 执行质量：成单率 + 滑点 + 延迟 + 撤单率
- 移动端默认卡片，PC 端默认列表（锁定区第 2 条）

### 4. 跟单订阅（双轨计费）

- **模式 1** 纯订阅费 $9.9/月
- **模式 2** 纯利润分成（盈利抽 20%）
- **模式 3** 订阅 + 分成（推荐）
- 每 5 分钟由 Celery 任务自动跟单（复用 leader 最近一笔订单）

### 5. 智能体租用（双轨计费）

- **按次试算**：$0.08 ~ $0.30/次（不拥有）
- **包时段独占**：$0.40 ~ $1.50/小时，1 天享 20h，7 天享 120h（省 14%）

### 6. 数据归档

- 订单日志 90 天热→冷（每日 03:30 Celery 任务搬运到 ES）

---

## 📁 项目结构

```
fwsort/
├── main.py                    # FastAPI 入口
├── requirements.txt
├── docker-compose.yml         # 一键起 PG/Redis/ES/FastAPI/Celery
├── Dockerfile
├── .env.example
├── core/
│   ├── config.py              # 配置（pydantic-settings）
│   ├── database.py            # 异步 + 同步 引擎
│   ├── models.py              # 15 张表
│   ├── schemas.py             # Pydantic 校验
│   ├── security.py            # JWT + bcrypt
│   ├── voting.py              # V1.0 投票引擎
│   ├── ranking_engine.py      # 福纹综合分
│   ├── indicator_calculator.py# 年化/夏普/卡玛/回撤
│   ├── redis_client.py        # ZSet 榜单
│   ├── es_client.py           # 订单日志 + 归档
│   ├── scheduler.py           # Celery 6 任务
│   ├── agents/                # 3 智能体 + Hermes MoA
│   └── execution/simulator.py # 统一下单
├── router/
│   ├── auth_router.py         # 注册/登录/Token
│   ├── ranking_router.py      # 榜单列表/详情/历史/变动/导出
│   ├── agent_router.py        # V1.0 预测+投票+下单
│   ├── follow_router.py       # 跟单订阅
│   ├── rental_router.py       # 智能体租用
│   ├── notification_router.py # 通知中心
│   ├── config_router.py       # 权重配置
│   ├── compare_router.py      # 策略对比
│   └── admin_router.py        # 初始化/播种/任务触发
└── web/
    ├── templates/             # HTML 页面
    │   ├── index.html         # 榜单
    │   ├── detail.html        # 策略详情（+4 图表）
    │   ├── accounts.html      # 我的执行账户
    │   ├── follow.html        # 跟单
    │   ├── rental.html        # 智能体租用
    │   └── admin.html         # 控制台
    └── static/
        ├── css/               # FWUI + 主题
        └── js/                # FWUI 组件库 + 业务
```

---

## 🚀 快速启动

### 方式 A：Docker Compose（一键，推荐）

```bash
# 1. 复制环境变量
cp .env.example .env
# 编辑 .env 填入 OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY

# 2. 一键启动（PostgreSQL + Redis + Elasticsearch + FastAPI + Celery）
docker compose up -d

# 3. 初始化数据库 + 管理员 + MOCK 数据
docker compose exec api python -c "from main import app; from core.database import init_db; init_db()"
docker compose exec api curl -X POST http://localhost:8000/api/admin/seed-admin
docker compose exec api curl -X POST http://localhost:8000/api/admin/seed-mock?n_accounts=20\&n_votes=50
docker compose exec api curl -X POST http://localhost:8000/api/admin/seed-rental-agents

# 4. 浏览器打开
open http://localhost:8000
```

### 方式 B：本地开发

```bash
# 1. 准备依赖服务（任选一种）
#    a) Docker 只起 PG/Redis/ES
docker compose up -d postgres redis elasticsearch

#    b) 或本机安装 PostgreSQL 15 / Redis 7 / Elasticsearch 8

# 2. 虚拟环境
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate    # Linux/macOS
pip install -r requirements.txt

# 3. 复制并填 .env
cp .env.example .env

# 4. 初始化
python -c "from core.database import init_db; init_db()"

# 5. 启动 FastAPI
uvicorn main:app --reload

# 6. 启动 Celery（新窗口）
celery -A fwsort.scheduler.celery_app worker --loglevel=info -B

# 7. 播种（另一窗口）
curl -X POST http://localhost:8000/api/admin/seed-admin
curl -X POST http://localhost:8000/api/admin/seed-mock?n_accounts=20&n_votes=50
curl -X POST http://localhost:8000/api/admin/seed-rental-agents

# 8. 浏览器
open http://localhost:8000
```

---

## 🔑 默认账户

| 角色 | 邮箱 | 密码 | 说明 |
|---|---|---|---|
| 管理员 | `admin@fwquant.com` | `admin123456` | role=3，可访问 `/admin` |
| 普通用户 | （注册获得） | 自定 | role=0 |

---

## 🧠 V1.0 投票规则 - 完整闭环

```text
# 3 智能体预测（Hermes MoA 聚合）
moa_result = await hermes.aggregate("BTCUSDT", "15m")
# → layer1_results: [AgentPrediction(direction, confidence, reasoning, ...), x3]

# 投票
directions = [p.direction for p in moa_result.layer1_results]  # [1, 1, 2]
result = vote(directions, account_balance, daily_pnl, initial_balance)
# → up=2, down=1, flat=0, final=1, amount=5.0, reason="base_5_majority_up"

# 模拟下单
if result.final_direction != 0 and result.order_amount_usd > 0:
    sim = simulator.submit(platform, symbol, side, amount_usd)
    # → OrderSimulatorResult(order_id, actual_price, slippage, latency_ms, ...)
```

---

## 🛣️ API 速查

### 认证

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/auth/register` | 注册 |
| POST | `/api/auth/login` | 登录（返回 JWT） |
| GET | `/api/auth/me` | 当前用户 |
| POST | `/api/auth/refresh` | 刷新 token |

### 智能体（V1.0 核心）

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/agent/accounts` | 我的执行账户 |
| POST | `/api/agent/accounts?name=&platform=&initial_balance=` | 创建执行账户 |
| POST | `/api/agent/predict-and-vote?account_id=` | **V1.0 核心：3 智能体预测+投票+下单** |
| GET | `/api/agent/execution/{uid}` | 订单执行日志 |

### 榜单

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/ranking/list?rank_type=&page=&sort_by=` | 榜单 |
| GET | `/api/ranking/detail/{uid}` | 详情 |
| GET | `/api/ranking/history` | 历史 |
| GET | `/api/ranking/change/{uid}` | 排名变动 |
| GET | `/api/ranking/export?rank_type=` | 导出 CSV |

### 跟单

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/follow/market` | 跟单市场（Top20） |
| POST | `/api/follow/subscribe?leader_uid=&mode=&amount=&months=` | 订阅 |
| GET | `/api/follow/my` | 我的订阅 |
| DELETE | `/api/follow/{id}` | 取消 |
| GET | `/api/follow/orders/{sub_id}` | 跟单成交 |

### 智能体租用

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/rental/agents` | 智能体清单 |
| POST | `/api/rental/call?agent_id=&symbol=&timeframe=` | 按次调用 |
| POST | `/api/rental/rent?agent_id=&hours=` | 包时段 |
| GET | `/api/rental/my` | 我的租用 |
| POST | `/api/rental/{id}/cancel` | 取消包时段 |

### 通知

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/notify/list?only_unread=` | 我的通知 |
| POST | `/api/notify/{id}/read` | 标已读 |
| POST | `/api/notify/read-all` | 全部已读 |

### 管理

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/admin/init-db` | 初始化表 |
| POST | `/api/admin/seed-admin` | 播种管理员 |
| POST | `/api/admin/seed-mock?n_accounts=20&n_votes=50` | 播种 MOCK 账户+投票+订单 |
| POST | `/api/admin/seed-rental-agents` | 播种租用品类 |
| POST | `/api/admin/trigger/{task_name}` | 手动触发 Celery（`refresh_realtime_rank` / `daily_snapshot` / `daily_cleanup` / `archive_hot_to_cold`） |

---

## 🖼️ 页面导航

| 路径 | 用途 |
|---|---|
| `/` | 榜单首页（实时/日/周/月/总 + 平台筛选 + 列表/卡片切换） |
| `/detail?uid=...` | 策略详情（净值/回撤/投票分布/执行质量 4 图表 + 触发投票 + 订单日志） |
| `/accounts` | 我的执行账户（创建/删除/触发投票） |
| `/follow` | 跟单管理（市场 + 我的订阅） |
| `/rental` | 智能体租用（按次 + 包时段 + 我的租用） |
| `/admin` | 控制台（初始化 + 播种 + 任务触发） |

---

## ⏰ Celery 定时任务

| 任务 | 周期 | 说明 |
|---|---|---|
| `refresh_realtime_rank` | 每分钟 | 实时榜 ZSet 刷新 |
| `daily_snapshot` | 每日 00:05 | 日榜快照固化 |
| `daily_cleanup` | 每日 03:00 | 临时缓存清理 |
| `archive_hot_to_cold` | 每日 03:30 | 订单日志 90 天热→冷 |
| `follow_auto_copy` | 每 5 分钟 | 跟单自动同步 |
| `notify_scan` | 每 10 分钟 | 风控冻结/订阅到期 通知扫描 |

---

## 🎨 FWUI 设计规范（锁定区第 2/5/10 条）

- **双主题**：暗紫（默认）/ 明亮（顶部一键切换），统一管理于 `theme_dark.css` / `theme_light.css`
- **响应式**：PC 默认列表，移动端（< 768px）自动卡片，支持手动切换
- **组件**：toast 提示 / modal 弹框 / tabs / tag / progress / card / table / input / select / pagination
- **集成管理**：`web/static/css/style.css` 是唯一 @import 入口，每页 CSS 子模块独立管理
- **段位徽章**：5 色（青铜 / 白银 / 黄金 / 铂金 / 钻石）

---

## 🧪 测试触发 V1.0 闭环

```bash
# 登录拿 token
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@fwquant.com","password":"admin123456"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['data']['access_token'])")

# 创建执行账户
ACC_ID=$(curl -s -X POST "http://localhost:8000/api/agent/accounts?name=test&platform=polymarket&initial_balance=1000" \
  -H "Authorization: Bearer $TOKEN" \
  | python -c "import sys,json; print(json.load(sys.stdin)['data']['id'])")

# 触发 V1.0 完整闭环（3 智能体预测 + 投票 + 模拟下单）
curl -X POST "http://localhost:8000/api/agent/predict-and-vote?account_id=$ACC_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTCUSDT","timeframe":"15m"}' | python -m json.tool
```

预期响应（无 API key 时降级 MOCK）：

```text
{
  "success": true,
  "data": {
    "vote_id": 123,
    "up_count": 2, "down_count": 1, "flat_count": 0,
    "final_direction": 1,
    "order_amount_usd": 5.0,
    "reason": "base_5_majority_up",
    "predictions": [
      { "agent_name": "GPT-4o", "direction": 1, "confidence": 0.78, ... },
      { "agent_name": "Claude 3.5", "direction": 1, "confidence": 0.81, ... },
      { "agent_name": "Gemini 2.0", "direction": 2, "confidence": 0.55, ... }
    ],
    "order_id": "SIM-20260724-...",
    "order_status": 3
  }
}
```

---

## 📜 文档

- 多智能体策略-订单执行规则V1.0：`docs/多智能体策略-订单执行规则V1.0.docx`
- 架构设计：`docs/福纹排行榜系统架构设计.md`
- 开发日志：`docs/开发日志_20260724.md`

---

## 🔒 安全 > 稳定 > 性能 > 功能 > 界面

- **JWT 鉴权**：access 30min + refresh 7day；401 自动清 token
- **参数校验**：所有路由入参走 Pydantic v2 + `core.exceptions` 业务异常
- **风控硬上限**：单笔 ≤ 余额 20%，日亏 ≥ 30% 强停
- **降级 MOCK**：无 AI key 时智能体返回 MOCK 预测，保证流程可跑通
- **CORS**：开发环境全开，生产请收敛到具体域名
- **日志**：loguru 统一格式 + 拦截 stdlib logging
- **健康检查**：`/health` 端点供 Docker/K8s 探针

---

## 📄 License

MIT
