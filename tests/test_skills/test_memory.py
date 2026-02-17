"""Tests for src/skills/memory.py — extraction and injection."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from unittest.mock import MagicMock

from src.skills.memory import (
    _estimate_tokens,
    _extract_anomaly_memories,
    _extract_decision_memories,
    _format_memory_line,
    compose_memory_context,
    extract_memories_from_results,
    filter_superseded_memories,
)

# =====================================================================
# Helpers — fake SkillResult
# =====================================================================


@dataclass
class FakeSkillResult:
    skill_id: str = "test_skill"
    user_id: str = "u1"
    output_text: str = ""
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    cost: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    chain_next: str | None = None


@dataclass
class FakeMemory:
    """Mimics a RoleMemory ORM object for compose_memory_context tests."""

    memory_type: str = "decision"
    title: str = "test"
    content: str = "test content"
    confidence: float = 1.0
    created_at: datetime = field(
        default_factory=lambda: datetime(2026, 2, 10, tzinfo=timezone.utc),
    )


# =====================================================================
# Token estimation
# =====================================================================


class TestEstimateTokens:
    def test_empty_string(self):
        assert _estimate_tokens("") == 1

    def test_short_string(self):
        assert _estimate_tokens("hi") == 1

    def test_hundred_chars(self):
        assert _estimate_tokens("a" * 100) == 25

    def test_four_hundred_chars(self):
        assert _estimate_tokens("a" * 400) == 100


# =====================================================================
# Decision memory extraction
# =====================================================================


class TestExtractDecisionMemories:
    def test_approved_recommendation(self):
        recs = [{"description": "Increase budget by 20%", "reasoning": "Strong ROAS"}]
        outcomes = [{"status": "approved"}]
        result = _extract_decision_memories(
            recs,
            outcomes,
            "buyer",
            "marketing",
            date(2026, 2, 10),
        )
        assert len(result) == 1
        assert result[0]["memory_type"] == "decision"
        assert "Approved" in result[0]["title"]
        assert result[0]["confidence"] == 1.0
        assert "2026-02-10" in result[0]["content"]

    def test_rejected_recommendation(self):
        recs = [{"description": "Pause campaign X"}]
        outcomes = [{"status": "rejected"}]
        result = _extract_decision_memories(
            recs,
            outcomes,
            "buyer",
            "marketing",
        )
        assert len(result) == 1
        assert "Rejected" in result[0]["title"]
        assert result[0]["confidence"] == 0.8

    def test_pending_skipped(self):
        recs = [{"description": "Something"}]
        outcomes = [{"status": "pending"}]
        result = _extract_decision_memories(
            recs,
            outcomes,
            "buyer",
            "marketing",
        )
        assert len(result) == 0

    def test_multiple_outcomes(self):
        recs = [
            {"description": "Action A"},
            {"description": "Action B"},
        ]
        outcomes = [
            {"status": "approved"},
            {"status": "rejected"},
        ]
        result = _extract_decision_memories(
            recs,
            outcomes,
            "buyer",
            "marketing",
        )
        assert len(result) == 2

    def test_outcome_description_override(self):
        recs = [{"description": "Original desc"}]
        outcomes = [{"status": "approved", "description": "Override desc"}]
        result = _extract_decision_memories(
            recs,
            outcomes,
            "buyer",
            "marketing",
        )
        assert "Override desc" in result[0]["title"]

    def test_empty_description_skipped(self):
        recs = [{}]
        outcomes = [{"status": "approved"}]
        result = _extract_decision_memories(
            recs,
            outcomes,
            "buyer",
            "marketing",
        )
        assert len(result) == 0

    def test_more_outcomes_than_recs(self):
        recs = [{"description": "Only one rec"}]
        outcomes = [
            {"status": "approved"},
            {"status": "approved", "description": "Extra outcome"},
        ]
        result = _extract_decision_memories(
            recs,
            outcomes,
            "buyer",
            "marketing",
        )
        assert len(result) == 2

    def test_evidence_contains_action_type(self):
        recs = [{"description": "Budget change", "action_type": "budget_change"}]
        outcomes = [{"status": "approved"}]
        result = _extract_decision_memories(
            recs,
            outcomes,
            "buyer",
            "marketing",
        )
        assert result[0]["evidence"]["action_type"] == "budget_change"

    def test_reasoning_in_content(self):
        recs = [{"description": "Do X", "reasoning": "Because Y"}]
        outcomes = [{"status": "approved"}]
        result = _extract_decision_memories(
            recs,
            outcomes,
            "buyer",
            "marketing",
        )
        assert "Because Y" in result[0]["content"]

    def test_projected_impact_in_content(self):
        recs = [
            {
                "description": "Do X",
                "projected_impact": "Save $500/day",
            },
        ]
        outcomes = [{"status": "approved"}]
        result = _extract_decision_memories(
            recs,
            outcomes,
            "buyer",
            "marketing",
        )
        assert "Save $500/day" in result[0]["content"]


# =====================================================================
# Anomaly memory extraction
# =====================================================================


class TestExtractAnomalyMemories:
    def test_detects_spike_keyword(self):
        sr = FakeSkillResult(
            output_text="CPA spiked 3x on Meta retargeting campaign yesterday.",
        )
        result = _extract_anomaly_memories(
            [sr],
            "buyer",
            "marketing",
            date(2026, 2, 10),
        )
        assert len(result) == 1
        assert result[0]["memory_type"] == "anomaly"
        assert result[0]["confidence"] == 0.9

    def test_detects_drop_keyword(self):
        sr = FakeSkillResult(
            output_text="CTR dropped 40% over 5 days on the prospecting ad set.",
        )
        result = _extract_anomaly_memories(
            [sr],
            "buyer",
            "marketing",
        )
        assert len(result) == 1
        assert "drop" in result[0]["content"].lower()

    def test_no_anomaly_keywords(self):
        sr = FakeSkillResult(
            output_text="Performance was stable across all campaigns today.",
        )
        result = _extract_anomaly_memories(
            [sr],
            "buyer",
            "marketing",
        )
        assert len(result) == 0

    def test_max_three_anomalies(self):
        text = (
            "First anomaly detected in campaign A. "
            "Second spike found in campaign B. "
            "Third alert triggered on campaign C. "
            "Fourth unusual pattern in campaign D."
        )
        sr = FakeSkillResult(output_text=text)
        result = _extract_anomaly_memories(
            [sr],
            "buyer",
            "marketing",
        )
        assert len(result) <= 3

    def test_deduplicates_titles(self):
        text = (
            "CPA spiked 3x on Meta retargeting campaign. "
            "CPA spiked 3x on Meta retargeting campaign."
        )
        sr = FakeSkillResult(output_text=text)
        result = _extract_anomaly_memories(
            [sr],
            "buyer",
            "marketing",
        )
        assert len(result) == 1

    def test_short_sentences_ignored(self):
        sr = FakeSkillResult(output_text="Spike. Drop.")
        result = _extract_anomaly_memories(
            [sr],
            "buyer",
            "marketing",
        )
        assert len(result) == 0

    def test_source_skill_id_set(self):
        sr = FakeSkillResult(
            skill_id="anomaly_detector",
            output_text="Unusual surge in CPC on Google Search brand campaigns.",
        )
        result = _extract_anomaly_memories(
            [sr],
            "buyer",
            "marketing",
        )
        assert result[0]["source_skill_id"] == "anomaly_detector"

    def test_multiple_skill_results(self):
        sr1 = FakeSkillResult(
            skill_id="skill_a",
            output_text="Performance was stable.",
        )
        sr2 = FakeSkillResult(
            skill_id="skill_b",
            output_text="Unexpected spike in CPA on Meta prospecting.",
        )
        result = _extract_anomaly_memories(
            [sr1, sr2],
            "buyer",
            "marketing",
        )
        assert len(result) == 1
        assert result[0]["source_skill_id"] == "skill_b"


# =====================================================================
# Main extraction entry point
# =====================================================================


class TestExtractMemoriesFromResults:
    def test_empty_inputs(self):
        result = extract_memories_from_results(
            "buyer",
            "marketing",
            [],
            None,
        )
        assert result == []

    def test_decisions_only(self):
        sr = FakeSkillResult(
            recommendations=[{"description": "Pause campaign X"}],
            output_text="No anomalies found.",
        )
        result = extract_memories_from_results(
            "buyer",
            "marketing",
            [sr],
            approval_outcomes=[{"status": "approved"}],
            run_date=date(2026, 2, 10),
        )
        # Should have 1 decision, 0 anomalies
        assert any(m["memory_type"] == "decision" for m in result)
        assert not any(m["memory_type"] == "anomaly" for m in result)

    def test_anomalies_only(self):
        sr = FakeSkillResult(
            output_text="CPA spiked 3x on Meta retargeting campaign yesterday.",
        )
        result = extract_memories_from_results(
            "buyer",
            "marketing",
            [sr],
        )
        assert len(result) == 1
        assert result[0]["memory_type"] == "anomaly"

    def test_both_decisions_and_anomalies(self):
        sr = FakeSkillResult(
            recommendations=[{"description": "Increase budget"}],
            output_text="Also detected anomaly spike in CPA on retargeting.",
        )
        result = extract_memories_from_results(
            "buyer",
            "marketing",
            [sr],
            approval_outcomes=[{"status": "approved"}],
            run_date=date(2026, 2, 10),
        )
        types = {m["memory_type"] for m in result}
        assert "decision" in types
        assert "anomaly" in types


# =====================================================================
# Compose memory context (injection)
# =====================================================================


class TestComposeMemoryContext:
    def test_empty_memories(self):
        assert compose_memory_context([]) == ""

    def test_single_decision_memory(self):
        mem = FakeMemory(
            memory_type="decision",
            content="[2026-02-10] Approved: Increase budget by 20%",
        )
        result = compose_memory_context([mem])
        assert "# Role Memory" in result
        assert "## Recent Decisions" in result
        assert "Increase budget" in result
        assert "search_role_memory_archive" in result

    def test_single_anomaly_memory(self):
        mem = FakeMemory(
            memory_type="anomaly",
            content="[2026-02-08] CPA spiked 3x",
        )
        result = compose_memory_context([mem])
        assert "## Known Anomalies" in result

    def test_multiple_types_grouped(self):
        mems = [
            FakeMemory(memory_type="decision", content="Decision 1"),
            FakeMemory(memory_type="anomaly", content="Anomaly 1"),
        ]
        result = compose_memory_context(mems)
        assert "## Recent Decisions" in result
        assert "## Known Anomalies" in result

    def test_token_budget_respected(self):
        # Each memory ~50 chars = ~12 tokens. Header ~90 tokens (includes
        # archive awareness hint). With budget of 200 tokens, should fit
        # header + a few memories but not all 20.
        mems = [
            FakeMemory(content=f"Memory entry number {i} with some extra text padding")
            for i in range(20)
        ]
        result = compose_memory_context(mems, token_budget=200)
        assert "# Role Memory" in result
        # Should have some but not all 20
        count = result.count("Memory entry")
        assert 0 < count < 20

    def test_sorted_by_confidence_desc(self):
        low = FakeMemory(
            content="Low confidence memory",
            confidence=0.3,
            created_at=datetime(2026, 2, 12, tzinfo=timezone.utc),
        )
        high = FakeMemory(
            content="High confidence memory",
            confidence=1.0,
            created_at=datetime(2026, 2, 8, tzinfo=timezone.utc),
        )
        result = compose_memory_context([low, high])
        # High confidence should appear before low
        high_pos = result.find("High confidence")
        low_pos = result.find("Low confidence")
        assert high_pos < low_pos

    def test_dict_memories_supported(self):
        mem = {
            "memory_type": "insight",
            "content": "Backend ROAS inflation ratio: 1.3x",
            "confidence": 0.9,
            "created_at": datetime(2026, 2, 10, tzinfo=timezone.utc),
        }
        result = compose_memory_context([mem])
        assert "## Key Insights" in result
        assert "inflation ratio" in result

    def test_preamble_text(self):
        mem = FakeMemory(content="Test")
        result = compose_memory_context([mem])
        assert "don't repeat them verbatim" in result

    def test_type_ordering(self):
        # decisions should come before anomalies in output
        mems = [
            FakeMemory(memory_type="anomaly", content="Anomaly first"),
            FakeMemory(memory_type="decision", content="Decision second"),
        ]
        result = compose_memory_context(mems)
        dec_pos = result.find("Recent Decisions")
        anom_pos = result.find("Known Anomalies")
        assert dec_pos < anom_pos

    def test_attributed_memory_formatted(self):
        """Memories with WHO/WHEN attribution should show 'X told you:' format."""
        mem = FakeMemory(
            memory_type="insight",
            content="[2026-02-12] [Conversation] (from Michael) Redis upgrade next week",
        )
        result = compose_memory_context([mem])
        assert "Michael told you:" in result
        assert "Redis upgrade next week" in result
        assert "[Feb 12]" in result

    def test_attributed_memory_no_name(self):
        """Memories without attribution should show date only."""
        mem = FakeMemory(
            memory_type="insight",
            content="[2026-02-12] [Conversation] Redis upgrade next week",
        )
        result = compose_memory_context([mem])
        assert "[Feb 12]" in result
        assert "Redis upgrade next week" in result
        assert "told you:" not in result

    def test_old_format_memory_passthrough(self):
        """Memories in old format (no [Conversation]) should still work."""
        mem = FakeMemory(
            memory_type="decision",
            content="[2026-02-10] Approved: Increase budget by 20%",
        )
        result = compose_memory_context([mem])
        assert "Increase budget" in result

    def test_preamble_includes_attribution_guidance(self):
        """Preamble should instruct agent to cite WHO and WHEN."""
        mem = FakeMemory(content="Test")
        result = compose_memory_context([mem])
        assert "who said it and when" in result


# =====================================================================
# _format_memory_line — WHO/WHEN parsing
# =====================================================================


class TestFormatMemoryLine:
    """Verify _format_memory_line parses attribution correctly."""

    def test_full_attribution(self):
        line = _format_memory_line(
            "[2026-02-12] [Conversation] (from Michael) Redis upgrade next week",
        )
        assert line == "- [Feb 12] Michael told you: Redis upgrade next week"

    def test_no_attribution(self):
        line = _format_memory_line(
            "[2026-02-12] [Conversation] Redis upgrade next week",
        )
        assert line == "- [Feb 12] Redis upgrade next week"

    def test_old_format_passthrough(self):
        line = _format_memory_line(
            "[2026-02-10] Approved: Increase budget by 20%",
        )
        assert line == "- [2026-02-10] Approved: Increase budget by 20%"

    def test_plain_text_passthrough(self):
        line = _format_memory_line("Some random memory content")
        assert line == "- Some random memory content"

    def test_january_date(self):
        line = _format_memory_line(
            "[2026-01-05] [Conversation] (from Sarah) Q1 plan locked",
        )
        assert line == "- [Jan 5] Sarah told you: Q1 plan locked"

    def test_december_date(self):
        line = _format_memory_line(
            "[2026-12-25] [Conversation] Holiday freeze active",
        )
        assert line == "- [Dec 25] Holiday freeze active"

    def test_multiword_name(self):
        line = _format_memory_line(
            "[2026-03-15] [Conversation] (from John Smith) Budget is $50k",
        )
        assert line == "- [Mar 15] John Smith told you: Budget is $50k"


# =====================================================================
# filter_superseded_memories
# =====================================================================


def _make_mock_memory(
    *,
    memory_id: int | None = None,
    memory_type: str = "decision",
    title: str = "test",
    content: str = "test content",
    confidence: float = 1.0,
    created_at: datetime | None = None,
) -> MagicMock:
    """Create a MagicMock mimicking a RoleMemory ORM object."""
    m = MagicMock()
    m.id = memory_id
    m.memory_type = memory_type
    m.title = title
    m.content = content
    m.confidence = confidence
    m.created_at = created_at or datetime(2026, 2, 10, tzinfo=timezone.utc)
    m.source_role_id = None  # Default: not an inter-agent memory
    return m


class TestFilterSupersededMemories:
    def test_filters_correctly(self):
        """Pass memories with IDs 1,2,3 and superseded_ids={2}, expect 1,3 returned."""
        m1 = _make_mock_memory(memory_id=1, title="mem1")
        m2 = _make_mock_memory(memory_id=2, title="mem2")
        m3 = _make_mock_memory(memory_id=3, title="mem3")
        result = filter_superseded_memories([m1, m2, m3], superseded_ids={2})
        assert len(result) == 2
        assert result[0].id == 1
        assert result[1].id == 3

    def test_empty_set_passthrough(self):
        """Empty superseded_ids returns all memories unchanged."""
        m1 = _make_mock_memory(memory_id=1)
        m2 = _make_mock_memory(memory_id=2)
        memories = [m1, m2]
        result = filter_superseded_memories(memories, superseded_ids=set())
        assert result is memories  # same list object returned
        assert len(result) == 2

    def test_dict_memories(self):
        """Works with list of dicts (dict has 'id' key)."""
        d1 = {"id": 10, "content": "first"}
        d2 = {"id": 20, "content": "second"}
        d3 = {"id": 30, "content": "third"}
        result = filter_superseded_memories([d1, d2, d3], superseded_ids={20})
        assert len(result) == 2
        assert result[0]["id"] == 10
        assert result[1]["id"] == 30

    def test_orm_memories(self):
        """Works with ORM objects (MagicMock with id attr)."""
        m1 = _make_mock_memory(memory_id=5, title="keep")
        m2 = _make_mock_memory(memory_id=7, title="remove")
        m3 = _make_mock_memory(memory_id=9, title="keep too")
        result = filter_superseded_memories([m1, m2, m3], superseded_ids={7})
        assert len(result) == 2
        assert all(m.id != 7 for m in result)

    def test_preserves_order(self):
        """Order is maintained after filtering."""
        m1 = _make_mock_memory(memory_id=100, title="A")
        m2 = _make_mock_memory(memory_id=200, title="B")
        m3 = _make_mock_memory(memory_id=300, title="C")
        m4 = _make_mock_memory(memory_id=400, title="D")
        result = filter_superseded_memories(
            [m1, m2, m3, m4],
            superseded_ids={200, 400},
        )
        assert len(result) == 2
        assert result[0].id == 100
        assert result[1].id == 300

    def test_memory_without_id_kept(self):
        """Memories with no id attribute are kept (not filtered out)."""
        m_no_id = MagicMock(spec=[])  # spec=[] means no attributes at all
        m_with_id = _make_mock_memory(memory_id=5)
        result = filter_superseded_memories(
            [m_no_id, m_with_id],
            superseded_ids={5},
        )
        # m_no_id has no id attr → getattr returns None → not in superseded_ids → kept
        # m_with_id has id=5 → in superseded_ids → removed
        assert len(result) == 1
        assert result[0] is m_no_id


# =====================================================================
# compose_memory_context — relationship type handling
# =====================================================================


class TestComposeMemoryContextRelationship:
    def test_relationship_type_first(self):
        """Relationship memories appear before other types in output."""
        mems = [
            _make_mock_memory(
                memory_type="decision",
                content="Decision content here",
                confidence=1.0,
            ),
            _make_mock_memory(
                memory_type="relationship",
                content="Relationship content here",
                confidence=1.0,
            ),
            _make_mock_memory(
                memory_type="anomaly",
                content="Anomaly content here",
                confidence=1.0,
            ),
        ]
        result = compose_memory_context(mems)
        rel_pos = result.find("Relationship Context")
        dec_pos = result.find("Recent Decisions")
        anom_pos = result.find("Known Anomalies")
        # Relationship should appear before both decisions and anomalies
        assert rel_pos != -1, "Relationship Context section not found"
        assert dec_pos != -1, "Recent Decisions section not found"
        assert anom_pos != -1, "Known Anomalies section not found"
        assert rel_pos < dec_pos
        assert rel_pos < anom_pos

    def test_relationship_header(self):
        """Section header is 'Relationship Context'."""
        mem = _make_mock_memory(
            memory_type="relationship",
            content="User prefers concise summaries",
        )
        result = compose_memory_context([mem])
        assert "## Relationship Context" in result

    def test_relationship_type_formatted(self):
        """Relationship type memory content formatted correctly."""
        mem = _make_mock_memory(
            memory_type="relationship",
            content="[2026-02-14] [Conversation] (from Alice) Prefers bullet points",
        )
        result = compose_memory_context([mem])
        assert "## Relationship Context" in result
        assert "Alice told you:" in result
        assert "Prefers bullet points" in result
        assert "[Feb 14]" in result
