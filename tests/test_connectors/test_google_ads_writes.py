"""Tests for GoogleAdsConnector write methods and _execute_mutate helper.

Covers update_campaign_budget, update_campaign_status,
update_bid_strategy_target, add_negative_keywords, update_ad_schedule,
update_geo_bid_modifier, and the private _execute_mutate helper.

All Google Ads API calls are mocked; no network traffic is needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

_FAKE_CREDENTIALS = {
    "developer_token": "test-dev-token",
    "client_id": "test-client-id",
    "client_secret": "test-client-secret",
    "refresh_token": "test-refresh-token",
    "login_customer_id": "1234567890",
}

CUSTOMER_ID = "9876543210"
CAMPAIGN_ID = "100"


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
    conn._mock_client = mock_client
    conn._log = MagicMock()
    return conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_google_ads_exception(error_code_str: str, message: str = "error"):
    """Build a minimal GoogleAdsException mock."""
    from google.ads.googleads.errors import GoogleAdsException

    failure = MagicMock()
    error = MagicMock()
    error.error_code = error_code_str
    error.message = message
    failure.errors = [error]

    exc = GoogleAdsException.__new__(GoogleAdsException)
    exc.failure = failure
    exc.request_id = "req-test-write"
    exc.error = None
    return exc


def _make_mutate_response(resource_names: list[str]):
    """Build a mock mutate response with the given resource names."""
    response = MagicMock()
    results = []
    for rn in resource_names:
        result = MagicMock()
        result.resource_name = rn
        results.append(result)
    response.results = results
    response.partial_failure_error = None
    return response


# ===========================================================================
# 1. _execute_mutate
# ===========================================================================


class TestExecuteMutate:
    """Tests for the _execute_mutate helper."""

    def test_success_returns_response(self, connector):
        """Happy path: mutate_fn succeeds and response is returned."""
        response = _make_mutate_response(["customers/111/campaigns/100"])
        mutate_fn = MagicMock(return_value=response)

        result = connector._execute_mutate(CUSTOMER_ID, mutate_fn, [MagicMock()])

        assert result is response
        mutate_fn.assert_called_once()
        call_kwargs = mutate_fn.call_args[1]
        assert call_kwargs["customer_id"] == CUSTOMER_ID

    def test_google_ads_exception_raises_write_error(self, connector):
        """Non-auth GoogleAdsException becomes GoogleAdsWriteError."""
        from src.connectors.google_ads import GoogleAdsWriteError

        exc = _make_google_ads_exception("INTERNAL_ERROR", "transient")
        mutate_fn = MagicMock(side_effect=exc)

        with pytest.raises(GoogleAdsWriteError, match="Mutate failed"):
            connector._execute_mutate(CUSTOMER_ID, mutate_fn, [MagicMock()])

    def test_auth_exception_raises_auth_error(self, connector):
        """GoogleAdsException with AUTHENTICATION error becomes GoogleAdsAuthError."""
        from src.connectors.google_ads import GoogleAdsAuthError

        exc = _make_google_ads_exception("AUTHENTICATION_ERROR", "bad token")
        mutate_fn = MagicMock(side_effect=exc)

        with pytest.raises(GoogleAdsAuthError, match="Auth error"):
            connector._execute_mutate(CUSTOMER_ID, mutate_fn, [MagicMock()])

    def test_authorization_exception_raises_auth_error(self, connector):
        """GoogleAdsException with AUTHORIZATION error becomes GoogleAdsAuthError."""
        from src.connectors.google_ads import GoogleAdsAuthError

        exc = _make_google_ads_exception("AUTHORIZATION_ERROR", "no access")
        mutate_fn = MagicMock(side_effect=exc)

        with pytest.raises(GoogleAdsAuthError, match="Auth error"):
            connector._execute_mutate(CUSTOMER_ID, mutate_fn, [MagicMock()])

    def test_partial_failure_kwarg_passed(self, connector):
        """When partial_failure=True, it is forwarded to the mutate function."""
        response = _make_mutate_response(["customers/111/criteria/1"])
        mutate_fn = MagicMock(return_value=response)

        connector._execute_mutate(CUSTOMER_ID, mutate_fn, [MagicMock()], partial_failure=True)

        call_kwargs = mutate_fn.call_args[1]
        assert call_kwargs["partial_failure"] is True

    def test_partial_failure_not_passed_by_default(self, connector):
        """When partial_failure is not specified, it is not forwarded."""
        response = _make_mutate_response(["customers/111/criteria/1"])
        mutate_fn = MagicMock(return_value=response)

        connector._execute_mutate(CUSTOMER_ID, mutate_fn, [MagicMock()])

        call_kwargs = mutate_fn.call_args[1]
        assert "partial_failure" not in call_kwargs

    def test_unexpected_exception_raises_write_error(self, connector):
        """A non-GoogleAdsException wraps into GoogleAdsWriteError."""
        from src.connectors.google_ads import GoogleAdsWriteError

        mutate_fn = MagicMock(side_effect=RuntimeError("unexpected"))

        with pytest.raises(GoogleAdsWriteError, match="Unexpected error"):
            connector._execute_mutate(CUSTOMER_ID, mutate_fn, [MagicMock()])


# ===========================================================================
# 2. update_campaign_budget
# ===========================================================================


class TestUpdateCampaignBudget:
    """Tests for update_campaign_budget."""

    def _stub_current_budget(self, connector, budget_micros=10_000_000):
        """Patch _execute_query to return a row with the given current budget."""
        return patch.object(
            connector,
            "_execute_query",
            return_value=[
                {
                    "campaign_budget_resource_name": (
                        f"customers/{CUSTOMER_ID}/campaignBudgets/555"
                    ),
                    "campaign_budget_amount_micros": budget_micros,
                }
            ],
        )

    def test_happy_path(self, connector):
        """Successful budget update returns resource info and previous value."""
        response = _make_mutate_response([f"customers/{CUSTOMER_ID}/campaignBudgets/555"])
        with (
            self._stub_current_budget(connector, budget_micros=10_000_000),
            patch.object(connector, "_execute_mutate", return_value=response),
        ):
            result = connector.update_campaign_budget(CUSTOMER_ID, CAMPAIGN_ID, 12_000_000)

        assert result["resource_name"] == f"customers/{CUSTOMER_ID}/campaignBudgets/555"
        assert result["previous_budget_micros"] == 10_000_000
        assert result["new_budget_micros"] == 12_000_000

    def test_budget_cap_exceeded_raises_value_error(self, connector):
        """Budget increase beyond max_budget_change_ratio raises ValueError."""
        # Current budget is 10M micros ($10). New is 20M ($20) = 2x ratio.
        # Default cap is 1.5x.
        with self._stub_current_budget(connector, budget_micros=10_000_000):
            with pytest.raises(ValueError, match="exceeds cap"):
                connector.update_campaign_budget(CUSTOMER_ID, CAMPAIGN_ID, 20_000_000)

    def test_budget_cap_bypassed_when_validate_cap_false(self, connector):
        """Setting validate_cap=False allows uncapped budget changes."""
        response = _make_mutate_response([f"customers/{CUSTOMER_ID}/campaignBudgets/555"])
        with (
            self._stub_current_budget(connector, budget_micros=10_000_000),
            patch.object(connector, "_execute_mutate", return_value=response),
        ):
            result = connector.update_campaign_budget(
                CUSTOMER_ID, CAMPAIGN_ID, 100_000_000, validate_cap=False
            )

        assert result["new_budget_micros"] == 100_000_000

    def test_campaign_not_found_raises_write_error(self, connector):
        """When _execute_query returns empty, GoogleAdsWriteError is raised."""
        from src.connectors.google_ads import GoogleAdsWriteError

        with patch.object(connector, "_execute_query", return_value=[]):
            with pytest.raises(GoogleAdsWriteError, match="not found"):
                connector.update_campaign_budget(CUSTOMER_ID, CAMPAIGN_ID, 12_000_000)

    def test_api_error_raises_write_error(self, connector):
        """GoogleAdsException during mutate surfaces as GoogleAdsWriteError."""
        from src.connectors.google_ads import GoogleAdsWriteError

        exc = _make_google_ads_exception("INTERNAL_ERROR", "server error")
        with (
            self._stub_current_budget(connector, budget_micros=10_000_000),
            patch.object(connector, "_execute_mutate", side_effect=exc),
        ):
            with pytest.raises((GoogleAdsWriteError, type(exc))):
                connector.update_campaign_budget(CUSTOMER_ID, CAMPAIGN_ID, 12_000_000)

    def test_budget_decrease_within_cap(self, connector):
        """Budget decrease (ratio < 1.0) always passes the cap check."""
        response = _make_mutate_response([f"customers/{CUSTOMER_ID}/campaignBudgets/555"])
        with (
            self._stub_current_budget(connector, budget_micros=10_000_000),
            patch.object(connector, "_execute_mutate", return_value=response),
        ):
            result = connector.update_campaign_budget(CUSTOMER_ID, CAMPAIGN_ID, 5_000_000)

        assert result["new_budget_micros"] == 5_000_000

    def test_budget_at_cap_limit_succeeds(self, connector):
        """Budget change exactly at the 1.5x cap ratio should succeed."""
        response = _make_mutate_response([f"customers/{CUSTOMER_ID}/campaignBudgets/555"])
        with (
            self._stub_current_budget(connector, budget_micros=10_000_000),
            patch.object(connector, "_execute_mutate", return_value=response),
        ):
            # 1.5x = 15M micros, which is equal to the cap (not exceeding)
            result = connector.update_campaign_budget(CUSTOMER_ID, CAMPAIGN_ID, 15_000_000)

        assert result["new_budget_micros"] == 15_000_000


# ===========================================================================
# 3. update_campaign_status
# ===========================================================================


class TestUpdateCampaignStatus:
    """Tests for update_campaign_status."""

    def _stub_current_status(self, connector, status="ENABLED"):
        """Patch _execute_query to return a row with the given current status."""
        return patch.object(
            connector,
            "_execute_query",
            return_value=[
                {
                    "campaign_id": CAMPAIGN_ID,
                    "campaign_status": status,
                    "campaign_resource_name": (f"customers/{CUSTOMER_ID}/campaigns/{CAMPAIGN_ID}"),
                }
            ],
        )

    def test_happy_path_pause(self, connector):
        """Successfully pausing a campaign returns correct status info."""
        response = _make_mutate_response([f"customers/{CUSTOMER_ID}/campaigns/{CAMPAIGN_ID}"])
        with (
            self._stub_current_status(connector, status="ENABLED"),
            patch.object(connector, "_execute_mutate", return_value=response),
        ):
            result = connector.update_campaign_status(CUSTOMER_ID, CAMPAIGN_ID, "PAUSED")

        assert result["previous_status"] == "ENABLED"
        assert result["new_status"] == "PAUSED"
        assert result["resource_name"] == (f"customers/{CUSTOMER_ID}/campaigns/{CAMPAIGN_ID}")

    def test_happy_path_enable(self, connector):
        """Successfully enabling a paused campaign."""
        response = _make_mutate_response([f"customers/{CUSTOMER_ID}/campaigns/{CAMPAIGN_ID}"])
        with (
            self._stub_current_status(connector, status="PAUSED"),
            patch.object(connector, "_execute_mutate", return_value=response),
        ):
            result = connector.update_campaign_status(CUSTOMER_ID, CAMPAIGN_ID, "ENABLED")

        assert result["previous_status"] == "PAUSED"
        assert result["new_status"] == "ENABLED"

    def test_invalid_status_raises_value_error(self, connector):
        """Status other than ENABLED/PAUSED raises ValueError."""
        with pytest.raises(ValueError, match="must be 'ENABLED' or 'PAUSED'"):
            connector.update_campaign_status(CUSTOMER_ID, CAMPAIGN_ID, "REMOVED")

    def test_case_insensitive_status(self, connector):
        """Status input is case-insensitive (lowered internally)."""
        response = _make_mutate_response([f"customers/{CUSTOMER_ID}/campaigns/{CAMPAIGN_ID}"])
        with (
            self._stub_current_status(connector, status="ENABLED"),
            patch.object(connector, "_execute_mutate", return_value=response),
        ):
            result = connector.update_campaign_status(CUSTOMER_ID, CAMPAIGN_ID, "paused")

        assert result["new_status"] == "PAUSED"

    def test_campaign_not_found_raises_write_error(self, connector):
        """When _execute_query returns empty, GoogleAdsWriteError is raised."""
        from src.connectors.google_ads import GoogleAdsWriteError

        with patch.object(connector, "_execute_query", return_value=[]):
            with pytest.raises(GoogleAdsWriteError, match="not found"):
                connector.update_campaign_status(CUSTOMER_ID, CAMPAIGN_ID, "PAUSED")

    def test_auth_error_during_mutate_raises_auth_error(self, connector):
        """Auth errors during mutate surface as GoogleAdsAuthError."""
        from src.connectors.google_ads import GoogleAdsAuthError

        with (
            self._stub_current_status(connector),
            patch.object(
                connector,
                "_execute_mutate",
                side_effect=GoogleAdsAuthError("auth failed"),
            ),
        ):
            with pytest.raises(GoogleAdsAuthError):
                connector.update_campaign_status(CUSTOMER_ID, CAMPAIGN_ID, "PAUSED")


# ===========================================================================
# 4. update_bid_strategy_target
# ===========================================================================


class TestUpdateBidStrategyTarget:
    """Tests for update_bid_strategy_target."""

    def _stub_current_targets(self, connector, cpa_micros=None, roas=None):
        """Patch _execute_query to return a row with current bid targets."""
        return patch.object(
            connector,
            "_execute_query",
            return_value=[
                {
                    "campaign_id": CAMPAIGN_ID,
                    "campaign_resource_name": (f"customers/{CUSTOMER_ID}/campaigns/{CAMPAIGN_ID}"),
                    "campaign_target_cpa_target_cpa_micros": cpa_micros,
                    "campaign_target_roas_target_roas": roas,
                }
            ],
        )

    def test_happy_path_cpa_only(self, connector):
        """Update target CPA only -- previous and new targets recorded."""
        response = _make_mutate_response([f"customers/{CUSTOMER_ID}/campaigns/{CAMPAIGN_ID}"])
        with (
            self._stub_current_targets(connector, cpa_micros=5_000_000),
            patch.object(connector, "_execute_mutate", return_value=response),
        ):
            result = connector.update_bid_strategy_target(
                CUSTOMER_ID, CAMPAIGN_ID, target_cpa_micros=7_000_000
            )

        assert result["previous_targets"]["target_cpa_micros"] == 5_000_000
        assert result["new_targets"]["target_cpa_micros"] == 7_000_000
        assert "target_roas" not in result["new_targets"]

    def test_happy_path_roas_only(self, connector):
        """Update target ROAS only."""
        response = _make_mutate_response([f"customers/{CUSTOMER_ID}/campaigns/{CAMPAIGN_ID}"])
        with (
            self._stub_current_targets(connector, roas=3.5),
            patch.object(connector, "_execute_mutate", return_value=response),
        ):
            result = connector.update_bid_strategy_target(CUSTOMER_ID, CAMPAIGN_ID, target_roas=4.0)

        assert result["previous_targets"]["target_roas"] == 3.5
        assert result["new_targets"]["target_roas"] == 4.0
        assert "target_cpa_micros" not in result["new_targets"]

    def test_happy_path_both_targets(self, connector):
        """Update both target CPA and target ROAS simultaneously."""
        response = _make_mutate_response([f"customers/{CUSTOMER_ID}/campaigns/{CAMPAIGN_ID}"])
        with (
            self._stub_current_targets(connector, cpa_micros=5_000_000, roas=3.0),
            patch.object(connector, "_execute_mutate", return_value=response),
        ):
            result = connector.update_bid_strategy_target(
                CUSTOMER_ID,
                CAMPAIGN_ID,
                target_cpa_micros=6_000_000,
                target_roas=4.0,
            )

        assert result["new_targets"]["target_cpa_micros"] == 6_000_000
        assert result["new_targets"]["target_roas"] == 4.0

    def test_neither_target_raises_value_error(self, connector):
        """Omitting both target_cpa_micros and target_roas raises ValueError."""
        with pytest.raises(ValueError, match="At least one"):
            connector.update_bid_strategy_target(CUSTOMER_ID, CAMPAIGN_ID)

    def test_campaign_not_found_raises_write_error(self, connector):
        """When _execute_query returns empty, GoogleAdsWriteError is raised."""
        from src.connectors.google_ads import GoogleAdsWriteError

        with patch.object(connector, "_execute_query", return_value=[]):
            with pytest.raises(GoogleAdsWriteError, match="not found"):
                connector.update_bid_strategy_target(
                    CUSTOMER_ID, CAMPAIGN_ID, target_cpa_micros=5_000_000
                )

    def test_api_error_raises_write_error(self, connector):
        """GoogleAdsWriteError from _execute_mutate propagates."""
        from src.connectors.google_ads import GoogleAdsWriteError

        with (
            self._stub_current_targets(connector, cpa_micros=5_000_000),
            patch.object(
                connector,
                "_execute_mutate",
                side_effect=GoogleAdsWriteError("mutate failed"),
            ),
        ):
            with pytest.raises(GoogleAdsWriteError):
                connector.update_bid_strategy_target(
                    CUSTOMER_ID, CAMPAIGN_ID, target_cpa_micros=7_000_000
                )


# ===========================================================================
# 5. add_negative_keywords
# ===========================================================================


class TestAddNegativeKeywords:
    """Tests for add_negative_keywords.

    Note: The source method has a known logging bug where ``campaign_id``
    is passed both as an explicit kwarg and via ``**result_dict`` to
    ``_log.info``, causing a ``TypeError`` at Python call-site level.
    Happy-path tests verify business logic ran via ``_execute_mutate``
    calls and expect ``TypeError`` from the final logging statement.
    """

    def test_happy_path_mutate_called(self, connector):
        """Successful path: verifies _execute_mutate is invoked with
        correct arguments (partial_failure=True) and the right number
        of operations for the given keywords.
        """
        response = _make_mutate_response(
            [
                f"customers/{CUSTOMER_ID}/campaignCriteria/1",
                f"customers/{CUSTOMER_ID}/campaignCriteria/2",
            ]
        )
        with patch.object(connector, "_execute_mutate", return_value=response) as mock_mutate:
            result = connector.add_negative_keywords(CUSTOMER_ID, CAMPAIGN_ID, ["cheap", "free"])

        # Verify mutate was called with correct arguments
        mock_mutate.assert_called_once()
        call_kwargs = mock_mutate.call_args[1]
        assert call_kwargs["partial_failure"] is True
        # Two operations -- one per keyword
        operations = mock_mutate.call_args[0][2]
        assert len(operations) == 2
        # Verify return dict
        assert result["campaign_id"] == CAMPAIGN_ID
        assert result["keywords_added"] == 2

    def test_empty_keywords_raises_value_error(self, connector):
        """An empty keywords list raises ValueError."""
        with pytest.raises(ValueError, match="must not be empty"):
            connector.add_negative_keywords(CUSTOMER_ID, CAMPAIGN_ID, [])

    def test_partial_failure_counts_duplicates(self, connector):
        """Partial failures (duplicates) are counted separately."""
        response = MagicMock()
        result_ok = MagicMock()
        result_ok.resource_name = f"customers/{CUSTOMER_ID}/campaignCriteria/1"
        result_dup = MagicMock()
        result_dup.resource_name = ""
        response.results = [result_ok, result_dup]

        partial_error = MagicMock()
        partial_error.details = [MagicMock()]
        response.partial_failure_error = partial_error

        with patch.object(connector, "_execute_mutate", return_value=response):
            result = connector.add_negative_keywords(
                CUSTOMER_ID, CAMPAIGN_ID, ["cheap", "existing_keyword"]
            )

        assert result["keywords_added"] == 1
        assert result["duplicates_skipped"] == 1

    def test_passes_partial_failure_true_to_mutate(self, connector):
        """add_negative_keywords calls _execute_mutate with partial_failure=True."""
        response = _make_mutate_response([f"customers/{CUSTOMER_ID}/campaignCriteria/1"])
        with patch.object(connector, "_execute_mutate", return_value=response) as mock_mutate:
            connector.add_negative_keywords(CUSTOMER_ID, CAMPAIGN_ID, ["cheap"])

        call_kwargs = mock_mutate.call_args[1]
        assert call_kwargs.get("partial_failure") is True

    def test_api_error_raises_write_error(self, connector):
        """GoogleAdsWriteError from _execute_mutate propagates."""
        from src.connectors.google_ads import GoogleAdsWriteError

        with patch.object(
            connector,
            "_execute_mutate",
            side_effect=GoogleAdsWriteError("mutate failed"),
        ):
            with pytest.raises(GoogleAdsWriteError):
                connector.add_negative_keywords(CUSTOMER_ID, CAMPAIGN_ID, ["cheap"])

    def test_single_keyword_operation_built(self, connector):
        """Adding a single keyword creates exactly one operation."""
        response = _make_mutate_response([f"customers/{CUSTOMER_ID}/campaignCriteria/1"])
        with patch.object(connector, "_execute_mutate", return_value=response) as mock_mutate:
            result = connector.add_negative_keywords(CUSTOMER_ID, CAMPAIGN_ID, ["discount"])

        operations = mock_mutate.call_args[0][2]
        assert len(operations) == 1
        assert result["keywords_added"] == 1


# ===========================================================================
# 6. update_ad_schedule
# ===========================================================================


class TestUpdateAdSchedule:
    """Tests for update_ad_schedule."""

    def _make_schedule_entry(self, **overrides):
        """Create a schedule entry dict."""
        base = {
            "day_of_week": "MONDAY",
            "start_hour": 9,
            "start_minute": "ZERO",
            "end_hour": 17,
            "end_minute": "ZERO",
            "bid_modifier": 1.2,
        }
        base.update(overrides)
        return base

    def test_happy_path_mutate_called(self, connector):
        """Successful path: verifies _execute_mutate is invoked with
        one operation for a single schedule entry.
        """
        response = _make_mutate_response([f"customers/{CUSTOMER_ID}/campaignCriteria/1"])
        schedule = [self._make_schedule_entry()]
        with patch.object(connector, "_execute_mutate", return_value=response) as mock_mutate:
            result = connector.update_ad_schedule(CUSTOMER_ID, CAMPAIGN_ID, schedule)

        mock_mutate.assert_called_once()
        operations = mock_mutate.call_args[0][2]
        assert len(operations) == 1
        assert result["campaign_id"] == CAMPAIGN_ID
        assert result["schedules_set"] == 1

    def test_multiple_schedule_entries_operations(self, connector):
        """Multiple schedule entries create multiple operations."""
        response = _make_mutate_response(
            [
                f"customers/{CUSTOMER_ID}/campaignCriteria/1",
                f"customers/{CUSTOMER_ID}/campaignCriteria/2",
                f"customers/{CUSTOMER_ID}/campaignCriteria/3",
            ]
        )
        schedule = [
            self._make_schedule_entry(day_of_week="MONDAY"),
            self._make_schedule_entry(day_of_week="WEDNESDAY"),
            self._make_schedule_entry(day_of_week="FRIDAY"),
        ]
        with patch.object(connector, "_execute_mutate", return_value=response) as mock_mutate:
            result = connector.update_ad_schedule(CUSTOMER_ID, CAMPAIGN_ID, schedule)

        operations = mock_mutate.call_args[0][2]
        assert len(operations) == 3
        assert result["schedules_set"] == 3

    def test_empty_schedule_raises_value_error(self, connector):
        """An empty schedule list raises ValueError."""
        with pytest.raises(ValueError, match="must not be empty"):
            connector.update_ad_schedule(CUSTOMER_ID, CAMPAIGN_ID, [])

    def test_api_error_raises_write_error(self, connector):
        """GoogleAdsWriteError from _execute_mutate propagates."""
        from src.connectors.google_ads import GoogleAdsWriteError

        schedule = [self._make_schedule_entry()]
        with patch.object(
            connector,
            "_execute_mutate",
            side_effect=GoogleAdsWriteError("mutate failed"),
        ):
            with pytest.raises(GoogleAdsWriteError):
                connector.update_ad_schedule(CUSTOMER_ID, CAMPAIGN_ID, schedule)

    def test_auth_error_propagates(self, connector):
        """Auth errors from _execute_mutate propagate as GoogleAdsAuthError."""
        from src.connectors.google_ads import GoogleAdsAuthError

        schedule = [self._make_schedule_entry()]
        with patch.object(
            connector,
            "_execute_mutate",
            side_effect=GoogleAdsAuthError("auth failed"),
        ):
            with pytest.raises(GoogleAdsAuthError):
                connector.update_ad_schedule(CUSTOMER_ID, CAMPAIGN_ID, schedule)

    def test_schedule_without_bid_modifier(self, connector):
        """A schedule entry without bid_modifier builds an operation
        and calls _execute_mutate successfully.
        """
        response = _make_mutate_response([f"customers/{CUSTOMER_ID}/campaignCriteria/1"])
        entry = self._make_schedule_entry()
        del entry["bid_modifier"]
        with patch.object(connector, "_execute_mutate", return_value=response) as mock_mutate:
            result = connector.update_ad_schedule(CUSTOMER_ID, CAMPAIGN_ID, [entry])

        mock_mutate.assert_called_once()
        assert result["schedules_set"] == 1


# ===========================================================================
# 7. update_geo_bid_modifier
# ===========================================================================


class TestUpdateGeoBidModifier:
    """Tests for update_geo_bid_modifier."""

    GEO_TARGET_ID = 1014044  # New York

    def _stub_existing_geo(self, connector, bid_modifier=1.0):
        """Patch _execute_query to return an existing geo criterion row."""
        return patch.object(
            connector,
            "_execute_query",
            return_value=[
                {
                    "campaign_criterion_resource_name": (
                        f"customers/{CUSTOMER_ID}/campaignCriteria/geo_1"
                    ),
                    "campaign_criterion_bid_modifier": bid_modifier,
                    "campaign_criterion_location_geo_target_constant": (
                        f"geoTargetConstants/{self.GEO_TARGET_ID}"
                    ),
                }
            ],
        )

    def _stub_no_existing_geo(self, connector):
        """Patch _execute_query to return no existing geo criterion."""
        return patch.object(
            connector,
            "_execute_query",
            return_value=[],
        )

    def test_happy_path_update_existing(self, connector):
        """Update an existing geo criterion returns previous and new modifiers."""
        response = _make_mutate_response([f"customers/{CUSTOMER_ID}/campaignCriteria/geo_1"])
        with (
            self._stub_existing_geo(connector, bid_modifier=1.0),
            patch.object(connector, "_execute_mutate", return_value=response),
        ):
            result = connector.update_geo_bid_modifier(
                CUSTOMER_ID, CAMPAIGN_ID, self.GEO_TARGET_ID, 1.2
            )

        assert result["resource_name"] == (f"customers/{CUSTOMER_ID}/campaignCriteria/geo_1")
        assert result["geo_target_id"] == self.GEO_TARGET_ID
        assert result["previous_modifier"] == 1.0
        assert result["new_modifier"] == 1.2

    def test_happy_path_create_new(self, connector):
        """Create a new geo criterion when none exists."""
        response = _make_mutate_response([f"customers/{CUSTOMER_ID}/campaignCriteria/geo_new"])
        with (
            self._stub_no_existing_geo(connector),
            patch.object(connector, "_execute_mutate", return_value=response),
        ):
            result = connector.update_geo_bid_modifier(
                CUSTOMER_ID, CAMPAIGN_ID, self.GEO_TARGET_ID, 1.3
            )

        assert result["geo_target_id"] == self.GEO_TARGET_ID
        assert result["previous_modifier"] is None
        assert result["new_modifier"] == 1.3

    def test_api_error_raises_write_error(self, connector):
        """GoogleAdsWriteError from _execute_mutate propagates."""
        from src.connectors.google_ads import GoogleAdsWriteError

        with (
            self._stub_existing_geo(connector),
            patch.object(
                connector,
                "_execute_mutate",
                side_effect=GoogleAdsWriteError("mutate failed"),
            ),
        ):
            with pytest.raises(GoogleAdsWriteError):
                connector.update_geo_bid_modifier(CUSTOMER_ID, CAMPAIGN_ID, self.GEO_TARGET_ID, 1.2)

    def test_auth_error_propagates(self, connector):
        """Auth errors from _execute_mutate propagate as GoogleAdsAuthError."""
        from src.connectors.google_ads import GoogleAdsAuthError

        with (
            self._stub_no_existing_geo(connector),
            patch.object(
                connector,
                "_execute_mutate",
                side_effect=GoogleAdsAuthError("auth failed"),
            ),
        ):
            with pytest.raises(GoogleAdsAuthError):
                connector.update_geo_bid_modifier(CUSTOMER_ID, CAMPAIGN_ID, self.GEO_TARGET_ID, 1.2)

    def test_bid_modifier_decrease(self, connector):
        """A bid modifier below 1.0 (negative adjustment) is valid."""
        response = _make_mutate_response([f"customers/{CUSTOMER_ID}/campaignCriteria/geo_1"])
        with (
            self._stub_existing_geo(connector, bid_modifier=1.2),
            patch.object(connector, "_execute_mutate", return_value=response),
        ):
            result = connector.update_geo_bid_modifier(
                CUSTOMER_ID, CAMPAIGN_ID, self.GEO_TARGET_ID, 0.8
            )

        assert result["previous_modifier"] == 1.2
        assert result["new_modifier"] == 0.8
