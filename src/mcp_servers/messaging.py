"""Peer-to-peer role messaging MCP tools for Sidera agents.

Provides tools for async communication between roles:
    1. ``send_message_to_role`` — Send a message to another role
    2. ``check_inbox`` — Check for pending messages
    3. ``reply_to_message`` — Reply to a received message
    4. ``push_learning_to_role`` — Push a structured learning to another role

Also provides ``compose_message_context()`` for injecting pending
messages into the role's prompt at the start of each run.

Uses ``contextvars.ContextVar`` to carry the role context into the
tool handlers (same pattern as delegation.py, memory.py).

Usage::

    from src.mcp_servers.messaging import (
        set_messaging_context, clear_messaging_context,
        compose_message_context,
    )
"""

from __future__ import annotations

import contextvars
from typing import Any

import structlog

from src.agent.tool_registry import tool
from src.mcp_servers.helpers import error_response, text_response

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Messaging context — per-async-task via contextvars (concurrency-safe)
# ---------------------------------------------------------------------------

_messaging_context_var: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "messaging_context", default=None
)

_message_count_var: contextvars.ContextVar[int] = contextvars.ContextVar(
    "message_send_count",
    default=0,
)

_learning_push_count_var: contextvars.ContextVar[int] = contextvars.ContextVar(
    "learning_push_count",
    default=0,
)


def set_messaging_context(
    role_id: str,
    department_id: str,
    registry: Any,
) -> None:
    """Set the messaging context for the current run.

    Called before running an agent turn so the messaging tools know
    which role is active and can validate target roles.

    Args:
        role_id: The role ID executing this turn.
        department_id: The department the role belongs to.
        registry: The SkillRegistry (for validating target role IDs).
    """
    _messaging_context_var.set(
        {
            "role_id": role_id,
            "department_id": department_id,
            "registry": registry,
        }
    )
    _message_count_var.set(0)
    _learning_push_count_var.set(0)


def clear_messaging_context() -> None:
    """Clear messaging context after a run completes."""
    _messaging_context_var.set(None)
    _message_count_var.set(0)
    _learning_push_count_var.set(0)


# ---------------------------------------------------------------------------
# Tool: send_message_to_role
# ---------------------------------------------------------------------------

_MAX_MESSAGES_PER_RUN = 3  # Prevent message loops
_MAX_CHAIN_DEPTH = 5  # Max reply chain depth


@tool(
    name="send_message_to_role",
    description=(
        "Send an async message to another role. The message will be "
        "delivered to them on their next run (heartbeat or briefing). "
        "Use this to share findings, ask questions across departments, "
        "or coordinate with peers. Max 3 messages per run. Messages "
        "expire after 7 days if unread."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "to_role_id": {
                "type": "string",
                "description": (
                    "The role ID to send the message to. "
                    "E.g., 'performance_media_buyer', 'head_of_it'."
                ),
            },
            "subject": {
                "type": "string",
                "description": "Short subject line (max 100 chars).",
            },
            "content": {
                "type": "string",
                "description": (
                    "Message content (1-3 sentences). Include specific "
                    "details, numbers, or questions so the recipient "
                    "can act on it."
                ),
            },
            "reply_to_id": {
                "type": "integer",
                "description": (
                    "Optional: message ID you are replying to. "
                    "Include this when responding to a received message."
                ),
            },
        },
        "required": ["to_role_id", "subject", "content"],
    },
)
async def send_message_to_role(args: dict[str, Any]) -> dict[str, Any]:
    """Send an async message to another role."""
    to_role_id = args.get("to_role_id", "").strip()
    subject = args.get("subject", "").strip()
    content = args.get("content", "").strip()
    reply_to_id = args.get("reply_to_id")

    if not to_role_id or not subject or not content:
        return error_response("to_role_id, subject, and content are all required.")

    # -- Check context --
    ctx = _messaging_context_var.get()
    if ctx is None:
        return error_response(
            "Messaging not available. This tool is only available during agent runs."
        )

    from_role_id = ctx["role_id"]
    from_dept_id = ctx["department_id"]
    registry = ctx["registry"]

    # -- Prevent self-messaging --
    if to_role_id == from_role_id:
        return error_response("Cannot send a message to yourself.")

    # -- Validate target role exists --
    target_role = registry.get_role(to_role_id)
    if target_role is None:
        return error_response(f"Role '{to_role_id}' not found. Check the role ID.")

    # -- Check message limit --
    count = _message_count_var.get()
    if count >= _MAX_MESSAGES_PER_RUN:
        return error_response(
            f"Maximum {_MAX_MESSAGES_PER_RUN} messages per run reached. "
            f"Additional messages can be sent in the next run."
        )

    # -- Check chain depth if replying --
    if reply_to_id is not None:
        try:
            chain_depth = await _get_chain_depth(reply_to_id)
            if chain_depth >= _MAX_CHAIN_DEPTH:
                return error_response(
                    f"Message thread too deep (max {_MAX_CHAIN_DEPTH} replies). "
                    f"Start a new message instead."
                )
        except Exception:
            pass  # On error, allow the message

    to_dept_id = getattr(target_role, "department_id", "")

    try:
        from src.db import service as db_service
        from src.db.session import get_db_session

        # Include sender clearance in message metadata
        from_role_def = registry.get_role(from_role_id)
        sender_clearance = getattr(from_role_def, "clearance_level", "internal")

        async with get_db_session() as session:
            msg_id = await db_service.create_role_message(
                session,
                from_role_id=from_role_id,
                to_role_id=to_role_id,
                from_department_id=from_dept_id,
                to_department_id=to_dept_id,
                subject=subject[:100],
                content=content,
                reply_to_id=reply_to_id,
                metadata={
                    "source": "send_message_to_role",
                    "sender_clearance": sender_clearance,
                },
            )

            # Save relationship memory (best-effort, non-critical)
            try:
                await db_service.save_memory(
                    session=session,
                    user_id="__system__",
                    role_id=from_role_id,
                    department_id=from_dept_id,
                    memory_type="relationship",
                    title=f"Contacted {to_role_id} about: {subject[:80]}",
                    content=(
                        f"Sent message to {target_role.name} ({to_role_id}): "
                        f"{subject}. {content[:200]}"
                    ),
                    confidence=0.6,
                    source_skill_id=f"messaging:{from_role_id}",
                    source_role_id=to_role_id,
                )
            except Exception:
                pass  # Memory save failure is non-critical

        _message_count_var.set(count + 1)

        # -- Optional Slack notification --
        await _notify_message_sent(
            from_role_id,
            to_role_id,
            subject,
            content,
        )

        logger.info(
            "messaging.sent",
            from_role=from_role_id,
            to_role=to_role_id,
            subject=subject[:50],
            message_id=msg_id,
        )

        return text_response(
            f"Message sent to **{target_role.name}** (ID: {msg_id})\n"
            f"Subject: {subject}\n"
            f"They will see this on their next run."
        )

    except Exception as exc:
        logger.exception(
            "messaging.send_error",
            from_role=from_role_id,
            to_role=to_role_id,
            error=str(exc),
        )
        return error_response(f"Failed to send message: {exc}")


# ---------------------------------------------------------------------------
# Tool: check_inbox
# ---------------------------------------------------------------------------


@tool(
    name="check_inbox",
    description=(
        "Check for pending messages from other roles. Messages are "
        "automatically shown in your context at the start of each run, "
        "but you can also explicitly check for new messages."
    ),
    input_schema={
        "type": "object",
        "properties": {},
    },
)
async def check_inbox(args: dict[str, Any]) -> dict[str, Any]:
    """Check for pending messages for the current role."""
    ctx = _messaging_context_var.get()
    if ctx is None:
        return error_response(
            "Messaging not available. This tool is only available during agent runs."
        )

    role_id = ctx["role_id"]

    try:
        from src.db import service as db_service
        from src.db.session import get_db_session

        async with get_db_session() as session:
            messages = await db_service.get_pending_messages(
                session,
                role_id,
                limit=10,
            )

            if not messages:
                return text_response("No pending messages in your inbox.")

            # Mark as read when explicitly checked
            msg_ids = [m.id for m in messages]
            await db_service.mark_messages_delivered(session, msg_ids)

            lines = [f"**{len(messages)} pending message(s):**\n"]
            for msg in messages:
                date_str = msg.created_at.strftime("%b %d %H:%M") if msg.created_at else "?"
                lines.append(
                    f"- **From:** {msg.from_role_id} [{date_str}]\n"
                    f"  **Subject:** {msg.subject}\n"
                    f"  {msg.content}\n"
                    f"  *(Message ID: {msg.id})*"
                )

            return text_response("\n".join(lines))

    except Exception as exc:
        logger.exception(
            "messaging.check_inbox_error",
            role_id=role_id,
            error=str(exc),
        )
        return error_response(f"Failed to check inbox: {exc}")


# ---------------------------------------------------------------------------
# Tool: reply_to_message
# ---------------------------------------------------------------------------


@tool(
    name="reply_to_message",
    description=(
        "Reply to a message from another role. Your reply will be "
        "delivered to them on their next run."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "message_id": {
                "type": "integer",
                "description": "The ID of the message to reply to.",
            },
            "content": {
                "type": "string",
                "description": "Your reply content (1-3 sentences).",
            },
        },
        "required": ["message_id", "content"],
    },
)
async def reply_to_message(args: dict[str, Any]) -> dict[str, Any]:
    """Reply to a received message."""
    message_id = args.get("message_id")
    content = args.get("content", "").strip()

    if not message_id or not content:
        return error_response("message_id and content are required.")

    ctx = _messaging_context_var.get()
    if ctx is None:
        return error_response(
            "Messaging not available. This tool is only available during agent runs."
        )

    from_role_id = ctx["role_id"]
    from_dept_id = ctx["department_id"]

    # -- Check message limit (shared with send_message_to_role) --
    count = _message_count_var.get()
    if count >= _MAX_MESSAGES_PER_RUN:
        return error_response(f"Maximum {_MAX_MESSAGES_PER_RUN} messages per run reached.")

    try:
        from src.db import service as db_service
        from src.db.session import get_db_session

        async with get_db_session() as session:
            # Load the original message to get the sender
            thread = await db_service.get_message_thread(session, message_id)
            if not thread:
                return error_response(f"Message {message_id} not found.")

            # Find the message being replied to
            original = None
            for msg in thread:
                if msg.id == message_id:
                    original = msg
                    break

            if original is None:
                return error_response(f"Message {message_id} not found in thread.")

            # Mark original as read
            await db_service.mark_message_read(session, message_id)

            # Create reply — send back to the original sender
            reply_id = await db_service.create_role_message(
                session,
                from_role_id=from_role_id,
                to_role_id=original.from_role_id,
                from_department_id=from_dept_id,
                to_department_id=original.from_department_id,
                subject=f"Re: {original.subject[:90]}",
                content=content,
                reply_to_id=message_id,
                metadata={"source": "reply_to_message"},
            )

        _message_count_var.set(count + 1)

        logger.info(
            "messaging.replied",
            from_role=from_role_id,
            to_role=original.from_role_id,
            reply_id=reply_id,
            original_id=message_id,
        )

        return text_response(
            f"Reply sent to **{original.from_role_id}** (ID: {reply_id})\n"
            f"Subject: Re: {original.subject}"
        )

    except Exception as exc:
        logger.exception(
            "messaging.reply_error",
            from_role=from_role_id,
            message_id=message_id,
            error=str(exc),
        )
        return error_response(f"Failed to reply: {exc}")


# ---------------------------------------------------------------------------
# Tool: push_learning_to_role
# ---------------------------------------------------------------------------

_MAX_LEARNINGS_PER_RUN = 3  # Prevent learning flood
_MIN_LEARNING_CONFIDENCE = 0.5  # Minimum confidence threshold


@tool(
    name="push_learning_to_role",
    description=(
        "Push a structured learning (insight, pattern, or lesson) to another "
        "role's memory. Unlike regular messages, learnings are saved directly "
        "as memories that persist and influence the recipient's future runs. "
        "Only works if the target role has whitelisted you in their "
        "learning_channels config. Max 3 learnings per run."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "to_role_id": {
                "type": "string",
                "description": (
                    "The role ID to push the learning to. "
                    "E.g., 'performance_media_buyer', 'head_of_it'."
                ),
            },
            "title": {
                "type": "string",
                "description": "Short title for the learning (max 100 chars).",
            },
            "content": {
                "type": "string",
                "description": (
                    "The learning content. Be specific — include numbers, "
                    "dates, campaign IDs, or other concrete details so the "
                    "recipient can act on it."
                ),
            },
            "confidence": {
                "type": "number",
                "description": (
                    "How confident you are in this learning (0.0-1.0). "
                    "Only learnings above 0.5 are accepted."
                ),
            },
        },
        "required": ["to_role_id", "title", "content", "confidence"],
    },
)
async def push_learning_to_role(args: dict[str, Any]) -> dict[str, Any]:
    """Push a structured learning as a cross-role insight memory."""
    to_role_id = args.get("to_role_id", "").strip()
    title = args.get("title", "").strip()[:100]
    content = args.get("content", "").strip()
    confidence = min(1.0, max(0.0, float(args.get("confidence", 0.0))))

    if not to_role_id or not title or not content:
        return error_response("to_role_id, title, and content are all required.")

    if confidence < _MIN_LEARNING_CONFIDENCE:
        return error_response(
            f"Confidence {confidence:.2f} is below the minimum threshold "
            f"({_MIN_LEARNING_CONFIDENCE}). Only push learnings you are "
            f"reasonably confident about."
        )

    # -- Check context --
    ctx = _messaging_context_var.get()
    if ctx is None:
        return error_response(
            "Learning push not available. This tool is only available during agent runs."
        )

    from_role_id = ctx["role_id"]
    from_dept_id = ctx["department_id"]
    registry = ctx["registry"]

    # -- Prevent self-push --
    if to_role_id == from_role_id:
        return error_response("Cannot push a learning to yourself.")

    # -- Validate target role exists --
    target_role = registry.get_role(to_role_id)
    if target_role is None:
        return error_response(f"Role '{to_role_id}' not found. Check the role ID.")

    # -- Check learning_channels whitelist --
    learning_channels = getattr(target_role, "learning_channels", ())
    if from_role_id not in learning_channels:
        return error_response(
            f"Role '{to_role_id}' does not accept learnings from '{from_role_id}'. "
            f"The target role must include your role ID in its learning_channels config. "
            f"Use send_message_to_role instead for general communication."
        )

    # -- Check push limit --
    push_count = _learning_push_count_var.get()
    if push_count >= _MAX_LEARNINGS_PER_RUN:
        return error_response(
            f"Maximum {_MAX_LEARNINGS_PER_RUN} learning pushes per run reached. "
            f"Additional learnings can be pushed in the next run."
        )

    to_dept_id = getattr(target_role, "department_id", "")

    try:
        from src.db import service as db_service
        from src.db.session import get_db_session

        from_role_def = registry.get_role(from_role_id)
        from_role_name = getattr(from_role_def, "name", from_role_id)

        async with get_db_session() as session:
            # Save as cross_role_insight memory on the TARGET role
            await db_service.save_memory(
                session=session,
                user_id="__system__",
                role_id=to_role_id,
                department_id=to_dept_id,
                memory_type="cross_role_insight",
                title=title,
                content=(f"[From {from_role_name} ({from_role_id})]: {content}"),
                confidence=confidence,
                source_skill_id=f"learning:{from_role_id}",
                source_role_id=from_role_id,
                evidence={
                    "source": "push_learning_to_role",
                    "from_role_id": from_role_id,
                    "from_department_id": from_dept_id,
                },
            )

        _learning_push_count_var.set(push_count + 1)

        # -- Optional Slack notification --
        await _notify_learning_pushed(
            from_role_id,
            to_role_id,
            title,
        )

        logger.info(
            "learning.pushed",
            from_role=from_role_id,
            to_role=to_role_id,
            title=title[:50],
            confidence=confidence,
        )

        return text_response(
            f"Learning pushed to **{target_role.name}** ({to_role_id})\n"
            f"Title: {title}\n"
            f"Confidence: {confidence:.2f}\n"
            f"This will appear in their memory context on their next run."
        )

    except Exception as exc:
        logger.exception(
            "learning.push_error",
            from_role=from_role_id,
            to_role=to_role_id,
            error=str(exc),
        )
        return error_response(f"Failed to push learning: {exc}")


# ---------------------------------------------------------------------------
# Message context composition (for prompt injection)
# ---------------------------------------------------------------------------


def compose_message_context(messages: list[Any]) -> str:
    """Format pending messages for injection into role context.

    Args:
        messages: List of ``RoleMessage`` objects (or dicts with
            ``from_role_id``, ``subject``, ``content``, ``id``,
            ``created_at`` fields).

    Returns:
        Formatted string with ``# Inbox`` header, or empty string
        if no messages.
    """
    if not messages:
        return ""

    lines = [
        "# Inbox — Messages from Other Roles\n\n"
        "You have unread messages from colleagues. Review and respond "
        "if appropriate using ``reply_to_message``.\n"
    ]

    for msg in messages:
        if isinstance(msg, dict):
            from_role = msg.get("from_role_id", "unknown")
            subject = msg.get("subject", "")
            content = msg.get("content", "")
            msg_id = msg.get("id", "?")
            created = msg.get("created_at")
            metadata = msg.get("metadata_") or msg.get("metadata") or {}
        else:
            from_role = getattr(msg, "from_role_id", "unknown")
            subject = getattr(msg, "subject", "")
            content = getattr(msg, "content", "")
            msg_id = getattr(msg, "id", "?")
            created = getattr(msg, "created_at", None)
            metadata = getattr(msg, "metadata_", None) or {}

        date_str = created.strftime("%b %d") if created else "?"
        sender_clearance = metadata.get("sender_clearance", "internal") if metadata else "internal"
        clearance_note = (
            f"\n*Sender clearance: {sender_clearance} — "
            f"only share information appropriate for their level.*"
        )
        lines.append(
            f"## From: {from_role} [{date_str}]\n"
            f"**Subject:** {subject}{clearance_note}\n\n"
            f"{content}\n\n"
            f"*(Message ID: {msg_id} — use reply_to_message to respond)*"
        )

    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _get_chain_depth(message_id: int) -> int:
    """Count how deep a reply chain is."""
    from src.db.session import get_db_session

    depth = 0
    current_id: int | None = message_id

    async with get_db_session() as session:
        while current_id is not None and depth < _MAX_CHAIN_DEPTH + 1:
            from sqlalchemy import select

            from src.models.schema import RoleMessage

            stmt = select(RoleMessage.reply_to_id).where(
                RoleMessage.id == current_id,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                break
            current_id = row
            depth += 1

    return depth


async def _notify_message_sent(
    from_role_id: str,
    to_role_id: str,
    subject: str,
    content: str,
) -> None:
    """Post a Slack notification when a role sends a message."""
    try:
        from src.config import settings

        if not settings.message_slack_notifications:
            return

        channel = settings.slack_channel_id
        if not channel:
            return

        from src.connectors.slack import SlackConnector

        slack = SlackConnector()

        # Truncate content for notification
        preview = content[:200] + "..." if len(content) > 200 else content

        text = f'📨 *{from_role_id}* → *{to_role_id}*: "{subject}"\n> {preview}'
        await slack.send_alert(channel=channel, text=text)
    except Exception:
        # Notification failures are non-critical
        pass


async def _notify_learning_pushed(
    from_role_id: str,
    to_role_id: str,
    title: str,
) -> None:
    """Post a Slack notification when a role pushes a learning."""
    try:
        from src.config import settings

        if not settings.message_slack_notifications:
            return

        channel = settings.slack_channel_id
        if not channel:
            return

        from src.connectors.slack import SlackConnector

        slack = SlackConnector()

        text = f'🧠 *{from_role_id}* pushed learning to *{to_role_id}*: "{title}"'
        await slack.send_alert(channel=channel, text=text)
    except Exception:
        # Notification failures are non-critical
        pass
