"""Tests for API key authentication middleware."""

from unittest.mock import patch

import pytest

from src.middleware.auth import require_api_key


class TestRequireApiKey:
    """Test the require_api_key dependency."""

    @pytest.mark.asyncio
    async def test_dev_mode_no_key_configured_allows_through(self):
        """In dev mode with no API_KEY set, requests pass without auth."""
        with patch("src.middleware.auth.settings") as mock_settings:
            mock_settings.api_key = ""
            mock_settings.is_production = False
            result = await require_api_key(api_key=None, auth_header=None)
            assert result is None

    @pytest.mark.asyncio
    async def test_production_no_key_configured_raises_500(self):
        """In production with no API_KEY, server returns 500 (misconfigured)."""
        from fastapi import HTTPException

        with patch("src.middleware.auth.settings") as mock_settings:
            mock_settings.api_key = ""
            mock_settings.is_production = True
            with pytest.raises(HTTPException) as exc_info:
                await require_api_key(api_key=None, auth_header=None)
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_valid_x_api_key_header(self):
        """Valid X-API-Key header is accepted."""
        with patch("src.middleware.auth.settings") as mock_settings:
            mock_settings.api_key = "test-secret-key"
            mock_settings.is_production = True
            result = await require_api_key(
                api_key="test-secret-key",
                auth_header=None,
            )
            assert result == "test-secret-key"

    @pytest.mark.asyncio
    async def test_valid_bearer_token(self):
        """Valid Authorization: Bearer <key> is accepted."""
        with patch("src.middleware.auth.settings") as mock_settings:
            mock_settings.api_key = "test-secret-key"
            mock_settings.is_production = True
            result = await require_api_key(
                api_key=None,
                auth_header="Bearer test-secret-key",
            )
            assert result == "test-secret-key"

    @pytest.mark.asyncio
    async def test_missing_key_raises_401(self):
        """Missing key with API_KEY configured raises 401."""
        from fastapi import HTTPException

        with patch("src.middleware.auth.settings") as mock_settings:
            mock_settings.api_key = "test-secret-key"
            mock_settings.is_production = True
            with pytest.raises(HTTPException) as exc_info:
                await require_api_key(api_key=None, auth_header=None)
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_key_raises_403(self):
        """Wrong key raises 403."""
        from fastapi import HTTPException

        with patch("src.middleware.auth.settings") as mock_settings:
            mock_settings.api_key = "correct-key"
            mock_settings.is_production = True
            with pytest.raises(HTTPException) as exc_info:
                await require_api_key(api_key="wrong-key", auth_header=None)
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_bearer_prefix_case_insensitive(self):
        """'bearer' prefix is case-insensitive."""
        with patch("src.middleware.auth.settings") as mock_settings:
            mock_settings.api_key = "my-key"
            mock_settings.is_production = False
            result = await require_api_key(
                api_key=None,
                auth_header="BEARER my-key",
            )
            assert result == "my-key"

    @pytest.mark.asyncio
    async def test_x_api_key_takes_precedence(self):
        """X-API-Key header is preferred over Authorization header."""
        with patch("src.middleware.auth.settings") as mock_settings:
            mock_settings.api_key = "from-x-header"
            mock_settings.is_production = False
            result = await require_api_key(
                api_key="from-x-header",
                auth_header="Bearer from-auth",
            )
            assert result == "from-x-header"
