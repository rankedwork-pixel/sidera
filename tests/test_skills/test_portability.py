"""Tests for src.skills.portability — skill export/import/bundle management.

Covers:
- Exporting skills to directory bundles
- Exporting skills to ZIP bundles
- Exporting skills to bytes (in-memory)
- Manifest generation and loading
- Bundle validation (valid, missing fields, integrity)
- Importing from directory bundles
- Importing from ZIP bundles
- Importing with ID/author overrides (forking)
- Installing to disk
- Listing bundles in a directory
- Searching/filtering bundles
- Sanitization of org-specific fields
- Context file handling
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
import yaml

from src.skills.portability import (
    BUNDLE_FORMAT_VERSION,
    ImportResult,
    SkillManifest,
    _build_manifest,
    _skill_to_portable_dict,
    export_skill_to_bytes,
    export_skill_to_dir,
    export_skill_to_zip,
    import_skill_from_bundle,
    list_bundles_in_dir,
    search_bundles,
    validate_bundle,
)
from src.skills.schema import SkillDefinition

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill(**overrides) -> SkillDefinition:
    """Build a SkillDefinition with sensible defaults."""
    defaults = {
        "id": "creative_analysis",
        "name": "Creative Analysis",
        "version": "1.0",
        "description": "Analyze ad creative performance",
        "category": "analysis",
        "platforms": ("google_ads", "meta"),
        "tags": ("creative", "performance", "ads", "ctr", "roas"),
        "tools_required": ("get_meta_campaigns",),
        "model": "sonnet",
        "max_turns": 10,
        "system_supplement": "You are a creative analyst.",
        "prompt_template": "Analyze creatives.",
        "output_format": "## Creative Report",
        "business_guidance": "Focus on ROAS impact.",
        "department_id": "marketing",
        "role_id": "performance_media_buyer",
        "source_dir": "/tmp/fake/source",
        "author": "test_author",
    }
    defaults.update(overrides)
    return SkillDefinition(**defaults)


def _write_bundle(
    tmp_path: Path,
    skill_yaml: dict | None = None,
    manifest: dict | None = None,
    context_files: dict[str, str] | None = None,
) -> Path:
    """Create a bundle directory with optional files."""
    bundle_dir = tmp_path / "test_skill"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    if skill_yaml is not None:
        (bundle_dir / "skill.yaml").write_text(
            yaml.dump(skill_yaml, sort_keys=False), encoding="utf-8"
        )

    if manifest is not None:
        (bundle_dir / "manifest.yaml").write_text(
            yaml.dump(manifest, sort_keys=False), encoding="utf-8"
        )

    if context_files:
        for rel_path, content in context_files.items():
            ctx_file = bundle_dir / "context" / rel_path
            ctx_file.parent.mkdir(parents=True, exist_ok=True)
            ctx_file.write_text(content, encoding="utf-8")

    return bundle_dir


def _minimal_skill_dict(**overrides) -> dict:
    """Build a minimal valid skill YAML dict."""
    d = {
        "id": "test_skill",
        "name": "Test Skill",
        "version": "1.0",
        "description": "A test skill",
        "category": "analysis",
        "platforms": ["google_ads"],
        "tags": ["test", "analysis", "demo", "sample", "qa"],
        "tools_required": ["get_google_ads_campaigns"],
        "model": "sonnet",
        "max_turns": 10,
        "system_supplement": "You are a test agent.",
        "prompt_template": "Do the thing.",
        "output_format": "## Output",
        "business_guidance": "Be good.",
    }
    d.update(overrides)
    return d


# ===========================================================================
# 1. Portable dict conversion
# ===========================================================================


class TestSkillToPortableDict:
    def test_strips_org_fields(self):
        skill = _make_skill()
        d = _skill_to_portable_dict(skill)
        assert "source_dir" not in d
        assert "context_text" not in d
        assert "department_id" not in d
        assert "role_id" not in d

    def test_keeps_required_fields(self):
        skill = _make_skill()
        d = _skill_to_portable_dict(skill)
        assert d["id"] == "creative_analysis"
        assert d["name"] == "Creative Analysis"
        assert d["version"] == "1.0"
        assert d["description"] == "Analyze ad creative performance"
        assert d["category"] == "analysis"
        assert d["model"] == "sonnet"

    def test_converts_tuples_to_lists(self):
        skill = _make_skill()
        d = _skill_to_portable_dict(skill)
        assert isinstance(d["platforms"], list)
        assert isinstance(d["tags"], list)

    def test_removes_empty_optionals(self):
        skill = _make_skill(schedule=None, chain_after=None)
        d = _skill_to_portable_dict(skill)
        assert "schedule" not in d
        assert "chain_after" not in d


# ===========================================================================
# 2. Manifest building
# ===========================================================================


class TestBuildManifest:
    def test_manifest_fields(self):
        skill = _make_skill()
        manifest = _build_manifest(skill, "abc123hash", ["examples/good.md"], "admin")
        assert manifest.format_version == BUNDLE_FORMAT_VERSION
        assert manifest.skill_id == "creative_analysis"
        assert manifest.skill_name == "Creative Analysis"
        assert manifest.skill_version == "1.0"
        assert manifest.sha256 == "abc123hash"
        assert manifest.exported_by == "admin"
        assert manifest.context_files == ["examples/good.md"]
        assert manifest.exported_at  # Non-empty ISO timestamp

    def test_compatibility_info(self):
        skill = _make_skill()
        manifest = _build_manifest(skill, "", [], "")
        assert "required_platforms" in manifest.compatibility
        assert "required_tools" in manifest.compatibility
        assert manifest.compatibility["model_tier"] == "sonnet"

    def test_provenance_info(self):
        skill = _make_skill()
        manifest = _build_manifest(skill, "", [], "")
        assert manifest.provenance["original_department"] == "marketing"
        assert manifest.provenance["original_role"] == "performance_media_buyer"


# ===========================================================================
# 3. Export to directory
# ===========================================================================


class TestExportToDir:
    def test_creates_bundle_dir(self, tmp_path):
        skill = _make_skill()
        result = export_skill_to_dir(skill, tmp_path)
        assert result.is_dir()
        assert result.name == "creative_analysis"

    def test_contains_skill_yaml(self, tmp_path):
        skill = _make_skill()
        bundle = export_skill_to_dir(skill, tmp_path)
        skill_path = bundle / "skill.yaml"
        assert skill_path.exists()
        data = yaml.safe_load(skill_path.read_text())
        assert data["id"] == "creative_analysis"
        assert "source_dir" not in data
        assert "department_id" not in data

    def test_contains_manifest(self, tmp_path):
        skill = _make_skill()
        bundle = export_skill_to_dir(skill, tmp_path)
        manifest_path = bundle / "manifest.yaml"
        assert manifest_path.exists()
        data = yaml.safe_load(manifest_path.read_text())
        assert data["format_version"] == BUNDLE_FORMAT_VERSION
        assert data["skill_id"] == "creative_analysis"

    def test_copies_context_files(self, tmp_path):
        # Create a source dir with context files
        src = tmp_path / "source"
        src.mkdir()
        examples = src / "examples"
        examples.mkdir()
        (examples / "good.md").write_text("# Good Example")
        (examples / "bad.md").write_text("# Bad Example")

        skill = _make_skill(
            source_dir=str(src),
            context_files=("examples/*.md",),
        )
        bundle = export_skill_to_dir(skill, tmp_path / "out")
        ctx = bundle / "context" / "examples"
        assert ctx.exists()
        assert (ctx / "good.md").exists()
        assert (ctx / "bad.md").exists()

    def test_exported_by_recorded(self, tmp_path):
        skill = _make_skill()
        bundle = export_skill_to_dir(skill, tmp_path, exported_by="alice")
        manifest = yaml.safe_load((bundle / "manifest.yaml").read_text())
        assert manifest["exported_by"] == "alice"


# ===========================================================================
# 4. Export to ZIP
# ===========================================================================


class TestExportToZip:
    def test_creates_zip(self, tmp_path):
        skill = _make_skill()
        zip_path = tmp_path / "bundle.zip"
        result = export_skill_to_zip(skill, zip_path)
        assert result.exists()
        assert result.suffix == ".zip"

    def test_zip_contains_files(self, tmp_path):
        skill = _make_skill()
        zip_path = tmp_path / "bundle.zip"
        export_skill_to_zip(skill, zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            assert any("skill.yaml" in n for n in names)
            assert any("manifest.yaml" in n for n in names)


# ===========================================================================
# 5. Export to bytes
# ===========================================================================


class TestExportToBytes:
    def test_returns_bytes(self):
        skill = _make_skill()
        data = export_skill_to_bytes(skill)
        assert isinstance(data, bytes)
        assert len(data) > 0

    def test_bytes_is_valid_zip(self):
        import io

        skill = _make_skill()
        data = export_skill_to_bytes(skill)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            assert any("skill.yaml" in n for n in names)


# ===========================================================================
# 6. Bundle validation
# ===========================================================================


class TestValidateBundle:
    def test_valid_bundle(self, tmp_path):
        bundle = _write_bundle(tmp_path, skill_yaml=_minimal_skill_dict())
        result = validate_bundle(bundle)
        assert result.success
        assert result.skill_id == "test_skill"
        assert not result.errors

    def test_missing_skill_yaml(self, tmp_path):
        bundle = tmp_path / "empty_bundle"
        bundle.mkdir()
        result = validate_bundle(bundle)
        assert not result.success
        assert any("skill.yaml" in e for e in result.errors)

    def test_missing_required_fields(self, tmp_path):
        bundle = _write_bundle(
            tmp_path,
            skill_yaml={"id": "incomplete", "name": "Incomplete"},
        )
        result = validate_bundle(bundle)
        assert not result.success
        assert any("Missing required" in e for e in result.errors)

    def test_invalid_model(self, tmp_path):
        bundle = _write_bundle(
            tmp_path,
            skill_yaml=_minimal_skill_dict(model="gpt4"),
        )
        result = validate_bundle(bundle)
        assert not result.success

    def test_not_a_directory(self, tmp_path):
        fake = tmp_path / "notadir.txt"
        fake.write_text("hello")
        result = validate_bundle(fake)
        assert not result.success
        assert any("not a directory" in e for e in result.errors)

    def test_warns_missing_manifest(self, tmp_path):
        bundle = _write_bundle(tmp_path, skill_yaml=_minimal_skill_dict())
        result = validate_bundle(bundle)
        assert any("manifest" in w.lower() for w in result.warnings)

    def test_warns_missing_context_files(self, tmp_path):
        skill_data = _minimal_skill_dict(context_files=["examples/*.md"])
        bundle = _write_bundle(tmp_path, skill_yaml=skill_data)
        result = validate_bundle(bundle)
        assert result.success  # Still valid, just warns
        assert any("context" in w.lower() for w in result.warnings)

    def test_zip_validation(self, tmp_path):
        skill = _make_skill()
        zip_path = tmp_path / "test.zip"
        export_skill_to_zip(skill, zip_path)
        result = validate_bundle(zip_path)
        assert result.success
        assert result.skill_id == "creative_analysis"


# ===========================================================================
# 7. Import from directory
# ===========================================================================


class TestImportFromBundle:
    def test_successful_import(self, tmp_path):
        bundle = _write_bundle(tmp_path, skill_yaml=_minimal_skill_dict())
        result = import_skill_from_bundle(
            bundle,
            target_department_id="marketing",
            target_role_id="analyst",
        )
        assert result.success
        assert result.skill_id == "test_skill"
        assert result.target_department_id == "marketing"
        assert result.target_role_id == "analyst"

    def test_import_with_id_override(self, tmp_path):
        bundle = _write_bundle(tmp_path, skill_yaml=_minimal_skill_dict())
        result = import_skill_from_bundle(
            bundle,
            new_skill_id="forked_skill",
        )
        assert result.success
        assert result.skill_id == "forked_skill"

    def test_import_with_author_override(self, tmp_path):
        bundle = _write_bundle(tmp_path, skill_yaml=_minimal_skill_dict())
        result = import_skill_from_bundle(
            bundle,
            new_author="bob",
        )
        assert result.success

    def test_import_invalid_bundle(self, tmp_path):
        bundle = _write_bundle(
            tmp_path,
            skill_yaml={"id": "bad"},
        )
        result = import_skill_from_bundle(bundle)
        assert not result.success
        assert result.errors

    def test_install_to_disk(self, tmp_path):
        bundle = _write_bundle(
            tmp_path / "src",
            skill_yaml=_minimal_skill_dict(),
            context_files={
                "examples/sample.md": "# Sample",
            },
        )
        install_dir = tmp_path / "installed"
        result = import_skill_from_bundle(
            bundle,
            install_to_disk=install_dir,
        )
        assert result.success
        installed = install_dir / "test_skill"
        assert installed.exists()
        assert (installed / "skill.yaml").exists()
        # Context files should be copied
        assert (installed / "context" / "examples" / "sample.md").exists()

    def test_import_from_zip(self, tmp_path):
        # Export first
        skill = _make_skill()
        zip_path = tmp_path / "export.zip"
        export_skill_to_zip(skill, zip_path)

        # Import back
        result = import_skill_from_bundle(
            zip_path,
            target_department_id="marketing",
        )
        assert result.success
        assert result.skill_id == "creative_analysis"


# ===========================================================================
# 8. Round-trip (export → import)
# ===========================================================================


class TestRoundTrip:
    def test_export_import_preserves_data(self, tmp_path):
        original = _make_skill()
        bundle = export_skill_to_dir(original, tmp_path / "export")

        result = import_skill_from_bundle(
            bundle,
            install_to_disk=tmp_path / "import",
        )
        assert result.success
        assert result.skill_id == original.id

        # Verify installed skill.yaml
        installed = tmp_path / "import" / original.id / "skill.yaml"
        data = yaml.safe_load(installed.read_text())
        assert data["name"] == original.name
        assert data["category"] == original.category
        assert data["model"] == original.model
        assert data["system_supplement"] == original.system_supplement

    def test_zip_round_trip(self, tmp_path):
        original = _make_skill()
        zip_path = tmp_path / "export.zip"
        export_skill_to_zip(original, zip_path)

        result = import_skill_from_bundle(zip_path)
        assert result.success
        assert result.skill_id == original.id


# ===========================================================================
# 9. Listing bundles
# ===========================================================================


class TestListBundles:
    def test_list_with_manifests(self, tmp_path):
        # Create two bundles
        _write_bundle(
            tmp_path,
            skill_yaml=_minimal_skill_dict(id="skill_a", name="Skill A"),
        )
        bundle_b = tmp_path / "skill_b"
        bundle_b.mkdir()
        (bundle_b / "skill.yaml").write_text(
            yaml.dump(_minimal_skill_dict(id="skill_b", name="Skill B")),
            encoding="utf-8",
        )

        manifests = list_bundles_in_dir(tmp_path)
        ids = {m.skill_id for m in manifests}
        assert "skill_a" in ids or "test_skill" in ids
        assert "skill_b" in ids

    def test_empty_directory(self, tmp_path):
        manifests = list_bundles_in_dir(tmp_path)
        assert manifests == []

    def test_nonexistent_directory(self):
        manifests = list_bundles_in_dir("/nonexistent/dir")
        assert manifests == []


# ===========================================================================
# 10. Searching bundles
# ===========================================================================


class TestSearchBundles:
    @pytest.fixture()
    def sample_bundles(self) -> list[SkillManifest]:
        return [
            SkillManifest(
                skill_id="creative_analysis",
                skill_name="Creative Analysis",
                description="Analyze ad creatives",
                category="analysis",
                platforms=["google_ads", "meta"],
                tags=["creative", "ads"],
            ),
            SkillManifest(
                skill_id="budget_pacing",
                skill_name="Budget Pacing Check",
                description="Monitor budget pacing",
                category="monitoring",
                platforms=["google_ads"],
                tags=["budget", "pacing"],
            ),
            SkillManifest(
                skill_id="geo_performance",
                skill_name="Geo Performance",
                description="Analyze geographic performance",
                category="analysis",
                platforms=["google_ads", "meta"],
                tags=["geo", "regions"],
            ),
        ]

    def test_search_by_query(self, sample_bundles):
        results = search_bundles(sample_bundles, query="creative")
        assert len(results) == 1
        assert results[0].skill_id == "creative_analysis"

    def test_search_by_category(self, sample_bundles):
        results = search_bundles(sample_bundles, category="analysis")
        assert len(results) == 2

    def test_search_by_platform(self, sample_bundles):
        results = search_bundles(sample_bundles, platform="meta")
        assert len(results) == 2

    def test_search_combined(self, sample_bundles):
        results = search_bundles(
            sample_bundles,
            query="geo",
            category="analysis",
        )
        assert len(results) == 1
        assert results[0].skill_id == "geo_performance"

    def test_search_no_matches(self, sample_bundles):
        results = search_bundles(sample_bundles, query="xyz")
        assert results == []

    def test_search_by_tag(self, sample_bundles):
        results = search_bundles(sample_bundles, query="pacing")
        assert len(results) == 1
        assert results[0].skill_id == "budget_pacing"


# ===========================================================================
# 11. SkillManifest dataclass
# ===========================================================================


class TestSkillManifest:
    def test_default_values(self):
        m = SkillManifest()
        assert m.format_version == BUNDLE_FORMAT_VERSION
        assert m.skill_id == ""
        assert m.platforms == []
        assert m.tags == []

    def test_custom_values(self):
        m = SkillManifest(
            skill_id="test",
            skill_name="Test Skill",
            platforms=["google_ads"],
        )
        assert m.skill_id == "test"
        assert m.platforms == ["google_ads"]


# ===========================================================================
# 12. ImportResult dataclass
# ===========================================================================


class TestImportResult:
    def test_default_failure(self):
        r = ImportResult()
        assert not r.success
        assert r.errors == []

    def test_success_result(self):
        r = ImportResult(
            success=True,
            skill_id="test",
            target_department_id="marketing",
        )
        assert r.success
        assert r.skill_id == "test"


# ===========================================================================
# 13. References export/import
# ===========================================================================


class TestReferencesPortability:
    """References are exported as list-of-dicts and imported back correctly."""

    def test_export_converts_to_list_of_dicts(self):
        """Tuple-of-tuples references become list-of-dicts on export."""
        skill = _make_skill(
            references=(
                ("other_skill", "methodology", "attribution windows"),
                ("brand_skill", "context", "brand voice"),
            ),
        )
        d = _skill_to_portable_dict(skill)
        assert "references" in d
        assert isinstance(d["references"], list)
        assert len(d["references"]) == 2
        assert d["references"][0] == {
            "skill_id": "other_skill",
            "relationship": "methodology",
            "reason": "attribution windows",
        }
        assert d["references"][1] == {
            "skill_id": "brand_skill",
            "relationship": "context",
            "reason": "brand voice",
        }

    def test_export_empty_references_stripped(self):
        """Empty references are not included in export."""
        skill = _make_skill(references=())
        d = _skill_to_portable_dict(skill)
        assert "references" not in d

    def test_round_trip_preserves_references(self, tmp_path):
        """Export → import preserves references."""
        original = _make_skill(
            references=(("target_skill", "methodology", "the reason"),),
        )
        bundle = export_skill_to_dir(original, tmp_path / "export")
        installed = tmp_path / "import"

        result = import_skill_from_bundle(
            bundle,
            install_to_disk=installed,
        )
        assert result.success

        # Read back the installed skill.yaml
        data = yaml.safe_load((installed / original.id / "skill.yaml").read_text())
        assert "references" in data
        assert len(data["references"]) == 1
        assert data["references"][0]["skill_id"] == "target_skill"
        assert data["references"][0]["relationship"] == "methodology"
        assert data["references"][0]["reason"] == "the reason"

    def test_import_warns_about_references(self, tmp_path):
        """Importing a skill with references generates a warning."""
        skill_dict = _minimal_skill_dict()
        skill_dict["references"] = [
            {"skill_id": "external_skill", "relationship": "data", "reason": "x"},
        ]
        bundle_dir = _write_bundle(tmp_path, skill_yaml=skill_dict)
        result = validate_bundle(bundle_dir)
        assert any("references" in w.lower() for w in result.warnings)
