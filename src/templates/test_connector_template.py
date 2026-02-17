"""Tests for src.connectors.__CHANNEL__ -- __Channel__Connector.

Covers construction, every public method, private helpers, and error handling.
All __Channel__ API calls are mocked; no network traffic is needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# Fake credentials used for explicit-credential tests.
_FAKE_CREDENTIALS = {
    # TODO: Update with the credential keys your connector expects
    "access_token": "test-access-token",
    "client_id": "test-client-id",
    "client_secret": "test-client-secret",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_client():
    """Return a MagicMock standing in for the __Channel__ SDK client."""
    return MagicMock()


@pytest.fixture()
def connector(mock_client):
    """Build a __Channel__Connector with a mocked SDK client."""
    with patch(
        # TODO: Update this patch target to match how you build the client
        "src.connectors.__CHANNEL__.__Channel__Connector._build_client",
        return_value=mock_client,
    ):
        from src.connectors.__CHANNEL__ import __Channel__Connector

        conn = __Channel__Connector(credentials=_FAKE_CREDENTIALS)
    conn._mock_client = mock_client
    return conn


# ===========================================================================
# 1. Construction
# ===========================================================================


class TestConstruction:
    """__Channel__Connector.__init__."""

    def test_explicit_credentials(self, connector):
        """Connector stores provided credentials."""
        assert connector._credentials == _FAKE_CREDENTIALS

    def test_credentials_from_settings(self, mock_client):
        """Falls back to settings singleton when no credentials given."""
        with (
            patch(
                "src.connectors.__CHANNEL__.__Channel__Connector._build_client",
                return_value=mock_client,
            ),
            patch("src.connectors.__CHANNEL__.settings") as mock_stg,
        ):
            # TODO: Set up mock settings attributes
            # mock_stg.__CHANNEL___access_token = "token-from-settings"
            # mock_stg.__CHANNEL___client_id = "id-from-settings"
            _ = mock_stg  # used when TODO is implemented

            from src.connectors.__CHANNEL__ import __Channel__Connector

            conn = __Channel__Connector()
            assert conn._credentials is not None

    def test_missing_credentials_raises_auth_error(self):
        """Raises __Channel__AuthError when credentials are missing."""
        from src.connectors.__CHANNEL__ import __Channel__AuthError

        with pytest.raises(__Channel__AuthError):
            from src.connectors.__CHANNEL__ import __Channel__Connector

            __Channel__Connector(credentials={"access_token": ""})


# ===========================================================================
# 2. get_ad_accounts
# ===========================================================================


class TestGetAdAccounts:
    """__Channel__Connector.get_ad_accounts."""

    def test_happy_path(self, connector):
        """Returns formatted account list."""
        # TODO: Mock the SDK response for listing accounts
        # connector._mock_client.list_accounts.return_value = [...]
        # accounts = connector.get_ad_accounts()
        # assert len(accounts) >= 1
        # assert "id" in accounts[0]
        pass

    def test_empty_result(self, connector):
        """Returns empty list when no accounts found."""
        # TODO: Mock empty SDK response
        # connector._mock_client.list_accounts.return_value = []
        # accounts = connector.get_ad_accounts()
        # assert accounts == []
        pass

    def test_api_error_returns_empty(self, connector):
        """Swallows non-auth API errors and returns empty list."""
        # TODO: Mock an API error
        # connector._mock_client.list_accounts.side_effect = SomeApiError()
        # accounts = connector.get_ad_accounts()
        # assert accounts == []
        pass


# ===========================================================================
# 3. get_campaigns
# ===========================================================================


class TestGetCampaigns:
    """__Channel__Connector.get_campaigns."""

    def test_happy_path(self, connector):
        """Returns formatted campaign list."""
        # TODO: Mock the SDK response and verify output structure
        pass

    def test_empty_result(self, connector):
        """Returns empty list when no campaigns found."""
        pass

    def test_normalizes_monetary_values(self, connector):
        """Monetary values are converted to standard units."""
        # TODO: Verify your platform's monetary conversion
        # (e.g., micros ÷ 1M, cents ÷ 100, etc.)
        pass


# ===========================================================================
# 4. get_campaign_metrics
# ===========================================================================


class TestGetCampaignMetrics:
    """__Channel__Connector.get_campaign_metrics."""

    def test_happy_path(self, connector):
        """Returns daily metrics for a campaign."""
        # TODO: Mock metrics API response
        pass

    def test_date_string_input(self, connector):
        """Accepts string dates as well as date objects."""
        pass

    def test_empty_result(self, connector):
        """Returns empty list when no metrics found."""
        pass


# ===========================================================================
# 5. get_account_metrics
# ===========================================================================


class TestGetAccountMetrics:
    """__Channel__Connector.get_account_metrics."""

    def test_happy_path(self, connector):
        """Returns account-level daily metrics."""
        pass

    def test_empty_result(self, connector):
        """Returns empty list when no metrics found."""
        pass


# ===========================================================================
# 6. Error handling
# ===========================================================================


class TestErrorHandling:
    """Auth error detection and handling."""

    def test_auth_error_is_raised(self, connector):
        """Auth errors are raised as __Channel__AuthError."""
        # TODO: Import and use __Channel__AuthError:
        # from src.connectors.__CHANNEL__ import __Channel__AuthError

        # TODO: Mock an auth failure and verify it's raised
        # connector._mock_client.list_accounts.side_effect = AuthError(401)
        # with pytest.raises(__Channel__AuthError):
        #     connector.get_ad_accounts()
        pass

    def test_transient_error_is_swallowed(self, connector):
        """Non-auth errors are logged and return empty results."""
        # TODO: Mock a transient failure
        pass
