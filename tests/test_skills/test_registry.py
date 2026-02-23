"""Tests for src.skills.registry -- SkillRegistry.

Covers construction, load_all (valid, empty, non-existent, invalid, duplicate),
lookup (get, list_all, list_by_category, list_by_platform, list_scheduled),
routing index, search, reload, count/__len__/__contains__.

ALL_TOOLS is patched so tests don't depend on the actual prompts module.
Temporary YAML files are created via tmp_path fixtures.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from src.skills.registry import _DEFAULT_SKILLS_DIR, SkillRegistry

# ---------------------------------------------------------------------------
# Shared mock tools -- must include every tool referenced in test YAMLs
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


def _skill_yaml(
    skill_id: str = "test_skill",
    name: str = "Test Skill",
    category: str = "analysis",
    platforms: list[str] | None = None,
    tags: list[str] | None = None,
    tools: list[str] | None = None,
    model: str = "sonnet",
    max_turns: int = 10,
    schedule: str | None = None,
    chain_after: str | None = None,
) -> str:
    """Return a YAML string for a valid skill definition with customizable fields."""
    data = {
        "id": skill_id,
        "name": name,
        "version": "1.0",
        "description": f"Description for {name}",
        "category": category,
        "platforms": platforms or ["google_ads"],
        "tags": tags or ["test"],
        "tools_required": tools or ["get_meta_campaigns"],
        "model": model,
        "max_turns": max_turns,
        "system_supplement": f"System supplement for {name}.",
        "prompt_template": f"Run {name} analysis.",
        "output_format": "## Results\nShow results.",
        "business_guidance": "Follow best practices.",
        "schedule": schedule,
        "chain_after": chain_after,
        "requires_approval": True,
        "author": "sidera",
        "created_at": "2025-01-01",
        "updated_at": "2025-01-01",
    }
    return yaml.dump(data, default_flow_style=False)


def _write_skill(tmp_path: Path, filename: str, content: str) -> Path:
    """Write a YAML string to tmp_path/filename, return the path."""
    p = tmp_path / filename
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def skills_dir(tmp_path: Path) -> Path:
    """Create a temp directory with two valid skill YAMLs."""
    _write_skill(
        tmp_path,
        "alpha.yaml",
        _skill_yaml(
            skill_id="alpha_analysis",
            name="Alpha Analysis",
            category="analysis",
            platforms=["google_ads", "meta"],
            tags=["alpha", "performance"],
        ),
    )
    _write_skill(
        tmp_path,
        "beta.yaml",
        _skill_yaml(
            skill_id="beta_reporting",
            name="Beta Reporting",
            category="reporting",
            platforms=["meta"],
            tags=["beta", "reporting"],
            schedule="0 8 * * *",
        ),
    )
    return tmp_path


@pytest.fixture()
def loaded_registry(skills_dir: Path) -> SkillRegistry:
    """Return a SkillRegistry with two skills already loaded."""
    with patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS):
        reg = SkillRegistry(skills_dir=skills_dir)
        reg.load_all()
    return reg


# ===========================================================================
# 1. Construction
# ===========================================================================


class TestConstruction:
    def test_default_dir(self):
        """Constructor with no args uses the default skills directory."""
        reg = SkillRegistry()
        assert reg.skills_dir == _DEFAULT_SKILLS_DIR

    def test_custom_dir(self, tmp_path: Path):
        """Constructor with a custom dir uses that directory."""
        reg = SkillRegistry(skills_dir=tmp_path)
        assert reg.skills_dir == tmp_path


# ===========================================================================
# 2. load_all
# ===========================================================================


class TestLoadAll:
    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_load_valid_skills(self, skills_dir: Path):
        """Loads all valid skills and returns the count."""
        reg = SkillRegistry(skills_dir=skills_dir)
        count = reg.load_all()
        assert count == 2
        assert len(reg) == 2

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_load_nonexistent_directory(self, tmp_path: Path):
        """Non-existent directory returns 0."""
        missing_dir = tmp_path / "no_such_dir"
        reg = SkillRegistry(skills_dir=missing_dir)
        count = reg.load_all()
        assert count == 0

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_load_empty_directory(self, tmp_path: Path):
        """Empty directory returns 0."""
        reg = SkillRegistry(skills_dir=tmp_path)
        count = reg.load_all()
        assert count == 0

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_load_skips_invalid_yaml(self, tmp_path: Path):
        """Invalid YAML files are skipped, valid ones still load."""
        _write_skill(
            tmp_path,
            "good.yaml",
            _skill_yaml(skill_id="good_skill", name="Good"),
        )
        _write_skill(
            tmp_path,
            "bad.yaml",
            "id: bad\n  broken_indent: [unclosed\n",
        )
        reg = SkillRegistry(skills_dir=tmp_path)
        count = reg.load_all()
        assert count == 1
        assert reg.get("good_skill") is not None

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_load_skips_duplicate_ids(self, tmp_path: Path):
        """Second file with the same skill ID is skipped."""
        _write_skill(
            tmp_path,
            "first.yaml",
            _skill_yaml(skill_id="dupe_skill", name="First"),
        )
        _write_skill(
            tmp_path,
            "second.yaml",
            _skill_yaml(skill_id="dupe_skill", name="Second"),
        )
        reg = SkillRegistry(skills_dir=tmp_path)
        count = reg.load_all()
        assert count == 1
        # The first one (alphabetically) wins
        assert reg.get("dupe_skill").name == "First"


# ===========================================================================
# 3. get
# ===========================================================================


class TestGet:
    def test_get_existing(self, loaded_registry: SkillRegistry):
        """get() returns the SkillDefinition for a known ID."""
        skill = loaded_registry.get("alpha_analysis")
        assert skill is not None
        assert skill.id == "alpha_analysis"
        assert skill.name == "Alpha Analysis"

    def test_get_unknown(self, loaded_registry: SkillRegistry):
        """get() returns None for an unknown ID."""
        assert loaded_registry.get("nonexistent") is None


# ===========================================================================
# 4. list_all
# ===========================================================================


class TestListAll:
    def test_list_all_sorted(self, loaded_registry: SkillRegistry):
        """list_all() returns skills sorted by ID."""
        skills = loaded_registry.list_all()
        assert len(skills) == 2
        assert skills[0].id == "alpha_analysis"
        assert skills[1].id == "beta_reporting"


# ===========================================================================
# 5. list_by_category
# ===========================================================================


class TestListByCategory:
    def test_filter_by_category(self, loaded_registry: SkillRegistry):
        """list_by_category returns only matching skills."""
        analysis = loaded_registry.list_by_category("analysis")
        assert len(analysis) == 1
        assert analysis[0].id == "alpha_analysis"

        reporting = loaded_registry.list_by_category("reporting")
        assert len(reporting) == 1
        assert reporting[0].id == "beta_reporting"

    def test_filter_by_nonexistent_category(self, loaded_registry: SkillRegistry):
        """No match returns an empty list."""
        assert loaded_registry.list_by_category("bidding") == []


# ===========================================================================
# 6. list_by_platform
# ===========================================================================


class TestListByPlatform:
    def test_filter_by_platform(self, loaded_registry: SkillRegistry):
        """list_by_platform returns skills that include the platform."""
        meta_skills = loaded_registry.list_by_platform("meta")
        ids = [s.id for s in meta_skills]
        # Both alpha (google_ads + meta) and beta (meta) have meta
        assert "alpha_analysis" in ids
        assert "beta_reporting" in ids

        google_skills = loaded_registry.list_by_platform("google_ads")
        ids = [s.id for s in google_skills]
        assert "alpha_analysis" in ids
        assert "beta_reporting" not in ids


# ===========================================================================
# 7. list_scheduled
# ===========================================================================


class TestListScheduled:
    def test_list_scheduled(self, loaded_registry: SkillRegistry):
        """list_scheduled returns only skills with a schedule."""
        scheduled = loaded_registry.list_scheduled()
        assert len(scheduled) == 1
        assert scheduled[0].id == "beta_reporting"
        assert scheduled[0].schedule == "0 8 * * *"


# ===========================================================================
# 8. build_routing_index
# ===========================================================================


class TestBuildRoutingIndex:
    def test_routing_index_format(self, loaded_registry: SkillRegistry):
        """build_routing_index produces 'id | description | tags' lines."""
        index = loaded_registry.build_routing_index()
        lines = index.strip().split("\n")
        assert len(lines) == 2
        # First line is alpha (sorted by ID)
        assert lines[0].startswith("alpha_analysis |")
        assert "alpha, performance" in lines[0]
        assert lines[1].startswith("beta_reporting |")
        assert "beta, reporting" in lines[1]

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_routing_index_empty(self, tmp_path: Path):
        """Empty registry produces an empty string."""
        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()
        assert reg.build_routing_index() == ""


# ===========================================================================
# 9. search
# ===========================================================================


class TestSearch:
    def test_search_finds_by_keyword(self, loaded_registry: SkillRegistry):
        """search() finds skills matching keywords in tags/description."""
        results = loaded_registry.search("alpha")
        assert len(results) >= 1
        assert results[0].id == "alpha_analysis"

    def test_search_no_match(self, loaded_registry: SkillRegistry):
        """search() returns empty list when nothing matches."""
        results = loaded_registry.search("zzzznonexistent")
        assert results == []

    def test_search_empty_query(self, loaded_registry: SkillRegistry):
        """search() with empty/whitespace query returns empty list."""
        assert loaded_registry.search("") == []
        assert loaded_registry.search("   ") == []


# ===========================================================================
# 10. reload
# ===========================================================================


class TestReload:
    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_reload_clears_and_reloads(self, skills_dir: Path):
        """reload() clears existing skills and loads fresh from disk."""
        reg = SkillRegistry(skills_dir=skills_dir)
        reg.load_all()
        assert len(reg) == 2

        # Add a third file to disk
        _write_skill(
            skills_dir,
            "gamma.yaml",
            _skill_yaml(skill_id="gamma_monitoring", name="Gamma", category="monitoring"),
        )

        count = reg.reload()
        assert count == 3
        assert reg.get("gamma_monitoring") is not None


# ===========================================================================
# 11. count / __len__ / __contains__
# ===========================================================================


class TestDunderMethods:
    def test_count_property(self, loaded_registry: SkillRegistry):
        """count property returns number of loaded skills."""
        assert loaded_registry.count == 2

    def test_len(self, loaded_registry: SkillRegistry):
        """__len__ returns number of loaded skills."""
        assert len(loaded_registry) == 2

    def test_contains_existing(self, loaded_registry: SkillRegistry):
        """__contains__ returns True for known skill ID."""
        assert "alpha_analysis" in loaded_registry

    def test_contains_missing(self, loaded_registry: SkillRegistry):
        """__contains__ returns False for unknown skill ID."""
        assert "nonexistent" not in loaded_registry


# ===========================================================================
# Reverse references (cross-skill references / skill graphs)
# ===========================================================================


class TestReverseReferences:
    """Tests for reverse reference index and lookup methods."""

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_reverse_index_built_on_load(self, tmp_path: Path):
        """Reverse index is built when skills have references."""
        # skill_a references skill_b
        data_a = yaml.safe_load(_skill_yaml(skill_id="skill_a", name="Skill A"))
        data_a["references"] = [
            {"skill_id": "skill_b", "relationship": "methodology", "reason": "testing"}
        ]
        _write_skill(tmp_path, "skill_a.yaml", yaml.dump(data_a))
        _write_skill(
            tmp_path,
            "skill_b.yaml",
            _skill_yaml(skill_id="skill_b", name="Skill B"),
        )

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        # Forward lookup
        refs = reg.get_references_for("skill_a")
        assert len(refs) == 1
        ref_skill, rel, reason = refs[0]
        assert ref_skill.id == "skill_b"
        assert rel == "methodology"
        assert reason == "testing"

        # Reverse lookup
        referenced_by = reg.get_referenced_by("skill_b")
        assert "skill_a" in referenced_by

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_get_references_for_no_refs(self, tmp_path: Path):
        """Skills without references return empty list."""
        _write_skill(
            tmp_path,
            "solo.yaml",
            _skill_yaml(skill_id="solo_skill", name="Solo"),
        )
        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()
        assert reg.get_references_for("solo_skill") == []

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_get_referenced_by_not_referenced(self, tmp_path: Path):
        """Skills not referenced by any other skill return empty set."""
        _write_skill(
            tmp_path,
            "alone.yaml",
            _skill_yaml(skill_id="alone_skill", name="Alone"),
        )
        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()
        assert reg.get_referenced_by("alone_skill") == set()

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_get_references_for_unknown_skill(self, tmp_path: Path):
        """get_references_for with unknown skill_id returns empty list."""
        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()
        assert reg.get_references_for("nonexistent") == []

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_get_referenced_by_unknown_skill(self, tmp_path: Path):
        """get_referenced_by with unknown skill_id returns empty set."""
        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()
        assert reg.get_referenced_by("nonexistent") == set()

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_dangling_reference_does_not_crash(self, tmp_path: Path):
        """Skill referencing a non-existent skill doesn't break load_all."""
        data = yaml.safe_load(_skill_yaml(skill_id="orphan_ref", name="Orphan Ref"))
        data["references"] = [{"skill_id": "nonexistent_skill", "relationship": "x", "reason": "y"}]
        _write_skill(tmp_path, "orphan.yaml", yaml.dump(data))

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()  # Should not raise
        assert reg.get("orphan_ref") is not None
        refs = reg.get_references_for("orphan_ref")
        # Dangling references return empty (target not in registry)
        assert len(refs) == 0
