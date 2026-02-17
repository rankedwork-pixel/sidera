"""Tests for the token refresh workflow and _refresh_oauth_token helper.

Covers:
- _refresh_oauth_token for google_ads, google_drive, meta, and unsupported platforms
- Encryption/decryption of tokens during refresh
- token_refresh_workflow end-to-end: happy path, partial failures, Slack alerts
- HTTP errors during token refresh

All external dependencies (httpx, database, Slack, encryption, settings)
are mocked.  Inngest Context is simulated with the shared helper from conftest.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.workflows.daily_briefing import (
    _refresh_oauth_token,
    token_refresh_workflow,
)
from tests.test_workflows.conftest import _make_mock_context

# =====================================================================
# Helpers
# =====================================================================


def _make_account(
    platform: str = "google_ads",
    *,
    account_id: int = 1,
    platform_account_id: str = "123456",
    oauth_refresh_token: str = "enc:refresh-tok",
    oauth_access_token: str = "enc:access-tok",
) -> MagicMock:
    """Build a mock Account with the fields _refresh_oauth_token reads."""
    account = MagicMock()
    account.id = account_id
    account.platform = platform
    account.platform_account_id = platform_account_id
    account.oauth_refresh_token = oauth_refresh_token
    account.oauth_access_token = oauth_access_token
    return account


def _google_token_response(
    access_token: str = "new-access",
    refresh_token: str | None = None,
    expires_in: int = 3600,
) -> dict:
    """Construct a Google OAuth token-endpoint response body."""
    data = {"access_token": access_token, "expires_in": expires_in}
    if refresh_token is not None:
        data["refresh_token"] = refresh_token
    return data


def _meta_token_response(
    access_token: str = "new-meta-access",
    expires_in: int = 5_184_000,
) -> dict:
    """Construct a Meta token-exchange response body."""
    return {"access_token": access_token, "expires_in": expires_in}


# _refresh_oauth_token uses local imports:
#   from src.config import settings
#   from src.utils.encryption import decrypt_token, encrypt_token
# Because these are local (not module-level), we patch at the source modules.
_PATCH_DECRYPT = "src.utils.encryption.decrypt_token"
_PATCH_ENCRYPT = "src.utils.encryption.encrypt_token"
_PATCH_SETTINGS = "src.config.settings"


# =====================================================================
# _refresh_oauth_token — Google Ads
# =====================================================================


@pytest.mark.asyncio
async def test_refresh_google_ads_calls_correct_endpoint():
    """google_ads platform POSTs to googleapis.com/token with correct data."""
    account = _make_account(platform="google_ads")
    mock_response = MagicMock()
    mock_response.json.return_value = _google_token_response()
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch(
            _PATCH_DECRYPT,
            side_effect=lambda t: t.replace("enc:", ""),
        ),
        patch(
            _PATCH_ENCRYPT,
            side_effect=lambda t: f"enc:{t}",
        ),
        patch(_PATCH_SETTINGS) as mock_settings,
    ):
        mock_settings.google_ads_client_id = "gads-client-id"
        mock_settings.google_ads_client_secret = "gads-client-secret"

        result = await _refresh_oauth_token(account)

    # Verify POST call
    mock_client.post.assert_awaited_once()
    call_args = mock_client.post.call_args
    assert call_args.args[0] == "https://oauth2.googleapis.com/token"
    post_data = call_args.kwargs["data"]
    assert post_data["client_id"] == "gads-client-id"
    assert post_data["client_secret"] == "gads-client-secret"
    assert post_data["grant_type"] == "refresh_token"
    assert post_data["refresh_token"] == "refresh-tok"  # decrypted

    assert result is not None
    assert result["access_token"] == "enc:new-access"


# =====================================================================
# _refresh_oauth_token — Google Drive
# =====================================================================


@pytest.mark.asyncio
async def test_refresh_google_drive_calls_google_endpoint():
    """google_drive platform uses the same Google endpoint as google_ads."""
    account = _make_account(platform="google_drive")
    mock_response = MagicMock()
    mock_response.json.return_value = _google_token_response()
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch(
            _PATCH_DECRYPT,
            side_effect=lambda t: t.replace("enc:", ""),
        ),
        patch(
            _PATCH_ENCRYPT,
            side_effect=lambda t: f"enc:{t}",
        ),
        patch(_PATCH_SETTINGS) as mock_settings,
    ):
        mock_settings.google_ads_client_id = "gads-client-id"
        mock_settings.google_ads_client_secret = "gads-client-secret"

        result = await _refresh_oauth_token(account)

    mock_client.post.assert_awaited_once()
    url = mock_client.post.call_args.args[0]
    assert url == "https://oauth2.googleapis.com/token"
    assert result is not None
    assert "access_token" in result


# =====================================================================
# _refresh_oauth_token — Meta
# =====================================================================


@pytest.mark.asyncio
async def test_refresh_meta_calls_facebook_endpoint():
    """meta platform GETs from graph.facebook.com with correct params."""
    account = _make_account(platform="meta")
    mock_response = MagicMock()
    mock_response.json.return_value = _meta_token_response()
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch(
            _PATCH_DECRYPT,
            side_effect=lambda t: t.replace("enc:", ""),
        ),
        patch(
            _PATCH_ENCRYPT,
            side_effect=lambda t: f"enc:{t}",
        ),
        patch(_PATCH_SETTINGS) as mock_settings,
    ):
        mock_settings.meta_app_id = "meta-app-id"
        mock_settings.meta_app_secret = "meta-app-secret"

        result = await _refresh_oauth_token(account)

    mock_client.get.assert_awaited_once()
    call_args = mock_client.get.call_args
    assert "graph.facebook.com" in call_args.args[0]
    params = call_args.kwargs["params"]
    assert params["grant_type"] == "fb_exchange_token"
    assert params["client_id"] == "meta-app-id"
    assert params["client_secret"] == "meta-app-secret"
    assert params["fb_exchange_token"] == "access-tok"  # decrypted

    assert result is not None
    assert result["access_token"] == "enc:new-meta-access"
    # Meta does not return refresh_token
    assert "refresh_token" not in result


# =====================================================================
# _refresh_oauth_token — unsupported platform
# =====================================================================


@pytest.mark.asyncio
async def test_refresh_unsupported_platform_returns_none():
    """Unsupported platform (e.g., tiktok) returns None without HTTP calls."""
    account = _make_account(platform="tiktok")

    with (
        patch("httpx.AsyncClient") as mock_httpx,
        patch(
            _PATCH_DECRYPT,
            side_effect=lambda t: t,
        ),
        patch(
            _PATCH_ENCRYPT,
            side_effect=lambda t: t,
        ),
        patch(_PATCH_SETTINGS),
    ):
        result = await _refresh_oauth_token(account)

    assert result is None
    # No HTTP client should have been instantiated
    mock_httpx.assert_not_called()


# =====================================================================
# _refresh_oauth_token — encrypts tokens before returning
# =====================================================================


@pytest.mark.asyncio
async def test_refresh_encrypts_tokens_before_returning():
    """Returned access_token and refresh_token are encrypted."""
    account = _make_account(platform="google_ads")

    mock_response = MagicMock()
    mock_response.json.return_value = _google_token_response(
        access_token="plain-access", refresh_token="plain-refresh"
    )
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    encrypt_calls = []

    def fake_encrypt(val):
        encrypt_calls.append(val)
        return f"enc:{val}"

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch(
            _PATCH_DECRYPT,
            side_effect=lambda t: t.replace("enc:", ""),
        ),
        patch(
            _PATCH_ENCRYPT,
            side_effect=fake_encrypt,
        ),
        patch(_PATCH_SETTINGS) as mock_settings,
    ):
        mock_settings.google_ads_client_id = "id"
        mock_settings.google_ads_client_secret = "secret"

        result = await _refresh_oauth_token(account)

    assert result is not None
    assert result["access_token"] == "enc:plain-access"
    assert result["refresh_token"] == "enc:plain-refresh"
    # encrypt_token was called for both access and refresh
    assert "plain-access" in encrypt_calls
    assert "plain-refresh" in encrypt_calls


# =====================================================================
# _refresh_oauth_token — decrypts existing tokens before sending
# =====================================================================


@pytest.mark.asyncio
async def test_refresh_decrypts_existing_tokens_before_api_call():
    """Existing encrypted refresh/access tokens are decrypted before sending to API."""
    account = _make_account(
        platform="google_ads",
        oauth_refresh_token="enc:my-encrypted-refresh",
    )

    mock_response = MagicMock()
    mock_response.json.return_value = _google_token_response()
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    decrypt_calls = []

    def fake_decrypt(val):
        decrypt_calls.append(val)
        return val.replace("enc:", "")

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch(
            _PATCH_DECRYPT,
            side_effect=fake_decrypt,
        ),
        patch(
            _PATCH_ENCRYPT,
            side_effect=lambda t: f"enc:{t}",
        ),
        patch(_PATCH_SETTINGS) as mock_settings,
    ):
        mock_settings.google_ads_client_id = "id"
        mock_settings.google_ads_client_secret = "secret"

        await _refresh_oauth_token(account)

    # decrypt_token was called with the stored encrypted value
    assert "enc:my-encrypted-refresh" in decrypt_calls
    # The plaintext was sent to the API
    post_data = mock_client.post.call_args.kwargs["data"]
    assert post_data["refresh_token"] == "my-encrypted-refresh"


# =====================================================================
# Workflow — accounts expiring in 3 days are refreshed
# =====================================================================


@pytest.mark.asyncio
async def test_workflow_refreshes_accounts_expiring_in_3_days():
    """Accounts with tokens expiring in 3 days are picked up and refreshed."""
    ctx = _make_mock_context()
    account = _make_account(platform="google_ads")

    mock_session = AsyncMock()

    with (
        patch("src.db.session.get_db_session") as mock_get_session,
        patch(
            "src.db.service.get_accounts_expiring_soon",
            return_value=[account],
        ) as mock_expiring,
        patch(
            "src.db.service.update_account_tokens",
            new_callable=AsyncMock,
        ) as mock_update,
        patch(
            "src.workflows.daily_briefing._refresh_oauth_token",
            new_callable=AsyncMock,
            return_value={
                "access_token": "enc:new-access",
                "refresh_token": "enc:new-refresh",
                "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            },
        ),
    ):
        mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await token_refresh_workflow._handler(ctx)

    assert result["refreshed"] == 1
    assert result["failed"] == 0
    # get_accounts_expiring_soon called with within_days=7
    mock_expiring.assert_awaited_once()
    call_kwargs = mock_expiring.call_args
    assert call_kwargs.kwargs.get("within_days") == 7 or call_kwargs.args[-1] == 7
    # update_account_tokens called with encrypted tokens
    mock_update.assert_awaited_once()


# =====================================================================
# Workflow — accounts expiring in 30 days are NOT refreshed
# =====================================================================


@pytest.mark.asyncio
async def test_workflow_does_not_refresh_accounts_expiring_in_30_days():
    """Accounts expiring beyond the 7-day window are not returned by the DB query."""
    ctx = _make_mock_context()

    mock_session = AsyncMock()

    with (
        patch("src.db.session.get_db_session") as mock_get_session,
        patch(
            "src.db.service.get_accounts_expiring_soon",
            return_value=[],  # DB correctly returns nothing for 30-day-out tokens
        ),
        patch(
            "src.db.service.update_account_tokens",
            new_callable=AsyncMock,
        ) as mock_update,
        patch(
            "src.workflows.daily_briefing._refresh_oauth_token",
            new_callable=AsyncMock,
        ) as mock_refresh,
    ):
        mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await token_refresh_workflow._handler(ctx)

    assert result["refreshed"] == 0
    assert result["failed"] == 0
    # _refresh_oauth_token should never be called
    mock_refresh.assert_not_awaited()
    mock_update.assert_not_awaited()


# =====================================================================
# Workflow — failed refresh sends Slack alert
# =====================================================================


@pytest.mark.asyncio
async def test_workflow_failed_refresh_sends_slack_alert():
    """When a token refresh fails, a Slack alert is sent."""
    ctx = _make_mock_context()
    account = _make_account(platform="google_ads")

    mock_session = AsyncMock()
    mock_slack = MagicMock()
    mock_slack.send_alert.return_value = {"ok": True}

    with (
        patch("src.db.session.get_db_session") as mock_get_session,
        patch(
            "src.db.service.get_accounts_expiring_soon",
            return_value=[account],
        ),
        patch(
            "src.db.service.update_account_tokens",
            new_callable=AsyncMock,
        ),
        patch(
            "src.workflows.daily_briefing._refresh_oauth_token",
            new_callable=AsyncMock,
            side_effect=Exception("Token endpoint unreachable"),
        ),
        patch(
            "src.connectors.slack.SlackConnector",
            return_value=mock_slack,
        ),
        patch("src.middleware.sentry_setup.capture_exception"),
    ):
        mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await token_refresh_workflow._handler(ctx)

    assert result["failed"] == 1
    # Slack alert step should have run
    mock_slack.send_alert.assert_called_once()
    alert_kwargs = mock_slack.send_alert.call_args
    assert alert_kwargs.kwargs.get("alert_type") == "token_refresh_failed"


# =====================================================================
# Workflow — tokens are encrypted before saving
# =====================================================================


@pytest.mark.asyncio
async def test_workflow_saves_encrypted_tokens():
    """update_account_tokens receives already-encrypted token values."""
    ctx = _make_mock_context()
    account = _make_account(platform="google_ads")

    mock_session = AsyncMock()

    with (
        patch("src.db.session.get_db_session") as mock_get_session,
        patch(
            "src.db.service.get_accounts_expiring_soon",
            return_value=[account],
        ),
        patch(
            "src.db.service.update_account_tokens",
            new_callable=AsyncMock,
        ) as mock_update,
        patch(
            "src.workflows.daily_briefing._refresh_oauth_token",
            new_callable=AsyncMock,
            return_value={
                "access_token": "enc:encrypted-access",
                "refresh_token": "enc:encrypted-refresh",
                "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            },
        ),
    ):
        mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

        await token_refresh_workflow._handler(ctx)

    mock_update.assert_awaited_once()
    call_kwargs = mock_update.call_args
    assert call_kwargs.kwargs.get("access_token") == "enc:encrypted-access" or (
        len(call_kwargs.args) > 2 and call_kwargs.args[2] == "enc:encrypted-access"
    )


# =====================================================================
# Workflow — multiple accounts: some succeed, some fail
# =====================================================================


@pytest.mark.asyncio
async def test_workflow_mixed_success_and_failure():
    """With 3 accounts, 2 succeed and 1 fails -> correct counts."""
    ctx = _make_mock_context()
    accounts = [
        _make_account(platform="google_ads", account_id=1, platform_account_id="a1"),
        _make_account(platform="meta", account_id=2, platform_account_id="a2"),
        _make_account(platform="google_drive", account_id=3, platform_account_id="a3"),
    ]

    call_count = 0

    async def mixed_refresh(acct):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise Exception("Meta token exchange failed")
        return {
            "access_token": f"enc:access-{acct.id}",
            "refresh_token": f"enc:refresh-{acct.id}",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        }

    mock_session = AsyncMock()
    mock_slack = MagicMock()
    mock_slack.send_alert.return_value = {"ok": True}

    with (
        patch("src.db.session.get_db_session") as mock_get_session,
        patch(
            "src.db.service.get_accounts_expiring_soon",
            return_value=accounts,
        ),
        patch(
            "src.db.service.update_account_tokens",
            new_callable=AsyncMock,
        ) as mock_update,
        patch(
            "src.workflows.daily_briefing._refresh_oauth_token",
            new_callable=AsyncMock,
            side_effect=mixed_refresh,
        ),
        patch(
            "src.connectors.slack.SlackConnector",
            return_value=mock_slack,
        ),
        patch("src.middleware.sentry_setup.capture_exception"),
    ):
        mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await token_refresh_workflow._handler(ctx)

    assert result["refreshed"] == 2
    assert result["failed"] == 1
    assert len(result["errors"]) == 1
    assert "Meta token exchange failed" in result["errors"][0]
    # update_account_tokens called twice (for the two successes)
    assert mock_update.await_count == 2
    # Slack alert sent because of the failure
    mock_slack.send_alert.assert_called_once()


# =====================================================================
# Workflow — HTTP error during refresh is caught
# =====================================================================


@pytest.mark.asyncio
async def test_refresh_http_error_caught_and_counted():
    """httpx.HTTPStatusError during refresh is caught and counted as failed."""
    ctx = _make_mock_context()
    account = _make_account(platform="google_ads")

    mock_session = AsyncMock()
    mock_slack = MagicMock()
    mock_slack.send_alert.return_value = {"ok": True}

    # Simulate raise_for_status raising HTTPStatusError
    http_error = httpx.HTTPStatusError(
        "401 Unauthorized",
        request=httpx.Request("POST", "https://oauth2.googleapis.com/token"),
        response=httpx.Response(401),
    )

    with (
        patch("src.db.session.get_db_session") as mock_get_session,
        patch(
            "src.db.service.get_accounts_expiring_soon",
            return_value=[account],
        ),
        patch(
            "src.db.service.update_account_tokens",
            new_callable=AsyncMock,
        ) as mock_update,
        patch(
            "src.workflows.daily_briefing._refresh_oauth_token",
            new_callable=AsyncMock,
            side_effect=http_error,
        ),
        patch(
            "src.connectors.slack.SlackConnector",
            return_value=mock_slack,
        ),
        patch("src.middleware.sentry_setup.capture_exception"),
    ):
        mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await token_refresh_workflow._handler(ctx)

    assert result["refreshed"] == 0
    assert result["failed"] == 1
    assert len(result["errors"]) == 1
    # update_account_tokens never called
    mock_update.assert_not_awaited()
    # Slack alert sent
    mock_slack.send_alert.assert_called_once()


# =====================================================================
# Cron trigger and function ID
# =====================================================================


def test_token_refresh_cron_trigger():
    """Token refresh workflow runs at 5 AM daily."""
    config = token_refresh_workflow.get_config("sidera")
    triggers = config.main.triggers
    assert len(triggers) == 1
    assert triggers[0].cron == "0 5 * * *"


def test_token_refresh_function_id():
    """Token refresh has the expected function ID."""
    assert token_refresh_workflow.id == "sidera-sidera-token-refresh"
