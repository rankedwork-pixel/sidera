"""Tests for manager delegation and synthesis methods on SideraAgent."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.api_client import TurnResult
from src.agent.core import SideraAgent
from src.agent.prompts import (
    DELEGATION_DECISION_PROMPT,
    SYNTHESIS_PROMPT,
    build_delegation_prompt,
    build_synthesis_prompt,
)

# =====================================================================
# Fixtures and helpers
# =====================================================================

SAMPLE_ROLES = [
    {
        "role_id": "media_buyer",
        "name": "Media Buyer",
        "description": "Manages campaign budgets and bidding",
        "briefing_skills": ["budget_analysis", "bid_optimization"],
    },
    {
        "role_id": "creative_analyst",
        "name": "Creative Analyst",
        "description": "Evaluates ad creative performance",
        "briefing_skills": ["creative_analysis", "ab_test_review"],
    },
    {
        "role_id": "data_engineer",
        "name": "Data Engineer",
        "description": "Monitors data pipelines and quality",
        "briefing_skills": ["data_quality_check"],
    },
]


def _make_turn_result(
    text: str = "",
    cost: float = 0.05,
    num_turns: int = 1,
    duration_ms: int = 1000,
) -> TurnResult:
    """Create a TurnResult for mocking run_agent_loop."""
    return TurnResult(
        text=text,
        cost={
            "total_cost_usd": cost,
            "num_turns": num_turns,
            "duration_ms": duration_ms,
            "model": "claude-sonnet-4-20250514",
            "is_error": False,
            "input_tokens": 500,
            "output_tokens": 200,
        },
        turn_count=num_turns,
        session_id="",
        is_error=False,
    )


def _build_agent() -> SideraAgent:
    """Create a SideraAgent instance without calling __init__.

    Sets up the minimal attributes needed for delegation and synthesis
    methods without requiring full initialization.
    """
    agent = SideraAgent.__new__(SideraAgent)
    agent._model_override = None
    agent._log = MagicMock()
    agent._google_ads_credentials = None
    agent._meta_credentials = None
    from src.agent.tool_registry import get_global_registry

    agent._registry = get_global_registry()
    return agent


# =====================================================================
# 1. build_delegation_prompt
# =====================================================================


class TestBuildDelegationPrompt:
    """Tests for the build_delegation_prompt function."""

    def test_build_delegation_prompt_formats_correctly(self) -> None:
        """Prompt should contain manager name, persona, and roles JSON."""
        prompt = build_delegation_prompt(
            manager_name="Head of Marketing",
            manager_persona="a senior performance marketer with 15 years of experience",
            own_results_summary="Spend is up 12% WoW, CPA stable.",
            available_roles=SAMPLE_ROLES,
        )

        assert "Head of Marketing" in prompt
        assert "a senior performance marketer with 15 years of experience" in prompt
        assert "Spend is up 12% WoW, CPA stable." in prompt
        assert "media_buyer" in prompt
        assert "Creative Analyst" in prompt
        assert "data_engineer" in prompt

    def test_delegation_decision_prompt_has_json_schema(self) -> None:
        """The prompt template should contain the JSON schema example."""
        assert '"activate"' in DELEGATION_DECISION_PROMPT
        assert '"skip"' in DELEGATION_DECISION_PROMPT
        assert '"role_id"' in DELEGATION_DECISION_PROMPT
        assert '"reason"' in DELEGATION_DECISION_PROMPT
        assert '"priority"' in DELEGATION_DECISION_PROMPT

    def test_roles_serialized_as_json(self) -> None:
        """Available roles should be serialized as valid JSON in the prompt."""
        prompt = build_delegation_prompt(
            manager_name="Manager",
            manager_persona="persona",
            own_results_summary="summary",
            available_roles=SAMPLE_ROLES,
        )

        # Extract the JSON block from the prompt
        assert "## Available Team Members" in prompt
        roles_section = prompt.split("## Available Team Members\n")[1]
        roles_text = roles_section.split("## Instructions")[0].strip()
        parsed = json.loads(roles_text)
        assert len(parsed) == 3
        assert parsed[0]["role_id"] == "media_buyer"


# =====================================================================
# 2. build_synthesis_prompt
# =====================================================================


class TestBuildSynthesisPrompt:
    """Tests for the build_synthesis_prompt function."""

    def test_build_synthesis_prompt_formats_correctly(self) -> None:
        """Prompt should contain all sections: manager info, own results, team reports."""
        prompt = build_synthesis_prompt(
            manager_name="CMO",
            manager_persona="the chief marketing officer",
            own_results="ROAS is 4.2x, above target.",
            sub_role_results=(
                "## Media Buyer\nBudget utilization at 87%.\n\n## Creative\nTop ad CTR 3.2%."
            ),
        )

        assert "CMO" in prompt
        assert "the chief marketing officer" in prompt
        assert "ROAS is 4.2x, above target." in prompt
        assert "Budget utilization at 87%." in prompt
        assert "Top ad CTR 3.2%." in prompt

    def test_build_synthesis_prompt_with_custom_instructions(self) -> None:
        """When synthesis_instructions is provided, it should appear as a focus block."""
        prompt = build_synthesis_prompt(
            manager_name="CMO",
            manager_persona="the chief marketing officer",
            own_results="Good overall.",
            sub_role_results="Reports here.",
            synthesis_instructions="Focus on cross-platform budget efficiency.",
        )

        assert "## Synthesis Focus" in prompt
        assert "Focus on cross-platform budget efficiency." in prompt

    def test_build_synthesis_prompt_without_custom_instructions(self) -> None:
        """When synthesis_instructions is empty, no extra focus block should appear."""
        prompt = build_synthesis_prompt(
            manager_name="CMO",
            manager_persona="the chief marketing officer",
            own_results="Good overall.",
            sub_role_results="Reports here.",
            synthesis_instructions="",
        )

        assert "## Synthesis Focus" not in prompt

    def test_synthesis_prompt_has_instructions(self) -> None:
        """The synthesis prompt template should contain numbered instructions."""
        assert "1. Starts with an executive summary" in SYNTHESIS_PROMPT
        assert "2. Highlights cross-cutting themes" in SYNTHESIS_PROMPT
        assert "3. Flags any conflicts" in SYNTHESIS_PROMPT
        assert "4. Provides a prioritized action plan" in SYNTHESIS_PROMPT
        assert "5. Notes which team member" in SYNTHESIS_PROMPT


# =====================================================================
# 3. _fallback_activate_all
# =====================================================================


class TestFallbackActivateAll:
    """Tests for SideraAgent._fallback_activate_all."""

    def test_fallback_activate_all(self) -> None:
        """Should activate all roles with sequential priorities."""
        result = SideraAgent._fallback_activate_all(SAMPLE_ROLES)

        assert len(result["activate"]) == 3
        assert result["skip"] == []

        # Check priorities are sequential
        priorities = [r["priority"] for r in result["activate"]]
        assert priorities == [1, 2, 3]

        # Check role_ids are extracted correctly
        role_ids = [r["role_id"] for r in result["activate"]]
        assert "media_buyer" in role_ids
        assert "creative_analyst" in role_ids
        assert "data_engineer" in role_ids

        # Check reason mentions fallback
        for entry in result["activate"]:
            assert "Fallback" in entry["reason"]

    def test_fallback_activate_all_empty(self) -> None:
        """Should return empty activate list for empty roles."""
        result = SideraAgent._fallback_activate_all([])

        assert result["activate"] == []
        assert result["skip"] == []

    def test_fallback_uses_id_when_role_id_missing(self) -> None:
        """Should fall back to 'id' key when 'role_id' is missing."""
        roles = [{"id": "fallback_role", "name": "Fallback"}]
        result = SideraAgent._fallback_activate_all(roles)

        assert result["activate"][0]["role_id"] == "fallback_role"

    def test_fallback_empty_string_when_no_id_keys(self) -> None:
        """Should use empty string when neither role_id nor id is present."""
        roles = [{"name": "No ID Role"}]
        result = SideraAgent._fallback_activate_all(roles)

        assert result["activate"][0]["role_id"] == ""


# =====================================================================
# 4. run_delegation_decision
# =====================================================================


class TestRunDelegationDecision:
    """Tests for SideraAgent.run_delegation_decision."""

    @pytest.mark.asyncio
    async def test_delegation_returns_parsed_json(self) -> None:
        """Should parse valid JSON response from the LLM."""
        agent = _build_agent()

        valid_json = json.dumps(
            {
                "activate": [
                    {"role_id": "media_buyer", "reason": "Budget review needed", "priority": 1},
                ],
                "skip": [
                    {"role_id": "creative_analyst", "reason": "No creative changes this week"},
                    {"role_id": "data_engineer", "reason": "Pipeline stable"},
                ],
            }
        )

        mock_result = _make_turn_result(text=valid_json)

        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await agent.run_delegation_decision(
                manager_name="Head of Marketing",
                manager_persona="Senior marketer",
                own_results_summary="Everything looks good",
                available_roles=SAMPLE_ROLES,
            )

        assert len(result["activate"]) == 1
        assert result["activate"][0]["role_id"] == "media_buyer"
        assert len(result["skip"]) == 2

    @pytest.mark.asyncio
    async def test_delegation_parses_json_in_code_block(self) -> None:
        """Should extract JSON from markdown code blocks."""
        agent = _build_agent()

        response_text = (
            "Here is my decision:\n\n"
            "```json\n"
            '{"activate": [{"role_id": "media_buyer", "reason": "needed", "priority": 1}], '
            '"skip": []}\n'
            "```\n"
        )

        mock_result = _make_turn_result(text=response_text)

        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await agent.run_delegation_decision(
                manager_name="Manager",
                manager_persona="persona",
                own_results_summary="summary",
                available_roles=[{"role_id": "media_buyer", "name": "Media Buyer"}],
            )

        assert len(result["activate"]) == 1
        assert result["activate"][0]["role_id"] == "media_buyer"

    @pytest.mark.asyncio
    async def test_delegation_falls_back_on_invalid_json(self) -> None:
        """Should activate all roles when LLM returns unparseable text."""
        agent = _build_agent()

        mock_result = _make_turn_result(text="This is not JSON at all. Just some text.")

        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await agent.run_delegation_decision(
                manager_name="Manager",
                manager_persona="persona",
                own_results_summary="summary",
                available_roles=SAMPLE_ROLES,
            )

        # Fallback should activate all roles
        assert len(result["activate"]) == 3
        assert result["skip"] == []
        role_ids = [r["role_id"] for r in result["activate"]]
        assert "media_buyer" in role_ids
        assert "creative_analyst" in role_ids
        assert "data_engineer" in role_ids

    @pytest.mark.asyncio
    async def test_delegation_falls_back_on_exception(self) -> None:
        """Should activate all roles when the LLM call raises an exception."""
        agent = _build_agent()

        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            side_effect=RuntimeError("API connection failed"),
        ):
            result = await agent.run_delegation_decision(
                manager_name="Manager",
                manager_persona="persona",
                own_results_summary="summary",
                available_roles=SAMPLE_ROLES,
            )

        # Fallback should activate all roles
        assert len(result["activate"]) == 3
        assert result["skip"] == []

    @pytest.mark.asyncio
    async def test_delegation_uses_no_tools(self) -> None:
        """Delegation should use no tools and max_turns=1."""
        agent = _build_agent()

        valid_json = json.dumps(
            {
                "activate": [{"role_id": "media_buyer", "reason": "needed", "priority": 1}],
                "skip": [],
            }
        )

        mock_result = _make_turn_result(text=valid_json)

        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_loop:
            await agent.run_delegation_decision(
                manager_name="Manager",
                manager_persona="persona",
                own_results_summary="summary",
                available_roles=[{"role_id": "media_buyer", "name": "Buyer"}],
            )

        mock_loop.assert_called_once()
        call_kwargs = mock_loop.call_args.kwargs
        assert call_kwargs["tools"] is None
        assert call_kwargs["max_turns"] == 1


# =====================================================================
# 5. run_synthesis
# =====================================================================


class TestRunSynthesis:
    """Tests for SideraAgent.run_synthesis."""

    @pytest.mark.asyncio
    async def test_synthesis_returns_text(self) -> None:
        """Should return the synthesized text from the LLM."""
        agent = _build_agent()

        synthesis_output = (
            "## Executive Summary\n"
            "Campaign performance improved 12% WoW across all channels.\n\n"
            "## Cross-Cutting Themes\n"
            "Both media buying and creative teams identified mobile as a growth driver."
        )

        mock_result = _make_turn_result(text=synthesis_output)

        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await agent.run_synthesis(
                manager_name="CMO",
                manager_persona="the chief marketing officer",
                own_results="Overall ROAS at 4.2x.",
                sub_role_results="## Media Buyer\nBudget 87% utilized.\n\n## Creative\nCTR 3.2%.",
            )

        assert "Executive Summary" in result
        assert "Cross-Cutting Themes" in result
        assert "mobile as a growth driver" in result

    @pytest.mark.asyncio
    async def test_synthesis_uses_no_tools(self) -> None:
        """Synthesis should use no tools and max_turns=1."""
        agent = _build_agent()

        mock_result = _make_turn_result(text="Synthesis text.")

        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_loop:
            await agent.run_synthesis(
                manager_name="CMO",
                manager_persona="persona",
                own_results="results",
                sub_role_results="reports",
            )

        mock_loop.assert_called_once()
        call_kwargs = mock_loop.call_args.kwargs
        assert call_kwargs["tools"] is None
        assert call_kwargs["max_turns"] == 1

    @pytest.mark.asyncio
    async def test_synthesis_raises_on_error(self) -> None:
        """Should propagate exceptions from the LLM call."""
        agent = _build_agent()

        with (
            patch(
                "src.agent.core.run_agent_loop",
                new_callable=AsyncMock,
                side_effect=RuntimeError("API failure"),
            ),
            pytest.raises(RuntimeError, match="API failure"),
        ):
            await agent.run_synthesis(
                manager_name="CMO",
                manager_persona="persona",
                own_results="results",
                sub_role_results="reports",
            )

    @pytest.mark.asyncio
    async def test_synthesis_with_custom_instructions(self) -> None:
        """Custom synthesis instructions should be passed through to the prompt."""
        agent = _build_agent()

        mock_result = _make_turn_result(text="Synthesis with focus.")

        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_loop:
            await agent.run_synthesis(
                manager_name="CMO",
                manager_persona="persona",
                own_results="results",
                sub_role_results="reports",
                synthesis_prompt="Focus on budget efficiency across platforms.",
            )

        call_kwargs = mock_loop.call_args.kwargs
        assert "Synthesis Focus" in call_kwargs["user_prompt"]
        assert "Focus on budget efficiency across platforms." in call_kwargs["user_prompt"]
