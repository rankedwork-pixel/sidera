"""Reflection → Skill/Role Evolution pipeline.

Scans accumulated lesson memories for recurring friction patterns and
auto-generates skill change proposals. Also scans for capability gap
observations (type "gap" from reflections) and proposes new roles when
agents repeatedly encounter out-of-scope requests.

The pipeline runs after the post-run reflection step in the role runner
workflow. It:

1. Loads recent lesson memories for the role (last 30 days).
2. Clusters them by ``source_skill_id`` to find skills with multiple lessons.
3. For skills with >= ``_MIN_LESSONS_FOR_PROPOSAL`` lessons, uses a cheap
   Haiku call to determine whether the lessons suggest a skill modification.
4. If yes, generates a structured skill proposal and returns it for routing
   through the existing approval pipeline.

Gap detection (new):

1. Loads recent insight memories tagged with ``[Gap Detection]`` in content.
2. Groups by ``gap_domain`` (from ``evidence.gap_domain`` field).
3. For domains with >= ``_MIN_GAPS_FOR_ROLE_PROPOSAL`` observations, uses
   Haiku to determine whether a new role should be proposed.
4. If yes, generates a role proposal routed through the approval pipeline.

Cost: ~$0.01-0.02 per role run (one Haiku call when lessons exist).
Non-fatal: all errors return empty results.

Usage::

    from src.skills.reflection_evolution import (
        scan_lessons_for_proposals,
        scan_gaps_for_role_proposals,
    )

    skill_proposals = await scan_lessons_for_proposals(
        role_id="performance_media_buyer",
        department_id="marketing",
        user_id="user_123",
    )
    role_proposals = await scan_gaps_for_role_proposals(
        role_id="head_of_marketing",
        department_id="marketing",
        user_id="user_123",
    )
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# --- Graduated evidence thresholds ---
# More lessons required before proposing changes to more sensitive fields.
_TIER_MINOR = 3  # business_guidance only
_TIER_MODERATE = 5  # + system_supplement
_TIER_MAJOR = 7  # + prompt_template, output_format, model, max_turns

_TIER_FIELDS: dict[int, frozenset[str]] = {
    _TIER_MINOR: frozenset({"business_guidance"}),
    _TIER_MODERATE: frozenset({"business_guidance", "system_supplement"}),
    _TIER_MAJOR: frozenset(
        {
            "business_guidance",
            "system_supplement",
            "prompt_template",
            "output_format",
            "model",
            "max_turns",
        }
    ),
}

# Backward-compat alias
_MIN_LESSONS_FOR_PROPOSAL = _TIER_MINOR

# Maximum proposals per role per run.
_MAX_PROPOSALS_PER_RUN = 2

# How far back to look for lessons.
_LESSON_LOOKBACK_DAYS = 30


def _get_allowed_fields(lesson_count: int) -> frozenset[str]:
    """Return the set of fields allowed for a given lesson count."""
    for threshold in sorted(_TIER_FIELDS.keys(), reverse=True):
        if lesson_count >= threshold:
            return _TIER_FIELDS[threshold]
    return frozenset()


def _get_risk_level(lesson_count: int) -> str:
    """Return risk level label for a given lesson count."""
    if lesson_count >= _TIER_MAJOR:
        return "major"
    elif lesson_count >= _TIER_MODERATE:
        return "moderate"
    return "minor"


async def scan_lessons_for_proposals(
    role_id: str,
    department_id: str,
    user_id: str,
) -> list[dict[str, Any]]:
    """Scan recent lessons and propose skill changes for recurring friction.

    Loads lesson memories from the last ``_LESSON_LOOKBACK_DAYS`` days,
    groups them by ``source_skill_id``, and for skills with enough
    accumulated friction (>= ``_MIN_LESSONS_FOR_PROPOSAL``), asks Haiku
    whether a skill modification would help.

    Returns structured proposals compatible with
    ``format_proposal_as_recommendation()`` from the evolution module.

    Args:
        role_id: The role whose lessons to scan.
        department_id: The department the role belongs to.
        user_id: The advertiser user ID.

    Returns:
        List of proposal dicts, each with ``skill_id``, ``changes``,
        ``reasoning``, and ``lessons_referenced``. Empty on error or
        if no proposals are warranted.
    """
    try:
        from src.db import service as db_service
        from src.db.session import get_db_session

        cutoff = date.today() - timedelta(days=_LESSON_LOOKBACK_DAYS)

        async with get_db_session() as session:
            lessons = await db_service.search_role_memories(
                session,
                user_id=user_id,
                role_id=role_id,
                memory_type="lesson",
                limit=50,
            )

        # Filter to recent lessons only
        recent_lessons = []
        for lesson in lessons:
            created = getattr(lesson, "created_at", None)
            if created is not None:
                lesson_date = created.date() if hasattr(created, "date") else created
                if lesson_date < cutoff:
                    continue
            recent_lessons.append(lesson)

        if not recent_lessons:
            return []

        # Group by source_skill_id
        by_skill: dict[str, list[Any]] = defaultdict(list)
        for lesson in recent_lessons:
            skill_id = getattr(lesson, "source_skill_id", "") or ""
            # Skip reflection-generated metadata skill IDs
            if skill_id.startswith("reflection:") or skill_id.startswith("conversation:"):
                # Try to extract from evidence
                evidence = getattr(lesson, "evidence", None) or {}
                if isinstance(evidence, dict):
                    executed_skills = evidence.get("skills_executed", [])
                    if executed_skills:
                        for sid in executed_skills:
                            by_skill[sid].append(lesson)
                        continue
                # Can't determine skill — skip
                continue
            if skill_id:
                by_skill[skill_id].append(lesson)

        # Find skills with enough lessons for a proposal
        candidates: list[tuple[str, list[Any]]] = []
        for skill_id, skill_lessons in by_skill.items():
            if len(skill_lessons) >= _MIN_LESSONS_FOR_PROPOSAL:
                candidates.append((skill_id, skill_lessons))

        if not candidates:
            return []

        # Sort by lesson count (most friction first), limit proposals
        candidates.sort(key=lambda x: len(x[1]), reverse=True)
        candidates = candidates[:_MAX_PROPOSALS_PER_RUN]

        proposals: list[dict[str, Any]] = []
        for skill_id, skill_lessons in candidates:
            proposal = await _generate_proposal_from_lessons(
                skill_id=skill_id,
                lessons=skill_lessons,
                role_id=role_id,
                department_id=department_id,
                lesson_count=len(skill_lessons),
            )
            if proposal:
                proposals.append(proposal)

        logger.info(
            "reflection_evolution.scan_complete",
            role_id=role_id,
            lessons_scanned=len(recent_lessons),
            candidates=len(candidates),
            proposals_generated=len(proposals),
        )

        return proposals

    except Exception as exc:
        logger.warning(
            "reflection_evolution.scan_error",
            role_id=role_id,
            error=str(exc),
        )
        return []


async def _generate_proposal_from_lessons(
    skill_id: str,
    lessons: list[Any],
    role_id: str,
    department_id: str,
    lesson_count: int = 0,
) -> dict[str, Any] | None:
    """Use Haiku to determine if lessons warrant a skill change.

    Uses graduated evidence thresholds: more lessons unlock more sensitive
    fields. 3 lessons → business_guidance only, 5 → +system_supplement,
    7 → +prompt_template/output_format/model/max_turns.

    Args:
        skill_id: The skill that accumulated friction.
        lessons: List of lesson memory objects.
        role_id: The role proposing the change.
        department_id: The department context.
        lesson_count: Number of lessons (for threshold calculation).

    Returns:
        Proposal dict or None if no change is warranted.
    """
    from src.agent.api_client import run_agent_loop
    from src.config import settings
    from src.llm.provider import TaskType

    # Determine allowed fields based on evidence level
    effective_count = lesson_count or len(lessons)
    allowed_fields = _get_allowed_fields(effective_count)
    risk_level = _get_risk_level(effective_count)
    allowed_fields_str = " or ".join(f'"{f}"' for f in sorted(allowed_fields))

    # Build lesson summary (compact, cost-efficient)
    lesson_texts: list[str] = []
    for lesson in lessons[:10]:  # Cap at 10 to control token cost
        title = getattr(lesson, "title", "") or ""
        content = getattr(lesson, "content", "") or ""
        lesson_texts.append(f"- {title}: {content[:150]}")

    lesson_summary = "\n".join(lesson_texts)

    prompt = (
        f"You are analyzing recurring friction for skill '{skill_id}' "
        f"(role: {role_id}, dept: {department_id}).\n\n"
        f"These lessons have accumulated over recent runs:\n\n{lesson_summary}\n\n"
        "Based on these lessons, should the skill's definition be modified to "
        "prevent this friction in the future?\n\n"
        "If YES, respond with a JSON object:\n"
        "{\n"
        '  "should_modify": true,\n'
        f'  "field": {allowed_fields_str},\n'
        '  "addition": "text to ADD to the existing field (1-3 sentences)",\n'
        '  "reasoning": "why this change would help (1 sentence)"\n'
        "}\n\n"
        "If NO (the lessons are situational and don't suggest a permanent change), "
        "respond with:\n"
        '{"should_modify": false, "reasoning": "why not"}\n\n'
        "Return ONLY the JSON object. No markdown, no explanation."
    )

    try:
        result = await run_agent_loop(
            system_prompt=(
                "You are a skill improvement analyst. Respond only with "
                "a valid JSON object. No markdown, no explanation."
            ),
            user_prompt=prompt,
            model=settings.model_fast,  # Haiku
            tools=None,
            max_turns=1,
            task_type=TaskType.FRICTION_DETECTION,
        )
    except Exception:
        logger.warning(
            "reflection_evolution.llm_error",
            skill_id=skill_id,
        )
        return None

    # Parse response
    try:
        raw_text = result.text.strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3].strip()

        decision = json.loads(raw_text)
        if not isinstance(decision, dict):
            return None
    except (json.JSONDecodeError, ValueError):
        logger.warning(
            "reflection_evolution.parse_error",
            skill_id=skill_id,
            raw_text=result.text[:200],
        )
        return None

    if not decision.get("should_modify"):
        logger.info(
            "reflection_evolution.no_change_needed",
            skill_id=skill_id,
            reasoning=decision.get("reasoning", ""),
        )
        return None

    field = decision.get("field", "business_guidance")
    if field not in allowed_fields:
        field = "business_guidance"

    addition = decision.get("addition", "")
    if not addition:
        return None

    return {
        "skill_id": skill_id,
        "role_id": role_id,
        "department_id": department_id,
        "changes": {field: addition},
        "reasoning": decision.get("reasoning", "Auto-generated from recurring lessons"),
        "lessons_referenced": [getattr(lesson, "title", "")[:100] for lesson in lessons[:5]],
        "source": "reflection_evolution",
        "risk_level": risk_level,
        "lesson_count": effective_count,
    }


# =====================================================================
# Gap Detection → Role Proposals
# =====================================================================

# Minimum gap observations in the same domain before proposing a new role.
_MIN_GAPS_FOR_ROLE_PROPOSAL = 3

# How far back to look for gap observations.
_GAP_LOOKBACK_DAYS = 60

# Maximum role proposals per scan.
_MAX_ROLE_PROPOSALS_PER_SCAN = 1


async def scan_gaps_for_role_proposals(
    role_id: str,
    department_id: str,
    user_id: str,
) -> list[dict[str, Any]]:
    """Scan recent gap observations and propose new roles for unmet needs.

    Loads insight memories tagged with ``[Gap Detection]`` from the last
    ``_GAP_LOOKBACK_DAYS`` days, groups them by domain, and for domains
    with enough observations, asks Haiku whether a new role should be
    created.

    Only managers (roles with ``manages``) can trigger role proposals.
    Non-manager roles' gap observations are still recorded as memories
    for visibility, but proposals are only generated when a manager role
    runs this scan.

    Args:
        role_id: The manager role scanning for gaps.
        department_id: The department to create the new role in.
        user_id: The advertiser user ID.

    Returns:
        List of role proposal dicts compatible with
        ``format_role_proposal_as_recommendation()``. Empty on error
        or if no proposals are warranted.
    """
    try:
        from src.db import service as db_service
        from src.db.session import get_db_session
        from src.skills.db_loader import load_registry_with_db

        # Only managers can propose new roles.
        registry = await load_registry_with_db()
        manager_role = registry.get_role(role_id)
        if not manager_role or not manager_role.manages:
            return []

        cutoff = date.today() - timedelta(days=_GAP_LOOKBACK_DAYS)

        # Load insight memories — gap observations are stored as insights
        # with "[Gap Detection]" in content and gap_domain in evidence.
        async with get_db_session() as session:
            memories = await db_service.search_role_memories(
                session,
                user_id=user_id,
                role_id=role_id,
                memory_type="insight",
                limit=100,
            )

        # Filter to gap detection observations only
        gap_observations: list[Any] = []
        for mem in memories:
            content = getattr(mem, "content", "") or ""
            if "[Gap Detection]" not in content:
                continue
            created = getattr(mem, "created_at", None)
            if created is not None:
                mem_date = created.date() if hasattr(created, "date") else created
                if mem_date < cutoff:
                    continue
            gap_observations.append(mem)

        if len(gap_observations) < _MIN_GAPS_FOR_ROLE_PROPOSAL:
            return []

        # Group by gap_domain
        by_domain: dict[str, list[Any]] = defaultdict(list)
        for mem in gap_observations:
            evidence = getattr(mem, "evidence", None) or {}
            if isinstance(evidence, dict):
                domain = evidence.get("gap_domain", "unknown")
            else:
                domain = "unknown"
            by_domain[domain].append(mem)

        # Find domains with enough gap observations
        candidates: list[tuple[str, list[Any]]] = []
        for domain, domain_gaps in by_domain.items():
            if domain == "unknown":
                continue  # Skip if no domain label
            if len(domain_gaps) >= _MIN_GAPS_FOR_ROLE_PROPOSAL:
                candidates.append((domain, domain_gaps))

        if not candidates:
            return []

        # Sort by count (most gaps first), limit proposals
        candidates.sort(key=lambda x: len(x[1]), reverse=True)
        candidates = candidates[:_MAX_ROLE_PROPOSALS_PER_SCAN]

        proposals: list[dict[str, Any]] = []
        for domain, domain_gaps in candidates:
            proposal = await _generate_role_proposal_from_gaps(
                domain=domain,
                gaps=domain_gaps,
                role_id=role_id,
                department_id=department_id,
            )
            if proposal:
                proposals.append(proposal)

        logger.info(
            "gap_detection.scan_complete",
            role_id=role_id,
            gaps_scanned=len(gap_observations),
            domains=len(by_domain),
            candidates=len(candidates),
            proposals_generated=len(proposals),
        )

        return proposals

    except Exception as exc:
        logger.warning(
            "gap_detection.scan_error",
            role_id=role_id,
            error=str(exc),
        )
        return []


async def _generate_role_proposal_from_gaps(
    domain: str,
    gaps: list[Any],
    role_id: str,
    department_id: str,
) -> dict[str, Any] | None:
    """Use Haiku to determine if gap observations warrant a new role.

    Asks Haiku to analyze the accumulated gap observations for a domain
    and propose a new role if warranted.

    Args:
        domain: The domain/capability area (e.g. "compliance").
        gaps: List of gap observation memory objects.
        role_id: The manager proposing the role.
        department_id: The department to create the role in.

    Returns:
        Role proposal dict or None if no new role is warranted.
    """
    from src.agent.api_client import run_agent_loop
    from src.config import settings
    from src.llm.provider import TaskType

    # Build gap summary (compact, cost-efficient)
    gap_texts: list[str] = []
    for gap in gaps[:10]:  # Cap at 10 to control token cost
        title = getattr(gap, "title", "") or ""
        content = getattr(gap, "content", "") or ""
        # Strip the date/tag prefix for cleaner summary
        clean_content = content
        if "] " in clean_content:
            # Remove "[date] [Gap Detection] " prefix
            parts = clean_content.split("] ", 2)
            if len(parts) >= 3:
                clean_content = parts[2]
            elif len(parts) >= 2:
                clean_content = parts[1]
        gap_texts.append(f"- {title}: {clean_content[:200]}")

    gap_summary = "\n".join(gap_texts)

    prompt = (
        f"You are analyzing recurring capability gaps detected by role "
        f"'{role_id}' in department '{department_id}'.\n\n"
        f"The agent has repeatedly encountered requests in the domain of "
        f"'{domain}' that fall outside its own capabilities and outside "
        f"any existing role.\n\n"
        f"Gap observations ({len(gaps)} total):\n\n{gap_summary}\n\n"
        "Based on these observations, should a new agent role be created "
        "to handle this domain?\n\n"
        "If YES, respond with a JSON object:\n"
        "{\n"
        '  "should_create": true,\n'
        '  "role_id": "suggested_role_id_snake_case",\n'
        '  "name": "Human-Readable Role Name",\n'
        '  "description": "1-2 sentence description of the role",\n'
        '  "persona": "1-3 sentence persona for the agent",\n'
        '  "reasoning": "why this role is needed (1-2 sentences)"\n'
        "}\n\n"
        "If NO (the gaps are situational, already covered by existing "
        "roles, or too vague to warrant a dedicated role), respond with:\n"
        '{"should_create": false, "reasoning": "why not"}\n\n'
        "Return ONLY the JSON object. No markdown, no explanation."
    )

    try:
        result = await run_agent_loop(
            system_prompt=(
                "You are an organizational design analyst. Respond only with "
                "a valid JSON object. No markdown, no explanation."
            ),
            user_prompt=prompt,
            model=settings.model_fast,  # Haiku
            tools=None,
            max_turns=1,
            task_type=TaskType.FRICTION_DETECTION,
        )
    except Exception:
        logger.warning(
            "gap_detection.llm_error",
            domain=domain,
            role_id=role_id,
        )
        return None

    # Parse response
    try:
        raw_text = result.text.strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3].strip()

        decision = json.loads(raw_text)
        if not isinstance(decision, dict):
            return None
    except (json.JSONDecodeError, ValueError):
        logger.warning(
            "gap_detection.parse_error",
            domain=domain,
            raw_text=result.text[:200],
        )
        return None

    if not decision.get("should_create"):
        logger.info(
            "gap_detection.no_role_needed",
            domain=domain,
            reasoning=decision.get("reasoning", ""),
        )
        return None

    # Build role proposal dict compatible with format_role_proposal_as_recommendation
    proposed_role_id = decision.get("role_id", f"{domain.lower().replace(' ', '_')}_specialist")
    proposed_name = decision.get("name", f"{domain.title()} Specialist")
    proposed_description = decision.get("description", "")
    proposed_persona = decision.get("persona", "")
    reasoning = decision.get("reasoning", f"Auto-proposed from {len(gaps)} gap observations")

    if not proposed_description or not proposed_persona:
        return None

    return {
        "proposed_changes": {
            "name": proposed_name,
            "description": proposed_description,
            "persona": proposed_persona,
        },
        # role_id is None → signals a new role (not modification)
        "proposer_role_id": role_id,
        "department_id": department_id,
        "reasoning": reasoning,
        "evidence": {
            "gap_count": len(gaps),
            "domain": domain,
            "observation_titles": [getattr(g, "title", "")[:100] for g in gaps[:5]],
            "source": "gap_detection",
        },
        "suggested_role_id": proposed_role_id,
    }
