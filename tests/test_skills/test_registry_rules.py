"""Tests for auto-execute rule loading via SkillRegistry.

Covers:
- get_rules() returns loaded ruleset for a known role
- get_rules() returns None for unknown role
- list_rulesets() returns all loaded rulesets
- ruleset_count property
- Rules loaded during load_all() from _rules.yaml in role directories

Temporary YAML files on disk are created via tmp_path fixtures.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from src.skills.registry import SkillRegistry

# ---------------------------------------------------------------------------
# Shared mock tools — must include every tool referenced in test YAMLs
# ---------------------------------------------------------------------------

_MOCK_ALL_TOOLS = [
    "get_meta_campaigns",
    "get_meta_performance",
    "get_google_ads_performance",
    "list_google_ads_accounts",
    "get_backend_performance",
    "send_slack_alert",
]


# ---------------------------------------------------------------------------
# YAML content helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> Path:
    """Write text to a file, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _department_yaml(
    dept_id: str = "marketing",
    name: str = "Marketing Department",
) -> str:
    """Return YAML content for a department definition."""
    data = {
        "id": dept_id,
        "name": name,
        "description": f"The {name}",
    }
    return yaml.dump(data, default_flow_style=False)


def _role_yaml(
    role_id: str = "media_buyer",
    name: str = "Media Buyer",
    department_id: str = "marketing",
) -> str:
    """Return YAML content for a role definition."""
    data = {
        "id": role_id,
        "name": name,
        "department_id": department_id,
        "description": f"The {name} role",
        "briefing_skills": ["skill_a"],
    }
    return yaml.dump(data, default_flow_style=False)


def _skill_yaml(
    skill_id: str = "test_skill",
    name: str = "Test Skill",
) -> str:
    """Return YAML content for a skill definition."""
    data = {
        "id": skill_id,
        "name": name,
        "version": "1.0",
        "description": f"Description for {name}",
        "category": "analysis",
        "platforms": ["google_ads"],
        "tags": ["test"],
        "tools_required": ["get_meta_campaigns"],
        "model": "sonnet",
        "max_turns": 10,
        "system_supplement": "System supplement.",
        "prompt_template": "Run analysis.",
        "output_format": "## Results\nShow results.",
        "business_guidance": "Follow best practices.",
    }
    return yaml.dump(data, default_flow_style=False)


def _rules_yaml(
    role_id: str = "media_buyer",
    rules: list[dict] | None = None,
) -> str:
    """Return YAML content for a _rules.yaml file."""
    if rules is None:
        rules = [
            {
                "id": "small_budget_up",
                "description": "Auto-approve small budget increases",
                "action_types": ["budget_change"],
                "conditions": [
                    {"field": "action_params.change_pct", "operator": "lte", "value": 20},
                ],
                "constraints": {
                    "max_daily_auto_executions": 5,
                    "cooldown_minutes": 30,
                    "platforms": ["google_ads", "meta"],
                },
            }
        ]
    data = {"role_id": role_id, "rules": rules}
    return yaml.dump(data, default_flow_style=False)


def _build_hierarchy_with_rules(
    tmp_path: Path,
    *,
    include_rules: bool = True,
    num_roles_with_rules: int = 1,
) -> Path:
    """Build a department/role/skill hierarchy on disk.

    Creates::

        tmp_path/
          marketing/
            _department.yaml
            media_buyer/
              _role.yaml
              _rules.yaml (optional)
              budget_check.yaml

    Args:
        tmp_path: Root directory.
        include_rules: Whether to include _rules.yaml.
        num_roles_with_rules: How many roles get a _rules.yaml.

    Returns:
        tmp_path (the library dir).
    """
    # Department
    _write(
        tmp_path / "marketing" / "_department.yaml",
        _department_yaml(),
    )

    # First role (media_buyer) — always created
    role_dir = tmp_path / "marketing" / "media_buyer"
    _write(role_dir / "_role.yaml", _role_yaml())
    _write(role_dir / "budget_check.yaml", _skill_yaml("budget_check", "Budget Check"))

    if include_rules:
        _write(role_dir / "_rules.yaml", _rules_yaml("media_buyer"))

    # Second role (creative_strategist) — optionally with rules
    if num_roles_with_rules > 1:
        role2_dir = tmp_path / "marketing" / "creative_strategist"
        _write(
            role2_dir / "_role.yaml",
            _role_yaml("creative_strategist", "Creative Strategist"),
        )
        _write(
            role2_dir / "ad_review.yaml",
            _skill_yaml("ad_review", "Ad Review"),
        )
        _write(
            role2_dir / "_rules.yaml",
            _rules_yaml(
                "creative_strategist",
                rules=[
                    {
                        "id": "safe_ad_toggle",
                        "description": "Toggle low-risk ad status",
                        "action_types": ["update_ad_status"],
                    }
                ],
            ),
        )

    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRegistryRules:
    """Tests for auto-execute rule loading and lookup in SkillRegistry."""

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_get_rules_returns_loaded_ruleset(self, tmp_path):
        """get_rules() returns the loaded AutoExecuteRuleSet for a known role."""
        lib = _build_hierarchy_with_rules(tmp_path, include_rules=True)
        registry = SkillRegistry(skills_dir=lib)
        registry.load_all()

        ruleset = registry.get_rules("media_buyer")
        assert ruleset is not None
        assert ruleset.role_id == "media_buyer"
        assert len(ruleset.rules) == 1
        assert ruleset.rules[0].id == "small_budget_up"

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_get_rules_returns_none_for_unknown_role(self, tmp_path):
        """get_rules() returns None for a role with no _rules.yaml."""
        lib = _build_hierarchy_with_rules(tmp_path, include_rules=False)
        registry = SkillRegistry(skills_dir=lib)
        registry.load_all()

        assert registry.get_rules("media_buyer") is None
        assert registry.get_rules("nonexistent_role") is None

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_list_rulesets(self, tmp_path):
        """list_rulesets() returns all loaded rule sets sorted by role_id."""
        lib = _build_hierarchy_with_rules(
            tmp_path,
            include_rules=True,
            num_roles_with_rules=2,
        )
        registry = SkillRegistry(skills_dir=lib)
        registry.load_all()

        rulesets = registry.list_rulesets()
        assert len(rulesets) == 2
        # Sorted by role_id
        assert rulesets[0].role_id == "creative_strategist"
        assert rulesets[1].role_id == "media_buyer"

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_ruleset_count(self, tmp_path):
        """ruleset_count property returns the correct count."""
        lib = _build_hierarchy_with_rules(
            tmp_path,
            include_rules=True,
            num_roles_with_rules=2,
        )
        registry = SkillRegistry(skills_dir=lib)
        registry.load_all()

        assert registry.ruleset_count == 2

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_rules_cleared_on_reload(self, tmp_path):
        """Rulesets are cleared and reloaded when load_all() is called again."""
        lib = _build_hierarchy_with_rules(tmp_path, include_rules=True)
        registry = SkillRegistry(skills_dir=lib)
        registry.load_all()

        assert registry.ruleset_count == 1

        # Remove the rules file and reload
        rules_path = lib / "marketing" / "media_buyer" / "_rules.yaml"
        rules_path.unlink()
        registry.load_all()

        assert registry.ruleset_count == 0
        assert registry.get_rules("media_buyer") is None

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_invalid_rules_file_logged_not_raised(self, tmp_path):
        """A malformed _rules.yaml is logged but does not crash load_all()."""
        lib = _build_hierarchy_with_rules(tmp_path, include_rules=False)

        # Write a bad rules file
        bad_rules = tmp_path / "marketing" / "media_buyer" / "_rules.yaml"
        bad_rules.write_text("{{invalid yaml", encoding="utf-8")

        registry = SkillRegistry(skills_dir=lib)
        count = registry.load_all()

        # Skills still loaded even though rules failed
        assert count >= 1
        assert registry.get_rules("media_buyer") is None
