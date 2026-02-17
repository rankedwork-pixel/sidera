"""Tests for src.mcp_servers.google_ads — Google Ads MCP tools.

Covers all 5 tools: list_google_ads_accounts, get_google_ads_campaigns,
get_google_ads_performance, get_google_ads_changes, get_google_ads_recommendations.

All connector calls are mocked via _get_connector(); no network traffic needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.mcp_servers.google_ads import (
    get_google_ads_campaigns,
    get_google_ads_changes,
    get_google_ads_performance,
    get_google_ads_recommendations,
    list_google_ads_accounts,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PATCH_TARGET = "src.mcp_servers.google_ads._get_connector"


@pytest.fixture()
def mock_connector():
    """Return a MagicMock standing in for GoogleAdsConnector."""
    with patch(PATCH_TARGET) as mock_get:
        connector = MagicMock()
        mock_get.return_value = connector
        yield connector


# ---------------------------------------------------------------------------
# Tool 1: list_google_ads_accounts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_accounts_happy_path(mock_connector):
    """Returns formatted account info when accounts exist."""
    mock_connector.get_accessible_accounts.return_value = ["1234567890", "9876543210"]
    mock_connector.get_account_info.side_effect = [
        {
            "descriptive_name": "My Brand Account",
            "currency": "USD",
            "timezone": "America/New_York",
        },
        {
            "descriptive_name": "EU Campaigns",
            "currency": "EUR",
            "timezone": "Europe/Berlin",
        },
    ]

    result = await list_google_ads_accounts.handler({})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "2 accessible account(s)" in text
    assert "My Brand Account" in text
    assert "1234567890" in text
    assert "USD" in text
    assert "EU Campaigns" in text
    assert "EUR" in text


@pytest.mark.asyncio
async def test_list_accounts_empty(mock_connector):
    """Returns guidance message when no accounts found."""
    mock_connector.get_accessible_accounts.return_value = []

    result = await list_google_ads_accounts.handler({})
    text = result["content"][0]["text"]

    assert "No Google Ads accounts found" in text
    assert "is_error" not in result


@pytest.mark.asyncio
async def test_list_accounts_no_account_info(mock_connector):
    """Falls back to just account ID when get_account_info returns None."""
    mock_connector.get_accessible_accounts.return_value = ["1111111111"]
    mock_connector.get_account_info.return_value = None

    result = await list_google_ads_accounts.handler({})
    text = result["content"][0]["text"]

    assert "Manager Account / MCC (ID: 1111111111)" in text


@pytest.mark.asyncio
async def test_list_accounts_connector_error(mock_connector):
    """Returns error response when connector raises."""
    mock_connector.get_accessible_accounts.side_effect = RuntimeError("API down")

    result = await list_google_ads_accounts.handler({})

    assert result["is_error"] is True
    assert "ERROR" in result["content"][0]["text"]
    assert "API down" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_list_accounts_partial_info(mock_connector):
    """Handles accounts where name/currency/timezone are missing."""
    mock_connector.get_accessible_accounts.return_value = ["5555555555"]
    mock_connector.get_account_info.return_value = {
        "descriptive_name": "",
        "name": "Fallback Name",
        "currency": "",
        "timezone": "",
    }

    result = await list_google_ads_accounts.handler({})
    text = result["content"][0]["text"]

    assert "Fallback Name" in text
    # No currency/timezone lines when empty
    assert "Currency:" not in text
    assert "Timezone:" not in text


# ---------------------------------------------------------------------------
# Tool 2: get_google_ads_campaigns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_campaigns_happy_path(mock_connector):
    """Returns formatted campaign list with all fields."""
    mock_connector.get_campaigns.return_value = [
        {
            "id": "111",
            "name": "Brand Search",
            "type": "search",
            "status": "enabled",
            "daily_budget": 50.00,
            "bid_strategy": "target_cpa",
        },
        {
            "id": "222",
            "name": "PMax Ecommerce",
            "type": "pmax",
            "status": "paused",
            "daily_budget": 100.00,
            "bid_strategy": "maximize_conversions",
        },
    ]

    result = await get_google_ads_campaigns.handler({"customer_id": "1234567890"})
    text = result["content"][0]["text"]

    assert "2 campaign(s)" in text
    assert "Brand Search" in text
    assert "search" in text
    assert "$50.00" in text
    assert "PMax Ecommerce" in text
    assert "paused" in text


@pytest.mark.asyncio
async def test_get_campaigns_empty(mock_connector):
    """Returns message when no campaigns found."""
    mock_connector.get_campaigns.return_value = []

    result = await get_google_ads_campaigns.handler({"customer_id": "1234567890"})
    text = result["content"][0]["text"]

    assert "No campaigns found" in text
    assert "1234567890" in text


@pytest.mark.asyncio
async def test_get_campaigns_missing_customer_id():
    """Returns error when customer_id is not provided."""
    result = await get_google_ads_campaigns.handler({})

    assert result["is_error"] is True
    assert "customer_id is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_campaigns_empty_customer_id():
    """Returns error when customer_id is empty string."""
    result = await get_google_ads_campaigns.handler({"customer_id": "  "})

    assert result["is_error"] is True
    assert "customer_id is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_campaigns_connector_error(mock_connector):
    """Returns error response when connector raises."""
    mock_connector.get_campaigns.side_effect = RuntimeError("connection failed")

    result = await get_google_ads_campaigns.handler({"customer_id": "1234567890"})

    assert result["is_error"] is True
    assert "connection failed" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_campaigns_alternative_keys(mock_connector):
    """Handles campaigns using alternative key names."""
    mock_connector.get_campaigns.return_value = [
        {
            "campaign_id": "333",
            "campaign_name": "Alt Campaign",
            "type": "display",
            "status": "ENABLED",
            "daily_budget": None,
            "bid_strategy": "manual_cpc",
        },
    ]

    result = await get_google_ads_campaigns.handler({"customer_id": "1234567890"})
    text = result["content"][0]["text"]

    assert "Alt Campaign" in text
    assert "N/A" in text  # None daily_budget formats as N/A


# ---------------------------------------------------------------------------
# Tool 3: get_google_ads_performance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_performance_happy_path(mock_connector):
    """Returns aggregated metrics and daily breakdown."""
    mock_connector.get_account_metrics.return_value = [
        {
            "metrics.impressions": 15000,
            "metrics.clicks": 800,
            "metrics.cost_micros": 12000000,
            "metrics.conversions": 40.0,
            "metrics.conversions_value": 8000.0,
            "segments.date": "2025-01-15",
            "campaign.name": "Brand Search",
        },
        {
            "metrics.impressions": 10000,
            "metrics.clicks": 500,
            "metrics.cost_micros": 8000000,
            "metrics.conversions": 25.0,
            "metrics.conversions_value": 5000.0,
            "segments.date": "2025-01-16",
            "campaign.name": "PMax",
        },
    ]

    result = await get_google_ads_performance.handler(
        {
            "customer_id": "1234567890",
            "start_date": "2025-01-15",
            "end_date": "2025-01-16",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "Performance for account 1234567890" in text
    assert "all campaigns" in text
    assert "TOTALS:" in text
    assert "DAILY BREAKDOWN:" in text
    assert "2 data row(s)" in text
    # Check aggregated totals: total spend = (12M + 8M) / 1M = $20
    assert "$20.00" in text
    # Total clicks = 800 + 500 = 1,300
    assert "1,300" in text


@pytest.mark.asyncio
async def test_get_performance_with_campaign_id(mock_connector):
    """Filters by campaign_id when provided."""
    mock_connector.get_campaign_metrics.return_value = [
        {
            "metrics.impressions": 5000,
            "metrics.clicks": 200,
            "metrics.cost_micros": 4000000,
            "metrics.conversions": 10.0,
            "metrics.conversions_value": 2000.0,
            "segments.date": "2025-01-15",
            "campaign.name": "Specific Campaign",
        },
    ]

    result = await get_google_ads_performance.handler(
        {
            "customer_id": "1234567890",
            "start_date": "2025-01-15",
            "end_date": "2025-01-15",
            "campaign_id": "999",
        }
    )
    text = result["content"][0]["text"]

    assert "campaign 999" in text
    mock_connector.get_campaign_metrics.assert_called_once_with(
        "1234567890", "999", "2025-01-15", "2025-01-15"
    )


@pytest.mark.asyncio
async def test_get_performance_empty_data(mock_connector):
    """Returns message when no data found."""
    mock_connector.get_account_metrics.return_value = []

    result = await get_google_ads_performance.handler(
        {
            "customer_id": "1234567890",
            "start_date": "2025-01-01",
            "end_date": "2025-01-07",
        }
    )
    text = result["content"][0]["text"]

    assert "No performance data found" in text


@pytest.mark.asyncio
async def test_get_performance_missing_customer_id():
    """Returns error when customer_id is missing."""
    result = await get_google_ads_performance.handler(
        {
            "start_date": "2025-01-01",
            "end_date": "2025-01-07",
        }
    )

    assert result["is_error"] is True
    assert "customer_id is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_performance_missing_start_date():
    """Returns error when start_date is missing."""
    result = await get_google_ads_performance.handler(
        {
            "customer_id": "1234567890",
            "end_date": "2025-01-07",
        }
    )

    assert result["is_error"] is True
    assert "start_date is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_performance_missing_end_date():
    """Returns error when end_date is missing."""
    result = await get_google_ads_performance.handler(
        {
            "customer_id": "1234567890",
            "start_date": "2025-01-01",
        }
    )

    assert result["is_error"] is True
    assert "end_date is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_performance_connector_error(mock_connector):
    """Returns error response when connector raises."""
    mock_connector.get_account_metrics.side_effect = RuntimeError("timeout")

    result = await get_google_ads_performance.handler(
        {
            "customer_id": "1234567890",
            "start_date": "2025-01-01",
            "end_date": "2025-01-07",
        }
    )

    assert result["is_error"] is True
    assert "timeout" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_performance_roas_calculation(mock_connector):
    """Verifies ROAS is calculated correctly in the output."""
    mock_connector.get_account_metrics.return_value = [
        {
            "metrics.impressions": 1000,
            "metrics.clicks": 100,
            "metrics.cost_micros": 10000000,  # $10
            "metrics.conversions": 5.0,
            "metrics.conversions_value": 50.0,  # ROAS = 50/10 = 5.0x
            "segments.date": "2025-01-15",
        },
    ]

    result = await get_google_ads_performance.handler(
        {
            "customer_id": "1234567890",
            "start_date": "2025-01-15",
            "end_date": "2025-01-15",
        }
    )
    text = result["content"][0]["text"]

    assert "5.00x" in text


# ---------------------------------------------------------------------------
# Tool 4: get_google_ads_changes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_changes_happy_path(mock_connector):
    """Returns formatted change history."""
    mock_connector.get_change_history.return_value = [
        {
            "change_date_time": "2025-01-15 10:30:00",
            "change_resource_type": "CAMPAIGN",
            "resource_name": "Brand Search",
            "operation": "UPDATE",
            "old_value": {"budget": 50},
            "new_value": {"budget": 75},
            "feed": "user@example.com",
        },
    ]

    result = await get_google_ads_changes.handler({"customer_id": "1234567890"})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "1 change(s) found" in text
    assert "CAMPAIGN" in text
    assert "Brand Search" in text
    assert "UPDATE" in text
    assert "user@example.com" in text


@pytest.mark.asyncio
async def test_get_changes_empty(mock_connector):
    """Returns stability message when no changes found."""
    mock_connector.get_change_history.return_value = []

    result = await get_google_ads_changes.handler({"customer_id": "1234567890"})
    text = result["content"][0]["text"]

    assert "No changes found" in text
    assert "stable" in text


@pytest.mark.asyncio
async def test_get_changes_missing_customer_id():
    """Returns error when customer_id is missing."""
    result = await get_google_ads_changes.handler({})

    assert result["is_error"] is True
    assert "customer_id is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_changes_custom_days(mock_connector):
    """Passes custom days parameter to connector."""
    mock_connector.get_change_history.return_value = []

    result = await get_google_ads_changes.handler({"customer_id": "1234567890", "days": 14})
    text = result["content"][0]["text"]

    mock_connector.get_change_history.assert_called_once_with("1234567890", days=14)
    assert "14 day(s)" in text


@pytest.mark.asyncio
async def test_get_changes_days_clamped_to_max(mock_connector):
    """Days value is clamped to max 30."""
    mock_connector.get_change_history.return_value = []

    await get_google_ads_changes.handler({"customer_id": "1234567890", "days": 100})

    mock_connector.get_change_history.assert_called_once_with("1234567890", days=30)


@pytest.mark.asyncio
async def test_get_changes_days_clamped_to_min(mock_connector):
    """Days value is clamped to min 1."""
    mock_connector.get_change_history.return_value = []

    await get_google_ads_changes.handler({"customer_id": "1234567890", "days": -5})

    mock_connector.get_change_history.assert_called_once_with("1234567890", days=1)


@pytest.mark.asyncio
async def test_get_changes_invalid_days_defaults_to_7(mock_connector):
    """Non-numeric days defaults to 7."""
    mock_connector.get_change_history.return_value = []

    await get_google_ads_changes.handler({"customer_id": "1234567890", "days": "invalid"})

    mock_connector.get_change_history.assert_called_once_with("1234567890", days=7)


@pytest.mark.asyncio
async def test_get_changes_connector_error(mock_connector):
    """Returns error response when connector raises."""
    mock_connector.get_change_history.side_effect = RuntimeError("API error")

    result = await get_google_ads_changes.handler({"customer_id": "1234567890"})

    assert result["is_error"] is True
    assert "API error" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Tool 5: get_google_ads_recommendations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_recommendations_happy_path(mock_connector):
    """Returns formatted recommendations list."""
    mock_connector.get_recommendations.return_value = [
        {
            "type": "KEYWORD",
            "campaign_name": "Brand Search",
            "description": "Add 'running shoes' as a keyword",
            "impact": {"clicks_change": "+15%", "cost_change": "+$200"},
        },
        {
            "type": "TARGET_CPA_OPT_IN",
            "campaign_name": "PMax",
            "description": "Switch to Target CPA",
            "impact": {"conversions_change": "+10%"},
        },
    ]

    result = await get_google_ads_recommendations.handler({"customer_id": "1234567890"})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "2 recommendation(s)" in text
    assert "KEYWORD" in text
    assert "Brand Search" in text
    assert "TARGET_CPA_OPT_IN" in text
    assert "Evaluate each against" in text


@pytest.mark.asyncio
async def test_get_recommendations_empty(mock_connector):
    """Returns message when no recommendations found."""
    mock_connector.get_recommendations.return_value = []

    result = await get_google_ads_recommendations.handler({"customer_id": "1234567890"})
    text = result["content"][0]["text"]

    assert "No recommendations" in text


@pytest.mark.asyncio
async def test_get_recommendations_missing_customer_id():
    """Returns error when customer_id is missing."""
    result = await get_google_ads_recommendations.handler({})

    assert result["is_error"] is True
    assert "customer_id is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_recommendations_connector_error(mock_connector):
    """Returns error response when connector raises."""
    mock_connector.get_recommendations.side_effect = RuntimeError("quota exceeded")

    result = await get_google_ads_recommendations.handler({"customer_id": "1234567890"})

    assert result["is_error"] is True
    assert "quota exceeded" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_recommendations_with_string_impact(mock_connector):
    """Handles recommendations where impact is a raw string."""
    mock_connector.get_recommendations.return_value = [
        {
            "type": "BUDGET",
            "campaign_name": "Test",
            "description": "Increase budget",
            "impact": "medium",
        },
    ]

    result = await get_google_ads_recommendations.handler({"customer_id": "1234567890"})
    text = result["content"][0]["text"]

    assert "medium" in text


@pytest.mark.asyncio
async def test_get_recommendations_extra_fields(mock_connector):
    """Extra fields in recommendation are shown in Details."""
    mock_connector.get_recommendations.return_value = [
        {
            "type": "KEYWORD",
            "description": "Add keyword",
            "impact": {},
            "keyword": "running shoes",
            "match_type": "BROAD",
        },
    ]

    result = await get_google_ads_recommendations.handler({"customer_id": "1234567890"})
    text = result["content"][0]["text"]

    assert "Details:" in text
    assert "running shoes" in text
