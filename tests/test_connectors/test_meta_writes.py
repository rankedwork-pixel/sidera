"""Tests for Meta connector write methods.

Covers the 6 approval-gated write methods on MetaConnector:
  - update_campaign_status
  - update_campaign_budget
  - update_adset_status
  - update_adset_budget
  - update_ad_status
  - update_adset_bid

All Meta Marketing API calls are mocked; no network traffic is needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# Fake credentials used for constructing the connector.
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


def _make_sdk_object_mock(**fields):
    """Create a mock SDK object (Campaign, AdSet, Ad) that supports .get()
    access on api_get() results and exposes api_update().
    """
    obj = MagicMock()
    result = MagicMock()
    result.get = lambda key, default=None: fields.get(key, default)
    result.__getitem__ = lambda self_inner, key: fields[key]
    result.__contains__ = lambda self_inner, key: key in fields
    obj.api_get.return_value = result
    return obj


def _make_facebook_request_error(message: str = "API error"):
    """Build a real FacebookRequestError for side_effect usage."""
    from facebook_business.exceptions import FacebookRequestError

    exc = FacebookRequestError.__new__(FacebookRequestError)
    exc._body = ""
    exc._message = message
    exc.api_error_code = lambda: 1
    exc.api_error_subcode = lambda: 0
    exc.api_error_message = lambda: message
    return exc


# ===========================================================================
# 1. update_campaign_status
# ===========================================================================


class TestUpdateCampaignStatus:
    """Tests for MetaConnector.update_campaign_status."""

    def test_happy_path_pause(self, connector):
        """Pausing an active campaign returns correct result dict."""
        mock_campaign = _make_sdk_object_mock(status="ACTIVE")

        with patch("src.connectors.meta.Campaign", return_value=mock_campaign):
            result = connector.update_campaign_status("act_123", "456", "PAUSED")

        assert result == {
            "campaign_id": "456",
            "previous_status": "ACTIVE",
            "new_status": "PAUSED",
        }
        mock_campaign.api_update.assert_called_once()

    def test_happy_path_activate(self, connector):
        """Activating a paused campaign returns correct result dict."""
        mock_campaign = _make_sdk_object_mock(status="PAUSED")

        with patch("src.connectors.meta.Campaign", return_value=mock_campaign):
            result = connector.update_campaign_status("act_123", "456", "ACTIVE")

        assert result["new_status"] == "ACTIVE"
        assert result["previous_status"] == "PAUSED"

    def test_invalid_status_raises_value_error(self, connector):
        """Passing an invalid status raises ValueError before any API call."""
        with pytest.raises(ValueError, match="Invalid status"):
            connector.update_campaign_status("act_123", "456", "DELETED")

    def test_api_error_raises_meta_write_error(self, connector):
        """A FacebookRequestError during update is wrapped in MetaWriteError."""
        from src.connectors.meta import MetaWriteError

        mock_campaign = _make_sdk_object_mock(status="ACTIVE")
        mock_campaign.api_update.side_effect = _make_facebook_request_error("update failed")

        with (
            patch("src.connectors.meta.Campaign", return_value=mock_campaign),
            pytest.raises(MetaWriteError, match="Failed to update campaign status"),
        ):
            connector.update_campaign_status("act_123", "456", "PAUSED")

    def test_return_dict_structure(self, connector):
        """Verify all expected keys are present in the return dict."""
        mock_campaign = _make_sdk_object_mock(status="ACTIVE")

        with patch("src.connectors.meta.Campaign", return_value=mock_campaign):
            result = connector.update_campaign_status("act_123", "789", "PAUSED")

        assert set(result.keys()) == {"campaign_id", "previous_status", "new_status"}
        assert result["campaign_id"] == "789"


# ===========================================================================
# 2. update_campaign_budget
# ===========================================================================


class TestUpdateCampaignBudget:
    """Tests for MetaConnector.update_campaign_budget."""

    def test_happy_path_daily_budget(self, connector):
        """Updating daily budget within cap succeeds and returns correct dict."""
        mock_campaign = _make_sdk_object_mock(
            daily_budget="5000",
            lifetime_budget=None,
        )

        with patch("src.connectors.meta.Campaign", return_value=mock_campaign):
            result = connector.update_campaign_budget("act_123", "456", 6000, "daily")

        assert result == {
            "campaign_id": "456",
            "previous_budget_cents": 5000,
            "new_budget_cents": 6000,
            "budget_type": "daily",
        }
        mock_campaign.api_update.assert_called_once()

    def test_happy_path_lifetime_budget(self, connector):
        """Updating lifetime budget within cap succeeds."""
        mock_campaign = _make_sdk_object_mock(
            daily_budget=None,
            lifetime_budget="100000",
        )

        with patch("src.connectors.meta.Campaign", return_value=mock_campaign):
            result = connector.update_campaign_budget("act_123", "456", 120000, "lifetime")

        assert result["budget_type"] == "lifetime"
        assert result["previous_budget_cents"] == 100000
        assert result["new_budget_cents"] == 120000

    def test_budget_cap_exceeded_increase(self, connector):
        """Budget increase beyond max_budget_change_ratio raises ValueError."""
        mock_campaign = _make_sdk_object_mock(
            daily_budget="5000",
            lifetime_budget=None,
        )

        with (
            patch("src.connectors.meta.Campaign", return_value=mock_campaign),
            patch("src.connectors.meta.settings") as mock_settings,
        ):
            mock_settings.max_budget_change_ratio = 1.5
            with pytest.raises(ValueError, match="Budget change ratio"):
                # 5000 -> 10000 = 2.0x, exceeds 1.5x cap
                connector.update_campaign_budget("act_123", "456", 10000, "daily")

    def test_budget_cap_exceeded_decrease(self, connector):
        """Budget decrease beyond max_budget_change_ratio raises ValueError."""
        mock_campaign = _make_sdk_object_mock(
            daily_budget="10000",
            lifetime_budget=None,
        )

        with (
            patch("src.connectors.meta.Campaign", return_value=mock_campaign),
            patch("src.connectors.meta.settings") as mock_settings,
        ):
            mock_settings.max_budget_change_ratio = 1.5
            with pytest.raises(ValueError, match="Budget change ratio"):
                # 10000 -> 3000 = 0.3x, below 1/1.5 = 0.667
                connector.update_campaign_budget("act_123", "456", 3000, "daily")

    def test_invalid_budget_type_raises_value_error(self, connector):
        """Passing an invalid budget_type raises ValueError."""
        with pytest.raises(ValueError, match="Invalid budget_type"):
            connector.update_campaign_budget("act_123", "456", 5000, "weekly")

    def test_api_error_raises_meta_write_error(self, connector):
        """A FacebookRequestError during update is wrapped in MetaWriteError."""
        from src.connectors.meta import MetaWriteError

        mock_campaign = _make_sdk_object_mock(
            daily_budget="5000",
            lifetime_budget=None,
        )
        mock_campaign.api_update.side_effect = _make_facebook_request_error("budget fail")

        with (
            patch("src.connectors.meta.Campaign", return_value=mock_campaign),
            pytest.raises(MetaWriteError, match="Failed to update campaign budget"),
        ):
            connector.update_campaign_budget("act_123", "456", 6000, "daily")

    def test_zero_previous_budget_skips_cap_validation(self, connector):
        """When previous budget is 0, cap validation is skipped (no division)."""
        mock_campaign = _make_sdk_object_mock(
            daily_budget=None,
            lifetime_budget=None,
        )

        with patch("src.connectors.meta.Campaign", return_value=mock_campaign):
            result = connector.update_campaign_budget("act_123", "456", 5000, "daily")

        assert result["previous_budget_cents"] == 0
        assert result["new_budget_cents"] == 5000

    def test_return_dict_structure(self, connector):
        """Verify all expected keys are present in the return dict."""
        mock_campaign = _make_sdk_object_mock(
            daily_budget="5000",
            lifetime_budget=None,
        )

        with patch("src.connectors.meta.Campaign", return_value=mock_campaign):
            result = connector.update_campaign_budget("act_123", "456", 6000, "daily")

        assert set(result.keys()) == {
            "campaign_id",
            "previous_budget_cents",
            "new_budget_cents",
            "budget_type",
        }


# ===========================================================================
# 3. update_adset_status
# ===========================================================================


class TestUpdateAdsetStatus:
    """Tests for MetaConnector.update_adset_status."""

    def test_happy_path_pause(self, connector):
        """Pausing an active ad set returns correct result dict."""
        mock_adset = _make_sdk_object_mock(status="ACTIVE")

        with patch("src.connectors.meta.AdSet", return_value=mock_adset):
            result = connector.update_adset_status("act_123", "789", "PAUSED")

        assert result == {
            "adset_id": "789",
            "previous_status": "ACTIVE",
            "new_status": "PAUSED",
        }
        mock_adset.api_update.assert_called_once()

    def test_happy_path_activate(self, connector):
        """Activating a paused ad set returns correct result dict."""
        mock_adset = _make_sdk_object_mock(status="PAUSED")

        with patch("src.connectors.meta.AdSet", return_value=mock_adset):
            result = connector.update_adset_status("act_123", "789", "ACTIVE")

        assert result["new_status"] == "ACTIVE"
        assert result["previous_status"] == "PAUSED"

    def test_invalid_status_raises_value_error(self, connector):
        """Passing an invalid status raises ValueError."""
        with pytest.raises(ValueError, match="Invalid status"):
            connector.update_adset_status("act_123", "789", "ARCHIVED")

    def test_api_error_raises_meta_write_error(self, connector):
        """A FacebookRequestError during update is wrapped in MetaWriteError."""
        from src.connectors.meta import MetaWriteError

        mock_adset = _make_sdk_object_mock(status="ACTIVE")
        mock_adset.api_update.side_effect = _make_facebook_request_error("adset fail")

        with (
            patch("src.connectors.meta.AdSet", return_value=mock_adset),
            pytest.raises(MetaWriteError, match="Failed to update ad set status"),
        ):
            connector.update_adset_status("act_123", "789", "PAUSED")

    def test_return_dict_structure(self, connector):
        """Verify all expected keys are present in the return dict."""
        mock_adset = _make_sdk_object_mock(status="ACTIVE")

        with patch("src.connectors.meta.AdSet", return_value=mock_adset):
            result = connector.update_adset_status("act_123", "789", "PAUSED")

        assert set(result.keys()) == {"adset_id", "previous_status", "new_status"}
        assert result["adset_id"] == "789"


# ===========================================================================
# 4. update_adset_budget
# ===========================================================================


class TestUpdateAdsetBudget:
    """Tests for MetaConnector.update_adset_budget."""

    def test_happy_path_daily_budget(self, connector):
        """Updating daily budget within cap succeeds."""
        mock_adset = _make_sdk_object_mock(
            daily_budget="8000",
            lifetime_budget=None,
        )

        with patch("src.connectors.meta.AdSet", return_value=mock_adset):
            result = connector.update_adset_budget("act_123", "789", 10000, "daily")

        assert result == {
            "adset_id": "789",
            "previous_budget_cents": 8000,
            "new_budget_cents": 10000,
            "budget_type": "daily",
        }
        mock_adset.api_update.assert_called_once()

    def test_happy_path_lifetime_budget(self, connector):
        """Updating lifetime budget within cap succeeds."""
        mock_adset = _make_sdk_object_mock(
            daily_budget=None,
            lifetime_budget="200000",
        )

        with patch("src.connectors.meta.AdSet", return_value=mock_adset):
            result = connector.update_adset_budget("act_123", "789", 250000, "lifetime")

        assert result["budget_type"] == "lifetime"
        assert result["previous_budget_cents"] == 200000
        assert result["new_budget_cents"] == 250000

    def test_budget_cap_exceeded_increase(self, connector):
        """Budget increase beyond max_budget_change_ratio raises ValueError."""
        mock_adset = _make_sdk_object_mock(
            daily_budget="5000",
            lifetime_budget=None,
        )

        with (
            patch("src.connectors.meta.AdSet", return_value=mock_adset),
            patch("src.connectors.meta.settings") as mock_settings,
        ):
            mock_settings.max_budget_change_ratio = 1.5
            with pytest.raises(ValueError, match="Budget change ratio"):
                # 5000 -> 15000 = 3.0x, exceeds 1.5x cap
                connector.update_adset_budget("act_123", "789", 15000, "daily")

    def test_budget_cap_exceeded_decrease(self, connector):
        """Budget decrease beyond max_budget_change_ratio raises ValueError."""
        mock_adset = _make_sdk_object_mock(
            daily_budget="10000",
            lifetime_budget=None,
        )

        with (
            patch("src.connectors.meta.AdSet", return_value=mock_adset),
            patch("src.connectors.meta.settings") as mock_settings,
        ):
            mock_settings.max_budget_change_ratio = 1.5
            with pytest.raises(ValueError, match="Budget change ratio"):
                # 10000 -> 2000 = 0.2x, below 1/1.5 = 0.667
                connector.update_adset_budget("act_123", "789", 2000, "daily")

    def test_invalid_budget_type_raises_value_error(self, connector):
        """Passing an invalid budget_type raises ValueError."""
        with pytest.raises(ValueError, match="Invalid budget_type"):
            connector.update_adset_budget("act_123", "789", 5000, "monthly")

    def test_api_error_raises_meta_write_error(self, connector):
        """A FacebookRequestError during update is wrapped in MetaWriteError."""
        from src.connectors.meta import MetaWriteError

        mock_adset = _make_sdk_object_mock(
            daily_budget="5000",
            lifetime_budget=None,
        )
        mock_adset.api_update.side_effect = _make_facebook_request_error("budget fail")

        with (
            patch("src.connectors.meta.AdSet", return_value=mock_adset),
            pytest.raises(MetaWriteError, match="Failed to update ad set budget"),
        ):
            connector.update_adset_budget("act_123", "789", 6000, "daily")

    def test_zero_previous_budget_skips_cap_validation(self, connector):
        """When previous budget is 0, cap validation is skipped."""
        mock_adset = _make_sdk_object_mock(
            daily_budget=None,
            lifetime_budget=None,
        )

        with patch("src.connectors.meta.AdSet", return_value=mock_adset):
            result = connector.update_adset_budget("act_123", "789", 8000, "daily")

        assert result["previous_budget_cents"] == 0
        assert result["new_budget_cents"] == 8000

    def test_return_dict_structure(self, connector):
        """Verify all expected keys are present in the return dict."""
        mock_adset = _make_sdk_object_mock(
            daily_budget="5000",
            lifetime_budget=None,
        )

        with patch("src.connectors.meta.AdSet", return_value=mock_adset):
            result = connector.update_adset_budget("act_123", "789", 6000, "daily")

        assert set(result.keys()) == {
            "adset_id",
            "previous_budget_cents",
            "new_budget_cents",
            "budget_type",
        }


# ===========================================================================
# 5. update_ad_status
# ===========================================================================


class TestUpdateAdStatus:
    """Tests for MetaConnector.update_ad_status."""

    def test_happy_path_pause(self, connector):
        """Pausing an active ad returns correct result dict."""
        mock_ad = _make_sdk_object_mock(status="ACTIVE")

        with patch("src.connectors.meta.Ad", return_value=mock_ad):
            result = connector.update_ad_status("act_123", "111", "PAUSED")

        assert result == {
            "ad_id": "111",
            "previous_status": "ACTIVE",
            "new_status": "PAUSED",
        }
        mock_ad.api_update.assert_called_once()

    def test_happy_path_activate(self, connector):
        """Activating a paused ad returns correct result dict."""
        mock_ad = _make_sdk_object_mock(status="PAUSED")

        with patch("src.connectors.meta.Ad", return_value=mock_ad):
            result = connector.update_ad_status("act_123", "111", "ACTIVE")

        assert result["new_status"] == "ACTIVE"
        assert result["previous_status"] == "PAUSED"

    def test_invalid_status_raises_value_error(self, connector):
        """Passing an invalid status raises ValueError."""
        with pytest.raises(ValueError, match="Invalid status"):
            connector.update_ad_status("act_123", "111", "REMOVED")

    def test_api_error_raises_meta_write_error(self, connector):
        """A FacebookRequestError during update is wrapped in MetaWriteError."""
        from src.connectors.meta import MetaWriteError

        mock_ad = _make_sdk_object_mock(status="ACTIVE")
        mock_ad.api_update.side_effect = _make_facebook_request_error("ad update fail")

        with (
            patch("src.connectors.meta.Ad", return_value=mock_ad),
            pytest.raises(MetaWriteError, match="Failed to update ad status"),
        ):
            connector.update_ad_status("act_123", "111", "PAUSED")

    def test_return_dict_structure(self, connector):
        """Verify all expected keys are present in the return dict."""
        mock_ad = _make_sdk_object_mock(status="PAUSED")

        with patch("src.connectors.meta.Ad", return_value=mock_ad):
            result = connector.update_ad_status("act_123", "222", "ACTIVE")

        assert set(result.keys()) == {"ad_id", "previous_status", "new_status"}
        assert result["ad_id"] == "222"


# ===========================================================================
# 6. update_adset_bid
# ===========================================================================


class TestUpdateAdsetBid:
    """Tests for MetaConnector.update_adset_bid."""

    def test_happy_path(self, connector):
        """Updating bid amount returns correct result dict."""
        mock_adset = _make_sdk_object_mock(bid_amount="500")

        with patch("src.connectors.meta.AdSet", return_value=mock_adset):
            result = connector.update_adset_bid("act_123", "789", 750)

        assert result == {
            "adset_id": "789",
            "previous_bid_cents": 500,
            "new_bid_cents": 750,
        }
        mock_adset.api_update.assert_called_once()

    def test_api_error_raises_meta_write_error(self, connector):
        """A FacebookRequestError during update is wrapped in MetaWriteError."""
        from src.connectors.meta import MetaWriteError

        mock_adset = _make_sdk_object_mock(bid_amount="500")
        mock_adset.api_update.side_effect = _make_facebook_request_error("bid fail")

        with (
            patch("src.connectors.meta.AdSet", return_value=mock_adset),
            pytest.raises(MetaWriteError, match="Failed to update ad set bid"),
        ):
            connector.update_adset_bid("act_123", "789", 750)

    def test_zero_previous_bid(self, connector):
        """When no previous bid exists, previous_bid_cents is 0."""
        mock_adset = _make_sdk_object_mock(bid_amount=None)

        with patch("src.connectors.meta.AdSet", return_value=mock_adset):
            result = connector.update_adset_bid("act_123", "789", 300)

        assert result["previous_bid_cents"] == 0
        assert result["new_bid_cents"] == 300

    def test_return_dict_structure(self, connector):
        """Verify all expected keys are present in the return dict."""
        mock_adset = _make_sdk_object_mock(bid_amount="1000")

        with patch("src.connectors.meta.AdSet", return_value=mock_adset):
            result = connector.update_adset_bid("act_123", "789", 1200)

        assert set(result.keys()) == {"adset_id", "previous_bid_cents", "new_bid_cents"}
        assert result["adset_id"] == "789"

    def test_api_get_failure_raises_meta_write_error(self, connector):
        """If api_get fails (e.g. invalid adset_id), MetaWriteError is raised."""
        from src.connectors.meta import MetaWriteError

        mock_adset = MagicMock()
        mock_adset.api_get.side_effect = _make_facebook_request_error("not found")

        with (
            patch("src.connectors.meta.AdSet", return_value=mock_adset),
            pytest.raises(MetaWriteError, match="Failed to update ad set bid"),
        ):
            connector.update_adset_bid("act_123", "789", 500)
