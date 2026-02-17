"""OpenAI-compatible LLM provider for external services.

Supports any provider that implements the OpenAI Chat Completions API:
- MiniMax (minimax-text-01)
- Groq (mixtral-8x7b-32768, llama-3.1-70b)
- Together AI (meta-llama/Llama-3.1-70B-Instruct-Turbo)
- Ollama (local, http://localhost:11434/v1)
- vLLM (local, any OpenAI-compatible endpoint)
- Any other OpenAI-compatible service

Uses ``httpx`` for async HTTP calls to avoid adding ``openai`` SDK
as a dependency.
"""

from __future__ import annotations

import httpx
import structlog

from src.llm.provider import LLMResult

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Pricing (USD per million tokens) — approximate
# ---------------------------------------------------------------------------

# Provider-specific pricing tables.  These are rough approximations;
# actual costs depend on the provider and model variant.
PROVIDER_PRICING: dict[str, dict[str, dict[str, float]]] = {
    # MiniMax — extremely cheap for Haiku-tier tasks
    "minimax-text-01": {"input": 0.01, "output": 0.01},
    # Groq — fast, affordable
    "mixtral-8x7b-32768": {"input": 0.24, "output": 0.24},
    "llama-3.1-70b-versatile": {"input": 0.59, "output": 0.79},
    "llama-3.1-8b-instant": {"input": 0.05, "output": 0.08},
    # Together AI
    "meta-llama/Llama-3.1-70B-Instruct-Turbo": {"input": 0.88, "output": 0.88},
    "meta-llama/Llama-3.1-8B-Instruct-Turbo": {"input": 0.18, "output": 0.18},
    # Ollama / local — free (self-hosted)
    "ollama": {"input": 0.0, "output": 0.0},
}

# Fallback pricing if model not in table — assume very cheap
_DEFAULT_PRICING = {"input": 0.10, "output": 0.10}


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class OpenAICompatibleProvider:
    """Provider for any OpenAI Chat Completions-compatible API.

    Args:
        name_id: Provider identifier (e.g. "groq", "minimax", "ollama").
        base_url: API base URL (e.g. "https://api.groq.com/openai/v1").
        api_key: API key for authentication (empty for local providers).
        default_model: Default model ID if not specified in call.
        timeout: HTTP timeout in seconds.
    """

    def __init__(
        self,
        *,
        name_id: str,
        base_url: str,
        api_key: str = "",
        default_model: str = "",
        timeout: float = 30.0,
    ) -> None:
        self._name_id = name_id
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_model = default_model
        self._timeout = timeout

    @property
    def name(self) -> str:
        return self._name_id

    async def complete(
        self,
        *,
        model: str,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 1024,
    ) -> LLMResult:
        """Make a single-turn call to an OpenAI-compatible API."""
        resolved_model = model or self._default_model

        headers: dict[str, str] = {
            "Content-Type": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        payload = {
            "model": resolved_model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }

        url = f"{self._base_url}/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "openai_compat.http_error",
                provider=self._name_id,
                status=exc.response.status_code,
                body=exc.response.text[:500],
                model=resolved_model,
            )
            raise
        except Exception as exc:
            logger.error(
                "openai_compat.error",
                provider=self._name_id,
                error=str(exc),
                model=resolved_model,
            )
            raise

        # Parse OpenAI-format response
        text = ""
        choices = data.get("choices", [])
        if choices:
            msg = choices[0].get("message", {})
            text = msg.get("content", "")

        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        cost = self.estimate_cost(resolved_model, input_tokens, output_tokens)

        return LLMResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=resolved_model,
            provider=self._name_id,
            cost_usd=cost,
            metadata={"raw_response_id": data.get("id", "")},
        )

    def estimate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        pricing = PROVIDER_PRICING.get(model, _DEFAULT_PRICING)
        return (
            input_tokens * pricing["input"] / 1_000_000
            + output_tokens * pricing["output"] / 1_000_000
        )
