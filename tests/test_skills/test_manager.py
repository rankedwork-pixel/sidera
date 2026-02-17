"""Tests for ManagerExecutor and ManagerResult.

Covers:
- ManagerResult dataclass construction and defaults
- ManagerExecutor.execute_manager happy path (all 4 phases)
- Phase 1: own skills execution (with and without briefing_skills)
- Phase 2: delegation decision parsing (valid JSON, invalid JSON, fallback)
- Phase 3: sub-role execution with errors (one fails, others continue)
- Phase 4: synthesis includes all results
- Memory loading and saving
- Manager with no own briefing_skills (skips Phase 1)
- Manager with empty manages (edge case -> NotAManagerError)
- Manager role not found (ManagerRoleNotFoundError)
- delegation_model routing (standard -> sonnet, fast -> haiku)
- _format_own_results_summary and _format_sub_role_results helpers
- _parse_delegation_decision validation
- _resolve_delegation_model mapping
- _merge_phase_cost aggregation
- Synthesis failure propagates exception
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.skills.executor import RoleResult, SkillResult
from src.skills.manager import (
    ManagerExecutor,
    ManagerResult,
    ManagerRoleNotFoundError,
    NotAManagerError,
    _format_own_results_summary,
    _format_sub_role_results,
    _merge_phase_cost,
    _parse_delegation_decision,
    _resolve_delegation_model,
)
from src.skills.schema import RoleDefinition

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ACCOUNTS = [{"platform": "meta", "account_id": "act_1"}]
_ANALYSIS_DATE = date(2025, 1, 15)


def _make_skill_result(
    skill_id: str = "s1",
    output: str = "Output for skill",
    cost_usd: float = 0.10,
) -> SkillResult:
    return SkillResult(
        skill_id=skill_id,
        user_id="user_1",
        output_text=output,
        cost={"total_cost_usd": cost_usd, "num_turns": 3, "duration_ms": 500},
        session_id="sess_1",
    )


def _make_role_result(
    role_id: str = "buyer",
    department_id: str = "marketing",
    skill_results: list[SkillResult] | None = None,
    combined_output: str = "Combined role output",
) -> RoleResult:
    results = skill_results or [_make_skill_result()]
    return RoleResult(
        role_id=role_id,
        department_id=department_id,
        user_id="user_1",
        skill_results=results,
        combined_output=combined_output,
        total_cost={
            "total_cost_usd": 0.10,
            "num_turns": 3,
            "duration_ms": 500,
            "skill_costs": {},
        },
        session_id="sess_1",
    )


def _make_manager_role(
    role_id: str = "director",
    manages: tuple[str, ...] = ("buyer", "analyst"),
    briefing_skills: tuple[str, ...] = ("overview_skill",),
    delegation_model: str = "standard",
    synthesis_prompt: str = "",
    persona: str = "You are the marketing director.",
) -> RoleDefinition:
    return RoleDefinition(
        id=role_id,
        name="Marketing Director",
        department_id="marketing",
        description="Oversees all marketing roles",
        persona=persona,
        briefing_skills=briefing_skills,
        manages=manages,
        delegation_model=delegation_model,
        synthesis_prompt=synthesis_prompt,
    )


def _make_managed_role(
    role_id: str = "buyer",
    briefing_skills: tuple[str, ...] = ("s1",),
) -> RoleDefinition:
    return RoleDefinition(
        id=role_id,
        name=f"Role {role_id}",
        department_id="marketing",
        description=f"Description for {role_id}",
        briefing_skills=briefing_skills,
    )


def _build_executor(
    manager_role: RoleDefinition | None = None,
    managed_roles: list[RoleDefinition] | None = None,
    own_role_result: RoleResult | None = None,
    sub_role_results: list[RoleResult] | None = None,
    delegation_decision: dict | None = None,
    synthesis_text: str = "Unified synthesis output.",
    delegation_raises: Exception | None = None,
    synthesis_raises: Exception | None = None,
) -> tuple[ManagerExecutor, MagicMock, MagicMock, MagicMock]:
    """Build a ManagerExecutor with mocked dependencies.

    Returns (executor, mock_skill_exec, mock_role_exec, mock_registry).
    """
    if manager_role is None:
        manager_role = _make_manager_role()

    if managed_roles is None:
        managed_roles = [
            _make_managed_role("buyer"),
            _make_managed_role("analyst"),
        ]

    if own_role_result is None:
        own_role_result = _make_role_result(
            role_id=manager_role.id,
            combined_output="Manager's own analysis output.",
        )

    if sub_role_results is None:
        sub_role_results = [
            _make_role_result("buyer", combined_output="Buyer report."),
            _make_role_result("analyst", combined_output="Analyst report."),
        ]

    if delegation_decision is None:
        delegation_decision = {
            "activate": [
                {"role_id": "buyer", "reason": "relevant", "priority": 1},
                {"role_id": "analyst", "reason": "relevant", "priority": 2},
            ],
            "skip": [],
        }

    # Mock agent
    mock_agent = MagicMock()

    if delegation_raises:
        mock_agent.run_delegation_decision = AsyncMock(
            side_effect=delegation_raises,
        )
    else:
        mock_agent.run_delegation_decision = AsyncMock(
            return_value=delegation_decision,
        )

    if synthesis_raises:
        mock_agent.run_synthesis = AsyncMock(side_effect=synthesis_raises)
    else:
        mock_agent.run_synthesis = AsyncMock(return_value=synthesis_text)

    # Mock skill executor (just needs _agent attribute)
    mock_skill_exec = MagicMock()
    mock_skill_exec._agent = mock_agent

    # Mock role executor
    mock_role_exec = MagicMock()
    # First call = own role, subsequent = sub-roles
    all_results = [own_role_result, *sub_role_results]
    mock_role_exec.execute_role = AsyncMock(side_effect=all_results)

    # Mock registry
    mock_registry = MagicMock()
    mock_registry.get_role.return_value = manager_role
    mock_registry.get_managed_roles.return_value = managed_roles

    executor = ManagerExecutor(mock_skill_exec, mock_role_exec, mock_registry)
    return executor, mock_skill_exec, mock_role_exec, mock_registry


# ===========================================================================
# 1. ManagerResult dataclass
# ===========================================================================


class TestManagerResult:
    """Test ManagerResult construction and defaults."""

    def test_basic_construction(self):
        result = ManagerResult(role_id="director")
        assert result.role_id == "director"
        assert result.own_skill_results == []
        assert result.delegation_decision == {}
        assert result.sub_role_results == {}
        assert result.synthesis == ""
        assert result.total_cost == {}
        assert result.skipped_roles == []

    def test_full_construction(self):
        sr = _make_skill_result()
        rr = _make_role_result()
        result = ManagerResult(
            role_id="director",
            own_skill_results=[sr],
            delegation_decision={"activate": [], "skip": []},
            sub_role_results={"buyer": rr},
            synthesis="Synthesis text.",
            total_cost={"total_cost_usd": 0.50},
            skipped_roles=["analyst"],
        )
        assert result.role_id == "director"
        assert len(result.own_skill_results) == 1
        assert "buyer" in result.sub_role_results
        assert result.synthesis == "Synthesis text."
        assert result.skipped_roles == ["analyst"]


# ===========================================================================
# 2. Exception classes
# ===========================================================================


class TestExceptions:
    def test_manager_role_not_found_error(self):
        assert issubclass(ManagerRoleNotFoundError, Exception)
        err = ManagerRoleNotFoundError("director not found")
        assert "director" in str(err)

    def test_not_a_manager_error(self):
        assert issubclass(NotAManagerError, Exception)
        err = NotAManagerError("buyer is not a manager")
        assert "buyer" in str(err)


# ===========================================================================
# 3. Happy path — full execution
# ===========================================================================


class TestExecuteManagerHappyPath:
    """Test the full four-phase execution."""

    async def test_full_pipeline(self):
        executor, _, mock_role_exec, _ = _build_executor()

        result = await executor.execute_manager(
            role_id="director",
            user_id="user_1",
            accounts=_ACCOUNTS,
            analysis_date=_ANALYSIS_DATE,
        )

        assert isinstance(result, ManagerResult)
        assert result.role_id == "director"
        assert result.synthesis == "Unified synthesis output."
        assert len(result.own_skill_results) > 0
        assert "buyer" in result.sub_role_results
        assert "analyst" in result.sub_role_results
        assert result.skipped_roles == []

        # Role executor called 3 times: own + buyer + analyst
        assert mock_role_exec.execute_role.call_count == 3

    async def test_total_cost_aggregated(self):
        executor, _, _, _ = _build_executor()

        result = await executor.execute_manager(
            role_id="director",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        # 3 role executions x 0.10 each = 0.30
        assert result.total_cost["total_cost_usd"] == pytest.approx(0.30)
        assert "phase_costs" in result.total_cost

    async def test_delegation_decision_stored(self):
        decision = {
            "activate": [{"role_id": "buyer", "reason": "needed", "priority": 1}],
            "skip": [{"role_id": "analyst", "reason": "not needed"}],
        }
        executor, _, _, _ = _build_executor(
            delegation_decision=decision,
            sub_role_results=[
                _make_role_result("buyer", combined_output="Buyer report."),
            ],
        )

        result = await executor.execute_manager(
            role_id="director",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        assert result.delegation_decision == decision
        assert "buyer" in result.sub_role_results
        assert result.skipped_roles == ["analyst"]

    async def test_analysis_date_defaults_to_today(self):
        executor, _, mock_role_exec, _ = _build_executor()

        await executor.execute_manager(
            role_id="director",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        # Verify role_executor was called (at least for own skills)
        assert mock_role_exec.execute_role.call_count >= 1


# ===========================================================================
# 4. Phase 1 — own skills
# ===========================================================================


class TestPhase1OwnSkills:
    """Phase 1: manager's own briefing_skills."""

    async def test_no_own_skills_skips_phase1(self):
        manager_role = _make_manager_role(briefing_skills=())
        executor, _, mock_role_exec, _ = _build_executor(
            manager_role=manager_role,
            sub_role_results=[
                _make_role_result("buyer", combined_output="Buyer report."),
                _make_role_result("analyst", combined_output="Analyst report."),
            ],
        )

        # Override: with no briefing_skills, role_exec is only called
        # for sub-roles, not for own skills
        mock_role_exec.execute_role = AsyncMock(
            side_effect=[
                _make_role_result("buyer", combined_output="Buyer report."),
                _make_role_result("analyst", combined_output="Analyst report."),
            ]
        )

        result = await executor.execute_manager(
            role_id="director",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        assert result.own_skill_results == []
        # Only 2 calls (buyer + analyst), no own-skill call
        assert mock_role_exec.execute_role.call_count == 2

    async def test_with_own_skills(self):
        own_sr = _make_skill_result("overview_skill", "Manager overview output.")
        own_rr = _make_role_result(
            "director",
            skill_results=[own_sr],
            combined_output="Manager overview.",
        )
        executor, _, _, _ = _build_executor(own_role_result=own_rr)

        result = await executor.execute_manager(
            role_id="director",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        assert len(result.own_skill_results) == 1
        assert result.own_skill_results[0].skill_id == "overview_skill"

    async def test_memory_context_passed_to_own_skills(self):
        executor, _, mock_role_exec, _ = _build_executor()

        await executor.execute_manager(
            role_id="director",
            user_id="user_1",
            accounts=_ACCOUNTS,
            memory_context="Previous run: CPA spiked 20%.",
        )

        # First call is for own skills — check memory_context
        first_call = mock_role_exec.execute_role.call_args_list[0]
        assert first_call.kwargs.get("memory_context") == "Previous run: CPA spiked 20%."


# ===========================================================================
# 5. Phase 2 — delegation decision parsing
# ===========================================================================


class TestPhase2DelegationDecision:
    """Phase 2: delegation decision parsing and validation."""

    async def test_valid_delegation_activates_subset(self):
        decision = {
            "activate": [{"role_id": "buyer", "reason": "focus"}],
            "skip": [{"role_id": "analyst", "reason": "stable"}],
        }
        executor, _, mock_role_exec, _ = _build_executor(
            delegation_decision=decision,
            sub_role_results=[
                _make_role_result("buyer", combined_output="Buyer report."),
            ],
        )

        # Override role_exec: 1 own + 1 sub (buyer only)
        mock_role_exec.execute_role = AsyncMock(
            side_effect=[
                _make_role_result("director", combined_output="Own analysis."),
                _make_role_result("buyer", combined_output="Buyer report."),
            ]
        )

        result = await executor.execute_manager(
            role_id="director",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        assert "buyer" in result.sub_role_results
        assert "analyst" not in result.sub_role_results
        assert "analyst" in result.skipped_roles

    async def test_delegation_failure_activates_all(self):
        executor, _, _, _ = _build_executor(
            delegation_raises=RuntimeError("LLM call failed"),
        )

        result = await executor.execute_manager(
            role_id="director",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        # Fallback: all roles activated
        assert "buyer" in result.sub_role_results
        assert "analyst" in result.sub_role_results

    async def test_delegation_with_unknown_role_ids_ignored(self):
        decision = {
            "activate": [
                {"role_id": "buyer", "reason": "ok"},
                {"role_id": "nonexistent", "reason": "bad"},
            ],
            "skip": [],
        }
        executor, _, mock_role_exec, _ = _build_executor(
            delegation_decision=decision,
            sub_role_results=[
                _make_role_result("buyer", combined_output="Buyer."),
            ],
        )

        # Own skills + buyer only (nonexistent ignored)
        mock_role_exec.execute_role = AsyncMock(
            side_effect=[
                _make_role_result("director", combined_output="Own."),
                _make_role_result("buyer", combined_output="Buyer."),
            ]
        )

        result = await executor.execute_manager(
            role_id="director",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        assert "buyer" in result.sub_role_results
        assert "nonexistent" not in result.sub_role_results


# ===========================================================================
# 6. Phase 3 — sub-role execution with errors
# ===========================================================================


class TestPhase3SubRoleExecution:
    """Phase 3: sub-role execution error handling."""

    async def test_one_sub_role_fails_others_continue(self):
        executor, _, mock_role_exec, _ = _build_executor()

        # Own -> buyer fails -> analyst succeeds
        mock_role_exec.execute_role = AsyncMock(
            side_effect=[
                _make_role_result("director", combined_output="Own."),
                RuntimeError("buyer failed"),
                _make_role_result("analyst", combined_output="Analyst report."),
            ]
        )

        result = await executor.execute_manager(
            role_id="director",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        assert "buyer" not in result.sub_role_results
        assert "analyst" in result.sub_role_results

    async def test_all_sub_roles_fail(self):
        executor, _, mock_role_exec, _ = _build_executor()

        # Own -> buyer fails -> analyst fails
        mock_role_exec.execute_role = AsyncMock(
            side_effect=[
                _make_role_result("director", combined_output="Own."),
                RuntimeError("buyer failed"),
                RuntimeError("analyst failed"),
            ]
        )

        result = await executor.execute_manager(
            role_id="director",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        assert result.sub_role_results == {}
        # Synthesis should still run (with empty sub-role results)
        assert result.synthesis == "Unified synthesis output."


# ===========================================================================
# 7. Phase 4 — synthesis
# ===========================================================================


class TestPhase4Synthesis:
    """Phase 4: synthesis includes all results."""

    async def test_synthesis_called_with_all_context(self):
        executor, mock_skill_exec, _, _ = _build_executor(
            synthesis_text="Cross-cutting insight: budget reallocation needed.",
        )

        result = await executor.execute_manager(
            role_id="director",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        assert result.synthesis == "Cross-cutting insight: budget reallocation needed."

        # Verify the agent's run_synthesis was called
        mock_agent = mock_skill_exec._agent
        mock_agent.run_synthesis.assert_called_once()
        call_kwargs = mock_agent.run_synthesis.call_args.kwargs
        assert call_kwargs["manager_name"] == "Marketing Director"
        assert "own_results" in call_kwargs
        assert "sub_role_results" in call_kwargs

    async def test_synthesis_failure_propagates(self):
        executor, _, _, _ = _build_executor(
            synthesis_raises=RuntimeError("Synthesis LLM failed"),
        )

        with pytest.raises(RuntimeError, match="Synthesis LLM failed"):
            await executor.execute_manager(
                role_id="director",
                user_id="user_1",
                accounts=_ACCOUNTS,
            )

    async def test_synthesis_with_custom_prompt(self):
        manager_role = _make_manager_role(
            synthesis_prompt="Focus on budget allocation efficiency.",
        )
        executor, mock_skill_exec, _, _ = _build_executor(
            manager_role=manager_role,
        )

        await executor.execute_manager(
            role_id="director",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        mock_agent = mock_skill_exec._agent
        call_kwargs = mock_agent.run_synthesis.call_args.kwargs
        assert call_kwargs["synthesis_prompt"] == "Focus on budget allocation efficiency."


# ===========================================================================
# 8. Memory integration
# ===========================================================================


class TestMemoryIntegration:
    """Memory loading and saving."""

    async def test_memory_context_passed_when_provided(self):
        executor, _, mock_role_exec, _ = _build_executor()

        await executor.execute_manager(
            role_id="director",
            user_id="user_1",
            accounts=_ACCOUNTS,
            memory_context="Previous: CPA was high.",
        )

        # Memory should be passed to own skills execution
        first_call = mock_role_exec.execute_role.call_args_list[0]
        assert "Previous: CPA was high." in first_call.kwargs.get("memory_context", "")

    async def test_memory_extraction_called(self):
        executor, _, _, _ = _build_executor()

        with patch("src.skills.manager.extract_memories_from_results") as mock_extract:
            mock_extract.return_value = [
                {"memory_type": "anomaly", "content": "CPA spike detected"},
            ]

            await executor.execute_manager(
                role_id="director",
                user_id="user_1",
                accounts=_ACCOUNTS,
            )

            mock_extract.assert_called_once()
            call_kwargs = mock_extract.call_args.kwargs
            assert call_kwargs["role_id"] == "director"
            assert call_kwargs["department_id"] == "marketing"

    async def test_memory_save_failure_does_not_crash(self):
        executor, _, _, _ = _build_executor()

        with patch(
            "src.skills.manager.extract_memories_from_results",
            side_effect=RuntimeError("Memory extraction failed"),
        ):
            # Should not raise — memory save failures are swallowed
            result = await executor.execute_manager(
                role_id="director",
                user_id="user_1",
                accounts=_ACCOUNTS,
            )

            assert result.synthesis == "Unified synthesis output."

    async def test_memory_load_returns_empty_on_error(self):
        executor, _, _, _ = _build_executor()

        with patch(
            "src.skills.manager.compose_memory_context",
            side_effect=RuntimeError("Redis down"),
        ):
            # Should not raise — fallback to empty memory
            result = await executor.execute_manager(
                role_id="director",
                user_id="user_1",
                accounts=_ACCOUNTS,
            )

            assert isinstance(result, ManagerResult)


# ===========================================================================
# 9. Edge cases
# ===========================================================================


class TestEdgeCases:
    """Edge cases and error handling."""

    async def test_manager_role_not_found(self):
        mock_registry = MagicMock()
        mock_registry.get_role.return_value = None

        executor = ManagerExecutor(MagicMock(), MagicMock(), mock_registry)

        with pytest.raises(ManagerRoleNotFoundError, match="nonexistent"):
            await executor.execute_manager(
                role_id="nonexistent",
                user_id="user_1",
                accounts=_ACCOUNTS,
            )

    async def test_not_a_manager(self):
        non_manager = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="marketing",
            description="A regular role",
            briefing_skills=("s1",),
            manages=(),  # empty = not a manager
        )
        mock_registry = MagicMock()
        mock_registry.get_role.return_value = non_manager

        executor = ManagerExecutor(MagicMock(), MagicMock(), mock_registry)

        with pytest.raises(NotAManagerError, match="buyer"):
            await executor.execute_manager(
                role_id="buyer",
                user_id="user_1",
                accounts=_ACCOUNTS,
            )

    async def test_no_managed_roles_in_registry(self):
        """Manager declares manages but none exist in registry."""
        executor, _, mock_role_exec, mock_registry = _build_executor()
        mock_registry.get_managed_roles.return_value = []

        # Only own skills call
        mock_role_exec.execute_role = AsyncMock(
            return_value=_make_role_result("director", combined_output="Own."),
        )

        result = await executor.execute_manager(
            role_id="director",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        assert result.sub_role_results == {}
        assert result.skipped_roles == []
        # Only 1 call for own skills
        assert mock_role_exec.execute_role.call_count == 1


# ===========================================================================
# 10. Delegation model routing
# ===========================================================================


class TestDelegationModelRouting:
    """delegation_model routes to correct model."""

    async def test_standard_model_uses_sonnet(self):
        manager_role = _make_manager_role(delegation_model="standard")
        executor, mock_skill_exec, _, _ = _build_executor(
            manager_role=manager_role,
        )

        await executor.execute_manager(
            role_id="director",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        mock_agent = mock_skill_exec._agent
        call_kwargs = mock_agent.run_delegation_decision.call_args.kwargs
        # "standard" should resolve to settings.model_standard
        assert call_kwargs["model"] is not None

    async def test_fast_model_uses_haiku(self):
        manager_role = _make_manager_role(delegation_model="fast")
        executor, mock_skill_exec, _, _ = _build_executor(
            manager_role=manager_role,
        )

        with patch("src.config.settings") as mock_settings:
            mock_settings.model_fast = "claude-3-haiku-20241022"
            mock_settings.model_standard = "claude-sonnet-4-20250514"

            await executor.execute_manager(
                role_id="director",
                user_id="user_1",
                accounts=_ACCOUNTS,
            )

            mock_agent = mock_skill_exec._agent
            call_kwargs = mock_agent.run_delegation_decision.call_args.kwargs
            assert call_kwargs["model"] == "claude-3-haiku-20241022"


# ===========================================================================
# 11. Helper functions
# ===========================================================================


class TestFormatOwnResultsSummary:
    """Test _format_own_results_summary."""

    def test_with_results(self):
        results = [
            _make_skill_result("s1", "Short output."),
            _make_skill_result("s2", "Another output."),
        ]
        summary = _format_own_results_summary("director", results)
        assert "director" in summary
        assert "s1" in summary
        assert "s2" in summary
        assert "Short output." in summary

    def test_empty_results(self):
        summary = _format_own_results_summary("director", [])
        assert "no own analysis results" in summary

    def test_long_output_truncated(self):
        long_output = "x" * 1000
        results = [_make_skill_result("s1", long_output)]
        summary = _format_own_results_summary("director", results)
        assert "..." in summary
        # Should be truncated to ~500 chars + "..."
        assert len(summary) < 600


class TestFormatSubRoleResults:
    """Test _format_sub_role_results."""

    def test_with_results(self):
        sub_results = {
            "buyer": _make_role_result("buyer", combined_output="Buyer analysis."),
            "analyst": _make_role_result("analyst", combined_output="Analyst analysis."),
        }
        text = _format_sub_role_results(sub_results)
        assert "buyer" in text
        assert "analyst" in text
        assert "Buyer analysis." in text
        assert "Analyst analysis." in text

    def test_empty_results(self):
        text = _format_sub_role_results({})
        assert "No sub-role reports available" in text


class TestParseDelegationDecision:
    """Test _parse_delegation_decision validation."""

    def test_valid_decision(self):
        decision = {
            "activate": [
                {"role_id": "buyer", "reason": "needed"},
                {"role_id": "analyst", "reason": "needed"},
            ],
            "skip": [],
        }
        activated, skipped = _parse_delegation_decision(
            decision,
            {"buyer", "analyst"},
        )
        assert activated == ["buyer", "analyst"]
        assert skipped == []

    def test_unknown_ids_filtered(self):
        decision = {
            "activate": [
                {"role_id": "buyer", "reason": "ok"},
                {"role_id": "fake", "reason": "bad"},
            ],
            "skip": [{"role_id": "nonexistent", "reason": "skip"}],
        }
        activated, skipped = _parse_delegation_decision(
            decision,
            {"buyer", "analyst"},
        )
        assert activated == ["buyer"]
        assert skipped == []

    def test_skip_with_valid_ids(self):
        decision = {
            "activate": [{"role_id": "buyer", "reason": "ok"}],
            "skip": [{"role_id": "analyst", "reason": "stable metrics"}],
        }
        activated, skipped = _parse_delegation_decision(
            decision,
            {"buyer", "analyst"},
        )
        assert activated == ["buyer"]
        assert skipped == ["analyst"]

    def test_empty_decision(self):
        decision = {"activate": [], "skip": []}
        activated, skipped = _parse_delegation_decision(
            decision,
            {"buyer", "analyst"},
        )
        assert activated == []
        assert skipped == []

    def test_string_entries_handled(self):
        decision = {
            "activate": ["buyer"],
            "skip": ["analyst"],
        }
        activated, skipped = _parse_delegation_decision(
            decision,
            {"buyer", "analyst"},
        )
        assert activated == ["buyer"]
        assert skipped == ["analyst"]


class TestResolveDelegationModel:
    """Test _resolve_delegation_model."""

    def test_standard_resolves_to_model_standard(self):
        with patch("src.config.settings") as mock_settings:
            mock_settings.model_standard = "claude-sonnet-4-20250514"
            result = _resolve_delegation_model("standard")
            assert result == "claude-sonnet-4-20250514"

    def test_fast_resolves_to_model_fast(self):
        with patch("src.config.settings") as mock_settings:
            mock_settings.model_fast = "claude-3-haiku-20241022"
            result = _resolve_delegation_model("fast")
            assert result == "claude-3-haiku-20241022"

    def test_unknown_defaults_to_standard(self):
        with patch("src.config.settings") as mock_settings:
            mock_settings.model_standard = "claude-sonnet-4-20250514"
            result = _resolve_delegation_model("unknown")
            assert result == "claude-sonnet-4-20250514"


class TestMergePhaseCost:
    """Test _merge_phase_cost aggregation."""

    def test_merges_correctly(self):
        total = {
            "total_cost_usd": 0.10,
            "num_turns": 3,
            "duration_ms": 500,
            "phase_costs": {},
        }
        phase_cost = {
            "total_cost_usd": 0.05,
            "num_turns": 2,
            "duration_ms": 200,
        }
        _merge_phase_cost(total, phase_cost, "phase2")

        assert total["total_cost_usd"] == pytest.approx(0.15)
        assert total["num_turns"] == 5
        assert total["duration_ms"] == 700
        assert total["phase_costs"]["phase2"] == phase_cost

    def test_handles_missing_keys(self):
        total = {
            "total_cost_usd": 0.0,
            "num_turns": 0,
            "duration_ms": 0,
            "phase_costs": {},
        }
        _merge_phase_cost(total, {}, "empty_phase")

        assert total["total_cost_usd"] == 0.0
        assert total["num_turns"] == 0
        assert total["duration_ms"] == 0
        assert total["phase_costs"]["empty_phase"] == {}
