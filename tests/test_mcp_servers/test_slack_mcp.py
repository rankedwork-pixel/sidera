"""Tests for src.mcp_servers.slack -- Slack MCP tools.

Covers all 6 tools: send_slack_alert, send_slack_briefing_preview,
check_slack_connection, send_slack_thread_reply, react_to_message, and
search_role_memory_archive. The SlackConnector is mocked for all tests.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.connectors.slack import SlackAuthError, SlackConnectorError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine synchronously for testing."""
    return asyncio.run(coro)


def _mock_connector():
    """Return a MagicMock standing in for SlackConnector."""
    return MagicMock()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_connector():
    """Patch _get_connector in the MCP module and return the mock."""
    connector = _mock_connector()
    with patch("src.mcp_servers.slack._get_connector", return_value=connector):
        yield connector


# ===========================================================================
# 1. send_slack_alert
# ===========================================================================


class TestSendSlackAlert:
    """send_slack_alert MCP tool."""

    def test_successful_alert(self, mock_connector):
        """send_slack_alert should return success text on a valid call."""
        from src.mcp_servers.slack import send_slack_alert

        mock_connector.send_alert.return_value = {
            "ok": True,
            "channel": "C0123456789",
            "ts": "1234567890.123456",
        }

        result = _run(
            send_slack_alert.handler(
                {
                    "alert_type": "cost_overrun",
                    "message": "Budget exceeded",
                }
            )
        )

        assert result["content"][0]["type"] == "text"
        assert "Alert sent successfully" in result["content"][0]["text"]
        assert "C0123456789" in result["content"][0]["text"]
        assert "is_error" not in result

    def test_alert_with_details(self, mock_connector):
        """send_slack_alert should pass details to the connector."""
        from src.mcp_servers.slack import send_slack_alert

        mock_connector.send_alert.return_value = {
            "ok": True,
            "channel": "C123",
            "ts": "123.456",
        }

        _run(
            send_slack_alert.handler(
                {
                    "alert_type": "anomaly",
                    "message": "CTR dropped",
                    "details": {"campaign": "Brand", "drop": "40%"},
                }
            )
        )

        mock_connector.send_alert.assert_called_once_with(
            channel_id=None,
            alert_type="anomaly",
            message="CTR dropped",
            details={"campaign": "Brand", "drop": "40%"},
        )

    def test_alert_missing_alert_type(self, mock_connector):
        """send_slack_alert should return error when alert_type is missing."""
        from src.mcp_servers.slack import send_slack_alert

        result = _run(send_slack_alert.handler({"message": "test"}))

        assert result["is_error"] is True
        assert "alert_type is required" in result["content"][0]["text"]

    def test_alert_missing_message(self, mock_connector):
        """send_slack_alert should return error when message is missing."""
        from src.mcp_servers.slack import send_slack_alert

        result = _run(send_slack_alert.handler({"alert_type": "info"}))

        assert result["is_error"] is True
        assert "message is required" in result["content"][0]["text"]

    def test_alert_connector_error(self, mock_connector):
        """send_slack_alert should return error on connector failure."""
        from src.mcp_servers.slack import send_slack_alert

        mock_connector.send_alert.side_effect = SlackConnectorError("channel_not_found")

        result = _run(
            send_slack_alert.handler(
                {
                    "alert_type": "error",
                    "message": "Test error",
                }
            )
        )

        assert result["is_error"] is True
        assert "Failed to send Slack alert" in result["content"][0]["text"]


# ===========================================================================
# 2. send_slack_briefing_preview
# ===========================================================================


class TestSendSlackBriefingPreview:
    """send_slack_briefing_preview MCP tool."""

    def test_successful_briefing_preview(self, mock_connector):
        """send_slack_briefing_preview should return success text."""
        from src.mcp_servers.slack import send_slack_briefing_preview

        mock_connector.send_briefing.return_value = {
            "ok": True,
            "channel": "C0123456789",
            "ts": "1234567890.123456",
        }

        result = _run(
            send_slack_briefing_preview.handler(
                {
                    "briefing_text": "Great day for ads!",
                    "recommendations": [{"title": "Scale up", "description": "Good ROAS"}],
                }
            )
        )

        assert "Briefing preview sent" in result["content"][0]["text"]
        assert "1 recommendation(s)" in result["content"][0]["text"]
        assert "is_error" not in result

    def test_briefing_preview_no_recommendations(self, mock_connector):
        """send_slack_briefing_preview should work without recommendations."""
        from src.mcp_servers.slack import send_slack_briefing_preview

        mock_connector.send_briefing.return_value = {
            "ok": True,
            "channel": "C123",
            "ts": "123.456",
        }

        result = _run(
            send_slack_briefing_preview.handler(
                {
                    "briefing_text": "Summary only.",
                }
            )
        )

        assert "0 recommendation(s)" in result["content"][0]["text"]
        mock_connector.send_briefing.assert_called_once_with(
            channel_id=None,
            briefing_text="Summary only.",
            recommendations=[],
        )

    def test_briefing_preview_missing_text(self, mock_connector):
        """send_slack_briefing_preview should return error when text is missing."""
        from src.mcp_servers.slack import send_slack_briefing_preview

        result = _run(send_slack_briefing_preview.handler({}))

        assert result["is_error"] is True
        assert "briefing_text is required" in result["content"][0]["text"]

    def test_briefing_preview_connector_error(self, mock_connector):
        """send_slack_briefing_preview should return error on connector failure."""
        from src.mcp_servers.slack import send_slack_briefing_preview

        mock_connector.send_briefing.side_effect = SlackConnectorError("api_error")

        result = _run(
            send_slack_briefing_preview.handler(
                {
                    "briefing_text": "Test briefing",
                }
            )
        )

        assert result["is_error"] is True
        assert "Failed to send briefing preview" in result["content"][0]["text"]


# ===========================================================================
# 3. check_slack_connection
# ===========================================================================


class TestCheckSlackConnection:
    """check_slack_connection MCP tool."""

    def test_successful_connection(self, mock_connector):
        """check_slack_connection should return team/user/bot_id."""
        from src.mcp_servers.slack import check_slack_connection

        mock_connector.test_connection.return_value = {
            "ok": True,
            "team": "Sidera Team",
            "user": "sidera-bot",
            "bot_id": "B123",
        }

        result = _run(check_slack_connection.handler({}))

        text = result["content"][0]["text"]
        assert "Slack connection successful" in text
        assert "Sidera Team" in text
        assert "sidera-bot" in text
        assert "B123" in text
        assert "is_error" not in result

    def test_connection_auth_error(self, mock_connector):
        """check_slack_connection should return error on auth failure."""
        from src.mcp_servers.slack import check_slack_connection

        mock_connector.test_connection.side_effect = SlackAuthError("invalid_auth")

        result = _run(check_slack_connection.handler({}))

        assert result["is_error"] is True
        assert "Slack connection failed" in result["content"][0]["text"]

    def test_connection_generic_error(self, mock_connector):
        """check_slack_connection should return error on generic failure."""
        from src.mcp_servers.slack import check_slack_connection

        mock_connector.test_connection.side_effect = SlackConnectorError("some_error")

        result = _run(check_slack_connection.handler({}))

        assert result["is_error"] is True
        assert "Slack connection failed" in result["content"][0]["text"]


# ===========================================================================
# 4. Factory functions
# ===========================================================================


class TestFactoryFunctions:
    """create_slack_tools helper."""

    def test_create_slack_tools_returns_six(self):
        """create_slack_tools should return exactly 6 tools."""
        from src.mcp_servers.slack import create_slack_tools

        tools = create_slack_tools()
        assert len(tools) == 6

    def test_create_slack_tools_includes_memory_search(self):
        """create_slack_tools should include the memory archive search tool."""
        from src.mcp_servers.slack import create_slack_tools

        tools = create_slack_tools()
        tool_names = [getattr(t, "tool_name", getattr(t, "name", None)) for t in tools]
        assert "search_role_memory_archive" in tool_names

    def test_create_slack_tools_includes_react(self):
        """create_slack_tools should include the react_to_message tool."""
        from src.mcp_servers.slack import create_slack_tools

        tools = create_slack_tools()
        tool_names = [getattr(t, "tool_name", getattr(t, "name", None)) for t in tools]
        assert "react_to_message" in tool_names


# ===========================================================================
# 5. react_to_message
# ===========================================================================


class TestReactToMessage:
    """react_to_message MCP tool."""

    def test_successful_reaction(self, mock_connector):
        """react_to_message should call add_reaction and return success."""
        from src.mcp_servers.slack import react_to_message

        result = _run(
            react_to_message.handler(
                {
                    "channel_id": "C0123456789",
                    "timestamp": "1234567890.123456",
                    "emoji": "fire",
                }
            )
        )

        assert "is_error" not in result
        assert ":fire:" in result["content"][0]["text"]
        mock_connector.add_reaction.assert_called_once_with(
            channel_id="C0123456789",
            timestamp="1234567890.123456",
            name="fire",
        )

    def test_strips_colons_from_emoji(self, mock_connector):
        """react_to_message should strip colons from the emoji name."""
        from src.mcp_servers.slack import react_to_message

        _run(
            react_to_message.handler(
                {
                    "channel_id": "C123",
                    "timestamp": "123.456",
                    "emoji": ":thumbsup:",
                }
            )
        )

        mock_connector.add_reaction.assert_called_once_with(
            channel_id="C123",
            timestamp="123.456",
            name="thumbsup",
        )

    def test_missing_channel_id(self, mock_connector):
        """react_to_message should return error when channel_id is missing."""
        from src.mcp_servers.slack import react_to_message

        result = _run(
            react_to_message.handler(
                {
                    "timestamp": "123.456",
                    "emoji": "fire",
                }
            )
        )

        assert result["is_error"] is True
        assert "channel_id is required" in result["content"][0]["text"]

    def test_missing_timestamp(self, mock_connector):
        """react_to_message should return error when timestamp is missing."""
        from src.mcp_servers.slack import react_to_message

        result = _run(
            react_to_message.handler(
                {
                    "channel_id": "C123",
                    "emoji": "fire",
                }
            )
        )

        assert result["is_error"] is True
        assert "timestamp is required" in result["content"][0]["text"]

    def test_missing_emoji(self, mock_connector):
        """react_to_message should return error when emoji is missing."""
        from src.mcp_servers.slack import react_to_message

        result = _run(
            react_to_message.handler(
                {
                    "channel_id": "C123",
                    "timestamp": "123.456",
                }
            )
        )

        assert result["is_error"] is True
        assert "emoji is required" in result["content"][0]["text"]

    def test_connector_error(self, mock_connector):
        """react_to_message should return error on connector failure."""
        from src.mcp_servers.slack import react_to_message

        mock_connector.add_reaction.side_effect = Exception("already_reacted")

        result = _run(
            react_to_message.handler(
                {
                    "channel_id": "C123",
                    "timestamp": "123.456",
                    "emoji": "fire",
                }
            )
        )

        assert result["is_error"] is True
        assert "Failed to add reaction" in result["content"][0]["text"]


# ===========================================================================
# 6. search_role_memory_archive
# ===========================================================================


class TestSearchRoleMemoryArchive:
    """search_role_memory_archive MCP tool."""

    def test_missing_role_id(self):
        """Should return error when role_id is missing."""
        from src.mcp_servers.slack import search_role_memory_archive

        result = _run(search_role_memory_archive.handler({}))

        assert result["is_error"] is True
        assert "role_id is required" in result["content"][0]["text"]

    def test_successful_search_with_results(self):
        """Should return formatted memories when found."""
        from unittest.mock import AsyncMock

        from src.mcp_servers.slack import search_role_memory_archive

        mock_memory = MagicMock()
        mock_memory.memory_type = "decision"
        mock_memory.title = "Paused Campaign X"
        mock_memory.content = "Campaign X paused due to high CPA."
        mock_memory.is_archived = True
        mock_memory.created_at = MagicMock()
        mock_memory.created_at.strftime.return_value = "2025-06-01"

        mock_search = AsyncMock(return_value=[mock_memory])
        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                "src.db.service.search_role_memories",
                mock_search,
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=mock_ctx,
            ),
        ):
            result = _run(
                search_role_memory_archive.handler(
                    {
                        "role_id": "analyst",
                        "query": "campaign",
                    }
                )
            )

        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "1 memories" in text
        assert "Paused Campaign X" in text
        assert "archived" in text

    def test_no_results(self):
        """Should return helpful message when no memories found."""
        from unittest.mock import AsyncMock

        from src.mcp_servers.slack import search_role_memory_archive

        mock_search = AsyncMock(return_value=[])
        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                "src.db.service.search_role_memories",
                mock_search,
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=mock_ctx,
            ),
        ):
            result = _run(
                search_role_memory_archive.handler(
                    {
                        "role_id": "analyst",
                        "query": "nonexistent",
                    }
                )
            )

        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "No memories found" in text

    def test_db_error_returns_error_response(self):
        """Should return error on database failure."""
        from src.mcp_servers.slack import search_role_memory_archive

        with patch(
            "src.db.session.get_db_session",
            side_effect=Exception("DB connection failed"),
        ):
            result = _run(
                search_role_memory_archive.handler(
                    {
                        "role_id": "analyst",
                    }
                )
            )

        assert result["is_error"] is True
        assert "Failed to search memory archive" in result["content"][0]["text"]
