"""Tests for context file descriptions in skill manifests.

Verifies that context_file_descriptions on SkillDefinition flow through
to build_context_manifest and appear in the lazy manifest text.
"""

from __future__ import annotations

from pathlib import Path

from src.mcp_servers.context import build_context_manifest
from src.skills.schema import SkillDefinition, load_skill_from_yaml

# ============================================================
# Tests — build_context_manifest with descriptions
# ============================================================


class TestManifestDescriptions:
    def test_manifest_with_descriptions(self, tmp_path: Path):
        """Descriptions should appear after file size in manifest entries."""
        # Create a fake context file
        examples_dir = tmp_path / "examples"
        examples_dir.mkdir()
        (examples_dir / "good_analysis.md").write_text("Example analysis content")

        manifest = build_context_manifest(
            skill_id="test_skill",
            source_dir=str(tmp_path),
            context_files=("examples/*.md",),
            descriptions={"examples/*.md": "Real-world analysis examples"},
        )

        assert "good_analysis.md" in manifest
        assert "Real-world analysis examples" in manifest

    def test_manifest_without_descriptions(self, tmp_path: Path):
        """Without descriptions, file entry lines should not have dash-space-dash."""
        examples_dir = tmp_path / "examples"
        examples_dir.mkdir()
        (examples_dir / "file.md").write_text("Content")

        manifest = build_context_manifest(
            skill_id="test_skill",
            source_dir=str(tmp_path),
            context_files=("examples/*.md",),
            descriptions=None,
        )

        assert "file.md" in manifest
        # Check only the file entry line, not the whole manifest (footer has " — ")
        lines = manifest.split("\n")
        file_line = [line for line in lines if "file.md" in line][0]
        assert " — " not in file_line

    def test_manifest_partial_descriptions(self, tmp_path: Path):
        """Only patterns with descriptions should get the suffix."""
        examples_dir = tmp_path / "examples"
        guidelines_dir = tmp_path / "guidelines"
        examples_dir.mkdir()
        guidelines_dir.mkdir()
        (examples_dir / "ex.md").write_text("Example")
        (guidelines_dir / "guide.md").write_text("Guideline")

        manifest = build_context_manifest(
            skill_id="test_skill",
            source_dir=str(tmp_path),
            context_files=("examples/*.md", "guidelines/*.md"),
            descriptions={"examples/*.md": "Examples for reference"},
        )

        assert "Examples for reference" in manifest
        # Guidelines should not have a description
        assert "guide.md" in manifest
        lines = manifest.split("\n")
        guide_line = [line for line in lines if "guide.md" in line][0]
        assert " — " not in guide_line


# ============================================================
# Tests — SkillDefinition field
# ============================================================


class TestSkillDefinitionField:
    def test_default_empty(self):
        """context_file_descriptions defaults to empty tuple."""
        skill = SkillDefinition(
            id="test",
            name="Test",
            version="1.0",
            description="Test skill",
            category="analysis",
            platforms=("google_ads",),
            tags=("test",),
            tools_required=(),
            model="haiku",
            max_turns=1,
            system_supplement="test",
            prompt_template="test",
            output_format="test",
            business_guidance="test",
        )
        assert skill.context_file_descriptions == ()

    def test_yaml_loading(self, tmp_path: Path):
        """context_file_descriptions should load from YAML."""
        yaml_content = """\
id: test_skill
name: Test Skill
version: "1.0"
description: A test skill
category: analysis
platforms: [google_ads]
tags: [test]
tools_required: []
model: haiku
system_supplement: "Test supplement"
prompt_template: "Test prompt"
output_format: "Test format"
business_guidance: "Test guidance"
context_files:
  - "examples/*.md"
context_file_descriptions:
  - pattern: "examples/*.md"
    description: "Real-world analysis examples for different verticals"
"""
        yaml_path = tmp_path / "test_skill.yaml"
        yaml_path.write_text(yaml_content)

        skill = load_skill_from_yaml(yaml_path)
        assert len(skill.context_file_descriptions) == 1
        assert skill.context_file_descriptions[0] == (
            "examples/*.md",
            "Real-world analysis examples for different verticals",
        )

    def test_yaml_loading_no_descriptions(self, tmp_path: Path):
        """Missing context_file_descriptions should default to empty."""
        yaml_content = """\
id: test_skill
name: Test Skill
version: "1.0"
description: A test skill
category: analysis
platforms: [google_ads]
tags: [test]
tools_required: []
model: haiku
system_supplement: "Test supplement"
prompt_template: "Test prompt"
output_format: "Test format"
business_guidance: "Test guidance"
"""
        yaml_path = tmp_path / "test_skill.yaml"
        yaml_path.write_text(yaml_content)

        skill = load_skill_from_yaml(yaml_path)
        assert skill.context_file_descriptions == ()
