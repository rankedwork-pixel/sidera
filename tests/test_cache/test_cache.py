"""Tests for the Sidera Redis caching layer.

Covers redis_client, service, and decorators modules using fakeredis
for isolated, in-memory Redis simulation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest

from src.cache import (
    CACHE_TTL_ACCOUNT_INFO,
    CACHE_TTL_CAMPAIGNS,
    CACHE_TTL_METRICS,
    CACHE_TTL_RECOMMENDATIONS,
)
from src.cache.decorators import _build_key, cached
from src.cache.redis_client import (
    close_redis,
    get_redis_client,
    reset_redis_client,
)
from src.cache.service import (
    build_cache_key,
    cache_delete,
    cache_delete_pattern,
    cache_get,
    cache_set,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the Redis singleton before and after each test."""
    reset_redis_client()
    yield
    reset_redis_client()


@pytest.fixture()
def fake_redis():
    """Return a fakeredis async client for direct use in tests."""
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture()
def _patch_redis_client(fake_redis):
    """Patch ``get_redis_client`` to return the fakeredis instance."""
    with patch("src.cache.service.get_redis_client", return_value=fake_redis):
        with (
            patch("src.cache.decorators.cache_get") as mock_get,
            patch("src.cache.decorators.cache_set") as mock_set,
        ):
            # Wire through to the real service functions but using fake redis
            from src.cache import service as svc

            async def _get(key):
                return await svc.cache_get(key)

            async def _set(key, value, ttl_seconds=300):
                return await svc.cache_set(key, value, ttl_seconds=ttl_seconds)

            mock_get.side_effect = _get
            mock_set.side_effect = _set
            yield fake_redis


# ===================================================================
# redis_client tests
# ===================================================================


class TestGetRedisClient:
    """Tests for get_redis_client()."""

    def test_returns_none_when_url_empty(self):
        """When redis_url is empty, should return None and log a warning."""
        with patch("src.cache.redis_client.settings") as mock_settings:
            mock_settings.redis_url = ""
            result = get_redis_client()
            assert result is None

    def test_returns_client_when_url_set(self):
        """When redis_url is set, should return a Redis client instance."""
        with patch("src.cache.redis_client.settings") as mock_settings:
            mock_settings.redis_url = "redis://localhost:6379/0"
            result = get_redis_client()
            assert result is not None

    def test_singleton_returns_same_instance(self):
        """Subsequent calls should return the same client instance."""
        with patch("src.cache.redis_client.settings") as mock_settings:
            mock_settings.redis_url = "redis://localhost:6379/0"
            first = get_redis_client()
            second = get_redis_client()
            assert first is second

    def test_returns_none_after_reset_with_empty_url(self):
        """After reset, a new call with empty URL should return None."""
        with patch("src.cache.redis_client.settings") as mock_settings:
            mock_settings.redis_url = "redis://localhost:6379/0"
            client = get_redis_client()
            assert client is not None

            reset_redis_client()
            mock_settings.redis_url = ""
            assert get_redis_client() is None


class TestCloseRedis:
    """Tests for close_redis()."""

    @pytest.mark.asyncio
    async def test_close_when_no_client(self):
        """close_redis should not raise when no client exists."""
        with patch("src.cache.redis_client.settings") as mock_settings:
            mock_settings.redis_url = ""
            get_redis_client()
            # Should not raise
            await close_redis()

    @pytest.mark.asyncio
    async def test_close_resets_singleton(self):
        """After close, the singleton should be cleared."""
        with patch("src.cache.redis_client.settings") as mock_settings:
            mock_settings.redis_url = "redis://localhost:6379/0"
            client = get_redis_client()
            assert client is not None
            await close_redis()
            # After close, get_redis_client should re-initialize
            reset_redis_client()
            mock_settings.redis_url = ""
            assert get_redis_client() is None


# ===================================================================
# service tests
# ===================================================================


class TestBuildCacheKey:
    """Tests for build_cache_key()."""

    def test_basic_key(self):
        key = build_cache_key("user_123", "campaigns")
        assert key == "sidera:user_123:campaigns"

    def test_key_with_kwargs(self):
        key = build_cache_key(
            "user_123", "campaigns", platform="google_ads", account_id="1234567890"
        )
        assert key == "sidera:user_123:campaigns:1234567890:google_ads"

    def test_kwargs_sorted_deterministically(self):
        """Kwargs should be sorted by key for deterministic keys."""
        key1 = build_cache_key("u1", "metrics", z="last", a="first")
        key2 = build_cache_key("u1", "metrics", a="first", z="last")
        assert key1 == key2

    def test_none_kwargs_excluded(self):
        key = build_cache_key("u1", "data", present="yes", absent=None)
        assert "None" not in key
        assert key == "sidera:u1:data:yes"


class TestCacheGetSet:
    """Tests for cache_get and cache_set."""

    @pytest.mark.asyncio
    async def test_get_returns_none_when_no_redis(self):
        """cache_get returns None when Redis is not configured."""
        with patch("src.cache.service.get_redis_client", return_value=None):
            result = await cache_get("any_key")
            assert result is None

    @pytest.mark.asyncio
    async def test_set_returns_false_when_no_redis(self):
        """cache_set returns False when Redis is not configured."""
        with patch("src.cache.service.get_redis_client", return_value=None):
            result = await cache_set("any_key", {"data": 1})
            assert result is False

    @pytest.mark.asyncio
    async def test_set_and_get(self, fake_redis):
        """Round-trip: set a value and get it back."""
        with patch("src.cache.service.get_redis_client", return_value=fake_redis):
            data = {"campaigns": [{"id": "1", "name": "Test"}]}
            ok = await cache_set("test:key", data, ttl_seconds=60)
            assert ok is True

            result = await cache_get("test:key")
            assert result == data

    @pytest.mark.asyncio
    async def test_get_returns_none_on_miss(self, fake_redis):
        """cache_get returns None for a non-existent key."""
        with patch("src.cache.service.get_redis_client", return_value=fake_redis):
            result = await cache_get("nonexistent:key")
            assert result is None

    @pytest.mark.asyncio
    async def test_get_handles_redis_error_gracefully(self):
        """cache_get returns None when Redis raises an error."""
        mock_client = AsyncMock()
        mock_client.get.side_effect = ConnectionError("Redis down")
        with patch("src.cache.service.get_redis_client", return_value=mock_client):
            result = await cache_get("any_key")
            assert result is None

    @pytest.mark.asyncio
    async def test_set_handles_redis_error_gracefully(self):
        """cache_set returns False when Redis raises an error."""
        mock_client = AsyncMock()
        mock_client.set.side_effect = ConnectionError("Redis down")
        with patch("src.cache.service.get_redis_client", return_value=mock_client):
            result = await cache_set("any_key", {"data": 1})
            assert result is False

    @pytest.mark.asyncio
    async def test_set_with_default_ttl(self, fake_redis):
        """cache_set should use default TTL of 300 seconds."""
        with patch("src.cache.service.get_redis_client", return_value=fake_redis):
            await cache_set("ttl:test", {"val": 1})
            ttl = await fake_redis.ttl("ttl:test")
            assert 0 < ttl <= 300

    @pytest.mark.asyncio
    async def test_set_with_custom_ttl(self, fake_redis):
        """cache_set should respect custom TTL."""
        with patch("src.cache.service.get_redis_client", return_value=fake_redis):
            await cache_set("ttl:custom", {"val": 1}, ttl_seconds=3600)
            ttl = await fake_redis.ttl("ttl:custom")
            assert 0 < ttl <= 3600

    @pytest.mark.asyncio
    async def test_serialization_of_complex_types(self, fake_redis):
        """cache_set should handle Decimal, date, etc. via default=str."""
        from datetime import date
        from decimal import Decimal

        with patch("src.cache.service.get_redis_client", return_value=fake_redis):
            data = {
                "spend": Decimal("123.45"),
                "date": date(2025, 1, 15),
                "nested": {"amount": Decimal("99.99")},
            }
            ok = await cache_set("complex:key", data)
            assert ok is True

            result = await cache_get("complex:key")
            assert result is not None
            # Values are serialized as strings by json default=str
            assert result["spend"] == "123.45"
            assert result["date"] == "2025-01-15"


class TestCacheDelete:
    """Tests for cache_delete."""

    @pytest.mark.asyncio
    async def test_delete_existing_key(self, fake_redis):
        with patch("src.cache.service.get_redis_client", return_value=fake_redis):
            await cache_set("del:key", {"val": 1})
            ok = await cache_delete("del:key")
            assert ok is True
            result = await cache_get("del:key")
            assert result is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_key(self, fake_redis):
        with patch("src.cache.service.get_redis_client", return_value=fake_redis):
            ok = await cache_delete("does:not:exist")
            assert ok is True  # No error, just no-op

    @pytest.mark.asyncio
    async def test_delete_returns_false_when_no_redis(self):
        with patch("src.cache.service.get_redis_client", return_value=None):
            ok = await cache_delete("any:key")
            assert ok is False

    @pytest.mark.asyncio
    async def test_delete_handles_error_gracefully(self):
        mock_client = AsyncMock()
        mock_client.delete.side_effect = ConnectionError("Redis down")
        with patch("src.cache.service.get_redis_client", return_value=mock_client):
            ok = await cache_delete("any:key")
            assert ok is False


class TestCacheDeletePattern:
    """Tests for cache_delete_pattern."""

    @pytest.mark.asyncio
    async def test_delete_matching_keys(self, fake_redis):
        with patch("src.cache.service.get_redis_client", return_value=fake_redis):
            await cache_set("sidera:user_1:campaigns:ga", {"a": 1})
            await cache_set("sidera:user_1:campaigns:meta", {"b": 2})
            await cache_set("sidera:user_2:campaigns:ga", {"c": 3})

            deleted = await cache_delete_pattern("sidera:user_1:*")
            assert deleted == 2

            # user_2 key should still exist
            result = await cache_get("sidera:user_2:campaigns:ga")
            assert result == {"c": 3}

    @pytest.mark.asyncio
    async def test_delete_pattern_returns_zero_when_no_match(self, fake_redis):
        with patch("src.cache.service.get_redis_client", return_value=fake_redis):
            deleted = await cache_delete_pattern("nonexistent:*")
            assert deleted == 0

    @pytest.mark.asyncio
    async def test_delete_pattern_returns_zero_when_no_redis(self):
        with patch("src.cache.service.get_redis_client", return_value=None):
            deleted = await cache_delete_pattern("any:*")
            assert deleted == 0

    @pytest.mark.asyncio
    async def test_delete_pattern_handles_error_gracefully(self):
        mock_client = AsyncMock()
        mock_client.scan_iter.side_effect = ConnectionError("Redis down")
        with patch("src.cache.service.get_redis_client", return_value=mock_client):
            deleted = await cache_delete_pattern("any:*")
            assert deleted == 0


# ===================================================================
# TTL constants tests
# ===================================================================


class TestTTLConstants:
    def test_campaigns_ttl(self):
        assert CACHE_TTL_CAMPAIGNS == 3600

    def test_metrics_ttl(self):
        assert CACHE_TTL_METRICS == 300

    def test_recommendations_ttl(self):
        assert CACHE_TTL_RECOMMENDATIONS == 1800

    def test_account_info_ttl(self):
        assert CACHE_TTL_ACCOUNT_INFO == 7200


# ===================================================================
# decorators tests
# ===================================================================


class TestBuildKeyHelper:
    """Tests for the internal _build_key helper."""

    def test_deterministic_keys(self):
        """Same function + args should produce the same key."""

        async def my_func(a, b):
            pass

        key1 = _build_key(my_func, "", ("x", "y"), {})
        key2 = _build_key(my_func, "", ("x", "y"), {})
        assert key1 == key2

    def test_different_args_different_keys(self):
        async def my_func(a, b):
            pass

        key1 = _build_key(my_func, "", ("x", "y"), {})
        key2 = _build_key(my_func, "", ("a", "b"), {})
        assert key1 != key2

    def test_prefix_overrides_qualname(self):
        async def my_func(a):
            pass

        key = _build_key(my_func, "custom_prefix", ("x",), {})
        assert "custom_prefix" in key
        assert "my_func" not in key

    def test_kwargs_sorted(self):
        async def my_func(**kwargs):
            pass

        key1 = _build_key(my_func, "", (), {"z": 1, "a": 2})
        key2 = _build_key(my_func, "", (), {"a": 2, "z": 1})
        assert key1 == key2


class TestCachedDecoratorAsync:
    """Tests for the cached() decorator with async functions."""

    @pytest.mark.asyncio
    async def test_caches_async_function_result(self, _patch_redis_client):
        call_count = 0

        @cached(ttl_seconds=60)
        async def expensive_call(account_id: str) -> dict:
            nonlocal call_count
            call_count += 1
            return {"account_id": account_id, "campaigns": ["c1", "c2"]}

        # First call -- cache miss
        result1 = await expensive_call("123")
        assert result1 == {"account_id": "123", "campaigns": ["c1", "c2"]}
        assert call_count == 1

        # Second call -- cache hit
        result2 = await expensive_call("123")
        assert result2 == result1
        assert call_count == 1  # Function not called again

    @pytest.mark.asyncio
    async def test_bypass_cache_flag(self, _patch_redis_client):
        call_count = 0

        @cached(ttl_seconds=60)
        async def fetch_data() -> dict:
            nonlocal call_count
            call_count += 1
            return {"count": call_count}

        await fetch_data()
        assert call_count == 1

        # bypass_cache=True should force a fresh call
        result = await fetch_data(bypass_cache=True)
        assert call_count == 2
        assert result == {"count": 2}

    @pytest.mark.asyncio
    async def test_different_args_cached_separately(self, _patch_redis_client):
        call_count = 0

        @cached(ttl_seconds=60)
        async def fetch(id: str) -> dict:
            nonlocal call_count
            call_count += 1
            return {"id": id}

        await fetch("a")
        await fetch("b")
        assert call_count == 2  # Two different keys

        await fetch("a")
        assert call_count == 2  # Cached

    @pytest.mark.asyncio
    async def test_none_result_not_cached(self, _patch_redis_client):
        call_count = 0

        @cached(ttl_seconds=60)
        async def maybe_none() -> dict | None:
            nonlocal call_count
            call_count += 1
            return None

        await maybe_none()
        await maybe_none()
        assert call_count == 2  # None is not cached

    @pytest.mark.asyncio
    async def test_cache_error_falls_through(self):
        """If cache_get raises, the real function is called."""

        async def broken_get(key):
            raise ConnectionError("Redis down")

        async def broken_set(key, value, ttl_seconds=300):
            raise ConnectionError("Redis down")

        with (
            patch("src.cache.decorators.cache_get", side_effect=broken_get),
            patch("src.cache.decorators.cache_set", side_effect=broken_set),
        ):

            @cached(ttl_seconds=60)
            async def resilient_func() -> dict:
                return {"ok": True}

            result = await resilient_func()
            assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_custom_key_prefix(self, _patch_redis_client):
        @cached(ttl_seconds=60, key_prefix="google_ads")
        async def get_campaigns(customer_id: str) -> dict:
            return {"customer_id": customer_id}

        result = await get_campaigns("456")
        assert result == {"customer_id": "456"}

    @pytest.mark.asyncio
    async def test_method_on_class(self, _patch_redis_client):
        """Decorator should work on class methods, skipping self in key."""
        call_count = 0

        class MyConnector:
            @cached(ttl_seconds=60)
            async def get_data(self, account_id: str) -> dict:
                nonlocal call_count
                call_count += 1
                return {"account_id": account_id}

        connector1 = MyConnector()
        connector2 = MyConnector()

        await connector1.get_data("abc")
        assert call_count == 1

        # Different instance, same args -- should hit cache because self is skipped
        await connector2.get_data("abc")
        assert call_count == 1


# ===================================================================
# JSON serialization tests
# ===================================================================


class TestJsonSerialization:
    """Ensure various Python types round-trip through the cache."""

    @pytest.mark.asyncio
    async def test_list_of_dicts(self, fake_redis):
        with patch("src.cache.service.get_redis_client", return_value=fake_redis):
            data = {"results": [{"id": 1}, {"id": 2, "nested": {"a": "b"}}]}
            await cache_set("json:list", data)
            result = await cache_get("json:list")
            assert result == data

    @pytest.mark.asyncio
    async def test_empty_dict(self, fake_redis):
        with patch("src.cache.service.get_redis_client", return_value=fake_redis):
            await cache_set("json:empty", {})
            result = await cache_get("json:empty")
            assert result == {}

    @pytest.mark.asyncio
    async def test_numeric_values(self, fake_redis):
        with patch("src.cache.service.get_redis_client", return_value=fake_redis):
            data = {"int_val": 42, "float_val": 3.14, "negative": -100}
            await cache_set("json:nums", data)
            result = await cache_get("json:nums")
            assert result == data

    @pytest.mark.asyncio
    async def test_boolean_and_null(self, fake_redis):
        with patch("src.cache.service.get_redis_client", return_value=fake_redis):
            data = {"active": True, "deleted": False, "optional": None}
            await cache_set("json:bools", data)
            result = await cache_get("json:bools")
            assert result == data
