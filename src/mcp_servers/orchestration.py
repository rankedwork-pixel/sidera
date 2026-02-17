"""Self-orchestration MCP tool for Sidera agents.

Exposes the ``Orchestrator`` to agents running inside ``run_agent_loop()``
so that a role (typically a manager) can dispatch a supervised, multi-step
investigation to any role — with iterative refinement, evaluation, and
optional role delegation — all within a single tool call.

This bridges the gap between simple ``delegate_to_role`` (one-shot
delegation, no quality check) and the full Orchestrator supervision
loop (dispatch → evaluate → refine/delegate → iterate).

Tools:
    1. orchestrate_task — Run a supervised multi-step task with evaluation

Uses ``contextvars.ContextVar`` for recursion protection (same pattern as
``delegation.py``).  Max depth = 1: the orchestrated sub-agent cannot
itself call ``orchestrate_task``.

Usage::

    from src.mcp_servers.orchestration import (
        set_orchestration_context,
        clear_orchestration_context,
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
# Recursion guard — max depth 1 (no nested orchestrations)
# ---------------------------------------------------------------------------

_orchestration_depth_var: contextvars.ContextVar[int] = contextvars.ContextVar(
    "orchestration_depth", default=0
)

# Budget cap per orchestration call (prevents runaway costs)
_MAX_ORCHESTRATION_BUDGET = 10.0

# Iteration cap per orchestration call
_MAX_ORCHESTRATION_ITERATIONS = 5

# Default values
_DEFAULT_BUDGET = 5.0
_DEFAULT_ITERATIONS = 3


# ---------------------------------------------------------------------------
# Context management (mirrors delegation.py pattern)
# ---------------------------------------------------------------------------


def set_orchestration_context() -> None:
    """Allow orchestrate_task to be called (set at start of agent turn).

    Called alongside ``set_delegation_context`` when running a manager
    role that should have access to orchestration.
    """
    # Nothing to store beyond what delegation context provides;
    # this is a no-op placeholder for future context if needed.
    pass


def clear_orchestration_context() -> None:
    """Reset orchestration depth after an agent turn completes."""
    _orchestration_depth_var.set(0)


# ---------------------------------------------------------------------------
# Tool: orchestrate_task
# ---------------------------------------------------------------------------


@tool(
    name="orchestrate_task",
    description=(
        "Run a supervised multi-step investigation by dispatching a task to "
        "a role with iterative quality evaluation. Unlike delegate_to_role "
        "(which is single-shot), this tool evaluates the output against your "
        "success criteria and automatically refines the prompt or delegates "
        "to a different role if the result is insufficient. Use this for "
        "complex tasks where you need a thorough, high-quality answer — "
        "e.g. root cause investigations, deep competitive analysis, or "
        "multi-platform audits. Cost: $0.10-2.00 per call depending on "
        "iterations. Max 1 orchestration per turn."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "objective": {
                "type": "string",
                "description": (
                    "What you need accomplished. Be specific about what "
                    "data to analyze, what questions to answer, and what "
                    "the output should contain."
                ),
            },
            "role_id": {
                "type": "string",
                "description": (
                    "The role to dispatch to initially (e.g. "
                    "'performance_media_buyer', 'reporting_analyst'). "
                    "The orchestrator may delegate to a different role "
                    "if the initial role's output is insufficient."
                ),
            },
            "success_criteria": {
                "type": "string",
                "description": (
                    "What makes the output acceptable. Be specific: "
                    "'Must include ROAS analysis with dollar figures' "
                    "is better than 'good analysis'. The evaluator uses "
                    "this to decide whether to accept or refine."
                ),
            },
            "max_iterations": {
                "type": "integer",
                "description": (
                    "Maximum number of attempts before accepting the "
                    "best result. Default 3, max 5. Each iteration "
                    "costs ~$0.03-0.50 depending on the role's model."
                ),
            },
            "max_cost_usd": {
                "type": "number",
                "description": (
                    "Maximum budget for this orchestration in USD. "
                    "Default $5.00, max $10.00. Orchestration stops "
                    "if budget is exhausted."
                ),
            },
        },
        "required": ["objective", "role_id"],
    },
)
async def orchestrate_task(args: dict[str, Any]) -> dict[str, Any]:
    """Run a supervised orchestration loop and return the result."""
    objective = (args.get("objective") or "").strip()
    role_id = (args.get("role_id") or "").strip()
    success_criteria = (args.get("success_criteria") or "").strip()
    max_iterations = args.get("max_iterations", _DEFAULT_ITERATIONS)
    max_cost_usd = args.get("max_cost_usd", _DEFAULT_BUDGET)

    # --- Validation ---
    if not objective:
        return error_response("objective is required.")

    if not role_id:
        return error_response("role_id is required.")

    # --- Recursion guard ---
    depth = _orchestration_depth_var.get(0)
    if depth >= 1:
        return error_response(
            "Cannot nest orchestrations. The sub-agent you dispatched "
            "cannot itself orchestrate. Use delegate_to_role for simpler "
            "delegation within an orchestration."
        )

    # --- Cap parameters ---
    if isinstance(max_iterations, (int, float)):
        max_iterations = min(int(max_iterations), _MAX_ORCHESTRATION_ITERATIONS)
    else:
        max_iterations = _DEFAULT_ITERATIONS

    if isinstance(max_cost_usd, (int, float)):
        max_cost_usd = min(float(max_cost_usd), _MAX_ORCHESTRATION_BUDGET)
    else:
        max_cost_usd = _DEFAULT_BUDGET

    logger.info(
        "orchestrate_task.start",
        objective_preview=objective[:100],
        role_id=role_id,
        max_iterations=max_iterations,
        max_cost_usd=max_cost_usd,
    )

    # --- Increment depth (blocks recursion in sub-agents) ---
    _orchestration_depth_var.set(depth + 1)

    try:
        from src.claude_code.orchestrator import Orchestrator

        orchestrator = Orchestrator()
        result = await orchestrator.run(
            objective=objective,
            primary_role_id=role_id,
            success_criteria=success_criteria,
            max_iterations=max_iterations,
            max_cost_usd=max_cost_usd,
            user_id="__orchestration__",
        )

        logger.info(
            "orchestrate_task.complete",
            success=result.success,
            iterations=result.iterations,
            total_cost=result.total_cost_usd,
            duration_ms=result.total_duration_ms,
        )

        # Build a rich response for the calling agent
        status = "completed successfully" if result.success else "completed with limitations"
        if result.abort_reason:
            status = f"aborted: {result.abort_reason}"

        # Include step summaries so the caller understands what happened
        step_summaries = []
        for step in result.steps:
            eval_info = step.evaluation
            verdict = eval_info.get("verdict", "?")
            score = eval_info.get("score", 0)
            step_summaries.append(
                f"  Step {step.step_number} (role={step.role_id}): "
                f"verdict={verdict}, score={score:.1f}"
            )

        response_parts = [
            f"**Orchestration {status}**",
            f"Iterations: {result.iterations} | "
            f"Cost: ${result.total_cost_usd:.4f} | "
            f"Duration: {result.total_duration_ms}ms",
        ]

        if step_summaries:
            response_parts.append("Steps:\n" + "\n".join(step_summaries))

        response_parts.append(f"\n---\n\n{result.final_output}")

        return text_response("\n".join(response_parts))

    except Exception as exc:
        logger.exception(
            "orchestrate_task.error",
            role_id=role_id,
            error=str(exc),
        )
        return error_response(f"Orchestration failed: {exc}")

    finally:
        # Always restore depth
        _orchestration_depth_var.set(depth)
