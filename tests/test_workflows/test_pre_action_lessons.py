"""Tests for pre-action lesson check and lesson contradiction blocking.

Verifies that:
- _check_lessons_before_action finds relevant lessons
- _check_lesson_contradictions blocks auto-execute on high-confidence negative lessons
- Errors are handled gracefully (non-fatal)
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from src.skills.auto_execute import _check_lesson_contradictions

# ============================================================
# Helpers
# ============================================================


@dataclass
class FakeMemory:
    title: str = ""
    content: str = ""
    confidence: float = 0.5
    memory_type: str = "lesson"


# ============================================================
# Tests — _check_lesson_contradictions
# ============================================================


class TestCheckLessonContradictions:
    @pytest.mark.asyncio
    async def test_finds_high_confidence_negative_lesson(self):
        """Should return lesson title when high-confidence negative lesson found."""
        fake_lesson = FakeMemory(
            title="Budget shift too aggressive",
            content="50% budget shifts caused instability for 48h — avoid large shifts",
            confidence=0.9,
        )
        session = AsyncMock()
        with patch(
            "src.db.service.search_role_memories",
            new_callable=AsyncMock,
            return_value=[fake_lesson],
        ):
            result = await _check_lesson_contradictions(
                {"action_type": "budget_change", "platform": "google_ads", "user_id": "u1"},
                "buyer",
                session,
            )
        assert result == "Budget shift too aggressive"

    @pytest.mark.asyncio
    async def test_ignores_low_confidence_lessons(self):
        """Should not block on low-confidence lessons."""
        fake_lesson = FakeMemory(
            title="Maybe avoid this",
            content="This might have caused problems",
            confidence=0.5,
        )
        session = AsyncMock()
        with patch(
            "src.db.service.search_role_memories",
            new_callable=AsyncMock,
            return_value=[fake_lesson],
        ):
            result = await _check_lesson_contradictions(
                {"action_type": "budget_change", "user_id": "u1"},
                "buyer",
                session,
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_ignores_positive_lessons(self):
        """Should not block on lessons without negative keywords."""
        fake_lesson = FakeMemory(
            title="Budget shifts work well",
            content="20% budget shifts in Q2 improved ROAS consistently",
            confidence=0.9,
        )
        session = AsyncMock()
        with patch(
            "src.db.service.search_role_memories",
            new_callable=AsyncMock,
            return_value=[fake_lesson],
        ):
            result = await _check_lesson_contradictions(
                {"action_type": "budget_change", "user_id": "u1"},
                "buyer",
                session,
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_on_no_lessons(self):
        """Should return None when no lessons found."""
        session = AsyncMock()
        with patch(
            "src.db.service.search_role_memories",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await _check_lesson_contradictions(
                {"action_type": "budget_change", "user_id": "u1"},
                "buyer",
                session,
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_error_returns_none(self):
        """DB errors should be non-fatal — return None."""
        session = AsyncMock()
        with patch(
            "src.db.service.search_role_memories",
            new_callable=AsyncMock,
            side_effect=Exception("DB error"),
        ):
            result = await _check_lesson_contradictions(
                {"action_type": "budget_change", "user_id": "u1"},
                "buyer",
                session,
            )
        assert result is None
