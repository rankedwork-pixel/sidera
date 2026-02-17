"""Tests for STEWARD_NOTE memory type in compose_memory_context.

Verifies that steward notes render first (highest priority) and that the
section header is correct.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.skills.memory import compose_memory_context

# ============================================================
# Helpers
# ============================================================


def _make_memory(
    memory_type: str = "insight",
    content: str = "Some insight content.",
    confidence: float = 0.8,
    created_at: datetime | None = None,
    source_role_id: str | None = None,
    memory_id: int | None = None,
):
    """Build a dict that compose_memory_context can process."""
    return {
        "id": memory_id,
        "memory_type": memory_type,
        "content": content,
        "confidence": confidence,
        "created_at": created_at or datetime(2025, 6, 1, tzinfo=timezone.utc),
        "source_role_id": source_role_id,
    }


# ============================================================
# Tests
# ============================================================


class TestStewardNoteRendering:
    def test_steward_note_section_appears_first(self):
        """Steward Guidance section should appear before other memory types."""
        memories = [
            _make_memory(memory_type="insight", content="Insight content", confidence=0.9),
            _make_memory(memory_type="decision", content="Decision content", confidence=1.0),
            _make_memory(
                memory_type="steward_note",
                content="Always prioritize ROAS over volume.",
                confidence=1.0,
            ),
            _make_memory(memory_type="anomaly", content="Spike detected", confidence=0.9),
        ]

        result = compose_memory_context(memories, token_budget=5000)

        assert "## Steward Guidance" in result

        # Steward Guidance must appear before all other sections
        steward_pos = result.index("## Steward Guidance")
        for header in ["## Recent Decisions", "## Known Anomalies", "## Key Insights"]:
            if header in result:
                assert steward_pos < result.index(header), (
                    f"Steward Guidance should appear before {header}"
                )

    def test_steward_note_content_rendered(self):
        """Steward note content should appear in the output."""
        memories = [
            _make_memory(
                memory_type="steward_note",
                content="Focus on branded search campaigns.",
                confidence=1.0,
            ),
        ]

        result = compose_memory_context(memories, token_budget=5000)
        assert "Focus on branded search campaigns" in result

    def test_empty_without_memories(self):
        """compose_memory_context returns empty string with no memories."""
        result = compose_memory_context([], token_budget=5000)
        assert result == ""

    def test_steward_note_not_truncated_before_others(self):
        """Steward notes should be included even when token budget is tight."""
        # Steward note has highest confidence (1.0) so should be sorted first
        memories = [
            _make_memory(
                memory_type="steward_note",
                content="Critical steward guidance.",
                confidence=1.0,
            ),
            _make_memory(
                memory_type="insight",
                content="x" * 5000,  # Very long insight
                confidence=0.5,
            ),
        ]

        # Small budget — steward note should still make it in
        result = compose_memory_context(memories, token_budget=500)
        assert "Critical steward guidance" in result

    def test_multiple_steward_notes(self):
        """Multiple steward notes should all render under the same section."""
        memories = [
            _make_memory(
                memory_type="steward_note",
                content="Note one.",
                confidence=1.0,
            ),
            _make_memory(
                memory_type="steward_note",
                content="Note two.",
                confidence=1.0,
            ),
        ]

        result = compose_memory_context(memories, token_budget=5000)
        assert "Note one" in result
        assert "Note two" in result
        # Only one Steward Guidance header
        assert result.count("## Steward Guidance") == 1


class TestStewardNoteWithRelationshipMemories:
    def test_steward_before_relationship(self):
        """Steward notes should render before relationship memories."""
        memories = [
            _make_memory(
                memory_type="relationship",
                content="Good working relationship with reporting analyst.",
                confidence=0.9,
                source_role_id="reporting_analyst",
            ),
            _make_memory(
                memory_type="steward_note",
                content="Steward says: be careful with budgets.",
                confidence=1.0,
            ),
        ]

        result = compose_memory_context(memories, token_budget=5000)

        if "## Steward Guidance" in result and "## Relationship Context" in result:
            steward_pos = result.index("## Steward Guidance")
            relationship_pos = result.index("## Relationship Context")
            assert steward_pos < relationship_pos
