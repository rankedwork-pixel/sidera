"""Tests for /sidera memory command in Slack routes."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.slack import handle_sidera_command

# =====================================================================
# Helpers
# =====================================================================


def _make_slash_body(
    text: str,
    user_id: str = "U123",
    channel_id: str = "C456",
) -> dict:
    return {"text": text, "user_id": user_id, "channel_id": channel_id}


# =====================================================================
# Tests
# =====================================================================


class TestSideraMemoryCommand:
    @pytest.mark.asyncio
    async def test_memory_no_role_id(self):
        """Empty role_id shows usage hint."""
        ack = AsyncMock()
        client = AsyncMock()
        body = _make_slash_body("memory")

        await handle_sidera_command(ack, body, client)

        ack.assert_awaited_once()
        msg = client.chat_postMessage.call_args.kwargs["text"]
        assert "Usage" in msg

    @pytest.mark.asyncio
    async def test_memory_with_results(self):
        """Shows formatted memories when found."""
        ack = AsyncMock()
        client = AsyncMock()
        body = _make_slash_body("memory performance_media_buyer")

        fake_mem = MagicMock()
        fake_mem.memory_type = "decision"
        fake_mem.title = "Budget approved +20%"
        fake_mem.confidence = 0.95
        fake_mem.created_at = datetime(2026, 2, 10, tzinfo=timezone.utc)

        mock_session = AsyncMock()
        with (
            patch(
                "src.db.service.get_role_memories",
                AsyncMock(return_value=[fake_mem]),
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=mock_session,
            ),
        ):
            await handle_sidera_command(ack, body, client)

        msg = client.chat_postMessage.call_args.kwargs["text"]
        assert "performance_media_buyer" in msg
        assert "decision" in msg
        assert "Budget approved" in msg
        assert "95%" in msg

    @pytest.mark.asyncio
    async def test_memory_no_results(self):
        """Shows 'no memories' message when empty."""
        ack = AsyncMock()
        client = AsyncMock()
        body = _make_slash_body("memory some_role")

        mock_session = AsyncMock()
        with (
            patch(
                "src.db.service.get_role_memories",
                AsyncMock(return_value=[]),
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=mock_session,
            ),
        ):
            await handle_sidera_command(ack, body, client)

        msg = client.chat_postMessage.call_args.kwargs["text"]
        assert "No memories" in msg

    @pytest.mark.asyncio
    async def test_memory_error_handling(self):
        """Shows error message on DB failure."""
        ack = AsyncMock()
        client = AsyncMock()
        body = _make_slash_body("memory buyer")

        with patch(
            "src.db.session.get_db_session",
            side_effect=Exception("DB connection failed"),
        ):
            await handle_sidera_command(ack, body, client)

        msg = client.chat_postMessage.call_args.kwargs["text"]
        assert "Error" in msg

    @pytest.mark.asyncio
    async def test_help_includes_memory_command(self):
        """Empty command help text mentions memory."""
        ack = AsyncMock()
        client = AsyncMock()
        body = _make_slash_body("")

        await handle_sidera_command(ack, body, client)

        msg = client.chat_postMessage.call_args.kwargs["text"]
        assert "memory" in msg
