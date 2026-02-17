"""Tests for hierarchy execution: RoleExecutor, DepartmentExecutor, compose_role_context.

Covers:
- compose_role_context() with department context, role persona, context files
- RoleExecutor.execute_role() — runs briefing_skills, merges output, aggregates cost
- RoleExecutor error handling — missing skills, failed skills
- DepartmentExecutor.execute_department() — runs all roles, merges output
- DepartmentExecutor error handling — failed roles
- role_context passthrough from SkillExecutor to agent.run_skill
- _merge_skill_outputs / _merge_role_outputs helpers
- RoleNotFoundError / DepartmentNotFoundError exceptions
- RoleResult / DepartmentResult dataclasses
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.skills.executor import (
    DepartmentExecutor,
    DepartmentNotFoundError,
    DepartmentResult,
    RoleExecutor,
    RoleNotFoundError,
    RoleResult,
    SkillExecutor,
    SkillResult,
    _merge_role_outputs,
    _merge_skill_outputs,
    compose_role_context,
)
from src.skills.schema import (
    DepartmentDefinition,
    RoleDefinition,
    SkillDefinition,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ACCOUNTS = [{"platform": "meta", "account_id": "act_1"}]


def _make_skill(skill_id: str = "s1", **kw: object) -> SkillDefinition:
    defaults = {
        "id": skill_id,
        "name": f"Skill {skill_id}",
        "version": "1.0",
        "description": f"Desc {skill_id}",
        "category": "analysis",
        "platforms": ("google_ads",),
        "tags": ("test",),
        "tools_required": ("get_meta_campaigns",),
        "model": "sonnet",
        "max_turns": 10,
        "system_supplement": "Supplement.",
        "prompt_template": "Run analysis for {analysis_date}.",
        "output_format": "## Results",
        "business_guidance": "Guidance.",
    }
    defaults.update(kw)
    return SkillDefinition(**defaults)


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
) -> RoleResult:
    results = skill_results or [_make_skill_result()]
    return RoleResult(
        role_id=role_id,
        department_id=department_id,
        user_id="user_1",
        skill_results=results,
        combined_output="Combined role output",
        total_cost={"total_cost_usd": 0.10, "num_turns": 3, "duration_ms": 500},
        session_id="sess_1",
    )


# ===========================================================================
# 1. compose_role_context
# ===========================================================================


class TestComposeRoleContext:
    """Test compose_role_context() helper."""

    def test_department_context_included(self):
        dept = DepartmentDefinition(
            id="mktg",
            name="Marketing",
            description="D",
            context="Q1 goal: $2M revenue.",
        )
        role = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="D",
            briefing_skills=("s",),
        )
        ctx = compose_role_context(dept, role)
        assert "Q1 goal: $2M revenue" in ctx
        assert "Marketing" in ctx

    def test_role_persona_included(self):
        role = RoleDefinition(
            id="buyer",
            name="Media Buyer",
            department_id="mktg",
            description="D",
            persona="You are a media buyer.",
            briefing_skills=("s",),
        )
        ctx = compose_role_context(None, role)
        assert "You are a media buyer" in ctx
        assert "Media Buyer" in ctx

    def test_both_dept_and_role(self):
        dept = DepartmentDefinition(
            id="mktg",
            name="Marketing",
            description="D",
            context="Department context here.",
        )
        role = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="D",
            persona="Role persona here.",
            briefing_skills=("s",),
        )
        ctx = compose_role_context(dept, role)
        assert "Department context here" in ctx
        assert "Role persona here" in ctx
        # Department should come before role
        dept_pos = ctx.index("Department context")
        role_pos = ctx.index("Role persona")
        assert dept_pos < role_pos

    def test_no_department(self):
        role = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="D",
            persona="Persona.",
            briefing_skills=("s",),
        )
        ctx = compose_role_context(None, role)
        assert "Persona" in ctx

    def test_no_context_returns_empty(self):
        role = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="D",
            briefing_skills=("s",),
        )
        ctx = compose_role_context(None, role)
        assert ctx == ""

    def test_department_context_files(self, tmp_path: Path):
        _write_file(tmp_path / "context" / "info.md", "Dept context file content")
        dept = DepartmentDefinition(
            id="mktg",
            name="Marketing",
            description="D",
            context_files=("context/*.md",),
            source_dir=str(tmp_path),
        )
        role = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="D",
            briefing_skills=("s",),
        )
        ctx = compose_role_context(dept, role)
        assert "Dept context file content" in ctx

    def test_role_context_files(self, tmp_path: Path):
        _write_file(tmp_path / "context" / "role_info.md", "Role context file")
        role = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="D",
            briefing_skills=("s",),
            context_files=("context/*.md",),
            source_dir=str(tmp_path),
        )
        ctx = compose_role_context(None, role)
        assert "Role context file" in ctx


class TestComposeRoleContextTeamAwareness:
    """Test team awareness injection for manager roles."""

    def test_manager_gets_team_section(self):
        """Manager with registry gets 'Your Team' section listing sub-roles."""
        manager = RoleDefinition(
            id="hom",
            name="Head of Marketing",
            department_id="mktg",
            description="Manager",
            manages=("buyer", "analyst"),
            briefing_skills=("strategy",),
            persona="You lead the team.",
        )
        buyer = RoleDefinition(
            id="buyer",
            name="Media Buyer",
            department_id="mktg",
            description="Buys media",
            briefing_skills=("s1",),
            persona="You are a performance media buyer. Expert in PPC.",
        )
        analyst = RoleDefinition(
            id="analyst",
            name="Reporting Analyst",
            department_id="mktg",
            description="Reports",
            briefing_skills=("s2",),
            persona="You analyze data and build reports.",
        )
        skill1 = _make_skill("creative_analysis", name="Creative Analysis", role_id="buyer")
        skill2 = _make_skill("budget_realloc", name="Budget Reallocation", role_id="buyer")
        skill3 = _make_skill("exec_summary", name="Executive Summary", role_id="analyst")

        registry = MagicMock()
        registry.get_role.side_effect = lambda rid: {"buyer": buyer, "analyst": analyst}.get(rid)
        registry.list_skills_for_role.side_effect = lambda rid: {
            "buyer": [skill1, skill2],
            "analyst": [skill3],
        }.get(rid, [])

        ctx = compose_role_context(None, manager, registry=registry)
        assert "Your Team" in ctx
        assert "Media Buyer" in ctx
        assert "Creative Analysis" in ctx
        assert "Budget Reallocation" in ctx
        assert "Reporting Analyst" in ctx
        assert "Executive Summary" in ctx
        # Persona first sentence
        assert "You are a performance media buyer" in ctx
        assert "You analyze data and build reports" in ctx

    def test_no_registry_no_team_section(self):
        """Manager without registry does not get team section (backward compat)."""
        manager = RoleDefinition(
            id="hom",
            name="Head of Marketing",
            department_id="mktg",
            description="Manager",
            manages=("buyer",),
            briefing_skills=("s",),
            persona="Leader.",
        )
        ctx = compose_role_context(None, manager)
        assert "Your Team" not in ctx

    def test_non_manager_no_team_section(self):
        """Non-manager role with registry does not get team section."""
        role = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="D",
            briefing_skills=("s",),
            persona="Buyer.",
        )
        registry = MagicMock()
        registry.list_roles.return_value = []
        ctx = compose_role_context(None, role, registry=registry)
        assert "Your Team" not in ctx

    def test_manager_gets_peer_section(self):
        """Manager with peers gets 'Peer Department Heads' section."""
        from src.skills.schema import DepartmentDefinition

        hom = RoleDefinition(
            id="hom",
            name="Head of Marketing",
            department_id="mktg",
            description="Marketing lead",
            manages=("buyer",),
            briefing_skills=("s",),
            persona="You lead marketing.",
        )
        hoit = RoleDefinition(
            id="hoit",
            name="Head of IT",
            department_id="it",
            description="IT lead",
            manages=("it_engineer",),
            briefing_skills=("s",),
            persona="You lead IT.",
        )
        buyer = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="D",
            briefing_skills=("s",),
        )
        it_dept = DepartmentDefinition(
            id="it",
            name="IT Department",
            description="IT",
        )
        registry = MagicMock()
        registry.get_role.side_effect = lambda rid: {
            "buyer": buyer,
        }.get(rid)
        registry.list_skills_for_role.return_value = []
        registry.list_roles.return_value = [buyer, hom, hoit]
        registry.get_department.side_effect = lambda did: {
            "it": it_dept,
        }.get(did)

        ctx = compose_role_context(None, hom, registry=registry)
        assert "Peer Department Heads" in ctx
        assert "Head of IT" in ctx
        assert "hoit" in ctx
        assert "consult_peer" in ctx

    def test_sole_manager_no_peer_section(self):
        """Manager with no peers doesn't get peer section."""
        hom = RoleDefinition(
            id="hom",
            name="Head of Marketing",
            department_id="mktg",
            description="Manager",
            manages=("buyer",),
            briefing_skills=("s",),
            persona="Leader.",
        )
        buyer = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="D",
            briefing_skills=("s",),
        )
        registry = MagicMock()
        registry.get_role.side_effect = lambda rid: {
            "buyer": buyer,
        }.get(rid)
        registry.list_skills_for_role.return_value = []
        registry.list_roles.return_value = [buyer, hom]

        ctx = compose_role_context(None, hom, registry=registry)
        assert "Peer Department Heads" not in ctx


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ===========================================================================
# 2. RoleResult / DepartmentResult dataclasses
# ===========================================================================


class TestRoleResult:
    def test_defaults(self):
        r = RoleResult(role_id="r", department_id="d", user_id="u")
        assert r.skill_results == []
        assert r.combined_output == ""
        assert r.total_cost == {}
        assert r.session_id == ""


class TestDepartmentResult:
    def test_defaults(self):
        r = DepartmentResult(department_id="d", user_id="u")
        assert r.role_results == []
        assert r.combined_output == ""
        assert r.total_cost == {}


# ===========================================================================
# 3. RoleExecutor — happy path
# ===========================================================================


class TestRoleExecutorHappy:
    """RoleExecutor runs all briefing_skills and merges output."""

    async def test_runs_all_skills(self):
        mock_skill_exec = MagicMock()
        mock_skill_exec.execute = AsyncMock(
            side_effect=[
                _make_skill_result("s1", "Output 1"),
                _make_skill_result("s2", "Output 2"),
            ]
        )

        mock_registry = MagicMock()
        mock_registry.get_role.return_value = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="D",
            briefing_skills=("s1", "s2"),
        )
        mock_registry.get_department.return_value = DepartmentDefinition(
            id="mktg",
            name="Marketing",
            description="D",
        )
        mock_registry.get.side_effect = lambda sid: _make_skill(sid)

        executor = RoleExecutor(mock_skill_exec, mock_registry)
        result = await executor.execute_role(
            "buyer",
            "user_1",
            _ACCOUNTS,
        )

        assert isinstance(result, RoleResult)
        assert len(result.skill_results) == 2
        assert result.role_id == "buyer"
        assert result.department_id == "mktg"
        assert "Output 1" in result.combined_output
        assert "Output 2" in result.combined_output

    async def test_cost_aggregated(self):
        mock_skill_exec = MagicMock()
        mock_skill_exec.execute = AsyncMock(
            side_effect=[
                _make_skill_result("s1", cost_usd=0.10),
                _make_skill_result("s2", cost_usd=0.20),
            ]
        )

        mock_registry = MagicMock()
        mock_registry.get_role.return_value = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="D",
            briefing_skills=("s1", "s2"),
        )
        mock_registry.get_department.return_value = None
        mock_registry.get.side_effect = lambda sid: _make_skill(sid)

        executor = RoleExecutor(mock_skill_exec, mock_registry)
        result = await executor.execute_role(
            "buyer",
            "user_1",
            _ACCOUNTS,
        )

        assert result.total_cost["total_cost_usd"] == pytest.approx(0.30)

    async def test_role_context_passed_to_skills(self):
        mock_skill_exec = MagicMock()
        mock_skill_exec.execute = AsyncMock(
            return_value=_make_skill_result(),
        )

        mock_registry = MagicMock()
        mock_registry.get_role.return_value = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="D",
            persona="You are a buyer.",
            briefing_skills=("s1",),
        )
        mock_registry.get_department.return_value = DepartmentDefinition(
            id="mktg",
            name="Marketing",
            description="D",
            context="Q1 goals.",
        )
        mock_registry.get.side_effect = lambda sid: _make_skill(sid)

        executor = RoleExecutor(mock_skill_exec, mock_registry)
        await executor.execute_role("buyer", "user_1", _ACCOUNTS)

        call_kwargs = mock_skill_exec.execute.call_args.kwargs
        assert "Q1 goals" in call_kwargs["role_context"]
        assert "You are a buyer" in call_kwargs["role_context"]


# ===========================================================================
# 4. RoleExecutor — error handling
# ===========================================================================


class TestRoleExecutorErrors:
    async def test_role_not_found(self):
        mock_registry = MagicMock()
        mock_registry.get_role.return_value = None

        executor = RoleExecutor(MagicMock(), mock_registry)
        with pytest.raises(RoleNotFoundError, match="buyer"):
            await executor.execute_role("buyer", "user_1", _ACCOUNTS)

    async def test_missing_skill_skipped(self):
        mock_skill_exec = MagicMock()
        mock_skill_exec.execute = AsyncMock(
            return_value=_make_skill_result("s2"),
        )

        mock_registry = MagicMock()
        mock_registry.get_role.return_value = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="D",
            briefing_skills=("nonexistent", "s2"),
        )
        mock_registry.get_department.return_value = None
        mock_registry.get.side_effect = lambda sid: _make_skill(sid) if sid == "s2" else None

        executor = RoleExecutor(mock_skill_exec, mock_registry)
        result = await executor.execute_role(
            "buyer",
            "user_1",
            _ACCOUNTS,
        )

        # Only s2 should have been executed
        assert len(result.skill_results) == 1
        assert result.skill_results[0].skill_id == "s2"

    async def test_failed_skill_continues(self):
        mock_skill_exec = MagicMock()
        mock_skill_exec.execute = AsyncMock(
            side_effect=[
                RuntimeError("s1 failed"),
                _make_skill_result("s2", "Output 2"),
            ]
        )

        mock_registry = MagicMock()
        mock_registry.get_role.return_value = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="D",
            briefing_skills=("s1", "s2"),
        )
        mock_registry.get_department.return_value = None
        mock_registry.get.side_effect = lambda sid: _make_skill(sid)

        executor = RoleExecutor(mock_skill_exec, mock_registry)
        result = await executor.execute_role(
            "buyer",
            "user_1",
            _ACCOUNTS,
        )

        # s1 failed, s2 succeeded
        assert len(result.skill_results) == 1
        assert result.skill_results[0].skill_id == "s2"

    async def test_empty_briefing_skills(self):
        mock_skill_exec = MagicMock()
        mock_registry = MagicMock()
        mock_registry.get_role.return_value = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="D",
            briefing_skills=(),
        )
        mock_registry.get_department.return_value = None

        executor = RoleExecutor(mock_skill_exec, mock_registry)
        result = await executor.execute_role(
            "buyer",
            "user_1",
            _ACCOUNTS,
        )

        assert len(result.skill_results) == 0
        assert "No skills produced output" in result.combined_output


# ===========================================================================
# 5. DepartmentExecutor — happy path
# ===========================================================================


class TestDepartmentExecutorHappy:
    async def test_runs_all_roles(self):
        mock_role_exec = MagicMock()
        mock_role_exec.execute_role = AsyncMock(
            side_effect=[
                _make_role_result("buyer"),
                _make_role_result("analyst"),
            ]
        )

        mock_registry = MagicMock()
        mock_registry.get_department.return_value = DepartmentDefinition(
            id="mktg",
            name="Marketing",
            description="D",
        )
        mock_registry.list_roles.return_value = [
            RoleDefinition(
                id="buyer",
                name="Buyer",
                department_id="mktg",
                description="D",
                briefing_skills=("s",),
            ),
            RoleDefinition(
                id="analyst",
                name="Analyst",
                department_id="mktg",
                description="D",
                briefing_skills=("s",),
            ),
        ]

        executor = DepartmentExecutor(mock_role_exec, mock_registry)
        result = await executor.execute_department(
            "mktg",
            "user_1",
            _ACCOUNTS,
        )

        assert isinstance(result, DepartmentResult)
        assert len(result.role_results) == 2
        assert result.department_id == "mktg"

    async def test_cost_aggregated(self):
        mock_role_exec = MagicMock()
        mock_role_exec.execute_role = AsyncMock(
            side_effect=[
                _make_role_result("buyer"),
                _make_role_result("analyst"),
            ]
        )

        mock_registry = MagicMock()
        mock_registry.get_department.return_value = DepartmentDefinition(
            id="mktg",
            name="Marketing",
            description="D",
        )
        mock_registry.list_roles.return_value = [
            RoleDefinition(
                id="buyer",
                name="B",
                department_id="mktg",
                description="D",
                briefing_skills=("s",),
            ),
            RoleDefinition(
                id="analyst",
                name="A",
                department_id="mktg",
                description="D",
                briefing_skills=("s",),
            ),
        ]

        executor = DepartmentExecutor(mock_role_exec, mock_registry)
        result = await executor.execute_department(
            "mktg",
            "user_1",
            _ACCOUNTS,
        )

        # Each role contributes 0.10
        assert result.total_cost["total_cost_usd"] == pytest.approx(0.20)


# ===========================================================================
# 6. DepartmentExecutor — error handling
# ===========================================================================


class TestDepartmentExecutorErrors:
    async def test_department_not_found(self):
        mock_registry = MagicMock()
        mock_registry.get_department.return_value = None

        executor = DepartmentExecutor(MagicMock(), mock_registry)
        with pytest.raises(DepartmentNotFoundError, match="mktg"):
            await executor.execute_department(
                "mktg",
                "user_1",
                _ACCOUNTS,
            )

    async def test_failed_role_continues(self):
        mock_role_exec = MagicMock()
        mock_role_exec.execute_role = AsyncMock(
            side_effect=[
                RuntimeError("buyer failed"),
                _make_role_result("analyst"),
            ]
        )

        mock_registry = MagicMock()
        mock_registry.get_department.return_value = DepartmentDefinition(
            id="mktg",
            name="Marketing",
            description="D",
        )
        mock_registry.list_roles.return_value = [
            RoleDefinition(
                id="buyer",
                name="B",
                department_id="mktg",
                description="D",
                briefing_skills=("s",),
            ),
            RoleDefinition(
                id="analyst",
                name="A",
                department_id="mktg",
                description="D",
                briefing_skills=("s",),
            ),
        ]

        executor = DepartmentExecutor(mock_role_exec, mock_registry)
        result = await executor.execute_department(
            "mktg",
            "user_1",
            _ACCOUNTS,
        )

        # buyer failed, analyst succeeded
        assert len(result.role_results) == 1

    async def test_no_roles(self):
        mock_registry = MagicMock()
        mock_registry.get_department.return_value = DepartmentDefinition(
            id="mktg",
            name="Marketing",
            description="D",
        )
        mock_registry.list_roles.return_value = []

        executor = DepartmentExecutor(MagicMock(), mock_registry)
        result = await executor.execute_department(
            "mktg",
            "user_1",
            _ACCOUNTS,
        )

        assert len(result.role_results) == 0
        assert "No roles produced output" in result.combined_output


# ===========================================================================
# 7. _merge_skill_outputs / _merge_role_outputs
# ===========================================================================


class TestMergeHelpers:
    def test_merge_skill_outputs(self):
        results = [
            _make_skill_result("s1", "Analysis 1"),
            _make_skill_result("s2", "Analysis 2"),
        ]
        output = _merge_skill_outputs("Media Buyer", results)
        assert "# Media Buyer" in output
        assert "## s1" in output
        assert "Analysis 1" in output
        assert "## s2" in output
        assert "Analysis 2" in output

    def test_merge_skill_outputs_empty(self):
        output = _merge_skill_outputs("Media Buyer", [])
        assert "No skills produced output" in output

    def test_merge_role_outputs(self):
        role_results = [
            _make_role_result("buyer"),
            _make_role_result("analyst"),
        ]
        output = _merge_role_outputs("Marketing", role_results)
        assert "# Marketing" in output

    def test_merge_role_outputs_empty(self):
        output = _merge_role_outputs("Marketing", [])
        assert "No roles produced output" in output


# ===========================================================================
# 8. SkillExecutor role_context passthrough
# ===========================================================================


class TestSkillExecutorRoleContextPassthrough:
    """role_context is passed through from SkillExecutor to agent."""

    async def test_role_context_forwarded(self):
        mock_agent = MagicMock()
        mock_agent.run_skill = AsyncMock(
            return_value=MagicMock(
                user_id="u",
                briefing_text="out",
                recommendations=[],
                cost={},
                session_id="s",
            )
        )

        mock_registry = MagicMock()
        skill = _make_skill("s1")
        mock_registry.get.return_value = skill
        mock_registry.count = 1

        executor = SkillExecutor(mock_agent, mock_registry)
        await executor.execute(
            skill_id="s1",
            user_id="u",
            accounts=_ACCOUNTS,
            role_context="Department context here.",
        )

        call_kwargs = mock_agent.run_skill.call_args.kwargs
        assert call_kwargs["role_context"] == "Department context here."

    async def test_role_context_default_empty(self):
        mock_agent = MagicMock()
        mock_agent.run_skill = AsyncMock(
            return_value=MagicMock(
                user_id="u",
                briefing_text="out",
                recommendations=[],
                cost={},
                session_id="s",
            )
        )

        mock_registry = MagicMock()
        mock_registry.get.return_value = _make_skill("s1")
        mock_registry.count = 1

        executor = SkillExecutor(mock_agent, mock_registry)
        await executor.execute(
            skill_id="s1",
            user_id="u",
            accounts=_ACCOUNTS,
        )

        call_kwargs = mock_agent.run_skill.call_args.kwargs
        assert call_kwargs["role_context"] == ""


# ===========================================================================
# 9. Exception classes
# ===========================================================================


class TestExceptionClasses:
    def test_role_not_found_error(self):
        assert issubclass(RoleNotFoundError, Exception)
        err = RoleNotFoundError("buyer not found")
        assert "buyer" in str(err)

    def test_department_not_found_error(self):
        assert issubclass(DepartmentNotFoundError, Exception)
        err = DepartmentNotFoundError("mktg not found")
        assert "mktg" in str(err)


# ===========================================================================
# 9. Pipeline: output passing between sequential skills
# ===========================================================================


class TestPipelineOutputPassing:
    """RoleExecutor passes previous skill output to next skill."""

    async def test_second_skill_receives_first_output(self):
        """Skill 2 receives skill 1's output via params."""
        mock_skill_exec = MagicMock()
        mock_skill_exec.execute = AsyncMock(
            side_effect=[
                _make_skill_result("s1", "Anomalies found: CPA spike"),
                _make_skill_result("s2", "Budget recs based on anomalies"),
            ]
        )

        mock_registry = MagicMock()
        mock_registry.get_role.return_value = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="D",
            briefing_skills=("s1", "s2"),
        )
        mock_registry.get_department.return_value = None
        mock_registry.get.side_effect = lambda sid: _make_skill(sid)

        executor = RoleExecutor(mock_skill_exec, mock_registry)
        await executor.execute_role("buyer", "user_1", _ACCOUNTS)

        calls = mock_skill_exec.execute.call_args_list
        # First skill: no params (or params=None)
        assert calls[0].kwargs.get("params") is None
        # Second skill: receives previous output
        assert calls[1].kwargs["params"] == {
            "previous_output": "Anomalies found: CPA spike",
        }

    async def test_first_skill_gets_no_previous_output(self):
        """First skill in a sequence should not receive params."""
        mock_skill_exec = MagicMock()
        mock_skill_exec.execute = AsyncMock(
            return_value=_make_skill_result("s1", "Output 1"),
        )

        mock_registry = MagicMock()
        mock_registry.get_role.return_value = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="D",
            briefing_skills=("s1",),
        )
        mock_registry.get_department.return_value = None
        mock_registry.get.side_effect = lambda sid: _make_skill(sid)

        executor = RoleExecutor(mock_skill_exec, mock_registry)
        await executor.execute_role("buyer", "user_1", _ACCOUNTS)

        call_kwargs = mock_skill_exec.execute.call_args.kwargs
        assert call_kwargs.get("params") is None

    async def test_three_skill_pipeline(self):
        """Skill 3 receives skill 2's output (not skill 1's)."""
        mock_skill_exec = MagicMock()
        mock_skill_exec.execute = AsyncMock(
            side_effect=[
                _make_skill_result("s1", "Phase 1 data"),
                _make_skill_result("s2", "Phase 2 analysis"),
                _make_skill_result("s3", "Phase 3 recs"),
            ]
        )

        mock_registry = MagicMock()
        mock_registry.get_role.return_value = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="D",
            briefing_skills=("s1", "s2", "s3"),
        )
        mock_registry.get_department.return_value = None
        mock_registry.get.side_effect = lambda sid: _make_skill(sid)

        executor = RoleExecutor(mock_skill_exec, mock_registry)
        await executor.execute_role("buyer", "user_1", _ACCOUNTS)

        calls = mock_skill_exec.execute.call_args_list
        assert calls[0].kwargs.get("params") is None
        assert calls[1].kwargs["params"] == {
            "previous_output": "Phase 1 data",
        }
        assert calls[2].kwargs["params"] == {
            "previous_output": "Phase 2 analysis",
        }

    async def test_failed_skill_preserves_last_output(self):
        """If skill 2 fails, skill 3 still gets skill 1's output."""
        call_count = 0

        async def execute_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("s2 failed")
            return _make_skill_result(
                kwargs["skill_id"],
                f"Output from {kwargs['skill_id']}",
            )

        mock_skill_exec = MagicMock()
        mock_skill_exec.execute = AsyncMock(
            side_effect=execute_side_effect,
        )

        mock_registry = MagicMock()
        mock_registry.get_role.return_value = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="D",
            briefing_skills=("s1", "s2", "s3"),
        )
        mock_registry.get_department.return_value = None
        mock_registry.get.side_effect = lambda sid: _make_skill(sid)

        executor = RoleExecutor(mock_skill_exec, mock_registry)
        result = await executor.execute_role(
            "buyer",
            "user_1",
            _ACCOUNTS,
        )

        # Only 2 successful results (s1 and s3)
        assert len(result.skill_results) == 2
        # s3 should have received s1's output (last successful)
        calls = mock_skill_exec.execute.call_args_list
        assert calls[2].kwargs["params"] == {
            "previous_output": "Output from s1",
        }

    async def test_single_skill_no_pipeline_params(self):
        """Single skill = no pipeline params, identical to before."""
        mock_skill_exec = MagicMock()
        mock_skill_exec.execute = AsyncMock(
            return_value=_make_skill_result("s1", "Solo output"),
        )

        mock_registry = MagicMock()
        mock_registry.get_role.return_value = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="D",
            briefing_skills=("s1",),
        )
        mock_registry.get_department.return_value = None
        mock_registry.get.side_effect = lambda sid: _make_skill(sid)

        executor = RoleExecutor(mock_skill_exec, mock_registry)
        result = await executor.execute_role(
            "buyer",
            "user_1",
            _ACCOUNTS,
        )

        assert len(result.skill_results) == 1
        call_kwargs = mock_skill_exec.execute.call_args.kwargs
        assert call_kwargs.get("params") is None
