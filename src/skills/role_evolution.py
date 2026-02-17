"""Role Evolution — department heads propose new roles or modify existing ones.

Provides validation, diff generation, and execution for role proposals.
Proposals flow through the standard approval queue (same as skill proposals
and budget changes) and, on human approval, write to the ``org_roles`` table
via Dynamic Org Chart CRUD methods.

Safety invariants:
    - Only manager roles (department heads) can propose role changes.
    - Proposals are department-scoped — a head can only create/modify roles
      in their own department.
    - Agents can NEVER set ``manages`` (can't self-promote or grant
      management authority).
    - Role proposals NEVER auto-execute — always require human approval.

Usage::

    from src.skills.role_evolution import (
        validate_role_proposal,
        generate_role_diff,
        execute_role_proposal,
        format_role_proposal_as_recommendation,
    )

    # Agent calls propose_role_change MCP tool → tool calls:
    ok, err = validate_role_proposal(changes, is_new=True, ...)
    diff = generate_role_diff(None, changes)
    rec = format_role_proposal_as_recommendation(...)

    # After human approval in Slack → workflow calls:
    result = await execute_role_proposal(action_params)
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

logger = structlog.get_logger() if hasattr(structlog, "get_logger") else logging.getLogger(__name__)

# =====================================================================
# Safety constants
# =====================================================================

ROLE_FORBIDDEN_FIELDS = frozenset(
    {
        "is_active",  # Agent can't deactivate roles
        "created_by",  # Metadata, not agent-modifiable
        "created_at",  # Metadata
        "updated_at",  # Metadata
        "id",  # Identity, not modifiable
        "manages",  # Agent can't self-promote or grant management
        "steward_user_id",  # Agent can't assign stewards
        "steward",  # Dataclass field name — same protection
        "document_sync",  # Steward/admin controls where output goes
        "learning_channels",  # Admin controls who can push learnings
    }
)

ROLE_ALLOWED_FIELDS = frozenset(
    {
        "name",
        "description",
        "persona",
        "connectors",
        "briefing_skills",
        "schedule",
        "context_text",
        "principles",
        "delegation_model",
        "synthesis_prompt",
        "clearance_level",
        "routing_keywords",
        "heartbeat_schedule",
        "heartbeat_model",
    }
)

REQUIRED_NEW_ROLE_FIELDS = frozenset(
    {
        "name",
        "description",
        "persona",
    }
)

VALID_DELEGATION_MODELS = frozenset({"standard", "fast"})

VALID_CLEARANCE_LEVELS = frozenset({"public", "internal", "confidential", "restricted"})


# =====================================================================
# Validation
# =====================================================================


def validate_role_proposal(
    proposed_changes: dict[str, Any],
    *,
    is_new: bool = False,
    proposer_role_id: str = "",
    proposer_department_id: str = "",
    target_role_id: str | None = None,
    registry: Any | None = None,
) -> tuple[bool, str]:
    """Validate a role proposal before entering the approval queue.

    Args:
        proposed_changes: Dict of field names → new values.
        is_new: Whether this is a new role (requires mandatory fields).
        proposer_role_id: The role ID of the agent making the proposal.
        proposer_department_id: The department of the proposing agent.
        target_role_id: For modifications, the role being modified.
        registry: The SkillRegistry, used to verify proposer is a manager
            and target role exists.

    Returns:
        Tuple of ``(is_valid, error_message)``. Empty error when valid.
    """
    if not proposed_changes:
        return False, "proposed_changes cannot be empty."

    # Check for forbidden fields
    forbidden_present = set(proposed_changes.keys()) & ROLE_FORBIDDEN_FIELDS
    if forbidden_present:
        return False, (f"Cannot modify restricted fields: {', '.join(sorted(forbidden_present))}")

    # Check all fields are known
    unknown = set(proposed_changes.keys()) - ROLE_ALLOWED_FIELDS
    if unknown:
        return False, (
            f"Unknown fields: {', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(sorted(ROLE_ALLOWED_FIELDS))}"
        )

    # For new roles, require mandatory fields
    if is_new:
        missing = REQUIRED_NEW_ROLE_FIELDS - set(proposed_changes.keys())
        if missing:
            return False, f"New role requires: {', '.join(sorted(missing))}"

    # Verify proposer is a manager role (has non-empty manages)
    if registry and proposer_role_id:
        proposer_role = registry.get_role(proposer_role_id)
        if proposer_role is None:
            return False, f"Proposer role '{proposer_role_id}' not found."
        if not getattr(proposer_role, "manages", ()):
            return False, (
                f"Role '{proposer_role_id}' is not a manager. "
                "Only department heads can propose role changes."
            )

    # For modifications, verify target role exists and is in the same department
    if not is_new and target_role_id and registry:
        target_role = registry.get_role(target_role_id)
        if target_role is None:
            return False, f"Target role '{target_role_id}' not found."
        if proposer_department_id and target_role.department_id != proposer_department_id:
            return False, (
                f"Cannot modify role '{target_role_id}' — it belongs to "
                f"department '{target_role.department_id}', not "
                f"'{proposer_department_id}'."
            )

    # Validate specific field values
    if "delegation_model" in proposed_changes:
        if proposed_changes["delegation_model"] not in VALID_DELEGATION_MODELS:
            return False, (
                f"Invalid delegation_model '{proposed_changes['delegation_model']}'. "
                f"Must be one of: {', '.join(sorted(VALID_DELEGATION_MODELS))}"
            )

    if "clearance_level" in proposed_changes:
        if proposed_changes["clearance_level"] not in VALID_CLEARANCE_LEVELS:
            return False, (
                f"Invalid clearance_level '{proposed_changes['clearance_level']}'. "
                f"Must be one of: {', '.join(sorted(VALID_CLEARANCE_LEVELS))}"
            )

    return True, ""


# =====================================================================
# Diff generation
# =====================================================================


def _truncate(text: str, max_len: int = 200) -> str:
    """Truncate text for diff display."""
    if len(str(text)) <= max_len:
        return str(text)
    return str(text)[:max_len] + "..."


def generate_role_diff(
    existing_role: dict[str, Any] | None,
    proposed_changes: dict[str, Any],
) -> str:
    """Generate a human-readable diff for a role proposal.

    Args:
        existing_role: Current role definition as dict, or ``None``
            for new roles.
        proposed_changes: The proposed field changes.

    Returns:
        Formatted string suitable for Slack Block Kit ``mrkdwn``.
    """
    lines: list[str] = []

    if existing_role is None:
        # New role
        role_name = proposed_changes.get("name", "unnamed")
        lines.append(f"New role: {role_name}")
        lines.append("")
        for field in sorted(proposed_changes.keys()):
            value = _truncate(proposed_changes[field])
            lines.append(f"  {field}: {value}")
    else:
        # Modification
        role_id = existing_role.get("role_id", existing_role.get("id", "unknown"))
        lines.append(f"Modify role: {role_id}")
        lines.append("")
        for field in sorted(proposed_changes.keys()):
            old_value = existing_role.get(field, "<not set>")
            new_value = proposed_changes[field]
            lines.append(f"  {field}:")
            lines.append(f"    before: {_truncate(old_value)}")
            lines.append(f"    after:  {_truncate(new_value)}")

    return "\n".join(lines)


# =====================================================================
# Format as recommendation
# =====================================================================


def format_role_proposal_as_recommendation(
    proposal: dict[str, Any],
    rationale: str,
    evidence_memory_ids: list[int] | None = None,
    diff: str = "",
    *,
    proposer_role_id: str = "",
    department_id: str = "",
) -> dict[str, Any]:
    """Convert a role proposal into the recommendation dict format.

    The returned dict is compatible with ``process_recommendations()``
    in ``src/workflows/approval_flow.py``.

    Args:
        proposal: Must contain ``proposed_changes`` and optionally
            ``role_id`` (for modifications).
        rationale: Why the change is needed.
        evidence_memory_ids: Optional memory IDs supporting the proposal.
        diff: Pre-generated diff text from ``generate_role_diff()``.
        proposer_role_id: The role that made this proposal.
        department_id: The department for the new/modified role.

    Returns:
        Recommendation dict ready for ``process_recommendations()``.
    """
    role_id = proposal.get("role_id")
    is_new = role_id is None
    proposed_changes = proposal.get("proposed_changes", {})

    if is_new:
        action_label = f"Create role: {proposed_changes.get('name', 'unnamed')}"
        proposal_type = "create"
    else:
        action_label = f"Modify role: {role_id}"
        proposal_type = "modify"

    action_params: dict[str, Any] = {
        "proposal_type": proposal_type,
        "diff": diff,
        "evidence_memory_ids": evidence_memory_ids or [],
        "proposer_role_id": proposer_role_id,
        "department_id": department_id,
    }

    if is_new:
        action_params["role_fields"] = proposed_changes
    else:
        action_params["role_id"] = role_id
        action_params["changes"] = proposed_changes

    return {
        "action_type": "role_proposal",
        "action": action_label,
        "description": rationale,
        "reasoning": rationale,
        "action_params": action_params,
        "projected_impact": "Role will be available in the next registry load.",
        "risk_level": "low",
        "skill_id": "",  # Role proposals aren't tied to a specific skill
    }


# =====================================================================
# Execution (after human approval)
# =====================================================================


async def execute_role_proposal(action_params: dict[str, Any]) -> dict[str, Any]:
    """Execute an approved role proposal by writing to the org_roles DB.

    Called by ``_execute_action()`` in the workflow after human approval.

    For "create" proposals, also auto-updates the proposing manager's
    ``manages`` list to include the new role.

    Args:
        action_params: The ``action_params`` dict from the approval queue
            item. Contains ``proposal_type`` (``"create"`` or ``"modify"``),
            ``department_id``, ``proposer_role_id``, plus either
            ``role_fields`` (for create) or ``role_id`` + ``changes``
            (for modify).

    Returns:
        Result dict with ``ok``, ``proposal_type``, and details.

    Raises:
        ValueError: If ``proposal_type`` is not recognized.
    """
    from src.db import service as db_service
    from src.db.session import get_db_session

    proposal_type = action_params.get("proposal_type")
    department_id = action_params.get("department_id", "")
    proposer_role_id = action_params.get("proposer_role_id", "")

    if proposal_type == "create":
        role_fields = dict(action_params.get("role_fields", {}))

        # Re-validate before writing (defense-in-depth)
        ok, err = validate_role_proposal(role_fields, is_new=True)
        if not ok:
            return {"ok": False, "error": f"Validation failed: {err}"}

        # Generate role_id from name if not provided
        role_id = role_fields.pop("role_id", None) or role_fields.get("name", "").lower().replace(
            " ", "_"
        ).replace("/", "_").replace("-", "_")

        # Extract fields that need special handling
        connectors = role_fields.pop("connectors", [])
        if isinstance(connectors, str):
            connectors = [connectors]
        briefing_skills = role_fields.pop("briefing_skills", [])
        if isinstance(briefing_skills, str):
            briefing_skills = [briefing_skills]
        principles = role_fields.pop("principles", [])
        if isinstance(principles, str):
            principles = [principles]

        # Pop fields that go into named params
        name = role_fields.pop("name", role_id)
        description = role_fields.pop("description", "")
        persona = role_fields.pop("persona", "")
        schedule = role_fields.pop("schedule", None)
        context_text = role_fields.pop("context_text", "")
        delegation_model = role_fields.pop("delegation_model", "standard")
        synthesis_prompt = role_fields.pop("synthesis_prompt", "")

        async with get_db_session() as session:
            await db_service.create_org_role(
                session,
                role_id=role_id,
                name=name,
                department_id=department_id,
                description=description,
                persona=persona,
                connectors=list(connectors),
                briefing_skills=list(briefing_skills),
                schedule=schedule,
                context_text=context_text,
                delegation_model=delegation_model,
                synthesis_prompt=synthesis_prompt,
                created_by="role_evolution",
            )
            await session.commit()

        logger.info(
            "role_evolution.created",
            role_id=role_id,
            department_id=department_id,
        )

        # Auto-update the proposing manager's manages list
        manages_updated = False
        if proposer_role_id:
            manages_updated = await _update_manager_manages(proposer_role_id, role_id)

        return {
            "ok": True,
            "proposal_type": "create",
            "role_id": role_id,
            "department_id": department_id,
            "manages_updated": manages_updated,
        }

    elif proposal_type == "modify":
        role_id = action_params.get("role_id")
        changes = action_params.get("changes", {})

        if not role_id:
            return {"ok": False, "error": "role_id required for modify"}

        # Re-validate before writing (defense-in-depth)
        ok, err = validate_role_proposal(changes, is_new=False)
        if not ok:
            return {"ok": False, "error": f"Validation failed: {err}"}

        async with get_db_session() as session:
            result = await db_service.update_org_role(
                session,
                role_id,
                **changes,
            )

        if result is None:
            # Role doesn't exist in DB yet (disk-only) — create as override
            logger.info(
                "role_evolution.creating_override",
                role_id=role_id,
            )
            async with get_db_session() as session:
                await db_service.create_org_role(
                    session,
                    role_id=role_id,
                    department_id=department_id,
                    created_by="role_evolution",
                    name=changes.get("name", role_id),
                    description=changes.get("description", ""),
                    persona=changes.get("persona", ""),
                )
                await session.commit()

        logger.info(
            "role_evolution.modified",
            role_id=role_id,
            changed_fields=list(changes.keys()),
        )
        return {
            "ok": True,
            "proposal_type": "modify",
            "role_id": role_id,
            "changed_fields": list(changes.keys()),
        }

    else:
        raise ValueError(f"Unknown proposal_type: {proposal_type}")


async def _update_manager_manages(
    manager_role_id: str,
    new_role_id: str,
) -> bool:
    """Add a new role to the manager's ``manages`` list.

    Handles both DB-defined and disk-only managers. For disk-only managers,
    creates a DB override entry with the updated ``manages`` list.

    Returns:
        True if the update was successful, False otherwise.
    """
    from src.db import service as db_service
    from src.db.session import get_db_session

    try:
        async with get_db_session() as session:
            # Try to load manager from DB first
            manager = await db_service.get_org_role(session, manager_role_id)

            if manager:
                # Manager exists in DB — update manages list
                current_manages = list(manager.manages or [])
                if new_role_id not in current_manages:
                    current_manages.append(new_role_id)
                    await db_service.update_org_role(
                        session,
                        manager_role_id,
                        manages=current_manages,
                    )
                    await session.commit()
                    logger.info(
                        "role_evolution.manages_updated",
                        manager_role_id=manager_role_id,
                        new_role_id=new_role_id,
                        manages=current_manages,
                    )
                return True
            else:
                # Manager is disk-only — load from registry and create DB override
                try:
                    from src.skills.db_loader import load_registry_with_db

                    registry = await load_registry_with_db()
                    disk_role = registry.get_role(manager_role_id)
                    if disk_role:
                        current_manages = list(disk_role.manages or ())
                        if new_role_id not in current_manages:
                            current_manages.append(new_role_id)
                        await db_service.create_org_role(
                            session,
                            role_id=manager_role_id,
                            name=disk_role.name,
                            department_id=disk_role.department_id,
                            description=disk_role.description,
                            persona=disk_role.persona or "",
                            manages=current_manages,
                            delegation_model=disk_role.delegation_model or "standard",
                            created_by="role_evolution",
                        )
                        await session.commit()
                        logger.info(
                            "role_evolution.manager_override_created",
                            manager_role_id=manager_role_id,
                            new_role_id=new_role_id,
                            manages=current_manages,
                        )
                        return True
                except Exception as exc:
                    logger.warning(
                        "role_evolution.disk_manager_fallback_failed",
                        manager_role_id=manager_role_id,
                        error=str(exc),
                    )

        return False

    except Exception as exc:
        logger.warning(
            "role_evolution.manages_update_failed",
            manager_role_id=manager_role_id,
            new_role_id=new_role_id,
            error=str(exc),
        )
        return False
