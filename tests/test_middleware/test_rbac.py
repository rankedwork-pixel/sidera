"""Tests for RBAC middleware (role-based access control).

Covers:
- Permission matrix (has_permission for all role × action combos)
- Role resolution (DB lookup, cache, default fallback)
- Slack permission wrapper (allowed/denied messages)
- Cache behavior (TTL, clearing)
- Edge cases (unknown actions, "none" role, DB failure graceful degradation)
"""

from unittest.mock import AsyncMock, patch  # noqa: I001

import pytest

from src.middleware.rbac import (
    _role_cache,
    check_permission,
    check_slack_permission,
    clear_role_cache,
    has_permission,
    resolve_user_role,
)


# ============================================================
# Permission matrix tests
# ============================================================


class TestHasPermission:
    """Test the static permission check."""

    def test_admin_can_manage_users(self):
        assert has_permission("admin", "manage_users") is True

    def test_admin_can_approve(self):
        assert has_permission("admin", "approve") is True

    def test_admin_can_view(self):
        assert has_permission("admin", "view") is True

    def test_approver_can_approve(self):
        assert has_permission("approver", "approve") is True

    def test_approver_can_reject(self):
        assert has_permission("approver", "reject") is True

    def test_approver_can_view(self):
        assert has_permission("approver", "view") is True

    def test_approver_can_run_skill(self):
        assert has_permission("approver", "run_skill") is True

    def test_approver_can_chat(self):
        assert has_permission("approver", "chat") is True

    def test_approver_cannot_manage_users(self):
        assert has_permission("approver", "manage_users") is False

    def test_approver_cannot_manage_org_chart(self):
        assert has_permission("approver", "manage_org_chart") is False

    def test_viewer_can_view(self):
        assert has_permission("viewer", "view") is True

    def test_viewer_can_list(self):
        assert has_permission("viewer", "list") is True

    def test_viewer_cannot_approve(self):
        assert has_permission("viewer", "approve") is False

    def test_viewer_cannot_run_skill(self):
        assert has_permission("viewer", "run_skill") is False

    def test_viewer_cannot_manage_users(self):
        assert has_permission("viewer", "manage_users") is False

    def test_none_role_denied_everything(self):
        assert has_permission("none", "view") is False
        assert has_permission("none", "approve") is False
        assert has_permission("none", "manage_users") is False

    def test_unknown_action_requires_admin(self):
        """Unknown actions default to admin-only for safety."""
        assert has_permission("admin", "unknown_action") is True
        assert has_permission("approver", "unknown_action") is False
        assert has_permission("viewer", "unknown_action") is False


# ============================================================
# Role resolution tests
# ============================================================


class TestResolveUserRole:
    """Test role resolution from DB with cache and default fallback."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        clear_role_cache()
        yield
        clear_role_cache()

    @pytest.mark.asyncio
    async def test_returns_db_role(self):
        """User in DB gets their stored role."""
        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.db.session.get_db_session", return_value=mock_cm),
            patch("src.db.service.get_user_role", AsyncMock(return_value="admin")),
        ):
            role = await resolve_user_role("U_ADMIN")

        assert role == "admin"

    @pytest.mark.asyncio
    async def test_returns_default_for_unknown_user(self):
        """User not in DB gets the default role from settings."""
        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.db.session.get_db_session", return_value=mock_cm),
            patch("src.db.service.get_user_role", AsyncMock(return_value=None)),
            patch("src.middleware.rbac.settings") as mock_settings,
        ):
            mock_settings.rbac_default_role = "viewer"
            mock_settings.is_production = False
            role = await resolve_user_role("U_UNKNOWN")

        assert role == "viewer"

    @pytest.mark.asyncio
    async def test_cache_hit_skips_db(self):
        """Cached role is returned without DB call."""
        # Pre-populate cache
        from src.middleware.rbac import _cache_set

        _cache_set("U_CACHED", "approver")

        # This should NOT hit DB
        role = await resolve_user_role("U_CACHED")
        assert role == "approver"

    @pytest.mark.asyncio
    async def test_db_failure_returns_default(self):
        """If DB is down, graceful degradation to default role."""
        with (
            patch(
                "src.db.session.get_db_session",
                side_effect=Exception("DB down"),
            ),
            patch("src.middleware.rbac.settings") as mock_settings,
        ):
            mock_settings.rbac_default_role = "approver"
            mock_settings.is_production = False
            role = await resolve_user_role("U_DB_DOWN")

        assert role == "approver"


# ============================================================
# Cache tests
# ============================================================


class TestRoleCache:
    """Test the in-memory role cache."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        clear_role_cache()
        yield
        clear_role_cache()

    def test_clear_all(self):
        from src.middleware.rbac import _cache_set

        _cache_set("U1", "admin")
        _cache_set("U2", "viewer")
        clear_role_cache()
        assert len(_role_cache) == 0

    def test_clear_specific_user(self):
        from src.middleware.rbac import _cache_set

        _cache_set("U1", "admin")
        _cache_set("U2", "viewer")
        clear_role_cache("U1")
        assert "U1" not in _role_cache
        assert "U2" in _role_cache


# ============================================================
# Slack permission wrapper tests
# ============================================================


class TestCheckSlackPermission:
    """Test the Slack-friendly permission check wrapper."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        clear_role_cache()
        yield
        clear_role_cache()

    @pytest.mark.asyncio
    async def test_allowed_returns_true_empty_msg(self):
        from src.middleware.rbac import _cache_set

        _cache_set("U_ADMIN", "admin")
        allowed, msg = await check_slack_permission("U_ADMIN", "manage_users")
        assert allowed is True
        assert msg == ""

    @pytest.mark.asyncio
    async def test_denied_returns_false_with_message(self):
        from src.middleware.rbac import _cache_set

        _cache_set("U_VIEWER", "viewer")
        allowed, msg = await check_slack_permission("U_VIEWER", "approve")
        assert allowed is False
        assert ":lock:" in msg
        assert "viewer" in msg
        assert "approver" in msg


# ============================================================
# Full check_permission integration
# ============================================================


class TestCheckPermission:
    """Test the combined resolve + check flow."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        clear_role_cache()
        yield
        clear_role_cache()

    @pytest.mark.asyncio
    async def test_admin_approve_allowed(self):
        from src.middleware.rbac import _cache_set

        _cache_set("U_ADMIN", "admin")
        assert await check_permission("U_ADMIN", "approve") is True

    @pytest.mark.asyncio
    async def test_viewer_approve_denied(self):
        from src.middleware.rbac import _cache_set

        _cache_set("U_VIEWER", "viewer")
        assert await check_permission("U_VIEWER", "approve") is False

    @pytest.mark.asyncio
    async def test_none_role_blocked(self):
        from src.middleware.rbac import _cache_set

        _cache_set("U_BLOCKED", "none")
        assert await check_permission("U_BLOCKED", "view") is False
