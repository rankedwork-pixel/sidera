"""Tests for the MeetingSessionManager (listen-only)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.meetings.session import (
    MeetingContext,
    MeetingSessionManager,
    get_meeting_manager,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def manager():
    """Create a MeetingSessionManager with mocked connectors."""
    mgr = MeetingSessionManager.__new__(MeetingSessionManager)
    mgr._recall = MagicMock()
    mgr._active_sessions = {}
    mgr._log = MagicMock()
    return mgr


@pytest.fixture
def ctx():
    """Create a minimal MeetingContext for testing."""
    return MeetingContext(
        meeting_id=1,
        bot_id="bot-123",
        role_id="head_of_marketing",
        role_name="Head of Marketing",
        user_id="U123",
        channel_id="C456",
        meeting_url="https://meet.google.com/abc-defg-hij",
    )


# ---------------------------------------------------------------------------
# MeetingContext
# ---------------------------------------------------------------------------


class TestMeetingContext:
    """Tests for the MeetingContext dataclass."""

    def test_defaults(self, ctx):
        assert ctx.transcript_buffer == []
        assert ctx.participants == []
        assert ctx.agent_turns == 0
        assert ctx.total_cost_usd == 0.0
        assert ctx.is_active is True
        assert ctx.processor_task is None

    def test_mutable_state(self, ctx):
        ctx.agent_turns = 5
        ctx.total_cost_usd = 2.50
        ctx.is_active = False
        assert ctx.agent_turns == 5
        assert ctx.total_cost_usd == 2.50
        assert ctx.is_active is False


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


class TestSessionManagement:
    """Tests for get/list active sessions."""

    def test_get_active_session(self, manager, ctx):
        manager._active_sessions["bot-123"] = ctx
        assert manager.get_active_session("bot-123") is ctx

    def test_get_active_session_not_found(self, manager):
        assert manager.get_active_session("nonexistent") is None

    def test_get_all_active_sessions(self, manager, ctx):
        ctx2 = MeetingContext(
            meeting_id=2,
            bot_id="bot-456",
            role_id="head_of_it",
            role_name="Head of IT",
            user_id="U789",
            channel_id="C789",
            meeting_url="https://meet.google.com/xyz",
        )
        manager._active_sessions["bot-123"] = ctx
        manager._active_sessions["bot-456"] = ctx2
        assert len(manager.get_all_active_sessions()) == 2

    def test_get_all_empty(self, manager):
        assert manager.get_all_active_sessions() == {}


# ---------------------------------------------------------------------------
# _get_recent_transcript_text
# ---------------------------------------------------------------------------


class TestGetRecentTranscriptText:
    """Tests for transcript text extraction."""

    def test_empty_buffer(self, manager, ctx):
        result = manager._get_recent_transcript_text(ctx)
        assert result == ""

    def test_text_format(self, manager, ctx):
        ctx.transcript_buffer = [
            {"speaker": "Alice", "text": "Hello everyone"},
            {"speaker": "Bob", "text": "Hi Alice"},
        ]
        result = manager._get_recent_transcript_text(ctx)
        assert "Alice: Hello everyone" in result
        assert "Bob: Hi Alice" in result

    def test_words_format(self, manager, ctx):
        ctx.transcript_buffer = [
            {
                "speaker": "Alice",
                "words": [
                    {"word": "what", "text": "what"},
                    {"word": "about", "text": "about"},
                    {"word": "budgets", "text": "budgets"},
                ],
            },
        ]
        result = manager._get_recent_transcript_text(ctx)
        assert "Alice: what about budgets" in result

    def test_skips_status_markers(self, manager, ctx):
        ctx.transcript_buffer = [
            {"_status_updated": True},
            {"speaker": "Alice", "text": "Hello"},
        ]
        result = manager._get_recent_transcript_text(ctx)
        assert "Alice: Hello" in result
        assert "_status_updated" not in result

    def test_truncates_at_limit(self, manager, ctx):
        """Should not exceed _MAX_TRANSCRIPT_CONTEXT chars."""
        # Create a very long transcript
        ctx.transcript_buffer = [{"speaker": f"Speaker{i}", "text": "x" * 500} for i in range(20)]
        result = manager._get_recent_transcript_text(ctx)
        # Should have been truncated (not all 20 entries)
        assert len(result) < 500 * 20


# ---------------------------------------------------------------------------
# leave
# ---------------------------------------------------------------------------


class TestLeave:
    """Tests for the leave method."""

    @pytest.mark.asyncio
    async def test_leave_no_session(self, manager):
        result = await manager.leave("nonexistent")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_leave_cleans_up(self, manager, ctx):
        manager._active_sessions["bot-123"] = ctx
        ctx.processor_task = None

        # Mock DB and Slack
        with patch(
            "src.meetings.session.MeetingSessionManager._notify_slack",
            new_callable=AsyncMock,
        ):
            with patch(
                "src.meetings.session.MeetingSessionManager._emit_meeting_ended",
                new_callable=AsyncMock,
            ):
                result = await manager.leave("bot-123")

        assert result["meeting_id"] == 1
        assert result["role_id"] == "head_of_marketing"
        assert "bot-123" not in manager._active_sessions
        assert ctx.is_active is False
        manager._recall.remove_bot.assert_called_once_with("bot-123")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    """Tests for the singleton getter."""

    def test_get_meeting_manager_returns_instance(self):
        with patch("src.meetings.session.MeetingSessionManager.__init__", return_value=None):
            mgr = get_meeting_manager()
            assert mgr is not None

    def test_get_meeting_manager_same_instance(self):
        with patch("src.meetings.session._meeting_manager", None):
            with patch("src.meetings.session.MeetingSessionManager.__init__", return_value=None):
                mgr1 = get_meeting_manager()
                mgr2 = get_meeting_manager()
                assert mgr1 is mgr2


# ---------------------------------------------------------------------------
# Transcript webhook processing
# ---------------------------------------------------------------------------


class TestTranscriptWebhook:
    """Tests for the receive_transcript_event method."""

    def test_final_transcript_buffered(self, manager, ctx):
        """Final transcripts should be buffered."""
        manager._active_sessions[ctx.bot_id] = ctx
        ctx.is_active = True

        event = {
            "data": {
                "speaker": "Alice",
                "words": [{"text": "hey"}, {"text": "there"}],
                "is_final": True,
            },
        }
        manager.receive_transcript_event(ctx.bot_id, event)

        assert len(ctx.transcript_buffer) == 1
        assert ctx.transcript_buffer[0]["speaker"] == "Alice"

    def test_partial_transcript_buffered(self, manager, ctx):
        """Partial transcripts should also be buffered."""
        manager._active_sessions[ctx.bot_id] = ctx
        ctx.is_active = True

        event = {
            "data": {
                "speaker": "Alice",
                "words": [{"text": "hello"}],
                "is_final": False,
            },
        }
        manager.receive_transcript_event(ctx.bot_id, event)

        assert len(ctx.transcript_buffer) == 1
        assert ctx.transcript_buffer[0]["speaker"] == "Alice"
