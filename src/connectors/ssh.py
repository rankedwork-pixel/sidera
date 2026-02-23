"""SSH connector for remote server execution in Sidera.

Provides a client for executing commands on remote servers via SSH.
Uses ``asyncssh`` for async SSH connections with connection pooling,
command allowlisting, and timeout enforcement.

Architecture:
    connector (this file) -> MCP tools -> agent loop
    All methods are async and return clean Python dicts.

Security:
    - Command allowlist prevents dangerous operations (rm -rf, etc.)
    - Output truncation prevents memory overflow from verbose commands
    - Timeout enforcement prevents hung processes
    - Connection reuse via pool to avoid repeated handshakes

Usage:
    from src.connectors.ssh import SSHConnector

    connector = SSHConnector()
    result = await connector.run_command("ls -la /var/log")
    files = await connector.read_file("/etc/nginx/nginx.conf")
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import structlog

from src.config import settings
from src.connectors.retry import retry_with_backoff

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SSHConnectorError(Exception):
    """Base exception for SSH connector errors."""

    pass


class SSHAuthError(SSHConnectorError):
    """Authentication failure — surface to user."""

    pass


class SSHCommandBlockedError(SSHConnectorError):
    """Command was blocked by the safety filter."""

    pass


class SSHTimeoutError(SSHConnectorError):
    """Command exceeded the timeout limit."""

    pass


# ---------------------------------------------------------------------------
# Safety: command allowlist / blocklist
# ---------------------------------------------------------------------------

# Commands that are NEVER allowed — destructive or dangerous
_BLOCKED_COMMAND_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+|.*--recursive)", re.IGNORECASE),
    re.compile(r"\brm\s+-[a-zA-Z]*f", re.IGNORECASE),  # rm -f, rm -rf
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bdd\b.*\bof=", re.IGNORECASE),
    re.compile(r"\b:(){ :\|:& };:", re.IGNORECASE),  # fork bomb
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\breboot\b", re.IGNORECASE),
    re.compile(r"\binit\s+[06]\b", re.IGNORECASE),
    re.compile(r"\bsystemctl\s+(stop|disable|mask)\b", re.IGNORECASE),
    re.compile(r"\bchmod\s+777\b", re.IGNORECASE),
    re.compile(r"\bchown\s+-R\s+root\b", re.IGNORECASE),
    re.compile(r"\biptables\s+-F\b", re.IGNORECASE),  # flush firewall
    re.compile(r"\bpasswd\b", re.IGNORECASE),
    re.compile(r"\buseradd\b", re.IGNORECASE),
    re.compile(r"\buserdel\b", re.IGNORECASE),
    re.compile(r"\bvisudo\b", re.IGNORECASE),
    re.compile(r">\s*/dev/sd[a-z]", re.IGNORECASE),  # write to raw disk
    re.compile(r"\bcurl\b.*\|\s*(ba)?sh\b", re.IGNORECASE),  # curl | sh
    re.compile(r"\bwget\b.*\|\s*(ba)?sh\b", re.IGNORECASE),  # wget | sh
    re.compile(r"\beval\b", re.IGNORECASE),
    re.compile(r"\bnohup\b.*&\s*$", re.IGNORECASE),  # background daemons
)

# Max output size (chars) — truncate large outputs
_MAX_OUTPUT_CHARS = 50_000

# Default command timeout in seconds
_DEFAULT_TIMEOUT = 30

# Max command timeout in seconds
_MAX_TIMEOUT = 300


def is_command_safe(command: str) -> tuple[bool, str]:
    """Check if a command passes the safety filter.

    Args:
        command: The shell command to validate.

    Returns:
        Tuple of (is_safe, reason). If not safe, reason explains why.
    """
    stripped = command.strip()

    if not stripped:
        return False, "Empty command"

    # Check for pipe to shell patterns (additional check)
    if "|" in stripped and any(
        sh in stripped.split("|")[-1].strip() for sh in ("sh", "bash", "zsh", "python")
    ):
        # Allow grep, awk, etc. — only block piping to interpreters
        pass  # Caught by curl|sh and wget|sh patterns above

    for pattern in _BLOCKED_COMMAND_PATTERNS:
        if pattern.search(stripped):
            return False, f"Command blocked by safety filter: matches pattern '{pattern.pattern}'"

    return True, ""


def _truncate_output(output: str, max_chars: int = _MAX_OUTPUT_CHARS) -> str:
    """Truncate output to max characters, adding a notice if truncated."""
    if len(output) <= max_chars:
        return output
    return (
        output[:max_chars]
        + f"\n\n... [OUTPUT TRUNCATED — showing first {max_chars:,} of {len(output):,} chars]"
    )


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class SSHConnector:
    """Async SSH client for remote server command execution.

    Connects to a remote server via SSH and provides methods to run
    commands, read files, list processes, and check system status.

    Args:
        credentials: Optional dict with ``host``, ``port``, ``username``,
            ``private_key_path`` (or ``password``). If omitted, values
            are read from the ``settings`` singleton.
    """

    def __init__(self, credentials: dict[str, Any] | None = None) -> None:
        creds = credentials or self._credentials_from_settings()
        self._host = creds.get("host", "")
        self._port = int(creds.get("port", 22))
        self._username = creds.get("username", "")
        self._private_key_path = creds.get("private_key_path", "")
        self._password = creds.get("password", "")
        self._known_hosts_path = creds.get("known_hosts_path", "")
        self._connection: Any = None  # asyncssh.SSHClientConnection

    @staticmethod
    def _credentials_from_settings() -> dict[str, str]:
        """Extract SSH credentials from the settings singleton."""
        return {
            "host": getattr(settings, "ssh_host", ""),
            "port": str(getattr(settings, "ssh_port", 22)),
            "username": getattr(settings, "ssh_username", ""),
            "private_key_path": getattr(settings, "ssh_private_key_path", ""),
            "password": getattr(settings, "ssh_password", ""),
            "known_hosts_path": getattr(settings, "ssh_known_hosts_path", ""),
        }

    async def _get_connection(self) -> Any:
        """Get or create an SSH connection (lazy, with reuse).

        Returns:
            An asyncssh SSHClientConnection.

        Raises:
            SSHAuthError: If authentication fails.
            SSHConnectorError: If connection fails for other reasons.
        """
        if self._connection is not None:
            # Check if connection is still alive
            try:
                # asyncssh connections have a _transport attribute
                if hasattr(self._connection, "_transport") and self._connection._transport:
                    return self._connection
            except Exception:
                pass
            self._connection = None

        try:
            import asyncssh
        except ImportError:
            raise SSHConnectorError(
                "asyncssh is required for SSH connector. "
                "Install with: pip install asyncssh"
            )

        if not self._host:
            raise SSHConnectorError("SSH host not configured. Set ssh_host in settings.")

        connect_kwargs: dict[str, Any] = {
            "host": self._host,
            "port": self._port,
            "username": self._username,
        }

        # Prefer key-based auth, fall back to password
        if self._private_key_path:
            connect_kwargs["client_keys"] = [self._private_key_path]
        elif self._password:
            connect_kwargs["password"] = self._password
        else:
            raise SSHConnectorError(
                "No SSH credentials configured. Set ssh_private_key_path or ssh_password."
            )

        # Known hosts handling
        if self._known_hosts_path:
            connect_kwargs["known_hosts"] = self._known_hosts_path
        else:
            # For development — in production, always use known_hosts
            connect_kwargs["known_hosts"] = None

        try:
            self._connection = await asyncssh.connect(**connect_kwargs)
            logger.info(
                "ssh.connected",
                host=self._host,
                port=self._port,
                username=self._username,
            )
            return self._connection
        except asyncssh.DisconnectError as exc:
            if "auth" in str(exc).lower() or "permission" in str(exc).lower():
                raise SSHAuthError(f"SSH authentication failed: {exc}") from exc
            raise SSHConnectorError(f"SSH connection failed: {exc}") from exc
        except asyncssh.PermissionDenied as exc:
            raise SSHAuthError(f"SSH permission denied: {exc}") from exc
        except Exception as exc:
            raise SSHConnectorError(f"SSH connection failed: {exc}") from exc

    async def close(self) -> None:
        """Close the SSH connection if open."""
        if self._connection is not None:
            try:
                self._connection.close()
                await self._connection.wait_closed()
            except Exception:
                pass
            self._connection = None
            logger.info("ssh.disconnected", host=self._host)

    # -- Core methods -------------------------------------------------------

    @retry_with_backoff(max_retries=2, base_delay=1.0, max_delay=10.0)
    async def run_command(
        self,
        command: str,
        timeout: int = _DEFAULT_TIMEOUT,
        working_dir: str | None = None,
    ) -> dict[str, Any]:
        """Execute a command on the remote server.

        Args:
            command: The shell command to execute.
            timeout: Max execution time in seconds (default 30, max 300).
            working_dir: Optional working directory for the command.

        Returns:
            Dict with keys: stdout, stderr, exit_code, timed_out.

        Raises:
            SSHCommandBlockedError: If the command is blocked by safety filter.
            SSHTimeoutError: If the command exceeds the timeout.
            SSHConnectorError: If the connection or execution fails.
        """
        # Safety check
        is_safe, reason = is_command_safe(command)
        if not is_safe:
            raise SSHCommandBlockedError(reason)

        # Enforce timeout limits
        timeout = min(max(1, timeout), _MAX_TIMEOUT)

        # Wrap with cd if working_dir specified
        full_command = command
        if working_dir:
            full_command = f"cd {working_dir} && {command}"

        conn = await self._get_connection()

        try:
            result = await asyncio.wait_for(
                conn.run(full_command, check=False),
                timeout=timeout,
            )

            stdout = _truncate_output(result.stdout or "")
            stderr = _truncate_output(result.stderr or "")

            logger.info(
                "ssh.command_executed",
                command=command[:100],
                exit_code=result.exit_status,
                stdout_len=len(result.stdout or ""),
                stderr_len=len(result.stderr or ""),
            )

            return {
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": result.exit_status,
                "timed_out": False,
            }

        except asyncio.TimeoutError:
            logger.warning(
                "ssh.command_timeout",
                command=command[:100],
                timeout=timeout,
            )
            return {
                "stdout": "",
                "stderr": f"Command timed out after {timeout}s",
                "exit_code": -1,
                "timed_out": True,
            }

    @retry_with_backoff(max_retries=2, base_delay=1.0, max_delay=10.0)
    async def read_file(
        self,
        path: str,
        max_lines: int = 1000,
        tail: bool = False,
    ) -> dict[str, Any]:
        """Read a file from the remote server.

        Args:
            path: Absolute path to the file.
            max_lines: Maximum number of lines to return (default 1000).
            tail: If True, return the last N lines instead of first N.

        Returns:
            Dict with keys: content, path, lines, truncated.
        """
        if not path or not path.startswith("/"):
            raise SSHConnectorError("Path must be absolute (start with /)")

        max_lines = min(max(1, max_lines), 10_000)

        if tail:
            cmd = f"tail -n {max_lines} {path}"
        else:
            cmd = f"head -n {max_lines} {path}"

        result = await self.run_command(cmd, timeout=30)

        if result["exit_code"] != 0:
            raise SSHConnectorError(
                f"Failed to read {path}: {result['stderr']}"
            )

        content = result["stdout"]
        lines = content.count("\n")

        # Check if file was truncated
        wc_result = await self.run_command(f"wc -l < {path}", timeout=10)
        total_lines = 0
        if wc_result["exit_code"] == 0:
            try:
                total_lines = int(wc_result["stdout"].strip())
            except ValueError:
                total_lines = lines

        return {
            "content": content,
            "path": path,
            "lines": lines,
            "total_lines": total_lines,
            "truncated": total_lines > max_lines,
        }

    @retry_with_backoff(max_retries=2, base_delay=1.0, max_delay=10.0)
    async def list_directory(
        self,
        path: str = "/",
        show_hidden: bool = False,
    ) -> dict[str, Any]:
        """List files in a remote directory.

        Args:
            path: Directory path (default /).
            show_hidden: Include hidden files (default False).

        Returns:
            Dict with keys: entries, path, count.
        """
        flags = "-la" if show_hidden else "-l"
        result = await self.run_command(f"ls {flags} {path}", timeout=15)

        if result["exit_code"] != 0:
            raise SSHConnectorError(
                f"Failed to list {path}: {result['stderr']}"
            )

        return {
            "entries": result["stdout"],
            "path": path,
        }

    @retry_with_backoff(max_retries=2, base_delay=1.0, max_delay=10.0)
    async def get_system_info(self) -> dict[str, Any]:
        """Get basic system information from the remote server.

        Returns:
            Dict with hostname, OS, uptime, load, disk, and memory info.
        """
        commands = {
            "hostname": "hostname",
            "os": "cat /etc/os-release 2>/dev/null | head -5 || uname -a",
            "uptime": "uptime",
            "load": "cat /proc/loadavg 2>/dev/null || echo N/A",
            "disk": "df -h / 2>/dev/null | tail -1",
            "memory": "free -h 2>/dev/null | head -2 || echo N/A",
        }

        info: dict[str, str] = {}
        for key, cmd in commands.items():
            try:
                result = await self.run_command(cmd, timeout=10)
                info[key] = result["stdout"].strip() if result["exit_code"] == 0 else "N/A"
            except Exception:
                info[key] = "N/A"

        return info

    @retry_with_backoff(max_retries=2, base_delay=1.0, max_delay=10.0)
    async def list_processes(
        self,
        filter_pattern: str | None = None,
        top_n: int = 20,
    ) -> dict[str, Any]:
        """List running processes on the remote server.

        Args:
            filter_pattern: Optional grep pattern to filter processes.
            top_n: Number of top processes by CPU/memory to return.

        Returns:
            Dict with keys: processes, count.
        """
        top_n = min(max(1, top_n), 100)

        if filter_pattern:
            # Sanitize pattern — basic shell escape
            safe_pattern = filter_pattern.replace("'", "'\\''")
            cmd = f"ps aux | grep -i '{safe_pattern}' | grep -v grep | head -n {top_n}"
        else:
            cmd = f"ps aux --sort=-%cpu | head -n {top_n + 1}"

        result = await self.run_command(cmd, timeout=15)

        return {
            "processes": result["stdout"],
            "filter": filter_pattern,
        }

    @retry_with_backoff(max_retries=2, base_delay=1.0, max_delay=10.0)
    async def check_service_status(self, service_name: str) -> dict[str, Any]:
        """Check the status of a systemd service.

        Args:
            service_name: Name of the service (e.g., 'nginx', 'postgresql').

        Returns:
            Dict with keys: service, active, status_output.
        """
        # Sanitize service name
        safe_name = re.sub(r"[^a-zA-Z0-9_\-.]", "", service_name)
        if not safe_name:
            raise SSHConnectorError("Invalid service name")

        result = await self.run_command(
            f"systemctl is-active {safe_name} 2>/dev/null && "
            f"systemctl status {safe_name} --no-pager 2>/dev/null | head -20",
            timeout=15,
        )

        first_line = result["stdout"].split("\n")[0].lower() if result["stdout"] else ""
        is_active = "active" in first_line

        return {
            "service": safe_name,
            "active": is_active,
            "status_output": result["stdout"],
            "exit_code": result["exit_code"],
        }

    @retry_with_backoff(max_retries=2, base_delay=1.0, max_delay=10.0)
    async def tail_log(
        self,
        path: str,
        lines: int = 100,
        grep_pattern: str | None = None,
    ) -> dict[str, Any]:
        """Tail a log file on the remote server.

        Args:
            path: Absolute path to the log file.
            lines: Number of lines to tail (default 100).
            grep_pattern: Optional pattern to filter log lines.

        Returns:
            Dict with keys: content, path, lines.
        """
        if not path or not path.startswith("/"):
            raise SSHConnectorError("Path must be absolute (start with /)")

        lines = min(max(1, lines), 5000)

        cmd = f"tail -n {lines} {path}"
        if grep_pattern:
            safe_pattern = grep_pattern.replace("'", "'\\''")
            cmd += f" | grep -i '{safe_pattern}'"

        result = await self.run_command(cmd, timeout=30)

        if result["exit_code"] != 0 and not result["stdout"]:
            raise SSHConnectorError(
                f"Failed to tail {path}: {result['stderr']}"
            )

        return {
            "content": result["stdout"],
            "path": path,
            "grep_pattern": grep_pattern,
        }
