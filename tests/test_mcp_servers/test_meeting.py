"""Tests for meeting MCP tools (listen-only)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_servers.meeting import (
    end_meeting_participation,
    get_meeting_participants,
    get_meeting_transcript,
)
from src.meetings.session import MeetingContext

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ctx():
    """Create a mock MeetingContext."""
    return MeetingContext(
        meeting_id=1,
        bot_id="bot-123",
        role_id="head_of_marketing",
        role_name="Head of Marketing",
        user_id="U123",
        channel_id="C456",
        meeting_url="https://meet.google.com/abc",
        transcript_buffer=[
            {"speaker": "Alice", "text": "Let's discuss the budget"},
            {"speaker": "Bob", "text": "I agree, ROAS is down"},
        ],
        participants=[
            {"name": "Alice"},
            {"name": "Bob"},
            {"name": "Head of Marketing"},
        ],
    )


@pytest.fixture
def mock_manager(mock_ctx):
    """Create a mock MeetingSessionManager."""
    mgr = MagicMock()
    mgr.get_active_session.return_value = mock_ctx
    mgr.leave = AsyncMock(
        return_value={
            "meeting_id": 1,
            "transcript_entries": 10,
            "total_cost_usd": 0.50,
        }
    )
    return mgr


# ---------------------------------------------------------------------------
# get_meeting_transcript
# ---------------------------------------------------------------------------


class TestGetMeetingTranscript:
    """Tests for the get_meeting_transcript tool."""

    @pytest.mark.asyncio
    async def test_success(self, mock_manager, mock_ctx):
        with patch("src.meetings.session.get_meeting_manager", return_value=mock_manager):
            result = await get_meeting_transcript({"bot_id": "bot-123"})

        assert "Alice: Let's discuss the budget" in str(result)
        assert "Bob: I agree, ROAS is down" in str(result)

    @pytest.mark.asyncio
    async def test_no_bot_id(self):
        result = await get_meeting_transcript({})
        assert "error" in str(result).lower() or "required" in str(result).lower()

    @pytest.mark.asyncio
    async def test_no_session(self):
        mgr = MagicMock()
        mgr.get_active_session.return_value = None
        with patch("src.meetings.session.get_meeting_manager", return_value=mgr):
            result = await get_meeting_transcript({"bot_id": "nonexistent"})
        assert "no active" in str(result).lower()

    @pytest.mark.asyncio
    async def test_empty_transcript(self, mock_manager, mock_ctx):
        mock_ctx.transcript_buffer = []
        with patch("src.meetings.session.get_meeting_manager", return_value=mock_manager):
            result = await get_meeting_transcript({"bot_id": "bot-123"})
        assert "no transcript" in str(result).lower()


# ---------------------------------------------------------------------------
# get_meeting_participants
# ---------------------------------------------------------------------------


class TestGetMeetingParticipants:
    """Tests for the get_meeting_participants tool."""

    @pytest.mark.asyncio
    async def test_success(self, mock_manager):
        with patch("src.meetings.session.get_meeting_manager", return_value=mock_manager):
            result = await get_meeting_participants({"bot_id": "bot-123"})

        assert "Alice" in str(result)
        assert "Bob" in str(result)
        assert "3" in str(result)

    @pytest.mark.asyncio
    async def test_no_participants(self, mock_manager, mock_ctx):
        mock_ctx.participants = []
        with patch("src.meetings.session.get_meeting_manager", return_value=mock_manager):
            result = await get_meeting_participants({"bot_id": "bot-123"})
        assert "no participant" in str(result).lower()


# ---------------------------------------------------------------------------
# end_meeting_participation
# ---------------------------------------------------------------------------


class TestEndMeetingParticipation:
    """Tests for the end_meeting_participation tool."""

    @pytest.mark.asyncio
    async def test_success(self, mock_manager):
        with patch("src.meetings.session.get_meeting_manager", return_value=mock_manager):
            result = await end_meeting_participation({"bot_id": "bot-123"})

        assert "left" in str(result).lower()
        mock_manager.leave.assert_called_once_with("bot-123")

    @pytest.mark.asyncio
    async def test_no_bot_id(self):
        result = await end_meeting_participation({})
        assert "required" in str(result).lower()

    @pytest.mark.asyncio
    async def test_no_session(self):
        mgr = MagicMock()
        mgr.get_active_session.return_value = None
        with patch("src.meetings.session.get_meeting_manager", return_value=mgr):
            result = await end_meeting_participation({"bot_id": "nonexistent"})
        assert "no active" in str(result).lower()
