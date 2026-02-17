from src.cache.decorators import cached
from src.cache.redis_client import close_redis, get_redis_client
from src.cache.service import (
    CACHE_TTL_ACCOUNT_INFO,
    CACHE_TTL_CAMPAIGNS,
    CACHE_TTL_METRICS,
    CACHE_TTL_RECOMMENDATIONS,
    build_cache_key,
    cache_delete,
    cache_delete_pattern,
    cache_get,
    cache_set,
)

__all__ = [
    "get_redis_client",
    "close_redis",
    "cache_get",
    "cache_set",
    "cache_delete",
    "cache_delete_pattern",
    "build_cache_key",
    "cached",
    "CACHE_TTL_CAMPAIGNS",
    "CACHE_TTL_METRICS",
    "CACHE_TTL_RECOMMENDATIONS",
    "CACHE_TTL_ACCOUNT_INFO",
]
