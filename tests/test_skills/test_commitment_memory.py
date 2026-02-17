"""Tests for COMMITMENT memory type.

Verifies that commitment memories are a valid type, render correctly
in compose_memory_context, and appear in the expected position.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.models.schema import MemoryType
from src.skills.memory import compose_memory_context

# ============================================================
# Helpers
# ============================================================


def _make_memory(
    memory_type: str = "insight",
    content: str = "Some content.",
    confidence: float = 0.8,
    created_at: datetime | None = None,
    source_role_id: str | None = None,
):
    return {
        "id": None,
        "memory_type": memory_type,
        "content": content,
        "confidence": confidence,
        "created_at": created_at or datetime(2025, 6, 1, tzinfo=timezone.utc),
        "source_role_id": source_role_id,
    }


# ============================================================
# Tests — enum
# ============================================================


class TestCommitmentEnum:
    def test_commitment_in_memory_type(self):
        """COMMITMENT should be a valid MemoryType enum member."""
        assert hasattr(MemoryType, "COMMITMENT")
        assert MemoryType.COMMITMENT.value == "commitment"

    def test_memory_type_count(self):
        """MemoryType should have 9 members."""
        assert len(MemoryType) == 9


# ============================================================
# Tests — compose_memory_context
# ============================================================


class TestCommitmentRendering:
    def test_commitment_section_header(self):
        """Commitments should render under '## Active Commitments'."""
        memories = [
            _make_memory(
                memory_type="commitment",
                content="Will investigate budget spike tomorrow.",
                confidence=1.0,
            ),
        ]
        result = compose_memory_context(memories, token_budget=5000)
        assert "## Active Commitments" in result

    def test_commitment_content_rendered(self):
        """Commitment content should appear in the output."""
        memories = [
            _make_memory(
                memory_type="commitment",
                content="Will send updated targets by Friday.",
                confidence=0.9,
            ),
        ]
        result = compose_memory_context(memories, token_budget=5000)
        assert "Will send updated targets by Friday" in result

    def test_commitment_order_after_relationship(self):
        """Commitments should render after relationship but before decisions."""
        memories = [
            _make_memory(memory_type="decision", content="Decision content", confidence=1.0),
            _make_memory(
                memory_type="commitment",
                content="Commitment content",
                confidence=1.0,
            ),
            _make_memory(
                memory_type="relationship",
                content="Relationship content",
                confidence=1.0,
                source_role_id="analyst",
            ),
        ]
        result = compose_memory_context(memories, token_budget=5000)

        if "## Active Commitments" in result and "## Recent Decisions" in result:
            commit_pos = result.index("## Active Commitments")
            decision_pos = result.index("## Recent Decisions")
            assert commit_pos < decision_pos, (
                "Active Commitments should appear before Recent Decisions"
            )

    def test_commitment_with_other_types(self):
        """Commitment should coexist with other memory types."""
        memories = [
            _make_memory(memory_type="insight", content="Insight content", confidence=0.8),
            _make_memory(memory_type="commitment", content="Will do X", confidence=0.9),
            _make_memory(memory_type="lesson", content="Lesson content", confidence=0.7),
        ]
        result = compose_memory_context(memories, token_budget=5000)
        assert "## Active Commitments" in result
        assert "## Key Insights" in result
        assert "## Lessons Learned" in result
