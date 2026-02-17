"""Sidera MCP stdio server — bridges Sidera tools to Claude Code.

Exposes 36 stateless tools directly from the ``ToolRegistry`` plus
7 meta-tools (``talk_to_role``, ``run_role``, ``list_roles``,
``review_pending_approvals``, ``decide_approval``,
``run_claude_code_task``, ``orchestrate``) via the MCP protocol
over stdio transport.

Usage::

    python -m src.mcp_stdio          # start the server
    Ctrl-C                           # stop

Claude Code configuration (``.mcp.json``)::

    {
        "mcpServers": {
            "sidera": {
                "type": "stdio",
                "command": "python",
                "args": ["-m", "src.mcp_stdio"],
                "cwd": "/path/to/Build an Agent"
            }
        }
    }

CRITICAL: stdout is reserved for JSON-RPC protocol messages.  ALL
application logging MUST go to stderr.
"""

from __future__ import annotations

import logging
import sys

# ---------------------------------------------------------------------------
# Redirect ALL logging to stderr BEFORE any Sidera imports.
# This is critical — stdout is the JSON-RPC transport channel.
# ---------------------------------------------------------------------------
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    force=True,
)

# Redirect structlog to stderr
import structlog  # noqa: E402

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
)

# ---------------------------------------------------------------------------
# Now safe to import Sidera modules (they use structlog)
# ---------------------------------------------------------------------------

# Trigger @tool registration for all MCP server modules
from mcp.server import Server  # noqa: E402, I001
from mcp.server.stdio import stdio_server  # noqa: E402
from mcp.types import TextContent  # noqa: E402
from mcp.types import Tool as MCPTool  # noqa: E402

import src.mcp_servers.actions  # noqa: F401, E402
import src.mcp_servers.bigquery  # noqa: F401, E402
import src.mcp_servers.claude_code_actions  # noqa: F401, E402
import src.mcp_servers.context  # noqa: F401, E402
import src.mcp_servers.delegation  # noqa: F401, E402
import src.mcp_servers.evolution  # noqa: F401, E402
import src.mcp_servers.google_ads  # noqa: F401, E402
import src.mcp_servers.google_drive  # noqa: F401, E402
import src.mcp_servers.meeting  # noqa: F401, E402
import src.mcp_servers.memory  # noqa: F401, E402
import src.mcp_servers.messaging  # noqa: F401, E402
import src.mcp_servers.meta  # noqa: F401, E402
import src.mcp_servers.slack  # noqa: F401, E402
import src.mcp_servers.system  # noqa: F401, E402
from src.agent.tool_registry import get_global_registry  # noqa: E402
from src.mcp_stdio.bridge import DIRECT_TOOLS  # noqa: E402
from src.mcp_stdio.meta_tools import (  # noqa: E402
    META_TOOL_DEFINITIONS,
    META_TOOL_HANDLERS,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# MCP Server instance
# ---------------------------------------------------------------------------

server = Server("sidera")
registry = get_global_registry()


@server.list_tools()
async def list_tools() -> list[MCPTool]:
    """Return all tools available to Claude Code."""
    tools: list[MCPTool] = []

    # Direct tools from the internal ToolRegistry
    for name in sorted(DIRECT_TOOLS):
        tool_def = registry._tools.get(name)
        if tool_def:
            tools.append(
                MCPTool(
                    name=tool_def.name,
                    description=tool_def.description,
                    inputSchema=tool_def.input_schema,
                )
            )

    # Meta-tools (defined in meta_tools.py)
    tools.extend(META_TOOL_DEFINITIONS)

    return tools


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Dispatch a tool call to the appropriate handler."""
    logger.info("mcp_stdio.call_tool", tool=name)

    if name in DIRECT_TOOLS:
        # Pass through to the internal ToolRegistry
        result_text = await registry.dispatch(name, arguments)
        return [TextContent(type="text", text=result_text)]

    if name in META_TOOL_HANDLERS:
        # Dispatch to the meta-tool handler
        return await META_TOOL_HANDLERS[name](arguments)

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    """Run the Sidera MCP server over stdio."""
    logger.info(
        "mcp_stdio.starting",
        direct_tools=len(DIRECT_TOOLS),
        meta_tools=len(META_TOOL_DEFINITIONS),
    )

    async with stdio_server() as (read_stream, write_stream):
        init_options = server.create_initialization_options()
        await server.run(read_stream, write_stream, init_options)
