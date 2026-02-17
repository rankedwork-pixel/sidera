"""Rate limiting middleware for Sidera API.

Supports two backends:
- **In-memory** (``RateLimiter``) — token-bucket, suitable for single-instance.
- **Redis** (``RedisRateLimiter``) — fixed-window counter via ``INCR`` + ``EXPIRE``,
  suitable for multi-instance deployment.

Use ``create_rate_limiter()`` to get the best available backend.
``RateLimitMiddleware`` auto-detects which backend it wraps and calls
``await`` only when needed (Redis is async, in-memory is sync).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = structlog.get_logger(__name__)


# =====================================================================
# In-memory token-bucket limiter (original)
# =====================================================================


@dataclass
class _TokenBucket:
    """A single token bucket for one client."""

    capacity: float
    refill_rate: float  # tokens per second
    tokens: float = 0.0
    last_refill: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self.tokens = self.capacity

    def consume(self) -> bool:
        """Try to consume one token. Returns True if allowed."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    def remaining(self) -> float:
        """Return current token count after refill."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        return min(self.capacity, self.tokens + elapsed * self.refill_rate)


class RateLimiter:
    """In-memory token-bucket rate limiter.

    Maintains separate per-minute and per-hour buckets for each client ID.
    Thread-safe via a threading lock.

    Parameters
    ----------
    requests_per_minute:
        Maximum burst of requests allowed per minute per client.
    requests_per_hour:
        Maximum sustained requests allowed per hour per client.
    """

    def __init__(
        self,
        requests_per_minute: int = 60,
        requests_per_hour: int = 1000,
    ) -> None:
        self.requests_per_minute = requests_per_minute
        self.requests_per_hour = requests_per_hour
        self._lock = threading.Lock()
        self._minute_buckets: dict[str, _TokenBucket] = {}
        self._hour_buckets: dict[str, _TokenBucket] = {}

    def _get_buckets(self, client_id: str) -> tuple[_TokenBucket, _TokenBucket]:
        """Return (minute_bucket, hour_bucket) for the given client, creating if needed."""
        if client_id not in self._minute_buckets:
            self._minute_buckets[client_id] = _TokenBucket(
                capacity=float(self.requests_per_minute),
                refill_rate=self.requests_per_minute / 60.0,
            )
        if client_id not in self._hour_buckets:
            self._hour_buckets[client_id] = _TokenBucket(
                capacity=float(self.requests_per_hour),
                refill_rate=self.requests_per_hour / 3600.0,
            )
        return self._minute_buckets[client_id], self._hour_buckets[client_id]

    def is_allowed(self, client_id: str) -> bool:
        """Check whether a request from *client_id* is allowed.

        Consumes one token from both the per-minute and per-hour buckets.
        Returns ``False`` if either bucket is exhausted.
        """
        with self._lock:
            minute_bucket, hour_bucket = self._get_buckets(client_id)
            minute_ok = minute_bucket.consume()
            hour_ok = hour_bucket.consume()
            return minute_ok and hour_ok

    def get_remaining(self, client_id: str) -> dict:
        """Return remaining request allowances for *client_id*.

        Returns a dict with ``remaining_minute``, ``limit_minute``,
        ``remaining_hour``, and ``limit_hour`` keys.
        """
        with self._lock:
            minute_bucket, hour_bucket = self._get_buckets(client_id)
            return {
                "remaining_minute": int(minute_bucket.remaining()),
                "limit_minute": self.requests_per_minute,
                "remaining_hour": int(hour_bucket.remaining()),
                "limit_hour": self.requests_per_hour,
            }


# =====================================================================
# Redis-backed fixed-window limiter
# =====================================================================


class RedisRateLimiter:
    """Redis-backed fixed-window rate limiter.

    Uses ``INCR`` + ``EXPIRE`` for simple, distributed rate limiting.
    Falls back to allowing requests when Redis is unavailable (graceful
    degradation — rate limiting is a safety net, not a gate).

    Parameters
    ----------
    requests_per_minute:
        Maximum requests per minute per client.
    requests_per_hour:
        Maximum requests per hour per client.
    """

    def __init__(
        self,
        requests_per_minute: int = 60,
        requests_per_hour: int = 1000,
    ) -> None:
        self.requests_per_minute = requests_per_minute
        self.requests_per_hour = requests_per_hour

    async def is_allowed(self, client_id: str) -> bool:
        """Check if a request is allowed, using Redis INCR + EXPIRE.

        Returns ``True`` if under limits. Returns ``True`` on any Redis
        error (graceful degradation).
        """
        from src.cache.redis_client import get_redis_client

        redis = get_redis_client()
        if redis is None:
            return True  # No Redis = allow through

        try:
            now = int(time.time())
            minute_window = now // 60
            hour_window = now // 3600

            minute_key = f"sidera:rate:{client_id}:m:{minute_window}"
            hour_key = f"sidera:rate:{client_id}:h:{hour_window}"

            pipe = redis.pipeline(transaction=False)
            pipe.incr(minute_key)
            pipe.expire(minute_key, 120)  # 2x window for safety
            pipe.incr(hour_key)
            pipe.expire(hour_key, 7200)  # 2x window for safety
            results = await pipe.execute()

            minute_count = results[0]
            hour_count = results[2]

            return minute_count <= self.requests_per_minute and hour_count <= self.requests_per_hour
        except Exception as exc:
            logger.warning(
                "rate_limit.redis_error",
                error=str(exc),
                client_id=client_id,
            )
            return True  # Graceful degradation: allow through

    async def get_remaining(self, client_id: str) -> dict:
        """Return remaining request allowances from Redis.

        Falls back to full capacity on Redis errors.
        """
        from src.cache.redis_client import get_redis_client

        redis = get_redis_client()
        if redis is None:
            return {
                "remaining_minute": self.requests_per_minute,
                "limit_minute": self.requests_per_minute,
                "remaining_hour": self.requests_per_hour,
                "limit_hour": self.requests_per_hour,
            }

        try:
            now = int(time.time())
            minute_window = now // 60
            hour_window = now // 3600

            minute_key = f"sidera:rate:{client_id}:m:{minute_window}"
            hour_key = f"sidera:rate:{client_id}:h:{hour_window}"

            pipe = redis.pipeline(transaction=False)
            pipe.get(minute_key)
            pipe.get(hour_key)
            results = await pipe.execute()

            minute_count = int(results[0] or 0)
            hour_count = int(results[1] or 0)

            return {
                "remaining_minute": max(0, self.requests_per_minute - minute_count),
                "limit_minute": self.requests_per_minute,
                "remaining_hour": max(0, self.requests_per_hour - hour_count),
                "limit_hour": self.requests_per_hour,
            }
        except Exception:
            return {
                "remaining_minute": self.requests_per_minute,
                "limit_minute": self.requests_per_minute,
                "remaining_hour": self.requests_per_hour,
                "limit_hour": self.requests_per_hour,
            }


# =====================================================================
# Factory
# =====================================================================


def create_rate_limiter(
    requests_per_minute: int = 60,
    requests_per_hour: int = 1000,
) -> RateLimiter | RedisRateLimiter:
    """Create the best available rate limiter.

    Returns a ``RedisRateLimiter`` if Redis is configured and reachable,
    otherwise falls back to the in-memory ``RateLimiter``.
    """
    from src.cache.redis_client import get_redis_client

    if get_redis_client() is not None:
        logger.info("rate_limiter.using_redis")
        return RedisRateLimiter(requests_per_minute, requests_per_hour)

    logger.info("rate_limiter.using_inmemory")
    return RateLimiter(requests_per_minute, requests_per_hour)


# Default shared instance (in-memory — for backward compatibility)
_default_limiter = RateLimiter()


# =====================================================================
# Client ID extraction
# =====================================================================


def _extract_client_id(request: Request) -> str:
    """Extract a client identifier from the request.

    Prefers the ``X-User-ID`` header; falls back to the client's IP address.
    """
    user_id = request.headers.get("X-User-ID")
    if user_id:
        return user_id
    if request.client:
        return request.client.host
    return "unknown"


# =====================================================================
# Middleware
# =====================================================================


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI/Starlette middleware that enforces rate limits.

    Supports both sync (in-memory) and async (Redis) limiters.
    Can be added globally via ``app.add_middleware(RateLimitMiddleware)``
    or instantiated with a custom limiter.
    """

    def __init__(
        self,
        app,  # noqa: ANN001
        limiter: RateLimiter | RedisRateLimiter | None = None,
    ) -> None:
        super().__init__(app)
        self.limiter = limiter or _default_limiter

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        client_id = _extract_client_id(request)

        # Check rate limit (async for Redis, sync for in-memory)
        if isinstance(self.limiter, RedisRateLimiter):
            allowed = await self.limiter.is_allowed(client_id)
        else:
            allowed = self.limiter.is_allowed(client_id)

        if not allowed:
            # Get remaining info
            if isinstance(self.limiter, RedisRateLimiter):
                remaining = await self.limiter.get_remaining(client_id)
            else:
                remaining = self.limiter.get_remaining(client_id)

            logger.warning(
                "rate_limit.exceeded",
                client_id=client_id,
                path=request.url.path,
            )
            return JSONResponse(
                status_code=429,
                content={"detail": "Too Many Requests"},
                headers={
                    "Retry-After": "60",
                    "X-RateLimit-Limit": str(remaining["limit_minute"]),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)

        # Add rate limit headers to successful responses
        if isinstance(self.limiter, RedisRateLimiter):
            remaining = await self.limiter.get_remaining(client_id)
        else:
            remaining = self.limiter.get_remaining(client_id)

        response.headers["X-RateLimit-Limit"] = str(remaining["limit_minute"])
        response.headers["X-RateLimit-Remaining"] = str(remaining["remaining_minute"])

        return response


async def rate_limit_middleware(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """Standalone middleware function for rate limiting.

    Can be used with ``app.middleware("http")(rate_limit_middleware)`` or
    applied selectively to specific routers.
    """
    client_id = _extract_client_id(request)

    if not _default_limiter.is_allowed(client_id):
        remaining = _default_limiter.get_remaining(client_id)
        logger.warning(
            "rate_limit.exceeded",
            client_id=client_id,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=429,
            content={"detail": "Too Many Requests"},
            headers={
                "Retry-After": "60",
                "X-RateLimit-Limit": str(remaining["limit_minute"]),
                "X-RateLimit-Remaining": "0",
            },
        )

    response = await call_next(request)

    remaining = _default_limiter.get_remaining(client_id)
    response.headers["X-RateLimit-Limit"] = str(remaining["limit_minute"])
    response.headers["X-RateLimit-Remaining"] = str(remaining["remaining_minute"])

    return response
