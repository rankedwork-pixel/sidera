"""Tests for the Anthropic LLM provider.

Verifies:
- Provider name
- Cost estimation
- Pricing table
"""

from __future__ import annotations

from src.llm.anthropic_provider import ANTHROPIC_PRICING, AnthropicProvider
from src.llm.provider import LLMProvider


class TestAnthropicProvider:
    def test_name(self):
        provider = AnthropicProvider()
        assert provider.name == "anthropic"

    def test_implements_protocol(self):
        provider = AnthropicProvider()
        assert isinstance(provider, LLMProvider)

    def test_estimate_cost_haiku(self):
        provider = AnthropicProvider()
        cost = provider.estimate_cost("claude-3-haiku-20240307", 1000, 500)
        # 1000 * 0.25/1M + 500 * 1.25/1M = 0.000875
        expected = 1000 * 0.25 / 1_000_000 + 500 * 1.25 / 1_000_000
        assert abs(cost - expected) < 0.0001

    def test_estimate_cost_sonnet(self):
        provider = AnthropicProvider()
        cost = provider.estimate_cost("claude-sonnet-4-20250514", 1000, 500)
        expected = 1000 * 3.0 / 1_000_000 + 500 * 15.0 / 1_000_000
        assert abs(cost - expected) < 0.0001

    def test_estimate_cost_unknown_model(self):
        """Unknown models should use default pricing (Sonnet-tier)."""
        provider = AnthropicProvider()
        cost = provider.estimate_cost("unknown-model", 1000, 500)
        expected = 1000 * 3.0 / 1_000_000 + 500 * 15.0 / 1_000_000
        assert abs(cost - expected) < 0.0001

    def test_pricing_table_has_all_tiers(self):
        # Haiku
        assert "claude-3-haiku-20240307" in ANTHROPIC_PRICING
        # Sonnet
        assert "claude-sonnet-4-20250514" in ANTHROPIC_PRICING
        # Opus
        assert "claude-opus-4-20250514" in ANTHROPIC_PRICING
