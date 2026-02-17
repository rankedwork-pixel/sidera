"""Tests for meeting session DB service methods."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.db import service as db_service
from src.models.schema import MeetingSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_meeting(**overrides) -> MeetingSession:
    """Create a mock MeetingSession with sensible defaults."""
    defaults = {
        "id": 1,
        "meeting_url": "https://meet.google.com/abc-defg-hij",
        "role_id": "head_of_marketing",
        "user_id": "U123",
        "bot_id": "bot-uuid-123",
        "status": "joining",
        "started_at": datetime(2026, 2, 15, 10, 0, 0),
        "joined_at": None,
        "ended_at": None,
        "transcript_json": [],
        "transcript_summary": "",
        "action_items_json": [],
        "delegation_result_id": None,
        "delegation_status": None,
        "total_cost_usd": Decimal("0.0000"),
        "agent_turns": 0,
        "duration_seconds": 0,
        "participants_json": [],
        "slack_notification_ts": None,
        "channel_id": "C123",
        "created_at": datetime(2026, 2, 15, 10, 0, 0),
        "updated_at": datetime(2026, 2, 15, 10, 0, 0),
    }
    defaults.update(overrides)
    meeting = MeetingSession(**defaults)
    return meeting


# ---------------------------------------------------------------------------
# create_meeting_session
# ---------------------------------------------------------------------------


class TestCreateMeetingSession:
    """Tests for create_meeting_session."""

    @pytest.mark.asyncio
    async def test_create_basic(self):
        session = AsyncMock()
        session.flush = AsyncMock()

        result = await db_service.create_meeting_session(
            session,
            meeting_url="https://meet.google.com/abc-defg-hij",
            role_id="head_of_marketing",
            user_id="U123",
            bot_id="bot-uuid-123",
            channel_id="C123",
        )

        session.add.assert_called_once()
        session.flush.assert_called_once()
        assert isinstance(result, MeetingSession)
        assert result.meeting_url == "https://meet.google.com/abc-defg-hij"
        assert result.role_id == "head_of_marketing"
        assert result.status == "joining"

    @pytest.mark.asyncio
    async def test_create_minimal(self):
        session = AsyncMock()
        session.flush = AsyncMock()

        result = await db_service.create_meeting_session(
            session,
            meeting_url="https://meet.google.com/xyz",
            role_id="head_of_it",
            user_id="U456",
        )

        assert result.bot_id == ""
        assert result.channel_id == ""


# ---------------------------------------------------------------------------
# get_meeting_session
# ---------------------------------------------------------------------------


class TestGetMeetingSession:
    """Tests for get_meeting_session."""

    @pytest.mark.asyncio
    async def test_found(self):
        meeting = _mock_meeting()
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = meeting
        session.execute = AsyncMock(return_value=mock_result)

        result = await db_service.get_meeting_session(session, 1)
        assert result is meeting

    @pytest.mark.asyncio
    async def test_not_found(self):
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        result = await db_service.get_meeting_session(session, 999)
        assert result is None


# ---------------------------------------------------------------------------
# get_meeting_session_by_bot_id
# ---------------------------------------------------------------------------


class TestGetMeetingByBotId:
    """Tests for get_meeting_session_by_bot_id."""

    @pytest.mark.asyncio
    async def test_found(self):
        meeting = _mock_meeting(bot_id="bot-xyz")
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = meeting
        session.execute = AsyncMock(return_value=mock_result)

        result = await db_service.get_meeting_session_by_bot_id(session, "bot-xyz")
        assert result is meeting
        assert result.bot_id == "bot-xyz"


# ---------------------------------------------------------------------------
# update_meeting_status
# ---------------------------------------------------------------------------


class TestUpdateMeetingStatus:
    """Tests for update_meeting_status."""

    @pytest.mark.asyncio
    async def test_update_status(self):
        session = AsyncMock()
        session.flush = AsyncMock()

        await db_service.update_meeting_status(
            session,
            meeting_id=1,
            status="in_call",
        )

        session.execute.assert_called_once()
        session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_with_kwargs(self):
        session = AsyncMock()
        session.flush = AsyncMock()

        await db_service.update_meeting_status(
            session,
            meeting_id=1,
            status="ended",
            ended_at=datetime(2026, 2, 15, 11, 0, 0),
            total_cost_usd=Decimal("3.50"),
        )

        session.execute.assert_called_once()


# ---------------------------------------------------------------------------
# update_meeting_transcript
# ---------------------------------------------------------------------------


class TestUpdateMeetingTranscript:
    """Tests for update_meeting_transcript."""

    @pytest.mark.asyncio
    async def test_update_transcript(self):
        session = AsyncMock()
        session.flush = AsyncMock()

        transcript = [
            {"speaker": "Alice", "text": "Hello", "timestamp": 1.0},
            {"speaker": "Bob", "text": "Hi", "timestamp": 2.0},
        ]

        await db_service.update_meeting_transcript(
            session,
            meeting_id=1,
            transcript_json=transcript,
            transcript_summary="Alice and Bob discussed budgets.",
        )

        session.execute.assert_called_once()
        session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_transcript_no_summary(self):
        session = AsyncMock()
        session.flush = AsyncMock()

        await db_service.update_meeting_transcript(
            session,
            meeting_id=1,
            transcript_json=[],
        )

        session.execute.assert_called_once()


# ---------------------------------------------------------------------------
# get_active_meetings
# ---------------------------------------------------------------------------


class TestGetActiveMeetings:
    """Tests for get_active_meetings."""

    @pytest.mark.asyncio
    async def test_returns_active(self):
        m1 = _mock_meeting(id=1, status="joining")
        m2 = _mock_meeting(id=2, status="in_call")

        session = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [m1, m2]
        mock_result.scalars.return_value = mock_scalars
        session.execute = AsyncMock(return_value=mock_result)

        result = await db_service.get_active_meetings(session)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_returns_empty(self):
        session = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        session.execute = AsyncMock(return_value=mock_result)

        result = await db_service.get_active_meetings(session)
        assert result == []
