"""Claude Code headless executor for Sidera skills.

Executes skills as direct Anthropic API agent loops via ``run_agent_loop()``,
dispatching tool calls through the ``ToolRegistry``.  No subprocess or CLI
dependency — works in all deployment environments (Railway, Docker, etc.).

Usage::

    executor = ClaudeCodeExecutor()
    result = await executor.execute(
        skill=skill,
        prompt="Analyze campaign performance...",
        user_id="claude_code",
    )
    print(result.output_text, result.cost_usd)
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from src.agent.api_client import run_agent_loop
from src.config import settings
from src.mcp_stdio.bridge import DIRECT_TOOLS, HEADLESS_CONTEXT_TOOLS
from src.skills.schema import SkillDefinition, load_context_text

logger = structlog.get_logger(__name__)


# =============================================================================
# Result dataclass
# =============================================================================


@dataclass
class ClaudeCodeResult:
    """Result from a Claude Code headless execution.

    Attributes:
        skill_id: The skill that was executed.
        user_id: Who triggered the execution.
        output_text: The final text output.
        structured_output: Validated JSON if ``output_format`` schema was set.
        cost_usd: Total USD cost of the execution.
        num_turns: Number of LLM turns used.
        duration_ms: Wall-clock time in milliseconds.
        session_id: Session identifier.
        usage: Token usage breakdown dict.
        is_error: Whether the execution ended in error.
        error_message: Error description if ``is_error`` is True.
    """

    skill_id: str
    user_id: str
    output_text: str
    structured_output: Any = None
    cost_usd: float = 0.0
    num_turns: int = 0
    duration_ms: int = 0
    session_id: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    is_error: bool = False
    error_message: str = ""


# =============================================================================
# Model resolution
# =============================================================================


def _resolve_model(model_name: str) -> str:
    """Map a Sidera model alias to a full Anthropic model ID.

    Uses the same mapping as ``SideraAgent._resolve_model`` in
    ``src/agent/core.py``.
    """
    model_map: dict[str, str] = {
        "haiku": settings.model_fast,
        "sonnet": settings.model_standard,
        "opus": settings.model_reasoning,
    }
    return model_map.get(model_name, settings.model_standard)


# =============================================================================
# Core executor
# =============================================================================


class ClaudeCodeExecutor:
    """Executes Sidera skills via the direct Anthropic API agent loop.

    Each execution calls ``run_agent_loop()`` from ``src.agent.api_client``,
    dispatching tool calls through the global ``ToolRegistry``.  No subprocess
    or CLI dependency — works in all deployment environments.

    Args:
        project_dir: Deprecated — kept for backward compatibility, ignored.
        sidera_mcp_config: Deprecated — kept for backward compatibility, ignored.
    """

    def __init__(
        self,
        project_dir: str | Path | None = None,  # noqa: ARG002
        sidera_mcp_config: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> None:
        self._log = logger.bind(component="claude_code_executor")

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    async def execute(
        self,
        skill: SkillDefinition,
        prompt: str,
        user_id: str,
        *,
        role_context: str = "",
        memory_context: str = "",
        permission_mode: str = "",  # noqa: ARG002 — kept for compat
        max_budget_usd: float | None = None,
        include_sidera_tools: bool = True,
        include_context_tools: bool = False,
        role_id: str = "",
        department_id: str = "",
        params: dict[str, Any] | None = None,
    ) -> ClaudeCodeResult:
        """Execute a skill via the direct Anthropic API agent loop.

        Args:
            skill: The skill definition to execute.
            prompt: The task prompt (overrides skill's prompt_template
                if non-empty).
            user_id: Who triggered the execution.
            role_context: Pre-composed role/department context string.
            memory_context: Pre-composed memory context string.
            permission_mode: Deprecated — kept for backward compatibility.
            max_budget_usd: Cost cap in USD.  Passed to ``run_agent_loop``
                for runtime enforcement (loop aborts when exceeded).
            include_sidera_tools: Whether to provide Sidera tools to the agent.
            include_context_tools: Whether to include context-dependent tools
                (memory, messaging, evolution).  When True, sets up contextvars
                for the execution and expands the tool set.
            role_id: Role ID for contextvars setup (required when
                ``include_context_tools=True``).
            department_id: Department ID for contextvars setup.
            params: Template parameters for prompt rendering.

        Returns:
            ``ClaudeCodeResult`` with output, cost, and metadata.
        """
        # 1. Compose system prompt (unchanged helper)
        system_prompt = self._compose_system_prompt(skill, role_context, memory_context)

        # 2. Render user prompt (unchanged helper)
        rendered_prompt = self._render_prompt(skill, prompt, params)

        # 3. Resolve model
        model = _resolve_model(skill.model)

        # 4. Resolve tools
        tools = self._resolve_tools(skill, include_context_tools) if include_sidera_tools else []

        self._log.info(
            "claude_code.execute_start",
            skill_id=skill.id,
            model=model,
            max_turns=skill.max_turns,
            tool_count=len(tools),
            context_tools=include_context_tools,
        )

        start_time = time.monotonic()

        # Set up contextvars if context-dependent tools are requested.
        if include_context_tools and role_id:
            self._setup_contextvars(role_id, department_id, user_id)

        try:
            from src.llm.provider import TaskType

            result = await run_agent_loop(
                system_prompt=system_prompt,
                user_prompt=rendered_prompt,
                model=model,
                tools=tools if tools else None,
                max_turns=skill.max_turns,
                max_cost_usd=max_budget_usd,
                task_type=TaskType.SKILL_EXECUTION,
            )
        except Exception as exc:
            elapsed = int((time.monotonic() - start_time) * 1000)
            self._log.error(
                "claude_code.execute_error",
                skill_id=skill.id,
                error=str(exc),
                duration_ms=elapsed,
            )
            return ClaudeCodeResult(
                skill_id=skill.id,
                user_id=user_id,
                output_text="",
                is_error=True,
                error_message=str(exc),
                duration_ms=elapsed,
            )
        finally:
            if include_context_tools and role_id:
                self._teardown_contextvars()

        # 5. Map TurnResult -> ClaudeCodeResult
        cost_dict = result.cost or {}
        elapsed = cost_dict.get("duration_ms", int((time.monotonic() - start_time) * 1000))

        self._log.info(
            "claude_code.execute_complete",
            skill_id=skill.id,
            cost_usd=cost_dict.get("total_cost_usd", 0),
            num_turns=result.turn_count,
            duration_ms=elapsed,
            is_error=result.is_error,
        )

        return ClaudeCodeResult(
            skill_id=skill.id,
            user_id=user_id,
            output_text=result.text or "",
            structured_output=self._try_parse_structured_output(result.text or "", skill),
            cost_usd=cost_dict.get("total_cost_usd", 0.0),
            num_turns=result.turn_count,
            duration_ms=elapsed,
            session_id=result.session_id,
            usage={
                "input_tokens": cost_dict.get("input_tokens", 0),
                "output_tokens": cost_dict.get("output_tokens", 0),
            },
            is_error=result.is_error,
            error_message="" if not result.is_error else (result.text or "Agent loop error"),
        )

    # -----------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------

    def _resolve_tools(
        self,
        skill: SkillDefinition,
        include_context_tools: bool = False,
    ) -> list[dict[str, Any]]:
        """Resolve tool definitions for a skill execution.

        If the skill specifies ``tools_required``, use that set (intersected
        with the allowed set).  Otherwise fall back to the full allowed set.

        When ``include_context_tools`` is True, the allowed set expands to
        include ``HEADLESS_CONTEXT_TOOLS`` (memory, messaging, evolution).
        """
        # Ensure MCP tools are registered in the global ToolRegistry.
        # These imports trigger @tool decorator registration on first import.
        import src.mcp_servers.bigquery  # noqa: F401
        import src.mcp_servers.code_execution  # noqa: F401
        import src.mcp_servers.google_ads  # noqa: F401
        import src.mcp_servers.google_drive  # noqa: F401
        import src.mcp_servers.meta  # noqa: F401
        import src.mcp_servers.slack  # noqa: F401
        import src.mcp_servers.skill_runner  # noqa: F401
        import src.mcp_servers.system  # noqa: F401

        if include_context_tools:
            import src.mcp_servers.actions  # noqa: F401
            import src.mcp_servers.context  # noqa: F401
            import src.mcp_servers.evolution  # noqa: F401
            import src.mcp_servers.memory  # noqa: F401
            import src.mcp_servers.messaging  # noqa: F401

        from src.agent.tool_registry import get_global_registry

        registry = get_global_registry()

        allowed_set = (
            DIRECT_TOOLS | HEADLESS_CONTEXT_TOOLS if include_context_tools else DIRECT_TOOLS
        )

        if skill.tools_required:
            allowed = [t for t in skill.tools_required if t in allowed_set]
            if allowed:
                return registry.get_filtered_definitions(allowed)

        return registry.get_filtered_definitions(list(allowed_set))

    def _compose_system_prompt(
        self,
        skill: SkillDefinition,
        role_context: str,
        memory_context: str,
    ) -> str:
        """Compose the full system prompt from skill + role + memory.

        Follows the same composition order as ``compose_role_context()``
        in ``src/skills/executor.py``:

        0. Base system prompt (only when no role_context — role_context
           already includes it via compose_role_context)
        1. Role context (dept context + role persona + principles)
        2. Memory context (hot memories)
        3. Skill supplement
        4. Context files (lazy manifest for multi-turn skills)
        5. Business guidance
        6. Output format instructions
        """
        from src.agent.prompts import BASE_SYSTEM_PROMPT

        sections: list[str] = []

        if role_context:
            sections.append(role_context)
        else:
            # No role context — include the base prompt so headless
            # executions still get core identity and guardrails.
            sections.append(BASE_SYSTEM_PROMPT)

        if memory_context:
            sections.append(memory_context)

        if skill.system_supplement:
            sections.append(f"# Skill: {skill.name}\n\n{skill.system_supplement}")

        # Multi-turn skills use lazy loading (manifest only); single-turn
        # skills get full context text injected.
        context = load_context_text(skill, lazy=(skill.max_turns > 1))
        if context:
            sections.append(context)

        if skill.business_guidance:
            sections.append(f"# Business Guidance\n\n{skill.business_guidance}")

        if skill.output_format:
            sections.append(f"# Output Format\n\n{skill.output_format}")

        return "\n\n---\n\n".join(sections)

    @staticmethod
    def _try_parse_structured_output(text: str, skill: SkillDefinition) -> Any:
        """Try to extract JSON structured output from the agent's response.

        Looks for ```json ... ``` fenced blocks first, then attempts to
        parse the full text as JSON.  Returns ``None`` on any failure.
        """
        if not skill.output_format or not text:
            return None
        try:
            # Try fenced JSON block first.
            match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            # Fallback: try the full text.
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None

    def _render_prompt(
        self,
        skill: SkillDefinition,
        prompt: str,
        params: dict[str, Any] | None,
    ) -> str:
        """Render the task prompt.

        If an explicit ``prompt`` is provided, use it directly.
        Otherwise, fall back to the skill's ``prompt_template``,
        optionally applying ``params`` via ``.format()``.
        """
        if prompt:
            return prompt
        template = skill.prompt_template
        if params:
            try:
                return template.format(**params)
            except (KeyError, IndexError):
                pass
        return template

    @staticmethod
    def _setup_contextvars(role_id: str, department_id: str, user_id: str) -> None:
        """Set up contextvars for context-dependent tool access.

        Mirrors the pattern used in ``_handle_talk_to_role`` in
        ``src/mcp_stdio/meta_tools.py``.
        """
        try:
            from src.mcp_servers.evolution import set_proposer_context
            from src.mcp_servers.memory import set_memory_context
            from src.mcp_servers.messaging import set_messaging_context

            set_memory_context(role_id, department_id, user_id)
            set_messaging_context(role_id, department_id)
            set_proposer_context(role_id, department_id)
        except Exception:
            logger.warning("claude_code.contextvars_setup_failed", role_id=role_id)

    @staticmethod
    def _teardown_contextvars() -> None:
        """Clear contextvars after execution."""
        try:
            from src.mcp_servers.evolution import clear_proposer_context
            from src.mcp_servers.memory import clear_memory_context
            from src.mcp_servers.messaging import clear_messaging_context

            clear_memory_context()
            clear_messaging_context()
            clear_proposer_context()
        except Exception:
            logger.warning("claude_code.contextvars_teardown_failed")
