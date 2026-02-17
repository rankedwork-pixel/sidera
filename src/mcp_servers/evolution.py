"""Skill & Role Evolution MCP tools for Sidera.

Provides 2 tools that agents call to propose changes to definitions.
Proposals go through the standard approval queue — agents propose, humans
decide.

Tools:
    1. propose_skill_change — Propose a new skill or modify an existing one
    2. propose_role_change  — Propose a new role or modify an existing one
                              (manager roles only, department-scoped)

Usage:
    from src.mcp_servers.evolution import create_evolution_tools

    tools = create_evolution_tools()
"""

from __future__ import annotations

import contextvars
from typing import Any

import structlog

from src.agent.tool_registry import tool
from src.mcp_servers.helpers import error_response, text_response
from src.skills.evolution import (
    ALLOWED_FIELDS,
    format_proposal_as_recommendation,
    generate_skill_diff,
    validate_skill_proposal,
)
from src.skills.role_evolution import (
    ROLE_ALLOWED_FIELDS,
    format_role_proposal_as_recommendation,
    generate_role_diff,
    validate_role_proposal,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Pending proposals — per-async-task via contextvars (concurrency-safe)
# ---------------------------------------------------------------------------

_pending_proposals_var: contextvars.ContextVar[list[dict[str, Any]]] = contextvars.ContextVar(
    "pending_proposals", default=[]
)


def _get_proposals_list() -> list[dict[str, Any]]:
    """Get the current task's pending proposals list."""
    try:
        return _pending_proposals_var.get()
    except LookupError:
        proposals: list[dict[str, Any]] = []
        _pending_proposals_var.set(proposals)
        return proposals


def get_pending_proposals() -> list[dict[str, Any]]:
    """Return and clear all pending skill proposals for this turn.

    Called by the workflow engine after an agent turn completes to
    collect any proposals the agent made via the MCP tool.
    Scoped to the current async task — safe for concurrent use.

    Returns:
        List of recommendation dicts (same format as agent recommendations).
    """
    proposals = list(_get_proposals_list())
    _pending_proposals_var.set([])
    return proposals


def clear_pending_proposals() -> None:
    """Clear pending proposals without returning them (for testing)."""
    _pending_proposals_var.set([])


# ---------------------------------------------------------------------------
# Proposer context — identifies which role/dept is calling (for role proposals)
# ---------------------------------------------------------------------------

_proposer_context_var: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "proposer_context", default=None
)


def set_proposer_context(role_id: str, department_id: str) -> None:
    """Set the proposer context for the current turn.

    Called before running an agent turn so ``propose_role_change`` can
    verify the caller is a manager in the correct department.

    Args:
        role_id: The role ID executing this turn.
        department_id: The department the role belongs to.
    """
    _proposer_context_var.set({"role_id": role_id, "department_id": department_id})


def clear_proposer_context() -> None:
    """Clear proposer context after a turn completes."""
    _proposer_context_var.set(None)


def _get_proposer_context() -> dict[str, str] | None:
    """Get the current task's proposer context."""
    try:
        return _proposer_context_var.get()
    except LookupError:
        return None


# ---------------------------------------------------------------------------
# Tool: propose_skill_change
# ---------------------------------------------------------------------------

PROPOSE_SKILL_CHANGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "skill_id": {
            "type": "string",
            "description": (
                "ID of an existing skill to modify. Omit this field "
                "to propose an entirely new skill."
            ),
        },
        "proposed_changes": {
            "type": "object",
            "description": (
                "Skill fields to set or change. Allowed fields: "
                + ", ".join(sorted(ALLOWED_FIELDS))
                + ". For new skills, you must include at minimum: "
                "name, description, category, system_supplement, "
                "prompt_template, output_format, business_guidance."
            ),
        },
        "rationale": {
            "type": "string",
            "description": (
                "Why this change would improve the skill. Be specific — "
                "reference data patterns, missing metrics, or gaps you "
                "observed during analysis."
            ),
        },
        "evidence_memory_ids": {
            "type": "array",
            "items": {"type": "integer"},
            "description": (
                "Optional. Memory entry IDs that support this proposal. "
                "Reference memories from previous runs that demonstrate "
                "why this change is needed."
            ),
        },
    },
    "required": ["proposed_changes", "rationale"],
}


@tool(
    name="propose_skill_change",
    description=(
        "Propose a change to an existing skill definition, or propose "
        "a brand-new skill. Your proposal will be reviewed by a human "
        "before taking effect — you cannot modify skills directly. "
        "Use this when you notice a skill's prompts, output format, "
        "guidance, or context could be improved based on patterns in "
        "the data you've analyzed. You CANNOT change safety fields "
        "like requires_approval, manages, or is_active."
    ),
    input_schema=PROPOSE_SKILL_CHANGE_SCHEMA,
)
async def propose_skill_change(args: dict[str, Any]) -> dict[str, Any]:
    """Propose a skill change — queued for human approval."""
    proposed_changes = args.get("proposed_changes")
    rationale = (args.get("rationale") or "").strip()
    skill_id = args.get("skill_id")
    evidence_ids = args.get("evidence_memory_ids") or []

    # --- Input validation ---
    if not proposed_changes:
        return error_response("proposed_changes is required and cannot be empty.")

    if not rationale:
        return error_response("rationale is required — explain why this change is needed.")

    is_new = skill_id is None
    ok, err = validate_skill_proposal(proposed_changes, is_new=is_new)
    if not ok:
        return error_response(f"Invalid proposal: {err}")

    logger.info(
        "tool.propose_skill_change",
        skill_id=skill_id or "<new>",
        is_new=is_new,
        changed_fields=list(proposed_changes.keys()),
    )

    # --- Build diff ---
    # For modifications, try to load existing skill for comparison
    existing_skill: dict[str, Any] | None = None
    if skill_id:
        try:
            # Use a synchronous-safe import; the registry is cached
            import asyncio

            from src.skills.db_loader import load_registry_with_db

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                # We're already in an async context — can't await here
                # so we skip loading the existing skill for diff
                existing_skill = None
            else:
                registry = asyncio.run(load_registry_with_db())
                skill_def = registry.get_skill(skill_id)
                if skill_def:
                    # Convert frozen dataclass to dict
                    existing_skill = {
                        field: getattr(skill_def, field, None)
                        for field in ALLOWED_FIELDS
                        if hasattr(skill_def, field)
                    }
        except Exception:
            # Non-fatal: we just won't have a rich diff
            existing_skill = None

    diff = generate_skill_diff(existing_skill, proposed_changes)

    # --- Format as recommendation and queue ---
    proposal = {"proposed_changes": proposed_changes}
    if skill_id:
        proposal["skill_id"] = skill_id

    recommendation = format_proposal_as_recommendation(
        proposal=proposal,
        rationale=rationale,
        evidence_memory_ids=evidence_ids,
        diff=diff,
    )

    _get_proposals_list().append(recommendation)

    # --- Respond to the agent ---
    action_label = "Create new skill" if is_new else f"Modify skill '{skill_id}'"
    return text_response(
        f"Skill proposal queued for human review.\n"
        f"Action: {action_label}\n"
        f"Changed fields: {', '.join(sorted(proposed_changes.keys()))}\n"
        f"This will appear in Slack for approval before taking effect."
    )


# ---------------------------------------------------------------------------
# Tool: propose_role_change
# ---------------------------------------------------------------------------

PROPOSE_ROLE_CHANGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "role_id": {
            "type": "string",
            "description": (
                "ID of an existing role to modify. Omit this field to propose an entirely new role."
            ),
        },
        "proposed_changes": {
            "type": "object",
            "description": (
                "Role fields to set or change. Allowed fields: "
                + ", ".join(sorted(ROLE_ALLOWED_FIELDS))
                + ". For new roles, you must include at minimum: "
                "name, description, persona."
            ),
        },
        "rationale": {
            "type": "string",
            "description": (
                "Why this role change is needed. Be specific — "
                "reference team gaps, workload patterns, or strategic "
                "needs that justify the new or modified role."
            ),
        },
        "evidence_memory_ids": {
            "type": "array",
            "items": {"type": "integer"},
            "description": ("Optional. Memory entry IDs that support this proposal."),
        },
    },
    "required": ["proposed_changes", "rationale"],
}


@tool(
    name="propose_role_change",
    description=(
        "Propose a new team member role or modify an existing role in "
        "your department. Only department heads (manager roles) can use "
        "this tool, and only for roles within their own department. "
        "Your proposal will be reviewed by a human before taking effect. "
        "On approval, the new role is created and automatically added "
        "to your managed team. You CANNOT set the 'manages' field "
        "(only humans can grant management authority)."
    ),
    input_schema=PROPOSE_ROLE_CHANGE_SCHEMA,
)
async def propose_role_change(args: dict[str, Any]) -> dict[str, Any]:
    """Propose a role change — queued for human approval."""
    proposed_changes = args.get("proposed_changes")
    rationale = (args.get("rationale") or "").strip()
    role_id = args.get("role_id")
    evidence_ids = args.get("evidence_memory_ids") or []

    # --- Input validation ---
    if not proposed_changes:
        return error_response("proposed_changes is required and cannot be empty.")

    if not rationale:
        return error_response("rationale is required — explain why this role change is needed.")

    # --- Proposer identity check ---
    proposer_ctx = _get_proposer_context()
    if not proposer_ctx:
        return error_response(
            "Cannot determine your identity. This tool can only be used "
            "during a conversation or scheduled run."
        )

    proposer_role_id = proposer_ctx["role_id"]
    proposer_dept_id = proposer_ctx["department_id"]

    # --- Load registry for validation ---
    registry = None
    try:
        from src.skills.db_loader import load_registry_with_db

        registry = await load_registry_with_db()
    except Exception:
        pass  # Non-fatal — validation will be less strict

    is_new = role_id is None
    ok, err = validate_role_proposal(
        proposed_changes,
        is_new=is_new,
        proposer_role_id=proposer_role_id,
        proposer_department_id=proposer_dept_id,
        target_role_id=role_id,
        registry=registry,
    )
    if not ok:
        return error_response(f"Invalid proposal: {err}")

    logger.info(
        "tool.propose_role_change",
        role_id=role_id or "<new>",
        is_new=is_new,
        proposer=proposer_role_id,
        department=proposer_dept_id,
        changed_fields=list(proposed_changes.keys()),
    )

    # --- Build diff ---
    existing_role: dict[str, Any] | None = None
    if role_id and registry:
        try:
            role_def = registry.get_role(role_id)
            if role_def:
                existing_role = {
                    field: getattr(role_def, field, None)
                    for field in ROLE_ALLOWED_FIELDS
                    if hasattr(role_def, field)
                }
        except Exception:
            existing_role = None

    diff = generate_role_diff(existing_role, proposed_changes)

    # --- Format as recommendation and queue ---
    proposal: dict[str, Any] = {"proposed_changes": proposed_changes}
    if role_id:
        proposal["role_id"] = role_id

    recommendation = format_role_proposal_as_recommendation(
        proposal=proposal,
        rationale=rationale,
        evidence_memory_ids=evidence_ids,
        diff=diff,
        proposer_role_id=proposer_role_id,
        department_id=proposer_dept_id,
    )

    _get_proposals_list().append(recommendation)

    # --- Respond to the agent ---
    action_label = (
        f"Create new role: {proposed_changes.get('name', 'unnamed')}"
        if is_new
        else f"Modify role '{role_id}'"
    )
    return text_response(
        f"Role proposal queued for human review.\n"
        f"Action: {action_label}\n"
        f"Department: {proposer_dept_id}\n"
        f"Fields: {', '.join(sorted(proposed_changes.keys()))}\n"
        f"This will appear in Slack for approval before taking effect."
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_evolution_tools() -> list[Any]:
    """Return the list of Skill & Role Evolution MCP tool definitions.

    Returns:
        List of 2 SdkMcpTool instances.
    """
    return [propose_skill_change, propose_role_change]
