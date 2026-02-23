"""Slack interactive routes for Sidera.

Handles Slack's interactive webhook — when users click Approve/Reject
buttons on budget reallocation proposals or other gated actions.

Architecture:
- Uses ``slack_bolt.async_app.AsyncApp`` for event/interaction handling.
- Wraps with ``AsyncSlackRequestHandler`` for FastAPI integration.
- Exports a FastAPI ``APIRouter`` with a single ``POST /slack/events``
  endpoint that routes all Slack interactions through Bolt.
- Approval decisions are stored in a module-level dict
  (``_pending_approvals``) as a temporary in-memory store. This will be
  replaced with PostgreSQL + Inngest events in the database integration
  phase.

Exports:
- ``slack_app``          — The AsyncApp instance
- ``slack_handler``      — The AsyncSlackRequestHandler
- ``router``             — FastAPI APIRouter
- ``_pending_approvals`` — In-memory approval state (temporary)
- ``get_approval_status`` — Helper to check approval status
"""

import asyncio as _asyncio
import json as _json
import re as _re
import time as _time

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import Response
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.async_app import AsyncApp

from src.config import settings
from src.middleware.rbac import check_slack_permission

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Slack Bolt async app
# ---------------------------------------------------------------------------

# Slack Bolt requires a non-empty token at construction time even if the
# app won't make real API calls yet (e.g. during tests or before OAuth
# is completed).  We pass a placeholder so the module can be imported
# safely; the real token is used at runtime when Slack delivers events.
slack_app = AsyncApp(
    token=settings.slack_bot_token or "xoxb-not-yet-configured",
    signing_secret=settings.slack_signing_secret or "not-yet-configured",
)

# ---------------------------------------------------------------------------
# In-memory approval store (temporary — will move to DB + Inngest)
# ---------------------------------------------------------------------------

_pending_approvals: dict[str, dict] = {}


def get_approval_status(approval_id: str) -> dict | None:
    """Check the status of an approval decision.

    Returns the approval dict if a decision has been recorded,
    or ``None`` if no decision exists for the given ID.
    """
    return _pending_approvals.get(approval_id)


# ---------------------------------------------------------------------------
# Message debounce — batch rapid-fire messages into a single turn
# ---------------------------------------------------------------------------

# When a user sends multiple messages quickly ("YOOO" then "MY MAN" 2s later),
# each one triggers a Slack event.  Without debounce, each fires a separate
# agent turn and the user gets multiple redundant replies.
#
# The debounce buffer collects messages per thread and waits for a short quiet
# period before dispatching a single combined turn.

_DEBOUNCE_SECONDS: float = 1.5  # quiet window before dispatching

# Keyed by thread_ts (or channel_id for non-threaded @mentions).
# Value: dict with buffered messages, metadata, and the pending timer handle.
_debounce_buffers: dict[str, dict] = {}
_debounce_lock = _asyncio.Lock()


async def _debounce_conversation_turn(
    *,
    debounce_key: str,
    role_id: str,
    channel_id: str,
    thread_ts: str,
    user_id: str,
    message_text: str,
    message_ts: str,
    source_user_name: str,
    image_content: list | None = None,
) -> None:
    """Buffer a message and schedule a debounced dispatch.

    If messages are already buffered for this thread, the new message is
    appended and the timer is reset.  When the quiet window expires, all
    buffered messages are joined with newlines and dispatched as one turn.
    """
    async with _debounce_lock:
        buf = _debounce_buffers.get(debounce_key)

        if buf is not None:
            # Cancel the existing timer — we'll reset it.
            timer: _asyncio.TimerHandle | _asyncio.Task = buf["timer"]
            if isinstance(timer, _asyncio.Task) and not timer.done():
                timer.cancel()
            # Append the new message text.
            buf["texts"].append(message_text)
            # Keep the latest message_ts and any images.
            buf["message_ts"] = message_ts
            if image_content:
                buf["image_content"] = (buf.get("image_content") or []) + image_content
            logger.info(
                "debounce.appended",
                debounce_key=debounce_key,
                buffered_count=len(buf["texts"]),
            )
        else:
            # First message — create a new buffer entry.
            buf = {
                "texts": [message_text],
                "role_id": role_id,
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "user_id": user_id,
                "message_ts": message_ts,
                "source_user_name": source_user_name,
                "image_content": image_content,
                "timer": None,  # will be set below
            }
            _debounce_buffers[debounce_key] = buf
            logger.info(
                "debounce.started",
                debounce_key=debounce_key,
            )

        # Schedule (or reschedule) the flush after the quiet window.
        buf["timer"] = _asyncio.get_event_loop().create_task(_debounce_flush(debounce_key))


async def _debounce_flush(debounce_key: str) -> None:
    """Wait for the quiet window, then dispatch the combined turn."""
    await _asyncio.sleep(_DEBOUNCE_SECONDS)

    async with _debounce_lock:
        buf = _debounce_buffers.pop(debounce_key, None)

    if buf is None:
        return

    # Combine all buffered messages into one.
    combined_text = "\n".join(buf["texts"])
    logger.info(
        "debounce.flushing",
        debounce_key=debounce_key,
        message_count=len(buf["texts"]),
        combined_length=len(combined_text),
    )

    await _dispatch_or_run_inline(
        event_name="sidera/conversation.turn",
        data={
            "role_id": buf["role_id"],
            "channel_id": buf["channel_id"],
            "thread_ts": buf["thread_ts"],
            "user_id": buf["user_id"],
            "message_text": combined_text,
            "message_ts": buf["message_ts"],
            "source_user_name": buf["source_user_name"],
            "image_content": buf.get("image_content"),
        },
    )


# ---------------------------------------------------------------------------
# Markdown → Slack mrkdwn conversion
# ---------------------------------------------------------------------------


def _markdown_to_mrkdwn(text: str) -> str:
    """Thin wrapper — delegates to the shared utility in the Slack connector."""
    from src.connectors.slack import markdown_to_mrkdwn

    return markdown_to_mrkdwn(text)


# ---------------------------------------------------------------------------
# Long response → Google Doc redirect
# ---------------------------------------------------------------------------

# Threshold in characters — responses longer than this get written to a
# Google Doc with a link posted in Slack instead of the raw text.
_DRIVE_REDIRECT_THRESHOLD = 3000


def _maybe_redirect_to_drive(
    response_text: str,
    role_label: str,
    *,
    doc_title_prefix: str = "",
) -> str:
    """If *response_text* exceeds the threshold, write it to a new Google
    Doc and return a short Slack message containing a summary excerpt plus
    a link to the full document.

    Falls back to returning *response_text* unchanged when:
    - the text is short enough for Slack
    - Google Drive credentials are not configured
    - the Drive API call fails for any reason

    The function is **non-fatal** — it should never prevent a response
    from reaching the user.
    """
    if len(response_text) <= _DRIVE_REDIRECT_THRESHOLD:
        return response_text

    try:
        from src.connectors.google_drive import GoogleDriveConnector

        drive = GoogleDriveConnector()
    except Exception:
        logger.debug("drive_redirect.no_connector", reason="Drive not configured")
        return response_text

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    title_date = now.strftime("%Y-%m-%d %H:%M")
    prefix = doc_title_prefix or role_label
    doc_title = f"{prefix} — {title_date}"

    try:
        result = drive.create_document(title=doc_title, content=response_text)
        if not result or not result.get("web_view_link"):
            logger.warning("drive_redirect.create_failed", result=result)
            return response_text

        link = result["web_view_link"]

        # Build a short summary: first ~600 chars of the response
        excerpt = response_text[:600].rstrip()
        if len(response_text) > 600:
            # Try to break at the last newline within the excerpt
            last_nl = excerpt.rfind("\n")
            if last_nl > 200:
                excerpt = excerpt[:last_nl]
            excerpt += "\n..."

        return f"{excerpt}\n\n:page_facing_up: *Full report:* <{link}|{doc_title}>"

    except Exception:
        logger.warning("drive_redirect.error", exc_info=True)
        return response_text


# ---------------------------------------------------------------------------
# Recommendation extraction + inline approval processing
# ---------------------------------------------------------------------------


def _extract_recommendations(
    response_text: str,
    expected_nonce: str = "",
) -> tuple[str, list[dict]]:
    """Extract JSON recommendation blocks from agent response text.

    Returns (clean_text, recommendations) where clean_text has the JSON
    block removed, and recommendations is the parsed list.

    When ``expected_nonce`` is provided, the extracted JSON must contain a
    matching ``_nonce`` field. This prevents prompt injection attacks where
    an attacker embeds a fake recommendations JSON block in a user message
    that the LLM might quote or reference in its response.
    """
    pattern = r'```json\s*(\{.*?"recommendations"\s*:\s*\[.*?\].*?\})\s*```'
    match = _re.search(pattern, response_text, _re.DOTALL)
    if not match:
        return response_text, []

    try:
        data = _json.loads(match.group(1))
        recs = data.get("recommendations", [])
        if not isinstance(recs, list):
            return response_text, []

        # Nonce provenance check — reject recommendations without
        # the correct nonce (defense against injected JSON blocks).
        if expected_nonce and data.get("_nonce") != expected_nonce:
            logger.warning(
                "extract_recommendations.nonce_mismatch",
                expected=expected_nonce[:8],
                got=str(data.get("_nonce", ""))[:8],
            )
            return response_text, []

        # Remove the JSON block from the response text shown to user
        clean = response_text[: match.start()].rstrip()
        return clean, recs
    except (_json.JSONDecodeError, KeyError):
        return response_text, []


async def _process_recommendations_inline(
    recommendations: list[dict],
    channel_id: str,
    thread_ts: str,
    user_id: str,
    role_id: str,
) -> list[dict]:
    """Create DB approvals and post Approve/Reject buttons to the thread.

    Inline equivalent of ``process_recommendations()`` — no Inngest steps.
    Returns list of ``{approval_id, db_id}`` for tracking.
    """
    from src.connectors.slack import SlackConnector
    from src.db import service as db_service
    from src.db.session import get_db_session

    results: list[dict] = []
    slack = SlackConnector()

    logger.info(
        "inline_approval.start",
        count=len(recommendations),
        channel_id=channel_id,
        thread_ts=thread_ts,
    )

    for i, rec in enumerate(recommendations):
        logger.info(
            "inline_approval.processing",
            index=i,
            action_type=rec.get("action_type"),
            description=rec.get("description", "")[:80],
        )

        # Inject context into Claude Code task proposals
        if rec.get("action_type") == "claude_code_task":
            rec.setdefault("action_params", {})["role_id"] = role_id
            rec.setdefault("action_params", {})["user_id"] = user_id

        db_id = 0
        try:
            async with get_db_session() as session:
                item = await db_service.create_approval(
                    session=session,
                    analysis_id=None,
                    user_id=user_id,
                    action_type=rec.get(
                        "action_type",
                        "recommendation_accept",
                    ),
                    account_id=None,
                    description=rec.get("description", ""),
                    reasoning=rec.get("reasoning", ""),
                    action_params=rec.get("action_params", {}),
                    projected_impact=rec.get("projected_impact"),
                    risk_assessment=rec.get("risk_level"),
                )
                db_id = item.id
                logger.info(
                    "inline_approval.db_created",
                    db_id=db_id,
                )
        except Exception as exc:
            logger.warning(
                "inline_approval.create_failed",
                error=str(exc),
            )
            # Still post Slack buttons so the user sees the proposal preview.
            # Use a timestamp-based fallback ID — approve/reject will fail
            # gracefully if DB is still unavailable when they click.
            db_id = int(_time.time() * 1000) % 1_000_000

        approval_id = f"conversation-{thread_ts}-{db_id}"

        # Build preview blocks for special action types
        task_preview = ""
        diff_text = ""
        if rec.get("action_type") == "claude_code_task":
            params = rec.get("action_params", {})
            task_preview = (
                f"Skill: {params.get('skill_name', params.get('skill_id', ''))}\n"
                f"Prompt: {params.get('prompt') or '(skill default)'}\n"
                f"Budget: ${params.get('max_budget_usd', 5.0):.2f}\n"
                f"Permission: {params.get('permission_mode', 'acceptEdits')}"
            )
        elif rec.get("action_type") in ("skill_proposal", "role_proposal"):
            diff_text = rec.get("action_params", {}).get("diff", "")

        # Post approval buttons to the thread
        try:
            slack.send_approval_request(
                channel_id=channel_id,
                approval_id=approval_id,
                action_type=rec.get("action_type", "unknown"),
                description=rec.get("description", ""),
                reasoning=rec.get("reasoning", ""),
                projected_impact=rec.get(
                    "projected_impact",
                    "",
                ),
                risk_level=rec.get("risk_level", "medium"),
                thread_ts=thread_ts,
                diff_text=diff_text,
                task_preview=task_preview,
            )
            logger.info(
                "inline_approval.slack_posted",
                approval_id=approval_id,
            )
        except Exception as exc:
            logger.error(
                "inline_approval.slack_failed",
                error=str(exc),
                exc_info=True,
            )

        results.append(
            {"approval_id": approval_id, "db_id": db_id},
        )

    return results


async def _execute_approved_action_inline(
    approval_id: str,
    channel_id: str,
    thread_ts: str,
    user_id: str,
) -> None:
    """Execute an approved action and post the result to the thread.

    Dev-mode inline equivalent of the Inngest approval-to-execute flow.
    """
    try:
        from src.db import service as db_service
        from src.db.session import get_db_session
        from src.workflows.daily_briefing import _execute_action

        # Parse DB ID from approval_id (format: "conversation-{ts}-{db_id}")
        parts = approval_id.split("-")
        db_id = int(parts[-1]) if parts[-1].isdigit() else 0
        if db_id == 0:
            return

        async with get_db_session() as session:
            item = await db_service.get_approval_by_id(session, db_id)
            if item is None or item.executed_at is not None:
                return  # Already executed or not found

            action_type = item.action_type.value if item.action_type else ""
            action_params = item.action_params or {}

        # Execute the action
        result = await _execute_action(action_type, action_params)

        # Record execution result
        async with get_db_session() as session:
            await db_service.record_execution_result(
                session,
                db_id,
                execution_result=result,
            )

        # Post success to thread
        from src.connectors.slack import SlackConnector

        slack = SlackConnector()

        # Format Claude Code results with richer output
        if action_type == "claude_code_task" and result.get("success"):
            output = result.get("output_text", "")
            cost = result.get("cost_usd", 0)
            turns = result.get("num_turns", 0)
            footer = f"\n_Cost: ${cost:.4f} | Turns: {turns}_"

            # Redirect long output to Google Drive (posts summary + link)
            output = _maybe_redirect_to_drive(
                output,
                "Claude Code Task",
            )

            reply_text = f":white_check_mark: *Claude Code task completed.*\n\n{output}{footer}"
            slack.send_thread_reply(
                channel_id=channel_id,
                thread_ts=thread_ts,
                text=reply_text,
            )
        elif action_type == "claude_code_task" and result.get("is_error"):
            error_msg = result.get("error_message", "Unknown error")
            reply_text = f":x: *Claude Code task failed.*\n{error_msg}"
            slack.send_thread_reply(
                channel_id=channel_id,
                thread_ts=thread_ts,
                text=reply_text,
            )
        else:
            result_text = _json.dumps(result, indent=2, default=str)[:2000]
            reply_text = f":white_check_mark: Action executed successfully.\n```{result_text}```"
            slack.send_thread_reply(
                channel_id=channel_id,
                thread_ts=thread_ts,
                text=reply_text,
            )

    except Exception as exc:
        logger.error("inline_execute.failed", error=str(exc))
        try:
            from src.middleware.sentry_setup import capture_exception

            capture_exception(exc)
        except Exception:
            pass
        try:
            from src.connectors.slack import SlackConnector

            slack = SlackConnector()
            slack.send_thread_reply(
                channel_id=channel_id,
                thread_ts=thread_ts,
                text=f":x: Execution failed: {exc}",
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Dev-mode inline conversation runner (bypasses Inngest)
# ---------------------------------------------------------------------------


async def _run_conversation_turn_inline(
    role_id: str,
    channel_id: str,
    thread_ts: str,
    user_id: str,
    message_text: str,
    message_ts: str = "",
    source_user_name: str = "",
    image_content: list[dict] | None = None,
) -> None:
    """Run a conversation turn directly, without Inngest.

    This is the local-dev equivalent of ``conversation_turn_workflow``.
    It skips durable step checkpointing but executes the same core logic:
    create/load thread → build context → get history → run agent → post reply
    → update thread.
    """
    typing_ts = ""
    try:
        from src.agent.core import SideraAgent
        from src.connectors.slack import SlackConnector
        from src.db import service as db_service
        from src.db.session import get_db_session
        from src.mcp_servers.actions import clear_pending_actions
        from src.mcp_servers.claude_code_actions import (
            clear_pending_cc_tasks,
            get_pending_cc_tasks,
        )
        from src.mcp_servers.delegation import (
            clear_delegation_context,
            get_delegation_results,
            set_delegation_context,
        )
        from src.mcp_servers.evolution import (
            clear_pending_proposals,
            clear_proposer_context,
            set_proposer_context,
        )
        from src.mcp_servers.memory import (
            clear_memory_context,
            extract_conversation_memories_llm,
            set_memory_context,
        )
        from src.mcp_servers.messaging import (
            clear_messaging_context,
            compose_message_context,
            set_messaging_context,
        )
        from src.mcp_servers.skill_runner import (
            clear_skill_runner_context,
            set_skill_runner_context,
        )
        from src.mcp_servers.working_group import (
            clear_working_group_context,
            get_pending_working_groups,
            set_working_group_context,
        )
        from src.skills.db_loader import load_registry_with_db
        from src.skills.executor import compose_role_context
        from src.skills.memory import compose_memory_context

        # Clear any stale pending actions/proposals from previous runs
        clear_pending_actions()
        clear_pending_proposals()
        clear_pending_cc_tasks()
        clear_delegation_context()
        clear_working_group_context()
        clear_memory_context()
        clear_messaging_context()
        clear_proposer_context()
        clear_skill_runner_context()

        # Step 1: Create or load conversation thread in DB
        turn_count = 1
        try:
            async with get_db_session() as session:
                thread = await db_service.get_conversation_thread(
                    session,
                    thread_ts,
                )
                if thread is None:
                    thread = await db_service.create_conversation_thread(
                        session=session,
                        thread_ts=thread_ts,
                        channel_id=channel_id,
                        role_id=role_id,
                        user_id=user_id,
                    )
                turn_count = (thread.turn_count or 0) + 1
        except Exception as exc:
            logger.warning("inline_conversation.db_skip", error=str(exc))

        registry = await load_registry_with_db()
        role = registry.get_role(role_id)
        if role is None:
            connector = SlackConnector()
            connector.send_thread_reply(
                channel_id=channel_id,
                thread_ts=thread_ts,
                text=f":x: Role '{role_id}' not found.",
            )
            return

        dept = registry.get_department(role.department_id)

        # Load memory context (best-effort)
        memory_ctx = ""
        try:
            from src.skills.memory import filter_superseded_memories

            async with get_db_session() as session:
                memories = await db_service.get_role_memories(
                    session,
                    user_id,
                    role_id,
                    limit=10,
                )
                superseded = await db_service.get_superseded_memory_ids(
                    session,
                    user_id,
                    role_id,
                )
                memories = filter_superseded_memories(memories, superseded)
                # Also load inter-agent relationship memories
                agent_memories = await db_service.get_agent_relationship_memories(
                    session, role_id, limit=5
                )
                all_memories = list(memories) + list(agent_memories)
                if all_memories:
                    memory_ctx = compose_memory_context(all_memories)
        except Exception:
            pass

        # Load pending peer messages (best-effort)
        message_ctx = ""
        try:
            async with get_db_session() as session:
                pending_msgs = await db_service.get_pending_messages(
                    session,
                    role_id,
                    limit=10,
                )
                message_ctx = compose_message_context(pending_msgs)
                if pending_msgs:
                    msg_ids = [m.id for m in pending_msgs]
                    await db_service.mark_messages_delivered(
                        session,
                        msg_ids,
                    )
        except Exception:
            pass

        import secrets as _secrets

        from src.agent.injection_defense import NONCE_INSTRUCTION_TEMPLATE

        _rec_nonce = _secrets.token_hex(8)

        role_context = compose_role_context(
            department=dept,
            role=role,
            memory_context=memory_ctx,
            registry=registry,
            pending_messages=message_ctx,
        )
        # Append nonce instruction so the agent tags its recommendations
        role_context += NONCE_INSTRUCTION_TEMPLATE.format(nonce=_rec_nonce)

        # Get thread history from Slack
        connector = SlackConnector()

        # Post visible typing indicator (with role name if available)
        typing_ts = connector.post_typing_indicator(
            channel_id,
            thread_ts,
            role_name=role.name if role else "",
        )

        thread_history = connector.get_thread_history(
            channel_id=channel_id,
            thread_ts=thread_ts,
            limit=50,
        )

        # Resolve user clearance for information filtering
        user_clearance = "public"
        try:
            from src.middleware.rbac import resolve_user_clearance

            user_clearance = await resolve_user_clearance(user_id)
        except Exception:
            pass  # Default to public on failure

        # Run the agent (with delegation + memory + messaging context)
        is_manager = bool(getattr(role, "manages", ()))
        delegation_results: list[dict] = []
        dept_id = dept.id if dept else ""
        if is_manager:
            set_delegation_context(role_id, registry)
            set_working_group_context(role_id, registry)
        set_memory_context(role_id, dept_id, user_id, source_user_name)
        set_messaging_context(role_id, dept_id, registry)
        set_proposer_context(role_id, dept_id)
        set_skill_runner_context(role_id, registry, user_id, role_context)

        agent = SideraAgent()
        try:
            result = await agent.run_conversation_turn(
                role_id=role_id,
                role_context=role_context,
                thread_history=thread_history,
                current_message=message_text,
                user_id=user_id,
                bot_user_id="",
                turn_number=turn_count,
                is_manager=is_manager,
                channel_id=channel_id,
                message_ts=message_ts,
                image_content=image_content,
                user_clearance=user_clearance,
            )
        finally:
            clear_skill_runner_context()
            clear_memory_context()
            clear_messaging_context()
            clear_proposer_context()
            if is_manager:
                delegation_results = get_delegation_results()
                clear_delegation_context()
                if delegation_results:
                    logger.info(
                        "inline_conversation.delegation_results",
                        role_id=role_id,
                        delegations=len(delegation_results),
                        results=delegation_results,
                    )
                # Dispatch pending working groups (inline)
                for wg_proposal in get_pending_working_groups():
                    wg_proposal["user_id"] = user_id
                    wg_proposal["channel_id"] = channel_id
                    wg_proposal["thread_ts"] = thread_ts
                    _dispatch_or_run_inline(
                        "sidera/working_group.run",
                        wg_proposal,
                    )
                clear_working_group_context()

        # Collect any action proposals the agent made via propose_action tool
        from src.mcp_servers.actions import get_pending_actions

        pending_actions = get_pending_actions()

        # Collect any skill evolution proposals
        skill_proposals = []
        try:
            from src.mcp_servers.evolution import get_pending_proposals

            skill_proposals = get_pending_proposals()
        except Exception:
            pass

        # Collect any Claude Code task proposals
        cc_task_proposals = []
        try:
            cc_task_proposals = get_pending_cc_tasks()
        except Exception:
            pass

        logger.info(
            "inline_conversation.response",
            role_id=role_id,
            response_length=len(result.response_text),
            pending_actions=len(pending_actions),
            skill_proposals=len(skill_proposals),
            cc_task_proposals=len(cc_task_proposals),
        )

        # Also check for JSON blocks in text as fallback
        clean_text, text_recs = _extract_recommendations(
            result.response_text,
            expected_nonce=_rec_nonce,
        )
        all_recs = pending_actions + text_recs + skill_proposals + cc_task_proposals
        response_text = clean_text if text_recs else result.response_text

        # Prefix reply with role identity (strip existing prefix to prevent duplication)
        role_label = role.name if role else role_id
        clean_response = response_text
        for prefix_variant in [f"*{role_label}:*\n", f"{role_label}:\n", f"**{role_label}:**\n"]:
            if clean_response.startswith(prefix_variant):
                clean_response = clean_response[len(prefix_variant) :]
                break
        # Convert Markdown → Slack mrkdwn (fixes **bold**, ## headers, etc.)
        clean_response = _markdown_to_mrkdwn(clean_response)

        # Redirect long responses to Google Drive (posts summary + link)
        clean_response = _maybe_redirect_to_drive(
            clean_response,
            role_label,
        )

        prefixed_text = f"*{role_label}:*\n{clean_response}"

        # Post reply to thread
        connector.send_thread_reply(
            channel_id=channel_id,
            thread_ts=thread_ts,
            text=prefixed_text,
        )

        # Clean up typing indicators now that we've replied
        if typing_ts:
            connector.delete_message(channel_id, typing_ts)
        if message_ts:
            try:
                connector.remove_reaction(channel_id, message_ts)
            except Exception:
                pass

        # Process action proposals: create DB approvals + post buttons to thread
        if all_recs:
            logger.info(
                "inline_conversation.processing_actions",
                count=len(all_recs),
                sources=f"tool={len(pending_actions)},text={len(text_recs)}",
            )
            await _process_recommendations_inline(
                recommendations=all_recs,
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_id=user_id,
                role_id=role_id,
            )

        # Update thread activity in DB (include delegation costs)
        try:
            cost_increment = (
                result.cost.get("total_cost_usd", 0.0) if isinstance(result.cost, dict) else 0.0
            )
            # Add delegation sub-role costs if any
            if is_manager and delegation_results:
                for dr in delegation_results:
                    dr_cost = dr.get("cost", {})
                    if isinstance(dr_cost, dict):
                        cost_increment += dr_cost.get(
                            "total_cost_usd",
                            0.0,
                        )
            async with get_db_session() as session:
                await db_service.update_conversation_thread_activity(
                    session=session,
                    thread_ts=thread_ts,
                    cost_increment=cost_increment,
                )
        except Exception as exc:
            logger.warning("inline_conversation.db_update_skip", error=str(exc))

        # Extract and save memories from conversation (LLM-powered)
        try:
            role_name = role.name if role else role_id
            entries = await extract_conversation_memories_llm(
                role_id=role_id,
                role_name=role_name,
                department_id=dept_id,
                user_message=message_text,
                agent_response=result.response_text,
                user_id=user_id,
                thread_history=thread_history,
                source_user_name=source_user_name,
            )
            if entries:
                async with get_db_session() as session:
                    for entry in entries:
                        await db_service.save_memory(
                            session=session,
                            user_id=user_id,
                            **entry,
                        )
                logger.info(
                    "inline_conversation.memories_saved",
                    role_id=role_id,
                    count=len(entries),
                )
        except Exception as exc:
            logger.warning("inline_conversation.memory_skip", error=str(exc))

        logger.info(
            "inline_conversation.completed",
            role_id=role_id,
            thread_ts=thread_ts,
            cost=result.cost,
        )

    except Exception as exc:
        logger.error("inline_conversation.error", error=str(exc))
        try:
            from src.middleware.sentry_setup import capture_exception

            capture_exception(exc)
        except Exception:
            pass
        try:
            from src.connectors.slack import SlackConnector

            err_connector = SlackConnector()
            # Clean up typing indicator so it doesn't stay stuck
            if typing_ts:
                try:
                    err_connector.delete_message(channel_id, typing_ts)
                except Exception:
                    pass
            if message_ts:
                try:
                    err_connector.remove_reaction(channel_id, message_ts)
                except Exception:
                    pass
            err_connector.send_thread_reply(
                channel_id=channel_id,
                thread_ts=thread_ts,
                text=("Sorry, I hit an issue processing that. Could you try again?"),
            )
        except Exception:
            pass


async def _run_meeting_join_inline(
    meeting_url: str,
    role_id: str,
    user_id: str,
    channel_id: str,
) -> None:
    """Join a meeting directly without Inngest (dev mode).

    Calls the MeetingSessionManager directly to join the meeting
    via Recall.ai, bypassing the Inngest workflow.
    """
    try:
        from src.meetings.session import get_meeting_manager
        from src.skills.db_loader import load_registry_with_db

        registry = await load_registry_with_db()
        role = registry.get_role(role_id)
        if role is None:
            logger.error(
                "inline_meeting.role_invalid",
                role_id=role_id,
            )
            return

        manager = get_meeting_manager()
        ctx = await manager.join(
            meeting_url=meeting_url,
            role_id=role_id,
            user_id=user_id,
            channel_id=channel_id,
        )

        logger.info(
            "inline_meeting.joined",
            meeting_url=meeting_url,
            role_id=role_id,
            bot_id=ctx.bot_id,
        )

    except Exception as exc:
        logger.error(
            "inline_meeting.error",
            meeting_url=meeting_url,
            role_id=role_id,
            error=str(exc),
        )
        # Try to notify Slack
        try:
            from src.connectors.slack import SlackConnector

            connector = SlackConnector()
            connector.send_alert(
                channel_id=channel_id or None,
                text=f":x: Failed to join meeting inline: {exc}",
            )
        except Exception:
            pass


async def _extract_and_download_images(
    event: dict,
    bot_token: str,
    *,
    max_images: int = 3,
    max_size_bytes: int = 5 * 1024 * 1024,
) -> list[dict]:
    """Extract image files from a Slack event and return Anthropic content blocks.

    Downloads each qualifying image, base64-encodes it, and returns a list
    of ``{"type": "image", "source": {...}}`` dicts ready for the Anthropic
    Messages API.

    Silently skips non-image files, oversized files, and download failures.
    Returns an empty list if no images qualify.
    """
    import base64

    from src.connectors.slack import ALLOWED_IMAGE_TYPES, download_slack_file

    files = event.get("files", [])
    if not files:
        return []

    image_blocks: list[dict] = []
    for f in files[:max_images]:
        mimetype = f.get("mimetype", "")
        if mimetype not in ALLOWED_IMAGE_TYPES:
            logger.info(
                "image_extract.skipped_type",
                filename=f.get("name", "?"),
                mimetype=mimetype,
            )
            continue

        download_url = f.get("url_private_download") or f.get("url_private")
        if not download_url:
            continue

        try:
            raw_bytes = await download_slack_file(
                download_url,
                bot_token,
                max_size_bytes=max_size_bytes,
            )
            b64_data = base64.b64encode(raw_bytes).decode("ascii")
            image_blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mimetype,
                        "data": b64_data,
                    },
                }
            )
            logger.info(
                "image_extract.downloaded",
                filename=f.get("name", "?"),
                size_bytes=len(raw_bytes),
            )
        except ValueError as exc:
            logger.warning(
                "image_extract.too_large",
                filename=f.get("name", "?"),
                error=str(exc),
            )
        except Exception as exc:
            logger.warning(
                "image_extract.download_failed",
                filename=f.get("name", "?"),
                error=str(exc),
            )

    return image_blocks


async def _dispatch_or_run_inline(
    event_name: str,
    data: dict,
) -> None:
    """Try to dispatch via Inngest; fall back to inline execution for dev.

    For conversation turns, runs the turn inline if Inngest send fails.
    For other events, just logs the failure.
    """
    try:
        import inngest as inngest_mod

        from src.workflows.inngest_client import inngest_client as ic

        await ic.send(inngest_mod.Event(name=event_name, data=data))
        return
    except Exception as exc:
        logger.warning(
            "inngest.send_failed",
            event_name=event_name,
            error=str(exc),
        )

    # Inngest unavailable — run inline in dev mode
    if event_name == "sidera/conversation.turn":
        import asyncio

        asyncio.create_task(
            _run_conversation_turn_inline(
                role_id=data["role_id"],
                channel_id=data["channel_id"],
                thread_ts=data["thread_ts"],
                user_id=data["user_id"],
                message_text=data["message_text"],
                message_ts=data.get("message_ts", ""),
                source_user_name=data.get("source_user_name", ""),
                image_content=data.get("image_content"),
            )
        )
    elif event_name == "sidera/meeting.join":
        import asyncio

        asyncio.create_task(
            _run_meeting_join_inline(
                meeting_url=data["meeting_url"],
                role_id=data["role_id"],
                user_id=data["user_id"],
                channel_id=data.get("channel_id", ""),
            )
        )
    else:
        logger.warning(
            "inngest.no_inline_fallback",
            event_name=event_name,
        )


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------


@slack_app.action("sidera_approve")
async def handle_approve(ack, body, client):
    """Handle an Approve button click from Slack.

    Workflow:
    1. Acknowledge the interaction (Slack requires a response within 3 s).
    2. Check RBAC permission (approver+ required).
    3. Log the approval decision.
    4. Update the original message to replace buttons with a status line.
    5. Store the decision in ``_pending_approvals`` for Inngest to pick up.
    """
    await ack()

    approval_id = body["actions"][0]["value"]
    user_id = body["user"]["id"]
    channel_id = body["channel"]["id"]
    message_ts = body["container"]["message_ts"]

    # --- RBAC check ---
    allowed, deny_msg = await check_slack_permission(user_id, "approve")
    if not allowed:
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=deny_msg,
        )
        return

    logger.info(
        "approval.approved",
        approval_id=approval_id,
        user_id=user_id,
    )

    # Replace the original buttons with a confirmation banner.
    await client.chat_update(
        channel=channel_id,
        ts=message_ts,
        text=f"Approved by <@{user_id}>",
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":white_check_mark: *Approved* by <@{user_id}>",
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Approval ID: `{approval_id}`",
                    }
                ],
            },
        ],
    )

    # Store the decision for the Inngest workflow to consume.
    _pending_approvals[approval_id] = {
        "status": "approved",
        "decided_by": user_id,
    }

    # Persist to database as durable backup
    try:
        from src.db import service as db_service
        from src.db.session import get_db_session
        from src.models.schema import ApprovalStatus

        async with get_db_session() as session:
            parts = approval_id.split("-")
            db_approval_id = int(parts[-1]) if parts[-1].isdigit() else 0
            await db_service.update_approval_status(
                session=session,
                approval_id=db_approval_id,
                status=ApprovalStatus.APPROVED,
                decided_by=user_id,
            )
            await db_service.log_event(
                session=session,
                user_id=user_id,
                event_type="approval_decided",
                event_data={
                    "approval_id": approval_id,
                    "status": "approved",
                    "decided_by": user_id,
                },
                source="slack_interactive",
            )
    except Exception as exc:
        logger.warning(
            "db.approval_update_failed",
            error=str(exc),
            approval_id=approval_id,
        )

    # Emit Inngest event so waiting workflows unblock immediately
    try:
        import inngest as inngest_mod

        from src.workflows.inngest_client import inngest_client as ic

        await ic.send(
            inngest_mod.Event(
                name="sidera/approval.decided",
                data={
                    "approval_id": approval_id,
                    "status": "approved",
                    "decided_by": user_id,
                },
            )
        )
    except Exception as exc:
        logger.warning(
            "inngest.approval_event_failed",
            error=str(exc),
            approval_id=approval_id,
        )

    # Dev-mode inline execution: if approval came from a conversation thread,
    # execute immediately and post result to the thread.
    thread_ts = body.get("container", {}).get("thread_ts", "")
    if thread_ts:
        import asyncio

        asyncio.create_task(
            _execute_approved_action_inline(
                approval_id=approval_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_id=user_id,
            )
        )


@slack_app.action("sidera_reject")
async def handle_reject(ack, body, client):
    """Handle a Reject button click from Slack.

    Same flow as ``handle_approve`` but records a rejection.
    """
    await ack()

    approval_id = body["actions"][0]["value"]
    user_id = body["user"]["id"]
    channel_id = body["channel"]["id"]
    message_ts = body["container"]["message_ts"]

    # --- RBAC check ---
    allowed, deny_msg = await check_slack_permission(user_id, "reject")
    if not allowed:
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=deny_msg,
        )
        return

    logger.info(
        "approval.rejected",
        approval_id=approval_id,
        user_id=user_id,
    )

    await client.chat_update(
        channel=channel_id,
        ts=message_ts,
        text=f"Rejected by <@{user_id}>",
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":x: *Rejected* by <@{user_id}>",
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Approval ID: `{approval_id}`",
                    }
                ],
            },
        ],
    )

    _pending_approvals[approval_id] = {
        "status": "rejected",
        "decided_by": user_id,
    }

    # Persist to database as durable backup
    try:
        from src.db import service as db_service
        from src.db.session import get_db_session
        from src.models.schema import ApprovalStatus

        async with get_db_session() as session:
            parts = approval_id.split("-")
            db_approval_id = int(parts[-1]) if parts[-1].isdigit() else 0
            await db_service.update_approval_status(
                session=session,
                approval_id=db_approval_id,
                status=ApprovalStatus.REJECTED,
                decided_by=user_id,
            )
            await db_service.log_event(
                session=session,
                user_id=user_id,
                event_type="approval_decided",
                event_data={
                    "approval_id": approval_id,
                    "status": "rejected",
                    "decided_by": user_id,
                },
                source="slack_interactive",
            )
    except Exception as exc:
        logger.warning(
            "db.approval_update_failed",
            error=str(exc),
            approval_id=approval_id,
        )

    # Emit Inngest event so waiting workflows unblock immediately
    try:
        import inngest as inngest_mod

        from src.workflows.inngest_client import inngest_client as ic

        await ic.send(
            inngest_mod.Event(
                name="sidera/approval.decided",
                data={
                    "approval_id": approval_id,
                    "status": "rejected",
                    "decided_by": user_id,
                },
            )
        )
    except Exception as exc:
        logger.warning(
            "inngest.approval_event_failed",
            error=str(exc),
            approval_id=approval_id,
        )


# ---------------------------------------------------------------------------
# Org chart management helper
# ---------------------------------------------------------------------------


# JSON fields on org entities that should be parsed from string input
_ORG_JSON_FIELDS = frozenset(
    {
        "connectors",
        "briefing_skills",
        "manages",
        "platforms",
        "tags",
        "tools_required",
    }
)

# Allowed fields per entity type — prevents setting dangerous columns
# (is_active, created_by, id) via Slack. Use /sidera org remove for is_active.
_ORG_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "dept": frozenset(
        {
            "name",
            "description",
            "context",
            "context_text",
        }
    ),
    "role": frozenset(
        {
            "name",
            "description",
            "persona",
            "department_id",
            "briefing_skills",
            "manages",
            "connectors",
            "delegation_model",
            "synthesis_prompt",
            "schedule",
            "context_text",
        }
    ),
    "skill": frozenset(
        {
            "name",
            "description",
            "category",
            "system_supplement",
            "prompt_template",
            "output_format",
            "business_guidance",
            "context_text",
            "platforms",
            "tags",
            "tools_required",
            "model",
            "max_turns",
            "version",
            "schedule",
            "chain_after",
            "role_id",
        }
    ),
}


async def _handle_org_command(parts: list[str], say, user_id: str, cmd: str = "/sidera") -> None:
    """Handle ``/sidera org …`` subcommands for dynamic org chart CRUD.

    Args:
        parts: Command parts *after* the ``org`` keyword.
            For example, ``/sidera org list`` yields ``["list"]``.
        say: Slack ``say`` callable for posting responses.
        user_id: The Slack user who invoked the command.
    """
    import json as json_mod

    from src.db import service as db_service
    from src.db.session import get_db_session

    sub = parts[0].lower() if parts else ""

    # RBAC: mutations require admin, reads require viewer
    _write_subs = {"add-dept", "add-role", "add-skill", "update", "remove"}
    if sub in _write_subs:
        allowed, deny_msg = await check_slack_permission(user_id, "manage_org_chart")
        if not allowed:
            await say(deny_msg)
            return
    else:
        allowed, deny_msg = await check_slack_permission(user_id, "view")
        if not allowed:
            await say(deny_msg)
            return

    # --- org list ---
    if sub == "list":
        try:
            async with get_db_session() as session:
                departments = await db_service.list_org_departments(session)
                roles = await db_service.list_org_roles(session)
                skills = await db_service.list_org_skills(session)

            lines = ["*Dynamic Org Chart (DB-defined)*\n"]

            lines.append(f"*Departments ({len(departments)}):*")
            for dept in departments:
                lines.append(f"\u2022 `{dept.dept_id}` \u2014 {dept.name}")

            lines.append(f"\n*Roles ({len(roles)}):*")
            for role in roles:
                lines.append(
                    f"\u2022 `{role.role_id}` \u2014 {role.name} (dept: {role.department_id})"
                )

            lines.append(f"\n*Skills ({len(skills)}):*")
            for skill in skills:
                lines.append(
                    f"\u2022 `{skill.skill_id}` \u2014 {skill.name} (role: {skill.role_id})"
                )

            await say("\n".join(lines))
        except Exception as exc:
            logger.warning("sidera_org.list_failed", error=str(exc))
            await say(f":x: Error listing org chart: {exc}")
        return

    # --- org show <type> <id> ---
    if sub == "show":
        if len(parts) < 3:
            await say(f":warning: Usage: `{cmd} org show <dept|role|skill> <id>`")
            return

        entity_type = parts[1].lower()
        entity_id = parts[2]

        try:
            async with get_db_session() as session:
                if entity_type == "dept":
                    entity = await db_service.get_org_department(session, entity_id)
                elif entity_type == "role":
                    entity = await db_service.get_org_role(session, entity_id)
                elif entity_type == "skill":
                    entity = await db_service.get_org_skill(session, entity_id)
                else:
                    await say(
                        f":warning: Unknown type `{entity_type}`. Use `dept`, `role`, or `skill`."
                    )
                    return

            if entity is None:
                await say(f":warning: {entity_type} `{entity_id}` not found.")
                return

            # Build a field display from all non-None columns
            lines = [f":mag: *{entity_type.title()} `{entity_id}`*\n"]
            for col in entity.__table__.columns:
                val = getattr(entity, col.name, None)
                if val is not None:
                    display_val = str(val)
                    if len(display_val) > 200:
                        display_val = display_val[:200] + "..."
                    lines.append(f"\u2022 *{col.name}:* {display_val}")

            await say("\n".join(lines))
        except Exception as exc:
            logger.warning("sidera_org.show_failed", error=str(exc))
            await say(f":x: Error showing {entity_type} `{entity_id}`: {exc}")
        return

    # --- org add-dept <id> <name> <description…> ---
    if sub == "add-dept":
        if len(parts) < 4:
            await say(f":warning: Usage: `{cmd} org add-dept <id> <name> <description…>`")
            return

        dept_id = parts[1]
        dept_name = parts[2]
        dept_desc = " ".join(parts[3:])

        try:
            async with get_db_session() as session:
                await db_service.create_org_department(
                    session,
                    dept_id=dept_id,
                    name=dept_name,
                    description=dept_desc,
                    created_by=user_id,
                )
                await session.commit()

            await say(
                f":white_check_mark: Department `{dept_id}` created.\n"
                f"\u2022 *Name:* {dept_name}\n"
                f"\u2022 *Description:* {dept_desc}"
            )
        except Exception as exc:
            logger.warning("sidera_org.add_dept_failed", error=str(exc))
            await say(f":x: Error creating department: {exc}")
        return

    # --- org add-role <id> <dept_id> <name…> ---
    if sub == "add-role":
        if len(parts) < 4:
            await say(f":warning: Usage: `{cmd} org add-role <id> <dept_id> <name…>`")
            return

        role_id = parts[1]
        dept_id = parts[2]
        role_name = " ".join(parts[3:])

        try:
            async with get_db_session() as session:
                await db_service.create_org_role(
                    session,
                    role_id=role_id,
                    name=role_name,
                    department_id=dept_id,
                    description=role_name,
                    created_by=user_id,
                )
                await session.commit()

            await say(
                f":white_check_mark: Role `{role_id}` created in department `{dept_id}`.\n"
                f"\u2022 *Name:* {role_name}"
            )
        except Exception as exc:
            logger.warning("sidera_org.add_role_failed", error=str(exc))
            await say(f":x: Error creating role: {exc}")
        return

    # --- org add-skill <id> <role_id> <name…> ---
    if sub == "add-skill":
        if len(parts) < 4:
            await say(f":warning: Usage: `{cmd} org add-skill <id> <role_id> <name…>`")
            return

        skill_id = parts[1]
        role_id = parts[2]
        skill_name = " ".join(parts[3:])

        try:
            async with get_db_session() as session:
                # Look up the role to inherit the department_id
                role_obj = await db_service.get_org_role(session, role_id)
                dept_id = role_obj.department_id if role_obj else ""

                await db_service.create_org_skill(
                    session,
                    skill_id=skill_id,
                    name=skill_name,
                    description=skill_name,
                    category="general",
                    system_supplement="TODO: Configure",
                    prompt_template="TODO: Configure",
                    output_format="TODO: Configure",
                    business_guidance="TODO: Configure",
                    department_id=dept_id,
                    role_id=role_id,
                    created_by=user_id,
                )
                await session.commit()

            await say(
                f":white_check_mark: Skill `{skill_id}` created under role `{role_id}`.\n"
                f"\u2022 *Name:* {skill_name}\n"
                f"_Scaffold fields set to TODO \u2014 use "
                f"`{cmd} org update skill {skill_id} <field> <value>` "
                f"to configure._"
            )
        except Exception as exc:
            logger.warning("sidera_org.add_skill_failed", error=str(exc))
            await say(f":x: Error creating skill: {exc}")
        return

    # --- org update <type> <id> <field> <value…> ---
    if sub == "update":
        if len(parts) < 5:
            await say(
                f":warning: Usage: `{cmd} org update <dept|role|skill> <id> <field> <value…>`"
            )
            return

        entity_type = parts[1].lower()
        entity_id = parts[2]
        field = parts[3]
        raw_value = " ".join(parts[4:])

        # Validate field is in the allowlist for this entity type
        allowed = _ORG_ALLOWED_FIELDS.get(entity_type, frozenset())
        if allowed and field not in allowed:
            await say(
                f":warning: Field `{field}` is not allowed for `{entity_type}`. "
                f"Allowed fields: {', '.join(sorted(allowed))}"
            )
            return

        # Parse JSON for list/object fields
        if field in _ORG_JSON_FIELDS:
            try:
                value = json_mod.loads(raw_value)
            except json_mod.JSONDecodeError:
                await say(f':warning: Field `{field}` expects JSON. Example: `["a", "b"]`')
                return
        else:
            value = raw_value

        try:
            async with get_db_session() as session:
                if entity_type == "dept":
                    result = await db_service.update_org_department(
                        session,
                        entity_id,
                        **{field: value},
                    )
                elif entity_type == "role":
                    result = await db_service.update_org_role(
                        session,
                        entity_id,
                        **{field: value},
                    )
                elif entity_type == "skill":
                    result = await db_service.update_org_skill(
                        session,
                        entity_id,
                        **{field: value},
                    )
                else:
                    await say(
                        f":warning: Unknown type `{entity_type}`. Use `dept`, `role`, or `skill`."
                    )
                    return

                if result is None:
                    await say(f":warning: {entity_type} `{entity_id}` not found.")
                    return

                await session.commit()

            await say(
                f":white_check_mark: Updated `{entity_id}` \u2014 set *{field}* to `{raw_value}`"
            )
        except Exception as exc:
            logger.warning("sidera_org.update_failed", error=str(exc))
            await say(f":x: Error updating {entity_type} `{entity_id}`: {exc}")
        return

    # --- org remove <type> <id> ---
    if sub == "remove":
        if len(parts) < 3:
            await say(f":warning: Usage: `{cmd} org remove <dept|role|skill> <id>`")
            return

        entity_type = parts[1].lower()
        entity_id = parts[2]

        try:
            async with get_db_session() as session:
                if entity_type == "dept":
                    result = await db_service.delete_org_department(session, entity_id)
                elif entity_type == "role":
                    result = await db_service.delete_org_role(session, entity_id)
                elif entity_type == "skill":
                    result = await db_service.delete_org_skill(session, entity_id)
                else:
                    await say(
                        f":warning: Unknown type `{entity_type}`. Use `dept`, `role`, or `skill`."
                    )
                    return

                if result is None:
                    await say(f":warning: {entity_type} `{entity_id}` not found.")
                    return

                await session.commit()

            await say(
                f":white_check_mark: {entity_type.title()} "
                f"`{entity_id}` has been soft-deleted (deactivated)."
            )
        except Exception as exc:
            logger.warning("sidera_org.remove_failed", error=str(exc))
            await say(f":x: Error removing {entity_type} `{entity_id}`: {exc}")
        return

    # --- org history <type> <id> ---
    if sub == "history":
        if len(parts) < 3:
            await say(f":warning: Usage: `{cmd} org history <dept|role|skill> <id>`")
            return

        entity_type = parts[1].lower()
        entity_id = parts[2]

        # Map short types to the entity_type stored in audit event_data
        type_map = {"dept": "department", "role": "role", "skill": "skill"}
        full_type = type_map.get(entity_type, entity_type)

        try:
            from sqlalchemy import select as sa_select

            from src.models.schema import AuditLog

            async with get_db_session() as session:
                stmt = (
                    sa_select(AuditLog)
                    .where(AuditLog.event_type == "org_chart_change")
                    .order_by(AuditLog.created_at.desc())
                    .limit(50)
                )
                result = await session.execute(stmt)
                all_entries = list(result.scalars().all())

            # Filter for matching entity
            matching = [
                e
                for e in all_entries
                if e.event_data
                and e.event_data.get("entity_type") == full_type
                and e.event_data.get("entity_id") == entity_id
            ]

            if not matching:
                await say(f":clipboard: No history found for {entity_type} `{entity_id}`.")
                return

            lines = [
                f":clipboard: *History for {entity_type} `{entity_id}`* ({len(matching)} entries)\n"
            ]
            for entry in matching[:20]:
                ts = entry.created_at.strftime("%Y-%m-%d %H:%M") if entry.created_at else "?"
                op = entry.event_data.get("operation", "?")
                changes = entry.event_data.get("changes", {})
                changed_fields = ", ".join(changes.keys()) if changes else "n/a"
                lines.append(
                    f"\u2022 `{ts}` \u2014 *{op}* (fields: {changed_fields}) by {entry.user_id}"
                )

            await say("\n".join(lines))
        except Exception as exc:
            logger.warning("sidera_org.history_failed", error=str(exc))
            await say(f":x: Error fetching history: {exc}")
        return

    # --- Unknown org subcommand — show help ---
    await say(
        f":card_index: *Org Chart Commands*\n"
        f"\u2022 `{cmd} org list` \u2014 Show all DB-defined departments, roles, skills\n"
        f"\u2022 `{cmd} org show <dept|role|skill> <id>` \u2014 Show full definition\n"
        f"\u2022 `{cmd} org add-dept <id> <name> <description…>` \u2014 Create department\n"
        f"\u2022 `{cmd} org add-role <id> <dept_id> <name…>` \u2014 Create role\n"
        f"\u2022 `{cmd} org add-skill <id> <role_id> <name…>` \u2014 Create skill scaffold\n"
        f"\u2022 `{cmd} org update <type> <id> <field> <value…>` \u2014 Update a field\n"
        f"\u2022 `{cmd} org remove <type> <id>` \u2014 Soft-delete\n"
        f"\u2022 `{cmd} org history <type> <id>` \u2014 Show audit trail"
    )


# ---------------------------------------------------------------------------
# User management handler — /sidera users
# ---------------------------------------------------------------------------


async def _handle_users_command(
    parts: list[str],
    say,
    user_id: str,
    cmd: str = "/sidera",
) -> None:
    """Handle ``/sidera users …`` subcommands for RBAC user management.

    Subcommands:
        list                    — List all users and their roles
        add <slack_user_id> <role> [name] — Add a user with a role
        set-role <slack_user_id> <role>   — Change a user's role
        set-clearance <slack_user_id> <level> — Change a user's clearance
        show <slack_user_id>             — Show a user's role + clearance
        remove <slack_user_id>            — Deactivate a user
        whoami                  — Show your own role + clearance

    All mutations require admin role. ``whoami``, ``show``, and ``list``
    are open.
    """
    from src.db import service as db_service
    from src.db.session import get_db_session
    from src.middleware.rbac import clear_role_cache

    sub = parts[0].lower() if parts else ""
    valid_roles = {"admin", "approver", "viewer"}

    # --- users list ---
    if sub == "list" or not sub:
        try:
            async with get_db_session() as session:
                users = await db_service.list_users(session, active_only=True)

            if not users:
                await say(
                    ":bust_in_silhouette: No users registered yet. "
                    f"Add one with `{cmd} users add <slack_user_id> <role>`."
                )
                return

            lines = [":busts_in_silhouette: *Registered Users*\n"]
            for u in users:
                role_val = u.role.value if hasattr(u.role, "value") else str(u.role)
                cl = getattr(u, "clearance_level", None)
                cl_val = cl.value if hasattr(cl, "value") else str(cl) if cl else "public"
                name_part = f" ({u.display_name})" if u.display_name else ""
                lines.append(
                    f"\u2022 <@{u.user_id}>{name_part} \u2014 *{role_val}* | clearance: *{cl_val}*"
                )
            await say("\n".join(lines))
        except Exception as exc:
            logger.warning("sidera_users.list_failed", error=str(exc))
            await say(f":x: Error listing users: {exc}")
        return

    # --- users whoami ---
    if sub == "whoami":
        try:
            from src.middleware.rbac import resolve_user_clearance, resolve_user_role

            role = await resolve_user_role(user_id)
            clearance = await resolve_user_clearance(user_id)
            await say(f":bust_in_silhouette: Your role is *{role}* | clearance: *{clearance}*")
        except Exception as exc:
            await say(f":x: Error: {exc}")
        return

    # --- users show <slack_user_id> ---
    if sub == "show":
        if len(parts) < 2:
            await say(f":warning: Usage: `{cmd} users show <slack_user_id>`")
            return

        target_id = parts[1].strip("<@>").upper()
        try:
            async with get_db_session() as session:
                user = await db_service.get_user(session, target_id)
            if not user:
                await say(f":warning: User `{target_id}` not found.")
                return
            role_val = user.role.value if hasattr(user.role, "value") else str(user.role)
            cl = getattr(user, "clearance_level", None)
            cl_val = cl.value if hasattr(cl, "value") else str(cl) if cl else "public"
            name_part = f" ({user.display_name})" if user.display_name else ""
            await say(
                f":bust_in_silhouette: <@{target_id}>{name_part}\n"
                f"\u2022 Role: *{role_val}*\n"
                f"\u2022 Clearance: *{cl_val}*"
            )
        except Exception as exc:
            await say(f":x: Error: {exc}")
        return

    # --- All mutations below require admin ---
    allowed, deny_msg = await check_slack_permission(user_id, "manage_users")
    if not allowed:
        await say(deny_msg)
        return

    # --- users add <slack_user_id> <role> [display_name] ---
    if sub == "add":
        if len(parts) < 3:
            await say(
                f":warning: Usage: `{cmd} users add <slack_user_id> <role> [name]`\n"
                f"Roles: {', '.join(sorted(valid_roles))}"
            )
            return

        target_id = parts[1].strip("<@>").upper()
        role = parts[2].lower()
        display_name = " ".join(parts[3:]) if len(parts) > 3 else ""

        if role not in valid_roles:
            opts = ", ".join(sorted(valid_roles))
            await say(f":warning: Invalid role `{role}`. Choose from: {opts}")
            return

        try:
            async with get_db_session() as session:
                existing = await db_service.get_user(session, target_id)
                if existing:
                    er = existing.role
                    rv = er.value if hasattr(er, "value") else er
                    await say(
                        f":warning: User <@{target_id}> already "
                        f"exists with role *{rv}*. "
                        f"Use `{cmd} users set-role` to change.",
                    )
                    return
                await db_service.create_user(
                    session,
                    target_id,
                    display_name=display_name,
                    role=role,
                    created_by=user_id,
                )
                await session.commit()
            clear_role_cache(target_id)
            await say(f":white_check_mark: Added <@{target_id}> as *{role}*.")
        except Exception as exc:
            logger.warning("sidera_users.add_failed", error=str(exc))
            await say(f":x: Error adding user: {exc}")
        return

    # --- users set-role <slack_user_id> <role> ---
    if sub == "set-role":
        if len(parts) < 3:
            await say(f":warning: Usage: `{cmd} users set-role <slack_user_id> <role>`")
            return

        target_id = parts[1].strip("<@>").upper()
        new_role = parts[2].lower()

        if new_role not in valid_roles:
            opts = ", ".join(sorted(valid_roles))
            await say(f":warning: Invalid role `{new_role}`. Choose from: {opts}")
            return

        try:
            async with get_db_session() as session:
                updated = await db_service.update_user_role(
                    session,
                    target_id,
                    new_role,
                    changed_by=user_id,
                )
                if updated is None:
                    await say(
                        f":warning: User `{target_id}` not found. "
                        f"Add them first with `{cmd} users add`."
                    )
                    return
                await session.commit()
            clear_role_cache(target_id)
            await say(f":white_check_mark: Updated <@{target_id}> to *{new_role}*.")
        except Exception as exc:
            logger.warning("sidera_users.set_role_failed", error=str(exc))
            await say(f":x: Error updating role: {exc}")
        return

    # --- users set-clearance <slack_user_id> <level> ---
    if sub == "set-clearance":
        valid_clearance = {"public", "internal", "confidential", "restricted"}

        if len(parts) < 3:
            await say(
                f":warning: Usage: `{cmd} users set-clearance <slack_user_id> <level>`\n"
                f"Levels: {', '.join(sorted(valid_clearance))}"
            )
            return

        target_id = parts[1].strip("<@>").upper()
        new_clearance = parts[2].lower()

        if new_clearance not in valid_clearance:
            opts = ", ".join(sorted(valid_clearance))
            await say(f":warning: Invalid clearance `{new_clearance}`. Choose from: {opts}")
            return

        try:
            from src.middleware.rbac import clear_clearance_cache

            async with get_db_session() as session:
                updated = await db_service.update_user_clearance(
                    session,
                    target_id,
                    new_clearance,
                    changed_by=user_id,
                )
                if not updated:
                    await say(
                        f":warning: User `{target_id}` not found. "
                        f"Add them first with `{cmd} users add`."
                    )
                    return
                await session.commit()
            clear_clearance_cache(target_id)
            await say(f":white_check_mark: Updated <@{target_id}> clearance to *{new_clearance}*.")
        except Exception as exc:
            logger.warning("sidera_users.set_clearance_failed", error=str(exc))
            await say(f":x: Error updating clearance: {exc}")
        return

    # --- users remove <slack_user_id> ---
    if sub == "remove":
        if len(parts) < 2:
            await say(f":warning: Usage: `{cmd} users remove <slack_user_id>`")
            return

        target_id = parts[1].strip("<@>").upper()

        try:
            async with get_db_session() as session:
                success = await db_service.deactivate_user(
                    session,
                    target_id,
                    deactivated_by=user_id,
                )
                if not success:
                    await say(f":warning: User `{target_id}` not found.")
                    return
                await session.commit()
            clear_role_cache(target_id)
            await say(f":white_check_mark: Deactivated <@{target_id}>.")
        except Exception as exc:
            logger.warning("sidera_users.remove_failed", error=str(exc))
            await say(f":x: Error removing user: {exc}")
        return

    # Unknown subcommand
    await say(
        f":warning: Unknown users command `{sub}`.\n"
        f"Usage:\n"
        f"\u2022 `{cmd} users list` \u2014 List all users\n"
        f"\u2022 `{cmd} users whoami` \u2014 Show your role + clearance\n"
        f"\u2022 `{cmd} users show <user_id>` \u2014 Show a user's role + clearance\n"
        f"\u2022 `{cmd} users add <user_id> <role> [name]` \u2014 Add a user (admin)\n"
        f"\u2022 `{cmd} users set-role <user_id> <role>` \u2014 Change role (admin)\n"
        f"\u2022 `{cmd} users set-clearance <user_id> <level>` \u2014 Change clearance (admin)\n"
        f"\u2022 `{cmd} users remove <user_id>` \u2014 Deactivate user (admin)"
    )


# ---------------------------------------------------------------------------
# Steward management handler
# ---------------------------------------------------------------------------


async def _handle_steward_command(
    parts: list[str],
    say,
    user_id: str,
    cmd: str = "/sidera",
) -> None:
    """Handle ``/sidera steward <subcommand>``."""
    sub = parts[0].lower() if parts else ""

    if not sub:
        await say(
            f":shield: *Steward Commands*\n"
            f"- `{cmd} steward list` — Show all stewardship assignments\n"
            f"- `{cmd} steward show <role_id>` — Show steward for a role\n"
            f"- `{cmd} steward assign <role_id|dept_id> <@user>` — Assign a steward (admin)\n"
            f"- `{cmd} steward release <role_id|dept_id>` — Release stewardship\n"
            f"- `{cmd} steward note <role_id> <text>` — Add a steward note to role memory\n"
            f"- `{cmd} steward my-roles` — Show roles you steward"
        )
        return

    from sqlalchemy import select

    from src.db import service as db_service
    from src.db.session import get_db_session
    from src.models.schema import OrgRole

    # --- steward list ---
    if sub == "list":
        if not check_slack_permission(user_id, "view"):
            await say(":no_entry: You don't have permission to view stewardship data.")
            return
        try:
            async with get_db_session() as session:
                assignments = await db_service.list_stewardships(session)

                # Also list roles WITHOUT stewards for visibility
                all_roles_stmt = select(OrgRole).where(OrgRole.is_active == True)  # noqa: E712
                result = await session.execute(all_roles_stmt)
                all_roles = list(result.scalars().all())
                stewarded_ids = {a["scope_id"] for a in assignments if a["scope_type"] == "role"}

            if not assignments and not all_roles:
                await say(":shield: No stewardship assignments found.")
                return

            lines = [":shield: *Stewardship Assignments*"]
            for a in assignments:
                lines.append(
                    f"- `{a['scope_type']}:{a['scope_id']}` ({a['name']}) "
                    f"— steward: <@{a['steward_user_id']}>"
                )
            # Flag unassigned roles
            unassigned = [r for r in all_roles if r.role_id not in stewarded_ids]
            if unassigned:
                lines.append("\n:warning: *Unassigned roles:*")
                for r in unassigned:
                    lines.append(f"- `{r.role_id}` ({r.name})")
            await say("\n".join(lines))
        except Exception as exc:
            await say(f":x: Error listing stewardships: {exc}")
        return

    # --- steward show <role_id> ---
    if sub == "show":
        if not check_slack_permission(user_id, "view"):
            await say(":no_entry: You don't have permission to view stewardship data.")
            return
        target = parts[1] if len(parts) > 1 else ""
        if not target:
            await say(f":x: Usage: `{cmd} steward show <role_id|dept_id>`")
            return
        try:
            async with get_db_session() as session:
                steward = await db_service.resolve_steward(session, target)
                if steward:
                    await say(f":shield: Steward for `{target}`: <@{steward}>")
                else:
                    await say(f":warning: No steward assigned for `{target}`.")
        except Exception as exc:
            await say(f":x: Error: {exc}")
        return

    # --- steward assign <scope_id> <@user> ---
    if sub == "assign":
        if not check_slack_permission(user_id, "manage_org_chart"):
            await say(":no_entry: Only admins can assign stewards.")
            return
        if len(parts) < 3:
            await say(f":x: Usage: `{cmd} steward assign <role_id|dept_id> <@user>`")
            return
        scope_id = parts[1]
        # Parse Slack user mention: <@U12345> or <@U12345|name>
        raw_mention = parts[2]
        target_user = raw_mention.strip("<>@").split("|")[0]
        if not target_user:
            await say(":x: Could not parse user mention. Use `@username`.")
            return
        try:
            async with get_db_session() as session:
                # Try role first, then department
                ok = await db_service.assign_steward(
                    session,
                    "role",
                    scope_id,
                    target_user,
                    assigned_by=user_id,
                )
                if not ok:
                    ok = await db_service.assign_steward(
                        session,
                        "department",
                        scope_id,
                        target_user,
                        assigned_by=user_id,
                    )
                if ok:
                    await say(
                        f":white_check_mark: <@{target_user}> is now steward of `{scope_id}`."
                    )
                else:
                    await say(f":x: Entity `{scope_id}` not found in org chart DB.")
        except Exception as exc:
            await say(f":x: Error assigning steward: {exc}")
        return

    # --- steward release <scope_id> ---
    if sub == "release":
        scope_id = parts[1] if len(parts) > 1 else ""
        if not scope_id:
            await say(f":x: Usage: `{cmd} steward release <role_id|dept_id>`")
            return
        # Allow admin or current steward to release
        is_admin = check_slack_permission(user_id, "manage_org_chart")
        try:
            async with get_db_session() as session:
                current_steward = await db_service.resolve_steward(session, scope_id)
                if not is_admin and current_steward != user_id:
                    await say(":no_entry: Only admins or the current steward can release.")
                    return
                ok = await db_service.release_steward(
                    session,
                    "role",
                    scope_id,
                    released_by=user_id,
                )
                if not ok:
                    ok = await db_service.release_steward(
                        session,
                        "department",
                        scope_id,
                        released_by=user_id,
                    )
                if ok:
                    await say(f":white_check_mark: Stewardship released for `{scope_id}`.")
                else:
                    await say(f":x: Entity `{scope_id}` not found in org chart DB.")
        except Exception as exc:
            await say(f":x: Error releasing steward: {exc}")
        return

    # --- steward note <role_id> <text> ---
    if sub == "note":
        if len(parts) < 3:
            await say(f":x: Usage: `{cmd} steward note <role_id> <text>`")
            return
        role_id = parts[1]
        note_text = " ".join(parts[2:])
        # Allow admin or current steward of the role
        is_admin = check_slack_permission(user_id, "manage_org_chart")
        try:
            async with get_db_session() as session:
                current_steward = await db_service.resolve_steward(session, role_id)
                if not is_admin and current_steward != user_id:
                    await say(":no_entry: Only admins or the role's steward can add notes.")
                    return

                # Look up department_id from OrgRole
                stmt = select(OrgRole.department_id).where(OrgRole.role_id == role_id)
                result = await session.execute(stmt)
                dept_id = result.scalar_one_or_none() or ""

                await db_service.save_memory(
                    session,
                    user_id=user_id,
                    role_id=role_id,
                    department_id=dept_id,
                    memory_type="steward_note",
                    title=f"Steward guidance from <@{user_id}>",
                    content=note_text,
                    confidence=1.0,
                    ttl_days=0,  # save_memory will also force this for steward_note
                )
                await say(f":white_check_mark: Steward note saved for `{role_id}`:\n> {note_text}")
        except Exception as exc:
            await say(f":x: Error saving steward note: {exc}")
        return

    # --- steward my-roles ---
    if sub == "my-roles":
        try:
            async with get_db_session() as session:
                roles = await db_service.get_steward_roles(session, user_id)
            if not roles:
                await say(":shield: You are not currently a steward for any roles.")
                return
            lines = [":shield: *Your stewardship assignments:*"]
            for r in roles:
                lines.append(f"- `{r['scope_type']}:{r['scope_id']}` ({r['name']})")
            await say("\n".join(lines))
        except Exception as exc:
            await say(f":x: Error: {exc}")
        return

    # Unknown subcommand
    await say(f":x: Unknown steward command `{sub}`. Try `{cmd} steward` for available commands.")


# ---------------------------------------------------------------------------
# Slash command handler — /sidera
# ---------------------------------------------------------------------------


@slack_app.command("/sidera")
@slack_app.command("/mjz-test-agent")
@slack_app.command("/project-sidera")
async def handle_sidera_command(ack, body, client):
    """Handle the ``/sidera`` (or ``/mjz-test-agent``) slash command from Slack.

    Supported commands:
    - ``/sidera list`` — List all available skills (alias: ``list skills``).
      Includes a "Managers" section when manager roles exist.
    - ``/sidera list departments`` — List all departments.
    - ``/sidera list roles [dept_id]`` — List roles, optionally filtered by department.
    - ``/sidera run <skill_id>`` — Run a specific skill by ID.
    - ``/sidera run role:<role_id>`` — Run all skills for a role.
      Auto-redirects to ``sidera/manager.run`` if the role is a manager.
    - ``/sidera run manager:<role_id>`` — Explicitly run a manager workflow.
    - ``/sidera run dept:<dept_id>`` — Run all roles in a department.
    - ``/sidera memory <role_id>`` — Show recent memories for a role.
    - ``/sidera meeting join <url> [as <role_id>]`` — Join a meeting as a voice participant.
    - ``/sidera meeting status`` — Show active meetings.
    - ``/sidera meeting leave <bot_id>`` — Leave a meeting.
    - ``/sidera org …`` — Manage dynamic org chart (list, show, add, update,
      remove, history for departments, roles, and skills).
    - ``/sidera users …`` — Manage users (list, whoami, show, add, set-role,
      set-clearance, remove). Mutations require admin.
    - ``/sidera <free text>`` — Route a natural-language query to the
      best matching skill via the SkillRouter.

    All runs are dispatched asynchronously via Inngest events.
    """
    await ack()

    text = (body.get("text") or "").strip()
    user_id = body["user_id"]
    channel_id = body["channel_id"]
    # Use the actual command name the user typed (e.g. "/sidera" or "/mjz-test-agent")
    cmd = body.get("command", "/sidera")

    # --- Top-level RBAC gate: block unregistered users ---
    allowed, deny_msg = await check_slack_permission(user_id, "view")
    if not allowed:
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=deny_msg,
        )
        return

    if not text:
        await client.chat_postMessage(
            channel=channel_id,
            text=(
                f":robot_face: *Sidera Commands*\n"
                f"- `{cmd} list` — See all available skills\n"
                f"- `{cmd} list departments` — See all departments\n"
                f"- `{cmd} list roles [dept]` — See all roles\n"
                f"- `{cmd} run <skill_id>` — Run a specific skill\n"
                f"- `{cmd} run role:<role_id>` — Run all skills for a role\n"
                f"- `{cmd} run manager:<role_id>` — Run a manager workflow\n"
                f"- `{cmd} run dept:<dept_id>` — Run all roles in a department\n"
                f"- `{cmd} memory <role_id>` — See memories for a role\n"
                f"- `{cmd} chat <role_id> [message]` — Start a conversation with a role\n"
                f"- `{cmd} meeting join <url> [as <role_id>]` — Join a meeting\n"
                f"- `{cmd} meeting status` — Show active meetings\n"
                f"- `{cmd} meeting leave <bot_id>` — Leave a meeting\n"
                f"- `{cmd} org` — Manage dynamic org chart\n"
                f"- `{cmd} users` — Manage user roles + clearance (admin only)\n"
                f"- `{cmd} steward` — Manage agent stewardship assignments\n"
                f"- `{cmd} <question>` — Ask a question (auto-routes to best skill)"
            ),
        )
        return

    text_lower = text.lower()
    parts = text.split()
    subcommand = parts[0].lower() if parts else ""

    # --- /sidera org … ---
    if subcommand == "org":

        async def _say(msg: str) -> None:
            await client.chat_postMessage(channel=channel_id, text=msg)

        await _handle_org_command(parts[1:], _say, user_id, cmd=cmd)
        return

    # --- /sidera users … ---
    if subcommand == "users":

        async def _say_users(msg: str) -> None:
            await client.chat_postMessage(channel=channel_id, text=msg)

        await _handle_users_command(parts[1:], _say_users, user_id, cmd=cmd)
        return

    # --- /sidera steward … ---
    if subcommand == "steward":

        async def _say_steward(msg: str) -> None:
            await client.chat_postMessage(channel=channel_id, text=msg)

        await _handle_steward_command(parts[1:], _say_steward, user_id, cmd=cmd)
        return

    # --- /sidera meeting … ---
    if subcommand == "meeting":
        meeting_parts = parts[1:] if len(parts) > 1 else []
        meeting_action = meeting_parts[0].lower() if meeting_parts else ""

        if not meeting_action:
            await client.chat_postMessage(
                channel=channel_id,
                text=(
                    f":microphone: *Meeting Commands*\n"
                    f"- `{cmd} meeting join <url> [as <role_id>]`"
                    f" — Join a meeting as a voice participant\n"
                    f"- `{cmd} meeting status`"
                    f" — Show active meetings\n"
                    f"- `{cmd} meeting leave <bot_id>`"
                    f" — Leave a meeting"
                ),
            )
            return

        # --- /sidera meeting join <url> [as <role_id>] ---
        if meeting_action == "join":
            if len(meeting_parts) < 2:
                await client.chat_postMessage(
                    channel=channel_id,
                    text=f":x: Usage: `{cmd} meeting join <meeting_url> [as <role_id>]`",
                )
                return

            meeting_url = meeting_parts[1]

            # Parse optional "as <role_id>"
            role_id = "head_of_marketing"  # default to first manager found
            if len(meeting_parts) >= 4 and meeting_parts[2].lower() == "as":
                role_id = meeting_parts[3]

            # Validate URL looks like a meeting link
            if not any(
                domain in meeting_url.lower()
                for domain in ["meet.google.com", "zoom.us", "teams.microsoft.com", "webex.com"]
            ):
                await client.chat_postMessage(
                    channel=channel_id,
                    text=(
                        f":warning: `{meeting_url}` doesn't look like a meeting URL. "
                        f"Supported: Google Meet, Zoom, Teams, WebEx."
                    ),
                )
                return

            # Validate role exists
            try:
                from src.skills.db_loader import load_registry_with_db

                registry = await load_registry_with_db()
                role = registry.get_role(role_id)
                if role is None:
                    await client.chat_postMessage(
                        channel=channel_id,
                        text=(
                            f":x: Role `{role_id}` not found. "
                            f"Use `{cmd} list roles` to see available roles."
                        ),
                    )
                    return
            except Exception as exc:
                await client.chat_postMessage(
                    channel=channel_id,
                    text=f":x: Failed to validate role: {exc}",
                )
                return

            # Dispatch meeting join event (Inngest or inline fallback)
            await client.chat_postMessage(
                channel=channel_id,
                text=(
                    f":ear: *{role.name}* is joining the meeting to listen...\n"
                    f"Meeting: {meeting_url}\n"
                    f"Role: `{role_id}`\n\n"
                    f"_The bot will join in a few seconds. "
                    f"It will listen and capture the transcript for post-call analysis._"
                ),
            )
            await _dispatch_or_run_inline(
                event_name="sidera/meeting.join",
                data={
                    "meeting_url": meeting_url,
                    "role_id": role_id,
                    "user_id": user_id,
                    "channel_id": channel_id,
                },
            )
            return

        # --- /sidera meeting status ---
        if meeting_action == "status":
            try:
                from src.meetings.session import get_meeting_manager

                manager = get_meeting_manager()
                sessions = manager.get_all_active_sessions()

                if not sessions:
                    await client.chat_postMessage(
                        channel=channel_id,
                        text=":information_source: No active meetings.",
                    )
                    return

                lines = [":microphone: *Active Meetings*\n"]
                for bot_id, sess in sessions.items():
                    lines.append(
                        f"• *{sess.role_name}* (`{bot_id}`)\n"
                        f"  Meeting: {sess.meeting_url}\n"
                        f"  Turns: {sess.agent_turns} · "
                        f"Cost: ${sess.total_cost_usd:.2f}"
                    )

                await client.chat_postMessage(
                    channel=channel_id,
                    text="\n".join(lines),
                )
            except Exception as exc:
                await client.chat_postMessage(
                    channel=channel_id,
                    text=f":x: Failed to get meeting status: {exc}",
                )
            return

        # --- /sidera meeting leave <bot_id> ---
        if meeting_action == "leave":
            if len(meeting_parts) < 2:
                await client.chat_postMessage(
                    channel=channel_id,
                    text=f":x: Usage: `{cmd} meeting leave <bot_id>`",
                )
                return

            bot_id = meeting_parts[1]

            try:
                from src.meetings.session import get_meeting_manager

                manager = get_meeting_manager()
                session = manager.get_active_session(bot_id)

                if session is None:
                    await client.chat_postMessage(
                        channel=channel_id,
                        text=f":x: No active meeting found for bot `{bot_id}`.",
                    )
                    return

                await client.chat_postMessage(
                    channel=channel_id,
                    text=f":wave: *{session.role_name}* is leaving the meeting...",
                )

                result = await manager.leave(bot_id)

                await client.chat_postMessage(
                    channel=channel_id,
                    text=(
                        f":checkered_flag: *{session.role_name}* has left the meeting.\n"
                        f"Turns: {result.get('agent_turns', 0)} · "
                        f"Transcript entries: {result.get('transcript_entries', 0)} · "
                        f"Cost: ${result.get('total_cost_usd', 0):.2f}\n\n"
                        f"_Post-call summary and delegation will follow shortly._"
                    ),
                )
            except Exception as exc:
                await client.chat_postMessage(
                    channel=channel_id,
                    text=f":x: Failed to leave meeting: {exc}",
                )
            return

        # Unknown meeting subcommand
        await client.chat_postMessage(
            channel=channel_id,
            text=(
                f":x: Unknown meeting command: `{meeting_action}`\n"
                f"Try `{cmd} meeting join`, `{cmd} meeting status`, or `{cmd} meeting leave`."
            ),
        )
        return

    # --- /sidera list departments ---
    if text_lower == "list departments":
        try:
            from src.skills.db_loader import load_registry_with_db

            registry = await load_registry_with_db()
            departments = registry.list_departments()

            if not departments:
                await client.chat_postMessage(
                    channel=channel_id,
                    text=":warning: No departments found.",
                )
                return

            lines = [":office: *Departments*\n"]
            for dept in departments:
                roles = registry.list_roles(dept.id)
                lines.append(
                    f"- `{dept.id}` — {dept.name} ({len(roles)} roles)\n  _{dept.description}_"
                )
            await client.chat_postMessage(
                channel=channel_id,
                text="\n".join(lines),
            )
        except Exception as exc:
            logger.warning("sidera_command.list_departments_failed", error=str(exc))
            await client.chat_postMessage(
                channel=channel_id,
                text=f":x: Error loading departments: {exc}",
            )
        return

    # --- /sidera list roles [dept_id] ---
    if text_lower.startswith("list roles"):
        try:
            from src.skills.db_loader import load_registry_with_db

            registry = await load_registry_with_db()

            dept_filter = text[10:].strip() or None
            roles = registry.list_roles(dept_filter)

            if not roles:
                msg = "No roles found"
                if dept_filter:
                    msg += f" for department `{dept_filter}`"
                await client.chat_postMessage(
                    channel=channel_id,
                    text=f":warning: {msg}.",
                )
                return

            header = ":busts_in_silhouette: *Roles*"
            if dept_filter:
                header += f" (department: `{dept_filter}`)"
            lines = [header + "\n"]
            for role in roles:
                skill_count = len(role.briefing_skills)
                schedule_tag = f" :clock1: `{role.schedule}`" if role.schedule else ""
                lines.append(
                    f"- `{role.id}` — {role.name} "
                    f"({skill_count} skills){schedule_tag}\n"
                    f"  _{role.description}_"
                )
            await client.chat_postMessage(
                channel=channel_id,
                text="\n".join(lines),
            )
        except Exception as exc:
            logger.warning("sidera_command.list_roles_failed", error=str(exc))
            await client.chat_postMessage(
                channel=channel_id,
                text=f":x: Error loading roles: {exc}",
            )
        return

    # --- /sidera list [skills] ---
    if text_lower in ("list", "list skills"):
        try:
            from src.skills.db_loader import load_registry_with_db

            registry = await load_registry_with_db()
            skills = registry.list_all()

            if not skills:
                await client.chat_postMessage(
                    channel=channel_id,
                    text=":warning: No skills are currently loaded.",
                )
                return

            lines = [":card_index_dividers: *Available Skills*\n"]
            for skill in skills:
                schedule_tag = " :clock1: (scheduled)" if skill.schedule else ""
                role_tag = f" [role: `{skill.role_id}`]" if skill.role_id else ""
                lines.append(
                    f"- `{skill.id}` — {skill.name}{schedule_tag}{role_tag}\n"
                    f"  _{skill.description}_"
                )

            # Append managers section if any exist
            managers = registry.list_managers()
            if managers:
                lines.append("\n:briefcase: *Managers*\n")
                for mgr in managers:
                    manages_str = ", ".join(mgr.manages)
                    lines.append(f"\u2022 *{mgr.name}* (manages: {manages_str})")

            await client.chat_postMessage(
                channel=channel_id,
                text="\n".join(lines),
            )
        except Exception as exc:
            logger.warning("sidera_command.list_failed", error=str(exc))
            await client.chat_postMessage(
                channel=channel_id,
                text=f":x: Error loading skills: {exc}",
            )
        return

    # --- /sidera chat <role_id> [message] ---
    if text_lower.startswith("chat"):
        parts = text[4:].strip().split(None, 1)
        chat_role_id = parts[0] if parts else ""
        chat_message = parts[1] if len(parts) > 1 else ""

        if not chat_role_id:
            await client.chat_postMessage(
                channel=channel_id,
                text=(
                    f":warning: Usage: `{cmd} chat <role_id> [message]`\n"
                    f"Example: `{cmd} chat strategist What's our Q2 plan?`\n\n"
                    "Available roles:\n"
                ),
            )
            # List available roles
            try:
                from src.skills.db_loader import load_registry_with_db

                registry = await load_registry_with_db()
                roles = registry.list_roles()
                if roles:
                    role_lines = [f"  - `{r.id}` — {r.name}" for r in roles]
                    await client.chat_postMessage(
                        channel=channel_id,
                        text="\n".join(role_lines),
                    )
            except Exception:
                pass
            return

        # Validate the role exists
        try:
            from src.skills.db_loader import load_registry_with_db

            registry = await load_registry_with_db()
            role = registry.get_role(chat_role_id)

            if role is None:
                await client.chat_postMessage(
                    channel=channel_id,
                    text=(
                        f":warning: Role `{chat_role_id}` not found. "
                        f"Use `{cmd} list roles` to see available roles."
                    ),
                )
                return

            # Post initial message to start a thread
            initial_text = f":speech_balloon: Starting conversation with *{role.name}*"
            if chat_message:
                initial_text += f"\n\n> {chat_message}"

            result = await client.chat_postMessage(
                channel=channel_id,
                text=initial_text,
            )

            # The ts of the initial message becomes the thread_ts
            new_thread_ts = result["ts"]

            if chat_message:
                # Dispatch conversation turn immediately
                import inngest as inngest_mod

                from src.workflows.inngest_client import inngest_client as ic

                chat_user_name = await _resolve_user_display_name(
                    client,
                    user_id,
                )
                await ic.send(
                    inngest_mod.Event(
                        name="sidera/conversation.turn",
                        data={
                            "role_id": chat_role_id,
                            "channel_id": channel_id,
                            "thread_ts": new_thread_ts,
                            "user_id": user_id,
                            "message_text": chat_message,
                            "message_ts": new_thread_ts,
                            "source_user_name": chat_user_name,
                        },
                    )
                )
            else:
                # Create the thread record now so future messages route correctly
                try:
                    from src.db import service as db_service
                    from src.db.session import get_db_session

                    async with get_db_session() as session:
                        await db_service.create_conversation_thread(
                            session=session,
                            thread_ts=new_thread_ts,
                            channel_id=channel_id,
                            role_id=chat_role_id,
                            user_id=user_id,
                        )
                except Exception as exc:
                    logger.warning("sidera_command.chat_create_thread_failed", error=str(exc))

                await client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=new_thread_ts,
                    text=f"Hi! I'm the {role.name}. How can I help you today?",
                )

        except Exception as exc:
            logger.warning("sidera_command.chat_failed", error=str(exc))
            await client.chat_postMessage(
                channel=channel_id,
                text=f":x: Error starting conversation: {exc}",
            )
        return

    # --- /sidera run … (all run variants require approver+ role) ---
    if text_lower.startswith("run "):
        allowed, deny_msg = await check_slack_permission(user_id, "run_skill")
        if not allowed:
            await client.chat_postMessage(channel=channel_id, text=deny_msg)
            return

    # --- /sidera run manager:<role_id> ---
    if text_lower.startswith("run manager:"):
        role_id = text[12:].strip()
        if not role_id:
            await client.chat_postMessage(
                channel=channel_id,
                text=f":warning: Usage: `{cmd} run manager:<role_id>`",
            )
            return

        try:
            from src.skills.db_loader import load_registry_with_db

            registry = await load_registry_with_db()

            role = registry.get_role(role_id)
            if role is None:
                await client.chat_postMessage(
                    channel=channel_id,
                    text=f":warning: Role `{role_id}` not found.",
                )
                return

            if not registry.is_manager(role_id):
                await client.chat_postMessage(
                    channel=channel_id,
                    text=(
                        f":warning: Role `{role_id}` is not a manager. "
                        f"Use `{cmd} run role:<role_id>` instead."
                    ),
                )
                return

            import inngest as inngest_mod

            from src.workflows.inngest_client import inngest_client as ic

            await ic.send(
                inngest_mod.Event(
                    name="sidera/manager.run",
                    data={
                        "user_id": user_id,
                        "role_id": role_id,
                        "channel_id": channel_id,
                    },
                )
            )
            await client.chat_postMessage(
                channel=channel_id,
                text=(
                    f":briefcase: Manager `{role_id}` dispatched! "
                    "Results will appear here when ready."
                ),
            )
        except Exception as exc:
            logger.warning(
                "sidera_command.run_manager_failed",
                role_id=role_id,
                error=str(exc),
            )
            await client.chat_postMessage(
                channel=channel_id,
                text=f":x: Error dispatching manager `{role_id}`: {exc}",
            )
        return

    # --- /sidera run role:<role_id> ---
    if text_lower.startswith("run role:"):
        role_id = text[9:].strip()
        if not role_id:
            await client.chat_postMessage(
                channel=channel_id,
                text=f":warning: Usage: `{cmd} run role:<role_id>`",
            )
            return

        try:
            import inngest as inngest_mod

            from src.skills.db_loader import load_registry_with_db
            from src.workflows.inngest_client import inngest_client as ic

            # Check if this role is a manager — auto-redirect
            registry = await load_registry_with_db()
            is_mgr = registry.is_manager(role_id)

            event_name = "sidera/manager.run" if is_mgr else "sidera/role.run"

            await ic.send(
                inngest_mod.Event(
                    name=event_name,
                    data={
                        "role_id": role_id,
                        "user_id": user_id,
                        "channel_id": channel_id,
                    },
                )
            )

            if is_mgr:
                label = ":briefcase: Manager"
            else:
                label = ":busts_in_silhouette: Role"
            await client.chat_postMessage(
                channel=channel_id,
                text=(f"{label} `{role_id}` dispatched! Results will appear here when ready."),
            )
        except Exception as exc:
            logger.warning(
                "sidera_command.run_role_failed",
                role_id=role_id,
                error=str(exc),
            )
            await client.chat_postMessage(
                channel=channel_id,
                text=f":x: Error dispatching role `{role_id}`: {exc}",
            )
        return

    # --- /sidera run dept:<department_id> ---
    if text_lower.startswith("run dept:"):
        dept_id = text[9:].strip()
        if not dept_id:
            await client.chat_postMessage(
                channel=channel_id,
                text=f":warning: Usage: `{cmd} run dept:<department_id>`",
            )
            return

        try:
            import inngest as inngest_mod

            from src.workflows.inngest_client import inngest_client as ic

            await ic.send(
                inngest_mod.Event(
                    name="sidera/department.run",
                    data={
                        "department_id": dept_id,
                        "user_id": user_id,
                        "channel_id": channel_id,
                    },
                )
            )
            await client.chat_postMessage(
                channel=channel_id,
                text=(
                    f":office: Department `{dept_id}` dispatched! "
                    "Results will appear here when ready."
                ),
            )
        except Exception as exc:
            logger.warning(
                "sidera_command.run_dept_failed",
                dept_id=dept_id,
                error=str(exc),
            )
            await client.chat_postMessage(
                channel=channel_id,
                text=f":x: Error dispatching department `{dept_id}`: {exc}",
            )
        return

    # --- /sidera run <skill_id> ---
    if text_lower.startswith("run "):
        skill_id = text[4:].strip()
        if not skill_id:
            await client.chat_postMessage(
                channel=channel_id,
                text=f":warning: Usage: `{cmd} run <skill_id>`",
            )
            return

        try:
            import inngest as inngest_mod

            from src.workflows.inngest_client import inngest_client as ic

            await ic.send(
                inngest_mod.Event(
                    name="sidera/skill.run",
                    data={
                        "skill_id": skill_id,
                        "user_id": user_id,
                        "channel_id": channel_id,
                        "params": {},
                        "chain_depth": 0,
                    },
                )
            )
            await client.chat_postMessage(
                channel=channel_id,
                text=(
                    f":rocket: Skill `{skill_id}` dispatched! Results will appear here when ready."
                ),
            )
        except Exception as exc:
            logger.warning(
                "sidera_command.run_failed",
                skill_id=skill_id,
                error=str(exc),
            )
            await client.chat_postMessage(
                channel=channel_id,
                text=f":x: Error dispatching skill `{skill_id}`: {exc}",
            )
        return

    # --- /sidera heartbeat <role_id> ---
    if text_lower.startswith("heartbeat"):
        hb_role_id = text[9:].strip()
        if not hb_role_id:
            await client.chat_postMessage(
                channel=channel_id,
                text=f":warning: Usage: `{cmd} heartbeat <role_id>`",
            )
            return
        try:
            import inngest as inngest_mod

            from src.workflows.inngest_client import inngest_client as ic

            await ic.send(
                inngest_mod.Event(
                    name="sidera/heartbeat.run",
                    data={
                        "role_id": hb_role_id,
                        "user_id": user_id,
                        "channel_id": channel_id,
                    },
                )
            )
            await client.chat_postMessage(
                channel=channel_id,
                text=(
                    f":heartbeat: Triggered heartbeat for `{hb_role_id}`.\n"
                    "Results will appear here if findings are detected."
                ),
            )
        except Exception as exc:
            logger.warning(
                "sidera_command.heartbeat_failed",
                error=str(exc),
            )
            await client.chat_postMessage(
                channel=channel_id,
                text=f":x: Error triggering heartbeat: {exc}",
            )
        return

    # --- /sidera messages [role_id] ---
    if text_lower.startswith("messages"):
        msg_role_id = text[8:].strip()
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            if msg_role_id:
                # Show messages for a specific role
                async with get_db_session() as session:
                    pending = await db_service.get_pending_messages(
                        session,
                        msg_role_id,
                        limit=10,
                    )
                if not pending:
                    await client.chat_postMessage(
                        channel=channel_id,
                        text=(f":mailbox_with_no_mail: No pending messages for `{msg_role_id}`."),
                    )
                else:
                    lines = [
                        f":mailbox_with_mail: *{len(pending)} pending "
                        f"message(s) for `{msg_role_id}`*\n",
                    ]
                    for m in pending:
                        date_str = m.created_at.strftime("%b %d %H:%M") if m.created_at else "?"
                        lines.append(f"- From *{m.from_role_id}* [{date_str}]: _{m.subject}_")
                    await client.chat_postMessage(
                        channel=channel_id,
                        text="\n".join(lines),
                    )
            else:
                await client.chat_postMessage(
                    channel=channel_id,
                    text=(f":warning: Usage: `{cmd} messages <role_id>`"),
                )
        except Exception as exc:
            logger.warning(
                "sidera_command.messages_failed",
                error=str(exc),
            )
            await client.chat_postMessage(
                channel=channel_id,
                text=f":x: Error loading messages: {exc}",
            )
        return

    # --- /sidera memory <role_id> ---
    if text_lower.startswith("memory"):
        role_id_arg = text[6:].strip()
        if not role_id_arg:
            await client.chat_postMessage(
                channel=channel_id,
                text=f":warning: Usage: `{cmd} memory <role_id>`",
            )
            return
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                memories = await db_service.get_role_memories(
                    session,
                    user_id,
                    role_id_arg,
                    limit=5,
                )

            if not memories:
                await client.chat_postMessage(
                    channel=channel_id,
                    text=(f":brain: No memories found for role `{role_id_arg}`."),
                )
                return

            from datetime import datetime, timezone

            now = datetime.now(timezone.utc)
            lines = [f":brain: *Memories for `{role_id_arg}`*\n"]
            for mem in memories:
                age = ""
                if mem.created_at:
                    days = (now - mem.created_at).days
                    age = f"{days}d ago" if days > 0 else "today"
                conf = int(mem.confidence * 100) if mem.confidence else 0
                lines.append(f"- [{mem.memory_type}] {mem.title} ({conf}%, {age})")
            await client.chat_postMessage(
                channel=channel_id,
                text="\n".join(lines),
            )
        except Exception as exc:
            logger.warning(
                "sidera_command.memory_failed",
                error=str(exc),
            )
            await client.chat_postMessage(
                channel=channel_id,
                text=f":x: Error loading memories: {exc}",
            )
        return

    # --- /sidera <free text> — route via SkillRouter ---
    try:
        from src.skills.db_loader import load_registry_with_db
        from src.skills.router import SkillRouter

        registry = await load_registry_with_db()
        skill_router = SkillRouter(registry)

        match = await skill_router.route(text)

        if match is None:
            await client.chat_postMessage(
                channel=channel_id,
                text=(
                    ":thinking_face: I couldn't match your request to a specific skill. "
                    f"Try `{cmd} list` to see available skills, or rephrase your question."
                ),
            )
            return

        # Dispatch the matched skill
        import inngest as inngest_mod

        from src.workflows.inngest_client import inngest_client as ic

        await ic.send(
            inngest_mod.Event(
                name="sidera/skill.run",
                data={
                    "skill_id": match.skill.id,
                    "user_id": user_id,
                    "channel_id": channel_id,
                    "params": {"original_query": text},
                    "chain_depth": 0,
                },
            )
        )
        await client.chat_postMessage(
            channel=channel_id,
            text=(
                f":brain: Matched to skill `{match.skill.id}` "
                f"({match.confidence:.0%} confidence)\n"
                f"_{match.reasoning}_\n\n"
                "Running now — results will appear here when ready."
            ),
        )
    except Exception as exc:
        logger.warning("sidera_command.route_failed", error=str(exc))
        await client.chat_postMessage(
            channel=channel_id,
            text=f":x: Error routing your request: {exc}",
        )


# ---------------------------------------------------------------------------
# Event handlers — Conversation mode
# ---------------------------------------------------------------------------


# Supported meeting URL domains for conversational meeting join detection
_MEETING_DOMAINS = ("meet.google.com", "zoom.us", "teams.microsoft.com", "webex.com")
_MEETING_URL_RE = _re.compile(
    r"https?://(?:" + "|".join(d.replace(".", r"\.") for d in _MEETING_DOMAINS) + r")[^\s>]*",
    _re.IGNORECASE,
)


def _sanitize_meeting_url(url: str) -> str:
    """Strip query parameters and fragments from a meeting URL.

    Meeting URLs like ``https://zoom.us/j/123?pwd=SECRET`` may contain
    passwords or tokens in query params.  We only need the path portion
    for the Recall.ai bot to join.
    """
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _detect_meeting_url(text: str) -> str | None:
    """Return the first meeting URL found in *text*, or ``None``.

    Supports Google Meet, Zoom, Microsoft Teams, and WebEx links.
    The URL is sanitized to strip query parameters and fragments.
    """
    m = _MEETING_URL_RE.search(text)
    return _sanitize_meeting_url(m.group(0)) if m else None


async def _resolve_user_display_name(client, user_id: str) -> str:
    """Look up Slack display name for memory attribution.

    Returns the display_name or real_name, or empty string on failure.
    Best-effort — memory attribution is nice-to-have, not critical.
    """
    try:
        info = await client.users_info(user=user_id)
        profile = info.get("user", {}).get("profile", {})
        return (
            profile.get("display_name")
            or profile.get("real_name")
            or info.get("user", {}).get("real_name", "")
        )
    except Exception:
        return ""


@slack_app.event("app_mention")
async def handle_app_mention(event, client, say):
    """Handle @Sidera mentions to start or continue conversations.

    When a user @mentions Sidera in a channel, this handler:
    1. Checks if the message is in a thread (reply) or top-level (new conversation)
    2. For new conversations: routes to the best role via RoleRouter
    3. Creates a conversation thread record in the DB
    4. Dispatches a ``sidera/conversation.turn`` Inngest event

    Thread replies in existing Sidera conversations are handled by
    ``handle_thread_message`` instead.
    """
    channel_id = event.get("channel", "")
    user_id = event.get("user", "")
    text = event.get("text", "")
    ts = event.get("ts", "")
    thread_ts = event.get("thread_ts", "")

    # --- Top-level RBAC gate: block unregistered users ---
    allowed, deny_msg = await check_slack_permission(user_id, "chat")
    if not allowed:
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=deny_msg,
        )
        return

    # Strip the bot mention from the message text
    # Slack formats mentions as <@BOT_USER_ID>
    import re

    clean_text = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()

    if not clean_text:
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts or ts,
            text=(
                ":wave: Hi! Mention me with a question and I'll "
                "route you to the right team member. For example:\n"
                "- _What's our ROAS this week?_\n"
                "- _Talk to the strategist about our Q2 plan_\n"
                "- Or use the `chat <role_id>` command to start a direct conversation."
            ),
        )
        return

    # Resolve user display name for memory attribution (best-effort)
    source_user_name = await _resolve_user_display_name(client, user_id)

    logger.info(
        "app_mention.received",
        channel_id=channel_id,
        user_id=user_id,
        text_preview=clean_text[:80],
        thread_ts=thread_ts,
    )

    # If this is a reply in an existing thread, check if it's a Sidera thread
    if thread_ts:
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                existing = await db_service.get_conversation_thread(
                    session,
                    thread_ts,
                )
                if existing:
                    # This is a follow-up in an existing Sidera thread
                    # Add eyes reaction as typing indicator
                    try:
                        from src.connectors.slack import SlackConnector

                        connector = SlackConnector()
                        connector.add_reaction(channel_id, ts)
                    except Exception:
                        pass
                    # Extract images from the message (if any)
                    image_content = await _extract_and_download_images(
                        event,
                        client.token or "",
                    )

                    # Check for meeting URL in the reply
                    detected_url = _detect_meeting_url(clean_text)

                    # Dispatch via debounce (batches rapid-fire messages)
                    await _debounce_conversation_turn(
                        debounce_key=thread_ts,
                        role_id=existing.role_id,
                        channel_id=channel_id,
                        thread_ts=thread_ts,
                        user_id=user_id,
                        message_text=clean_text,
                        message_ts=ts,
                        source_user_name=source_user_name,
                        image_content=image_content or None,
                    )

                    # If a meeting URL was detected, also join the meeting
                    if detected_url:
                        logger.info(
                            "app_mention.thread_meeting_url_detected",
                            meeting_url=detected_url,
                            role_id=existing.role_id,
                        )
                        await client.chat_postMessage(
                            channel=channel_id,
                            thread_ts=thread_ts,
                            text=(
                                f":ear: Joining the meeting to listen...\n"
                                f"Meeting: {detected_url}\n\n"
                                f"_I'll capture the transcript and post a "
                                f"summary here when the meeting ends._"
                            ),
                        )
                        await _dispatch_or_run_inline(
                            event_name="sidera/meeting.join",
                            data={
                                "meeting_url": detected_url,
                                "role_id": existing.role_id,
                                "user_id": user_id,
                                "channel_id": channel_id,
                            },
                        )

                    return
        except Exception as exc:
            logger.warning("app_mention.thread_lookup_failed", error=str(exc))

    # New conversation — route to the best role
    try:
        from src.skills.db_loader import load_registry_with_db
        from src.skills.role_router import RoleRouter

        registry = await load_registry_with_db()
        role_router = RoleRouter(registry)

        match = await role_router.route(clean_text)

        if match is None:
            # Build a friendly list of available roles
            role_list_lines = []
            for r in registry.list_roles():
                role_list_lines.append(f"• *{r.name}* — `@Project Sidera talk to {r.id}`")
            role_block = "\n".join(role_list_lines) if role_list_lines else ""
            fallback_text = (
                "Hey! I'd love to help but I'm not sure who on the team "
                "is the best fit for that.\n\n"
                "Here's who's available:\n"
                f"{role_block}\n\n"
                "Just mention me with what you need and I'll connect you "
                "to the right person!"
            )
            await client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts or ts,
                text=fallback_text,
            )
            return

        role = match.role
        effective_thread_ts = thread_ts or ts

        # Add eyes reaction as typing indicator
        try:
            from src.connectors.slack import SlackConnector

            connector = SlackConnector()
            connector.add_reaction(channel_id, ts)
            logger.info("app_mention.eyes_added", channel=channel_id, ts=ts)
        except Exception as react_exc:
            logger.warning("app_mention.eyes_failed", error=str(react_exc))

        # Extract images from the message (if any)
        image_content = await _extract_and_download_images(
            event,
            client.token or "",
        )

        # Check for meeting URL — if present, also dispatch meeting join
        detected_url = _detect_meeting_url(clean_text)

        # Dispatch via debounce (batches rapid-fire messages)
        await _debounce_conversation_turn(
            debounce_key=effective_thread_ts,
            role_id=role.id,
            channel_id=channel_id,
            thread_ts=effective_thread_ts,
            user_id=user_id,
            message_text=clean_text,
            message_ts=ts,
            source_user_name=source_user_name,
            image_content=image_content or None,
        )

        # If a meeting URL was detected, also join the meeting
        if detected_url:
            logger.info(
                "app_mention.meeting_url_detected",
                meeting_url=detected_url,
                role_id=role.id,
            )
            await client.chat_postMessage(
                channel=channel_id,
                thread_ts=effective_thread_ts,
                text=(
                    f":ear: *{role.name}* is joining the meeting to listen...\n"
                    f"Meeting: {detected_url}\n\n"
                    f"_I'll capture the transcript and post a summary "
                    f"here when the meeting ends._"
                ),
            )
            await _dispatch_or_run_inline(
                event_name="sidera/meeting.join",
                data={
                    "meeting_url": detected_url,
                    "role_id": role.id,
                    "user_id": user_id,
                    "channel_id": channel_id,
                },
            )

        logger.info(
            "app_mention.dispatched",
            role_id=role.id,
            confidence=match.confidence,
            thread_ts=effective_thread_ts,
        )

    except Exception as exc:
        logger.error("app_mention.error", error=str(exc))
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts or ts,
            text=f":x: Sorry, I encountered an error: {exc}",
        )


@slack_app.event("message")
async def handle_thread_message(event, client):
    """Handle messages in Slack threads for ongoing conversations.

    This fires for ALL messages. We immediately exit unless:
    1. The message is in a thread (has ``thread_ts``)
    2. The thread is a known Sidera conversation thread
    3. The message is from a human (not a bot)
    4. The message is not a changed/deleted subtypes

    If all checks pass, dispatches a ``sidera/conversation.turn`` event.
    """
    # Skip non-thread messages
    thread_ts = event.get("thread_ts")
    if not thread_ts:
        return

    # Skip bot messages
    if event.get("bot_id") or event.get("subtype") in (
        "bot_message",
        "message_changed",
        "message_deleted",
    ):
        return

    channel_id = event.get("channel", "")
    user_id = event.get("user", "")
    text = event.get("text", "")
    ts = event.get("ts", "")

    # --- RBAC gate: block unregistered users from thread conversations ---
    allowed, _ = await check_slack_permission(user_id, "chat")
    if not allowed:
        return  # Silently ignore — no ephemeral in thread message handler

    # Skip if the message contains a bot mention (handled by app_mention)
    import re

    if re.search(r"<@[A-Z0-9]+>", text):
        return

    # Check if this thread is a known Sidera conversation
    logger.info(
        "thread_message.checking",
        thread_ts=thread_ts,
        channel_id=channel_id,
        user_id=user_id,
        text_preview=text[:50],
    )
    try:
        from src.db import service as db_service
        from src.db.session import get_db_session

        async with get_db_session() as session:
            thread = await db_service.get_conversation_thread(
                session,
                thread_ts,
            )
            if thread is None:
                # Not a Sidera thread — ignore
                logger.info("thread_message.not_sidera_thread", thread_ts=thread_ts)
                return

            if not thread.is_active:
                logger.info("thread_message.inactive_thread", thread_ts=thread_ts)
                return

        # Strip any bot mention that might have slipped through
        clean_text = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()
        if not clean_text:
            return

        # Add eyes reaction as typing indicator
        try:
            from src.connectors.slack import SlackConnector

            connector = SlackConnector()
            connector.add_reaction(channel_id, ts)
        except Exception:
            pass

        # Resolve user display name for memory attribution
        source_user_name = await _resolve_user_display_name(client, user_id)

        # Extract images from the message (if any)
        image_content = await _extract_and_download_images(
            event,
            client.token or "",
        )

        # Check for meeting URL — if present, also dispatch meeting join
        detected_url = _detect_meeting_url(clean_text)

        # Dispatch via debounce (batches rapid-fire messages)
        await _debounce_conversation_turn(
            debounce_key=thread_ts,
            role_id=thread.role_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
            message_text=clean_text,
            message_ts=ts,
            source_user_name=source_user_name,
            image_content=image_content or None,
        )

        # If a meeting URL was detected, also join the meeting
        if detected_url:
            logger.info(
                "thread_message.meeting_url_detected",
                meeting_url=detected_url,
                role_id=thread.role_id,
            )
            # Load role name for the notification message
            try:
                from src.skills.db_loader import load_registry_with_db

                reg = await load_registry_with_db()
                role_obj = reg.get_role(thread.role_id)
                role_name = role_obj.name if role_obj else thread.role_id
            except Exception:
                role_name = thread.role_id

            from src.connectors.slack import SlackConnector

            try:
                sc = SlackConnector()
                sc.post_thread_reply(
                    channel_id,
                    thread_ts,
                    (
                        f":ear: *{role_name}* is joining the meeting to listen...\n"
                        f"Meeting: {detected_url}\n\n"
                        f"_I'll capture the transcript and post a summary "
                        f"here when the meeting ends._"
                    ),
                )
            except Exception:
                pass

            await _dispatch_or_run_inline(
                event_name="sidera/meeting.join",
                data={
                    "meeting_url": detected_url,
                    "role_id": thread.role_id,
                    "user_id": user_id,
                    "channel_id": channel_id,
                },
            )

        logger.info(
            "thread_message.dispatched",
            role_id=thread.role_id,
            thread_ts=thread_ts,
            user_id=user_id,
        )

    except Exception as exc:
        logger.warning("thread_message.error", error=str(exc))


# ---------------------------------------------------------------------------
# FastAPI integration
# ---------------------------------------------------------------------------

slack_handler = AsyncSlackRequestHandler(slack_app)

router = APIRouter(tags=["Slack"])


@router.post("/slack/events")
async def slack_events(request: Request) -> Response:
    """Route all Slack events and interactions through Bolt.

    Slack sends all interactive payloads (button clicks, modals, etc.)
    to this single endpoint. The ``AsyncSlackRequestHandler`` validates
    the request signature and dispatches to the appropriate action handler.
    """
    return await slack_handler.handle(request)
