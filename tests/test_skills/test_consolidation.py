"""Tests for src.skills.consolidation — memory consolidation engine.

Covers:
- _format_memories_for_prompt: all fields present, missing dates, content
  truncation at 300 chars.
- _parse_consolidation_response: valid JSON array, markdown code fences
  stripped, invalid JSON returns [], non-array JSON returns [], JSON
  embedded in surrounding text extracted.
- consolidate_role_memories: mock Haiku call with consolidated memories
  saved, invalid source_ids filtered out, groups with < 2 sources skipped,
  empty input returns zero stats, LLM error handled gracefully, invalid
  types normalized to "insight".

All DB and LLM operations are mocked; no database or API connection needed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.skills.consolidation import (
    _format_memories_for_prompt,
    _parse_consolidation_response,
    consolidate_role_memories,
)

# =====================================================================
# Helpers
# =====================================================================


def _make_memory(
    *,
    id: int = 1,
    memory_type: str = "decision",
    title: str = "Test memory",
    content: str = "Some content",
    confidence: float = 0.9,
    created_at: datetime | None = None,
    department_id: str = "marketing",
) -> MagicMock:
    """Create a MagicMock that mimics a RoleMemory ORM object."""
    mem = MagicMock()
    mem.id = id
    mem.memory_type = memory_type
    mem.title = title
    mem.content = content
    mem.confidence = confidence
    mem.created_at = created_at or datetime(2026, 2, 10, tzinfo=timezone.utc)
    mem.department_id = department_id
    return mem


def _make_turn_result(text: str = "[]", cost_usd: float = 0.01) -> MagicMock:
    """Create a MagicMock that mimics a TurnResult from run_agent_loop."""
    result = MagicMock()
    result.text = text
    result.cost = {"total_cost_usd": cost_usd}
    return result


def _patch_consolidation_deps(
    llm_return=None,
    llm_side_effect=None,
    save_side_effect=None,
):
    """Return a tuple of context-manager patches for consolidate_role_memories.

    The source code uses local imports so we patch at the origin modules:
      - src.agent.api_client.run_agent_loop
      - src.config.settings
      - src.db.session.get_db_session
      - src.db.service.save_consolidated_memory
    """
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    patches = {
        "llm": patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=llm_return,
            side_effect=llm_side_effect,
        ),
        "db_session": patch(
            "src.db.session.get_db_session",
            return_value=mock_session,
        ),
        "save": patch(
            "src.db.service.save_consolidated_memory",
            new_callable=AsyncMock,
            side_effect=save_side_effect,
        ),
        "settings": patch("src.config.settings"),
    }
    return patches


# =====================================================================
# _format_memories_for_prompt
# =====================================================================


class TestFormatMemoriesForPrompt:
    """Tests for _format_memories_for_prompt."""

    def test_all_fields_present(self):
        """All RoleMemory attributes are formatted into the output line."""
        mem = _make_memory(
            id=42,
            memory_type="anomaly",
            title="CTR spike",
            content="CTR rose 3x overnight",
            confidence=0.85,
            created_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        )
        output = _format_memories_for_prompt([mem])

        assert "42." in output
        assert "[2026-01-15]" in output
        assert "[anomaly]" in output
        assert "(conf=0.8)" in output  # 0.85 formatted as 0.8 with :.1f
        assert "CTR spike" in output
        assert "CTR rose 3x overnight" in output

    def test_multiple_memories_numbered(self):
        """Each memory gets its own numbered line."""
        mems = [
            _make_memory(id=1, title="First"),
            _make_memory(id=2, title="Second"),
            _make_memory(id=3, title="Third"),
        ]
        output = _format_memories_for_prompt(mems)
        lines = output.strip().split("\n")

        assert len(lines) == 3
        assert lines[0].startswith("1.")
        assert lines[1].startswith("2.")
        assert lines[2].startswith("3.")

    def test_missing_created_at_uses_question_mark(self):
        """When created_at is None, the date placeholder is '?'."""
        mem = _make_memory(id=5)
        mem.created_at = None
        output = _format_memories_for_prompt([mem])

        assert "[?]" in output

    def test_content_truncation_at_300_chars(self):
        """Content longer than 300 chars is truncated with '...'."""
        long_content = "A" * 500
        mem = _make_memory(id=7, content=long_content)
        output = _format_memories_for_prompt([mem])

        # Truncated content should be 297 chars + "..." = 300 chars total
        assert "A" * 297 + "..." in output
        # Original 500-char content should NOT appear in full
        assert "A" * 400 not in output

    def test_content_exactly_300_chars_not_truncated(self):
        """Content of exactly 300 chars should not be truncated."""
        content_300 = "B" * 300
        mem = _make_memory(id=8, content=content_300)
        output = _format_memories_for_prompt([mem])

        assert content_300 in output
        assert "..." not in output

    def test_empty_list_returns_empty_string(self):
        """No memories produces an empty string."""
        output = _format_memories_for_prompt([])
        assert output == ""

    def test_confidence_formatted_one_decimal(self):
        """Confidence is formatted to one decimal place."""
        mem = _make_memory(id=9, confidence=0.85)
        output = _format_memories_for_prompt([mem])

        assert "(conf=0.8)" in output  # 0.85 rounds to 0.8 with :.1f


# =====================================================================
# _parse_consolidation_response
# =====================================================================


class TestParseConsolidationResponse:
    """Tests for _parse_consolidation_response."""

    def test_valid_json_array(self):
        """A plain JSON array is parsed correctly."""
        data = [
            {
                "source_ids": [1, 2],
                "type": "insight",
                "title": "Merged",
                "content": "Combined content",
                "confidence": 0.9,
            }
        ]
        result = _parse_consolidation_response(json.dumps(data))

        assert len(result) == 1
        assert result[0]["source_ids"] == [1, 2]
        assert result[0]["type"] == "insight"
        assert result[0]["title"] == "Merged"

    def test_empty_array(self):
        """An empty JSON array returns an empty list."""
        result = _parse_consolidation_response("[]")
        assert result == []

    def test_markdown_code_fences_stripped(self):
        """Markdown ```json fences are removed before parsing."""
        raw = (
            "```json\n"
            '[{"source_ids": [1, 2], "type": "insight", "title": "T", '
            '"content": "C", "confidence": 0.8}]\n'
            "```"
        )
        result = _parse_consolidation_response(raw)

        assert len(result) == 1
        assert result[0]["source_ids"] == [1, 2]

    def test_markdown_plain_fences_stripped(self):
        """Markdown ``` fences (without language tag) are removed."""
        raw = (
            "```\n"
            '[{"source_ids": [3, 4], "type": "pattern", "title": "P", '
            '"content": "X", "confidence": 0.7}]\n'
            "```"
        )
        result = _parse_consolidation_response(raw)

        assert len(result) == 1
        assert result[0]["type"] == "pattern"

    def test_invalid_json_returns_empty(self):
        """Totally invalid JSON returns an empty list."""
        result = _parse_consolidation_response("this is not json at all")
        assert result == []

    def test_non_array_json_returns_empty(self):
        """A JSON object (not array) returns an empty list."""
        result = _parse_consolidation_response('{"key": "value"}')
        assert result == []

    def test_json_embedded_in_text_extracted(self):
        """JSON array embedded in surrounding text is extracted."""
        raw = (
            "Here are the consolidation results:\n"
            '[{"source_ids": [10, 11], "type": "lesson", "title": "L", '
            '"content": "Learned", "confidence": 0.95}]\n'
            "I hope this helps."
        )
        result = _parse_consolidation_response(raw)

        assert len(result) == 1
        assert result[0]["source_ids"] == [10, 11]
        assert result[0]["type"] == "lesson"

    def test_multiple_groups_parsed(self):
        """Multiple consolidation groups in one array are all returned."""
        data = [
            {
                "source_ids": [1, 2],
                "type": "insight",
                "title": "A",
                "content": "CA",
                "confidence": 0.8,
            },
            {
                "source_ids": [3, 4, 5],
                "type": "decision",
                "title": "B",
                "content": "CB",
                "confidence": 0.9,
            },
        ]
        result = _parse_consolidation_response(json.dumps(data))

        assert len(result) == 2
        assert result[0]["title"] == "A"
        assert result[1]["source_ids"] == [3, 4, 5]

    def test_whitespace_stripped(self):
        """Leading/trailing whitespace doesn't affect parsing."""
        raw = (
            '   \n  [{"source_ids": [1, 2], "type": "insight", '
            '"title": "T", "content": "C", "confidence": 0.5}]  \n  '
        )
        result = _parse_consolidation_response(raw)

        assert len(result) == 1


# =====================================================================
# consolidate_role_memories
# =====================================================================


class TestConsolidateRoleMemories:
    """Tests for consolidate_role_memories (async)."""

    @pytest.mark.asyncio
    async def test_empty_input_returns_zero_stats(self):
        """Fewer than 2 memories returns zeroed stats without calling LLM."""
        stats = await consolidate_role_memories(
            "u1",
            "media_buyer",
            "marketing",
            [],
        )
        assert stats["consolidated_count"] == 0
        assert stats["originals_marked"] == 0
        assert stats["cost_usd"] == 0.0
        assert stats["errors"] == []

    @pytest.mark.asyncio
    async def test_single_memory_returns_zero_stats(self):
        """A single memory is too few to consolidate."""
        mem = _make_memory(id=1)
        stats = await consolidate_role_memories(
            "u1",
            "media_buyer",
            "marketing",
            [mem],
        )
        assert stats["consolidated_count"] == 0

    @pytest.mark.asyncio
    async def test_successful_consolidation(self):
        """Mock Haiku call returns valid groups; consolidated memories saved."""
        mems = [
            _make_memory(id=10, title="Budget increased", content="Raised budget from 100 to 150"),
            _make_memory(id=11, title="Budget change", content="Budget went up by 50%"),
            _make_memory(id=12, title="Unrelated anomaly", content="CTR dropped"),
        ]

        llm_response = json.dumps(
            [
                {
                    "source_ids": [10, 11],
                    "type": "decision",
                    "title": "Budget increase decision",
                    "content": "Budget was raised from 100 to 150 (50% increase).",
                    "confidence": 0.92,
                }
            ]
        )

        mock_result = _make_turn_result(text=llm_response, cost_usd=0.005)
        patches = _patch_consolidation_deps(llm_return=mock_result)

        with (
            patches["llm"] as mock_llm,
            patches["db_session"],
            patches["save"] as mock_save,
            patches["settings"] as mock_settings,
        ):
            mock_settings.model_fast = "claude-3-haiku-20240307"
            stats = await consolidate_role_memories(
                "u1",
                "media_buyer",
                "marketing",
                mems,
            )

        assert stats["consolidated_count"] == 1
        assert stats["originals_marked"] == 2
        assert stats["cost_usd"] == 0.005
        assert stats["errors"] == []

        # Verify LLM was called
        mock_llm.assert_awaited_once()
        call_kwargs = mock_llm.call_args[1]
        assert call_kwargs["model"] == "claude-3-haiku-20240307"
        assert call_kwargs["tools"] is None
        assert call_kwargs["max_turns"] == 1

        # Verify save was called with correct args
        mock_save.assert_awaited_once()
        save_kwargs = mock_save.call_args[1]
        assert save_kwargs["user_id"] == "u1"
        assert save_kwargs["role_id"] == "media_buyer"
        assert save_kwargs["department_id"] == "marketing"
        assert save_kwargs["memory_type"] == "decision"
        assert save_kwargs["title"] == "Budget increase decision"
        assert save_kwargs["source_ids"] == [10, 11]
        assert save_kwargs["confidence"] == 0.92

    @pytest.mark.asyncio
    async def test_invalid_source_ids_filtered_out(self):
        """Source IDs not in the input memories are filtered; group skipped."""
        mems = [
            _make_memory(id=20, title="A"),
            _make_memory(id=21, title="B"),
        ]

        # LLM returns source_ids including 999 which doesn't exist
        llm_response = json.dumps(
            [
                {
                    "source_ids": [20, 999],
                    "type": "insight",
                    "title": "Partial match",
                    "content": "Only one valid source.",
                    "confidence": 0.8,
                }
            ]
        )

        mock_result = _make_turn_result(text=llm_response)
        patches = _patch_consolidation_deps(llm_return=mock_result)

        with (
            patches["llm"],
            patches["db_session"],
            patches["save"] as mock_save,
            patches["settings"] as mock_settings,
        ):
            mock_settings.model_fast = "claude-3-haiku-20240307"
            stats = await consolidate_role_memories(
                "u1",
                "role1",
                "dept1",
                mems,
            )

        # Only 1 valid source_id (20) remains, < 2, so group is skipped
        assert stats["consolidated_count"] == 0
        mock_save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_groups_with_fewer_than_2_sources_skipped(self):
        """Groups ending up with fewer than 2 valid sources are skipped."""
        mems = [
            _make_memory(id=30, title="Solo"),
            _make_memory(id=31, title="Partner"),
        ]

        # LLM returns a group with only 1 source_id
        llm_response = json.dumps(
            [
                {
                    "source_ids": [30],
                    "type": "pattern",
                    "title": "Solo group",
                    "content": "Can't consolidate just one.",
                    "confidence": 0.7,
                }
            ]
        )

        mock_result = _make_turn_result(text=llm_response)
        patches = _patch_consolidation_deps(llm_return=mock_result)

        with (
            patches["llm"],
            patches["db_session"],
            patches["save"] as mock_save,
            patches["settings"] as mock_settings,
        ):
            mock_settings.model_fast = "claude-3-haiku-20240307"
            stats = await consolidate_role_memories(
                "u1",
                "role1",
                "dept1",
                mems,
            )

        assert stats["consolidated_count"] == 0
        mock_save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_llm_error_handled_gracefully(self):
        """LLM exception is caught; error recorded in stats, no crash."""
        mems = [
            _make_memory(id=40, title="A"),
            _make_memory(id=41, title="B"),
        ]

        patches = _patch_consolidation_deps(
            llm_side_effect=RuntimeError("API timeout"),
        )

        with (
            patches["llm"],
            patches["db_session"],
            patches["save"],
            patches["settings"] as mock_settings,
        ):
            mock_settings.model_fast = "claude-3-haiku-20240307"
            stats = await consolidate_role_memories(
                "u1",
                "role1",
                "dept1",
                mems,
            )

        assert stats["consolidated_count"] == 0
        assert stats["originals_marked"] == 0
        assert len(stats["errors"]) == 1
        assert "LLM error" in stats["errors"][0]
        assert "API timeout" in stats["errors"][0]

    @pytest.mark.asyncio
    async def test_invalid_type_normalized_to_insight(self):
        """Memory type not in valid set is normalized to 'insight'."""
        mems = [
            _make_memory(id=50, title="A"),
            _make_memory(id=51, title="B"),
        ]

        llm_response = json.dumps(
            [
                {
                    "source_ids": [50, 51],
                    "type": "bogus_type",
                    "title": "Has invalid type",
                    "content": "Content here.",
                    "confidence": 0.75,
                }
            ]
        )

        mock_result = _make_turn_result(text=llm_response)
        patches = _patch_consolidation_deps(llm_return=mock_result)

        with (
            patches["llm"],
            patches["db_session"],
            patches["save"] as mock_save,
            patches["settings"] as mock_settings,
        ):
            mock_settings.model_fast = "claude-3-haiku-20240307"
            stats = await consolidate_role_memories(
                "u1",
                "role1",
                "dept1",
                mems,
            )

        assert stats["consolidated_count"] == 1
        save_kwargs = mock_save.call_args[1]
        assert save_kwargs["memory_type"] == "insight"

    @pytest.mark.asyncio
    async def test_title_truncated_to_100_chars(self):
        """Titles exceeding 100 characters are truncated."""
        mems = [
            _make_memory(id=60, title="A"),
            _make_memory(id=61, title="B"),
        ]

        long_title = "X" * 200
        llm_response = json.dumps(
            [
                {
                    "source_ids": [60, 61],
                    "type": "insight",
                    "title": long_title,
                    "content": "Valid content.",
                    "confidence": 0.8,
                }
            ]
        )

        mock_result = _make_turn_result(text=llm_response)
        patches = _patch_consolidation_deps(llm_return=mock_result)

        with (
            patches["llm"],
            patches["db_session"],
            patches["save"] as mock_save,
            patches["settings"] as mock_settings,
        ):
            mock_settings.model_fast = "claude-3-haiku-20240307"
            stats = await consolidate_role_memories(
                "u1",
                "role1",
                "dept1",
                mems,
            )

        assert stats["consolidated_count"] == 1
        save_kwargs = mock_save.call_args[1]
        assert len(save_kwargs["title"]) == 100

    @pytest.mark.asyncio
    async def test_content_truncated_to_500_chars(self):
        """Content exceeding 500 characters is truncated."""
        mems = [
            _make_memory(id=70, title="A"),
            _make_memory(id=71, title="B"),
        ]

        long_content = "Y" * 800
        llm_response = json.dumps(
            [
                {
                    "source_ids": [70, 71],
                    "type": "decision",
                    "title": "Truncation test",
                    "content": long_content,
                    "confidence": 0.9,
                }
            ]
        )

        mock_result = _make_turn_result(text=llm_response)
        patches = _patch_consolidation_deps(llm_return=mock_result)

        with (
            patches["llm"],
            patches["db_session"],
            patches["save"] as mock_save,
            patches["settings"] as mock_settings,
        ):
            mock_settings.model_fast = "claude-3-haiku-20240307"
            stats = await consolidate_role_memories(
                "u1",
                "role1",
                "dept1",
                mems,
            )

        assert stats["consolidated_count"] == 1
        save_kwargs = mock_save.call_args[1]
        assert len(save_kwargs["content"]) == 500

    @pytest.mark.asyncio
    async def test_confidence_clamped_to_valid_range(self):
        """Confidence values outside 0.0-1.0 are clamped."""
        mems = [
            _make_memory(id=80, title="A"),
            _make_memory(id=81, title="B"),
        ]

        llm_response = json.dumps(
            [
                {
                    "source_ids": [80, 81],
                    "type": "insight",
                    "title": "High confidence",
                    "content": "Very sure.",
                    "confidence": 5.0,
                }
            ]
        )

        mock_result = _make_turn_result(text=llm_response)
        patches = _patch_consolidation_deps(llm_return=mock_result)

        with (
            patches["llm"],
            patches["db_session"],
            patches["save"] as mock_save,
            patches["settings"] as mock_settings,
        ):
            mock_settings.model_fast = "claude-3-haiku-20240307"
            stats = await consolidate_role_memories(
                "u1",
                "role1",
                "dept1",
                mems,
            )

        assert stats["consolidated_count"] == 1
        save_kwargs = mock_save.call_args[1]
        assert save_kwargs["confidence"] == 1.0

    @pytest.mark.asyncio
    async def test_empty_title_or_content_skipped(self):
        """Groups with empty title or content are skipped."""
        mems = [
            _make_memory(id=90, title="A"),
            _make_memory(id=91, title="B"),
        ]

        llm_response = json.dumps(
            [
                {
                    "source_ids": [90, 91],
                    "type": "insight",
                    "title": "",
                    "content": "Has content but no title.",
                    "confidence": 0.8,
                }
            ]
        )

        mock_result = _make_turn_result(text=llm_response)
        patches = _patch_consolidation_deps(llm_return=mock_result)

        with (
            patches["llm"],
            patches["db_session"],
            patches["save"] as mock_save,
            patches["settings"] as mock_settings,
        ):
            mock_settings.model_fast = "claude-3-haiku-20240307"
            stats = await consolidate_role_memories(
                "u1",
                "role1",
                "dept1",
                mems,
            )

        assert stats["consolidated_count"] == 0
        mock_save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_db_save_error_recorded_in_stats(self):
        """Database errors during save are caught and recorded."""
        mems = [
            _make_memory(id=100, title="A"),
            _make_memory(id=101, title="B"),
        ]

        llm_response = json.dumps(
            [
                {
                    "source_ids": [100, 101],
                    "type": "insight",
                    "title": "Will fail to save",
                    "content": "DB goes boom.",
                    "confidence": 0.8,
                }
            ]
        )

        mock_result = _make_turn_result(text=llm_response)
        patches = _patch_consolidation_deps(
            llm_return=mock_result,
            save_side_effect=RuntimeError("Connection refused"),
        )

        with (
            patches["llm"],
            patches["db_session"],
            patches["save"],
            patches["settings"] as mock_settings,
        ):
            mock_settings.model_fast = "claude-3-haiku-20240307"
            stats = await consolidate_role_memories(
                "u1",
                "role1",
                "dept1",
                mems,
            )

        assert stats["consolidated_count"] == 0
        assert len(stats["errors"]) == 1
        assert "Save error" in stats["errors"][0]
        assert "Connection refused" in stats["errors"][0]

    @pytest.mark.asyncio
    async def test_multiple_groups_processed(self):
        """Multiple valid consolidation groups are all saved."""
        mems = [
            _make_memory(id=110, title="A"),
            _make_memory(id=111, title="B"),
            _make_memory(id=112, title="C"),
            _make_memory(id=113, title="D"),
        ]

        llm_response = json.dumps(
            [
                {
                    "source_ids": [110, 111],
                    "type": "decision",
                    "title": "Group 1",
                    "content": "Merged AB.",
                    "confidence": 0.9,
                },
                {
                    "source_ids": [112, 113],
                    "type": "pattern",
                    "title": "Group 2",
                    "content": "Merged CD.",
                    "confidence": 0.85,
                },
            ]
        )

        mock_result = _make_turn_result(text=llm_response)
        patches = _patch_consolidation_deps(llm_return=mock_result)

        with (
            patches["llm"],
            patches["db_session"],
            patches["save"] as mock_save,
            patches["settings"] as mock_settings,
        ):
            mock_settings.model_fast = "claude-3-haiku-20240307"
            stats = await consolidate_role_memories(
                "u1",
                "role1",
                "dept1",
                mems,
            )

        assert stats["consolidated_count"] == 2
        assert stats["originals_marked"] == 4  # 2 + 2
        assert mock_save.await_count == 2

    @pytest.mark.asyncio
    async def test_source_ids_not_list_skipped(self):
        """Groups where source_ids is not a list are skipped."""
        mems = [
            _make_memory(id=120, title="A"),
            _make_memory(id=121, title="B"),
        ]

        llm_response = json.dumps(
            [
                {
                    "source_ids": "not a list",
                    "type": "insight",
                    "title": "Bad source_ids",
                    "content": "Should be skipped.",
                    "confidence": 0.8,
                }
            ]
        )

        mock_result = _make_turn_result(text=llm_response)
        patches = _patch_consolidation_deps(llm_return=mock_result)

        with (
            patches["llm"],
            patches["db_session"],
            patches["save"] as mock_save,
            patches["settings"] as mock_settings,
        ):
            mock_settings.model_fast = "claude-3-haiku-20240307"
            stats = await consolidate_role_memories(
                "u1",
                "role1",
                "dept1",
                mems,
            )

        assert stats["consolidated_count"] == 0
        mock_save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_llm_returns_no_groups(self):
        """LLM returning empty array means nothing to consolidate."""
        mems = [
            _make_memory(id=130, title="A"),
            _make_memory(id=131, title="B"),
        ]

        mock_result = _make_turn_result(text="[]")
        patches = _patch_consolidation_deps(llm_return=mock_result)

        with (
            patches["llm"],
            patches["db_session"],
            patches["save"],
            patches["settings"] as mock_settings,
        ):
            mock_settings.model_fast = "claude-3-haiku-20240307"
            stats = await consolidate_role_memories(
                "u1",
                "role1",
                "dept1",
                mems,
            )

        assert stats["consolidated_count"] == 0
        assert stats["originals_marked"] == 0
        assert stats["errors"] == []

    @pytest.mark.asyncio
    async def test_cost_recorded_from_llm_result(self):
        """Cost from the LLM result is captured in stats."""
        mems = [
            _make_memory(id=140, title="A"),
            _make_memory(id=141, title="B"),
        ]

        mock_result = _make_turn_result(text="[]", cost_usd=0.0042)
        patches = _patch_consolidation_deps(llm_return=mock_result)

        with (
            patches["llm"],
            patches["db_session"],
            patches["save"],
            patches["settings"] as mock_settings,
        ):
            mock_settings.model_fast = "claude-3-haiku-20240307"
            stats = await consolidate_role_memories(
                "u1",
                "role1",
                "dept1",
                mems,
            )

        assert stats["cost_usd"] == 0.0042

    @pytest.mark.asyncio
    async def test_cost_none_handled(self):
        """When result.cost is None, cost_usd defaults to 0.0."""
        mems = [
            _make_memory(id=150, title="A"),
            _make_memory(id=151, title="B"),
        ]

        mock_result = MagicMock()
        mock_result.text = "[]"
        mock_result.cost = None

        patches = _patch_consolidation_deps(llm_return=mock_result)

        with (
            patches["llm"],
            patches["db_session"],
            patches["save"],
            patches["settings"] as mock_settings,
        ):
            mock_settings.model_fast = "claude-3-haiku-20240307"
            stats = await consolidate_role_memories(
                "u1",
                "role1",
                "dept1",
                mems,
            )

        assert stats["cost_usd"] == 0.0
