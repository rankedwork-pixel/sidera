"""Tests for the memory_consolidation_workflow in daily_briefing.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.workflows.daily_briefing import memory_consolidation_workflow
from tests.test_workflows.conftest import _make_mock_context

# =====================================================================
# Helpers
# =====================================================================


def _make_mock_memory(
    *,
    mem_id: int = 1,
    department_id: str = "marketing",
    memory_type: str = "decision",
    title: str = "Test memory",
    content: str = "Test content",
    confidence: float = 0.9,
) -> MagicMock:
    """Build a fake RoleMemory ORM object."""
    mem = MagicMock()
    mem.id = mem_id
    mem.department_id = department_id
    mem.memory_type = memory_type
    mem.title = title
    mem.content = content
    mem.confidence = confidence
    return mem


def _consolidation_stats(
    *,
    consolidated_count: int = 2,
    originals_marked: int = 5,
    cost_usd: float = 0.003,
    errors: list | None = None,
) -> dict:
    """Return a fake stats dict as returned by consolidate_role_memories."""
    return {
        "consolidated_count": consolidated_count,
        "originals_marked": originals_marked,
        "cost_usd": cost_usd,
        "errors": errors or [],
    }


# =====================================================================
# Tests
# =====================================================================


class TestMemoryConsolidationWorkflow:
    """Tests for memory_consolidation_workflow."""

    @pytest.mark.asyncio
    async def test_processes_all_role_pairs(self):
        """When get_distinct_memory_role_pairs returns 2 pairs, both are processed."""
        ctx = _make_mock_context()

        mock_memories = [_make_mock_memory(mem_id=i) for i in range(5)]
        mock_consolidate = AsyncMock(return_value=_consolidation_stats())

        with (
            patch(
                "src.db.service.get_distinct_memory_role_pairs",
                AsyncMock(return_value=[("user1", "buyer"), ("user2", "analyst")]),
            ),
            patch(
                "src.db.service.get_unconsolidated_memories",
                AsyncMock(return_value=mock_memories),
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=AsyncMock(),
            ) as mock_session,
            patch(
                "src.skills.consolidation.consolidate_role_memories",
                mock_consolidate,
            ),
        ):
            # Make the async context manager work
            mock_session.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(),
            )
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await memory_consolidation_workflow._handler(ctx)

        # Both pairs should be processed
        assert result["roles_processed"] == 2
        assert mock_consolidate.call_count == 2

        # Verify each pair was called with the correct args
        calls = mock_consolidate.call_args_list
        assert calls[0].args[0] == "user1"
        assert calls[0].args[1] == "buyer"
        assert calls[1].args[0] == "user2"
        assert calls[1].args[1] == "analyst"

    @pytest.mark.asyncio
    async def test_skips_few_memories(self):
        """When a pair has < 3 memories, it is skipped (not consolidated)."""
        ctx = _make_mock_context()

        # Only 2 memories — below the threshold of 3
        few_memories = [_make_mock_memory(mem_id=i) for i in range(2)]
        mock_consolidate = AsyncMock(return_value=_consolidation_stats())

        with (
            patch(
                "src.db.service.get_distinct_memory_role_pairs",
                AsyncMock(return_value=[("user1", "buyer")]),
            ),
            patch(
                "src.db.service.get_unconsolidated_memories",
                AsyncMock(return_value=few_memories),
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=AsyncMock(),
            ) as mock_session,
            patch(
                "src.skills.consolidation.consolidate_role_memories",
                mock_consolidate,
            ),
        ):
            mock_session.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(),
            )
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await memory_consolidation_workflow._handler(ctx)

        # Skipped — should not be counted as processed
        assert result["roles_processed"] == 0
        assert result["consolidated_count"] == 0
        # consolidate_role_memories should never have been called
        mock_consolidate.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_consolidation_errors(self):
        """Errors during one pair's consolidation don't crash the workflow."""
        ctx = _make_mock_context()

        mock_memories = [_make_mock_memory(mem_id=i) for i in range(5)]

        # First pair succeeds, second pair raises
        call_count = 0

        async def consolidate_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("LLM timeout")
            return _consolidation_stats()

        with (
            patch(
                "src.db.service.get_distinct_memory_role_pairs",
                AsyncMock(
                    return_value=[("user1", "buyer"), ("user2", "analyst")],
                ),
            ),
            patch(
                "src.db.service.get_unconsolidated_memories",
                AsyncMock(return_value=mock_memories),
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=AsyncMock(),
            ) as mock_session,
            patch(
                "src.skills.consolidation.consolidate_role_memories",
                AsyncMock(side_effect=consolidate_side_effect),
            ),
        ):
            mock_session.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(),
            )
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            # Should NOT raise
            result = await memory_consolidation_workflow._handler(ctx)

        # First pair processed, second errored
        assert result["roles_processed"] == 1
        assert len(result["errors"]) == 1
        assert "user2/analyst" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_accumulates_stats(self):
        """Stats from multiple pairs are accumulated correctly."""
        ctx = _make_mock_context()

        mock_memories = [_make_mock_memory(mem_id=i) for i in range(5)]

        # Each pair returns different stats
        stats_sequence = [
            _consolidation_stats(
                consolidated_count=3,
                originals_marked=6,
                cost_usd=0.005,
            ),
            _consolidation_stats(
                consolidated_count=1,
                originals_marked=2,
                cost_usd=0.002,
            ),
        ]
        mock_consolidate = AsyncMock(side_effect=stats_sequence)

        with (
            patch(
                "src.db.service.get_distinct_memory_role_pairs",
                AsyncMock(return_value=[("u1", "r1"), ("u2", "r2")]),
            ),
            patch(
                "src.db.service.get_unconsolidated_memories",
                AsyncMock(return_value=mock_memories),
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=AsyncMock(),
            ) as mock_session,
            patch(
                "src.skills.consolidation.consolidate_role_memories",
                mock_consolidate,
            ),
        ):
            mock_session.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(),
            )
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await memory_consolidation_workflow._handler(ctx)

        assert result["roles_processed"] == 2
        assert result["consolidated_count"] == 3 + 1
        assert result["originals_marked"] == 6 + 2
        assert result["total_cost_usd"] == pytest.approx(0.005 + 0.002)
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_empty_pairs_returns_zero_stats(self):
        """No role pairs means zero stats returned."""
        ctx = _make_mock_context()

        with (
            patch(
                "src.db.service.get_distinct_memory_role_pairs",
                AsyncMock(return_value=[]),
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=AsyncMock(),
            ) as mock_session,
        ):
            mock_session.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(),
            )
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await memory_consolidation_workflow._handler(ctx)

        assert result["roles_processed"] == 0
        assert result["consolidated_count"] == 0
        assert result["originals_marked"] == 0
        assert result["total_cost_usd"] == 0.0
        assert result["errors"] == []
