"""Tests for the lesson memory type.

Verifies that:
- MemoryType.LESSON exists in the enum
- compose_memory_context handles lesson memories correctly
- Lesson memories get their own section header
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.models.schema import MemoryType
from src.skills.memory import compose_memory_context


@dataclass
class FakeMemory:
    """Mimics a RoleMemory ORM object."""

    memory_type: str = "lesson"
    title: str = "test lesson"
    content: str = "test content"
    confidence: float = 0.8
    created_at: datetime = field(
        default_factory=lambda: datetime(2026, 2, 15, tzinfo=timezone.utc),
    )


class TestLessonMemoryType:
    def test_lesson_in_enum(self):
        assert MemoryType.LESSON == "lesson"
        assert MemoryType.LESSON.value == "lesson"

    def test_all_memory_types(self):
        expected = {
            "decision",
            "anomaly",
            "pattern",
            "insight",
            "lesson",
            "commitment",
            "relationship",
            "steward_note",
            "cross_role_insight",
        }
        actual = {m.value for m in MemoryType}
        assert expected == actual


class TestComposeLessonMemory:
    def test_single_lesson_memory(self):
        mem = FakeMemory(
            memory_type="lesson",
            content=(
                "[2026-02-15] [Reflection] Tried aggressive budget"
                " shift but platform data lagged 24h behind backend"
            ),
        )
        result = compose_memory_context([mem])
        assert "# Role Memory" in result
        assert "## Lessons Learned" in result
        assert "budget shift" in result

    def test_lesson_appears_after_insights(self):
        mems = [
            FakeMemory(memory_type="lesson", content="Lesson entry"),
            FakeMemory(memory_type="insight", content="Insight entry"),
        ]
        result = compose_memory_context(mems)
        insight_pos = result.find("Key Insights")
        lesson_pos = result.find("Lessons Learned")
        assert insight_pos < lesson_pos

    def test_lesson_mixed_with_other_types(self):
        mems = [
            FakeMemory(memory_type="decision", content="Decision 1"),
            FakeMemory(memory_type="anomaly", content="Anomaly 1"),
            FakeMemory(memory_type="lesson", content="Lesson 1"),
            FakeMemory(memory_type="insight", content="Insight 1"),
        ]
        result = compose_memory_context(mems)
        assert "## Recent Decisions" in result
        assert "## Known Anomalies" in result
        assert "## Key Insights" in result
        assert "## Lessons Learned" in result

    def test_lesson_dict_memories(self):
        mem = {
            "memory_type": "lesson",
            "content": "I tried X but it failed because Y",
            "confidence": 0.9,
            "created_at": datetime(2026, 2, 15, tzinfo=timezone.utc),
        }
        result = compose_memory_context([mem])
        assert "## Lessons Learned" in result
        assert "tried X" in result
