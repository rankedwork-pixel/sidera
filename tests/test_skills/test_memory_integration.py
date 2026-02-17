"""Tests for memory wiring in compose_role_context and RoleExecutor."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.skills.executor import compose_role_context

# =====================================================================
# Helpers — fake definitions
# =====================================================================


@dataclass
class FakeDepartment:
    id: str = "marketing"
    name: str = "Marketing"
    description: str = "Marketing department"
    context: str = "Department context here"
    context_files: tuple = ()
    source_dir: str = ""
    vocabulary: tuple = ()


@dataclass
class FakeRole:
    id: str = "media_buyer"
    name: str = "Media Buyer"
    department_id: str = "marketing"
    description: str = "Buys media"
    persona: str = "You are a media buyer."
    connectors: tuple = ()
    briefing_skills: tuple = ()
    schedule: str | None = None
    context_files: tuple = ()
    source_dir: str = ""
    principles: tuple = ()
    goals: tuple = ()


# =====================================================================
# compose_role_context with memory_context
# =====================================================================


class TestComposeRoleContextMemory:
    def test_no_memory_backward_compat(self):
        """Without memory_context, result is same as before."""
        result = compose_role_context(FakeDepartment(), FakeRole())
        assert "Department context here" in result
        assert "You are a media buyer" in result
        assert "Role Memory" not in result

    def test_memory_injected_after_role(self):
        """memory_context appears after role persona."""
        memory = "# Role Memory\n\n## Recent Decisions\n- Budget approved"
        result = compose_role_context(
            FakeDepartment(),
            FakeRole(),
            memory_context=memory,
        )
        assert "Role Memory" in result
        assert "Budget approved" in result
        # Memory should be after role persona
        role_pos = result.find("media buyer")
        mem_pos = result.find("Role Memory")
        assert mem_pos > role_pos

    def test_empty_memory_string_ignored(self):
        """Empty string doesn't add extra section."""
        result = compose_role_context(
            FakeDepartment(),
            FakeRole(),
            memory_context="",
        )
        sections = [s for s in result.split("\n\n") if s.strip()]
        result_no_mem = compose_role_context(FakeDepartment(), FakeRole())
        sections_no_mem = [s for s in result_no_mem.split("\n\n") if s.strip()]
        assert len(sections) == len(sections_no_mem)

    def test_memory_with_no_department(self):
        """Works with department=None."""
        memory = "# Role Memory\n\n- Test memory"
        result = compose_role_context(
            None,
            FakeRole(),
            memory_context=memory,
        )
        assert "Role Memory" in result
        assert "Test memory" in result

    def test_memory_preserves_formatting(self):
        """Memory text is included verbatim."""
        memory = "# Role Memory\n\n## Decisions\n- Item 1\n- Item 2"
        result = compose_role_context(
            FakeDepartment(),
            FakeRole(),
            memory_context=memory,
        )
        assert "## Decisions" in result
        assert "- Item 1" in result
        assert "- Item 2" in result


# =====================================================================
# RoleExecutor.execute_role with memory_context
# =====================================================================


class TestRoleExecutorMemory:
    @pytest.mark.asyncio
    async def test_memory_context_passed_through(self):
        """memory_context flows to compose_role_context."""
        from src.skills.executor import RoleExecutor

        mock_skill_executor = MagicMock()
        mock_skill_executor.execute = AsyncMock(
            return_value=MagicMock(
                skill_id="test",
                output_text="Output",
                recommendations=[],
                cost={"total_cost_usd": 0.01, "num_turns": 1, "duration_ms": 100},
                session_id="s1",
                chain_next=None,
            ),
        )

        mock_registry = MagicMock()
        mock_role = FakeRole(briefing_skills=("test_skill",))
        mock_registry.get_role.return_value = mock_role
        mock_registry.get_department.return_value = FakeDepartment()
        mock_registry.get.return_value = MagicMock(
            id="test_skill",
            name="Test",
        )

        executor = RoleExecutor(mock_skill_executor, mock_registry)
        await executor.execute_role(
            role_id="media_buyer",
            user_id="u1",
            accounts=[{"platform": "google_ads", "account_id": "123"}],
            memory_context="# Role Memory\n\n- Test mem",
        )

        # Verify skill_executor.execute was called with role_context
        # that includes the memory
        call_kwargs = mock_skill_executor.execute.call_args
        role_ctx = call_kwargs.kwargs.get(
            "role_context",
            call_kwargs.args[4] if len(call_kwargs.args) > 4 else "",
        )
        assert "Role Memory" in role_ctx

    @pytest.mark.asyncio
    async def test_no_memory_default(self):
        """Without memory_context, execute_role works as before."""
        from src.skills.executor import RoleExecutor

        mock_skill_executor = MagicMock()
        mock_skill_executor.execute = AsyncMock(
            return_value=MagicMock(
                skill_id="test",
                output_text="Output",
                recommendations=[],
                cost={"total_cost_usd": 0.01, "num_turns": 1, "duration_ms": 100},
                session_id="s1",
                chain_next=None,
            ),
        )

        mock_registry = MagicMock()
        mock_role = FakeRole(briefing_skills=("test_skill",))
        mock_registry.get_role.return_value = mock_role
        mock_registry.get_department.return_value = FakeDepartment()
        mock_registry.get.return_value = MagicMock(id="test_skill", name="Test")

        executor = RoleExecutor(mock_skill_executor, mock_registry)
        result = await executor.execute_role(
            role_id="media_buyer",
            user_id="u1",
            accounts=[{"platform": "google_ads", "account_id": "123"}],
        )

        assert result.role_id == "media_buyer"
        assert result.combined_output  # Some output was produced
