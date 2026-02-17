"""Anthropic (Claude) LLM provider.

Wraps the Anthropic Messages API for single-turn completions.
The full multi-turn agent loop (``run_agent_loop``) stays in
``api_client.py`` since it handles tool dispatch; this provider
is only used for structured, no-tool tasks when selected by the
hybrid router.
"""

from __future__ import annotations

import anthropic
import structlog

from src.config import settings
from src.llm.provider import LLMResult

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Pricing (USD per million tokens)
# ---------------------------------------------------------------------------

ANTHROPIC_PRICING: dict[str, dict[str, float]] = {
    "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-0": {"input": 3.00, "output": 15.00},
    "claude-opus-4-20250514": {"input": 15.00, "output": 75.00},
    "claude-opus-4-0": {"input": 15.00, "output": 75.00},
}

_DEFAULT_PRICING = {"input": 3.00, "output": 15.00}

# Per-model max output tokens
_MODEL_MAX_OUTPUT: dict[str, int] = {
    "claude-3-haiku-20240307": 4096,
}
_DEFAULT_MAX_TOKENS = 8192


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class AnthropicProvider:
    """Claude API provider for single-turn completions."""

    @property
    def name(self) -> str:
        return "anthropic"

    async def complete(
        self,
        *,
        model: str,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 1024,
    ) -> LLMResult:
        """Make a single-turn Claude API call."""
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

        resolved_max = min(
            max_tokens,
            _MODEL_MAX_OUTPUT.get(model, _DEFAULT_MAX_TOKENS),
        )

        try:
            response = await client.messages.create(
                model=model,
                max_tokens=resolved_max,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
        except Exception as exc:
            logger.error(
                "anthropic_provider.error",
                error=str(exc),
                model=model,
            )
            raise

        # Extract text
        text = ""
        if response.content:
            first = response.content[0]
            if hasattr(first, "text"):
                text = first.text

        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost = self.estimate_cost(model, input_tokens, output_tokens)

        return LLMResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
            provider=self.name,
            cost_usd=cost,
        )

    def estimate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        pricing = ANTHROPIC_PRICING.get(model, _DEFAULT_PRICING)
        return (
            input_tokens * pricing["input"] / 1_000_000
            + output_tokens * pricing["output"] / 1_000_000
        )
