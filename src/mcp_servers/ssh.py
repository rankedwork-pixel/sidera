"""SSH MCP tools for Sidera.

Provides 6 tools that agents can use to execute commands and inspect
remote servers via SSH.

Tools:
    1. run_remote_command       - Execute a shell command on the remote server
    2. read_remote_file         - Read a file from the remote server
    3. list_remote_directory    - List files in a remote directory
    4. get_remote_system_info   - Get system info (hostname, OS, disk, memory)
    5. list_remote_processes    - List running processes
    6. tail_remote_log          - Tail a log file with optional grep filtering

Security:
    All commands pass through the SSH connector's safety filter which blocks
    destructive operations (rm -rf, mkfs, shutdown, etc.).  Output is truncated
    to prevent memory overflow.  All operations are audit-logged.

Usage:
    from src.mcp_servers.ssh import create_ssh_tools

    tools = create_ssh_tools()
    # These are registered globally via @tool decorator.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.agent.tool_registry import tool
from src.mcp_servers.helpers import error_response, text_response

logger = structlog.get_logger(__name__)


def _get_connector() -> Any:
    """Lazy-load the SSH connector."""
    from src.connectors.ssh import SSHConnector

    return SSHConnector()


# ---------------------------------------------------------------------------
# Tool 1: Run remote command
# ---------------------------------------------------------------------------

RUN_COMMAND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": (
                "The shell command to execute on the remote server. "
                "Destructive commands (rm -rf, mkfs, shutdown, etc.) are blocked."
            ),
        },
        "timeout": {
            "type": "integer",
            "description": "Max execution time in seconds (default 30, max 300).",
            "default": 30,
        },
        "working_dir": {
            "type": "string",
            "description": "Optional working directory for the command.",
        },
    },
    "required": ["command"],
}


@tool(
    name="run_remote_command",
    description=(
        "Execute a shell command on the remote server via SSH. "
        "Returns stdout, stderr, and exit code. Dangerous commands are blocked. "
        "Use for: checking logs, running diagnostics, restarting safe services, "
        "querying databases, inspecting infrastructure."
    ),
    input_schema=RUN_COMMAND_SCHEMA,
)
async def run_remote_command(args: dict[str, Any]) -> dict[str, Any]:
    """Execute a command on the remote server."""
    command = args.get("command", "").strip()
    if not command:
        return error_response("command is required")

    timeout = args.get("timeout", 30)
    working_dir = args.get("working_dir")

    connector = _get_connector()
    try:
        result = await connector.run_command(
            command=command,
            timeout=timeout,
            working_dir=working_dir,
        )

        parts = []
        if result["stdout"]:
            parts.append(f"**stdout:**\n```\n{result['stdout']}\n```")
        if result["stderr"]:
            parts.append(f"**stderr:**\n```\n{result['stderr']}\n```")
        parts.append(f"**Exit code:** {result['exit_code']}")
        if result["timed_out"]:
            parts.append("⚠️ **Command timed out**")

        return text_response("\n\n".join(parts))

    except Exception as exc:
        logger.error("ssh_tool.run_command_error", command=command[:100], error=str(exc))
        return error_response(f"Command failed: {exc}")


# ---------------------------------------------------------------------------
# Tool 2: Read remote file
# ---------------------------------------------------------------------------

READ_FILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Absolute path to the file on the remote server.",
        },
        "max_lines": {
            "type": "integer",
            "description": "Maximum number of lines to read (default 1000, max 10000).",
            "default": 1000,
        },
        "tail": {
            "type": "boolean",
            "description": "If true, read the last N lines instead of first N.",
            "default": False,
        },
    },
    "required": ["path"],
}


@tool(
    name="read_remote_file",
    description=(
        "Read a file from the remote server. Returns the file content with "
        "line count and truncation info. Supports reading from the head or "
        "tail of the file."
    ),
    input_schema=READ_FILE_SCHEMA,
)
async def read_remote_file(args: dict[str, Any]) -> dict[str, Any]:
    """Read a file from the remote server."""
    path = args.get("path", "").strip()
    if not path:
        return error_response("path is required")

    max_lines = args.get("max_lines", 1000)
    tail = args.get("tail", False)

    connector = _get_connector()
    try:
        result = await connector.read_file(
            path=path,
            max_lines=max_lines,
            tail=tail,
        )

        header = f"**File:** `{result['path']}`"
        if result["truncated"]:
            header += (
                f"\n⚠️ Showing {'last' if tail else 'first'} {result['lines']} "
                f"of {result['total_lines']} total lines"
            )

        return text_response(f"{header}\n\n```\n{result['content']}\n```")

    except Exception as exc:
        logger.error("ssh_tool.read_file_error", path=path, error=str(exc))
        return error_response(f"Failed to read file: {exc}")


# ---------------------------------------------------------------------------
# Tool 3: List remote directory
# ---------------------------------------------------------------------------

LIST_DIR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Directory path to list (default: /).",
            "default": "/",
        },
        "show_hidden": {
            "type": "boolean",
            "description": "Include hidden files (default: false).",
            "default": False,
        },
    },
    "required": [],
}


@tool(
    name="list_remote_directory",
    description=(
        "List files and directories on the remote server. "
        "Returns a detailed listing with permissions, sizes, and dates."
    ),
    input_schema=LIST_DIR_SCHEMA,
)
async def list_remote_directory(args: dict[str, Any]) -> dict[str, Any]:
    """List files in a remote directory."""
    path = args.get("path", "/").strip() or "/"
    show_hidden = args.get("show_hidden", False)

    connector = _get_connector()
    try:
        result = await connector.list_directory(
            path=path,
            show_hidden=show_hidden,
        )

        return text_response(
            f"**Directory:** `{result['path']}`\n\n```\n{result['entries']}\n```"
        )

    except Exception as exc:
        logger.error("ssh_tool.list_dir_error", path=path, error=str(exc))
        return error_response(f"Failed to list directory: {exc}")


# ---------------------------------------------------------------------------
# Tool 4: Get remote system info
# ---------------------------------------------------------------------------

SYSTEM_INFO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
}


@tool(
    name="get_remote_system_info",
    description=(
        "Get system information from the remote server: hostname, OS version, "
        "uptime, load average, disk usage, and memory usage. "
        "Use this for health checks and diagnostics."
    ),
    input_schema=SYSTEM_INFO_SCHEMA,
)
async def get_remote_system_info(args: dict[str, Any]) -> dict[str, Any]:
    """Get system information from the remote server."""
    connector = _get_connector()
    try:
        info = await connector.get_system_info()

        lines = [
            "# Remote Server Info\n",
            f"**Hostname:** {info.get('hostname', 'N/A')}",
            f"**OS:** {info.get('os', 'N/A')}",
            f"**Uptime:** {info.get('uptime', 'N/A')}",
            f"**Load Average:** {info.get('load', 'N/A')}",
            f"**Disk (/):** {info.get('disk', 'N/A')}",
            f"**Memory:**\n```\n{info.get('memory', 'N/A')}\n```",
        ]

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error("ssh_tool.system_info_error", error=str(exc))
        return error_response(f"Failed to get system info: {exc}")


# ---------------------------------------------------------------------------
# Tool 5: List remote processes
# ---------------------------------------------------------------------------

LIST_PROCESSES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "filter_pattern": {
            "type": "string",
            "description": "Optional grep pattern to filter processes (e.g., 'python', 'nginx').",
        },
        "top_n": {
            "type": "integer",
            "description": "Number of top processes to return (default 20, max 100).",
            "default": 20,
        },
    },
    "required": [],
}


@tool(
    name="list_remote_processes",
    description=(
        "List running processes on the remote server, sorted by CPU usage. "
        "Optionally filter by process name or pattern. "
        "Use for diagnosing high CPU/memory usage or finding specific services."
    ),
    input_schema=LIST_PROCESSES_SCHEMA,
)
async def list_remote_processes(args: dict[str, Any]) -> dict[str, Any]:
    """List running processes on the remote server."""
    filter_pattern = args.get("filter_pattern")
    top_n = args.get("top_n", 20)

    connector = _get_connector()
    try:
        result = await connector.list_processes(
            filter_pattern=filter_pattern,
            top_n=top_n,
        )

        header = "# Running Processes"
        if result.get("filter"):
            header += f" (filter: `{result['filter']}`)"

        return text_response(f"{header}\n\n```\n{result['processes']}\n```")

    except Exception as exc:
        logger.error("ssh_tool.list_processes_error", error=str(exc))
        return error_response(f"Failed to list processes: {exc}")


# ---------------------------------------------------------------------------
# Tool 6: Tail remote log
# ---------------------------------------------------------------------------

TAIL_LOG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Absolute path to the log file.",
        },
        "lines": {
            "type": "integer",
            "description": "Number of lines to tail (default 100, max 5000).",
            "default": 100,
        },
        "grep_pattern": {
            "type": "string",
            "description": "Optional pattern to filter log lines (e.g., 'ERROR', 'timeout').",
        },
    },
    "required": ["path"],
}


@tool(
    name="tail_remote_log",
    description=(
        "Tail a log file on the remote server with optional grep filtering. "
        "Returns the last N lines, optionally filtered by a pattern. "
        "Use for investigating errors, monitoring application logs, "
        "or checking recent activity."
    ),
    input_schema=TAIL_LOG_SCHEMA,
)
async def tail_remote_log(args: dict[str, Any]) -> dict[str, Any]:
    """Tail a log file on the remote server."""
    path = args.get("path", "").strip()
    if not path:
        return error_response("path is required")

    lines = args.get("lines", 100)
    grep_pattern = args.get("grep_pattern")

    connector = _get_connector()
    try:
        result = await connector.tail_log(
            path=path,
            lines=lines,
            grep_pattern=grep_pattern,
        )

        header = f"**Log:** `{result['path']}`"
        if result.get("grep_pattern"):
            header += f" (filter: `{result['grep_pattern']}`)"

        content = result["content"] or "(no matching lines)"
        return text_response(f"{header}\n\n```\n{content}\n```")

    except Exception as exc:
        logger.error("ssh_tool.tail_log_error", path=path, error=str(exc))
        return error_response(f"Failed to tail log: {exc}")


# ---------------------------------------------------------------------------
# Convenience function for registration
# ---------------------------------------------------------------------------


def create_ssh_tools() -> list[str]:
    """Return the names of all SSH tools (registered via @tool decorator)."""
    return [
        "run_remote_command",
        "read_remote_file",
        "list_remote_directory",
        "get_remote_system_info",
        "list_remote_processes",
        "tail_remote_log",
    ]
