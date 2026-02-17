"""Tests for the Claude Code task workflow (Inngest).

Tests the ``claude_code_task_workflow`` — load-context, execute-claude-code,
save-results, notify-slack steps.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.workflows.daily_briefing import claude_code_task_workflow
from tests.test_workflows.conftest import _make_mock_context

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill(skill_id="test_skill"):
    skill = MagicMock()
    skill.id = skill_id
    skill.name = "Test Skill"
    skill.model = "sonnet"
    skill.department_id = "test_dept"
    return skill


def _make_registry(skill=None, role=None, dept=None):
    registry = MagicMock()
    registry.get_skill.return_value = skill
    registry.get_role.return_value = role
    registry.get_department.return_value = dept
    return registry


def _mock_db_session():
    mock_session = AsyncMock()
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm, mock_session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClaudeCodeTaskWorkflow:
    """Tests for claude_code_task_workflow."""

    @pytest.mark.asyncio
    async def test_missing_skill_id_raises(self):
        """Missing skill_id should raise NonRetriableError."""
        import inngest

        ctx = _make_mock_context(event_data={})
        with pytest.raises(inngest.NonRetriableError, match="skill_id"):
            await claude_code_task_workflow._handler(ctx)

    @pytest.mark.asyncio
    async def test_claude_code_disabled_raises(self):
        """When kill switch is off, should raise NonRetriableError."""
        import inngest

        ctx = _make_mock_context(event_data={"skill_id": "test_skill"})

        with patch("src.config.settings") as mock_settings:
            mock_settings.claude_code_enabled = False
            with pytest.raises(inngest.NonRetriableError, match="disabled"):
                await claude_code_task_workflow._handler(ctx)

    @pytest.mark.asyncio
    async def test_skill_not_found_raises(self):
        """Unknown skill_id should raise NonRetriableError."""
        import inngest

        ctx = _make_mock_context(event_data={"skill_id": "nonexistent"})
        registry = _make_registry(skill=None)

        with (
            patch("src.config.settings") as mock_settings,
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=registry,
            ),
        ):
            mock_settings.claude_code_enabled = True
            mock_settings.claude_code_default_budget_usd = 5.0
            mock_settings.claude_code_max_budget_usd = 25.0
            with pytest.raises(inngest.NonRetriableError, match="not found"):
                await claude_code_task_workflow._handler(ctx)

    @pytest.mark.asyncio
    async def test_happy_path(self):
        """Full workflow should load context, execute, save results."""
        from src.claude_code.executor import ClaudeCodeResult

        skill = _make_skill()
        registry = _make_registry(skill=skill)
        mock_cm, mock_session = _mock_db_session()

        fake_result = ClaudeCodeResult(
            skill_id="test_skill",
            user_id="claude_code",
            output_text="Analysis complete.",
            cost_usd=0.08,
            num_turns=4,
            duration_ms=6000,
            session_id="sess_123",
        )

        ctx = _make_mock_context(event_data={"skill_id": "test_skill"})

        with (
            patch("src.config.settings") as mock_settings,
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=registry,
            ),
            patch("src.claude_code.task_manager.ClaudeCodeTaskManager") as mock_manager_cls,
            patch(
                "src.db.session.get_db_session",
                return_value=mock_cm,
            ),
            patch(
                "src.db.service.save_skill_result",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.log_event",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.create_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.update_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.record_cost",
                new_callable=AsyncMock,
            ),
        ):
            mock_settings.claude_code_enabled = True
            mock_settings.claude_code_default_budget_usd = 5.0
            mock_settings.claude_code_max_budget_usd = 25.0
            mock_settings.claude_code_max_concurrent = 20
            mock_settings.claude_code_default_permission_mode = "acceptEdits"

            mock_mgr = MagicMock()
            mock_mgr.run_task_sync = AsyncMock(return_value=fake_result)
            mock_manager_cls.return_value = mock_mgr

            result = await claude_code_task_workflow._handler(ctx)

        assert result["status"] == "completed"
        assert result["skill_id"] == "test_skill"
        assert result["cost_usd"] == 0.08
        assert result["is_error"] is False

    @pytest.mark.asyncio
    async def test_budget_capped_at_max(self):
        """Requested budget should be capped at claude_code_max_budget_usd."""
        from src.claude_code.executor import ClaudeCodeResult

        skill = _make_skill()
        registry = _make_registry(skill=skill)
        mock_cm, mock_session = _mock_db_session()

        captured_budget: list[float] = []

        async def capture_run_task_sync(skill, prompt, user_id, **kwargs):
            captured_budget.append(kwargs.get("max_budget_usd"))
            return ClaudeCodeResult(
                skill_id="test_skill",
                user_id="claude_code",
                output_text="Done.",
                cost_usd=0.05,
            )

        ctx = _make_mock_context(
            event_data={
                "skill_id": "test_skill",
                "max_budget_usd": 100.0,  # Over the max
            }
        )

        with (
            patch("src.config.settings") as mock_settings,
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=registry,
            ),
            patch("src.claude_code.task_manager.ClaudeCodeTaskManager") as mock_manager_cls,
            patch(
                "src.db.session.get_db_session",
                return_value=mock_cm,
            ),
            patch(
                "src.db.service.save_skill_result",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.log_event",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.create_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.update_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.record_cost",
                new_callable=AsyncMock,
            ),
        ):
            mock_settings.claude_code_enabled = True
            mock_settings.claude_code_default_budget_usd = 5.0
            mock_settings.claude_code_max_budget_usd = 25.0
            mock_settings.claude_code_max_concurrent = 20
            mock_settings.claude_code_default_permission_mode = "acceptEdits"

            mock_mgr = MagicMock()
            mock_mgr.run_task_sync = AsyncMock(side_effect=capture_run_task_sync)
            mock_manager_cls.return_value = mock_mgr

            await claude_code_task_workflow._handler(ctx)

        # Budget should have been capped at 25.0
        assert captured_budget[0] == 25.0

    @pytest.mark.asyncio
    async def test_slack_notification_when_channel_provided(self):
        """When channel_id is provided, Slack should be notified."""
        from src.claude_code.executor import ClaudeCodeResult

        skill = _make_skill()
        registry = _make_registry(skill=skill)
        mock_cm, mock_session = _mock_db_session()

        fake_result = ClaudeCodeResult(
            skill_id="test_skill",
            user_id="claude_code",
            output_text="Task complete.",
            cost_usd=0.03,
            num_turns=2,
            duration_ms=3000,
        )

        slack_calls: list[dict] = []

        ctx = _make_mock_context(
            event_data={
                "skill_id": "test_skill",
                "channel_id": "C12345",
            }
        )

        mock_slack = MagicMock()

        async def mock_send_alert(**kwargs):
            slack_calls.append(kwargs)

        mock_slack.send_alert = mock_send_alert

        with (
            patch("src.config.settings") as mock_settings,
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=registry,
            ),
            patch("src.claude_code.task_manager.ClaudeCodeTaskManager") as mock_manager_cls,
            patch(
                "src.db.session.get_db_session",
                return_value=mock_cm,
            ),
            patch(
                "src.db.service.save_skill_result",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.log_event",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.create_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.update_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.record_cost",
                new_callable=AsyncMock,
            ),
            patch(
                "src.connectors.slack.SlackConnector",
                return_value=mock_slack,
            ),
        ):
            mock_settings.claude_code_enabled = True
            mock_settings.claude_code_default_budget_usd = 5.0
            mock_settings.claude_code_max_budget_usd = 25.0
            mock_settings.claude_code_max_concurrent = 20
            mock_settings.claude_code_default_permission_mode = "acceptEdits"

            mock_mgr = MagicMock()
            mock_mgr.run_task_sync = AsyncMock(return_value=fake_result)
            mock_manager_cls.return_value = mock_mgr

            result = await claude_code_task_workflow._handler(ctx)

        assert result["status"] == "completed"
        assert len(slack_calls) == 1
        assert slack_calls[0]["channel"] == "C12345"
        assert "Test Skill" in slack_calls[0]["text"]

    @pytest.mark.asyncio
    async def test_no_slack_without_channel(self):
        """Without channel_id, no Slack notification should be sent."""
        from src.claude_code.executor import ClaudeCodeResult

        skill = _make_skill()
        registry = _make_registry(skill=skill)
        mock_cm, mock_session = _mock_db_session()

        fake_result = ClaudeCodeResult(
            skill_id="test_skill",
            user_id="claude_code",
            output_text="Done.",
            cost_usd=0.01,
        )

        ctx = _make_mock_context(event_data={"skill_id": "test_skill"})
        # No channel_id

        with (
            patch("src.config.settings") as mock_settings,
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=registry,
            ),
            patch("src.claude_code.task_manager.ClaudeCodeTaskManager") as mock_manager_cls,
            patch(
                "src.db.session.get_db_session",
                return_value=mock_cm,
            ),
            patch(
                "src.db.service.save_skill_result",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.log_event",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.create_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.update_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.record_cost",
                new_callable=AsyncMock,
            ),
        ):
            mock_settings.claude_code_enabled = True
            mock_settings.claude_code_default_budget_usd = 5.0
            mock_settings.claude_code_max_budget_usd = 25.0
            mock_settings.claude_code_max_concurrent = 20
            mock_settings.claude_code_default_permission_mode = "acceptEdits"

            mock_mgr = MagicMock()
            mock_mgr.run_task_sync = AsyncMock(return_value=fake_result)
            mock_manager_cls.return_value = mock_mgr

            result = await claude_code_task_workflow._handler(ctx)

        assert result["status"] == "completed"
        # Step 4 (notify-slack) should not have been called
        step_ids = [call.args[0] for call in ctx.step.run.call_args_list]
        assert "notify-slack" not in step_ids

    @pytest.mark.asyncio
    async def test_dlq_on_unhandled_exception(self):
        """Unhandled exceptions should be recorded in the DLQ."""
        ctx = _make_mock_context(event_data={"skill_id": "test_skill"})
        record_mock = AsyncMock()
        mock_cm, _ = _mock_db_session()

        with (
            patch("src.config.settings") as mock_settings,
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB connection lost"),
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=mock_cm,
            ),
            patch(
                "src.db.service.record_failed_run",
                record_mock,
            ),
            patch(
                "src.middleware.sentry_setup.capture_exception",
            ),
        ):
            mock_settings.claude_code_enabled = True
            mock_settings.claude_code_default_budget_usd = 5.0
            mock_settings.claude_code_max_budget_usd = 25.0

            with pytest.raises(RuntimeError, match="DB connection lost"):
                await claude_code_task_workflow._handler(ctx)

        # DLQ should have been called
        record_mock.assert_called_once()
        call_kwargs = record_mock.call_args
        assert call_kwargs[1]["workflow_name"] == "claude_code_task"

    @pytest.mark.asyncio
    async def test_error_status_returned_on_task_error(self):
        """If the task errors, workflow should return status='failed'."""
        from src.claude_code.executor import ClaudeCodeResult

        skill = _make_skill()
        registry = _make_registry(skill=skill)
        mock_cm, _ = _mock_db_session()

        error_result = ClaudeCodeResult(
            skill_id="test_skill",
            user_id="claude_code",
            output_text="",
            is_error=True,
            error_message="Agent crashed",
            cost_usd=0.02,
            num_turns=1,
            duration_ms=1000,
        )

        ctx = _make_mock_context(event_data={"skill_id": "test_skill"})

        with (
            patch("src.config.settings") as mock_settings,
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=registry,
            ),
            patch("src.claude_code.task_manager.ClaudeCodeTaskManager") as mock_manager_cls,
            patch(
                "src.db.session.get_db_session",
                return_value=mock_cm,
            ),
            patch(
                "src.db.service.save_skill_result",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.log_event",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.create_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.update_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.record_cost",
                new_callable=AsyncMock,
            ),
        ):
            mock_settings.claude_code_enabled = True
            mock_settings.claude_code_default_budget_usd = 5.0
            mock_settings.claude_code_max_budget_usd = 25.0
            mock_settings.claude_code_max_concurrent = 20
            mock_settings.claude_code_default_permission_mode = "acceptEdits"

            mock_mgr = MagicMock()
            mock_mgr.run_task_sync = AsyncMock(return_value=error_result)
            mock_manager_cls.return_value = mock_mgr

            result = await claude_code_task_workflow._handler(ctx)

        assert result["status"] == "failed"
        assert result["is_error"] is True
