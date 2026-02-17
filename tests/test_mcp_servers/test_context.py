"""Tests for on-demand context file loading (src/mcp_servers/context.py).

Verifies that:
- load_skill_context tool loads files on demand
- build_context_manifest creates correct manifests
- Edge cases (no files, missing skill, etc.) handled gracefully
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.mcp_servers.context import build_context_manifest, load_skill_context_handler


class TestBuildContextManifest:
    def test_no_context_files(self):
        result = build_context_manifest("test", "/some/dir", ())
        assert result == ""

    def test_no_source_dir(self):
        result = build_context_manifest("test", "", ("**/*.md",))
        assert result == ""

    def test_nonexistent_dir(self):
        result = build_context_manifest(
            "test",
            "/nonexistent/path",
            ("**/*.md",),
        )
        assert result == ""

    def test_with_files(self, tmp_path: Path):
        # Create some context files
        examples_dir = tmp_path / "examples"
        examples_dir.mkdir()
        (examples_dir / "good_analysis.md").write_text("Good example content")
        (examples_dir / "bad_analysis.md").write_text("Bad example content")
        (tmp_path / "guidelines" / "rules.md").mkdir(parents=True, exist_ok=True)
        # Overwrite directory with file
        import shutil

        shutil.rmtree(tmp_path / "guidelines")
        guidelines_dir = tmp_path / "guidelines"
        guidelines_dir.mkdir()
        (guidelines_dir / "rules.md").write_text("Some rules")

        result = build_context_manifest(
            "creative_analysis",
            str(tmp_path),
            ("examples/*.md", "guidelines/*.md"),
        )

        assert "# Available Context Files" in result
        assert "examples/good_analysis.md" in result
        assert "examples/bad_analysis.md" in result
        assert "guidelines/rules.md" in result
        assert 'skill_id="creative_analysis"' in result
        assert "load_skill_context" in result

    def test_shows_file_sizes(self, tmp_path: Path):
        (tmp_path / "small.md").write_text("x" * 100)
        (tmp_path / "large.md").write_text("x" * 5000)

        result = build_context_manifest(
            "test",
            str(tmp_path),
            ("*.md",),
        )

        assert "B)" in result or "KB)" in result


class TestLoadSkillContextHandler:
    @pytest.mark.asyncio
    async def test_missing_skill_id(self):
        result = await load_skill_context_handler({})
        assert "error" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_skill_not_found(self):
        result = await load_skill_context_handler(
            {"skill_id": "nonexistent_skill_12345"},
        )
        assert "not found" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_skill_with_no_context(self):
        # This loads the real registry; a skill without context files
        # should return "No context files configured"
        result = await load_skill_context_handler(
            {"skill_id": "nonexistent_skill_xyz"},
        )
        text = result["content"][0]["text"]
        assert "not found" in text.lower() or "no context" in text.lower()


class TestLoadContextTextLazy:
    def test_lazy_returns_manifest(self, tmp_path: Path):
        """When lazy=True, load_context_text returns a manifest."""
        from src.skills.schema import SkillDefinition, load_context_text

        # Create a skill with context files
        examples_dir = tmp_path / "examples"
        examples_dir.mkdir()
        (examples_dir / "example1.md").write_text("Example content")

        skill = SkillDefinition(
            id="test_skill",
            name="Test Skill",
            version="1.0",
            description="Test",
            category="analysis",
            platforms=("google_ads",),
            tags=("test",),
            tools_required=(),
            model="sonnet",
            max_turns=5,
            system_supplement="Test supplement",
            prompt_template="Test prompt",
            output_format="Test format",
            business_guidance="Test guidance",
            context_files=("examples/*.md",),
            source_dir=str(tmp_path),
        )

        # Eager mode returns full content
        eager_result = load_context_text(skill, lazy=False)
        assert "Example content" in eager_result
        assert "# Context:" in eager_result

        # Lazy mode returns manifest
        lazy_result = load_context_text(skill, lazy=True)
        assert "Available Context Files" in lazy_result
        assert "load_skill_context" in lazy_result
        assert "Example content" not in lazy_result

    def test_lazy_false_by_default(self, tmp_path: Path):
        """Default behavior (lazy=False) returns full content."""
        from src.skills.schema import SkillDefinition, load_context_text

        examples_dir = tmp_path / "examples"
        examples_dir.mkdir()
        (examples_dir / "example1.md").write_text("Full content here")

        skill = SkillDefinition(
            id="test_skill",
            name="Test",
            version="1.0",
            description="Test",
            category="analysis",
            platforms=(),
            tags=(),
            tools_required=(),
            model="sonnet",
            max_turns=1,
            system_supplement="s",
            prompt_template="p",
            output_format="o",
            business_guidance="b",
            context_files=("examples/*.md",),
            source_dir=str(tmp_path),
        )

        result = load_context_text(skill)
        assert "Full content here" in result

    def test_db_skill_always_eager(self, tmp_path: Path):
        """DB-defined skills with context_text bypass lazy mode."""
        from src.skills.schema import SkillDefinition, load_context_text

        skill = SkillDefinition(
            id="db_skill",
            name="DB Skill",
            version="1.0",
            description="Test",
            category="analysis",
            platforms=(),
            tags=(),
            tools_required=(),
            model="sonnet",
            max_turns=5,
            system_supplement="s",
            prompt_template="p",
            output_format="o",
            business_guidance="b",
            context_text="Pre-rendered DB context",
        )

        result = load_context_text(skill, lazy=True)
        assert result == "Pre-rendered DB context"
