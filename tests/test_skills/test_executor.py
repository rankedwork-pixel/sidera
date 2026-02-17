"""Tests for src.skills.executor -- SkillResult, SkillNotFoundError, SkillExecutor.

Covers dataclass construction, exception behaviour, registry lookup delegation,
agent.run_skill() delegation, prompt-template parameter forwarding, chain_next
propagation, default analysis_date, cost/session/output/recommendations
extraction, multi-account passthrough, and error propagation.

All agent and registry interactions are mocked so these tests run without
real API keys or a Claude SDK installation.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.core import BriefingResult
from src.skills.executor import (
    SkillExecutor,
    SkillNotFoundError,
    SkillResult,
    _briefing_to_skill_result,
)
from src.skills.schema import SkillDefinition

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SKILL_DEFAULTS = {
    "id": "test_skill",
    "name": "Test Skill",
    "version": "1.0",
    "description": "A test skill for executor tests",
    "category": "analysis",
    "platforms": ("google_ads", "meta"),
    "tags": ("test",),
    "tools_required": ("get_meta_campaigns",),
    "model": "sonnet",
    "max_turns": 10,
    "system_supplement": "You are running a test skill.",
    "prompt_template": "Analyse accounts for {analysis_date}.",
    "output_format": "## Results\nShow the results here.",
    "business_guidance": "Follow best practices.",
}


def _make_skill(**overrides: object) -> SkillDefinition:
    """Build a ``SkillDefinition`` with sensible defaults, applying *overrides*."""
    kw = {**_SKILL_DEFAULTS, **overrides}
    return SkillDefinition(**kw)


def _make_briefing(
    user_id: str = "user_1",
    briefing_text: str = "Test output",
    recommendations: list | None = None,
    cost: dict | None = None,
    session_id: str = "sess_abc",
) -> BriefingResult:
    """Build a ``BriefingResult`` with sensible defaults."""
    return BriefingResult(
        user_id=user_id,
        briefing_text=briefing_text,
        recommendations=recommendations or [],
        cost=cost or {},
        session_id=session_id,
    )


def _make_executor(
    registry_skills: dict[str, SkillDefinition] | None = None,
) -> tuple[SkillExecutor, MagicMock, MagicMock]:
    """Build a ``SkillExecutor`` with mocked agent and registry.

    Returns:
        (executor, mock_agent, mock_registry)
    """
    mock_agent = MagicMock()
    mock_agent.run_skill = AsyncMock()

    mock_registry = MagicMock()
    skills = registry_skills or {}
    mock_registry.get.side_effect = lambda sid: skills.get(sid)
    mock_registry.count = len(skills)

    executor = SkillExecutor(agent=mock_agent, registry=mock_registry)
    return executor, mock_agent, mock_registry


# ---------------------------------------------------------------------------
# Sample accounts used across tests
# ---------------------------------------------------------------------------

_SINGLE_ACCOUNT = [{"platform": "meta", "account_id": "act_1", "account_name": "Acme"}]

_MULTI_ACCOUNTS = [
    {"platform": "meta", "account_id": "act_1", "account_name": "Acme Meta"},
    {"platform": "google_ads", "account_id": "123", "account_name": "Acme Google"},
]


# ===========================================================================
# 1. SkillResult dataclass
# ===========================================================================


class TestSkillResult:
    """SkillResult construction and default field values."""

    def test_all_fields_set(self):
        """All explicitly supplied fields are stored."""
        result = SkillResult(
            skill_id="creative_analysis",
            user_id="user_1",
            output_text="Some output",
            recommendations=[{"action": "Pause campaign"}],
            cost={"total_cost_usd": 0.05},
            session_id="sess_xyz",
            chain_next="budget_reallocation",
        )
        assert result.skill_id == "creative_analysis"
        assert result.user_id == "user_1"
        assert result.output_text == "Some output"
        assert result.recommendations == [{"action": "Pause campaign"}]
        assert result.cost == {"total_cost_usd": 0.05}
        assert result.session_id == "sess_xyz"
        assert result.chain_next == "budget_reallocation"

    def test_defaults(self):
        """Optional fields have correct defaults when omitted."""
        result = SkillResult(
            skill_id="s",
            user_id="u",
            output_text="text",
        )
        assert result.recommendations == []
        assert result.cost == {}
        assert result.session_id == ""
        assert result.chain_next is None


# ===========================================================================
# 2. SkillNotFoundError
# ===========================================================================


class TestSkillNotFoundError:
    """SkillNotFoundError is a proper exception subclass."""

    def test_is_exception(self):
        """SkillNotFoundError inherits from Exception."""
        assert issubclass(SkillNotFoundError, Exception)

    def test_message_preserved(self):
        """The error message is retrievable via str()."""
        err = SkillNotFoundError("Skill 'foo' not found")
        assert "foo" in str(err)


# ===========================================================================
# 3. SkillExecutor.__init__
# ===========================================================================


class TestSkillExecutorInit:
    """SkillExecutor stores agent and registry references."""

    def test_stores_agent_and_registry(self):
        """__init__ stores the provided agent and registry."""
        executor, mock_agent, mock_registry = _make_executor()
        assert executor._agent is mock_agent
        assert executor._registry is mock_registry


# ===========================================================================
# 4. SkillExecutor.execute -- happy path
# ===========================================================================


class TestSkillExecutorExecuteHappy:
    """Successful execute() calls with valid skill IDs."""

    async def test_delegates_to_agent_run_skill(self):
        """execute() looks up the skill and calls agent.run_skill."""
        skill = _make_skill()
        executor, mock_agent, _ = _make_executor(registry_skills={"test_skill": skill})
        mock_agent.run_skill.return_value = _make_briefing()

        await executor.execute(
            skill_id="test_skill",
            user_id="user_1",
            accounts=_SINGLE_ACCOUNT,
        )

        mock_agent.run_skill.assert_awaited_once()
        call_kwargs = mock_agent.run_skill.call_args.kwargs
        assert call_kwargs["skill"] is skill
        assert call_kwargs["user_id"] == "user_1"
        assert call_kwargs["account_ids"] == _SINGLE_ACCOUNT

    async def test_returns_skill_result(self):
        """execute() returns a SkillResult with the correct skill_id."""
        skill = _make_skill()
        executor, mock_agent, _ = _make_executor(registry_skills={"test_skill": skill})
        mock_agent.run_skill.return_value = _make_briefing()

        result = await executor.execute(
            skill_id="test_skill",
            user_id="user_1",
            accounts=_SINGLE_ACCOUNT,
        )

        assert isinstance(result, SkillResult)
        assert result.skill_id == "test_skill"
        assert result.user_id == "user_1"

    async def test_output_text_extraction(self):
        """output_text is forwarded from BriefingResult.briefing_text."""
        skill = _make_skill()
        executor, mock_agent, _ = _make_executor(registry_skills={"test_skill": skill})
        mock_agent.run_skill.return_value = _make_briefing(briefing_text="Full analysis here.")

        result = await executor.execute(
            skill_id="test_skill",
            user_id="user_1",
            accounts=_SINGLE_ACCOUNT,
        )

        assert result.output_text == "Full analysis here."

    async def test_recommendations_extraction(self):
        """recommendations list is forwarded from BriefingResult."""
        recs = [{"action": "Increase budget"}, {"action": "Pause low performers"}]
        skill = _make_skill()
        executor, mock_agent, _ = _make_executor(registry_skills={"test_skill": skill})
        mock_agent.run_skill.return_value = _make_briefing(recommendations=recs)

        result = await executor.execute(
            skill_id="test_skill",
            user_id="user_1",
            accounts=_SINGLE_ACCOUNT,
        )

        assert result.recommendations == recs

    async def test_cost_dict_forwarding(self):
        """cost dict is forwarded from BriefingResult."""
        cost = {"total_cost_usd": 0.12, "num_turns": 5}
        skill = _make_skill()
        executor, mock_agent, _ = _make_executor(registry_skills={"test_skill": skill})
        mock_agent.run_skill.return_value = _make_briefing(cost=cost)

        result = await executor.execute(
            skill_id="test_skill",
            user_id="user_1",
            accounts=_SINGLE_ACCOUNT,
        )

        assert result.cost == cost

    async def test_session_id_forwarding(self):
        """session_id is forwarded from BriefingResult."""
        skill = _make_skill()
        executor, mock_agent, _ = _make_executor(registry_skills={"test_skill": skill})
        mock_agent.run_skill.return_value = _make_briefing(session_id="sess_123")

        result = await executor.execute(
            skill_id="test_skill",
            user_id="user_1",
            accounts=_SINGLE_ACCOUNT,
        )

        assert result.session_id == "sess_123"

    async def test_session_id_generated_when_empty(self):
        """When BriefingResult.session_id is empty a UUID is generated."""
        skill = _make_skill()
        executor, mock_agent, _ = _make_executor(registry_skills={"test_skill": skill})
        mock_agent.run_skill.return_value = _make_briefing(session_id="")

        result = await executor.execute(
            skill_id="test_skill",
            user_id="user_1",
            accounts=_SINGLE_ACCOUNT,
        )

        # _briefing_to_skill_result generates a UUID when session_id is falsy
        assert result.session_id != ""
        assert len(result.session_id) > 10  # UUID is 36 chars


# ===========================================================================
# 5. chain_next propagation
# ===========================================================================


class TestChainNextPropagation:
    """chain_next on SkillResult mirrors the skill's chain_after field."""

    async def test_chain_after_propagated(self):
        """When skill.chain_after is set, result.chain_next matches it."""
        skill = _make_skill(chain_after="budget_reallocation")
        executor, mock_agent, _ = _make_executor(
            registry_skills={"test_skill": skill},
        )
        mock_agent.run_skill.return_value = _make_briefing()

        result = await executor.execute(
            skill_id="test_skill",
            user_id="user_1",
            accounts=_SINGLE_ACCOUNT,
        )

        assert result.chain_next == "budget_reallocation"

    async def test_chain_after_none(self):
        """When skill.chain_after is None, result.chain_next is None."""
        skill = _make_skill(chain_after=None)
        executor, mock_agent, _ = _make_executor(registry_skills={"test_skill": skill})
        mock_agent.run_skill.return_value = _make_briefing()

        result = await executor.execute(
            skill_id="test_skill",
            user_id="user_1",
            accounts=_SINGLE_ACCOUNT,
        )

        assert result.chain_next is None


# ===========================================================================
# 6. Default analysis_date
# ===========================================================================


class TestDefaultAnalysisDate:
    """analysis_date defaults to today when not supplied."""

    async def test_default_analysis_date_is_today(self):
        """When analysis_date is omitted, today's date is forwarded."""
        skill = _make_skill()
        executor, mock_agent, _ = _make_executor(registry_skills={"test_skill": skill})
        mock_agent.run_skill.return_value = _make_briefing()

        await executor.execute(
            skill_id="test_skill",
            user_id="user_1",
            accounts=_SINGLE_ACCOUNT,
        )

        call_kwargs = mock_agent.run_skill.call_args.kwargs
        assert call_kwargs["analysis_date"] is None  # executor passes None; agent defaults

    async def test_explicit_analysis_date_forwarded(self):
        """An explicit analysis_date is forwarded to agent.run_skill."""
        target_date = date(2025, 6, 15)
        skill = _make_skill()
        executor, mock_agent, _ = _make_executor(registry_skills={"test_skill": skill})
        mock_agent.run_skill.return_value = _make_briefing()

        await executor.execute(
            skill_id="test_skill",
            user_id="user_1",
            accounts=_SINGLE_ACCOUNT,
            analysis_date=target_date,
        )

        call_kwargs = mock_agent.run_skill.call_args.kwargs
        assert call_kwargs["analysis_date"] == target_date


# ===========================================================================
# 7. Multiple accounts
# ===========================================================================


class TestMultipleAccounts:
    """Multiple account dicts are passed through to the agent."""

    async def test_multiple_accounts_forwarded(self):
        """All accounts in the list are forwarded to agent.run_skill."""
        skill = _make_skill()
        executor, mock_agent, _ = _make_executor(registry_skills={"test_skill": skill})
        mock_agent.run_skill.return_value = _make_briefing()

        await executor.execute(
            skill_id="test_skill",
            user_id="user_1",
            accounts=_MULTI_ACCOUNTS,
        )

        call_kwargs = mock_agent.run_skill.call_args.kwargs
        assert call_kwargs["account_ids"] == _MULTI_ACCOUNTS
        assert len(call_kwargs["account_ids"]) == 2


# ===========================================================================
# 8. Params forwarding
# ===========================================================================


class TestParamsForwarding:
    """Optional params dict is forwarded to agent.run_skill."""

    async def test_params_forwarded(self):
        """Custom params are forwarded unchanged."""
        skill = _make_skill()
        executor, mock_agent, _ = _make_executor(registry_skills={"test_skill": skill})
        mock_agent.run_skill.return_value = _make_briefing()

        params = {"lookback_days": 14, "extra_instructions": "Focus on ROAS."}
        await executor.execute(
            skill_id="test_skill",
            user_id="user_1",
            accounts=_SINGLE_ACCOUNT,
            params=params,
        )

        call_kwargs = mock_agent.run_skill.call_args.kwargs
        assert call_kwargs["params"] == params

    async def test_params_default_none(self):
        """When params is omitted it is forwarded as None."""
        skill = _make_skill()
        executor, mock_agent, _ = _make_executor(registry_skills={"test_skill": skill})
        mock_agent.run_skill.return_value = _make_briefing()

        await executor.execute(
            skill_id="test_skill",
            user_id="user_1",
            accounts=_SINGLE_ACCOUNT,
        )

        call_kwargs = mock_agent.run_skill.call_args.kwargs
        assert call_kwargs["params"] is None


# ===========================================================================
# 9. Unknown skill raises SkillNotFoundError
# ===========================================================================


class TestUnknownSkill:
    """execute() with an unregistered skill_id raises SkillNotFoundError."""

    async def test_raises_skill_not_found(self):
        """An unknown skill_id raises SkillNotFoundError."""
        executor, mock_agent, _ = _make_executor(registry_skills={})

        with pytest.raises(SkillNotFoundError, match="not_real"):
            await executor.execute(
                skill_id="not_real",
                user_id="user_1",
                accounts=_SINGLE_ACCOUNT,
            )

        # Agent should never be called
        mock_agent.run_skill.assert_not_awaited()


# ===========================================================================
# 10. Error propagation from agent.run_skill
# ===========================================================================


class TestAgentErrorPropagation:
    """Exceptions from agent.run_skill bubble up through execute()."""

    async def test_runtime_error_propagates(self):
        """A RuntimeError from agent.run_skill propagates unchanged."""
        skill = _make_skill()
        executor, mock_agent, _ = _make_executor(registry_skills={"test_skill": skill})
        mock_agent.run_skill.side_effect = RuntimeError("SDK connection failed")

        with pytest.raises(RuntimeError, match="SDK connection failed"):
            await executor.execute(
                skill_id="test_skill",
                user_id="user_1",
                accounts=_SINGLE_ACCOUNT,
            )


# ===========================================================================
# 11. _briefing_to_skill_result helper
# ===========================================================================


class TestBriefingToSkillResult:
    """Direct tests for the module-level conversion helper."""

    def test_basic_conversion(self):
        """Converts a BriefingResult to a SkillResult correctly."""
        briefing = _make_briefing(
            user_id="user_99",
            briefing_text="Analysis complete.",
            recommendations=[{"action": "Scale"}],
            cost={"total_cost_usd": 0.03},
            session_id="sess_direct",
        )
        result = _briefing_to_skill_result(
            briefing_result=briefing,
            skill_id="direct_test",
            chain_after="next_step",
        )

        assert isinstance(result, SkillResult)
        assert result.skill_id == "direct_test"
        assert result.user_id == "user_99"
        assert result.output_text == "Analysis complete."
        assert result.recommendations == [{"action": "Scale"}]
        assert result.cost == {"total_cost_usd": 0.03}
        assert result.session_id == "sess_direct"
        assert result.chain_next == "next_step"

    def test_empty_session_id_generates_uuid(self):
        """When session_id is empty, a UUID string is generated."""
        briefing = _make_briefing(session_id="")
        result = _briefing_to_skill_result(
            briefing_result=briefing,
            skill_id="s",
            chain_after=None,
        )
        assert result.session_id != ""
        # UUID v4 is 36 chars with hyphens
        assert len(result.session_id) == 36

    def test_none_session_id_generates_uuid(self):
        """When session_id is None (falsy), a UUID string is generated."""
        briefing = _make_briefing(session_id="")
        # Manually set to None to test the `or` branch
        briefing.session_id = None  # type: ignore[assignment]
        result = _briefing_to_skill_result(
            briefing_result=briefing,
            skill_id="s",
            chain_after=None,
        )
        assert result.session_id != ""
        assert len(result.session_id) == 36
