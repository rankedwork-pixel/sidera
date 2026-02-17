"""Tests for the hybrid LLM router.

Verifies:
- Provider selection based on task type and config
- External provider initialization
- Fallback behavior
- Model selection for each task type
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.llm.anthropic_provider import AnthropicProvider
from src.llm.openai_compat_provider import OpenAICompatibleProvider
from src.llm.provider import LLMResult, TaskType
from src.llm.router import (
    _anthropic_model_for_task,
    _should_use_external,
    complete_with_fallback,
    get_provider,
    reset_providers,
)


@pytest.fixture(autouse=True)
def _clean_singletons():
    """Reset provider singletons between tests."""
    reset_providers()
    yield
    reset_providers()


class TestProviderSelection:
    def test_default_is_anthropic(self):
        """Without external LLM enabled, all tasks use Anthropic."""
        for task_type in TaskType:
            provider = get_provider(task_type)
            assert isinstance(provider, AnthropicProvider)

    @patch("src.llm.router.settings")
    def test_external_enabled_for_eligible_task(self, mock_settings):
        mock_settings.external_llm_enabled = True
        mock_settings.external_llm_endpoint = "https://api.groq.com/openai/v1"
        mock_settings.external_llm_api_key = "gsk_test"
        mock_settings.external_llm_provider = "groq"
        mock_settings.external_llm_model = "llama-3.1-70b-versatile"
        mock_settings.external_llm_timeout = 30.0
        mock_settings.external_llm_tasks = ["skill_routing", "role_routing"]

        provider = get_provider(TaskType.SKILL_ROUTING)
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.name == "groq"

    @patch("src.llm.router.settings")
    def test_external_enabled_but_claude_only_task(self, mock_settings):
        mock_settings.external_llm_enabled = True
        mock_settings.external_llm_endpoint = "https://api.groq.com/openai/v1"
        mock_settings.external_llm_api_key = "gsk_test"
        mock_settings.external_llm_provider = "groq"
        mock_settings.external_llm_model = "llama-3.1-70b"
        mock_settings.external_llm_timeout = 30.0
        mock_settings.external_llm_tasks = ["skill_routing"]

        # CONVERSATION is never external-eligible
        provider = get_provider(TaskType.CONVERSATION)
        assert isinstance(provider, AnthropicProvider)

    @patch("src.llm.router.settings")
    def test_external_enabled_but_task_not_in_list(self, mock_settings):
        mock_settings.external_llm_enabled = True
        mock_settings.external_llm_endpoint = "https://api.groq.com/openai/v1"
        mock_settings.external_llm_api_key = "gsk_test"
        mock_settings.external_llm_provider = "groq"
        mock_settings.external_llm_model = "llama-3.1-70b"
        mock_settings.external_llm_timeout = 30.0
        # reflection is eligible but not in the configured list
        mock_settings.external_llm_tasks = ["skill_routing"]

        provider = get_provider(TaskType.REFLECTION)
        assert isinstance(provider, AnthropicProvider)

    @patch("src.llm.router.settings")
    def test_external_enabled_no_endpoint(self, mock_settings):
        mock_settings.external_llm_enabled = True
        mock_settings.external_llm_endpoint = ""  # No endpoint configured
        mock_settings.external_llm_tasks = ["skill_routing"]

        provider = get_provider(TaskType.SKILL_ROUTING)
        assert isinstance(provider, AnthropicProvider)


class TestShouldUseExternal:
    @patch("src.llm.router.settings")
    def test_disabled(self, mock_settings):
        mock_settings.external_llm_enabled = False
        assert not _should_use_external(TaskType.SKILL_ROUTING)

    @patch("src.llm.router.settings")
    def test_enabled_eligible_and_in_list(self, mock_settings):
        mock_settings.external_llm_enabled = True
        mock_settings.external_llm_tasks = ["skill_routing"]
        assert _should_use_external(TaskType.SKILL_ROUTING)

    @patch("src.llm.router.settings")
    def test_not_eligible(self, mock_settings):
        mock_settings.external_llm_enabled = True
        mock_settings.external_llm_tasks = ["analysis"]
        assert not _should_use_external(TaskType.ANALYSIS)


class TestAnthropicModelForTask:
    def test_haiku_tier(self):
        haiku_tasks = [
            TaskType.SKILL_ROUTING,
            TaskType.ROLE_ROUTING,
            TaskType.MEMORY_EXTRACTION,
            TaskType.REFLECTION,
            TaskType.MEMORY_CONSOLIDATION,
            TaskType.MEMORY_VERSIONING,
            TaskType.FRICTION_DETECTION,
            TaskType.PHASE_COMPRESSION,
        ]
        for task in haiku_tasks:
            model = _anthropic_model_for_task(task)
            assert "haiku" in model, f"{task} should use Haiku, got {model}"

    def test_sonnet_tier(self):
        sonnet_tasks = [
            TaskType.ANALYSIS,
            TaskType.CONVERSATION,
            TaskType.HEARTBEAT,
            TaskType.DELEGATION,
            TaskType.SYNTHESIS,
            TaskType.DATA_COLLECTION,
            TaskType.SKILL_EXECUTION,
        ]
        for task in sonnet_tasks:
            model = _anthropic_model_for_task(task)
            assert "sonnet" in model, f"{task} should use Sonnet, got {model}"

    def test_opus_tier(self):
        model = _anthropic_model_for_task(TaskType.STRATEGY)
        assert "opus" in model

    def test_general_fallback(self):
        model = _anthropic_model_for_task(TaskType.GENERAL)
        assert "sonnet" in model


class TestCompleteWithFallback:
    @pytest.mark.asyncio
    async def test_anthropic_direct(self):
        """When using Anthropic, no fallback needed."""
        mock_result = LLMResult(
            text='{"skill_id": "test", "confidence": 0.9}',
            model="claude-3-haiku-20240307",
            provider="anthropic",
        )

        with patch("src.llm.router.get_provider") as mock_get:
            mock_provider = AsyncMock()
            mock_provider.name = "anthropic"
            mock_provider.complete = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_provider

            with patch("src.llm.router.get_model_for_task", return_value="claude-3-haiku-20240307"):
                result = await complete_with_fallback(
                    task_type=TaskType.SKILL_ROUTING,
                    system_prompt="test",
                    user_message="test",
                )

            assert result.text == '{"skill_id": "test", "confidence": 0.9}'
            assert result.is_fallback is False

    @pytest.mark.asyncio
    async def test_external_success(self):
        """External provider succeeds — no fallback."""
        mock_result = LLMResult(
            text='{"skill_id": "test", "confidence": 0.8}',
            model="llama-3.1-70b",
            provider="groq",
        )

        with patch("src.llm.router.get_provider") as mock_get:
            mock_provider = AsyncMock()
            mock_provider.name = "groq"
            mock_provider.complete = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_provider

            with patch("src.llm.router.get_model_for_task", return_value="llama-3.1-70b"):
                result = await complete_with_fallback(
                    task_type=TaskType.SKILL_ROUTING,
                    system_prompt="test",
                    user_message="test",
                )

            assert result.provider == "groq"
            assert result.is_fallback is False

    @pytest.mark.asyncio
    async def test_external_failure_falls_back(self):
        """External provider fails — falls back to Anthropic."""
        fallback_result = LLMResult(
            text='{"skill_id": "test", "confidence": 0.9}',
            model="claude-3-haiku-20240307",
            provider="anthropic",
        )

        with patch("src.llm.router.get_provider") as mock_get:
            mock_provider = AsyncMock()
            mock_provider.name = "groq"
            mock_provider.complete = AsyncMock(side_effect=Exception("Connection refused"))
            mock_get.return_value = mock_provider

            with (
                patch("src.llm.router.get_model_for_task", return_value="llama-3.1-70b"),
                patch("src.llm.router._get_anthropic_provider") as mock_fallback,
            ):
                mock_anthro = AsyncMock()
                mock_anthro.complete = AsyncMock(return_value=fallback_result)
                mock_fallback.return_value = mock_anthro

                with patch(
                    "src.llm.router._anthropic_model_for_task",
                    return_value="claude-3-haiku-20240307",
                ):
                    result = await complete_with_fallback(
                        task_type=TaskType.SKILL_ROUTING,
                        system_prompt="test",
                        user_message="test",
                    )

            assert result.is_fallback is True
            assert result.metadata["original_provider"] == "groq"
            assert "Connection refused" in result.metadata["fallback_reason"]
