"""Tests for graceful degradation and volatility gating in SideraAgent.

Covers:
- _compute_volatility_score: static percentage extraction from collected data
- _get_last_known_analysis: fallback to Redis cache or database
- Phase 1 failure -> graceful fallback to stale data
- Phase 3 volatility gate: skip Opus when metrics are stable
- force_refresh override for the volatility gate
- degradation_status field on BriefingResult

All Anthropic API interactions, cache calls, and DB sessions are mocked.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.api_client import TurnResult
from src.agent.core import BriefingResult, SideraAgent

# =====================================================================
# Helpers
# =====================================================================


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


def _make_agent() -> SideraAgent:
    """Create a SideraAgent instance."""
    return SideraAgent()


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
]


# =====================================================================
# 1. _compute_volatility_score
# =====================================================================


class TestComputeVolatilityScore:
    """Tests for SideraAgent._compute_volatility_score (static method)."""

    def test_single_positive_percentage(self) -> None:
        """'+15.3%' should return 15.3."""
        result = SideraAgent._compute_volatility_score("Spend changed +15.3%")
        assert result == pytest.approx(15.3)

    def test_multiple_percentages_returns_max_absolute(self) -> None:
        """With '-8.2%' and '+3.1%', should return 8.2 (the max absolute)."""
        data = "CPA changed -8.2%, CTR improved +3.1%"
        result = SideraAgent._compute_volatility_score(data)
        assert result == pytest.approx(8.2)

    def test_no_percentages_returns_zero(self) -> None:
        """Text with no percentage patterns should return 0.0."""
        result = SideraAgent._compute_volatility_score(
            "All metrics stable. No significant changes."
        )
        assert result == 0.0

    def test_mixed_text_and_percentages(self) -> None:
        """Should extract percentages from complex mixed text."""
        data = (
            "Campaign A: Spend $10,232 (up +22.5% WoW)\n"
            "Campaign B: CPA $45.30 (down -4.1% WoW)\n"
            "Campaign C: ROAS 3.8x (stable, +0.5% WoW)\n"
            "Total impressions: 1,234,567"
        )
        result = SideraAgent._compute_volatility_score(data)
        # Max absolute is 22.5
        assert result == pytest.approx(22.5)

    def test_negative_only_percentage(self) -> None:
        """A single negative percentage should return its absolute value."""
        result = SideraAgent._compute_volatility_score("Revenue down -7.8%")
        assert result == pytest.approx(7.8)

    def test_percentage_without_sign(self) -> None:
        """A percentage without +/- sign should still be parsed."""
        result = SideraAgent._compute_volatility_score("CTR improved 12.4%")
        assert result == pytest.approx(12.4)


# =====================================================================
# 2. _get_last_known_analysis
# =====================================================================


class TestGetLastKnownAnalysis:
    """Tests for SideraAgent._get_last_known_analysis."""

    @pytest.mark.asyncio
    async def test_falls_back_to_redis_cache(self) -> None:
        """When Redis has a cached result, return it as a BriefingResult."""
        agent = _make_agent()

        cached_data = {
            "briefing_text": "## Cached Briefing\nFrom yesterday.",
            "recommendations": [{"action": "Hold steady"}],
            "cost": {"total_cost_usd": 0.10},
            "session_id": "cached-sess",
        }

        with patch("src.agent.core.cache_get", return_value=cached_data):
            result = await agent._get_last_known_analysis("user-42", date(2025, 6, 15))

        assert result is not None
        assert isinstance(result, BriefingResult)
        assert "Cached Briefing" in result.briefing_text
        assert result.recommendations == [{"action": "Hold steady"}]

    @pytest.mark.asyncio
    async def test_falls_back_to_database(self) -> None:
        """When cache misses but DB has recent analysis, return that."""
        agent = _make_agent()

        mock_analysis = MagicMock()
        mock_analysis.id = 42
        mock_analysis.briefing_content = "## DB Briefing\nFrom 3 days ago."
        mock_analysis.recommendations = [{"action": "Reduce Meta spend"}]

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.agent.core.cache_get", return_value=None),
            patch(
                "src.db.service.get_analyses_for_period",
                return_value=[mock_analysis],
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=mock_session,
            ),
        ):
            result = await agent._get_last_known_analysis("user-42", date(2025, 6, 15))

        assert result is not None
        assert isinstance(result, BriefingResult)
        assert "DB Briefing" in result.briefing_text
        assert result.recommendations == [{"action": "Reduce Meta spend"}]

    @pytest.mark.asyncio
    async def test_returns_none_when_no_fallback_available(self) -> None:
        """When both cache and DB have nothing, return None."""
        agent = _make_agent()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.agent.core.cache_get", return_value=None),
            patch(
                "src.db.service.get_analyses_for_period",
                return_value=[],
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=mock_session,
            ),
        ):
            result = await agent._get_last_known_analysis("user-42", date(2025, 6, 15))

        assert result is None


# =====================================================================
# 3. Phase 1 failure -> graceful degradation in run_daily_briefing_optimized
# =====================================================================


class TestPhase1Fallback:
    """Tests for graceful degradation when Phase 1 (data collection) fails."""

    @pytest.mark.asyncio
    async def test_phase1_failure_returns_stale_cached_result(self) -> None:
        """Phase 1 failure should fall back to cached result with 'stale' status."""
        cached_data = {
            "briefing_text": "## Yesterday's Briefing\nStill valid.",
            "recommendations": [],
            "cost": {"total_cost_usd": 0.42},
            "session_id": "old-sess",
        }

        call_count = 0

        async def failing_phase1(**kwargs: Any) -> TurnResult:
            nonlocal call_count
            call_count += 1
            raise ConnectionError("API unreachable")

        with (
            patch("src.agent.core.run_agent_loop", side_effect=failing_phase1),
            patch("src.agent.core.cache_get") as mock_cache_get,
            patch("src.agent.core.cache_set", return_value=True),
            patch("src.middleware.sentry_setup.capture_exception"),
        ):
            # First cache_get call (result cache check at top) returns None
            # Second cache_get call (inside _get_last_known_analysis) returns cached_data
            mock_cache_get.side_effect = [None, cached_data]

            agent = SideraAgent()
            result = await agent.run_daily_briefing_optimized(
                user_id="user-42",
                account_ids=SAMPLE_ACCOUNTS,
                analysis_date=date(2025, 6, 15),
            )

        assert isinstance(result, BriefingResult)
        assert result.degradation_status == "stale"
        assert "Yesterday's Briefing" in result.briefing_text
        assert result.cost.get("note") == "stale_fallback"

    @pytest.mark.asyncio
    async def test_phase1_failure_falls_back_to_db(self) -> None:
        """Phase 1 failure with cache miss should try DB for recent analysis."""
        mock_analysis = MagicMock()
        mock_analysis.id = 99
        mock_analysis.briefing_content = "## DB Fallback\nFrom last Tuesday."
        mock_analysis.recommendations = []

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        async def failing_phase1(**kwargs: Any) -> TurnResult:
            raise ConnectionError("Network error")

        with (
            patch("src.agent.core.run_agent_loop", side_effect=failing_phase1),
            patch("src.agent.core.cache_get", return_value=None),
            patch("src.agent.core.cache_set", return_value=True),
            patch(
                "src.db.service.get_analyses_for_period",
                return_value=[mock_analysis],
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=mock_session,
            ),
            patch("src.middleware.sentry_setup.capture_exception"),
        ):
            agent = SideraAgent()
            result = await agent.run_daily_briefing_optimized(
                user_id="user-42",
                account_ids=SAMPLE_ACCOUNTS,
                analysis_date=date(2025, 6, 15),
            )

        assert result.degradation_status == "stale"
        assert "DB Fallback" in result.briefing_text

    @pytest.mark.asyncio
    async def test_phase1_failure_no_fallback_raises(self) -> None:
        """Phase 1 failure with no cache and no DB data should re-raise."""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        async def failing_phase1(**kwargs: Any) -> TurnResult:
            raise ConnectionError("Total outage")

        with (
            patch("src.agent.core.run_agent_loop", side_effect=failing_phase1),
            patch("src.agent.core.cache_get", return_value=None),
            patch("src.agent.core.cache_set", return_value=True),
            patch(
                "src.db.service.get_analyses_for_period",
                return_value=[],
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=mock_session,
            ),
            patch("src.middleware.sentry_setup.capture_exception"),
        ):
            agent = SideraAgent()
            with pytest.raises(ConnectionError, match="Total outage"):
                await agent.run_daily_briefing_optimized(
                    user_id="user-42",
                    account_ids=SAMPLE_ACCOUNTS,
                    analysis_date=date(2025, 6, 15),
                )


# =====================================================================
# 4. Phase 3 volatility gate
# =====================================================================


class TestVolatilityGate:
    """Tests for the Phase 3 Opus skip when metrics are stable."""

    @pytest.mark.asyncio
    async def test_low_volatility_skips_phase3(self) -> None:
        """Volatility < 10% should skip Phase 3 and set skipped=True in cost."""
        call_count = 0

        async def mock_run_agent_loop(**kwargs: Any) -> TurnResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Phase 1: stable data with small percentages
                return _make_turn_result(
                    text="Spend: $10,000 (+2.1% WoW)\nCPA: $25.30 (-1.5% WoW)",
                    cost=0.02,
                )
            elif call_count == 2:
                # Phase 2: tactical analysis
                return _make_turn_result(
                    text="## Briefing\nAll stable.",
                    cost=0.15,
                )
            else:
                # Phase 3 should NOT be reached
                return _make_turn_result(
                    text="## Strategic\nShould not appear.",
                    cost=0.35,
                )

        with (
            patch("src.agent.core.run_agent_loop", side_effect=mock_run_agent_loop),
            patch("src.agent.core.cache_get", return_value=None),
            patch("src.agent.core.cache_set", return_value=True),
        ):
            agent = SideraAgent()
            result = await agent.run_daily_briefing_optimized(
                user_id="user-42",
                account_ids=SAMPLE_ACCOUNTS,
                analysis_date=date(2025, 6, 15),
            )

        # Only 2 phases should have run (Phase 3 skipped)
        assert call_count == 2
        # Cost dict should indicate Phase 3 was skipped
        phase3_cost = result.cost["phases"]["strategic_analysis"]
        assert phase3_cost["skipped"] is True
        assert phase3_cost["volatility"] < 10.0

    @pytest.mark.asyncio
    async def test_high_volatility_runs_phase3(self) -> None:
        """Volatility >= 10% should run Phase 3 (Opus)."""
        call_count = 0

        async def mock_run_agent_loop(**kwargs: Any) -> TurnResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Phase 1: volatile data with large percentage swings
                return _make_turn_result(
                    text="Spend: $10,000 (+25.0% WoW)\nCPA: $45.30 (+18.2% WoW)",
                    cost=0.02,
                )
            elif call_count == 2:
                # Phase 2
                return _make_turn_result(
                    text="## Briefing\nVolatile period.",
                    cost=0.15,
                )
            else:
                # Phase 3: Opus strategic
                return _make_turn_result(
                    text="## Strategic Insights\nConsider rebalancing.",
                    cost=0.35,
                )

        with (
            patch("src.agent.core.run_agent_loop", side_effect=mock_run_agent_loop),
            patch("src.agent.core.cache_get", return_value=None),
            patch("src.agent.core.cache_set", return_value=True),
        ):
            agent = SideraAgent()
            result = await agent.run_daily_briefing_optimized(
                user_id="user-42",
                account_ids=SAMPLE_ACCOUNTS,
                analysis_date=date(2025, 6, 15),
            )

        # All 3 phases should have run
        assert call_count == 3
        # Strategic insights should be in the output
        assert "Strategic Insights" in result.briefing_text

    @pytest.mark.asyncio
    async def test_force_refresh_runs_phase3_despite_low_volatility(self) -> None:
        """force_refresh=True should run Phase 3 even when volatility < 10%."""
        call_count = 0

        async def mock_run_agent_loop(**kwargs: Any) -> TurnResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Phase 1: stable data
                return _make_turn_result(
                    text="Spend: $10,000 (+1.0% WoW)\nCPA: $25.30 (-0.5% WoW)",
                    cost=0.02,
                )
            elif call_count == 2:
                # Phase 2
                return _make_turn_result(
                    text="## Briefing\nStable.",
                    cost=0.15,
                )
            else:
                # Phase 3: should run because force_refresh=True
                return _make_turn_result(
                    text="## Strategic Insights\nForced deep analysis.",
                    cost=0.35,
                )

        with (
            patch("src.agent.core.run_agent_loop", side_effect=mock_run_agent_loop),
            patch("src.agent.core.cache_get", return_value=None),
            patch("src.agent.core.cache_set", return_value=True),
        ):
            agent = SideraAgent()
            result = await agent.run_daily_briefing_optimized(
                user_id="user-42",
                account_ids=SAMPLE_ACCOUNTS,
                analysis_date=date(2025, 6, 15),
                force_refresh=True,
            )

        # All 3 phases should have run despite low volatility
        assert call_count == 3
        assert "Strategic Insights" in result.briefing_text
