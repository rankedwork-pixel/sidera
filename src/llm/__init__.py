"""LLM provider abstraction layer for Sidera.

Supports hybrid model routing: quality-critical tasks stay on Claude API,
while high-volume structured tasks can route to cheaper OpenAI-compatible
providers (MiniMax, Groq, Together AI) or local models (Ollama, vLLM).

Usage::

    from src.llm import get_provider, TaskType

    provider = get_provider(TaskType.SKILL_ROUTING)
    result = await provider.complete(
        system_prompt="You are a router...",
        user_message="Which skill matches?",
        max_tokens=200,
    )
"""

from src.llm.provider import LLMProvider, LLMResult, TaskType
from src.llm.router import get_provider

__all__ = [
    "LLMProvider",
    "LLMResult",
    "TaskType",
    "get_provider",
]
