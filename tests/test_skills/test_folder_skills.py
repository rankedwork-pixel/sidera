"""Tests for folder-based skill support.

Covers:
- Schema: context_files and source_dir fields on SkillDefinition
- Schema: resolve_context_files() glob resolution
- Schema: load_context_text() reads and formats context
- Registry: load_all() discovers folder-based skills (subdirectory/skill.yaml)
- Registry: flat files and folder skills coexist
- Registry: folder skill without context_files works like a flat file
- Validation: context_files patterns that match no files produce warnings
- Agent integration: context files are injected into system prompt
- Library: creative_analysis loads as a folder-based skill with context
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from src.skills.schema import (
    SkillDefinition,
    load_context_text,
    load_skill_from_yaml,
    resolve_context_files,
    validate_skill,
)

# ---------------------------------------------------------------------------
# Shared mock tools
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
# Helper: write a valid skill YAML
# ---------------------------------------------------------------------------


def _skill_yaml(
    skill_id: str = "folder_skill",
    name: str = "Folder Skill",
    context_files: list[str] | None = None,
    **overrides: object,
) -> str:
    """Return a YAML string for a valid skill definition."""
    data: dict[str, object] = {
        "id": skill_id,
        "name": name,
        "version": "1.0",
        "description": f"Description for {name}",
        "category": "analysis",
        "platforms": ["google_ads"],
        "tags": ["test", "folder", "context", "examples", "analysis"],
        "tools_required": ["get_meta_campaigns"],
        "model": "sonnet",
        "max_turns": 10,
        "system_supplement": f"System supplement for {name}.",
        "prompt_template": f"Run {name} analysis.",
        "output_format": "## Results\nShow results.",
        "business_guidance": "Follow best practices.",
        "requires_approval": True,
        "author": "sidera",
    }
    if context_files is not None:
        data["context_files"] = context_files
    data.update(overrides)
    return yaml.dump(data, default_flow_style=False)


def _write(path: Path, content: str) -> Path:
    """Write text to a file, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ===========================================================================
# 1. SkillDefinition — context_files and source_dir fields
# ===========================================================================


class TestSkillDefinitionFields:
    """Verify new fields exist and default correctly."""

    def test_default_context_files_empty(self):
        """context_files defaults to empty tuple."""
        skill = SkillDefinition(
            id="test",
            name="Test",
            version="1.0",
            description="desc",
            category="analysis",
            platforms=("google_ads",),
            tags=("test",),
            tools_required=("get_meta_campaigns",),
            model="sonnet",
            max_turns=10,
            system_supplement="sup",
            prompt_template="tmpl",
            output_format="fmt",
            business_guidance="guide",
        )
        assert skill.context_files == ()
        assert skill.source_dir == ""

    def test_context_files_set(self):
        """context_files and source_dir can be set."""
        skill = SkillDefinition(
            id="test",
            name="Test",
            version="1.0",
            description="desc",
            category="analysis",
            platforms=("google_ads",),
            tags=("test",),
            tools_required=("get_meta_campaigns",),
            model="sonnet",
            max_turns=10,
            system_supplement="sup",
            prompt_template="tmpl",
            output_format="fmt",
            business_guidance="guide",
            context_files=("examples/*.md", "context/*.md"),
            source_dir="/some/path",
        )
        assert skill.context_files == ("examples/*.md", "context/*.md")
        assert skill.source_dir == "/some/path"


# ===========================================================================
# 2. load_skill_from_yaml — context_files parsing
# ===========================================================================


class TestLoadSkillContextFiles:
    """Verify YAML loader picks up context_files."""

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_yaml_with_context_files(self, tmp_path: Path):
        """YAML with context_files produces the correct tuple."""
        yaml_str = _skill_yaml(
            context_files=["examples/*.md", "context/*.md"],
        )
        p = _write(tmp_path / "skill.yaml", yaml_str)
        skill = load_skill_from_yaml(p)
        assert skill.context_files == ("examples/*.md", "context/*.md")
        assert skill.source_dir == str(tmp_path)

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_yaml_without_context_files(self, tmp_path: Path):
        """YAML without context_files defaults to empty tuple."""
        yaml_str = _skill_yaml()
        p = _write(tmp_path / "skill.yaml", yaml_str)
        skill = load_skill_from_yaml(p)
        assert skill.context_files == ()
        assert skill.source_dir == str(tmp_path)

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_source_dir_is_parent_of_yaml(self, tmp_path: Path):
        """source_dir is always the parent directory of the YAML file."""
        subdir = tmp_path / "my_skill"
        subdir.mkdir()
        yaml_str = _skill_yaml(skill_id="my_skill")
        p = _write(subdir / "skill.yaml", yaml_str)
        skill = load_skill_from_yaml(p)
        assert skill.source_dir == str(subdir)


# ===========================================================================
# 3. resolve_context_files — glob resolution
# ===========================================================================


class TestResolveContextFiles:
    """Verify glob pattern resolution."""

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_resolves_matching_files(self, tmp_path: Path):
        """Glob patterns resolve to existing files."""
        # Create skill and context files
        _write(
            tmp_path / "skill.yaml",
            _skill_yaml(
                context_files=["examples/*.md", "context/*.md"],
            ),
        )
        _write(tmp_path / "examples" / "ex1.md", "Example 1")
        _write(tmp_path / "examples" / "ex2.md", "Example 2")
        _write(tmp_path / "context" / "rubric.md", "Rubric")

        skill = load_skill_from_yaml(tmp_path / "skill.yaml")
        resolved = resolve_context_files(skill)

        assert len(resolved) == 3
        names = sorted(f.name for f in resolved)
        assert names == ["ex1.md", "ex2.md", "rubric.md"]

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_ignores_non_matching_patterns(self, tmp_path: Path):
        """Patterns that match nothing produce empty list."""
        _write(
            tmp_path / "skill.yaml",
            _skill_yaml(
                context_files=["nonexistent/*.md"],
            ),
        )
        skill = load_skill_from_yaml(tmp_path / "skill.yaml")
        resolved = resolve_context_files(skill)
        assert resolved == []

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_no_context_files_returns_empty(self, tmp_path: Path):
        """Skill without context_files returns empty list."""
        _write(tmp_path / "skill.yaml", _skill_yaml())
        skill = load_skill_from_yaml(tmp_path / "skill.yaml")
        resolved = resolve_context_files(skill)
        assert resolved == []

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_deduplicates_across_patterns(self, tmp_path: Path):
        """Overlapping patterns don't produce duplicate entries."""
        _write(
            tmp_path / "skill.yaml",
            _skill_yaml(
                context_files=["*.md", "*.md"],
            ),
        )
        _write(tmp_path / "readme.md", "Hello")

        skill = load_skill_from_yaml(tmp_path / "skill.yaml")
        resolved = resolve_context_files(skill)
        # Should only have 1 entry, not 2 (but skill.yaml isn't .md)
        assert len(resolved) == 1

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_skips_directories(self, tmp_path: Path):
        """Directories matching the glob are excluded."""
        _write(
            tmp_path / "skill.yaml",
            _skill_yaml(
                context_files=["examples/*"],
            ),
        )
        _write(tmp_path / "examples" / "file.md", "Content")
        (tmp_path / "examples" / "subdir").mkdir()

        skill = load_skill_from_yaml(tmp_path / "skill.yaml")
        resolved = resolve_context_files(skill)
        assert len(resolved) == 1
        assert resolved[0].name == "file.md"


# ===========================================================================
# 4. load_context_text — full text composition
# ===========================================================================


class TestLoadContextText:
    """Verify context text assembly."""

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_formats_sections_with_headers(self, tmp_path: Path):
        """Each file gets a '# Context: relative/path' header."""
        _write(
            tmp_path / "skill.yaml",
            _skill_yaml(
                context_files=["examples/*.md"],
            ),
        )
        _write(tmp_path / "examples" / "demo.md", "Demo content here")

        skill = load_skill_from_yaml(tmp_path / "skill.yaml")
        text = load_context_text(skill)

        assert "# Context: examples/demo.md" in text
        assert "Demo content here" in text

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_multiple_files_combined(self, tmp_path: Path):
        """Multiple files are combined with section headers."""
        _write(
            tmp_path / "skill.yaml",
            _skill_yaml(
                context_files=["context/*.md"],
            ),
        )
        _write(tmp_path / "context" / "a.md", "Content A")
        _write(tmp_path / "context" / "b.md", "Content B")

        skill = load_skill_from_yaml(tmp_path / "skill.yaml")
        text = load_context_text(skill)

        assert "# Context: context/a.md" in text
        assert "Content A" in text
        assert "# Context: context/b.md" in text
        assert "Content B" in text

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_empty_files_skipped(self, tmp_path: Path):
        """Empty context files are silently skipped."""
        _write(
            tmp_path / "skill.yaml",
            _skill_yaml(
                context_files=["context/*.md"],
            ),
        )
        _write(tmp_path / "context" / "empty.md", "")
        _write(tmp_path / "context" / "real.md", "Real content")

        skill = load_skill_from_yaml(tmp_path / "skill.yaml")
        text = load_context_text(skill)

        assert "empty.md" not in text
        assert "Real content" in text

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_no_context_returns_empty(self, tmp_path: Path):
        """Skill without context_files returns empty string."""
        _write(tmp_path / "skill.yaml", _skill_yaml())
        skill = load_skill_from_yaml(tmp_path / "skill.yaml")
        text = load_context_text(skill)
        assert text == ""


# ===========================================================================
# 5. Registry — folder-based skill discovery
# ===========================================================================


class TestRegistryFolderDiscovery:
    """Verify SkillRegistry discovers folder-based skills."""

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_loads_folder_skill(self, tmp_path: Path):
        """Registry discovers skill.yaml inside a subdirectory."""
        from src.skills.registry import SkillRegistry

        skill_dir = tmp_path / "my_analysis"
        skill_dir.mkdir()
        _write(
            skill_dir / "skill.yaml",
            _skill_yaml(
                skill_id="my_analysis",
                name="My Analysis",
                context_files=["context/*.md"],
            ),
        )
        _write(skill_dir / "context" / "info.md", "Info")

        reg = SkillRegistry(skills_dir=tmp_path)
        count = reg.load_all()

        assert count == 1
        skill = reg.get("my_analysis")
        assert skill is not None
        assert skill.context_files == ("context/*.md",)

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_flat_and_folder_coexist(self, tmp_path: Path):
        """Flat files and folder skills load together."""
        from src.skills.registry import SkillRegistry

        # Flat file
        _write(
            tmp_path / "flat_skill.yaml",
            _skill_yaml(
                skill_id="flat_skill",
                name="Flat Skill",
            ),
        )

        # Folder-based
        folder = tmp_path / "folder_skill"
        folder.mkdir()
        _write(
            folder / "skill.yaml",
            _skill_yaml(
                skill_id="folder_skill",
                name="Folder Skill",
                context_files=["examples/*.md"],
            ),
        )
        _write(folder / "examples" / "ex.md", "Example")

        reg = SkillRegistry(skills_dir=tmp_path)
        count = reg.load_all()

        assert count == 2
        assert reg.get("flat_skill") is not None
        assert reg.get("folder_skill") is not None
        assert reg.get("folder_skill").context_files == ("examples/*.md",)
        assert reg.get("flat_skill").context_files == ()

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_folder_without_skill_yaml_ignored(self, tmp_path: Path):
        """Subdirectory without skill.yaml is silently skipped."""
        from src.skills.registry import SkillRegistry

        (tmp_path / "random_folder").mkdir()
        _write(
            tmp_path / "random_folder" / "notes.txt",
            "Not a skill",
        )

        reg = SkillRegistry(skills_dir=tmp_path)
        count = reg.load_all()
        assert count == 0

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_folder_skill_without_context_files(self, tmp_path: Path):
        """Folder skill without context_files loads like a flat file."""
        from src.skills.registry import SkillRegistry

        folder = tmp_path / "plain_folder"
        folder.mkdir()
        _write(
            folder / "skill.yaml",
            _skill_yaml(
                skill_id="plain_folder",
                name="Plain Folder",
            ),
        )

        reg = SkillRegistry(skills_dir=tmp_path)
        count = reg.load_all()

        assert count == 1
        skill = reg.get("plain_folder")
        assert skill is not None
        assert skill.context_files == ()

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_duplicate_id_across_flat_and_folder(self, tmp_path: Path):
        """First-discovered wins when same skill ID (folders before flat)."""
        from src.skills.registry import SkillRegistry

        # Flat file (loaded in Phase 2 of discovery)
        _write(
            tmp_path / "alpha.yaml",
            _skill_yaml(
                skill_id="dupe_skill",
                name="Flat Version",
            ),
        )

        # Folder-based skill (loaded in Phase 1 of discovery)
        folder = tmp_path / "zz_folder"
        folder.mkdir()
        _write(
            folder / "skill.yaml",
            _skill_yaml(
                skill_id="dupe_skill",
                name="Folder Version",
            ),
        )

        reg = SkillRegistry(skills_dir=tmp_path)
        count = reg.load_all()

        assert count == 1
        # Folder-based skill wins (directories scanned before flat files)
        assert reg.get("dupe_skill").name == "Folder Version"


# ===========================================================================
# 6. Validation — context_files patterns
# ===========================================================================


class TestValidationContextFiles:
    """Verify validation handles context_files correctly."""

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_valid_skill_with_context(self, tmp_path: Path):
        """Skill with context_files that resolve passes validation."""
        _write(
            tmp_path / "skill.yaml",
            _skill_yaml(
                context_files=["context/*.md"],
            ),
        )
        _write(tmp_path / "context" / "info.md", "Info")

        skill = load_skill_from_yaml(tmp_path / "skill.yaml")
        errors = validate_skill(skill)
        assert errors == []

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_skill_without_context_passes(self, tmp_path: Path):
        """Skill without context_files passes validation."""
        _write(tmp_path / "skill.yaml", _skill_yaml())
        skill = load_skill_from_yaml(tmp_path / "skill.yaml")
        errors = validate_skill(skill)
        assert errors == []

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_unresolvable_patterns_warn(self, tmp_path: Path):
        """Patterns matching no files produce a validation error."""
        _write(
            tmp_path / "skill.yaml",
            _skill_yaml(
                context_files=["nonexistent/*.md"],
            ),
        )
        skill = load_skill_from_yaml(tmp_path / "skill.yaml")
        errors = validate_skill(skill)
        assert len(errors) == 1
        assert "matched no files" in errors[0]


# ===========================================================================
# 7. Library integration — creative_analysis
# ===========================================================================


class TestCreativeAnalysisFolder:
    """Verify the real creative_analysis skill loads from its folder."""

    def test_creative_analysis_loads(self):
        """creative_analysis loads from its folder."""
        from src.skills.registry import SkillRegistry

        reg = SkillRegistry()
        reg.load_all()

        skill = reg.get("creative_analysis")
        assert skill is not None
        assert skill.version == "2.0"

    def test_creative_analysis_has_context_files(self):
        """creative_analysis has context_files configured."""
        from src.skills.registry import SkillRegistry

        reg = SkillRegistry()
        reg.load_all()

        skill = reg.get("creative_analysis")
        assert skill is not None
        assert len(skill.context_files) == 3
        assert "examples/*.md" in skill.context_files
        assert "context/*.md" in skill.context_files
        assert "guidelines/*.md" in skill.context_files

    def test_creative_analysis_context_resolves(self):
        """creative_analysis context files actually resolve to files."""
        from src.skills.registry import SkillRegistry

        reg = SkillRegistry()
        reg.load_all()

        skill = reg.get("creative_analysis")
        resolved = resolve_context_files(skill)
        # We created 5 context files:
        # examples/good_analysis_ecommerce.md
        # examples/good_analysis_lead_gen.md
        # context/scoring_rubric.md
        # context/platform_benchmarks.md
        # guidelines/decision_framework.md
        assert len(resolved) == 5

    def test_creative_analysis_context_text_nonempty(self):
        """creative_analysis produces non-empty context text."""
        from src.skills.registry import SkillRegistry

        reg = SkillRegistry()
        reg.load_all()

        skill = reg.get("creative_analysis")
        text = load_context_text(skill)
        assert len(text) > 1000  # We wrote ~12K chars of context
        assert "# Context:" in text

    def test_all_23_skills_still_load(self):
        """All 23 skills load (folder-based creative_analysis + fb_creative_cuts included)."""
        from src.skills.registry import SkillRegistry

        reg = SkillRegistry()
        count = reg.load_all()
        assert count == 23

    def test_creative_analysis_passes_validation(self):
        """creative_analysis passes full validation."""
        from src.skills.registry import SkillRegistry

        reg = SkillRegistry()
        reg.load_all()

        skill = reg.get("creative_analysis")
        errors = validate_skill(skill)
        assert errors == [], f"Validation errors: {errors}"
