"""Tests for agent-to-agent learning (push_learning_to_role MCP tool).

Tests the cross-role learning pipeline:
- push_learning_to_role tool
- learning_channels whitelist validation
- confidence threshold
- push count limits
- contextvars integration
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_servers.messaging import (
    _learning_push_count_var,
    _messaging_context_var,
    clear_messaging_context,
    push_learning_to_role,
    set_messaging_context,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_text(result: dict[str, Any]) -> str:
    """Extract the text from an MCP response dict."""
    return result["content"][0]["text"]


def _is_error(result: dict[str, Any]) -> bool:
    """Check if an MCP response is an error."""
    return result.get("is_error", False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class MockRole:
    id: str
    name: str
    department_id: str
    learning_channels: tuple[str, ...] = ()
    clearance_level: str = "internal"


@pytest.fixture(autouse=True)
def _clean_context():
    """Ensure context is clean before and after each test."""
    clear_messaging_context()
    yield
    clear_messaging_context()


def _make_registry(roles: dict[str, MockRole]) -> MagicMock:
    registry = MagicMock()
    registry.get_role = lambda role_id: roles.get(role_id)
    return registry


# ---------------------------------------------------------------------------
# Tests: push_learning_to_role
# ---------------------------------------------------------------------------


class TestPushLearningToRole:
    """Tests for the push_learning_to_role MCP tool."""

    @pytest.mark.asyncio
    async def test_push_success(self):
        """Successfully push a learning to a whitelisted role."""
        roles = {
            "analyst": MockRole(
                id="analyst",
                name="Analyst",
                department_id="marketing",
            ),
            "buyer": MockRole(
                id="buyer",
                name="Buyer",
                department_id="marketing",
                learning_channels=("analyst",),
            ),
        }
        registry = _make_registry(roles)
        set_messaging_context("analyst", "marketing", registry)

        mock_memory = MagicMock()

        with (
            patch(
                "src.db.session.get_db_session",
            ) as mock_session_ctx,
            patch(
                "src.mcp_servers.messaging._notify_learning_pushed",
                new_callable=AsyncMock,
            ),
        ):
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.db.service.save_memory",
                new_callable=AsyncMock,
                return_value=mock_memory,
            ):
                result = await push_learning_to_role(
                    {
                        "to_role_id": "buyer",
                        "title": "Cost spike detected in Q4",
                        "content": "Meta CPM increased 35% week-over-week",
                        "confidence": 0.8,
                    }
                )

        text = _get_text(result)
        assert "Buyer" in text
        assert not _is_error(result)

    @pytest.mark.asyncio
    async def test_no_context_errors(self):
        """Tool errors when no messaging context is set."""
        result = await push_learning_to_role(
            {
                "to_role_id": "buyer",
                "title": "Test",
                "content": "Test content",
                "confidence": 0.8,
            }
        )

        text = _get_text(result)
        assert "not available" in text.lower()
        assert _is_error(result)

    @pytest.mark.asyncio
    async def test_self_push_blocked(self):
        """Cannot push a learning to yourself."""
        roles = {
            "analyst": MockRole(
                id="analyst",
                name="Analyst",
                department_id="marketing",
                learning_channels=("analyst",),
            ),
        }
        registry = _make_registry(roles)
        set_messaging_context("analyst", "marketing", registry)

        result = await push_learning_to_role(
            {
                "to_role_id": "analyst",
                "title": "Test",
                "content": "Test content",
                "confidence": 0.8,
            }
        )

        text = _get_text(result)
        assert "yourself" in text.lower()
        assert _is_error(result)

    @pytest.mark.asyncio
    async def test_unknown_role_errors(self):
        """Errors when target role doesn't exist."""
        roles = {
            "analyst": MockRole(
                id="analyst",
                name="Analyst",
                department_id="marketing",
            ),
        }
        registry = _make_registry(roles)
        set_messaging_context("analyst", "marketing", registry)

        result = await push_learning_to_role(
            {
                "to_role_id": "nonexistent",
                "title": "Test",
                "content": "Test content",
                "confidence": 0.8,
            }
        )

        text = _get_text(result)
        assert "not found" in text.lower()
        assert _is_error(result)

    @pytest.mark.asyncio
    async def test_not_in_learning_channels_blocked(self):
        """Blocked when sender is not in target's learning_channels."""
        roles = {
            "analyst": MockRole(
                id="analyst",
                name="Analyst",
                department_id="marketing",
            ),
            "buyer": MockRole(
                id="buyer",
                name="Buyer",
                department_id="marketing",
                learning_channels=("strategist",),
            ),
        }
        registry = _make_registry(roles)
        set_messaging_context("analyst", "marketing", registry)

        result = await push_learning_to_role(
            {
                "to_role_id": "buyer",
                "title": "Test",
                "content": "Test content",
                "confidence": 0.8,
            }
        )

        text = _get_text(result)
        assert "does not accept learnings" in text.lower()
        assert _is_error(result)

    @pytest.mark.asyncio
    async def test_empty_learning_channels_blocked(self):
        """Blocked when target has no learning_channels configured."""
        roles = {
            "analyst": MockRole(
                id="analyst",
                name="Analyst",
                department_id="marketing",
            ),
            "buyer": MockRole(
                id="buyer",
                name="Buyer",
                department_id="marketing",
                learning_channels=(),
            ),
        }
        registry = _make_registry(roles)
        set_messaging_context("analyst", "marketing", registry)

        result = await push_learning_to_role(
            {
                "to_role_id": "buyer",
                "title": "Test",
                "content": "Test content",
                "confidence": 0.8,
            }
        )

        text = _get_text(result)
        assert "does not accept learnings" in text.lower()
        assert _is_error(result)

    @pytest.mark.asyncio
    async def test_low_confidence_rejected(self):
        """Rejects learnings below the minimum confidence threshold."""
        roles = {
            "analyst": MockRole(
                id="analyst",
                name="Analyst",
                department_id="marketing",
            ),
        }
        registry = _make_registry(roles)
        set_messaging_context("analyst", "marketing", registry)

        result = await push_learning_to_role(
            {
                "to_role_id": "buyer",
                "title": "Test",
                "content": "Test content",
                "confidence": 0.3,
            }
        )

        text = _get_text(result)
        assert "below" in text.lower() or "threshold" in text.lower()
        assert _is_error(result)

    @pytest.mark.asyncio
    async def test_push_limit_enforced(self):
        """Enforces the per-run push limit."""
        roles = {
            "analyst": MockRole(
                id="analyst",
                name="Analyst",
                department_id="marketing",
            ),
            "buyer": MockRole(
                id="buyer",
                name="Buyer",
                department_id="marketing",
                learning_channels=("analyst",),
            ),
        }
        registry = _make_registry(roles)
        set_messaging_context("analyst", "marketing", registry)

        # Simulate already at limit
        _learning_push_count_var.set(3)

        result = await push_learning_to_role(
            {
                "to_role_id": "buyer",
                "title": "Test",
                "content": "Test content",
                "confidence": 0.8,
            }
        )

        text = _get_text(result)
        assert "maximum" in text.lower()
        assert _is_error(result)

    @pytest.mark.asyncio
    async def test_missing_fields_error(self):
        """Errors when required fields are missing."""
        roles = {
            "analyst": MockRole(
                id="analyst",
                name="Analyst",
                department_id="marketing",
            ),
        }
        registry = _make_registry(roles)
        set_messaging_context("analyst", "marketing", registry)

        result = await push_learning_to_role(
            {
                "to_role_id": "buyer",
                "title": "",
                "content": "",
                "confidence": 0.8,
            }
        )

        text = _get_text(result)
        assert "required" in text.lower()
        assert _is_error(result)


class TestLearningContextVars:
    """Tests for learning push count tracking via contextvars."""

    def test_set_context_resets_counts(self):
        """Setting context resets both message and learning counts."""
        _learning_push_count_var.set(5)
        registry = MagicMock()
        set_messaging_context("role1", "dept1", registry)
        assert _learning_push_count_var.get() == 0

    def test_clear_context_resets_counts(self):
        """Clearing context resets learning count."""
        _learning_push_count_var.set(5)
        clear_messaging_context()
        assert _learning_push_count_var.get() == 0
        assert _messaging_context_var.get() is None


class TestCrossRoleInsightMemoryType:
    """Tests for CROSS_ROLE_INSIGHT in MemoryType enum."""

    def test_enum_value(self):
        from src.models.schema import MemoryType

        assert MemoryType.CROSS_ROLE_INSIGHT == "cross_role_insight"
        assert MemoryType.CROSS_ROLE_INSIGHT.value == "cross_role_insight"

    def test_enum_member_count(self):
        """MemoryType should have 9 members after adding CROSS_ROLE_INSIGHT."""
        from src.models.schema import MemoryType

        assert len(MemoryType) == 9


class TestLearningChannelsOnRole:
    """Tests for the learning_channels field on RoleDefinition."""

    def test_default_empty(self):
        from src.skills.schema import RoleDefinition

        role = RoleDefinition(
            id="test",
            name="Test",
            department_id="dept",
            description="A test role",
        )
        assert role.learning_channels == ()

    def test_populated(self):
        from src.skills.schema import RoleDefinition

        role = RoleDefinition(
            id="test",
            name="Test",
            department_id="dept",
            description="A test role",
            learning_channels=("buyer", "analyst"),
        )
        assert role.learning_channels == ("buyer", "analyst")
        assert "buyer" in role.learning_channels

    def test_yaml_loading(self, tmp_path):
        """learning_channels loads from YAML."""
        from src.skills.schema import load_role_from_yaml

        yaml_content = """
id: test_role
name: Test Role
department_id: marketing
description: A test role
learning_channels:
  - buyer
  - analyst
"""
        yaml_file = tmp_path / "_role.yaml"
        yaml_file.write_text(yaml_content)

        role = load_role_from_yaml(yaml_file)
        assert role.learning_channels == ("buyer", "analyst")

    def test_yaml_loading_empty(self, tmp_path):
        """Missing learning_channels defaults to empty tuple."""
        from src.skills.schema import load_role_from_yaml

        yaml_content = """
id: test_role
name: Test Role
department_id: marketing
description: A test role
"""
        yaml_file = tmp_path / "_role.yaml"
        yaml_file.write_text(yaml_content)

        role = load_role_from_yaml(yaml_file)
        assert role.learning_channels == ()


class TestMemoryInjectionOrder:
    """Tests that cross_role_insight appears in the correct position."""

    def test_compose_memory_context_includes_cross_role_insight(self):
        from src.skills.memory import compose_memory_context

        memories = [
            MagicMock(
                memory_type="cross_role_insight",
                title="Learning from peer",
                content="[From Analyst (analyst)]: CPM rising",
                confidence=0.8,
                source_role_id="analyst",
            ),
        ]

        result = compose_memory_context(memories)
        assert "Learnings from Peers" in result
        assert "CPM rising" in result

    def test_compose_memory_index_includes_cross_role_insight(self):
        from src.skills.memory import compose_memory_index

        memories = [
            MagicMock(
                id=1,
                memory_type="cross_role_insight",
                title="Learning from peer",
                created_at=None,
            ),
        ]

        result = compose_memory_index(memories)
        assert "Learnings from Peers" in result
        assert "[1]" in result

    def test_cross_role_insight_order_before_commitment(self):
        """cross_role_insight should appear after relationship, before commitment."""
        from src.skills.memory import compose_memory_context

        memories = [
            MagicMock(
                memory_type="relationship",
                title="Knows buyer",
                content="Met during Q3 review",
                confidence=0.7,
                source_role_id="buyer",
            ),
            MagicMock(
                memory_type="cross_role_insight",
                title="Learning from peer",
                content="CPA trending up",
                confidence=0.8,
                source_role_id="analyst",
            ),
            MagicMock(
                memory_type="commitment",
                title="Promised to investigate",
                content="Will check budget",
                confidence=0.9,
                source_role_id=None,
            ),
        ]

        result = compose_memory_context(memories)

        assert "Relationship Context" in result
        assert "Learnings from Peers" in result
        assert "Active Commitments" in result

        rel_pos = result.index("Relationship Context")
        peer_pos = result.index("Learnings from Peers")
        commit_pos = result.index("Active Commitments")
        assert rel_pos < peer_pos < commit_pos


class TestReflectionShareWith:
    """Tests for the share_with field in reflection output."""

    @pytest.mark.asyncio
    async def test_reflection_includes_peer_prompt(self):
        """When peer_role_ids provided, the prompt mentions them."""
        from src.agent.core import SideraAgent

        agent = SideraAgent()

        mock_result = MagicMock()
        mock_result.text = "[]"

        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_loop:
            await agent.run_reflection(
                role_id="buyer",
                role_name="Buyer",
                output_text="Some output text",
                peer_role_ids=("analyst", "strategist"),
            )

            call_kwargs = mock_loop.call_args
            user_prompt = call_kwargs.kwargs.get("user_prompt", "")
            assert "analyst" in user_prompt
            assert "strategist" in user_prompt
            assert "share_with" in user_prompt

    @pytest.mark.asyncio
    async def test_reflection_captures_share_with(self):
        """share_with in LLM output is captured in evidence."""
        import json

        from src.agent.core import SideraAgent

        agent = SideraAgent()

        observations = [
            {
                "type": "insight",
                "title": "CPM spike detected",
                "content": "Meta CPMs up 35% this week",
                "confidence": 0.9,
                "share_with": ["analyst", "strategist"],
            }
        ]
        mock_result = MagicMock()
        mock_result.text = json.dumps(observations)

        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            memories = await agent.run_reflection(
                role_id="buyer",
                role_name="Buyer",
                output_text="Some output text",
                peer_role_ids=("analyst", "strategist"),
            )

        assert len(memories) == 1
        evidence = memories[0]["evidence"]
        assert "share_with" in evidence
        assert evidence["share_with"] == ["analyst", "strategist"]

    @pytest.mark.asyncio
    async def test_reflection_filters_invalid_peers(self):
        """share_with only includes valid peer role IDs."""
        import json

        from src.agent.core import SideraAgent

        agent = SideraAgent()

        observations = [
            {
                "type": "insight",
                "title": "Test",
                "content": "Test content",
                "confidence": 0.9,
                "share_with": ["analyst", "unknown_role"],
            }
        ]
        mock_result = MagicMock()
        mock_result.text = json.dumps(observations)

        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            memories = await agent.run_reflection(
                role_id="buyer",
                role_name="Buyer",
                output_text="Some output text",
                peer_role_ids=("analyst",),
            )

        assert len(memories) == 1
        evidence = memories[0]["evidence"]
        assert evidence["share_with"] == ["analyst"]

    @pytest.mark.asyncio
    async def test_reflection_no_peers_no_share_with(self):
        """When no peer_role_ids, share_with is never in evidence."""
        import json

        from src.agent.core import SideraAgent

        agent = SideraAgent()

        observations = [
            {
                "type": "insight",
                "title": "Test",
                "content": "Test content",
                "confidence": 0.9,
                "share_with": ["analyst"],
            }
        ]
        mock_result = MagicMock()
        mock_result.text = json.dumps(observations)

        with patch(
            "src.agent.core.run_agent_loop",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            memories = await agent.run_reflection(
                role_id="buyer",
                role_name="Buyer",
                output_text="Some output text",
                peer_role_ids=(),
            )

        assert len(memories) == 1
        evidence = memories[0]["evidence"]
        assert "share_with" not in evidence


class TestRoleEvolutionForbiddenFields:
    """Tests that learning_channels is in ROLE_FORBIDDEN_FIELDS."""

    def test_learning_channels_forbidden(self):
        from src.skills.role_evolution import ROLE_FORBIDDEN_FIELDS

        assert "learning_channels" in ROLE_FORBIDDEN_FIELDS
