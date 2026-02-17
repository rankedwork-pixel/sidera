"""Cache service for Sidera.

Provides async get/set/invalidate operations with JSON serialization and TTL.
Cache keys are namespaced by user_id and data type for clean isolation.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from src.cache.redis_client import get_redis_client

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Default TTL constants (seconds)
# ---------------------------------------------------------------------------
CACHE_TTL_CAMPAIGNS = 3600  # 1 hour -- campaign structure changes rarely
CACHE_TTL_METRICS = 300  # 5 minutes -- metrics change throughout the day
CACHE_TTL_RECOMMENDATIONS = 1800  # 30 minutes
CACHE_TTL_ACCOUNT_INFO = 7200  # 2 hours
CACHE_TTL_BQ_GOALS = 3600  # 1 hour -- goals change weekly at most
CACHE_TTL_BQ_PACING = 600  # 10 minutes -- pacing updates throughout the day
CACHE_TTL_BQ_METRICS = 300  # 5 minutes -- same as platform metrics
CACHE_TTL_DRIVE_LIST = 300  # 5 minutes -- file listings change moderately
CACHE_TTL_DRIVE_METADATA = 600  # 10 minutes -- file metadata is fairly stable
CACHE_TTL_DRIVE_CONTENT = 300  # 5 minutes -- document content may be edited
CACHE_TTL_BRIEFING_RESULT = 7200  # 2 hours -- analysis result cache for deduplication


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_cache_key(user_id: str, data_type: str, **kwargs: Any) -> str:
    """Build a namespaced cache key.

    Produces deterministic, human-readable keys like::

        sidera:user_123:campaigns:google_ads:1234567890

    Args:
        user_id: The user / account owner identifier.
        data_type: A short label for the data category
            (e.g. ``"campaigns"``, ``"metrics"``).
        **kwargs: Additional segments appended in sorted-key order so the
            key is deterministic regardless of caller kwarg ordering.

    Returns:
        A colon-separated cache key string.
    """
    parts = ["sidera", user_id, data_type]
    for key in sorted(kwargs.keys()):
        value = kwargs[key]
        if value is not None:
            parts.append(str(value))
    return ":".join(parts)


async def cache_get(key: str) -> dict | None:
    """Retrieve a cached value by key.

    Returns ``None`` on cache miss **or** on any Redis error so the caller
    can fall through to the underlying data source.

    Args:
        key: The cache key to look up.

    Returns:
        Deserialized dict on hit, ``None`` on miss or error.
    """
    client = get_redis_client()
    if client is None:
        return None

    try:
        raw = await client.get(key)
        if raw is None:
            logger.debug("cache_miss", key=key)
            return None

        logger.debug("cache_hit", key=key)
        return json.loads(raw)
    except Exception:
        logger.exception("cache_get_error", key=key)
        return None


async def cache_set(key: str, value: dict, ttl_seconds: int = 300) -> bool:
    """Store a value in the cache with a TTL.

    Args:
        key: The cache key.
        value: A JSON-serializable dict to cache.
        ttl_seconds: Time-to-live in seconds (default 5 minutes).

    Returns:
        ``True`` if the value was stored successfully, ``False`` otherwise.
    """
    client = get_redis_client()
    if client is None:
        return False

    try:
        serialized = json.dumps(value, default=str)
        await client.set(key, serialized, ex=ttl_seconds)
        logger.debug("cache_set", key=key, ttl=ttl_seconds)
        return True
    except Exception:
        logger.exception("cache_set_error", key=key)
        return False


async def cache_delete(key: str) -> bool:
    """Delete a single cache key.

    Args:
        key: The cache key to delete.

    Returns:
        ``True`` if the key was deleted (or didn't exist), ``False`` on error.
    """
    client = get_redis_client()
    if client is None:
        return False

    try:
        await client.delete(key)
        logger.debug("cache_delete", key=key)
        return True
    except Exception:
        logger.exception("cache_delete_error", key=key)
        return False


async def cache_delete_pattern(pattern: str) -> int:
    """Delete all keys matching a glob pattern.

    Uses ``SCAN`` (not ``KEYS``) to avoid blocking Redis on large key spaces.

    Args:
        pattern: A Redis glob pattern (e.g. ``"sidera:user_123:*"``).

    Returns:
        Number of keys deleted, or ``0`` on error.
    """
    client = get_redis_client()
    if client is None:
        return 0

    try:
        deleted = 0
        async for key in client.scan_iter(match=pattern, count=100):
            await client.delete(key)
            deleted += 1

        logger.debug("cache_delete_pattern", pattern=pattern, deleted=deleted)
        return deleted
    except Exception:
        logger.exception("cache_delete_pattern_error", pattern=pattern)
        return 0
