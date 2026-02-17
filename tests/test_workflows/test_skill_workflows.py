"""Tests for skill_runner_workflow and skill_scheduler_workflow.

Covers the two skill-related Inngest workflows in
``src/workflows/daily_briefing.py`` plus the ``_cron_matches_now`` helper.
All external dependencies (SkillExecutor, SkillRegistry, Slack, DB) are
mocked. Inngest Context is simulated with a helper that wires ``step.run``
to actually call the handler so we can verify end-to-end logic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import inngest
import pytest

from src.workflows.daily_briefing import (
    _MAX_CHAIN_DEPTH,
    _cron_matches_now,
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


def _make_skill_definition(
    *,
    skill_id: str = "test_skill",
    requires_approval: bool = True,
    chain_after: str | None = None,
    schedule: str | None = None,
) -> MagicMock:
    """Build a mock SkillDefinition."""
    skill = MagicMock()
    skill.id = skill_id
    skill.name = f"Test Skill ({skill_id})"
    skill.requires_approval = requires_approval
    skill.chain_after = chain_after
    skill.schedule = schedule
    return skill


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


def _mock_step_run_count(ctx: MagicMock) -> int:
    """Return how many times ctx.step.run was awaited."""
    return ctx.step.run.await_count


# =====================================================================
# _cron_matches_now tests
# =====================================================================


class TestCronMatchesNow:
    """Tests for the _cron_matches_now helper function."""

    def test_matching_cron_expression(self):
        """Exact match: '0 9 * * MON' at Monday 09:00 UTC."""
        # Monday 2025-01-06 09:00 UTC (weekday()=0 means Monday)
        now = datetime(2025, 1, 6, 9, 0, tzinfo=timezone.utc)
        assert _cron_matches_now("0 9 * * MON", now) is True

    def test_non_matching_cron_wrong_hour(self):
        """Fails when the hour does not match."""
        now = datetime(2025, 1, 6, 10, 0, tzinfo=timezone.utc)
        assert _cron_matches_now("0 9 * * MON", now) is False

    def test_non_matching_cron_wrong_day(self):
        """Fails when the weekday does not match."""
        # Tuesday
        now = datetime(2025, 1, 7, 9, 0, tzinfo=timezone.utc)
        assert _cron_matches_now("0 9 * * MON", now) is False

    def test_wildcard_matches_anything(self):
        """'* * * * *' matches any datetime."""
        now = datetime(2025, 3, 15, 14, 37, tzinfo=timezone.utc)
        assert _cron_matches_now("* * * * *", now) is True

    def test_step_expression_supported(self):
        """Step expressions like '*/5' are supported and match correctly."""
        # minute 5 matches */5 (0, 5, 10, 15, ...)
        now = datetime(2025, 1, 6, 9, 5, tzinfo=timezone.utc)
        assert _cron_matches_now("*/5 * * * *", now) is True

    def test_step_expression_non_matching(self):
        """Step expressions like '*/5' return False when minute doesn't align."""
        # minute 3 does not match */5
        now = datetime(2025, 1, 6, 9, 3, tzinfo=timezone.utc)
        assert _cron_matches_now("*/5 * * * *", now) is False

    def test_step_expression_every_15_minutes(self):
        """Step expression '*/15' matches every 15 minutes."""
        now_0 = datetime(2025, 1, 6, 9, 0, tzinfo=timezone.utc)
        now_15 = datetime(2025, 1, 6, 9, 15, tzinfo=timezone.utc)
        now_30 = datetime(2025, 1, 6, 9, 30, tzinfo=timezone.utc)
        now_7 = datetime(2025, 1, 6, 9, 7, tzinfo=timezone.utc)
        assert _cron_matches_now("*/15 * * * *", now_0) is True
        assert _cron_matches_now("*/15 * * * *", now_15) is True
        assert _cron_matches_now("*/15 * * * *", now_30) is True
        assert _cron_matches_now("*/15 * * * *", now_7) is False

    def test_invalid_cron_expression_returns_false(self):
        """Malformed cron (wrong field count) returns False."""
        now = datetime(2025, 1, 6, 9, 0, tzinfo=timezone.utc)
        assert _cron_matches_now("0 9 *", now) is False

    def test_none_cron_expression_returns_false(self):
        """None cron expression returns False."""
        now = datetime(2025, 1, 6, 9, 0, tzinfo=timezone.utc)
        assert _cron_matches_now(None, now) is False

    def test_empty_string_returns_false(self):
        """Empty string cron expression returns False."""
        now = datetime(2025, 1, 6, 9, 0, tzinfo=timezone.utc)
        assert _cron_matches_now("", now) is False

    def test_specific_minute_and_hour(self):
        """Exact minute and hour match with wildcard day/month/weekday."""
        now = datetime(2025, 6, 15, 14, 30, tzinfo=timezone.utc)
        assert _cron_matches_now("30 14 * * *", now) is True
        assert _cron_matches_now("31 14 * * *", now) is False


# =====================================================================
# Fixtures for skill runner
# =====================================================================


@pytest.fixture()
def skill_runner_context():
    """Context with valid skill_id, accounts, and channel."""
    return _make_mock_context(
        event_data={
            "skill_id": "test_skill",
            "user_id": "user-42",
            "channel_id": "C0123SLACK",
            "accounts": SAMPLE_ACCOUNTS,
            "params": {"lookback_days": 7},
            "chain_depth": 0,
        }
    )


# =====================================================================
# skill_runner_workflow — success path
# =====================================================================


@pytest.mark.asyncio
async def test_skill_runner_full_success(skill_runner_context):
    """Full happy-path: load accounts, execute skill, save, notify, return."""
    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=_make_mock_skill_result())
    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 5
    skill_def = _make_skill_definition(requires_approval=False)
    mock_registry.get.return_value = skill_def

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True, "ts": "111.222"}

    with (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch("src.skills.executor.SkillExecutor", return_value=mock_executor),
        patch("src.agent.core.SideraAgent"),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        result = await skill_runner_workflow._handler(skill_runner_context)

    assert result["skill_id"] == "test_skill"
    assert result["user_id"] == "user-42"
    assert result["output_sent"] is True
    assert result["chained"] is False


# =====================================================================
# skill_runner_workflow — missing skill_id
# =====================================================================


@pytest.mark.asyncio
async def test_skill_runner_missing_skill_id():
    """Raises NonRetriableError when skill_id is empty."""
    ctx = _make_mock_context(event_data={"user_id": "user-42", "accounts": SAMPLE_ACCOUNTS})
    with pytest.raises(inngest.NonRetriableError, match="skill_id is required"):
        await skill_runner_workflow._handler(ctx)


# =====================================================================
# skill_runner_workflow — no accounts
# =====================================================================


@pytest.mark.asyncio
async def test_skill_runner_no_accounts():
    """Raises NonRetriableError when no accounts configured."""
    ctx = _make_mock_context(
        event_data={"skill_id": "test_skill", "user_id": "user-42", "accounts": []}
    )
    with pytest.raises(inngest.NonRetriableError, match="No accounts configured"):
        await skill_runner_workflow._handler(ctx)


# =====================================================================
# skill_runner_workflow — skill execution error
# =====================================================================


@pytest.mark.asyncio
async def test_skill_runner_execution_error(skill_runner_context):
    """Propagates exceptions from SkillExecutor.execute."""
    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(side_effect=RuntimeError("Agent crashed"))
    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 3

    with (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch("src.skills.executor.SkillExecutor", return_value=mock_executor),
        patch("src.agent.core.SideraAgent"),
    ):
        with pytest.raises(RuntimeError, match="Agent crashed"):
            await skill_runner_workflow._handler(skill_runner_context)


# =====================================================================
# skill_runner_workflow — DB save step
# =====================================================================


@pytest.mark.asyncio
async def test_skill_runner_db_save_step(skill_runner_context):
    """The save-to-db step is invoked after execution."""
    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=_make_mock_skill_result())
    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 2
    skill_def = _make_skill_definition(requires_approval=False)
    mock_registry.get.return_value = skill_def

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}

    with (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch("src.skills.executor.SkillExecutor", return_value=mock_executor),
        patch("src.agent.core.SideraAgent"),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        await skill_runner_workflow._handler(skill_runner_context)

    step_ids = [call.args[0] for call in skill_runner_context.step.run.call_args_list]
    assert "save-to-db" in step_ids


# =====================================================================
# skill_runner_workflow — Slack notification step
# =====================================================================


@pytest.mark.asyncio
async def test_skill_runner_slack_notification(skill_runner_context):
    """The send-output step is invoked and uses SlackConnector.send_briefing."""
    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=_make_mock_skill_result())
    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 1
    skill_def = _make_skill_definition(requires_approval=False)
    mock_registry.get.return_value = skill_def

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True, "ts": "123.456"}

    with (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch("src.skills.executor.SkillExecutor", return_value=mock_executor),
        patch("src.agent.core.SideraAgent"),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        result = await skill_runner_workflow._handler(skill_runner_context)

    assert result["output_sent"] is True
    step_ids = [call.args[0] for call in skill_runner_context.step.run.call_args_list]
    assert "send-output" in step_ids


# =====================================================================
# skill_runner_workflow — approval flow (requires_approval=True, approved)
# =====================================================================


@pytest.mark.asyncio
async def test_skill_runner_approval_approved(skill_runner_context):
    """When requires_approval=True and approvals are granted, decisions captured."""
    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=_make_mock_skill_result())
    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 1
    skill_def = _make_skill_definition(requires_approval=True)
    mock_registry.get.return_value = skill_def

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}
    mock_slack.send_approval_request.return_value = {"ok": True}

    # Simulate all approvals being granted
    approved_event = MagicMock()
    approved_event.data = MagicMock()
    approved_event.data.get = lambda key, default="": {
        "status": "approved",
        "decided_by": "user-42",
    }.get(key, default)
    skill_runner_context.step.wait_for_event = AsyncMock(return_value=approved_event)

    with (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch("src.skills.executor.SkillExecutor", return_value=mock_executor),
        patch("src.agent.core.SideraAgent"),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        result = await skill_runner_workflow._handler(skill_runner_context)

    assert result["approvals_sent"] == 2
    for decision in result["decisions"].values():
        assert decision["status"] == "approved"
        assert decision["decided_by"] == "user-42"


# =====================================================================
# skill_runner_workflow — approval flow (requires_approval=True, rejected)
# =====================================================================


@pytest.mark.asyncio
async def test_skill_runner_approval_rejected(skill_runner_context):
    """When requires_approval=True and approvals are rejected, no chaining."""
    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(
        return_value=_make_mock_skill_result(chain_next="follow_up_skill")
    )
    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 1
    skill_def = _make_skill_definition(requires_approval=True)
    mock_registry.get.return_value = skill_def

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}
    mock_slack.send_approval_request.return_value = {"ok": True}

    rejected_event = MagicMock()
    rejected_event.data = MagicMock()
    rejected_event.data.get = lambda key, default="": {
        "status": "rejected",
        "decided_by": "admin-1",
    }.get(key, default)
    skill_runner_context.step.wait_for_event = AsyncMock(return_value=rejected_event)

    with (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch("src.skills.executor.SkillExecutor", return_value=mock_executor),
        patch("src.agent.core.SideraAgent"),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        result = await skill_runner_workflow._handler(skill_runner_context)

    # All rejected, so no chaining
    for decision in result["decisions"].values():
        assert decision["status"] == "rejected"
    assert result["chained"] is False
    assert result["chain_next"] is None


# =====================================================================
# skill_runner_workflow — approval flow (requires_approval=False)
# =====================================================================


@pytest.mark.asyncio
async def test_skill_runner_no_approval_required(skill_runner_context):
    """When requires_approval=False, approval step is skipped entirely."""
    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=_make_mock_skill_result())
    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 1
    skill_def = _make_skill_definition(requires_approval=False)
    mock_registry.get.return_value = skill_def

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}

    with (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch("src.skills.executor.SkillExecutor", return_value=mock_executor),
        patch("src.agent.core.SideraAgent"),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        result = await skill_runner_workflow._handler(skill_runner_context)

    assert result["approvals_sent"] == 0
    assert result["decisions"] == {}
    mock_slack.send_approval_request.assert_not_called()
    skill_runner_context.step.wait_for_event.assert_not_awaited()


# =====================================================================
# skill_runner_workflow — chain triggering after approval
# =====================================================================


@pytest.mark.asyncio
async def test_skill_runner_chain_after_approval(skill_runner_context):
    """When chain_next is set and all approvals pass, triggers next skill."""
    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(
        return_value=_make_mock_skill_result(chain_next="follow_up_skill")
    )
    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 1
    skill_def = _make_skill_definition(requires_approval=True)
    mock_registry.get.return_value = skill_def

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}
    mock_slack.send_approval_request.return_value = {"ok": True}

    approved_event = MagicMock()
    approved_event.data = MagicMock()
    approved_event.data.get = lambda key, default="": {
        "status": "approved",
        "decided_by": "user-42",
    }.get(key, default)
    skill_runner_context.step.wait_for_event = AsyncMock(return_value=approved_event)

    mock_inngest_send = AsyncMock()

    with (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch("src.skills.executor.SkillExecutor", return_value=mock_executor),
        patch("src.agent.core.SideraAgent"),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
        patch(
            "src.workflows.daily_briefing.inngest_client.send",
            mock_inngest_send,
        ),
    ):
        result = await skill_runner_workflow._handler(skill_runner_context)

    assert result["chained"] is True
    assert result["chain_next"] == "follow_up_skill"

    # Verify the chained event was sent
    mock_inngest_send.assert_awaited_once()
    sent_event = mock_inngest_send.call_args.args[0]
    assert sent_event.name == "sidera/skill.run"
    assert sent_event.data["skill_id"] == "follow_up_skill"
    assert sent_event.data["chain_depth"] == 1


# =====================================================================
# skill_runner_workflow — chain without approval (requires_approval=False)
# =====================================================================


@pytest.mark.asyncio
async def test_skill_runner_chain_no_approval_needed(skill_runner_context):
    """When chain_next is set and no approvals required, chains immediately."""
    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=_make_mock_skill_result(chain_next="next_skill"))
    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 1
    skill_def = _make_skill_definition(requires_approval=False)
    mock_registry.get.return_value = skill_def

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}

    mock_inngest_send = AsyncMock()

    with (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch("src.skills.executor.SkillExecutor", return_value=mock_executor),
        patch("src.agent.core.SideraAgent"),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
        patch(
            "src.workflows.daily_briefing.inngest_client.send",
            mock_inngest_send,
        ),
    ):
        result = await skill_runner_workflow._handler(skill_runner_context)

    # No approvals => decisions is empty => all_approved is True => chain fires
    assert result["chained"] is True
    assert result["chain_next"] == "next_skill"
    mock_inngest_send.assert_awaited_once()


# =====================================================================
# skill_runner_workflow — chain depth limit exceeded
# =====================================================================


@pytest.mark.asyncio
async def test_skill_runner_chain_depth_limit():
    """Raises NonRetriableError when chain depth reaches _MAX_CHAIN_DEPTH."""
    ctx = _make_mock_context(
        event_data={
            "skill_id": "deep_skill",
            "user_id": "user-42",
            "accounts": SAMPLE_ACCOUNTS,
            "chain_depth": _MAX_CHAIN_DEPTH,
        }
    )
    with pytest.raises(inngest.NonRetriableError, match="chain depth limit"):
        await skill_runner_workflow._handler(ctx)


@pytest.mark.asyncio
async def test_skill_runner_chain_depth_just_under_limit():
    """chain_depth one below _MAX_CHAIN_DEPTH should proceed normally."""
    ctx = _make_mock_context(
        event_data={
            "skill_id": "deep_skill",
            "user_id": "user-42",
            "accounts": SAMPLE_ACCOUNTS,
            "chain_depth": _MAX_CHAIN_DEPTH - 1,
        }
    )

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=_make_mock_skill_result(skill_id="deep_skill"))
    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 1
    skill_def = _make_skill_definition(skill_id="deep_skill", requires_approval=False)
    mock_registry.get.return_value = skill_def

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}

    with (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch("src.skills.executor.SkillExecutor", return_value=mock_executor),
        patch("src.agent.core.SideraAgent"),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        result = await skill_runner_workflow._handler(ctx)

    assert result["skill_id"] == "deep_skill"


# =====================================================================
# skill_runner_workflow — step IDs deterministic
# =====================================================================


@pytest.mark.asyncio
async def test_skill_runner_step_ids(skill_runner_context):
    """Step IDs must be fixed strings (required by Inngest memoization)."""
    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=_make_mock_skill_result())
    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 1
    skill_def = _make_skill_definition(requires_approval=True)
    mock_registry.get.return_value = skill_def

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}
    mock_slack.send_approval_request.return_value = {"ok": True}

    with (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch("src.skills.executor.SkillExecutor", return_value=mock_executor),
        patch("src.agent.core.SideraAgent"),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        await skill_runner_workflow._handler(skill_runner_context)

    step_ids = [call.args[0] for call in skill_runner_context.step.run.call_args_list]
    assert "load-accounts" in step_ids
    assert "execute-skill" in step_ids
    assert "save-to-db" in step_ids
    assert "send-output" in step_ids
    assert "send-approval-0" in step_ids
    assert "send-approval-1" in step_ids


# =====================================================================
# skill_runner_workflow — default values
# =====================================================================


@pytest.mark.asyncio
async def test_skill_runner_default_user_id():
    """When user_id is missing, defaults to 'default'."""
    ctx = _make_mock_context(
        event_data={
            "skill_id": "test_skill",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    mock_executor = MagicMock()
    skill_result = _make_mock_skill_result()
    skill_result.recommendations = []
    mock_executor.execute = AsyncMock(return_value=skill_result)
    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 1
    skill_def = _make_skill_definition(requires_approval=False)
    mock_registry.get.return_value = skill_def

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}

    with (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch("src.skills.executor.SkillExecutor", return_value=mock_executor),
        patch("src.agent.core.SideraAgent"),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        result = await skill_runner_workflow._handler(ctx)

    assert result["user_id"] == "default"


# =====================================================================
# skill_runner_workflow — wait_for_event parameters
# =====================================================================


@pytest.mark.asyncio
async def test_skill_runner_wait_for_event_params(skill_runner_context):
    """Verify wait_for_event called with correct event name and timeout."""
    mock_executor = MagicMock()
    skill_result = _make_mock_skill_result()
    skill_result.recommendations = [SAMPLE_RECOMMENDATIONS[0]]
    mock_executor.execute = AsyncMock(return_value=skill_result)
    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 1
    skill_def = _make_skill_definition(requires_approval=True)
    mock_registry.get.return_value = skill_def

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}
    mock_slack.send_approval_request.return_value = {"ok": True}

    with (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch("src.skills.executor.SkillExecutor", return_value=mock_executor),
        patch("src.agent.core.SideraAgent"),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        await skill_runner_workflow._handler(skill_runner_context)

    wait_call = skill_runner_context.step.wait_for_event.call_args_list[0]
    assert "wait-approval-" in wait_call.args[0]
    assert wait_call.kwargs["event"] == "sidera/approval.decided"
    assert wait_call.kwargs["timeout"] == 86_400_000


# =====================================================================
# skill_scheduler_workflow — dispatches matching skills
# =====================================================================


@pytest.mark.asyncio
async def test_skill_scheduler_dispatches_matching():
    """Dispatches events for skills whose cron matches the current time."""
    ctx = _make_mock_context(event_data={"user_id": "user-42", "channel_id": "C01"})

    skill_a = _make_skill_definition(skill_id="morning_report", schedule="0 9 * * *")
    skill_b = _make_skill_definition(skill_id="evening_report", schedule="0 18 * * *")

    mock_registry = MagicMock()
    mock_registry.__len__ = MagicMock(return_value=2)
    mock_registry.list_scheduled.return_value = [skill_a, skill_b]
    mock_registry.list_roles.return_value = []
    mock_registry.role_count = 0

    mock_inngest_send = AsyncMock()

    # Mock _cron_matches_now so only morning_report matches
    def selective_cron_match(cron_expr, now):
        return cron_expr == "0 9 * * *"

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
            side_effect=selective_cron_match,
        ),
    ):
        result = await skill_scheduler_workflow._handler(ctx)

    assert result["loaded"] == 2
    assert result["scheduled_skills"] == 2
    assert result["dispatched"] == 1
    # Verify the correct skill was dispatched
    sent_event = mock_inngest_send.call_args.args[0]
    assert sent_event.data["skill_id"] == "morning_report"


# =====================================================================
# skill_scheduler_workflow — skips when cron doesn't match
# =====================================================================


@pytest.mark.asyncio
async def test_skill_scheduler_skips_non_matching():
    """No events dispatched when no cron expressions match."""
    ctx = _make_mock_context(event_data={"user_id": "user-42"})

    skill_a = _make_skill_definition(skill_id="late_night", schedule="0 3 * * *")

    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 1
    mock_registry.list_scheduled.return_value = [skill_a]
    mock_registry.list_roles.return_value = []
    mock_registry.role_count = 0

    mock_inngest_send = AsyncMock()

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
            return_value=False,
        ),
    ):
        result = await skill_scheduler_workflow._handler(ctx)

    assert result["dispatched"] == 0
    mock_inngest_send.assert_not_awaited()


# =====================================================================
# skill_scheduler_workflow — empty registry
# =====================================================================


@pytest.mark.asyncio
async def test_skill_scheduler_empty_registry():
    """Returns early with zero dispatched when no scheduled skills exist."""
    ctx = _make_mock_context(event_data={})

    mock_registry = MagicMock()
    mock_registry.__len__ = MagicMock(return_value=0)
    mock_registry.list_scheduled.return_value = []
    mock_registry.list_roles.return_value = []
    mock_registry.role_count = 0

    with patch(
        "src.skills.db_loader.load_registry_with_db",
        new_callable=AsyncMock,
        return_value=mock_registry,
    ):
        result = await skill_scheduler_workflow._handler(ctx)

    assert result["loaded"] == 0
    assert result["scheduled_skills"] == 0
    assert result["dispatched"] == 0


# =====================================================================
# skill_scheduler_workflow — registry load failure
# =====================================================================


@pytest.mark.asyncio
async def test_skill_scheduler_registry_load_failure():
    """Handles exceptions from registry load gracefully."""
    ctx = _make_mock_context(event_data={})

    with (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Filesystem error"),
        ),
        pytest.raises(RuntimeError, match="Filesystem error"),
    ):
        await skill_scheduler_workflow._handler(ctx)


# =====================================================================
# skill_scheduler_workflow — multiple skills at same time
# =====================================================================


@pytest.mark.asyncio
async def test_skill_scheduler_multiple_dispatched():
    """Dispatches events for all skills matching the current time."""
    ctx = _make_mock_context(event_data={"user_id": "user-42", "channel_id": "C01"})

    skill_a = _make_skill_definition(skill_id="alpha_report", schedule="* * * * *")
    skill_b = _make_skill_definition(skill_id="beta_report", schedule="* * * * *")
    skill_c = _make_skill_definition(skill_id="gamma_report", schedule="0 3 * * *")

    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 3
    mock_registry.list_scheduled.return_value = [skill_a, skill_b, skill_c]
    mock_registry.list_roles.return_value = []
    mock_registry.role_count = 0

    mock_inngest_send = AsyncMock()

    # Mock _cron_matches_now so wildcards match but "0 3" does not
    def selective_cron_match(cron_expr, now):
        return cron_expr == "* * * * *"

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
            side_effect=selective_cron_match,
        ),
    ):
        result = await skill_scheduler_workflow._handler(ctx)

    assert result["scheduled_skills"] == 3
    assert result["dispatched"] == 2
    assert mock_inngest_send.await_count == 2

    # Verify both dispatched skill IDs
    dispatched_ids = [call.args[0].data["skill_id"] for call in mock_inngest_send.call_args_list]
    assert "alpha_report" in dispatched_ids
    assert "beta_report" in dispatched_ids
    assert "gamma_report" not in dispatched_ids


# =====================================================================
# Configuration tests
# =====================================================================


def test_skill_runner_function_id():
    """Skill runner has the expected function ID."""
    assert skill_runner_workflow.id == "sidera-sidera-skill-runner"


def test_skill_scheduler_function_id():
    """Skill scheduler has the expected function ID."""
    assert skill_scheduler_workflow.id == "sidera-sidera-skill-scheduler"


def test_skill_runner_event_trigger():
    """Skill runner triggers on sidera/skill.run event."""
    config = skill_runner_workflow.get_config("sidera")
    triggers = config.main.triggers
    assert len(triggers) == 1
    trigger = triggers[0]
    assert trigger.event == "sidera/skill.run"


def test_skill_scheduler_cron_trigger():
    """Skill scheduler runs every minute."""
    config = skill_scheduler_workflow.get_config("sidera")
    triggers = config.main.triggers
    assert len(triggers) == 1
    trigger = triggers[0]
    assert trigger.cron == "* * * * *"


def test_max_chain_depth_constant():
    """_MAX_CHAIN_DEPTH is set to 5."""
    assert _MAX_CHAIN_DEPTH == 5
