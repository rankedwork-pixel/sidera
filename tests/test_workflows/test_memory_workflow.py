"""Tests for memory loading and extraction steps in role_runner_workflow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.workflows.daily_briefing import role_runner_workflow
from tests.test_workflows.conftest import (
    SAMPLE_ACCOUNTS,
    SAMPLE_RECOMMENDATIONS,
    _make_mock_context,
)

# =====================================================================
# Helpers
# =====================================================================


def _make_mock_role_result(
    *,
    role_id: str = "test_role",
    department_id: str = "test_dept",
) -> MagicMock:
    sr = MagicMock()
    sr.skill_id = "skill_0"
    sr.output_text = "Output"
    sr.recommendations = SAMPLE_RECOMMENDATIONS[:1]
    sr.cost = {"total_cost_usd": 0.10, "num_turns": 2}
    sr.session_id = "session-0"

    result = MagicMock()
    result.role_id = role_id
    result.department_id = department_id
    result.combined_output = "# Briefing\n\nContent"
    result.total_cost = {"total_cost_usd": 0.20, "num_turns": 4}
    result.session_id = "session-0"
    result.skill_results = [sr]
    return result


def _run_role_runner_with_mocks(
    event_data: dict,
    *,
    mock_memories: list | None = None,
    mock_role_result: MagicMock | None = None,
):
    """Set up all mocks and return awaitable + mocks dict."""
    ctx = _make_mock_context(event_data=event_data)

    role_result = mock_role_result or _make_mock_role_result()
    mock_role_executor = MagicMock()
    mock_role_executor.execute_role = AsyncMock(return_value=role_result)

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True, "ts": "111.222"}
    mock_slack.send_approval_request.return_value = {"ok": True}

    mock_get_memories = AsyncMock(return_value=mock_memories or [])
    mock_save_memory = AsyncMock()
    mock_session = AsyncMock()
    mock_get_pending_messages = AsyncMock(return_value=[])
    mock_mark_messages_delivered = AsyncMock(return_value=0)
    mock_get_superseded = AsyncMock(return_value=set())
    mock_get_agent_rel_memories = AsyncMock(return_value=[])

    patches = {
        "registry": patch("src.skills.db_loader.load_registry_with_db", new_callable=AsyncMock),
        "agent": patch("src.agent.core.SideraAgent"),
        "skill_exec": patch("src.skills.executor.SkillExecutor"),
        "role_exec": patch(
            "src.skills.executor.RoleExecutor",
            return_value=mock_role_executor,
        ),
        "slack": patch(
            "src.connectors.slack.SlackConnector",
            return_value=mock_slack,
        ),
        "get_memories": patch(
            "src.db.service.get_role_memories",
            mock_get_memories,
        ),
        "get_superseded": patch(
            "src.db.service.get_superseded_memory_ids",
            mock_get_superseded,
        ),
        "save_memory": patch(
            "src.db.service.save_memory",
            mock_save_memory,
        ),
        "get_session": patch(
            "src.db.session.get_db_session",
            return_value=mock_session,
        ),
        "save_analysis": patch(
            "src.db.service.save_analysis_result",
            AsyncMock(return_value=MagicMock(id=42)),
        ),
        "record_cost": patch(
            "src.db.service.record_cost",
            AsyncMock(),
        ),
        "log_event": patch(
            "src.db.service.log_event",
            AsyncMock(),
        ),
        "get_pending_messages": patch(
            "src.db.service.get_pending_messages",
            mock_get_pending_messages,
        ),
        "mark_messages_delivered": patch(
            "src.db.service.mark_messages_delivered",
            mock_mark_messages_delivered,
        ),
        "get_agent_rel_memories": patch(
            "src.db.service.get_agent_relationship_memories",
            mock_get_agent_rel_memories,
        ),
    }

    return (
        ctx,
        patches,
        {
            "role_executor": mock_role_executor,
            "get_memories": mock_get_memories,
            "save_memory": mock_save_memory,
            "slack": mock_slack,
        },
    )


# =====================================================================
# load-role-memory step
# =====================================================================


class TestLoadRoleMemory:
    @pytest.mark.asyncio
    async def test_memory_loaded_before_execution(self):
        """Memories are loaded and passed to execute_role."""
        fake_mem = MagicMock()
        fake_mem.memory_type = "decision"
        fake_mem.content = "Budget approved +20%"
        fake_mem.confidence = 1.0
        fake_mem.created_at = None

        ctx, patches, mocks = _run_role_runner_with_mocks(
            event_data={
                "role_id": "buyer",
                "user_id": "u1",
                "accounts": SAMPLE_ACCOUNTS,
            },
            mock_memories=[fake_mem],
        )

        with (
            patches["registry"],
            patches["agent"],
            patches["skill_exec"],
            patches["role_exec"],
            patches["slack"],
            patches["get_memories"],
            patches["get_superseded"],
            patches["save_memory"],
            patches["get_session"],
            patches["save_analysis"],
            patches["record_cost"],
            patches["log_event"],
            patches["get_pending_messages"],
            patches["mark_messages_delivered"],
            patches["get_agent_rel_memories"],
        ):
            await role_runner_workflow._handler(ctx)

        # execute_role should have been called with memory_context
        call_kwargs = mocks["role_executor"].execute_role.call_args
        assert call_kwargs is not None
        mem_ctx = call_kwargs.kwargs.get("memory_context", "")
        assert "Role Memory" in mem_ctx
        assert "Budget approved" in mem_ctx

    @pytest.mark.asyncio
    async def test_no_memories_empty_context(self):
        """With no memories, memory_context is empty string."""
        ctx, patches, mocks = _run_role_runner_with_mocks(
            event_data={
                "role_id": "buyer",
                "user_id": "u1",
                "accounts": SAMPLE_ACCOUNTS,
            },
            mock_memories=[],
        )

        with (
            patches["registry"],
            patches["agent"],
            patches["skill_exec"],
            patches["role_exec"],
            patches["slack"],
            patches["get_memories"],
            patches["get_superseded"],
            patches["save_memory"],
            patches["get_session"],
            patches["save_analysis"],
            patches["record_cost"],
            patches["log_event"],
            patches["get_pending_messages"],
            patches["mark_messages_delivered"],
            patches["get_agent_rel_memories"],
        ):
            await role_runner_workflow._handler(ctx)

        call_kwargs = mocks["role_executor"].execute_role.call_args
        mem_ctx = call_kwargs.kwargs.get("memory_context", "")
        assert mem_ctx == ""

    @pytest.mark.asyncio
    async def test_memory_load_failure_graceful(self):
        """If memory loading fails, execution continues."""
        ctx, patches, mocks = _run_role_runner_with_mocks(
            event_data={
                "role_id": "buyer",
                "user_id": "u1",
                "accounts": SAMPLE_ACCOUNTS,
            },
        )

        # Make get_role_memories raise
        mocks["get_memories"].side_effect = Exception("DB down")

        with (
            patches["registry"],
            patches["agent"],
            patches["skill_exec"],
            patches["role_exec"],
            patches["slack"],
            patches["get_memories"],
            patches["get_superseded"],
            patches["save_memory"],
            patches["get_session"],
            patches["save_analysis"],
            patches["record_cost"],
            patches["log_event"],
            patches["get_pending_messages"],
            patches["mark_messages_delivered"],
            patches["get_agent_rel_memories"],
        ):
            result = await role_runner_workflow._handler(ctx)

        # Should still complete successfully
        assert result["role_id"] == "buyer"


# =====================================================================
# extract-and-save-memories step
# =====================================================================


class TestExtractAndSaveMemories:
    @pytest.mark.asyncio
    async def test_memories_extracted_from_output(self):
        """Anomaly keywords in output produce memory entries."""
        role_result = _make_mock_role_result()
        role_result.combined_output = "CPA spiked 3x on Meta retargeting campaign yesterday."

        ctx, patches, mocks = _run_role_runner_with_mocks(
            event_data={
                "role_id": "buyer",
                "user_id": "u1",
                "accounts": SAMPLE_ACCOUNTS,
            },
            mock_role_result=role_result,
        )

        with (
            patches["registry"],
            patches["agent"],
            patches["skill_exec"],
            patches["role_exec"],
            patches["slack"],
            patches["get_memories"],
            patches["get_superseded"],
            patches["save_memory"],
            patches["get_session"],
            patches["save_analysis"],
            patches["record_cost"],
            patches["log_event"],
            patches["get_pending_messages"],
            patches["mark_messages_delivered"],
            patches["get_agent_rel_memories"],
        ):
            await role_runner_workflow._handler(ctx)

        # save_memory should have been called at least once
        assert mocks["save_memory"].call_count >= 1

    @pytest.mark.asyncio
    async def test_no_memories_when_no_anomalies(self):
        """Clean output produces no memory entries."""
        role_result = _make_mock_role_result()
        role_result.combined_output = "All campaigns are performing within normal parameters."

        ctx, patches, mocks = _run_role_runner_with_mocks(
            event_data={
                "role_id": "buyer",
                "user_id": "u1",
                "accounts": SAMPLE_ACCOUNTS,
            },
            mock_role_result=role_result,
        )

        with (
            patches["registry"],
            patches["agent"],
            patches["skill_exec"],
            patches["role_exec"],
            patches["slack"],
            patches["get_memories"],
            patches["get_superseded"],
            patches["save_memory"],
            patches["get_session"],
            patches["save_analysis"],
            patches["record_cost"],
            patches["log_event"],
            patches["get_pending_messages"],
            patches["mark_messages_delivered"],
            patches["get_agent_rel_memories"],
        ):
            await role_runner_workflow._handler(ctx)

        # save_memory should not have been called
        assert mocks["save_memory"].call_count == 0

    @pytest.mark.asyncio
    async def test_memory_save_failure_graceful(self):
        """If memory saving fails, workflow continues."""
        role_result = _make_mock_role_result()
        role_result.combined_output = "CPA spiked 3x on Meta retargeting campaign."

        ctx, patches, mocks = _run_role_runner_with_mocks(
            event_data={
                "role_id": "buyer",
                "user_id": "u1",
                "accounts": SAMPLE_ACCOUNTS,
            },
            mock_role_result=role_result,
        )

        mocks["save_memory"].side_effect = Exception("DB down")

        with (
            patches["registry"],
            patches["agent"],
            patches["skill_exec"],
            patches["role_exec"],
            patches["slack"],
            patches["get_memories"],
            patches["get_superseded"],
            patches["save_memory"],
            patches["get_session"],
            patches["save_analysis"],
            patches["record_cost"],
            patches["log_event"],
            patches["get_pending_messages"],
            patches["mark_messages_delivered"],
            patches["get_agent_rel_memories"],
        ):
            # Should not raise
            result = await role_runner_workflow._handler(ctx)

        assert result["role_id"] == "buyer"


# =====================================================================
# Step ID verification
# =====================================================================


class TestMemoryStepIds:
    @pytest.mark.asyncio
    async def test_load_memory_step_exists(self):
        """load-role-memory step is called during workflow."""
        ctx, patches, mocks = _run_role_runner_with_mocks(
            event_data={
                "role_id": "buyer",
                "user_id": "u1",
                "accounts": SAMPLE_ACCOUNTS,
            },
        )

        with (
            patches["registry"],
            patches["agent"],
            patches["skill_exec"],
            patches["role_exec"],
            patches["slack"],
            patches["get_memories"],
            patches["get_superseded"],
            patches["save_memory"],
            patches["get_session"],
            patches["save_analysis"],
            patches["record_cost"],
            patches["log_event"],
            patches["get_pending_messages"],
            patches["mark_messages_delivered"],
            patches["get_agent_rel_memories"],
        ):
            await role_runner_workflow._handler(ctx)

        step_ids = [call.args[0] for call in ctx.step.run.call_args_list]
        assert "load-role-memory" in step_ids

    @pytest.mark.asyncio
    async def test_extract_memory_step_exists(self):
        """extract-and-save-memories step is called during workflow."""
        ctx, patches, mocks = _run_role_runner_with_mocks(
            event_data={
                "role_id": "buyer",
                "user_id": "u1",
                "accounts": SAMPLE_ACCOUNTS,
            },
        )

        with (
            patches["registry"],
            patches["agent"],
            patches["skill_exec"],
            patches["role_exec"],
            patches["slack"],
            patches["get_memories"],
            patches["get_superseded"],
            patches["save_memory"],
            patches["get_session"],
            patches["save_analysis"],
            patches["record_cost"],
            patches["log_event"],
            patches["get_pending_messages"],
            patches["mark_messages_delivered"],
            patches["get_agent_rel_memories"],
        ):
            await role_runner_workflow._handler(ctx)

        step_ids = [call.args[0] for call in ctx.step.run.call_args_list]
        assert "extract-and-save-memories" in step_ids

    @pytest.mark.asyncio
    async def test_memory_steps_order(self):
        """Memory load before execution, extract after save."""
        ctx, patches, mocks = _run_role_runner_with_mocks(
            event_data={
                "role_id": "buyer",
                "user_id": "u1",
                "accounts": SAMPLE_ACCOUNTS,
            },
        )

        with (
            patches["registry"],
            patches["agent"],
            patches["skill_exec"],
            patches["role_exec"],
            patches["slack"],
            patches["get_memories"],
            patches["get_superseded"],
            patches["save_memory"],
            patches["get_session"],
            patches["save_analysis"],
            patches["record_cost"],
            patches["log_event"],
            patches["get_pending_messages"],
            patches["mark_messages_delivered"],
            patches["get_agent_rel_memories"],
        ):
            await role_runner_workflow._handler(ctx)

        step_ids = [call.args[0] for call in ctx.step.run.call_args_list]
        load_idx = step_ids.index("load-role-memory")
        exec_idx = step_ids.index("execute-role")
        save_idx = step_ids.index("save-to-db")
        extract_idx = step_ids.index("extract-and-save-memories")
        send_idx = step_ids.index("send-output")

        assert load_idx < exec_idx
        assert save_idx < extract_idx
        assert extract_idx < send_idx
