"""Skill executor for Sidera.

Bridges the skill registry and the agent: looks up a skill by ID, delegates
execution to ``SideraAgent.run_skill()``, and converts the raw
``BriefingResult`` into a ``SkillResult`` that includes chain metadata.

Usage::

    from src.agent.core import SideraAgent
    from src.skills.executor import SkillExecutor
    from src.skills.registry import SkillRegistry

    registry = SkillRegistry()
    registry.load_all()

    agent = SideraAgent()
    executor = SkillExecutor(agent=agent, registry=registry)

    result = await executor.execute(
        skill_id="creative_analysis",
        user_id="user_123",
        accounts=[{"platform": "meta", "account_id": "act_456"}],
    )
    print(result.output_text)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

import structlog

from src.agent.core import BriefingResult, SideraAgent
from src.skills.registry import SkillRegistry

# ManagerExecutor is created by another agent — import with fallback
try:
    from src.skills.manager import ManagerExecutor as _ManagerExecutor
except ImportError:  # pragma: no cover
    _ManagerExecutor = None  # type: ignore[assignment,misc]

if TYPE_CHECKING:
    from src.skills.schema import DepartmentDefinition, RoleDefinition

logger = structlog.get_logger(__name__)


# =============================================================================
# Result dataclass
# =============================================================================


@dataclass
class SkillResult:
    """Return value from a skill execution.

    Attributes:
        skill_id: The ID of the skill that was executed.
        user_id: Identifier for the advertiser / user.
        output_text: The full text output produced by the agent.
        recommendations: Structured recommendations extracted from the output.
        cost: Cost and usage metadata from the agent run.
        session_id: The Claude session ID for the conversation.
        chain_next: If this skill has a ``chain_after`` configured, this holds
            the ID of the next skill that should be executed. ``None`` if no
            chaining is configured.
    """

    skill_id: str
    user_id: str
    output_text: str
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    cost: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    chain_next: str | None = None


# =============================================================================
# Exceptions
# =============================================================================


class SkillNotFoundError(Exception):
    """Raised when a skill ID is not found in the registry."""

    pass


# =============================================================================
# Executor
# =============================================================================


class SkillExecutor:
    """Executes skills by delegating to a ``SideraAgent`` instance.

    Looks up the skill definition from the registry, calls
    ``agent.run_skill()``, and wraps the result in a ``SkillResult``
    with chain metadata.

    Args:
        agent: The ``SideraAgent`` instance to delegate execution to.
        registry: The ``SkillRegistry`` containing loaded skill definitions.
    """

    def __init__(self, agent: SideraAgent, registry: SkillRegistry) -> None:
        self._agent = agent
        self._registry = registry
        self._log = logger.bind(component="skill_executor")

    async def execute(
        self,
        skill_id: str,
        user_id: str,
        accounts: list[dict[str, Any]],
        params: dict[str, Any] | None = None,
        analysis_date: date | None = None,
        role_context: str = "",
        user_clearance: str = "",
    ) -> SkillResult:
        """Execute a skill by ID.

        1. Looks up the skill from the registry.
        2. Checks clearance gating (if user_clearance provided).
        3. Delegates to ``agent.run_skill()`` with the skill definition.
        4. Converts the ``BriefingResult`` into a ``SkillResult``.
        5. Sets ``chain_next`` from the skill's ``chain_after`` field.

        Args:
            skill_id: The unique skill identifier (e.g., ``"creative_analysis"``).
            user_id: Identifier for the advertiser / user.
            accounts: List of account context dicts.
            params: Optional parameters to inject into the skill's prompt template.
            analysis_date: Reference date for the analysis. Defaults to today.
            role_context: Pre-composed context from department + role
                definitions. Passed through to ``agent.run_skill()``.
            user_clearance: The requesting user's (or agent's) clearance level.
                If provided and insufficient, the skill returns an error result.

        Returns:
            ``SkillResult`` with the output text, recommendations, cost,
            session ID, and optional chain_next.

        Raises:
            SkillNotFoundError: If ``skill_id`` is not found in the registry.
        """
        skill = self._registry.get(skill_id)
        if skill is None:
            raise SkillNotFoundError(
                f"Skill '{skill_id}' not found in registry ({self._registry.count} skills loaded)"
            )

        # Clearance gating: check if user has sufficient clearance for this skill
        if user_clearance and skill.min_clearance != "public":
            from src.middleware.rbac import has_clearance

            if not has_clearance(user_clearance, skill.min_clearance):
                self._log.warning(
                    "executor.clearance_denied",
                    skill_id=skill_id,
                    user_clearance=user_clearance,
                    required=skill.min_clearance,
                )
                return SkillResult(
                    skill_id=skill_id,
                    user_id=user_id,
                    output_text=(
                        f"[CLEARANCE DENIED] This skill requires {skill.min_clearance} clearance. "
                        f"Your current clearance level ({user_clearance}) is insufficient."
                    ),
                    recommendations=[],
                    cost={},
                    session_id="",
                )

        self._log.info(
            "executor.start",
            skill_id=skill_id,
            skill_name=skill.name,
            user_id=user_id,
            num_accounts=len(accounts),
            skill_type=skill.skill_type,
        )

        # Code-backed skills: route to ClaudeCodeExecutor for full agent instance
        if skill.skill_type == "code_backed":
            return await self._execute_code_backed(
                skill=skill,
                user_id=user_id,
                role_context=role_context,
                params=params,
            )

        # Standard LLM skills: delegate to the agent
        briefing_result: BriefingResult = await self._agent.run_skill(
            skill=skill,
            user_id=user_id,
            account_ids=accounts,
            params=params,
            analysis_date=analysis_date,
            role_context=role_context,
        )

        # Convert to SkillResult
        result = _briefing_to_skill_result(
            briefing_result=briefing_result,
            skill_id=skill_id,
            chain_after=skill.chain_after,
        )

        self._log.info(
            "executor.complete",
            skill_id=skill_id,
            user_id=user_id,
            output_length=len(result.output_text),
            chain_next=result.chain_next,
            session_id=result.session_id,
        )

        return result

    async def _execute_code_backed(
        self,
        skill: Any,
        user_id: str,
        role_context: str = "",
        params: dict[str, Any] | None = None,
    ) -> SkillResult:
        """Execute a code-backed skill via ClaudeCodeExecutor.

        Spins up a full Claude Code agent instance with access to all
        connectors (Google Drive, Slack, BigQuery, etc.) plus the
        ``run_skill_code`` tool.  The agent runs the Python code,
        interprets the output, and can push results to any connector.
        """
        from src.claude_code.executor import ClaudeCodeExecutor

        self._log.info(
            "executor.code_backed_start",
            skill_id=skill.id,
            entrypoint=skill.code_entrypoint,
        )

        executor = ClaudeCodeExecutor()
        cc_result = await executor.execute(
            skill=skill,
            prompt=skill.prompt_template.format(**params) if params else skill.prompt_template,
            user_id=user_id,
            role_context=role_context,
            max_budget_usd=5.0,
            include_sidera_tools=True,
        )

        self._log.info(
            "executor.code_backed_complete",
            skill_id=skill.id,
            cost_usd=cc_result.cost_usd,
            num_turns=cc_result.num_turns,
            is_error=cc_result.is_error,
        )

        return SkillResult(
            skill_id=skill.id,
            user_id=user_id,
            output_text=cc_result.output_text,
            recommendations=[],
            cost={
                "total_cost_usd": cc_result.cost_usd,
                "num_turns": cc_result.num_turns,
                "duration_ms": cc_result.duration_ms,
            },
            session_id=cc_result.session_id,
            chain_next=skill.chain_after,
        )


# =============================================================================
# Helpers
# =============================================================================


def _briefing_to_skill_result(
    briefing_result: BriefingResult,
    skill_id: str,
    chain_after: str | None,
) -> SkillResult:
    """Convert a ``BriefingResult`` to a ``SkillResult``.

    Args:
        briefing_result: The raw result from ``SideraAgent.run_skill()``.
        skill_id: The skill ID that produced this result.
        chain_after: The ``chain_after`` value from the skill definition.

    Returns:
        A ``SkillResult`` wrapping the briefing data with chain metadata.
    """
    return SkillResult(
        skill_id=skill_id,
        user_id=briefing_result.user_id,
        output_text=briefing_result.briefing_text,
        recommendations=briefing_result.recommendations,
        cost=briefing_result.cost,
        session_id=briefing_result.session_id or str(uuid.uuid4()),
        chain_next=chain_after,
    )


# =============================================================================
# Role result dataclass
# =============================================================================


@dataclass
class RoleResult:
    """Return value from a role execution (all briefing_skills run).

    Attributes:
        role_id: The role that was executed.
        department_id: The department the role belongs to.
        user_id: Identifier for the advertiser / user.
        skill_results: Individual results from each skill.
        combined_output: Merged output with section headers.
        total_cost: Aggregated cost from all skill runs.
        session_id: Session ID (from the first skill run).
    """

    role_id: str
    department_id: str
    user_id: str
    skill_results: list[SkillResult] = field(default_factory=list)
    combined_output: str = ""
    total_cost: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""


# =============================================================================
# Department result dataclass
# =============================================================================


@dataclass
class DepartmentResult:
    """Return value from a department execution (all roles run).

    Attributes:
        department_id: The department that was executed.
        user_id: Identifier for the advertiser / user.
        role_results: Individual results from each role.
        combined_output: Merged output with department-level headers.
        total_cost: Aggregated cost from all role runs.
    """

    department_id: str
    user_id: str
    role_results: list[RoleResult] = field(default_factory=list)
    combined_output: str = ""
    total_cost: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Role Executor
# =============================================================================


class RoleExecutor:
    """Executes all briefing_skills for a role sequentially.

    Composes role context (department + role persona) and passes it
    through to each skill execution. Merges individual skill outputs
    into a unified briefing with section headers.

    Args:
        skill_executor: The ``SkillExecutor`` to delegate skill runs to.
        registry: The ``SkillRegistry`` for looking up roles/departments.
    """

    def __init__(
        self,
        skill_executor: SkillExecutor,
        registry: SkillRegistry,
    ) -> None:
        self._skill_executor = skill_executor
        self._registry = registry
        self._log = logger.bind(component="role_executor")

    async def execute_role(
        self,
        role_id: str,
        user_id: str,
        accounts: list[dict[str, Any]],
        analysis_date: date | None = None,
        memory_context: str = "",
        pending_messages: str = "",
    ) -> RoleResult:
        """Run all briefing_skills for a role sequentially.

        Args:
            role_id: The role to execute.
            user_id: Identifier for the advertiser / user.
            accounts: List of account context dicts.
            analysis_date: Reference date for the analysis.
            memory_context: Pre-composed memory string to inject into
                role context (default empty — backward compatible).

        Returns:
            ``RoleResult`` with merged output from all skills.

        Raises:
            RoleNotFoundError: If the role is not in the registry.
        """
        role = self._registry.get_role(role_id)
        if role is None:
            raise RoleNotFoundError(f"Role '{role_id}' not found in registry")

        department = self._registry.get_department(role.department_id)

        self._log.info(
            "role_executor.start",
            role_id=role_id,
            department_id=role.department_id,
            user_id=user_id,
            num_skills=len(role.briefing_skills),
        )

        # Build role context from department + role + memory + messages
        role_context = compose_role_context(
            department,
            role,
            memory_context=memory_context,
            registry=self._registry,
            pending_messages=pending_messages,
        )

        skill_results: list[SkillResult] = []
        total_cost: dict[str, Any] = {
            "total_cost_usd": 0.0,
            "num_turns": 0,
            "duration_ms": 0,
            "skill_costs": {},
        }

        previous_output = ""  # Pipeline: output from previous skill

        for skill_id in role.briefing_skills:
            skill = self._registry.get(skill_id)
            if skill is None:
                self._log.warning(
                    "role_executor.skill_not_found",
                    role_id=role_id,
                    skill_id=skill_id,
                )
                continue

            # Build params with previous skill output for pipeline
            skill_params: dict[str, Any] | None = None
            if previous_output:
                skill_params = {"previous_output": previous_output}

            try:
                result = await self._skill_executor.execute(
                    skill_id=skill_id,
                    user_id=user_id,
                    accounts=accounts,
                    params=skill_params,
                    analysis_date=analysis_date,
                    role_context=role_context,
                )
                skill_results.append(result)
                _merge_cost(total_cost, result.cost, skill_id)
                # Update for next skill in the pipeline
                previous_output = result.output_text
            except Exception:
                self._log.exception(
                    "role_executor.skill_failed",
                    role_id=role_id,
                    skill_id=skill_id,
                )
                # On failure, keep previous_output from last
                # successful skill — next skill still gets context

        combined = _merge_skill_outputs(role.name, skill_results)
        session_id = skill_results[0].session_id if skill_results else ""

        self._log.info(
            "role_executor.complete",
            role_id=role_id,
            user_id=user_id,
            skills_run=len(skill_results),
            output_length=len(combined),
        )

        return RoleResult(
            role_id=role_id,
            department_id=role.department_id,
            user_id=user_id,
            skill_results=skill_results,
            combined_output=combined,
            total_cost=total_cost,
            session_id=session_id,
        )


# =============================================================================
# Department Executor
# =============================================================================


class DepartmentExecutor:
    """Executes all roles in a department.

    Runs each role's briefing_skills via the ``RoleExecutor``, then
    merges all role outputs into a department-level report.

    Manager roles are detected and delegated to a ``ManagerExecutor``
    (if provided).  Roles managed by a manager are skipped to avoid
    double-execution — the manager handles them internally.

    Args:
        role_executor: The ``RoleExecutor`` to delegate role runs to.
        registry: The ``SkillRegistry`` for looking up departments/roles.
        manager_executor: Optional ``ManagerExecutor`` for running manager
            roles.  If ``None`` and managers exist in the department, a
            warning is logged and all roles (including managers) are run
            as regular roles for backward compatibility.
    """

    def __init__(
        self,
        role_executor: RoleExecutor,
        registry: SkillRegistry,
        manager_executor: Any | None = None,
    ) -> None:
        self._role_executor = role_executor
        self._registry = registry
        self._manager_executor = manager_executor
        self._log = logger.bind(component="department_executor")

    async def execute_department(
        self,
        department_id: str,
        user_id: str,
        accounts: list[dict[str, Any]],
        analysis_date: date | None = None,
    ) -> DepartmentResult:
        """Run all roles in a department.

        When a ``manager_executor`` is available, manager roles are
        identified via ``registry.list_managers(department_id=...)``.
        Each manager is run through ``ManagerExecutor.execute_manager()``,
        which internally handles its sub-roles.  Non-managed roles are
        run via ``RoleExecutor.execute_role()`` as usual.  Roles that
        are managed by any manager are excluded from the independent
        role run to prevent double-execution.

        If no ``manager_executor`` was provided, all roles are run as
        regular roles (backward compatible).

        Args:
            department_id: The department to execute.
            user_id: Identifier for the advertiser / user.
            accounts: List of account context dicts.
            analysis_date: Reference date for the analysis.

        Returns:
            ``DepartmentResult`` with merged output from all roles.

        Raises:
            DepartmentNotFoundError: If the department is not in the registry.
        """
        dept = self._registry.get_department(department_id)
        if dept is None:
            raise DepartmentNotFoundError(f"Department '{department_id}' not found in registry")

        roles = self._registry.list_roles(department_id)

        self._log.info(
            "department_executor.start",
            department_id=department_id,
            user_id=user_id,
            num_roles=len(roles),
        )

        role_results: list[RoleResult] = []
        total_cost: dict[str, Any] = {
            "total_cost_usd": 0.0,
            "num_turns": 0,
            "duration_ms": 0,
            "role_costs": {},
        }

        # Identify managers in this department
        managers = self._registry.list_managers(department_id=department_id)

        # Build set of all managed role IDs (roles handled by a manager)
        managed_role_ids: set[str] = set()
        for mgr in managers:
            for managed_id in mgr.manages:
                managed_role_ids.add(managed_id)

        # Determine whether we can use manager execution
        use_managers = bool(managers) and self._manager_executor is not None

        if managers and self._manager_executor is None:
            self._log.warning(
                "department_executor.no_manager_executor",
                department_id=department_id,
                manager_ids=[m.id for m in managers],
                msg=(
                    "Manager roles found but no manager_executor provided. "
                    "Running all roles as regular roles (backward compat)."
                ),
            )
            # Fall through — managed_role_ids stays populated but won't
            # be used because use_managers is False.
            managed_role_ids = set()

        # Phase 1: Run managers via ManagerExecutor
        if use_managers:
            for mgr in managers:
                try:
                    result = await self._manager_executor.execute_manager(
                        role_id=mgr.id,
                        user_id=user_id,
                        accounts=accounts,
                        analysis_date=analysis_date,
                    )
                    role_results.append(result)
                    _accumulate_role_cost(total_cost, result)
                except Exception:
                    self._log.exception(
                        "department_executor.manager_failed",
                        department_id=department_id,
                        manager_id=mgr.id,
                    )

        # Phase 2: Run remaining non-managed, non-manager roles
        manager_ids = {m.id for m in managers} if use_managers else set()
        for role in roles:
            # Skip roles handled by a manager
            if role.id in managed_role_ids:
                self._log.debug(
                    "department_executor.skip_managed_role",
                    department_id=department_id,
                    role_id=role.id,
                )
                continue
            # Skip manager roles themselves (already run in Phase 1)
            if role.id in manager_ids:
                continue

            try:
                result = await self._role_executor.execute_role(
                    role_id=role.id,
                    user_id=user_id,
                    accounts=accounts,
                    analysis_date=analysis_date,
                )
                role_results.append(result)
                _accumulate_role_cost(total_cost, result)
            except Exception:
                self._log.exception(
                    "department_executor.role_failed",
                    department_id=department_id,
                    role_id=role.id,
                )

        combined = _merge_role_outputs(dept.name, role_results)

        self._log.info(
            "department_executor.complete",
            department_id=department_id,
            user_id=user_id,
            roles_run=len(role_results),
            output_length=len(combined),
        )

        return DepartmentResult(
            department_id=department_id,
            user_id=user_id,
            role_results=role_results,
            combined_output=combined,
            total_cost=total_cost,
        )


# =============================================================================
# Exceptions
# =============================================================================


class RoleNotFoundError(Exception):
    """Raised when a role ID is not found in the registry."""

    pass


class DepartmentNotFoundError(Exception):
    """Raised when a department ID is not found in the registry."""

    pass


# =============================================================================
# Context composition
# =============================================================================


def compose_role_context(
    department: "DepartmentDefinition | None",
    role: "RoleDefinition",
    memory_context: str = "",
    registry: "SkillRegistry | None" = None,
    pending_messages: str = "",
) -> str:
    """Build the role context string from department + role definitions.

    This context is injected between BASE_SYSTEM_PROMPT and the skill's
    system_supplement when skills are run as part of a role.

    Args:
        department: The department definition (may be None for loose roles).
        role: The role definition.
        memory_context: Pre-composed memory string to inject after role
            persona (default empty — backward compatible).
        registry: Optional skill registry. When provided and the role is a
            manager, a "Your Team" section is appended listing each managed
            role's name, skills, and persona summary.
        pending_messages: Pre-composed message inbox string to inject
            after memory context (default empty — no messages).

    Returns:
        Combined context string. Empty string if no context to add.
    """
    from src.skills.schema import load_hierarchy_context_text

    sections: list[str] = []

    # Department context
    if department:
        if department.context:
            sections.append(f"# Department Context: {department.name}\n\n{department.context}")
        if department.context_files:
            dept_text = load_hierarchy_context_text(
                department.context_files,
                department.source_dir,
            )
            if dept_text:
                sections.append(dept_text)
        # Department vocabulary (domain-native terminology)
        if department.vocabulary:
            vocab_lines = [f"- **{term}**: {defn}" for term, defn in department.vocabulary]
            sections.append(
                "# Department Vocabulary\n\n"
                "Use these terms consistently:\n" + "\n".join(vocab_lines)
            )

    # Role persona
    if role.persona:
        sections.append(f"# Role: {role.name}\n\n{role.persona}")

    # Decision-making principles (injected after persona, before goals)
    if role.principles:
        principles_text = "\n".join(f"- {p}" for p in role.principles)
        sections.append(
            f"# Decision-Making Principles\n\n"
            f"When facing ambiguous situations or trade-offs, apply these "
            f"principles to guide your reasoning:\n\n{principles_text}"
        )

    # Active goals (always-present decision filters)
    if role.goals:
        goal_lines = [f"- {g}" for g in role.goals]
        sections.append(
            "# Active Goals\n\nFilter every decision through these goals:\n" + "\n".join(goal_lines)
        )

    if role.context_files:
        role_text = load_hierarchy_context_text(
            role.context_files,
            role.source_dir,
        )
        if role_text:
            sections.append(role_text)

    # Persistent memory (injected after role persona, before skills)
    if memory_context:
        sections.append(memory_context)

    # Pending peer messages (injected after memory, before team awareness)
    if pending_messages:
        sections.append(pending_messages)

    # Team awareness for manager roles
    if getattr(role, "manages", ()) and registry is not None:
        team_lines: list[str] = ["# Your Team\n"]
        for managed_id in role.manages:
            managed_role = registry.get_role(managed_id)
            if managed_role is None:
                continue
            skills = registry.list_skills_for_role(managed_id)
            skill_names = ", ".join(s.name for s in skills) if skills else "No skills defined"
            # First sentence of persona for compact context
            persona_summary = ""
            if managed_role.persona:
                first_sentence = managed_role.persona.split(".")[0].strip()
                persona_summary = f"\n{first_sentence}."
            team_lines.append(f"## {managed_role.name}\nSkills: {skill_names}{persona_summary}")
        if len(team_lines) > 1:  # More than just the header
            sections.append("\n\n".join(team_lines))

    # Peer department heads (other managers the role can consult)
    if getattr(role, "manages", ()) and registry is not None:
        all_roles = registry.list_roles()
        peers = [r for r in all_roles if getattr(r, "manages", ()) and r.id != role.id]
        if peers:
            peer_lines: list[str] = [
                "# Peer Department Heads\nYou can consult these peers via `consult_peer`."
            ]
            for peer in peers:
                dept = registry.get_department(peer.department_id)
                dept_name = dept.name if dept else peer.department_id
                peer_lines.append(f"- **{peer.name}** (`{peer.id}`) — {dept_name}")
            sections.append("\n".join(peer_lines))

    return "\n\n".join(sections)


# =============================================================================
# Output merging helpers
# =============================================================================


def _merge_skill_outputs(
    role_name: str,
    skill_results: list[SkillResult],
) -> str:
    """Merge skill outputs into a single role briefing.

    Each skill's output becomes a section under a skill-name header.
    No additional LLM call — simple concatenation.

    Args:
        role_name: The human-readable role name for the top header.
        skill_results: List of skill execution results.

    Returns:
        Merged output text.
    """
    if not skill_results:
        return f"# {role_name} — Briefing\n\nNo skills produced output."

    sections = [f"# {role_name} — Briefing"]
    for result in skill_results:
        sections.append(f"\n\n## {result.skill_id}\n\n{result.output_text}")

    return "".join(sections)


def _merge_role_outputs(
    dept_name: str,
    role_results: list[RoleResult],
) -> str:
    """Merge role outputs into a single department report.

    Args:
        dept_name: The human-readable department name.
        role_results: List of role execution results.

    Returns:
        Merged output text.
    """
    if not role_results:
        return f"# {dept_name} — Report\n\nNo roles produced output."

    sections = [f"# {dept_name} — Department Report"]
    for result in role_results:
        sections.append(f"\n\n{result.combined_output}")

    return "".join(sections)


def _merge_cost(
    total: dict[str, Any],
    skill_cost: dict[str, Any],
    skill_id: str,
) -> None:
    """Merge a single skill's cost into the running total."""
    total["total_cost_usd"] += skill_cost.get("total_cost_usd", 0.0)
    total["num_turns"] += skill_cost.get("num_turns", 0)
    total["duration_ms"] += skill_cost.get("duration_ms", 0)
    total["skill_costs"][skill_id] = skill_cost


def _accumulate_role_cost(
    total: dict[str, Any],
    role_result: RoleResult,
) -> None:
    """Accumulate a role result's cost into the department-level total.

    Args:
        total: The running total cost dict for the department.
        role_result: The role result whose cost to merge in.
    """
    total["total_cost_usd"] += role_result.total_cost.get("total_cost_usd", 0.0)
    total["num_turns"] += role_result.total_cost.get("num_turns", 0)
    total["duration_ms"] += role_result.total_cost.get("duration_ms", 0)
    total["role_costs"][role_result.role_id] = role_result.total_cost
