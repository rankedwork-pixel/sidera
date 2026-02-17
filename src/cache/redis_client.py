"""Redis client singleton for Sidera.

Uses redis-py async client. Falls back gracefully when redis_url is not configured.
Redis is NEVER the sole source of truth -- it's a cache layer for API response caching
and session state. The database is always authoritative.
"""

from __future__ import annotations

import redis.asyncio as aioredis
import structlog

from src.config import settings

logger = structlog.get_logger(__name__)

# Module-level singleton -- lazily initialized
_redis_client: aioredis.Redis | None = None
_initialized: bool = False


def get_redis_client() -> aioredis.Redis | None:
    """Return the shared async Redis client, creating it on first call.

    If ``settings.redis_url`` is empty, logs a warning and returns ``None``.
    Callers must handle the ``None`` case (all cache operations become no-ops).

    Returns:
        ``redis.asyncio.Redis`` instance, or ``None`` when Redis is not configured.
    """
    global _redis_client, _initialized  # noqa: PLW0603

    if _initialized:
        return _redis_client

    _initialized = True

    if not settings.redis_url:
        logger.warning(
            "redis_not_configured",
            hint="Set REDIS_URL in .env to enable caching. "
            "The app will function without caching but API calls won't be cached.",
        )
        _redis_client = None
        return None

    try:
        _redis_client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
        )
        logger.info("redis_client_created", url=_mask_url(settings.redis_url))
        return _redis_client
    except Exception:
        logger.exception("redis_client_creation_failed")
        _redis_client = None
        return None


async def close_redis() -> None:
    """Close the Redis connection pool during application shutdown.

    Safe to call even if Redis was never initialized or is already closed.
    """
    global _redis_client, _initialized  # noqa: PLW0603

    if _redis_client is not None:
        try:
            await _redis_client.aclose()
            logger.info("redis_connection_closed")
        except Exception:
            logger.exception("redis_close_error")
        finally:
            _redis_client = None
            _initialized = False


def reset_redis_client() -> None:
    """Reset the singleton state. Intended for testing only."""
    global _redis_client, _initialized  # noqa: PLW0603
    _redis_client = None
    _initialized = False


def _mask_url(url: str) -> str:
    """Mask the password in a Redis URL for safe logging.

    Example:
        ``redis://:secretpass@host:6379/0`` -> ``redis://:***@host:6379/0``
    """
    if "@" in url:
        # Mask everything between :// and @
        prefix_end = url.find("://")
        at_pos = url.find("@")
        if prefix_end != -1 and at_pos != -1:
            return url[: prefix_end + 3] + "***@" + url[at_pos + 1 :]
    return url
