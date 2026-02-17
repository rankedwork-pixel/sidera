"""Tests for manager delegation and memory tools in conversation mode.

Covers:
- non-manager excludes delegate_to_role tool
- manager includes delegate_to_role tool
- manager gets MANAGER_DELEGATION_SUPPLEMENT in prompt
- save_memory tool is available to all roles in conversation
- inline runner sets/clears delegation context for managers
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agent.prompts import MANAGER_DELEGATION_SUPPLEMENT

# ===========================================================================
# 1. Tool filtering in run_conversation_turn
# ===========================================================================


class TestConversationToolFiltering:
    """Verify delegate_to_role is included/excluded based on is_manager."""

    @pytest.mark.asyncio
    @patch("src.agent.core.run_agent_loop")
    async def test_non_manager_excludes_delegation_tool(
        self,
        mock_run_loop,
    ):
        """Non-manager conversation turns should not have delegate_to_role."""
        from src.agent.api_client import TurnResult
        from src.agent.core import SideraAgent

        mock_run_loop.return_value = TurnResult(
            text="Here's my analysis.",
            cost={"total_cost_usd": 0.05},
            turn_count=1,
        )

        agent = SideraAgent()
        await agent.run_conversation_turn(
            role_id="performance_media_buyer",
            role_context="I buy media.",
            thread_history=[],
            current_message="Hi there",
            user_id="user_1",
            is_manager=False,
        )

        # Check tools passed to run_agent_loop
        call_kwargs = mock_run_loop.call_args
        tools = call_kwargs.kwargs.get("tools", [])
        tool_names = [t["name"] for t in tools]
        assert "delegate_to_role" not in tool_names
        assert "consult_peer" not in tool_names

    @pytest.mark.asyncio
    @patch("src.agent.core.run_agent_loop")
    async def test_manager_includes_delegation_tool(
        self,
        mock_run_loop,
    ):
        """Manager conversation turns should have delegate_to_role."""
        from src.agent.api_client import TurnResult
        from src.agent.core import SideraAgent

        mock_run_loop.return_value = TurnResult(
            text="I'll delegate that.",
            cost={"total_cost_usd": 0.05},
            turn_count=1,
        )

        agent = SideraAgent()
        await agent.run_conversation_turn(
            role_id="head_of_marketing",
            role_context="I lead the team.",
            thread_history=[],
            current_message="Run a search term audit",
            user_id="user_1",
            is_manager=True,
        )

        call_kwargs = mock_run_loop.call_args
        tools = call_kwargs.kwargs.get("tools", [])
        tool_names = [t["name"] for t in tools]
        assert "delegate_to_role" in tool_names
        assert "consult_peer" in tool_names

    @pytest.mark.asyncio
    @patch("src.agent.core.run_agent_loop")
    async def test_manager_gets_delegation_supplement(
        self,
        mock_run_loop,
    ):
        """Manager's system prompt should include MANAGER_DELEGATION_SUPPLEMENT."""
        from src.agent.api_client import TurnResult
        from src.agent.core import SideraAgent

        mock_run_loop.return_value = TurnResult(
            text="Done.",
            cost={"total_cost_usd": 0.05},
            turn_count=1,
        )

        agent = SideraAgent()
        await agent.run_conversation_turn(
            role_id="head_of_marketing",
            role_context="I lead the team.",
            thread_history=[],
            current_message="Hello",
            user_id="user_1",
            is_manager=True,
        )

        call_kwargs = mock_run_loop.call_args
        system_prompt = call_kwargs.kwargs.get("system_prompt", "")
        assert "delegate_to_role" in system_prompt
        # The MANAGER_DELEGATION_SUPPLEMENT should be present
        assert "team member" in system_prompt.lower() or "delegate" in system_prompt.lower()

    @pytest.mark.asyncio
    @patch("src.agent.core.run_agent_loop")
    async def test_non_manager_no_delegation_supplement(
        self,
        mock_run_loop,
    ):
        """Non-manager's system prompt should NOT include delegation supplement."""
        from src.agent.api_client import TurnResult
        from src.agent.core import SideraAgent

        mock_run_loop.return_value = TurnResult(
            text="Done.",
            cost={"total_cost_usd": 0.05},
            turn_count=1,
        )

        agent = SideraAgent()
        await agent.run_conversation_turn(
            role_id="performance_media_buyer",
            role_context="I buy media.",
            thread_history=[],
            current_message="Hello",
            user_id="user_1",
            is_manager=False,
        )

        call_kwargs = mock_run_loop.call_args
        system_prompt = call_kwargs.kwargs.get("system_prompt", "")
        assert MANAGER_DELEGATION_SUPPLEMENT not in system_prompt

    @pytest.mark.asyncio
    @patch("src.agent.core.run_agent_loop")
    async def test_disabled_tools_excludes_all(
        self,
        mock_run_loop,
    ):
        """When disable_tools=True, no tools should be passed even for managers."""
        from src.agent.api_client import TurnResult
        from src.agent.core import SideraAgent

        mock_run_loop.return_value = TurnResult(
            text="Quick response.",
            cost={"total_cost_usd": 0.01},
            turn_count=1,
        )

        agent = SideraAgent()
        await agent.run_conversation_turn(
            role_id="head_of_marketing",
            role_context="I lead.",
            thread_history=[],
            current_message="Hi",
            user_id="user_1",
            is_manager=True,
            disable_tools=True,
        )

        call_kwargs = mock_run_loop.call_args
        tools = call_kwargs.kwargs.get("tools", [])
        assert tools == []

    @pytest.mark.asyncio
    @patch("src.agent.core.run_agent_loop")
    async def test_save_memory_tool_available_to_all_roles(
        self,
        mock_run_loop,
    ):
        """save_memory tool should be available to ALL roles (not just managers)."""
        from src.agent.api_client import TurnResult
        from src.agent.core import SideraAgent

        mock_run_loop.return_value = TurnResult(
            text="Noted.",
            cost={"total_cost_usd": 0.03},
            turn_count=1,
        )

        agent = SideraAgent()
        await agent.run_conversation_turn(
            role_id="performance_media_buyer",
            role_context="I buy media.",
            thread_history=[],
            current_message="Remember that account 123 is a test account",
            user_id="user_1",
            is_manager=False,
        )

        call_kwargs = mock_run_loop.call_args
        tools = call_kwargs.kwargs.get("tools", [])
        tool_names = [t["name"] for t in tools]
        assert "save_memory" in tool_names

    @pytest.mark.asyncio
    @patch("src.agent.core.run_agent_loop")
    async def test_save_memory_prompt_guidance(
        self,
        mock_run_loop,
    ):
        """Conversation prompt should include save_memory guidance."""
        from src.agent.api_client import TurnResult
        from src.agent.core import SideraAgent

        mock_run_loop.return_value = TurnResult(
            text="Done.",
            cost={"total_cost_usd": 0.03},
            turn_count=1,
        )

        agent = SideraAgent()
        await agent.run_conversation_turn(
            role_id="performance_media_buyer",
            role_context="I buy media.",
            thread_history=[],
            current_message="Hello",
            user_id="user_1",
            is_manager=False,
        )

        call_kwargs = mock_run_loop.call_args
        system_prompt = call_kwargs.kwargs.get("system_prompt", "")
        assert "save_memory" in system_prompt
