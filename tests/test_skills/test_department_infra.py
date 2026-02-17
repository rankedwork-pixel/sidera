"""Tests for department-scoped infrastructure resolution.

Tests the ``resolve_slack_channel()`` and
``resolve_department_credentials()`` helpers that provide
per-department Slack channel routing and credential resolution
with graceful fallback to global defaults.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.skills.department_infra import (
    resolve_department_credentials,
    resolve_department_for_role,
    resolve_slack_channel,
)

# ---------------------------------------------------------------------------
# resolve_slack_channel
# ---------------------------------------------------------------------------


class TestResolveSlackChannel:
    """Tests for department-scoped Slack channel resolution."""

    @pytest.mark.asyncio
    async def test_returns_department_channel_when_set(self):
        """If the department has a slack_channel_id, return it."""
        mock_dept = MagicMock()
        mock_dept.slack_channel_id = "C_MARKETING"

        mock_registry = MagicMock()
        mock_registry.get_department.return_value = mock_dept

        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ):
            result = await resolve_slack_channel("marketing")

        assert result == "C_MARKETING"

    @pytest.mark.asyncio
    async def test_falls_back_to_global_when_no_dept_channel(self):
        """If dept has no channel, fall back to settings."""
        mock_dept = MagicMock()
        mock_dept.slack_channel_id = ""

        mock_registry = MagicMock()
        mock_registry.get_department.return_value = mock_dept

        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch(
                "src.skills.department_infra.settings",
            ) as mock_settings,
        ):
            mock_settings.slack_channel_id = "C_GLOBAL"
            result = await resolve_slack_channel("marketing")

        assert result == "C_GLOBAL"

    @pytest.mark.asyncio
    async def test_falls_back_to_global_when_dept_not_found(self):
        """If dept doesn't exist, fall back to settings."""
        mock_registry = MagicMock()
        mock_registry.get_department.return_value = None

        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch(
                "src.skills.department_infra.settings",
            ) as mock_settings,
        ):
            mock_settings.slack_channel_id = "C_GLOBAL"
            result = await resolve_slack_channel("nonexistent")

        assert result == "C_GLOBAL"

    @pytest.mark.asyncio
    async def test_no_fallback_returns_empty(self):
        """If fallback disabled and no dept channel, return empty."""
        mock_registry = MagicMock()
        mock_registry.get_department.return_value = None

        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ):
            result = await resolve_slack_channel(
                "nonexistent",
                fallback_to_global=False,
            )

        assert result == ""

    @pytest.mark.asyncio
    async def test_none_department_returns_global(self):
        """If department_id is None, skip lookup and return global."""
        with patch(
            "src.skills.department_infra.settings",
        ) as mock_settings:
            mock_settings.slack_channel_id = "C_GLOBAL"
            result = await resolve_slack_channel(None)

        assert result == "C_GLOBAL"

    @pytest.mark.asyncio
    async def test_registry_error_falls_back_gracefully(self):
        """If registry loading fails, fall back to global."""
        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                side_effect=Exception("DB down"),
            ),
            patch(
                "src.skills.department_infra.settings",
            ) as mock_settings,
        ):
            mock_settings.slack_channel_id = "C_GLOBAL"
            result = await resolve_slack_channel("marketing")

        assert result == "C_GLOBAL"


# ---------------------------------------------------------------------------
# resolve_department_credentials
# ---------------------------------------------------------------------------


class TestResolveDepartmentCredentials:
    """Tests for department-scoped credential resolution."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_department(self):
        """No department_id means use global credentials."""
        result = await resolve_department_credentials(None, "google_ads")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_scope_not_department(self):
        """If credentials_scope is not 'department', return None."""
        mock_dept = MagicMock()
        mock_dept.credentials_scope = ""

        mock_registry = MagicMock()
        mock_registry.get_department.return_value = mock_dept

        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ):
            result = await resolve_department_credentials("marketing", "google_ads")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_creds_from_env_when_scoped(self):
        """When scope is 'department', resolve from env vars."""
        mock_dept = MagicMock()
        mock_dept.credentials_scope = "department"

        mock_registry = MagicMock()
        mock_registry.get_department.return_value = mock_dept

        env = {
            "MARKETING_GOOGLE_ADS_CLIENT_ID": "dept-client-id",
            "MARKETING_GOOGLE_ADS_REFRESH_TOKEN": "dept-token",
        }

        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch.dict(os.environ, env, clear=False),
        ):
            result = await resolve_department_credentials("marketing", "google_ads")

        assert result is not None
        assert result["client_id"] == "dept-client-id"
        assert result["refresh_token"] == "dept-token"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_env_vars_found(self):
        """Even if scoped, if no env vars found, return None."""
        mock_dept = MagicMock()
        mock_dept.credentials_scope = "department"

        mock_registry = MagicMock()
        mock_registry.get_department.return_value = mock_dept

        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ):
            result = await resolve_department_credentials("marketing", "google_ads")

        assert result is None

    @pytest.mark.asyncio
    async def test_meta_credentials_resolution(self):
        """Test Meta platform credential resolution."""
        mock_dept = MagicMock()
        mock_dept.credentials_scope = "department"

        mock_registry = MagicMock()
        mock_registry.get_department.return_value = mock_dept

        env = {
            "MARKETING_META_ACCESS_TOKEN": "meta-dept-token",
        }

        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch.dict(os.environ, env, clear=False),
        ):
            result = await resolve_department_credentials("marketing", "meta")

        assert result is not None
        assert result["access_token"] == "meta-dept-token"


# ---------------------------------------------------------------------------
# resolve_department_for_role (sync helper)
# ---------------------------------------------------------------------------


class TestResolveDepartmentForRole:
    """Tests for the sync department lookup helper."""

    def test_returns_department_id_when_found(self):
        mock_role = MagicMock()
        mock_role.department_id = "marketing"

        mock_registry = MagicMock()
        mock_registry.get_role.return_value = mock_role

        with (
            patch(
                "src.skills.registry.SkillRegistry",
                return_value=mock_registry,
            ),
        ):
            mock_registry.load_all.return_value = 5
            result = resolve_department_for_role("media_buyer")

        assert result == "marketing"

    def test_returns_none_on_error(self):
        with patch(
            "src.skills.registry.SkillRegistry",
            side_effect=Exception("fail"),
        ):
            result = resolve_department_for_role("unknown")

        assert result is None


# ---------------------------------------------------------------------------
# DepartmentDefinition fields
# ---------------------------------------------------------------------------


class TestDepartmentDefinitionFields:
    """Verify the new fields exist on the dataclass."""

    def test_slack_channel_id_field(self):
        from src.skills.schema import DepartmentDefinition

        dept = DepartmentDefinition(
            id="test",
            name="Test",
            description="Test dept",
            slack_channel_id="C_TEST",
        )
        assert dept.slack_channel_id == "C_TEST"

    def test_credentials_scope_field(self):
        from src.skills.schema import DepartmentDefinition

        dept = DepartmentDefinition(
            id="test",
            name="Test",
            description="Test dept",
            credentials_scope="department",
        )
        assert dept.credentials_scope == "department"

    def test_defaults_to_empty_string(self):
        from src.skills.schema import DepartmentDefinition

        dept = DepartmentDefinition(
            id="test",
            name="Test",
            description="Test dept",
        )
        assert dept.slack_channel_id == ""
        assert dept.credentials_scope == ""
