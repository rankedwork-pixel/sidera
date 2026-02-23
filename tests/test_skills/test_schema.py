"""Tests for src.skills.schema -- SkillDefinition, load_skill_from_yaml, validate_skill.

Covers dataclass immutability, YAML loading (valid, missing, wrong extension,
syntax errors, non-dict, missing fields), and validation (model, category,
platform, tools, max_turns, prompt fields, chain_after).

ALL_TOOLS is patched so tests don't depend on the actual prompts module.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import pytest

from src.skills.schema import (
    SkillDefinition,
    SkillLoadError,
    load_skill_from_yaml,
    validate_skill,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Minimal valid skill dict that satisfies all required fields
_VALID_SKILL_DICT = {
    "id": "test_skill",
    "name": "Test Skill",
    "version": "1.0",
    "description": "A test skill for unit tests",
    "category": "analysis",
    "platforms": ["google_ads", "meta"],
    "tags": ["test", "unit"],
    "tools_required": ["get_meta_campaigns", "get_google_ads_performance"],
    "model": "sonnet",
    "max_turns": 10,
    "system_supplement": "You are running a test skill.",
    "prompt_template": "Run a test analysis for {account}.",
    "output_format": "## Results\nShow the results here.",
    "business_guidance": "Follow best practices for testing.",
    "schedule": None,
    "chain_after": None,
    "requires_approval": True,
    "author": "sidera",
    "created_at": "2025-01-01",
    "updated_at": "2025-01-01",
}

# Tool names we allow during testing -- matches what creative_analysis.yaml uses
_MOCK_ALL_TOOLS = [
    "get_meta_campaigns",
    "get_meta_performance",
    "get_meta_audience_insights",
    "get_backend_performance",
    "get_campaign_attribution",
    "create_google_doc",
    "manage_google_sheets",
    "get_google_ads_performance",
    "list_google_ads_accounts",
]


def _write_yaml(tmp_path: Path, filename: str, content: str) -> Path:
    """Write *content* to a YAML file under *tmp_path* and return its path."""
    p = tmp_path / filename
    p.write_text(content, encoding="utf-8")
    return p


def _valid_yaml_content() -> str:
    """Return a YAML string for a valid skill definition."""
    import yaml

    return yaml.dump(_VALID_SKILL_DICT, default_flow_style=False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def valid_yaml_path(tmp_path: Path) -> Path:
    """Write a valid skill YAML file to a temp directory."""
    return _write_yaml(tmp_path, "test_skill.yaml", _valid_yaml_content())


# ===========================================================================
# 1. SkillDefinition immutability
# ===========================================================================


class TestSkillDefinitionFrozen:
    """SkillDefinition is a frozen dataclass -- mutation must raise."""

    def test_cannot_mutate_id(self):
        """Assigning to .id raises FrozenInstanceError."""
        skill = SkillDefinition(
            id="frozen_test",
            name="Frozen",
            version="1.0",
            description="desc",
            category="analysis",
            platforms=("google_ads",),
            tags=("test",),
            tools_required=("get_meta_campaigns",),
            model="sonnet",
            max_turns=10,
            system_supplement="supplement",
            prompt_template="template",
            output_format="format",
            business_guidance="guidance",
        )
        with pytest.raises(FrozenInstanceError):
            skill.id = "new_id"  # type: ignore[misc]


# ===========================================================================
# 2. load_skill_from_yaml
# ===========================================================================


class TestLoadSkillFromYaml:
    """Loading YAML files into SkillDefinition instances."""

    def test_load_valid_yaml(self, valid_yaml_path: Path):
        """A well-formed YAML file loads into a SkillDefinition."""
        skill = load_skill_from_yaml(valid_yaml_path)
        assert isinstance(skill, SkillDefinition)
        assert skill.id == "test_skill"
        assert skill.name == "Test Skill"
        assert skill.model == "sonnet"
        assert skill.max_turns == 10
        assert "google_ads" in skill.platforms
        assert "meta" in skill.platforms

    def test_load_missing_file(self, tmp_path: Path):
        """Non-existent file raises SkillLoadError."""
        missing = tmp_path / "does_not_exist.yaml"
        with pytest.raises(SkillLoadError, match="not found"):
            load_skill_from_yaml(missing)

    def test_load_wrong_extension(self, tmp_path: Path):
        """A .txt file raises SkillLoadError."""
        txt_file = _write_yaml(tmp_path, "skill.txt", _valid_yaml_content())
        with pytest.raises(SkillLoadError, match="Expected .yaml or .yml"):
            load_skill_from_yaml(txt_file)

    def test_load_invalid_yaml_syntax(self, tmp_path: Path):
        """Malformed YAML raises SkillLoadError."""
        bad_yaml = _write_yaml(
            tmp_path,
            "bad.yaml",
            "id: test\n  bad_indent: [unclosed\n",
        )
        with pytest.raises(SkillLoadError, match="Invalid YAML"):
            load_skill_from_yaml(bad_yaml)

    def test_load_non_dict_yaml(self, tmp_path: Path):
        """A YAML list (not mapping) raises SkillLoadError."""
        list_yaml = _write_yaml(
            tmp_path,
            "list.yaml",
            "- item1\n- item2\n- item3\n",
        )
        with pytest.raises(SkillLoadError, match="Expected a YAML mapping"):
            load_skill_from_yaml(list_yaml)

    def test_load_missing_required_fields(self, tmp_path: Path):
        """YAML with missing required fields raises SkillLoadError."""
        incomplete_yaml = _write_yaml(
            tmp_path,
            "incomplete.yaml",
            "id: partial_skill\nname: Partial\n",
        )
        with pytest.raises(SkillLoadError, match="Missing required fields"):
            load_skill_from_yaml(incomplete_yaml)

    def test_load_yml_extension(self, tmp_path: Path):
        """A .yml extension is also accepted."""
        yml_path = _write_yaml(tmp_path, "test_skill.yml", _valid_yaml_content())
        skill = load_skill_from_yaml(yml_path)
        assert skill.id == "test_skill"


# ===========================================================================
# 3. validate_skill
# ===========================================================================


class TestValidateSkill:
    """validate_skill returns a list of error strings."""

    def _make_skill(self, **overrides) -> SkillDefinition:
        """Build a SkillDefinition with sensible defaults, applying overrides."""
        defaults = {
            "id": "valid_skill",
            "name": "Valid Skill",
            "version": "1.0",
            "description": "A valid skill",
            "category": "analysis",
            "platforms": ("google_ads",),
            "tags": ("test",),
            "tools_required": ("get_meta_campaigns",),
            "model": "sonnet",
            "max_turns": 10,
            "system_supplement": "You are a test skill.",
            "prompt_template": "Run analysis for {account}.",
            "output_format": "## Results\nHere.",
            "business_guidance": "Follow best practices.",
        }
        defaults.update(overrides)
        return SkillDefinition(**defaults)

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_valid_skill_no_errors(self):
        """A fully valid skill produces an empty error list."""
        skill = self._make_skill()
        errors = validate_skill(skill)
        assert errors == []

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_invalid_model(self):
        """An unrecognized model produces an error."""
        skill = self._make_skill(model="gpt4")
        errors = validate_skill(skill)
        assert any("Invalid model" in e for e in errors)

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_invalid_category(self):
        """An unrecognized category produces an error."""
        skill = self._make_skill(category="magic")
        errors = validate_skill(skill)
        assert any("Invalid category" in e for e in errors)

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_unknown_platform(self):
        """An unrecognized platform produces an error."""
        skill = self._make_skill(platforms=("google_ads", "tiktok"))
        errors = validate_skill(skill)
        assert any("Unknown platform" in e and "tiktok" in e for e in errors)

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_unknown_tool(self):
        """A tool not in ALL_TOOLS produces an error."""
        skill = self._make_skill(tools_required=("nonexistent_tool",))
        errors = validate_skill(skill)
        assert any("Unknown tool" in e and "nonexistent_tool" in e for e in errors)

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_max_turns_too_low(self):
        """max_turns < 1 produces an error."""
        skill = self._make_skill(max_turns=0)
        errors = validate_skill(skill)
        assert any("max_turns must be >= 1" in e for e in errors)

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_max_turns_too_high(self):
        """max_turns > 50 produces an error."""
        skill = self._make_skill(max_turns=100)
        errors = validate_skill(skill)
        assert any("max_turns must be <= 50" in e for e in errors)

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_empty_system_supplement(self):
        """Empty system_supplement produces an error."""
        skill = self._make_skill(system_supplement="   ")
        errors = validate_skill(skill)
        assert any("system_supplement is empty" in e for e in errors)

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_empty_prompt_template(self):
        """Empty prompt_template produces an error."""
        skill = self._make_skill(prompt_template="")
        errors = validate_skill(skill)
        assert any("prompt_template is empty" in e for e in errors)

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_self_referencing_chain_after(self):
        """chain_after == id produces an error about infinite loop."""
        skill = self._make_skill(id="loop_skill", chain_after="loop_skill")
        errors = validate_skill(skill)
        assert any("chain_after pointing to itself" in e for e in errors)

    # --- references validation ---

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_references_defaults_empty(self):
        """references defaults to empty tuple."""
        skill = self._make_skill()
        assert skill.references == ()
        errors = validate_skill(skill)
        assert not errors

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_references_valid(self):
        """Valid references pass validation."""
        skill = self._make_skill(
            references=(("other_skill", "methodology", "attribution windows"),)
        )
        errors = validate_skill(skill)
        assert not errors

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_references_self_reference_rejected(self):
        """Self-reference is rejected."""
        skill = self._make_skill(
            id="my_skill",
            references=(("my_skill", "methodology", "reason"),),
        )
        errors = validate_skill(skill)
        assert any("references itself" in e for e in errors)

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_references_duplicate_rejected(self):
        """Duplicate references are rejected."""
        skill = self._make_skill(
            references=(
                ("other", "methodology", "reason 1"),
                ("other", "methodology", "reason 2"),
            ),
        )
        errors = validate_skill(skill)
        assert any("Duplicate reference" in e for e in errors)

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_references_empty_skill_id_rejected(self):
        """Reference with empty skill_id is rejected."""
        skill = self._make_skill(
            references=(("", "methodology", "reason"),),
        )
        errors = validate_skill(skill)
        assert any("skill_id cannot be empty" in e for e in errors)


# ===========================================================================
# 4. YAML parsing of references
# ===========================================================================


class TestReferencesYamlParsing:
    """References are parsed correctly from YAML."""

    def test_references_from_yaml(self, tmp_path: Path):
        """references list-of-dicts in YAML becomes tuple-of-tuples."""
        import yaml

        d = dict(_VALID_SKILL_DICT)
        d["references"] = [
            {
                "skill_id": "attribution_analysis",
                "relationship": "methodology",
                "reason": "pull attribution windows",
            },
            {
                "skill_id": "brand_guidelines",
                "relationship": "context",
                "reason": "brand voice",
            },
        ]
        path = _write_yaml(tmp_path, "ref_skill.yaml", yaml.dump(d))
        skill = load_skill_from_yaml(path)
        assert len(skill.references) == 2
        assert skill.references[0] == (
            "attribution_analysis",
            "methodology",
            "pull attribution windows",
        )
        assert skill.references[1] == (
            "brand_guidelines",
            "context",
            "brand voice",
        )

    def test_references_empty_by_default(self, tmp_path: Path):
        """No references key in YAML → empty tuple."""
        path = _write_yaml(tmp_path, "no_refs.yaml", _valid_yaml_content())
        skill = load_skill_from_yaml(path)
        assert skill.references == ()

    def test_references_skips_entries_without_skill_id(self, tmp_path: Path):
        """Entries without skill_id are silently skipped."""
        import yaml

        d = dict(_VALID_SKILL_DICT)
        d["references"] = [
            {"relationship": "methodology", "reason": "no skill_id"},
            {"skill_id": "valid_ref", "relationship": "context", "reason": "ok"},
        ]
        path = _write_yaml(tmp_path, "partial_refs.yaml", yaml.dump(d))
        skill = load_skill_from_yaml(path)
        assert len(skill.references) == 1
        assert skill.references[0][0] == "valid_ref"
