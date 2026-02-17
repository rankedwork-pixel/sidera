"""Manager executor for Sidera.

Orchestrates a manager role through a four-phase execution pipeline:

1. **Own skills** -- Run the manager's own ``briefing_skills`` via
   ``RoleExecutor`` to build initial context.
2. **Delegation decision** -- Call the agent to decide which sub-roles
   should be activated based on the manager's own analysis.
3. **Sub-role execution** -- Run each activated sub-role via
   ``RoleExecutor``, capturing results and continuing on failure.
4. **Synthesis** -- Call the agent to produce a unified output that
   merges the manager's own analysis with all sub-role reports.

Memory is integrated at the boundaries: loaded before execution and
extracted/saved after synthesis.

Usage::

    from src.skills.executor import SkillExecutor, RoleExecutor
    from src.skills.manager import ManagerExecutor
    from src.skills.registry import SkillRegistry

    registry = SkillRegistry()
    registry.load_all()

    agent = SideraAgent()
    skill_executor = SkillExecutor(agent=agent, registry=registry)
    role_executor = RoleExecutor(skill_executor=skill_executor, registry=registry)

    manager_executor = ManagerExecutor(
        skill_executor=skill_executor,
        role_executor=role_executor,
        registry=registry,
    )

    result = await manager_executor.execute_manager(
        role_id="marketing_director",
        user_id="user_123",
        accounts=[{"platform": "meta", "account_id": "act_456"}],
    )
    print(result.synthesis)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import structlog

from src.skills.executor import RoleExecutor, RoleResult, SkillExecutor, SkillResult
from src.skills.memory import compose_memory_context, extract_memories_from_results
from src.skills.registry import SkillRegistry

logger = structlog.get_logger("sidera.manager")


# =============================================================================
# Result dataclass
# =============================================================================


@dataclass
class ManagerResult:
    """Return value from a manager execution.

    Attributes:
        role_id: The manager role that was executed.
        own_skill_results: SkillResult list from running the manager's
            own briefing_skills (Phase 1). Empty if the manager has no
            briefing_skills.
        delegation_decision: Which sub-roles were activated or skipped,
            with reasoning. Contains ``activate`` and ``skip`` keys.
        sub_role_results: Mapping of role_id to RoleResult for each
            activated sub-role (Phase 3).
        synthesis: Unified output text produced by the synthesis phase
            (Phase 4).
        total_cost: Aggregated cost from all phases.
        skipped_roles: List of role IDs that were skipped by the
            delegation decision.
    """

    role_id: str
    own_skill_results: list[SkillResult] = field(default_factory=list)
    delegation_decision: dict[str, Any] = field(default_factory=dict)
    sub_role_results: dict[str, RoleResult] = field(default_factory=dict)
    synthesis: str = ""
    total_cost: dict[str, Any] = field(default_factory=dict)
    skipped_roles: list[str] = field(default_factory=list)


# =============================================================================
# Exceptions
# =============================================================================


class ManagerRoleNotFoundError(Exception):
    """Raised when a manager role ID is not found in the registry."""

    pass


class NotAManagerError(Exception):
    """Raised when a role is not a manager (has no manages field)."""

    pass


# =============================================================================
# Manager Executor
# =============================================================================


class ManagerExecutor:
    """Executes a manager role through a four-phase pipeline.

    Phase 1: Run the manager's own briefing_skills (reuses RoleExecutor).
    Phase 2: Call the agent for a delegation decision (which sub-roles
        to activate).
    Phase 3: Run each activated sub-role sequentially (reuses RoleExecutor).
    Phase 4: Call the agent to synthesize all results into a unified output.

    Memory is loaded at the start and saved after synthesis.

    Args:
        skill_executor: The ``SkillExecutor`` for individual skill runs.
        role_executor: The ``RoleExecutor`` for running roles.
        registry: The ``SkillRegistry`` for looking up roles and
            managed role relationships.
    """

    def __init__(
        self,
        skill_executor: SkillExecutor,
        role_executor: RoleExecutor,
        registry: SkillRegistry,
    ) -> None:
        self._skill_executor = skill_executor
        self._role_executor = role_executor
        self._registry = registry
        self._log = logger.bind(component="manager_executor")

    async def execute_manager(
        self,
        role_id: str,
        user_id: str,
        accounts: list[dict[str, Any]],
        *,
        analysis_date: date | None = None,
        memory_context: str = "",
    ) -> ManagerResult:
        """Execute a manager role through all four phases.

        Args:
            role_id: The manager role to execute.
            user_id: Identifier for the advertiser / user.
            accounts: List of account context dicts.
            analysis_date: Reference date for the analysis. Defaults to today.
            memory_context: Pre-composed memory string. If empty, memory
                is loaded from the memory system automatically.

        Returns:
            ``ManagerResult`` with own skill results, delegation decision,
            sub-role results, synthesis text, and aggregated cost.

        Raises:
            ManagerRoleNotFoundError: If the role is not in the registry.
            NotAManagerError: If the role exists but has no ``manages``.
        """
        if analysis_date is None:
            analysis_date = date.today()

        role = self._registry.get_role(role_id)
        if role is None:
            raise ManagerRoleNotFoundError(f"Manager role '{role_id}' not found in registry")

        if not role.manages:
            raise NotAManagerError(f"Role '{role_id}' is not a manager (manages is empty)")

        self._log.info(
            "manager_executor.start",
            role_id=role_id,
            user_id=user_id,
            num_managed=len(role.manages),
            has_own_skills=bool(role.briefing_skills),
        )

        total_cost: dict[str, Any] = {
            "total_cost_usd": 0.0,
            "num_turns": 0,
            "duration_ms": 0,
            "phase_costs": {},
        }

        # -- Load memory --
        if not memory_context:
            memory_context = self._load_memory(role_id)

        # ============================================================
        # Phase 1: Own skills
        # ============================================================
        own_role_result: RoleResult | None = None
        own_skill_results: list[SkillResult] = []

        if role.briefing_skills:
            self._log.info(
                "manager_executor.phase1.start",
                role_id=role_id,
                num_skills=len(role.briefing_skills),
            )

            own_role_result = await self._role_executor.execute_role(
                role_id=role_id,
                user_id=user_id,
                accounts=accounts,
                analysis_date=analysis_date,
                memory_context=memory_context,
            )
            own_skill_results = own_role_result.skill_results
            _merge_phase_cost(total_cost, own_role_result.total_cost, "own_skills")

            self._log.info(
                "manager_executor.phase1.complete",
                role_id=role_id,
                skills_run=len(own_skill_results),
            )
        else:
            self._log.info(
                "manager_executor.phase1.skipped",
                role_id=role_id,
                reason="no_briefing_skills",
            )

        # ============================================================
        # Phase 2: Delegation decision
        # ============================================================
        managed_roles = self._registry.get_managed_roles(role_id)

        if not managed_roles:
            self._log.warning(
                "manager_executor.phase2.no_managed_roles",
                role_id=role_id,
            )
            delegation_decision: dict[str, Any] = {"activate": [], "skip": []}
            skipped_roles: list[str] = []
        else:
            self._log.info(
                "manager_executor.phase2.start",
                role_id=role_id,
                available_roles=[r.id for r in managed_roles],
            )

            # Build context from Phase 1
            manager_context = _format_own_results_summary(
                role_id,
                own_skill_results,
            )

            # Build available roles info
            available_roles_info = [
                {
                    "role_id": r.id,
                    "name": r.name,
                    "description": r.description,
                    "briefing_skills": list(r.briefing_skills),
                }
                for r in managed_roles
            ]

            # Resolve delegation model
            delegation_model = _resolve_delegation_model(role.delegation_model)

            # Call agent for delegation decision
            delegation_decision = await self._run_delegation(
                role=role,
                manager_context=manager_context,
                available_roles_info=available_roles_info,
                model=delegation_model,
            )

            # Validate and extract activated/skipped role IDs
            valid_role_ids = {r.id for r in managed_roles}
            activated_ids, skipped_roles = _parse_delegation_decision(
                delegation_decision,
                valid_role_ids,
            )

            self._log.info(
                "manager_executor.phase2.complete",
                activated=activated_ids,
                skipped=skipped_roles,
            )

        # ============================================================
        # Phase 3: Sub-role execution
        # ============================================================
        sub_role_results: dict[str, RoleResult] = {}

        if managed_roles and activated_ids:
            self._log.info(
                "manager_executor.phase3.start",
                num_activated=len(activated_ids),
            )

            for sub_role_id in activated_ids:
                try:
                    sub_result = await self._role_executor.execute_role(
                        role_id=sub_role_id,
                        user_id=user_id,
                        accounts=accounts,
                        analysis_date=analysis_date,
                        memory_context=memory_context,
                    )
                    sub_role_results[sub_role_id] = sub_result
                    _merge_phase_cost(
                        total_cost,
                        sub_result.total_cost,
                        f"sub_role_{sub_role_id}",
                    )
                except Exception:
                    self._log.exception(
                        "manager_executor.phase3.sub_role_failed",
                        sub_role_id=sub_role_id,
                    )
                    # Continue with remaining sub-roles

            self._log.info(
                "manager_executor.phase3.complete",
                successful=list(sub_role_results.keys()),
                failed=[rid for rid in activated_ids if rid not in sub_role_results],
            )

        # ============================================================
        # Phase 4: Synthesis
        # ============================================================
        self._log.info("manager_executor.phase4.start", role_id=role_id)

        own_results_text = own_role_result.combined_output if own_role_result else ""
        sub_results_text = _format_sub_role_results(sub_role_results)

        synthesis = await self._run_synthesis(
            role=role,
            own_results=own_results_text,
            sub_results_text=sub_results_text,
        )

        self._log.info(
            "manager_executor.phase4.complete",
            synthesis_length=len(synthesis),
        )

        # -- Save memory --
        self._save_memory(
            role_id=role_id,
            department_id=role.department_id,
            own_skill_results=own_skill_results,
            sub_role_results=sub_role_results,
            analysis_date=analysis_date,
        )

        self._log.info(
            "manager_executor.complete",
            role_id=role_id,
            total_cost=total_cost.get("total_cost_usd", 0.0),
        )

        return ManagerResult(
            role_id=role_id,
            own_skill_results=own_skill_results,
            delegation_decision=delegation_decision,
            sub_role_results=sub_role_results,
            synthesis=synthesis,
            total_cost=total_cost,
            skipped_roles=skipped_roles,
        )

    # ------------------------------------------------------------------
    # Phase helpers
    # ------------------------------------------------------------------

    async def _run_delegation(
        self,
        role: Any,
        manager_context: str,
        available_roles_info: list[dict[str, Any]],
        model: str,
    ) -> dict[str, Any]:
        """Run the delegation decision via the agent.

        Falls back to activating all roles if the agent call fails.
        """
        try:
            agent = self._skill_executor._agent
            decision = await agent.run_delegation_decision(
                manager_name=role.name,
                manager_persona=role.persona or role.description,
                own_results_summary=manager_context,
                available_roles=available_roles_info,
                model=model,
            )
            return decision
        except Exception:
            self._log.exception(
                "manager_executor.delegation_failed",
                role_id=role.id,
            )
            # Fallback: activate all
            return {
                "activate": [
                    {"role_id": r["role_id"], "reason": "fallback"} for r in available_roles_info
                ],
                "skip": [],
            }

    async def _run_synthesis(
        self,
        role: Any,
        own_results: str,
        sub_results_text: str,
    ) -> str:
        """Run the synthesis via the agent."""
        try:
            agent = self._skill_executor._agent
            return await agent.run_synthesis(
                manager_name=role.name,
                manager_persona=role.persona or role.description,
                own_results=own_results,
                sub_role_results=sub_results_text,
                synthesis_prompt=role.synthesis_prompt,
            )
        except Exception:
            self._log.exception(
                "manager_executor.synthesis_failed",
                role_id=role.id,
            )
            raise

    # ------------------------------------------------------------------
    # Memory helpers
    # ------------------------------------------------------------------

    def _load_memory(self, role_id: str) -> str:
        """Load memory context for the manager role.

        Returns composed memory string, or empty string if no memories.
        """
        try:
            # Memory entries would typically come from the database.
            # For now, compose_memory_context accepts a list of memory
            # objects. Without a DB session, we return empty.
            return compose_memory_context([], token_budget=2000)
        except Exception:
            self._log.debug(
                "manager_executor.memory_load_failed",
                role_id=role_id,
            )
            return ""

    def _save_memory(
        self,
        role_id: str,
        department_id: str,
        own_skill_results: list[SkillResult],
        sub_role_results: dict[str, RoleResult],
        analysis_date: date | None = None,
    ) -> None:
        """Extract and save memory from the completed manager run.

        Collects skill results from both the manager's own skills and
        all sub-role skills, then extracts memories using the standard
        memory extraction pipeline.
        """
        try:
            all_skill_results: list[SkillResult] = list(own_skill_results)
            for role_result in sub_role_results.values():
                all_skill_results.extend(role_result.skill_results)

            memories = extract_memories_from_results(
                role_id=role_id,
                department_id=department_id,
                skill_results=all_skill_results,
                run_date=analysis_date,
            )

            if memories:
                self._log.info(
                    "manager_executor.memory_extracted",
                    role_id=role_id,
                    memory_count=len(memories),
                )
            # Saving to DB would happen here with a DB session.
            # The extracted memories are ready for db_service.save_memory().
        except Exception:
            self._log.debug(
                "manager_executor.memory_save_failed",
                role_id=role_id,
            )


# =============================================================================
# Helpers
# =============================================================================


def _format_own_results_summary(
    role_id: str,
    skill_results: list[SkillResult],
) -> str:
    """Format the manager's own skill results into a summary for delegation.

    Args:
        role_id: The manager role ID.
        skill_results: Results from the manager's own briefing_skills.

    Returns:
        Formatted summary string.
    """
    if not skill_results:
        return f"Manager '{role_id}' has no own analysis results."

    sections: list[str] = [f"Manager '{role_id}' analysis summary:"]
    for result in skill_results:
        # Truncate to first 500 chars for a concise summary
        preview = result.output_text[:500]
        if len(result.output_text) > 500:
            preview += "..."
        sections.append(f"\n### {result.skill_id}\n{preview}")

    return "\n".join(sections)


def _format_sub_role_results(
    sub_role_results: dict[str, RoleResult],
) -> str:
    """Format sub-role results for the synthesis prompt.

    Args:
        sub_role_results: Mapping of role_id to RoleResult.

    Returns:
        Formatted text of all sub-role outputs.
    """
    if not sub_role_results:
        return "No sub-role reports available."

    sections: list[str] = []
    for role_id, role_result in sub_role_results.items():
        sections.append(f"\n### {role_id} Report\n{role_result.combined_output}")

    return "\n".join(sections)


def _parse_delegation_decision(
    decision: dict[str, Any],
    valid_role_ids: set[str],
) -> tuple[list[str], list[str]]:
    """Parse and validate the delegation decision.

    Extracts activated and skipped role IDs from the decision dict,
    validating each against the set of valid managed role IDs. Unknown
    IDs are silently dropped.

    Args:
        decision: The raw delegation decision dict with ``activate``
            and ``skip`` keys.
        valid_role_ids: Set of role IDs that are actually managed.

    Returns:
        Tuple of (activated_ids, skipped_ids).
    """
    activated: list[str] = []
    skipped: list[str] = []

    for entry in decision.get("activate", []):
        rid = entry.get("role_id", "") if isinstance(entry, dict) else str(entry)
        if rid in valid_role_ids:
            activated.append(rid)

    for entry in decision.get("skip", []):
        rid = entry.get("role_id", "") if isinstance(entry, dict) else str(entry)
        if rid in valid_role_ids:
            skipped.append(rid)

    return activated, skipped


def _resolve_delegation_model(delegation_model: str) -> str:
    """Resolve the delegation model name to a settings model ID.

    Args:
        delegation_model: Either ``"standard"`` (Sonnet) or ``"fast"`` (Haiku).

    Returns:
        The resolved model ID string from settings.
    """
    from src.config import settings

    if delegation_model == "fast":
        return settings.model_fast
    return settings.model_standard


def _merge_phase_cost(
    total: dict[str, Any],
    phase_cost: dict[str, Any],
    phase_name: str,
) -> None:
    """Merge a phase's cost into the running total.

    Args:
        total: The running total cost dict.
        phase_cost: Cost from a single phase.
        phase_name: Label for this phase (stored in ``phase_costs``).
    """
    total["total_cost_usd"] += phase_cost.get("total_cost_usd", 0.0)
    total["num_turns"] += phase_cost.get("num_turns", 0)
    total["duration_ms"] += phase_cost.get("duration_ms", 0)
    total["phase_costs"][phase_name] = phase_cost
