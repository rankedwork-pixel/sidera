"""Tests for src.connectors.google_ads — GoogleAdsConnector.

Covers construction, every public method, and all private helpers.
All Google Ads API calls are mocked; no network traffic is needed.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

# We patch GoogleAdsClient.load_from_dict and settings at the module level
# so importing the connector never actually calls the real API.
_FAKE_CREDENTIALS = {
    "developer_token": "test-dev-token",
    "client_id": "test-client-id",
    "client_secret": "test-client-secret",
    "refresh_token": "test-refresh-token",
    "login_customer_id": "1234567890",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_client():
    """Return a MagicMock standing in for GoogleAdsClient."""
    return MagicMock()


@pytest.fixture()
def connector(mock_client):
    """Build a GoogleAdsConnector with a mocked client."""
    with patch(
        "src.connectors.google_ads.GoogleAdsClient.load_from_dict",
        return_value=mock_client,
    ):
        from src.connectors.google_ads import GoogleAdsConnector

        conn = GoogleAdsConnector(credentials=_FAKE_CREDENTIALS)
    # Expose the mock client for further stubbing in each test
    conn._mock_client = mock_client
    return conn


@pytest.fixture()
def ga_service(connector):
    """Return the mock GoogleAdsService obtained via get_service("GoogleAdsService")."""
    svc = MagicMock()
    connector._mock_client.get_service.return_value = svc
    return svc


@pytest.fixture()
def customer_service(connector):
    """Return the mock CustomerService obtained via get_service("CustomerService")."""
    svc = MagicMock()
    connector._mock_client.get_service.return_value = svc
    return svc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proto_row(nested: dict) -> MagicMock:
    """Create a mock protobuf row that _proto_to_dict can handle.

    ``nested`` should mirror the dict that ``MessageToDict`` would return,
    e.g. {"campaign": {"id": "111", "name": "Brand"}, "metrics": {"clicks": "42"}}.
    """
    row = MagicMock()
    row._pb = MagicMock()
    return row, nested


def _make_batch(rows: list[MagicMock]) -> MagicMock:
    """Wrap a list of mock rows in a search_stream batch."""
    batch = MagicMock()
    batch.results = rows
    return batch


# ===========================================================================
# 1. Construction
# ===========================================================================


class TestConstruction:
    """GoogleAdsConnector.__init__."""

    def test_explicit_credentials(self, mock_client):
        with patch(
            "src.connectors.google_ads.GoogleAdsClient.load_from_dict",
            return_value=mock_client,
        ) as mock_load:
            from src.connectors.google_ads import GoogleAdsConnector

            GoogleAdsConnector(credentials=_FAKE_CREDENTIALS)

        # load_from_dict must receive a config built from our credentials
        call_args = mock_load.call_args[0][0]
        assert call_args["developer_token"] == "test-dev-token"
        assert call_args["client_id"] == "test-client-id"
        assert call_args["refresh_token"] == "test-refresh-token"
        assert call_args["login_customer_id"] == "1234567890"
        assert call_args["use_proto_plus"] is False

    def test_fallback_to_settings(self, mock_client):
        with (
            patch(
                "src.connectors.google_ads.GoogleAdsClient.load_from_dict",
                return_value=mock_client,
            ) as mock_load,
            patch(
                "src.connectors.google_ads.settings",
            ) as mock_settings,
        ):
            mock_settings.google_ads_developer_token = "settings-dev-token"
            mock_settings.google_ads_client_id = "settings-client-id"
            mock_settings.google_ads_client_secret = "settings-client-secret"
            mock_settings.google_ads_refresh_token = "settings-refresh-token"
            mock_settings.google_ads_login_customer_id = "settings-login-id"

            from src.connectors.google_ads import GoogleAdsConnector

            GoogleAdsConnector()  # no credentials argument

        call_args = mock_load.call_args[0][0]
        assert call_args["developer_token"] == "settings-dev-token"
        assert call_args["client_id"] == "settings-client-id"
        assert call_args["login_customer_id"] == "settings-login-id"

    def test_invalid_credentials_raises_auth_error(self):
        with patch(
            "src.connectors.google_ads.GoogleAdsClient.load_from_dict",
            side_effect=ValueError("bad config"),
        ):
            from src.connectors.google_ads import (
                GoogleAdsAuthError,
                GoogleAdsConnector,
            )

            with pytest.raises(GoogleAdsAuthError, match="Failed to create"):
                GoogleAdsConnector(credentials={"developer_token": ""})


# ===========================================================================
# 2. get_accessible_accounts
# ===========================================================================


class TestGetAccessibleAccounts:
    def test_returns_customer_ids_from_resource_names(self, connector, customer_service):
        response = MagicMock()
        response.resource_names = [
            "customers/1111111111",
            "customers/2222222222",
            "customers/3333333333",
        ]
        customer_service.list_accessible_customers.return_value = response

        result = connector.get_accessible_accounts()

        assert result == ["1111111111", "2222222222", "3333333333"]

    def test_empty_response_returns_empty_list(self, connector, customer_service):
        response = MagicMock()
        response.resource_names = []
        customer_service.list_accessible_customers.return_value = response

        assert connector.get_accessible_accounts() == []

    def test_google_ads_exception_returns_empty_list(self, connector, customer_service):
        from google.ads.googleads.errors import GoogleAdsException

        # Build a minimal GoogleAdsException mock
        failure = MagicMock()
        error = MagicMock()
        error.error_code = "INTERNAL_ERROR"
        error.message = "transient"
        failure.errors = [error]

        exc = GoogleAdsException.__new__(GoogleAdsException)
        exc.failure = failure
        exc.request_id = "req-abc"
        exc.error = None

        customer_service.list_accessible_customers.side_effect = exc

        assert connector.get_accessible_accounts() == []


# ===========================================================================
# 3. get_account_info
# ===========================================================================


class TestGetAccountInfo:
    def test_returns_account_dict(self, connector):
        fake_row = {
            "customer_id": "9876543210",
            "customer_descriptive_name": "Acme Corp",
            "customer_currency_code": "USD",
            "customer_time_zone": "America/New_York",
        }
        with patch.object(connector, "_execute_query", return_value=[fake_row]):
            result = connector.get_account_info("9876543210")

        assert result == {
            "id": "9876543210",
            "name": "Acme Corp",
            "descriptive_name": "Acme Corp",
            "currency": "USD",
            "timezone": "America/New_York",
        }

    def test_returns_none_when_query_fails(self, connector):
        with patch.object(connector, "_execute_query", return_value=None):
            assert connector.get_account_info("9876543210") is None

    def test_returns_none_when_empty_rows(self, connector):
        with patch.object(connector, "_execute_query", return_value=[]):
            assert connector.get_account_info("9876543210") is None


# ===========================================================================
# 4. get_campaigns
# ===========================================================================


class TestGetCampaigns:
    def _campaign_row(
        self,
        campaign_id="100",
        name="Brand Search",
        status="ENABLED",
        channel_type="SEARCH",
        bid_strategy="TARGET_CPA",
        budget_micros=6000000,
        delivery="STANDARD",
    ):
        return {
            "campaign_id": campaign_id,
            "campaign_name": name,
            "campaign_status": status,
            "campaign_advertising_channel_type": channel_type,
            "campaign_bidding_strategy_type": bid_strategy,
            "campaign_budget_amount_micros": budget_micros,
            "campaign_budget_delivery_method": delivery,
        }

    def test_maps_campaign_type_search(self, connector):
        with patch.object(
            connector,
            "_execute_query",
            return_value=[self._campaign_row(channel_type="SEARCH")],
        ):
            result = connector.get_campaigns("111")

        assert result[0]["type"] == "search"

    def test_maps_campaign_type_pmax(self, connector):
        with patch.object(
            connector,
            "_execute_query",
            return_value=[self._campaign_row(channel_type="PERFORMANCE_MAX")],
        ):
            result = connector.get_campaigns("111")

        assert result[0]["type"] == "pmax"

    def test_maps_campaign_type_display(self, connector):
        with patch.object(
            connector,
            "_execute_query",
            return_value=[self._campaign_row(channel_type="DISPLAY")],
        ):
            result = connector.get_campaigns("111")

        assert result[0]["type"] == "display"

    def test_unmapped_campaign_type_lowered(self, connector):
        with patch.object(
            connector,
            "_execute_query",
            return_value=[self._campaign_row(channel_type="SMART")],
        ):
            result = connector.get_campaigns("111")

        assert result[0]["type"] == "smart"

    def test_budget_micros_conversion(self, connector):
        with patch.object(
            connector,
            "_execute_query",
            return_value=[self._campaign_row(budget_micros=6000000)],
        ):
            result = connector.get_campaigns("111")

        assert result[0]["daily_budget"] == 6.0

    def test_budget_micros_zero(self, connector):
        with patch.object(
            connector,
            "_execute_query",
            return_value=[self._campaign_row(budget_micros=0)],
        ):
            result = connector.get_campaigns("111")

        assert result[0]["daily_budget"] == 0.0

    def test_status_lowercased(self, connector):
        with patch.object(
            connector,
            "_execute_query",
            return_value=[self._campaign_row(status="PAUSED")],
        ):
            result = connector.get_campaigns("111")

        assert result[0]["status"] == "paused"

    def test_campaign_output_shape(self, connector):
        with patch.object(
            connector,
            "_execute_query",
            return_value=[self._campaign_row()],
        ):
            result = connector.get_campaigns("111")

        c = result[0]
        assert set(c.keys()) == {
            "id",
            "name",
            "type",
            "status",
            "daily_budget",
            "bid_strategy",
            "platform_data",
        }
        assert c["platform_data"]["channel_type"] == "SEARCH"
        assert c["platform_data"]["delivery_method"] == "STANDARD"

    def test_multiple_campaigns(self, connector):
        rows = [
            self._campaign_row(campaign_id="1", name="Alpha"),
            self._campaign_row(campaign_id="2", name="Beta"),
        ]
        with patch.object(connector, "_execute_query", return_value=rows):
            result = connector.get_campaigns("111")

        assert len(result) == 2
        assert result[0]["name"] == "Alpha"
        assert result[1]["name"] == "Beta"

    def test_empty_on_failure(self, connector):
        with patch.object(connector, "_execute_query", return_value=None):
            assert connector.get_campaigns("111") == []

    def test_budget_micros_none_treated_as_zero(self, connector):
        with patch.object(
            connector,
            "_execute_query",
            return_value=[self._campaign_row(budget_micros=None)],
        ):
            result = connector.get_campaigns("111")

        assert result[0]["daily_budget"] == 0.0


# ===========================================================================
# 5. get_campaign_metrics / get_account_metrics
# ===========================================================================


class TestGetCampaignMetrics:
    def _metric_row(self, **overrides):
        base = {
            "campaign_id": "100",
            "campaign_name": "Brand",
            "segments_date": "2025-01-15",
            "metrics_impressions": 1000,
            "metrics_clicks": 50,
            "metrics_cost_micros": 5000000,
            "metrics_conversions": 3.0,
            "metrics_conversions_value": 150.0,
            "metrics_search_impression_share": 0.65,
            "metrics_search_top_impression_percentage": 0.40,
            "metrics_search_absolute_top_impression_percentage": 0.20,
            "metrics_average_cpc": 100000,
            "metrics_cost_per_conversion": 1666667,
            "metrics_interaction_rate": 0.05,
            "metrics_all_conversions": 4.0,
            "metrics_view_through_conversions": 1,
        }
        base.update(overrides)
        return base

    def test_returns_formatted_rows(self, connector):
        with patch.object(
            connector,
            "_execute_query",
            return_value=[self._metric_row()],
        ):
            result = connector.get_campaign_metrics(
                "111", "100", date(2025, 1, 15), date(2025, 1, 15)
            )

        assert len(result) == 1
        row = result[0]
        assert row["campaign_id"] == "100"
        assert row["campaign_name"] == "Brand"
        assert row["date"] == "2025-01-15"
        # cost_micros 5000000 -> 5.0
        assert row["cost"] == 5.0
        assert row["impressions"] == 1000
        assert row["clicks"] == 50

    def test_date_objects_and_strings_work(self, connector):
        with patch.object(connector, "_execute_query", return_value=[]) as mock_eq:
            # date objects
            connector.get_campaign_metrics("111", "100", date(2025, 1, 1), date(2025, 1, 31))
            query1 = mock_eq.call_args[0][1]
            assert "'2025-01-01'" in query1
            assert "'2025-01-31'" in query1

        with patch.object(connector, "_execute_query", return_value=[]) as mock_eq:
            # string dates
            connector.get_campaign_metrics("111", "100", "2025-02-01", "2025-02-28")
            query2 = mock_eq.call_args[0][1]
            assert "'2025-02-01'" in query2
            assert "'2025-02-28'" in query2

    def test_empty_on_failure(self, connector):
        with patch.object(connector, "_execute_query", return_value=None):
            assert connector.get_campaign_metrics("111", "100", "2025-01-01", "2025-01-31") == []


class TestGetAccountMetrics:
    def test_returns_formatted_rows(self, connector):
        row = {
            "campaign_id": "200",
            "campaign_name": "Display",
            "segments_date": "2025-01-20",
            "metrics_impressions": 5000,
            "metrics_clicks": 100,
            "metrics_cost_micros": 10000000,
            "metrics_conversions": 8.0,
            "metrics_conversions_value": 400.0,
        }
        with patch.object(connector, "_execute_query", return_value=[row]):
            result = connector.get_account_metrics("111", date(2025, 1, 20), date(2025, 1, 20))

        assert len(result) == 1
        assert result[0]["campaign_id"] == "200"
        assert result[0]["cost"] == 10.0  # 10_000_000 micros

    def test_date_objects_and_strings_work(self, connector):
        with patch.object(connector, "_execute_query", return_value=[]) as mock_eq:
            connector.get_account_metrics("111", date(2025, 3, 1), date(2025, 3, 31))
            query = mock_eq.call_args[0][1]
            assert "'2025-03-01'" in query
            assert "'2025-03-31'" in query

    def test_empty_on_failure(self, connector):
        with patch.object(connector, "_execute_query", return_value=None):
            assert connector.get_account_metrics("111", "2025-01-01", "2025-01-31") == []


# ===========================================================================
# 6. get_change_history
# ===========================================================================


class TestGetChangeHistory:
    def test_returns_change_events(self, connector):
        row = {
            "change_event_change_date_time": "2025-01-10T14:30:00",
            "change_event_change_resource_type": "CAMPAIGN",
            "change_event_resource_change_operation": "UPDATE",
            "change_event_changed_fields": "budget.amount_micros",
            "change_event_old_resource": {"budget": {"amount_micros": 5000000}},
            "change_event_new_resource": {"budget": {"amount_micros": 8000000}},
            "campaign_id": "100",
            "campaign_name": "Brand",
        }
        with patch.object(connector, "_execute_query", return_value=[row]):
            result = connector.get_change_history("111", days=7)

        assert len(result) == 1
        c = result[0]
        assert c["change_date_time"] == "2025-01-10T14:30:00"
        assert c["change_resource_type"] == "CAMPAIGN"
        assert c["resource_change_operation"] == "UPDATE"
        assert c["changed_fields"] == "budget.amount_micros"
        assert c["campaign_id"] == "100"
        assert c["campaign_name"] == "Brand"
        assert c["old_resource"]["budget"]["amount_micros"] == 5000000
        assert c["new_resource"]["budget"]["amount_micros"] == 8000000

    def test_empty_on_failure(self, connector):
        with patch.object(connector, "_execute_query", return_value=None):
            assert connector.get_change_history("111") == []


# ===========================================================================
# 7. get_recommendations
# ===========================================================================


class TestGetRecommendations:
    def test_returns_recommendations(self, connector):
        row = {
            "recommendation_resource_name": "customers/111/recommendations/abc",
            "recommendation_type": "KEYWORD",
            "recommendation_impact": {"base_metrics": {"impressions": 1000}},
            "recommendation_campaign": "customers/111/campaigns/200",
            "recommendation_dismissed": False,
        }
        with patch.object(connector, "_execute_query", return_value=[row]):
            result = connector.get_recommendations("111")

        assert len(result) == 1
        r = result[0]
        assert r["id"] == "customers/111/recommendations/abc"
        assert r["type"] == "KEYWORD"
        assert r["impact"] == {"base_metrics": {"impressions": 1000}}
        assert r["campaign_id"] == "200"
        assert r["dismissed"] is False

    def test_campaign_id_extracted_from_resource_name(self, connector):
        row = {
            "recommendation_resource_name": "customers/111/recommendations/xyz",
            "recommendation_type": "TARGET_CPA_OPT_IN",
            "recommendation_impact": {},
            "recommendation_campaign": "customers/111/campaigns/9999",
            "recommendation_dismissed": False,
        }
        with patch.object(connector, "_execute_query", return_value=[row]):
            result = connector.get_recommendations("111")

        assert result[0]["campaign_id"] == "9999"

    def test_no_campaign_resource_gives_empty_campaign_id(self, connector):
        row = {
            "recommendation_resource_name": "customers/111/recommendations/xyz",
            "recommendation_type": "SITELINK",
            "recommendation_impact": {},
            "recommendation_campaign": "",
            "recommendation_dismissed": False,
        }
        with patch.object(connector, "_execute_query", return_value=[row]):
            result = connector.get_recommendations("111")

        assert result[0]["campaign_id"] == ""

    def test_impact_string_wrapped_in_dict(self, connector):
        row = {
            "recommendation_resource_name": "customers/111/recommendations/xyz",
            "recommendation_type": "KEYWORD",
            "recommendation_impact": "HIGH",
            "recommendation_campaign": "",
            "recommendation_dismissed": False,
        }
        with patch.object(connector, "_execute_query", return_value=[row]):
            result = connector.get_recommendations("111")

        assert result[0]["impact"] == {"raw": "HIGH"}

    def test_empty_on_failure(self, connector):
        with patch.object(connector, "_execute_query", return_value=None):
            assert connector.get_recommendations("111") == []


# ===========================================================================
# 8. _proto_to_dict
# ===========================================================================


class TestProtoToDict:
    def test_flattens_nested_dict(self, connector):
        nested = {
            "campaign": {"id": "111", "name": "Brand Search"},
            "metrics": {"clicks": "42", "impressions": "1000"},
        }
        row = MagicMock()
        row._pb = MagicMock()

        with patch("src.connectors.google_ads.MessageToDict", return_value=nested):
            result = connector._proto_to_dict(row)

        assert result["campaign_id"] == "111"
        assert result["campaign_name"] == "Brand Search"
        assert result["metrics_clicks"] == "42"
        assert result["metrics_impressions"] == "1000"

    def test_camel_case_to_snake_case(self, connector):
        nested = {
            "customer": {
                "descriptiveName": "Acme Corp",
                "currencyCode": "USD",
                "timeZone": "America/New_York",
            }
        }
        row = MagicMock()
        row._pb = MagicMock()

        with patch("src.connectors.google_ads.MessageToDict", return_value=nested):
            result = connector._proto_to_dict(row)

        assert "customer_descriptive_name" in result
        assert "customer_currency_code" in result
        assert "customer_time_zone" in result

    def test_non_dict_fields_kept_as_is(self, connector):
        nested = {
            "campaign": {"id": "111"},
            "topLevelScalar": 42,
        }
        row = MagicMock()
        row._pb = MagicMock()

        with patch("src.connectors.google_ads.MessageToDict", return_value=nested):
            result = connector._proto_to_dict(row)

        # Non-dict values stay under their original key
        assert result["topLevelScalar"] == 42
        assert result["campaign_id"] == "111"

    def test_uses_pb_attribute_when_present(self, connector):
        row = MagicMock()
        row._pb = MagicMock()

        with patch("src.connectors.google_ads.MessageToDict", return_value={}) as mock_mtd:
            connector._proto_to_dict(row)

        mock_mtd.assert_called_once_with(row._pb)

    def test_uses_row_directly_when_no_pb(self, connector):
        row = MagicMock(spec=[])  # no _pb attribute

        with patch("src.connectors.google_ads.MessageToDict", return_value={}) as mock_mtd:
            connector._proto_to_dict(row)

        mock_mtd.assert_called_once_with(row)


# ===========================================================================
# 9. _format_metric_row
# ===========================================================================


class TestFormatMetricRow:
    def test_micros_fields_divided(self, connector):
        """Fields whose flat key contains 'micros' get divided by 1,000,000.

        The code checks ``"micros" in flat_key`` where flat_key is the
        Google Ads API field name with dots replaced by underscores.
        So ``metrics.cost_micros`` -> ``metrics_cost_micros`` is divided,
        but ``metrics.average_cpc`` -> ``metrics_average_cpc`` is NOT
        (it passes through as-is because the flat key has no "micros").
        """
        row = {
            "campaign_id": "100",
            "campaign_name": "Brand",
            "segments_date": "2025-01-15",
            "metrics_cost_micros": 5000000,
            "metrics_average_cpc": 100000,
            "metrics_cost_per_conversion": 1666667,
            "metrics_impressions": 500,
            "metrics_clicks": 25,
            "metrics_conversions": 3.0,
            "metrics_conversions_value": 150.0,
        }
        result = connector._format_metric_row(row)

        # cost_micros flat key contains "micros" -> divided by 1,000,000
        assert result["cost"] == 5.0

        # average_cpc flat key is "metrics_average_cpc" (no "micros") -> pass-through
        assert result["avg_cpc_micros"] == 100000

        # cost_per_conversion: flat key has no "micros" -> pass-through
        assert result["cost_per_conversion_micros"] == 1666667

    def test_non_micros_fields_pass_through(self, connector):
        row = {
            "campaign_id": "100",
            "campaign_name": "Brand",
            "segments_date": "2025-01-15",
            "metrics_impressions": 1234,
            "metrics_clicks": 56,
            "metrics_conversions": 7.0,
            "metrics_conversions_value": 350.0,
            "metrics_search_impression_share": 0.65,
            "metrics_interaction_rate": 0.05,
        }
        result = connector._format_metric_row(row)

        assert result["impressions"] == 1234
        assert result["clicks"] == 56
        assert result["conversions"] == 7.0
        assert result["conversion_value"] == 350.0
        assert result["search_impression_share"] == 0.65
        assert result["interaction_rate"] == 0.05

    def test_missing_values_default_to_zero(self, connector):
        row = {
            "campaign_id": "100",
            "campaign_name": "Brand",
            "segments_date": "2025-01-15",
            # No metric keys at all
        }
        result = connector._format_metric_row(row)

        # Integer-like metrics default to 0
        assert result["impressions"] == 0
        assert result["clicks"] == 0
        assert result["conversions"] == 0
        assert result["all_conversions"] == 0
        assert result["view_through_conversions"] == 0
        # Monetary/rate metrics default to 0.0
        assert result["cost"] == 0.0

    def test_output_has_campaign_and_date_fields(self, connector):
        row = {
            "campaign_id": "300",
            "campaign_name": "Shopping",
            "segments_date": "2025-02-01",
        }
        result = connector._format_metric_row(row)

        assert result["campaign_id"] == "300"
        assert result["campaign_name"] == "Shopping"
        assert result["date"] == "2025-02-01"


# ===========================================================================
# 10. _handle_google_ads_exception
# ===========================================================================


class TestHandleGoogleAdsException:
    def _make_exception(self, error_code_str: str, message: str = "error"):
        from google.ads.googleads.errors import GoogleAdsException

        failure = MagicMock()
        error = MagicMock()
        error.error_code = error_code_str
        error.message = message
        failure.errors = [error]

        exc = GoogleAdsException.__new__(GoogleAdsException)
        exc.failure = failure
        exc.request_id = "req-test-123"
        exc.error = None
        return exc

    def test_auth_error_raises_google_ads_auth_error(self, connector):
        from src.connectors.google_ads import GoogleAdsAuthError

        exc = self._make_exception("AUTHENTICATION_ERROR")
        with pytest.raises(GoogleAdsAuthError, match="auth error"):
            connector._handle_google_ads_exception(exc, "test_op")

    def test_authorization_error_raises(self, connector):
        from src.connectors.google_ads import GoogleAdsAuthError

        exc = self._make_exception("AUTHORIZATION_ERROR")
        with pytest.raises(GoogleAdsAuthError):
            connector._handle_google_ads_exception(exc, "test_op")

    def test_not_whitelisted_error_raises(self, connector):
        from src.connectors.google_ads import GoogleAdsAuthError

        exc = self._make_exception("NOT_WHITELISTED_ERROR")
        with pytest.raises(GoogleAdsAuthError):
            connector._handle_google_ads_exception(exc, "test_op")

    def test_non_auth_error_is_swallowed(self, connector):
        exc = self._make_exception("INTERNAL_ERROR", "transient")
        # Should NOT raise
        connector._handle_google_ads_exception(exc, "test_op")

    def test_request_error_is_swallowed(self, connector):
        exc = self._make_exception("REQUEST_ERROR", "bad query")
        connector._handle_google_ads_exception(exc, "test_op")

    def test_multiple_errors_raises_on_first_auth(self, connector):
        from google.ads.googleads.errors import GoogleAdsException

        from src.connectors.google_ads import GoogleAdsAuthError

        failure = MagicMock()
        err1 = MagicMock()
        err1.error_code = "INTERNAL_ERROR"
        err1.message = "internal"
        err2 = MagicMock()
        err2.error_code = "AUTHENTICATION_ERROR"
        err2.message = "auth failed"
        failure.errors = [err1, err2]

        exc = GoogleAdsException.__new__(GoogleAdsException)
        exc.failure = failure
        exc.request_id = "req-multi"
        exc.error = None

        with pytest.raises(GoogleAdsAuthError):
            connector._handle_google_ads_exception(exc, "test_op")


# ===========================================================================
# 11. _camel_to_snake
# ===========================================================================


class TestCamelToSnake:
    """Module-level _camel_to_snake helper."""

    @pytest.fixture(autouse=True)
    def _import_fn(self):
        from src.connectors.google_ads import _camel_to_snake

        self.fn = _camel_to_snake

    def test_descriptive_name(self):
        assert self.fn("descriptiveName") == "descriptive_name"

    def test_cost_micros(self):
        assert self.fn("costMicros") == "cost_micros"

    def test_currency_code(self):
        assert self.fn("currencyCode") == "currency_code"

    def test_time_zone(self):
        assert self.fn("timeZone") == "time_zone"

    def test_already_snake_case(self):
        assert self.fn("already_snake") == "already_snake"

    def test_single_word(self):
        assert self.fn("clicks") == "clicks"

    def test_multiple_capitals(self):
        assert self.fn("searchAbsoluteTopImpressionPercentage") == (
            "search_absolute_top_impression_percentage"
        )

    def test_empty_string(self):
        assert self.fn("") == ""

    def test_leading_uppercase(self):
        # First char uppercase: lowered, no leading underscore
        assert self.fn("Id") == "id"


# ===========================================================================
# Integration-style: _execute_query → full chain
# ===========================================================================


class TestExecuteQuery:
    """Tests for _execute_query wiring with search_stream."""

    def test_iterates_batches_and_converts_rows(self, connector, ga_service):
        row1 = MagicMock()
        row1._pb = MagicMock()
        row2 = MagicMock()
        row2._pb = MagicMock()

        batch1 = MagicMock()
        batch1.results = [row1]
        batch2 = MagicMock()
        batch2.results = [row2]

        ga_service.search_stream.return_value = [batch1, batch2]

        with patch(
            "src.connectors.google_ads.MessageToDict",
            side_effect=[
                {"campaign": {"id": "1"}},
                {"campaign": {"id": "2"}},
            ],
        ):
            result = connector._execute_query("111", "SELECT campaign.id FROM campaign")

        assert len(result) == 2
        assert result[0]["campaign_id"] == "1"
        assert result[1]["campaign_id"] == "2"

    def test_returns_none_on_transient_error(self, connector, ga_service):
        from google.ads.googleads.errors import GoogleAdsException

        failure = MagicMock()
        error = MagicMock()
        error.error_code = "INTERNAL_ERROR"
        error.message = "transient"
        failure.errors = [error]

        exc = GoogleAdsException.__new__(GoogleAdsException)
        exc.failure = failure
        exc.request_id = "req-xyz"
        exc.error = None

        ga_service.search_stream.side_effect = exc

        result = connector._execute_query("111", "SELECT 1")
        assert result is None

    def test_raises_on_auth_error(self, connector, ga_service):
        from google.ads.googleads.errors import GoogleAdsException

        from src.connectors.google_ads import GoogleAdsAuthError

        failure = MagicMock()
        error = MagicMock()
        error.error_code = "AUTHENTICATION_ERROR"
        error.message = "bad token"
        failure.errors = [error]

        exc = GoogleAdsException.__new__(GoogleAdsException)
        exc.failure = failure
        exc.request_id = "req-auth"
        exc.error = None

        ga_service.search_stream.side_effect = exc

        with pytest.raises(GoogleAdsAuthError):
            connector._execute_query("111", "SELECT 1")


# ===========================================================================
# Create Campaign
# ===========================================================================


class TestCreateCampaign:
    """GoogleAdsConnector.create_campaign."""

    def test_happy_path(self, connector):
        """Successfully creates a budget and campaign."""
        # Mock the two mutate calls (budget then campaign)
        budget_response = MagicMock()
        budget_response.results = [MagicMock(resource_name="customers/111/campaignBudgets/999")]

        campaign_response = MagicMock()
        campaign_response.results = [MagicMock(resource_name="customers/111/campaigns/555")]

        # _execute_mutate will be called twice
        with patch.object(
            connector,
            "_execute_mutate",
            side_effect=[budget_response, campaign_response],
        ):
            result = connector.create_campaign(
                "111",
                "Test Campaign",
                "SEARCH",
                10_000_000,
            )

        assert result["campaign_id"] == "555"
        assert result["name"] == "Test Campaign"
        assert result["channel_type"] == "SEARCH"
        assert result["daily_budget_micros"] == 10_000_000
        assert result["status"] == "PAUSED"
        assert result["budget_resource_name"] == "customers/111/campaignBudgets/999"
        assert result["campaign_resource_name"] == "customers/111/campaigns/555"

    def test_invalid_channel_type(self, connector):
        """Raises ValueError for invalid channel type."""
        with pytest.raises(ValueError, match="channel_type must be one of"):
            connector.create_campaign("111", "Test", "INVALID_TYPE", 10_000_000)

    def test_invalid_status(self, connector):
        """Raises ValueError for invalid status."""
        with pytest.raises(ValueError, match="status must be"):
            connector.create_campaign(
                "111",
                "Test",
                "SEARCH",
                10_000_000,
                status="REMOVED",
            )

    def test_invalid_bidding_strategy(self, connector):
        """Raises ValueError for invalid bidding strategy."""
        with pytest.raises(ValueError, match="bidding_strategy must be one of"):
            connector.create_campaign(
                "111",
                "Test",
                "SEARCH",
                10_000_000,
                bidding_strategy="INVALID",
            )

    def test_defaults(self, connector):
        """Default status=PAUSED, channel_type=SEARCH, strategy=MAXIMIZE_CLICKS."""
        budget_response = MagicMock()
        budget_response.results = [MagicMock(resource_name="customers/111/campaignBudgets/999")]
        campaign_response = MagicMock()
        campaign_response.results = [MagicMock(resource_name="customers/111/campaigns/555")]

        with patch.object(
            connector,
            "_execute_mutate",
            side_effect=[budget_response, campaign_response],
        ):
            result = connector.create_campaign("111", "Default Test", daily_budget_micros=5_000_000)

        assert result["status"] == "PAUSED"
        assert result["channel_type"] == "SEARCH"
        assert result["name"] == "Default Test"

    def test_case_insensitive_inputs(self, connector):
        """Channel type, status, and bidding strategy are case-insensitive."""
        budget_response = MagicMock()
        budget_response.results = [MagicMock(resource_name="customers/111/campaignBudgets/999")]
        campaign_response = MagicMock()
        campaign_response.results = [MagicMock(resource_name="customers/111/campaigns/555")]

        with patch.object(
            connector,
            "_execute_mutate",
            side_effect=[budget_response, campaign_response],
        ):
            result = connector.create_campaign(
                "111",
                "Test",
                "display",
                10_000_000,
                status="enabled",
                bidding_strategy="manual_cpc",
            )

        assert result["channel_type"] == "DISPLAY"
        assert result["status"] == "ENABLED"
