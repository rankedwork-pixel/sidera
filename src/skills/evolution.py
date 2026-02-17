"""Skill Evolution — agents propose changes to their own skill definitions.

Provides validation, diff generation, and execution for skill proposals.
Proposals flow through the standard approval queue (same as budget changes)
and, on human approval, write to the ``org_skills`` table via Dynamic Org
Chart CRUD methods.

Safety invariant: agents can NEVER modify safety-critical fields
(``requires_approval``, ``manages``, ``is_active``).

Usage::

    from src.skills.evolution import (
        validate_skill_proposal,
        generate_skill_diff,
        execute_skill_proposal,
        format_proposal_as_recommendation,
    )

    # Agent calls propose_skill_change MCP tool → tool calls:
    ok, err = validate_skill_proposal(proposal, is_new=False)
    diff = generate_skill_diff(existing_skill_dict, proposal["proposed_changes"])
    rec = format_proposal_as_recommendation(proposal, rationale, evidence, diff)

    # After human approval in Slack → workflow calls:
    result = await execute_skill_proposal(action_params)
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

logger = structlog.get_logger() if hasattr(structlog, "get_logger") else logging.getLogger(__name__)

# =====================================================================
# Safety constants
# =====================================================================

FORBIDDEN_FIELDS = frozenset(
    {
        "requires_approval",  # Agent can't bypass its own approval gate
        "manages",  # Agent can't grant itself management authority
        "is_active",  # Agent can't deactivate skills
        "created_by",  # Metadata, not agent-modifiable
        "id",  # Identity, not modifiable
        "skill_id",  # Identity, not modifiable on update
        "created_at",  # Metadata
        "updated_at",  # Metadata
        "steward_user_id",  # Agent can't change its own steward
        "skill_type",  # Agent can't change execution type
        "code_entrypoint",  # Agent can't modify code execution path
        "code_timeout_seconds",  # Agent can't change timeout
        "code_output_patterns",  # Agent can't change output patterns
    }
)

ALLOWED_FIELDS = frozenset(
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
        "department_id",
        "role_id",
        "author",
    }
)

REQUIRED_NEW_SKILL_FIELDS = frozenset(
    {
        "name",
        "description",
        "category",
        "system_supplement",
        "prompt_template",
        "output_format",
        "business_guidance",
    }
)

VALID_MODELS = frozenset({"haiku", "sonnet", "opus"})

# Import categories from the schema module to stay in sync
try:
    from src.skills.schema import VALID_CATEGORIES
except ImportError:  # pragma: no cover
    VALID_CATEGORIES = frozenset(
        {
            "analysis",
            "optimization",
            "reporting",
            "monitoring",
            "creative",
            "audience",
            "bidding",
            "budget",
            "forecasting",
            "attribution",
        }
    )


# =====================================================================
# Validation
# =====================================================================


def validate_skill_proposal(
    proposed_changes: dict[str, Any],
    *,
    is_new: bool = False,
) -> tuple[bool, str]:
    """Validate a skill proposal before entering the approval queue.

    Args:
        proposed_changes: Dict of field names → new values.
        is_new: Whether this is a new skill (requires all mandatory fields).

    Returns:
        Tuple of ``(is_valid, error_message)``. ``error_message`` is empty
        when valid.
    """
    if not proposed_changes:
        return False, "proposed_changes cannot be empty."

    # Check for forbidden fields
    forbidden_present = set(proposed_changes.keys()) & FORBIDDEN_FIELDS
    if forbidden_present:
        return False, (
            f"Cannot modify safety-critical fields: {', '.join(sorted(forbidden_present))}"
        )

    # Check all fields are known
    unknown = set(proposed_changes.keys()) - ALLOWED_FIELDS
    if unknown:
        return False, (
            f"Unknown fields: {', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(sorted(ALLOWED_FIELDS))}"
        )

    # For new skills, require mandatory fields
    if is_new:
        missing = REQUIRED_NEW_SKILL_FIELDS - set(proposed_changes.keys())
        if missing:
            return False, (f"New skill requires: {', '.join(sorted(missing))}")

    # Validate specific field values
    if "model" in proposed_changes:
        if proposed_changes["model"] not in VALID_MODELS:
            return False, (
                f"Invalid model '{proposed_changes['model']}'. "
                f"Must be one of: {', '.join(sorted(VALID_MODELS))}"
            )

    if "max_turns" in proposed_changes:
        mt = proposed_changes["max_turns"]
        if not isinstance(mt, int) or mt < 1 or mt > 50:
            return False, "max_turns must be an integer between 1 and 50."

    if "category" in proposed_changes:
        if proposed_changes["category"] not in VALID_CATEGORIES:
            return False, (
                f"Invalid category '{proposed_changes['category']}'. "
                f"Must be one of: {', '.join(sorted(VALID_CATEGORIES))}"
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


def generate_skill_diff(
    existing_skill: dict[str, Any] | None,
    proposed_changes: dict[str, Any],
) -> str:
    """Generate a human-readable diff for a skill proposal.

    Args:
        existing_skill: Current skill definition as dict, or ``None``
            for new skills.
        proposed_changes: The proposed field changes.

    Returns:
        Formatted string suitable for Slack Block Kit ``mrkdwn``.
    """
    lines: list[str] = []

    if existing_skill is None:
        # New skill
        skill_name = proposed_changes.get("name", "unnamed")
        lines.append(f"New skill: {skill_name}")
        lines.append("")
        for field in sorted(proposed_changes.keys()):
            value = _truncate(proposed_changes[field])
            lines.append(f"  {field}: {value}")
    else:
        # Modification
        skill_id = existing_skill.get("skill_id", existing_skill.get("id", "unknown"))
        lines.append(f"Modify skill: {skill_id}")
        lines.append("")
        for field in sorted(proposed_changes.keys()):
            old_value = existing_skill.get(field, "<not set>")
            new_value = proposed_changes[field]
            lines.append(f"  {field}:")
            lines.append(f"    before: {_truncate(old_value)}")
            lines.append(f"    after:  {_truncate(new_value)}")

    return "\n".join(lines)


# =====================================================================
# Format as recommendation
# =====================================================================


def format_proposal_as_recommendation(
    proposal: dict[str, Any],
    rationale: str,
    evidence_memory_ids: list[int] | None = None,
    diff: str = "",
    *,
    source_skill_id: str = "",
) -> dict[str, Any]:
    """Convert a proposal into the recommendation dict format.

    The returned dict is compatible with ``process_recommendations()``
    in ``src/workflows/approval_flow.py``.

    Args:
        proposal: Must contain ``proposed_changes`` and optionally
            ``skill_id`` (for modifications).
        rationale: Why the change is needed.
        evidence_memory_ids: Optional memory IDs supporting the proposal.
        diff: Pre-generated diff text from ``generate_skill_diff()``.
        source_skill_id: The skill that was running when the agent
            made this proposal.

    Returns:
        Recommendation dict ready for ``process_recommendations()``.
    """
    skill_id = proposal.get("skill_id")
    is_new = skill_id is None
    proposed_changes = proposal.get("proposed_changes", {})

    if is_new:
        action_label = f"Create skill: {proposed_changes.get('name', 'unnamed')}"
        proposal_type = "create"
    else:
        action_label = f"Modify skill: {skill_id}"
        proposal_type = "modify"

    action_params: dict[str, Any] = {
        "proposal_type": proposal_type,
        "diff": diff,
        "evidence_memory_ids": evidence_memory_ids or [],
    }

    if is_new:
        action_params["skill_fields"] = proposed_changes
    else:
        action_params["skill_id"] = skill_id
        action_params["changes"] = proposed_changes

    return {
        "action_type": "skill_proposal",
        "action": action_label,
        "description": rationale,
        "reasoning": rationale,
        "action_params": action_params,
        "projected_impact": "Skill will be updated in the next registry load.",
        "risk_level": "low",
        "skill_id": source_skill_id,
    }


# =====================================================================
# Execution (after human approval)
# =====================================================================


async def execute_skill_proposal(action_params: dict[str, Any]) -> dict[str, Any]:
    """Execute an approved skill proposal by writing to the org_skills DB.

    Called by ``_execute_action()`` in the workflow after human approval.

    Args:
        action_params: The ``action_params`` dict from the approval queue
            item. Contains ``proposal_type`` (``"create"`` or ``"modify"``),
            plus either ``skill_fields`` (for create) or ``skill_id`` +
            ``changes`` (for modify).

    Returns:
        Result dict with ``ok``, ``proposal_type``, and details.

    Raises:
        ValueError: If ``proposal_type`` is not recognized.
    """
    from src.db import service as db_service
    from src.db.session import get_db_session

    proposal_type = action_params.get("proposal_type")

    if proposal_type == "create":
        skill_fields = action_params.get("skill_fields", {})

        # Re-validate before writing (defense-in-depth)
        ok, err = validate_skill_proposal(skill_fields, is_new=True)
        if not ok:
            return {"ok": False, "error": f"Validation failed: {err}"}

        async with get_db_session() as session:
            skill_id = skill_fields.pop("skill_id", None) or skill_fields.get(
                "name", ""
            ).lower().replace(" ", "_")
            result = await db_service.create_org_skill(
                session,
                skill_id=skill_id,
                created_by="skill_evolution",
                **skill_fields,
            )

        logger.info(
            "skill_evolution.created",
            skill_id=skill_id,
            proposal_type=proposal_type,
        )
        return {
            "ok": True,
            "proposal_type": "create",
            "skill_id": skill_id,
        }

    elif proposal_type == "modify":
        skill_id = action_params.get("skill_id")
        changes = action_params.get("changes", {})

        if not skill_id:
            return {"ok": False, "error": "skill_id required for modify"}

        # Re-validate before writing (defense-in-depth)
        ok, err = validate_skill_proposal(changes, is_new=False)
        if not ok:
            return {"ok": False, "error": f"Validation failed: {err}"}

        async with get_db_session() as session:
            result = await db_service.update_org_skill(
                session,
                skill_id,
                **changes,
            )

        if result is None:
            # Skill doesn't exist in DB yet — create as override
            logger.info(
                "skill_evolution.creating_override",
                skill_id=skill_id,
            )
            async with get_db_session() as session:
                result = await db_service.create_org_skill(
                    session,
                    skill_id=skill_id,
                    created_by="skill_evolution",
                    # Provide defaults for required fields when creating
                    # an override from a modification
                    name=changes.get("name", skill_id),
                    description=changes.get("description", ""),
                    category=changes.get("category", "analysis"),
                    system_supplement=changes.get("system_supplement", ""),
                    prompt_template=changes.get("prompt_template", ""),
                    output_format=changes.get("output_format", ""),
                    business_guidance=changes.get("business_guidance", ""),
                    **{
                        k: v
                        for k, v in changes.items()
                        if k
                        not in {
                            "name",
                            "description",
                            "category",
                            "system_supplement",
                            "prompt_template",
                            "output_format",
                            "business_guidance",
                        }
                    },
                )

        logger.info(
            "skill_evolution.modified",
            skill_id=skill_id,
            proposal_type=proposal_type,
            changed_fields=list(changes.keys()),
        )
        return {
            "ok": True,
            "proposal_type": "modify",
            "skill_id": skill_id,
            "changed_fields": list(changes.keys()),
        }

    else:
        raise ValueError(f"Unknown proposal_type: {proposal_type}")
