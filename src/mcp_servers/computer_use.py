"""Computer Use MCP tools for Sidera.

Provides 3 tools that agents can use to control a remote desktop
environment via Anthropic's Computer Use capability.

Tools:
    1. run_computer_use_task    - Execute a complete desktop automation task
    2. get_computer_use_session - Check status of an active session
    3. stop_computer_use_session - Stop an active session

The primary tool (``run_computer_use_task``) is high-level: the agent
describes what it wants done on the desktop, and the connector handles
the full screenshot→action→screenshot loop internally.

Usage:
    from src.mcp_servers.computer_use import create_computer_use_tools

    tools = create_computer_use_tools()
    # These are registered globally via @tool decorator.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.agent.tool_registry import tool
from src.mcp_servers.helpers import error_response, text_response

logger = structlog.get_logger(__name__)


def _get_connector() -> Any:
    """Lazy-load the Computer Use connector."""
    from src.connectors.computer_use import ComputerUseConnector

    return ComputerUseConnector()


# ---------------------------------------------------------------------------
# Tool 1: Run a complete computer use task
# ---------------------------------------------------------------------------

RUN_TASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "description": (
                "What to do on the desktop. Be specific — describe the steps, "
                "what application to use, and the expected outcome. "
                "Example: 'Open Firefox, navigate to Google Ads, download "
                "the performance report for the last 30 days.'"
            ),
        },
        "max_iterations": {
            "type": "integer",
            "description": (
                "Maximum number of screenshot→action cycles (default 50). "
                "Simple tasks need 5-10, complex multi-step tasks may need 30-50."
            ),
            "default": 50,
        },
        "timeout": {
            "type": "integer",
            "description": "Total task timeout in seconds (default 300, max 600).",
            "default": 300,
        },
    },
    "required": ["task"],
}


@tool(
    name="run_computer_use_task",
    description=(
        "Execute a desktop automation task via Anthropic Computer Use. "
        "A sandboxed desktop environment is created, and a Claude agent "
        "controls the mouse, keyboard, and screen to complete the task. "
        "Use this for: navigating web UIs that don't have APIs, downloading "
        "reports from dashboards, interacting with desktop applications, "
        "filling out forms, or anything that requires visual interaction. "
        "The agent can see the screen, click, type, scroll, and use keyboard "
        "shortcuts. Each task gets its own isolated environment."
    ),
    input_schema=RUN_TASK_SCHEMA,
)
async def run_computer_use_task(args: dict[str, Any]) -> dict[str, Any]:
    """Run a complete computer use task."""
    task = args.get("task", "").strip()
    if not task:
        return error_response("task description is required")

    max_iterations = min(args.get("max_iterations", 50), 100)
    timeout = min(args.get("timeout", 300), 600)

    connector = _get_connector()
    try:
        result = await connector.run_task(
            task_prompt=task,
            max_iterations=max_iterations,
            timeout=timeout,
        )

        parts = [
            "# Computer Use Task Result\n",
            f"**Task:** {task[:200]}",
            f"**Actions taken:** {result['action_count']}",
            f"**Iterations:** {result['iterations']}",
            f"**Cost:** ${result['cost_usd']:.4f}",
            f"**Session:** {result['session_id']}",
            "",
            "## Output\n",
            result["output"] or "(No text output — task completed via GUI actions)",
        ]

        if result.get("screenshots"):
            parts.append(f"\n📸 {len(result['screenshots'])} screenshot(s) captured")

        return text_response("\n".join(parts))

    except Exception as exc:
        logger.error("computer_use_tool.run_task_error", task=task[:100], error=str(exc))
        return error_response(f"Computer use task failed: {exc}")


# ---------------------------------------------------------------------------
# Tool 2: Get session status
# ---------------------------------------------------------------------------

GET_SESSION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "session_id": {
            "type": "string",
            "description": "The session ID to check.",
        },
    },
    "required": ["session_id"],
}


@tool(
    name="get_computer_use_session",
    description=(
        "Check the status of an active computer use session. "
        "Returns action count, duration, and whether it's still active."
    ),
    input_schema=GET_SESSION_SCHEMA,
)
async def get_computer_use_session(args: dict[str, Any]) -> dict[str, Any]:
    """Check status of a computer use session."""
    session_id = args.get("session_id", "").strip()
    if not session_id:
        return error_response("session_id is required")

    connector = _get_connector()
    session = connector.get_session(session_id)

    if session is None:
        return error_response(f"No session found: {session_id}")

    import time

    elapsed = time.time() - session.created_at

    return text_response(
        f"**Session:** {session.session_id}\n"
        f"**Active:** {session.is_active}\n"
        f"**Actions:** {session.action_count}\n"
        f"**Duration:** {elapsed:.0f}s\n"
        f"**Display:** {session.display_width}x{session.display_height}\n"
        f"**Cost:** ${session.total_cost_usd:.4f}"
    )


# ---------------------------------------------------------------------------
# Tool 3: Stop session
# ---------------------------------------------------------------------------

STOP_SESSION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "session_id": {
            "type": "string",
            "description": "The session ID to stop.",
        },
    },
    "required": ["session_id"],
}


@tool(
    name="stop_computer_use_session",
    description=(
        "Stop an active computer use session and clean up resources. "
        "Use this if a task is taking too long or needs to be cancelled."
    ),
    input_schema=STOP_SESSION_SCHEMA,
)
async def stop_computer_use_session(args: dict[str, Any]) -> dict[str, Any]:
    """Stop a computer use session."""
    session_id = args.get("session_id", "").strip()
    if not session_id:
        return error_response("session_id is required")

    connector = _get_connector()
    await connector.destroy_session(session_id)

    return text_response(f"Session {session_id} stopped and cleaned up.")


# ---------------------------------------------------------------------------
# Convenience function for registration
# ---------------------------------------------------------------------------


def create_computer_use_tools() -> list[str]:
    """Return the names of all Computer Use tools."""
    return [
        "run_computer_use_task",
        "get_computer_use_session",
        "stop_computer_use_session",
    ]
