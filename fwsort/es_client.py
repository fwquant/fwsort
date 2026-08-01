# Elasticsearch 客户端：订单执行日志检索（架构文档 9.2 性能优化）
from typing import Optional

from elasticsearch import AsyncElasticsearch
from elasticsearch.exceptions import ApiError
from elastic_transport import TransportError
from loguru import logger

from fwsort.config import settings


async_es: Optional[AsyncElasticsearch] = None
es_available: bool = False


def init_es_client() -> None:
    """初始化 ES 客户端（延迟初始化）"""
    global async_es
    try:
        async_es = AsyncElasticsearch(
            hosts=[settings.ES_HOST],
            request_timeout=2,
            max_retries=0,
            retry_on_timeout=False,
        )
    except Exception as e:
        logger.warning(f"ES client initialization failed: {e}")


def get_es_client() -> Optional[AsyncElasticsearch]:
    """获取 ES 客户端（延迟初始化，返回 AsyncElasticsearch 或 None）"""
    global async_es
    if async_es is None:
        init_es_client()
    return async_es


# ES 索引映射（订单执行日志）
ORDER_LOG_MAPPING = {
    "mappings": {
        "properties": {
            "id": {"type": "long"},
            "uid": {"type": "keyword"},
            "order_id": {"type": "keyword"},
            "order_type": {"type": "byte"},
            "side": {"type": "byte"},
            "symbol": {"type": "keyword"},
            "expected_price": {"type": "double"},
            "actual_price": {"type": "double"},
            "quantity": {"type": "double"},
            "status": {"type": "byte"},
            "latency_ms": {"type": "integer"},
            "slippage": {"type": "double"},
            "platform": {"type": "keyword"},
            "created_at": {"type": "date"},
        }
    },
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "refresh_interval": "5s",
    },
}


async def ensure_order_log_index() -> None:
    """启动时确保订单日志索引存在（优雅降级：ES不可用时继续启动）"""
    global es_available
    if async_es is None:
        init_es_client()

    if async_es is None:
        logger.warning("ES client not initialized, skipping index creation")
        return

    try:
        if not await async_es.indices.exists(index=settings.ES_INDEX_ORDER_LOG):
            await async_es.indices.create(
                index=settings.ES_INDEX_ORDER_LOG, body=ORDER_LOG_MAPPING
            )
            logger.info(f"Created ES index: {settings.ES_INDEX_ORDER_LOG}")
        es_available = True
        logger.info(f"ES connection successful: {settings.ES_HOST}")
    except Exception as e:  # noqa: BLE001
        # ES 8.x elastic_transport.ConnectionError / ApiError / TransportError 一律降级
        es_available = False
        logger.warning(f"ES connection failed: {type(e).__name__}: {e}. Order log features disabled.")


async def close_es_client() -> None:
    """关闭 ES 客户端连接"""
    if async_es is not None:
        try:
            await async_es.close()
            logger.info("ES client closed")
        except Exception as e:
            logger.warning(f"Error closing ES client: {e}")