"""Tests for the OpenAI-compatible LLM provider.

Verifies:
- Response parsing
- Cost estimation
- Error handling
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.openai_compat_provider import OpenAICompatibleProvider


@pytest.fixture
def provider():
    return OpenAICompatibleProvider(
        name_id="test_provider",
        base_url="https://api.test.com/v1",
        api_key="test-key",
        default_model="test-model",
    )


class TestOpenAICompatibleProvider:
    def test_name(self, provider):
        assert provider.name == "test_provider"

    def test_estimate_cost_known_model(self):
        provider = OpenAICompatibleProvider(
            name_id="groq",
            base_url="https://api.groq.com/openai/v1",
        )
        cost = provider.estimate_cost("mixtral-8x7b-32768", 1000, 500)
        # 1000 * 0.24/1M + 500 * 0.24/1M = 0.00036
        assert abs(cost - 0.00036) < 0.0001

    def test_estimate_cost_unknown_model(self, provider):
        cost = provider.estimate_cost("unknown-model", 1000, 500)
        # Uses default pricing: 1000 * 0.10/1M + 500 * 0.10/1M = 0.00015
        assert abs(cost - 0.00015) < 0.0001

    def test_estimate_cost_ollama_free(self):
        provider = OpenAICompatibleProvider(
            name_id="ollama",
            base_url="http://localhost:11434/v1",
        )
        cost = provider.estimate_cost("ollama", 10000, 5000)
        assert cost == 0.0

    @pytest.mark.asyncio
    async def test_complete_success(self, provider):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "id": "chatcmpl-123",
            "choices": [
                {
                    "message": {
                        "content": '{"skill_id": "test", "confidence": 0.8}',
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
            },
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await provider.complete(
                model="test-model",
                system_prompt="You are a router.",
                user_message="Route this query.",
                max_tokens=200,
            )

        assert result.text == '{"skill_id": "test", "confidence": 0.8}'
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        assert result.provider == "test_provider"

    @pytest.mark.asyncio
    async def test_complete_empty_choices(self, provider):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [],
            "usage": {"prompt_tokens": 50, "completion_tokens": 0},
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await provider.complete(
                model="test-model",
                system_prompt="test",
                user_message="test",
            )

        assert result.text == ""
