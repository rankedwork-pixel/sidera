"""Meeting MCP tools for Sidera.

Provides 3 tools that the agent can use during listen-only meeting sessions
to access meeting context and manage participation.

Tools:
    1. get_meeting_transcript    - Get the current meeting transcript
    2. get_meeting_participants  - List current meeting participants
    3. end_meeting_participation - Leave the meeting

Usage:
    from src.mcp_servers.meeting import create_meeting_tools

    tools = create_meeting_tools()
    # These are registered globally via @tool decorator.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.agent.tool_registry import tool
from src.mcp_servers.helpers import error_response, text_response

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Tool 1: Get meeting transcript
# ---------------------------------------------------------------------------

GET_TRANSCRIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "bot_id": {
            "type": "string",
            "description": "The Recall.ai bot UUID for the active meeting session.",
        },
        "last_minutes": {
            "type": "integer",
            "description": "Only return transcript from the last N minutes (default: all).",
            "default": 0,
        },
    },
    "required": ["bot_id"],
}


@tool(
    name="get_meeting_transcript",
    description=(
        "Get the transcript from the current meeting. Returns speaker-attributed "
        "text from the live meeting. Use this to check what has been discussed."
    ),
    input_schema=GET_TRANSCRIPT_SCHEMA,
)
async def get_meeting_transcript(args: dict[str, Any]) -> dict[str, Any]:
    """Get the current meeting transcript."""
    from src.meetings.session import get_meeting_manager

    bot_id = args.get("bot_id", "")
    if not bot_id:
        return error_response("bot_id is required")

    manager = get_meeting_manager()
    ctx = manager.get_active_session(bot_id)
    if ctx is None:
        return error_response(f"No active meeting session for bot {bot_id}")

    # Build transcript text
    lines: list[str] = []
    for entry in ctx.transcript_buffer:
        if entry.get("_status_updated"):
            continue
        speaker = entry.get("speaker", "Unknown")
        text = ""
        if isinstance(entry.get("words"), list):
            text = " ".join(w.get("text", w.get("word", "")) for w in entry["words"])
        elif isinstance(entry.get("text"), str):
            text = entry["text"]
        if text.strip():
            lines.append(f"{speaker}: {text.strip()}")

    if not lines:
        return text_response("No transcript available yet.")

    transcript_text = "\n".join(lines)
    return text_response(f"Meeting transcript ({len(lines)} entries):\n\n{transcript_text}")


# ---------------------------------------------------------------------------
# Tool 2: Get meeting participants
# ---------------------------------------------------------------------------

GET_PARTICIPANTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "bot_id": {
            "type": "string",
            "description": "The Recall.ai bot UUID for the active meeting session.",
        },
    },
    "required": ["bot_id"],
}


@tool(
    name="get_meeting_participants",
    description=(
        "Get the list of current participants in the meeting. Returns names "
        "of everyone in the call."
    ),
    input_schema=GET_PARTICIPANTS_SCHEMA,
)
async def get_meeting_participants(args: dict[str, Any]) -> dict[str, Any]:
    """Get current meeting participants."""
    from src.meetings.session import get_meeting_manager

    bot_id = args.get("bot_id", "")
    if not bot_id:
        return error_response("bot_id is required")

    manager = get_meeting_manager()
    ctx = manager.get_active_session(bot_id)
    if ctx is None:
        return error_response(f"No active meeting session for bot {bot_id}")

    participants = ctx.participants
    if not participants:
        return text_response("No participant information available yet.")

    names = [p.get("name", "Unknown") for p in participants]
    return text_response(
        f"Meeting participants ({len(names)}):\n" + "\n".join(f"- {name}" for name in names)
    )


# ---------------------------------------------------------------------------
# Tool 3: End meeting participation
# ---------------------------------------------------------------------------

END_MEETING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "bot_id": {
            "type": "string",
            "description": "The Recall.ai bot UUID for the active meeting session.",
        },
    },
    "required": ["bot_id"],
}


@tool(
    name="end_meeting_participation",
    description=(
        "Leave the meeting. Use this when the meeting is wrapping up or "
        "you've been asked to leave. Post-call processing will automatically "
        "summarize the transcript and delegate action items to your team."
    ),
    input_schema=END_MEETING_SCHEMA,
)
async def end_meeting_participation(args: dict[str, Any]) -> dict[str, Any]:
    """Leave the meeting and trigger post-call processing."""
    from src.meetings.session import get_meeting_manager

    bot_id = args.get("bot_id", "")
    if not bot_id:
        return error_response("bot_id is required")

    manager = get_meeting_manager()
    ctx = manager.get_active_session(bot_id)
    if ctx is None:
        return error_response(f"No active meeting session for bot {bot_id}")

    result = await manager.leave(bot_id)
    return text_response(
        f"Left the meeting. "
        f"{result.get('transcript_entries', 0)} transcript entries captured. "
        "Post-call delegation will run automatically."
    )


# ---------------------------------------------------------------------------
# Convenience: list all meeting tools
# ---------------------------------------------------------------------------


def create_meeting_tools() -> list:
    """Return all meeting tool functions (already registered via @tool)."""
    return [
        get_meeting_transcript,
        get_meeting_participants,
        end_meeting_participation,
    ]
