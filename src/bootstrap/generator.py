"""Plan generator for the bootstrap pipeline.

Pure Python -- no LLM calls.  Takes extracted knowledge and merges,
deduplicates, validates, and assembles it into a complete
``BootstrapPlan`` ready for human review.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from src.bootstrap.models import (
    BootstrapPlan,
    ExtractedDepartment,
    ExtractedKnowledge,
    ExtractedMemory,
    ExtractedRole,
    ExtractedSkill,
)

logger = structlog.get_logger(__name__)

# Valid skill categories (from src.skills.schema)
_VALID_CATEGORIES = {
    "monitoring",
    "optimization",
    "reporting",
    "analysis",
    "creative",
    "planning",
    "forecasting",
    "compliance",
    "strategy",
    "operations",
    "general",
}

# Valid model values
_VALID_MODELS = {"haiku", "sonnet", "opus"}


def generate_plan(
    knowledge: ExtractedKnowledge,
    *,
    source_folder_id: str = "",
    documents_crawled: int = 0,
    documents_classified: int = 0,
    documents_extracted: int = 0,
    estimated_cost: float = 0.0,
) -> BootstrapPlan:
    """Assemble extracted knowledge into a validated BootstrapPlan.

    Steps:
    1. Deduplicate departments and roles (by name similarity)
    2. Normalize IDs to valid slugs
    3. Resolve manager hierarchies
    4. Assign skills to roles
    5. Validate all entities
    6. Assemble into BootstrapPlan
    """
    errors: list[str] = []

    # --- Step 1: Deduplicate ---
    departments = _deduplicate_departments(knowledge.departments)
    roles = _deduplicate_roles(knowledge.roles)

    # --- Step 2: Normalize IDs ---
    dept_id_map = _normalize_entity_ids(departments, "department")
    role_id_map = _normalize_entity_ids(roles, "role")

    # Update role references to use normalized dept IDs
    for role in roles:
        if role.department_id in dept_id_map:
            role.department_id = dept_id_map[role.department_id]

    # Update manager references to use normalized role IDs
    for role in roles:
        role.manages = [
            role_id_map.get(m, m) for m in role.manages if role_id_map.get(m, m)
        ]

    # --- Step 3: Validate department references ---
    valid_dept_ids = {d.id for d in departments}
    for role in roles:
        if role.department_id not in valid_dept_ids:
            if departments:
                role.department_id = departments[0].id
                errors.append(
                    f"Role '{role.id}' had unknown dept '{role.department_id}'"
                    f" -- assigned to '{departments[0].id}'"
                )
            else:
                errors.append(
                    f"Role '{role.id}' has unknown dept and no departments exist"
                )

    # --- Step 4: Validate and fix skills ---
    valid_role_ids = {r.id for r in roles}
    skills = _validate_skills(knowledge.skills, valid_role_ids, valid_dept_ids, errors)

    # Update skill references to normalized IDs
    for skill in skills:
        if skill.role_id in role_id_map:
            skill.role_id = role_id_map[skill.role_id]
        if skill.department_id in dept_id_map:
            skill.department_id = dept_id_map[skill.department_id]

    # --- Step 5: Validate memories ---
    memories = _validate_memories(knowledge.memories, valid_role_ids, valid_dept_ids)

    # --- Step 6: Auto-detect manager roles ---
    _detect_managers(roles)

    # --- Step 7: Assign briefing_skills ---
    _assign_briefing_skills(roles, skills)

    plan = BootstrapPlan(
        source_folder_id=source_folder_id,
        documents_crawled=documents_crawled,
        documents_classified=documents_classified,
        documents_extracted=documents_extracted,
        departments=departments,
        roles=roles,
        skills=skills,
        memories=memories,
        estimated_cost=estimated_cost,
        errors=errors,
    )

    logger.info(
        "bootstrap.plan_generated",
        plan_id=plan.id,
        departments=len(departments),
        roles=len(roles),
        skills=len(skills),
        memories=len(memories),
        errors=len(errors),
    )

    return plan


# =====================================================================
# Deduplication
# =====================================================================


def _deduplicate_departments(
    depts: list[ExtractedDepartment],
) -> list[ExtractedDepartment]:
    """Merge departments with the same or very similar IDs/names."""
    seen: dict[str, ExtractedDepartment] = {}

    for dept in depts:
        key = _normalize_slug(dept.id or dept.name)
        if key in seen:
            # Merge: combine descriptions, vocabulary, source docs
            existing = seen[key]
            if dept.description and not existing.description:
                existing.description = dept.description
            if dept.context and not existing.context:
                existing.context = dept.context
            existing.vocabulary.extend(dept.vocabulary)
            existing.source_docs.extend(dept.source_docs)
        else:
            dept.id = key
            seen[key] = dept

    # Deduplicate vocabulary within each department
    for dept in seen.values():
        seen_terms: set[str] = set()
        unique_vocab: list[dict[str, str]] = []
        for v in dept.vocabulary:
            term = v.get("term", "").lower()
            if term and term not in seen_terms:
                seen_terms.add(term)
                unique_vocab.append(v)
        dept.vocabulary = unique_vocab

    return list(seen.values())


def _deduplicate_roles(roles: list[ExtractedRole]) -> list[ExtractedRole]:
    """Merge roles with the same or very similar IDs/names."""
    seen: dict[str, ExtractedRole] = {}

    for role in roles:
        key = _normalize_slug(role.id or role.name)
        if key in seen:
            existing = seen[key]
            if role.persona and not existing.persona:
                existing.persona = role.persona
            if role.description and not existing.description:
                existing.description = role.description
            existing.principles.extend(role.principles)
            existing.goals.extend(role.goals)
            existing.source_docs.extend(role.source_docs)
            if role.manages:
                existing.manages = list(
                    set(existing.manages) | set(role.manages)
                )
        else:
            role.id = key
            seen[key] = role

    # Deduplicate principles/goals within each role
    for role in seen.values():
        role.principles = list(dict.fromkeys(role.principles))
        role.goals = list(dict.fromkeys(role.goals))

    return list(seen.values())


# =====================================================================
# Normalization and validation
# =====================================================================


def _normalize_slug(text: str) -> str:
    """Convert a name or ID to a valid slug (lowercase, underscores)."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = slug.strip("_")
    return slug or "unknown"


def _normalize_entity_ids(
    entities: list[Any], entity_type: str
) -> dict[str, str]:
    """Normalize IDs on a list of entities.  Returns old->new ID mapping."""
    id_map: dict[str, str] = {}
    seen_ids: set[str] = set()

    for entity in entities:
        old_id = entity.id
        new_id = _normalize_slug(old_id)

        # Handle collisions
        if new_id in seen_ids:
            counter = 2
            while f"{new_id}_{counter}" in seen_ids:
                counter += 1
            new_id = f"{new_id}_{counter}"

        if old_id != new_id:
            id_map[old_id] = new_id

        entity.id = new_id
        seen_ids.add(new_id)

    return id_map


def _validate_skills(
    skills: list[ExtractedSkill],
    valid_role_ids: set[str],
    valid_dept_ids: set[str],
    errors: list[str],
) -> list[ExtractedSkill]:
    """Validate and fix skill definitions."""
    validated: list[ExtractedSkill] = []
    seen_ids: set[str] = set()

    for skill in skills:
        # Normalize ID
        skill.id = _normalize_slug(skill.id or skill.name)

        # Handle ID collisions
        if skill.id in seen_ids:
            counter = 2
            while f"{skill.id}_{counter}" in seen_ids:
                counter += 1
            skill.id = f"{skill.id}_{counter}"
        seen_ids.add(skill.id)

        # Validate category
        if skill.category not in _VALID_CATEGORIES:
            skill.category = "general"

        # Validate model
        if skill.model not in _VALID_MODELS:
            skill.model = "sonnet"

        # Validate role reference
        if skill.role_id not in valid_role_ids:
            errors.append(
                f"Skill '{skill.id}' references unknown role '{skill.role_id}'"
            )

        # Validate department reference
        if skill.department_id not in valid_dept_ids:
            errors.append(
                f"Skill '{skill.id}' references unknown dept '{skill.department_id}'"
            )

        validated.append(skill)

    return validated


def _validate_memories(
    memories: list[ExtractedMemory],
    valid_role_ids: set[str],
    valid_dept_ids: set[str],
) -> list[ExtractedMemory]:
    """Filter memories that reference valid roles and departments."""
    valid: list[ExtractedMemory] = []
    valid_types = {"insight", "decision", "relationship", "pattern"}

    for mem in memories:
        if mem.role_id and mem.role_id not in valid_role_ids:
            continue
        if mem.department_id and mem.department_id not in valid_dept_ids:
            continue
        if mem.memory_type not in valid_types:
            mem.memory_type = "insight"
        if mem.title and mem.content:
            valid.append(mem)

    return valid


# =====================================================================
# Hierarchy helpers
# =====================================================================


def _detect_managers(roles: list[ExtractedRole]) -> None:
    """Auto-detect manager roles based on 'manages' lists.

    If a role's ``manages`` list references other role IDs, ensure
    those IDs actually exist.  Also look for roles whose names suggest
    management (head_of, director, vp, manager, lead).
    """
    role_ids = {r.id for r in roles}

    for role in roles:
        # Clean up manages list to only reference valid roles
        role.manages = [m for m in role.manages if m in role_ids and m != role.id]

    # Detect managers by name pattern if manages list is empty
    manager_re = re.compile(
        r"(head_of|director|vp_|vice_president|manager|chief|cto|cfo|ceo|coo|lead)",
        re.IGNORECASE,
    )
    for role in roles:
        if not role.manages and manager_re.search(role.id):
            # Find roles in the same department that aren't managers
            dept_roles = [
                r.id
                for r in roles
                if r.department_id == role.department_id
                and r.id != role.id
                and not manager_re.search(r.id)
            ]
            if dept_roles:
                role.manages = dept_roles


def _assign_briefing_skills(
    roles: list[ExtractedRole], skills: list[ExtractedSkill]
) -> None:
    """No-op in the data model (briefing_skills lives in RoleDefinition).

    This prepares the mapping but the actual assignment happens during
    execution when ``create_org_role`` is called.  We just validate that
    each skill has a valid role_id.
    """
    # Group skills by role for logging
    skills_by_role: dict[str, list[str]] = {}
    for skill in skills:
        skills_by_role.setdefault(skill.role_id, []).append(skill.id)

    for role in roles:
        skill_count = len(skills_by_role.get(role.id, []))
        if skill_count == 0:
            logger.debug(
                "bootstrap.role_no_skills",
                role_id=role.id,
                hint="This role has no skills assigned",
            )
