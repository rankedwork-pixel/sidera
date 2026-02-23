"""Tests for extended thinking support in the agent loop.

Verifies that:
- thinking_budget parameter is wired into API kwargs correctly
- Thinking blocks are NOT included in output text
- Non-thinking-capable models skip thinking config
- max_tokens is adjusted when thinking is active
- Interleaved thinking header is added when tools are present
- Config settings control thinking behavior
- Thinking blocks are preserved in tool use messages
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.api_client import (
    _THINKING_CAPABLE_MODELS,
    _model_supports_thinking,
    run_agent_loop,
)

# ---------------------------------------------------------------------------
# Model capability check
# ---------------------------------------------------------------------------


class TestModelSupportsThinking:
    """Tests for _model_supports_thinking()."""

    def test_sonnet_4_supported(self):
        assert _model_supports_thinking("claude-sonnet-4-20250514") is True

    def test_sonnet_4_0_supported(self):
        assert _model_supports_thinking("claude-sonnet-4-0") is True

    def test_opus_4_supported(self):
        assert _model_supports_thinking("claude-opus-4-20250514") is True

    def test_opus_4_0_supported(self):
        assert _model_supports_thinking("claude-opus-4-0") is True

    def test_haiku_45_supported(self):
        assert _model_supports_thinking("claude-haiku-4-5-20251001") is True

    def test_old_haiku_not_supported(self):
        assert _model_supports_thinking("claude-3-haiku-20240307") is False

    def test_unknown_model_not_supported(self):
        assert _model_supports_thinking("some-random-model") is False

    def test_capable_models_frozenset(self):
        """Verify the constant is a frozenset with expected entries."""
        assert isinstance(_THINKING_CAPABLE_MODELS, frozenset)
        assert len(_THINKING_CAPABLE_MODELS) >= 5


# ---------------------------------------------------------------------------
# API kwargs construction
# ---------------------------------------------------------------------------


def _make_mock_response(
    content: list[Any] | None = None,
    input_tokens: int = 100,
    output_tokens: int = 200,
    stop_reason: str = "end_turn",
) -> MagicMock:
    """Build a mock Anthropic API response."""
    if content is None:
        text_block = SimpleNamespace(type="text", text="Hello world")
        content = [text_block]

    response = MagicMock()
    response.content = content
    response.usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    response.stop_reason = stop_reason
    return response


class TestThinkingApiKwargs:
    """Tests for thinking parameter injection into API calls."""

    @pytest.mark.asyncio
    async def test_thinking_budget_added_for_capable_model(self):
        """Thinking config should be in API kwargs for Sonnet 4."""
        mock_response = _make_mock_response()

        with (
            patch("src.agent.api_client.anthropic") as mock_anthropic,
            patch("src.agent.api_client.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "test-key"
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            await run_agent_loop(
                system_prompt="test",
                user_prompt="test",
                model="claude-sonnet-4-20250514",
                tools=None,
                max_turns=1,
                thinking_budget=10000,
            )

            call_kwargs = mock_client.messages.create.call_args
            # Check thinking is in the call kwargs
            assert "thinking" in call_kwargs.kwargs or any(
                "thinking" in str(kw) for kw in [call_kwargs]
            )

    @pytest.mark.asyncio
    async def test_thinking_not_added_for_old_haiku(self):
        """Old Haiku (3) should NOT get thinking config."""
        mock_response = _make_mock_response()

        with (
            patch("src.agent.api_client.anthropic") as mock_anthropic,
            patch("src.agent.api_client.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "test-key"
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            await run_agent_loop(
                system_prompt="test",
                user_prompt="test",
                model="claude-3-haiku-20240307",
                tools=None,
                max_turns=1,
                thinking_budget=10000,
            )

            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert "thinking" not in call_kwargs

    @pytest.mark.asyncio
    async def test_thinking_not_added_when_budget_none(self):
        """No thinking config when budget is None."""
        mock_response = _make_mock_response()

        with (
            patch("src.agent.api_client.anthropic") as mock_anthropic,
            patch("src.agent.api_client.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "test-key"
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            await run_agent_loop(
                system_prompt="test",
                user_prompt="test",
                model="claude-sonnet-4-20250514",
                tools=None,
                max_turns=1,
                thinking_budget=None,
            )

            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert "thinking" not in call_kwargs

    @pytest.mark.asyncio
    async def test_max_tokens_adjusted_with_thinking(self):
        """max_tokens should be at least thinking_budget + 4096."""
        mock_response = _make_mock_response()

        with (
            patch("src.agent.api_client.anthropic") as mock_anthropic,
            patch("src.agent.api_client.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "test-key"
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            await run_agent_loop(
                system_prompt="test",
                user_prompt="test",
                model="claude-sonnet-4-20250514",
                tools=None,
                max_turns=1,
                thinking_budget=10000,
            )

            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert call_kwargs["max_tokens"] >= 10000 + 4096

    @pytest.mark.asyncio
    async def test_interleaved_header_with_tools(self):
        """Beta header for interleaved thinking should be set when tools present."""
        mock_response = _make_mock_response()

        tool_defs = [
            {
                "name": "test_tool",
                "description": "A test tool",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]

        with (
            patch("src.agent.api_client.anthropic") as mock_anthropic,
            patch("src.agent.api_client.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "test-key"
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            await run_agent_loop(
                system_prompt="test",
                user_prompt="test",
                model="claude-sonnet-4-20250514",
                tools=tool_defs,
                max_turns=1,
                thinking_budget=10000,
            )

            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert "extra_headers" in call_kwargs
            assert call_kwargs["extra_headers"]["anthropic-beta"] == (
                "interleaved-thinking-2025-05-14"
            )

    @pytest.mark.asyncio
    async def test_no_interleaved_header_without_tools(self):
        """No beta header when thinking is on but no tools."""
        mock_response = _make_mock_response()

        with (
            patch("src.agent.api_client.anthropic") as mock_anthropic,
            patch("src.agent.api_client.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "test-key"
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            await run_agent_loop(
                system_prompt="test",
                user_prompt="test",
                model="claude-sonnet-4-20250514",
                tools=None,
                max_turns=1,
                thinking_budget=10000,
            )

            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert "extra_headers" not in call_kwargs


# ---------------------------------------------------------------------------
# Response block handling
# ---------------------------------------------------------------------------


class TestThinkingBlockHandling:
    """Tests for thinking block processing in responses."""

    @pytest.mark.asyncio
    async def test_thinking_blocks_not_in_output(self):
        """Thinking blocks should NOT appear in the output text."""
        thinking_block = SimpleNamespace(
            type="thinking",
            thinking="Let me reason about this...",
            signature="abc123",
        )
        text_block = SimpleNamespace(type="text", text="The answer is 42.")
        mock_response = _make_mock_response(content=[thinking_block, text_block])

        with (
            patch("src.agent.api_client.anthropic") as mock_anthropic,
            patch("src.agent.api_client.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "test-key"
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            result = await run_agent_loop(
                system_prompt="test",
                user_prompt="test",
                model="claude-sonnet-4-20250514",
                tools=None,
                max_turns=1,
                thinking_budget=10000,
            )

            assert "The answer is 42." in result.text
            assert "Let me reason about this" not in result.text

    @pytest.mark.asyncio
    async def test_redacted_thinking_blocks_not_in_output(self):
        """Redacted thinking blocks should NOT appear in the output text."""
        redacted_block = SimpleNamespace(
            type="redacted_thinking",
            data="encrypted_content_here",
        )
        text_block = SimpleNamespace(type="text", text="Here is my response.")
        mock_response = _make_mock_response(content=[redacted_block, text_block])

        with (
            patch("src.agent.api_client.anthropic") as mock_anthropic,
            patch("src.agent.api_client.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "test-key"
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            result = await run_agent_loop(
                system_prompt="test",
                user_prompt="test",
                model="claude-sonnet-4-20250514",
                tools=None,
                max_turns=1,
                thinking_budget=10000,
            )

            assert "Here is my response." in result.text
            assert "encrypted_content_here" not in result.text

    @pytest.mark.asyncio
    async def test_thinking_blocks_preserved_in_tool_messages(self):
        """Thinking blocks must be passed back in assistant messages for tool use."""
        thinking_block = SimpleNamespace(
            type="thinking",
            thinking="I should call this tool...",
            signature="sig123",
        )
        tool_use_block = SimpleNamespace(
            type="tool_use",
            id="tool_1",
            name="test_tool",
            input={"query": "test"},
        )
        # First response: thinking + tool_use
        response1 = _make_mock_response(
            content=[thinking_block, tool_use_block],
            stop_reason="tool_use",
        )
        # Second response: final text
        text_block = SimpleNamespace(type="text", text="Done.")
        response2 = _make_mock_response(content=[text_block])

        with (
            patch("src.agent.api_client.anthropic") as mock_anthropic,
            patch("src.agent.api_client.settings") as mock_settings,
            patch("src.agent.api_client.get_global_registry") as mock_registry,
        ):
            mock_settings.anthropic_api_key = "test-key"
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(
                side_effect=[response1, response2],
            )
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            # Mock tool dispatch
            mock_reg = MagicMock()
            mock_reg.dispatch = AsyncMock(return_value="tool result")
            mock_registry.return_value = mock_reg

            await run_agent_loop(
                system_prompt="test",
                user_prompt="test",
                model="claude-sonnet-4-20250514",
                tools=[{"name": "test_tool", "description": "test", "input_schema": {}}],
                max_turns=3,
                thinking_budget=10000,
            )

            # The second API call should have the assistant message with
            # thinking blocks preserved (response.content passed as-is)
            second_call = mock_client.messages.create.call_args_list[1]
            messages = second_call.kwargs["messages"]
            # Find the assistant message
            assistant_msgs = [m for m in messages if m["role"] == "assistant"]
            assert len(assistant_msgs) == 1
            # The content should be the raw response.content (includes thinking)
            assert assistant_msgs[0]["content"] == response1.content


# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------


class TestThinkingCostTracking:
    """Tests for thinking-related fields in cost dict."""

    @pytest.mark.asyncio
    async def test_thinking_enabled_in_cost_dict(self):
        """Cost dict should include thinking_enabled=True when active."""
        mock_response = _make_mock_response()

        with (
            patch("src.agent.api_client.anthropic") as mock_anthropic,
            patch("src.agent.api_client.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "test-key"
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            result = await run_agent_loop(
                system_prompt="test",
                user_prompt="test",
                model="claude-sonnet-4-20250514",
                tools=None,
                max_turns=1,
                thinking_budget=10000,
            )

            assert result.cost["thinking_enabled"] is True

    @pytest.mark.asyncio
    async def test_thinking_disabled_in_cost_dict(self):
        """Cost dict should include thinking_enabled=False when not active."""
        mock_response = _make_mock_response()

        with (
            patch("src.agent.api_client.anthropic") as mock_anthropic,
            patch("src.agent.api_client.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "test-key"
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            result = await run_agent_loop(
                system_prompt="test",
                user_prompt="test",
                model="claude-sonnet-4-20250514",
                tools=None,
                max_turns=1,
                thinking_budget=None,
            )

            assert result.cost["thinking_enabled"] is False

    @pytest.mark.asyncio
    async def test_thinking_disabled_for_incapable_model(self):
        """Cost dict should show thinking_enabled=False for old Haiku even with budget."""
        mock_response = _make_mock_response()

        with (
            patch("src.agent.api_client.anthropic") as mock_anthropic,
            patch("src.agent.api_client.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "test-key"
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            result = await run_agent_loop(
                system_prompt="test",
                user_prompt="test",
                model="claude-3-haiku-20240307",
                tools=None,
                max_turns=1,
                thinking_budget=10000,
            )

            assert result.cost["thinking_enabled"] is False


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestExtendedThinkingConfig:
    """Tests for extended thinking config settings."""

    def test_extended_thinking_enabled_default(self):
        """extended_thinking_enabled should default to True."""
        from src.config import Settings

        s = Settings(anthropic_api_key="test")
        assert s.extended_thinking_enabled is True

    def test_extended_thinking_budget_default(self):
        """extended_thinking_budget_tokens should default to 10000."""
        from src.config import Settings

        s = Settings(anthropic_api_key="test")
        assert s.extended_thinking_budget_tokens == 10000

    def test_thinking_budget_helper_enabled(self):
        """_thinking_budget() returns budget when enabled."""
        from src.agent.core import _thinking_budget

        with patch("src.agent.core.settings") as mock_settings:
            mock_settings.extended_thinking_enabled = True
            mock_settings.extended_thinking_budget_tokens = 15000
            assert _thinking_budget() == 15000

    def test_thinking_budget_helper_disabled(self):
        """_thinking_budget() returns None when disabled."""
        from src.agent.core import _thinking_budget

        with patch("src.agent.core.settings") as mock_settings:
            mock_settings.extended_thinking_enabled = False
            mock_settings.extended_thinking_budget_tokens = 15000
            assert _thinking_budget() is None
