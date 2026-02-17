"""Tests for src.connectors.bigquery -- BigQueryConnector.

Covers construction, every public method, table config, and error handling.
All BigQuery API calls are mocked; no network traffic is needed.
"""

from __future__ import annotations

import base64
import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Patch the @cached decorator to be a no-op BEFORE importing the connector.
# The decorator wraps sync methods and tries to talk to Redis via
# asyncio.run, which would fail in test isolation. Replacing it with a
# passthrough avoids that entirely.
# ---------------------------------------------------------------------------
import src.cache.decorators

_original_cached = src.cache.decorators.cached


def _noop_cached(**_kwargs):
    """Passthrough decorator -- returns the function unchanged."""

    def decorator(func):
        return func

    return decorator


src.cache.decorators.cached = _noop_cached

# Now safe to import the connector (decorator is already neutered)
from src.connectors.bigquery import (  # noqa: E402
    _MAX_CUSTOM_QUERY_ROWS,
    BigQueryAuthError,
    BigQueryConnector,
    BigQueryConnectorError,
    BigQueryTableNotFoundError,
)

# ---------------------------------------------------------------------------
# Fake credentials
# ---------------------------------------------------------------------------

_FAKE_SA_JSON = json.dumps(
    {
        "type": "service_account",
        "project_id": "test-project",
        "private_key_id": "key123",
        "private_key": ("-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n"),
        "client_email": "test@test-project.iam.gserviceaccount.com",
        "client_id": "123456",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
)

_FAKE_CREDENTIALS = {
    "project_id": "test-project",
    "dataset_id": "test_dataset",
    "credentials_json": _FAKE_SA_JSON,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeRow(dict):
    """Dict subclass that behaves like a BigQuery Row for ``dict(row)``."""

    pass


def _make_query_result(rows: list[dict]):
    """Create a mock query-job whose .result() yields FakeRow objects."""
    mock_job = MagicMock()
    mock_job.result.return_value = [FakeRow(r) for r in rows]
    return mock_job


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_bq_client():
    """Return a MagicMock standing in for bigquery.Client."""
    return MagicMock()


@pytest.fixture()
def connector(mock_bq_client):
    """Build a BigQueryConnector with a mocked client and table config."""
    with patch(
        "src.connectors.bigquery.bigquery.Client",
        return_value=mock_bq_client,
    ):
        with patch("src.connectors.bigquery.Credentials"):
            conn = BigQueryConnector(credentials=_FAKE_CREDENTIALS.copy())
    conn._client = mock_bq_client
    # Ensure table config is fully populated for the test project/dataset
    conn._table_config = {
        "goals": "test-project.test_dataset.goals",
        "orders": "test-project.test_dataset.orders",
        "channel_performance": ("test-project.test_dataset.channel_performance"),
        "budget_pacing": "test-project.test_dataset.budget_pacing",
        "campaign_attribution": ("test-project.test_dataset.campaign_attribution"),
    }
    return conn


# ===========================================================================
# 1. Construction
# ===========================================================================


class TestConstruction:
    """BigQueryConnector.__init__ and _build_client."""

    def test_explicit_credentials(self, mock_bq_client):
        with patch(
            "src.connectors.bigquery.bigquery.Client",
            return_value=mock_bq_client,
        ):
            with patch("src.connectors.bigquery.Credentials") as mock_creds:
                mock_creds.from_service_account_info.return_value = MagicMock()
                conn = BigQueryConnector(credentials=_FAKE_CREDENTIALS.copy())

        assert conn._credentials["project_id"] == "test-project"
        assert conn._credentials["dataset_id"] == "test_dataset"

    def test_fallback_to_settings(self, mock_bq_client):
        with (
            patch(
                "src.connectors.bigquery.bigquery.Client",
                return_value=mock_bq_client,
            ),
            patch("src.connectors.bigquery.Credentials") as mock_creds,
            patch("src.connectors.bigquery.settings") as mock_settings,
        ):
            mock_settings.bigquery_project_id = "settings-project"
            mock_settings.bigquery_dataset_id = "settings_dataset"
            mock_settings.bigquery_credentials_json = _FAKE_SA_JSON
            mock_settings.bigquery_table_goals = "goals"
            mock_settings.bigquery_table_orders = "orders"
            mock_settings.bigquery_table_channel_performance = "channel_performance"
            mock_settings.bigquery_table_budget_pacing = "budget_pacing"
            mock_settings.bigquery_table_campaign_attribution = "campaign_attribution"
            mock_creds.from_service_account_info.return_value = MagicMock()

            conn = BigQueryConnector()  # no credentials argument

        assert conn._credentials["project_id"] == "settings-project"
        assert conn._credentials["dataset_id"] == "settings_dataset"

    def test_service_account_json_auth(self, mock_bq_client):
        with (
            patch(
                "src.connectors.bigquery.bigquery.Client",
                return_value=mock_bq_client,
            ),
            patch("src.connectors.bigquery.Credentials") as mock_creds,
        ):
            mock_creds.from_service_account_info.return_value = MagicMock()
            BigQueryConnector(credentials=_FAKE_CREDENTIALS.copy())

        mock_creds.from_service_account_info.assert_called_once()
        call_args = mock_creds.from_service_account_info.call_args
        sa_info = call_args[0][0]
        assert sa_info["type"] == "service_account"

    def test_base64_encoded_json_auth(self, mock_bq_client):
        encoded = base64.b64encode(_FAKE_SA_JSON.encode()).decode()
        creds = {
            "project_id": "test-project",
            "dataset_id": "test_dataset",
            "credentials_json": encoded,
        }
        with (
            patch(
                "src.connectors.bigquery.bigquery.Client",
                return_value=mock_bq_client,
            ),
            patch("src.connectors.bigquery.Credentials") as mock_creds,
        ):
            mock_creds.from_service_account_info.return_value = MagicMock()
            BigQueryConnector(credentials=creds)

        mock_creds.from_service_account_info.assert_called_once()
        call_args = mock_creds.from_service_account_info.call_args
        sa_info = call_args[0][0]
        assert sa_info["type"] == "service_account"

    def test_invalid_credentials_raises_auth_error(self):
        with patch(
            "src.connectors.bigquery.bigquery.Client",
            side_effect=Exception("bad creds"),
        ):
            with pytest.raises(BigQueryAuthError, match="Failed to create"):
                BigQueryConnector(
                    credentials={
                        "project_id": "p",
                        "dataset_id": "d",
                        "credentials_json": "",
                    }
                )

    def test_adc_mode_when_credentials_json_empty(self, mock_bq_client):
        """When credentials_json is empty, fall back to ADC."""
        creds = {
            "project_id": "test-project",
            "dataset_id": "test_dataset",
            "credentials_json": "",
        }
        with (
            patch(
                "src.connectors.bigquery.bigquery.Client",
                return_value=mock_bq_client,
            ) as mock_client_cls,
            patch("src.connectors.bigquery.Credentials") as mock_creds,
        ):
            BigQueryConnector(credentials=creds)

        # Credentials.from_service_account_info should NOT be called
        mock_creds.from_service_account_info.assert_not_called()
        # Client created with project only (ADC mode)
        mock_client_cls.assert_called_once_with(project="test-project")


# ===========================================================================
# 2. Table config
# ===========================================================================


class TestTableConfig:
    """_load_table_config and _resolve_table."""

    def test_default_table_names_prefixed(self, mock_bq_client):
        """Bare table names should get project.dataset prefix."""
        with (
            patch(
                "src.connectors.bigquery.bigquery.Client",
                return_value=mock_bq_client,
            ),
            patch("src.connectors.bigquery.Credentials") as mock_creds,
            patch("src.connectors.bigquery.settings") as mock_settings,
        ):
            mock_creds.from_service_account_info.return_value = MagicMock()
            mock_settings.bigquery_table_goals = "goals"
            mock_settings.bigquery_table_orders = "orders"
            mock_settings.bigquery_table_channel_performance = "channel_performance"
            mock_settings.bigquery_table_budget_pacing = "budget_pacing"
            mock_settings.bigquery_table_campaign_attribution = "campaign_attribution"

            conn = BigQueryConnector(credentials=_FAKE_CREDENTIALS.copy())

        assert conn._table_config["goals"] == "test-project.test_dataset.goals"
        assert conn._table_config["orders"] == "test-project.test_dataset.orders"

    def test_fully_qualified_table_names_pass_through(self, mock_bq_client):
        """If table name already has dots, use it as-is."""
        fq_goals = "other-project.other_dataset.custom_goals"
        with (
            patch(
                "src.connectors.bigquery.bigquery.Client",
                return_value=mock_bq_client,
            ),
            patch("src.connectors.bigquery.Credentials") as mock_creds,
            patch("src.connectors.bigquery.settings") as mock_settings,
        ):
            mock_creds.from_service_account_info.return_value = MagicMock()
            mock_settings.bigquery_table_goals = fq_goals
            mock_settings.bigquery_table_orders = "orders"
            mock_settings.bigquery_table_channel_performance = "channel_performance"
            mock_settings.bigquery_table_budget_pacing = "budget_pacing"
            mock_settings.bigquery_table_campaign_attribution = "campaign_attribution"

            conn = BigQueryConnector(credentials=_FAKE_CREDENTIALS.copy())

        assert conn._table_config["goals"] == fq_goals

    def test_missing_project_dataset_uses_bare_names(self, mock_bq_client):
        """When project/dataset are empty, bare names stored as-is."""
        creds = {
            "project_id": "",
            "dataset_id": "",
            "credentials_json": "",
        }
        with (
            patch(
                "src.connectors.bigquery.bigquery.Client",
                return_value=mock_bq_client,
            ),
            patch("src.connectors.bigquery.settings") as mock_settings,
        ):
            mock_settings.bigquery_table_goals = "goals"
            mock_settings.bigquery_table_orders = "orders"
            mock_settings.bigquery_table_channel_performance = "channel_performance"
            mock_settings.bigquery_table_budget_pacing = "budget_pacing"
            mock_settings.bigquery_table_campaign_attribution = "campaign_attribution"

            conn = BigQueryConnector(credentials=creds)

        assert conn._table_config["goals"] == "goals"

    def test_resolve_table_raises_for_unknown_table(self, connector):
        with pytest.raises(BigQueryTableNotFoundError, match="not configured"):
            connector._resolve_table("nonexistent_table")


# ===========================================================================
# 3. discover_tables
# ===========================================================================


class TestDiscoverTables:
    def test_happy_path_returns_table_dicts(self, connector, mock_bq_client):
        table_ref = MagicMock()
        table_ref.table_id = "goals"
        table_ref.table_type = "TABLE"
        table_ref.reference = "test-project.test_dataset.goals"

        full_table = MagicMock()
        full_table.num_rows = 1000
        full_table.num_bytes = 50000
        full_table.description = "Revenue goals"

        mock_bq_client.list_tables.return_value = [table_ref]
        mock_bq_client.get_table.return_value = full_table

        result = connector.discover_tables()

        assert len(result) == 1
        assert result[0]["table_id"] == "goals"
        assert result[0]["table_type"] == "TABLE"
        assert result[0]["num_rows"] == 1000
        assert result[0]["num_bytes"] == 50000
        assert result[0]["description"] == "Revenue goals"

    def test_empty_dataset_returns_empty_list(self, connector, mock_bq_client):
        mock_bq_client.list_tables.return_value = []

        result = connector.discover_tables()

        assert result == []

    def test_api_error_returns_empty_list(self, connector, mock_bq_client):
        from google.api_core.exceptions import GoogleAPICallError

        mock_bq_client.list_tables.side_effect = GoogleAPICallError("internal error")

        result = connector.discover_tables()

        assert result == []


# ===========================================================================
# 4. get_goals
# ===========================================================================


class TestGetGoals:
    def test_happy_path_no_filters(self, connector, mock_bq_client):
        rows = [
            {
                "period": "2024-01",
                "channel": "google_ads",
                "metric_name": "roas",
                "target_value": 4.0,
            },
            {
                "period": "2024-01",
                "channel": "meta",
                "metric_name": "cpa",
                "target_value": 25.0,
            },
        ]
        mock_bq_client.query.return_value = _make_query_result(rows)

        result = connector.get_goals()

        assert len(result) == 2
        assert result[0]["period"] == "2024-01"
        assert result[1]["channel"] == "meta"

    def test_filtering_by_period_and_channel(self, connector, mock_bq_client):
        rows = [
            {
                "period": "2024-02",
                "channel": "google_ads",
                "metric_name": "roas",
                "target_value": 5.0,
            }
        ]
        mock_bq_client.query.return_value = _make_query_result(rows)

        result = connector.get_goals(period="2024-02", channel="google_ads")

        assert len(result) == 1
        # Verify params were passed in query config
        call_args = mock_bq_client.query.call_args
        query_str = call_args[0][0]
        assert "period = @period" in query_str
        assert "channel = @channel" in query_str

    def test_empty_result(self, connector, mock_bq_client):
        mock_bq_client.query.return_value = _make_query_result([])

        result = connector.get_goals(period="2099-01")

        assert result == []

    def test_api_error_returns_empty_list(self, connector, mock_bq_client):
        from google.api_core.exceptions import BadRequest

        mock_bq_client.query.side_effect = BadRequest("syntax error")

        result = connector.get_goals()

        assert result == []


# ===========================================================================
# 5. get_budget_pacing
# ===========================================================================


class TestGetBudgetPacing:
    def test_happy_path(self, connector, mock_bq_client):
        rows = [
            {
                "period": "2024-01",
                "channel": "google_ads",
                "planned_spend": 10000,
                "actual_spend": 8500,
                "pacing_pct": 0.85,
            },
        ]
        mock_bq_client.query.return_value = _make_query_result(rows)

        result = connector.get_budget_pacing()

        assert len(result) == 1
        assert result[0]["pacing_pct"] == 0.85

    def test_with_filters(self, connector, mock_bq_client):
        rows = [
            {
                "period": "2024-03",
                "channel": "meta",
                "planned_spend": 5000,
                "actual_spend": 5200,
                "pacing_pct": 1.04,
            }
        ]
        mock_bq_client.query.return_value = _make_query_result(rows)

        result = connector.get_budget_pacing(period="2024-03", channel="meta")

        assert len(result) == 1
        query_str = mock_bq_client.query.call_args[0][0]
        assert "period = @period" in query_str
        assert "channel = @channel" in query_str

    def test_empty_result(self, connector, mock_bq_client):
        mock_bq_client.query.return_value = _make_query_result([])

        result = connector.get_budget_pacing(period="2099-01")

        assert result == []


# ===========================================================================
# 6. get_business_metrics
# ===========================================================================


class TestGetBusinessMetrics:
    def test_happy_path_daily_granularity(self, connector, mock_bq_client):
        rows = [
            {
                "date": "2024-01-15",
                "revenue": 12500.00,
                "orders": 50,
                "aov": 250.00,
            },
            {
                "date": "2024-01-14",
                "revenue": 11000.00,
                "orders": 44,
                "aov": 250.00,
            },
        ]
        mock_bq_client.query.return_value = _make_query_result(rows)

        result = connector.get_business_metrics("2024-01-14", "2024-01-15")

        assert len(result) == 2
        assert result[0]["revenue"] == 12500.00

    def test_weekly_granularity(self, connector, mock_bq_client):
        rows = [
            {
                "date": "2024-01-08",
                "revenue": 80000.00,
                "orders": 320,
                "aov": 250.00,
            }
        ]
        mock_bq_client.query.return_value = _make_query_result(rows)

        result = connector.get_business_metrics("2024-01-01", "2024-01-31", granularity="weekly")

        assert len(result) == 1
        query_str = mock_bq_client.query.call_args[0][0]
        assert "DATE_TRUNC" in query_str
        assert "WEEK" in query_str

    def test_monthly_granularity(self, connector, mock_bq_client):
        rows = [
            {
                "date": "2024-01-01",
                "revenue": 300000.00,
                "orders": 1200,
                "aov": 250.00,
            }
        ]
        mock_bq_client.query.return_value = _make_query_result(rows)

        result = connector.get_business_metrics("2024-01-01", "2024-01-31", granularity="monthly")

        assert len(result) == 1
        query_str = mock_bq_client.query.call_args[0][0]
        assert "DATE_TRUNC" in query_str
        assert "MONTH" in query_str

    def test_date_objects_vs_string_dates(self, connector, mock_bq_client):
        mock_bq_client.query.return_value = _make_query_result([])

        # date objects
        connector.get_business_metrics(date(2024, 1, 1), date(2024, 1, 31))
        call1 = mock_bq_client.query.call_args
        params1 = call1[1]["job_config"].query_parameters
        pv1 = {p.name: p.value for p in params1}
        assert pv1["start_date"] == "2024-01-01"
        assert pv1["end_date"] == "2024-01-31"

        # string dates
        connector.get_business_metrics("2024-02-01", "2024-02-28")
        call2 = mock_bq_client.query.call_args
        params2 = call2[1]["job_config"].query_parameters
        pv2 = {p.name: p.value for p in params2}
        assert pv2["start_date"] == "2024-02-01"
        assert pv2["end_date"] == "2024-02-28"

    def test_empty_result(self, connector, mock_bq_client):
        mock_bq_client.query.return_value = _make_query_result([])

        result = connector.get_business_metrics("2099-01-01", "2099-01-31")

        assert result == []


# ===========================================================================
# 7. get_channel_performance
# ===========================================================================


class TestGetChannelPerformance:
    def test_happy_path(self, connector, mock_bq_client):
        rows = [
            {
                "channel": "google_ads",
                "date": "2024-01-15",
                "revenue": 5000.00,
                "orders": 20,
                "cost": 1000.00,
            },
            {
                "channel": "meta",
                "date": "2024-01-15",
                "revenue": 3000.00,
                "orders": 15,
                "cost": 800.00,
            },
        ]
        mock_bq_client.query.return_value = _make_query_result(rows)

        result = connector.get_channel_performance("2024-01-15", "2024-01-15")

        assert len(result) == 2
        assert result[0]["channel"] == "google_ads"
        assert result[1]["channel"] == "meta"

    def test_empty_result(self, connector, mock_bq_client):
        mock_bq_client.query.return_value = _make_query_result([])

        result = connector.get_channel_performance("2099-01-01", "2099-01-31")

        assert result == []

    def test_api_error_returns_empty_list(self, connector, mock_bq_client):
        from google.api_core.exceptions import BadRequest

        mock_bq_client.query.side_effect = BadRequest("bad query")

        result = connector.get_channel_performance("2024-01-01", "2024-01-31")

        assert result == []


# ===========================================================================
# 8. get_campaign_attribution
# ===========================================================================


class TestGetCampaignAttribution:
    def test_happy_path_all_columns(self, connector, mock_bq_client):
        rows = [
            {
                "campaign_id": "camp_123",
                "campaign_name": "Brand Search",
                "channel": "google_ads",
                "date": "2024-01-15",
                "conversions": 25,
                "revenue": 6250.00,
            }
        ]
        mock_bq_client.query.return_value = _make_query_result(rows)

        result = connector.get_campaign_attribution("2024-01-15", "2024-01-15")

        assert len(result) == 1
        assert result[0]["campaign_id"] == "camp_123"
        assert result[0]["campaign_name"] == "Brand Search"
        assert result[0]["channel"] == "google_ads"
        assert result[0]["conversions"] == 25
        assert result[0]["revenue"] == 6250.00

    def test_with_channel_filter(self, connector, mock_bq_client):
        rows = [
            {
                "campaign_id": "c1",
                "campaign_name": "Retarget",
                "channel": "meta",
                "date": "2024-01-15",
                "conversions": 10,
                "revenue": 2500.00,
            }
        ]
        mock_bq_client.query.return_value = _make_query_result(rows)

        result = connector.get_campaign_attribution("2024-01-15", "2024-01-15", channel="meta")

        assert len(result) == 1
        query_str = mock_bq_client.query.call_args[0][0]
        assert "channel = @channel" in query_str

    def test_empty_result(self, connector, mock_bq_client):
        mock_bq_client.query.return_value = _make_query_result([])

        result = connector.get_campaign_attribution("2099-01-01", "2099-01-31")

        assert result == []

    def test_date_objects_accepted(self, connector, mock_bq_client):
        mock_bq_client.query.return_value = _make_query_result([])

        result = connector.get_campaign_attribution(date(2024, 3, 1), date(2024, 3, 31))

        assert result == []
        # Verify the query was called (no type errors)
        mock_bq_client.query.assert_called_once()


# ===========================================================================
# 9. run_custom_query
# ===========================================================================


class TestRunCustomQuery:
    def test_valid_select_query(self, connector, mock_bq_client):
        rows = [{"col1": "val1", "col2": 42}]
        mock_bq_client.query.return_value = _make_query_result(rows)

        result = connector.run_custom_query("SELECT col1, col2 FROM my_table")

        assert len(result) == 1
        assert result[0]["col1"] == "val1"

    def test_non_select_raises_error(self, connector):
        with pytest.raises(BigQueryConnectorError, match="Only SELECT"):
            connector.run_custom_query("DELETE FROM my_table WHERE 1=1")

    def test_insert_raises_error(self, connector):
        with pytest.raises(BigQueryConnectorError, match="Only SELECT"):
            connector.run_custom_query("INSERT INTO my_table VALUES (1, 'a')")

    def test_row_cap_enforcement(self, connector, mock_bq_client):
        big_rows = [{"id": i} for i in range(_MAX_CUSTOM_QUERY_ROWS + 500)]
        mock_bq_client.query.return_value = _make_query_result(big_rows)

        result = connector.run_custom_query("SELECT id FROM big_table", max_rows=10000)

        assert len(result) == _MAX_CUSTOM_QUERY_ROWS

    def test_auto_appends_limit_when_missing(self, connector, mock_bq_client):
        mock_bq_client.query.return_value = _make_query_result([])

        connector.run_custom_query("SELECT * FROM my_table")

        query_str = mock_bq_client.query.call_args[0][0]
        assert "LIMIT" in query_str

    def test_respects_existing_limit(self, connector, mock_bq_client):
        mock_bq_client.query.return_value = _make_query_result([])

        connector.run_custom_query("SELECT * FROM my_table LIMIT 100")

        query_str = mock_bq_client.query.call_args[0][0]
        # Original query already has LIMIT -- no second one appended
        assert query_str.count("LIMIT") == 1


# ===========================================================================
# 10. Error handling (_handle_bigquery_error)
# ===========================================================================


class TestHandleBigQueryError:
    def test_forbidden_raises_auth_error(self, connector):
        from google.api_core.exceptions import Forbidden

        exc = Forbidden("permission denied")

        with pytest.raises(BigQueryAuthError, match="permission denied"):
            connector._handle_bigquery_error(exc, "test_op")

    def test_not_found_raises_table_not_found_error(self, connector):
        from google.api_core.exceptions import NotFound

        exc = NotFound("table not found")

        with pytest.raises(BigQueryTableNotFoundError, match="not found"):
            connector._handle_bigquery_error(exc, "test_op")

    def test_bad_request_is_logged_and_returns_none(self, connector):
        from google.api_core.exceptions import BadRequest

        exc = BadRequest("syntax error in SQL")

        # Should NOT raise; the method just logs and returns
        connector._handle_bigquery_error(exc, "test_op")

    def test_generic_error_does_not_raise(self, connector):
        from google.api_core.exceptions import GoogleAPICallError

        exc = GoogleAPICallError("transient server error")

        # Should NOT raise
        connector._handle_bigquery_error(exc, "test_op")


# ===========================================================================
# 11. _execute_query integration path
# ===========================================================================


class TestExecuteQuery:
    """Tests for _execute_query internal method."""

    def test_returns_list_of_dicts(self, connector, mock_bq_client):
        rows = [
            {"name": "alpha", "value": 1},
            {"name": "beta", "value": 2},
        ]
        mock_bq_client.query.return_value = _make_query_result(rows)

        result = connector._execute_query("SELECT name, value FROM t")

        assert result == [
            {"name": "alpha", "value": 1},
            {"name": "beta", "value": 2},
        ]

    def test_returns_none_on_transient_error(self, connector, mock_bq_client):
        from google.api_core.exceptions import GoogleAPICallError

        mock_bq_client.query.side_effect = GoogleAPICallError("server error")

        result = connector._execute_query("SELECT 1")

        assert result is None

    def test_raises_on_forbidden_error(self, connector, mock_bq_client):
        from google.api_core.exceptions import Forbidden

        mock_bq_client.query.side_effect = Forbidden("no access")

        with pytest.raises(BigQueryAuthError):
            connector._execute_query("SELECT 1")


# ===========================================================================
# Teardown: restore original @cached decorator
# ===========================================================================


def teardown_module():
    """Restore the original @cached decorator after all tests run."""
    src.cache.decorators.cached = _original_cached
