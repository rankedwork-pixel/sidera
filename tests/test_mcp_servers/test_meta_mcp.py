"""Tests for src.mcp_servers.meta — Meta MCP tools.

Covers all 5 tools: list_meta_ad_accounts, get_meta_campaigns,
get_meta_performance, get_meta_audience_insights, get_meta_account_activity.

All connector calls are mocked via _get_connector(); no network traffic needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.mcp_servers.meta import (
    get_meta_account_activity,
    get_meta_audience_insights,
    get_meta_campaigns,
    get_meta_performance,
    list_meta_ad_accounts,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PATCH_TARGET = "src.mcp_servers.meta._get_connector"


@pytest.fixture()
def mock_connector():
    """Return a MagicMock standing in for MetaConnector."""
    with patch(PATCH_TARGET) as mock_get:
        connector = MagicMock()
        mock_get.return_value = connector
        yield connector


# ---------------------------------------------------------------------------
# Tool 1: list_meta_ad_accounts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_accounts_happy_path(mock_connector):
    """Returns formatted account info when accounts exist."""
    mock_connector.get_ad_accounts.return_value = [
        {
            "id": "act_123456789",
            "name": "Ecommerce Store",
            "account_status": 1,
            "currency": "USD",
            "timezone_name": "America/New_York",
        },
        {
            "id": "act_987654321",
            "name": "Lead Gen",
            "account_status": 2,
            "currency": "EUR",
            "timezone_name": "Europe/London",
        },
    ]

    result = await list_meta_ad_accounts.handler({})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "2 Meta ad account(s)" in text
    assert "Ecommerce Store" in text
    assert "act_123456789" in text
    assert "active" in text
    assert "Lead Gen" in text
    assert "disabled" in text
    assert "EUR" in text


@pytest.mark.asyncio
async def test_list_accounts_empty(mock_connector):
    """Returns guidance message when no accounts found."""
    mock_connector.get_ad_accounts.return_value = []

    result = await list_meta_ad_accounts.handler({})
    text = result["content"][0]["text"]

    assert "No Meta ad accounts found" in text
    assert "is_error" not in result


@pytest.mark.asyncio
async def test_list_accounts_connector_error(mock_connector):
    """Returns error response when connector raises."""
    mock_connector.get_ad_accounts.side_effect = RuntimeError("API down")

    result = await list_meta_ad_accounts.handler({})

    assert result["is_error"] is True
    assert "API down" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_list_accounts_unknown_status_code(mock_connector):
    """Handles unknown integer status codes gracefully."""
    mock_connector.get_ad_accounts.return_value = [
        {
            "id": "act_111",
            "name": "Test Account",
            "account_status": 999,
            "currency": "GBP",
            "timezone_name": "",
        },
    ]

    result = await list_meta_ad_accounts.handler({})
    text = result["content"][0]["text"]

    assert "999" in text
    assert "Timezone:" not in text  # empty timezone not shown


@pytest.mark.asyncio
async def test_list_accounts_string_status(mock_connector):
    """Handles string status values (non-integer)."""
    mock_connector.get_ad_accounts.return_value = [
        {
            "id": "act_222",
            "name": "String Status",
            "account_status": "ACTIVE",
            "currency": "",
            "timezone_name": "",
        },
    ]

    result = await list_meta_ad_accounts.handler({})
    text = result["content"][0]["text"]

    assert "ACTIVE" in text
    assert "Currency:" not in text


# ---------------------------------------------------------------------------
# Tool 2: get_meta_campaigns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_campaigns_happy_path(mock_connector):
    """Returns formatted campaign list with all fields."""
    mock_connector.get_campaigns.return_value = [
        {
            "id": "120330000000123456",
            "name": "Prospecting - Lookalike",
            "objective": "sales",
            "status": "active",
            "daily_budget": 50.00,
            "lifetime_budget": None,
            "bid_strategy": "lowest_cost",
        },
        {
            "id": "120330000000789012",
            "name": "Retargeting",
            "objective": "traffic",
            "status": "paused",
            "daily_budget": None,
            "lifetime_budget": 1000.00,
            "bid_strategy": "cost_cap",
        },
    ]

    result = await get_meta_campaigns.handler({"account_id": "act_123456789"})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "2 campaign(s)" in text
    assert "Prospecting - Lookalike" in text
    assert "sales" in text
    assert "$50.00" in text
    assert "Retargeting" in text
    assert "Lifetime budget: $1,000.00" in text


@pytest.mark.asyncio
async def test_get_campaigns_empty(mock_connector):
    """Returns message when no campaigns found."""
    mock_connector.get_campaigns.return_value = []

    result = await get_meta_campaigns.handler({"account_id": "act_123456789"})
    text = result["content"][0]["text"]

    assert "No campaigns found" in text


@pytest.mark.asyncio
async def test_get_campaigns_missing_account_id():
    """Returns error when account_id is not provided."""
    result = await get_meta_campaigns.handler({})

    assert result["is_error"] is True
    assert "account_id is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_campaigns_empty_account_id():
    """Returns error when account_id is empty string."""
    result = await get_meta_campaigns.handler({"account_id": "  "})

    assert result["is_error"] is True
    assert "account_id is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_campaigns_connector_error(mock_connector):
    """Returns error response when connector raises."""
    mock_connector.get_campaigns.side_effect = RuntimeError("connection refused")

    result = await get_meta_campaigns.handler({"account_id": "act_123456789"})

    assert result["is_error"] is True
    assert "connection refused" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_campaigns_no_budgets(mock_connector):
    """Handles campaigns with no daily or lifetime budget."""
    mock_connector.get_campaigns.return_value = [
        {
            "id": "333",
            "name": "No Budget",
            "objective": "awareness",
            "status": "active",
            "daily_budget": None,
            "lifetime_budget": None,
            "bid_strategy": "",
        },
    ]

    result = await get_meta_campaigns.handler({"account_id": "act_123"})
    text = result["content"][0]["text"]

    assert "No Budget" in text
    # Neither budget line should appear
    assert "Daily budget:" not in text
    assert "Lifetime budget:" not in text


# ---------------------------------------------------------------------------
# Tool 3: get_meta_performance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_performance_happy_path(mock_connector):
    """Returns aggregated metrics and daily breakdown."""
    mock_connector.get_account_metrics.return_value = [
        {
            "impressions": "28500",
            "clicks": "1120",
            "spend": "48.75",
            "date": "2025-01-15",
            "campaign_name": "Prospecting",
            "actions": [
                {"action_type": "purchase", "value": "32"},
            ],
            "action_values": [
                {"action_type": "purchase", "value": "4850.00"},
            ],
        },
    ]

    result = await get_meta_performance.handler(
        {
            "account_id": "act_123456789",
            "start_date": "2025-01-15",
            "end_date": "2025-01-15",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "Performance for account act_123456789" in text
    assert "all campaigns" in text
    assert "TOTALS:" in text
    assert "DAILY BREAKDOWN:" in text
    assert "1 data row(s)" in text


@pytest.mark.asyncio
async def test_get_performance_with_campaign_id(mock_connector):
    """Filters by campaign_id when provided."""
    mock_connector.get_campaign_metrics.return_value = [
        {
            "impressions": "5000",
            "clicks": "200",
            "spend": "25.00",
            "date": "2025-01-15",
            "campaign_name": "Specific Campaign",
            "actions": [],
            "action_values": [],
        },
    ]

    result = await get_meta_performance.handler(
        {
            "account_id": "act_123456789",
            "start_date": "2025-01-15",
            "end_date": "2025-01-15",
            "campaign_id": "999",
        }
    )
    text = result["content"][0]["text"]

    assert "campaign 999" in text
    mock_connector.get_campaign_metrics.assert_called_once_with(
        "act_123456789", "999", "2025-01-15", "2025-01-15"
    )


@pytest.mark.asyncio
async def test_get_performance_empty_data(mock_connector):
    """Returns message when no data found."""
    mock_connector.get_account_metrics.return_value = []

    result = await get_meta_performance.handler(
        {
            "account_id": "act_123456789",
            "start_date": "2025-01-01",
            "end_date": "2025-01-07",
        }
    )
    text = result["content"][0]["text"]

    assert "No performance data found" in text


@pytest.mark.asyncio
async def test_get_performance_missing_account_id():
    """Returns error when account_id is missing."""
    result = await get_meta_performance.handler(
        {
            "start_date": "2025-01-01",
            "end_date": "2025-01-07",
        }
    )

    assert result["is_error"] is True
    assert "account_id is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_performance_missing_start_date():
    """Returns error when start_date is missing."""
    result = await get_meta_performance.handler(
        {
            "account_id": "act_123456789",
            "end_date": "2025-01-07",
        }
    )

    assert result["is_error"] is True
    assert "start_date is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_performance_missing_end_date():
    """Returns error when end_date is missing."""
    result = await get_meta_performance.handler(
        {
            "account_id": "act_123456789",
            "start_date": "2025-01-01",
        }
    )

    assert result["is_error"] is True
    assert "end_date is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_performance_connector_error(mock_connector):
    """Returns error response when connector raises."""
    mock_connector.get_account_metrics.side_effect = RuntimeError("timeout")

    result = await get_meta_performance.handler(
        {
            "account_id": "act_123456789",
            "start_date": "2025-01-01",
            "end_date": "2025-01-07",
        }
    )

    assert result["is_error"] is True
    assert "timeout" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_performance_no_conversions(mock_connector):
    """Handles rows with no actions or action_values."""
    mock_connector.get_account_metrics.return_value = [
        {
            "impressions": "1000",
            "clicks": "50",
            "spend": "10.00",
            "date": "2025-01-15",
            "campaign_name": "Awareness",
            "actions": None,
            "action_values": None,
        },
    ]

    result = await get_meta_performance.handler(
        {
            "account_id": "act_123456789",
            "start_date": "2025-01-15",
            "end_date": "2025-01-15",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "TOTALS:" in text


# ---------------------------------------------------------------------------
# Tool 4: get_meta_audience_insights
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audience_insights_happy_path(mock_connector):
    """Returns formatted audience breakdown."""
    mock_connector.get_campaign_insights.return_value = [
        {
            "campaign_name": "Prospecting",
            "age": "25-34",
            "spend": "25.00",
            "impressions": 10000,
            "clicks": 400,
            "conversions": 15,
            "conversion_value": 2500.00,
        },
        {
            "campaign_name": "Prospecting",
            "age": "35-44",
            "spend": "18.00",
            "impressions": 8000,
            "clicks": 300,
            "conversions": 10,
            "conversion_value": 1800.00,
        },
    ]

    result = await get_meta_audience_insights.handler(
        {
            "account_id": "act_123456789",
            "campaign_id": "999",
            "breakdown": "age",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "Breakdown by: age" in text
    assert "25-34" in text
    assert "35-44" in text
    assert "2 segment(s)" in text

    # Verify sorted by spend descending (25 > 18)
    pos_25_34 = text.index("25-34")
    pos_35_44 = text.index("35-44")
    assert pos_25_34 < pos_35_44


@pytest.mark.asyncio
async def test_audience_insights_empty(mock_connector):
    """Returns message when no insights found."""
    mock_connector.get_campaign_insights.return_value = []

    result = await get_meta_audience_insights.handler(
        {
            "account_id": "act_123456789",
            "campaign_id": "999",
            "breakdown": "gender",
        }
    )
    text = result["content"][0]["text"]

    assert "No audience insights found" in text


@pytest.mark.asyncio
async def test_audience_insights_missing_account_id():
    """Returns error when account_id is missing."""
    result = await get_meta_audience_insights.handler(
        {
            "campaign_id": "999",
            "breakdown": "age",
        }
    )

    assert result["is_error"] is True
    assert "account_id is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_audience_insights_missing_campaign_id():
    """Returns error when campaign_id is missing."""
    result = await get_meta_audience_insights.handler(
        {
            "account_id": "act_123456789",
            "breakdown": "age",
        }
    )

    assert result["is_error"] is True
    assert "campaign_id is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_audience_insights_missing_breakdown():
    """Returns error when breakdown is missing."""
    result = await get_meta_audience_insights.handler(
        {
            "account_id": "act_123456789",
            "campaign_id": "999",
        }
    )

    assert result["is_error"] is True
    assert "breakdown is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_audience_insights_invalid_breakdown():
    """Returns error for invalid breakdown value."""
    result = await get_meta_audience_insights.handler(
        {
            "account_id": "act_123456789",
            "campaign_id": "999",
            "breakdown": "invalid_dimension",
        }
    )

    assert result["is_error"] is True
    assert "Invalid breakdown" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_audience_insights_connector_error(mock_connector):
    """Returns error response when connector raises."""
    mock_connector.get_campaign_insights.side_effect = RuntimeError("rate limit")

    result = await get_meta_audience_insights.handler(
        {
            "account_id": "act_123456789",
            "campaign_id": "999",
            "breakdown": "age",
        }
    )

    assert result["is_error"] is True
    assert "rate limit" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_audience_insights_platform_breakdown(mock_connector):
    """Works with publisher_platform breakdown."""
    mock_connector.get_campaign_insights.return_value = [
        {
            "campaign_name": "Cross-Platform",
            "publisher_platform": "facebook",
            "spend": "30.00",
            "impressions": 12000,
            "clicks": 500,
            "conversions": 20,
            "conversion_value": 3000.00,
        },
        {
            "campaign_name": "Cross-Platform",
            "publisher_platform": "instagram",
            "spend": "20.00",
            "impressions": 8000,
            "clicks": 350,
            "conversions": 12,
            "conversion_value": 1800.00,
        },
    ]

    result = await get_meta_audience_insights.handler(
        {
            "account_id": "act_123456789",
            "campaign_id": "999",
            "breakdown": "publisher_platform",
        }
    )
    text = result["content"][0]["text"]

    assert "facebook" in text
    assert "instagram" in text
    assert "Breakdown by: publisher_platform" in text


# ---------------------------------------------------------------------------
# Tool 5: get_meta_account_activity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_activity_happy_path(mock_connector):
    """Returns formatted activity list."""
    mock_connector.get_account_activity.return_value = [
        {
            "type": "new_campaign",
            "campaign_id": "111",
            "campaign_name": "New Prospecting",
            "description": "Campaign started running in the current period.",
            "current_spend": 500.00,
        },
        {
            "type": "spend_change",
            "campaign_id": "222",
            "campaign_name": "Retargeting",
            "description": "Spend changed by +45.0% ($200.00 -> $290.00)",
            "change_pct": 45.0,
            "current_spend": 290.00,
            "previous_spend": 200.00,
        },
    ]

    result = await get_meta_account_activity.handler({"account_id": "act_123456789"})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "2 notable change(s)" in text
    assert "NEW CAMPAIGN" in text
    assert "New Prospecting" in text
    assert "SPEND CHANGE" in text
    assert "+45.0%" in text


@pytest.mark.asyncio
async def test_get_activity_empty(mock_connector):
    """Returns stability message when no activity found."""
    mock_connector.get_account_activity.return_value = []

    result = await get_meta_account_activity.handler({"account_id": "act_123456789"})
    text = result["content"][0]["text"]

    assert "No significant changes found" in text
    assert "stable" in text


@pytest.mark.asyncio
async def test_get_activity_missing_account_id():
    """Returns error when account_id is missing."""
    result = await get_meta_account_activity.handler({})

    assert result["is_error"] is True
    assert "account_id is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_activity_custom_days(mock_connector):
    """Passes custom days parameter to connector."""
    mock_connector.get_account_activity.return_value = []

    result = await get_meta_account_activity.handler({"account_id": "act_123", "days": 14})
    text = result["content"][0]["text"]

    mock_connector.get_account_activity.assert_called_once_with("act_123", days=14)
    assert "14 day(s)" in text


@pytest.mark.asyncio
async def test_get_activity_days_clamped_to_max(mock_connector):
    """Days value is clamped to max 30."""
    mock_connector.get_account_activity.return_value = []

    await get_meta_account_activity.handler({"account_id": "act_123", "days": 100})

    mock_connector.get_account_activity.assert_called_once_with("act_123", days=30)


@pytest.mark.asyncio
async def test_get_activity_days_clamped_to_min(mock_connector):
    """Days value is clamped to min 1."""
    mock_connector.get_account_activity.return_value = []

    await get_meta_account_activity.handler({"account_id": "act_123", "days": -5})

    mock_connector.get_account_activity.assert_called_once_with("act_123", days=1)


@pytest.mark.asyncio
async def test_get_activity_invalid_days_defaults_to_7(mock_connector):
    """Non-numeric days defaults to 7."""
    mock_connector.get_account_activity.return_value = []

    await get_meta_account_activity.handler({"account_id": "act_123", "days": "invalid"})

    mock_connector.get_account_activity.assert_called_once_with("act_123", days=7)


@pytest.mark.asyncio
async def test_get_activity_connector_error(mock_connector):
    """Returns error response when connector raises."""
    mock_connector.get_account_activity.side_effect = RuntimeError("API error")

    result = await get_meta_account_activity.handler({"account_id": "act_123"})

    assert result["is_error"] is True
    assert "API error" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_activity_stopped_campaign(mock_connector):
    """Formats stopped campaign activity correctly."""
    mock_connector.get_account_activity.return_value = [
        {
            "type": "stopped_campaign",
            "campaign_id": "333",
            "campaign_name": "Old Campaign",
            "description": "Campaign stopped running in the current period.",
            "previous_spend": 150.00,
        },
    ]

    result = await get_meta_account_activity.handler({"account_id": "act_123"})
    text = result["content"][0]["text"]

    assert "STOPPED CAMPAIGN" in text
    assert "Old Campaign" in text
    assert "$150.00" in text
