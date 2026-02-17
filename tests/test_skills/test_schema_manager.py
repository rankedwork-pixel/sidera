"""Tests for Manager Role fields on RoleDefinition.

Covers the three new fields added in Phase 1:
- manages: tuple of role IDs this manager directs
- delegation_model: "standard" or "fast"
- synthesis_prompt: custom synthesis instructions
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from src.skills.schema import (
    RoleDefinition,
    load_role_from_yaml,
    validate_role,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_role(**overrides) -> RoleDefinition:
    """Create a RoleDefinition with sensible defaults, applying overrides."""
    defaults = {
        "id": "test_role",
        "name": "Test Role",
        "department_id": "marketing",
        "description": "A test role for unit tests",
        "briefing_skills": ("budget_pacing",),
    }
    defaults.update(overrides)
    return RoleDefinition(**defaults)


def _write_role_yaml(tmp_path: Path, content: str) -> Path:
    """Write a _role.yaml file into tmp_path and return the path."""
    yaml_path = tmp_path / "_role.yaml"
    yaml_path.write_text(textwrap.dedent(content), encoding="utf-8")
    return yaml_path


# ── Dataclass defaults ──────────────────────────────────────────────────────


class TestRoleDefinitionDefaults:
    """Verify default values for the three new manager fields."""

    def test_manages_defaults_to_empty_tuple(self):
        role = _make_role()
        assert role.manages == ()

    def test_delegation_model_defaults_to_standard(self):
        role = _make_role()
        assert role.delegation_model == "standard"

    def test_synthesis_prompt_defaults_to_empty_string(self):
        role = _make_role()
        assert role.synthesis_prompt == ""


# ── Manager detection via manages field ─────────────────────────────────────


class TestManagerDetection:
    """A role with manages != () is a manager; empty manages is not."""

    def test_role_with_manages_is_manager(self):
        role = _make_role(manages=("worker_a", "worker_b"))
        assert role.manages == ("worker_a", "worker_b")
        assert len(role.manages) == 2

    def test_role_with_empty_manages_is_not_manager(self):
        role = _make_role(manages=())
        assert role.manages == ()
        assert len(role.manages) == 0

    def test_role_without_manages_kwarg_is_not_manager(self):
        role = _make_role()
        assert role.manages == ()


# ── Validation: delegation_model ────────────────────────────────────────────


class TestDelegationModelValidation:
    """validate_role must accept 'standard'/'fast' and reject others."""

    def test_standard_is_valid(self):
        role = _make_role(delegation_model="standard")
        errors = validate_role(role)
        assert not any("delegation_model" in e for e in errors)

    def test_fast_is_valid(self):
        role = _make_role(delegation_model="fast")
        errors = validate_role(role)
        assert not any("delegation_model" in e for e in errors)

    def test_turbo_is_invalid(self):
        role = _make_role(delegation_model="turbo")
        errors = validate_role(role)
        dm_errors = [e for e in errors if "delegation_model" in e]
        assert len(dm_errors) == 1
        assert "'turbo'" in dm_errors[0]

    def test_empty_string_is_invalid(self):
        role = _make_role(delegation_model="")
        errors = validate_role(role)
        dm_errors = [e for e in errors if "delegation_model" in e]
        assert len(dm_errors) == 1


# ── Validation: managed role IDs ────────────────────────────────────────────


class TestManagedRoleIdValidation:
    """Managed role IDs must be alphanumeric + underscore/hyphen."""

    def test_valid_managed_ids(self):
        role = _make_role(manages=("worker_a", "worker-b", "worker123"))
        errors = validate_role(role)
        assert not any("managed role ID" in e for e in errors)

    def test_invalid_managed_id_with_space(self):
        role = _make_role(manages=("bad id",))
        errors = validate_role(role)
        mid_errors = [e for e in errors if "managed role ID" in e]
        assert len(mid_errors) == 1
        assert "'bad id'" in mid_errors[0]

    def test_invalid_managed_id_with_exclamation(self):
        role = _make_role(manages=("bad_id!",))
        errors = validate_role(role)
        mid_errors = [e for e in errors if "managed role ID" in e]
        assert len(mid_errors) == 1
        assert "'bad_id!'" in mid_errors[0]

    def test_multiple_invalid_managed_ids(self):
        role = _make_role(manages=("ok_one", "bad id!", "also bad@"))
        errors = validate_role(role)
        mid_errors = [e for e in errors if "managed role ID" in e]
        assert len(mid_errors) == 2


# ── Validation: briefing_skills + manages interaction ───────────────────────


class TestBriefingSkillsManagesInteraction:
    """Manager roles can have no briefing_skills; non-managers need them."""

    def test_manager_with_no_briefing_skills_passes(self):
        role = _make_role(
            briefing_skills=(),
            manages=("sub_role_a",),
        )
        errors = validate_role(role)
        assert not any("briefing_skills" in e for e in errors)
        assert not any("manages" in e for e in errors)

    def test_neither_briefing_skills_nor_manages_fails(self):
        role = _make_role(
            briefing_skills=(),
            manages=(),
        )
        errors = validate_role(role)
        relevant = [e for e in errors if "briefing_skills" in e or "manages" in e]
        assert len(relevant) == 1
        assert "briefing_skills" in relevant[0]
        assert "manages" in relevant[0]

    def test_role_with_both_briefing_skills_and_manages_passes(self):
        role = _make_role(
            briefing_skills=("budget_pacing",),
            manages=("sub_role_a",),
        )
        errors = validate_role(role)
        assert not any("briefing_skills" in e for e in errors)
        assert not any("manages" in e for e in errors)

    def test_role_with_only_briefing_skills_passes(self):
        role = _make_role(
            briefing_skills=("budget_pacing",),
            manages=(),
        )
        errors = validate_role(role)
        assert not any("briefing_skills" in e for e in errors)


# ── YAML loader: manages ────────────────────────────────────────────────────


class TestLoadRoleManages:
    """load_role_from_yaml correctly parses the manages field."""

    def test_parses_manages_list(self, tmp_path):
        yaml_path = _write_role_yaml(
            tmp_path,
            """\
            id: marketing_manager
            name: Marketing Manager
            department_id: marketing
            description: Oversees marketing roles
            manages:
              - performance_media_buyer
              - creative_strategist
            """,
        )
        role = load_role_from_yaml(yaml_path)
        assert role.manages == ("performance_media_buyer", "creative_strategist")

    def test_manages_defaults_when_absent(self, tmp_path):
        yaml_path = _write_role_yaml(
            tmp_path,
            """\
            id: worker_role
            name: Worker
            department_id: marketing
            description: A regular worker role
            briefing_skills:
              - budget_pacing
            """,
        )
        role = load_role_from_yaml(yaml_path)
        assert role.manages == ()

    def test_manages_empty_list_in_yaml(self, tmp_path):
        yaml_path = _write_role_yaml(
            tmp_path,
            """\
            id: worker_role
            name: Worker
            department_id: marketing
            description: A regular worker role
            manages: []
            briefing_skills:
              - budget_pacing
            """,
        )
        role = load_role_from_yaml(yaml_path)
        assert role.manages == ()


# ── YAML loader: delegation_model ───────────────────────────────────────────


class TestLoadRoleDelegationModel:
    """load_role_from_yaml correctly parses delegation_model."""

    def test_parses_delegation_model(self, tmp_path):
        yaml_path = _write_role_yaml(
            tmp_path,
            """\
            id: fast_manager
            name: Fast Manager
            department_id: marketing
            description: A manager using fast delegation
            manages:
              - worker_a
            delegation_model: fast
            """,
        )
        role = load_role_from_yaml(yaml_path)
        assert role.delegation_model == "fast"

    def test_delegation_model_defaults_to_standard(self, tmp_path):
        yaml_path = _write_role_yaml(
            tmp_path,
            """\
            id: default_manager
            name: Default Manager
            department_id: marketing
            description: A manager with default delegation
            manages:
              - worker_a
            """,
        )
        role = load_role_from_yaml(yaml_path)
        assert role.delegation_model == "standard"


# ── YAML loader: synthesis_prompt ───────────────────────────────────────────


class TestLoadRoleSynthesisPrompt:
    """load_role_from_yaml correctly parses synthesis_prompt."""

    def test_parses_synthesis_prompt(self, tmp_path):
        yaml_path = _write_role_yaml(
            tmp_path,
            """\
            id: custom_manager
            name: Custom Manager
            department_id: marketing
            description: Manager with custom synthesis
            manages:
              - worker_a
            synthesis_prompt: Combine all reports into a single executive summary.
            """,
        )
        role = load_role_from_yaml(yaml_path)
        assert role.synthesis_prompt == ("Combine all reports into a single executive summary.")

    def test_synthesis_prompt_defaults_to_empty(self, tmp_path):
        yaml_path = _write_role_yaml(
            tmp_path,
            """\
            id: default_manager
            name: Default Manager
            department_id: marketing
            description: Manager with no custom synthesis
            manages:
              - worker_a
            """,
        )
        role = load_role_from_yaml(yaml_path)
        assert role.synthesis_prompt == ""


# ── YAML loader: all three fields together ──────────────────────────────────


class TestLoadRoleAllManagerFields:
    """Verify all three manager fields load together from a single YAML."""

    def test_full_manager_yaml(self, tmp_path):
        yaml_path = _write_role_yaml(
            tmp_path,
            """\
            id: vp_marketing
            name: VP of Marketing
            department_id: executive
            description: Senior marketing leadership
            manages:
              - brand_manager
              - perf_manager
            delegation_model: fast
            synthesis_prompt: Focus on cross-channel synergies.
            """,
        )
        role = load_role_from_yaml(yaml_path)
        assert role.id == "vp_marketing"
        assert role.manages == ("brand_manager", "perf_manager")
        assert role.delegation_model == "fast"
        assert role.synthesis_prompt == "Focus on cross-channel synergies."


# ── Full validation pass/fail round-trips ───────────────────────────────────


class TestFullValidationRoundTrips:
    """End-to-end validation for manager roles."""

    def test_valid_manager_role_passes(self):
        role = _make_role(
            manages=("sub_a", "sub_b"),
            delegation_model="fast",
            synthesis_prompt="Merge results into a brief.",
            briefing_skills=(),
        )
        errors = validate_role(role)
        assert errors == []

    def test_valid_standard_role_still_passes(self):
        role = _make_role(
            briefing_skills=("budget_pacing",),
        )
        errors = validate_role(role)
        assert errors == []

    def test_invalid_delegation_model_reported(self):
        role = _make_role(delegation_model="turbo")
        errors = validate_role(role)
        assert any("delegation_model" in e and "'turbo'" in e for e in errors)

    def test_invalid_managed_id_reported(self):
        role = _make_role(manages=("good_id", "bad id!"))
        errors = validate_role(role)
        assert any("managed role ID" in e and "'bad id!'" in e for e in errors)
