"""MCP client wrapper for connecting to external plugin MCP servers.

Manages the lifecycle of an MCP server subprocess: spawn, initialize
handshake, tool discovery, tool invocation, and graceful shutdown.

Uses the ``mcp`` SDK's stdio client (already a Sidera dependency) for
process management and JSON-RPC communication.
"""

from __future__ import annotations

import os
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Environment variable expansion
# ---------------------------------------------------------------------------

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _expand_env(value: str, plugin_dir: str) -> str:
    """Expand ``${VAR}`` references in a string.

    ``${CLAUDE_PLUGIN_ROOT}`` resolves to *plugin_dir*.
    All other ``${VAR}`` references resolve to ``os.environ``.
    Missing variables resolve to the empty string.
    """

    def _replacer(match: re.Match) -> str:
        var = match.group(1)
        if var == "CLAUDE_PLUGIN_ROOT":
            return plugin_dir
        return os.environ.get(var, "")

    return _ENV_VAR_RE.sub(_replacer, value)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MCPServerConfig:
    """Configuration for a single MCP server from ``.mcp.json``."""

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""


@dataclass
class MCPServerConnection:
    """A live connection to an MCP server process."""

    config: MCPServerConfig
    plugin_name: str = ""
    session: Any = None  # mcp.ClientSession — typed as Any to allow None
    tools: list[dict[str, Any]] = field(default_factory=list)
    is_connected: bool = False
    _exit_stack: AsyncExitStack | None = None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_mcp_json(
    mcp_json: dict[str, Any],
    plugin_dir: str,
) -> list[MCPServerConfig]:
    """Parse a ``.mcp.json`` dict into :class:`MCPServerConfig` objects.

    Environment variables (``${VAR}``) in ``command``, ``args``, ``env``,
    and ``cwd`` are expanded.
    """
    servers_dict = mcp_json.get("mcpServers", {})
    configs: list[MCPServerConfig] = []
    for name, server in servers_dict.items():
        command = _expand_env(server.get("command", ""), plugin_dir)
        args = tuple(_expand_env(a, plugin_dir) for a in server.get("args", []))
        raw_env = server.get("env", {})
        env = {k: _expand_env(v, plugin_dir) for k, v in raw_env.items()}
        cwd = _expand_env(server.get("cwd", plugin_dir), plugin_dir)
        configs.append(
            MCPServerConfig(
                name=name,
                command=command,
                args=args,
                env=env,
                cwd=cwd,
            )
        )
    return configs


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


async def connect_mcp_server(
    config: MCPServerConfig,
    plugin_name: str,
) -> MCPServerConnection:
    """Spawn an MCP server process and connect to it.

    Uses the MCP SDK's :func:`stdio_client` to manage the subprocess and
    :class:`ClientSession` for the JSON-RPC handshake.  On failure returns
    an :class:`MCPServerConnection` with ``is_connected=False``.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    conn = MCPServerConnection(config=config, plugin_name=plugin_name)

    # Merge current env with plugin-specific overrides
    merged_env = {**os.environ, **config.env}

    server_params = StdioServerParameters(
        command=config.command,
        args=list(config.args),
        env=merged_env,
        cwd=config.cwd or None,
    )

    exit_stack = AsyncExitStack()
    try:
        transport = await exit_stack.enter_async_context(stdio_client(server_params))
        read_stream, write_stream = transport

        session = await exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
        await session.initialize()

        # Discover tools
        response = await session.list_tools()
        tools = [
            {
                "name": tool.name,
                "description": getattr(tool, "description", "") or "",
                "inputSchema": getattr(tool, "inputSchema", {}),
            }
            for tool in response.tools
        ]

        conn.session = session
        conn.tools = tools
        conn.is_connected = True
        conn._exit_stack = exit_stack

        logger.info(
            "mcp_client.connected",
            server=config.name,
            plugin=plugin_name,
            tool_count=len(tools),
            tool_names=[t["name"] for t in tools],
        )
        return conn

    except Exception as exc:
        logger.error(
            "mcp_client.connection_failed",
            server=config.name,
            plugin=plugin_name,
            error=str(exc),
        )
        # Clean up any partial state
        try:
            await exit_stack.aclose()
        except Exception:
            pass
        return conn


async def disconnect_mcp_server(conn: MCPServerConnection) -> None:
    """Gracefully shut down an MCP server connection."""
    if conn._exit_stack is not None:
        try:
            await conn._exit_stack.aclose()
        except Exception as exc:
            logger.warning(
                "mcp_client.disconnect_error",
                server=conn.config.name,
                error=str(exc),
            )
    conn.is_connected = False
    conn.session = None
    conn._exit_stack = None
    logger.info("mcp_client.disconnected", server=conn.config.name)
