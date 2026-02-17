"""Slack MCP server tools for Sidera.

Provides 6 tools that the Claude agent can call to interact with Slack
and search its own persistent memory.

Tools:
    1. send_slack_alert            - Send an alert or notification
    2. send_slack_briefing_preview - Send a preview of the daily briefing
    3. check_slack_connection      - Test the Slack connection
    4. send_slack_thread_reply     - Reply in a Slack thread (conversation mode)
    5. react_to_message            - Add an emoji reaction to a message
    6. search_role_memory_archive  - Search full memory history (hot + cold)

Usage:
    from src.mcp_servers.slack import create_slack_mcp_server

    server_config = create_slack_mcp_server()
    # Pass to ClaudeAgentOptions.mcp_servers
"""

from __future__ import annotations

from typing import Any

import structlog

from src.agent.tool_registry import tool
from src.connectors.slack import SlackConnector
from src.mcp_servers.helpers import error_response, text_response

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_connector() -> SlackConnector:
    """Create a fresh SlackConnector instance."""
    return SlackConnector()


# ---------------------------------------------------------------------------
# Tool 1: Send alert
# ---------------------------------------------------------------------------

SEND_ALERT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "alert_type": {
            "type": "string",
            "description": ("Type of alert. Examples: 'cost_overrun', 'anomaly', 'error', 'info'."),
        },
        "message": {
            "type": "string",
            "description": "Human-readable alert message.",
        },
        "details": {
            "type": "object",
            "description": (
                "Optional. Additional context as a JSON object "
                "(e.g. affected campaigns, metric values)."
            ),
        },
    },
    "required": ["alert_type", "message"],
}


@tool(
    name="send_slack_alert",
    description=(
        "Send an alert or notification to the configured Slack channel. "
        "Use this for cost overruns, anomalies, errors, or other important "
        "notifications that the advertiser should see immediately. "
        "Do NOT use this for daily briefings (use send_slack_briefing_preview) "
        "or approval requests (those are handled by the workflow engine)."
    ),
    input_schema=SEND_ALERT_SCHEMA,
)
async def send_slack_alert(args: dict[str, Any]) -> dict[str, Any]:
    """Send an alert to Slack."""
    alert_type = args.get("alert_type", "").strip()
    message = args.get("message", "").strip()
    details = args.get("details")

    if not alert_type:
        return error_response("alert_type is required.")
    if not message:
        return error_response("message is required.")

    logger.info(
        "tool.send_slack_alert",
        alert_type=alert_type,
    )
    try:
        connector = _get_connector()
        result = connector.send_alert(
            channel_id=None,
            alert_type=alert_type,
            message=message,
            details=details,
        )
        return text_response(
            f"Alert sent successfully to channel {result.get('channel', 'unknown')}.\n"
            f"Message timestamp: {result.get('ts', 'unknown')}"
        )

    except Exception as exc:
        logger.error("tool.send_slack_alert.error", error=str(exc))
        return error_response(f"Failed to send Slack alert: {exc}")


# ---------------------------------------------------------------------------
# Tool 2: Send briefing preview
# ---------------------------------------------------------------------------

SEND_BRIEFING_PREVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "briefing_text": {
            "type": "string",
            "description": (
                "The main briefing content in Slack mrkdwn format. "
                "Include key metrics, trends, and insights."
            ),
        },
        "recommendations": {
            "type": "array",
            "description": (
                "Optional list of recommendation objects, each with 'title' and 'description' keys."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                },
            },
        },
    },
    "required": ["briefing_text"],
}


@tool(
    name="send_slack_briefing_preview",
    description=(
        "Send a preview of the daily briefing to Slack. This is a read-only "
        "preview without approval buttons -- it simply shows the briefing "
        "content and any recommendations. Use this when the agent wants to "
        "share analysis results or a briefing draft with the team."
    ),
    input_schema=SEND_BRIEFING_PREVIEW_SCHEMA,
)
async def send_slack_briefing_preview(args: dict[str, Any]) -> dict[str, Any]:
    """Send a briefing preview to Slack."""
    briefing_text = args.get("briefing_text", "").strip()
    recommendations = args.get("recommendations", [])

    if not briefing_text:
        return error_response("briefing_text is required.")

    logger.info("tool.send_slack_briefing_preview")
    try:
        connector = _get_connector()
        result = connector.send_briefing(
            channel_id=None,
            briefing_text=briefing_text,
            recommendations=recommendations or [],
        )
        rec_count = len(recommendations) if recommendations else 0
        return text_response(
            f"Briefing preview sent to channel {result.get('channel', 'unknown')}.\n"
            f"Included {rec_count} recommendation(s).\n"
            f"Message timestamp: {result.get('ts', 'unknown')}"
        )

    except Exception as exc:
        logger.error("tool.send_slack_briefing_preview.error", error=str(exc))
        return error_response(f"Failed to send briefing preview: {exc}")


# ---------------------------------------------------------------------------
# Tool 3: Check connection
# ---------------------------------------------------------------------------

CHECK_CONNECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
}


@tool(
    name="check_slack_connection",
    description=(
        "Test the Slack connection by verifying the bot token. Returns "
        "the team name, bot user, and bot ID if successful. Use this to "
        "confirm Slack is properly configured before sending messages."
    ),
    input_schema=CHECK_CONNECTION_SCHEMA,
)
async def check_slack_connection(args: dict[str, Any]) -> dict[str, Any]:
    """Test the Slack connection."""
    logger.info("tool.check_slack_connection")
    try:
        connector = _get_connector()
        result = connector.test_connection()
        return text_response(
            f"Slack connection successful!\n"
            f"  Team: {result.get('team', 'unknown')}\n"
            f"  Bot user: {result.get('user', 'unknown')}\n"
            f"  Bot ID: {result.get('bot_id', 'unknown')}"
        )

    except Exception as exc:
        logger.error("tool.check_slack_connection.error", error=str(exc))
        return error_response(f"Slack connection failed: {exc}")


# ---------------------------------------------------------------------------
# Tool 4: Send thread reply (conversation mode)
# ---------------------------------------------------------------------------

SEND_THREAD_REPLY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "channel_id": {
            "type": "string",
            "description": ("The Slack channel ID to post in. Required for thread replies."),
        },
        "thread_ts": {
            "type": "string",
            "description": (
                "The thread timestamp to reply in. This is the ts of the "
                "parent message that started the thread."
            ),
        },
        "message": {
            "type": "string",
            "description": (
                "The message text to post as a thread reply. Supports Slack mrkdwn formatting."
            ),
        },
    },
    "required": ["channel_id", "thread_ts", "message"],
}


@tool(
    name="send_slack_thread_reply",
    description=(
        "Send a reply in a Slack thread. Use this in conversation mode to "
        "post follow-up messages or additional analysis results in the same "
        "thread. Requires channel_id and thread_ts to identify the thread."
    ),
    input_schema=SEND_THREAD_REPLY_SCHEMA,
)
async def send_slack_thread_reply(args: dict[str, Any]) -> dict[str, Any]:
    """Send a thread reply in Slack."""
    channel_id = args.get("channel_id", "").strip()
    thread_ts = args.get("thread_ts", "").strip()
    message = args.get("message", "").strip()

    if not channel_id:
        return error_response("channel_id is required.")
    if not thread_ts:
        return error_response("thread_ts is required.")
    if not message:
        return error_response("message is required.")

    logger.info(
        "tool.send_slack_thread_reply",
        channel_id=channel_id,
        thread_ts=thread_ts,
    )
    try:
        connector = _get_connector()
        result = connector.send_thread_reply(
            channel_id=channel_id,
            thread_ts=thread_ts,
            text=message,
        )
        return text_response(
            f"Thread reply sent successfully.\n"
            f"Channel: {result.get('channel', 'unknown')}\n"
            f"Message timestamp: {result.get('ts', 'unknown')}"
        )

    except Exception as exc:
        logger.error("tool.send_slack_thread_reply.error", error=str(exc))
        return error_response(f"Failed to send thread reply: {exc}")


# ---------------------------------------------------------------------------
# Tool 5: React to a message
# ---------------------------------------------------------------------------

REACT_TO_MESSAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "channel_id": {
            "type": "string",
            "description": ("The Slack channel ID containing the message to react to."),
        },
        "timestamp": {
            "type": "string",
            "description": ("The ts (timestamp) of the message to react to."),
        },
        "emoji": {
            "type": "string",
            "description": (
                "The emoji name without colons. Examples: 'fire', "
                "'thumbsup', 'eyes', 'white_check_mark', '100', "
                "'bulb', 'chart_with_upwards_trend', 'raised_hands'."
            ),
        },
    },
    "required": ["channel_id", "timestamp", "emoji"],
}


@tool(
    name="react_to_message",
    description=(
        "Add an emoji reaction to a Slack message. Use this to acknowledge "
        "a message, show agreement, or express a reaction without sending "
        "a full text reply. Use sparingly and when genuinely appropriate — "
        "a quick reaction is often better than a wordy 'got it' reply."
    ),
    input_schema=REACT_TO_MESSAGE_SCHEMA,
)
async def react_to_message(args: dict[str, Any]) -> dict[str, Any]:
    """Add an emoji reaction to a Slack message."""
    channel_id = args.get("channel_id", "").strip()
    timestamp = args.get("timestamp", "").strip()
    emoji = args.get("emoji", "").strip().strip(":")

    if not channel_id:
        return error_response("channel_id is required.")
    if not timestamp:
        return error_response("timestamp is required.")
    if not emoji:
        return error_response("emoji is required.")

    logger.info(
        "tool.react_to_message",
        channel_id=channel_id,
        timestamp=timestamp,
        emoji=emoji,
    )
    try:
        connector = _get_connector()
        connector.add_reaction(
            channel_id=channel_id,
            timestamp=timestamp,
            name=emoji,
        )
        return text_response(f"Reacted with :{emoji}: to the message.")

    except Exception as exc:
        logger.error("tool.react_to_message.error", error=str(exc))
        return error_response(f"Failed to add reaction: {exc}")


# ---------------------------------------------------------------------------
# Tool 6: Search role memory archive
# ---------------------------------------------------------------------------

SEARCH_MEMORY_ARCHIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "role_id": {
            "type": "string",
            "description": (
                "The role ID whose memory archive to search. "
                "This is the role you are currently operating as."
            ),
        },
        "query": {
            "type": "string",
            "description": (
                "Free-text search term to match against memory titles and "
                "content. Examples: 'budget spike', 'campaign paused', "
                "'Q3 strategy'. Leave empty to get the most recent archived memories."
            ),
        },
        "memory_type": {
            "type": "string",
            "enum": ["decision", "anomaly", "pattern", "insight"],
            "description": (
                "Optional filter by memory type. 'decision' = past approval/rejection "
                "outcomes, 'anomaly' = detected spikes/drops, 'pattern' = recognized "
                "recurring trends, 'insight' = strategic learnings."
            ),
        },
        "limit": {
            "type": "integer",
            "description": "Max results to return (default 10, max 50).",
            "default": 10,
        },
    },
    "required": ["role_id"],
}


@tool(
    name="search_role_memory_archive",
    description=(
        "Search your full memory history — both recent (hot) and archived (cold) "
        "memories. Use this when you encounter a situation that feels familiar and "
        "want to check if something similar happened before, even months ago. "
        "Searches across all time by keyword matching on title and content. "
        "Results are returned newest-first."
    ),
    input_schema=SEARCH_MEMORY_ARCHIVE_SCHEMA,
)
async def search_role_memory_archive(args: dict[str, Any]) -> dict[str, Any]:
    """Search the full memory archive for a role."""
    role_id = args.get("role_id", "").strip()
    query = args.get("query", "").strip()
    memory_type = args.get("memory_type")
    limit = min(args.get("limit", 10), 50)

    if not role_id:
        return error_response("role_id is required.")

    logger.info(
        "tool.search_role_memory_archive",
        role_id=role_id,
        query=query,
        memory_type=memory_type,
    )

    try:
        from src.db import service as db_service
        from src.db.session import get_db_session

        async with get_db_session() as session:
            memories = await db_service.search_role_memories(
                session,
                user_id="default",  # TODO: wire real user_id when multi-tenant
                role_id=role_id,
                query=query,
                memory_type=memory_type,
                limit=limit,
            )

        if not memories:
            hint = f" matching '{query}'" if query else ""
            return text_response(
                f"No memories found for role '{role_id}'{hint}.\n"
                "This role may not have accumulated any relevant history yet."
            )

        lines = [f"Found {len(memories)} memories for role '{role_id}':\n"]
        for mem in memories:
            age_label = "archived" if mem.is_archived else "active"
            date_str = mem.created_at.strftime("%Y-%m-%d") if mem.created_at else "unknown"
            lines.append(
                f"- [{mem.memory_type}] ({date_str}, {age_label}) "
                f"{mem.title}\n  {mem.content[:300]}"
            )

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error("tool.search_role_memory_archive.error", error=str(exc))
        return error_response(f"Failed to search memory archive: {exc}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_slack_tools() -> list[Any]:
    """Return the list of Slack MCP tool definitions.

    These can be passed to ``create_sdk_mcp_server(tools=...)`` or used
    individually for testing.

    Returns:
        List of 6 SdkMcpTool instances.
    """
    return [
        send_slack_alert,
        send_slack_briefing_preview,
        check_slack_connection,
        send_slack_thread_reply,
        react_to_message,
        search_role_memory_archive,
    ]
