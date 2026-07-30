# 订单日志子包：ES 异步写入 + Outbox 模式
# 职责：
#   - es_writer.py → 写完 PostgreSQL 后异步落 ES（无 ES 时降级）
#   - outbox.py    → Outbox 模式（事务内入队，Celery 30s 消费）
# 注：本包不属于 gateway（网关），而是订单执行后的可观测性/事件分发层
