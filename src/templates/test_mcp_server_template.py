"""Tests for src.mcp_servers.__CHANNEL__ -- __Channel__ MCP tools.

Covers all 5 tools: list___CHANNEL___accounts, get___CHANNEL___campaigns,
get___CHANNEL___performance, get___CHANNEL___insights,
get___CHANNEL___account_activity.

All connector calls are mocked via _get_connector(); no network traffic needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.mcp_servers.__CHANNEL__ import (
    get___CHANNEL___account_activity,
    get___CHANNEL___campaigns,
    get___CHANNEL___insights,
    get___CHANNEL___performance,
    list___CHANNEL___accounts,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PATCH_TARGET = "src.mcp_servers.__CHANNEL__._get_connector"


@pytest.fixture()
def mock_connector():
    """Return a MagicMock standing in for __Channel__Connector."""
    with patch(PATCH_TARGET) as mock_get:
        connector = MagicMock()
        mock_get.return_value = connector
        yield connector


# ---------------------------------------------------------------------------
# Tool 1: list___CHANNEL___accounts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_accounts_happy_path(mock_connector):
    """Returns formatted account info when accounts exist."""
    mock_connector.get_ad_accounts.return_value = [
        {
            "id": "acct_123",
            "name": "Main Account",
            "status": "active",
            "currency": "USD",
            "timezone": "America/New_York",
        },
    ]

    result = await list___CHANNEL___accounts.handler({})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "1 __Channel__ ad account(s)" in text
    assert "Main Account" in text
    assert "acct_123" in text


@pytest.mark.asyncio
async def test_list_accounts_empty(mock_connector):
    """Returns guidance message when no accounts found."""
    mock_connector.get_ad_accounts.return_value = []

    result = await list___CHANNEL___accounts.handler({})
    text = result["content"][0]["text"]

    assert "No __Channel__ ad accounts found" in text
    assert "is_error" not in result


@pytest.mark.asyncio
async def test_list_accounts_error(mock_connector):
    """Returns error response when connector raises."""
    mock_connector.get_ad_accounts.side_effect = RuntimeError("API down")

    result = await list___CHANNEL___accounts.handler({})

    assert result["is_error"] is True
    assert "API down" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Tool 2: get___CHANNEL___campaigns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_campaigns_happy_path(mock_connector):
    """Returns formatted campaign list."""
    mock_connector.get_campaigns.return_value = [
        {
            "id": "camp_1",
            "name": "Brand Awareness",
            "objective": "awareness",
            "status": "active",
            "daily_budget": 50.0,
            "lifetime_budget": None,
        },
    ]

    result = await get___CHANNEL___campaigns.handler({"account_id": "acct_123"})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "Brand Awareness" in text
    assert "camp_1" in text


@pytest.mark.asyncio
async def test_get_campaigns_missing_account_id(mock_connector):
    """Returns error when account_id is missing."""
    result = await get___CHANNEL___campaigns.handler({"account_id": ""})

    assert result["is_error"] is True
    assert "account_id is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_campaigns_empty(mock_connector):
    """Returns message when no campaigns found."""
    mock_connector.get_campaigns.return_value = []

    result = await get___CHANNEL___campaigns.handler({"account_id": "acct_123"})
    text = result["content"][0]["text"]

    assert "No campaigns found" in text


# ---------------------------------------------------------------------------
# Tool 3: get___CHANNEL___performance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_performance_happy_path(mock_connector):
    """Returns formatted performance metrics."""
    mock_connector.get_account_metrics.return_value = [
        {
            "campaign_id": "camp_1",
            "campaign_name": "Brand Awareness",
            "date": "2024-01-15",
            "impressions": 10000,
            "clicks": 250,
            "spend": 125.50,
            "conversions": 10,
            "conversion_value": 500.0,
        },
    ]

    result = await get___CHANNEL___performance.handler({
        "account_id": "acct_123",
        "start_date": "2024-01-15",
        "end_date": "2024-01-15",
    })
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "1 data row(s)" in text


@pytest.mark.asyncio
async def test_get_performance_missing_dates(mock_connector):
    """Returns error when dates are missing."""
    result = await get___CHANNEL___performance.handler({
        "account_id": "acct_123",
        "start_date": "",
        "end_date": "2024-01-15",
    })

    assert result["is_error"] is True
    assert "start_date is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_performance_with_campaign_filter(mock_connector):
    """Filters by campaign_id when provided."""
    mock_connector.get_campaign_metrics.return_value = []

    result = await get___CHANNEL___performance.handler({
        "account_id": "acct_123",
        "start_date": "2024-01-15",
        "end_date": "2024-01-15",
        "campaign_id": "camp_1",
    })
    text = result["content"][0]["text"]

    assert "No performance data" in text
    mock_connector.get_campaign_metrics.assert_called_once()


# ---------------------------------------------------------------------------
# Tool 4: get___CHANNEL___insights
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_insights_missing_params(mock_connector):
    """Returns error when required params are missing."""
    result = await get___CHANNEL___insights.handler({"account_id": "", "campaign_id": "camp_1"})

    assert result["is_error"] is True
    assert "account_id is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_insights_happy_path(mock_connector):
    """Returns insight data for a campaign."""
    result = await get___CHANNEL___insights.handler({
        "account_id": "acct_123",
        "campaign_id": "camp_1",
    })

    # Template returns TODO text — update once implemented
    assert "is_error" not in result


# ---------------------------------------------------------------------------
# Tool 5: get___CHANNEL___account_activity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_activity_happy_path(mock_connector):
    """Returns account activity."""
    result = await get___CHANNEL___account_activity.handler({"account_id": "acct_123"})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "acct_123" in text


@pytest.mark.asyncio
async def test_get_activity_missing_account_id(mock_connector):
    """Returns error when account_id is missing."""
    result = await get___CHANNEL___account_activity.handler({"account_id": ""})

    assert result["is_error"] is True


@pytest.mark.asyncio
async def test_get_activity_clamps_days(mock_connector):
    """Days parameter is clamped to valid range."""
    result = await get___CHANNEL___account_activity.handler({
        "account_id": "acct_123",
        "days": 100,
    })

    # Should clamp to 30 max
    assert "is_error" not in result


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def test_create_tools():
    """create___CHANNEL___tools returns 5 tools."""
    from src.mcp_servers.__CHANNEL__ import create___CHANNEL___tools

    tools = create___CHANNEL___tools()
    assert len(tools) == 5


def test_create_mcp_server():
    """create___CHANNEL___mcp_server returns a valid config."""
    from src.mcp_servers.__CHANNEL__ import create___CHANNEL___mcp_server

    server = create___CHANNEL___mcp_server()
    assert server is not None
