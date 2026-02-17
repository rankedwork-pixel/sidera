"""Working group MCP tools for multi-agent planning.

Provides tools for manager roles to form and monitor ad hoc working
groups — temporary teams of roles that coordinate around a shared
objective.

Uses ``contextvars.ContextVar`` to carry working group context into
the tool handlers (same pattern as ``delegation.py``).

Usage::

    from src.mcp_servers.working_group import (
        set_working_group_context,
        clear_working_group_context,
    )

    # Before a manager's agent turn:
    set_working_group_context(role_id, registry)

    # ... agent runs, may call form_working_group tool ...

    # After the turn:
    proposals = get_pending_working_groups()
    clear_working_group_context()
"""

from __future__ import annotations

import contextvars
from typing import Any

import structlog

from src.agent.tool_registry import tool
from src.mcp_servers.helpers import error_response, text_response
from src.skills.working_group import (
    _DEFAULT_COST_CAP_USD,
    _DEFAULT_MAX_DURATION_MIN,
    generate_group_id,
    validate_working_group_request,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Context vars (same pattern as delegation.py)
# ---------------------------------------------------------------------------

_wg_context_var: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "wg_context", default=None
)

_pending_groups_var: contextvars.ContextVar[list[dict[str, Any]]] = contextvars.ContextVar(
    "pending_groups", default=[]
)


def set_working_group_context(
    role_id: str,
    registry: Any,
) -> None:
    """Set working group context for the current agent turn.

    Args:
        role_id: The manager role ID.
        registry: The loaded ``SkillRegistry`` instance.
    """
    _wg_context_var.set({"role_id": role_id, "registry": registry})
    _pending_groups_var.set([])


def clear_working_group_context() -> None:
    """Clear working group context after an agent turn."""
    _wg_context_var.set(None)


def get_pending_working_groups() -> list[dict[str, Any]]:
    """Return and clear any working group proposals from this turn."""
    groups = list(_pending_groups_var.get([]))
    _pending_groups_var.set([])
    return groups


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@tool(
    name="form_working_group",
    description=(
        "Form a multi-agent working group to tackle a complex objective "
        "that requires coordinated effort from multiple roles. The group "
        "will plan task assignments, execute in parallel, and synthesize "
        "a unified result. Only available to manager roles."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "objective": {
                "type": "string",
                "description": (
                    "Clear description of what the working group should "
                    "accomplish. Be specific about the desired outcome."
                ),
            },
            "member_role_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "IDs of roles to include in the working group. "
                    "Each role will be assigned a specific task."
                ),
            },
            "cost_cap_usd": {
                "type": "number",
                "description": (
                    "Maximum cost in USD for the entire working group "
                    f"session. Default: {_DEFAULT_COST_CAP_USD}"
                ),
            },
            "shared_context": {
                "type": "string",
                "description": (
                    "Optional shared context or data that all members "
                    "should have access to during execution."
                ),
            },
        },
        "required": ["objective", "member_role_ids"],
    },
)
async def form_working_group(
    args: dict[str, Any],
) -> dict[str, Any]:
    """Form a working group. Validated and queued for execution."""
    ctx = _wg_context_var.get()
    if ctx is None:
        return error_response(
            "Working group tools require context. "
            "This tool is only available during manager agent turns."
        )

    role_id = ctx["role_id"]
    registry = ctx["registry"]

    objective = args.get("objective", "").strip()
    member_role_ids = args.get("member_role_ids", [])
    cost_cap = float(args.get("cost_cap_usd", _DEFAULT_COST_CAP_USD))
    shared_context = args.get("shared_context", "")

    # Validate
    errors = validate_working_group_request(
        coordinator_role_id=role_id,
        member_role_ids=member_role_ids,
        objective=objective,
        registry=registry,
    )
    if errors:
        return error_response(
            "Working group validation failed:\n" + "\n".join(f"- {e}" for e in errors)
        )

    # Generate group ID and queue the proposal
    group_id = generate_group_id()

    proposal = {
        "group_id": group_id,
        "objective": objective,
        "coordinator_role_id": role_id,
        "member_role_ids": member_role_ids,
        "cost_cap_usd": cost_cap,
        "max_duration_minutes": _DEFAULT_MAX_DURATION_MIN,
        "shared_context": shared_context,
    }

    pending = _pending_groups_var.get([])
    pending.append(proposal)
    _pending_groups_var.set(pending)

    member_names = []
    for rid in member_role_ids:
        r = registry.get_role(rid)
        member_names.append(f"{rid} ({r.name})" if r else rid)

    logger.info(
        "working_group.proposed",
        group_id=group_id,
        coordinator=role_id,
        members=member_role_ids,
        objective=objective[:100],
    )

    return text_response(
        f"Working group **{group_id}** has been proposed.\n\n"
        f"**Objective:** {objective}\n"
        f"**Members:** {', '.join(member_names)}\n"
        f"**Cost cap:** ${cost_cap:.2f}\n\n"
        "The group will be formed and executed after this conversation "
        "turn completes. Each member will receive a specific task "
        "assignment based on their expertise, execute independently, "
        "and results will be synthesized into a unified output."
    )


@tool(
    name="get_working_group_status",
    description=(
        "Check the status of a working group by its group ID. "
        "Returns the current status, plan, member results, and synthesis."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "group_id": {
                "type": "string",
                "description": "The working group ID to check.",
            },
        },
        "required": ["group_id"],
    },
)
async def get_working_group_status(
    args: dict[str, Any],
) -> dict[str, Any]:
    """Check status of a working group."""
    group_id = args.get("group_id", "")
    if not group_id:
        return error_response("group_id is required")

    try:
        from src.db.session import get_session

        async with get_session() as session:
            from src.db.service import get_working_group

            wg = await get_working_group(session, group_id)
            if wg is None:
                return error_response(f"Working group '{group_id}' not found")

            plan = wg.plan_json or {}
            results = wg.member_results_json or {}
            members = wg.member_role_ids or []

            completed = [rid for rid in members if rid in results]
            pending = [rid for rid in members if rid not in results]

            lines = [
                f"**Working Group:** {group_id}",
                f"**Status:** {wg.status}",
                f"**Objective:** {wg.objective}",
                f"**Coordinator:** {wg.coordinator_role_id}",
                f"**Members:** {', '.join(members)}",
                f"**Completed:** {', '.join(completed) or 'none'}",
                f"**Pending:** {', '.join(pending) or 'none'}",
                f"**Cost:** ${float(wg.total_cost_usd or 0):.4f}"
                f" / ${float(wg.cost_cap_usd or 0):.2f}",
            ]

            if plan.get("plan_summary"):
                lines.append(f"\n**Plan:** {plan['plan_summary']}")

            if wg.synthesis:
                lines.append(f"\n**Synthesis:**\n{wg.synthesis[:500]}")

            return text_response("\n".join(lines))

    except Exception as exc:
        logger.error(
            "working_group.status_error",
            group_id=group_id,
            error=str(exc),
        )
        return error_response(f"Error checking status: {exc}")
