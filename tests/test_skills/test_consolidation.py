"""Tests for src.skills.consolidation — memory consolidation engine.

Covers:
- _extract_keywords: empty string, basic text, mixed case, stopword removal.
- _jaccard_similarity: both empty, identical, no overlap, partial overlap.
- cluster_memories_by_similarity: empty list, all unique, overlapping,
  min_cluster_size respected.
- apply_confidence_boosting: 1 source (no boost), 2/3/4+ sources, cap at 0.95.
- _format_memories_for_prompt: all fields present, missing dates, content
  truncation at 300 chars, clustered with [Cluster N] headers + [Unclustered].
- _parse_consolidation_response: valid JSON array, markdown code fences
  stripped, invalid JSON returns [], non-array JSON returns [], JSON
  embedded in surrounding text extracted.
- consolidate_role_memories: mock Haiku call with consolidated memories
  saved, invalid source_ids filtered out, groups with < 2 sources skipped,
  empty input returns zero stats, LLM error handled gracefully, invalid
  types normalized to "insight", confidence boosting applied post-hoc,
  summaries_created stat tracked.

All DB and LLM operations are mocked; no database or API connection needed.
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.skills.consolidation import (
    _extract_keywords,
    _format_memories_for_prompt,
    _jaccard_similarity,
    _parse_consolidation_response,
    apply_confidence_boosting,
    cluster_memories_by_similarity,
    consolidate_role_memories,
)

# =====================================================================
# Helpers
# =====================================================================


def _make_memory(
    id: int = 1,
    title: str = "Test memory",
    content: str = "Some content",
    memory_type: str = "insight",
    confidence: float = 0.7,
    created_at: datetime | None = None,
) -> SimpleNamespace:
    """Create a SimpleNamespace that mimics a RoleMemory ORM object."""
    return SimpleNamespace(
        id=id,
        title=title,
        content=content,
        memory_type=memory_type,
        confidence=confidence,
        created_at=created_at or datetime(2025, 1, 15),
    )


def _make_turn_result(text: str = "[]", cost_usd: float = 0.01) -> SimpleNamespace:
    """Create a SimpleNamespace that mimics a TurnResult from run_agent_loop."""
    return SimpleNamespace(
        text=text,
        cost={"total_cost_usd": cost_usd},
    )


def _patch_consolidation_deps(
    llm_return=None,
    llm_side_effect=None,
    save_side_effect=None,
):
    """Return a dict of context-manager patches for consolidate_role_memories.

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
# _extract_keywords
# =====================================================================


class TestExtractKeywords:
    """Tests for _extract_keywords."""

    def test_empty_string_returns_empty_set(self):
        """Empty string produces no keywords."""
        assert _extract_keywords("") == set()

    def test_basic_text_extracts_keywords(self):
        """Meaningful words are extracted; stopwords and short words removed."""
        result = _extract_keywords("The budget increase was significant for campaigns")
        # "the", "was", "for" are stopwords; all kept words should be >= 3 chars
        assert "budget" in result
        assert "increase" in result
        assert "significant" in result
        assert "campaigns" in result
        # Stopwords excluded
        assert "the" not in result
        assert "was" not in result
        assert "for" not in result

    def test_mixed_case_lowered(self):
        """All keywords are lowercased regardless of input case."""
        result = _extract_keywords("Budget INCREASE Campaign")
        assert "budget" in result
        assert "increase" in result
        assert "campaign" in result
        # Uppercase originals not present
        assert "Budget" not in result
        assert "INCREASE" not in result

    def test_short_words_removed(self):
        """Words shorter than 3 characters are excluded."""
        result = _extract_keywords("I am on it no go up")
        assert len(result) == 0

    def test_non_alpha_removed(self):
        """Numbers and special characters are stripped; only alpha words kept."""
        result = _extract_keywords("revenue $500 increased 3x overnight!")
        assert "revenue" in result
        assert "increased" in result
        assert "overnight" in result
        # Numbers and symbols should not appear
        assert "500" not in result
        assert "3x" not in result


# =====================================================================
# _jaccard_similarity
# =====================================================================


class TestJaccardSimilarity:
    """Tests for _jaccard_similarity."""

    def test_both_empty_returns_zero(self):
        """Two empty sets produce 0.0 similarity."""
        assert _jaccard_similarity(set(), set()) == 0.0

    def test_identical_sets_returns_one(self):
        """Identical sets produce 1.0 similarity."""
        s = {"budget", "increase", "campaign"}
        assert _jaccard_similarity(s, s) == 1.0

    def test_no_overlap_returns_zero(self):
        """Completely disjoint sets produce 0.0."""
        a = {"budget", "increase"}
        b = {"creative", "analysis"}
        assert _jaccard_similarity(a, b) == 0.0

    def test_partial_overlap_correct_ratio(self):
        """Partial overlap gives |intersection| / |union|."""
        a = {"budget", "increase", "campaign"}
        b = {"budget", "campaign", "creative"}
        # intersection = {budget, campaign} -> 2
        # union = {budget, increase, campaign, creative} -> 4
        assert _jaccard_similarity(a, b) == pytest.approx(0.5)

    def test_one_empty_one_full_returns_zero(self):
        """One empty set and one non-empty produce 0.0."""
        assert _jaccard_similarity(set(), {"budget"}) == 0.0
        assert _jaccard_similarity({"budget"}, set()) == 0.0


# =====================================================================
# cluster_memories_by_similarity
# =====================================================================


class TestClusterMemoriesBySimilarity:
    """Tests for cluster_memories_by_similarity."""

    def test_empty_list_returns_empty(self):
        """No memories produces no clusters."""
        assert cluster_memories_by_similarity([]) == []

    def test_all_unique_no_clusters(self):
        """Memories with completely different content produce no clusters
        meeting min_cluster_size."""
        mems = [
            _make_memory(id=1, title="alpha", content="completely unique words here"),
            _make_memory(id=2, title="beta", content="totally different vocabulary present"),
            _make_memory(id=3, title="gamma", content="nothing overlapping whatsoever shown"),
        ]
        clusters = cluster_memories_by_similarity(mems, similarity_threshold=0.9)
        assert clusters == []

    def test_overlapping_memories_clustered(self):
        """Memories with high keyword overlap are grouped together."""
        mems = [
            _make_memory(
                id=1,
                title="budget increase",
                content="campaign budget increased significantly",
            ),
            _make_memory(
                id=2,
                title="budget change",
                content="budget for campaign was increased",
            ),
            _make_memory(
                id=3,
                title="creative analysis",
                content="creative performance report generated",
            ),
        ]
        clusters = cluster_memories_by_similarity(
            mems,
            similarity_threshold=0.2,
        )
        # Memories 1 and 2 share "budget", "campaign", "increased"
        assert len(clusters) >= 1
        # At least one cluster has both budget memories
        cluster_ids = [[getattr(m, "id") for m in c] for c in clusters]
        budget_cluster = [c for c in cluster_ids if 1 in c and 2 in c]
        assert len(budget_cluster) == 1

    def test_min_cluster_size_respected(self):
        """Clusters smaller than min_cluster_size are excluded."""
        mems = [
            _make_memory(id=1, title="budget increase", content="campaign budget increased"),
            _make_memory(id=2, title="budget change", content="budget for campaign was increased"),
        ]
        # With min_cluster_size=3, a cluster of 2 should be excluded
        clusters = cluster_memories_by_similarity(
            mems,
            min_cluster_size=3,
            similarity_threshold=0.1,
        )
        assert clusters == []

    def test_min_cluster_size_default_is_two(self):
        """Default min_cluster_size allows pairs."""
        mems = [
            _make_memory(id=1, title="budget increase", content="campaign budget went up"),
            _make_memory(id=2, title="budget raise", content="campaign budget increased"),
        ]
        clusters = cluster_memories_by_similarity(mems, similarity_threshold=0.1)
        # Should form a cluster of 2
        assert len(clusters) == 1
        assert len(clusters[0]) == 2


# =====================================================================
# apply_confidence_boosting
# =====================================================================


class TestApplyConfidenceBoosting:
    """Tests for apply_confidence_boosting."""

    def test_one_source_no_boost(self):
        """Single source returns the original confidence unchanged."""
        assert apply_confidence_boosting(1, 0.7) == 0.7

    def test_two_sources_plus_005(self):
        """Two sources add 0.05 to max confidence."""
        assert apply_confidence_boosting(2, 0.7) == pytest.approx(0.75)

    def test_three_sources_plus_010(self):
        """Three sources add 0.10 to max confidence."""
        assert apply_confidence_boosting(3, 0.7) == pytest.approx(0.80)

    def test_four_sources_plus_015(self):
        """Four or more sources add 0.15 to max confidence."""
        assert apply_confidence_boosting(4, 0.7) == pytest.approx(0.85)

    def test_five_sources_same_as_four(self):
        """Five sources also add 0.15 (same as 4+)."""
        assert apply_confidence_boosting(5, 0.7) == pytest.approx(0.85)

    def test_cap_at_095(self):
        """Result is capped at 0.95 regardless of input."""
        assert apply_confidence_boosting(4, 0.90) == 0.95
        assert apply_confidence_boosting(3, 0.95) == 0.95
        assert apply_confidence_boosting(2, 0.92) == 0.95

    def test_zero_sources_returns_original(self):
        """Zero sources (edge case) returns original confidence."""
        assert apply_confidence_boosting(0, 0.5) == 0.5


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
            created_at=datetime(2025, 1, 15),
        )
        output = _format_memories_for_prompt([mem])

        assert "42." in output
        assert "[2025-01-15]" in output
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
        mem = _make_memory(id=5, created_at=None)
        # Override created_at to None (SimpleNamespace allows this)
        mem.created_at = None
        output = _format_memories_for_prompt([mem])

        assert "[?]" in output

    def test_content_truncation_at_300_chars(self):
        """Content longer than 300 chars is truncated with '...'."""
        long_content = "A" * 500
        mem = _make_memory(id=7, content=long_content)
        output = _format_memories_for_prompt([mem])

        assert "A" * 297 + "..." in output
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

    def test_without_clusters_flat_list(self):
        """Without clusters, output is a flat numbered list."""
        mems = [
            _make_memory(id=1, title="Alpha"),
            _make_memory(id=2, title="Beta"),
        ]
        output = _format_memories_for_prompt(mems)
        assert "[Cluster" not in output
        assert "[Unclustered]" not in output
        assert "1." in output
        assert "2." in output

    def test_with_clusters_shows_headers(self):
        """With clusters, output has [Cluster N] headers and [Unclustered]."""
        mem1 = _make_memory(id=1, title="Budget up")
        mem2 = _make_memory(id=2, title="Budget down")
        mem3 = _make_memory(id=3, title="Creative test")

        clusters = [[mem1, mem2]]  # mem3 is unclustered

        output = _format_memories_for_prompt([mem1, mem2, mem3], clusters=clusters)

        assert "[Cluster 1]" in output
        assert "(2 memories)" in output
        assert "[Unclustered]" in output
        # All memories should appear
        assert "1." in output
        assert "2." in output
        assert "3." in output

    def test_with_clusters_no_unclustered(self):
        """When all memories are clustered, no [Unclustered] section appears."""
        mem1 = _make_memory(id=1, title="Budget up")
        mem2 = _make_memory(id=2, title="Budget down")

        clusters = [[mem1, mem2]]

        output = _format_memories_for_prompt([mem1, mem2], clusters=clusters)

        assert "[Cluster 1]" in output
        assert "[Unclustered]" not in output


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


# =====================================================================
# consolidate_role_memories
# =====================================================================


class TestConsolidateRoleMemories:
    """Tests for consolidate_role_memories (async)."""

    @pytest.mark.asyncio
    async def test_empty_input_returns_zero_stats(self):
        """Fewer than 2 memories returns zeroed stats without calling LLM."""
        stats = await consolidate_role_memories("u1", "media_buyer", "marketing", [])
        assert stats["consolidated_count"] == 0
        assert stats["originals_marked"] == 0
        assert stats["cost_usd"] == 0.0
        assert stats["errors"] == []

    @pytest.mark.asyncio
    async def test_single_memory_returns_zero_stats(self):
        """A single memory is too few to consolidate."""
        mem = _make_memory(id=1)
        stats = await consolidate_role_memories("u1", "media_buyer", "marketing", [mem])
        assert stats["consolidated_count"] == 0

    @pytest.mark.asyncio
    async def test_successful_consolidation(self):
        """Mock Haiku call returns valid groups; consolidated memories saved."""
        mems = [
            _make_memory(
                id=10,
                title="Budget increased",
                content="Raised budget from 100 to 150",
                confidence=0.8,
            ),
            _make_memory(
                id=11,
                title="Budget change",
                content="Budget went up by 50%",
                confidence=0.7,
            ),
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
                    "is_summary": False,
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
            stats = await consolidate_role_memories("u1", "media_buyer", "marketing", mems)

        assert stats["consolidated_count"] == 1
        assert stats["originals_marked"] == 2
        assert stats["cost_usd"] == 0.005
        assert stats["errors"] == []

        mock_llm.assert_awaited_once()
        mock_save.assert_awaited_once()
        save_kwargs = mock_save.call_args[1]
        assert save_kwargs["user_id"] == "u1"
        assert save_kwargs["role_id"] == "media_buyer"
        assert save_kwargs["source_ids"] == [10, 11]

    @pytest.mark.asyncio
    async def test_confidence_boosting_applied_post_hoc(self):
        """Confidence boosting is applied using source memory confidences,
        not the LLM's returned confidence value."""
        mems = [
            _make_memory(id=1, title="Budget up", content="Budget increased", confidence=0.8),
            _make_memory(id=2, title="Budget raise", content="Budget was raised", confidence=0.7),
            _make_memory(id=3, title="Budget hike", content="Budget went higher", confidence=0.6),
        ]

        # LLM returns confidence=0.5, but boosting should override with
        # max(0.8, 0.7, 0.6) + 0.10 (3 sources) = 0.90
        llm_response = json.dumps(
            [
                {
                    "source_ids": [1, 2, 3],
                    "type": "insight",
                    "title": "Budget increases",
                    "content": "Multiple budget increases observed.",
                    "confidence": 0.5,
                    "is_summary": False,
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
            stats = await consolidate_role_memories("u1", "role1", "dept1", mems)

        assert stats["consolidated_count"] == 1
        save_kwargs = mock_save.call_args[1]
        # 3 sources: max_conf=0.8 + 0.10 = 0.90
        assert save_kwargs["confidence"] == pytest.approx(0.90)

    @pytest.mark.asyncio
    async def test_summaries_created_stat_tracked(self):
        """When LLM returns is_summary=true, summaries_created is incremented
        and title gets [Summary] prefix."""
        mems = [
            _make_memory(id=1, title="Pattern A", content="observed pattern alpha"),
            _make_memory(id=2, title="Pattern B", content="observed pattern beta"),
        ]

        llm_response = json.dumps(
            [
                {
                    "source_ids": [1, 2],
                    "type": "pattern",
                    "title": "Recurring pattern",
                    "content": "Summary of recurring patterns.",
                    "confidence": 0.8,
                    "is_summary": True,
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
            stats = await consolidate_role_memories("u1", "role1", "dept1", mems)

        assert stats["summaries_created"] == 1
        assert stats["consolidated_count"] == 1
        save_kwargs = mock_save.call_args[1]
        assert save_kwargs["title"].startswith("[Summary]")

    @pytest.mark.asyncio
    async def test_invalid_source_ids_filtered_out(self):
        """Source IDs not in the input memories are filtered; group skipped if < 2."""
        mems = [
            _make_memory(id=20, title="A"),
            _make_memory(id=21, title="B"),
        ]

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
            stats = await consolidate_role_memories("u1", "role1", "dept1", mems)

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
            stats = await consolidate_role_memories("u1", "role1", "dept1", mems)

        assert stats["consolidated_count"] == 0
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
            stats = await consolidate_role_memories("u1", "role1", "dept1", mems)

        assert stats["consolidated_count"] == 1
        save_kwargs = mock_save.call_args[1]
        assert save_kwargs["memory_type"] == "insight"

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
            stats = await consolidate_role_memories("u1", "role1", "dept1", mems)

        assert stats["consolidated_count"] == 0
        assert stats["originals_marked"] == 0
        assert stats["errors"] == []
