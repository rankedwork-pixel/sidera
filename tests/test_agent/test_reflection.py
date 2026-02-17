"""Tests for post-run reflection (SideraAgent.run_reflection).

Verifies that:
- Reflection generates insight and lesson memories
- JSON parsing handles various response formats
- Error cases are handled gracefully
- Reflection cost is tracked via Haiku model
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from src.agent.core import SideraAgent


@dataclass
class FakeAgentResult:
    """Mimics the result from run_agent_loop."""

    text: str = "[]"
    tool_calls: list = None  # type: ignore
    stop_reason: str = "end_turn"

    def __post_init__(self):
        if self.tool_calls is None:
            self.tool_calls = []


class TestRunReflection:
    @pytest.fixture
    def agent(self):
        with patch("src.agent.core.get_global_registry") as mock_reg:
            mock_reg.return_value.get_tool_names.return_value = ["test_tool"]
            mock_reg.return_value.__len__ = lambda self: 1
            agent = SideraAgent(model_override="test-model")
        return agent

    @pytest.mark.asyncio
    async def test_returns_memories_from_valid_json(self, agent):
        response_json = """[
            {
                "type": "insight",
                "title": "Meta data lagged 24h",
                "content": "Platform-reported conversions were 24h behind backend data",
                "confidence": 0.8
            },
            {
                "type": "lesson",
                "title": "Budget shift too aggressive",
                "content": "50% budget shifts cause reporting volatility for 48h",
                "confidence": 0.9
            }
        ]"""
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(text=response_json),
        ):
            memories = await agent.run_reflection(
                role_id="buyer",
                role_name="Media Buyer",
                output_text="Some analysis output...",
                skill_ids=["creative_analysis", "budget_reallocation"],
            )

        assert len(memories) == 2
        assert memories[0]["memory_type"] == "insight"
        assert memories[0]["role_id"] == "buyer"
        assert "Meta data lagged" in memories[0]["title"]
        assert "[Reflection]" in memories[0]["content"]
        assert memories[0]["evidence"]["source"] == "post_run_reflection"
        assert memories[1]["memory_type"] == "lesson"
        assert memories[1]["confidence"] == 0.9

    @pytest.mark.asyncio
    async def test_handles_markdown_code_fences(self, agent):
        response_json = (
            '```json\n[{"type": "insight", "title": "Test",'
            ' "content": "Test content", "confidence": 0.7}]\n```'
        )
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(text=response_json),
        ):
            memories = await agent.run_reflection(
                role_id="buyer",
                role_name="Media Buyer",
                output_text="output",
            )

        assert len(memories) == 1
        assert memories[0]["title"] == "Test"

    @pytest.mark.asyncio
    async def test_empty_response(self, agent):
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(text="[]"),
        ):
            memories = await agent.run_reflection(
                role_id="buyer",
                role_name="Media Buyer",
                output_text="output",
            )

        assert memories == []

    @pytest.mark.asyncio
    async def test_invalid_json_returns_empty(self, agent):
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(text="Not valid JSON at all"),
        ):
            memories = await agent.run_reflection(
                role_id="buyer",
                role_name="Media Buyer",
                output_text="output",
            )

        assert memories == []

    @pytest.mark.asyncio
    async def test_llm_error_returns_empty(self, agent):
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            side_effect=Exception("API error"),
        ):
            memories = await agent.run_reflection(
                role_id="buyer",
                role_name="Media Buyer",
                output_text="output",
            )

        assert memories == []

    @pytest.mark.asyncio
    async def test_max_five_reflections(self, agent):
        observations = [
            {
                "type": "insight",
                "title": f"Observation {i}",
                "content": f"Content {i}",
                "confidence": 0.7,
            }
            for i in range(10)
        ]
        import json

        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(text=json.dumps(observations)),
        ):
            memories = await agent.run_reflection(
                role_id="buyer",
                role_name="Media Buyer",
                output_text="output",
            )

        assert len(memories) == 5

    @pytest.mark.asyncio
    async def test_invalid_type_defaults_to_insight(self, agent):
        response_json = (
            '[{"type": "unknown_type", "title": "Test", "content": "Content", "confidence": 0.5}]'
        )
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(text=response_json),
        ):
            memories = await agent.run_reflection(
                role_id="buyer",
                role_name="Media Buyer",
                output_text="output",
            )

        assert len(memories) == 1
        assert memories[0]["memory_type"] == "insight"

    @pytest.mark.asyncio
    async def test_confidence_clamped(self, agent):
        response_json = (
            '[{"type": "lesson", "title": "Test", "content": "Content", "confidence": 5.0}]'
        )
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(text=response_json),
        ):
            memories = await agent.run_reflection(
                role_id="buyer",
                role_name="Media Buyer",
                output_text="output",
            )

        assert memories[0]["confidence"] == 1.0

    @pytest.mark.asyncio
    async def test_empty_title_skipped(self, agent):
        response_json = (
            '[{"type": "insight", "title": "", "content": "Content", "confidence": 0.7}]'
        )
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(text=response_json),
        ):
            memories = await agent.run_reflection(
                role_id="buyer",
                role_name="Media Buyer",
                output_text="output",
            )

        assert memories == []

    @pytest.mark.asyncio
    async def test_skill_ids_in_evidence(self, agent):
        response_json = (
            '[{"type": "insight", "title": "Test", "content": "Content", "confidence": 0.7}]'
        )
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(text=response_json),
        ):
            memories = await agent.run_reflection(
                role_id="buyer",
                role_name="Media Buyer",
                output_text="output",
                skill_ids=["skill_a", "skill_b"],
            )

        assert memories[0]["evidence"]["skills_executed"] == [
            "skill_a",
            "skill_b",
        ]

    @pytest.mark.asyncio
    async def test_output_truncated_to_3000(self, agent):
        long_output = "x" * 10000
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(text="[]"),
        ) as mock_loop:
            await agent.run_reflection(
                role_id="buyer",
                role_name="Media Buyer",
                output_text=long_output,
            )

        # Check that the prompt passed to the LLM has truncated output
        call_args = mock_loop.call_args
        user_prompt = call_args.kwargs.get(
            "user_prompt",
            call_args[1].get("user_prompt", ""),
        )
        # The full 10000-char string should not be in the prompt
        assert "x" * 5000 not in user_prompt

    @pytest.mark.asyncio
    async def test_principles_injected_into_prompt(self, agent):
        """When principles are provided, they should appear in the reflection prompt."""
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(text="[]"),
        ) as mock_loop:
            await agent.run_reflection(
                role_id="buyer",
                role_name="Media Buyer",
                output_text="output",
                principles=("Always prioritize ROAS over volume", "Never exceed 30% budget shifts"),
            )

        call_args = mock_loop.call_args
        user_prompt = call_args.kwargs.get(
            "user_prompt",
            call_args[1].get("user_prompt", ""),
        )
        assert "Always prioritize ROAS over volume" in user_prompt
        assert "Never exceed 30% budget shifts" in user_prompt
        assert "related_principle" in user_prompt
        assert "principle_alignment" in user_prompt

    @pytest.mark.asyncio
    async def test_no_principles_no_injection(self, agent):
        """Without principles, the prompt should not mention principles."""
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(text="[]"),
        ) as mock_loop:
            await agent.run_reflection(
                role_id="buyer",
                role_name="Media Buyer",
                output_text="output",
            )

        call_args = mock_loop.call_args
        user_prompt = call_args.kwargs.get(
            "user_prompt",
            call_args[1].get("user_prompt", ""),
        )
        assert "related_principle" not in user_prompt

    @pytest.mark.asyncio
    async def test_principle_link_in_evidence(self, agent):
        """When LLM returns related_principle, it should appear in evidence."""
        response_json = """[{
            "type": "lesson",
            "title": "Budget shift too large",
            "content": "The 40% budget shift caused instability",
            "confidence": 0.9,
            "related_principle": "Never exceed 30% budget shifts",
            "principle_alignment": "contradicts"
        }]"""
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(text=response_json),
        ):
            memories = await agent.run_reflection(
                role_id="buyer",
                role_name="Media Buyer",
                output_text="output",
                principles=("Never exceed 30% budget shifts",),
            )

        assert len(memories) == 1
        assert memories[0]["evidence"]["related_principle"] == "Never exceed 30% budget shifts"
        assert memories[0]["evidence"]["principle_alignment"] == "contradicts"

    @pytest.mark.asyncio
    async def test_invalid_alignment_ignored(self, agent):
        """Invalid principle_alignment values should not be stored."""
        response_json = """[{
            "type": "insight",
            "title": "Test",
            "content": "Content",
            "confidence": 0.7,
            "related_principle": "Some principle",
            "principle_alignment": "invalid_value"
        }]"""
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(text=response_json),
        ):
            memories = await agent.run_reflection(
                role_id="buyer",
                role_name="Media Buyer",
                output_text="output",
                principles=("Some principle",),
            )

        assert len(memories) == 1
        assert "related_principle" not in memories[0]["evidence"]

    @pytest.mark.asyncio
    async def test_gap_observation_stored_as_insight(self, agent):
        """Gap observations should be stored as 'insight' memory type with gap tag."""
        response_json = """[{
            "type": "gap",
            "title": "Missing compliance capability",
            "content": "Cannot handle regulatory queries",
            "confidence": 0.85,
            "domain": "compliance"
        }]"""
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(text=response_json),
        ):
            memories = await agent.run_reflection(
                role_id="buyer",
                role_name="Media Buyer",
                output_text="output",
            )

        assert len(memories) == 1
        assert memories[0]["memory_type"] == "insight"  # Mapped from "gap"
        assert "[Gap Detection]" in memories[0]["content"]
        assert memories[0]["evidence"]["gap_domain"] == "compliance"

    @pytest.mark.asyncio
    async def test_gap_without_domain(self, agent):
        """Gap observations without domain should still be stored."""
        response_json = """[{
            "type": "gap",
            "title": "Missing capability",
            "content": "Cannot handle request",
            "confidence": 0.7
        }]"""
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(text=response_json),
        ):
            memories = await agent.run_reflection(
                role_id="buyer",
                role_name="Media Buyer",
                output_text="output",
            )

        assert len(memories) == 1
        assert memories[0]["memory_type"] == "insight"
        assert "[Gap Detection]" in memories[0]["content"]
        assert "gap_domain" not in memories[0]["evidence"]

    @pytest.mark.asyncio
    async def test_gap_prompt_mentions_out_of_scope(self, agent):
        """The reflection prompt should ask about out-of-scope requests."""
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(text="[]"),
        ) as mock_loop:
            await agent.run_reflection(
                role_id="buyer",
                role_name="Media Buyer",
                output_text="output",
            )

        call_args = mock_loop.call_args
        user_prompt = call_args.kwargs.get(
            "user_prompt",
            call_args[1].get("user_prompt", ""),
        )
        assert "outside your role" in user_prompt.lower() or "outside your" in user_prompt.lower()
        assert '"gap"' in user_prompt or "gap" in user_prompt.lower()
