"""Tests for the SideraAgent orchestrator (src/agent/core.py).

Covers construction, tool helpers, response extraction, and the public entry
points (run_daily_briefing, run_query, run_daily_briefing_optimized).

All Anthropic API interactions are mocked via ``run_agent_loop`` -- these
tests never hit the real API.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.api_client import TurnResult
from src.agent.core import BriefingResult, QueryResult, SideraAgent
from src.agent.prompts import (
    SYSTEM_PROMPT,
    build_analysis_prompt,
    build_daily_briefing_prompt,
)
from src.config import Settings

# =====================================================================
# Fixtures
# =====================================================================

SAMPLE_ACCOUNTS = [
    {
        "platform": "google_ads",
        "account_id": "1234567890",
        "account_name": "Acme Store",
        "target_roas": 4.0,
        "target_cpa": 25.00,
        "monthly_budget_cap": 50_000,
        "currency": "USD",
    },
    {
        "platform": "meta",
        "account_id": "act_999888777",
        "account_name": "Acme Meta",
        "target_roas": 3.5,
        "target_cpa": 30.00,
        "monthly_budget_cap": 20_000,
        "currency": "USD",
    },
]


def _make_turn_result(
    text: str = "",
    cost: float = 0.05,
    num_turns: int = 5,
    duration_ms: int = 3000,
    is_error: bool = False,
    model: str = "claude-sonnet-4-20250514",
) -> TurnResult:
    """Create a TurnResult for mocking run_agent_loop."""
    return TurnResult(
        text=text,
        cost={
            "total_cost_usd": cost,
            "num_turns": num_turns,
            "duration_ms": duration_ms,
            "model": model,
            "is_error": is_error,
            "input_tokens": 1000,
            "output_tokens": 500,
        },
        turn_count=num_turns,
        session_id="",
        is_error=is_error,
    )


@pytest.fixture
def agent() -> SideraAgent:
    """Create a SideraAgent instance."""
    return SideraAgent()


@pytest.fixture
def agent_with_override() -> SideraAgent:
    """Create a SideraAgent with an explicit model override."""
    return SideraAgent(model_override="claude-haiku-4-5-20251001")


# =====================================================================
# 1. SideraAgent construction
# =====================================================================


class TestSideraAgentConstruction:
    """Tests for SideraAgent.__init__."""

    def test_creates_with_default_config(self, agent: SideraAgent) -> None:
        """Agent should initialize without errors using default settings."""
        assert agent is not None
        assert agent._model_override is None
        assert agent._google_ads_credentials is None
        assert agent._meta_credentials is None

    def test_creates_with_model_override(self, agent_with_override: SideraAgent) -> None:
        """Agent should store the model override when provided."""
        assert agent_with_override._model_override == "claude-haiku-4-5-20251001"


# =====================================================================
# 2. _extract_recommendations
# =====================================================================


class TestExtractRecommendations:
    """Tests for SideraAgent._extract_recommendations."""

    def test_parses_standard_format(self) -> None:
        """Should parse plain-text Action/Reasoning/Projected Impact/Risk Level."""
        text = (
            "## Executive Summary\nThings are going well.\n\n"
            "## Recommendations\n"
            "- Action: Increase budget on Campaign X by 15%\n"
            "- Reasoning: CPA is 20% below target with room to scale\n"
            "- Projected Impact: +12 conversions per week\n"
            "- Risk Level: Low\n"
        )
        recs = SideraAgent._extract_recommendations(text)
        assert len(recs) == 1
        assert recs[0]["action"] == "Increase budget on Campaign X by 15%"
        assert "CPA" in recs[0]["reasoning"]
        assert "+12" in recs[0]["projected_impact"]
        assert recs[0]["risk_level"] == "Low"

    def test_parses_markdown_bold_format(self) -> None:
        """Should parse **Action:** markdown bold format."""
        text = (
            "## Recommendations\n"
            "- **Action:** Pause underperforming ad sets\n"
            "- **Reasoning:** These ad sets have CPA 3x above target\n"
            "- **Projected Impact:** Save $500/week\n"
            "- **Risk Level:** Medium\n"
        )
        recs = SideraAgent._extract_recommendations(text)
        assert len(recs) == 1
        assert "Pause underperforming ad sets" in recs[0]["action"]
        assert "Medium" in recs[0]["risk_level"]

    def test_multiple_recommendations(self) -> None:
        """Should parse multiple recommendations in sequence."""
        text = (
            "## Recommendations\n"
            "- Action: First recommendation\n"
            "- Reasoning: First reason\n"
            "- Projected Impact: First impact\n"
            "- Risk Level: Low\n"
            "\n"
            "- Action: Second recommendation\n"
            "- Reasoning: Second reason\n"
            "- Projected Impact: Second impact\n"
            "- Risk Level: High\n"
        )
        recs = SideraAgent._extract_recommendations(text)
        assert len(recs) == 2
        assert recs[0]["action"] == "First recommendation"
        assert recs[1]["action"] == "Second recommendation"
        assert recs[0]["risk_level"] == "Low"
        assert recs[1]["risk_level"] == "High"

    def test_no_recommendations_section(self) -> None:
        """Should return empty list when there is no Recommendations section."""
        text = (
            "## Executive Summary\n"
            "Performance is stable.\n\n"
            "## Key Metrics Dashboard\n"
            "Spend: $10,000\n"
        )
        recs = SideraAgent._extract_recommendations(text)
        assert recs == []

    def test_empty_text(self) -> None:
        """Should return empty list for empty input."""
        recs = SideraAgent._extract_recommendations("")
        assert recs == []

    def test_partial_recommendation(self) -> None:
        """Should still capture a recommendation even if some fields are missing."""
        text = (
            "## Recommendations\n"
            "- Action: Do something important\n"
            "- Reasoning: Because data says so\n"
            # Missing Projected Impact and Risk Level
        )
        recs = SideraAgent._extract_recommendations(text)
        assert len(recs) == 1
        assert recs[0]["action"] == "Do something important"
        assert recs[0]["reasoning"] == "Because data says so"
        assert "projected_impact" not in recs[0]
        assert "risk_level" not in recs[0]


# =====================================================================
# 3. run_daily_briefing
# =====================================================================


class TestRunDailyBriefing:
    """Tests for SideraAgent.run_daily_briefing."""

    @pytest.mark.asyncio
    async def test_calls_api_and_returns_briefing_result(self) -> None:
        """Should call run_agent_loop and return a BriefingResult."""
        briefing_text = (
            "## Executive Summary\nAll good.\n\n"
            "## Recommendations\n"
            "- Action: Scale Campaign A by 10%\n"
            "- Reasoning: ROAS is 5x, above target\n"
            "- Projected Impact: +8 conversions/week\n"
            "- Risk Level: Low\n"
        )

        mock_result = _make_turn_result(
            text=briefing_text,
            cost=0.08,
            num_turns=4,
            duration_ms=5000,
        )

        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            agent = SideraAgent()
            result = await agent.run_daily_briefing(
                user_id="user_42",
                account_ids=SAMPLE_ACCOUNTS,
                analysis_date=date(2025, 1, 15),
            )

        assert isinstance(result, BriefingResult)
        assert result.user_id == "user_42"
        assert "Executive Summary" in result.briefing_text
        assert result.cost["total_cost_usd"] == 0.08

    @pytest.mark.asyncio
    async def test_returns_briefing_with_recommendations(self) -> None:
        """Returned BriefingResult should contain extracted recommendations."""
        briefing_text = (
            "## Recommendations\n"
            "- Action: Shift $2K from Meta to Google Search\n"
            "- Reasoning: Google CPA is 40% lower\n"
            "- Projected Impact: 15 additional conversions\n"
            "- Risk Level: Medium\n"
        )

        mock_result = _make_turn_result(
            text=briefing_text,
            cost=0.06,
            num_turns=3,
        )

        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            agent = SideraAgent()
            result = await agent.run_daily_briefing(
                user_id="user_42",
                account_ids=SAMPLE_ACCOUNTS,
                analysis_date=date(2025, 1, 15),
            )

        assert len(result.recommendations) == 1
        assert "Shift" in result.recommendations[0]["action"]
        assert result.recommendations[0]["risk_level"] == "Medium"


# =====================================================================
# 4. run_query
# =====================================================================


class TestRunQuery:
    """Tests for SideraAgent.run_query."""

    @pytest.mark.asyncio
    async def test_calls_run_agent_loop_and_returns_query_result(self) -> None:
        """Should use run_agent_loop and return a QueryResult."""
        response_text = "CPA spiked because Campaign B exhausted its budget at 2pm."

        mock_result = _make_turn_result(
            text=response_text,
            cost=0.03,
            num_turns=2,
            duration_ms=1500,
        )

        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            agent = SideraAgent()
            result = await agent.run_query(
                user_id="user_42",
                query_text="Why did CPA spike yesterday?",
                account_ids=SAMPLE_ACCOUNTS,
                analysis_date=date(2025, 1, 15),
            )

        assert isinstance(result, QueryResult)
        assert result.user_id == "user_42"
        assert "CPA spiked" in result.response_text
        assert result.cost["total_cost_usd"] == 0.03

    @pytest.mark.asyncio
    async def test_builds_prompt_with_query_text(self) -> None:
        """Should pass the user's question through build_analysis_prompt."""
        mock_result = _make_turn_result(text="Answer text")

        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_loop:
            agent = SideraAgent()
            await agent.run_query(
                user_id="user_42",
                query_text="Compare search vs pmax this month",
                account_ids=SAMPLE_ACCOUNTS,
                analysis_date=date(2025, 1, 15),
            )

        # run_agent_loop should have been called with a prompt containing the query
        call_kwargs = mock_loop.call_args.kwargs
        assert "Compare search vs pmax this month" in call_kwargs["user_prompt"]
        assert "2025-01-15" in call_kwargs["user_prompt"]


# =====================================================================
# 5. Prompt templates (prompts.py)
# =====================================================================


class TestPromptTemplates:
    """Tests for prompt template functions in src/agent/prompts.py."""

    def test_build_daily_briefing_prompt(self) -> None:
        """Should produce a prompt containing account details and date."""
        prompt = build_daily_briefing_prompt(
            accounts=SAMPLE_ACCOUNTS,
            analysis_date=date(2025, 2, 1),
        )
        assert "2025-02-01" in prompt
        assert "Acme Store" in prompt
        assert "1234567890" in prompt
        assert "Acme Meta" in prompt
        assert "act_999888777" in prompt
        assert "target ROAS: 4.0x" in prompt
        assert "target CPA: $25.0" in prompt
        assert "monthly budget cap: $50,000" in prompt

    def test_build_analysis_prompt(self) -> None:
        """Should produce a prompt containing the user query and accounts."""
        prompt = build_analysis_prompt(
            query="Why did CPA spike yesterday?",
            accounts=SAMPLE_ACCOUNTS,
            analysis_date=date(2025, 2, 1),
        )
        assert "Why did CPA spike yesterday?" in prompt
        assert "2025-02-01" in prompt
        assert "Acme Store" in prompt
        assert "Acme Meta" in prompt

    def test_system_prompt_is_non_empty_and_contains_key_phrases(self) -> None:
        """SYSTEM_PROMPT should contain core Sidera identity phrases."""
        assert len(SYSTEM_PROMPT) > 100
        assert "Sidera" in SYSTEM_PROMPT
        assert "first-principles" in SYSTEM_PROMPT.lower()
        assert "advertiser" in SYSTEM_PROMPT.lower()
        assert "Read-only" in SYSTEM_PROMPT
        assert "Recommendations" in SYSTEM_PROMPT

    def test_three_phase_prompt_templates_exist(self) -> None:
        """Data collection, analysis-only, and strategic prompts are importable."""
        from src.agent.prompts import (
            DATA_COLLECTION_SYSTEM,
            STRATEGIC_ANALYSIS_SYSTEM,
            build_analysis_only_prompt,
            build_data_collection_prompt,
            build_strategic_prompt,
        )

        assert "data collection" in DATA_COLLECTION_SYSTEM.lower()
        assert "strategist" in STRATEGIC_ANALYSIS_SYSTEM.lower()

        # Builder functions return non-empty strings
        dc = build_data_collection_prompt(SAMPLE_ACCOUNTS, date(2025, 1, 15))
        assert "Acme Store" in dc
        assert "2025-01-15" in dc

        ao = build_analysis_only_prompt(SAMPLE_ACCOUNTS, "raw metrics here", date(2025, 1, 15))
        assert "raw metrics here" in ao
        assert "2025-01-15" in ao

        sp = build_strategic_prompt(SAMPLE_ACCOUNTS, "briefing here", date(2025, 1, 15))
        assert "briefing here" in sp


# =====================================================================
# 6. run_daily_briefing_optimized — three-phase model routing
# =====================================================================


class TestRunDailyBriefingOptimized:
    """Tests for SideraAgent.run_daily_briefing_optimized."""

    @pytest.mark.asyncio
    async def test_returns_briefing_result_with_three_phases(self) -> None:
        """Should call run_agent_loop three times and return BriefingResult."""
        # Phase 1: Haiku data collection (include % changes to trigger Phase 3)
        phase1 = _make_turn_result(
            text="## Collected Data\nSpend: $10K\nCTR WoW: +15.3%\nCPA WoW: -12.1%",
            cost=0.02,
            num_turns=3,
            duration_ms=2000,
        )

        # Phase 2: Sonnet analysis
        phase2 = _make_turn_result(
            text=(
                "## Executive Summary\nAll good.\n\n"
                "## Recommendations\n"
                "- Action: Scale Campaign A by 10%\n"
                "- Reasoning: ROAS above target\n"
                "- Projected Impact: +8 conv/week\n"
                "- Risk Level: Low\n"
            ),
            cost=0.15,
            num_turns=1,
            duration_ms=3000,
        )

        # Phase 3: Opus strategic
        phase3 = _make_turn_result(
            text="## Strategic Insights\nConsider rebalancing portfolio.",
            cost=0.35,
            num_turns=1,
            duration_ms=5000,
        )

        call_count = 0

        async def mock_run_agent_loop(**kwargs: Any) -> TurnResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return phase1
            elif call_count == 2:
                return phase2
            else:
                return phase3

        with (
            patch("src.agent.core.run_agent_loop", side_effect=mock_run_agent_loop),
            patch("src.agent.core.cache_get", return_value=None),
            patch("src.agent.core.cache_set", return_value=True),
        ):
            agent = SideraAgent()
            result = await agent.run_daily_briefing_optimized(
                user_id="user_42",
                account_ids=SAMPLE_ACCOUNTS,
                analysis_date=date(2025, 1, 15),
            )

        assert isinstance(result, BriefingResult)
        assert result.user_id == "user_42"
        assert "Executive Summary" in result.briefing_text
        assert "Strategic Insights" in result.briefing_text
        # run_agent_loop called 3 times (one per phase)
        assert call_count == 3
        # Cost should be sum of all three phases
        assert abs(result.cost["total_cost_usd"] - 0.52) < 0.01
        assert "phases" in result.cost
        assert "data_collection" in result.cost["phases"]
        assert "tactical_analysis" in result.cost["phases"]
        assert "strategic_analysis" in result.cost["phases"]

    @pytest.mark.asyncio
    async def test_cache_hit_returns_immediately(self) -> None:
        """When Redis cache has a result, no phases are executed."""
        cached_data = {
            "user_id": "user_42",
            "briefing_text": "## Cached Briefing\nFrom Redis.",
            "recommendations": [{"action": "Do nothing"}],
            "cost": {"total_cost_usd": 0.0},
            "session_id": "cached-sess",
        }

        with (
            patch(
                "src.agent.core.run_agent_loop",
                new_callable=AsyncMock,
            ) as mock_loop,
            patch("src.agent.core.cache_get", return_value=cached_data),
        ):
            agent = SideraAgent()
            result = await agent.run_daily_briefing_optimized(
                user_id="user_42",
                account_ids=SAMPLE_ACCOUNTS,
                analysis_date=date(2025, 1, 15),
            )

        assert isinstance(result, BriefingResult)
        assert "Cached Briefing" in result.briefing_text
        assert result.recommendations == [{"action": "Do nothing"}]
        # run_agent_loop should NOT have been called
        mock_loop.assert_not_called()

    @pytest.mark.asyncio
    async def test_force_refresh_bypasses_cache(self) -> None:
        """When force_refresh=True, cache is skipped even if data exists."""
        fresh_result = _make_turn_result(
            text="## Fresh Analysis\nNew data.",
            cost=0.10,
            num_turns=1,
            duration_ms=1000,
        )

        with (
            patch(
                "src.agent.core.run_agent_loop",
                new_callable=AsyncMock,
                return_value=fresh_result,
            ),
            patch("src.agent.core.cache_get") as mock_cache_get,
            patch("src.agent.core.cache_set", return_value=True),
        ):
            agent = SideraAgent()
            result = await agent.run_daily_briefing_optimized(
                user_id="user_42",
                account_ids=SAMPLE_ACCOUNTS,
                analysis_date=date(2025, 1, 15),
                force_refresh=True,
            )

        # cache_get should NOT have been called (force_refresh skips it)
        mock_cache_get.assert_not_called()
        # Fresh analysis should have run
        assert "Fresh Analysis" in result.briefing_text

    @pytest.mark.asyncio
    async def test_cache_miss_runs_full_analysis_and_caches(self) -> None:
        """On cache miss, runs all three phases and stores result in Redis."""
        mock_result = _make_turn_result(
            text="## Phase output",
            cost=0.05,
            num_turns=1,
            duration_ms=500,
        )

        with (
            patch(
                "src.agent.core.run_agent_loop",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch("src.agent.core.cache_get", return_value=None),
            patch("src.agent.core.cache_set", return_value=True) as mock_cache_set,
        ):
            agent = SideraAgent()
            await agent.run_daily_briefing_optimized(
                user_id="user_42",
                account_ids=SAMPLE_ACCOUNTS,
                analysis_date=date(2025, 1, 15),
            )

        # cache_set should have been called with the briefing result
        mock_cache_set.assert_awaited_once()
        call_args = mock_cache_set.call_args
        assert "sidera:briefing:user_42:2025-01-15" in call_args.args[0]
        cached_value = call_args.args[1]
        assert "briefing_text" in cached_value
        assert "recommendations" in cached_value
        # TTL should be 7200 (CACHE_TTL_BRIEFING_RESULT)
        assert call_args.kwargs.get("ttl_seconds") == 7200

    @pytest.mark.asyncio
    async def test_opus_skipped_when_no_strategic_insights(self) -> None:
        """When Opus says 'No additional strategic insights', text is not appended."""
        phase_count = 0

        async def mock_run_agent_loop(**kwargs: Any) -> TurnResult:
            nonlocal phase_count
            phase_count += 1
            if phase_count == 1:
                # Phase 1: include % changes to trigger Phase 3
                return _make_turn_result(
                    text="## Collected Data\nCPA WoW: +25.0%",
                    cost=0.05,
                )
            elif phase_count == 2:
                return _make_turn_result(
                    text="## Tactical Analysis\nGood stuff.",
                    cost=0.05,
                )
            else:
                return _make_turn_result(
                    text="No additional strategic insights. The tactical analysis is thorough.",
                    cost=0.10,
                )

        with (
            patch("src.agent.core.run_agent_loop", side_effect=mock_run_agent_loop),
            patch("src.agent.core.cache_get", return_value=None),
            patch("src.agent.core.cache_set", return_value=True),
        ):
            agent = SideraAgent()
            result = await agent.run_daily_briefing_optimized(
                user_id="user_42",
                account_ids=SAMPLE_ACCOUNTS,
                analysis_date=date(2025, 1, 15),
            )

        # Strategic text should NOT be appended
        assert "No additional strategic insights" not in result.briefing_text
        # But all three phases should still have run
        assert phase_count == 3

    @pytest.mark.asyncio
    async def test_phase1_uses_fast_model(self) -> None:
        """Phase 1 (data collection) should use model_fast (Haiku)."""
        captured_kwargs: list[dict[str, Any]] = []

        async def mock_run_agent_loop(**kwargs: Any) -> TurnResult:
            captured_kwargs.append(kwargs)
            if len(captured_kwargs) == 1:
                # Phase 1: include % to trigger Phase 3
                return _make_turn_result(text="Spend WoW: +20.5%", cost=0.01)
            return _make_turn_result(text="data", cost=0.01)

        with (
            patch("src.agent.core.run_agent_loop", side_effect=mock_run_agent_loop),
            patch("src.agent.core.cache_get", return_value=None),
            patch("src.agent.core.cache_set", return_value=True),
        ):
            agent = SideraAgent()
            await agent.run_daily_briefing_optimized(
                user_id="user_42",
                account_ids=SAMPLE_ACCOUNTS,
                analysis_date=date(2025, 1, 15),
            )

        settings_obj = Settings()

        # Phase 1 should use the fast model
        assert captured_kwargs[0]["model"] == settings_obj.model_fast

        # Phase 2 should use the standard model
        assert captured_kwargs[1]["model"] == settings_obj.model_standard

        # Phase 3 should use the reasoning model
        assert captured_kwargs[2]["model"] == settings_obj.model_reasoning

    @pytest.mark.asyncio
    async def test_phase2_and_3_have_no_tools(self) -> None:
        """Phases 2 and 3 should have no tools and max_turns=1."""
        captured_kwargs: list[dict[str, Any]] = []

        async def mock_run_agent_loop(**kwargs: Any) -> TurnResult:
            captured_kwargs.append(kwargs)
            if len(captured_kwargs) == 1:
                # Phase 1: include % to trigger Phase 3
                return _make_turn_result(text="CPC WoW: -18.0%", cost=0.01)
            return _make_turn_result(text="text", cost=0.01)

        with (
            patch("src.agent.core.run_agent_loop", side_effect=mock_run_agent_loop),
            patch("src.agent.core.cache_get", return_value=None),
            patch("src.agent.core.cache_set", return_value=True),
        ):
            agent = SideraAgent()
            await agent.run_daily_briefing_optimized(
                user_id="user_42",
                account_ids=SAMPLE_ACCOUNTS,
                analysis_date=date(2025, 1, 15),
            )

        assert len(captured_kwargs) == 3

        # Phase 1 should have tools
        assert captured_kwargs[0]["tools"] is not None
        assert len(captured_kwargs[0]["tools"]) > 0

        # Phase 2: no tools, max 1 turn
        assert captured_kwargs[1]["tools"] is None
        assert captured_kwargs[1]["max_turns"] == 1

        # Phase 3: no tools, max 1 turn
        assert captured_kwargs[2]["tools"] is None
        assert captured_kwargs[2]["max_turns"] == 1


# =====================================================================
# Pipeline: previous_output injection in run_skill
# =====================================================================


class TestPipelineOutputInjection:
    """Test that run_skill injects previous_output into system prompt."""

    def _make_skill(self, **overrides: Any) -> MagicMock:
        """Create a minimal SkillDefinition-like object."""
        skill = MagicMock()
        skill.id = overrides.get("id", "test_skill")
        skill.name = overrides.get("name", "Test Skill")
        skill.system_supplement = overrides.get(
            "system_supplement",
            "Skill supplement.",
        )
        skill.context_files = overrides.get("context_files", ())
        skill.references = overrides.get("references", ())
        skill.output_format = overrides.get("output_format", "")
        skill.business_guidance = overrides.get(
            "business_guidance",
            "",
        )
        skill.prompt_template = overrides.get(
            "prompt_template",
            "Analyze {analysis_date}.",
        )
        skill.tools_required = overrides.get(
            "tools_required",
            ("get_meta_campaigns",),
        )
        skill.model = overrides.get("model", "sonnet")
        skill.max_turns = overrides.get("max_turns", 10)
        return skill

    @pytest.mark.asyncio
    async def test_previous_output_injected_into_system_prompt(self):
        """When params has previous_output, it appears in system."""
        captured: dict[str, Any] = {}

        async def mock_loop(**kwargs: Any) -> TurnResult:
            captured.update(kwargs)
            return _make_turn_result("Analysis complete.")

        agent = SideraAgent()
        with patch(
            "src.agent.core.run_agent_loop",
            side_effect=mock_loop,
        ):
            await agent.run_skill(
                skill=self._make_skill(),
                user_id="u1",
                account_ids=SAMPLE_ACCOUNTS,
                params={"previous_output": "CPA spiked 40%"},
            )

        system = captured["system_prompt"]
        assert "# Previous Skill Output" in system
        assert "CPA spiked 40%" in system

    @pytest.mark.asyncio
    async def test_no_injection_when_empty(self):
        """No previous output section when previous_output is empty."""
        captured: dict[str, Any] = {}

        async def mock_loop(**kwargs: Any) -> TurnResult:
            captured.update(kwargs)
            return _make_turn_result("Analysis complete.")

        agent = SideraAgent()
        with patch(
            "src.agent.core.run_agent_loop",
            side_effect=mock_loop,
        ):
            await agent.run_skill(
                skill=self._make_skill(),
                user_id="u1",
                account_ids=SAMPLE_ACCOUNTS,
            )

        system = captured["system_prompt"]
        assert "# Previous Skill Output" not in system

    @pytest.mark.asyncio
    async def test_previous_output_truncated(self):
        """Long previous output is truncated at 4000 chars."""
        captured: dict[str, Any] = {}

        async def mock_loop(**kwargs: Any) -> TurnResult:
            captured.update(kwargs)
            return _make_turn_result("Done.")

        big_output = "x" * 6000
        agent = SideraAgent()
        with patch(
            "src.agent.core.run_agent_loop",
            side_effect=mock_loop,
        ):
            await agent.run_skill(
                skill=self._make_skill(),
                user_id="u1",
                account_ids=SAMPLE_ACCOUNTS,
                params={"previous_output": big_output},
            )

        system = captured["system_prompt"]
        assert "# Previous Skill Output" in system
        assert "[... truncated ...]" in system
        # Should contain only first 4000 x's
        assert "x" * 4000 in system
        assert "x" * 4001 not in system

    @pytest.mark.asyncio
    async def test_previous_output_in_template(self):
        """previous_output available as template placeholder."""
        captured: dict[str, Any] = {}

        async def mock_loop(**kwargs: Any) -> TurnResult:
            captured.update(kwargs)
            return _make_turn_result("Done.")

        skill = self._make_skill(
            prompt_template=("Prior: {previous_output}\nDate: {analysis_date}"),
        )
        agent = SideraAgent()
        with patch(
            "src.agent.core.run_agent_loop",
            side_effect=mock_loop,
        ):
            await agent.run_skill(
                skill=skill,
                user_id="u1",
                account_ids=SAMPLE_ACCOUNTS,
                params={"previous_output": "Anomaly detected"},
            )

        prompt = captured["user_prompt"]
        assert "Prior: Anomaly detected" in prompt

    @pytest.mark.asyncio
    async def test_template_default_when_no_previous(self):
        """previous_output defaults to empty string in template."""
        captured: dict[str, Any] = {}

        async def mock_loop(**kwargs: Any) -> TurnResult:
            captured.update(kwargs)
            return _make_turn_result("Done.")

        skill = self._make_skill(
            prompt_template="Prior: [{previous_output}] end",
        )
        agent = SideraAgent()
        with patch(
            "src.agent.core.run_agent_loop",
            side_effect=mock_loop,
        ):
            await agent.run_skill(
                skill=skill,
                user_id="u1",
                account_ids=SAMPLE_ACCOUNTS,
            )

        prompt = captured["user_prompt"]
        assert "Prior: [] end" in prompt
