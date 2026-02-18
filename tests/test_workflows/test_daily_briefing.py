"""Tests for Inngest workflows in src/workflows/daily_briefing.py.

Covers the daily_briefing_workflow and cost_monitor_workflow functions.
All external dependencies (SideraAgent, SlackConnector) are mocked.
Inngest Context is simulated with a helper that wires step.run to
actually call the handler so we can verify end-to-end logic.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import inngest
import pytest

from src.workflows.daily_briefing import (
    all_workflows,
    cost_monitor_workflow,
    daily_briefing_workflow,
    manager_runner_workflow,
    skill_runner_workflow,
    skill_scheduler_workflow,
    token_refresh_workflow,
)
from tests.test_workflows.conftest import (
    SAMPLE_ACCOUNTS,
    SAMPLE_RECOMMENDATIONS,
    _make_mock_context,
)

# =====================================================================
# Fixtures
# =====================================================================


def _make_mock_briefing_result():
    """Create a mock BriefingResult returned by SideraAgent."""
    result = MagicMock()
    result.briefing_text = "## Daily Briefing\nAll campaigns performing well."
    result.recommendations = SAMPLE_RECOMMENDATIONS
    result.cost = {"total_cost_usd": 0.42, "num_turns": 5}
    result.session_id = "session-abc-123"
    return result


@pytest.fixture()
def mock_context():
    """Context with valid accounts and channel."""
    return _make_mock_context(
        event_data={
            "accounts": SAMPLE_ACCOUNTS,
            "user_id": "user-42",
            "channel_id": "C0123SLACK",
        }
    )


@pytest.fixture()
def mock_context_no_accounts():
    """Context with empty accounts list."""
    return _make_mock_context(event_data={"accounts": [], "user_id": "user-42"})


@pytest.fixture()
def mock_context_no_recs():
    """Context with accounts but the agent returns no recommendations."""
    return _make_mock_context(
        event_data={
            "accounts": SAMPLE_ACCOUNTS,
            "user_id": "user-42",
            "channel_id": "C0123SLACK",
        }
    )


# =====================================================================
# Daily briefing — success path
# =====================================================================


@pytest.mark.asyncio
async def test_daily_briefing_success(mock_context):
    """Full happy-path: analysis -> briefing -> approvals -> wait -> log."""
    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=_make_mock_briefing_result())
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True, "ts": "111.222"}
    mock_slack.send_approval_request.return_value = {"ok": True}

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        result = await daily_briefing_workflow._handler(mock_context)

    # Verify result structure
    assert result["user_id"] == "user-42"
    assert result["briefing_sent"] is True
    assert result["approvals_sent"] == 2
    assert len(result["decisions"]) == 2
    # Both should be expired since wait_for_event returns None by default
    for decision in result["decisions"].values():
        assert decision["status"] == "expired"


# =====================================================================
# Daily briefing — no accounts
# =====================================================================


@pytest.mark.asyncio
async def test_daily_briefing_no_accounts(mock_context_no_accounts):
    """Raises NonRetriableError when no accounts configured."""
    with pytest.raises(inngest.NonRetriableError, match="No accounts configured"):
        await daily_briefing_workflow._handler(mock_context_no_accounts)


@pytest.mark.asyncio
async def test_daily_briefing_missing_accounts_key():
    """Raises NonRetriableError when accounts key absent from event data."""
    ctx = _make_mock_context(event_data={"user_id": "user-42"})
    with pytest.raises(inngest.NonRetriableError, match="No accounts configured"):
        await daily_briefing_workflow._handler(ctx)


# =====================================================================
# Daily briefing — empty recommendations
# =====================================================================


@pytest.mark.asyncio
async def test_daily_briefing_no_recommendations(mock_context_no_recs):
    """When agent returns no recommendations, no approvals are sent."""
    mock_result = _make_mock_briefing_result()
    mock_result.recommendations = []

    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=mock_result)
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        result = await daily_briefing_workflow._handler(mock_context_no_recs)

    assert result["approvals_sent"] == 0
    assert result["decisions"] == {}
    # send_approval_request should never be called
    mock_slack.send_approval_request.assert_not_called()


# =====================================================================
# Daily briefing — approval received
# =====================================================================


@pytest.mark.asyncio
async def test_daily_briefing_approval_received(mock_context):
    """When wait_for_event returns an event, the decision is captured."""
    # Simulate an approved event coming back
    approved_event = MagicMock()
    approved_event.data = MagicMock()
    approved_event.data.get = lambda key, default="": {
        "status": "approved",
        "decided_by": "user-42",
        "approval_id": "approval-test-run-123-0",
    }.get(key, default)

    mock_context.step.wait_for_event = AsyncMock(return_value=approved_event)

    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=_make_mock_briefing_result())
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}
    mock_slack.send_approval_request.return_value = {"ok": True}

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        result = await daily_briefing_workflow._handler(mock_context)

    # All decisions should be "approved" since the mock always returns
    for decision in result["decisions"].values():
        assert decision["status"] == "approved"
        assert decision["decided_by"] == "user-42"


@pytest.mark.asyncio
async def test_daily_briefing_approval_rejected(mock_context):
    """When a recommendation is rejected, the decision captures that."""
    rejected_event = MagicMock()
    rejected_event.data = MagicMock()
    rejected_event.data.get = lambda key, default="": {
        "status": "rejected",
        "decided_by": "user-99",
        "approval_id": "approval-test-run-123-0",
    }.get(key, default)

    mock_context.step.wait_for_event = AsyncMock(return_value=rejected_event)

    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=_make_mock_briefing_result())
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}
    mock_slack.send_approval_request.return_value = {"ok": True}

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        result = await daily_briefing_workflow._handler(mock_context)

    for decision in result["decisions"].values():
        assert decision["status"] == "rejected"


# =====================================================================
# Daily briefing — approval expired (timeout)
# =====================================================================


@pytest.mark.asyncio
async def test_daily_briefing_approval_expired(mock_context):
    """When wait_for_event returns None, the decision is 'expired'."""
    # Default mock returns None for wait_for_event
    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=_make_mock_briefing_result())
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}
    mock_slack.send_approval_request.return_value = {"ok": True}

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        result = await daily_briefing_workflow._handler(mock_context)

    for decision in result["decisions"].values():
        assert decision["status"] == "expired"


# =====================================================================
# Daily briefing — mixed approval outcomes
# =====================================================================


@pytest.mark.asyncio
async def test_daily_briefing_mixed_approvals(mock_context):
    """First recommendation approved, second expired."""
    call_count = 0

    async def mixed_wait(step_id, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First recommendation approved
            event = MagicMock()
            event.data = MagicMock()
            event.data.get = lambda key, default="": {
                "status": "approved",
                "decided_by": "user-42",
            }.get(key, default)
            return event
        # Second recommendation expires
        return None

    mock_context.step.wait_for_event = AsyncMock(side_effect=mixed_wait)

    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=_make_mock_briefing_result())
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}
    mock_slack.send_approval_request.return_value = {"ok": True}

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        result = await daily_briefing_workflow._handler(mock_context)

    decisions = list(result["decisions"].values())
    assert decisions[0]["status"] == "approved"
    assert decisions[1]["status"] == "expired"


# =====================================================================
# Daily briefing — step IDs are deterministic
# =====================================================================


@pytest.mark.asyncio
async def test_daily_briefing_step_ids_deterministic(mock_context):
    """Step IDs must be fixed strings (required by Inngest memoization)."""
    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=_make_mock_briefing_result())
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}
    mock_slack.send_approval_request.return_value = {"ok": True}

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        await daily_briefing_workflow._handler(mock_context)

    # Collect all step IDs from step.run calls
    step_ids = [call.args[0] for call in mock_context.step.run.call_args_list]
    assert "check-existing-briefing" in step_ids
    assert "run-analysis" in step_ids
    assert "send-briefing" in step_ids
    assert "send-approval-0" in step_ids
    assert "send-approval-1" in step_ids
    assert "log-results" in step_ids

    # All step IDs are strings
    for sid in step_ids:
        assert isinstance(sid, str)
        assert len(sid) > 0


# =====================================================================
# Daily briefing — default user_id
# =====================================================================


@pytest.mark.asyncio
async def test_daily_briefing_default_user_id():
    """When user_id is missing from event data, defaults to 'default'."""
    ctx = _make_mock_context(event_data={"accounts": SAMPLE_ACCOUNTS, "channel_id": "C01"})

    mock_result = _make_mock_briefing_result()
    mock_result.recommendations = []

    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=mock_result)
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        result = await daily_briefing_workflow._handler(ctx)

    assert result["user_id"] == "default"


# =====================================================================
# Daily briefing — agent called with correct arguments
# =====================================================================


@pytest.mark.asyncio
async def test_daily_briefing_agent_called_correctly(mock_context):
    """SideraAgent.run_daily_briefing_optimized called with user_id and accounts."""
    mock_result = _make_mock_briefing_result()
    mock_result.recommendations = []

    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=mock_result)
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        await daily_briefing_workflow._handler(mock_context)

    mock_agent.run_daily_briefing_optimized.assert_awaited_once_with(
        user_id="user-42",
        account_ids=SAMPLE_ACCOUNTS,
        force_refresh=False,
    )


# =====================================================================
# Cost monitor — under threshold (no alert)
# =====================================================================


@pytest.mark.asyncio
async def test_cost_monitor_under_threshold():
    """No alert sent when cost is below 80% of limit."""
    ctx = _make_mock_context()

    result = await cost_monitor_workflow._handler(ctx)

    assert result["total_cost_today"] == 0.0
    assert result["limit"] == 10.0
    # step.run called only once (check-costs), not twice (no alert)
    assert mock_step_run_count(ctx) == 1


@pytest.mark.asyncio
async def test_cost_monitor_at_threshold():
    """No alert sent when cost is exactly at 80% threshold."""
    ctx = _make_mock_context()

    # Override check_costs to return exactly 80%
    original_run = ctx.step.run.side_effect

    async def custom_run(step_id, handler, *args):
        if step_id == "check-costs":
            return {"total_cost_today": 8.0, "limit": 10.0, "accounts_checked": 3}
        return await original_run(step_id, handler, *args)

    ctx.step.run = AsyncMock(side_effect=custom_run)

    result = await cost_monitor_workflow._handler(ctx)

    assert result["total_cost_today"] == 8.0
    # 8.0 == 10.0 * 0.8, not strictly greater than, so no alert
    assert mock_step_run_count(ctx) == 1


# =====================================================================
# Cost monitor — over threshold (alert sent)
# =====================================================================


@pytest.mark.asyncio
async def test_cost_monitor_over_threshold():
    """Alert sent when cost exceeds 80% of limit."""
    ctx = _make_mock_context()

    async def custom_run(step_id, handler, *args):
        if step_id == "check-costs":
            return {
                "total_cost_today": 8.50,
                "limit": 10.0,
                "accounts_checked": 5,
            }
        if step_id == "send-cost-alert":
            # Actually call the handler so we can verify it works
            if asyncio.iscoroutinefunction(handler):
                return await handler(*args)
            return handler(*args)
        return None

    ctx.step.run = AsyncMock(side_effect=custom_run)

    mock_slack = MagicMock()
    mock_slack.send_alert.return_value = {"ok": True}

    with patch("src.connectors.slack.SlackConnector", return_value=mock_slack):
        result = await cost_monitor_workflow._handler(ctx)

    assert result["total_cost_today"] == 8.50
    # step.run called twice: check-costs + send-cost-alert
    assert mock_step_run_count(ctx) == 2
    mock_slack.send_alert.assert_called_once()


@pytest.mark.asyncio
async def test_cost_monitor_alert_message_content():
    """Alert message contains cost figures and percentage."""
    ctx = _make_mock_context()

    async def custom_run(step_id, handler, *args):
        if step_id == "check-costs":
            return {
                "total_cost_today": 9.25,
                "limit": 10.0,
                "accounts_checked": 2,
            }
        if step_id == "send-cost-alert":
            if asyncio.iscoroutinefunction(handler):
                return await handler(*args)
            return handler(*args)
        return None

    ctx.step.run = AsyncMock(side_effect=custom_run)

    mock_slack = MagicMock()
    mock_slack.send_alert.return_value = {"ok": True}

    with patch("src.connectors.slack.SlackConnector", return_value=mock_slack):
        await cost_monitor_workflow._handler(ctx)

    call_kwargs = mock_slack.send_alert.call_args
    message = call_kwargs.kwargs.get("message") or call_kwargs[1].get("message", "")
    assert "$9.25" in message
    assert "$10.00" in message
    assert "92%" in message or "93%" in message


# =====================================================================
# Cron trigger configuration
# =====================================================================


def test_daily_briefing_cron_trigger():
    """Daily briefing runs at 7 AM weekdays."""
    config = daily_briefing_workflow.get_config("sidera")
    triggers = config.main.triggers
    assert len(triggers) == 1
    trigger = triggers[0]
    assert trigger.cron == "0 7 * * MON-FRI"


def test_cost_monitor_cron_trigger():
    """Cost monitor runs every 30 minutes."""
    config = cost_monitor_workflow.get_config("sidera")
    triggers = config.main.triggers
    assert len(triggers) == 1
    trigger = triggers[0]
    assert trigger.cron == "*/30 * * * *"


# =====================================================================
# Function IDs
# =====================================================================


def test_daily_briefing_function_id():
    """Daily briefing has the expected function ID."""
    assert daily_briefing_workflow.id == "sidera-sidera-daily-briefing"


def test_cost_monitor_function_id():
    """Cost monitor has the expected function ID."""
    assert cost_monitor_workflow.id == "sidera-sidera-cost-monitor"


# =====================================================================
# Exports
# =====================================================================


def test_all_workflows_list():
    """all_workflows exports all eighteen workflow functions."""
    assert len(all_workflows) == 18
    assert daily_briefing_workflow in all_workflows
    assert cost_monitor_workflow in all_workflows
    assert skill_runner_workflow in all_workflows
    assert skill_scheduler_workflow in all_workflows
    assert token_refresh_workflow in all_workflows
    assert manager_runner_workflow in all_workflows


# =====================================================================
# Wait-for-event parameters
# =====================================================================


@pytest.mark.asyncio
async def test_wait_for_event_uses_correct_params(mock_context):
    """Verify wait_for_event called with correct event name and timeout."""
    mock_agent = MagicMock()
    mock_result = _make_mock_briefing_result()
    mock_result.recommendations = [SAMPLE_RECOMMENDATIONS[0]]
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=mock_result)
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}
    mock_slack.send_approval_request.return_value = {"ok": True}

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        await daily_briefing_workflow._handler(mock_context)

    wait_call = mock_context.step.wait_for_event.call_args_list[0]
    # Positional arg is step_id
    assert "wait-approval-" in wait_call.args[0]
    # Keyword args
    assert wait_call.kwargs["event"] == "sidera/approval.decided"
    assert wait_call.kwargs["timeout"] == 86_400_000


# =====================================================================
# Deduplication — existing briefing found
# =====================================================================


@pytest.mark.asyncio
async def test_daily_briefing_deduplication_skips_agent():
    """When today's briefing exists in DB, agent.run_daily_briefing_optimized is NOT called."""
    ctx = _make_mock_context(
        event_data={
            "accounts": SAMPLE_ACCOUNTS,
            "user_id": "user-42",
            "channel_id": "C0123SLACK",
        }
    )

    # Mock DB to return an existing analysis
    mock_existing = MagicMock()
    mock_existing.briefing_content = "## Cached Briefing\nPrevious analysis."
    mock_existing.recommendations = []

    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=_make_mock_briefing_result())
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
        patch("src.db.service.get_analyses_for_period", return_value=[mock_existing]),
        patch("src.db.session.get_db_session") as mock_get_session,
    ):
        # Make get_db_session return an async context manager
        mock_session = AsyncMock()
        mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

        await daily_briefing_workflow._handler(ctx)

    # Agent should NOT have been called
    mock_agent.run_daily_briefing_optimized.assert_not_awaited()
    # Briefing should still be sent to Slack (with cached content)
    mock_slack.send_briefing.assert_called_once()
    briefing_text = mock_slack.send_briefing.call_args.kwargs.get("briefing_text", "")
    assert "Cached Briefing" in briefing_text or "Previous analysis" in briefing_text


@pytest.mark.asyncio
async def test_daily_briefing_deduplication_force_refresh():
    """When force_refresh=True, agent runs even if today's briefing exists."""
    ctx = _make_mock_context(
        event_data={
            "accounts": SAMPLE_ACCOUNTS,
            "user_id": "user-42",
            "channel_id": "C0123SLACK",
            "force_refresh": True,
        }
    )

    mock_result = _make_mock_briefing_result()
    mock_result.recommendations = []

    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=mock_result)
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        await daily_briefing_workflow._handler(ctx)

    # Agent SHOULD have been called despite any cached result
    mock_agent.run_daily_briefing_optimized.assert_awaited_once()
    # Verify force_refresh was passed through
    call_kwargs = mock_agent.run_daily_briefing_optimized.call_args
    assert call_kwargs.kwargs.get("force_refresh") is True


@pytest.mark.asyncio
async def test_daily_briefing_deduplication_db_error_falls_through():
    """When the DB check fails, the workflow falls through to fresh analysis."""
    ctx = _make_mock_context(
        event_data={
            "accounts": SAMPLE_ACCOUNTS,
            "user_id": "user-42",
            "channel_id": "C0123SLACK",
        }
    )

    mock_result = _make_mock_briefing_result()
    mock_result.recommendations = []

    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=mock_result)
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}

    # DB will fail (no real DB configured) — the except handler returns {"exists": False}
    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        await daily_briefing_workflow._handler(ctx)

    # Agent should be called because dedup falls through on error
    mock_agent.run_daily_briefing_optimized.assert_awaited_once()


# =====================================================================
# force_refresh parameter extraction
# =====================================================================


@pytest.mark.asyncio
async def test_daily_briefing_force_refresh_default_false():
    """force_refresh defaults to False when not in event data."""
    ctx = _make_mock_context(
        event_data={
            "accounts": SAMPLE_ACCOUNTS,
            "user_id": "user-42",
            "channel_id": "C0123SLACK",
            # No force_refresh key
        }
    )

    mock_result = _make_mock_briefing_result()
    mock_result.recommendations = []

    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=mock_result)
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        await daily_briefing_workflow._handler(ctx)

    call_kwargs = mock_agent.run_daily_briefing_optimized.call_args
    assert call_kwargs.kwargs.get("force_refresh") is False


# =====================================================================
# Deduplication — save_to_db skipped for cached results
# =====================================================================


@pytest.mark.asyncio
async def test_daily_briefing_dedup_skips_save():
    """When using a deduplicated result, save_to_db returns skipped=True."""
    ctx = _make_mock_context(
        event_data={
            "accounts": SAMPLE_ACCOUNTS,
            "user_id": "user-42",
            "channel_id": "C0123SLACK",
        }
    )

    # Track what the save-to-db step returns
    save_results = []

    original_run = ctx.step.run.side_effect

    async def tracking_run(step_id, handler, *args):
        result = await original_run(step_id, handler, *args)
        if step_id == "save-to-db":
            save_results.append(result)
        return result

    ctx.step.run = AsyncMock(side_effect=tracking_run)

    mock_existing = MagicMock()
    mock_existing.briefing_content = "## Cached\nExisting."
    mock_existing.recommendations = []

    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock()
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
        patch("src.db.service.get_analyses_for_period", return_value=[mock_existing]),
        patch("src.db.session.get_db_session") as mock_get_session,
    ):
        mock_session = AsyncMock()
        mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

        await daily_briefing_workflow._handler(ctx)

    # save-to-db should have returned skipped=True
    assert len(save_results) == 1
    assert save_results[0].get("skipped") is True


# =====================================================================
# Helpers
# =====================================================================


def mock_step_run_count(ctx: MagicMock) -> int:
    """Return how many times ctx.step.run was awaited."""
    return ctx.step.run.await_count
