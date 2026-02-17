"""Tests for /sidera meeting Slack commands."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.slack import handle_sidera_command

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_body(text: str, user_id: str = "U123", channel_id: str = "C456") -> dict:
    return {
        "text": text,
        "user_id": user_id,
        "channel_id": channel_id,
        "command": "/sidera",
    }


# ---------------------------------------------------------------------------
# /sidera meeting (no subcommand)
# ---------------------------------------------------------------------------


class TestMeetingHelp:
    """Show meeting help when no action given."""

    @pytest.mark.asyncio
    async def test_meeting_help(self):
        ack = AsyncMock()
        client = MagicMock()
        client.chat_postMessage = AsyncMock()

        body = _build_body("meeting")
        await handle_sidera_command(ack, body, client)

        ack.assert_called_once()
        msg = client.chat_postMessage.call_args[1]["text"]
        assert "meeting join" in msg
        assert "meeting status" in msg
        assert "meeting leave" in msg


# ---------------------------------------------------------------------------
# /sidera meeting join
# ---------------------------------------------------------------------------


class TestMeetingJoin:
    """Tests for /sidera meeting join."""

    @pytest.mark.asyncio
    async def test_join_missing_url(self):
        ack = AsyncMock()
        client = MagicMock()
        client.chat_postMessage = AsyncMock()

        body = _build_body("meeting join")
        await handle_sidera_command(ack, body, client)

        msg = client.chat_postMessage.call_args[1]["text"]
        assert "Usage" in msg

    @pytest.mark.asyncio
    async def test_join_invalid_url(self):
        ack = AsyncMock()
        client = MagicMock()
        client.chat_postMessage = AsyncMock()

        body = _build_body("meeting join https://example.com/not-a-meeting")
        await handle_sidera_command(ack, body, client)

        msg = client.chat_postMessage.call_args[1]["text"]
        assert "doesn't look like a meeting URL" in msg

    @pytest.mark.asyncio
    async def test_join_role_not_found(self):
        ack = AsyncMock()
        client = MagicMock()
        client.chat_postMessage = AsyncMock()

        mock_registry = MagicMock()
        mock_registry.get_role.return_value = None

        body = _build_body("meeting join https://meet.google.com/abc as nonexistent")

        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = mock_registry
            await handle_sidera_command(ack, body, client)

        msg = client.chat_postMessage.call_args[1]["text"]
        assert "not found" in msg

    @pytest.mark.asyncio
    async def test_join_success(self):
        ack = AsyncMock()
        client = MagicMock()
        client.chat_postMessage = AsyncMock()

        mock_role = MagicMock()
        mock_role.name = "Head of Marketing"
        mock_role.manages = ()

        mock_registry = MagicMock()
        mock_registry.get_role.return_value = mock_role

        body = _build_body("meeting join https://meet.google.com/abc as head_of_marketing")

        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
            ) as mock_load,
            patch("src.workflows.inngest_client.inngest_client") as mock_ic,
        ):
            mock_load.return_value = mock_registry
            mock_ic.send = AsyncMock()
            await handle_sidera_command(ack, body, client)

        # Should post confirmation
        last_msg = client.chat_postMessage.call_args[1]["text"]
        assert "joining" in last_msg.lower()
        assert "Head of Marketing" in last_msg

    @pytest.mark.asyncio
    async def test_join_default_role(self):
        """When no 'as <role>' provided, defaults to head_of_marketing."""
        ack = AsyncMock()
        client = MagicMock()
        client.chat_postMessage = AsyncMock()

        mock_role = MagicMock()
        mock_role.name = "Head of Marketing"
        mock_role.manages = ()

        mock_registry = MagicMock()
        mock_registry.get_role.return_value = mock_role

        body = _build_body("meeting join https://meet.google.com/xyz")

        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
            ) as mock_load,
            patch("src.workflows.inngest_client.inngest_client") as mock_ic,
        ):
            mock_load.return_value = mock_registry
            mock_ic.send = AsyncMock()
            await handle_sidera_command(ack, body, client)

        # Registry should have been called with default role
        mock_registry.get_role.assert_called_with("head_of_marketing")


# ---------------------------------------------------------------------------
# /sidera meeting status
# ---------------------------------------------------------------------------


class TestMeetingStatus:
    """Tests for /sidera meeting status."""

    @pytest.mark.asyncio
    async def test_status_no_active(self):
        ack = AsyncMock()
        client = MagicMock()
        client.chat_postMessage = AsyncMock()

        mock_manager = MagicMock()
        mock_manager.get_all_active_sessions.return_value = {}

        body = _build_body("meeting status")

        with patch("src.meetings.session.get_meeting_manager", return_value=mock_manager):
            await handle_sidera_command(ack, body, client)

        msg = client.chat_postMessage.call_args[1]["text"]
        assert "No active meetings" in msg

    @pytest.mark.asyncio
    async def test_status_with_sessions(self):
        ack = AsyncMock()
        client = MagicMock()
        client.chat_postMessage = AsyncMock()

        mock_session = MagicMock()
        mock_session.role_name = "Head of Marketing"
        mock_session.meeting_url = "https://meet.google.com/abc"
        mock_session.agent_turns = 5
        mock_session.total_cost_usd = 1.23

        mock_manager = MagicMock()
        mock_manager.get_all_active_sessions.return_value = {
            "bot-123": mock_session,
        }

        body = _build_body("meeting status")

        with patch("src.meetings.session.get_meeting_manager", return_value=mock_manager):
            await handle_sidera_command(ack, body, client)

        msg = client.chat_postMessage.call_args[1]["text"]
        assert "Head of Marketing" in msg
        assert "bot-123" in msg
        assert "5" in msg


# ---------------------------------------------------------------------------
# /sidera meeting leave
# ---------------------------------------------------------------------------


class TestMeetingLeave:
    """Tests for /sidera meeting leave."""

    @pytest.mark.asyncio
    async def test_leave_missing_bot_id(self):
        ack = AsyncMock()
        client = MagicMock()
        client.chat_postMessage = AsyncMock()

        body = _build_body("meeting leave")
        await handle_sidera_command(ack, body, client)

        msg = client.chat_postMessage.call_args[1]["text"]
        assert "Usage" in msg

    @pytest.mark.asyncio
    async def test_leave_no_session(self):
        ack = AsyncMock()
        client = MagicMock()
        client.chat_postMessage = AsyncMock()

        mock_manager = MagicMock()
        mock_manager.get_active_session.return_value = None

        body = _build_body("meeting leave bot-xyz")

        with patch("src.meetings.session.get_meeting_manager", return_value=mock_manager):
            await handle_sidera_command(ack, body, client)

        msg = client.chat_postMessage.call_args[1]["text"]
        assert "No active meeting" in msg

    @pytest.mark.asyncio
    async def test_leave_success(self):
        ack = AsyncMock()
        client = MagicMock()
        client.chat_postMessage = AsyncMock()

        mock_session = MagicMock()
        mock_session.role_name = "Head of Marketing"

        mock_manager = MagicMock()
        mock_manager.get_active_session.return_value = mock_session
        mock_manager.leave = AsyncMock(
            return_value={
                "agent_turns": 5,
                "transcript_entries": 25,
                "total_cost_usd": 2.50,
            }
        )

        body = _build_body("meeting leave bot-123")

        with patch("src.meetings.session.get_meeting_manager", return_value=mock_manager):
            await handle_sidera_command(ack, body, client)

        # Should have posted "leaving" and then "left" messages
        calls = client.chat_postMessage.call_args_list
        assert len(calls) >= 2
        assert "leaving" in calls[0][1]["text"].lower()
        assert "left" in calls[1][1]["text"].lower()


# ---------------------------------------------------------------------------
# /sidera meeting <unknown>
# ---------------------------------------------------------------------------


class TestMeetingUnknown:
    """Unknown meeting subcommand."""

    @pytest.mark.asyncio
    async def test_unknown_subcommand(self):
        ack = AsyncMock()
        client = MagicMock()
        client.chat_postMessage = AsyncMock()

        body = _build_body("meeting foobar")
        await handle_sidera_command(ack, body, client)

        msg = client.chat_postMessage.call_args[1]["text"]
        assert "Unknown meeting command" in msg


# ---------------------------------------------------------------------------
# SlackConnector.send_meeting_notification
# ---------------------------------------------------------------------------


class TestSendMeetingNotification:
    """Tests for the SlackConnector send_meeting_notification method."""

    def test_joining_notification(self):
        from src.connectors.slack import SlackConnector

        with patch("src.connectors.slack.WebClient") as mock_wc_cls:
            mock_wc = MagicMock()
            mock_wc.chat_postMessage.return_value = {
                "ok": True,
                "channel": "C456",
                "ts": "1234.5678",
            }
            mock_wc_cls.return_value = mock_wc

            connector = SlackConnector(
                credentials={
                    "bot_token": "xoxb-test",
                    "channel_id": "C456",
                }
            )
            result = connector.send_meeting_notification(
                channel_id="C456",
                meeting_url="https://meet.google.com/abc",
                role_name="Head of Marketing",
                status="joining",
            )

        assert result["ok"] is True
        call_kwargs = mock_wc.chat_postMessage.call_args[1]
        assert "Head of Marketing" in call_kwargs["text"]
        assert "joining" in call_kwargs["text"].lower() or "Joining" in str(call_kwargs["blocks"])

    def test_ended_notification_with_details(self):
        from src.connectors.slack import SlackConnector

        with patch("src.connectors.slack.WebClient") as mock_wc_cls:
            mock_wc = MagicMock()
            mock_wc.chat_postMessage.return_value = {
                "ok": True,
                "channel": "C456",
                "ts": "1234.5678",
            }
            mock_wc_cls.return_value = mock_wc

            connector = SlackConnector(
                credentials={
                    "bot_token": "xoxb-test",
                    "channel_id": "C456",
                }
            )
            result = connector.send_meeting_notification(
                channel_id="C456",
                meeting_url="https://meet.google.com/abc",
                role_name="Head of Marketing",
                status="ended",
                details={
                    "bot_id": "bot-123",
                    "duration_minutes": 45,
                    "agent_turns": 8,
                    "cost": 3.25,
                },
            )

        assert result["ok"] is True
        call_kwargs = mock_wc.chat_postMessage.call_args[1]
        blocks = call_kwargs["blocks"]
        # Should have a context block with details
        context_blocks = [b for b in blocks if b["type"] == "context"]
        assert len(context_blocks) == 1
        detail_text = context_blocks[0]["elements"][0]["text"]
        assert "bot-123" in detail_text
        assert "45 min" in detail_text
