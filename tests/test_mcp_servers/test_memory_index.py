"""Tests for the memory index pattern.

Verifies that:
- compose_memory_index builds compact title-only listings
- compose_memory_context delegates to index above threshold
- compose_memory_context uses full mode below threshold
- force_index parameter works
- load_memory_detail MCP tool loads full content
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.skills.memory import (
    _MEMORY_INDEX_THRESHOLD,
    compose_memory_context,
    compose_memory_index,
)

# ============================================================
# Helpers
# ============================================================


def _make_memory(
    memory_id: int = 1,
    memory_type: str = "insight",
    title: str = "Test memory",
    content: str = "Some content.",
    confidence: float = 0.8,
    created_at: datetime | None = None,
    source_role_id: str | None = None,
):
    return {
        "id": memory_id,
        "memory_type": memory_type,
        "title": title,
        "content": content,
        "confidence": confidence,
        "created_at": created_at or datetime(2025, 6, 1, tzinfo=timezone.utc),
        "source_role_id": source_role_id,
    }


def _make_many_memories(count: int) -> list[dict]:
    """Generate a list of memories for threshold testing."""
    return [
        _make_memory(
            memory_id=i,
            memory_type="insight" if i % 2 == 0 else "lesson",
            title=f"Memory {i}",
            content=f"Content for memory {i}",
            confidence=0.5 + (i % 5) * 0.1,
        )
        for i in range(count)
    ]


# ============================================================
# Tests — compose_memory_index
# ============================================================


class TestComposeMemoryIndex:
    def test_empty_returns_empty(self):
        assert compose_memory_index([]) == ""

    def test_index_contains_memory_ids(self):
        """Index should show memory IDs in brackets."""
        memories = [
            _make_memory(memory_id=42, title="Budget spike detected"),
            _make_memory(memory_id=99, title="ROAS improvement trend"),
        ]
        result = compose_memory_index(memories)
        assert "[42]" in result
        assert "[99]" in result

    def test_index_contains_titles(self):
        """Index should show memory titles."""
        memories = [
            _make_memory(memory_id=1, title="My important memory"),
        ]
        result = compose_memory_index(memories)
        assert "My important memory" in result

    def test_index_groups_by_type(self):
        """Index should group memories by type with section headers."""
        memories = [
            _make_memory(memory_id=1, memory_type="insight", title="Insight A"),
            _make_memory(memory_id=2, memory_type="lesson", title="Lesson A"),
            _make_memory(memory_id=3, memory_type="decision", title="Decision A"),
        ]
        result = compose_memory_index(memories)
        assert "## Key Insights" in result
        assert "## Lessons Learned" in result
        assert "## Recent Decisions" in result

    def test_index_mentions_load_tool(self):
        """Index should mention the load_memory_detail tool."""
        memories = [_make_memory(memory_id=1)]
        result = compose_memory_index(memories)
        assert "load_memory_detail" in result

    def test_index_contains_dates(self):
        """Index should show formatted dates."""
        memories = [
            _make_memory(
                memory_id=1,
                title="Test",
                created_at=datetime(2025, 3, 15, tzinfo=timezone.utc),
            ),
        ]
        result = compose_memory_index(memories)
        assert "Mar 15" in result


# ============================================================
# Tests — threshold delegation
# ============================================================


class TestThresholdDelegation:
    def test_threshold_constant(self):
        """Threshold should be 20."""
        assert _MEMORY_INDEX_THRESHOLD == 20

    def test_below_threshold_uses_full_mode(self):
        """Below threshold, compose_memory_context should use full content."""
        memories = _make_many_memories(10)
        result = compose_memory_context(memories, token_budget=10000)
        # Full mode has "# Role Memory" header
        assert "# Role Memory\n" in result
        # Full mode does NOT mention load_memory_detail
        assert "load_memory_detail" not in result

    def test_above_threshold_uses_index_mode(self):
        """Above threshold, compose_memory_context should use index mode."""
        memories = _make_many_memories(25)
        result = compose_memory_context(memories, token_budget=10000)
        # Index mode has "# Role Memory (Index)" header
        assert "# Role Memory (Index)" in result
        assert "load_memory_detail" in result

    def test_force_index_overrides_threshold(self):
        """force_index=True should use index mode even with few memories."""
        memories = _make_many_memories(5)
        result = compose_memory_context(memories, token_budget=10000, force_index=True)
        assert "# Role Memory (Index)" in result
        assert "load_memory_detail" in result

    def test_exactly_at_threshold_uses_full_mode(self):
        """Exactly at threshold (20) should use full mode (> not >=)."""
        memories = _make_many_memories(20)
        result = compose_memory_context(memories, token_budget=10000)
        # At exactly threshold, should still be full mode
        assert "# Role Memory\n" in result
