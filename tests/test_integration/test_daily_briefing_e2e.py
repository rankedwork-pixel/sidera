"""End-to-end integration tests for the daily briefing workflow.

Tests the complete flow: Inngest trigger -> Agent analysis -> DB persistence -> Slack delivery.
All external APIs (Google Ads, Meta, Slack, Claude) are mocked, but internal wiring is real.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import inngest
import pytest

from src.api.routes import slack as slack_module
from src.workflows.daily_briefing import (
    cost_monitor_workflow,
    daily_briefing_workflow,
)

# =====================================================================
# Constants & fixture data
# =====================================================================

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

SAMPLE_ACCOUNTS = [
    {
        "platform": "google_ads",
        "account_id": "1234567890",
        "account_name": "Acme Store",
        "target_roas": 4.0,
        "target_cpa": 25.00,
        "monthly_budget_cap": 50_000,
    },
    {
        "platform": "meta",
        "account_id": "120330000000123456",
        "account_name": "Acme Meta",
        "target_roas": 3.5,
        "target_cpa": 30.00,
        "monthly_budget_cap": 30_000,
    },
]

SAMPLE_RECOMMENDATIONS = [
    {
        "action": "Increase search budget by 15%",
        "reasoning": "Strong ROAS trend over last 7 days",
        "projected_impact": "+$2,400 revenue/week",
        "risk_level": "low",
    },
    {
        "action": "Pause underperforming ad set",
        "reasoning": "CPA 3x above target with declining CTR",
        "projected_impact": "Save $500/week",
        "risk_level": "medium",
    },
]


def _load_fixture(name: str) -> dict:
    """Load a JSON fixture file from tests/fixtures/."""
    return json.loads((FIXTURES_DIR / name).read_text())


# =====================================================================
# Helpers
# =====================================================================


def _make_mock_context(event_data: dict | None = None) -> MagicMock:
    """Build a mock Inngest Context with working step.run.

    step.run calls the async handler and returns its result.
    step.wait_for_event defaults to None (timeout / expiry).
    """
    ctx = MagicMock()
    ctx.event = MagicMock()
    ctx.event.data = event_data or {}
    ctx.run_id = "e2e-test-run-001"

    async def mock_step_run(step_id: str, handler, *args):
        if asyncio.iscoroutinefunction(handler):
            return await handler(*args)
        return handler(*args)

    ctx.step.run = AsyncMock(side_effect=mock_step_run)
    ctx.step.wait_for_event = AsyncMock(return_value=None)
    ctx.step.send_event = AsyncMock(return_value=["event-id-1"])

    return ctx


def _make_mock_briefing_result():
    """Create a mock BriefingResult returned by SideraAgent."""
    result = MagicMock()
    result.briefing_text = (
        "## Daily Briefing\n"
        "All campaigns performing well.\n\n"
        "## Recommendations\n"
        "- Action: Increase search budget by 15%\n"
        "  Reasoning: Strong ROAS trend\n"
    )
    result.recommendations = SAMPLE_RECOMMENDATIONS
    result.cost = {"total_cost_usd": 0.42, "num_turns": 5}
    result.session_id = "session-e2e-001"
    return result


def _mock_step_run_count(ctx: MagicMock) -> int:
    """Return how many times ctx.step.run was awaited."""
    return ctx.step.run.await_count


# =====================================================================
# Test 1: Full happy-path briefing flow with mocked services
# =====================================================================


@pytest.mark.asyncio
async def test_full_briefing_flow_with_mocked_services():
    """Complete happy path: analysis runs, briefing sent, approvals created, results logged.

    Verifies the full end-to-end internal wiring:
    1. load_accounts returns accounts from event data
    2. SideraAgent.run_daily_briefing_optimized is called with correct args
    3. save_to_db step runs (mocked DB)
    4. SlackConnector.send_briefing is called with briefing text
    5. Approval requests are sent for each recommendation
    6. wait_for_event is called for each approval
    7. log_results aggregates everything correctly
    """
    ctx = _make_mock_context(
        event_data={
            "accounts": SAMPLE_ACCOUNTS,
            "user_id": "user-e2e",
            "channel_id": "C0123E2E",
        }
    )

    mock_briefing = _make_mock_briefing_result()
    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=mock_briefing)

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True, "channel": "C0123E2E", "ts": "111.222"}
    mock_slack.send_approval_request.return_value = {
        "ok": True,
        "channel": "C0123E2E",
        "ts": "333.444",
    }

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
        patch("src.db.session.get_db_session", side_effect=RuntimeError("no db")),
    ):
        result = await daily_briefing_workflow._handler(ctx)

    # 1. Verify agent was called with correct user and accounts
    mock_agent.run_daily_briefing_optimized.assert_awaited_once_with(
        user_id="user-e2e",
        account_ids=SAMPLE_ACCOUNTS,
        force_refresh=False,
    )

    # 2. Verify briefing sent to Slack
    mock_slack.send_briefing.assert_called_once()
    briefing_call = mock_slack.send_briefing.call_args
    channel_kwarg = briefing_call.kwargs.get("channel_id") or briefing_call[1].get("channel_id")
    assert channel_kwarg == "C0123E2E"

    # 3. Verify approval requests sent (one per recommendation)
    assert mock_slack.send_approval_request.call_count == len(SAMPLE_RECOMMENDATIONS)

    # 4. Verify result structure
    assert result["user_id"] == "user-e2e"
    assert result["briefing_sent"] is True
    assert result["approvals_sent"] == 2
    assert len(result["decisions"]) == 2
    assert result["cost"] == {"total_cost_usd": 0.42, "num_turns": 5}

    # 5. Verify all step IDs are present
    step_ids = [call.args[0] for call in ctx.step.run.call_args_list]
    assert "load-accounts" in step_ids
    assert "check-existing-briefing" in step_ids
    assert "run-analysis" in step_ids
    assert "save-to-db" in step_ids
    assert "send-briefing" in step_ids
    assert "send-approval-0" in step_ids
    assert "send-approval-1" in step_ids
    assert "log-results" in step_ids


# =====================================================================
# Test 2: Handles no accounts by raising NonRetriableError
# =====================================================================


@pytest.mark.asyncio
async def test_briefing_handles_no_accounts():
    """Workflow raises NonRetriableError when no accounts are configured.

    This ensures the Inngest function signals a permanent failure
    (no point retrying if there are no accounts).
    """
    ctx = _make_mock_context(
        event_data={
            "accounts": [],
            "user_id": "user-empty",
        }
    )

    with pytest.raises(inngest.NonRetriableError, match="No accounts configured"):
        await daily_briefing_workflow._handler(ctx)

    # Also test missing accounts key entirely
    ctx2 = _make_mock_context(event_data={"user_id": "user-no-key"})
    with pytest.raises(inngest.NonRetriableError, match="No accounts configured"):
        await daily_briefing_workflow._handler(ctx2)


# =====================================================================
# Test 3: Briefing continues without DB (graceful degradation)
# =====================================================================


@pytest.mark.asyncio
async def test_briefing_continues_without_db():
    """Workflow completes successfully even when database is unavailable.

    The save_to_db and log_results steps catch DB exceptions internally,
    so the briefing is still sent to Slack regardless of DB state.
    """
    ctx = _make_mock_context(
        event_data={
            "accounts": SAMPLE_ACCOUNTS,
            "user_id": "user-no-db",
            "channel_id": "C0NO_DB",
        }
    )

    mock_briefing = _make_mock_briefing_result()
    mock_briefing.recommendations = []  # No approvals needed

    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=mock_briefing)

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True, "channel": "C0NO_DB", "ts": "999.000"}

    # DB is unavailable -- get_db_session raises RuntimeError
    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
        patch("src.db.session.get_db_session", side_effect=RuntimeError("Database not configured")),
    ):
        result = await daily_briefing_workflow._handler(ctx)

    # Workflow should still complete
    assert result["user_id"] == "user-no-db"
    assert result["briefing_sent"] is True

    # Briefing still sent to Slack
    mock_slack.send_briefing.assert_called_once()

    # Agent analysis still ran
    mock_agent.run_daily_briefing_optimized.assert_awaited_once()


# =====================================================================
# Test 4: Approval flow (approve and reject via Slack route handlers)
# =====================================================================


@pytest.mark.asyncio
async def test_approval_flow_approve_and_reject():
    """Simulates approve/reject button clicks through Slack route handlers.

    Tests the in-memory approval store and verifies that the handlers
    correctly record decisions.
    """
    # Clear any lingering approvals from other tests
    slack_module._pending_approvals.clear()

    approval_id_approve = "approval-e2e-test-0"
    approval_id_reject = "approval-e2e-test-1"

    # -- Simulate approval click --
    mock_ack = AsyncMock()
    mock_client = AsyncMock()
    mock_client.chat_update = AsyncMock()

    approve_body = {
        "actions": [{"value": approval_id_approve}],
        "user": {"id": "U_APPROVER"},
        "channel": {"id": "C0123"},
        "container": {"message_ts": "111.222"},
    }

    with patch("src.db.session.get_db_session", side_effect=RuntimeError("no db")):
        await slack_module.handle_approve(mock_ack, approve_body, mock_client)

    # Verify ack was called
    mock_ack.assert_awaited_once()

    # Verify message was updated
    mock_client.chat_update.assert_awaited_once()

    # Verify in-memory store
    status = slack_module.get_approval_status(approval_id_approve)
    assert status is not None
    assert status["status"] == "approved"
    assert status["decided_by"] == "U_APPROVER"

    # -- Simulate rejection click --
    mock_ack2 = AsyncMock()
    mock_client2 = AsyncMock()
    mock_client2.chat_update = AsyncMock()

    reject_body = {
        "actions": [{"value": approval_id_reject}],
        "user": {"id": "U_REJECTOR"},
        "channel": {"id": "C0123"},
        "container": {"message_ts": "333.444"},
    }

    with patch("src.db.session.get_db_session", side_effect=RuntimeError("no db")):
        await slack_module.handle_reject(mock_ack2, reject_body, mock_client2)

    # Verify ack was called
    mock_ack2.assert_awaited_once()

    # Verify in-memory store
    status = slack_module.get_approval_status(approval_id_reject)
    assert status is not None
    assert status["status"] == "rejected"
    assert status["decided_by"] == "U_REJECTOR"

    # Cleanup
    slack_module._pending_approvals.clear()


# =====================================================================
# Test 5: Cost monitor sends alert above threshold
# =====================================================================


@pytest.mark.asyncio
async def test_cost_monitor_sends_alert_above_threshold():
    """Cost monitor detects high usage and sends a Slack alert.

    Verifies that when daily LLM cost exceeds 80% of the configured
    limit, the workflow sends an alert to Slack with correct figures.
    """
    ctx = _make_mock_context()

    # Override step.run to return high cost from check-costs step
    async def custom_step_run(step_id, handler, *args):
        if step_id == "check-costs":
            return {
                "total_cost_today": 9.50,
                "limit": 10.0,
                "accounts_checked": 3,
                "source": "test",
            }
        # For send-cost-alert, actually invoke the handler
        if step_id == "send-cost-alert":
            if asyncio.iscoroutinefunction(handler):
                return await handler(*args)
            return handler(*args)
        return None

    ctx.step.run = AsyncMock(side_effect=custom_step_run)

    mock_slack = MagicMock()
    mock_slack.send_alert.return_value = {"ok": True, "channel": "C0ALERT", "ts": "555.666"}

    with patch("src.connectors.slack.SlackConnector", return_value=mock_slack):
        result = await cost_monitor_workflow._handler(ctx)

    # Verify the result
    assert result["total_cost_today"] == 9.50
    assert result["limit"] == 10.0

    # Verify alert was sent
    mock_slack.send_alert.assert_called_once()

    # Verify alert content
    alert_call = mock_slack.send_alert.call_args
    kwargs = alert_call.kwargs if alert_call.kwargs else {}
    # send_alert is called with keyword arguments
    message = kwargs.get("message", "")
    assert "$9.50" in message
    assert "$10.00" in message
    assert "95%" in message

    # Verify step.run was called twice (check-costs + send-cost-alert)
    assert _mock_step_run_count(ctx) == 2


@pytest.mark.asyncio
async def test_cost_monitor_no_alert_under_threshold():
    """Cost monitor does NOT send alert when usage is below 80%."""
    ctx = _make_mock_context()

    async def custom_step_run(step_id, handler, *args):
        if step_id == "check-costs":
            return {
                "total_cost_today": 5.00,
                "limit": 10.0,
                "accounts_checked": 2,
                "source": "test",
            }
        return None

    ctx.step.run = AsyncMock(side_effect=custom_step_run)

    result = await cost_monitor_workflow._handler(ctx)

    assert result["total_cost_today"] == 5.00
    # Only one step.run call (check-costs), no alert
    assert _mock_step_run_count(ctx) == 1


# =====================================================================
# Test: Fixtures load correctly
# =====================================================================


def test_google_ads_fixture_loads():
    """Verify Google Ads sample fixture loads and has expected structure."""
    data = _load_fixture("google_ads_sample.json")
    assert "campaigns" in data
    assert len(data["campaigns"]) == 2

    campaign = data["campaigns"][0]
    assert campaign["campaign.name"] == "Brand Search - US"
    assert campaign["campaign.status"] == "ENABLED"
    assert int(campaign["metrics.cost_micros"]) == 12_450_000


def test_meta_fixture_loads():
    """Verify Meta sample fixture loads and has expected structure."""
    data = _load_fixture("meta_sample.json")
    assert "campaigns" in data
    assert len(data["campaigns"]) == 1

    campaign = data["campaigns"][0]
    assert campaign["name"] == "Prospecting - Lookalike"
    assert campaign["status"] == "ACTIVE"
    assert len(campaign["actions"]) == 2
    assert campaign["actions"][0]["action_type"] == "purchase"
