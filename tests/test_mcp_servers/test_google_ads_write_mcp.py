"""Tests for Google Ads write MCP tools — update_google_ads_campaign and
update_google_ads_keywords.

Both tools require a valid approval_id and go through the write_safety layer
before calling the connector.  All connector calls and write_safety functions
are mocked; no network or database traffic needed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_servers.google_ads import (
    update_google_ads_campaign,
    update_google_ads_keywords,
)
from src.models.schema import ApprovalStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PATCH_CONNECTOR = "src.mcp_servers.google_ads._get_connector"
PATCH_VERIFY = "src.mcp_servers.google_ads.verify_and_load_approval"
PATCH_LOG_START = "src.mcp_servers.google_ads.log_execution_start"
PATCH_RECORD = "src.mcp_servers.google_ads.record_execution_outcome"

# Because the write tools do lazy imports inside the handler function body,
# we patch at the write_safety module level and they pick it up.
PATCH_VERIFY_MOD = "src.mcp_servers.write_safety.verify_and_load_approval"
PATCH_LOG_START_MOD = "src.mcp_servers.write_safety.log_execution_start"
PATCH_RECORD_MOD = "src.mcp_servers.write_safety.record_execution_outcome"


def _make_approval_item(
    *,
    user_id: str = "user_abc",
    action_type_value: str = "budget_change",
) -> MagicMock:
    """Create a mock ApprovalQueueItem that passes verification."""
    item = MagicMock()
    item.user_id = user_id
    item.status = ApprovalStatus.APPROVED
    item.executed_at = None
    item.action_type = MagicMock()
    item.action_type.value = action_type_value
    return item


@pytest.fixture()
def mock_connector():
    """Return a MagicMock standing in for GoogleAdsConnector."""
    with patch(PATCH_CONNECTOR) as mock_get:
        connector = MagicMock()
        mock_get.return_value = connector
        yield connector


@pytest.fixture()
def mock_write_safety():
    """Mock all three write_safety functions used by the write tools."""
    item = _make_approval_item()
    with (
        patch(PATCH_VERIFY_MOD, new_callable=AsyncMock, return_value=(item, "")) as mock_verify,
        patch(PATCH_LOG_START_MOD, new_callable=AsyncMock) as mock_log,
        patch(PATCH_RECORD_MOD, new_callable=AsyncMock) as mock_record,
    ):
        yield {
            "verify": mock_verify,
            "log_start": mock_log,
            "record": mock_record,
            "item": item,
        }


# ===========================================================================
# Tool 6: update_google_ads_campaign
# ===========================================================================


# --- Validation tests (no mocks needed for these) ---


@pytest.mark.asyncio
async def test_update_campaign_missing_approval_id():
    """Returns error when approval_id is not provided."""
    result = await update_google_ads_campaign.handler(
        {
            "action": "pause",
            "customer_id": "1234567890",
            "campaign_id": "111",
        }
    )
    assert result["is_error"] is True
    assert "approval_id" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_update_campaign_missing_action():
    """Returns error when action is not provided."""
    result = await update_google_ads_campaign.handler(
        {
            "approval_id": 42,
            "customer_id": "1234567890",
            "campaign_id": "111",
        }
    )
    assert result["is_error"] is True
    assert "action is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_update_campaign_missing_customer_id():
    """Returns error when customer_id is not provided."""
    result = await update_google_ads_campaign.handler(
        {
            "approval_id": 42,
            "action": "pause",
            "campaign_id": "111",
        }
    )
    assert result["is_error"] is True
    assert "customer_id" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_update_campaign_missing_campaign_id():
    """Returns error when campaign_id is not provided."""
    result = await update_google_ads_campaign.handler(
        {
            "approval_id": 42,
            "action": "pause",
            "customer_id": "1234567890",
        }
    )
    assert result["is_error"] is True
    assert "campaign_id" in result["content"][0]["text"]


# --- Approval verification failures ---


@pytest.mark.asyncio
async def test_update_campaign_approval_not_found():
    """Returns error when approval verification fails."""
    with patch(
        PATCH_VERIFY_MOD,
        new_callable=AsyncMock,
        return_value=(None, "Approval #99 not found."),
    ):
        result = await update_google_ads_campaign.handler(
            {
                "approval_id": 99,
                "action": "pause",
                "customer_id": "1234567890",
                "campaign_id": "111",
            }
        )
    assert result["is_error"] is True
    assert "Approval verification failed" in result["content"][0]["text"]
    assert "not found" in result["content"][0]["text"]


# --- Action: pause ---


@pytest.mark.asyncio
async def test_update_campaign_pause(mock_connector, mock_write_safety):
    """Pause action calls update_campaign_status with PAUSED."""
    mock_connector.update_campaign_status.return_value = {"status": "PAUSED"}

    result = await update_google_ads_campaign.handler(
        {
            "approval_id": 42,
            "action": "pause",
            "customer_id": "1234567890",
            "campaign_id": "111",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "paused" in text.lower()
    assert "111" in text
    mock_connector.update_campaign_status.assert_called_once_with("1234567890", "111", "PAUSED")
    mock_write_safety["record"].assert_called_once()


# --- Action: enable ---


@pytest.mark.asyncio
async def test_update_campaign_enable(mock_connector, mock_write_safety):
    """Enable action calls update_campaign_status with ENABLED."""
    mock_connector.update_campaign_status.return_value = {"status": "ENABLED"}

    result = await update_google_ads_campaign.handler(
        {
            "approval_id": 42,
            "action": "enable",
            "customer_id": "1234567890",
            "campaign_id": "111",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "enabled" in text.lower()
    mock_connector.update_campaign_status.assert_called_once_with("1234567890", "111", "ENABLED")


# --- Action: update_budget ---


@pytest.mark.asyncio
async def test_update_campaign_budget_happy_path(mock_connector, mock_write_safety):
    """update_budget action calls update_campaign_budget with correct micros."""
    mock_connector.update_campaign_budget.return_value = {"new_budget_micros": 50000000}

    result = await update_google_ads_campaign.handler(
        {
            "approval_id": 42,
            "action": "update_budget",
            "customer_id": "1234567890",
            "campaign_id": "111",
            "new_budget_micros": 50000000,
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "budget updated" in text.lower()
    assert "$50.00" in text
    mock_connector.update_campaign_budget.assert_called_once_with("1234567890", "111", 50000000)


@pytest.mark.asyncio
async def test_update_campaign_budget_missing_micros(mock_connector, mock_write_safety):
    """update_budget action returns error when new_budget_micros is missing."""
    result = await update_google_ads_campaign.handler(
        {
            "approval_id": 42,
            "action": "update_budget",
            "customer_id": "1234567890",
            "campaign_id": "111",
        }
    )
    assert result["is_error"] is True
    assert "new_budget_micros" in result["content"][0]["text"]


# --- Action: update_bid_target ---


@pytest.mark.asyncio
async def test_update_campaign_bid_target_cpa(mock_connector, mock_write_safety):
    """update_bid_target with target_cpa_micros calls connector correctly."""
    mock_connector.update_bid_strategy_target.return_value = {"target_cpa_micros": 5000000}

    result = await update_google_ads_campaign.handler(
        {
            "approval_id": 42,
            "action": "update_bid_target",
            "customer_id": "1234567890",
            "campaign_id": "111",
            "target_cpa_micros": 5000000,
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "bid target updated" in text.lower()
    mock_connector.update_bid_strategy_target.assert_called_once_with(
        "1234567890", "111", target_cpa_micros=5000000, target_roas=None
    )


@pytest.mark.asyncio
async def test_update_campaign_bid_target_roas(mock_connector, mock_write_safety):
    """update_bid_target with target_roas calls connector correctly."""
    mock_connector.update_bid_strategy_target.return_value = {"target_roas": 4.0}

    result = await update_google_ads_campaign.handler(
        {
            "approval_id": 42,
            "action": "update_bid_target",
            "customer_id": "1234567890",
            "campaign_id": "111",
            "target_roas": 4.0,
        }
    )

    assert "is_error" not in result
    mock_connector.update_bid_strategy_target.assert_called_once_with(
        "1234567890", "111", target_cpa_micros=None, target_roas=4.0
    )


@pytest.mark.asyncio
async def test_update_campaign_bid_target_missing_both(mock_connector, mock_write_safety):
    """update_bid_target returns error when neither cpa nor roas is provided."""
    result = await update_google_ads_campaign.handler(
        {
            "approval_id": 42,
            "action": "update_bid_target",
            "customer_id": "1234567890",
            "campaign_id": "111",
        }
    )
    assert result["is_error"] is True
    assert "target_cpa_micros" in result["content"][0]["text"]


# --- Unknown action ---


@pytest.mark.asyncio
async def test_update_campaign_unknown_action(mock_connector, mock_write_safety):
    """Unknown action returns an error response."""
    result = await update_google_ads_campaign.handler(
        {
            "approval_id": 42,
            "action": "delete",
            "customer_id": "1234567890",
            "campaign_id": "111",
        }
    )
    assert result["is_error"] is True
    assert "Unknown action" in result["content"][0]["text"]


# --- Connector error ---


@pytest.mark.asyncio
async def test_update_campaign_connector_error(mock_connector, mock_write_safety):
    """Records error outcome when connector raises an exception."""
    mock_connector.update_campaign_status.side_effect = RuntimeError("API quota exceeded")

    result = await update_google_ads_campaign.handler(
        {
            "approval_id": 42,
            "action": "pause",
            "customer_id": "1234567890",
            "campaign_id": "111",
        }
    )

    assert result["is_error"] is True
    assert "API quota exceeded" in result["content"][0]["text"]
    # Verify failure was recorded
    mock_write_safety["record"].assert_called_once()
    call_kwargs = mock_write_safety["record"].call_args
    assert call_kwargs[1].get("error") == "API quota exceeded" or (
        len(call_kwargs[0]) >= 5 and call_kwargs[0][4] is None
    )


# --- Audit trail verification ---


@pytest.mark.asyncio
async def test_update_campaign_logs_execution_start(mock_connector, mock_write_safety):
    """Verifies log_execution_start is called before the connector write."""
    mock_connector.update_campaign_status.return_value = {"status": "PAUSED"}

    await update_google_ads_campaign.handler(
        {
            "approval_id": 42,
            "action": "pause",
            "customer_id": "1234567890",
            "campaign_id": "111",
        }
    )

    mock_write_safety["log_start"].assert_called_once()
    call_args = mock_write_safety["log_start"].call_args
    assert call_args[0][0] == 42  # approval_id
    assert call_args[0][1] == "user_abc"  # user_id


@pytest.mark.asyncio
async def test_update_campaign_records_success(mock_connector, mock_write_safety):
    """Verifies record_execution_outcome is called with result on success."""
    connector_result = {"status": "ENABLED", "campaign_id": "111"}
    mock_connector.update_campaign_status.return_value = connector_result

    await update_google_ads_campaign.handler(
        {
            "approval_id": 42,
            "action": "enable",
            "customer_id": "1234567890",
            "campaign_id": "111",
        }
    )

    mock_write_safety["record"].assert_called_once()
    call_kwargs = mock_write_safety["record"].call_args[1]
    assert call_kwargs.get("result") == connector_result


# --- Action: create ---


@pytest.mark.asyncio
async def test_create_campaign_happy_path(mock_connector, mock_write_safety):
    """Create action calls create_campaign and returns success."""
    mock_connector.create_campaign.return_value = {
        "campaign_id": "555",
        "campaign_resource_name": "customers/1234567890/campaigns/555",
        "budget_resource_name": "customers/1234567890/campaignBudgets/999",
        "name": "Brand Search",
        "channel_type": "SEARCH",
        "daily_budget_micros": 10_000_000,
        "status": "PAUSED",
    }

    result = await update_google_ads_campaign.handler(
        {
            "approval_id": 42,
            "action": "create",
            "customer_id": "1234567890",
            "name": "Brand Search",
            "channel_type": "SEARCH",
            "new_budget_micros": 10_000_000,
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "Brand Search" in text
    assert "created" in text.lower()
    assert "555" in text
    assert "$10.00" in text
    mock_connector.create_campaign.assert_called_once_with(
        "1234567890",
        "Brand Search",
        channel_type="SEARCH",
        daily_budget_micros=10_000_000,
        bidding_strategy="MAXIMIZE_CLICKS",
    )
    mock_write_safety["record"].assert_called_once()


@pytest.mark.asyncio
async def test_create_campaign_missing_name(mock_connector, mock_write_safety):
    """Returns error when name is not provided for create action."""
    result = await update_google_ads_campaign.handler(
        {
            "approval_id": 42,
            "action": "create",
            "customer_id": "1234567890",
            "new_budget_micros": 10_000_000,
        }
    )
    assert result["is_error"] is True
    assert "name is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_create_campaign_missing_budget(mock_connector, mock_write_safety):
    """Returns error when new_budget_micros is not provided for create action."""
    result = await update_google_ads_campaign.handler(
        {
            "approval_id": 42,
            "action": "create",
            "customer_id": "1234567890",
            "name": "Brand Search",
        }
    )
    assert result["is_error"] is True
    assert "new_budget_micros is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_create_campaign_no_campaign_id_required(mock_connector, mock_write_safety):
    """Create action should not require campaign_id."""
    mock_connector.create_campaign.return_value = {
        "campaign_id": "555",
        "campaign_resource_name": "customers/1234567890/campaigns/555",
        "budget_resource_name": "customers/1234567890/campaignBudgets/999",
        "name": "Test",
        "channel_type": "DISPLAY",
        "daily_budget_micros": 5_000_000,
        "status": "PAUSED",
    }

    result = await update_google_ads_campaign.handler(
        {
            "approval_id": 42,
            "action": "create",
            "customer_id": "1234567890",
            "name": "Test",
            "channel_type": "DISPLAY",
            "new_budget_micros": 5_000_000,
            "bidding_strategy": "MANUAL_CPC",
        }
    )

    assert "is_error" not in result
    mock_connector.create_campaign.assert_called_once_with(
        "1234567890",
        "Test",
        channel_type="DISPLAY",
        daily_budget_micros=5_000_000,
        bidding_strategy="MANUAL_CPC",
    )


@pytest.mark.asyncio
async def test_create_campaign_connector_error(mock_connector, mock_write_safety):
    """Records error outcome when connector raises during create."""
    mock_connector.create_campaign.side_effect = RuntimeError("API error")

    result = await update_google_ads_campaign.handler(
        {
            "approval_id": 42,
            "action": "create",
            "customer_id": "1234567890",
            "name": "Brand Search",
            "new_budget_micros": 10_000_000,
        }
    )

    assert result["is_error"] is True
    assert "API error" in result["content"][0]["text"]
    mock_write_safety["record"].assert_called_once()


# ===========================================================================
# Tool 7: update_google_ads_keywords
# ===========================================================================


# --- Validation tests ---


@pytest.mark.asyncio
async def test_update_keywords_missing_approval_id():
    """Returns error when approval_id is not provided."""
    result = await update_google_ads_keywords.handler(
        {
            "customer_id": "1234567890",
            "campaign_id": "111",
            "keywords": ["cheap", "free"],
        }
    )
    assert result["is_error"] is True
    assert "approval_id" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_update_keywords_missing_customer_id():
    """Returns error when customer_id is not provided."""
    result = await update_google_ads_keywords.handler(
        {
            "approval_id": 42,
            "campaign_id": "111",
            "keywords": ["cheap"],
        }
    )
    assert result["is_error"] is True
    assert "customer_id" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_update_keywords_missing_campaign_id():
    """Returns error when campaign_id is not provided."""
    result = await update_google_ads_keywords.handler(
        {
            "approval_id": 42,
            "customer_id": "1234567890",
            "keywords": ["cheap"],
        }
    )
    assert result["is_error"] is True
    assert "campaign_id" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_update_keywords_empty_keywords_list():
    """Returns error when keywords list is empty."""
    result = await update_google_ads_keywords.handler(
        {
            "approval_id": 42,
            "customer_id": "1234567890",
            "campaign_id": "111",
            "keywords": [],
        }
    )
    assert result["is_error"] is True
    assert "keywords" in result["content"][0]["text"].lower()


# --- Happy path ---


@pytest.mark.asyncio
async def test_update_keywords_happy_path(mock_connector, mock_write_safety):
    """Adds negative keywords and returns formatted success message."""
    mock_connector.add_negative_keywords.return_value = {
        "keywords_added": 3,
        "duplicates_skipped": 0,
    }

    result = await update_google_ads_keywords.handler(
        {
            "approval_id": 42,
            "customer_id": "1234567890",
            "campaign_id": "111",
            "keywords": ["cheap", "free", "discount"],
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "3" in text
    assert "111" in text
    mock_connector.add_negative_keywords.assert_called_once_with(
        "1234567890", "111", ["cheap", "free", "discount"]
    )


# --- Duplicates handled ---


@pytest.mark.asyncio
async def test_update_keywords_with_duplicates(mock_connector, mock_write_safety):
    """Reports duplicates skipped when connector returns them."""
    mock_connector.add_negative_keywords.return_value = {
        "keywords_added": 1,
        "duplicates_skipped": 2,
    }

    result = await update_google_ads_keywords.handler(
        {
            "approval_id": 42,
            "customer_id": "1234567890",
            "campaign_id": "111",
            "keywords": ["cheap", "free", "discount"],
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "1" in text  # keywords added
    assert "2" in text  # duplicates skipped


# --- Connector error ---


@pytest.mark.asyncio
async def test_update_keywords_connector_error(mock_connector, mock_write_safety):
    """Records error outcome when connector raises."""
    mock_connector.add_negative_keywords.side_effect = RuntimeError("quota limit")

    result = await update_google_ads_keywords.handler(
        {
            "approval_id": 42,
            "customer_id": "1234567890",
            "campaign_id": "111",
            "keywords": ["cheap"],
        }
    )

    assert result["is_error"] is True
    assert "quota limit" in result["content"][0]["text"]
    mock_write_safety["record"].assert_called_once()
