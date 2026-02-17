"""Tests for the MCP stdio meta-tools.

Tests the 6 meta-tool handlers: talk_to_role, run_role, list_roles,
review_pending_approvals, decide_approval, run_claude_code_task.

Note: handlers use deferred (local) imports, so patches target the
source modules rather than ``src.mcp_stdio.meta_tools.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.types import TextContent

from src.mcp_stdio.meta_tools import META_TOOL_HANDLERS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeConversationResult:
    """Minimal stand-in for ``ConversationTurnResult``."""

    response_text: str = "I'm the head of IT."
    cost: dict[str, Any] = field(default_factory=lambda: {"total_cost_usd": 0.05})
    turn_number: int = 1


@dataclass
class FakeRoleResult:
    """Stand-in for ``RoleResult``."""

    combined_output: str = "System healthy."
    total_cost: dict[str, Any] = field(default_factory=lambda: {"total_cost_usd": 0.10})
    skill_results: list = field(default_factory=list)


def _make_role(role_id="head_of_it", dept_id="it", manages=()):
    """Create a mock role definition."""
    role = MagicMock()
    role.id = role_id
    role.name = "Head of IT"
    role.department_id = dept_id
    role.persona = "IT leader"
    role.briefing_skills = ("system_health_check",)
    role.manages = manages
    role.principles = ()
    role.context_files = ()
    return role


def _make_dept(dept_id="it"):
    """Create a mock department definition."""
    dept = MagicMock()
    dept.id = dept_id
    dept.name = "IT"
    dept.context = "IT department context"
    dept.context_files = ()
    return dept


def _make_registry(role=None, dept=None):
    """Create a mock SkillRegistry."""
    r = MagicMock()
    r.get_role.return_value = role
    r.get_department.return_value = dept
    r.get_departments.return_value = [dept] if dept else []
    r.get_roles_for_department.return_value = [role] if role else []
    return r


def _mock_db_session():
    """Create a mock async context manager for get_db_session()."""
    mock_session = AsyncMock()
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm, mock_session


# ---------------------------------------------------------------------------
# talk_to_role
# ---------------------------------------------------------------------------


class TestTalkToRole:
    """Tests for the talk_to_role meta-tool."""

    @pytest.mark.asyncio
    async def test_missing_role_id_returns_error(self):
        result = await META_TOOL_HANDLERS["talk_to_role"]({"message": "hi"})
        assert len(result) == 1
        assert "required" in result[0].text.lower()

    @pytest.mark.asyncio
    async def test_missing_message_returns_error(self):
        result = await META_TOOL_HANDLERS["talk_to_role"]({"role_id": "head_of_it"})
        assert len(result) == 1
        assert "required" in result[0].text.lower()

    @pytest.mark.asyncio
    async def test_unknown_role_returns_error(self):
        mock_registry = _make_registry()
        mock_registry.get_role.return_value = None

        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ):
            result = await META_TOOL_HANDLERS["talk_to_role"](
                {"role_id": "nonexistent", "message": "hi"}
            )
        assert "not found" in result[0].text.lower()

    @pytest.mark.asyncio
    async def test_basic_conversation_turn(self):
        """Verify talk_to_role runs the agent and returns a response."""
        role = _make_role()
        dept = _make_dept()
        mock_registry = _make_registry(role, dept)
        mock_agent = MagicMock()
        mock_agent.run_conversation_turn = AsyncMock(return_value=FakeConversationResult())
        mock_cm, mock_session = _mock_db_session()

        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch("src.db.session.get_db_session", return_value=mock_cm),
            patch("src.agent.core.SideraAgent", return_value=mock_agent),
            patch("src.skills.executor.compose_role_context", return_value="ctx"),
            patch("src.skills.memory.compose_memory_context", return_value=""),
            patch("src.skills.memory.filter_superseded_memories", return_value=[]),
            patch("src.db.service.get_role_memories", new_callable=AsyncMock, return_value=[]),
            patch(
                "src.db.service.get_superseded_memory_ids",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "src.db.service.get_agent_relationship_memories",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "src.db.service.get_pending_messages",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "src.mcp_servers.messaging.compose_message_context",
                return_value="",
            ),
        ):
            result = await META_TOOL_HANDLERS["talk_to_role"](
                {"role_id": "head_of_it", "message": "check system health"}
            )

        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert "I'm the head of IT" in result[0].text

    @pytest.mark.asyncio
    async def test_pending_actions_included_in_response(self):
        """Actions proposed by the agent should appear in the response."""
        role = _make_role()
        dept = _make_dept()
        mock_registry = _make_registry(role, dept)
        mock_agent = MagicMock()
        mock_agent.run_conversation_turn = AsyncMock(return_value=FakeConversationResult())
        mock_cm, mock_session = _mock_db_session()

        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch("src.db.session.get_db_session", return_value=mock_cm),
            patch("src.agent.core.SideraAgent", return_value=mock_agent),
            patch("src.skills.executor.compose_role_context", return_value="ctx"),
            patch("src.skills.memory.compose_memory_context", return_value=""),
            patch("src.skills.memory.filter_superseded_memories", return_value=[]),
            patch("src.db.service.get_role_memories", new_callable=AsyncMock, return_value=[]),
            patch(
                "src.db.service.get_superseded_memory_ids",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "src.db.service.get_agent_relationship_memories",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "src.db.service.get_pending_messages",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("src.mcp_servers.messaging.compose_message_context", return_value=""),
            patch(
                "src.mcp_servers.actions.get_pending_actions",
                return_value=[
                    {"description": "Pause campaign X", "reasoning": "Low ROAS"},
                ],
            ),
        ):
            result = await META_TOOL_HANDLERS["talk_to_role"](
                {"role_id": "head_of_it", "message": "optimize campaigns"}
            )

        assert "Proposed Actions" in result[0].text
        assert "Pause campaign X" in result[0].text

    @pytest.mark.asyncio
    async def test_contextvars_cleared_on_error(self):
        """Contextvars should be cleared even if the agent raises."""
        role = _make_role()
        dept = _make_dept()
        mock_registry = _make_registry(role, dept)
        mock_agent = MagicMock()
        mock_agent.run_conversation_turn = AsyncMock(side_effect=RuntimeError("agent crashed"))
        mock_cm, mock_session = _mock_db_session()

        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch("src.db.session.get_db_session", return_value=mock_cm),
            patch("src.agent.core.SideraAgent", return_value=mock_agent),
            patch("src.skills.executor.compose_role_context", return_value="ctx"),
            patch("src.skills.memory.compose_memory_context", return_value=""),
            patch("src.skills.memory.filter_superseded_memories", return_value=[]),
            patch("src.db.service.get_role_memories", new_callable=AsyncMock, return_value=[]),
            patch(
                "src.db.service.get_superseded_memory_ids",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "src.db.service.get_agent_relationship_memories",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "src.db.service.get_pending_messages",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("src.mcp_servers.messaging.compose_message_context", return_value=""),
            patch("src.mcp_servers.memory.clear_memory_context") as mock_clear_mem,
            patch("src.mcp_servers.messaging.clear_messaging_context"),
        ):
            result = await META_TOOL_HANDLERS["talk_to_role"](
                {"role_id": "head_of_it", "message": "hi"}
            )

        # Should get an error response, not a crash
        assert "error" in result[0].text.lower()
        # Contextvars should have been cleared via the finally block
        mock_clear_mem.assert_called()


# ---------------------------------------------------------------------------
# run_role
# ---------------------------------------------------------------------------


class TestRunRole:
    """Tests for the run_role meta-tool."""

    @pytest.mark.asyncio
    async def test_missing_role_id_returns_error(self):
        result = await META_TOOL_HANDLERS["run_role"]({})
        assert "required" in result[0].text.lower()

    @pytest.mark.asyncio
    async def test_unknown_role_returns_error(self):
        mock_registry = _make_registry()
        mock_registry.get_role.return_value = None

        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ):
            result = await META_TOOL_HANDLERS["run_role"]({"role_id": "nonexistent"})
        assert "not found" in result[0].text.lower()

    @pytest.mark.asyncio
    async def test_basic_role_execution(self):
        """Verify run_role executes and returns combined output."""
        role = _make_role()
        dept = _make_dept()
        mock_registry = _make_registry(role, dept)
        mock_cm, mock_session = _mock_db_session()

        mock_role_executor = MagicMock()
        mock_role_executor.execute_role = AsyncMock(return_value=FakeRoleResult())

        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch("src.db.session.get_db_session", return_value=mock_cm),
            patch("src.agent.core.SideraAgent"),
            patch("src.skills.executor.SkillExecutor"),
            patch(
                "src.skills.executor.RoleExecutor",
                return_value=mock_role_executor,
            ),
            patch("src.skills.memory.compose_memory_context", return_value=""),
            patch("src.skills.memory.filter_superseded_memories", return_value=[]),
            patch("src.db.service.get_role_memories", new_callable=AsyncMock, return_value=[]),
            patch(
                "src.db.service.get_superseded_memory_ids",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "src.db.service.get_agent_relationship_memories",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "src.db.service.get_pending_messages",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("src.mcp_servers.messaging.compose_message_context", return_value=""),
        ):
            result = await META_TOOL_HANDLERS["run_role"]({"role_id": "head_of_it"})

        assert "System healthy" in result[0].text


# ---------------------------------------------------------------------------
# list_roles
# ---------------------------------------------------------------------------


class TestListRoles:
    """Tests for the list_roles meta-tool."""

    @pytest.mark.asyncio
    async def test_returns_hierarchy(self):
        role = _make_role()
        dept = _make_dept()
        mock_registry = _make_registry(role, dept)

        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ):
            result = await META_TOOL_HANDLERS["list_roles"]({})

        text = result[0].text
        assert "IT" in text
        assert "Head of IT" in text
        assert "system_health_check" in text

    @pytest.mark.asyncio
    async def test_filter_by_department(self):
        role = _make_role()
        dept = _make_dept()
        mock_registry = _make_registry(role, dept)

        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ):
            result = await META_TOOL_HANDLERS["list_roles"]({"department_id": "it"})

        assert "IT" in result[0].text

    @pytest.mark.asyncio
    async def test_unknown_department_returns_message(self):
        mock_registry = _make_registry()
        mock_registry.get_departments.return_value = []

        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ):
            result = await META_TOOL_HANDLERS["list_roles"]({"department_id": "nonexistent"})

        text = result[0].text
        assert "no department found" in text.lower() or "no departments" in text.lower()


# ---------------------------------------------------------------------------
# review_pending_approvals
# ---------------------------------------------------------------------------


class TestReviewPendingApprovals:
    """Tests for the review_pending_approvals meta-tool."""

    @pytest.mark.asyncio
    async def test_empty_queue(self):
        mock_cm, mock_session = _mock_db_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.db.session.get_db_session", return_value=mock_cm):
            result = await META_TOOL_HANDLERS["review_pending_approvals"]({})

        assert "No pending approvals" in result[0].text

    @pytest.mark.asyncio
    async def test_with_items(self):
        mock_item = MagicMock()
        mock_item.id = 42
        mock_item.action_type = "budget_change"
        mock_item.description = "Increase budget by 20%"
        mock_item.reasoning = "Good ROAS"
        mock_item.risk_assessment = "Low risk"
        mock_item.projected_impact = "+$500/day"
        mock_item.action_params = {"platform": "google_ads", "new_budget_micros": 100000000}
        mock_item.created_at = "2026-02-16T10:00:00Z"

        mock_cm, mock_session = _mock_db_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_item]
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.db.session.get_db_session", return_value=mock_cm):
            result = await META_TOOL_HANDLERS["review_pending_approvals"]({})

        text = result[0].text
        assert "42" in text
        assert "budget_change" in text
        assert "Increase budget by 20%" in text


# ---------------------------------------------------------------------------
# decide_approval
# ---------------------------------------------------------------------------


class TestDecideApproval:
    """Tests for the decide_approval meta-tool."""

    @pytest.mark.asyncio
    async def test_missing_params_returns_error(self):
        result = await META_TOOL_HANDLERS["decide_approval"]({})
        assert "required" in result[0].text.lower()

    @pytest.mark.asyncio
    async def test_invalid_decision_returns_error(self):
        result = await META_TOOL_HANDLERS["decide_approval"](
            {"approval_id": 1, "decision": "maybe"}
        )
        assert "required" in result[0].text.lower()

    @pytest.mark.asyncio
    async def test_not_found_returns_error(self):
        mock_cm, mock_session = _mock_db_session()

        with (
            patch("src.db.session.get_db_session", return_value=mock_cm),
            patch(
                "src.db.service.get_approval_by_id",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = await META_TOOL_HANDLERS["decide_approval"](
                {"approval_id": 999, "decision": "approve"}
            )

        assert "not found" in result[0].text.lower()

    @pytest.mark.asyncio
    async def test_reject_approval(self):
        from src.models.schema import ApprovalStatus

        mock_item = MagicMock()
        mock_item.id = 1
        mock_item.status = ApprovalStatus.PENDING
        mock_item.description = "Pause campaign"
        mock_item.action_type = "pause_campaign"

        mock_cm, mock_session = _mock_db_session()

        with (
            patch("src.db.session.get_db_session", return_value=mock_cm),
            patch(
                "src.db.service.get_approval_by_id",
                new_callable=AsyncMock,
                return_value=mock_item,
            ),
        ):
            result = await META_TOOL_HANDLERS["decide_approval"](
                {"approval_id": 1, "decision": "reject", "reason": "Not needed"}
            )

        assert "Rejected" in result[0].text
        assert mock_item.status == ApprovalStatus.REJECTED

    @pytest.mark.asyncio
    async def test_approve_and_execute(self):
        from src.models.schema import ApprovalStatus

        mock_item = MagicMock()
        mock_item.id = 2
        mock_item.status = ApprovalStatus.PENDING
        mock_item.description = "Enable campaign"
        mock_item.action_type = MagicMock()
        mock_item.action_type.value = "enable_campaign"
        mock_item.action_params = {"platform": "google_ads", "customer_id": "123"}

        mock_cm, mock_session = _mock_db_session()

        with (
            patch("src.db.session.get_db_session", return_value=mock_cm),
            patch(
                "src.db.service.get_approval_by_id",
                new_callable=AsyncMock,
                return_value=mock_item,
            ),
            patch(
                "src.workflows.daily_briefing._execute_action",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
        ):
            result = await META_TOOL_HANDLERS["decide_approval"](
                {"approval_id": 2, "decision": "approve"}
            )

        assert "Approved and executed" in result[0].text
        assert mock_item.status == ApprovalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_already_decided_returns_error(self):
        from src.models.schema import ApprovalStatus

        mock_item = MagicMock()
        mock_item.id = 3
        # Use a non-PENDING status
        mock_item.status = ApprovalStatus.APPROVED

        mock_cm, mock_session = _mock_db_session()

        with (
            patch("src.db.session.get_db_session", return_value=mock_cm),
            patch(
                "src.db.service.get_approval_by_id",
                new_callable=AsyncMock,
                return_value=mock_item,
            ),
        ):
            result = await META_TOOL_HANDLERS["decide_approval"](
                {"approval_id": 3, "decision": "approve"}
            )

        assert "already" in result[0].text.lower()


# ---------------------------------------------------------------------------
# run_claude_code_task
# ---------------------------------------------------------------------------


class TestRunClaudeCodeTask:
    """Tests for the run_claude_code_task meta-tool."""

    @pytest.mark.asyncio
    async def test_missing_skill_id_returns_error(self):
        result = await META_TOOL_HANDLERS["run_claude_code_task"]({"prompt": "hi"})
        assert len(result) == 1
        assert "required" in result[0].text.lower() or "skill_id" in result[0].text.lower()

    @pytest.mark.asyncio
    async def test_disabled_returns_error(self):
        with patch("src.config.settings") as mock_settings:
            mock_settings.claude_code_enabled = False
            result = await META_TOOL_HANDLERS["run_claude_code_task"]({"skill_id": "test_skill"})
        assert "disabled" in result[0].text.lower()

    @pytest.mark.asyncio
    async def test_skill_not_found_returns_error(self):
        mock_registry = _make_registry()
        mock_registry.get.return_value = None

        with (
            patch("src.config.settings") as mock_settings,
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
        ):
            mock_settings.claude_code_enabled = True
            result = await META_TOOL_HANDLERS["run_claude_code_task"]({"skill_id": "nonexistent"})

        assert "not found" in result[0].text.lower()

    @pytest.mark.asyncio
    async def test_happy_path_with_mock_executor(self):
        """Verify handler executes and formats the response."""
        from src.claude_code.executor import ClaudeCodeResult

        mock_skill = MagicMock()
        mock_skill.id = "test_skill"
        mock_skill.name = "Test Skill"
        mock_skill.department_id = "test_dept"

        mock_registry = _make_registry()
        mock_registry.get.return_value = mock_skill
        mock_registry.get_role.return_value = None

        fake_result = ClaudeCodeResult(
            skill_id="test_skill",
            user_id="claude_code",
            output_text="Analysis complete.",
            cost_usd=0.05,
            num_turns=3,
            duration_ms=5000,
        )

        mock_manager = MagicMock()
        mock_manager.run_task_sync = AsyncMock(return_value=fake_result)

        with (
            patch("src.config.settings") as mock_settings,
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch(
                "src.claude_code.task_manager.ClaudeCodeTaskManager",
                return_value=mock_manager,
            ),
        ):
            mock_settings.claude_code_enabled = True
            mock_settings.claude_code_default_budget_usd = 5.0
            mock_settings.claude_code_max_budget_usd = 25.0
            mock_settings.claude_code_max_concurrent = 20
            mock_settings.claude_code_default_permission_mode = "acceptEdits"

            result = await META_TOOL_HANDLERS["run_claude_code_task"]({"skill_id": "test_skill"})

        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert "Analysis complete." in result[0].text
        assert "$0.0500" in result[0].text

    @pytest.mark.asyncio
    async def test_role_context_loaded_when_role_id_provided(self):
        """When role_id is given, role context + memory should be loaded."""
        from src.claude_code.executor import ClaudeCodeResult

        role = _make_role()
        dept = _make_dept()
        mock_skill = MagicMock()
        mock_skill.id = "test_skill"
        mock_skill.department_id = "it"

        mock_registry = _make_registry(role, dept)
        mock_registry.get.return_value = mock_skill

        mock_cm, mock_session = _mock_db_session()

        fake_result = ClaudeCodeResult(
            skill_id="test_skill",
            user_id="claude_code",
            output_text="Done with context.",
            cost_usd=0.03,
            num_turns=2,
            duration_ms=3000,
        )

        mock_manager = MagicMock()
        mock_manager.run_task_sync = AsyncMock(return_value=fake_result)

        with (
            patch("src.config.settings") as mock_settings,
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch(
                "src.claude_code.task_manager.ClaudeCodeTaskManager",
                return_value=mock_manager,
            ),
            patch(
                "src.skills.executor.compose_role_context",
                return_value="You are the head of IT.",
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=mock_cm,
            ),
            patch(
                "src.db.service.get_role_memories",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "src.db.service.get_superseded_memory_ids",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "src.skills.memory.compose_memory_context",
                return_value="",
            ),
            patch(
                "src.skills.memory.filter_superseded_memories",
                return_value=[],
            ),
        ):
            mock_settings.claude_code_enabled = True
            mock_settings.claude_code_default_budget_usd = 5.0
            mock_settings.claude_code_max_budget_usd = 25.0
            mock_settings.claude_code_max_concurrent = 20
            mock_settings.claude_code_default_permission_mode = "acceptEdits"

            result = await META_TOOL_HANDLERS["run_claude_code_task"](
                {"skill_id": "test_skill", "role_id": "head_of_it"}
            )

        # Verify role context was passed to the manager
        call_kwargs = mock_manager.run_task_sync.call_args
        assert call_kwargs[1]["role_context"] == "You are the head of IT."
        assert "Done with context." in result[0].text

    @pytest.mark.asyncio
    async def test_budget_capped_at_max_setting(self):
        """Requested budget should be capped at claude_code_max_budget_usd."""
        from src.claude_code.executor import ClaudeCodeResult

        mock_skill = MagicMock()
        mock_skill.id = "test_skill"
        mock_skill.department_id = "test_dept"

        mock_registry = _make_registry()
        mock_registry.get.return_value = mock_skill
        mock_registry.get_role.return_value = None

        fake_result = ClaudeCodeResult(
            skill_id="test_skill",
            user_id="claude_code",
            output_text="Done.",
            cost_usd=0.01,
            num_turns=1,
            duration_ms=1000,
        )

        mock_manager = MagicMock()
        mock_manager.run_task_sync = AsyncMock(return_value=fake_result)

        with (
            patch("src.config.settings") as mock_settings,
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch(
                "src.claude_code.task_manager.ClaudeCodeTaskManager",
                return_value=mock_manager,
            ),
        ):
            mock_settings.claude_code_enabled = True
            mock_settings.claude_code_default_budget_usd = 5.0
            mock_settings.claude_code_max_budget_usd = 10.0  # Max cap
            mock_settings.claude_code_max_concurrent = 20
            mock_settings.claude_code_default_permission_mode = "acceptEdits"

            await META_TOOL_HANDLERS["run_claude_code_task"](
                {"skill_id": "test_skill", "max_budget_usd": 50.0}
            )

        # Budget passed should be capped at 10.0
        call_kwargs = mock_manager.run_task_sync.call_args
        assert call_kwargs[1]["max_budget_usd"] == 10.0
