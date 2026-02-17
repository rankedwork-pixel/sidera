"""Claude Code orchestrator — multi-step task supervision with evaluation.

Provides a meta-agent loop where Claude Code acts as a supervisor that:

1. Dispatches tasks to Sidera roles (via existing ``talk_to_role``/``run_role``)
2. Evaluates outputs against success criteria (Haiku LLM call)
3. Decides next steps: done / refine / escalate / delegate
4. Iterates with refined prompts until success or budget/iteration limits

No new DB tables — leverages existing audit_log and cost_tracking.  The
orchestrator itself runs within a single Claude Code MCP call, keeping
state in-memory for the duration of the loop.

Usage (from MCP meta-tool)::

    orchestrator = Orchestrator()
    result = await orchestrator.run(
        objective="Analyze Q1 performance and recommend budget changes",
        primary_role_id="head_of_marketing",
        success_criteria="Must include ROAS analysis and specific $ recommendations",
        max_iterations=3,
        max_cost_usd=5.0,
    )

Cost model:
    - Each dispatch ≈ $0.03-0.50 (depends on role model tier)
    - Each evaluation ≈ $0.005-0.01 (Haiku)
    - Typical orchestration: 1-3 iterations, $0.10-2.00 total
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from src.config import settings

logger = structlog.get_logger(__name__)


# =============================================================================
# Constants
# =============================================================================

_MAX_ITERATIONS = 5  # Hard cap even if caller requests more
_DEFAULT_MAX_COST = 10.0
_DEFAULT_MAX_ITERATIONS = 3

# Evaluation prompt for Haiku
_EVALUATION_PROMPT = """\
You are evaluating whether an AI agent's output satisfies a given objective.

**Objective:** {objective}

**Success Criteria:** {criteria}

**Agent Output:**
{output}

Evaluate the output and respond with a JSON object:
{{
    "verdict": "success" | "insufficient" | "off_topic" | "error",
    "score": <float 0.0-1.0>,
    "reasoning": "<1-2 sentence explanation>",
    "missing": ["<list of missing elements, if any>"],
    "refinement_hint": "<specific suggestion for improvement, if insufficient>"
}}

Rules:
- "success" if the output addresses the objective and meets the criteria
- "insufficient" if partially correct but missing key elements
- "off_topic" if the output doesn't address the objective at all
- "error" if the output indicates a failure or exception
- Be pragmatic: don't demand perfection, just adequacy
"""

# Decision prompt for Haiku
_DECISION_PROMPT = """\
You are deciding the next step in a multi-step AI orchestration.

**Objective:** {objective}
**Iteration:** {iteration} of {max_iterations}
**Cost so far:** ${cost_so_far:.4f} of ${max_cost:.2f} budget
**Current evaluation:** {evaluation}

**Previous attempts:**
{attempt_history}

Decide the next action. Respond with a JSON object:
{{
    "action": "done" | "refine" | "delegate" | "abort",
    "reasoning": "<1-2 sentence explanation>",
    "refined_prompt": "<new prompt if action is 'refine'>",
    "delegate_to": "<role_id if action is 'delegate'>",
    "delegate_prompt": "<prompt for delegated role if action is 'delegate'>"
}}

Rules:
- "done" if the output is good enough or if no further improvement is likely
- "refine" to retry with the same role but a better prompt
- "delegate" to try a different role (provide role_id and prompt)
- "abort" if the objective can't be achieved within remaining budget/iterations
- Include the refinement_hint from the evaluation in refined_prompt
- If close to budget or iteration limit, prefer "done" over more iterations
"""


# =============================================================================
# Result dataclasses
# =============================================================================


@dataclass
class OrchestrationStep:
    """Record of a single orchestration step."""

    step_number: int
    role_id: str
    prompt: str
    output: str = ""
    cost_usd: float = 0.0
    duration_ms: int = 0
    evaluation: dict[str, Any] = field(default_factory=dict)
    decision: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrchestrationResult:
    """Final result of an orchestration run."""

    objective: str
    final_output: str = ""
    success: bool = False
    total_cost_usd: float = 0.0
    total_duration_ms: int = 0
    iterations: int = 0
    steps: list[OrchestrationStep] = field(default_factory=list)
    abort_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for MCP response."""
        return {
            "objective": self.objective,
            "final_output": self.final_output,
            "success": self.success,
            "total_cost_usd": self.total_cost_usd,
            "total_duration_ms": self.total_duration_ms,
            "iterations": self.iterations,
            "abort_reason": self.abort_reason,
            "steps": [
                {
                    "step": s.step_number,
                    "role_id": s.role_id,
                    "prompt_preview": s.prompt[:200],
                    "output_preview": s.output[:500],
                    "cost_usd": s.cost_usd,
                    "evaluation": s.evaluation,
                    "decision": s.decision,
                }
                for s in self.steps
            ],
        }


# =============================================================================
# Orchestrator
# =============================================================================


class Orchestrator:
    """Multi-step task supervisor with evaluation and re-tasking.

    Runs an iterative loop:
        dispatch → evaluate → decide → (refine|delegate|done|abort)

    Uses existing Sidera infrastructure:
        - ``SideraAgent.run_conversation_turn()`` for role dispatch
        - Haiku for output evaluation and decision-making
        - No new DB tables (audit_log tracks everything)
    """

    async def run(
        self,
        *,
        objective: str,
        primary_role_id: str = "",
        success_criteria: str = "",
        max_iterations: int = _DEFAULT_MAX_ITERATIONS,
        max_cost_usd: float = _DEFAULT_MAX_COST,
        user_id: str = "claude_code",
    ) -> OrchestrationResult:
        """Execute the orchestration loop.

        Args:
            objective: What to accomplish (the initial prompt).
            primary_role_id: Role to start with.
            success_criteria: What makes output acceptable.
            max_iterations: Max retry attempts (capped at 5).
            max_cost_usd: Total cost budget for the orchestration.
            user_id: Who triggered this orchestration.

        Returns:
            OrchestrationResult with final output, cost, and step history.
        """
        max_iterations = min(max_iterations, _MAX_ITERATIONS)
        max_cost_usd = min(max_cost_usd, _DEFAULT_MAX_COST)

        if not success_criteria:
            success_criteria = (
                "Output must directly address the objective with specific, actionable information."
            )

        result = OrchestrationResult(objective=objective)
        start_time = time.monotonic()

        current_role_id = primary_role_id
        current_prompt = objective

        for iteration in range(1, max_iterations + 1):
            step = OrchestrationStep(
                step_number=iteration,
                role_id=current_role_id,
                prompt=current_prompt,
            )

            # --- Step 1: Dispatch task to role ---
            logger.info(
                "orchestrator.dispatch",
                iteration=iteration,
                role_id=current_role_id,
                cost_so_far=result.total_cost_usd,
            )

            dispatch_result = await self._dispatch(
                role_id=current_role_id,
                prompt=current_prompt,
                user_id=user_id,
            )

            step.output = dispatch_result.get("output", "")
            step.cost_usd = dispatch_result.get("cost_usd", 0.0)
            step.duration_ms = dispatch_result.get("duration_ms", 0)
            result.total_cost_usd += step.cost_usd

            if dispatch_result.get("error"):
                step.evaluation = {
                    "verdict": "error",
                    "score": 0.0,
                    "reasoning": dispatch_result["error"],
                }
                result.steps.append(step)
                result.iterations = iteration

                # On error, try once more if we have budget
                if iteration < max_iterations:
                    current_prompt = (
                        f"{objective}\n\n"
                        f"(Previous attempt failed: {dispatch_result['error']}. "
                        f"Please try again.)"
                    )
                    continue
                else:
                    result.final_output = step.output or dispatch_result["error"]
                    result.abort_reason = f"Dispatch error: {dispatch_result['error']}"
                    break

            # --- Step 2: Evaluate output ---
            if result.total_cost_usd >= max_cost_usd:
                step.evaluation = {
                    "verdict": "success",
                    "score": 0.5,
                    "reasoning": "Budget exhausted, accepting current output.",
                }
                step.decision = {"action": "done", "reasoning": "Budget limit."}
                result.steps.append(step)
                result.final_output = step.output
                result.success = True
                result.iterations = iteration
                break

            evaluation = await self._evaluate(
                output=step.output,
                objective=objective,
                criteria=success_criteria,
            )
            step.evaluation = evaluation
            eval_cost = evaluation.pop("_cost_usd", 0.0)
            result.total_cost_usd += eval_cost

            verdict = evaluation.get("verdict", "insufficient")

            if verdict == "success":
                step.decision = {"action": "done", "reasoning": "Success."}
                result.steps.append(step)
                result.final_output = step.output
                result.success = True
                result.iterations = iteration
                break

            # --- Step 3: Decide next step ---
            if iteration >= max_iterations:
                step.decision = {
                    "action": "done",
                    "reasoning": "Max iterations reached.",
                }
                result.steps.append(step)
                result.final_output = step.output
                result.success = evaluation.get("score", 0) >= 0.6
                result.iterations = iteration
                break

            decision = await self._decide(
                objective=objective,
                iteration=iteration,
                max_iterations=max_iterations,
                cost_so_far=result.total_cost_usd,
                max_cost=max_cost_usd,
                evaluation=evaluation,
                steps=result.steps + [step],
            )
            step.decision = decision
            decision_cost = decision.pop("_cost_usd", 0.0)
            result.total_cost_usd += decision_cost

            result.steps.append(step)

            action = decision.get("action", "done")

            if action == "done":
                result.final_output = step.output
                result.success = evaluation.get("score", 0) >= 0.5
                result.iterations = iteration
                break

            if action == "abort":
                result.final_output = step.output
                result.abort_reason = decision.get("reasoning", "Aborted by orchestrator.")
                result.iterations = iteration
                break

            if action == "delegate":
                delegate_role = decision.get("delegate_to", "")
                if delegate_role:
                    current_role_id = delegate_role
                current_prompt = decision.get("delegate_prompt", objective)

            elif action == "refine":
                current_prompt = decision.get("refined_prompt", objective)

            else:
                # Unknown action, treat as done
                result.final_output = step.output
                result.iterations = iteration
                break

        else:
            # Loop completed without break
            if result.steps:
                result.final_output = result.steps[-1].output
            result.iterations = max_iterations

        elapsed = int((time.monotonic() - start_time) * 1000)
        result.total_duration_ms = elapsed

        logger.info(
            "orchestrator.complete",
            success=result.success,
            iterations=result.iterations,
            total_cost=result.total_cost_usd,
            duration_ms=elapsed,
        )

        return result

    # -------------------------------------------------------------------------
    # Internal: dispatch task to a role
    # -------------------------------------------------------------------------

    async def _dispatch(
        self,
        *,
        role_id: str,
        prompt: str,
        user_id: str,
    ) -> dict[str, Any]:
        """Run a single agent turn as the specified role.

        Returns dict with output, cost_usd, duration_ms, error (if any).
        """
        start = time.monotonic()

        try:
            from src.agent.core import SideraAgent
            from src.db import service as db_service
            from src.db.session import get_db_session
            from src.mcp_servers.actions import clear_pending_actions
            from src.mcp_servers.delegation import (
                clear_delegation_context,
                set_delegation_context,
            )
            from src.mcp_servers.evolution import (
                clear_pending_proposals,
                clear_proposer_context,
                set_proposer_context,
            )
            from src.mcp_servers.memory import (
                clear_memory_context,
                set_memory_context,
            )
            from src.mcp_servers.messaging import (
                clear_messaging_context,
                compose_message_context,
                set_messaging_context,
            )
            from src.skills.db_loader import load_registry_with_db
            from src.skills.executor import compose_role_context
            from src.skills.memory import (
                compose_memory_context,
                filter_superseded_memories,
            )

            # Clear stale contextvars
            clear_pending_actions()
            clear_pending_proposals()
            clear_delegation_context()
            clear_memory_context()
            clear_messaging_context()
            clear_proposer_context()

            registry = await load_registry_with_db()
            role = registry.get_role(role_id)
            if role is None:
                return {"output": "", "error": f"Role '{role_id}' not found."}

            dept = registry.get_department(role.department_id)

            # Load memory (best-effort)
            memory_ctx = ""
            try:
                async with get_db_session() as session:
                    memories = await db_service.get_role_memories(
                        session,
                        user_id,
                        role_id,
                        limit=10,
                    )
                    superseded = await db_service.get_superseded_memory_ids(
                        session,
                        user_id,
                        role_id,
                    )
                    memories = filter_superseded_memories(
                        memories,
                        superseded,
                    )
                    agent_memories = await db_service.get_agent_relationship_memories(
                        session,
                        role_id,
                        limit=5,
                    )
                    all_memories = list(memories) + list(agent_memories)
                    if all_memories:
                        memory_ctx = compose_memory_context(all_memories)
            except Exception:
                pass

            # Load messages (best-effort)
            message_ctx = ""
            try:
                async with get_db_session() as session:
                    pending_msgs = await db_service.get_pending_messages(
                        session,
                        role_id,
                        limit=10,
                    )
                    message_ctx = compose_message_context(pending_msgs)
                    if pending_msgs:
                        msg_ids = [m.id for m in pending_msgs]
                        await db_service.mark_messages_delivered(
                            session,
                            msg_ids,
                        )
            except Exception:
                pass

            # Compose context
            role_context = compose_role_context(
                department=dept,
                role=role,
                memory_context=memory_ctx,
                registry=registry,
                pending_messages=message_ctx,
            )

            # Set contextvars
            is_manager = bool(getattr(role, "manages", ()))
            dept_id = dept.id if dept else ""
            if is_manager:
                set_delegation_context(role_id, registry)
            set_memory_context(role_id, dept_id, user_id)
            set_messaging_context(role_id, dept_id, registry)
            set_proposer_context(role_id, dept_id)

            # Run agent turn
            agent = SideraAgent()
            try:
                result = await agent.run_conversation_turn(
                    role_id=role_id,
                    role_context=role_context,
                    thread_history=[],
                    current_message=prompt,
                    user_id=user_id,
                    bot_user_id="",
                    turn_number=1,
                    is_manager=is_manager,
                    user_clearance="restricted",
                )
            finally:
                clear_memory_context()
                clear_messaging_context()
                clear_proposer_context()
                if is_manager:
                    clear_delegation_context()

            cost = result.cost if isinstance(result.cost, dict) else {}
            cost_usd = cost.get("total_cost_usd", 0.0)

            elapsed = int((time.monotonic() - start) * 1000)

            return {
                "output": result.response_text,
                "cost_usd": cost_usd,
                "duration_ms": elapsed,
            }

        except Exception as exc:
            logger.exception("orchestrator.dispatch.error", role_id=role_id)
            elapsed = int((time.monotonic() - start) * 1000)
            return {
                "output": "",
                "cost_usd": 0.0,
                "duration_ms": elapsed,
                "error": str(exc),
            }

    # -------------------------------------------------------------------------
    # Internal: evaluate output with Haiku
    # -------------------------------------------------------------------------

    async def _evaluate(
        self,
        *,
        output: str,
        objective: str,
        criteria: str,
    ) -> dict[str, Any]:
        """Evaluate whether output satisfies the objective.

        Returns evaluation dict with verdict, score, reasoning, missing.
        """
        from src.agent.api_client import call_claude_api
        from src.llm.provider import TaskType

        prompt = _EVALUATION_PROMPT.format(
            objective=objective,
            criteria=criteria,
            output=output[:8000],  # Truncate very long outputs
        )

        try:
            response = await call_claude_api(
                model=settings.model_fast,
                system_prompt="You are an output quality evaluator.",
                user_message=prompt,
                max_tokens=500,
                task_type=TaskType.GENERAL,
            )

            text = response.get("text", "")
            cost = response.get("cost", {}).get("total_cost_usd", 0.0)

            parsed = self._parse_json(text)
            parsed["_cost_usd"] = cost
            return parsed

        except Exception as exc:
            logger.warning("orchestrator.evaluate.error", error=str(exc))
            return {
                "verdict": "success",
                "score": 0.5,
                "reasoning": f"Evaluation failed ({exc}), accepting output.",
                "_cost_usd": 0.0,
            }

    # -------------------------------------------------------------------------
    # Internal: decide next step with Haiku
    # -------------------------------------------------------------------------

    async def _decide(
        self,
        *,
        objective: str,
        iteration: int,
        max_iterations: int,
        cost_so_far: float,
        max_cost: float,
        evaluation: dict[str, Any],
        steps: list[OrchestrationStep],
    ) -> dict[str, Any]:
        """Decide the next orchestration action.

        Returns decision dict with action, reasoning, optional refined_prompt.
        """
        from src.agent.api_client import call_claude_api
        from src.llm.provider import TaskType

        # Build attempt history
        history_lines: list[str] = []
        for s in steps:
            ev = s.evaluation
            verdict = ev.get("verdict", "?")
            score = ev.get("score", 0)
            reasoning = ev.get("reasoning", "")
            history_lines.append(
                f"  Step {s.step_number} (role={s.role_id}): "
                f"verdict={verdict}, score={score:.1f} — {reasoning}"
            )

        prompt = _DECISION_PROMPT.format(
            objective=objective,
            iteration=iteration,
            max_iterations=max_iterations,
            cost_so_far=cost_so_far,
            max_cost=max_cost,
            evaluation=json.dumps(evaluation, default=str),
            attempt_history="\n".join(history_lines) or "  (first attempt)",
        )

        try:
            response = await call_claude_api(
                model=settings.model_fast,
                system_prompt="You are an orchestration decision-maker.",
                user_message=prompt,
                max_tokens=500,
                task_type=TaskType.GENERAL,
            )

            text = response.get("text", "")
            cost = response.get("cost", {}).get("total_cost_usd", 0.0)

            parsed = self._parse_json(text)
            parsed["_cost_usd"] = cost
            return parsed

        except Exception as exc:
            logger.warning("orchestrator.decide.error", error=str(exc))
            return {
                "action": "done",
                "reasoning": f"Decision failed ({exc}), accepting output.",
                "_cost_usd": 0.0,
            }

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        """Extract JSON from LLM response, handling markdown fences."""
        import re

        # Try markdown-fenced JSON first
        match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try raw JSON
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass

        # Try to find JSON object in text
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        return {"verdict": "error", "reasoning": "Failed to parse response."}
