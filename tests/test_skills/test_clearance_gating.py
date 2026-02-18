"""Tests for skill-level clearance gating.

Covers:
- SkillExecutor blocks skills when user clearance is insufficient
- SkillExecutor allows skills when clearance is sufficient
- Default public skills have no gate
- SkillDefinition min_clearance field loaded from YAML
- RoleDefinition clearance_level field loaded from YAML
- Validation of clearance values in schema
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.skills.schema import (
    RoleDefinition,
    SkillDefinition,
    validate_role,
    validate_skill,
)

# ============================================================
# Helpers
# ============================================================


def _make_skill(**overrides) -> SkillDefinition:
    defaults = {
        "id": "test_skill",
        "name": "Test Skill",
        "version": "1.0",
        "description": "A test skill",
        "category": "analysis",
        "platforms": ("google_ads",),
        "tags": ("test",),
        "tools_required": ("get_google_ads_performance",),
        "model": "sonnet",
        "max_turns": 1,
        "prompt_template": "Do the thing",
        "output_format": "markdown",
        "business_guidance": "",
        "system_supplement": "",
        "requires_approval": False,
        "min_clearance": "public",
    }
    defaults.update(overrides)
    return SkillDefinition(**defaults)


def _make_role(**overrides) -> RoleDefinition:
    defaults = {
        "id": "test_role",
        "name": "Test Role",
        "department_id": "test_dept",
        "description": "A test role",
        "persona": "You are a test role.",
        "connectors": ("google_ads",),
        "briefing_skills": ("test_skill",),
        "clearance_level": "internal",
    }
    defaults.update(overrides)
    return RoleDefinition(**defaults)


# ============================================================
# SkillDefinition clearance field
# ============================================================


class TestSkillClearanceField:
    def test_default_is_public(self):
        skill = _make_skill()
        assert skill.min_clearance == "public"

    def test_can_set_confidential(self):
        skill = _make_skill(min_clearance="confidential")
        assert skill.min_clearance == "confidential"

    def test_can_set_restricted(self):
        skill = _make_skill(min_clearance="restricted")
        assert skill.min_clearance == "restricted"


# ============================================================
# RoleDefinition clearance field
# ============================================================


class TestRoleClearanceField:
    def test_default_is_internal(self):
        role = _make_role()
        assert role.clearance_level == "internal"

    def test_can_set_confidential(self):
        role = _make_role(clearance_level="confidential")
        assert role.clearance_level == "confidential"

    def test_can_set_restricted(self):
        role = _make_role(clearance_level="restricted")
        assert role.clearance_level == "restricted"


# ============================================================
# Validation
# ============================================================


class TestClearanceValidation:
    def test_valid_skill_clearance_passes(self):
        skill = _make_skill(min_clearance="confidential")
        errors = validate_skill(skill)
        # Should have no clearance-related error
        clearance_errors = [e for e in errors if "clearance" in e.lower()]
        assert clearance_errors == []

    def test_invalid_skill_clearance_fails(self):
        skill = _make_skill(min_clearance="top_secret")
        errors = validate_skill(skill)
        clearance_errors = [e for e in errors if "clearance" in e.lower()]
        assert len(clearance_errors) > 0

    def test_valid_role_clearance_passes(self):
        role = _make_role(clearance_level="restricted")
        errors = validate_role(role)
        clearance_errors = [e for e in errors if "clearance" in e.lower()]
        assert clearance_errors == []

    def test_invalid_role_clearance_fails(self):
        role = _make_role(clearance_level="top_secret")
        errors = validate_role(role)
        clearance_errors = [e for e in errors if "clearance" in e.lower()]
        assert len(clearance_errors) > 0


# ============================================================
# Skill-level clearance gating in executor
# ============================================================


class TestSkillClearanceGating:
    @pytest.mark.asyncio
    async def test_skill_blocked_for_low_clearance(self):
        """A public-clearance user can't run a confidential skill."""
        from src.skills.executor import SkillExecutor

        skill = _make_skill(min_clearance="confidential")

        mock_agent = AsyncMock()
        registry = MagicMock()
        registry.get.return_value = skill

        executor = SkillExecutor(agent=mock_agent, registry=registry)

        result = await executor.execute(
            skill_id="test_skill",
            user_id="user1",
            accounts=[],
            user_clearance="public",
        )
        assert "CLEARANCE DENIED" in result.output_text

    @pytest.mark.asyncio
    async def test_skill_allowed_for_matching_clearance(self):
        """A confidential-clearance user can run a confidential skill."""
        from src.skills.executor import SkillExecutor

        skill = _make_skill(min_clearance="confidential")

        mock_agent = AsyncMock()
        mock_agent.run_skill.return_value = MagicMock(
            output_text="result",
            recommendations=[],
            cost={"total_cost": 0.01},
            session_id="s1",
        )

        registry = MagicMock()
        registry.get.return_value = skill

        executor = SkillExecutor(agent=mock_agent, registry=registry)

        result = await executor.execute(
            skill_id="test_skill",
            user_id="user1",
            accounts=[{"id": "acc1"}],
            user_clearance="confidential",
        )
        assert "CLEARANCE DENIED" not in result.output_text

    @pytest.mark.asyncio
    async def test_skill_default_public_no_gate(self):
        """A public skill (default) is accessible to everyone."""
        from src.skills.executor import SkillExecutor

        skill = _make_skill(min_clearance="public")

        mock_agent = AsyncMock()
        mock_agent.run_skill.return_value = MagicMock(
            output_text="result",
            recommendations=[],
            cost={"total_cost": 0.01},
            session_id="s1",
        )

        registry = MagicMock()
        registry.get.return_value = skill

        executor = SkillExecutor(agent=mock_agent, registry=registry)

        result = await executor.execute(
            skill_id="test_skill",
            user_id="user1",
            accounts=[{"id": "acc1"}],
            user_clearance="public",
        )
        assert "CLEARANCE DENIED" not in result.output_text

    @pytest.mark.asyncio
    async def test_skill_allowed_without_clearance(self):
        """If user_clearance is empty (not provided), gating is skipped."""
        from src.skills.executor import SkillExecutor

        skill = _make_skill(min_clearance="confidential")

        mock_agent = AsyncMock()
        mock_agent.run_skill.return_value = MagicMock(
            output_text="result",
            recommendations=[],
            cost={"total_cost": 0.01},
            session_id="s1",
        )

        registry = MagicMock()
        registry.get.return_value = skill

        executor = SkillExecutor(agent=mock_agent, registry=registry)

        result = await executor.execute(
            skill_id="test_skill",
            user_id="user1",
            accounts=[{"id": "acc1"}],
            user_clearance="",
        )
        # No clearance = autonomous run, gating skipped
        assert "CLEARANCE DENIED" not in result.output_text


# ============================================================
# YAML loading includes clearance fields
# ============================================================


class TestYAMLClearanceLoading:
    """Test that all YAML skills/roles in the library have valid clearance."""

    def test_all_skills_have_valid_clearance(self):
        from src.skills.registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_all()

        valid = {"public", "internal", "confidential", "restricted"}
        for skill_id, skill in registry._skills.items():
            assert skill.min_clearance in valid, (
                f"Skill {skill_id} has invalid min_clearance={skill.min_clearance}"
            )

    def test_all_roles_have_valid_clearance(self):
        from src.skills.registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_all()

        valid = {"public", "internal", "confidential", "restricted"}
        for role_id, role in registry._roles.items():
            assert role.clearance_level in valid, (
                f"Role {role_id} has invalid clearance_level={role.clearance_level}"
            )

    def test_competitor_benchmark_is_confidential(self):
        from src.skills.registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_all()

        skill = registry.get("competitor_benchmark")
        assert skill is not None
        assert skill.min_clearance == "confidential"

    def test_head_of_marketing_is_confidential(self):
        from src.skills.registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_all()

        role = registry.get_role("head_of_marketing")
        assert role is not None
        assert role.clearance_level == "confidential"

    def test_head_of_it_is_internal(self):
        from src.skills.registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_all()

        role = registry.get_role("head_of_it")
        assert role is not None
        assert role.clearance_level == "internal"

    def test_system_health_check_is_public(self):
        """Health check should be accessible to everyone (default public)."""
        from src.skills.registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_all()

        skill = registry.get("system_health_check")
        assert skill is not None
        assert skill.min_clearance == "public"
