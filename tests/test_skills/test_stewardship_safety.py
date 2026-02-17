"""Tests for stewardship safety constraints.

Verifies that agents cannot modify their own steward through skill or
role evolution proposals, and cannot create steward_note memories.
"""

from __future__ import annotations

import pytest

from src.skills.evolution import FORBIDDEN_FIELDS, validate_skill_proposal
from src.skills.role_evolution import ROLE_FORBIDDEN_FIELDS, validate_role_proposal

# ============================================================
# Skill evolution — steward_user_id forbidden
# ============================================================


class TestSkillEvolutionForbiddenFields:
    def test_steward_user_id_in_forbidden_fields(self):
        """steward_user_id must be in FORBIDDEN_FIELDS."""
        assert "steward_user_id" in FORBIDDEN_FIELDS

    def test_validate_rejects_steward_user_id(self):
        """validate_skill_proposal should reject steward_user_id changes."""
        ok, err = validate_skill_proposal(
            {"steward_user_id": "U_ATTACKER", "name": "test"},
            is_new=False,
        )
        assert ok is False
        assert "steward_user_id" in err

    def test_validate_rejects_steward_user_id_on_new_skill(self):
        """Even new skills cannot set steward_user_id."""
        ok, err = validate_skill_proposal(
            {
                "steward_user_id": "U_ATTACKER",
                "name": "test",
                "description": "test",
                "category": "analysis",
                "system_supplement": "x",
                "prompt_template": "x",
                "output_format": "x",
                "business_guidance": "x",
            },
            is_new=True,
        )
        assert ok is False
        assert "steward_user_id" in err


# ============================================================
# Role evolution — steward_user_id and steward forbidden
# ============================================================


class TestRoleEvolutionForbiddenFields:
    def test_steward_user_id_in_role_forbidden_fields(self):
        """steward_user_id must be in ROLE_FORBIDDEN_FIELDS."""
        assert "steward_user_id" in ROLE_FORBIDDEN_FIELDS

    def test_steward_in_role_forbidden_fields(self):
        """steward (dataclass field name) must be in ROLE_FORBIDDEN_FIELDS."""
        assert "steward" in ROLE_FORBIDDEN_FIELDS

    def test_validate_rejects_steward_user_id(self):
        """validate_role_proposal should reject steward_user_id changes."""
        ok, err = validate_role_proposal(
            {"steward_user_id": "U_ATTACKER", "name": "test"},
            is_new=False,
        )
        assert ok is False
        assert "steward_user_id" in err

    def test_validate_rejects_steward_field(self):
        """validate_role_proposal should reject steward changes."""
        ok, err = validate_role_proposal(
            {"steward": "U_ATTACKER", "name": "test"},
            is_new=False,
        )
        assert ok is False
        assert "steward" in err


# ============================================================
# MCP save_memory rejects steward_note from agents
# ============================================================


class TestSaveMemoryRejectsStewardNote:
    @pytest.mark.asyncio
    async def test_agent_cannot_create_steward_note(self):
        """The save_memory MCP tool handler rejects steward_note type."""
        from src.mcp_servers.memory import _memory_context_var
        from src.mcp_servers.memory import save_memory as save_memory_handler

        # Set context so the tool thinks it's in a conversation
        _memory_context_var.set(
            {
                "role_id": "test_role",
                "department_id": "test_dept",
                "user_id": "test_user",
            }
        )

        try:
            result = await save_memory_handler(
                {
                    "memory_type": "steward_note",
                    "title": "My steward note",
                    "content": "I want to be my own steward.",
                }
            )

            # Should return an error response
            result_str = str(result)
            assert "steward" in result_str.lower() or "error" in result_str.lower()
        finally:
            _memory_context_var.set(None)
