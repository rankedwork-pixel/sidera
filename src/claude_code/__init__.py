"""Claude Code headless task execution for Sidera.

Executes skills via the direct Anthropic API agent loop (``run_agent_loop``),
dispatching tool calls through the global ``ToolRegistry``.  No subprocess
or CLI dependency — works in all deployment environments (Railway, Docker, etc.).

Modules:
    executor      — Maps SkillDefinition → run_agent_loop() → ClaudeCodeResult
    task_manager  — Concurrent task management, DB tracking, cost recording

Convenience:
    run_claude_code_skill()  — one-call entrypoint: load registry → resolve
        skill/role → compose context → create task manager → execute.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


async def run_claude_code_skill(
    *,
    skill_id: str,
    prompt: str = "",
    role_id: str = "",
    user_id: str = "system",
    max_budget_usd: float = 5.0,
    params: dict[str, Any] | None = None,
    include_context_tools: bool = False,
) -> dict[str, Any]:
    """Execute a Sidera skill headlessly with full context composition.

    Consolidates the repeated pattern used in ``meta_tools.py`` and
    ``daily_briefing.py`` into a single top-level convenience function.

    Args:
        skill_id: Skill to execute (must exist in registry).
        prompt: Custom user prompt (overrides skill's prompt_template).
        role_id: Optional role context.  When provided, the role's persona,
            memory, and department context are injected.
        user_id: Who triggered the execution.
        max_budget_usd: Cost cap in USD (enforced at runtime).
        params: Template parameters for prompt rendering.
        include_context_tools: Whether to enable context-dependent tools
            (memory, messaging, evolution) via contextvars.

    Returns:
        Dict with ``output_text``, ``structured_output``, ``cost_usd``,
        ``num_turns``, ``duration_ms``, ``is_error``, ``error_message``.
    """
    from src.claude_code.executor import ClaudeCodeExecutor
    from src.skills.db_loader import load_registry_with_db
    from src.skills.executor import compose_role_context
    from src.skills.memory import compose_memory_context

    # Load registry (YAML + DB overlay).
    registry = await load_registry_with_db()

    # Resolve skill.
    skill = registry.get_skill(skill_id)
    if skill is None:
        return {
            "output_text": f"Skill '{skill_id}' not found.",
            "structured_output": None,
            "cost_usd": 0.0,
            "num_turns": 0,
            "duration_ms": 0,
            "is_error": True,
            "error_message": f"Skill '{skill_id}' not found.",
        }

    # Compose role context if a role is specified.
    role_context = ""
    memory_context = ""
    department_id = ""

    if role_id:
        role = registry.get_role(role_id)
        if role:
            department_id = role.department_id
            dept = registry.get_department(department_id) if department_id else None
            role_context = compose_role_context(registry, role, dept)
            try:
                memory_context = await compose_memory_context(role_id)
            except Exception:
                logger.warning("run_claude_code_skill.memory_failed", role_id=role_id)

    executor = ClaudeCodeExecutor()
    result = await executor.execute(
        skill=skill,
        prompt=prompt,
        user_id=user_id,
        role_context=role_context,
        memory_context=memory_context,
        max_budget_usd=max_budget_usd,
        include_sidera_tools=True,
        include_context_tools=include_context_tools,
        role_id=role_id,
        department_id=department_id,
        params=params,
    )

    return {
        "output_text": result.output_text,
        "structured_output": result.structured_output,
        "cost_usd": result.cost_usd,
        "num_turns": result.num_turns,
        "duration_ms": result.duration_ms,
        "is_error": result.is_error,
        "error_message": result.error_message,
    }
