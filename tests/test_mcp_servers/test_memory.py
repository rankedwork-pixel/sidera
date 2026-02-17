"""Tests for the save_memory MCP tool and LLM-powered conversation memory extraction.

Covers:
- save_memory tool: context checks, validation, DB persistence, count limits
- extract_conversation_memories_llm: parsing, confidence filtering, error handling
- Memory context lifecycle: set/clear
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_servers.memory import (
    _CONVERSATION_MEMORY_PROMPT,
    _MAX_MEMORIES_PER_TURN,
    _memory_context_var,
    _memory_count_var,
    clear_memory_context,
    extract_conversation_memories_llm,
    save_memory,
    set_memory_context,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_error(result: dict) -> bool:
    """Check if an MCP result is an error response."""
    return result.get("is_error", False)


def _text(result: dict) -> str:
    """Extract text from an MCP result."""
    content = result.get("content", [])
    if content and isinstance(content, list):
        return content[0].get("text", "")
    return ""


def _mock_db_session():
    """Create a mock async context manager for get_db_session."""
    mock_session = AsyncMock()
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm, mock_session


# ===========================================================================
# 1. Memory context lifecycle
# ===========================================================================


class TestMemoryContext:
    """Verify set/clear memory context works."""

    def test_set_and_clear_context(self):
        """set_memory_context stores values, clear_memory_context resets."""
        set_memory_context("test_role", "test_dept", "user_1")
        ctx = _memory_context_var.get()
        assert ctx is not None
        assert ctx["role_id"] == "test_role"
        assert ctx["department_id"] == "test_dept"
        assert ctx["user_id"] == "user_1"
        assert ctx["source_user_name"] == ""  # default

        clear_memory_context()
        assert _memory_context_var.get() is None

    def test_set_context_with_source_user_name(self):
        """set_memory_context stores source_user_name when provided."""
        set_memory_context("test_role", "test_dept", "user_1", "Michael")
        ctx = _memory_context_var.get()
        assert ctx is not None
        assert ctx["source_user_name"] == "Michael"

        clear_memory_context()
        assert _memory_context_var.get() is None

    def test_clear_without_set(self):
        """clear_memory_context works even when no context was set."""
        clear_memory_context()
        assert _memory_context_var.get() is None


# ===========================================================================
# 2. save_memory tool — validation
# ===========================================================================


class TestSaveMemoryValidation:
    """Verify input validation for the save_memory tool."""

    @pytest.mark.asyncio
    async def test_no_context_returns_error(self):
        """save_memory without context should return an error."""
        clear_memory_context()
        result = await save_memory(
            {
                "title": "Test",
                "content": "Test content",
                "memory_type": "insight",
            }
        )
        assert _is_error(result)
        assert "not available" in _text(result).lower()

    @pytest.mark.asyncio
    async def test_missing_title(self):
        """save_memory with empty title should return an error."""
        set_memory_context("role_1", "dept_1", "user_1")
        try:
            result = await save_memory(
                {
                    "title": "",
                    "content": "Some content",
                    "memory_type": "insight",
                }
            )
            assert _is_error(result)
            assert "required" in _text(result).lower()
        finally:
            clear_memory_context()

    @pytest.mark.asyncio
    async def test_missing_content(self):
        """save_memory with empty content should return an error."""
        set_memory_context("role_1", "dept_1", "user_1")
        try:
            result = await save_memory(
                {
                    "title": "A title",
                    "content": "",
                    "memory_type": "insight",
                }
            )
            assert _is_error(result)
            assert "required" in _text(result).lower()
        finally:
            clear_memory_context()

    @pytest.mark.asyncio
    async def test_invalid_memory_type_defaults_to_insight(self):
        """Invalid memory_type should default to 'insight'."""
        set_memory_context("role_1", "dept_1", "user_1")
        _memory_count_var.set(0)
        try:
            mock_cm, _ = _mock_db_session()
            with (
                patch(
                    "src.db.session.get_db_session",
                    return_value=mock_cm,
                ),
                patch(
                    "src.db.service.save_memory",
                    new_callable=AsyncMock,
                ) as mock_save,
            ):
                result = await save_memory(
                    {
                        "title": "Test",
                        "content": "Content",
                        "memory_type": "invalid_type",
                    }
                )
                assert not _is_error(result)
                # Should have defaulted to insight
                call_kwargs = mock_save.call_args.kwargs
                assert call_kwargs["memory_type"] == "insight"
        finally:
            clear_memory_context()
            _memory_count_var.set(0)


# ===========================================================================
# 3. save_memory tool — successful save
# ===========================================================================


class TestSaveMemorySuccess:
    """Verify save_memory calls DB correctly on success."""

    @pytest.mark.asyncio
    async def test_successful_save(self):
        """save_memory should call db_service.save_memory on success."""
        set_memory_context("media_buyer", "marketing", "user_42")
        _memory_count_var.set(0)

        try:
            mock_cm, _ = _mock_db_session()
            with (
                patch(
                    "src.db.session.get_db_session",
                    return_value=mock_cm,
                ),
                patch(
                    "src.db.service.save_memory",
                    new_callable=AsyncMock,
                ) as mock_save,
            ):
                result = await save_memory(
                    {
                        "title": "Client prefers conservative bids",
                        "content": "The client said they prefer bid changes under 15%",
                        "memory_type": "decision",
                    }
                )

                assert not _is_error(result)
                assert "saved to memory" in _text(result).lower()

                # Verify DB call
                mock_save.assert_called_once()
                call_kwargs = mock_save.call_args.kwargs
                assert call_kwargs["role_id"] == "media_buyer"
                assert call_kwargs["department_id"] == "marketing"
                assert call_kwargs["memory_type"] == "decision"
                assert "conservative bids" in call_kwargs["title"]
                assert "Conversation" in call_kwargs["content"]

        finally:
            clear_memory_context()
            _memory_count_var.set(0)

    @pytest.mark.asyncio
    async def test_successful_save_with_attribution(self):
        """save_memory should include source_user_name in content and evidence."""
        set_memory_context("media_buyer", "marketing", "user_42", "Michael")
        _memory_count_var.set(0)

        try:
            mock_cm, _ = _mock_db_session()
            with (
                patch(
                    "src.db.session.get_db_session",
                    return_value=mock_cm,
                ),
                patch(
                    "src.db.service.save_memory",
                    new_callable=AsyncMock,
                ) as mock_save,
            ):
                result = await save_memory(
                    {
                        "title": "ROAS target is 3.0",
                        "content": "User set ROAS target to 3.0 for all campaigns",
                        "memory_type": "insight",
                    }
                )

                assert not _is_error(result)

                call_kwargs = mock_save.call_args.kwargs
                # Content should include "(from Michael)" attribution
                assert "(from Michael)" in call_kwargs["content"]
                assert "[Conversation]" in call_kwargs["content"]
                # Evidence should include source_user_name
                assert call_kwargs["evidence"]["source_user_name"] == "Michael"

        finally:
            clear_memory_context()
            _memory_count_var.set(0)

    @pytest.mark.asyncio
    async def test_successful_save_without_user_name(self):
        """save_memory with no source_user_name should omit attribution."""
        set_memory_context("media_buyer", "marketing", "user_42")
        _memory_count_var.set(0)

        try:
            mock_cm, _ = _mock_db_session()
            with (
                patch(
                    "src.db.session.get_db_session",
                    return_value=mock_cm,
                ),
                patch(
                    "src.db.service.save_memory",
                    new_callable=AsyncMock,
                ) as mock_save,
            ):
                result = await save_memory(
                    {
                        "title": "Test",
                        "content": "Test content",
                        "memory_type": "insight",
                    }
                )

                assert not _is_error(result)

                call_kwargs = mock_save.call_args.kwargs
                # No "(from ...)" in content
                assert "(from " not in call_kwargs["content"]
                assert "[Conversation]" in call_kwargs["content"]

        finally:
            clear_memory_context()
            _memory_count_var.set(0)

    @pytest.mark.asyncio
    async def test_count_increments(self):
        """Each successful save should increment the counter."""
        set_memory_context("role_1", "dept_1", "user_1")
        _memory_count_var.set(0)

        try:
            mock_cm, _ = _mock_db_session()
            with (
                patch(
                    "src.db.session.get_db_session",
                    return_value=mock_cm,
                ),
                patch(
                    "src.db.service.save_memory",
                    new_callable=AsyncMock,
                ),
            ):
                await save_memory(
                    {
                        "title": "First",
                        "content": "Content 1",
                        "memory_type": "insight",
                    }
                )
                assert _memory_count_var.get() == 1

                await save_memory(
                    {
                        "title": "Second",
                        "content": "Content 2",
                        "memory_type": "lesson",
                    }
                )
                assert _memory_count_var.get() == 2

        finally:
            clear_memory_context()
            _memory_count_var.set(0)


# ===========================================================================
# 4. save_memory tool — count limit
# ===========================================================================


class TestSaveMemoryLimit:
    """Verify max memories per turn is enforced."""

    @pytest.mark.asyncio
    async def test_max_limit_reached(self):
        """save_memory should error when max memories per turn is reached."""
        set_memory_context("role_1", "dept_1", "user_1")
        _memory_count_var.set(_MAX_MEMORIES_PER_TURN)

        try:
            result = await save_memory(
                {
                    "title": "One too many",
                    "content": "This should be rejected",
                    "memory_type": "insight",
                }
            )
            assert _is_error(result)
            assert "maximum" in _text(result).lower()
        finally:
            clear_memory_context()
            _memory_count_var.set(0)


# ===========================================================================
# 5. extract_conversation_memories_llm — successful extraction
# ===========================================================================


class TestExtractConversationMemoriesLLM:
    """Verify LLM-powered conversation memory extraction."""

    @pytest.mark.asyncio
    async def test_extracts_memories_from_valid_response(self):
        """Should parse valid JSON array from LLM and return memory dicts."""
        mock_result = MagicMock()
        mock_result.text = """[
            {
                "type": "insight",
                "title": "Client budget is $50k/month",
                "content": "The user mentioned their monthly budget cap is $50,000.",
                "confidence": 0.8
            },
            {
                "type": "decision",
                "title": "Pause brand campaigns during weekends",
                "content": "User decided to pause brand campaigns on weekends to save budget.",
                "confidence": 0.9
            }
        ]"""

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            entries = await extract_conversation_memories_llm(
                role_id="media_buyer",
                role_name="Performance Media Buyer",
                department_id="marketing",
                user_message="Our monthly budget is $50k. Also, pause brand campaigns on weekends.",
                agent_response=(
                    "Got it! I'll note the $50k budget cap and pause brand campaigns on weekends."
                ),
                user_id="user_1",
            )

            assert len(entries) == 2
            assert entries[0]["memory_type"] == "insight"
            assert entries[0]["role_id"] == "media_buyer"
            assert entries[1]["memory_type"] == "decision"
            assert entries[1]["department_id"] == "marketing"

    @pytest.mark.asyncio
    async def test_empty_array_returns_empty(self):
        """Empty JSON array from LLM should return empty list."""
        mock_result = MagicMock()
        mock_result.text = "[]"

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            entries = await extract_conversation_memories_llm(
                role_id="media_buyer",
                role_name="Performance Media Buyer",
                department_id="marketing",
                user_message="Hello",
                agent_response="Hi! How can I help?",
                user_id="user_1",
            )

            assert entries == []

    @pytest.mark.asyncio
    async def test_low_confidence_filtered(self):
        """Memories with confidence < 0.3 should be filtered out."""
        mock_result = MagicMock()
        mock_result.text = """[
            {
                "type": "insight",
                "title": "Very low confidence observation",
                "content": "Something vague.",
                "confidence": 0.2
            },
            {
                "type": "insight",
                "title": "Moderate confidence observation",
                "content": "Something moderately useful.",
                "confidence": 0.4
            },
            {
                "type": "insight",
                "title": "High confidence observation",
                "content": "Something specific and valuable.",
                "confidence": 0.8
            }
        ]"""

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            entries = await extract_conversation_memories_llm(
                role_id="media_buyer",
                role_name="Performance Media Buyer",
                department_id="marketing",
                user_message="Test",
                agent_response="Test",
                user_id="user_1",
            )

            # 0.2 filtered out, 0.4 and 0.8 kept
            assert len(entries) == 2
            assert entries[0]["title"] == "Moderate confidence observation"
            assert entries[1]["title"] == "High confidence observation"

    @pytest.mark.asyncio
    async def test_max_three_entries(self):
        """Should cap at 3 memories per extraction."""
        mock_result = MagicMock()
        mock_result.text = """[
            {"type": "insight", "title": "A", "content": "Content A", "confidence": 0.9},
            {"type": "insight", "title": "B", "content": "Content B", "confidence": 0.9},
            {"type": "insight", "title": "C", "content": "Content C", "confidence": 0.9},
            {"type": "insight", "title": "D", "content": "Content D", "confidence": 0.9},
            {"type": "insight", "title": "E", "content": "Content E", "confidence": 0.9}
        ]"""

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            entries = await extract_conversation_memories_llm(
                role_id="r",
                role_name="R",
                department_id="d",
                user_message="m",
                agent_response="r",
                user_id="u",
            )

            assert len(entries) == 3

    @pytest.mark.asyncio
    async def test_llm_error_returns_empty(self):
        """LLM API failure should return empty list, not crash."""
        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            side_effect=RuntimeError("API error"),
        ):
            entries = await extract_conversation_memories_llm(
                role_id="r",
                role_name="R",
                department_id="d",
                user_message="m",
                agent_response="r",
                user_id="u",
            )

            assert entries == []

    @pytest.mark.asyncio
    async def test_invalid_json_returns_empty(self):
        """Invalid JSON from LLM should return empty list, not crash."""
        mock_result = MagicMock()
        mock_result.text = "This is not valid JSON"

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            entries = await extract_conversation_memories_llm(
                role_id="r",
                role_name="R",
                department_id="d",
                user_message="m",
                agent_response="r",
                user_id="u",
            )

            assert entries == []

    @pytest.mark.asyncio
    async def test_code_fence_stripped(self):
        """JSON wrapped in markdown code fences should be parsed."""
        mock_result = MagicMock()
        mock_result.text = (
            '```json\n[{"type": "insight", "title": "Test", '
            '"content": "Content", "confidence": 0.8}]\n```'
        )

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            entries = await extract_conversation_memories_llm(
                role_id="r",
                role_name="R",
                department_id="d",
                user_message="m",
                agent_response="r",
                user_id="u",
            )

            assert len(entries) == 1
            assert entries[0]["title"] == "Test"

    @pytest.mark.asyncio
    async def test_thread_history_included(self):
        """Thread history should be included in the LLM prompt."""
        mock_result = MagicMock()
        mock_result.text = "[]"

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_loop:
            await extract_conversation_memories_llm(
                role_id="r",
                role_name="R",
                department_id="d",
                user_message="current message",
                agent_response="current response",
                user_id="u",
                thread_history=[
                    {"text": "earlier message", "is_bot": False},
                    {"text": "earlier response", "is_bot": True},
                ],
            )

            # Verify the prompt includes thread context
            prompt = mock_loop.call_args.kwargs["user_prompt"]
            assert "earlier message" in prompt
            assert "earlier response" in prompt

    @pytest.mark.asyncio
    async def test_memory_fields_populated(self):
        """Extracted memories should have all required fields."""
        mock_result = MagicMock()
        mock_result.text = (
            '[{"type": "lesson", "title": "API error pattern", '
            '"content": "Google Ads API returns 429 after 15 rapid calls", '
            '"confidence": 0.85}]'
        )

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            entries = await extract_conversation_memories_llm(
                role_id="media_buyer",
                role_name="Performance Media Buyer",
                department_id="marketing",
                user_message="test",
                agent_response="test",
                user_id="user_42",
            )

            assert len(entries) == 1
            entry = entries[0]
            assert entry["role_id"] == "media_buyer"
            assert entry["department_id"] == "marketing"
            assert entry["memory_type"] == "lesson"
            assert entry["confidence"] == 0.85
            assert "conversation:" in entry["source_skill_id"]
            assert entry["evidence"]["source"] == "conversation_auto_extract"
            assert entry["evidence"]["user_id"] == "user_42"
            assert "Conversation" in entry["content"]

    @pytest.mark.asyncio
    async def test_extraction_with_source_user_name(self):
        """Extracted memories should include source_user_name attribution."""
        mock_result = MagicMock()
        mock_result.text = (
            '[{"type": "insight", "title": "Redis upgrade", '
            '"content": "Redis module upgrade scheduled for next week", '
            '"confidence": 0.9}]'
        )

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            entries = await extract_conversation_memories_llm(
                role_id="head_of_it",
                role_name="Head of IT",
                department_id="it",
                user_message="We have a Redis upgrade next week",
                agent_response="Noted, I'll plan for the Redis upgrade.",
                user_id="user_42",
                source_user_name="Michael",
            )

            assert len(entries) == 1
            entry = entries[0]
            # Content should include "(from Michael)" attribution
            assert "(from Michael)" in entry["content"]
            assert "[Conversation]" in entry["content"]
            # Evidence should include source_user_name
            assert entry["evidence"]["source_user_name"] == "Michael"

    @pytest.mark.asyncio
    async def test_extraction_without_source_user_name(self):
        """Extracted memories without source_user_name should omit attribution."""
        mock_result = MagicMock()
        mock_result.text = (
            '[{"type": "insight", "title": "Test", "content": "Content", "confidence": 0.8}]'
        )

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            entries = await extract_conversation_memories_llm(
                role_id="r",
                role_name="R",
                department_id="d",
                user_message="m",
                agent_response="r",
                user_id="u",
            )

            assert len(entries) == 1
            # No "(from ...)" in content
            assert "(from " not in entries[0]["content"]
            assert entries[0]["evidence"]["source_user_name"] == ""

    @pytest.mark.asyncio
    async def test_invalid_type_defaults_to_insight(self):
        """Invalid memory type from LLM should default to insight."""
        mock_result = MagicMock()
        mock_result.text = (
            '[{"type": "random_type", "title": "Test", "content": "Content", "confidence": 0.8}]'
        )

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            entries = await extract_conversation_memories_llm(
                role_id="r",
                role_name="R",
                department_id="d",
                user_message="m",
                agent_response="r",
                user_id="u",
            )

            assert len(entries) == 1
            assert entries[0]["memory_type"] == "insight"


# ===========================================================================
# 6. Relationship memory type
# ===========================================================================


class TestRelationshipMemoryType:
    """Tests for the 'relationship' memory type in the extraction system."""

    def test_extraction_prompt_includes_relationship_type(self):
        """Verify _CONVERSATION_MEMORY_PROMPT mentions 'relationship' as a type."""
        assert "relationship" in _CONVERSATION_MEMORY_PROMPT
        # The prompt should list "relationship" as one of the allowed type values
        assert '"relationship"' in _CONVERSATION_MEMORY_PROMPT

    @pytest.mark.asyncio
    async def test_relationship_type_accepted_in_extraction(self):
        """A relationship observation from the LLM should be kept as-is, not
        normalized to 'insight'."""
        mock_result = MagicMock()
        mock_result.text = (
            '[{"type": "relationship", '
            '"title": "Michael prefers direct answers", '
            '"content": "Michael has a high-energy informal style and '
            'prefers concise answers without filler.", '
            '"confidence": 0.85}]'
        )

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            entries = await extract_conversation_memories_llm(
                role_id="media_buyer",
                role_name="Performance Media Buyer",
                department_id="marketing",
                user_message="Just give me the numbers, skip the preamble.",
                agent_response="CPC: $1.23, CTR: 3.4%, ROAS: 4.1x.",
                user_id="user_42",
                source_user_name="Michael",
            )

            assert len(entries) == 1
            entry = entries[0]
            # Crucially: type should remain "relationship", NOT normalized
            assert entry["memory_type"] == "relationship"
            assert entry["title"] == "Michael prefers direct answers"
            assert entry["role_id"] == "media_buyer"
            assert entry["department_id"] == "marketing"

    @pytest.mark.asyncio
    async def test_invalid_type_normalized_to_insight(self):
        """Types not in the allowed list should be normalized to 'insight'."""
        mock_result = MagicMock()
        mock_result.text = (
            '[{"type": "preference", '
            '"title": "Likes morning reports", '
            '"content": "User prefers receiving reports before 9 AM.", '
            '"confidence": 0.7}]'
        )

        with patch(
            "src.agent.api_client.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            entries = await extract_conversation_memories_llm(
                role_id="r",
                role_name="R",
                department_id="d",
                user_message="m",
                agent_response="r",
                user_id="u",
            )

            assert len(entries) == 1
            # "preference" is not in the allowed set, should be normalized
            assert entries[0]["memory_type"] == "insight"
