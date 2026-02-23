"""Direct Anthropic API client with tool execution loop for Sidera.

Replaces the Claude Agent SDK's ``query()`` function with a plain
``run_agent_loop()`` that calls ``anthropic.AsyncAnthropic.messages.create``
in a loop, dispatching tool calls through the :mod:`tool_registry`.

Usage::

    from src.agent.api_client import run_agent_loop

    result = await run_agent_loop(
        system_prompt="You are a marketing analyst...",
        user_prompt="Run a platform health check.",
        model="claude-3-haiku-20240307",
        tools=registry.get_tool_definitions(),
        max_turns=5,
    )
    print(result.text)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import anthropic
import structlog

from src.agent.tool_registry import get_global_registry
from src.config import settings
from src.llm.provider import TaskType

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Model pricing (USD per million tokens)
# ---------------------------------------------------------------------------

MODEL_PRICING: dict[str, dict[str, float]] = {
    # Haiku
    "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    # Sonnet
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-0": {"input": 3.00, "output": 15.00},
    # Opus
    "claude-opus-4-20250514": {"input": 15.00, "output": 75.00},
    "claude-opus-4-0": {"input": 15.00, "output": 75.00},
}

# Fallback pricing if a model isn't in the table (Sonnet-tier).
_DEFAULT_PRICING = {"input": 3.00, "output": 15.00}


# ---------------------------------------------------------------------------
# Extended thinking support
# ---------------------------------------------------------------------------

_THINKING_CAPABLE_MODELS: frozenset[str] = frozenset(
    {
        "claude-sonnet-4-20250514",
        "claude-sonnet-4-0",
        "claude-opus-4-20250514",
        "claude-opus-4-0",
        "claude-haiku-4-5-20251001",
    }
)


def _model_supports_thinking(model: str) -> bool:
    """Check if a model supports extended thinking."""
    return model in _THINKING_CAPABLE_MODELS


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class TurnResult:
    """Outcome of a complete ``run_agent_loop`` invocation.

    Attributes:
        text: Concatenated text produced by the model across all turns.
        cost: Dict with ``total_cost_usd``, ``num_turns``, ``duration_ms``,
            ``input_tokens``, ``output_tokens``, ``model``, ``is_error``.
        turn_count: Number of API round-trips.
        session_id: Placeholder for compatibility (always ``""``).
        is_error: ``True`` if the loop ended due to an exception.
        thinking_blocks: List of thinking block dicts captured during the
            run.  Each dict has ``turn`` (int) and ``thinking`` (str).
            Empty when extended thinking is disabled.
    """

    text: str = ""
    cost: dict[str, Any] = field(default_factory=dict)
    turn_count: int = 0
    session_id: str = ""
    is_error: bool = False
    thinking_blocks: list[dict[str, Any]] = field(default_factory=list)
    tool_errors: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost from token counts and published pricing."""
    pricing = MODEL_PRICING.get(model, _DEFAULT_PRICING)
    return (
        input_tokens * pricing["input"] / 1_000_000 + output_tokens * pricing["output"] / 1_000_000
    )


# ---------------------------------------------------------------------------
# Core agent loop
# ---------------------------------------------------------------------------


_MODEL_MAX_OUTPUT_TOKENS: dict[str, int] = {
    "claude-3-haiku-20240307": 4096,
}

_DEFAULT_MAX_TOKENS = 8192


async def run_agent_loop(
    *,
    system_prompt: str,
    user_prompt: str | list[dict[str, Any]],
    model: str,
    tools: list[dict[str, Any]] | None = None,
    max_turns: int = 20,
    max_tokens: int | None = None,
    max_cost_usd: float | None = None,
    task_type: TaskType = TaskType.GENERAL,
    thinking_budget: int | None = None,
) -> TurnResult:
    """Run a complete agent loop: prompt ➜ tool calls ➜ final response.

    This is the core replacement for the Claude Agent SDK's ``query()``
    function.  It sends the user prompt to the Anthropic Messages API,
    and if the model requests tool calls it dispatches them through the
    global :class:`ToolRegistry`, feeds results back, and repeats until
    the model produces a final text response or *max_turns* is reached.

    Args:
        system_prompt: System prompt text.
        user_prompt: User message to send.  Can be a plain string for
            text-only turns, or a list of Anthropic content blocks for
            multimodal turns (e.g. text + images).
        model: Anthropic model ID (e.g. ``"claude-3-haiku-20240307"``).
        tools: Tool definitions in Anthropic API format.  ``None`` or
            empty list means no tools (single-turn).
        max_turns: Maximum number of API round-trips.
        max_tokens: Maximum response tokens per API call.
        max_cost_usd: Optional cost cap in USD.  If the estimated cost
            exceeds this threshold after any turn, the loop stops
            gracefully and returns partial results.
        task_type: Classification of the LLM task (for metrics/logging).
            Defaults to ``TaskType.GENERAL``.
        thinking_budget: Token budget for extended thinking.  ``None``
            disables thinking.  When set, the model reasons internally
            before responding — dramatically improves quality for complex
            analysis, tool orchestration, and multi-step reasoning.
            Only supported on Sonnet 4+, Opus 4+, and Haiku 4.5+.

    Returns:
        A :class:`TurnResult` with the collected text, cost metadata,
        and turn count.
    """
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    registry = get_global_registry()

    # Resolve max_tokens: honour per-model limits so we don't send
    # a value the API will reject (e.g. Haiku caps at 4096).
    resolved_max_tokens = max_tokens or _MODEL_MAX_OUTPUT_TOKENS.get(model, _DEFAULT_MAX_TOKENS)

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_prompt},
    ]

    collected_text: list[str] = []
    collected_thinking: list[dict[str, Any]] = []
    total_input_tokens = 0
    total_output_tokens = 0
    turn_count = 0
    collected_tool_errors: list[dict[str, Any]] = []
    start_time = time.monotonic()

    # Build kwargs once (tools may be omitted for no-tool turns).
    api_kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": resolved_max_tokens,
        "system": system_prompt,
    }
    if tools:
        api_kwargs["tools"] = tools

    # Extended thinking — internal reasoning before responding.
    # Dramatically improves multi-step analysis, tool orchestration,
    # and strategic reasoning.  Only on capable models.
    _thinking_active = bool(thinking_budget and _model_supports_thinking(model))
    if _thinking_active:
        api_kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
        # max_tokens must be > budget_tokens; ensure room for both
        # thinking and response output.
        api_kwargs["max_tokens"] = max(resolved_max_tokens, thinking_budget + 4096)
        # Interleaved thinking: lets the model reason between tool calls
        # (think → tool call → think about result → next tool call).
        if tools:
            api_kwargs["extra_headers"] = {
                "anthropic-beta": "interleaved-thinking-2025-05-14",
            }

    try:
        for _turn in range(max_turns):
            turn_count += 1

            response = await client.messages.create(
                messages=messages,
                **api_kwargs,
            )

            total_input_tokens += response.usage.input_tokens
            total_output_tokens += response.usage.output_tokens

            # Runtime cost cap enforcement.
            if max_cost_usd is not None:
                running_cost = _estimate_cost(model, total_input_tokens, total_output_tokens)
                if running_cost >= max_cost_usd:
                    logger.warning(
                        "agent_loop.cost_cap_reached",
                        model=model,
                        cost_usd=running_cost,
                        cap_usd=max_cost_usd,
                        turns=turn_count,
                    )
                    # Collect any text from this final response before stopping.
                    for block in response.content:
                        if block.type == "text":
                            collected_text.append(block.text)
                    collected_text.append(
                        f"\n\n[Cost cap reached: ${max_cost_usd:.2f}. Returning partial results.]"
                    )
                    break

            # Separate text blocks from tool_use blocks.
            # Thinking and redacted_thinking blocks are internal reasoning —
            # they're preserved in messages for continuity but NOT included
            # in the output text.
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []

            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )
                elif block.type == "thinking":
                    # Extended thinking: internal reasoning block.
                    # Captured for optional visibility (e.g. Slack thread)
                    # but NOT included in the output text.
                    collected_thinking.append(
                        {
                            "turn": turn_count,
                            "thinking": block.thinking,
                        }
                    )
                elif block.type == "redacted_thinking":
                    # Redacted thinking — content is not available.
                    pass

            if text_parts:
                collected_text.extend(text_parts)

            # Done: no tool calls requested, or model signaled end_turn.
            if not tool_calls or response.stop_reason == "end_turn":
                break

            # Dispatch tool calls and feed results back.
            messages.append({"role": "assistant", "content": response.content})

            tool_results: list[dict[str, Any]] = []
            for tc in tool_calls:
                logger.info(
                    "tool.execute",
                    tool=tc["name"],
                    input_keys=list(tc["input"].keys()) if tc["input"] else [],
                )
                result_text = await registry.dispatch(tc["name"], tc["input"])
                logger.info(
                    "tool.complete",
                    tool=tc["name"],
                    result_chars=len(result_text),
                )
                # Capture tool errors for reflection
                if result_text.startswith("Error:") or result_text.startswith("error:"):
                    collected_tool_errors.append(
                        {
                            "tool_name": tc["name"],
                            "error_message": result_text[:500],
                            "input_summary": str(tc["input"])[:200],
                        }
                    )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": result_text,
                    }
                )

            messages.append({"role": "user", "content": tool_results})

    except Exception:
        logger.exception("agent_loop.error", model=model, turns=turn_count)
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        return TurnResult(
            text="\n".join(collected_text),
            cost={
                "total_cost_usd": _estimate_cost(model, total_input_tokens, total_output_tokens),
                "num_turns": turn_count,
                "duration_ms": elapsed_ms,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "model": model,
                "task_type": task_type.value,
                "thinking_enabled": _thinking_active,
                "is_error": True,
            },
            turn_count=turn_count,
            is_error=True,
            thinking_blocks=collected_thinking,
            tool_errors=collected_tool_errors,
        )

    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    estimated_cost = _estimate_cost(model, total_input_tokens, total_output_tokens)

    return TurnResult(
        text="\n".join(collected_text),
        cost={
            "total_cost_usd": estimated_cost,
            "num_turns": turn_count,
            "duration_ms": elapsed_ms,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "model": model,
            "task_type": task_type.value,
            "thinking_enabled": _thinking_active,
            "is_error": False,
        },
        turn_count=turn_count,
        is_error=False,
        thinking_blocks=collected_thinking,
        tool_errors=collected_tool_errors,
    )


# ---------------------------------------------------------------------------
# Lightweight single-turn helper (no tools)
# ---------------------------------------------------------------------------


async def call_claude_api(
    *,
    model: str,
    system_prompt: str,
    user_message: str,
    max_tokens: int = 1024,
    task_type: TaskType = TaskType.GENERAL,
) -> dict[str, Any]:
    """Make a single-turn Claude API call without tools.

    Convenience wrapper for cheap classification / evaluation tasks
    (e.g. orchestrator output grading, routing decisions).

    Returns:
        Dict with ``text`` and ``cost`` (containing ``total_cost_usd``).
    """
    result = await run_agent_loop(
        system_prompt=system_prompt,
        user_prompt=user_message,
        model=model,
        tools=None,
        max_turns=1,
        max_tokens=max_tokens,
        task_type=task_type,
    )
    return {
        "text": result.text,
        "cost": result.cost,
    }
