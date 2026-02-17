"""LLM provider protocol and shared types.

Defines the interface that all LLM providers must implement, plus the
``TaskType`` enum used by the router to select providers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Task types — used by the router to select providers
# ---------------------------------------------------------------------------


class TaskType(str, Enum):
    """Classification of LLM call purpose.

    The router uses this to decide which provider handles each call.
    Tasks marked as "external_eligible" in the docstring can be routed
    to cheaper providers when ``external_llm_enabled`` is True.
    """

    # --- External-eligible (structured output, no tools) ---
    SKILL_ROUTING = "skill_routing"
    """Skill classification — JSON output, ~200 tokens. External-eligible."""

    ROLE_ROUTING = "role_routing"
    """Role classification — JSON output, ~200 tokens. External-eligible."""

    MEMORY_EXTRACTION = "memory_extraction"
    """Post-turn memory extraction — JSON output. External-eligible."""

    REFLECTION = "reflection"
    """Post-run reflection — JSON output. External-eligible."""

    MEMORY_CONSOLIDATION = "memory_consolidation"
    """Weekly memory consolidation — JSON output. External-eligible."""

    MEMORY_VERSIONING = "memory_versioning"
    """Memory supersedes check — JSON output. External-eligible."""

    FRICTION_DETECTION = "friction_detection"
    """Skill friction analysis — JSON output. External-eligible."""

    PHASE_COMPRESSION = "phase_compression"
    """Phase 1.5 data compression — text output. External-eligible."""

    # --- Claude-only (tool use, quality-critical, user-facing) ---
    DATA_COLLECTION = "data_collection"
    """Phase 1 data collection with tools. Claude-only."""

    ANALYSIS = "analysis"
    """Phase 2 tactical analysis (Sonnet). Claude-only."""

    STRATEGY = "strategy"
    """Phase 3 strategic insights (Opus). Claude-only."""

    CONVERSATION = "conversation"
    """Conversational turns with tools. Claude-only."""

    HEARTBEAT = "heartbeat"
    """Proactive heartbeat with tools. Claude-only."""

    DELEGATION = "delegation"
    """Manager delegation decision. Claude-only."""

    SYNTHESIS = "synthesis"
    """Manager synthesis of sub-role outputs. Claude-only."""

    SKILL_EXECUTION = "skill_execution"
    """General skill execution with tools. Claude-only."""

    GENERAL = "general"
    """Uncategorized / fallback. Claude-only."""


# Tasks that CAN be routed to external providers
EXTERNAL_ELIGIBLE_TASKS: frozenset[TaskType] = frozenset(
    {
        TaskType.SKILL_ROUTING,
        TaskType.ROLE_ROUTING,
        TaskType.MEMORY_EXTRACTION,
        TaskType.REFLECTION,
        TaskType.MEMORY_CONSOLIDATION,
        TaskType.MEMORY_VERSIONING,
        TaskType.FRICTION_DETECTION,
        TaskType.PHASE_COMPRESSION,
    }
)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class LLMResult:
    """Result from a single LLM completion (no tool loop).

    Attributes:
        text: The model's text response.
        input_tokens: Number of input tokens used.
        output_tokens: Number of output tokens generated.
        model: The model ID that was actually used.
        provider: Provider name ("anthropic", "openai_compatible", etc.).
        cost_usd: Estimated cost in USD.
        is_fallback: True if this result came from a fallback provider.
    """

    text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    provider: str = ""
    cost_usd: float = 0.0
    is_fallback: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMProvider(Protocol):
    """Interface for LLM providers.

    All providers must implement ``complete()`` for single-turn,
    no-tool completions (the use case for external-eligible tasks).
    """

    @property
    def name(self) -> str:
        """Provider identifier (e.g. "anthropic", "openai_compatible")."""
        ...

    async def complete(
        self,
        *,
        model: str,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 1024,
    ) -> LLMResult:
        """Make a single-turn completion (no tools, no multi-turn).

        Args:
            model: Model identifier for this provider.
            system_prompt: System prompt text.
            user_message: User message text.
            max_tokens: Maximum response tokens.

        Returns:
            An ``LLMResult`` with the response text and metadata.
        """
        ...

    def estimate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Estimate USD cost from token counts.

        Args:
            model: Model identifier.
            input_tokens: Number of input tokens.
            output_tokens: Number of output tokens.

        Returns:
            Estimated cost in USD.
        """
        ...
