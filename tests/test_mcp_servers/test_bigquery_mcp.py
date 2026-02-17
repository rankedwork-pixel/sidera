"""Tests for src.mcp_servers.bigquery -- BigQuery MCP tools.

Covers all 5 tools: discover_bigquery_tables, get_business_goals,
get_backend_performance, get_budget_pacing, get_campaign_attribution.

All connector calls are mocked via _get_connector(); no network traffic needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.mcp_servers.bigquery import (
    discover_bigquery_tables,
    get_backend_performance,
    get_budget_pacing,
    get_business_goals,
    get_campaign_attribution,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PATCH_TARGET = "src.mcp_servers.bigquery._get_connector"


@pytest.fixture()
def mock_connector():
    """Return a MagicMock standing in for BigQueryConnector."""
    with patch(PATCH_TARGET) as mock_get:
        connector = MagicMock()
        mock_get.return_value = connector
        yield connector


# ---------------------------------------------------------------------------
# Tool 1: discover_bigquery_tables
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_tables_happy_path(mock_connector):
    """Returns formatted table info when tables exist."""
    mock_connector.discover_tables.return_value = [
        {
            "table_name": "orders",
            "table_type": "TABLE",
            "row_count": 50000,
            "description": "Order data",
        },
        {
            "table_name": "goals",
            "table_type": "TABLE",
            "row_count": 12,
            "description": "Business goals",
        },
    ]

    result = await discover_bigquery_tables.handler({})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "2 table(s)" in text
    assert "orders" in text
    assert "goals" in text
    assert "50,000" in text
    assert "Order data" in text


@pytest.mark.asyncio
async def test_discover_tables_empty(mock_connector):
    """Returns guidance message when no tables found."""
    mock_connector.discover_tables.return_value = []

    result = await discover_bigquery_tables.handler({})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "No tables found" in text
    assert "empty" in text or "lack access" in text


@pytest.mark.asyncio
async def test_discover_tables_error(mock_connector):
    """Returns error response when connector raises."""
    mock_connector.discover_tables.side_effect = RuntimeError("BigQuery unavailable")

    result = await discover_bigquery_tables.handler({})

    assert result["is_error"] is True
    assert "ERROR" in result["content"][0]["text"]
    assert "BigQuery unavailable" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_discover_tables_partial_data(mock_connector):
    """Handles tables with missing optional fields gracefully."""
    mock_connector.discover_tables.return_value = [
        {"table_name": "raw_events", "table_type": "VIEW"},
        {"table_name": "sessions"},
    ]

    result = await discover_bigquery_tables.handler({})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "2 table(s)" in text
    assert "raw_events" in text
    assert "VIEW" in text
    assert "sessions" in text
    # row_count missing -> N/A
    assert "N/A" in text


@pytest.mark.asyncio
async def test_discover_tables_no_args(mock_connector):
    """Tool works with empty args dict (no required parameters)."""
    mock_connector.discover_tables.return_value = [
        {
            "table_name": "products",
            "table_type": "TABLE",
            "row_count": 100,
            "description": "Product catalog",
        },
    ]

    result = await discover_bigquery_tables.handler({})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "1 table(s)" in text
    assert "products" in text


@pytest.mark.asyncio
async def test_discover_tables_no_description(mock_connector):
    """Table without a description does not output a Description line."""
    mock_connector.discover_tables.return_value = [
        {"table_name": "temp", "table_type": "TABLE", "row_count": 5},
    ]

    result = await discover_bigquery_tables.handler({})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "temp" in text
    # No description field -> no "Description:" line
    assert "Description:" not in text


# ---------------------------------------------------------------------------
# Tool 2: get_business_goals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_goals_happy_path(mock_connector):
    """Returns formatted goals with targets."""
    mock_connector.get_goals.return_value = [
        {
            "period": "2025-Q1",
            "channel": "all",
            "revenue_target": 500000,
            "cpa_target": 25.50,
            "roas_target": 4.0,
            "budget_planned": 125000,
        },
    ]

    result = await get_business_goals.handler({})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "1 target(s)" in text
    assert "2025-Q1" in text
    assert "$500,000.00" in text
    assert "$25.50" in text
    assert "4.00x" in text
    assert "$125,000.00" in text


@pytest.mark.asyncio
async def test_get_goals_empty(mock_connector):
    """Returns guidance message when no goals found."""
    mock_connector.get_goals.return_value = []

    result = await get_business_goals.handler({})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "No business goals found" in text


@pytest.mark.asyncio
async def test_get_goals_with_period_filter(mock_connector):
    """Period filter is passed through to connector."""
    mock_connector.get_goals.return_value = []

    await get_business_goals.handler({"period": "2025-Q1"})

    mock_connector.get_goals.assert_called_once_with(period="2025-Q1")


@pytest.mark.asyncio
async def test_get_goals_with_channel_filter(mock_connector):
    """Channel filter is passed through to connector."""
    mock_connector.get_goals.return_value = []

    await get_business_goals.handler({"channel": "google_ads"})

    mock_connector.get_goals.assert_called_once_with(channel="google_ads")


@pytest.mark.asyncio
async def test_get_goals_error(mock_connector):
    """Returns error response when connector raises."""
    mock_connector.get_goals.side_effect = RuntimeError("BQ timeout")

    result = await get_business_goals.handler({})

    assert result["is_error"] is True
    assert "ERROR" in result["content"][0]["text"]
    assert "BQ timeout" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_goals_partial_targets(mock_connector):
    """Goals with only some targets set still format correctly."""
    mock_connector.get_goals.return_value = [
        {
            "period": "2025-01",
            "channel": "meta",
            "revenue_target": None,
            "cpa_target": 30.0,
            "roas_target": None,
            "budget_planned": None,
        },
    ]

    result = await get_business_goals.handler({})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "2025-01" in text
    assert "meta" in text
    assert "$30.00" in text
    # None targets should not generate lines
    assert "Revenue target" not in text
    assert "ROAS target" not in text
    assert "Budget planned" not in text


@pytest.mark.asyncio
async def test_get_goals_empty_with_filters_shows_filter_desc(mock_connector):
    """Empty result includes filter info in the message."""
    mock_connector.get_goals.return_value = []

    result = await get_business_goals.handler({"period": "2025-Q2", "channel": "meta"})
    text = result["content"][0]["text"]

    assert "2025-Q2" in text
    assert "meta" in text


# ---------------------------------------------------------------------------
# Tool 3: get_backend_performance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_performance_happy_path(mock_connector):
    """Returns business metrics with channel breakdown."""
    mock_connector.get_business_metrics.return_value = [
        {
            "date": "2025-01-15",
            "total_revenue": 10000,
            "total_orders": 100,
            "aov": 100.0,
            "conversion_rate": 0.032,
        },
        {
            "date": "2025-01-16",
            "total_revenue": 12000,
            "total_orders": 120,
            "aov": 100.0,
            "conversion_rate": 0.035,
        },
    ]
    mock_connector.get_channel_performance.return_value = [
        {
            "channel": "google_ads",
            "date": "2025-01-15",
            "revenue": 6000,
            "orders": 60,
            "cost": 1500,
            "aov": 100.0,
        },
        {
            "channel": "meta",
            "date": "2025-01-15",
            "revenue": 4000,
            "orders": 40,
            "cost": 1000,
            "aov": 100.0,
        },
    ]

    result = await get_backend_performance.handler(
        {
            "start_date": "2025-01-15",
            "end_date": "2025-01-16",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "Backend Performance" in text
    assert "SOURCE OF TRUTH" in text
    assert "2 day(s)" in text
    assert "$22,000.00" in text  # 10000 + 12000
    assert "220" in text  # 100 + 120 orders
    assert "CHANNEL BREAKDOWN" in text
    assert "google_ads" in text
    assert "meta" in text


@pytest.mark.asyncio
async def test_backend_performance_without_channel_breakdown(mock_connector):
    """Respects include_channel_breakdown=false and skips channel section."""
    mock_connector.get_business_metrics.return_value = [
        {
            "date": "2025-01-15",
            "total_revenue": 5000,
            "total_orders": 50,
            "aov": 100.0,
            "conversion_rate": 0.02,
        },
    ]

    result = await get_backend_performance.handler(
        {
            "start_date": "2025-01-15",
            "end_date": "2025-01-15",
            "include_channel_breakdown": False,
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "$5,000.00" in text
    assert "CHANNEL BREAKDOWN" not in text
    # get_channel_performance should NOT have been called
    mock_connector.get_channel_performance.assert_not_called()


@pytest.mark.asyncio
async def test_backend_performance_empty_metrics(mock_connector):
    """Returns no-data message when business_rows is empty."""
    mock_connector.get_business_metrics.return_value = []

    result = await get_backend_performance.handler(
        {
            "start_date": "2025-01-01",
            "end_date": "2025-01-07",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "No backend business data found" in text


@pytest.mark.asyncio
async def test_backend_performance_missing_start_date(mock_connector):
    """Returns error when start_date is missing."""
    result = await get_backend_performance.handler({"end_date": "2025-01-07"})

    assert result["is_error"] is True
    assert "start_date is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_backend_performance_missing_end_date(mock_connector):
    """Returns error when end_date is missing."""
    result = await get_backend_performance.handler({"start_date": "2025-01-01"})

    assert result["is_error"] is True
    assert "end_date is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_backend_performance_no_channel_data(mock_connector):
    """Has business metrics but no channel-level data; shows fallback message."""
    mock_connector.get_business_metrics.return_value = [
        {
            "date": "2025-01-15",
            "total_revenue": 8000,
            "total_orders": 80,
            "aov": 100.0,
            "conversion_rate": 0.025,
        },
    ]
    mock_connector.get_channel_performance.return_value = []

    result = await get_backend_performance.handler(
        {
            "start_date": "2025-01-15",
            "end_date": "2025-01-15",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "$8,000.00" in text
    assert "No channel-level data available" in text


@pytest.mark.asyncio
async def test_backend_performance_error(mock_connector):
    """Returns error response when connector raises."""
    mock_connector.get_business_metrics.side_effect = RuntimeError("BQ quota exceeded")

    result = await get_backend_performance.handler(
        {
            "start_date": "2025-01-01",
            "end_date": "2025-01-07",
        }
    )

    assert result["is_error"] is True
    assert "BQ quota exceeded" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Tool 4: get_budget_pacing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_pacing_happy_path(mock_connector):
    """Returns pacing data with status indicators."""
    mock_connector.get_budget_pacing.return_value = [
        {
            "period": "2025-01",
            "channel": "google_ads",
            "campaign_name": "Brand Search",
            "budget_planned": 10000,
            "budget_spent": 5200,
            "days_elapsed": 15,
            "days_remaining": 16,
            "projected_spend": 10700,
            "pacing_status": "on_track",
        },
    ]

    result = await get_budget_pacing.handler({})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "1 entry" in text
    assert "ON TRACK" in text
    assert "google_ads" in text
    assert "Brand Search" in text
    assert "$10,000.00" in text
    assert "$5,200.00" in text
    assert "15 of 31" in text
    assert "16 remaining" in text


@pytest.mark.asyncio
async def test_budget_pacing_empty(mock_connector):
    """Returns guidance message when no pacing data found."""
    mock_connector.get_budget_pacing.return_value = []

    result = await get_budget_pacing.handler({})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "No budget pacing data found" in text


@pytest.mark.asyncio
async def test_budget_pacing_with_filters(mock_connector):
    """Period and channel are passed through to the connector."""
    mock_connector.get_budget_pacing.return_value = []

    await get_budget_pacing.handler({"period": "2025-Q1", "channel": "meta"})

    mock_connector.get_budget_pacing.assert_called_once_with(period="2025-Q1", channel="meta")


@pytest.mark.asyncio
async def test_budget_pacing_overspend_status(mock_connector):
    """Verifies OVERSPEND formatting and projected-over message."""
    mock_connector.get_budget_pacing.return_value = [
        {
            "period": "2025-01",
            "channel": "meta",
            "campaign_name": "",
            "budget_planned": 10000,
            "budget_spent": 8000,
            "days_elapsed": 20,
            "days_remaining": 11,
            "projected_spend": 12400,
            "pacing_status": "overspend",
        },
    ]

    result = await get_budget_pacing.handler({})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "OVERSPEND" in text
    assert "$12,400.00" in text
    assert "Projected over by" in text
    # Overspend amount = 12400 - 10000 = 2400
    assert "$2,400.00" in text


@pytest.mark.asyncio
async def test_budget_pacing_error(mock_connector):
    """Returns error response when connector raises."""
    mock_connector.get_budget_pacing.side_effect = RuntimeError("connection lost")

    result = await get_budget_pacing.handler({})

    assert result["is_error"] is True
    assert "connection lost" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_budget_pacing_projected_under(mock_connector):
    """Verifies projected underspend formatting."""
    mock_connector.get_budget_pacing.return_value = [
        {
            "period": "2025-02",
            "channel": "google_ads",
            "campaign_name": "Display Retargeting",
            "budget_planned": 20000,
            "budget_spent": 5000,
            "days_elapsed": 10,
            "days_remaining": 18,
            "projected_spend": 14000,
            "pacing_status": "underspend",
        },
    ]

    result = await get_budget_pacing.handler({})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "UNDERSPEND" in text
    assert "$14,000.00" in text
    assert "Projected under by" in text
    # Underspend amount = 20000 - 14000 = 6000
    assert "$6,000.00" in text


@pytest.mark.asyncio
async def test_budget_pacing_empty_with_filters_shows_filter_desc(mock_connector):
    """Empty result with filters includes filter info in the message."""
    mock_connector.get_budget_pacing.return_value = []

    result = await get_budget_pacing.handler({"period": "2025-Q3", "channel": "google_ads"})
    text = result["content"][0]["text"]

    assert "2025-Q3" in text
    assert "google_ads" in text


# ---------------------------------------------------------------------------
# Tool 5: get_campaign_attribution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attribution_happy_path(mock_connector):
    """Returns campaigns with revenue, orders, cost, ROAS."""
    mock_connector.get_campaign_attribution.return_value = [
        {
            "campaign_name": "Brand Search",
            "channel": "google_ads",
            "platform_campaign_id": "C001",
            "revenue": 15000,
            "orders": 150,
            "cost": 3000,
        },
        {
            "campaign_name": "Prospecting LAL",
            "channel": "meta",
            "platform_campaign_id": "M001",
            "revenue": 10000,
            "orders": 100,
            "cost": 2500,
        },
    ]

    result = await get_campaign_attribution.handler(
        {
            "start_date": "2025-01-01",
            "end_date": "2025-01-31",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "Campaign Attribution" in text
    assert "SOURCE OF TRUTH" in text
    assert "2 campaign(s)" in text
    assert "Brand Search" in text
    assert "Prospecting LAL" in text
    assert "C001" in text
    assert "M001" in text
    # Grand totals: revenue=25000, orders=250, cost=5500
    assert "$25,000.00" in text
    assert "250" in text
    assert "$5,500.00" in text
    # Grand ROAS = 25000/5500 ~= 4.55x
    assert "4.55x" in text
    # Revenue share: 15000/25000 = 60.00%
    assert "60.00%" in text


@pytest.mark.asyncio
async def test_attribution_empty(mock_connector):
    """Returns no-data message when no attribution rows returned."""
    mock_connector.get_campaign_attribution.return_value = []

    result = await get_campaign_attribution.handler(
        {
            "start_date": "2025-01-01",
            "end_date": "2025-01-31",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "No campaign attribution data found" in text


@pytest.mark.asyncio
async def test_attribution_missing_start_date(mock_connector):
    """Returns error when start_date is missing."""
    result = await get_campaign_attribution.handler({"end_date": "2025-01-31"})

    assert result["is_error"] is True
    assert "start_date is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_attribution_missing_end_date(mock_connector):
    """Returns error when end_date is missing."""
    result = await get_campaign_attribution.handler({"start_date": "2025-01-01"})

    assert result["is_error"] is True
    assert "end_date is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_attribution_with_channel_filter(mock_connector):
    """Channel filter is passed through to the connector."""
    mock_connector.get_campaign_attribution.return_value = []

    await get_campaign_attribution.handler(
        {
            "start_date": "2025-01-01",
            "end_date": "2025-01-31",
            "channel": "google_ads",
        }
    )

    mock_connector.get_campaign_attribution.assert_called_once_with(
        start_date="2025-01-01", end_date="2025-01-31", channel="google_ads"
    )


@pytest.mark.asyncio
async def test_attribution_error(mock_connector):
    """Returns error response when connector raises."""
    mock_connector.get_campaign_attribution.side_effect = RuntimeError("access denied")

    result = await get_campaign_attribution.handler(
        {
            "start_date": "2025-01-01",
            "end_date": "2025-01-31",
        }
    )

    assert result["is_error"] is True
    assert "access denied" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_attribution_aggregation(mock_connector):
    """Same campaign across multiple dates is aggregated correctly."""
    mock_connector.get_campaign_attribution.return_value = [
        {
            "campaign_name": "Brand Search",
            "channel": "google_ads",
            "platform_campaign_id": "C001",
            "date": "2025-01-15",
            "revenue": 5000,
            "orders": 50,
            "cost": 1000,
        },
        {
            "campaign_name": "Brand Search",
            "channel": "google_ads",
            "platform_campaign_id": "C001",
            "date": "2025-01-16",
            "revenue": 7000,
            "orders": 70,
            "cost": 1400,
        },
    ]

    result = await get_campaign_attribution.handler(
        {
            "start_date": "2025-01-15",
            "end_date": "2025-01-16",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    # Should be 1 aggregated campaign, not 2
    assert "1 campaign(s)" in text
    # Totals: revenue=12000, orders=120, cost=2400
    assert "$12,000.00" in text
    assert "120" in text
    assert "$2,400.00" in text
    # ROAS = 12000/2400 = 5.00x
    assert "5.00x" in text


@pytest.mark.asyncio
async def test_attribution_sorted_by_revenue(mock_connector):
    """Campaigns are sorted by revenue descending."""
    mock_connector.get_campaign_attribution.return_value = [
        {
            "campaign_name": "Small Campaign",
            "channel": "meta",
            "platform_campaign_id": "",
            "revenue": 1000,
            "orders": 10,
            "cost": 500,
        },
        {
            "campaign_name": "Big Campaign",
            "channel": "google_ads",
            "platform_campaign_id": "",
            "revenue": 50000,
            "orders": 500,
            "cost": 10000,
        },
    ]

    result = await get_campaign_attribution.handler(
        {
            "start_date": "2025-01-01",
            "end_date": "2025-01-31",
        }
    )
    text = result["content"][0]["text"]

    # Big Campaign should appear before Small Campaign
    big_idx = text.index("Big Campaign")
    small_idx = text.index("Small Campaign")
    assert big_idx < small_idx


@pytest.mark.asyncio
async def test_attribution_empty_with_channel_filter(mock_connector):
    """Empty result with channel filter includes channel in message."""
    mock_connector.get_campaign_attribution.return_value = []

    result = await get_campaign_attribution.handler(
        {
            "start_date": "2025-01-01",
            "end_date": "2025-01-31",
            "channel": "meta",
        }
    )
    text = result["content"][0]["text"]

    assert "meta" in text
    assert "No campaign attribution data found" in text
