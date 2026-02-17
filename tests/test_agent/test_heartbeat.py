"""Tests for proactive heartbeat check-ins (SideraAgent.run_heartbeat_turn).

Verifies that:
- HeartbeatResult is returned with correct fields
- System prompt includes HEARTBEAT_SUPPLEMENT
- Model resolution follows priority chain
- Manager roles get delegation tools
- Findings detection works correctly
- build_heartbeat_prompt formats correctly
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from src.agent.core import HeartbeatResult, SideraAgent
from src.agent.prompts import HEARTBEAT_SUPPLEMENT, build_heartbeat_prompt


@dataclass
class FakeAgentResult:
    """Mimics the result from run_agent_loop."""

    text: str = "All clear — nothing unusual."
    cost: dict = None  # type: ignore
    tool_calls: list = None  # type: ignore
    stop_reason: str = "end_turn"

    def __post_init__(self):
        if self.cost is None:
            self.cost = {"total_cost_usd": 0.02, "model": "haiku", "tool_calls": 3}
        if self.tool_calls is None:
            self.tool_calls = []


# =====================================================================
# HeartbeatResult
# =====================================================================


class TestHeartbeatResult:
    def test_default_values(self):
        result = HeartbeatResult(role_id="test", output_text="hello")
        assert result.role_id == "test"
        assert result.output_text == "hello"
        assert result.cost == {}
        assert result.session_id == ""
        assert result.tool_calls_used == 0
        assert result.has_findings is False

    def test_with_findings(self):
        result = HeartbeatResult(
            role_id="test",
            output_text="Found anomaly",
            has_findings=True,
            tool_calls_used=5,
        )
        assert result.has_findings is True
        assert result.tool_calls_used == 5


# =====================================================================
# build_heartbeat_prompt
# =====================================================================


class TestBuildHeartbeatPrompt:
    def test_basic_prompt(self):
        prompt = build_heartbeat_prompt(role_name="Head of IT")
        assert "Head of IT" in prompt
        assert "Proactive Check-In" in prompt
        assert "investigation" in prompt.lower()

    def test_with_messages_summary(self):
        prompt = build_heartbeat_prompt(
            role_name="Head of IT",
            pending_messages_summary="You have 2 unread messages.",
        )
        assert "2 unread messages" in prompt

    def test_without_messages_summary(self):
        prompt = build_heartbeat_prompt(role_name="Sysadmin")
        assert "unread" not in prompt


# =====================================================================
# SideraAgent.run_heartbeat_turn
# =====================================================================


class TestRunHeartbeatTurn:
    @pytest.fixture
    def agent(self):
        with patch("src.agent.core.get_global_registry") as mock_reg:
            mock_reg.return_value.get_tool_names.return_value = ["test_tool"]
            mock_reg.return_value.__len__ = lambda self: 1
            agent = SideraAgent(model_override="test-model")
        return agent

    @pytest.mark.asyncio
    async def test_returns_heartbeat_result(self, agent):
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(),
        ):
            result = await agent.run_heartbeat_turn(
                role_id="head_of_it",
                role_context="You are the Head of IT.",
            )
        assert isinstance(result, HeartbeatResult)
        assert result.role_id == "head_of_it"

    @pytest.mark.asyncio
    async def test_all_clear_no_findings(self, agent):
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(text="All clear — nothing unusual."),
        ):
            result = await agent.run_heartbeat_turn(
                role_id="head_of_it",
                role_context="",
            )
        assert result.has_findings is False

    @pytest.mark.asyncio
    async def test_findings_detected(self, agent):
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(
                text="I detected a cost spike of 3x in the last hour.",
            ),
        ):
            result = await agent.run_heartbeat_turn(
                role_id="head_of_it",
                role_context="",
            )
        assert result.has_findings is True

    @pytest.mark.asyncio
    async def test_system_prompt_includes_heartbeat_supplement(self, agent):
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(),
        ) as mock_loop:
            await agent.run_heartbeat_turn(
                role_id="head_of_it",
                role_context="Role context here",
            )
        call_kwargs = mock_loop.call_args
        system_prompt = call_kwargs.kwargs.get(
            "system_prompt", call_kwargs.args[0] if call_kwargs.args else ""
        )
        assert "proactive check-in" in system_prompt.lower()

    @pytest.mark.asyncio
    async def test_role_context_in_system_prompt(self, agent):
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(),
        ) as mock_loop:
            await agent.run_heartbeat_turn(
                role_id="test",
                role_context="Custom role context for testing",
            )
        call_kwargs = mock_loop.call_args
        system_prompt = call_kwargs.kwargs.get(
            "system_prompt", call_kwargs.args[0] if call_kwargs.args else ""
        )
        assert "Custom role context for testing" in system_prompt

    @pytest.mark.asyncio
    async def test_model_resolution_heartbeat_model(self, agent):
        """heartbeat_model param takes priority."""
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(),
        ) as mock_loop:
            await agent.run_heartbeat_turn(
                role_id="test",
                role_context="",
                heartbeat_model="custom-haiku-model",
            )
        call_kwargs = mock_loop.call_args
        model = call_kwargs.kwargs.get("model", "")
        assert model == "custom-haiku-model"

    @pytest.mark.asyncio
    async def test_model_resolution_falls_back_to_model_override(self, agent):
        """When heartbeat_model is empty, falls back to agent's model_override."""
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(),
        ) as mock_loop:
            await agent.run_heartbeat_turn(
                role_id="test",
                role_context="",
                heartbeat_model="",
            )
        call_kwargs = mock_loop.call_args
        model = call_kwargs.kwargs.get("model", "")
        assert model == "test-model"  # The model_override set on the agent

    @pytest.mark.asyncio
    async def test_manager_gets_delegation_tools(self, agent):
        fake_tools = [
            {"name": "get_system_health"},
            {"name": "delegate_to_role"},
            {"name": "consult_peer"},
        ]
        with (
            patch.object(
                agent,
                "_get_tools",
                return_value=fake_tools,
            ),
            patch(
                "src.agent.core.run_agent_loop",
                new_callable=AsyncMock,
                return_value=FakeAgentResult(),
            ) as mock_loop,
        ):
            await agent.run_heartbeat_turn(
                role_id="head_of_marketing",
                role_context="",
                is_manager=True,
            )
        call_kwargs = mock_loop.call_args
        tools = call_kwargs.kwargs.get("tools", [])
        tool_names = [t["name"] for t in tools]
        assert "delegate_to_role" in tool_names
        assert "consult_peer" in tool_names

    @pytest.mark.asyncio
    async def test_non_manager_excludes_delegation_tools(self, agent):
        fake_tools = [
            {"name": "get_system_health"},
            {"name": "delegate_to_role"},
            {"name": "consult_peer"},
        ]
        with (
            patch.object(
                agent,
                "_get_tools",
                return_value=fake_tools,
            ),
            patch(
                "src.agent.core.run_agent_loop",
                new_callable=AsyncMock,
                return_value=FakeAgentResult(),
            ) as mock_loop,
        ):
            await agent.run_heartbeat_turn(
                role_id="sysadmin",
                role_context="",
                is_manager=False,
            )
        call_kwargs = mock_loop.call_args
        tools = call_kwargs.kwargs.get("tools", [])
        tool_names = [t["name"] for t in tools]
        assert "delegate_to_role" not in tool_names
        assert "consult_peer" not in tool_names

    @pytest.mark.asyncio
    async def test_cost_tracked(self, agent):
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(
                cost={"total_cost_usd": 0.08, "tool_calls": 7},
            ),
        ):
            result = await agent.run_heartbeat_turn(
                role_id="test",
                role_context="",
            )
        assert result.cost["total_cost_usd"] == 0.08
        assert result.tool_calls_used == 7

    @pytest.mark.asyncio
    async def test_error_propagated(self, agent):
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            side_effect=RuntimeError("API failure"),
        ):
            with pytest.raises(RuntimeError, match="API failure"):
                await agent.run_heartbeat_turn(
                    role_id="test",
                    role_context="",
                )

    @pytest.mark.asyncio
    async def test_nothing_to_report_no_findings(self, agent):
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(text="Nothing to report."),
        ):
            result = await agent.run_heartbeat_turn(
                role_id="test",
                role_context="",
            )
        assert result.has_findings is False

    @pytest.mark.asyncio
    async def test_no_issues_no_findings(self, agent):
        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=FakeAgentResult(text="Checked everything. No issues found."),
        ):
            result = await agent.run_heartbeat_turn(
                role_id="test",
                role_context="",
            )
        assert result.has_findings is False


# =====================================================================
# HEARTBEAT_SUPPLEMENT content
# =====================================================================


class TestHeartbeatSupplement:
    def test_contains_key_instructions(self):
        assert "proactive check-in" in HEARTBEAT_SUPPLEMENT.lower()
        assert "investigate" in HEARTBEAT_SUPPLEMENT.lower()
        assert "memory" in HEARTBEAT_SUPPLEMENT.lower()
        assert "concise" in HEARTBEAT_SUPPLEMENT.lower()

    def test_mentions_messaging(self):
        assert "send_message_to_role" in HEARTBEAT_SUPPLEMENT
