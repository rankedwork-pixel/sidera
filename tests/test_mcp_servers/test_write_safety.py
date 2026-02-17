"""Tests for src.mcp_servers.write_safety — shared approval verification.

Covers all 3 functions:
    1. verify_and_load_approval - load + validate approval queue item
    2. log_execution_start - write pre-execution audit event
    3. record_execution_outcome - write success/failure to approval + audit

All DB operations are mocked; no database connection needed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.schema import ApprovalStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PATCH_DB = "src.mcp_servers.write_safety.db"
PATCH_SESSION = "src.mcp_servers.write_safety.get_db_session"


def _make_approval_item(
    *,
    approval_id: int = 42,
    user_id: str = "user_abc",
    status: ApprovalStatus = ApprovalStatus.APPROVED,
    executed_at: datetime | None = None,
    action_type_value: str = "budget_change",
) -> MagicMock:
    """Create a mock ApprovalQueueItem."""
    item = MagicMock()
    item.id = approval_id
    item.user_id = user_id
    item.status = status
    item.executed_at = executed_at
    item.action_type = MagicMock()
    item.action_type.value = action_type_value
    return item


def _mock_session_context():
    """Return an async context-manager mock for get_db_session."""
    session = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, session


# ---------------------------------------------------------------------------
# 1. verify_and_load_approval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_approval_not_found():
    """Returns None + error message when approval_id does not exist."""
    ctx, session = _mock_session_context()

    with (
        patch(PATCH_SESSION, return_value=ctx),
        patch(PATCH_DB) as mock_db,
    ):
        mock_db.get_approval_by_id = AsyncMock(return_value=None)

        from src.mcp_servers.write_safety import verify_and_load_approval

        item, err = await verify_and_load_approval(999)

    assert item is None
    assert "not found" in err
    assert "999" in err


@pytest.mark.asyncio
async def test_verify_approval_wrong_status():
    """Returns None + error when status is not APPROVED."""
    approval = _make_approval_item(status=ApprovalStatus.PENDING)
    ctx, session = _mock_session_context()

    with (
        patch(PATCH_SESSION, return_value=ctx),
        patch(PATCH_DB) as mock_db,
    ):
        mock_db.get_approval_by_id = AsyncMock(return_value=approval)

        from src.mcp_servers.write_safety import verify_and_load_approval

        item, err = await verify_and_load_approval(42)

    assert item is None
    assert "pending" in err
    assert "expected 'approved'" in err


@pytest.mark.asyncio
async def test_verify_approval_rejected_status():
    """Returns None + error when status is REJECTED."""
    approval = _make_approval_item(status=ApprovalStatus.REJECTED)
    ctx, session = _mock_session_context()

    with (
        patch(PATCH_SESSION, return_value=ctx),
        patch(PATCH_DB) as mock_db,
    ):
        mock_db.get_approval_by_id = AsyncMock(return_value=approval)

        from src.mcp_servers.write_safety import verify_and_load_approval

        item, err = await verify_and_load_approval(42)

    assert item is None
    assert "rejected" in err


@pytest.mark.asyncio
async def test_verify_approval_already_executed():
    """Returns None + error when executed_at is already set."""
    executed_time = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    approval = _make_approval_item(executed_at=executed_time)
    ctx, session = _mock_session_context()

    with (
        patch(PATCH_SESSION, return_value=ctx),
        patch(PATCH_DB) as mock_db,
    ):
        mock_db.get_approval_by_id = AsyncMock(return_value=approval)

        from src.mcp_servers.write_safety import verify_and_load_approval

        item, err = await verify_and_load_approval(42)

    assert item is None
    assert "already executed" in err


@pytest.mark.asyncio
async def test_verify_approval_success():
    """Returns the item and empty string when approval is valid."""
    approval = _make_approval_item()
    ctx, session = _mock_session_context()

    with (
        patch(PATCH_SESSION, return_value=ctx),
        patch(PATCH_DB) as mock_db,
    ):
        mock_db.get_approval_by_id = AsyncMock(return_value=approval)

        from src.mcp_servers.write_safety import verify_and_load_approval

        item, err = await verify_and_load_approval(42)

    assert item is approval
    assert err == ""


# ---------------------------------------------------------------------------
# 2. log_execution_start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_execution_start_writes_audit_event():
    """log_execution_start writes an action_execution_started event."""
    ctx, session = _mock_session_context()

    with (
        patch(PATCH_SESSION, return_value=ctx),
        patch(PATCH_DB) as mock_db,
    ):
        mock_db.log_event = AsyncMock()

        from src.mcp_servers.write_safety import log_execution_start

        await log_execution_start(
            approval_id=42,
            user_id="user_abc",
            action_type="budget_change",
            action_params={"platform": "google_ads", "action": "update_budget"},
        )

    mock_db.log_event.assert_called_once()
    call_kwargs = mock_db.log_event.call_args
    # Positional: session is first arg
    assert call_kwargs[1]["user_id"] == "user_abc"
    assert call_kwargs[1]["event_type"] == "action_execution_started"
    assert call_kwargs[1]["event_data"]["approval_id"] == 42
    assert call_kwargs[1]["event_data"]["action_type"] == "budget_change"
    assert call_kwargs[1]["source"] == "approval_workflow"
    assert call_kwargs[1]["required_approval"] is True


@pytest.mark.asyncio
async def test_log_execution_start_includes_action_params():
    """Verifies the action_params are included in the audit event_data."""
    ctx, session = _mock_session_context()

    with (
        patch(PATCH_SESSION, return_value=ctx),
        patch(PATCH_DB) as mock_db,
    ):
        mock_db.log_event = AsyncMock()

        from src.mcp_servers.write_safety import log_execution_start

        params = {"platform": "meta", "action": "pause", "campaign_id": "111"}
        await log_execution_start(
            approval_id=10, user_id="u1", action_type="pause_campaign", action_params=params
        )

    event_data = mock_db.log_event.call_args[1]["event_data"]
    assert event_data["action_params"] == params


# ---------------------------------------------------------------------------
# 3. record_execution_outcome
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_execution_outcome_success():
    """Records success: calls record_execution_result and log_event with success=True."""
    ctx, session = _mock_session_context()

    with (
        patch(PATCH_SESSION, return_value=ctx),
        patch(PATCH_DB) as mock_db,
    ):
        mock_db.record_execution_result = AsyncMock()
        mock_db.log_event = AsyncMock()

        from src.mcp_servers.write_safety import record_execution_outcome

        await record_execution_outcome(
            approval_id=42,
            user_id="user_abc",
            action_type="budget_change",
            result={"status": "ok", "new_budget": 50000000},
        )

    # Verify record_execution_result was called
    mock_db.record_execution_result.assert_called_once()
    rer_kwargs = mock_db.record_execution_result.call_args[1]
    assert rer_kwargs["approval_id"] == 42
    assert rer_kwargs["execution_result"]["status"] == "ok"
    assert rer_kwargs["execution_error"] is None

    # Verify audit log event
    mock_db.log_event.assert_called_once()
    log_kwargs = mock_db.log_event.call_args[1]
    assert log_kwargs["event_type"] == "action_execution_completed"
    assert log_kwargs["event_data"]["success"] is True
    assert "result" in log_kwargs["event_data"]


@pytest.mark.asyncio
async def test_record_execution_outcome_error():
    """Records failure: record_execution_result with error, success=False."""
    ctx, session = _mock_session_context()

    with (
        patch(PATCH_SESSION, return_value=ctx),
        patch(PATCH_DB) as mock_db,
    ):
        mock_db.record_execution_result = AsyncMock()
        mock_db.log_event = AsyncMock()

        from src.mcp_servers.write_safety import record_execution_outcome

        await record_execution_outcome(
            approval_id=42,
            user_id="user_abc",
            action_type="budget_change",
            error="API returned 500",
        )

    # Verify record_execution_result was called with error
    rer_kwargs = mock_db.record_execution_result.call_args[1]
    assert rer_kwargs["execution_result"] is None
    assert rer_kwargs["execution_error"] == "API returned 500"

    # Verify audit log event
    log_kwargs = mock_db.log_event.call_args[1]
    assert log_kwargs["event_data"]["success"] is False
    assert log_kwargs["event_data"]["error"] == "API returned 500"
    assert "result" not in log_kwargs["event_data"]
