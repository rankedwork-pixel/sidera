"""Tests for the reflection → skill/role evolution pipeline.

Verifies that:
- scan_lessons_for_proposals groups lessons by skill
- Proposals are only generated when enough lessons accumulate
- Haiku is called to determine if a modification is warranted
- scan_gaps_for_role_proposals detects capability gaps and proposes new roles
- Gap detection only triggers for managers with enough gap observations
- Error handling is non-fatal
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.skills.reflection_evolution import (
    _MIN_GAPS_FOR_ROLE_PROPOSAL,
    _MIN_LESSONS_FOR_PROPOSAL,
    _generate_proposal_from_lessons,
    _generate_role_proposal_from_gaps,
    scan_gaps_for_role_proposals,
    scan_lessons_for_proposals,
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


# ============================================================
# Tests — scan_lessons_for_proposals
# ============================================================


class TestScanLessonsForProposals:
    @pytest.mark.asyncio
    async def test_returns_empty_on_no_lessons(self):
        """Should return empty when no lessons exist."""
        mock_session = AsyncMock()
        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "src.db.service.search_role_memories",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=mock_session_ctx,
            ),
        ):
            result = await scan_lessons_for_proposals(
                role_id="buyer",
                department_id="marketing",
                user_id="u1",
            )
        assert result == []

    @pytest.mark.asyncio
    async def test_requires_minimum_lessons(self):
        """Should not propose changes for skills with fewer than threshold lessons."""
        lessons = [
            FakeLesson(id=i, source_skill_id="some_skill")
            for i in range(_MIN_LESSONS_FOR_PROPOSAL - 1)
        ]
        mock_session = AsyncMock()
        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "src.db.service.search_role_memories",
                new_callable=AsyncMock,
                return_value=lessons,
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=mock_session_ctx,
            ),
        ):
            result = await scan_lessons_for_proposals(
                role_id="buyer",
                department_id="marketing",
                user_id="u1",
            )
        assert result == []

    @pytest.mark.asyncio
    async def test_error_returns_empty(self):
        """Should return empty on DB error."""
        with patch(
            "src.db.session.get_db_session",
            side_effect=Exception("DB down"),
        ):
            result = await scan_lessons_for_proposals(
                role_id="buyer",
                department_id="marketing",
                user_id="u1",
            )
        assert result == []


# ============================================================
# Tests — _generate_proposal_from_lessons
# ============================================================


class TestGenerateProposalFromLessons:
    @pytest.mark.asyncio
    async def test_generates_proposal_when_haiku_agrees(self):
        """Should return a proposal when Haiku says modification is needed."""
        haiku_response = FakeAgentResult(
            text='{"should_modify": true, "field": "business_guidance", '
            '"addition": "Always wait 48h after budget shifts before evaluating.", '
            '"reasoning": "Multiple lessons show post-shift instability"}'
        )
        lessons = [FakeLesson(id=i, title=f"Lesson {i}") for i in range(3)]

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=haiku_response,
        ):
            result = await _generate_proposal_from_lessons(
                skill_id="budget_reallocation",
                lessons=lessons,
                role_id="buyer",
                department_id="marketing",
            )

        assert result is not None
        assert result["skill_id"] == "budget_reallocation"
        assert "business_guidance" in result["changes"]
        assert result["source"] == "reflection_evolution"
        assert len(result["lessons_referenced"]) == 3

    @pytest.mark.asyncio
    async def test_returns_none_when_haiku_says_no(self):
        """Should return None when Haiku says no modification needed."""
        haiku_response = FakeAgentResult(
            text='{"should_modify": false, "reasoning": "situational issue"}'
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
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_handles_llm_error_gracefully(self):
        """Should return None on LLM error."""
        lessons = [FakeLesson(id=i) for i in range(3)]

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            side_effect=Exception("API error"),
        ):
            result = await _generate_proposal_from_lessons(
                skill_id="test_skill",
                lessons=lessons,
                role_id="buyer",
                department_id="marketing",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_handles_invalid_json(self):
        """Should return None on unparseable LLM response."""
        haiku_response = FakeAgentResult(text="Not valid JSON at all")
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
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_field_defaults_to_business_guidance(self):
        """Invalid field should default to business_guidance."""
        haiku_response = FakeAgentResult(
            text='{"should_modify": true, "field": "invalid_field", '
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
            )

        assert result is not None
        assert "business_guidance" in result["changes"]

    @pytest.mark.asyncio
    async def test_empty_addition_returns_none(self):
        """Should return None if Haiku says modify but addition is empty."""
        haiku_response = FakeAgentResult(
            text='{"should_modify": true, "field": "business_guidance", '
            '"addition": "", "reasoning": "test"}'
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
            )

        assert result is None


# ============================================================
# Helpers for gap detection tests
# ============================================================


@dataclass
class FakeGapObservation:
    id: int = 1
    title: str = "Missing compliance capability"
    content: str = "[2025-01-15] [Gap Detection] Cannot handle regulatory queries"
    memory_type: str = "insight"
    confidence: float = 0.8
    source_skill_id: str = "reflection:head_of_marketing"
    created_at: datetime = None  # type: ignore
    evidence: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)
        if not self.evidence:
            self.evidence = {"gap_domain": "compliance"}


def _make_manager_role(role_id: str = "head_of_marketing") -> SimpleNamespace:
    """Create a fake manager RoleDefinition."""
    return SimpleNamespace(
        id=role_id,
        name="Head of Marketing",
        department_id="marketing",
        manages=("performance_media_buyer", "reporting_analyst"),
    )


def _make_non_manager_role(role_id: str = "performance_media_buyer") -> SimpleNamespace:
    """Create a fake non-manager RoleDefinition."""
    return SimpleNamespace(
        id=role_id,
        name="Performance Media Buyer",
        department_id="marketing",
        manages=(),
    )


def _make_fake_registry(role):
    """Create a fake registry that returns the given role."""
    registry = MagicMock()
    registry.get_role.return_value = role
    return registry


# ============================================================
# Tests — scan_gaps_for_role_proposals
# ============================================================


class TestScanGapsForRoleProposals:
    @pytest.mark.asyncio
    async def test_returns_empty_for_non_manager(self):
        """Non-manager roles should not propose new roles."""
        registry = _make_fake_registry(_make_non_manager_role())

        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=registry,
        ):
            result = await scan_gaps_for_role_proposals(
                role_id="performance_media_buyer",
                department_id="marketing",
                user_id="u1",
            )
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_too_few_gaps(self):
        """Should not propose when fewer gaps than threshold."""
        registry = _make_fake_registry(_make_manager_role())
        gaps = [FakeGapObservation(id=i) for i in range(_MIN_GAPS_FOR_ROLE_PROPOSAL - 1)]

        mock_session = AsyncMock()
        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=registry,
            ),
            patch(
                "src.db.service.search_role_memories",
                new_callable=AsyncMock,
                return_value=gaps,
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=mock_session_ctx,
            ),
        ):
            result = await scan_gaps_for_role_proposals(
                role_id="head_of_marketing",
                department_id="marketing",
                user_id="u1",
            )
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_gap_domain(self):
        """Gaps without a domain label ('unknown') should be skipped."""
        registry = _make_fake_registry(_make_manager_role())
        # Gaps with no gap_domain in evidence (use non-empty dict
        # to avoid __post_init__ replacing with default).
        gaps = [
            FakeGapObservation(
                id=i,
                evidence={"source": "post_run_reflection"},  # No gap_domain
            )
            for i in range(_MIN_GAPS_FOR_ROLE_PROPOSAL + 1)
        ]

        mock_session = AsyncMock()
        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=registry,
            ),
            patch(
                "src.db.service.search_role_memories",
                new_callable=AsyncMock,
                return_value=gaps,
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=mock_session_ctx,
            ),
        ):
            result = await scan_gaps_for_role_proposals(
                role_id="head_of_marketing",
                department_id="marketing",
                user_id="u1",
            )
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_non_gap_insights(self):
        """Should only process insights with [Gap Detection] in content."""
        registry = _make_fake_registry(_make_manager_role())
        # Mix of gap and non-gap insights
        regular_insights = [
            FakeGapObservation(
                id=i,
                content="[2025-01-15] [Reflection] Regular insight",
                evidence={"gap_domain": "compliance"},
            )
            for i in range(5)
        ]

        mock_session = AsyncMock()
        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=registry,
            ),
            patch(
                "src.db.service.search_role_memories",
                new_callable=AsyncMock,
                return_value=regular_insights,
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=mock_session_ctx,
            ),
        ):
            result = await scan_gaps_for_role_proposals(
                role_id="head_of_marketing",
                department_id="marketing",
                user_id="u1",
            )
        assert result == []

    @pytest.mark.asyncio
    async def test_error_returns_empty(self):
        """Should return empty on any error."""
        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            side_effect=Exception("DB down"),
        ):
            result = await scan_gaps_for_role_proposals(
                role_id="head_of_marketing",
                department_id="marketing",
                user_id="u1",
            )
        assert result == []


# ============================================================
# Tests — _generate_role_proposal_from_gaps
# ============================================================


class TestGenerateRoleProposalFromGaps:
    @pytest.mark.asyncio
    async def test_generates_proposal_when_haiku_agrees(self):
        """Should return a role proposal when Haiku says a new role is needed."""
        haiku_response = FakeAgentResult(
            text='{"should_create": true, "role_id": "compliance_auditor", '
            '"name": "Compliance Auditor", '
            '"description": "Monitors regulatory compliance", '
            '"persona": "A diligent compliance specialist", '
            '"reasoning": "Multiple gap observations indicate unmet need"}'
        )
        gaps = [FakeGapObservation(id=i) for i in range(3)]

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=haiku_response,
        ):
            result = await _generate_role_proposal_from_gaps(
                domain="compliance",
                gaps=gaps,
                role_id="head_of_marketing",
                department_id="marketing",
            )

        assert result is not None
        assert result["proposed_changes"]["name"] == "Compliance Auditor"
        assert result["proposed_changes"]["persona"] == "A diligent compliance specialist"
        assert result["proposer_role_id"] == "head_of_marketing"
        assert result["department_id"] == "marketing"
        assert result["evidence"]["gap_count"] == 3
        assert result["evidence"]["domain"] == "compliance"
        assert result["evidence"]["source"] == "gap_detection"
        assert result["suggested_role_id"] == "compliance_auditor"

    @pytest.mark.asyncio
    async def test_returns_none_when_haiku_says_no(self):
        """Should return None when Haiku says no new role needed."""
        haiku_response = FakeAgentResult(
            text='{"should_create": false, "reasoning": "already covered"}'
        )
        gaps = [FakeGapObservation(id=i) for i in range(3)]

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=haiku_response,
        ):
            result = await _generate_role_proposal_from_gaps(
                domain="compliance",
                gaps=gaps,
                role_id="head_of_marketing",
                department_id="marketing",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_llm_error(self):
        """Should return None on LLM error."""
        gaps = [FakeGapObservation(id=i) for i in range(3)]

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            side_effect=Exception("API error"),
        ):
            result = await _generate_role_proposal_from_gaps(
                domain="compliance",
                gaps=gaps,
                role_id="head_of_marketing",
                department_id="marketing",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_invalid_json(self):
        """Should return None on unparseable LLM response."""
        haiku_response = FakeAgentResult(text="Not valid JSON")
        gaps = [FakeGapObservation(id=i) for i in range(3)]

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=haiku_response,
        ):
            result = await _generate_role_proposal_from_gaps(
                domain="compliance",
                gaps=gaps,
                role_id="head_of_marketing",
                department_id="marketing",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_missing_description(self):
        """Should return None if Haiku provides no description."""
        haiku_response = FakeAgentResult(
            text='{"should_create": true, "role_id": "auditor", '
            '"name": "Auditor", "description": "", '
            '"persona": "An auditor", "reasoning": "needed"}'
        )
        gaps = [FakeGapObservation(id=i) for i in range(3)]

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=haiku_response,
        ):
            result = await _generate_role_proposal_from_gaps(
                domain="compliance",
                gaps=gaps,
                role_id="head_of_marketing",
                department_id="marketing",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_missing_persona(self):
        """Should return None if Haiku provides no persona."""
        haiku_response = FakeAgentResult(
            text='{"should_create": true, "role_id": "auditor", '
            '"name": "Auditor", "description": "Monitors compliance", '
            '"persona": "", "reasoning": "needed"}'
        )
        gaps = [FakeGapObservation(id=i) for i in range(3)]

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=haiku_response,
        ):
            result = await _generate_role_proposal_from_gaps(
                domain="compliance",
                gaps=gaps,
                role_id="head_of_marketing",
                department_id="marketing",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_handles_markdown_fenced_json(self):
        """Should handle Haiku wrapping JSON in markdown code fences."""
        haiku_response = FakeAgentResult(
            text='```json\n{"should_create": true, "role_id": "compliance_auditor", '
            '"name": "Compliance Auditor", '
            '"description": "Monitors regulatory compliance", '
            '"persona": "A diligent compliance specialist", '
            '"reasoning": "Multiple gap observations"}\n```'
        )
        gaps = [FakeGapObservation(id=i) for i in range(3)]

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=haiku_response,
        ):
            result = await _generate_role_proposal_from_gaps(
                domain="compliance",
                gaps=gaps,
                role_id="head_of_marketing",
                department_id="marketing",
            )

        assert result is not None
        assert result["proposed_changes"]["name"] == "Compliance Auditor"

    @pytest.mark.asyncio
    async def test_fallback_role_id_when_not_provided(self):
        """Should generate role_id from domain when Haiku doesn't provide one."""
        haiku_response = FakeAgentResult(
            text='{"should_create": true, '
            '"name": "Data Engineering Specialist", '
            '"description": "Handles data pipeline issues", '
            '"persona": "A data engineering expert", '
            '"reasoning": "Recurring data pipeline gaps"}'
        )
        gaps = [
            FakeGapObservation(
                id=i,
                evidence={"gap_domain": "data engineering"},
            )
            for i in range(3)
        ]

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=haiku_response,
        ):
            result = await _generate_role_proposal_from_gaps(
                domain="data engineering",
                gaps=gaps,
                role_id="head_of_marketing",
                department_id="marketing",
            )

        assert result is not None
        assert result["suggested_role_id"] == "data_engineering_specialist"
