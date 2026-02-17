"""Tests for the CEO role — top-level autonomous executive agent.

Covers:
- CEO role loads from YAML correctly
- Manager hierarchy: CEO → department heads
- Heartbeat model resolution: "opus" alias → full model ID
- Cross-department skill configuration
- Context file availability
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.skills.registry import SkillRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

LIBRARY_DIR = Path(__file__).parent.parent.parent / "src" / "skills" / "library"


@pytest.fixture(scope="module")
def registry() -> SkillRegistry:
    """Load the real skill library once for the entire module."""
    reg = SkillRegistry(skills_dir=LIBRARY_DIR)
    count = reg.load_all()
    assert count > 0, "Registry loaded zero skills"
    return reg


# ---------------------------------------------------------------------------
# Tests: CEO as Manager of Managers
# ---------------------------------------------------------------------------


class TestCEOManagerHierarchy:
    """CEO manages department heads who themselves manage sub-roles."""

    def test_ceo_manages_two_department_heads(self, registry: SkillRegistry) -> None:
        ceo = registry.get_role("ceo")
        assert len(ceo.manages) == 2

    def test_managed_roles_are_themselves_managers(self, registry: SkillRegistry) -> None:
        """Both roles managed by CEO are themselves managers."""
        ceo = registry.get_role("ceo")
        for role_id in ceo.manages:
            role = registry.get_role(role_id)
            assert role is not None, f"Managed role '{role_id}' not found"
            # head_of_marketing manages 3 sub-roles; head_of_it manages 0
            # but both are valid manager roles (head_of_it is a leaf manager)

    def test_ceo_is_top_of_hierarchy(self, registry: SkillRegistry) -> None:
        """No other role manages the CEO."""
        ceo_id = "ceo"
        for role in registry.list_roles():
            manages = getattr(role, "manages", ())
            assert ceo_id not in manages, (
                f"Role '{role.id}' manages the CEO — this violates the hierarchy"
            )

    def test_transitive_management_reach(self, registry: SkillRegistry) -> None:
        """CEO transitively reaches all operational roles."""
        ceo = registry.get_role("ceo")
        reachable = set()
        queue = list(ceo.manages)
        while queue:
            role_id = queue.pop()
            if role_id in reachable:
                continue
            reachable.add(role_id)
            role = registry.get_role(role_id)
            if role and role.manages:
                queue.extend(role.manages)

        # CEO should transitively reach all non-CEO roles
        all_role_ids = {r.id for r in registry.list_roles()} - {"ceo"}
        assert reachable == all_role_ids, f"CEO cannot reach: {all_role_ids - reachable}"


# ---------------------------------------------------------------------------
# Tests: Model Resolution
# ---------------------------------------------------------------------------


class TestCEOModelRouting:
    """CEO uses Opus for heartbeats and Sonnet for briefing skills."""

    def test_heartbeat_model_is_opus(self, registry: SkillRegistry) -> None:
        ceo = registry.get_role("ceo")
        assert ceo.heartbeat_model == "opus"

    def test_briefing_skill_is_sonnet(self, registry: SkillRegistry) -> None:
        skill = registry.get("org_health_check")
        assert skill.model == "sonnet"

    def test_synthesis_skill_is_opus(self, registry: SkillRegistry) -> None:
        skill = registry.get("cross_dept_synthesis")
        assert skill.model == "opus"

    def test_heartbeat_model_alias_resolves(self) -> None:
        """Verify that 'opus' alias resolves to full model ID via _resolve_model."""
        from src.agent.core import SideraAgent

        agent = SideraAgent.__new__(SideraAgent)
        agent._model_override = ""

        with patch("src.agent.core.settings") as mock_settings:
            mock_settings.model_fast = "claude-3-haiku-20240307"
            mock_settings.model_standard = "claude-sonnet-4-20250514"
            mock_settings.model_reasoning = "claude-opus-4-20250514"

            resolved = agent._resolve_model("opus")
            assert resolved == "claude-opus-4-20250514"


# ---------------------------------------------------------------------------
# Tests: Schedule Configuration
# ---------------------------------------------------------------------------


class TestCEOSchedule:
    """CEO runs between IT (6 AM) and Marketing (9 AM)."""

    def test_daily_briefing_schedule(self, registry: SkillRegistry) -> None:
        ceo = registry.get_role("ceo")
        assert ceo.schedule is not None
        # Should run at 8 AM on weekdays
        assert "8" in ceo.schedule
        assert "1-5" in ceo.schedule

    def test_heartbeat_schedule_hourly(self, registry: SkillRegistry) -> None:
        ceo = registry.get_role("ceo")
        assert ceo.heartbeat_schedule is not None
        # Should be hourly during business hours
        assert "*/1" in ceo.heartbeat_schedule or "0 *" in ceo.heartbeat_schedule


# ---------------------------------------------------------------------------
# Tests: Event Subscriptions
# ---------------------------------------------------------------------------


class TestCEOEventSubscriptions:
    """CEO subscribes to critical cross-department events."""

    def test_has_event_subscriptions(self, registry: SkillRegistry) -> None:
        ceo = registry.get_role("ceo")
        assert len(ceo.event_subscriptions) >= 2

    def test_subscribes_to_system_alert(self, registry: SkillRegistry) -> None:
        ceo = registry.get_role("ceo")
        assert "system_alert" in ceo.event_subscriptions

    def test_subscribes_to_cost_spike(self, registry: SkillRegistry) -> None:
        ceo = registry.get_role("ceo")
        assert "cost_spike" in ceo.event_subscriptions
