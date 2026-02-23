"""Tests for the gap detection -> skill creator connection.

Verifies:
- scan_gaps_for_skill_suggestions: no gaps, below threshold, unknown domain skip,
  role-classified domains skipped, skill-classified domains generate suggestions.
- _classify_gap_scope: LLM returns role/skill, defaults to skill on error.
- _generate_skill_suggestion_from_gaps: success, not warranted, missing description.

All DB and LLM calls are mocked; no database or API connection needed.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.skills.reflection_evolution import (
    _MIN_GAPS_FOR_SKILL_SUGGESTION,
    _classify_gap_scope,
    _generate_skill_suggestion_from_gaps,
    scan_gaps_for_skill_suggestions,
)

# ============================================================
# Helpers
# ============================================================


def _make_gap_memory(
    id: int,
    domain: str,
    title: str = "Gap found",
    content: str | None = None,
    days_ago: int = 5,
) -> SimpleNamespace:
    """Create a fake gap-detection memory."""
    if content is None:
        content = f"[{date.today()}] [Gap Detection] Missing capability in {domain}"
    return SimpleNamespace(
        id=id,
        title=title,
        content=content,
        memory_type="insight",
        confidence=0.7,
        created_at=datetime.now() - timedelta(days=days_ago),
        evidence={"gap_domain": domain},
    )


class FakeAgentResult:
    """Minimal stand-in for an agent loop result."""

    def __init__(self, text: str):
        self.text = text


def _mock_db_session():
    """Return mocks for get_db_session context manager + search_role_memories."""
    mock_session = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_session_ctx


# ============================================================
# Tests -- scan_gaps_for_skill_suggestions
# ============================================================


class TestScanGapsForSkillSuggestions:
    """Tests for the top-level scan_gaps_for_skill_suggestions function."""

    @pytest.mark.asyncio
    async def test_scan_gaps_no_observations(self):
        """No gap memories at all -> empty result."""
        session_ctx = _mock_db_session()

        with (
            patch(
                "src.db.service.search_role_memories",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=session_ctx,
            ),
        ):
            result = await scan_gaps_for_skill_suggestions(
                role_id="performance_media_buyer",
                department_id="marketing",
                user_id="u1",
            )

        assert result == []

    @pytest.mark.asyncio
    async def test_scan_gaps_below_threshold(self):
        """Only 1 gap observation (threshold is 2) -> empty result."""
        gaps = [_make_gap_memory(id=1, domain="compliance")]
        session_ctx = _mock_db_session()

        with (
            patch(
                "src.db.service.search_role_memories",
                new_callable=AsyncMock,
                return_value=gaps,
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=session_ctx,
            ),
        ):
            result = await scan_gaps_for_skill_suggestions(
                role_id="buyer",
                department_id="marketing",
                user_id="u1",
            )

        assert result == []
        # Sanity: threshold really is 2
        assert _MIN_GAPS_FOR_SKILL_SUGGESTION == 2

    @pytest.mark.asyncio
    async def test_scan_gaps_classifies_as_role(self):
        """Gap classified as 'role' by LLM -> skipped (handled by role pipeline)."""
        gaps = [
            _make_gap_memory(id=i, domain="compliance")
            for i in range(_MIN_GAPS_FOR_SKILL_SUGGESTION)
        ]
        session_ctx = _mock_db_session()

        classify_result = FakeAgentResult(text="role")

        with (
            patch(
                "src.db.service.search_role_memories",
                new_callable=AsyncMock,
                return_value=gaps,
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=session_ctx,
            ),
            patch(
                "src.agent.api_client.run_agent_loop",
                new_callable=AsyncMock,
                return_value=classify_result,
            ),
        ):
            result = await scan_gaps_for_skill_suggestions(
                role_id="buyer",
                department_id="marketing",
                user_id="u1",
            )

        assert result == []

    @pytest.mark.asyncio
    async def test_scan_gaps_classifies_as_skill(self):
        """Gap classified as 'skill' -> suggestion generated."""
        gaps = [
            _make_gap_memory(id=i, domain="weekly_reporting")
            for i in range(_MIN_GAPS_FOR_SKILL_SUGGESTION)
        ]
        session_ctx = _mock_db_session()

        # First call: _classify_gap_scope -> "skill"
        classify_result = FakeAgentResult(text="skill")

        # Second call: _generate_skill_suggestion_from_gaps -> valid suggestion
        suggestion_json = json.dumps(
            {
                "should_create": True,
                "skill_name": "weekly_report_generator",
                "display_name": "Weekly Report Generator",
                "description": "Generates weekly performance reports automatically.",
                "category": "reporting",
                "suggested_role_id": "reporting_analyst",
                "reasoning": "Multiple gaps indicate need for automated weekly reports.",
            }
        )
        suggestion_result = FakeAgentResult(text=suggestion_json)

        call_count = 0

        async def _side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return classify_result
            return suggestion_result

        with (
            patch(
                "src.db.service.search_role_memories",
                new_callable=AsyncMock,
                return_value=gaps,
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=session_ctx,
            ),
            patch(
                "src.agent.api_client.run_agent_loop",
                new_callable=AsyncMock,
                side_effect=_side_effect,
            ),
        ):
            result = await scan_gaps_for_skill_suggestions(
                role_id="buyer",
                department_id="marketing",
                user_id="u1",
            )

        assert len(result) == 1
        assert result[0]["domain"] == "weekly_reporting"
        assert result[0]["suggested_skill_name"] == "weekly_report_generator"
        assert result[0]["display_name"] == "Weekly Report Generator"
        assert result[0]["suggested_description"] == (
            "Generates weekly performance reports automatically."
        )
        assert result[0]["suggested_category"] == "reporting"
        assert result[0]["suggested_role_id"] == "reporting_analyst"
        assert result[0]["source_role_id"] == "buyer"
        assert result[0]["department_id"] == "marketing"
        assert result[0]["gap_count"] == _MIN_GAPS_FOR_SKILL_SUGGESTION

    @pytest.mark.asyncio
    async def test_unknown_domain_skipped(self):
        """Gaps with domain 'unknown' are filtered out even if count >= threshold."""
        gaps = [
            _make_gap_memory(id=i, domain="unknown")
            for i in range(_MIN_GAPS_FOR_SKILL_SUGGESTION + 3)
        ]
        session_ctx = _mock_db_session()

        with (
            patch(
                "src.db.service.search_role_memories",
                new_callable=AsyncMock,
                return_value=gaps,
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=session_ctx,
            ),
        ):
            result = await scan_gaps_for_skill_suggestions(
                role_id="buyer",
                department_id="marketing",
                user_id="u1",
            )

        assert result == []

    @pytest.mark.asyncio
    async def test_scan_error_returns_empty(self):
        """Any exception during scan -> empty result (non-fatal)."""
        with patch(
            "src.db.session.get_db_session",
            side_effect=Exception("DB down"),
        ):
            result = await scan_gaps_for_skill_suggestions(
                role_id="buyer",
                department_id="marketing",
                user_id="u1",
            )

        assert result == []


# ============================================================
# Tests -- _classify_gap_scope
# ============================================================


class TestClassifyGapScope:
    """Tests for the _classify_gap_scope helper."""

    @pytest.mark.asyncio
    async def test_classifies_as_role(self):
        """LLM returns 'role' -> function returns 'role'."""
        gaps = [_make_gap_memory(id=1, domain="compliance")]
        llm_result = FakeAgentResult(text="role")

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=llm_result,
        ):
            scope = await _classify_gap_scope("compliance", gaps)

        assert scope == "role"

    @pytest.mark.asyncio
    async def test_classifies_as_skill(self):
        """LLM returns 'skill' -> function returns 'skill'."""
        gaps = [_make_gap_memory(id=1, domain="weekly_report")]
        llm_result = FakeAgentResult(text="skill")

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=llm_result,
        ):
            scope = await _classify_gap_scope("weekly_report", gaps)

        assert scope == "skill"

    @pytest.mark.asyncio
    async def test_defaults_to_skill_on_ambiguous_response(self):
        """LLM returns something other than 'role'/'skill' -> defaults to 'skill'."""
        gaps = [_make_gap_memory(id=1, domain="analytics")]
        llm_result = FakeAgentResult(text="maybe both")

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=llm_result,
        ):
            scope = await _classify_gap_scope("analytics", gaps)

        assert scope == "skill"

    @pytest.mark.asyncio
    async def test_defaults_to_skill_on_error(self):
        """LLM error -> defaults to 'skill' (lighter-weight default)."""
        gaps = [_make_gap_memory(id=1, domain="analytics")]

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            side_effect=Exception("API error"),
        ):
            scope = await _classify_gap_scope("analytics", gaps)

        assert scope == "skill"

    @pytest.mark.asyncio
    async def test_strips_whitespace_from_response(self):
        """LLM response with whitespace/newlines is still handled."""
        gaps = [_make_gap_memory(id=1, domain="compliance")]
        llm_result = FakeAgentResult(text="  role  \n")

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=llm_result,
        ):
            scope = await _classify_gap_scope("compliance", gaps)

        assert scope == "role"


# ============================================================
# Tests -- _generate_skill_suggestion_from_gaps
# ============================================================


class TestGenerateSkillSuggestionFromGaps:
    """Tests for the _generate_skill_suggestion_from_gaps helper."""

    @pytest.mark.asyncio
    async def test_generate_skill_suggestion_success(self):
        """LLM returns valid suggestion -> dict with all expected fields."""
        gaps = [_make_gap_memory(id=i, domain="budget_alerts") for i in range(3)]
        suggestion_json = json.dumps(
            {
                "should_create": True,
                "skill_name": "budget_alert_skill",
                "display_name": "Budget Alert Monitor",
                "description": "Monitors campaign budgets and alerts on overspend.",
                "category": "monitoring",
                "suggested_role_id": "performance_media_buyer",
                "reasoning": "Three gap observations about missing budget alerts.",
            }
        )
        llm_result = FakeAgentResult(text=suggestion_json)

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=llm_result,
        ):
            result = await _generate_skill_suggestion_from_gaps(
                domain="budget_alerts",
                gaps=gaps,
                role_id="buyer",
                department_id="marketing",
            )

        assert result is not None
        assert result["domain"] == "budget_alerts"
        assert result["suggested_skill_name"] == "budget_alert_skill"
        assert result["display_name"] == "Budget Alert Monitor"
        assert result["suggested_description"] == (
            "Monitors campaign budgets and alerts on overspend."
        )
        assert result["suggested_category"] == "monitoring"
        assert result["suggested_role_id"] == "performance_media_buyer"
        assert result["gap_count"] == 3
        assert result["source_role_id"] == "buyer"
        assert result["department_id"] == "marketing"
        assert "reasoning" in result
        assert "gap_summary" in result

    @pytest.mark.asyncio
    async def test_generate_skill_suggestion_not_warranted(self):
        """LLM returns should_create=false -> None."""
        gaps = [_make_gap_memory(id=i, domain="compliance") for i in range(3)]
        llm_result = FakeAgentResult(
            text='{"should_create": false, "reasoning": "existing skills cover this"}'
        )

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=llm_result,
        ):
            result = await _generate_skill_suggestion_from_gaps(
                domain="compliance",
                gaps=gaps,
                role_id="buyer",
                department_id="marketing",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_generate_skill_suggestion_missing_description(self):
        """LLM returns should_create=true but empty description -> None."""
        gaps = [_make_gap_memory(id=i, domain="compliance") for i in range(3)]
        suggestion_json = json.dumps(
            {
                "should_create": True,
                "skill_name": "compliance_check",
                "display_name": "Compliance Check",
                "description": "",
                "category": "analysis",
                "suggested_role_id": "buyer",
                "reasoning": "needed",
            }
        )
        llm_result = FakeAgentResult(text=suggestion_json)

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=llm_result,
        ):
            result = await _generate_skill_suggestion_from_gaps(
                domain="compliance",
                gaps=gaps,
                role_id="buyer",
                department_id="marketing",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_generate_skill_suggestion_llm_error(self):
        """LLM raises exception -> None (non-fatal)."""
        gaps = [_make_gap_memory(id=i, domain="analytics") for i in range(3)]

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            side_effect=Exception("API down"),
        ):
            result = await _generate_skill_suggestion_from_gaps(
                domain="analytics",
                gaps=gaps,
                role_id="buyer",
                department_id="marketing",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_generate_skill_suggestion_invalid_json(self):
        """LLM returns unparseable text -> None."""
        gaps = [_make_gap_memory(id=i, domain="analytics") for i in range(3)]
        llm_result = FakeAgentResult(text="Not valid JSON at all")

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=llm_result,
        ):
            result = await _generate_skill_suggestion_from_gaps(
                domain="analytics",
                gaps=gaps,
                role_id="buyer",
                department_id="marketing",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_generate_skill_suggestion_markdown_fenced_json(self):
        """LLM wraps JSON in markdown code fences -> still parsed."""
        gaps = [_make_gap_memory(id=i, domain="seo") for i in range(2)]
        inner_json = json.dumps(
            {
                "should_create": True,
                "skill_name": "seo_audit",
                "display_name": "SEO Audit",
                "description": "Runs periodic SEO audits on landing pages.",
                "category": "analysis",
                "suggested_role_id": "buyer",
                "reasoning": "Repeated gaps around SEO monitoring.",
            }
        )
        llm_result = FakeAgentResult(text=f"```json\n{inner_json}\n```")

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=llm_result,
        ):
            result = await _generate_skill_suggestion_from_gaps(
                domain="seo",
                gaps=gaps,
                role_id="buyer",
                department_id="marketing",
            )

        assert result is not None
        assert result["suggested_skill_name"] == "seo_audit"
        assert result["display_name"] == "SEO Audit"

    @pytest.mark.asyncio
    async def test_fallback_skill_name_when_not_provided(self):
        """If LLM omits skill_name, a default is generated from the domain."""
        gaps = [_make_gap_memory(id=i, domain="data quality") for i in range(2)]
        suggestion_json = json.dumps(
            {
                "should_create": True,
                # No "skill_name" key
                "display_name": "Data Quality Checker",
                "description": "Validates data quality across pipelines.",
                "category": "monitoring",
                "reasoning": "Gaps around data quality issues.",
            }
        )
        llm_result = FakeAgentResult(text=suggestion_json)

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=llm_result,
        ):
            result = await _generate_skill_suggestion_from_gaps(
                domain="data quality",
                gaps=gaps,
                role_id="buyer",
                department_id="marketing",
            )

        assert result is not None
        # Fallback: domain.lower().replace(' ', '_') + '_skill'
        assert result["suggested_skill_name"] == "data_quality_skill"
        # suggested_role_id falls back to the requesting role_id
        assert result["suggested_role_id"] == "buyer"
