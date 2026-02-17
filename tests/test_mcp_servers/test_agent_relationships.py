"""Tests for inter-agent relationships and agent-to-agent clearance.

Covers:
- Messaging saves relationship memory with source_role_id
- Messaging includes sender clearance in metadata
- Delegation saves relationship memories for both sides
- Delegation injects requester clearance into context
- Inter-agent memories loaded into role context
- compose_memory_context formats agent memories distinctly
- compose_message_context shows sender clearance
- save_memory MCP tool accepts relationship type (bug fix test)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ============================================================
# Helpers
# ============================================================


@dataclass
class FakeMemory:
    memory_type: str = "relationship"
    title: str = "test"
    content: str = "test content"
    confidence: float = 0.6
    created_at: datetime = field(
        default_factory=lambda: datetime(2026, 2, 10, tzinfo=timezone.utc),
    )
    source_role_id: str | None = None
    id: int = 1


@dataclass
class FakeMessage:
    id: int = 1
    from_role_id: str = "performance_media_buyer"
    to_role_id: str = "head_of_marketing"
    from_department_id: str = "marketing"
    to_department_id: str = "marketing"
    subject: str = "Budget analysis"
    content: str = "CPA is trending up 15% on Google Ads."
    status: str = "pending"
    created_at: datetime = field(
        default_factory=lambda: datetime(2026, 2, 10, tzinfo=timezone.utc),
    )
    metadata_: dict = field(default_factory=lambda: {"sender_clearance": "internal"})
    reply_to_id: int | None = None


# ============================================================
# Messaging saves relationship memory
# ============================================================


class TestMessagingSavesRelationshipMemory:
    @pytest.mark.asyncio
    async def test_send_message_saves_relationship_memory(self):
        """send_message_to_role should save a relationship memory."""
        from src.mcp_servers.messaging import (
            _message_count_var,
            _messaging_context_var,
            send_message_to_role,
        )

        # Set up context
        mock_registry = MagicMock()
        target_role = MagicMock()
        target_role.name = "Performance Media Buyer"
        target_role.department_id = "marketing"
        mock_registry.get_role.return_value = target_role

        from_role = MagicMock()
        from_role.clearance_level = "confidential"
        mock_registry.get_role.side_effect = lambda rid: (
            from_role if rid == "head_of_marketing" else target_role
        )

        token = _messaging_context_var.set(
            {
                "role_id": "head_of_marketing",
                "department_id": "marketing",
                "registry": mock_registry,
            }
        )
        _message_count_var.set(0)

        try:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            with (
                patch(
                    "src.db.session.get_db_session",
                    return_value=mock_session,
                ),
                patch(
                    "src.db.service.create_role_message",
                    new_callable=AsyncMock,
                    return_value=42,
                ),
                patch(
                    "src.db.service.save_memory",
                    new_callable=AsyncMock,
                ) as mock_save_memory,
                patch(
                    "src.mcp_servers.messaging._notify_message_sent",
                    new_callable=AsyncMock,
                ),
            ):
                await send_message_to_role(
                    {
                        "to_role_id": "performance_media_buyer",
                        "subject": "Budget review",
                        "content": "Please review Q4 budget allocation.",
                    }
                )

                # Verify save_memory was called with source_role_id
                assert mock_save_memory.called
                call_kwargs = mock_save_memory.call_args.kwargs
                assert call_kwargs.get("memory_type") == "relationship"
                assert call_kwargs.get("source_role_id") == "performance_media_buyer"
        finally:
            _messaging_context_var.reset(token)


# ============================================================
# Messaging includes sender clearance in metadata
# ============================================================


class TestMessagingSenderClearance:
    @pytest.mark.asyncio
    async def test_message_metadata_includes_sender_clearance(self):
        """Message metadata should include sender_clearance from role definition."""
        from src.mcp_servers.messaging import (
            _message_count_var,
            _messaging_context_var,
            send_message_to_role,
        )

        mock_registry = MagicMock()
        target_role = MagicMock()
        target_role.name = "Head of IT"
        target_role.department_id = "it"

        from_role = MagicMock()
        from_role.clearance_level = "confidential"

        mock_registry.get_role.side_effect = lambda rid: (
            from_role if rid == "head_of_marketing" else target_role
        )

        token = _messaging_context_var.set(
            {
                "role_id": "head_of_marketing",
                "department_id": "marketing",
                "registry": mock_registry,
            }
        )
        _message_count_var.set(0)

        try:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            with (
                patch(
                    "src.db.session.get_db_session",
                    return_value=mock_session,
                ),
                patch(
                    "src.db.service.create_role_message",
                    new_callable=AsyncMock,
                    return_value=42,
                ) as mock_create,
                patch(
                    "src.db.service.save_memory",
                    new_callable=AsyncMock,
                ),
                patch(
                    "src.mcp_servers.messaging._notify_message_sent",
                    new_callable=AsyncMock,
                ),
            ):
                await send_message_to_role(
                    {
                        "to_role_id": "head_of_it",
                        "subject": "Cost check",
                        "content": "How are costs trending?",
                    }
                )

                call_kwargs = mock_create.call_args.kwargs
                metadata = call_kwargs.get("metadata", {})
                assert metadata.get("sender_clearance") == "confidential"
        finally:
            _messaging_context_var.reset(token)


# ============================================================
# compose_message_context shows sender clearance
# ============================================================


class TestComposeMessageContextClearance:
    def test_shows_sender_clearance(self):
        from src.mcp_servers.messaging import compose_message_context

        msg = FakeMessage(
            metadata_={"sender_clearance": "internal"},
        )
        result = compose_message_context([msg])
        assert "internal" in result.lower()
        assert "clearance" in result.lower()

    def test_defaults_to_internal_when_no_metadata(self):
        from src.mcp_servers.messaging import compose_message_context

        msg = FakeMessage(metadata_=None)
        result = compose_message_context([msg])
        assert "internal" in result.lower()

    def test_empty_messages_returns_empty(self):
        from src.mcp_servers.messaging import compose_message_context

        result = compose_message_context([])
        assert result == ""


# ============================================================
# compose_memory_context formats agent memories
# ============================================================


class TestComposeMemoryContextAgentMemories:
    def test_inter_agent_memory_formatted_with_re_prefix(self):
        """Agent memories (source_role_id set) should be formatted with 'Re:' prefix."""
        from src.skills.memory import compose_memory_context

        mem = FakeMemory(
            memory_type="relationship",
            content="Delegated budget analysis to media buyer",
            source_role_id="performance_media_buyer",
        )
        result = compose_memory_context([mem])
        assert "Re: performance_media_buyer" in result

    def test_human_memory_not_formatted_with_re(self):
        """Non-agent memories (source_role_id=None) should NOT use 'Re:' prefix."""
        from src.skills.memory import compose_memory_context

        mem = FakeMemory(
            memory_type="relationship",
            content="User prefers weekly reports",
            source_role_id=None,
        )
        result = compose_memory_context([mem])
        assert "Re:" not in result

    def test_mixed_memories_formatted_correctly(self):
        from src.skills.memory import compose_memory_context

        agent_mem = FakeMemory(
            memory_type="relationship",
            content="Contacted head_of_it about costs",
            source_role_id="head_of_it",
            id=1,
        )
        human_mem = FakeMemory(
            memory_type="insight",
            content="CPC trending down this quarter",
            source_role_id=None,
            id=2,
        )
        result = compose_memory_context([agent_mem, human_mem])
        assert "Re: head_of_it" in result
        assert "CPC trending" in result


# ============================================================
# _format_memory_line with source_role_id
# ============================================================


class TestFormatMemoryLineInterAgent:
    def test_with_source_role_id(self):
        from src.skills.memory import _format_memory_line

        line = _format_memory_line(
            "Delegated Q4 analysis — thorough response",
            source_role_id="performance_media_buyer",
        )
        assert line.startswith("- Re: performance_media_buyer")
        assert "Delegated Q4" in line

    def test_without_source_role_id(self):
        from src.skills.memory import _format_memory_line

        line = _format_memory_line("Some regular memory content")
        assert "Re:" not in line

    def test_truncates_long_content(self):
        from src.skills.memory import _format_memory_line

        long_content = "x" * 300
        line = _format_memory_line(long_content, source_role_id="test_role")
        # Should truncate to 200 chars
        assert len(line) < 250


# ============================================================
# save_memory MCP tool accepts relationship type (bug fix)
# ============================================================


class TestSaveMemoryToolRelationshipType:
    @pytest.mark.asyncio
    async def test_save_memory_tool_accepts_relationship(self):
        """The save_memory MCP tool should accept 'relationship' as a valid type."""
        from src.mcp_servers.memory import (
            _memory_context_var,
            save_memory,
        )

        token = _memory_context_var.set(
            {
                "role_id": "head_of_it",
                "department_id": "it",
                "user_id": "user1",
            }
        )

        try:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            with (
                patch(
                    "src.db.session.get_db_session",
                    return_value=mock_session,
                ),
                patch(
                    "src.db.service.save_memory",
                    new_callable=AsyncMock,
                    return_value=MagicMock(id=99),
                ),
            ):
                result = await save_memory(
                    {
                        "memory_type": "relationship",
                        "title": "Good rapport with media buyer",
                        "content": "Media buyer responds quickly and thoroughly.",
                    }
                )

                # Should succeed, not return error about invalid type
                response_text = str(result)
                assert "invalid" not in response_text.lower()
                assert "error" not in response_text.lower() or "saved" in response_text.lower()
        finally:
            _memory_context_var.reset(token)

    @pytest.mark.asyncio
    async def test_save_memory_tool_falls_back_for_invalid_type(self):
        """Invalid memory type should fall back to 'insight' (forgiving input)."""
        from src.mcp_servers.memory import (
            _memory_context_var,
            save_memory,
        )

        token = _memory_context_var.set(
            {
                "role_id": "head_of_it",
                "department_id": "it",
                "user_id": "user1",
            }
        )

        try:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            with (
                patch(
                    "src.db.session.get_db_session",
                    return_value=mock_session,
                ),
                patch(
                    "src.db.service.save_memory",
                    new_callable=AsyncMock,
                    return_value=MagicMock(id=100),
                ) as mock_save,
            ):
                result = await save_memory(
                    {
                        "memory_type": "gossip",
                        "title": "Not valid type",
                        "content": "This should fall back to insight.",
                    }
                )

                # Should succeed (not error) — invalid type falls back to "insight"
                response_text = str(result)
                assert "saved" in response_text.lower() or "error" not in response_text.lower()
                # Verify the fallback type was used
                if mock_save.called:
                    call_kwargs = mock_save.call_args.kwargs
                    assert call_kwargs.get("memory_type") == "insight"
        finally:
            _memory_context_var.reset(token)


# ============================================================
# Delegation saves relationship memories
# ============================================================


class TestDelegationRelationshipMemories:
    @pytest.mark.asyncio
    async def test_delegation_saves_memories_for_both_sides(self):
        """After delegation, both manager and sub-role should get relationship memories."""
        from src.mcp_servers.delegation import (
            _delegation_context_var,
            _delegation_count_var,
            delegate_to_role,
        )

        mock_registry = MagicMock()
        manager_role = MagicMock()
        manager_role.manages = ("performance_media_buyer",)
        manager_role.name = "Head of Marketing"
        manager_role.department_id = "marketing"
        manager_role.clearance_level = "confidential"

        sub_role = MagicMock()
        sub_role.id = "performance_media_buyer"
        sub_role.name = "Performance Media Buyer"
        sub_role.department_id = "marketing"
        sub_role.persona = "You are a media buyer."
        sub_role.clearance_level = "internal"
        sub_role.connectors = ("google_ads",)
        sub_role.briefing_skills = ("budget_reallocation",)
        sub_role.principles = ()
        sub_role.context_files = ()

        mock_registry.get_role.side_effect = lambda rid: {
            "head_of_marketing": manager_role,
            "performance_media_buyer": sub_role,
        }.get(rid)
        mock_registry.get_managed_roles.return_value = [sub_role]
        mock_registry.get_department.return_value = MagicMock(context="", context_files=())

        token = _delegation_context_var.set(
            {
                "role_id": "head_of_marketing",
                "department_id": "marketing",
                "registry": mock_registry,
                "user_id": "user1",
                "thread_ts": "t1",
            }
        )
        _delegation_count_var.set(0)

        try:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            mock_agent = AsyncMock()
            mock_agent.run_conversation_turn.return_value = MagicMock(
                response_text="Analysis complete. CPA trending up.",
                cost={"total_cost": 0.05},
            )

            with (
                patch(
                    "src.db.session.get_db_session",
                    return_value=mock_session,
                ),
                patch(
                    "src.db.service.save_memory",
                    new_callable=AsyncMock,
                ) as mock_save_mem,
                patch(
                    "src.agent.core.SideraAgent",
                    return_value=mock_agent,
                ),
                patch(
                    "src.db.service.get_role_memories",
                    new_callable=AsyncMock,
                    return_value=[],
                ),
                patch(
                    "src.db.service.get_superseded_memory_ids",
                    new_callable=AsyncMock,
                    return_value=set(),
                ),
                patch(
                    "src.db.service.get_pending_messages",
                    new_callable=AsyncMock,
                    return_value=[],
                ),
                patch(
                    "src.db.service.get_agent_relationship_memories",
                    new_callable=AsyncMock,
                    return_value=[],
                ),
            ):
                await delegate_to_role(
                    {
                        "role_id": "performance_media_buyer",
                        "task": "Analyze Q4 budget allocation",
                    }
                )

                # Should have saved at least 2 relationship memories
                # (one for manager, one for sub-role)
                save_calls = mock_save_mem.call_args_list
                relationship_calls = [
                    c for c in save_calls if c.kwargs.get("memory_type") == "relationship"
                ]
                assert len(relationship_calls) >= 2, (
                    f"Expected at least 2 relationship memory saves, got {len(relationship_calls)}"
                )
        finally:
            _delegation_context_var.reset(token)


# ============================================================
# Delegation injects requester clearance
# ============================================================


class TestDelegationClearanceInjection:
    @pytest.mark.asyncio
    async def test_delegation_includes_clearance_in_prompt(self):
        """Delegation should inject manager's clearance into sub-role context."""
        from src.mcp_servers.delegation import (
            _delegation_context_var,
            _delegation_count_var,
            delegate_to_role,
        )

        mock_registry = MagicMock()
        manager_role = MagicMock()
        manager_role.manages = ("performance_media_buyer",)
        manager_role.name = "Head of Marketing"
        manager_role.department_id = "marketing"
        manager_role.clearance_level = "confidential"

        sub_role = MagicMock()
        sub_role.id = "performance_media_buyer"
        sub_role.name = "Performance Media Buyer"
        sub_role.department_id = "marketing"
        sub_role.persona = "You are a media buyer."
        sub_role.clearance_level = "internal"
        sub_role.connectors = ("google_ads",)
        sub_role.briefing_skills = ("budget_reallocation",)
        sub_role.principles = ()
        sub_role.context_files = ()

        mock_registry.get_role.side_effect = lambda rid: {
            "head_of_marketing": manager_role,
            "performance_media_buyer": sub_role,
        }.get(rid)
        mock_registry.get_managed_roles.return_value = [sub_role]
        mock_registry.get_department.return_value = MagicMock(context="", context_files=())

        token = _delegation_context_var.set(
            {
                "role_id": "head_of_marketing",
                "department_id": "marketing",
                "registry": mock_registry,
                "user_id": "user1",
                "thread_ts": "t1",
            }
        )
        _delegation_count_var.set(0)

        captured_system_prompt = {}

        async def capture_agent_loop(**kwargs):
            captured_system_prompt["prompt"] = kwargs.get("system_prompt", "")
            return MagicMock(
                text="Done",
                cost={"total_cost_usd": 0.01},
                is_error=False,
                turn_count=1,
            )

        try:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            mock_tool_registry = MagicMock()
            mock_tool_registry.get_tool_definitions.return_value = []

            with (
                patch(
                    "src.db.session.get_db_session",
                    return_value=mock_session,
                ),
                patch(
                    "src.db.service.save_memory",
                    new_callable=AsyncMock,
                ),
                patch(
                    "src.agent.api_client.run_agent_loop",
                    side_effect=capture_agent_loop,
                ),
                patch(
                    "src.agent.tool_registry.get_global_registry",
                    return_value=mock_tool_registry,
                ),
                patch(
                    "src.db.service.get_role_memories",
                    new_callable=AsyncMock,
                    return_value=[],
                ),
                patch(
                    "src.db.service.get_superseded_memory_ids",
                    new_callable=AsyncMock,
                    return_value=set(),
                ),
                patch(
                    "src.db.service.get_pending_messages",
                    new_callable=AsyncMock,
                    return_value=[],
                ),
                patch(
                    "src.db.service.get_agent_relationship_memories",
                    new_callable=AsyncMock,
                    return_value=[],
                ),
            ):
                await delegate_to_role(
                    {
                        "role_id": "performance_media_buyer",
                        "task": "Check budget allocation",
                    }
                )

                # The system prompt should mention the manager's clearance
                prompt = captured_system_prompt.get("prompt", "")
                assert "confidential" in prompt.lower(), (
                    f"Expected 'confidential' in delegation prompt, got: {prompt[:200]}"
                )
        finally:
            _delegation_context_var.reset(token)
