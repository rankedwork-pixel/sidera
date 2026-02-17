"""Tests for role_runner_workflow and department_runner_workflow.

Covers the two hierarchy Inngest workflows in
``src/workflows/daily_briefing.py``. All external dependencies
(RoleExecutor, DepartmentExecutor, Slack, DB) are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import inngest
import pytest

from src.workflows.daily_briefing import (
    department_runner_workflow,
    role_runner_workflow,
)
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
    num_skills: int = 2,
) -> MagicMock:
    """Create a mock RoleResult returned by RoleExecutor.execute_role."""
    skill_results = []
    for i in range(num_skills):
        sr = MagicMock()
        sr.skill_id = f"skill_{i}"
        sr.output_text = f"Output from skill_{i}"
        sr.recommendations = SAMPLE_RECOMMENDATIONS[:1] if i == 0 else []
        sr.cost = {"total_cost_usd": 0.10, "num_turns": 2}
        sr.session_id = f"session-{i}"
        skill_results.append(sr)

    result = MagicMock()
    result.role_id = role_id
    result.department_id = department_id
    result.combined_output = f"# {role_id} — Briefing\n\n## skill_0\nOutput from skill_0"
    result.total_cost = {"total_cost_usd": 0.20, "num_turns": 4}
    result.session_id = "session-0"
    result.skill_results = skill_results
    return result


def _make_mock_dept_result(
    *,
    department_id: str = "test_dept",
    num_roles: int = 1,
) -> MagicMock:
    """Create a mock DepartmentResult returned by DepartmentExecutor."""
    role_results = [
        _make_mock_role_result(
            role_id=f"role_{i}",
            department_id=department_id,
        )
        for i in range(num_roles)
    ]

    result = MagicMock()
    result.department_id = department_id
    result.combined_output = f"# {department_id} — Department Report\n\nContent"
    result.total_cost = {"total_cost_usd": 0.40, "num_turns": 8}
    result.role_results = role_results
    return result


# =====================================================================
# role_runner_workflow — configuration
# =====================================================================


class TestRoleRunnerConfig:
    """Configuration tests for role_runner_workflow."""

    def test_function_id(self):
        assert role_runner_workflow.id == "sidera-sidera-role-runner"

    def test_event_trigger(self):
        config = role_runner_workflow.get_config("sidera")
        triggers = config.main.triggers
        assert len(triggers) == 1
        assert triggers[0].event == "sidera/role.run"


# =====================================================================
# role_runner_workflow — success path
# =====================================================================


@pytest.mark.asyncio
async def test_role_runner_full_success():
    """Full happy-path: load accounts, execute role, save, send to Slack."""
    ctx = _make_mock_context(
        event_data={
            "role_id": "media_buyer",
            "user_id": "user-42",
            "channel_id": "C0123",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    mock_role_executor = MagicMock()
    mock_role_executor.execute_role = AsyncMock(
        return_value=_make_mock_role_result(role_id="media_buyer")
    )

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True, "ts": "111.222"}
    mock_slack.send_approval_request.return_value = {"ok": True}

    with (
        patch("src.skills.db_loader.load_registry_with_db", new_callable=AsyncMock),
        patch("src.agent.core.SideraAgent"),
        patch("src.skills.executor.SkillExecutor"),
        patch(
            "src.skills.executor.RoleExecutor",
            return_value=mock_role_executor,
        ),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        result = await role_runner_workflow._handler(ctx)

    assert result["role_id"] == "media_buyer"
    assert result["user_id"] == "user-42"
    assert result["output_sent"] is True
    assert result["skills_run"] == 2


# =====================================================================
# role_runner_workflow — missing role_id
# =====================================================================


@pytest.mark.asyncio
async def test_role_runner_missing_role_id():
    """Raises NonRetriableError when role_id is empty."""
    ctx = _make_mock_context(event_data={"user_id": "user-42", "accounts": SAMPLE_ACCOUNTS})
    with pytest.raises(inngest.NonRetriableError, match="role_id is required"):
        await role_runner_workflow._handler(ctx)


# =====================================================================
# role_runner_workflow — no accounts
# =====================================================================


@pytest.mark.asyncio
async def test_role_runner_no_accounts():
    """Raises NonRetriableError when no accounts configured."""
    ctx = _make_mock_context(
        event_data={"role_id": "test_role", "user_id": "user-42", "accounts": []}
    )
    with pytest.raises(inngest.NonRetriableError, match="No accounts configured"):
        await role_runner_workflow._handler(ctx)


# =====================================================================
# role_runner_workflow — execution error goes to DLQ
# =====================================================================


@pytest.mark.asyncio
async def test_role_runner_execution_error():
    """Exceptions from RoleExecutor propagate (for DLQ recording)."""
    ctx = _make_mock_context(
        event_data={
            "role_id": "broken_role",
            "user_id": "user-42",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    mock_role_executor = MagicMock()
    mock_role_executor.execute_role = AsyncMock(side_effect=RuntimeError("Role failed"))

    with (
        patch("src.skills.db_loader.load_registry_with_db", new_callable=AsyncMock),
        patch("src.agent.core.SideraAgent"),
        patch("src.skills.executor.SkillExecutor"),
        patch(
            "src.skills.executor.RoleExecutor",
            return_value=mock_role_executor,
        ),
    ):
        with pytest.raises(RuntimeError, match="Role failed"):
            await role_runner_workflow._handler(ctx)


# =====================================================================
# role_runner_workflow — step IDs
# =====================================================================


@pytest.mark.asyncio
async def test_role_runner_step_ids():
    """Step IDs must include the expected fixed strings."""
    ctx = _make_mock_context(
        event_data={
            "role_id": "test_role",
            "user_id": "user-42",
            "channel_id": "C01",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    mock_role_executor = MagicMock()
    mock_role_result = _make_mock_role_result()
    # Zero recommendations so no approval step
    for sr in mock_role_result.skill_results:
        sr.recommendations = []
    mock_role_executor.execute_role = AsyncMock(return_value=mock_role_result)

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}

    with (
        patch("src.skills.db_loader.load_registry_with_db", new_callable=AsyncMock),
        patch("src.agent.core.SideraAgent"),
        patch("src.skills.executor.SkillExecutor"),
        patch(
            "src.skills.executor.RoleExecutor",
            return_value=mock_role_executor,
        ),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        await role_runner_workflow._handler(ctx)

    step_ids = [call.args[0] for call in ctx.step.run.call_args_list]
    assert "load-accounts" in step_ids
    assert "execute-role" in step_ids
    assert "save-to-db" in step_ids
    assert "send-output" in step_ids


# =====================================================================
# role_runner_workflow — collects recommendations from all skills
# =====================================================================


@pytest.mark.asyncio
async def test_role_runner_collects_all_recommendations():
    """Recommendations from all skills are aggregated for approval."""
    ctx = _make_mock_context(
        event_data={
            "role_id": "media_buyer",
            "user_id": "user-42",
            "channel_id": "C01",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    mock_role_executor = MagicMock()
    mock_role_executor.execute_role = AsyncMock(return_value=_make_mock_role_result(num_skills=3))

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}
    mock_slack.send_approval_request.return_value = {"ok": True}

    with (
        patch("src.skills.db_loader.load_registry_with_db", new_callable=AsyncMock),
        patch("src.agent.core.SideraAgent"),
        patch("src.skills.executor.SkillExecutor"),
        patch(
            "src.skills.executor.RoleExecutor",
            return_value=mock_role_executor,
        ),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        result = await role_runner_workflow._handler(ctx)

    # skill_0 has 1 recommendation, skills 1-2 have 0
    assert result["approvals_sent"] == 1


# =====================================================================
# role_runner_workflow — default user_id
# =====================================================================


@pytest.mark.asyncio
async def test_role_runner_default_user_id():
    """When user_id is missing, defaults to 'default'."""
    ctx = _make_mock_context(
        event_data={
            "role_id": "test_role",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    mock_role_executor = MagicMock()
    role_result = _make_mock_role_result()
    for sr in role_result.skill_results:
        sr.recommendations = []
    mock_role_executor.execute_role = AsyncMock(return_value=role_result)

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}

    with (
        patch("src.skills.db_loader.load_registry_with_db", new_callable=AsyncMock),
        patch("src.agent.core.SideraAgent"),
        patch("src.skills.executor.SkillExecutor"),
        patch(
            "src.skills.executor.RoleExecutor",
            return_value=mock_role_executor,
        ),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        result = await role_runner_workflow._handler(ctx)

    assert result["user_id"] == "default"


# =====================================================================
# department_runner_workflow — configuration
# =====================================================================


class TestDepartmentRunnerConfig:
    """Configuration tests for department_runner_workflow."""

    def test_function_id(self):
        assert department_runner_workflow.id == "sidera-sidera-department-runner"

    def test_event_trigger(self):
        config = department_runner_workflow.get_config("sidera")
        triggers = config.main.triggers
        assert len(triggers) == 1
        assert triggers[0].event == "sidera/department.run"


# =====================================================================
# department_runner_workflow — success path
# =====================================================================


@pytest.mark.asyncio
async def test_department_runner_full_success():
    """Full happy-path: load accounts, execute department, save, send."""
    ctx = _make_mock_context(
        event_data={
            "department_id": "marketing",
            "user_id": "user-42",
            "channel_id": "C01",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    mock_dept_executor = MagicMock()
    mock_dept_executor.execute_department = AsyncMock(
        return_value=_make_mock_dept_result(department_id="marketing")
    )

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True, "ts": "123.456"}

    with (
        patch("src.skills.db_loader.load_registry_with_db", new_callable=AsyncMock),
        patch("src.agent.core.SideraAgent"),
        patch("src.skills.executor.SkillExecutor"),
        patch("src.skills.executor.RoleExecutor"),
        patch(
            "src.skills.executor.DepartmentExecutor",
            return_value=mock_dept_executor,
        ),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        result = await department_runner_workflow._handler(ctx)

    assert result["department_id"] == "marketing"
    assert result["user_id"] == "user-42"
    assert result["output_sent"] is True
    assert result["roles_run"] == 1


# =====================================================================
# department_runner_workflow — missing department_id
# =====================================================================


@pytest.mark.asyncio
async def test_department_runner_missing_dept_id():
    """Raises NonRetriableError when department_id is empty."""
    ctx = _make_mock_context(event_data={"user_id": "user-42", "accounts": SAMPLE_ACCOUNTS})
    with pytest.raises(inngest.NonRetriableError, match="department_id is required"):
        await department_runner_workflow._handler(ctx)


# =====================================================================
# department_runner_workflow — no accounts
# =====================================================================


@pytest.mark.asyncio
async def test_department_runner_no_accounts():
    """Raises NonRetriableError when no accounts configured."""
    ctx = _make_mock_context(
        event_data={
            "department_id": "marketing",
            "user_id": "user-42",
            "accounts": [],
        }
    )
    with pytest.raises(inngest.NonRetriableError, match="No accounts configured"):
        await department_runner_workflow._handler(ctx)


# =====================================================================
# department_runner_workflow — execution error
# =====================================================================


@pytest.mark.asyncio
async def test_department_runner_execution_error():
    """Exceptions from DepartmentExecutor propagate (for DLQ recording)."""
    ctx = _make_mock_context(
        event_data={
            "department_id": "broken",
            "user_id": "user-42",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    mock_dept_executor = MagicMock()
    mock_dept_executor.execute_department = AsyncMock(side_effect=RuntimeError("Dept failed"))

    with (
        patch("src.skills.db_loader.load_registry_with_db", new_callable=AsyncMock),
        patch("src.agent.core.SideraAgent"),
        patch("src.skills.executor.SkillExecutor"),
        patch("src.skills.executor.RoleExecutor"),
        patch(
            "src.skills.executor.DepartmentExecutor",
            return_value=mock_dept_executor,
        ),
    ):
        with pytest.raises(RuntimeError, match="Dept failed"):
            await department_runner_workflow._handler(ctx)


# =====================================================================
# department_runner_workflow — step IDs
# =====================================================================


@pytest.mark.asyncio
async def test_department_runner_step_ids():
    """Step IDs must include the expected fixed strings."""
    ctx = _make_mock_context(
        event_data={
            "department_id": "marketing",
            "user_id": "user-42",
            "channel_id": "C01",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    mock_dept_executor = MagicMock()
    mock_dept_executor.execute_department = AsyncMock(return_value=_make_mock_dept_result())

    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": True}

    with (
        patch("src.skills.db_loader.load_registry_with_db", new_callable=AsyncMock),
        patch("src.agent.core.SideraAgent"),
        patch("src.skills.executor.SkillExecutor"),
        patch("src.skills.executor.RoleExecutor"),
        patch(
            "src.skills.executor.DepartmentExecutor",
            return_value=mock_dept_executor,
        ),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
    ):
        await department_runner_workflow._handler(ctx)

    step_ids = [call.args[0] for call in ctx.step.run.call_args_list]
    assert "load-accounts" in step_ids
    assert "execute-department" in step_ids
    assert "save-to-db" in step_ids
    assert "send-output" in step_ids


# =====================================================================
# all_workflows includes hierarchy workflows
# =====================================================================


def test_all_workflows_includes_role_runner():
    """role_runner_workflow is in the all_workflows export list."""
    from src.workflows.daily_briefing import all_workflows

    assert role_runner_workflow in all_workflows


def test_all_workflows_includes_department_runner():
    """department_runner_workflow is in the all_workflows export list."""
    from src.workflows.daily_briefing import all_workflows

    assert department_runner_workflow in all_workflows


def test_all_workflows_has_sixteen_entries():
    """all_workflows now exports 17 workflows total (including event_reactor + working_group)."""
    from src.workflows.daily_briefing import all_workflows

    assert len(all_workflows) == 17


# =====================================================================
# skill_scheduler_workflow dispatches roles
# =====================================================================


@pytest.mark.asyncio
async def test_skill_scheduler_dispatches_roles():
    """Scheduler dispatches sidera/role.run for roles with matching cron."""
    from src.workflows.daily_briefing import skill_scheduler_workflow

    ctx = _make_mock_context(event_data={"user_id": "user-42", "channel_id": "C01"})

    mock_role = MagicMock()
    mock_role.id = "media_buyer"
    mock_role.schedule = "0 7 * * MON"
    mock_role.heartbeat_schedule = None  # No heartbeat for this test

    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 3
    mock_registry.list_scheduled.return_value = []
    mock_registry.list_roles.return_value = [mock_role]
    mock_registry.role_count = 1

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
            return_value=True,
        ),
    ):
        result = await skill_scheduler_workflow._handler(ctx)

    assert result["scheduled_roles"] == 1
    assert result["dispatched"] == 1

    sent_event = mock_inngest_send.call_args.args[0]
    assert sent_event.name == "sidera/role.run"
    assert sent_event.data["role_id"] == "media_buyer"
