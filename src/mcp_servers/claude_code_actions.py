"""Claude Code task proposal MCP tools for Sidera.

Provides a ``propose_claude_code_task`` tool that agents call during
conversations to propose Claude Code task execution for human approval.
Proposals are collected per-turn using ``contextvars.ContextVar`` so that
concurrent conversations never leak tasks between users.

The conversation runner creates DB approval items and posts Approve/Reject
buttons in the Slack thread (with task preview showing skill, prompt,
budget, and permission mode).

Tools:
    1. propose_claude_code_task — Propose a Claude Code task for approval

Usage:
    from src.mcp_servers.claude_code_actions import (
        get_pending_cc_tasks, clear_pending_cc_tasks,
    )
"""

from __future__ import annotations

import contextvars
from typing import Any

import structlog

from src.agent.tool_registry import tool
from src.mcp_servers.helpers import error_response, text_response

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Pending CC tasks — per-async-task via contextvars (concurrency-safe)
# ---------------------------------------------------------------------------

_pending_cc_tasks_var: contextvars.ContextVar[list[dict[str, Any]]] = contextvars.ContextVar(
    "pending_cc_tasks", default=[]
)


def _get_cc_tasks_list() -> list[dict[str, Any]]:
    """Get the current task's pending CC tasks list, creating if needed."""
    try:
        return _pending_cc_tasks_var.get()
    except LookupError:
        tasks: list[dict[str, Any]] = []
        _pending_cc_tasks_var.set(tasks)
        return tasks


def get_pending_cc_tasks() -> list[dict[str, Any]]:
    """Return and clear all pending Claude Code task proposals for this turn.

    Called by the conversation runner after an agent turn completes to
    collect any CC task proposals the agent made via the MCP tool.
    Scoped to the current async task — safe for concurrent use.

    Returns:
        List of recommendation dicts ready for approval processing.
    """
    tasks = list(_get_cc_tasks_list())
    _pending_cc_tasks_var.set([])
    return tasks


def clear_pending_cc_tasks() -> None:
    """Clear pending CC tasks without returning them."""
    _pending_cc_tasks_var.set([])


# ---------------------------------------------------------------------------
# Tool: propose_claude_code_task
# ---------------------------------------------------------------------------

PROPOSE_CC_TASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "skill_id": {
            "type": "string",
            "description": (
                "The skill to execute as a Claude Code task. Must exist in the skill registry."
            ),
        },
        "description": {
            "type": "string",
            "description": (
                "Human-readable description of the task for the approval preview shown in Slack."
            ),
        },
        "reasoning": {
            "type": "string",
            "description": "Why this task should be executed with Claude Code.",
        },
        "prompt": {
            "type": "string",
            "description": (
                "The task prompt describing what Claude Code should do. "
                "If omitted, the skill's prompt_template is used."
            ),
        },
        "max_budget_usd": {
            "type": "number",
            "description": "Cost cap for this task in USD (default: 5.0).",
        },
        "permission_mode": {
            "type": "string",
            "enum": ["default", "acceptEdits", "plan", "bypassPermissions"],
            "description": (
                "Claude Code permission level. "
                "default=most restrictive, acceptEdits=allows file edits, "
                "plan=propose-only, bypassPermissions=full autonomy. "
                "Default: acceptEdits."
            ),
        },
        "risk_level": {
            "type": "string",
            "enum": ["low", "medium", "high"],
            "description": "Risk assessment for the approval preview.",
        },
    },
    "required": ["skill_id", "description", "reasoning"],
}


@tool(
    name="propose_claude_code_task",
    description=(
        "Propose a Claude Code task for human approval. Claude Code tasks "
        "run as headless agentic instances with full file editing, bash "
        "execution, and multi-turn capabilities. Use this when the user "
        "asks to 'use claude code' or requests a complex task that needs "
        "Claude Code's full capabilities. The task will appear in Slack "
        "with Approve/Reject buttons before executing."
    ),
    input_schema=PROPOSE_CC_TASK_SCHEMA,
)
async def propose_claude_code_task(
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Propose a Claude Code task — queued for human approval."""
    skill_id = (arguments.get("skill_id") or "").strip()
    description = (arguments.get("description") or "").strip()
    reasoning = (arguments.get("reasoning") or "").strip()
    prompt = (arguments.get("prompt") or "").strip()
    max_budget = float(arguments.get("max_budget_usd", 5.0))
    permission_mode = (arguments.get("permission_mode") or "acceptEdits").strip()
    risk_level = (arguments.get("risk_level") or "medium").strip()

    if not skill_id:
        return error_response("skill_id is required.")
    if not description:
        return error_response("description is required.")
    if not reasoning:
        return error_response("reasoning is required.")

    # Validate skill exists
    try:
        from src.skills.db_loader import load_registry_with_db

        registry = await load_registry_with_db()
        skill = registry.get_skill(skill_id)
        if not skill:
            return error_response(f"Skill '{skill_id}' not found in registry.")
    except Exception as exc:
        logger.warning(
            "propose_cc_task.skill_validation_failed",
            skill_id=skill_id,
            error=str(exc),
        )
        return error_response(f"Could not validate skill: {exc}")

    # Build recommendation in the same format as propose_action / propose_skill_change
    recommendation: dict[str, Any] = {
        "action_type": "claude_code_task",
        "description": description,
        "reasoning": reasoning,
        "projected_impact": (f"Claude Code: {skill.name} (${max_budget:.2f})"),
        "risk_level": risk_level,
        "action_params": {
            "skill_id": skill_id,
            "skill_name": skill.name,
            "prompt": prompt,
            "max_budget_usd": max_budget,
            "permission_mode": permission_mode,
        },
    }

    _get_cc_tasks_list().append(recommendation)

    logger.info(
        "propose_cc_task.queued",
        skill_id=skill_id,
        budget=max_budget,
        permission_mode=permission_mode,
    )

    return text_response(
        f"Claude Code task proposal queued for human approval.\n"
        f"Skill: {skill.name} ({skill_id})\n"
        f"Budget cap: ${max_budget:.2f}\n"
        f"Permission mode: {permission_mode}\n"
        f"The user will see Approve/Reject buttons in this thread."
    )
