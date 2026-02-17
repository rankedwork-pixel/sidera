"""Tests for Sentry capture_exception wiring in workflows and connectors.

Verifies that ``capture_exception`` is called with the actual exception
object when various except blocks fire in:

- ``daily_briefing_workflow`` (load_accounts, check_existing_briefing,
  save_to_db, log_results)
- ``skill_runner_workflow`` (load_accounts, save_to_db)
- ``skill_scheduler_workflow`` (check_and_dispatch inner except)
- ``SlackConnector._handle_slack_error``

All external dependencies (DB, agent, Slack, registries) are mocked.
Inngest Context is simulated with the shared ``_make_mock_context`` helper.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.workflows.daily_briefing import (
    daily_briefing_workflow,
    skill_runner_workflow,
    skill_scheduler_workflow,
)
from tests.test_workflows.conftest import (
    SAMPLE_ACCOUNTS,
    SAMPLE_RECOMMENDATIONS,
    _make_mock_context,
)

# =====================================================================
# Helpers
# =====================================================================


def _make_mock_briefing_result():
    """Create a mock BriefingResult returned by SideraAgent."""
    result = MagicMock()
    result.briefing_text = "## Daily Briefing\nAll campaigns performing well."
    result.recommendations = SAMPLE_RECOMMENDATIONS
    result.cost = {"total_cost_usd": 0.42, "num_turns": 5}
    result.session_id = "session-abc-123"
    result.degradation_status = None
    return result


def _make_mock_skill_result(
    *,
    skill_id: str = "test_skill",
    chain_next: str | None = None,
) -> MagicMock:
    """Create a mock SkillResult returned by SkillExecutor.execute."""
    result = MagicMock()
    result.skill_id = skill_id
    result.output_text = "## Skill Output\nAnalysis complete."
    result.recommendations = SAMPLE_RECOMMENDATIONS
    result.cost = {"total_cost_usd": 0.25, "num_turns": 3}
    result.session_id = "session-skill-789"
    result.chain_next = chain_next
    return result


def _make_skill_definition(
    *,
    skill_id: str = "test_skill",
    requires_approval: bool = False,
) -> MagicMock:
    """Build a mock SkillDefinition."""
    skill = MagicMock()
    skill.id = skill_id
    skill.name = f"Test Skill ({skill_id})"
    skill.requires_approval = requires_approval
    skill.chain_after = None
    skill.schedule = None
    return skill


def _briefing_context(**overrides):
    """Build a standard daily-briefing context with optional overrides."""
    data = {
        "accounts": SAMPLE_ACCOUNTS,
        "user_id": "user-42",
        "channel_id": "C0123SLACK",
    }
    data.update(overrides)
    return _make_mock_context(event_data=data)


def _skill_runner_context(**overrides):
    """Build a standard skill-runner context with optional overrides."""
    data = {
        "skill_id": "test_skill",
        "user_id": "user-42",
        "channel_id": "C0123SLACK",
        "accounts": SAMPLE_ACCOUNTS,
        "params": {},
        "chain_depth": 0,
    }
    data.update(overrides)
    return _make_mock_context(event_data=data)


# =====================================================================
# 1. daily_briefing_workflow: capture_exception when load_accounts fails
# =====================================================================


@pytest.mark.asyncio
async def test_briefing_load_accounts_calls_capture_exception():
    """When the DB raises in load_accounts, capture_exception is called."""
    ctx = _briefing_context()
    db_error = RuntimeError("DB connection refused")

    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=_make_mock_briefing_result())
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}
    mock_slack.send_approval_request.return_value = {"ok": True}

    mock_session = AsyncMock()
    mock_get_session = MagicMock()
    mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_get_accounts = AsyncMock(side_effect=db_error)

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
        patch("src.db.session.get_db_session", mock_get_session),
        patch("src.db.service.get_accounts_for_user", mock_get_accounts),
        patch("src.middleware.sentry_setup.capture_exception") as mock_capture,
    ):
        result = await daily_briefing_workflow._handler(ctx)

    # capture_exception must have been called with the actual exception
    mock_capture.assert_any_call(db_error)
    # Workflow should still complete (falls back to event data)
    assert result["user_id"] == "user-42"


# =====================================================================
# 2. daily_briefing_workflow: capture_exception when
#    check_existing_briefing fails
# =====================================================================


@pytest.mark.asyncio
async def test_briefing_check_existing_calls_capture_exception():
    """When the DB raises in check_existing_briefing, capture_exception is called."""
    ctx = _briefing_context()
    db_error = ConnectionError("Cannot reach Supabase")

    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=_make_mock_briefing_result())
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}
    mock_slack.send_approval_request.return_value = {"ok": True}

    mock_session = AsyncMock()
    mock_get_session = MagicMock()
    mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_get_analyses = AsyncMock(side_effect=db_error)

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
        patch("src.db.session.get_db_session", mock_get_session),
        patch("src.db.service.get_analyses_for_period", mock_get_analyses),
        patch("src.middleware.sentry_setup.capture_exception") as mock_capture,
    ):
        result = await daily_briefing_workflow._handler(ctx)

    mock_capture.assert_any_call(db_error)
    # Should fall through to fresh analysis
    assert result["briefing_sent"] is True


# =====================================================================
# 3. daily_briefing_workflow: capture_exception when save_to_db fails
# =====================================================================


@pytest.mark.asyncio
async def test_briefing_save_to_db_calls_capture_exception():
    """When save_analysis_result raises, capture_exception is called."""
    ctx = _briefing_context()
    db_error = RuntimeError("Unique constraint violation")

    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=_make_mock_briefing_result())
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}
    mock_slack.send_approval_request.return_value = {"ok": True}

    mock_session = AsyncMock()
    mock_get_session = MagicMock()
    mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_save = AsyncMock(side_effect=db_error)

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
        patch("src.db.session.get_db_session", mock_get_session),
        patch("src.db.service.save_analysis_result", mock_save),
        patch("src.middleware.sentry_setup.capture_exception") as mock_capture,
    ):
        result = await daily_briefing_workflow._handler(ctx)

    mock_capture.assert_any_call(db_error)
    # Workflow returns fallback (saved=False)
    assert result["briefing_sent"] is True


# =====================================================================
# 4. daily_briefing_workflow: capture_exception when log_results fails
# =====================================================================


@pytest.mark.asyncio
async def test_briefing_log_results_calls_capture_exception():
    """When update_approval_status raises in log_results, capture_exception is called."""
    ctx = _briefing_context()

    # Simulate an approved event so log_results actually tries DB writes
    approved_event = MagicMock()
    approved_event.data = MagicMock()
    approved_event.data.get = lambda key, default="": {
        "status": "approved",
        "decided_by": "user-42",
    }.get(key, default)
    ctx.step.wait_for_event = AsyncMock(return_value=approved_event)

    db_error = RuntimeError("FK violation in approval_queue")

    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=_make_mock_briefing_result())
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}
    mock_slack.send_approval_request.return_value = {"ok": True}

    mock_session = AsyncMock()
    mock_get_session = MagicMock()
    mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_update_approval = AsyncMock(side_effect=db_error)

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
        patch("src.db.session.get_db_session", mock_get_session),
        patch("src.db.service.update_approval_status", mock_update_approval),
        patch("src.middleware.sentry_setup.capture_exception") as mock_capture,
    ):
        result = await daily_briefing_workflow._handler(ctx)

    mock_capture.assert_any_call(db_error)
    # Workflow still returns results despite log_results failure
    assert result["user_id"] == "user-42"
    assert "decisions" in result


# =====================================================================
# 5. skill_runner_workflow: capture_exception when load_accounts fails
# =====================================================================


@pytest.mark.asyncio
async def test_skill_runner_load_accounts_calls_capture_exception():
    """When DB raises in skill_runner load_accounts, capture_exception is called."""
    ctx = _skill_runner_context()
    db_error = RuntimeError("Connection pool exhausted")

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=_make_mock_skill_result())
    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 5
    skill_def = _make_skill_definition(requires_approval=False)
    mock_registry.get.return_value = skill_def

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}

    mock_session = AsyncMock()
    mock_get_session = MagicMock()
    mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_get_accounts = AsyncMock(side_effect=db_error)

    with (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch("src.skills.executor.SkillExecutor", return_value=mock_executor),
        patch("src.agent.core.SideraAgent"),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
        patch("src.db.session.get_db_session", mock_get_session),
        patch("src.db.service.get_accounts_for_user", mock_get_accounts),
        patch("src.middleware.sentry_setup.capture_exception") as mock_capture,
    ):
        result = await skill_runner_workflow._handler(ctx)

    mock_capture.assert_any_call(db_error)
    # Falls back to event data accounts
    assert result["skill_id"] == "test_skill"


# =====================================================================
# 6. skill_runner_workflow: capture_exception when save_to_db fails
# =====================================================================


@pytest.mark.asyncio
async def test_skill_runner_save_to_db_calls_capture_exception():
    """When save_skill_result raises, capture_exception is called."""
    ctx = _skill_runner_context()
    db_error = RuntimeError("Disk full")

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=_make_mock_skill_result())
    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 1
    skill_def = _make_skill_definition(requires_approval=False)
    mock_registry.get.return_value = skill_def

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}

    mock_session = AsyncMock()
    mock_get_session = MagicMock()
    mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_save = AsyncMock(side_effect=db_error)

    with (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch("src.skills.executor.SkillExecutor", return_value=mock_executor),
        patch("src.agent.core.SideraAgent"),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
        patch("src.db.session.get_db_session", mock_get_session),
        patch("src.db.service.save_skill_result", mock_save),
        patch("src.middleware.sentry_setup.capture_exception") as mock_capture,
    ):
        result = await skill_runner_workflow._handler(ctx)

    mock_capture.assert_any_call(db_error)
    # Workflow continues despite save failure
    assert result["output_sent"] is True


# =====================================================================
# 7. skill_scheduler_workflow: capture_exception when scheduler fails
# =====================================================================


@pytest.mark.asyncio
async def test_skill_scheduler_dispatch_error_calls_capture_exception():
    """When inngest_client.send raises during dispatch, capture_exception is called."""
    ctx = _make_mock_context(event_data={"user_id": "user-42", "channel_id": "C01"})
    send_error = RuntimeError("Inngest API unavailable")

    skill_a = MagicMock()
    skill_a.id = "morning_report"
    skill_a.schedule = "0 9 * * *"

    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 1
    mock_registry.list_scheduled.return_value = [skill_a]

    mock_inngest_send = AsyncMock(side_effect=send_error)

    with (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch(
            "src.workflows.daily_briefing.inngest_client.send",
            mock_inngest_send,
        ),
        patch(
            "src.workflows.daily_briefing._cron_matches_now",
            return_value=True,
        ),
        patch("src.middleware.sentry_setup.capture_exception") as mock_capture,
    ):
        result = await skill_scheduler_workflow._handler(ctx)

    mock_capture.assert_any_call(send_error)
    # Scheduler returns 0 dispatched after the error
    assert result["dispatched"] == 0


# =====================================================================
# 8. SlackConnector._handle_slack_error calls capture_exception
# =====================================================================


def test_slack_handle_error_calls_capture_exception():
    """_handle_slack_error calls capture_exception with the SlackApiError."""
    from slack_sdk.errors import SlackApiError

    mock_response = MagicMock()
    mock_response.get.return_value = "channel_not_found"

    exc = SlackApiError(message="channel_not_found", response=mock_response)

    with patch("src.connectors.slack.capture_exception") as mock_capture:
        connector = MagicMock(spec=["_handle_slack_error", "_log"])
        connector._log = MagicMock()

        from src.connectors.slack import SlackConnector, SlackConnectorError

        # Call the actual static-ish method directly via unbound approach
        with pytest.raises(SlackConnectorError):
            SlackConnector._handle_slack_error(connector, exc, "send_briefing")

    mock_capture.assert_called_once_with(exc)


def test_slack_handle_error_auth_error_calls_capture_exception():
    """_handle_slack_error calls capture_exception even for auth errors."""
    from slack_sdk.errors import SlackApiError

    mock_response = MagicMock()
    mock_response.get.return_value = "invalid_auth"

    exc = SlackApiError(message="invalid_auth", response=mock_response)

    with patch("src.connectors.slack.capture_exception") as mock_capture:
        from src.connectors.slack import SlackAuthError, SlackConnector

        connector = SlackConnector(credentials={"bot_token": "xoxb-fake", "channel_id": "C01"})

        with pytest.raises(SlackAuthError):
            connector._handle_slack_error(exc, "test_connection")

    mock_capture.assert_called_once_with(exc)


# =====================================================================
# 9. capture_exception is called with the actual exception object
# =====================================================================


@pytest.mark.asyncio
async def test_capture_exception_receives_exact_exception_object():
    """The exact exception instance is passed to capture_exception, not a copy."""
    ctx = _briefing_context()
    db_error = ValueError("Specific identifiable error #12345")

    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=_make_mock_briefing_result())
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}
    mock_slack.send_approval_request.return_value = {"ok": True}

    mock_session = AsyncMock()
    mock_get_session = MagicMock()
    mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_get_accounts = AsyncMock(side_effect=db_error)

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
        patch("src.db.session.get_db_session", mock_get_session),
        patch("src.db.service.get_accounts_for_user", mock_get_accounts),
        patch("src.middleware.sentry_setup.capture_exception") as mock_capture,
    ):
        await daily_briefing_workflow._handler(ctx)

    # Verify the exact object identity
    captured_args = [c.args[0] for c in mock_capture.call_args_list]
    assert db_error in captured_args
    assert any(arg is db_error for arg in captured_args)


@pytest.mark.asyncio
async def test_skill_runner_capture_receives_exact_exception():
    """skill_runner_workflow passes the exact exception to capture_exception."""
    ctx = _skill_runner_context()
    db_error = OSError("Filesystem unavailable")

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=_make_mock_skill_result())
    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 1
    skill_def = _make_skill_definition(requires_approval=False)
    mock_registry.get.return_value = skill_def

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}

    mock_session = AsyncMock()
    mock_get_session = MagicMock()
    mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_get_accounts = AsyncMock(side_effect=db_error)

    with (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch("src.skills.executor.SkillExecutor", return_value=mock_executor),
        patch("src.agent.core.SideraAgent"),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
        patch("src.db.session.get_db_session", mock_get_session),
        patch("src.db.service.get_accounts_for_user", mock_get_accounts),
        patch("src.middleware.sentry_setup.capture_exception") as mock_capture,
    ):
        await skill_runner_workflow._handler(ctx)

    captured_args = [c.args[0] for c in mock_capture.call_args_list]
    assert any(arg is db_error for arg in captured_args)


# =====================================================================
# 10. Workflows return fallback values after capture
# =====================================================================


@pytest.mark.asyncio
async def test_briefing_load_accounts_returns_fallback_after_capture():
    """After capture_exception in load_accounts, workflow uses event data fallback."""
    ctx = _briefing_context()

    mock_agent = MagicMock()
    mock_result = _make_mock_briefing_result()
    mock_result.recommendations = []
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=mock_result)
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}

    mock_session = AsyncMock()
    mock_get_session = MagicMock()
    mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_get_accounts = AsyncMock(side_effect=RuntimeError("DB down"))

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
        patch("src.db.session.get_db_session", mock_get_session),
        patch("src.db.service.get_accounts_for_user", mock_get_accounts),
        patch("src.middleware.sentry_setup.capture_exception"),
    ):
        result = await daily_briefing_workflow._handler(ctx)

    # The workflow completed successfully with event data fallback
    assert result["briefing_sent"] is True
    assert result["user_id"] == "user-42"


@pytest.mark.asyncio
async def test_briefing_save_to_db_returns_fallback_after_capture():
    """After capture_exception in save_to_db, the step returns saved=False."""
    ctx = _briefing_context()

    # Track save-to-db step results
    save_results = []
    original_run = ctx.step.run.side_effect

    async def tracking_run(step_id, handler, *args):
        result = await original_run(step_id, handler, *args)
        if step_id == "save-to-db":
            save_results.append(result)
        return result

    ctx.step.run = AsyncMock(side_effect=tracking_run)

    mock_agent = MagicMock()
    mock_agent.run_daily_briefing_optimized = AsyncMock(return_value=_make_mock_briefing_result())
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}
    mock_slack.send_approval_request.return_value = {"ok": True}

    mock_session = AsyncMock()
    mock_get_session = MagicMock()
    mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

    save_error = RuntimeError("Insert failed")
    mock_save = AsyncMock(side_effect=save_error)

    with (
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
        patch("src.db.session.get_db_session", mock_get_session),
        patch("src.db.service.save_analysis_result", mock_save),
        patch("src.middleware.sentry_setup.capture_exception"),
    ):
        result = await daily_briefing_workflow._handler(ctx)

    # save-to-db should have returned the error fallback
    assert len(save_results) == 1
    assert save_results[0]["saved"] is False
    assert save_results[0]["analysis_id"] is None
    assert "Insert failed" in save_results[0]["error"]
    # Workflow still completed
    assert result["briefing_sent"] is True


@pytest.mark.asyncio
async def test_skill_runner_save_to_db_returns_fallback_after_capture():
    """After capture_exception in skill_runner save_to_db, returns saved=False."""
    ctx = _skill_runner_context()

    save_results = []
    original_run = ctx.step.run.side_effect

    async def tracking_run(step_id, handler, *args):
        result = await original_run(step_id, handler, *args)
        if step_id == "save-to-db":
            save_results.append(result)
        return result

    ctx.step.run = AsyncMock(side_effect=tracking_run)

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=_make_mock_skill_result())
    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 1
    skill_def = _make_skill_definition(requires_approval=False)
    mock_registry.get.return_value = skill_def

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}

    mock_session = AsyncMock()
    mock_get_session = MagicMock()
    mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

    save_error = RuntimeError("Network partition")
    mock_save = AsyncMock(side_effect=save_error)

    with (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch("src.skills.executor.SkillExecutor", return_value=mock_executor),
        patch("src.agent.core.SideraAgent"),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
        patch("src.db.session.get_db_session", mock_get_session),
        patch("src.db.service.save_skill_result", mock_save),
        patch("src.middleware.sentry_setup.capture_exception"),
    ):
        result = await skill_runner_workflow._handler(ctx)

    assert len(save_results) == 1
    assert save_results[0]["saved"] is False
    assert save_results[0]["analysis_id"] is None
    assert "Network partition" in save_results[0]["error"]
    # Workflow still completed successfully
    assert result["output_sent"] is True


@pytest.mark.asyncio
async def test_scheduler_returns_zero_dispatched_after_capture():
    """After capture_exception in scheduler, dispatched count is 0."""
    ctx = _make_mock_context(event_data={"user_id": "user-42", "channel_id": "C01"})

    skill_a = MagicMock()
    skill_a.id = "report_a"
    skill_a.schedule = "* * * * *"

    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 1
    mock_registry.list_scheduled.return_value = [skill_a]
    mock_registry.list_roles.return_value = []
    mock_registry.role_count = 0

    mock_inngest_send = AsyncMock(side_effect=RuntimeError("Send failed"))

    with (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch(
            "src.workflows.daily_briefing.inngest_client.send",
            mock_inngest_send,
        ),
        patch(
            "src.workflows.daily_briefing._cron_matches_now",
            return_value=True,
        ),
        patch("src.middleware.sentry_setup.capture_exception"),
    ):
        result = await skill_scheduler_workflow._handler(ctx)

    assert result["dispatched"] == 0
    assert result["scheduled_skills"] == 1
