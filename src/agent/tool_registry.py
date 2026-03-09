"""Tool registry for Sidera — replaces Claude Agent SDK MCP server pattern.

Provides a ``@tool`` decorator and a ``ToolRegistry`` that manages tool
definitions and dispatching.  Tools are registered at **import time** via
the decorator, so importing an MCP server module automatically registers
all of its tools in the global registry.

Usage::

    # In an MCP server module (e.g. src/mcp_servers/google_ads.py):
    from src.agent.tool_registry import tool

    @tool(
        name="list_google_ads_accounts",
        description="Lists all Google Ads accounts.",
        input_schema={"type": "object", "properties": {}, "required": []},
    )
    async def list_google_ads_accounts(args: dict[str, Any]) -> dict[str, Any]:
        ...

    # In the agent core:
    from src.agent.tool_registry import get_global_registry

    registry = get_global_registry()
    tool_defs = registry.get_tool_definitions()       # Anthropic API format
    result_text = await registry.dispatch("list_google_ads_accounts", {})
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolDefinition:
    """A single tool: metadata + async handler function."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """Central registry of all available tools.

    Tools are stored by name and can be queried in the Anthropic
    ``tools`` parameter format, optionally filtered by an allow-list.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    # -- Registration -------------------------------------------------------

    def register(self, tool_def: ToolDefinition) -> None:
        """Register a tool definition."""
        if tool_def.name in self._tools:
            logger.debug(
                "tool_registry.overwrite",
                name=tool_def.name,
            )
        self._tools[tool_def.name] = tool_def

    # -- Querying -----------------------------------------------------------

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return **all** tools in Anthropic API ``tools`` format."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self._tools.values()
        ]

    def get_filtered_definitions(
        self,
        allowed: list[str] | tuple[str, ...],
    ) -> list[dict[str, Any]]:
        """Return only tools whose names appear in *allowed*."""
        allowed_set = set(allowed)
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self._tools.values()
            if t.name in allowed_set
        ]

    def get_tool_names(self) -> list[str]:
        """Return a sorted list of all registered tool names."""
        return sorted(self._tools.keys())

    def remove(self, name: str) -> bool:
        """Remove a tool by name.  Returns ``True`` if it was present."""
        return self._tools.pop(name, None) is not None

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    # -- Dispatching --------------------------------------------------------

    async def dispatch(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        """Execute a tool and return the result as a string.

        The MCP tool handler convention is to return::

            {"content": [{"type": "text", "text": "..."}], "is_error": False}

        This method extracts the text portion and returns it as a plain
        string suitable for an Anthropic ``tool_result`` message.
        """
        tool_def = self._tools.get(tool_name)
        if not tool_def:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

        try:
            result = await tool_def.handler(tool_input)
            return self._extract_text(result)
        except Exception as e:
            logger.error(
                "tool_registry.dispatch_error",
                tool=tool_name,
                error=str(e),
            )
            return json.dumps({"error": f"Tool '{tool_name}' failed: {e}"})

    @staticmethod
    def _extract_text(result: Any) -> str:
        """Extract text from an MCP-style tool response."""
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            content = result.get("content", [])
            if isinstance(content, list):
                parts = [
                    c["text"] for c in content if isinstance(c, dict) and c.get("type") == "text"
                ]
                if parts:
                    return "\n".join(parts)
            # Fallback: serialize the whole dict
            return json.dumps(result)
        return str(result)


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_global_registry = ToolRegistry()


def get_global_registry() -> ToolRegistry:
    """Return the module-level tool registry singleton."""
    return _global_registry


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def tool(
    *,
    name: str,
    description: str,
    input_schema: dict[str, Any],
) -> Callable:
    """Decorator that registers an async handler in the global tool registry.

    Drop-in replacement for ``claude_agent_sdk.tool``.  The decorated
    function keeps the same signature and can still be called directly
    in tests via ``func(args)`` or ``func.handler(args)``.
    """

    def decorator(
        func: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
        tool_def = ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=func,
        )
        _global_registry.register(tool_def)

        # Attach metadata for backward compatibility with tests that
        # access ``tool_fn.name``, ``tool_fn.handler(args)``, etc.
        func.tool_name = name  # type: ignore[attr-defined]
        func.description = description  # type: ignore[attr-defined]
        func.input_schema = input_schema  # type: ignore[attr-defined]
        func.handler = func  # type: ignore[attr-defined]

        return func

    return decorator
