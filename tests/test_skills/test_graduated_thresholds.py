"""Tests for graduated evidence thresholds in reflection evolution.

Verifies that the tiered threshold system correctly gates which fields
can be proposed for modification based on lesson count.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.skills.reflection_evolution import (
    _TIER_FIELDS,
    _TIER_MAJOR,
    _generate_proposal_from_lessons,
    _get_allowed_fields,
    _get_risk_level,
)

# ============================================================
# Helpers
# ============================================================


@dataclass
class FakeLesson:
    id: int = 1
    title: str = "Test lesson"
    content: str = "Something went wrong"
    memory_type: str = "lesson"
    confidence: float = 0.8
    source_skill_id: str = "creative_analysis"
    created_at: datetime = None  # type: ignore
    evidence: dict = None  # type: ignore

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)
        if self.evidence is None:
            self.evidence = {}


@dataclass
class FakeAgentResult:
    text: str = '{"should_modify": false, "reasoning": "no change needed"}'
    cost: dict = None  # type: ignore

    def __post_init__(self):
        if self.cost is None:
            self.cost = {"total_cost_usd": 0.01}


# ============================================================
# Tests — _get_allowed_fields
# ============================================================


class TestGetAllowedFields:
    def test_below_minimum_returns_empty(self):
        """Below minimum threshold should return empty set."""
        assert _get_allowed_fields(0) == frozenset()
        assert _get_allowed_fields(1) == frozenset()
        assert _get_allowed_fields(2) == frozenset()

    def test_minor_tier(self):
        """3 lessons should only allow business_guidance + references."""
        fields = _get_allowed_fields(3)
        assert fields == frozenset({"business_guidance", "references"})

    def test_moderate_tier(self):
        """5 lessons should allow business_guidance + system_supplement + references."""
        fields = _get_allowed_fields(5)
        assert "business_guidance" in fields
        assert "system_supplement" in fields
        assert "references" in fields
        assert len(fields) == 3

    def test_major_tier(self):
        """7 lessons should allow all major fields."""
        fields = _get_allowed_fields(7)
        assert "business_guidance" in fields
        assert "system_supplement" in fields
        assert "prompt_template" in fields
        assert "output_format" in fields
        assert "model" in fields
        assert "max_turns" in fields
        assert "references" in fields

    def test_between_tiers(self):
        """4 lessons should still be minor tier."""
        fields = _get_allowed_fields(4)
        assert fields == frozenset({"business_guidance", "references"})

    def test_above_major(self):
        """10 lessons should use major tier."""
        fields = _get_allowed_fields(10)
        assert fields == _TIER_FIELDS[_TIER_MAJOR]


# ============================================================
# Tests — _get_risk_level
# ============================================================


class TestGetRiskLevel:
    def test_minor_level(self):
        assert _get_risk_level(3) == "minor"
        assert _get_risk_level(4) == "minor"

    def test_moderate_level(self):
        assert _get_risk_level(5) == "moderate"
        assert _get_risk_level(6) == "moderate"

    def test_major_level(self):
        assert _get_risk_level(7) == "major"
        assert _get_risk_level(10) == "major"


# ============================================================
# Tests — _generate_proposal_from_lessons with tiers
# ============================================================


class TestGraduatedProposals:
    @pytest.mark.asyncio
    async def test_minor_tier_only_allows_business_guidance(self):
        """With 3 lessons, only business_guidance should be allowed."""
        haiku_response = FakeAgentResult(
            text='{"should_modify": true, "field": "system_supplement", '
            '"addition": "Some addition.", "reasoning": "test"}'
        )
        lessons = [FakeLesson(id=i) for i in range(3)]

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=haiku_response,
        ):
            result = await _generate_proposal_from_lessons(
                skill_id="test_skill",
                lessons=lessons,
                role_id="buyer",
                department_id="marketing",
                lesson_count=3,
            )

        # system_supplement not allowed at minor tier — should default to business_guidance
        assert result is not None
        assert "business_guidance" in result["changes"]
        assert result["risk_level"] == "minor"
        assert result["lesson_count"] == 3

    @pytest.mark.asyncio
    async def test_moderate_tier_allows_system_supplement(self):
        """With 5 lessons, system_supplement should be allowed."""
        haiku_response = FakeAgentResult(
            text='{"should_modify": true, "field": "system_supplement", '
            '"addition": "Some addition.", "reasoning": "test"}'
        )
        lessons = [FakeLesson(id=i) for i in range(5)]

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=haiku_response,
        ):
            result = await _generate_proposal_from_lessons(
                skill_id="test_skill",
                lessons=lessons,
                role_id="buyer",
                department_id="marketing",
                lesson_count=5,
            )

        assert result is not None
        assert "system_supplement" in result["changes"]
        assert result["risk_level"] == "moderate"

    @pytest.mark.asyncio
    async def test_major_tier_allows_prompt_template(self):
        """With 7 lessons, prompt_template should be allowed."""
        haiku_response = FakeAgentResult(
            text='{"should_modify": true, "field": "prompt_template", '
            '"addition": "Some addition.", "reasoning": "test"}'
        )
        lessons = [FakeLesson(id=i) for i in range(7)]

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=haiku_response,
        ):
            result = await _generate_proposal_from_lessons(
                skill_id="test_skill",
                lessons=lessons,
                role_id="buyer",
                department_id="marketing",
                lesson_count=7,
            )

        assert result is not None
        assert "prompt_template" in result["changes"]
        assert result["risk_level"] == "major"
        assert result["lesson_count"] == 7

    @pytest.mark.asyncio
    async def test_proposal_includes_risk_and_count(self):
        """Proposal dict should include risk_level and lesson_count."""
        haiku_response = FakeAgentResult(
            text='{"should_modify": true, "field": "business_guidance", '
            '"addition": "Some guidance.", "reasoning": "test"}'
        )
        lessons = [FakeLesson(id=i) for i in range(3)]

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=haiku_response,
        ):
            result = await _generate_proposal_from_lessons(
                skill_id="test_skill",
                lessons=lessons,
                role_id="buyer",
                department_id="marketing",
                lesson_count=3,
            )

        assert result is not None
        assert "risk_level" in result
        assert "lesson_count" in result
        assert result["source"] == "reflection_evolution"
