"""Tests for conversational meeting join — detecting meeting URLs in conversations.

Users can naturally ask a role to join a meeting by including a meeting URL
in a conversation message (either a new @mention or an existing thread reply).
The system detects the URL and dispatches ``sidera/meeting.join`` alongside
the normal ``sidera/conversation.turn`` event.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.slack import _detect_meeting_url

# ---------------------------------------------------------------------------
# _detect_meeting_url — unit tests
# ---------------------------------------------------------------------------


class TestDetectMeetingUrl:
    """Unit tests for the meeting URL detection helper."""

    def test_google_meet(self):
        text = "join this https://meet.google.com/abc-defg-hij please"
        assert _detect_meeting_url(text) == "https://meet.google.com/abc-defg-hij"

    def test_zoom(self):
        text = "hey can you hop on https://zoom.us/j/12345678 real quick"
        assert _detect_meeting_url(text) == "https://zoom.us/j/12345678"

    def test_teams(self):
        text = "link: https://teams.microsoft.com/l/meetup-join/abc"
        url = _detect_meeting_url(text)
        assert url is not None
        assert "teams.microsoft.com" in url

    def test_webex(self):
        text = "https://webex.com/meet/user123 let's go"
        assert _detect_meeting_url(text) == "https://webex.com/meet/user123"

    def test_no_url(self):
        assert _detect_meeting_url("hey can you join the meeting?") is None

    def test_non_meeting_url(self):
        assert _detect_meeting_url("check https://google.com/x") is None

    def test_http_url(self):
        url = _detect_meeting_url("http://meet.google.com/abc-defg-hij")
        assert url == "http://meet.google.com/abc-defg-hij"

    def test_case_insensitive(self):
        assert _detect_meeting_url("https://Meet.Google.Com/abc") is not None

    def test_url_with_query_params(self):
        url = _detect_meeting_url("https://zoom.us/j/123?pwd=abc123 join")
        assert url is not None
        assert "zoom.us" in url

    def test_url_in_slack_angle_brackets(self):
        """Slack wraps URLs in angle brackets — regex should stop at >."""
        text = "join <https://meet.google.com/abc-defg-hij> please"
        url = _detect_meeting_url(text)
        assert url is not None
        assert url.endswith("abc-defg-hij")
        assert ">" not in url

    def test_multiple_urls_returns_first(self):
        text = "https://meet.google.com/first and https://zoom.us/j/second"
        assert _detect_meeting_url(text) == "https://meet.google.com/first"

    def test_empty_string(self):
        assert _detect_meeting_url("") is None

    def test_url_alone(self):
        assert _detect_meeting_url("https://meet.google.com/xyz") == "https://meet.google.com/xyz"


# ---------------------------------------------------------------------------
# handle_app_mention — meeting URL detection in new conversations
# ---------------------------------------------------------------------------


class TestAppMentionMeetingJoin:
    """Meeting URL detection in new @mention conversations."""

    @pytest.mark.asyncio
    @patch(
        "src.api.routes.slack._dispatch_or_run_inline",
        new_callable=AsyncMock,
    )
    @patch(
        "src.api.routes.slack.check_slack_permission",
        return_value=(True, ""),
    )
    @patch(
        "src.api.routes.slack._resolve_user_display_name",
        new_callable=AsyncMock,
        return_value="Test User",
    )
    @patch(
        "src.api.routes.slack._extract_and_download_images",
        new_callable=AsyncMock,
        return_value=None,
    )
    async def test_mention_with_meeting_url_dispatches_both(
        self, mock_images, mock_name, mock_rbac, mock_dispatch
    ):
        """@mention with a meeting URL dispatches both conversation
        and meeting join events."""
        from src.api.routes.slack import handle_app_mention

        mock_role = MagicMock()
        mock_role.id = "head_of_marketing"
        mock_role.name = "Head of Marketing"
        mock_match = MagicMock()
        mock_match.role = mock_role
        mock_match.confidence = 0.9

        event = {
            "channel": "C123",
            "user": "U456",
            "text": ("<@BOT123> join this meeting https://meet.google.com/abc-defg-hij"),
            "ts": "1234567890.123456",
        }
        client = MagicMock()
        client.chat_postMessage = AsyncMock()
        say = AsyncMock()

        # Patch imports that happen inside the handler
        mock_registry = MagicMock()
        mock_router = AsyncMock()
        mock_router.route = AsyncMock(return_value=mock_match)

        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch(
                "src.skills.role_router.RoleRouter",
                return_value=mock_router,
            ),
        ):
            await handle_app_mention(event, client, say)

        # Should have dispatched conversation turn AND meeting join
        assert mock_dispatch.call_count == 2

        call_events = [c.kwargs["event_name"] for c in mock_dispatch.call_args_list]
        assert "sidera/conversation.turn" in call_events
        assert "sidera/meeting.join" in call_events

        # Meeting join should have the right data
        meeting_call = [
            c
            for c in mock_dispatch.call_args_list
            if c.kwargs["event_name"] == "sidera/meeting.join"
        ][0]
        meeting_data = meeting_call.kwargs["data"]
        assert "meet.google.com" in meeting_data["meeting_url"]
        assert meeting_data["role_id"] == "head_of_marketing"

        # Should have posted the meeting join notification
        posted = False
        for call in client.chat_postMessage.call_args_list:
            msg = call.kwargs.get("text", "")
            if "joining" in msg.lower():
                posted = True
                break
        assert posted, "Expected a meeting join notification message"

    @pytest.mark.asyncio
    @patch(
        "src.api.routes.slack._dispatch_or_run_inline",
        new_callable=AsyncMock,
    )
    @patch(
        "src.api.routes.slack.check_slack_permission",
        return_value=(True, ""),
    )
    @patch(
        "src.api.routes.slack._resolve_user_display_name",
        new_callable=AsyncMock,
        return_value="Test User",
    )
    @patch(
        "src.api.routes.slack._extract_and_download_images",
        new_callable=AsyncMock,
        return_value=None,
    )
    async def test_mention_without_meeting_url_single_dispatch(
        self, mock_images, mock_name, mock_rbac, mock_dispatch
    ):
        """@mention WITHOUT a meeting URL dispatches only the conversation
        turn — no meeting join."""
        from src.api.routes.slack import handle_app_mention

        mock_role = MagicMock()
        mock_role.id = "strategist"
        mock_role.name = "Strategist"
        mock_match = MagicMock()
        mock_match.role = mock_role
        mock_match.confidence = 0.9

        event = {
            "channel": "C123",
            "user": "U456",
            "text": "<@BOT123> what's our ROAS this week?",
            "ts": "1234567890.123456",
        }
        client = MagicMock()
        client.chat_postMessage = AsyncMock()
        say = AsyncMock()

        mock_registry = MagicMock()
        mock_router = AsyncMock()
        mock_router.route = AsyncMock(return_value=mock_match)

        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch(
                "src.skills.role_router.RoleRouter",
                return_value=mock_router,
            ),
        ):
            await handle_app_mention(event, client, say)

        # Only conversation turn — no meeting join
        assert mock_dispatch.call_count == 1
        call_event = mock_dispatch.call_args_list[0].kwargs["event_name"]
        assert call_event == "sidera/conversation.turn"


# ---------------------------------------------------------------------------
# handle_thread_message — meeting URL detection in existing threads
# ---------------------------------------------------------------------------


class TestThreadMessageMeetingJoin:
    """Meeting URL detection in existing conversation threads."""

    @pytest.mark.asyncio
    @patch(
        "src.api.routes.slack._dispatch_or_run_inline",
        new_callable=AsyncMock,
    )
    @patch(
        "src.api.routes.slack.check_slack_permission",
        return_value=(True, ""),
    )
    @patch(
        "src.api.routes.slack._resolve_user_display_name",
        new_callable=AsyncMock,
        return_value="Test User",
    )
    @patch(
        "src.api.routes.slack._extract_and_download_images",
        new_callable=AsyncMock,
        return_value=None,
    )
    async def test_thread_with_meeting_url_dispatches_both(
        self, mock_images, mock_name, mock_rbac, mock_dispatch
    ):
        """Reply in a Sidera thread with a meeting URL dispatches both
        conversation and meeting join events."""
        from src.api.routes.slack import handle_thread_message

        mock_thread = MagicMock()
        mock_thread.role_id = "head_of_marketing"
        mock_thread.is_active = True

        event = {
            "channel": "C123",
            "user": "U456",
            "text": "join https://meet.google.com/abc-defg-hij",
            "ts": "1234567891.000001",
            "thread_ts": "1234567890.123456",
        }
        client = MagicMock()
        client.chat_postMessage = AsyncMock()

        # Mock the async context manager for DB session
        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_role = MagicMock()
        mock_role.name = "Head of Marketing"
        mock_registry = MagicMock()
        mock_registry.get_role.return_value = mock_role

        mock_sc = MagicMock()

        with (
            patch(
                "src.db.session.get_db_session",
                return_value=mock_ctx,
            ),
            patch(
                "src.db.service.get_conversation_thread",
                new_callable=AsyncMock,
                return_value=mock_thread,
            ),
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch(
                "src.connectors.slack.SlackConnector",
                return_value=mock_sc,
            ),
        ):
            await handle_thread_message(event, client)

        # Should have dispatched both events
        assert mock_dispatch.call_count == 2

        call_events = [c.kwargs["event_name"] for c in mock_dispatch.call_args_list]
        assert "sidera/conversation.turn" in call_events
        assert "sidera/meeting.join" in call_events

        # Meeting join has correct data
        meeting_call = [
            c
            for c in mock_dispatch.call_args_list
            if c.kwargs["event_name"] == "sidera/meeting.join"
        ][0]
        d = meeting_call.kwargs["data"]
        assert "meet.google.com" in d["meeting_url"]
        assert d["role_id"] == "head_of_marketing"

    @pytest.mark.asyncio
    @patch(
        "src.api.routes.slack._dispatch_or_run_inline",
        new_callable=AsyncMock,
    )
    @patch(
        "src.api.routes.slack.check_slack_permission",
        return_value=(True, ""),
    )
    @patch(
        "src.api.routes.slack._resolve_user_display_name",
        new_callable=AsyncMock,
        return_value="Test User",
    )
    @patch(
        "src.api.routes.slack._extract_and_download_images",
        new_callable=AsyncMock,
        return_value=None,
    )
    async def test_thread_without_url_no_meeting_dispatch(
        self, mock_images, mock_name, mock_rbac, mock_dispatch
    ):
        """Thread reply without meeting URL dispatches only
        the conversation turn."""
        from src.api.routes.slack import handle_thread_message

        mock_thread = MagicMock()
        mock_thread.role_id = "strategist"
        mock_thread.is_active = True

        event = {
            "channel": "C123",
            "user": "U456",
            "text": "what about next quarter?",
            "ts": "1234567891.000001",
            "thread_ts": "1234567890.123456",
        }
        client = MagicMock()

        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "src.db.session.get_db_session",
                return_value=mock_ctx,
            ),
            patch(
                "src.db.service.get_conversation_thread",
                new_callable=AsyncMock,
                return_value=mock_thread,
            ),
        ):
            await handle_thread_message(event, client)

        # Only conversation turn — no meeting join
        assert mock_dispatch.call_count == 1
        call_event = mock_dispatch.call_args_list[0].kwargs["event_name"]
        assert call_event == "sidera/conversation.turn"
