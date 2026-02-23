"""Tests for the rate limiter middleware."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.middleware.rate_limiter import (
    RateLimiter,
    RateLimitMiddleware,
    RedisRateLimiter,
    _extract_client_id,
    create_rate_limiter,
)

# ------------------------------------------------------------------
# RateLimiter class (in-memory)
# ------------------------------------------------------------------


class TestRateLimiter:
    """Tests for the RateLimiter token-bucket implementation."""

    def test_allows_requests_within_limit(self):
        """Requests within the per-minute limit should be allowed."""
        limiter = RateLimiter(requests_per_minute=5, requests_per_hour=100)
        for _ in range(5):
            assert limiter.is_allowed("client-a") is True

    def test_denies_requests_over_minute_limit(self):
        """Requests exceeding the per-minute limit should be denied."""
        limiter = RateLimiter(requests_per_minute=3, requests_per_hour=100)
        for _ in range(3):
            assert limiter.is_allowed("client-b") is True
        assert limiter.is_allowed("client-b") is False

    def test_denies_requests_over_hour_limit(self):
        """Requests exceeding the per-hour limit should be denied."""
        limiter = RateLimiter(requests_per_minute=100, requests_per_hour=5)
        for _ in range(5):
            assert limiter.is_allowed("client-c") is True
        assert limiter.is_allowed("client-c") is False

    def test_different_clients_isolated(self):
        """Rate limits for different client IDs should be independent."""
        limiter = RateLimiter(requests_per_minute=2, requests_per_hour=100)
        assert limiter.is_allowed("alice") is True
        assert limiter.is_allowed("alice") is True
        assert limiter.is_allowed("alice") is False
        # Bob should still have his full allowance
        assert limiter.is_allowed("bob") is True
        assert limiter.is_allowed("bob") is True

    def test_get_remaining_returns_correct_counts(self):
        """get_remaining should reflect consumed tokens."""
        limiter = RateLimiter(requests_per_minute=10, requests_per_hour=100)
        # Consume 3 requests
        for _ in range(3):
            limiter.is_allowed("client-d")

        remaining = limiter.get_remaining("client-d")
        assert remaining["limit_minute"] == 10
        assert remaining["limit_hour"] == 100
        # After consuming 3 out of 10, remaining should be ~7
        assert remaining["remaining_minute"] <= 10
        assert remaining["remaining_minute"] >= 5  # allow some tolerance for refill
        assert remaining["remaining_hour"] <= 100

    def test_get_remaining_new_client(self):
        """get_remaining for a brand-new client should return full capacity."""
        limiter = RateLimiter(requests_per_minute=60, requests_per_hour=1000)
        remaining = limiter.get_remaining("new-client")
        assert remaining["remaining_minute"] == 60
        assert remaining["remaining_hour"] == 1000
        assert remaining["limit_minute"] == 60
        assert remaining["limit_hour"] == 1000


# ------------------------------------------------------------------
# RateLimitMiddleware integration (in-memory)
# ------------------------------------------------------------------


def _make_app(limiter: RateLimiter) -> FastAPI:
    """Create a minimal FastAPI app with rate limiting middleware."""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, limiter=limiter)

    @app.get("/test")
    async def test_endpoint():
        return {"ok": True}

    return app


class TestRateLimitMiddleware:
    """Integration tests for the rate limiting middleware."""

    def test_middleware_allows_and_adds_headers(self):
        """Successful requests should carry rate-limit response headers."""
        limiter = RateLimiter(requests_per_minute=10, requests_per_hour=100)
        client = TestClient(_make_app(limiter))

        resp = client.get("/test")
        assert resp.status_code == 200
        assert "X-RateLimit-Limit" in resp.headers
        assert "X-RateLimit-Remaining" in resp.headers

    def test_middleware_returns_429_when_exceeded(self):
        """Exceeding the rate limit should return 429 with Retry-After."""
        limiter = RateLimiter(requests_per_minute=2, requests_per_hour=100)
        client = TestClient(_make_app(limiter))

        # Exhaust the limit
        client.get("/test")
        client.get("/test")

        resp = client.get("/test")
        assert resp.status_code == 429
        assert resp.json()["detail"] == "Too Many Requests"
        assert "Retry-After" in resp.headers
        assert resp.headers["X-RateLimit-Remaining"] == "0"

    def test_middleware_ignores_x_user_id_header(self):
        """X-User-ID should NOT be used for client identity (spoofable)."""
        limiter = RateLimiter(requests_per_minute=2, requests_per_hour=100)
        client = TestClient(_make_app(limiter))

        # Even though X-User-ID differs, same IP → same bucket
        resp1 = client.get("/test", headers={"X-User-ID": "user-1"})
        assert resp1.status_code == 200
        resp2 = client.get("/test", headers={"X-User-ID": "user-2"})
        assert resp2.status_code == 200
        # Third request exceeds limit (all same IP)
        resp3 = client.get("/test", headers={"X-User-ID": "user-3"})
        assert resp3.status_code == 429

    def test_middleware_uses_api_key_for_identity(self):
        """Requests with X-API-Key should be identified by hashed key."""
        limiter = RateLimiter(requests_per_minute=1, requests_per_hour=100)
        client = TestClient(_make_app(limiter))

        # First key exhausts its limit
        resp1 = client.get("/test", headers={"X-API-Key": "key-aaa"})
        assert resp1.status_code == 200
        resp2 = client.get("/test", headers={"X-API-Key": "key-aaa"})
        assert resp2.status_code == 429

        # Different key should still work
        resp3 = client.get("/test", headers={"X-API-Key": "key-bbb"})
        assert resp3.status_code == 200

    def test_middleware_uses_bearer_token_for_identity(self):
        """Requests with Bearer token should be identified by hashed token."""
        limiter = RateLimiter(requests_per_minute=1, requests_per_hour=100)
        client = TestClient(_make_app(limiter))

        resp1 = client.get(
            "/test",
            headers={"Authorization": "Bearer tok-111"},
        )
        assert resp1.status_code == 200
        resp2 = client.get(
            "/test",
            headers={"Authorization": "Bearer tok-111"},
        )
        assert resp2.status_code == 429

        # Different token should still work
        resp3 = client.get(
            "/test",
            headers={"Authorization": "Bearer tok-222"},
        )
        assert resp3.status_code == 200


# ------------------------------------------------------------------
# RedisRateLimiter (mocked Redis)
# ------------------------------------------------------------------


class TestRedisRateLimiter:
    """Tests for the Redis-backed rate limiter."""

    @pytest.mark.asyncio
    async def test_allows_under_limit(self):
        """Requests under the limit should be allowed."""
        limiter = RedisRateLimiter(requests_per_minute=10, requests_per_hour=100)

        mock_pipe = AsyncMock()
        # INCR minute → 5, EXPIRE minute, INCR hour → 50, EXPIRE hour
        mock_pipe.execute.return_value = [5, True, 50, True]

        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe

        with patch(
            "src.middleware.rate_limiter.RedisRateLimiter.is_allowed",
            wraps=limiter.is_allowed,
        ):
            with patch(
                "src.cache.redis_client.get_redis_client",
                return_value=mock_redis,
            ):
                result = await limiter.is_allowed("client-1")
                assert result is True

    @pytest.mark.asyncio
    async def test_blocks_over_minute_limit(self):
        """Requests over the per-minute limit should be blocked."""
        limiter = RedisRateLimiter(requests_per_minute=10, requests_per_hour=100)

        mock_pipe = AsyncMock()
        # INCR minute → 11 (over limit), EXPIRE, INCR hour → 50, EXPIRE
        mock_pipe.execute.return_value = [11, True, 50, True]

        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe

        with patch(
            "src.cache.redis_client.get_redis_client",
            return_value=mock_redis,
        ):
            result = await limiter.is_allowed("client-2")
            assert result is False

    @pytest.mark.asyncio
    async def test_blocks_over_hour_limit(self):
        """Requests over the per-hour limit should be blocked."""
        limiter = RedisRateLimiter(requests_per_minute=100, requests_per_hour=10)

        mock_pipe = AsyncMock()
        # INCR minute → 5, EXPIRE, INCR hour → 11 (over limit), EXPIRE
        mock_pipe.execute.return_value = [5, True, 11, True]

        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe

        with patch(
            "src.cache.redis_client.get_redis_client",
            return_value=mock_redis,
        ):
            result = await limiter.is_allowed("client-3")
            assert result is False

    @pytest.mark.asyncio
    async def test_graceful_degradation_on_error(self):
        """Redis error should allow the request through (graceful degradation)."""
        limiter = RedisRateLimiter(requests_per_minute=10, requests_per_hour=100)

        mock_pipe = AsyncMock()
        mock_pipe.execute.side_effect = ConnectionError("Redis unavailable")

        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe

        with patch(
            "src.cache.redis_client.get_redis_client",
            return_value=mock_redis,
        ):
            result = await limiter.is_allowed("client-4")
            assert result is True  # Should allow through on error

    @pytest.mark.asyncio
    async def test_allows_when_no_redis(self):
        """When Redis is not configured, all requests should be allowed."""
        limiter = RedisRateLimiter(requests_per_minute=1, requests_per_hour=1)

        with patch(
            "src.cache.redis_client.get_redis_client",
            return_value=None,
        ):
            result = await limiter.is_allowed("client-5")
            assert result is True

    @pytest.mark.asyncio
    async def test_get_remaining_returns_counts(self):
        """get_remaining should return correct remaining counts from Redis."""
        limiter = RedisRateLimiter(requests_per_minute=60, requests_per_hour=1000)

        mock_pipe = AsyncMock()
        mock_pipe.execute.return_value = ["15", "200"]  # minute, hour counts

        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe

        with patch(
            "src.cache.redis_client.get_redis_client",
            return_value=mock_redis,
        ):
            remaining = await limiter.get_remaining("client-6")
            assert remaining["remaining_minute"] == 45  # 60 - 15
            assert remaining["remaining_hour"] == 800  # 1000 - 200
            assert remaining["limit_minute"] == 60
            assert remaining["limit_hour"] == 1000

    @pytest.mark.asyncio
    async def test_get_remaining_no_redis_returns_full(self):
        """get_remaining should return full capacity when Redis is unavailable."""
        limiter = RedisRateLimiter(requests_per_minute=60, requests_per_hour=1000)

        with patch(
            "src.cache.redis_client.get_redis_client",
            return_value=None,
        ):
            remaining = await limiter.get_remaining("client-7")
            assert remaining["remaining_minute"] == 60
            assert remaining["remaining_hour"] == 1000


# ------------------------------------------------------------------
# Factory function
# ------------------------------------------------------------------


class TestCreateRateLimiter:
    """Tests for the create_rate_limiter factory."""

    def test_returns_redis_when_available(self):
        """Factory returns RedisRateLimiter when Redis client exists."""
        mock_redis = MagicMock()
        with patch(
            "src.cache.redis_client.get_redis_client",
            return_value=mock_redis,
        ):
            limiter = create_rate_limiter()
            assert isinstance(limiter, RedisRateLimiter)

    def test_returns_inmemory_when_no_redis(self):
        """Factory returns in-memory RateLimiter when Redis is not available."""
        with patch(
            "src.cache.redis_client.get_redis_client",
            return_value=None,
        ):
            limiter = create_rate_limiter()
            assert isinstance(limiter, RateLimiter)

    def test_factory_passes_limits(self):
        """Factory passes custom limits to the created limiter."""
        with patch(
            "src.cache.redis_client.get_redis_client",
            return_value=None,
        ):
            limiter = create_rate_limiter(
                requests_per_minute=30,
                requests_per_hour=500,
            )
            assert limiter.requests_per_minute == 30
            assert limiter.requests_per_hour == 500


# ------------------------------------------------------------------
# _extract_client_id unit tests
# ------------------------------------------------------------------


class TestExtractClientId:
    """Tests for _extract_client_id hardening."""

    def test_ignores_x_user_id(self) -> None:
        """X-User-ID header should not be used for identity."""
        req = MagicMock()
        req.headers = {"X-User-ID": "attacker-supplied"}
        req.client = MagicMock(host="1.2.3.4")
        result = _extract_client_id(req)
        assert result == "ip:1.2.3.4"

    def test_uses_hashed_api_key(self) -> None:
        """Should return key:<hash> for API key auth."""
        req = MagicMock()
        req.headers = {"X-API-Key": "secret-key-123"}
        req.client = MagicMock(host="1.2.3.4")
        result = _extract_client_id(req)
        assert result.startswith("key:")
        assert len(result) == 4 + 16  # "key:" + 16 hex chars

    def test_uses_hashed_bearer_token(self) -> None:
        """Should return key:<hash> for Bearer token auth."""
        req = MagicMock()
        req.headers = {
            "Authorization": "Bearer my-token-abc",
            "X-API-Key": "",
        }
        req.client = MagicMock(host="1.2.3.4")
        result = _extract_client_id(req)
        assert result.startswith("key:")

    def test_falls_back_to_ip(self) -> None:
        """Should use client IP when no auth headers present."""
        req = MagicMock()
        req.headers = {}
        req.client = MagicMock(host="10.0.0.1")
        result = _extract_client_id(req)
        assert result == "ip:10.0.0.1"

    def test_returns_unknown_when_no_client(self) -> None:
        """Should return 'unknown' when no client info."""
        req = MagicMock()
        req.headers = {}
        req.client = None
        result = _extract_client_id(req)
        assert result == "unknown"
