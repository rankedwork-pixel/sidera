"""Conversational plan refinement for the bootstrap pipeline.

Takes a draft ``BootstrapPlan`` and natural language feedback from a
human reviewer, sends both to Sonnet, and applies the structured
modifications returned by the LLM.

Example:
    plan, changes, cost = await refine_plan(plan, "Add an ops team")
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from src.agent.api_client import call_claude_api
from src.bootstrap.models import (
    BootstrapPlan,
    ExtractedDepartment,
    ExtractedRole,
    ExtractedSkill,
)
from src.bootstrap.prompts import REFINE_SYSTEM_PROMPT, REFINE_USER_TEMPLATE
from src.config import settings
from src.llm.provider import TaskType

logger = structlog.get_logger(__name__)


async def refine_plan(
    plan: BootstrapPlan,
    feedback: str,
) -> tuple[BootstrapPlan, list[str], float]:
    """Send plan + feedback to Sonnet, get structured modifications, apply them.

    Parameters
    ----------
    plan:
        The draft plan to refine.
    feedback:
        Natural language feedback from the reviewer.

    Returns
    -------
    tuple[BootstrapPlan, list[str], float]
        The modified plan, list of human-readable change descriptions,
        and the LLM cost.
    """
    plan_summary = _format_plan_for_llm(plan)
    user_message = REFINE_USER_TEMPLATE.format(plan_summary=plan_summary, feedback=feedback)

    try:
        result = await call_claude_api(
            model=settings.model_standard,  # Sonnet
            system_prompt=REFINE_SYSTEM_PROMPT,
            user_message=user_message,
            max_tokens=4096,
            task_type=TaskType.GENERAL,
        )
    except Exception as exc:
        logger.warning("bootstrap.refine_error", error=str(exc))
        return plan, [f"Refinement failed: {exc}"], 0.0

    cost = result.get("cost", {}).get("total_cost_usd", 0.0)
    text = result.get("text", "")

    # Parse structured response
    mods = _parse_refinement_response(text)
    if not mods:
        return plan, ["Could not parse LLM response"], cost

    changes = _apply_modifications(plan, mods)

    logger.info(
        "bootstrap.refine_complete",
        plan_id=plan.id,
        changes=len(changes),
        cost=f"${cost:.4f}",
    )

    return plan, changes, cost


def _format_plan_for_llm(plan: BootstrapPlan) -> str:
    """Create a concise plan summary for the refinement prompt context."""
    parts: list[str] = []

    parts.append("Departments:")
    for d in plan.departments:
        parts.append(f"  - {d.id} ({d.name}): {d.description}")

    parts.append("\nRoles:")
    for r in plan.roles:
        manages_str = f", manages: [{', '.join(r.manages)}]" if r.manages else ""
        parts.append(
            f"  - {r.id} ({r.name}) [dept: {r.department_id}]{manages_str}: {r.description}"
        )
        if r.persona:
            parts.append(f"    Persona: {r.persona[:100]}")

    parts.append("\nSkills:")
    for s in plan.skills:
        parts.append(
            f"  - {s.id} ({s.name}) [role: {s.role_id}, dept: {s.department_id}]: {s.description}"
        )

    parts.append(f"\nMemories: {len(plan.memories)}")
    parts.append(f"Conflicts: {len(plan.conflicts)}")

    return "\n".join(parts)


def _parse_refinement_response(text: str) -> dict[str, Any]:
    """Parse the LLM's JSON response, stripping markdown fences."""
    cleaned = text.strip()

    if cleaned.startswith("```"):
        first_newline = cleaned.index("\n") if "\n" in cleaned else len(cleaned)
        cleaned = cleaned[first_newline + 1 :]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict) and "changes" in parsed:
            return parsed
        return {}
    except json.JSONDecodeError:
        logger.warning("bootstrap.refine_parse_error", raw_text=text[:200])
        return {}


def _apply_modifications(plan: BootstrapPlan, mods: dict[str, Any]) -> list[str]:
    """Apply add/remove/modify operations to the plan.

    Returns a list of human-readable change descriptions.
    """
    changes: list[str] = []

    for change in mods.get("changes", []):
        action = change.get("action", "")
        entity_type = change.get("entity_type", "")
        entity_id = change.get("entity_id", "")
        fields = change.get("fields", {})

        if not action or not entity_type or not entity_id:
            continue

        if action == "add":
            desc = _apply_add(plan, entity_type, entity_id, fields)
        elif action == "remove":
            desc = _apply_remove(plan, entity_type, entity_id)
        elif action == "modify":
            desc = _apply_modify(plan, entity_type, entity_id, fields)
        else:
            desc = f"Unknown action '{action}' for {entity_type} '{entity_id}'"

        if desc:
            changes.append(desc)

    # Include explanation from LLM if present
    explanation = mods.get("explanation", "")
    if explanation:
        changes.append(f"LLM explanation: {explanation}")

    return changes


def _apply_add(
    plan: BootstrapPlan,
    entity_type: str,
    entity_id: str,
    fields: dict[str, Any],
) -> str:
    """Add a new entity to the plan."""
    if entity_type == "department":
        if any(d.id == entity_id for d in plan.departments):
            return f"Department '{entity_id}' already exists, skipped add"
        dept = ExtractedDepartment(
            id=entity_id,
            name=fields.get("name", entity_id),
            description=fields.get("description", ""),
            context=fields.get("context", ""),
        )
        plan.departments.append(dept)
        return f"Added department '{entity_id}' ({dept.name})"

    elif entity_type == "role":
        if any(r.id == entity_id for r in plan.roles):
            return f"Role '{entity_id}' already exists, skipped add"
        role = ExtractedRole(
            id=entity_id,
            name=fields.get("name", entity_id),
            department_id=fields.get("department_id", ""),
            description=fields.get("description", ""),
            persona=fields.get("persona", ""),
        )
        plan.roles.append(role)
        return f"Added role '{entity_id}' ({role.name}) to dept '{role.department_id}'"

    elif entity_type == "skill":
        if any(s.id == entity_id for s in plan.skills):
            return f"Skill '{entity_id}' already exists, skipped add"
        skill = ExtractedSkill(
            id=entity_id,
            name=fields.get("name", entity_id),
            role_id=fields.get("role_id", ""),
            department_id=fields.get("department_id", ""),
            description=fields.get("description", ""),
            category=fields.get("category", "general"),
            model=fields.get("model", "sonnet"),
        )
        plan.skills.append(skill)
        return f"Added skill '{entity_id}' ({skill.name}) to role '{skill.role_id}'"

    return f"Unknown entity type '{entity_type}' for add"


def _apply_remove(
    plan: BootstrapPlan,
    entity_type: str,
    entity_id: str,
) -> str:
    """Remove an entity from the plan."""
    if entity_type == "department":
        if not any(d.id == entity_id for d in plan.departments):
            return f"Department '{entity_id}' not found, skipped remove"
        cascade_role_ids = {r.id for r in plan.roles if r.department_id == entity_id}
        plan.departments = [d for d in plan.departments if d.id != entity_id]
        plan.roles = [r for r in plan.roles if r.department_id != entity_id]
        plan.skills = [
            s
            for s in plan.skills
            if s.department_id != entity_id and s.role_id not in cascade_role_ids
        ]
        plan.memories = [
            m
            for m in plan.memories
            if m.department_id != entity_id and m.role_id not in cascade_role_ids
        ]
        return f"Removed department '{entity_id}' (cascaded {len(cascade_role_ids)} roles)"

    elif entity_type == "role":
        if not any(r.id == entity_id for r in plan.roles):
            return f"Role '{entity_id}' not found, skipped remove"
        plan.roles = [r for r in plan.roles if r.id != entity_id]
        plan.skills = [s for s in plan.skills if s.role_id != entity_id]
        plan.memories = [m for m in plan.memories if m.role_id != entity_id]
        for r in plan.roles:
            r.manages = [m for m in r.manages if m != entity_id]
        return f"Removed role '{entity_id}'"

    elif entity_type == "skill":
        if not any(s.id == entity_id for s in plan.skills):
            return f"Skill '{entity_id}' not found, skipped remove"
        plan.skills = [s for s in plan.skills if s.id != entity_id]
        return f"Removed skill '{entity_id}'"

    return f"Unknown entity type '{entity_type}' for remove"


def _apply_modify(
    plan: BootstrapPlan,
    entity_type: str,
    entity_id: str,
    fields: dict[str, Any],
) -> str:
    """Modify fields on an existing entity."""
    if entity_type == "department":
        dept = next((d for d in plan.departments if d.id == entity_id), None)
        if not dept:
            return f"Department '{entity_id}' not found, skipped modify"
        for k, v in fields.items():
            if hasattr(dept, k):
                setattr(dept, k, v)
        return f"Modified department '{entity_id}': {list(fields.keys())}"

    elif entity_type == "role":
        role = next((r for r in plan.roles if r.id == entity_id), None)
        if not role:
            return f"Role '{entity_id}' not found, skipped modify"
        for k, v in fields.items():
            if hasattr(role, k):
                setattr(role, k, v)
        return f"Modified role '{entity_id}': {list(fields.keys())}"

    elif entity_type == "skill":
        skill = next((s for s in plan.skills if s.id == entity_id), None)
        if not skill:
            return f"Skill '{entity_id}' not found, skipped modify"
        for k, v in fields.items():
            if hasattr(skill, k):
                setattr(skill, k, v)
        return f"Modified skill '{entity_id}': {list(fields.keys())}"

    return f"Unknown entity type '{entity_type}' for modify"
