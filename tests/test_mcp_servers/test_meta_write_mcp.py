"""Tests for Meta write MCP tools — update_meta_campaign and update_meta_ad.

Both tools require a valid approval_id and go through the write_safety layer
before calling the connector.  All connector calls and write_safety functions
are mocked; no network or database traffic needed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_servers.meta import (
    update_meta_ad,
    update_meta_campaign,
)
from src.models.schema import ApprovalStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PATCH_CONNECTOR = "src.mcp_servers.meta._get_connector"

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
    """Return a MagicMock standing in for MetaConnector."""
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
# Tool 6: update_meta_campaign
# ===========================================================================


# --- Validation tests ---


@pytest.mark.asyncio
async def test_update_campaign_missing_approval_id():
    """Returns error when approval_id is not provided."""
    result = await update_meta_campaign.handler(
        {
            "action": "pause",
            "account_id": "act_123456789",
            "campaign_id": "111",
        }
    )
    assert result["is_error"] is True
    assert "approval_id" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_update_campaign_missing_action():
    """Returns error when action is not provided."""
    result = await update_meta_campaign.handler(
        {
            "approval_id": 42,
            "account_id": "act_123456789",
            "campaign_id": "111",
        }
    )
    assert result["is_error"] is True
    assert "action is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_update_campaign_missing_account_id():
    """Returns error when account_id is not provided."""
    result = await update_meta_campaign.handler(
        {
            "approval_id": 42,
            "action": "pause",
            "campaign_id": "111",
        }
    )
    assert result["is_error"] is True
    assert "account_id" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_update_campaign_missing_campaign_id():
    """Returns error when campaign_id is not provided."""
    result = await update_meta_campaign.handler(
        {
            "approval_id": 42,
            "action": "pause",
            "account_id": "act_123456789",
        }
    )
    assert result["is_error"] is True
    assert "campaign_id" in result["content"][0]["text"]


# --- Approval verification failure ---


@pytest.mark.asyncio
async def test_update_campaign_approval_not_found():
    """Returns error when approval verification fails."""
    with patch(
        PATCH_VERIFY_MOD,
        new_callable=AsyncMock,
        return_value=(None, "Approval #99 not found."),
    ):
        result = await update_meta_campaign.handler(
            {
                "approval_id": 99,
                "action": "pause",
                "account_id": "act_123456789",
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

    result = await update_meta_campaign.handler(
        {
            "approval_id": 42,
            "action": "pause",
            "account_id": "act_123456789",
            "campaign_id": "111",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "paused" in text.lower()
    assert "111" in text
    mock_connector.update_campaign_status.assert_called_once_with("act_123456789", "111", "PAUSED")
    mock_write_safety["record"].assert_called_once()


# --- Action: enable ---


@pytest.mark.asyncio
async def test_update_campaign_enable(mock_connector, mock_write_safety):
    """Enable action calls update_campaign_status with ACTIVE."""
    mock_connector.update_campaign_status.return_value = {"status": "ACTIVE"}

    result = await update_meta_campaign.handler(
        {
            "approval_id": 42,
            "action": "enable",
            "account_id": "act_123456789",
            "campaign_id": "111",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "enabled" in text.lower()
    mock_connector.update_campaign_status.assert_called_once_with("act_123456789", "111", "ACTIVE")


# --- Action: update_budget ---


@pytest.mark.asyncio
async def test_update_campaign_budget_daily(mock_connector, mock_write_safety):
    """update_budget action with daily budget calls connector correctly."""
    mock_connector.update_campaign_budget.return_value = {"new_budget_cents": 5000}

    result = await update_meta_campaign.handler(
        {
            "approval_id": 42,
            "action": "update_budget",
            "account_id": "act_123456789",
            "campaign_id": "111",
            "new_budget_cents": 5000,
            "budget_type": "daily",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "budget" in text.lower()
    assert "$50.00" in text
    mock_connector.update_campaign_budget.assert_called_once_with(
        "act_123456789", "111", 5000, "daily"
    )


@pytest.mark.asyncio
async def test_update_campaign_budget_lifetime(mock_connector, mock_write_safety):
    """update_budget action with lifetime budget calls connector correctly."""
    mock_connector.update_campaign_budget.return_value = {"new_budget_cents": 100000}

    result = await update_meta_campaign.handler(
        {
            "approval_id": 42,
            "action": "update_budget",
            "account_id": "act_123456789",
            "campaign_id": "111",
            "new_budget_cents": 100000,
            "budget_type": "lifetime",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "lifetime" in text.lower()
    assert "$1,000.00" in text
    mock_connector.update_campaign_budget.assert_called_once_with(
        "act_123456789", "111", 100000, "lifetime"
    )


@pytest.mark.asyncio
async def test_update_campaign_budget_missing_cents(mock_connector, mock_write_safety):
    """update_budget action returns error when new_budget_cents is missing."""
    result = await update_meta_campaign.handler(
        {
            "approval_id": 42,
            "action": "update_budget",
            "account_id": "act_123456789",
            "campaign_id": "111",
        }
    )
    assert result["is_error"] is True
    assert "new_budget_cents" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_update_campaign_budget_defaults_to_daily(mock_connector, mock_write_safety):
    """budget_type defaults to 'daily' when not specified."""
    mock_connector.update_campaign_budget.return_value = {"new_budget_cents": 5000}

    result = await update_meta_campaign.handler(
        {
            "approval_id": 42,
            "action": "update_budget",
            "account_id": "act_123456789",
            "campaign_id": "111",
            "new_budget_cents": 5000,
        }
    )

    assert "is_error" not in result
    mock_connector.update_campaign_budget.assert_called_once_with(
        "act_123456789", "111", 5000, "daily"
    )


# --- Unknown action ---


@pytest.mark.asyncio
async def test_update_campaign_unknown_action(mock_connector, mock_write_safety):
    """Unknown action returns an error response."""
    result = await update_meta_campaign.handler(
        {
            "approval_id": 42,
            "action": "delete",
            "account_id": "act_123456789",
            "campaign_id": "111",
        }
    )
    assert result["is_error"] is True
    assert "Unknown action" in result["content"][0]["text"]


# --- Connector error ---


@pytest.mark.asyncio
async def test_update_campaign_connector_error(mock_connector, mock_write_safety):
    """Records error outcome when connector raises."""
    mock_connector.update_campaign_status.side_effect = RuntimeError("Meta API 500")

    result = await update_meta_campaign.handler(
        {
            "approval_id": 42,
            "action": "pause",
            "account_id": "act_123456789",
            "campaign_id": "111",
        }
    )

    assert result["is_error"] is True
    assert "Meta API 500" in result["content"][0]["text"]
    mock_write_safety["record"].assert_called_once()


# --- Audit trail ---


@pytest.mark.asyncio
async def test_update_campaign_logs_execution_start(mock_connector, mock_write_safety):
    """Verifies log_execution_start is called before the connector write."""
    mock_connector.update_campaign_status.return_value = {"status": "PAUSED"}

    await update_meta_campaign.handler(
        {
            "approval_id": 42,
            "action": "pause",
            "account_id": "act_123456789",
            "campaign_id": "111",
        }
    )

    mock_write_safety["log_start"].assert_called_once()
    call_args = mock_write_safety["log_start"].call_args
    assert call_args[0][0] == 42  # approval_id
    assert call_args[0][1] == "user_abc"  # user_id


# ===========================================================================
# Tool 7: update_meta_ad
# ===========================================================================


# --- Validation tests ---


@pytest.mark.asyncio
async def test_update_ad_missing_approval_id():
    """Returns error when approval_id is not provided."""
    result = await update_meta_ad.handler(
        {
            "action": "pause",
            "account_id": "act_123456789",
            "entity_type": "ad",
            "entity_id": "555",
        }
    )
    assert result["is_error"] is True
    assert "approval_id" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_update_ad_missing_action():
    """Returns error when action is not provided."""
    result = await update_meta_ad.handler(
        {
            "approval_id": 42,
            "account_id": "act_123456789",
            "entity_type": "ad",
            "entity_id": "555",
        }
    )
    assert result["is_error"] is True
    assert "action is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_update_ad_missing_account_id():
    """Returns error when account_id is not provided."""
    result = await update_meta_ad.handler(
        {
            "approval_id": 42,
            "action": "pause",
            "entity_type": "ad",
            "entity_id": "555",
        }
    )
    assert result["is_error"] is True
    assert "account_id" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_update_ad_missing_entity_type():
    """Returns error when entity_type is not provided."""
    result = await update_meta_ad.handler(
        {
            "approval_id": 42,
            "action": "pause",
            "account_id": "act_123456789",
            "entity_id": "555",
        }
    )
    assert result["is_error"] is True
    assert "entity_type" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_update_ad_missing_entity_id():
    """Returns error when entity_id is not provided."""
    result = await update_meta_ad.handler(
        {
            "approval_id": 42,
            "action": "pause",
            "account_id": "act_123456789",
            "entity_type": "ad",
        }
    )
    assert result["is_error"] is True
    assert "entity_id" in result["content"][0]["text"]


# --- Approval verification failure ---


@pytest.mark.asyncio
async def test_update_ad_approval_not_found():
    """Returns error when approval verification fails."""
    with patch(
        PATCH_VERIFY_MOD,
        new_callable=AsyncMock,
        return_value=(None, "Approval #99 not found."),
    ):
        result = await update_meta_ad.handler(
            {
                "approval_id": 99,
                "action": "pause",
                "account_id": "act_123456789",
                "entity_type": "ad",
                "entity_id": "555",
            }
        )
    assert result["is_error"] is True
    assert "Approval verification failed" in result["content"][0]["text"]


# --- Pause/enable campaign entity ---


@pytest.mark.asyncio
async def test_update_ad_pause_campaign(mock_connector, mock_write_safety):
    """Pause a campaign entity calls update_campaign_status with PAUSED."""
    mock_connector.update_campaign_status.return_value = {"status": "PAUSED"}

    result = await update_meta_ad.handler(
        {
            "approval_id": 42,
            "action": "pause",
            "account_id": "act_123456789",
            "entity_type": "campaign",
            "entity_id": "111",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "paused" in text.lower()
    assert "Campaign" in text
    mock_connector.update_campaign_status.assert_called_once_with("act_123456789", "111", "PAUSED")


@pytest.mark.asyncio
async def test_update_ad_enable_campaign(mock_connector, mock_write_safety):
    """Enable a campaign entity calls update_campaign_status with ACTIVE."""
    mock_connector.update_campaign_status.return_value = {"status": "ACTIVE"}

    result = await update_meta_ad.handler(
        {
            "approval_id": 42,
            "action": "enable",
            "account_id": "act_123456789",
            "entity_type": "campaign",
            "entity_id": "111",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "enabled" in text.lower()
    mock_connector.update_campaign_status.assert_called_once_with("act_123456789", "111", "ACTIVE")


# --- Pause/enable adset entity ---


@pytest.mark.asyncio
async def test_update_ad_pause_adset(mock_connector, mock_write_safety):
    """Pause an adset entity calls update_adset_status with PAUSED."""
    mock_connector.update_adset_status.return_value = {"status": "PAUSED"}

    result = await update_meta_ad.handler(
        {
            "approval_id": 42,
            "action": "pause",
            "account_id": "act_123456789",
            "entity_type": "adset",
            "entity_id": "222",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "paused" in text.lower()
    assert "Adset" in text
    mock_connector.update_adset_status.assert_called_once_with("act_123456789", "222", "PAUSED")


# --- Pause/enable ad entity ---


@pytest.mark.asyncio
async def test_update_ad_pause_ad(mock_connector, mock_write_safety):
    """Pause an ad entity calls update_ad_status with PAUSED."""
    mock_connector.update_ad_status.return_value = {"status": "PAUSED"}

    result = await update_meta_ad.handler(
        {
            "approval_id": 42,
            "action": "pause",
            "account_id": "act_123456789",
            "entity_type": "ad",
            "entity_id": "333",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "paused" in text.lower()
    assert "Ad" in text
    mock_connector.update_ad_status.assert_called_once_with("act_123456789", "333", "PAUSED")


@pytest.mark.asyncio
async def test_update_ad_enable_ad(mock_connector, mock_write_safety):
    """Enable an ad entity calls update_ad_status with ACTIVE."""
    mock_connector.update_ad_status.return_value = {"status": "ACTIVE"}

    result = await update_meta_ad.handler(
        {
            "approval_id": 42,
            "action": "enable",
            "account_id": "act_123456789",
            "entity_type": "ad",
            "entity_id": "333",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "enabled" in text.lower()
    mock_connector.update_ad_status.assert_called_once_with("act_123456789", "333", "ACTIVE")


# --- Unknown entity_type ---


@pytest.mark.asyncio
async def test_update_ad_unknown_entity_type(mock_connector, mock_write_safety):
    """Unknown entity_type returns an error response."""
    result = await update_meta_ad.handler(
        {
            "approval_id": 42,
            "action": "pause",
            "account_id": "act_123456789",
            "entity_type": "audience",
            "entity_id": "444",
        }
    )
    assert result["is_error"] is True
    assert "Unknown entity_type" in result["content"][0]["text"]


# --- Connector error ---


@pytest.mark.asyncio
async def test_update_ad_connector_error(mock_connector, mock_write_safety):
    """Records error outcome when connector raises."""
    mock_connector.update_ad_status.side_effect = RuntimeError("rate limited")

    result = await update_meta_ad.handler(
        {
            "approval_id": 42,
            "action": "pause",
            "account_id": "act_123456789",
            "entity_type": "ad",
            "entity_id": "333",
        }
    )

    assert result["is_error"] is True
    assert "rate limited" in result["content"][0]["text"]
    mock_write_safety["record"].assert_called_once()


# --- Audit trail ---


@pytest.mark.asyncio
async def test_update_ad_logs_execution_start(mock_connector, mock_write_safety):
    """Verifies log_execution_start is called before the connector write."""
    mock_connector.update_ad_status.return_value = {"status": "PAUSED"}

    await update_meta_ad.handler(
        {
            "approval_id": 42,
            "action": "pause",
            "account_id": "act_123456789",
            "entity_type": "ad",
            "entity_id": "333",
        }
    )

    mock_write_safety["log_start"].assert_called_once()
    call_args = mock_write_safety["log_start"].call_args
    assert call_args[0][0] == 42  # approval_id
    assert call_args[0][1] == "user_abc"  # user_id


@pytest.mark.asyncio
async def test_update_ad_records_success(mock_connector, mock_write_safety):
    """Verifies record_execution_outcome is called with result on success."""
    connector_result = {"status": "ACTIVE", "ad_id": "333"}
    mock_connector.update_ad_status.return_value = connector_result

    await update_meta_ad.handler(
        {
            "approval_id": 42,
            "action": "enable",
            "account_id": "act_123456789",
            "entity_type": "ad",
            "entity_id": "333",
        }
    )

    mock_write_safety["record"].assert_called_once()
    call_kwargs = mock_write_safety["record"].call_args[1]
    assert call_kwargs.get("result") == connector_result
