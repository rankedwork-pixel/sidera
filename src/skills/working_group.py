"""Multi-agent working group coordination for Sidera.

Working groups are ad hoc teams of roles that form around a shared
objective, coordinate a plan, execute member tasks, and produce a
unified synthesis.

Architecture:

1. **Formation** — A manager role calls ``form_working_group`` MCP tool
   with an objective and list of member role IDs.  Creates a DB session
   and dispatches the ``sidera/working_group.run`` Inngest event.

2. **Planning** — The coordinator (the manager who formed the group) runs
   a planning LLM call that receives the objective + member role
   descriptions and produces task assignments for each member.

3. **Execution** — Each member role runs its assigned task as a separate
   Inngest step (sequential with checkpointing for durability, but
   logically independent — each member gets full persona, tools, memory).

4. **Synthesis** — The coordinator runs a synthesis LLM call that sees
   all member outputs and produces a unified result.

Uses:
    - ``RoleExecutor`` for member execution (full persona, tools, memory)
    - ``SideraAgent`` for planning and synthesis LLM calls
    - ``WorkingGroupSession`` DB model for state tracking
    - ``compose_role_context()`` for member context composition
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_MEMBERS = 10
_MAX_CONCURRENT_GROUPS = 5
_DEFAULT_COST_CAP_USD = 5.0
_DEFAULT_MAX_DURATION_MIN = 60

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

PLANNING_PROMPT = """\
You are coordinating a multi-agent working group. Your job is to analyze \
the objective and assign specific tasks to each member role based on their \
expertise.

## Objective
{objective}

## Available Members
{member_descriptions}

## Shared Context
{shared_context}

## Instructions

Produce a JSON plan with this exact structure:
```json
{{
  "plan_summary": "Brief 1-2 sentence summary of the approach",
  "assignments": [
    {{
      "role_id": "the_role_id",
      "task": "Specific task description for this role",
      "priority": "high|medium|low",
      "depends_on": []
    }}
  ]
}}
```

Rules:
- Every member in the list MUST get exactly one assignment
- Tasks should be specific and actionable, not vague
- Each task should leverage the role's specific expertise
- Include ``depends_on`` (list of role_ids) only if a role genuinely \
needs another role's output first — prefer independent parallel tasks
- Keep tasks focused — each role should complete in a single agent run
"""

SYNTHESIS_PROMPT = """\
You are synthesizing the outputs from a multi-agent working group into a \
single coherent result.

## Original Objective
{objective}

## Plan
{plan_summary}

## Member Outputs

{member_outputs}

## Instructions

Synthesize all member outputs into a single unified response that:
1. Addresses the original objective directly
2. Integrates insights from all members
3. Resolves any conflicting information
4. Highlights the most important findings and recommendations
5. Is concise but comprehensive (aim for 200-500 words)

Start with a one-sentence executive summary, then provide the synthesis.
"""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class WorkingGroupPlan:
    """Plan produced by the coordinator during the planning phase."""

    plan_summary: str = ""
    assignments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MemberTaskResult:
    """Result from a single member's task execution."""

    role_id: str = ""
    task: str = ""
    output: str = ""
    cost_usd: float = 0.0
    success: bool = True
    error: str = ""


@dataclass
class WorkingGroupResult:
    """Final result of a complete working group execution."""

    group_id: str = ""
    objective: str = ""
    coordinator_role_id: str = ""
    plan: WorkingGroupPlan | None = None
    member_results: list[MemberTaskResult] = field(default_factory=list)
    synthesis: str = ""
    total_cost_usd: float = 0.0
    success: bool = True
    error: str = ""


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def generate_group_id() -> str:
    """Generate a unique working group ID."""
    return f"wg-{uuid.uuid4().hex[:12]}"


def build_member_descriptions(
    member_roles: list[Any],
) -> str:
    """Build a compact description of member roles for the planning prompt.

    Args:
        member_roles: List of ``RoleDefinition`` objects.

    Returns:
        Formatted string with role ID, name, and description for each member.
    """
    lines: list[str] = []
    for role in member_roles:
        lines.append(f"- **{role.id}** ({role.name}): {role.description}")
    return "\n".join(lines)


def format_member_outputs(results: list[MemberTaskResult]) -> str:
    """Format member task results for the synthesis prompt.

    Args:
        results: List of completed member task results.

    Returns:
        Formatted string with each member's task and output.
    """
    sections: list[str] = []
    for r in results:
        status = "completed" if r.success else f"FAILED: {r.error}"
        sections.append(f"### {r.role_id} ({status})\n**Task:** {r.task}\n\n{r.output}\n")
    return "\n---\n".join(sections)


def parse_plan(plan_text: str) -> WorkingGroupPlan:
    """Parse a planning LLM response into a WorkingGroupPlan.

    Extracts JSON from the response text, handling markdown code fences.

    Args:
        plan_text: Raw LLM response containing the plan JSON.

    Returns:
        Parsed ``WorkingGroupPlan``.
    """
    import json

    text = plan_text.strip()

    # Strip markdown code fences
    if "```" in text:
        lines = text.split("\n")
        json_lines: list[str] = []
        in_block = False
        for line in lines:
            if line.strip().startswith("```"):
                in_block = not in_block
                continue
            if in_block:
                json_lines.append(line)
        text = "\n".join(json_lines).strip()

    # Find JSON object
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        text = text[start:end]

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(
            "working_group.plan_parse_error",
            text_preview=plan_text[:200],
        )
        return WorkingGroupPlan(plan_summary="Plan parsing failed")

    return WorkingGroupPlan(
        plan_summary=data.get("plan_summary", ""),
        assignments=data.get("assignments", []),
    )


def validate_working_group_request(
    coordinator_role_id: str,
    member_role_ids: list[str],
    objective: str,
    registry: Any,
) -> list[str]:
    """Validate a working group formation request.

    Args:
        coordinator_role_id: ID of the coordinator (must be a manager).
        member_role_ids: IDs of member roles.
        objective: The group's objective.
        registry: The loaded ``SkillRegistry``.

    Returns:
        List of error strings. Empty = valid.
    """
    errors: list[str] = []

    if not objective.strip():
        errors.append("Objective is empty")

    if not member_role_ids:
        errors.append("No member roles specified")

    if len(member_role_ids) > _MAX_MEMBERS:
        errors.append(f"Too many members: {len(member_role_ids)} (max {_MAX_MEMBERS})")

    # Coordinator must be a manager
    coordinator = registry.get_role(coordinator_role_id)
    if coordinator is None:
        errors.append(f"Coordinator role '{coordinator_role_id}' not found")
    elif not coordinator.manages:
        errors.append(f"Coordinator '{coordinator_role_id}' is not a manager role")

    # All members must exist
    for rid in member_role_ids:
        role = registry.get_role(rid)
        if role is None:
            errors.append(f"Member role '{rid}' not found in registry")

    # Coordinator shouldn't be in the member list
    if coordinator_role_id in member_role_ids:
        errors.append("Coordinator should not be in the member list")

    # No duplicates
    if len(member_role_ids) != len(set(member_role_ids)):
        errors.append("Duplicate member role IDs")

    return errors
