"""Integration tests for the performance_media_buyer _rules.yaml file.

Loads the real YAML file from disk (no mocking) and validates its
structure, content, and compatibility with the auto-execute engine
and SkillRegistry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.models.schema import ActionType
from src.skills.auto_execute import load_rules_from_yaml, validate_rules
from src.skills.registry import SkillRegistry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RULES_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "skills"
    / "library"
    / "marketing"
    / "performance_media_buyer"
    / "_rules.yaml"
)

_LIBRARY_DIR = Path(__file__).parent.parent.parent / "src" / "skills" / "library"

_EXPECTED_RULE_IDS = {
    "pause_low_roas_ads",
    "pause_high_cpa_ads",
    "add_obvious_negatives",
    "small_budget_increase",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ruleset():
    """Load the real _rules.yaml once for the module."""
    return load_rules_from_yaml(_RULES_PATH)


@pytest.fixture(scope="module")
def registry() -> SkillRegistry:
    """Load the full skill library once for the module."""
    reg = SkillRegistry(skills_dir=_LIBRARY_DIR)
    count = reg.load_all()
    assert count > 0, "Registry loaded zero skills"
    return reg


# ===========================================================================
# Tests
# ===========================================================================


class TestRulesLibrary:
    """Validate the performance_media_buyer _rules.yaml file."""

    def test_loads_successfully(self, ruleset):
        """File should parse without errors via load_rules_from_yaml()."""
        assert ruleset is not None
        assert ruleset.role_id == "performance_media_buyer"
        assert len(ruleset.rules) > 0

    def test_contains_expected_rule_ids(self, ruleset):
        """All four expected rule IDs should be present."""
        loaded_ids = {rule.id for rule in ruleset.rules}
        assert loaded_ids == _EXPECTED_RULE_IDS, f"Expected {_EXPECTED_RULE_IDS}, got {loaded_ids}"

    def test_validates_successfully(self, ruleset):
        """validate_rules() should return no errors."""
        errors = validate_rules(ruleset)
        assert errors == [], f"Validation returned errors: {errors}"

    def test_small_budget_increase_disabled_by_default(self, ruleset):
        """The small_budget_increase rule should be disabled."""
        rule = next(r for r in ruleset.rules if r.id == "small_budget_increase")
        assert rule.enabled is False, "small_budget_increase should have enabled=false"

    def test_all_action_types_match_schema_enum(self, ruleset):
        """Every action_type in every rule should be a valid ActionType."""
        valid_values = {at.value for at in ActionType}
        for rule in ruleset.rules:
            for action_type in rule.action_types:
                assert action_type in valid_values, (
                    f"Rule '{rule.id}' has invalid action_type "
                    f"'{action_type}'. Valid: {sorted(valid_values)}"
                )

    def test_registry_loads_rules_for_role(self, registry):
        """SkillRegistry.get_rules() should return the ruleset."""
        rules = registry.get_rules("performance_media_buyer")
        assert rules is not None, "get_rules('performance_media_buyer') returned None"
        loaded_ids = {r.id for r in rules.rules}
        assert loaded_ids == _EXPECTED_RULE_IDS
