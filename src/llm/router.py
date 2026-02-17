"""Hybrid LLM provider router.

Selects the cheapest capable provider for each task type based on
configuration.  External-eligible tasks (routing, memory extraction,
reflection, etc.) can be routed to cheaper providers when enabled.
Quality-critical tasks (analysis, conversation, tool use) always
stay on the Anthropic Claude API.

Automatic fallback: if an external provider fails, the router
transparently retries with the Anthropic provider.
"""

from __future__ import annotations

import structlog

from src.config import settings
from src.llm.anthropic_provider import AnthropicProvider
from src.llm.openai_compat_provider import OpenAICompatibleProvider
from src.llm.provider import (
    EXTERNAL_ELIGIBLE_TASKS,
    LLMProvider,
    LLMResult,
    TaskType,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Provider singletons (lazy-initialized)
# ---------------------------------------------------------------------------

_anthropic_provider: AnthropicProvider | None = None
_external_provider: OpenAICompatibleProvider | None = None


def _get_anthropic_provider() -> AnthropicProvider:
    """Get or create the Anthropic provider singleton."""
    global _anthropic_provider
    if _anthropic_provider is None:
        _anthropic_provider = AnthropicProvider()
    return _anthropic_provider


def _get_external_provider() -> OpenAICompatibleProvider | None:
    """Get or create the external provider singleton.

    Returns None if external LLM is not configured.
    """
    global _external_provider
    if _external_provider is not None:
        return _external_provider

    if not settings.external_llm_enabled:
        return None

    if not settings.external_llm_endpoint:
        logger.warning(
            "llm_router.no_endpoint",
            msg="external_llm_enabled=True but no endpoint configured",
        )
        return None

    _external_provider = OpenAICompatibleProvider(
        name_id=settings.external_llm_provider,
        base_url=settings.external_llm_endpoint,
        api_key=settings.external_llm_api_key,
        default_model=settings.external_llm_model,
        timeout=settings.external_llm_timeout,
    )
    logger.info(
        "llm_router.external_provider_initialized",
        provider=settings.external_llm_provider,
        endpoint=settings.external_llm_endpoint,
        model=settings.external_llm_model,
    )
    return _external_provider


def reset_providers() -> None:
    """Reset provider singletons (for testing)."""
    global _anthropic_provider, _external_provider
    _anthropic_provider = None
    _external_provider = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_provider(task_type: TaskType) -> LLMProvider:
    """Get the best provider for a given task type.

    If external LLM is enabled and the task is eligible, returns the
    external provider.  Otherwise returns the Anthropic provider.

    Note: This returns the *provider*, not the result.  Callers should
    use ``complete_with_fallback()`` for automatic fallback behavior.

    Args:
        task_type: The type of LLM task to perform.

    Returns:
        The selected LLM provider.
    """
    if _should_use_external(task_type):
        external = _get_external_provider()
        if external is not None:
            return external

    return _get_anthropic_provider()


def get_model_for_task(task_type: TaskType) -> str:
    """Get the appropriate model ID for a task type.

    When routing to an external provider, returns the external model.
    Otherwise returns the Anthropic model from settings.

    Args:
        task_type: The type of LLM task.

    Returns:
        Model ID string.
    """
    if _should_use_external(task_type):
        external = _get_external_provider()
        if external is not None:
            return settings.external_llm_model

    # Default Anthropic model selection based on task tier
    return _anthropic_model_for_task(task_type)


async def complete_with_fallback(
    *,
    task_type: TaskType,
    system_prompt: str,
    user_message: str,
    model: str | None = None,
    max_tokens: int = 1024,
) -> LLMResult:
    """Complete a single-turn LLM call with automatic fallback.

    Tries the selected provider first.  If it's an external provider
    and the call fails, automatically falls back to the Anthropic
    provider with the appropriate model.

    Args:
        task_type: The type of LLM task (determines provider selection).
        system_prompt: System prompt text.
        user_message: User message text.
        model: Optional model override.  If None, auto-selected based
            on task_type.
        max_tokens: Maximum response tokens.

    Returns:
        An ``LLMResult`` with the response.  If fallback was used,
        ``is_fallback`` will be True.
    """
    provider = get_provider(task_type)
    resolved_model = model or get_model_for_task(task_type)

    # If using external provider, attempt with fallback
    if provider.name != "anthropic":
        try:
            result = await provider.complete(
                model=resolved_model,
                system_prompt=system_prompt,
                user_message=user_message,
                max_tokens=max_tokens,
            )
            logger.debug(
                "llm_router.external_success",
                provider=provider.name,
                model=resolved_model,
                task_type=task_type.value,
                cost_usd=result.cost_usd,
            )
            return result
        except Exception as exc:
            logger.warning(
                "llm_router.external_failed_fallback",
                provider=provider.name,
                model=resolved_model,
                task_type=task_type.value,
                error=str(exc),
            )
            # Fallback to Anthropic
            fallback = _get_anthropic_provider()
            fallback_model = _anthropic_model_for_task(task_type)
            result = await fallback.complete(
                model=fallback_model,
                system_prompt=system_prompt,
                user_message=user_message,
                max_tokens=max_tokens,
            )
            result.is_fallback = True
            result.metadata["fallback_reason"] = str(exc)
            result.metadata["original_provider"] = provider.name
            return result

    # Direct Anthropic call (no fallback needed)
    return await provider.complete(
        model=resolved_model,
        system_prompt=system_prompt,
        user_message=user_message,
        max_tokens=max_tokens,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _should_use_external(task_type: TaskType) -> bool:
    """Check if a task should be routed to the external provider."""
    if not settings.external_llm_enabled:
        return False

    if task_type not in EXTERNAL_ELIGIBLE_TASKS:
        return False

    # Check if this specific task type is in the configured list
    return task_type.value in settings.external_llm_tasks


def _anthropic_model_for_task(task_type: TaskType) -> str:
    """Select the Anthropic model for a task type."""
    # Haiku-tier tasks
    if task_type in {
        TaskType.SKILL_ROUTING,
        TaskType.ROLE_ROUTING,
        TaskType.MEMORY_EXTRACTION,
        TaskType.REFLECTION,
        TaskType.MEMORY_CONSOLIDATION,
        TaskType.MEMORY_VERSIONING,
        TaskType.FRICTION_DETECTION,
        TaskType.PHASE_COMPRESSION,
    }:
        return settings.model_fast

    # Sonnet-tier tasks
    if task_type in {
        TaskType.ANALYSIS,
        TaskType.CONVERSATION,
        TaskType.HEARTBEAT,
        TaskType.DELEGATION,
        TaskType.SYNTHESIS,
        TaskType.DATA_COLLECTION,
        TaskType.SKILL_EXECUTION,
    }:
        return settings.model_standard

    # Opus-tier tasks
    if task_type == TaskType.STRATEGY:
        return settings.model_reasoning

    # Fallback: Sonnet
    return settings.model_standard
