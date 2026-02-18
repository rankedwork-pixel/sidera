"""Tests for manager_runner_workflow.

Covers the manager Inngest workflow in
``src/workflows/daily_briefing.py``. All external dependencies
(SkillRegistry, SideraAgent, RoleExecutor, Slack, DB) are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import inngest
import pytest

from src.workflows.daily_briefing import manager_runner_workflow
from tests.test_workflows.conftest import (
    SAMPLE_ACCOUNTS,
    SAMPLE_RECOMMENDATIONS,
    _make_mock_context,
)

# =====================================================================
# Helpers
# =====================================================================


def _make_mock_role(
    *,
    role_id: str = "head_of_marketing",
    name: str = "Head of Marketing",
    manages: tuple[str, ...] = ("media_buyer", "analyst"),
    briefing_skills: tuple[str, ...] = ("overview_skill",),
    persona: str = "Strategic marketing leader",
    department_id: str = "marketing",
    delegation_model: str = "standard",
    synthesis_prompt: str = "",
) -> MagicMock:
    """Create a mock RoleDefinition for a manager role."""
    role = MagicMock()
    role.id = role_id
    role.name = name
    role.manages = manages
    role.briefing_skills = briefing_skills
    role.persona = persona
    role.department_id = department_id
    role.delegation_model = delegation_model
    role.synthesis_prompt = synthesis_prompt
    return role


def _make_mock_managed_role(
    *,
    role_id: str = "media_buyer",
    name: str = "Media Buyer",
    description: str = "Manages ad campaigns",
    department_id: str = "marketing",
    briefing_skills: tuple[str, ...] = ("campaign_analysis",),
) -> MagicMock:
    """Create a mock RoleDefinition for a managed sub-role."""
    role = MagicMock()
    role.id = role_id
    role.name = name
    role.description = description
    role.department_id = department_id
    role.briefing_skills = briefing_skills
    return role


def _make_mock_role_result(
    *,
    role_id: str = "test_role",
    department_id: str = "marketing",
    num_skills: int = 1,
    with_recommendations: bool = True,
) -> MagicMock:
    """Create a mock RoleResult returned by RoleExecutor.execute_role."""
    skill_results = []
    for i in range(num_skills):
        sr = MagicMock()
        sr.skill_id = f"skill_{i}"
        sr.output_text = f"Output from skill_{i}"
        sr.recommendations = SAMPLE_RECOMMENDATIONS[:1] if (i == 0 and with_recommendations) else []
        sr.cost = {"total_cost_usd": 0.10, "num_turns": 2}
        sr.session_id = f"session-{i}"
        skill_results.append(sr)

    result = MagicMock()
    result.role_id = role_id
    result.department_id = department_id
    result.combined_output = f"# {role_id} Briefing\n\nOutput from skill_0"
    result.total_cost = {"total_cost_usd": 0.20, "num_turns": 4}
    result.session_id = "session-0"
    result.skill_results = skill_results
    return result


def _build_standard_patches(
    *,
    manager_role: MagicMock | None = None,
    managed_roles: list[MagicMock] | None = None,
    own_role_result: MagicMock | None = None,
    sub_role_results: dict[str, MagicMock] | None = None,
    delegation_decision: dict | None = None,
    synthesis_text: str = "# Unified Briefing\n\nSynthesized content.",
    slack_ok: bool = True,
):
    """Build a dict of standard patches for manager_runner_workflow tests.

    Returns a context manager that patches all external deps.
    """
    if manager_role is None:
        manager_role = _make_mock_role()
    if managed_roles is None:
        managed_roles = [
            _make_mock_managed_role(role_id="media_buyer"),
            _make_mock_managed_role(
                role_id="analyst",
                name="Analyst",
                description="Analyzes data",
            ),
        ]
    if own_role_result is None:
        own_role_result = _make_mock_role_result(
            role_id="head_of_marketing",
        )
    if sub_role_results is None:
        sub_role_results = {
            "media_buyer": _make_mock_role_result(role_id="media_buyer"),
            "analyst": _make_mock_role_result(
                role_id="analyst",
                with_recommendations=False,
            ),
        }
    if delegation_decision is None:
        delegation_decision = {
            "activate": [
                {"role_id": "media_buyer", "reason": "Active campaigns", "priority": 1},
                {"role_id": "analyst", "reason": "Data needed", "priority": 2},
            ],
            "skip": [],
        }

    # Build mock registry
    mock_registry_instance = MagicMock()
    mock_registry_instance.load_all.return_value = 10
    mock_registry_instance.get_role.return_value = manager_role
    mock_registry_instance.get_managed_roles.return_value = managed_roles

    # Build mock role_executor that returns different results per role_id
    mock_role_executor = MagicMock()

    async def _mock_execute_role(role_id, user_id, accounts, **kwargs):
        if role_id in sub_role_results:
            return sub_role_results[role_id]
        return own_role_result

    mock_role_executor.execute_role = AsyncMock(
        side_effect=_mock_execute_role,
    )

    # Build mock agent
    mock_agent = MagicMock()
    mock_agent.run_delegation_decision = AsyncMock(
        return_value=delegation_decision,
    )
    mock_agent.run_synthesis = AsyncMock(
        return_value=synthesis_text,
    )

    # Build mock slack
    mock_slack = MagicMock()
    mock_slack.send_briefing.return_value = {"ok": slack_ok}
    mock_slack.send_approval_request.return_value = {"ok": True}

    # Build mock DB analysis result
    mock_analysis = MagicMock()
    mock_analysis.id = 42

    return (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry_instance,
        ),
        patch("src.agent.core.SideraAgent", return_value=mock_agent),
        patch("src.skills.executor.SkillExecutor"),
        patch(
            "src.skills.executor.RoleExecutor",
            return_value=mock_role_executor,
        ),
        patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
        patch(
            "src.workflows.approval_flow.process_recommendations",
            new_callable=AsyncMock,
            return_value={
                "auto_executed": 0,
                "sent_for_approval": 1,
                "approved": 0,
                "rejected": 0,
                "expired": 0,
                "executed": 0,
                "failed": 0,
                "errors": [],
            },
        ),
        mock_registry_instance,
        mock_agent,
        mock_role_executor,
        mock_slack,
    )


# =====================================================================
# Configuration tests
# =====================================================================


class TestManagerRunnerConfig:
    """Configuration tests for manager_runner_workflow."""

    def test_function_id(self):
        assert manager_runner_workflow.id == "sidera-manager-runner"

    def test_event_trigger(self):
        config = manager_runner_workflow.get_config("sidera")
        triggers = config.main.triggers
        assert len(triggers) == 1
        assert triggers[0].event == "sidera/manager.run"

    def test_in_all_workflows(self):
        from src.workflows.daily_briefing import all_workflows

        assert manager_runner_workflow in all_workflows

    def test_all_workflows_has_sixteen_entries(self):
        from src.workflows.daily_briefing import all_workflows

        assert len(all_workflows) == 18


# =====================================================================
# Happy path
# =====================================================================


@pytest.mark.asyncio
async def test_manager_runner_full_success():
    """Full happy path: load manager, own skills, delegate, sub-roles, synthesis, Slack."""
    ctx = _make_mock_context(
        event_data={
            "role_id": "head_of_marketing",
            "user_id": "user-42",
            "channel_id": "C0123",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    patches = _build_standard_patches()
    (
        p_registry,
        p_agent,
        p_skill_exec,
        p_role_exec,
        p_slack,
        p_approval,
        mock_registry,
        mock_agent,
        mock_role_executor,
        mock_slack,
    ) = patches

    with p_registry, p_agent, p_skill_exec, p_role_exec, p_slack, p_approval:
        result = await manager_runner_workflow._handler(ctx)

    assert result["role_id"] == "head_of_marketing"
    assert result["user_id"] == "user-42"
    assert result["output_sent"] is True
    assert "media_buyer" in result["sub_roles_activated"]
    assert "analyst" in result["sub_roles_activated"]
    assert len(result["sub_roles_succeeded"]) == 2
    assert len(result["sub_roles_failed"]) == 0
    assert result["synthesis_length"] > 0


# =====================================================================
# Missing role_id
# =====================================================================


@pytest.mark.asyncio
async def test_manager_runner_missing_role_id():
    """Raises NonRetriableError when role_id is empty."""
    ctx = _make_mock_context(event_data={"user_id": "user-42", "accounts": SAMPLE_ACCOUNTS})
    with pytest.raises(inngest.NonRetriableError, match="role_id is required"):
        await manager_runner_workflow._handler(ctx)


# =====================================================================
# Role not found
# =====================================================================


@pytest.mark.asyncio
async def test_manager_runner_role_not_found():
    """Raises NonRetriableError when role_id is not in registry."""
    ctx = _make_mock_context(
        event_data={
            "role_id": "nonexistent",
            "user_id": "user-42",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 5
    mock_registry.get_role.return_value = None

    with patch(
        "src.skills.db_loader.load_registry_with_db",
        new_callable=AsyncMock,
        return_value=mock_registry,
    ):
        with pytest.raises(
            inngest.NonRetriableError,
            match="not found in registry",
        ):
            await manager_runner_workflow._handler(ctx)


# =====================================================================
# Role is not a manager (no manages field)
# =====================================================================


@pytest.mark.asyncio
async def test_manager_runner_not_a_manager():
    """Raises NonRetriableError when role has no manages field."""
    ctx = _make_mock_context(
        event_data={
            "role_id": "plain_role",
            "user_id": "user-42",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    non_manager = _make_mock_role(role_id="plain_role", manages=())

    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 5
    mock_registry.get_role.return_value = non_manager

    with patch(
        "src.skills.db_loader.load_registry_with_db",
        new_callable=AsyncMock,
        return_value=mock_registry,
    ):
        with pytest.raises(
            inngest.NonRetriableError,
            match="not a manager",
        ):
            await manager_runner_workflow._handler(ctx)


# =====================================================================
# No accounts
# =====================================================================


@pytest.mark.asyncio
async def test_manager_runner_no_accounts():
    """Raises NonRetriableError when no accounts configured."""
    ctx = _make_mock_context(
        event_data={
            "role_id": "head_of_marketing",
            "user_id": "user-42",
            "accounts": [],
        }
    )

    patches = _build_standard_patches()
    (
        p_registry,
        p_agent,
        p_skill_exec,
        p_role_exec,
        p_slack,
        p_approval,
        *_mocks,
    ) = patches

    with p_registry, p_agent, p_skill_exec, p_role_exec, p_slack, p_approval:
        with pytest.raises(
            inngest.NonRetriableError,
            match="No accounts configured",
        ):
            await manager_runner_workflow._handler(ctx)


# =====================================================================
# No own briefing_skills (goes straight to delegation)
# =====================================================================


@pytest.mark.asyncio
async def test_manager_runner_no_own_skills():
    """Manager with no briefing_skills skips own skill step, goes to delegation."""
    ctx = _make_mock_context(
        event_data={
            "role_id": "head_of_marketing",
            "user_id": "user-42",
            "channel_id": "C01",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    manager_role = _make_mock_role(briefing_skills=())

    patches = _build_standard_patches(manager_role=manager_role)
    (
        p_registry,
        p_agent,
        p_skill_exec,
        p_role_exec,
        p_slack,
        p_approval,
        mock_registry,
        mock_agent,
        mock_role_executor,
        mock_slack,
    ) = patches

    with p_registry, p_agent, p_skill_exec, p_role_exec, p_slack, p_approval:
        result = await manager_runner_workflow._handler(ctx)

    assert result["own_skills_run"] == 0
    assert result["output_sent"] is True

    # Verify run-own-skills step was NOT called
    step_ids = [call.args[0] for call in ctx.step.run.call_args_list]
    assert "run-own-skills" not in step_ids
    assert "delegation-decision" in step_ids


# =====================================================================
# Delegation fallback when LLM call fails
# =====================================================================


@pytest.mark.asyncio
async def test_manager_runner_delegation_fallback():
    """When delegation decision LLM fails, all sub-roles are activated."""
    ctx = _make_mock_context(
        event_data={
            "role_id": "head_of_marketing",
            "user_id": "user-42",
            "channel_id": "C01",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    patches = _build_standard_patches()
    (
        p_registry,
        p_agent,
        p_skill_exec,
        p_role_exec,
        p_slack,
        p_approval,
        mock_registry,
        mock_agent,
        mock_role_executor,
        mock_slack,
    ) = patches

    # Make delegation call raise
    mock_agent.run_delegation_decision = AsyncMock(
        side_effect=RuntimeError("LLM timeout"),
    )

    with p_registry, p_agent, p_skill_exec, p_role_exec, p_slack, p_approval:
        result = await manager_runner_workflow._handler(ctx)

    # Fallback activates all managed roles
    assert "media_buyer" in result["sub_roles_activated"]
    assert "analyst" in result["sub_roles_activated"]
    assert result["output_sent"] is True


# =====================================================================
# Sub-role error handling (one fails, others continue)
# =====================================================================


@pytest.mark.asyncio
async def test_manager_runner_sub_role_partial_failure():
    """When one sub-role fails, others succeed and synthesis notes error."""
    ctx = _make_mock_context(
        event_data={
            "role_id": "head_of_marketing",
            "user_id": "user-42",
            "channel_id": "C01",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    # Create role executor that fails on analyst but succeeds on media_buyer
    sub_results = {
        "media_buyer": _make_mock_role_result(role_id="media_buyer"),
    }

    patches = _build_standard_patches(sub_role_results=sub_results)
    (
        p_registry,
        p_agent,
        p_skill_exec,
        p_role_exec,
        p_slack,
        p_approval,
        mock_registry,
        mock_agent,
        mock_role_executor,
        mock_slack,
    ) = patches

    call_count = 0

    async def _failing_execute(role_id, user_id, accounts, **kwargs):
        nonlocal call_count
        call_count += 1
        if role_id == "analyst":
            raise RuntimeError("Analyst connector offline")
        return sub_results.get(role_id, _make_mock_role_result(role_id=role_id))

    mock_role_executor.execute_role = AsyncMock(side_effect=_failing_execute)

    with p_registry, p_agent, p_skill_exec, p_role_exec, p_slack, p_approval:
        result = await manager_runner_workflow._handler(ctx)

    assert "media_buyer" in result["sub_roles_succeeded"]
    assert "analyst" in result["sub_roles_failed"]
    assert result["output_sent"] is True


# =====================================================================
# Step IDs
# =====================================================================


@pytest.mark.asyncio
async def test_manager_runner_step_ids():
    """Step IDs include the expected fixed strings."""
    ctx = _make_mock_context(
        event_data={
            "role_id": "head_of_marketing",
            "user_id": "user-42",
            "channel_id": "C01",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    patches = _build_standard_patches()
    (
        p_registry,
        p_agent,
        p_skill_exec,
        p_role_exec,
        p_slack,
        p_approval,
        *_mocks,
    ) = patches

    with p_registry, p_agent, p_skill_exec, p_role_exec, p_slack, p_approval:
        await manager_runner_workflow._handler(ctx)

    step_ids = [call.args[0] for call in ctx.step.run.call_args_list]
    assert "load-manager" in step_ids
    assert "load-accounts" in step_ids
    assert "run-own-skills" in step_ids
    assert "delegation-decision" in step_ids
    assert "synthesis" in step_ids
    assert "store-results" in step_ids
    assert "send-briefing" in step_ids
    assert "save-memory" in step_ids


# =====================================================================
# Sub-role step IDs are unique per role
# =====================================================================


@pytest.mark.asyncio
async def test_manager_runner_sub_role_step_ids():
    """Each sub-role gets its own step ID: run-sub-role-{id}."""
    ctx = _make_mock_context(
        event_data={
            "role_id": "head_of_marketing",
            "user_id": "user-42",
            "channel_id": "C01",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    patches = _build_standard_patches()
    (
        p_registry,
        p_agent,
        p_skill_exec,
        p_role_exec,
        p_slack,
        p_approval,
        *_mocks,
    ) = patches

    with p_registry, p_agent, p_skill_exec, p_role_exec, p_slack, p_approval:
        await manager_runner_workflow._handler(ctx)

    step_ids = [call.args[0] for call in ctx.step.run.call_args_list]
    assert "run-sub-role-media_buyer" in step_ids
    assert "run-sub-role-analyst" in step_ids


# =====================================================================
# Store results and Slack notification
# =====================================================================


@pytest.mark.asyncio
async def test_manager_runner_stores_results_and_notifies():
    """Verifies store-results and send-briefing steps are executed."""
    ctx = _make_mock_context(
        event_data={
            "role_id": "head_of_marketing",
            "user_id": "user-42",
            "channel_id": "C01",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    patches = _build_standard_patches()
    (
        p_registry,
        p_agent,
        p_skill_exec,
        p_role_exec,
        p_slack,
        p_approval,
        mock_registry,
        mock_agent,
        mock_role_executor,
        mock_slack,
    ) = patches

    with p_registry, p_agent, p_skill_exec, p_role_exec, p_slack, p_approval:
        result = await manager_runner_workflow._handler(ctx)

    assert result["output_sent"] is True

    step_ids = [call.args[0] for call in ctx.step.run.call_args_list]
    assert "store-results" in step_ids
    assert "send-briefing" in step_ids


# =====================================================================
# Delegation skips some roles
# =====================================================================


@pytest.mark.asyncio
async def test_manager_runner_delegation_skips_role():
    """When delegation skips a role, that sub-role is not executed."""
    ctx = _make_mock_context(
        event_data={
            "role_id": "head_of_marketing",
            "user_id": "user-42",
            "channel_id": "C01",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    delegation = {
        "activate": [
            {"role_id": "media_buyer", "reason": "Active campaigns", "priority": 1},
        ],
        "skip": [
            {"role_id": "analyst", "reason": "No data changes"},
        ],
    }

    patches = _build_standard_patches(delegation_decision=delegation)
    (
        p_registry,
        p_agent,
        p_skill_exec,
        p_role_exec,
        p_slack,
        p_approval,
        *_mocks,
    ) = patches

    with p_registry, p_agent, p_skill_exec, p_role_exec, p_slack, p_approval:
        result = await manager_runner_workflow._handler(ctx)

    assert result["sub_roles_activated"] == ["media_buyer"]
    step_ids = [call.args[0] for call in ctx.step.run.call_args_list]
    assert "run-sub-role-media_buyer" in step_ids
    assert "run-sub-role-analyst" not in step_ids


# =====================================================================
# Recommendations go through approval flow
# =====================================================================


@pytest.mark.asyncio
async def test_manager_runner_processes_recommendations():
    """Recommendations from own skills + sub-roles go through approval."""
    ctx = _make_mock_context(
        event_data={
            "role_id": "head_of_marketing",
            "user_id": "user-42",
            "channel_id": "C01",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    patches = _build_standard_patches()
    (
        p_registry,
        p_agent,
        p_skill_exec,
        p_role_exec,
        p_slack,
        p_approval,
        *_mocks,
    ) = patches

    with p_registry, p_agent, p_skill_exec, p_role_exec, p_slack, p_approval:
        result = await manager_runner_workflow._handler(ctx)

    # Own skills produce 1 rec, media_buyer produces 1 rec, analyst 0
    assert result["recommendations_count"] == 2
    step_ids = [call.args[0] for call in ctx.step.run.call_args_list]
    assert "process-recommendations" in step_ids


# =====================================================================
# No recommendations skips approval flow
# =====================================================================


@pytest.mark.asyncio
async def test_manager_runner_no_recommendations():
    """When no recommendations exist, approval step is skipped."""
    ctx = _make_mock_context(
        event_data={
            "role_id": "head_of_marketing",
            "user_id": "user-42",
            "channel_id": "C01",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    # All results have no recommendations
    own_result = _make_mock_role_result(
        role_id="head_of_marketing",
        with_recommendations=False,
    )
    sub_results = {
        "media_buyer": _make_mock_role_result(
            role_id="media_buyer",
            with_recommendations=False,
        ),
        "analyst": _make_mock_role_result(
            role_id="analyst",
            with_recommendations=False,
        ),
    }

    patches = _build_standard_patches(
        own_role_result=own_result,
        sub_role_results=sub_results,
    )
    (
        p_registry,
        p_agent,
        p_skill_exec,
        p_role_exec,
        p_slack,
        p_approval,
        *_mocks,
    ) = patches

    with p_registry, p_agent, p_skill_exec, p_role_exec, p_slack, p_approval:
        result = await manager_runner_workflow._handler(ctx)

    assert result["recommendations_count"] == 0
    step_ids = [call.args[0] for call in ctx.step.run.call_args_list]
    assert "process-recommendations" not in step_ids


# =====================================================================
# Default user_id
# =====================================================================


@pytest.mark.asyncio
async def test_manager_runner_default_user_id():
    """When user_id is missing, defaults to 'default'."""
    ctx = _make_mock_context(
        event_data={
            "role_id": "head_of_marketing",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    patches = _build_standard_patches()
    (
        p_registry,
        p_agent,
        p_skill_exec,
        p_role_exec,
        p_slack,
        p_approval,
        *_mocks,
    ) = patches

    with p_registry, p_agent, p_skill_exec, p_role_exec, p_slack, p_approval:
        result = await manager_runner_workflow._handler(ctx)

    assert result["user_id"] == "default"


# =====================================================================
# DLQ recording on unhandled exception
# =====================================================================


@pytest.mark.asyncio
async def test_manager_runner_dlq_on_error():
    """Unhandled exceptions are re-raised and DLQ recording is attempted."""
    ctx = _make_mock_context(
        event_data={
            "role_id": "head_of_marketing",
            "user_id": "user-42",
            "accounts": SAMPLE_ACCOUNTS,
        }
    )

    mock_registry = MagicMock()
    mock_registry.load_all.return_value = 5
    mock_registry.get_role.side_effect = RuntimeError("Registry exploded")

    with (
        patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch("src.middleware.sentry_setup.capture_exception"),
        patch("src.db.service.record_failed_run", new_callable=AsyncMock),
        patch("src.db.session.get_db_session"),
    ):
        with pytest.raises(RuntimeError, match="Registry exploded"):
            await manager_runner_workflow._handler(ctx)
