"""Conversational skill execution MCP tool.

Provides a ``run_skill`` tool that lets a role execute one of its own
skills mid-conversation.  Follows the same ``contextvars.ContextVar``
pattern as ``delegation.py`` — the caller's identity is carried into the
tool handler so it can validate skill ownership and delegate to
``SkillExecutor.execute()``.

Usage::

    from src.mcp_servers.skill_runner import (
        set_skill_runner_context, clear_skill_runner_context,
    )

    # Before the agent turn:
    set_skill_runner_context(role_id, registry, user_id, role_context)

    # After the agent turn (in finally block):
    clear_skill_runner_context()
"""

from __future__ import annotations

import contextvars
from typing import Any

import structlog

from src.agent.tool_registry import tool
from src.mcp_servers.helpers import error_response, text_response

logger = structlog.get_logger(__name__)

# Max skill runs per single conversation turn (cost control)
_MAX_RUNS_PER_TURN = 2

# ---------------------------------------------------------------------------
# Skill runner context — per-async-task via contextvars (concurrency-safe)
# ---------------------------------------------------------------------------

_skill_runner_context_var: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "skill_runner_context", default=None
)

_skill_run_count_var: contextvars.ContextVar[int] = contextvars.ContextVar(
    "skill_run_count", default=0
)


def set_skill_runner_context(
    role_id: str,
    registry: Any,
    user_id: str,
    role_context: str = "",
) -> None:
    """Set the skill runner context for the current conversation turn.

    Called before running an agent's conversation turn so the ``run_skill``
    tool knows which role is calling and can validate skill ownership.

    Args:
        role_id: The calling role's ID.
        registry: The loaded ``SkillRegistry`` instance.
        user_id: The Slack user who initiated the conversation.
        role_context: Pre-composed role context string.
    """
    _skill_runner_context_var.set(
        {
            "role_id": role_id,
            "registry": registry,
            "user_id": user_id,
            "role_context": role_context,
        }
    )
    _skill_run_count_var.set(0)


def clear_skill_runner_context() -> None:
    """Clear skill runner context after a conversation turn completes."""
    _skill_runner_context_var.set(None)
    _skill_run_count_var.set(0)


# ---------------------------------------------------------------------------
# Tool: run_skill
# ---------------------------------------------------------------------------


@tool(
    name="run_skill",
    description=(
        "Execute one of your own skills to produce analysis or output. "
        "Use this when the user asks you to run a specific analysis "
        "(e.g. creative cuts, budget pacing, anomaly detection). "
        "The skill runs with full tool access and returns results "
        "that you can summarize in the conversation. "
        "You can run up to 2 skills per message."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "skill_id": {
                "type": "string",
                "description": (
                    "The ID of your skill to run "
                    "(e.g. 'fb_creative_cuts', 'anomaly_detector', "
                    "'budget_pacing_check')."
                ),
            },
            "analysis_focus": {
                "type": "string",
                "description": (
                    "Optional: specific question or focus for the analysis. "
                    "For example: 'focus on California ad sets' or "
                    "'look at the last 7 days only'."
                ),
            },
        },
        "required": ["skill_id"],
    },
)
async def run_skill(
    skill_id: str,
    analysis_focus: str = "",
) -> dict[str, Any]:
    """Execute a skill in the current role's context."""
    # -- Check context --
    ctx = _skill_runner_context_var.get()
    if ctx is None:
        return error_response(
            "Skill execution not available — no conversation context. "
            "This tool can only be used during a conversation turn."
        )

    role_id = ctx["role_id"]
    registry = ctx["registry"]
    user_id = ctx["user_id"]
    role_context = ctx.get("role_context", "")

    # -- Check run count --
    count = _skill_run_count_var.get()
    if count >= _MAX_RUNS_PER_TURN:
        return error_response(
            f"Maximum skill runs per message reached ({_MAX_RUNS_PER_TURN}). "
            "Please ask the user to send another message to run more skills."
        )

    # -- Validate skill belongs to this role --
    role = registry.get_role(role_id)
    if role is None:
        return error_response(f"Role '{role_id}' not found in registry.")

    if skill_id not in role.briefing_skills:
        available = ", ".join(sorted(role.briefing_skills))
        return error_response(
            f"Skill '{skill_id}' is not in your skill set. Your available skills: {available}"
        )

    # -- Load skill definition --
    skill = registry.get(skill_id)
    if skill is None:
        return error_response(f"Skill '{skill_id}' not found in registry.")

    # -- Execute via SkillExecutor --
    logger.info(
        "run_skill.start",
        role_id=role_id,
        skill_id=skill_id,
        skill_type=skill.skill_type,
        analysis_focus=analysis_focus[:100] if analysis_focus else "",
    )

    try:
        from src.agent.core import SideraAgent
        from src.skills.executor import SkillExecutor

        agent = SideraAgent()
        executor = SkillExecutor(agent=agent, registry=registry)

        params = {"analysis_focus": analysis_focus} if analysis_focus else None

        result = await executor.execute(
            skill_id=skill_id,
            user_id=user_id,
            accounts=[],
            params=params,
            role_context=role_context,
        )

        _skill_run_count_var.set(count + 1)

        cost_usd = result.cost.get("total_cost_usd", 0)
        logger.info(
            "run_skill.complete",
            role_id=role_id,
            skill_id=skill_id,
            cost_usd=cost_usd,
            output_len=len(result.output_text),
        )

        return text_response(
            f"**Skill Result: {skill.name}**\n\n"
            f"{result.output_text}\n\n"
            f"---\n"
            f"_Skill: {skill_id} | "
            f"Cost: ${cost_usd:.3f} | "
            f"Type: {skill.skill_type}_"
        )

    except Exception as exc:
        logger.exception(
            "run_skill.error",
            role_id=role_id,
            skill_id=skill_id,
        )
        return error_response(f"Skill execution failed: {exc}")
