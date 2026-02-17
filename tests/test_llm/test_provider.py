"""Tests for the LLM provider abstraction layer.

Verifies:
- TaskType enum completeness
- EXTERNAL_ELIGIBLE_TASKS correctness
- LLMResult dataclass
- Provider protocol
"""

from __future__ import annotations

from src.llm.provider import (
    EXTERNAL_ELIGIBLE_TASKS,
    LLMProvider,
    LLMResult,
    TaskType,
)


class TestTaskType:
    def test_all_task_types(self):
        """TaskType should have 17 members."""
        assert len(TaskType) == 17

    def test_external_eligible_count(self):
        """8 task types should be external-eligible."""
        assert len(EXTERNAL_ELIGIBLE_TASKS) == 8

    def test_external_eligible_members(self):
        expected = {
            TaskType.SKILL_ROUTING,
            TaskType.ROLE_ROUTING,
            TaskType.MEMORY_EXTRACTION,
            TaskType.REFLECTION,
            TaskType.MEMORY_CONSOLIDATION,
            TaskType.MEMORY_VERSIONING,
            TaskType.FRICTION_DETECTION,
            TaskType.PHASE_COMPRESSION,
        }
        assert EXTERNAL_ELIGIBLE_TASKS == expected

    def test_claude_only_not_in_eligible(self):
        """Quality-critical tasks must NOT be external-eligible."""
        claude_only = {
            TaskType.DATA_COLLECTION,
            TaskType.ANALYSIS,
            TaskType.STRATEGY,
            TaskType.CONVERSATION,
            TaskType.HEARTBEAT,
            TaskType.DELEGATION,
            TaskType.SYNTHESIS,
            TaskType.SKILL_EXECUTION,
            TaskType.GENERAL,
        }
        for task in claude_only:
            assert task not in EXTERNAL_ELIGIBLE_TASKS, f"{task} should not be external-eligible"

    def test_task_type_values_are_strings(self):
        for task in TaskType:
            assert isinstance(task.value, str)


class TestLLMResult:
    def test_default_values(self):
        result = LLMResult()
        assert result.text == ""
        assert result.input_tokens == 0
        assert result.output_tokens == 0
        assert result.model == ""
        assert result.provider == ""
        assert result.cost_usd == 0.0
        assert result.is_fallback is False
        assert result.metadata == {}

    def test_custom_values(self):
        result = LLMResult(
            text="hello",
            input_tokens=100,
            output_tokens=50,
            model="minimax-text-01",
            provider="minimax",
            cost_usd=0.001,
            is_fallback=True,
            metadata={"reason": "test"},
        )
        assert result.text == "hello"
        assert result.provider == "minimax"
        assert result.is_fallback is True
        assert result.metadata["reason"] == "test"


class TestLLMProviderProtocol:
    def test_protocol_is_runtime_checkable(self):
        """LLMProvider should be a runtime-checkable Protocol."""
        from src.llm.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider()
        assert isinstance(provider, LLMProvider)
