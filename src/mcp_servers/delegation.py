"""Delegation and peer consultation MCP tools for manager roles.

Provides two tools for manager-to-manager and manager-to-subordinate
communication during conversations:

    1. ``delegate_to_role`` — Delegate a task to a managed sub-role
    2. ``consult_peer`` — Consult another department head as an equal

Both tools run a complete inner agent loop as the target role (full
persona, context, memory, tools) and return the result to the caller.

Uses ``contextvars.ContextVar`` to carry delegation context into the
tool handlers (same pattern as ``actions.py`` and ``evolution.py``).

Usage::

    from src.mcp_servers.delegation import (
        set_delegation_context, clear_delegation_context,
        get_delegation_results,
    )
"""

from __future__ import annotations

import contextvars
from typing import Any

import structlog

from src.agent.tool_registry import tool
from src.mcp_servers.helpers import error_response, text_response

logger = structlog.get_logger(__name__)

# Max delegations per single conversation turn (prevents runaway costs)
_MAX_DELEGATIONS_PER_TURN = 3

# ---------------------------------------------------------------------------
# Delegation context — per-async-task via contextvars (concurrency-safe)
# ---------------------------------------------------------------------------

_delegation_context_var: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "delegation_context", default=None
)

_delegation_results_var: contextvars.ContextVar[list[dict[str, Any]]] = contextvars.ContextVar(
    "delegation_results", default=[]
)

_delegation_count_var: contextvars.ContextVar[int] = contextvars.ContextVar(
    "delegation_count",
    default=0,
)


def set_delegation_context(
    role_id: str,
    registry: Any,
) -> None:
    """Set the delegation context for the current conversation turn.

    Called before running a manager's agent turn so the delegation tool
    knows which role is the caller and has access to the registry.

    Args:
        role_id: The manager role ID.
        registry: The loaded ``SkillRegistry`` instance.
    """
    _delegation_context_var.set({"role_id": role_id, "registry": registry})
    _delegation_results_var.set([])
    _delegation_count_var.set(0)


def clear_delegation_context() -> None:
    """Clear delegation context after a conversation turn completes."""
    _delegation_context_var.set(None)
    _delegation_count_var.set(0)


def get_delegation_results() -> list[dict[str, Any]]:
    """Return and clear accumulated delegation results for this turn.

    Returns:
        List of dicts with ``role_id``, ``cost``, ``success`` for each
        delegation that occurred during this turn.
    """
    results = list(_delegation_results_var.get())
    _delegation_results_var.set([])
    return results


# ---------------------------------------------------------------------------
# Tool: delegate_to_role
# ---------------------------------------------------------------------------


@tool(
    name="delegate_to_role",
    description=(
        "Delegate a task to one of your managed team members. "
        "The team member runs the task with their full expertise, "
        "tools, and context, then returns the result to you. "
        "Use this when a request matches a team member's "
        "specialization (see 'Your Team' section in your context). "
        "You can delegate up to 3 times per message."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "role_id": {
                "type": "string",
                "description": (
                    "The ID of the team member to delegate to "
                    "(e.g. 'performance_media_buyer', 'reporting_analyst')."
                ),
            },
            "task": {
                "type": "string",
                "description": (
                    "A clear, specific task description. Include "
                    "relevant context: account IDs, date ranges, "
                    "campaign names, specific questions. The team "
                    "member will execute this with full tool access."
                ),
            },
        },
        "required": ["role_id", "task"],
    },
)
async def delegate_to_role(args: dict[str, Any]) -> dict[str, Any]:
    """Execute a task as a sub-role and return the result."""
    role_id = args.get("role_id", "")
    task = args.get("task", "")

    if not role_id or not task:
        return error_response("Both role_id and task are required.")

    # -- Check delegation context --
    ctx = _delegation_context_var.get()
    if ctx is None:
        return error_response(
            "Delegation not available. This tool is only "
            "available to manager roles in conversation mode."
        )

    manager_role_id = ctx["role_id"]
    registry = ctx["registry"]

    # -- Check delegation count --
    count = _delegation_count_var.get()
    if count >= _MAX_DELEGATIONS_PER_TURN:
        return error_response(
            f"Maximum {_MAX_DELEGATIONS_PER_TURN} delegations per "
            f"turn reached. Summarize what you have so far."
        )

    # -- Validate manager manages this role --
    manager_role = registry.get_role(manager_role_id)
    if manager_role is None:
        return error_response(f"Manager role '{manager_role_id}' not found.")

    if role_id not in (manager_role.manages or ()):
        managed_ids = ", ".join(manager_role.manages or ())
        return error_response(
            f"You cannot delegate to '{role_id}'. Your team members are: {managed_ids}"
        )

    # -- Look up sub-role --
    sub_role = registry.get_role(role_id)
    if sub_role is None:
        return error_response(f"Role '{role_id}' not found in registry.")

    logger.info(
        "delegation.start",
        manager=manager_role_id,
        sub_role=role_id,
        task_preview=task[:100],
    )

    # -- Build sub-role context --
    try:
        from src.agent.api_client import run_agent_loop
        from src.agent.prompts import DELEGATION_TASK_SUPPLEMENT, get_base_system_prompt
        from src.agent.tool_registry import get_global_registry
        from src.config import settings
        from src.skills.executor import compose_role_context

        dept = registry.get_department(sub_role.department_id)

        # Load sub-role memory (best-effort)
        memory_ctx = ""
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session
            from src.skills.memory import (
                compose_memory_context,
                filter_superseded_memories,
            )

            async with get_db_session() as session:
                memories = await db_service.get_role_memories(
                    session,
                    "system",
                    role_id,
                    limit=10,
                )
                superseded = await db_service.get_superseded_memory_ids(
                    session,
                    "system",
                    role_id,
                )
                memories = filter_superseded_memories(memories, superseded)
                if memories:
                    memory_ctx = compose_memory_context(memories)
        except Exception:
            pass  # Memory is best-effort

        sub_role_context = compose_role_context(
            department=dept,
            role=sub_role,
            memory_context=memory_ctx,
            registry=registry,
        )

        # Build system prompt
        system_prompt = get_base_system_prompt()
        if sub_role_context:
            system_prompt += "\n\n" + sub_role_context
        system_prompt += "\n\n" + DELEGATION_TASK_SUPPLEMENT

        # Inject manager clearance context so sub-role filters its response
        manager_clearance = getattr(manager_role, "clearance_level", "internal")
        from src.agent.prompts import build_clearance_context

        clearance_ctx = build_clearance_context(manager_clearance)
        if clearance_ctx:
            system_prompt += (
                f"\n\n# Delegation Clearance\n"
                f"The requesting manager ({manager_role_id}) has "
                f"**{manager_clearance}** clearance. Tailor your response "
                f"to not exceed their clearance level.\n" + clearance_ctx
            )

        # Get tools for sub-role (exclude delegation + orchestration tools — no recursion)
        tool_registry = get_global_registry()
        all_tools = tool_registry.get_tool_definitions()
        _no_recurse = {"delegate_to_role", "consult_peer", "orchestrate_task"}
        sub_tools = [t for t in all_tools if t["name"] not in _no_recurse]

        # Run the sub-role agent
        from src.llm.provider import TaskType

        _budget = (
            settings.extended_thinking_budget_tokens if settings.extended_thinking_enabled else None
        )
        result = await run_agent_loop(
            system_prompt=system_prompt,
            user_prompt=task,
            model=settings.model_standard,
            tools=sub_tools,
            max_turns=settings.conversation_tool_calls_per_turn,
            task_type=TaskType.DELEGATION,
            thinking_budget=_budget,
        )

        # Track cost
        _delegation_count_var.set(count + 1)
        results_list = _delegation_results_var.get()
        results_list.append(
            {
                "role_id": role_id,
                "cost": result.cost,
                "success": not result.is_error,
                "turn_count": result.turn_count,
            }
        )
        _delegation_results_var.set(results_list)

        logger.info(
            "delegation.complete",
            manager=manager_role_id,
            sub_role=role_id,
            cost=result.cost.get("total_cost_usd", 0),
            turns=result.turn_count,
            is_error=result.is_error,
        )

        # Save relationship memories for both sides (best-effort)
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            outcome = "completed" if not result.is_error else "failed"
            async with get_db_session() as session:
                # Manager's memory about delegating
                await db_service.save_memory(
                    session=session,
                    user_id="__system__",
                    role_id=manager_role_id,
                    department_id=getattr(manager_role, "department_id", ""),
                    memory_type="relationship",
                    title=f"Delegated to {role_id}: {task[:60]}",
                    content=(
                        f"Delegated task to {sub_role.name} ({role_id}): "
                        f"{task[:150]}. Outcome: {outcome}."
                    ),
                    confidence=0.6,
                    source_skill_id=f"delegation:{manager_role_id}",
                    source_role_id=role_id,
                )
                # Sub-role's memory about receiving delegation
                await db_service.save_memory(
                    session=session,
                    user_id="__system__",
                    role_id=role_id,
                    department_id=sub_role.department_id,
                    memory_type="relationship",
                    title=f"Received delegation from {manager_role_id}",
                    content=(
                        f"Received task from {manager_role.name} ({manager_role_id}): "
                        f"{task[:150]}. Outcome: {outcome}."
                    ),
                    confidence=0.6,
                    source_skill_id=f"delegation:{role_id}",
                    source_role_id=manager_role_id,
                )
        except Exception:
            pass  # Relationship memory is non-critical

        if result.is_error:
            return text_response(
                f"**{sub_role.name}** encountered an error while "
                f"executing the task. They reported:\n\n{result.text}"
            )

        return text_response(f"**{sub_role.name}** completed the task:\n\n{result.text}")

    except Exception as exc:
        logger.exception(
            "delegation.error",
            manager=manager_role_id,
            sub_role=role_id,
            error=str(exc),
        )
        # Track failed delegation
        _delegation_count_var.set(count + 1)
        results_list = _delegation_results_var.get()
        results_list.append(
            {
                "role_id": role_id,
                "cost": {},
                "success": False,
            }
        )
        _delegation_results_var.set(results_list)

        return error_response(f"Failed to delegate to {sub_role.name}: {exc}")


# ---------------------------------------------------------------------------
# Tool: consult_peer
# ---------------------------------------------------------------------------


@tool(
    name="consult_peer",
    description=(
        "Consult another department head as a peer. "
        "They will respond with their full expertise, tools, "
        "and data from their domain. Use this for cross-department "
        "questions — e.g. asking the Head of IT about system issues, "
        "or a peer about data in their domain. "
        "They respond as equals, not subordinates. "
        "Counts toward the same 3-per-turn delegation limit."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "role_id": {
                "type": "string",
                "description": (
                    "The ID of the peer department head to consult "
                    "(e.g. 'head_of_it', 'head_of_marketing')."
                ),
            },
            "question": {
                "type": "string",
                "description": (
                    "Your question or request for their input. "
                    "Be specific about what you need — include "
                    "context about why you're asking so they can "
                    "give a relevant, data-backed answer."
                ),
            },
        },
        "required": ["role_id", "question"],
    },
)
async def consult_peer(args: dict[str, Any]) -> dict[str, Any]:
    """Consult a peer manager and return their response."""
    role_id = args.get("role_id", "")
    question = args.get("question", "")

    if not role_id or not question:
        return error_response("Both role_id and question are required.")

    # -- Check delegation context --
    ctx = _delegation_context_var.get()
    if ctx is None:
        return error_response(
            "Peer consultation not available. This tool is only "
            "available to manager roles in conversation mode."
        )

    caller_role_id = ctx["role_id"]
    registry = ctx["registry"]

    # -- Check delegation count (shared with delegate_to_role) --
    count = _delegation_count_var.get()
    if count >= _MAX_DELEGATIONS_PER_TURN:
        return error_response(
            f"Maximum {_MAX_DELEGATIONS_PER_TURN} delegations/consultations "
            f"per turn reached. Work with what you have so far."
        )

    # -- Can't consult yourself --
    if role_id == caller_role_id:
        return error_response("You cannot consult yourself.")

    # -- Validate target is a manager (peer = has manages field) --
    peer_role = registry.get_role(role_id)
    if peer_role is None:
        return error_response(f"Role '{role_id}' not found in registry.")

    if not getattr(peer_role, "manages", ()):
        # List available peers for helpful error
        all_roles = registry.list_roles()
        peer_ids = [r.id for r in all_roles if getattr(r, "manages", ()) and r.id != caller_role_id]
        if peer_ids:
            peer_list = ", ".join(peer_ids)
            return error_response(
                f"'{role_id}' is not a department head. Available peers: {peer_list}"
            )
        return error_response(
            f"'{role_id}' is not a department head. No other department heads found."
        )

    logger.info(
        "peer_consultation.start",
        caller=caller_role_id,
        peer=role_id,
        question_preview=question[:100],
    )

    # -- Build peer context and run inner agent --
    try:
        from src.agent.api_client import run_agent_loop
        from src.agent.prompts import (
            PEER_CONSULTATION_SUPPLEMENT,
            get_base_system_prompt,
        )
        from src.agent.tool_registry import get_global_registry
        from src.config import settings
        from src.llm.provider import TaskType
        from src.skills.executor import compose_role_context

        dept = registry.get_department(peer_role.department_id)

        # Load peer memory (best-effort)
        memory_ctx = ""
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session
            from src.skills.memory import (
                compose_memory_context,
                filter_superseded_memories,
            )

            async with get_db_session() as session:
                memories = await db_service.get_role_memories(
                    session,
                    "system",
                    role_id,
                    limit=10,
                )
                superseded = await db_service.get_superseded_memory_ids(
                    session,
                    "system",
                    role_id,
                )
                memories = filter_superseded_memories(memories, superseded)
                if memories:
                    memory_ctx = compose_memory_context(memories)
        except Exception:
            pass  # Memory is best-effort

        peer_context = compose_role_context(
            department=dept,
            role=peer_role,
            memory_context=memory_ctx,
            registry=registry,
        )

        # Build system prompt
        system_prompt = get_base_system_prompt()
        if peer_context:
            system_prompt += "\n\n" + peer_context
        system_prompt += "\n\n" + PEER_CONSULTATION_SUPPLEMENT

        # Get tools (exclude delegation + orchestration tools — no recursion)
        tool_registry = get_global_registry()
        all_tools = tool_registry.get_tool_definitions()
        _no_recurse = {"delegate_to_role", "consult_peer", "orchestrate_task"}
        peer_tools = [t for t in all_tools if t["name"] not in _no_recurse]

        # Get caller name for context
        caller_role = registry.get_role(caller_role_id)
        caller_name = caller_role.name if caller_role else caller_role_id

        # Run the peer agent
        _budget = (
            settings.extended_thinking_budget_tokens if settings.extended_thinking_enabled else None
        )
        result = await run_agent_loop(
            system_prompt=system_prompt,
            user_prompt=(f"[Consultation from {caller_name}]\n\n{question}"),
            model=settings.model_standard,
            tools=peer_tools,
            max_turns=settings.conversation_tool_calls_per_turn,
            task_type=TaskType.CONVERSATION,
            thinking_budget=_budget,
        )

        # Track cost (shared counter with delegate_to_role)
        _delegation_count_var.set(count + 1)
        results_list = _delegation_results_var.get()
        results_list.append(
            {
                "role_id": role_id,
                "type": "consultation",
                "cost": result.cost,
                "success": not result.is_error,
                "turn_count": result.turn_count,
            }
        )
        _delegation_results_var.set(results_list)

        logger.info(
            "peer_consultation.complete",
            caller=caller_role_id,
            peer=role_id,
            cost=result.cost.get("total_cost_usd", 0),
            turns=result.turn_count,
            is_error=result.is_error,
        )

        if result.is_error:
            return text_response(f"**{peer_role.name}** had trouble answering:\n\n{result.text}")

        return text_response(f"**{peer_role.name}** responds:\n\n{result.text}")

    except Exception as exc:
        logger.exception(
            "peer_consultation.error",
            caller=caller_role_id,
            peer=role_id,
            error=str(exc),
        )
        # Track failed consultation
        _delegation_count_var.set(count + 1)
        results_list = _delegation_results_var.get()
        results_list.append(
            {
                "role_id": role_id,
                "type": "consultation",
                "cost": {},
                "success": False,
            }
        )
        _delegation_results_var.set(results_list)

        return error_response(f"Failed to consult {peer_role.name}: {exc}")
