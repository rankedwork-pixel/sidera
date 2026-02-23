"""Tests for SSH MCP tools (src/mcp_servers/ssh.py).

Covers all 6 tools:
    1. run_remote_command       - Execute a shell command
    2. read_remote_file         - Read a file
    3. list_remote_directory    - List a directory
    4. get_remote_system_info   - System info
    5. list_remote_processes    - Process listing
    6. tail_remote_log          - Tail a log file

The SSHConnector is mocked for all tests — no real SSH connections.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors.ssh import SSHCommandBlockedError, SSHConnectorError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine synchronously for testing."""
    return asyncio.run(coro)


def _mock_connector():
    """Return a MagicMock standing in for SSHConnector."""
    return MagicMock()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_connector():
    """Patch _get_connector in the MCP module and return the mock."""
    connector = _mock_connector()
    with patch("src.mcp_servers.ssh._get_connector", return_value=connector):
        yield connector


# ===========================================================================
# 1. run_remote_command
# ===========================================================================


class TestRunRemoteCommand:
    """run_remote_command MCP tool tests."""

    def test_successful_command(self, mock_connector):
        """Should format stdout, stderr, and exit code."""
        from src.mcp_servers.ssh import run_remote_command

        mock_connector.run_command = AsyncMock(return_value={
            "stdout": "hello world",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
        })

        result = _run(run_remote_command.handler({"command": "echo hello world"}))

        assert result["content"][0]["type"] == "text"
        text = result["content"][0]["text"]
        assert "hello world" in text
        assert "Exit code:** 0" in text
        assert "is_error" not in result

    def test_command_with_stderr(self, mock_connector):
        """Should include stderr in output."""
        from src.mcp_servers.ssh import run_remote_command

        mock_connector.run_command = AsyncMock(return_value={
            "stdout": "",
            "stderr": "file not found",
            "exit_code": 1,
            "timed_out": False,
        })

        result = _run(run_remote_command.handler({"command": "cat /missing"}))

        text = result["content"][0]["text"]
        assert "file not found" in text
        assert "Exit code:** 1" in text

    def test_timed_out_command(self, mock_connector):
        """Should show timeout warning."""
        from src.mcp_servers.ssh import run_remote_command

        mock_connector.run_command = AsyncMock(return_value={
            "stdout": "",
            "stderr": "Command timed out after 30s",
            "exit_code": -1,
            "timed_out": True,
        })

        result = _run(run_remote_command.handler({"command": "sleep 999"}))

        text = result["content"][0]["text"]
        assert "timed out" in text.lower()

    def test_missing_command_returns_error(self, mock_connector):
        """Empty command should return error response."""
        from src.mcp_servers.ssh import run_remote_command

        result = _run(run_remote_command.handler({"command": ""}))

        assert result.get("is_error") is True
        assert "required" in result["content"][0]["text"].lower()

    def test_missing_command_key_returns_error(self, mock_connector):
        """Missing command key should return error response."""
        from src.mcp_servers.ssh import run_remote_command

        result = _run(run_remote_command.handler({}))

        assert result.get("is_error") is True

    def test_blocked_command_returns_error(self, mock_connector):
        """Blocked command should return error response via exception."""
        from src.mcp_servers.ssh import run_remote_command

        mock_connector.run_command = AsyncMock(
            side_effect=SSHCommandBlockedError("Command blocked")
        )

        result = _run(run_remote_command.handler({"command": "rm -rf /"}))

        assert result.get("is_error") is True
        assert "blocked" in result["content"][0]["text"].lower()

    def test_timeout_and_working_dir_passed(self, mock_connector):
        """timeout and working_dir should be forwarded to connector."""
        from src.mcp_servers.ssh import run_remote_command

        mock_connector.run_command = AsyncMock(return_value={
            "stdout": "ok", "stderr": "", "exit_code": 0, "timed_out": False,
        })

        _run(run_remote_command.handler({
            "command": "ls",
            "timeout": 60,
            "working_dir": "/var/log",
        }))

        mock_connector.run_command.assert_called_once_with(
            command="ls",
            timeout=60,
            working_dir="/var/log",
        )

    def test_connector_error_returns_error_response(self, mock_connector):
        """Generic connector errors should return error response."""
        from src.mcp_servers.ssh import run_remote_command

        mock_connector.run_command = AsyncMock(
            side_effect=SSHConnectorError("Connection lost")
        )

        result = _run(run_remote_command.handler({"command": "ls"}))

        assert result.get("is_error") is True
        assert "Connection lost" in result["content"][0]["text"]


# ===========================================================================
# 2. read_remote_file
# ===========================================================================


class TestReadRemoteFile:
    """read_remote_file MCP tool tests."""

    def test_successful_read(self, mock_connector):
        """Should format file content with header."""
        from src.mcp_servers.ssh import read_remote_file

        mock_connector.read_file = AsyncMock(return_value={
            "content": "server {\n  listen 80;\n}\n",
            "path": "/etc/nginx/nginx.conf",
            "lines": 3,
            "total_lines": 3,
            "truncated": False,
        })

        result = _run(read_remote_file.handler({"path": "/etc/nginx/nginx.conf"}))

        text = result["content"][0]["text"]
        assert "/etc/nginx/nginx.conf" in text
        assert "listen 80" in text
        assert "is_error" not in result

    def test_truncated_file_shows_warning(self, mock_connector):
        """Truncated file should show line count warning."""
        from src.mcp_servers.ssh import read_remote_file

        mock_connector.read_file = AsyncMock(return_value={
            "content": "line\n" * 100,
            "path": "/big/file",
            "lines": 100,
            "total_lines": 50000,
            "truncated": True,
        })

        result = _run(read_remote_file.handler({"path": "/big/file"}))

        text = result["content"][0]["text"]
        assert "50000" in text or "50,000" in text

    def test_tail_mode(self, mock_connector):
        """tail=True should be passed through and displayed."""
        from src.mcp_servers.ssh import read_remote_file

        mock_connector.read_file = AsyncMock(return_value={
            "content": "last line",
            "path": "/var/log/syslog",
            "lines": 1,
            "total_lines": 10000,
            "truncated": True,
        })

        result = _run(read_remote_file.handler({
            "path": "/var/log/syslog",
            "tail": True,
            "max_lines": 50,
        }))

        text = result["content"][0]["text"]
        assert "last" in text
        mock_connector.read_file.assert_called_once_with(
            path="/var/log/syslog", max_lines=50, tail=True,
        )

    def test_empty_path_returns_error(self, mock_connector):
        """Empty path should return error response."""
        from src.mcp_servers.ssh import read_remote_file

        result = _run(read_remote_file.handler({"path": ""}))

        assert result.get("is_error") is True

    def test_missing_path_returns_error(self, mock_connector):
        """Missing path key should return error response."""
        from src.mcp_servers.ssh import read_remote_file

        result = _run(read_remote_file.handler({}))

        assert result.get("is_error") is True

    def test_connector_error_returns_error(self, mock_connector):
        """Connector errors should be caught and formatted."""
        from src.mcp_servers.ssh import read_remote_file

        mock_connector.read_file = AsyncMock(
            side_effect=SSHConnectorError("Read failed")
        )

        result = _run(read_remote_file.handler({"path": "/etc/hosts"}))

        assert result.get("is_error") is True
        assert "Read failed" in result["content"][0]["text"]


# ===========================================================================
# 3. list_remote_directory
# ===========================================================================


class TestListRemoteDirectory:
    """list_remote_directory MCP tool tests."""

    def test_successful_listing(self, mock_connector):
        """Should format directory listing."""
        from src.mcp_servers.ssh import list_remote_directory

        mock_connector.list_directory = AsyncMock(return_value={
            "entries": "drwxr-xr-x root root bin\n-rw-r--r-- root root file.txt\n",
            "path": "/home",
        })

        result = _run(list_remote_directory.handler({"path": "/home"}))

        text = result["content"][0]["text"]
        assert "/home" in text
        assert "bin" in text
        assert "is_error" not in result

    def test_default_path(self, mock_connector):
        """Missing path should default to /."""
        from src.mcp_servers.ssh import list_remote_directory

        mock_connector.list_directory = AsyncMock(return_value={
            "entries": "listing",
            "path": "/",
        })

        _run(list_remote_directory.handler({}))

        mock_connector.list_directory.assert_called_once_with(
            path="/", show_hidden=False,
        )

    def test_show_hidden_passed(self, mock_connector):
        """show_hidden should be forwarded."""
        from src.mcp_servers.ssh import list_remote_directory

        mock_connector.list_directory = AsyncMock(return_value={
            "entries": "listing",
            "path": "/home",
        })

        _run(list_remote_directory.handler({"path": "/home", "show_hidden": True}))

        mock_connector.list_directory.assert_called_once_with(
            path="/home", show_hidden=True,
        )

    def test_error_returns_error_response(self, mock_connector):
        """Connector errors should be caught."""
        from src.mcp_servers.ssh import list_remote_directory

        mock_connector.list_directory = AsyncMock(
            side_effect=SSHConnectorError("Directory not found")
        )

        result = _run(list_remote_directory.handler({"path": "/nope"}))

        assert result.get("is_error") is True


# ===========================================================================
# 4. get_remote_system_info
# ===========================================================================


class TestGetRemoteSystemInfo:
    """get_remote_system_info MCP tool tests."""

    def test_successful_info(self, mock_connector):
        """Should format system info fields."""
        from src.mcp_servers.ssh import get_remote_system_info

        mock_connector.get_system_info = AsyncMock(return_value={
            "hostname": "web-server-01",
            "os": "Ubuntu 22.04.3 LTS",
            "uptime": "up 30 days, 5:42",
            "load": "0.50 0.40 0.30 1/256 12345",
            "disk": "/dev/sda1 50G 20G 30G 40% /",
            "memory": "Mem: 16Gi 8.0Gi 4.0Gi",
        })

        result = _run(get_remote_system_info.handler({}))

        text = result["content"][0]["text"]
        assert "web-server-01" in text
        assert "Ubuntu" in text
        assert "30 days" in text
        assert "is_error" not in result

    def test_error_returns_error_response(self, mock_connector):
        """Connector errors should return error response."""
        from src.mcp_servers.ssh import get_remote_system_info

        mock_connector.get_system_info = AsyncMock(
            side_effect=SSHConnectorError("Connection failed")
        )

        result = _run(get_remote_system_info.handler({}))

        assert result.get("is_error") is True


# ===========================================================================
# 5. list_remote_processes
# ===========================================================================


class TestListRemoteProcesses:
    """list_remote_processes MCP tool tests."""

    def test_successful_listing(self, mock_connector):
        """Should format process listing."""
        from src.mcp_servers.ssh import list_remote_processes

        mock_connector.list_processes = AsyncMock(return_value={
            "processes": "USER PID %CPU %MEM\nroot 1 0.5 0.1 /sbin/init\n",
            "filter": None,
        })

        result = _run(list_remote_processes.handler({}))

        text = result["content"][0]["text"]
        assert "Running Processes" in text
        assert "/sbin/init" in text

    def test_with_filter(self, mock_connector):
        """Filter should be shown in header."""
        from src.mcp_servers.ssh import list_remote_processes

        mock_connector.list_processes = AsyncMock(return_value={
            "processes": "python3 /app/main.py\n",
            "filter": "python",
        })

        result = _run(list_remote_processes.handler({"filter_pattern": "python"}))

        text = result["content"][0]["text"]
        assert "python" in text

    def test_top_n_passed(self, mock_connector):
        """top_n should be forwarded."""
        from src.mcp_servers.ssh import list_remote_processes

        mock_connector.list_processes = AsyncMock(return_value={
            "processes": "", "filter": None,
        })

        _run(list_remote_processes.handler({"top_n": 5}))

        mock_connector.list_processes.assert_called_once_with(
            filter_pattern=None, top_n=5,
        )

    def test_error_returns_error_response(self, mock_connector):
        """Connector errors should return error response."""
        from src.mcp_servers.ssh import list_remote_processes

        mock_connector.list_processes = AsyncMock(
            side_effect=SSHConnectorError("Processes error")
        )

        result = _run(list_remote_processes.handler({}))

        assert result.get("is_error") is True


# ===========================================================================
# 6. tail_remote_log
# ===========================================================================


class TestTailRemoteLog:
    """tail_remote_log MCP tool tests."""

    def test_successful_tail(self, mock_connector):
        """Should format log output."""
        from src.mcp_servers.ssh import tail_remote_log

        mock_connector.tail_log = AsyncMock(return_value={
            "content": "2024-01-01 ERROR timeout\n2024-01-01 INFO ok\n",
            "path": "/var/log/app.log",
            "grep_pattern": None,
        })

        result = _run(tail_remote_log.handler({"path": "/var/log/app.log"}))

        text = result["content"][0]["text"]
        assert "/var/log/app.log" in text
        assert "ERROR timeout" in text
        assert "is_error" not in result

    def test_with_grep_pattern(self, mock_connector):
        """grep_pattern should be shown in header."""
        from src.mcp_servers.ssh import tail_remote_log

        mock_connector.tail_log = AsyncMock(return_value={
            "content": "2024-01-01 ERROR timeout\n",
            "path": "/var/log/app.log",
            "grep_pattern": "ERROR",
        })

        result = _run(tail_remote_log.handler({
            "path": "/var/log/app.log",
            "grep_pattern": "ERROR",
        }))

        text = result["content"][0]["text"]
        assert "ERROR" in text

    def test_empty_content_shows_no_matching(self, mock_connector):
        """Empty content should show no matching lines message."""
        from src.mcp_servers.ssh import tail_remote_log

        mock_connector.tail_log = AsyncMock(return_value={
            "content": "",
            "path": "/var/log/app.log",
            "grep_pattern": "NOTFOUND",
        })

        result = _run(tail_remote_log.handler({
            "path": "/var/log/app.log",
            "grep_pattern": "NOTFOUND",
        }))

        text = result["content"][0]["text"]
        assert "no matching" in text.lower()

    def test_empty_path_returns_error(self, mock_connector):
        """Empty path should return error response."""
        from src.mcp_servers.ssh import tail_remote_log

        result = _run(tail_remote_log.handler({"path": ""}))

        assert result.get("is_error") is True

    def test_missing_path_returns_error(self, mock_connector):
        """Missing path key should return error response."""
        from src.mcp_servers.ssh import tail_remote_log

        result = _run(tail_remote_log.handler({}))

        assert result.get("is_error") is True

    def test_lines_passed(self, mock_connector):
        """lines parameter should be forwarded."""
        from src.mcp_servers.ssh import tail_remote_log

        mock_connector.tail_log = AsyncMock(return_value={
            "content": "log", "path": "/var/log/app.log", "grep_pattern": None,
        })

        _run(tail_remote_log.handler({"path": "/var/log/app.log", "lines": 200}))

        mock_connector.tail_log.assert_called_once_with(
            path="/var/log/app.log", lines=200, grep_pattern=None,
        )

    def test_error_returns_error_response(self, mock_connector):
        """Connector errors should return error response."""
        from src.mcp_servers.ssh import tail_remote_log

        mock_connector.tail_log = AsyncMock(
            side_effect=SSHConnectorError("Log failed")
        )

        result = _run(tail_remote_log.handler({"path": "/var/log/app.log"}))

        assert result.get("is_error") is True


# ===========================================================================
# create_ssh_tools
# ===========================================================================


class TestCreateSshTools:
    """create_ssh_tools convenience function."""

    def test_returns_all_tool_names(self):
        """Should return a list of all 6 tool names."""
        from src.mcp_servers.ssh import create_ssh_tools

        names = create_ssh_tools()
        assert len(names) == 6
        assert "run_remote_command" in names
        assert "read_remote_file" in names
        assert "list_remote_directory" in names
        assert "get_remote_system_info" in names
        assert "list_remote_processes" in names
        assert "tail_remote_log" in names
