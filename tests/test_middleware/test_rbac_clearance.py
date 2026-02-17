"""Tests for clearance features in RBAC middleware.

Covers:
- Clearance hierarchy ordering
- has_clearance() comparisons
- resolve_user_clearance() from cache, DB, and defaults
- check_clearance() and check_slack_clearance() wrappers
- Cache behavior (set, clear)
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.middleware.rbac import (
    _CLEARANCE_HIERARCHY,
    _clearance_cache,
    check_clearance,
    check_slack_clearance,
    clear_clearance_cache,
    has_clearance,
    resolve_user_clearance,
)

# ============================================================
# Hierarchy tests
# ============================================================


class TestClearanceHierarchy:
    def test_hierarchy_has_four_levels(self):
        assert len(_CLEARANCE_HIERARCHY) == 4

    def test_public_is_lowest(self):
        assert _CLEARANCE_HIERARCHY["public"] == 1

    def test_internal_above_public(self):
        assert _CLEARANCE_HIERARCHY["internal"] > _CLEARANCE_HIERARCHY["public"]

    def test_confidential_above_internal(self):
        assert _CLEARANCE_HIERARCHY["confidential"] > _CLEARANCE_HIERARCHY["internal"]

    def test_restricted_is_highest(self):
        assert _CLEARANCE_HIERARCHY["restricted"] == max(_CLEARANCE_HIERARCHY.values())

    def test_ordering_is_strict(self):
        levels = sorted(_CLEARANCE_HIERARCHY, key=_CLEARANCE_HIERARCHY.get)
        assert levels == ["public", "internal", "confidential", "restricted"]


# ============================================================
# has_clearance() tests
# ============================================================


class TestHasClearance:
    def test_public_can_access_public(self):
        assert has_clearance("public", "public") is True

    def test_public_cannot_access_internal(self):
        assert has_clearance("public", "internal") is False

    def test_public_cannot_access_confidential(self):
        assert has_clearance("public", "confidential") is False

    def test_public_cannot_access_restricted(self):
        assert has_clearance("public", "restricted") is False

    def test_internal_can_access_public(self):
        assert has_clearance("internal", "public") is True

    def test_internal_can_access_internal(self):
        assert has_clearance("internal", "internal") is True

    def test_internal_cannot_access_confidential(self):
        assert has_clearance("internal", "confidential") is False

    def test_confidential_can_access_internal(self):
        assert has_clearance("confidential", "internal") is True

    def test_confidential_can_access_confidential(self):
        assert has_clearance("confidential", "confidential") is True

    def test_confidential_cannot_access_restricted(self):
        assert has_clearance("confidential", "restricted") is False

    def test_restricted_can_access_all(self):
        for level in ("public", "internal", "confidential", "restricted"):
            assert has_clearance("restricted", level) is True

    def test_unknown_clearance_defaults_false(self):
        assert has_clearance("unknown", "internal") is False

    def test_unknown_required_defaults_false(self):
        assert has_clearance("internal", "unknown") is False


# ============================================================
# resolve_user_clearance() tests
# ============================================================


class TestResolveUserClearance:
    def setup_method(self):
        _clearance_cache.clear()

    def teardown_method(self):
        _clearance_cache.clear()

    @pytest.mark.asyncio
    async def test_returns_from_cache(self):
        import time

        _clearance_cache["U_CACHED"] = ("confidential", time.time())
        result = await resolve_user_clearance("U_CACHED")
        assert result == "confidential"

    @pytest.mark.asyncio
    async def test_resolves_from_db(self):
        mock_session = AsyncMock()

        async def fake_get_clearance(session, user_id):
            return "restricted"

        with (
            patch(
                "src.db.session.get_db_session",
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=mock_session),
                    __aexit__=AsyncMock(return_value=False),
                ),
            ),
            patch(
                "src.db.service.get_user_clearance",
                side_effect=fake_get_clearance,
            ),
        ):
            result = await resolve_user_clearance("U_FROM_DB")
            assert result == "restricted"

    @pytest.mark.asyncio
    async def test_default_for_unknown_user(self):
        """Unknown user (not in DB) gets the default clearance from settings."""

        async def fake_get_clearance(session, user_id):
            return None

        mock_session = AsyncMock()

        with (
            patch(
                "src.db.session.get_db_session",
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=mock_session),
                    __aexit__=AsyncMock(return_value=False),
                ),
            ),
            patch(
                "src.db.service.get_user_clearance",
                side_effect=fake_get_clearance,
            ),
            patch("src.middleware.rbac.settings") as mock_settings,
        ):
            mock_settings.rbac_default_clearance = "public"
            result = await resolve_user_clearance("U_UNKNOWN")
            assert result == "public"

    @pytest.mark.asyncio
    async def test_db_failure_returns_default(self):
        """DB error gracefully falls back to settings default."""
        with (
            patch(
                "src.db.session.get_db_session",
                side_effect=Exception("DB down"),
            ),
            patch("src.middleware.rbac.settings") as mock_settings,
        ):
            mock_settings.rbac_default_clearance = "public"
            result = await resolve_user_clearance("U_DB_FAIL")
            assert result == "public"


# ============================================================
# check_clearance() and check_slack_clearance() tests
# ============================================================


class TestCheckClearance:
    @pytest.mark.asyncio
    async def test_check_clearance_allowed(self):
        with patch(
            "src.middleware.rbac.resolve_user_clearance",
            return_value="restricted",
        ):
            result = await check_clearance("U1", "confidential")
            assert result is True

    @pytest.mark.asyncio
    async def test_check_clearance_denied(self):
        with patch(
            "src.middleware.rbac.resolve_user_clearance",
            return_value="public",
        ):
            result = await check_clearance("U1", "confidential")
            assert result is False


class TestCheckSlackClearance:
    @pytest.mark.asyncio
    async def test_allowed_returns_true_and_empty_message(self):
        with patch(
            "src.middleware.rbac.resolve_user_clearance",
            return_value="confidential",
        ):
            allowed, msg = await check_slack_clearance("U1", "confidential")
            assert allowed is True
            assert msg == ""

    @pytest.mark.asyncio
    async def test_denied_returns_false_and_message(self):
        with patch(
            "src.middleware.rbac.resolve_user_clearance",
            return_value="public",
        ):
            allowed, msg = await check_slack_clearance("U1", "confidential")
            assert allowed is False
            assert "clearance" in msg.lower() or "insufficient" in msg.lower()


# ============================================================
# Cache behavior
# ============================================================


class TestClearanceCache:
    def setup_method(self):
        _clearance_cache.clear()

    def teardown_method(self):
        _clearance_cache.clear()

    def test_clear_specific_user(self):
        import time

        _clearance_cache["U1"] = ("restricted", time.time())
        _clearance_cache["U2"] = ("internal", time.time())
        clear_clearance_cache("U1")
        assert "U1" not in _clearance_cache
        assert "U2" in _clearance_cache

    def test_clear_all(self):
        import time

        _clearance_cache["U1"] = ("restricted", time.time())
        _clearance_cache["U2"] = ("internal", time.time())
        clear_clearance_cache()
        assert len(_clearance_cache) == 0
