"""Tests for src.connectors.meta -- MetaConnector.

Covers construction, every public method, private helpers, and error handling.
All Meta Marketing API calls are mocked; no network traffic is needed.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

# Fake credentials used for explicit-credential tests.
_FAKE_CREDENTIALS = {
    "access_token": "test-access-token",
    "app_id": "test-app-id",
    "app_secret": "test-app-secret",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_api():
    """Return a MagicMock standing in for FacebookAdsApi."""
    return MagicMock()


@pytest.fixture()
def connector(mock_api):
    """Build a MetaConnector with a mocked API instance."""
    with patch(
        "src.connectors.meta.FacebookAdsApi.init",
        return_value=mock_api,
    ):
        from src.connectors.meta import MetaConnector

        conn = MetaConnector(credentials=_FAKE_CREDENTIALS)
    conn._mock_api = mock_api
    return conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_facebook_request_error(error_code: int, message: str = "error"):
    """Build a mock FacebookRequestError with the given error code."""
    from facebook_business.exceptions import FacebookRequestError

    exc = MagicMock(spec=FacebookRequestError)
    exc.api_error_code.return_value = error_code
    exc.api_error_subcode.return_value = 0
    exc.api_error_message.return_value = message
    # Make it a real instance-ish so isinstance checks pass if needed,
    # but since code catches FacebookRequestError and we raise MagicMock,
    # we use a real exception subclass instead.
    real_exc = FacebookRequestError.__new__(FacebookRequestError)
    real_exc.api_error_code = lambda: error_code
    real_exc.api_error_subcode = lambda: 0
    real_exc.api_error_message = lambda: message
    real_exc._body = ""
    real_exc._message = message
    return real_exc


def _make_mock_ad_account_object(**fields):
    """Create a mock that behaves like an SDK AdAccount/User/Campaign object.

    The SDK objects support dict-like .get() access.
    """
    obj = MagicMock()
    obj.get = lambda key, default="": fields.get(key, default)
    obj.__getitem__ = lambda self_inner, key: fields[key]
    obj.__contains__ = lambda self_inner, key: key in fields
    return obj


def _make_mock_insights_row(**fields):
    """Create a mock AdsInsights row supporting .get() access."""
    obj = MagicMock()
    obj.get = lambda key, default=None: fields.get(key, default)
    obj.__getitem__ = lambda self_inner, key: fields[key]
    obj.__contains__ = lambda self_inner, key: key in fields
    return obj


# ===========================================================================
# 1. Construction
# ===========================================================================


class TestConstruction:
    """MetaConnector.__init__."""

    def test_explicit_credentials(self, mock_api):
        with patch(
            "src.connectors.meta.FacebookAdsApi.init",
            return_value=mock_api,
        ) as mock_init:
            from src.connectors.meta import MetaConnector

            MetaConnector(credentials=_FAKE_CREDENTIALS)

        mock_init.assert_called_once_with(
            app_id="test-app-id",
            app_secret="test-app-secret",
            access_token="test-access-token",
            api_version=MetaConnector.API_VERSION,
        )

    def test_fallback_to_settings(self, mock_api):
        with (
            patch(
                "src.connectors.meta.FacebookAdsApi.init",
                return_value=mock_api,
            ) as mock_init,
            patch("src.connectors.meta.settings") as mock_settings,
        ):
            mock_settings.meta_access_token = "settings-access-token"
            mock_settings.meta_app_id = "settings-app-id"
            mock_settings.meta_app_secret = "settings-app-secret"

            from src.connectors.meta import MetaConnector

            MetaConnector()  # no credentials argument

        mock_init.assert_called_once_with(
            app_id="settings-app-id",
            app_secret="settings-app-secret",
            access_token="settings-access-token",
            api_version=MetaConnector.API_VERSION,
        )


# ===========================================================================
# 2. get_ad_accounts
# ===========================================================================


class TestGetAdAccounts:
    def test_returns_account_dicts(self, connector):
        mock_acct = _make_mock_ad_account_object(
            id="act_111",
            name="Test Account",
            account_status=1,
            currency="USD",
            timezone_name="America/New_York",
        )

        with patch("src.connectors.meta.User") as mock_user_cls:
            mock_user_instance = MagicMock()
            mock_user_instance.get_ad_accounts.return_value = [mock_acct]
            mock_user_cls.return_value = mock_user_instance

            result = connector.get_ad_accounts()

        assert len(result) == 1
        assert result[0]["id"] == "act_111"
        assert result[0]["name"] == "Test Account"
        assert result[0]["currency"] == "USD"
        assert result[0]["timezone_name"] == "America/New_York"

    def test_empty_response(self, connector):
        with patch("src.connectors.meta.User") as mock_user_cls:
            mock_user_instance = MagicMock()
            mock_user_instance.get_ad_accounts.return_value = []
            mock_user_cls.return_value = mock_user_instance

            result = connector.get_ad_accounts()

        assert result == []

    def test_facebook_request_error_returns_empty(self, connector):
        exc = _make_facebook_request_error(error_code=1, message="transient")

        with patch("src.connectors.meta.User") as mock_user_cls:
            mock_user_instance = MagicMock()
            mock_user_instance.get_ad_accounts.side_effect = exc
            mock_user_cls.return_value = mock_user_instance

            result = connector.get_ad_accounts()

        assert result == []


# ===========================================================================
# 3. get_account_info
# ===========================================================================


class TestGetAccountInfo:
    def test_returns_account_info_dict(self, connector):
        mock_info = _make_mock_ad_account_object(
            id="act_999",
            name="My Account",
            currency="EUR",
            timezone_name="Europe/Berlin",
            account_status=1,
            spend_cap="50000",
            business_name="Acme Corp",
        )

        with patch.object(connector, "_get_account") as mock_get:
            mock_account = MagicMock()
            mock_account.api_get.return_value = mock_info
            mock_get.return_value = mock_account

            result = connector.get_account_info("act_999")

        assert result is not None
        assert result["id"] == "act_999"
        assert result["name"] == "My Account"
        assert result["currency"] == "EUR"
        assert result["timezone"] == "Europe/Berlin"
        assert result["account_status"] == 1
        assert result["spend_cap"] == "50000"
        assert result["business_name"] == "Acme Corp"

    def test_returns_none_on_failure(self, connector):
        exc = _make_facebook_request_error(error_code=1, message="not found")

        with patch.object(connector, "_get_account") as mock_get:
            mock_account = MagicMock()
            mock_account.api_get.side_effect = exc
            mock_get.return_value = mock_account

            result = connector.get_account_info("act_999")

        assert result is None


# ===========================================================================
# 4. get_campaigns
# ===========================================================================


class TestGetCampaigns:
    def _campaign_obj(
        self,
        campaign_id="100",
        name="Brand Campaign",
        objective="OUTCOME_SALES",
        status="ACTIVE",
        daily_budget="5000",
        lifetime_budget=None,
        bid_strategy="LOWEST_COST_WITHOUT_CAP",
    ):
        return _make_mock_ad_account_object(
            id=campaign_id,
            name=name,
            objective=objective,
            status=status,
            daily_budget=daily_budget,
            lifetime_budget=lifetime_budget,
            bid_strategy=bid_strategy,
        )

    def test_objective_mapping(self, connector):
        camp = self._campaign_obj(objective="OUTCOME_SALES")

        with patch.object(connector, "_get_account") as mock_get:
            mock_account = MagicMock()
            mock_account.get_campaigns.return_value = [camp]
            mock_get.return_value = mock_account

            result = connector.get_campaigns("act_123")

        assert result[0]["objective"] == "sales"

    def test_budget_conversion_from_string_cents(self, connector):
        # "5000" in Meta = $50.00
        camp = self._campaign_obj(daily_budget="5000", lifetime_budget="100000")

        with patch.object(connector, "_get_account") as mock_get:
            mock_account = MagicMock()
            mock_account.get_campaigns.return_value = [camp]
            mock_get.return_value = mock_account

            result = connector.get_campaigns("act_123")

        assert result[0]["daily_budget"] == 50.0
        assert result[0]["lifetime_budget"] == 1000.0

    def test_deleted_campaigns_excluded_via_filter_param(self, connector):
        """Verify the filtering param excludes DELETED campaigns."""
        camp = self._campaign_obj(status="ACTIVE")

        with patch.object(connector, "_get_account") as mock_get:
            mock_account = MagicMock()
            mock_account.get_campaigns.return_value = [camp]
            mock_get.return_value = mock_account

            connector.get_campaigns("act_123")

        # Verify the filter was passed to get_campaigns
        call_kwargs = mock_account.get_campaigns.call_args
        if "params" in call_kwargs[1]:
            params = call_kwargs[1]["params"]
        elif len(call_kwargs[0]) > 1:
            params = call_kwargs[0][1]
        else:
            params = call_kwargs[1].get("params")
        filtering = params["filtering"]
        assert any(
            f["field"] == "effective_status"
            and f["operator"] == "NOT_IN"
            and "DELETED" in f["value"]
            for f in filtering
        )

    def test_status_lowercased(self, connector):
        camp = self._campaign_obj(status="PAUSED")

        with patch.object(connector, "_get_account") as mock_get:
            mock_account = MagicMock()
            mock_account.get_campaigns.return_value = [camp]
            mock_get.return_value = mock_account

            result = connector.get_campaigns("act_123")

        assert result[0]["status"] == "paused"

    def test_empty_on_failure(self, connector):
        exc = _make_facebook_request_error(error_code=1, message="transient")

        with patch.object(connector, "_get_account") as mock_get:
            mock_account = MagicMock()
            mock_account.get_campaigns.side_effect = exc
            mock_get.return_value = mock_account

            result = connector.get_campaigns("act_123")

        assert result == []


# ===========================================================================
# 5. get_campaign_metrics
# ===========================================================================


class TestGetCampaignMetrics:
    def _insights_row(self, **overrides):
        base = {
            "campaign_id": "100",
            "campaign_name": "Brand",
            "impressions": "1000",
            "clicks": "50",
            "spend": "123.45",
            "actions": [
                {"action_type": "purchase", "value": "3"},
                {"action_type": "link_click", "value": "45"},
            ],
            "action_values": [
                {"action_type": "purchase", "value": "450.00"},
            ],
            "cpm": "5.50",
            "cpp": "6.00",
            "frequency": "1.2",
            "reach": "800",
            "date_start": "2025-01-15",
            "date_stop": "2025-01-15",
        }
        base.update(overrides)
        return _make_mock_insights_row(**base)

    def test_spend_string_to_float(self, connector):
        row = self._insights_row(spend="99.99")

        with patch.object(connector, "_get_account") as mock_get:
            mock_account = MagicMock()
            mock_account.get_insights.return_value = [row]
            mock_get.return_value = mock_account

            result = connector.get_campaign_metrics(
                "act_123", "100", date(2025, 1, 15), date(2025, 1, 15)
            )

        assert result[0]["spend"] == 99.99

    def test_actions_array_extraction(self, connector):
        row = self._insights_row(
            actions=[
                {"action_type": "purchase", "value": "5"},
                {"action_type": "lead", "value": "2"},
                {"action_type": "link_click", "value": "100"},
            ],
        )

        with patch.object(connector, "_get_account") as mock_get:
            mock_account = MagicMock()
            mock_account.get_insights.return_value = [row]
            mock_get.return_value = mock_account

            result = connector.get_campaign_metrics(
                "act_123", "100", date(2025, 1, 15), date(2025, 1, 15)
            )

        # purchase (5) + lead (2) = 7 conversions
        assert result[0]["conversions"] == 7.0

    def test_empty_on_failure(self, connector):
        exc = _make_facebook_request_error(error_code=1, message="transient")

        with patch.object(connector, "_get_account") as mock_get:
            mock_account = MagicMock()
            mock_account.get_insights.side_effect = exc
            mock_get.return_value = mock_account

            result = connector.get_campaign_metrics("act_123", "100", "2025-01-01", "2025-01-31")

        assert result == []


# ===========================================================================
# 6. get_account_metrics
# ===========================================================================


class TestGetAccountMetrics:
    def test_calls_with_level_campaign(self, connector):
        with patch.object(connector, "_get_account") as mock_get:
            mock_account = MagicMock()
            mock_account.get_insights.return_value = []
            mock_get.return_value = mock_account

            connector.get_account_metrics("act_123", date(2025, 1, 1), date(2025, 1, 31))

        call_kwargs = mock_account.get_insights.call_args
        kwargs = call_kwargs[1]
        params = kwargs.get("params")
        assert params["level"] == "campaign"

    def test_empty_on_failure(self, connector):
        exc = _make_facebook_request_error(error_code=1, message="transient")

        with patch.object(connector, "_get_account") as mock_get:
            mock_account = MagicMock()
            mock_account.get_insights.side_effect = exc
            mock_get.return_value = mock_account

            result = connector.get_account_metrics("act_123", "2025-01-01", "2025-01-31")

        assert result == []


# ===========================================================================
# 7. get_campaign_insights
# ===========================================================================


class TestGetCampaignInsights:
    def test_breakdown_param_passed(self, connector):
        mock_row = _make_mock_insights_row(
            campaign_id="100",
            campaign_name="Brand",
            impressions="500",
            clicks="20",
            spend="50.00",
            actions=None,
            action_values=None,
            cpm="3.00",
            frequency="1.1",
            reach="400",
            date_start="2025-01-01",
            date_stop="2025-01-31",
            age="25-34",
        )

        with patch.object(connector, "_get_account") as mock_get:
            mock_account = MagicMock()
            mock_account.get_insights.return_value = [mock_row]
            mock_get.return_value = mock_account

            result = connector.get_campaign_insights("act_123", "100", breakdowns=["age"])

        # Verify breakdowns was passed in params
        call_kwargs = mock_account.get_insights.call_args
        kwargs = call_kwargs[1]
        params = kwargs.get("params")
        assert params["breakdowns"] == ["age"]

        # Verify breakdown dimension is in result
        assert result[0]["age"] == "25-34"

    def test_empty_on_failure(self, connector):
        exc = _make_facebook_request_error(error_code=1, message="transient")

        with patch.object(connector, "_get_account") as mock_get:
            mock_account = MagicMock()
            mock_account.get_insights.side_effect = exc
            mock_get.return_value = mock_account

            result = connector.get_campaign_insights("act_123", "100")

        assert result == []


# ===========================================================================
# 8. get_account_activity
# ===========================================================================


class TestGetAccountActivity:
    def test_detects_spend_change(self, connector):
        """Mock two period comparisons and verify activity detection."""
        current_metrics = [
            {
                "campaign_id": "100",
                "campaign_name": "Brand",
                "spend": 150.0,
                "impressions": 1000,
                "clicks": 50,
                "conversions": 5.0,
            },
        ]
        prev_metrics = [
            {
                "campaign_id": "100",
                "campaign_name": "Brand",
                "spend": 100.0,
                "impressions": 800,
                "clicks": 40,
                "conversions": 4.0,
            },
        ]

        with patch.object(
            connector,
            "get_account_metrics",
            side_effect=[current_metrics, prev_metrics],
        ):
            result = connector.get_account_activity("act_123", days=7)

        # 50% spend increase -> should be flagged
        assert len(result) == 1
        assert result[0]["type"] == "spend_change"
        assert result[0]["campaign_id"] == "100"
        assert result[0]["change_pct"] == 50.0

    def test_empty_on_failure(self, connector):
        exc = _make_facebook_request_error(error_code=1, message="transient")

        with patch.object(
            connector,
            "get_account_metrics",
            side_effect=exc,
        ):
            result = connector.get_account_activity("act_123")

        assert result == []


# ===========================================================================
# 9. _ensure_act_prefix
# ===========================================================================


class TestEnsureActPrefix:
    def test_adds_prefix_to_bare_id(self):
        from src.connectors.meta import MetaConnector

        assert MetaConnector._ensure_act_prefix("123") == "act_123"

    def test_preserves_existing_prefix(self):
        from src.connectors.meta import MetaConnector

        assert MetaConnector._ensure_act_prefix("act_123") == "act_123"

    def test_already_has_prefix_with_long_id(self):
        from src.connectors.meta import MetaConnector

        assert MetaConnector._ensure_act_prefix("act_9876543210") == "act_9876543210"


# ===========================================================================
# 10. _format_insights_row
# ===========================================================================


class TestFormatInsightsRow:
    def test_spend_string_decimal_conversion(self, connector):
        row = _make_mock_insights_row(
            campaign_id="100",
            campaign_name="Brand",
            impressions="1000",
            clicks="50",
            spend="123.45",
            actions=None,
            action_values=None,
            cpm="5.00",
            cpp="6.00",
            frequency="1.2",
            reach="800",
            date_start="2025-01-15",
            date_stop="2025-01-15",
        )

        result = connector._format_insights_row(row)

        assert result["spend"] == 123.45
        assert isinstance(result["spend"], float)

    def test_actions_array_to_conversions(self, connector):
        row = _make_mock_insights_row(
            campaign_id="100",
            campaign_name="Brand",
            impressions="500",
            clicks="20",
            spend="50.00",
            actions=[
                {"action_type": "purchase", "value": "3"},
                {"action_type": "lead", "value": "2"},
                {"action_type": "link_click", "value": "100"},
            ],
            action_values=[
                {"action_type": "purchase", "value": "450.00"},
            ],
            cpm="3.00",
            cpp="4.00",
            frequency="1.0",
            reach="500",
            date_start="2025-01-15",
            date_stop="2025-01-15",
        )

        result = connector._format_insights_row(row)

        # purchase (3) + lead (2) = 5 conversions
        assert result["conversions"] == 5.0

    def test_action_values_to_conversion_value(self, connector):
        row = _make_mock_insights_row(
            campaign_id="100",
            campaign_name="Brand",
            impressions="500",
            clicks="20",
            spend="50.00",
            actions=[
                {"action_type": "purchase", "value": "3"},
            ],
            action_values=[
                {"action_type": "purchase", "value": "450.00"},
                {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "200.00"},
            ],
            cpm="3.00",
            cpp="4.00",
            frequency="1.0",
            reach="500",
            date_start="2025-01-15",
            date_stop="2025-01-15",
        )

        result = connector._format_insights_row(row)

        # purchase (450) + offsite_conversion.fb_pixel_purchase (200) = 650
        assert result["conversion_value"] == 650.0

    def test_empty_actions_defaults_to_zero(self, connector):
        row = _make_mock_insights_row(
            campaign_id="100",
            campaign_name="Brand",
            impressions="500",
            clicks="20",
            spend="50.00",
            actions=None,
            action_values=None,
            cpm="3.00",
            cpp="4.00",
            frequency="1.0",
            reach="500",
            date_start="2025-01-15",
            date_stop="2025-01-15",
        )

        result = connector._format_insights_row(row)

        assert result["conversions"] == 0.0
        assert result["conversion_value"] == 0.0


# ===========================================================================
# 11. Error handling
# ===========================================================================


class TestErrorHandling:
    def test_auth_error_code_190_raises_meta_auth_error(self, connector):
        from src.connectors.meta import MetaAuthError

        exc = _make_facebook_request_error(error_code=190, message="invalid token")

        with pytest.raises(MetaAuthError, match="auth error"):
            connector._handle_facebook_error(exc, "test_op")

    def test_auth_error_code_102_raises_meta_auth_error(self, connector):
        from src.connectors.meta import MetaAuthError

        exc = _make_facebook_request_error(error_code=102, message="session expired")

        with pytest.raises(MetaAuthError, match="auth error"):
            connector._handle_facebook_error(exc, "test_op")

    def test_non_auth_error_swallowed(self, connector):
        exc = _make_facebook_request_error(error_code=1, message="transient error")

        # Should NOT raise -- non-auth errors are logged and swallowed
        connector._handle_facebook_error(exc, "test_op")
