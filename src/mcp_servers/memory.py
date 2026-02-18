"""Memory MCP tools for Sidera agents.

Provides tools that agents can use to explicitly save memories during
conversations, and a helper function for LLM-powered automatic memory
extraction from conversation turns.

Tools:
    1. ``save_memory`` — Save a specific piece of information to persistent
       role memory. Used when a user tells the agent to "remember" something
       or when the agent identifies important context worth preserving.

Automatic extraction:
    ``extract_conversation_memories_llm()`` — Haiku-powered analysis of a
    conversation turn that identifies noteworthy facts, preferences, context,
    and lessons worth remembering. Called automatically after each conversation
    turn (both inline and Inngest paths).

Uses ``contextvars.ContextVar`` to carry the role/user context into the
tool handler (same pattern as ``actions.py``, ``evolution.py``, ``delegation.py``).

Usage::

    from src.mcp_servers.memory import (
        set_memory_context, clear_memory_context,
        extract_conversation_memories_llm,
    )
"""

from __future__ import annotations

import contextvars
import json
from typing import Any

import structlog

from src.agent.tool_registry import tool
from src.mcp_servers.helpers import error_response, text_response

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Memory context — per-async-task via contextvars (concurrency-safe)
# ---------------------------------------------------------------------------

_memory_context_var: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "memory_context", default=None
)


def set_memory_context(
    role_id: str,
    department_id: str,
    user_id: str,
    source_user_name: str = "",
) -> None:
    """Set the memory context for the current conversation turn.

    Called before running an agent's conversation turn so the save_memory
    tool knows which role/user is active.

    Args:
        role_id: The role ID executing this turn.
        department_id: The department the role belongs to.
        user_id: The user interacting with the agent.
        source_user_name: Display name of the Slack user (for memory
            attribution — "Michael told you: ...").
    """
    _memory_context_var.set(
        {
            "role_id": role_id,
            "department_id": department_id,
            "user_id": user_id,
            "source_user_name": source_user_name,
        }
    )


def clear_memory_context() -> None:
    """Clear memory context after a conversation turn completes."""
    _memory_context_var.set(None)


# ---------------------------------------------------------------------------
# Tool: save_memory
# ---------------------------------------------------------------------------

_MAX_MEMORIES_PER_TURN = 5  # Prevent runaway memory creation

_memory_count_var: contextvars.ContextVar[int] = contextvars.ContextVar(
    "memory_save_count",
    default=0,
)


@tool(
    name="save_memory",
    description=(
        "Save an important piece of information to your persistent memory. "
        "Use this when the user tells you to 'remember' something, shares "
        "preferences, corrections, account-specific context, or any fact "
        "you should retain across conversations. Memories persist indefinitely "
        "and are automatically loaded into your context on future runs. "
        "Max 5 per conversation turn."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": (
                    "A short, descriptive title for this memory (max 100 chars). "
                    "Should be scannable — e.g. 'Test account 8382412741 shows $0 budget', "
                    "'Client prefers conservative bid changes', "
                    "'Q4 budget freeze until Jan 15'."
                ),
            },
            "content": {
                "type": "string",
                "description": (
                    "The full memory content (1-3 sentences). Include specifics: "
                    "account IDs, dates, thresholds, preferences, reasoning. "
                    "This will be injected into your context on future runs."
                ),
            },
            "memory_type": {
                "type": "string",
                "enum": [
                    "insight",
                    "lesson",
                    "pattern",
                    "decision",
                    "relationship",
                    "commitment",
                ],
                "description": (
                    "Type of memory: 'insight' for facts/context/preferences, "
                    "'lesson' for mistakes or things to avoid, "
                    "'pattern' for recurring trends, "
                    "'decision' for agreed-upon strategies or rules, "
                    "'relationship' for interpersonal dynamics and "
                    "communication style, "
                    "'commitment' for promises or planned actions "
                    "(e.g. 'I will investigate this tomorrow')."
                ),
            },
        },
        "required": ["title", "content", "memory_type"],
    },
)
async def save_memory(args: dict[str, Any]) -> dict[str, Any]:
    """Save a memory entry for the current role."""
    title = args.get("title", "").strip()
    content = args.get("content", "").strip()
    memory_type = args.get("memory_type", "insight")

    if not title or not content:
        return error_response("Both title and content are required.")

    if memory_type == "steward_note":
        return error_response("Steward notes can only be created by human stewards, not by agents.")

    if len(title) > 200:
        title = title[:200]

    _valid_types = (
        "insight",
        "lesson",
        "pattern",
        "decision",
        "relationship",
        "commitment",
    )
    if memory_type not in _valid_types:
        memory_type = "insight"

    # -- Check context --
    ctx = _memory_context_var.get()
    if ctx is None:
        return error_response(
            "Memory saving not available. This tool is only available during conversation mode."
        )

    # -- Check count limit --
    count = _memory_count_var.get()
    if count >= _MAX_MEMORIES_PER_TURN:
        return error_response(
            f"Maximum {_MAX_MEMORIES_PER_TURN} memories per turn reached. "
            f"Save additional memories in the next turn."
        )

    role_id = ctx["role_id"]
    department_id = ctx["department_id"]
    user_id = ctx["user_id"]
    source_name = ctx.get("source_user_name", "")

    try:
        from datetime import date

        from src.db import service as db_service
        from src.db.session import get_db_session

        today = date.today()
        attribution = f" (from {source_name})" if source_name else ""

        async with get_db_session() as session:
            await db_service.save_memory(
                session=session,
                user_id=user_id,
                role_id=role_id,
                department_id=department_id,
                memory_type=memory_type,
                title=title,
                content=f"[{today}] [Conversation]{attribution} {content}",
                confidence=1.0,  # Explicit user request = high confidence
                source_skill_id=f"conversation:{role_id}",
                source_run_date=today,
                evidence={
                    "source": "save_memory_tool",
                    "user_id": user_id,
                    "source_user_name": source_name,
                },
            )

        _memory_count_var.set(count + 1)

        logger.info(
            "save_memory.success",
            role_id=role_id,
            memory_type=memory_type,
            title=title[:50],
        )

        return text_response(
            f"Saved to memory: **{title}**\n"
            f"Type: {memory_type} | "
            f"This will be available in all future conversations."
        )

    except Exception as exc:
        logger.exception(
            "save_memory.error",
            role_id=role_id,
            error=str(exc),
        )
        return error_response(f"Failed to save memory: {exc}")


# ---------------------------------------------------------------------------
# Automatic conversation memory extraction (LLM-powered)
# ---------------------------------------------------------------------------

_CONVERSATION_MEMORY_PROMPT = """\
You are analyzing a conversation turn between a user and an AI agent \
(role: {role_name}, role_id: {role_id}).

Review the conversation below and extract any facts, preferences, context, \
or lessons that are worth remembering for future conversations. Focus on:

1. **User preferences** — how they like reports, thresholds, communication style
2. **Account/business context** — account IDs, budget info, goals, constraints, \
   seasonal patterns, client relationships
3. **Corrections** — things the user corrected or clarified about data, processes
4. **Strategic decisions** — agreed-upon strategies, rules, or approaches
5. **Lessons** — things that went wrong, data that was missing, approaches that \
   didn't work
6. **Commitments and promises** — things you or the user committed to doing \
   (e.g. "I'll investigate this tomorrow", "we agreed to revisit budgets next week", \
   "you said you'd send me the new targets")
7. **Upcoming events or deadlines** — scheduled upgrades, launches, freezes, \
   maintenance windows, meetings
8. **Team/organizational changes** — personnel changes, role assignments, \
   responsibility shifts
9. **Interpersonal dynamics** — how this person communicates, nicknames they \
   use for you, energy level, rapport, whether they prefer formal or informal tone

Be aggressive about extracting — if the user shares ANY fact about their \
business, infrastructure, upcoming events, deadlines, or preferences, extract \
it even if confidence is moderate. It is better to over-extract than to miss \
something the user would expect you to remember later.

Do NOT extract:
- Generic greetings or small talk ("hi", "thanks", "ok")
- Things already obvious from the role's existing context
- Repetitions of information the agent already provided

Respond with a JSON array of memories. Each memory should have:
- "type": one of "insight", "lesson", "pattern", "decision", "relationship", "commitment"
- "title": short descriptive title (max 100 chars)
- "content": 1-2 sentence explanation with specifics
- "confidence": 0.0-1.0 how valuable this is to remember long-term

Use "relationship" type for interpersonal dynamics — how the person \
communicates with you, nicknames they use, energy level, rapport indicators, \
communication preferences. Examples: "Michael has a high-energy informal \
style, calls me 'Ops', prefers direct answers without filler", "Sarah is \
methodical and prefers step-by-step explanations with data backing each point"

If there's nothing worth remembering from this turn, return an empty array [].

Return ONLY the JSON array. No markdown, no explanation.\
"""


async def extract_conversation_memories_llm(
    role_id: str,
    role_name: str,
    department_id: str,
    user_message: str,
    agent_response: str,
    user_id: str,
    thread_history: list[dict[str, Any]] | None = None,
    source_user_name: str = "",
) -> list[dict[str, Any]]:
    """Extract memories from a conversation turn using Haiku.

    Runs a cheap Haiku call (~$0.005-0.01) to analyze the conversation
    and identify facts, preferences, and lessons worth persisting.

    Args:
        role_id: The role handling this conversation.
        role_name: Human-readable name of the role.
        department_id: Department the role belongs to.
        user_message: The user's message this turn.
        agent_response: The agent's response this turn.
        user_id: User identifier.
        thread_history: Optional recent thread history for context.
        source_user_name: Display name of the user who sent the message
            (for WHO attribution in memory content).

    Returns:
        List of memory entry dicts ready for ``db_service.save_memory()``.
        Empty list on error or if nothing worth remembering.
    """
    from src.agent.api_client import run_agent_loop
    from src.config import settings
    from src.utils.input_boundary import wrap_untrusted

    # Build conversation context (truncated to keep costs low).
    # User messages are wrapped in boundary tags to prevent prompt injection.
    conversation = (
        f"User: {wrap_untrusted(user_message[:1500])}\n\n"
        f"Agent: {agent_response[:1500]}"
    )

    # Add recent thread context if available (last 3 messages)
    if thread_history:
        recent = thread_history[-3:]
        history_parts: list[str] = []
        for m in recent:
            text = (m.get("text", "") or "")[:300]
            if m.get("is_bot"):
                history_parts.append(f"Agent: {text}")
            else:
                history_parts.append(f"User: {wrap_untrusted(text)}")
        history_text = "\n".join(history_parts)
        conversation = (
            f"Recent thread context:\n{history_text}\n\n---\n\nCurrent turn:\n{conversation}"
        )

    prompt = (
        _CONVERSATION_MEMORY_PROMPT.format(
            role_name=role_name,
            role_id=role_id,
        )
        + f"\n\n---\n\n{conversation}"
    )

    try:
        from src.llm.provider import TaskType

        result = await run_agent_loop(
            system_prompt=(
                "You are a memory extraction system. Respond only with "
                "a valid JSON array. No markdown, no explanation."
            ),
            user_prompt=prompt,
            model=settings.model_fast,  # Haiku — cheap and fast
            tools=None,
            max_turns=1,
            task_type=TaskType.MEMORY_EXTRACTION,
        )
    except Exception:
        logger.warning("conversation_memory.llm_error", role_id=role_id)
        return []

    # Parse the JSON response
    try:
        raw_text = result.text.strip()
        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3].strip()

        observations = json.loads(raw_text)
        if not isinstance(observations, list):
            return []
    except (json.JSONDecodeError, ValueError):
        logger.warning(
            "conversation_memory.parse_error",
            role_id=role_id,
            raw_text=result.text[:200],
        )
        return []

    # Convert to memory entries
    from datetime import date

    memories: list[dict[str, Any]] = []
    today = date.today()

    attribution = f" (from {source_user_name})" if source_user_name else ""

    _extract_valid_types = (
        "insight",
        "lesson",
        "pattern",
        "decision",
        "relationship",
        "commitment",
    )
    for obs in observations[:3]:  # Max 3 auto-extracted per turn
        obs_type = obs.get("type", "insight")
        if obs_type not in _extract_valid_types:
            obs_type = "insight"

        title = str(obs.get("title", ""))[:100]
        content = str(obs.get("content", ""))
        confidence = min(1.0, max(0.0, float(obs.get("confidence", 0.6))))

        # Only save if at least moderately confident
        if confidence < 0.3:
            continue

        if not title or not content:
            continue

        memories.append(
            {
                "role_id": role_id,
                "department_id": department_id,
                "memory_type": obs_type,
                "title": title,
                "content": f"[{today}] [Conversation]{attribution} {content}",
                "confidence": confidence,
                "source_skill_id": f"conversation:{role_id}",
                "source_run_date": today,
                "evidence": {
                    "source": "conversation_auto_extract",
                    "user_id": user_id,
                    "source_user_name": source_user_name,
                },
            }
        )

    logger.info(
        "conversation_memory.extracted",
        role_id=role_id,
        memories_count=len(memories),
    )

    return memories


# ---------------------------------------------------------------------------
# Tool: load_memory_detail — load full content of specific memories by ID
# ---------------------------------------------------------------------------


@tool(
    name="load_memory_detail",
    description=(
        "Load the full content of specific memories by their IDs. Use this when "
        "you have a memory index (compact listing of titles) and need to read the "
        "full content of particular memories before making decisions. Max 10 per call."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "memory_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": (
                    "List of memory IDs to load (max 10). "
                    "IDs are shown in brackets in the memory index, e.g. [42]."
                ),
            },
        },
        "required": ["memory_ids"],
    },
)
async def load_memory_detail(args: dict[str, Any]) -> dict[str, Any]:
    """Load full content of specific memories by their IDs."""
    memory_ids = args.get("memory_ids", [])

    if not memory_ids:
        return error_response("memory_ids is required and must be non-empty.")

    if len(memory_ids) > 10:
        memory_ids = memory_ids[:10]

    # Validate all IDs are integers
    try:
        memory_ids = [int(mid) for mid in memory_ids]
    except (TypeError, ValueError):
        return error_response("All memory_ids must be integers.")

    ctx = _memory_context_var.get()
    if ctx is None:
        return error_response(
            "Memory loading not available. This tool is only available during conversation mode."
        )

    role_id = ctx["role_id"]
    user_id = ctx["user_id"]

    try:
        from src.db import service as db_service
        from src.db.session import get_db_session

        sections: list[str] = []
        async with get_db_session() as session:
            for mid in memory_ids:
                # Use search with no keyword to get by ID range
                memories = await db_service.search_role_memories(
                    session,
                    user_id=user_id,
                    role_id=role_id,
                    limit=50,
                )
                for mem in memories:
                    mem_id = getattr(mem, "id", None)
                    if mem_id == mid:
                        mtype = getattr(mem, "memory_type", "unknown")
                        title = getattr(mem, "title", "")
                        content = getattr(mem, "content", "")
                        confidence = getattr(mem, "confidence", 0.0)
                        created = getattr(mem, "created_at", None)
                        date_str = str(created)[:10] if created else "unknown"
                        sections.append(
                            f"## [{mid}] {title}\n"
                            f"Type: {mtype} | Confidence: {confidence} | Date: {date_str}\n\n"
                            f"{content}"
                        )
                        break

        if not sections:
            return text_response("No memories found matching the provided IDs.")

        return text_response("\n\n---\n\n".join(sections))

    except Exception as exc:
        logger.warning("load_memory_detail.error", error=str(exc))
        return error_response(f"Failed to load memories: {exc}")
