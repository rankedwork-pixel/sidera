"""Tests for _execute_claude_code_task and _execute_action routing.

Verifies that the approval flow correctly routes ``claude_code_task``
actions to the Claude Code task manager, respects the kill switch,
validates skills, and enforces budget caps.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.workflows.daily_briefing import _execute_action, _execute_claude_code_task

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill(skill_id: str = "test_skill", department_id: str = "test_dept"):
    skill = MagicMock()
    skill.id = skill_id
    skill.name = "Test Skill"
    skill.model = "sonnet"
    skill.department_id = department_id
    return skill


def _make_registry(skill=None, role=None, dept=None):
    registry = MagicMock()
    registry.get_skill.return_value = skill
    registry.get_role.return_value = role
    registry.get_department.return_value = dept
    return registry


def _base_settings_mock(mock_settings: MagicMock) -> None:
    """Apply default enabled settings to a patched settings mock."""
    mock_settings.claude_code_enabled = True
    mock_settings.claude_code_default_budget_usd = 5.0
    mock_settings.claude_code_max_budget_usd = 25.0
    mock_settings.claude_code_max_concurrent = 20
    mock_settings.claude_code_default_permission_mode = "acceptEdits"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExecuteClaudeCodeTask:
    """Tests for _execute_claude_code_task and _execute_action routing."""

    @pytest.mark.asyncio
    async def test_execute_action_routes_to_cc(self):
        """_execute_action with action_type='claude_code_task' delegates
        to _execute_claude_code_task."""
        fake_result = {"success": True, "output_text": "done"}

        with patch(
            "src.workflows.daily_briefing._execute_claude_code_task",
            new_callable=AsyncMock,
            return_value=fake_result,
        ) as mock_cc:
            params = {"skill_id": "test_skill", "prompt": "Analyze"}
            result = await _execute_action("claude_code_task", params)

        mock_cc.assert_called_once_with(params)
        assert result == fake_result

    @pytest.mark.asyncio
    async def test_execute_cc_task_disabled(self):
        """When claude_code_enabled is False, should return success=False
        with a 'disabled' error."""
        with patch("src.config.settings") as mock_settings:
            mock_settings.claude_code_enabled = False

            result = await _execute_claude_code_task({"skill_id": "test"})

        assert result["success"] is False
        assert "disabled" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_execute_cc_task_success(self):
        """Happy path: skill found, task manager returns a successful
        ClaudeCodeResult."""
        from src.claude_code.executor import ClaudeCodeResult

        skill = _make_skill()
        registry = _make_registry(skill=skill)

        fake_result = ClaudeCodeResult(
            skill_id="test_skill",
            user_id="claude_code",
            output_text="Analysis complete.",
            cost_usd=0.08,
            num_turns=4,
            duration_ms=6000,
            session_id="sess_abc",
        )

        with (
            patch("src.config.settings") as mock_settings,
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=registry,
            ),
            patch(
                "src.claude_code.task_manager.ClaudeCodeTaskManager",
            ) as mock_manager_cls,
        ):
            _base_settings_mock(mock_settings)

            mock_mgr = MagicMock()
            mock_mgr.run_task_sync = AsyncMock(return_value=fake_result)
            mock_manager_cls.return_value = mock_mgr

            result = await _execute_claude_code_task(
                {
                    "skill_id": "test_skill",
                    "prompt": "Analyze campaign performance",
                }
            )

        assert result["success"] is True
        assert result["is_error"] is False
        assert result["cost_usd"] == 0.08
        assert result["num_turns"] == 4
        assert result["duration_ms"] == 6000
        assert result["output_text"] == "Analysis complete."
        assert result["error_message"] == ""

    @pytest.mark.asyncio
    async def test_execute_cc_task_skill_not_found(self):
        """When the registry cannot find the skill, should return
        success=False with a 'not found' error."""
        registry = _make_registry(skill=None)

        with (
            patch("src.config.settings") as mock_settings,
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=registry,
            ),
        ):
            _base_settings_mock(mock_settings)

            result = await _execute_claude_code_task(
                {
                    "skill_id": "nonexistent_skill",
                }
            )

        assert result["success"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_execute_cc_task_budget_capped(self):
        """Requested budget exceeding claude_code_max_budget_usd should
        be capped at the configured maximum."""
        from src.claude_code.executor import ClaudeCodeResult

        skill = _make_skill()
        registry = _make_registry(skill=skill)

        captured_budget: list[float] = []

        async def capture_run_task_sync(skill, prompt, user_id, **kwargs):
            captured_budget.append(kwargs.get("max_budget_usd"))
            return ClaudeCodeResult(
                skill_id="test_skill",
                user_id="claude_code",
                output_text="Done.",
                cost_usd=0.05,
            )

        with (
            patch("src.config.settings") as mock_settings,
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=registry,
            ),
            patch(
                "src.claude_code.task_manager.ClaudeCodeTaskManager",
            ) as mock_manager_cls,
        ):
            _base_settings_mock(mock_settings)
            # Set a ceiling lower than what the action params request
            mock_settings.claude_code_max_budget_usd = 25.0

            mock_mgr = MagicMock()
            mock_mgr.run_task_sync = AsyncMock(side_effect=capture_run_task_sync)
            mock_manager_cls.return_value = mock_mgr

            await _execute_claude_code_task(
                {
                    "skill_id": "test_skill",
                    "prompt": "Analyze",
                    "max_budget_usd": 100.0,  # Over the 25.0 cap
                }
            )

        assert len(captured_budget) == 1
        assert captured_budget[0] == 25.0
