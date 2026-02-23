"""Tests for src.skills.anthropic_compat — Anthropic SKILL.md format converter.

Covers:
- Parsing SKILL.md files (frontmatter + body + subdirectories)
- Writing SKILL.md files to disk
- Converting Anthropic → Sidera format
- Converting Sidera → Anthropic format
- Round-trip fidelity (Sidera → Anthropic → Sidera)
- Validation of Anthropic bundles
- Format detection (is_anthropic_bundle)
- Importing Anthropic skills (directory + ZIP)
- Exporting Sidera skills to Anthropic format (directory + ZIP)
- Listing Anthropic skills in a directory
- Tool mapping between ecosystems
- Edge cases (missing fields, malformed YAML, non-kebab names)
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
import yaml

from src.skills.anthropic_compat import (
    _ANTHROPIC_TO_SIDERA_TOOLS,
    _KEBAB_CASE_RE,
    _SIDERA_TO_ANTHROPIC_TOOLS,
    AnthropicSkillBundle,
    _split_frontmatter,
    anthropic_to_sidera,
    export_to_anthropic_dir,
    export_to_anthropic_zip,
    import_anthropic_skill,
    is_anthropic_bundle,
    list_anthropic_skills,
    parse_skill_md,
    sidera_to_anthropic,
    validate_anthropic_bundle,
    write_skill_md,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_SKILL_MD = """\
---
name: my-test-skill
description: A test skill for unit tests
---

## Instructions

Do something useful.
"""

_FULL_SKILL_MD = """\
---
name: weekly-spend-report
description: Generate a weekly spend report
license: MIT
allowed-tools:
  - WebFetch
  - Bash
  - Read
compatibility: ">=1.0"
metadata:
  author: test-author
  version: "2.0"
  category: reporting
  tags:
    - weekly
    - spend
---

## Instructions

Generate a comprehensive weekly spend report.

## Business Rules

Always compare platform-reported conversions against backend data.
"""


def _create_skill_md(
    tmp_path: Path,
    name: str = "my-test-skill",
    content: str = _MINIMAL_SKILL_MD,
    *,
    with_scripts: bool = False,
    with_references: bool = False,
    with_assets: bool = False,
) -> Path:
    """Create a minimal Anthropic skill bundle on disk."""
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    if with_scripts:
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "run.py").write_text("print('hello')")

    if with_references:
        refs_dir = skill_dir / "references"
        refs_dir.mkdir()
        (refs_dir / "guide.md").write_text("# Guide\nSome docs.")

    if with_assets:
        assets_dir = skill_dir / "assets"
        assets_dir.mkdir()
        (assets_dir / "template.txt").write_text("Template content")

    return skill_dir


def _create_skill_md_zip(tmp_path: Path, name: str = "my-test-skill") -> Path:
    """Create a ZIP file containing an Anthropic skill bundle."""
    skill_dir = _create_skill_md(tmp_path / "src", name, with_references=True)
    zip_path = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in skill_dir.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(tmp_path / "src"))
    return zip_path


# ===========================================================================
# Tests: _split_frontmatter
# ===========================================================================


class TestSplitFrontmatter:
    def test_valid_frontmatter(self):
        fm, body = _split_frontmatter("---\nname: test\n---\nBody here")
        assert fm == "name: test"
        assert "Body here" in body

    def test_no_frontmatter(self):
        fm, body = _split_frontmatter("Just some text")
        assert fm is None
        assert body == "Just some text"

    def test_no_closing_delimiter(self):
        fm, body = _split_frontmatter("---\nname: test\nno closing")
        assert fm is None
        assert "no closing" in body

    def test_empty_frontmatter(self):
        fm, body = _split_frontmatter("---\n\n---\nBody")
        assert fm == ""
        assert "Body" in body

    def test_multiline_frontmatter(self):
        text = "---\nname: test\ndescription: foo\ntags:\n  - a\n  - b\n---\n# Hello"
        fm, body = _split_frontmatter(text)
        assert fm is not None
        assert "name: test" in fm
        assert "tags:" in fm
        assert "# Hello" in body


# ===========================================================================
# Tests: parse_skill_md
# ===========================================================================


class TestParseSkillMd:
    def test_parse_minimal(self, tmp_path):
        skill_dir = _create_skill_md(tmp_path)
        bundle = parse_skill_md(skill_dir)
        assert bundle.name == "my-test-skill"
        assert bundle.description == "A test skill for unit tests"
        assert "Do something useful." in bundle.body_markdown
        assert bundle.source_dir == str(skill_dir)

    def test_parse_full(self, tmp_path):
        skill_dir = _create_skill_md(
            tmp_path,
            "weekly-spend-report",
            _FULL_SKILL_MD,
        )
        bundle = parse_skill_md(skill_dir)
        assert bundle.name == "weekly-spend-report"
        assert bundle.license == "MIT"
        assert "WebFetch" in bundle.allowed_tools
        assert "Bash" in bundle.allowed_tools
        assert "Read" in bundle.allowed_tools
        assert bundle.compatibility == ">=1.0"
        assert bundle.metadata["author"] == "test-author"
        assert bundle.metadata["version"] == "2.0"
        assert bundle.metadata["category"] == "reporting"
        assert "weekly" in bundle.metadata["tags"]

    def test_parse_from_file_path(self, tmp_path):
        skill_dir = _create_skill_md(tmp_path)
        skill_md = skill_dir / "SKILL.md"
        bundle = parse_skill_md(skill_md)
        assert bundle.name == "my-test-skill"

    def test_parse_with_subdirectories(self, tmp_path):
        skill_dir = _create_skill_md(
            tmp_path,
            with_scripts=True,
            with_references=True,
            with_assets=True,
        )
        bundle = parse_skill_md(skill_dir)
        assert any("scripts/run.py" in s for s in bundle.scripts)
        assert any("references/guide.md" in s for s in bundle.references)
        assert any("assets/template.txt" in s for s in bundle.assets)

    def test_parse_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            parse_skill_md(tmp_path / "nonexistent")

    def test_parse_no_frontmatter_raises(self, tmp_path):
        skill_dir = tmp_path / "bad-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("No frontmatter here")
        with pytest.raises(ValueError, match="No YAML frontmatter"):
            parse_skill_md(skill_dir)

    def test_parse_allowed_tools_as_string(self, tmp_path):
        content = "---\nname: test-skill\ndescription: test\nallowed-tools: Bash Read\n---\nBody"
        skill_dir = _create_skill_md(tmp_path, "test-skill", content)
        bundle = parse_skill_md(skill_dir)
        assert bundle.allowed_tools == ["Bash", "Read"]

    def test_parse_empty_metadata(self, tmp_path):
        content = "---\nname: test-skill\ndescription: test\n---\nBody"
        skill_dir = _create_skill_md(tmp_path, "test-skill", content)
        bundle = parse_skill_md(skill_dir)
        assert bundle.metadata == {}


# ===========================================================================
# Tests: write_skill_md
# ===========================================================================


class TestWriteSkillMd:
    def test_write_minimal(self, tmp_path):
        bundle = AnthropicSkillBundle(
            name="test-skill",
            description="A test",
            body_markdown="## Instructions\n\nDo stuff.",
        )
        out = write_skill_md(bundle, tmp_path / "output")
        assert (out / "SKILL.md").exists()

        # Parse back
        parsed = parse_skill_md(out)
        assert parsed.name == "test-skill"
        assert parsed.description == "A test"
        assert "Do stuff." in parsed.body_markdown

    def test_write_with_metadata(self, tmp_path):
        bundle = AnthropicSkillBundle(
            name="test-skill",
            description="A test",
            license="MIT",
            allowed_tools=["Bash", "WebFetch"],
            compatibility=">=1.0",
            metadata={"author": "me", "tags": ["a", "b"]},
            body_markdown="Instructions here.",
        )
        out = write_skill_md(bundle, tmp_path / "output")
        content = (out / "SKILL.md").read_text()
        assert "MIT" in content
        assert "Bash" in content
        assert "WebFetch" in content

    def test_write_copies_source_files(self, tmp_path):
        # Create source with scripts
        src = tmp_path / "source"
        src.mkdir()
        scripts_dir = src / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "run.py").write_text("print('hi')")

        bundle = AnthropicSkillBundle(
            name="test-skill",
            description="A test",
            body_markdown="Body",
            source_dir=str(src),
        )
        out = write_skill_md(bundle, tmp_path / "output")
        assert (out / "scripts" / "run.py").exists()

    def test_write_creates_directory(self, tmp_path):
        bundle = AnthropicSkillBundle(
            name="test-skill",
            description="A test",
            body_markdown="Body",
        )
        deep_path = tmp_path / "a" / "b" / "c"
        out = write_skill_md(bundle, deep_path)
        assert out.exists()
        assert (out / "SKILL.md").exists()


# ===========================================================================
# Tests: anthropic_to_sidera
# ===========================================================================


class TestAnthropicToSidera:
    def test_basic_conversion(self):
        bundle = AnthropicSkillBundle(
            name="my-test-skill",
            description="Test description",
            body_markdown="Do the thing.",
        )
        result, warnings = anthropic_to_sidera(bundle)
        assert result["id"] == "my_test_skill"
        assert result["name"] == "My Test Skill"
        assert result["description"] == "Test description"
        assert result["system_supplement"] == "Do the thing."
        assert result["model"] == "sonnet"
        assert result["max_turns"] == 20
        assert result["requires_approval"] is True
        assert warnings == []

    def test_tool_mapping(self):
        bundle = AnthropicSkillBundle(
            name="web-skill",
            description="Uses web tools",
            allowed_tools=["WebFetch", "WebSearch", "Bash", "Read"],
            body_markdown="Instructions",
        )
        result, warnings = anthropic_to_sidera(bundle)
        assert "fetch_web_page" in result["tools_required"]
        assert "web_search" in result["tools_required"]
        # Bash and Read have no Sidera equivalent
        assert len(warnings) == 1
        assert "Bash" in warnings[0]
        assert "Read" in warnings[0]

    def test_tool_with_qualifier(self):
        bundle = AnthropicSkillBundle(
            name="code-skill",
            description="Uses bash",
            allowed_tools=["Bash(python:*)"],
            body_markdown="Run python",
        )
        result, warnings = anthropic_to_sidera(bundle)
        assert len(warnings) == 1
        assert "Bash(python:*)" in warnings[0]

    def test_metadata_extraction(self):
        bundle = AnthropicSkillBundle(
            name="my-skill",
            description="Test",
            metadata={
                "author": "test-author",
                "version": "2.5",
                "category": "analysis",
                "tags": ["a", "b", "c"],
            },
            body_markdown="Body",
        )
        result, _ = anthropic_to_sidera(bundle)
        assert result["author"] == "test-author"
        assert result["version"] == "2.5"
        assert result["category"] == "analysis"
        assert result["tags"] == ["a", "b", "c"]

    def test_tags_as_string(self):
        bundle = AnthropicSkillBundle(
            name="my-skill",
            description="Test",
            metadata={"tags": "single-tag"},
            body_markdown="Body",
        )
        result, _ = anthropic_to_sidera(bundle)
        assert result["tags"] == ["single-tag"]

    def test_license_preserved_in_metadata(self):
        bundle = AnthropicSkillBundle(
            name="licensed-skill",
            description="Test",
            license="MIT",
            body_markdown="Body",
        )
        result, _ = anthropic_to_sidera(bundle)
        assert result["_anthropic_metadata"]["license"] == "MIT"

    def test_compatibility_preserved(self):
        bundle = AnthropicSkillBundle(
            name="compat-skill",
            description="Test",
            compatibility=">=1.0",
            body_markdown="Body",
        )
        result, _ = anthropic_to_sidera(bundle)
        assert result["_anthropic_metadata"]["compatibility"] == ">=1.0"

    def test_round_trip_fields_restored(self):
        bundle = AnthropicSkillBundle(
            name="round-trip-skill",
            description="Test",
            metadata={
                "sidera": {
                    "model": "opus",
                    "max_turns": 5,
                    "schedule": "0 9 * * *",
                    "requires_approval": False,
                    "platforms": ["google_ads", "meta"],
                },
            },
            body_markdown="Body",
        )
        result, _ = anthropic_to_sidera(bundle)
        assert result["model"] == "opus"
        assert result["max_turns"] == 5
        assert result["schedule"] == "0 9 * * *"
        assert result["requires_approval"] is False
        assert result["platforms"] == ["google_ads", "meta"]

    def test_empty_body_uses_default(self):
        bundle = AnthropicSkillBundle(
            name="empty-body",
            description="Test",
            body_markdown="",
        )
        result, _ = anthropic_to_sidera(bundle)
        assert result["system_supplement"] == "Follow the skill instructions."


# ===========================================================================
# Tests: sidera_to_anthropic
# ===========================================================================


class TestSideraToAnthropic:
    def test_basic_conversion(self):
        skill_dict = {
            "id": "creative_analysis",
            "name": "Creative Analysis",
            "description": "Analyze creative performance",
            "system_supplement": "Analyze ad creatives.",
            "business_guidance": "Focus on CTR and ROAS.",
            "output_format": "## Summary\n- Key findings",
            "prompt_template": "Run creative analysis. {analysis_date}",
            "model": "sonnet",
            "max_turns": 10,
            "requires_approval": True,
            "version": "1.0",
            "category": "analysis",
            "tags": ["creative", "ads"],
            "author": "test-author",
        }
        bundle = sidera_to_anthropic(skill_dict)
        assert bundle.name == "creative-analysis"
        assert bundle.description == "Analyze creative performance"
        assert "## Instructions" in bundle.body_markdown
        assert "Analyze ad creatives." in bundle.body_markdown
        assert "## Business Guidance" in bundle.body_markdown
        assert "Focus on CTR and ROAS." in bundle.body_markdown
        assert "## Expected Output" in bundle.body_markdown
        assert "## Usage" in bundle.body_markdown

    def test_tool_mapping(self):
        skill_dict = {
            "id": "web_skill",
            "description": "Uses web tools",
            "tools_required": ["fetch_web_page", "web_search", "get_google_ads_campaigns"],
        }
        bundle = sidera_to_anthropic(skill_dict)
        assert "WebFetch" in bundle.allowed_tools
        assert "WebSearch" in bundle.allowed_tools
        # get_google_ads_campaigns has no Anthropic equivalent
        assert len(bundle.allowed_tools) == 2

    def test_sidera_fields_preserved_in_metadata(self):
        skill_dict = {
            "id": "test_skill",
            "description": "Test",
            "model": "opus",
            "max_turns": 5,
            "schedule": "0 9 * * *",
            "requires_approval": False,
            "platforms": ("google_ads", "meta"),
            "tools_required": ("get_google_ads_campaigns",),
        }
        bundle = sidera_to_anthropic(skill_dict)
        sidera = bundle.metadata.get("sidera", {})
        assert sidera["model"] == "opus"
        assert sidera["max_turns"] == 5
        assert sidera["schedule"] == "0 9 * * *"
        assert sidera["requires_approval"] is False
        assert sidera["platforms"] == ["google_ads", "meta"]
        # tools_required should also be in sidera block
        assert "get_google_ads_campaigns" in sidera["tools_required"]

    def test_metadata_standard_fields(self):
        skill_dict = {
            "id": "test_skill",
            "description": "Test",
            "author": "me",
            "version": "2.0",
            "category": "monitoring",
            "tags": ("alert", "watch"),
        }
        bundle = sidera_to_anthropic(skill_dict)
        assert bundle.metadata["author"] == "me"
        assert bundle.metadata["version"] == "2.0"
        assert bundle.metadata["category"] == "monitoring"
        assert bundle.metadata["tags"] == ["alert", "watch"]

    def test_empty_sections_omitted(self):
        skill_dict = {
            "id": "minimal",
            "description": "Minimal skill",
            "system_supplement": "Do things.",
        }
        bundle = sidera_to_anthropic(skill_dict)
        assert "## Instructions" in bundle.body_markdown
        assert "## Business Guidance" not in bundle.body_markdown
        assert "## Expected Output" not in bundle.body_markdown
        assert "## Usage" not in bundle.body_markdown


# ===========================================================================
# Tests: Round-trip fidelity
# ===========================================================================


class TestRoundTrip:
    def test_sidera_to_anthropic_to_sidera(self):
        """Core fields survive a Sidera → Anthropic → Sidera round trip."""
        original = {
            "id": "weekly_spend_report",
            "name": "Weekly Spend Report",
            "version": "1.5",
            "description": "Generate a weekly spend report",
            "category": "reporting",
            "tags": ["weekly", "spend", "report"],
            "tools_required": ["fetch_web_page", "web_search"],
            "model": "opus",
            "max_turns": 15,
            "requires_approval": False,
            "platforms": ["google_ads", "meta"],
            "author": "test-author",
            "system_supplement": "Generate a comprehensive weekly spend report.",
            "prompt_template": "Run the report. {analysis_date}",
            "output_format": "## Summary",
            "business_guidance": "Compare platform vs backend.",
        }

        # Forward: Sidera → Anthropic
        anthropic_bundle = sidera_to_anthropic(original)

        # Reverse: Anthropic → Sidera
        restored, warnings = anthropic_to_sidera(anthropic_bundle)

        # Core fields should survive
        assert restored["id"] == original["id"]
        assert restored["version"] == original["version"]
        assert restored["description"] == original["description"]
        assert restored["category"] == original["category"]
        assert restored["author"] == original["author"]

        # Sidera-only fields restored from metadata.sidera block
        assert restored["model"] == original["model"]
        assert restored["max_turns"] == original["max_turns"]
        assert restored["requires_approval"] == original["requires_approval"]
        assert restored["platforms"] == original["platforms"]

        # Tools should round-trip (for those with mappings)
        assert "fetch_web_page" in restored["tools_required"]
        assert "web_search" in restored["tools_required"]

    def test_write_then_parse_round_trip(self, tmp_path):
        """Write a bundle to disk, parse it back, verify fields match."""
        bundle = AnthropicSkillBundle(
            name="round-trip-test",
            description="Testing round trip",
            license="Apache-2.0",
            allowed_tools=["WebFetch", "Bash"],
            metadata={"author": "tester", "version": "1.0"},
            compatibility=">=1.0",
            body_markdown="## Instructions\n\nDo the thing.\n\n## Notes\n\nSome notes.",
        )

        out_dir = write_skill_md(bundle, tmp_path / "output")
        parsed = parse_skill_md(out_dir)

        assert parsed.name == bundle.name
        assert parsed.description == bundle.description
        assert parsed.license == bundle.license
        assert set(parsed.allowed_tools) == set(bundle.allowed_tools)
        assert parsed.compatibility == bundle.compatibility
        assert parsed.metadata["author"] == "tester"
        assert "Do the thing." in parsed.body_markdown


# ===========================================================================
# Tests: validate_anthropic_bundle
# ===========================================================================


class TestValidateAnthropicBundle:
    def test_valid_bundle(self, tmp_path):
        skill_dir = _create_skill_md(tmp_path)
        is_valid, errors, warnings = validate_anthropic_bundle(skill_dir)
        assert is_valid
        assert errors == []

    def test_not_a_directory(self, tmp_path):
        file_path = tmp_path / "not-a-dir.txt"
        file_path.write_text("hi")
        is_valid, errors, _ = validate_anthropic_bundle(file_path)
        assert not is_valid
        assert any("directory" in e.lower() for e in errors)

    def test_missing_skill_md(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        is_valid, errors, _ = validate_anthropic_bundle(empty_dir)
        assert not is_valid
        assert any("SKILL.md" in e for e in errors)

    def test_missing_name(self, tmp_path):
        content = "---\ndescription: test\n---\nBody"
        skill_dir = _create_skill_md(tmp_path, "no-name", content)
        is_valid, errors, _ = validate_anthropic_bundle(skill_dir)
        assert not is_valid
        assert any("name" in e.lower() for e in errors)

    def test_missing_description(self, tmp_path):
        content = "---\nname: test-skill\n---\nBody"
        skill_dir = _create_skill_md(tmp_path, "test-skill", content)
        is_valid, errors, _ = validate_anthropic_bundle(skill_dir)
        assert not is_valid
        assert any("description" in e.lower() for e in errors)

    def test_non_kebab_case_warns(self, tmp_path):
        content = "---\nname: NotKebab\ndescription: test\n---\nBody"
        skill_dir = _create_skill_md(tmp_path, "NotKebab", content)
        is_valid, _, warnings = validate_anthropic_bundle(skill_dir)
        assert is_valid  # It's a warning, not an error
        assert any("kebab" in w.lower() for w in warnings)

    def test_dir_name_mismatch_warns(self, tmp_path):
        content = "---\nname: actual-name\ndescription: test\n---\nBody"
        skill_dir = _create_skill_md(tmp_path, "different-dir", content)
        is_valid, _, warnings = validate_anthropic_bundle(skill_dir)
        assert is_valid
        assert any("directory" in w.lower() for w in warnings)

    def test_malformed_yaml_errors(self, tmp_path):
        content = "---\n: invalid: yaml: [broken\n---\nBody"
        skill_dir = _create_skill_md(tmp_path, "bad-yaml", content)
        is_valid, errors, _ = validate_anthropic_bundle(skill_dir)
        assert not is_valid
        assert any("parse" in e.lower() or "failed" in e.lower() for e in errors)


# ===========================================================================
# Tests: is_anthropic_bundle
# ===========================================================================


class TestIsAnthropicBundle:
    def test_anthropic_directory(self, tmp_path):
        skill_dir = _create_skill_md(tmp_path)
        assert is_anthropic_bundle(skill_dir) is True

    def test_sidera_directory(self, tmp_path):
        d = tmp_path / "sidera-skill"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: test\n---\nBody")
        (d / "skill.yaml").write_text("id: test")
        assert is_anthropic_bundle(d) is False

    def test_manifest_directory(self, tmp_path):
        d = tmp_path / "sidera-bundle"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: test\n---\nBody")
        (d / "manifest.yaml").write_text("skill_id: test")
        assert is_anthropic_bundle(d) is False

    def test_empty_directory(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        assert is_anthropic_bundle(d) is False

    def test_zip_with_skill_md(self, tmp_path):
        zip_path = _create_skill_md_zip(tmp_path)
        assert is_anthropic_bundle(zip_path) is True

    def test_zip_with_sidera_bundle(self, tmp_path):
        d = tmp_path / "src" / "sidera-skill"
        d.mkdir(parents=True)
        (d / "skill.yaml").write_text("id: test")
        (d / "manifest.yaml").write_text("skill_id: test")

        zip_path = tmp_path / "sidera.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for f in d.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(tmp_path / "src"))
        assert is_anthropic_bundle(zip_path) is False

    def test_bad_zip(self, tmp_path):
        bad_zip = tmp_path / "bad.zip"
        bad_zip.write_bytes(b"not a zip")
        assert is_anthropic_bundle(bad_zip) is False

    def test_nonexistent_path(self, tmp_path):
        assert is_anthropic_bundle(tmp_path / "nope") is False

    def test_regular_file(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hi")
        assert is_anthropic_bundle(f) is False


# ===========================================================================
# Tests: import_anthropic_skill
# ===========================================================================


class TestImportAnthropicSkill:
    def test_import_from_directory(self, tmp_path):
        skill_dir = _create_skill_md(tmp_path)
        result = import_anthropic_skill(skill_dir)
        assert result.success
        assert result.skill_id == "my_test_skill"
        assert result.skill_name == "My Test Skill"

    def test_import_with_overrides(self, tmp_path):
        skill_dir = _create_skill_md(tmp_path)
        result = import_anthropic_skill(
            skill_dir,
            new_skill_id="custom_id",
            new_author="custom-author",
            target_department_id="marketing",
            target_role_id="analyst",
        )
        assert result.success
        assert result.skill_id == "custom_id"
        assert result.target_department_id == "marketing"
        assert result.target_role_id == "analyst"

    def test_import_installs_to_disk(self, tmp_path):
        skill_dir = _create_skill_md(
            tmp_path,
            with_references=True,
            with_scripts=True,
        )
        target = tmp_path / "installed"
        result = import_anthropic_skill(skill_dir, target_dir=target)
        assert result.success

        # Check installed files
        installed = target / "my_test_skill"
        assert (installed / "skill.yaml").exists()

        # Context files from references should be in context/references/
        assert (installed / "context" / "references" / "guide.md").exists()

        # Scripts should be in code/
        assert (installed / "code" / "run.py").exists()

    def test_import_from_zip(self, tmp_path):
        zip_path = _create_skill_md_zip(tmp_path)
        result = import_anthropic_skill(zip_path)
        assert result.success
        assert result.skill_id == "my_test_skill"

    def test_import_invalid_zip(self, tmp_path):
        bad_zip = tmp_path / "bad.zip"
        bad_zip.write_bytes(b"not a zip file")
        result = import_anthropic_skill(bad_zip)
        assert not result.success
        assert any("zip" in e.lower() for e in result.errors)

    def test_import_zip_without_skill_md(self, tmp_path):
        zip_path = tmp_path / "empty.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("readme.txt", "hello")
        result = import_anthropic_skill(zip_path)
        assert not result.success
        assert any("SKILL.md" in e for e in result.errors)

    def test_import_invalid_bundle(self, tmp_path):
        content = "---\ndescription: no name field\n---\nBody"
        skill_dir = _create_skill_md(tmp_path, "bad-skill", content)
        result = import_anthropic_skill(skill_dir)
        assert not result.success

    def test_import_records_context_count(self, tmp_path):
        skill_dir = _create_skill_md(
            tmp_path,
            with_references=True,
            with_assets=True,
            with_scripts=True,
        )
        target = tmp_path / "installed"
        result = import_anthropic_skill(skill_dir, target_dir=target)
        assert result.success
        assert result.context_files_count >= 3  # guide.md + template.txt + run.py

    def test_import_skill_yaml_clean(self, tmp_path):
        """Installed skill.yaml should not contain internal fields."""
        skill_dir = _create_skill_md(tmp_path)
        target = tmp_path / "installed"
        import_anthropic_skill(skill_dir, target_dir=target)

        yaml_path = target / "my_test_skill" / "skill.yaml"
        data = yaml.safe_load(yaml_path.read_text())
        # Internal fields should be stripped
        assert "_anthropic_metadata" not in data
        assert "department_id" not in data
        assert "role_id" not in data
        assert "source_dir" not in data
        # Core fields should be present
        assert data["id"] == "my_test_skill"
        assert data["model"] == "sonnet"


# ===========================================================================
# Tests: export_to_anthropic_dir / export_to_anthropic_zip
# ===========================================================================


class TestExportToAnthropic:
    def test_export_dir_creates_skill_md(self, tmp_path):
        skill_dict = {
            "id": "test_skill",
            "name": "Test Skill",
            "description": "A test",
            "system_supplement": "Do things.",
            "version": "1.0",
            "category": "analysis",
            "author": "me",
        }
        out = export_to_anthropic_dir(skill_dict, tmp_path / "output")
        assert (out / "SKILL.md").exists()

        # Verify name is kebab-case
        assert out.name == "test-skill"

        # Verify SKILL.md content
        bundle = parse_skill_md(out)
        assert bundle.name == "test-skill"
        assert bundle.description == "A test"

    def test_export_dir_copies_context_files(self, tmp_path):
        # Create source with context files
        source_dir = tmp_path / "source"
        ctx = source_dir / "context"
        ctx.mkdir(parents=True)
        (ctx / "guide.md").write_text("# Guide")

        code = source_dir / "code"
        code.mkdir()
        (code / "run.py").write_text("print('hi')")

        skill_dict = {"id": "test_skill", "description": "Test"}
        out = export_to_anthropic_dir(skill_dict, tmp_path / "output", source_dir)

        # context → references/context
        assert (out / "references" / "context" / "guide.md").exists()
        # code → scripts
        assert (out / "scripts" / "run.py").exists()

    def test_export_zip(self, tmp_path):
        skill_dict = {
            "id": "test_skill",
            "description": "A test",
            "system_supplement": "Instructions",
        }
        zip_path = tmp_path / "output.zip"
        result = export_to_anthropic_zip(skill_dict, zip_path)
        assert result.exists()
        assert result.suffix == ".zip"

        # Verify ZIP contents
        with zipfile.ZipFile(result) as zf:
            names = zf.namelist()
            assert any("SKILL.md" in n for n in names)

    def test_export_preserves_sidera_fields_in_metadata(self, tmp_path):
        skill_dict = {
            "id": "pmax_analysis",
            "description": "Analyze PMAX campaigns",
            "model": "opus",
            "max_turns": 5,
            "schedule": "0 9 * * 1",
            "requires_approval": False,
            "platforms": ("google_ads",),
            "tools_required": ("get_google_ads_campaigns", "fetch_web_page"),
        }
        out = export_to_anthropic_dir(skill_dict, tmp_path / "output")
        bundle = parse_skill_md(out)

        sidera = bundle.metadata.get("sidera", {})
        assert sidera["model"] == "opus"
        assert sidera["max_turns"] == 5
        assert sidera["schedule"] == "0 9 * * 1"
        assert sidera["requires_approval"] is False

        # Tools should include the Anthropic-mapped ones
        assert "WebFetch" in bundle.allowed_tools


# ===========================================================================
# Tests: list_anthropic_skills
# ===========================================================================


class TestListAnthropicSkills:
    def test_list_empty_directory(self, tmp_path):
        assert list_anthropic_skills(tmp_path) == []

    def test_list_nonexistent_directory(self, tmp_path):
        assert list_anthropic_skills(tmp_path / "nope") == []

    def test_list_multiple_skills(self, tmp_path):
        _create_skill_md(
            tmp_path, "skill-one", "---\nname: skill-one\ndescription: First\n---\nBody 1"
        )
        _create_skill_md(
            tmp_path, "skill-two", "---\nname: skill-two\ndescription: Second\n---\nBody 2"
        )
        _create_skill_md(
            tmp_path, "skill-three", "---\nname: skill-three\ndescription: Third\n---\nBody 3"
        )

        bundles = list_anthropic_skills(tmp_path)
        assert len(bundles) == 3
        names = [b.name for b in bundles]
        assert "skill-one" in names
        assert "skill-two" in names
        assert "skill-three" in names

    def test_skips_non_skill_directories(self, tmp_path):
        _create_skill_md(
            tmp_path, "real-skill", "---\nname: real-skill\ndescription: Real\n---\nBody"
        )
        # Create a dir without SKILL.md
        (tmp_path / "not-a-skill").mkdir()
        (tmp_path / "not-a-skill" / "readme.md").write_text("hi")

        bundles = list_anthropic_skills(tmp_path)
        assert len(bundles) == 1
        assert bundles[0].name == "real-skill"

    def test_skips_unparseable_skills(self, tmp_path):
        _create_skill_md(
            tmp_path, "good-skill", "---\nname: good-skill\ndescription: Good\n---\nBody"
        )
        # Create skill with no frontmatter
        bad = tmp_path / "bad-skill"
        bad.mkdir()
        (bad / "SKILL.md").write_text("No frontmatter here")

        bundles = list_anthropic_skills(tmp_path)
        assert len(bundles) == 1
        assert bundles[0].name == "good-skill"


# ===========================================================================
# Tests: Tool mapping constants
# ===========================================================================


class TestToolMapping:
    def test_reverse_mapping_consistency(self):
        """Every non-None value in _ANTHROPIC_TO_SIDERA_TOOLS should
        appear as a key in _SIDERA_TO_ANTHROPIC_TOOLS."""
        for anthropic_name, sidera_name in _ANTHROPIC_TO_SIDERA_TOOLS.items():
            if sidera_name is not None:
                assert sidera_name in _SIDERA_TO_ANTHROPIC_TOOLS
                assert _SIDERA_TO_ANTHROPIC_TOOLS[sidera_name] == anthropic_name

    def test_known_mappings(self):
        assert _ANTHROPIC_TO_SIDERA_TOOLS["WebFetch"] == "fetch_web_page"
        assert _ANTHROPIC_TO_SIDERA_TOOLS["WebSearch"] == "web_search"
        assert _ANTHROPIC_TO_SIDERA_TOOLS["Bash"] is None
        assert _ANTHROPIC_TO_SIDERA_TOOLS["Read"] is None

    def test_sidera_to_anthropic_known(self):
        assert _SIDERA_TO_ANTHROPIC_TOOLS["fetch_web_page"] == "WebFetch"
        assert _SIDERA_TO_ANTHROPIC_TOOLS["web_search"] == "WebSearch"


# ===========================================================================
# Tests: Kebab-case regex
# ===========================================================================


class TestKebabCaseRegex:
    @pytest.mark.parametrize(
        "name",
        [
            "my-skill",
            "a",
            "weekly-spend-report",
            "skill123",
            "a-b-c-d",
            "test1-test2",
        ],
    )
    def test_valid_kebab_case(self, name):
        assert _KEBAB_CASE_RE.match(name) is not None

    @pytest.mark.parametrize(
        "name",
        [
            "MySkill",
            "my_skill",
            "123-skill",
            "-leading-dash",
            "trailing-dash-",
            "double--dash",
            "UPPER",
            "",
        ],
    )
    def test_invalid_kebab_case(self, name):
        assert _KEBAB_CASE_RE.match(name) is None


# ===========================================================================
# Tests: portability.py integration
# ===========================================================================


class TestPortabilityIntegration:
    """Test the Anthropic format integration in portability.py."""

    def test_validate_bundle_detects_anthropic(self, tmp_path):
        """validate_bundle auto-detects Anthropic format."""
        from src.skills.portability import validate_bundle

        skill_dir = _create_skill_md(tmp_path)
        result = validate_bundle(skill_dir)
        assert result.success
        assert result.skill_id == "my_test_skill"

    def test_import_skill_from_bundle_detects_anthropic(self, tmp_path):
        """import_skill_from_bundle auto-detects Anthropic format."""
        from src.skills.portability import import_skill_from_bundle

        skill_dir = _create_skill_md(tmp_path)
        result = import_skill_from_bundle(skill_dir)
        assert result.success
        assert result.skill_id == "my_test_skill"

    def test_export_skill_to_anthropic(self, tmp_path):
        """export_skill_to_anthropic creates proper Anthropic bundle."""
        from src.skills.portability import export_skill_to_anthropic
        from src.skills.schema import SkillDefinition

        skill = SkillDefinition(
            id="test_export",
            name="Test Export",
            version="1.0",
            description="Testing export",
            category="analysis",
            platforms=("google_ads",),
            tags=("test",),
            tools_required=("fetch_web_page",),
            model="sonnet",
            max_turns=10,
            system_supplement="Do stuff.",
            prompt_template="Run. {analysis_date}",
            output_format="## Summary",
            business_guidance="Be accurate.",
            requires_approval=True,
        )

        out = export_skill_to_anthropic(skill, tmp_path / "output")
        assert (out / "SKILL.md").exists()

        bundle = parse_skill_md(out)
        assert bundle.name == "test-export"
        assert bundle.description == "Testing export"

    def test_validate_anthropic_bundle_invalid(self, tmp_path):
        """_validate_anthropic_bundle returns errors for invalid bundles."""
        from src.skills.portability import validate_bundle

        content = "---\ndescription: no name\n---\nBody"
        skill_dir = _create_skill_md(tmp_path, "bad", content)
        result = validate_bundle(skill_dir)
        assert not result.success


# ===========================================================================
# Tests: AnthropicSkillBundle dataclass
# ===========================================================================


class TestAnthropicSkillBundle:
    def test_default_values(self):
        bundle = AnthropicSkillBundle()
        assert bundle.name == ""
        assert bundle.description == ""
        assert bundle.license == ""
        assert bundle.allowed_tools == []
        assert bundle.metadata == {}
        assert bundle.compatibility == ""
        assert bundle.body_markdown == ""
        assert bundle.scripts == []
        assert bundle.references == []
        assert bundle.assets == []
        assert bundle.source_dir == ""

    def test_custom_values(self):
        bundle = AnthropicSkillBundle(
            name="test",
            description="desc",
            license="MIT",
            allowed_tools=["Bash"],
            metadata={"author": "me"},
            compatibility=">=1.0",
            body_markdown="Body",
            scripts=["scripts/run.py"],
            references=["references/doc.md"],
            assets=["assets/icon.png"],
            source_dir="/tmp/test",
        )
        assert bundle.name == "test"
        assert bundle.license == "MIT"
        assert bundle.scripts == ["scripts/run.py"]
