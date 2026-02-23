"""Tests for Computer Use MCP tools (src/mcp_servers/computer_use.py).

Covers all 3 tools:
    1. run_computer_use_task     - Execute a desktop automation task
    2. get_computer_use_session  - Check session status
    3. stop_computer_use_session - Stop a session

The ComputerUseConnector is mocked for all tests.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors.computer_use import (
    ComputerUseError,
    ComputerUseSession,
    ComputerUseTimeoutError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine synchronously for testing."""
    return asyncio.run(coro)


def _mock_connector():
    """Return a MagicMock standing in for ComputerUseConnector."""
    return MagicMock()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_connector():
    """Patch _get_connector in the MCP module and return the mock."""
    connector = _mock_connector()
    with patch("src.mcp_servers.computer_use._get_connector", return_value=connector):
        yield connector


# ===========================================================================
# 1. run_computer_use_task
# ===========================================================================


class TestRunComputerUseTask:
    """run_computer_use_task MCP tool tests."""

    def test_successful_task(self, mock_connector):
        """Should format task result with output and stats."""
        from src.mcp_servers.computer_use import run_computer_use_task

        mock_connector.run_task = AsyncMock(return_value={
            "output": "Clicked the button and submitted the form.",
            "action_count": 5,
            "iterations": 3,
            "cost_usd": 0.0523,
            "session_id": "abc123",
            "screenshots": ["img1", "img2"],
            "input_tokens": 5000,
            "output_tokens": 2000,
        })

        result = _run(run_computer_use_task.handler({
            "task": "Fill out the contact form and submit it",
        }))

        text = result["content"][0]["text"]
        assert "Contact form" in text or "contact form" in text
        assert "5" in text  # action_count
        assert "abc123" in text  # session_id
        assert "$0.0523" in text  # cost
        assert "2 screenshot" in text  # screenshot count
        assert "is_error" not in result

    def test_empty_output(self, mock_connector):
        """Should show default message when no text output."""
        from src.mcp_servers.computer_use import run_computer_use_task

        mock_connector.run_task = AsyncMock(return_value={
            "output": "",
            "action_count": 10,
            "iterations": 5,
            "cost_usd": 0.1,
            "session_id": "def456",
            "screenshots": [],
        })

        result = _run(run_computer_use_task.handler({"task": "Do something"}))

        text = result["content"][0]["text"]
        assert "GUI actions" in text or "No text output" in text

    def test_missing_task_returns_error(self, mock_connector):
        """Empty task should return error response."""
        from src.mcp_servers.computer_use import run_computer_use_task

        result = _run(run_computer_use_task.handler({"task": ""}))

        assert result.get("is_error") is True
        assert "required" in result["content"][0]["text"].lower()

    def test_missing_task_key_returns_error(self, mock_connector):
        """Missing task key should return error response."""
        from src.mcp_servers.computer_use import run_computer_use_task

        result = _run(run_computer_use_task.handler({}))

        assert result.get("is_error") is True

    def test_max_iterations_capped(self, mock_connector):
        """max_iterations should be capped at 100."""
        from src.mcp_servers.computer_use import run_computer_use_task

        mock_connector.run_task = AsyncMock(return_value={
            "output": "done", "action_count": 1, "iterations": 1,
            "cost_usd": 0.01, "session_id": "x", "screenshots": [],
        })

        _run(run_computer_use_task.handler({
            "task": "do something",
            "max_iterations": 500,
        }))

        call_kwargs = mock_connector.run_task.call_args[1]
        assert call_kwargs["max_iterations"] <= 100

    def test_timeout_capped(self, mock_connector):
        """timeout should be capped at 600."""
        from src.mcp_servers.computer_use import run_computer_use_task

        mock_connector.run_task = AsyncMock(return_value={
            "output": "done", "action_count": 1, "iterations": 1,
            "cost_usd": 0.01, "session_id": "x", "screenshots": [],
        })

        _run(run_computer_use_task.handler({
            "task": "do something",
            "timeout": 9999,
        }))

        call_kwargs = mock_connector.run_task.call_args[1]
        assert call_kwargs["timeout"] <= 600

    def test_connector_error_returns_error_response(self, mock_connector):
        """Connector errors should return error response."""
        from src.mcp_servers.computer_use import run_computer_use_task

        mock_connector.run_task = AsyncMock(
            side_effect=ComputerUseError("Container unavailable")
        )

        result = _run(run_computer_use_task.handler({
            "task": "do something",
        }))

        assert result.get("is_error") is True
        assert "Container unavailable" in result["content"][0]["text"]

    def test_timeout_error_returns_error_response(self, mock_connector):
        """Timeout errors should return error response."""
        from src.mcp_servers.computer_use import run_computer_use_task

        mock_connector.run_task = AsyncMock(
            side_effect=ComputerUseTimeoutError("Timed out")
        )

        result = _run(run_computer_use_task.handler({
            "task": "do something",
        }))

        assert result.get("is_error") is True

    def test_task_text_truncated_in_output(self, mock_connector):
        """Very long task descriptions should be truncated in display."""
        from src.mcp_servers.computer_use import run_computer_use_task

        long_task = "A" * 500

        mock_connector.run_task = AsyncMock(return_value={
            "output": "done", "action_count": 1, "iterations": 1,
            "cost_usd": 0.01, "session_id": "x", "screenshots": [],
        })

        result = _run(run_computer_use_task.handler({"task": long_task}))

        text = result["content"][0]["text"]
        # The task display is truncated to 200 chars
        assert "A" * 201 not in text

    def test_no_screenshots_no_count(self, mock_connector):
        """No screenshots should not show screenshot count."""
        from src.mcp_servers.computer_use import run_computer_use_task

        mock_connector.run_task = AsyncMock(return_value={
            "output": "done", "action_count": 1, "iterations": 1,
            "cost_usd": 0.01, "session_id": "x", "screenshots": [],
        })

        result = _run(run_computer_use_task.handler({"task": "do it"}))

        text = result["content"][0]["text"]
        assert "screenshot" not in text.lower()


# ===========================================================================
# 2. get_computer_use_session
# ===========================================================================


class TestGetComputerUseSession:
    """get_computer_use_session MCP tool tests."""

    def test_existing_session(self, mock_connector):
        """Should return session details for existing session."""
        from src.mcp_servers.computer_use import get_computer_use_session

        session = ComputerUseSession(
            session_id="abc123",
            display_width=1024,
            display_height=768,
            action_count=15,
            total_cost_usd=0.25,
            is_active=True,
            created_at=time.time() - 60,  # 60 seconds ago
        )
        mock_connector.get_session.return_value = session

        result = _run(get_computer_use_session.handler({"session_id": "abc123"}))

        text = result["content"][0]["text"]
        assert "abc123" in text
        assert "True" in text  # is_active
        assert "15" in text  # action_count
        assert "1024x768" in text  # display dimensions
        assert "is_error" not in result

    def test_nonexistent_session(self, mock_connector):
        """Should return error for unknown session."""
        from src.mcp_servers.computer_use import get_computer_use_session

        mock_connector.get_session.return_value = None

        result = _run(get_computer_use_session.handler({"session_id": "nope"}))

        assert result.get("is_error") is True
        assert "No session found" in result["content"][0]["text"]

    def test_empty_session_id_returns_error(self, mock_connector):
        """Empty session_id should return error."""
        from src.mcp_servers.computer_use import get_computer_use_session

        result = _run(get_computer_use_session.handler({"session_id": ""}))

        assert result.get("is_error") is True

    def test_missing_session_id_returns_error(self, mock_connector):
        """Missing session_id should return error."""
        from src.mcp_servers.computer_use import get_computer_use_session

        result = _run(get_computer_use_session.handler({}))

        assert result.get("is_error") is True


# ===========================================================================
# 3. stop_computer_use_session
# ===========================================================================


class TestStopComputerUseSession:
    """stop_computer_use_session MCP tool tests."""

    def test_successful_stop(self, mock_connector):
        """Should stop session and return confirmation."""
        from src.mcp_servers.computer_use import stop_computer_use_session

        mock_connector.destroy_session = AsyncMock()

        result = _run(stop_computer_use_session.handler({"session_id": "abc123"}))

        text = result["content"][0]["text"]
        assert "abc123" in text
        assert "stopped" in text.lower() or "cleaned" in text.lower()
        mock_connector.destroy_session.assert_called_once_with("abc123")
        assert "is_error" not in result

    def test_empty_session_id_returns_error(self, mock_connector):
        """Empty session_id should return error."""
        from src.mcp_servers.computer_use import stop_computer_use_session

        result = _run(stop_computer_use_session.handler({"session_id": ""}))

        assert result.get("is_error") is True

    def test_missing_session_id_returns_error(self, mock_connector):
        """Missing session_id should return error."""
        from src.mcp_servers.computer_use import stop_computer_use_session

        result = _run(stop_computer_use_session.handler({}))

        assert result.get("is_error") is True

    def test_nonexistent_session_still_succeeds(self, mock_connector):
        """Stopping a nonexistent session should succeed (destroy is idempotent)."""
        from src.mcp_servers.computer_use import stop_computer_use_session

        mock_connector.destroy_session = AsyncMock()

        result = _run(stop_computer_use_session.handler({"session_id": "nonexistent"}))

        assert "is_error" not in result
        mock_connector.destroy_session.assert_called_once()


# ===========================================================================
# create_computer_use_tools
# ===========================================================================


class TestCreateComputerUseTools:
    """create_computer_use_tools convenience function."""

    def test_returns_all_tool_names(self):
        """Should return a list of all 3 tool names."""
        from src.mcp_servers.computer_use import create_computer_use_tools

        names = create_computer_use_tools()
        assert len(names) == 3
        assert "run_computer_use_task" in names
        assert "get_computer_use_session" in names
        assert "stop_computer_use_session" in names
