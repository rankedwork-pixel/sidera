"""Tests for on-demand context file loading (src/mcp_servers/context.py).

Verifies that:
- load_skill_context tool loads files on demand
- build_context_manifest creates correct manifests
- Edge cases (no files, missing skill, etc.) handled gracefully
- Cross-skill references manifest and traversal budget
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.mcp_servers.context import (
    _MAX_REFERENCE_CHARS_PER_TURN,
    _MAX_REFERENCE_LOADS_PER_TURN,
    build_context_manifest,
    get_reference_chars_loaded,
    get_reference_load_count,
    load_referenced_skill_context_handler,
    load_skill_context_handler,
    reset_reference_load_count,
)


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


# ===========================================================================
# Manifest with references
# ===========================================================================


class TestBuildContextManifestReferences:
    """build_context_manifest renders a Related Skills section."""

    def test_references_only(self):
        """Manifest with references but no context_files."""
        result = build_context_manifest(
            skill_id="my_skill",
            source_dir="",
            context_files=(),
            references=(
                ("attribution_analysis", "methodology", "attribution windows"),
                ("brand_guidelines", "context", "brand voice"),
            ),
        )
        assert "# Related Skills" in result
        assert "**attribution_analysis**" in result
        assert "(methodology)" in result
        assert "attribution windows" in result
        assert "**brand_guidelines**" in result
        assert "load_referenced_skill_context" in result
        assert 'skill_id="my_skill"' in result
        # No context files section
        assert "# Available Context Files" not in result

    def test_both_context_and_references(self, tmp_path: Path):
        """Manifest includes both context files AND references."""
        (tmp_path / "example.md").write_text("example content")

        result = build_context_manifest(
            skill_id="dual_skill",
            source_dir=str(tmp_path),
            context_files=("*.md",),
            references=(("other_skill", "data", "needs their data"),),
        )

        assert "# Available Context Files" in result
        assert "example.md" in result
        assert "# Related Skills" in result
        assert "**other_skill**" in result

    def test_references_empty_tuple(self):
        """Empty references tuple produces no Related Skills section."""
        result = build_context_manifest(
            skill_id="test",
            source_dir="",
            context_files=(),
            references=(),
        )
        assert result == ""

    def test_references_without_relationship(self):
        """References with empty relationship still render."""
        result = build_context_manifest(
            skill_id="test",
            source_dir="",
            context_files=(),
            references=(("other", "", "just a reason"),),
        )
        assert "**other**" in result
        assert "just a reason" in result
        assert "()" not in result  # No empty parens

    def test_references_without_reason(self):
        """References with empty reason still render."""
        result = build_context_manifest(
            skill_id="test",
            source_dir="",
            context_files=(),
            references=(("other", "methodology", ""),),
        )
        assert "**other**" in result
        assert "(methodology)" in result


# ===========================================================================
# Traversal budget
# ===========================================================================


class TestTraversalBudget:
    """Tests for reference load counter and budget enforcement."""

    def test_reset_sets_to_zero(self):
        """reset_reference_load_count sets counter to 0."""
        reset_reference_load_count()
        assert get_reference_load_count() == 0

    def test_max_constant(self):
        """Budget constant is 3."""
        assert _MAX_REFERENCE_LOADS_PER_TURN == 3

    def test_char_budget_constant(self):
        """Char budget constant is 12,000."""
        assert _MAX_REFERENCE_CHARS_PER_TURN == 12_000

    def test_reset_clears_char_counter(self):
        """reset_reference_load_count also resets char counter."""
        from src.mcp_servers.context import _reference_chars_loaded

        _reference_chars_loaded.set(5000)
        reset_reference_load_count()
        assert get_reference_chars_loaded() == 0

    def test_get_reference_chars_loaded_default(self):
        """Default char counter is 0."""
        reset_reference_load_count()
        assert get_reference_chars_loaded() == 0


# ===========================================================================
# load_referenced_skill_context handler
# ===========================================================================


class TestLoadReferencedSkillContext:
    """Tests for the load_referenced_skill_context MCP tool handler."""

    def test_missing_skill_id(self):
        """Missing skill_id returns error."""
        result = load_referenced_skill_context_handler({"reference_skill_id": "other"})
        assert "Error" in result["content"][0]["text"]

    def test_missing_reference_skill_id(self):
        """Missing reference_skill_id returns error."""
        result = load_referenced_skill_context_handler({"skill_id": "mine"})
        assert "Error" in result["content"][0]["text"]

    def test_budget_exhausted(self):
        """Exceeding traversal budget returns error."""
        from src.mcp_servers.context import _reference_load_count

        # Set counter to max
        _reference_load_count.set(_MAX_REFERENCE_LOADS_PER_TURN)
        try:
            result = load_referenced_skill_context_handler(
                {"skill_id": "mine", "reference_skill_id": "other"}
            )
            assert "budget exhausted" in result["content"][0]["text"].lower()
        finally:
            reset_reference_load_count()

    def test_char_budget_exhausted_at_limit(self):
        """Char budget at limit returns error before registry lookup."""
        from src.mcp_servers.context import _reference_chars_loaded

        _reference_chars_loaded.set(_MAX_REFERENCE_CHARS_PER_TURN)
        try:
            result = load_referenced_skill_context_handler(
                {"skill_id": "mine", "reference_skill_id": "other"}
            )
            text = result["content"][0]["text"]
            assert "character budget exhausted" in text.lower()
        finally:
            reset_reference_load_count()

    def test_char_budget_exhausted_over_limit(self):
        """Char budget over limit also returns error."""
        from src.mcp_servers.context import _reference_chars_loaded

        _reference_chars_loaded.set(_MAX_REFERENCE_CHARS_PER_TURN + 5000)
        try:
            result = load_referenced_skill_context_handler(
                {"skill_id": "mine", "reference_skill_id": "other"}
            )
            text = result["content"][0]["text"]
            assert "character budget exhausted" in text.lower()
        finally:
            reset_reference_load_count()
