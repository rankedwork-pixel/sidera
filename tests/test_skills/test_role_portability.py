"""Tests for src.skills.role_portability --- role export/import/bundle management.

Covers:
- Converting roles to portable dicts (field stripping, tuple-to-list, cleanup)
- Exporting roles to directory bundles (structure, skills, context, rules, memories)
- Exporting roles to ZIP archives
- Validating role bundles (valid, missing files, integrity, skills dir)
- Importing roles from bundles (DB creation, ID override, seed memories)
- Listing and searching role bundles
"""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from src.skills.portability import BUNDLE_FORMAT_VERSION
from src.skills.role_portability import (
    _role_to_portable_dict,
    export_role_to_dir,
    export_role_to_zip,
    import_role_from_bundle,
    list_role_bundles_in_dir,
    search_role_bundles,
    validate_role_bundle,
)

# ---------------------------------------------------------------------------
# Mock dataclasses
# ---------------------------------------------------------------------------


@dataclass
class MockRole:
    id: str = "test_role"
    name: str = "Test Role"
    department_id: str = "test_dept"
    description: str = "A test role"
    persona: str = "You are a test role."
    connectors: tuple = ()
    briefing_skills: tuple = ("skill_a",)
    principles: tuple = ("Be accurate",)
    goals: tuple = ("Test goal",)
    manages: tuple = ()
    routing_keywords: tuple = ("test",)
    context_files: tuple = ()
    source_dir: str = ""
    context_text: str = ""
    steward: str = "U123"
    document_sync: tuple = ()
    learning_channels: tuple = ()
    event_subscriptions: tuple = ()
    schedule: str | None = None
    delegation_model: str = "standard"
    synthesis_prompt: str = ""
    clearance_level: str = "internal"
    heartbeat_schedule: str | None = None
    heartbeat_model: str = ""


@dataclass
class MockSkill:
    id: str = "test_skill"
    name: str = "Test Skill"
    version: str = "1.0"
    description: str = "A test skill"
    category: str = "analysis"
    platforms: tuple = ("google_ads",)
    tags: tuple = ("test",)
    tools_required: tuple = ("fetch_web_page",)
    model: str = "sonnet"
    max_turns: int = 10
    system_supplement: str = "Test supplement"
    prompt_template: str = "Run analysis for {analysis_date}"
    output_format: str = "markdown"
    business_guidance: str = "Be careful"
    context_files: tuple = ()
    context_file_descriptions: tuple = ()
    source_dir: str = ""
    context_text: str = ""
    department_id: str = ""
    role_id: str = ""
    author: str = "test"
    created_at: str = ""
    updated_at: str = ""
    schedule: str | None = None
    chain_after: str | None = None
    requires_approval: bool = True
    skill_type: str = "llm"
    code_entrypoint: str = ""
    code_timeout_seconds: int = 300
    code_output_patterns: tuple = ()
    min_clearance: str = ""
    references: tuple = ()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_role(**overrides) -> MockRole:
    return MockRole(**overrides)


def _make_skill(**overrides) -> MockSkill:
    return MockSkill(**overrides)


def _write_role_bundle(
    tmp_path: Path,
    role_yaml: dict | None = None,
    manifest: dict | None = None,
    skills: list[dict] | None = None,
) -> Path:
    """Create a role bundle directory with optional files."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    if role_yaml is not None:
        role_text = yaml.dump(role_yaml, default_flow_style=False)
        (bundle_dir / "_role.yaml").write_text(role_text, encoding="utf-8")

    if manifest is not None:
        manifest_text = yaml.dump(manifest, default_flow_style=False)
        (bundle_dir / "manifest.yaml").write_text(manifest_text, encoding="utf-8")

    if skills is not None:
        skills_dir = bundle_dir / "skills"
        for skill_data in skills:
            sid = skill_data.get("id", "unknown")
            skill_dir = skills_dir / sid
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_text = yaml.dump(skill_data, default_flow_style=False)
            (skill_dir / "skill.yaml").write_text(skill_text, encoding="utf-8")

    return bundle_dir


def _compute_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ===========================================================================
# TestRoleToPortableDict
# ===========================================================================


class TestRoleToPortableDict:
    """Tests for _role_to_portable_dict."""

    def test_strips_org_specific_fields(self):
        role = _make_role(
            source_dir="/some/path",
            steward="U999",
            document_sync=("briefings",),
            learning_channels=("peer_role",),
            event_subscriptions=("budget_alert",),
            context_text="some context",
        )
        d = _role_to_portable_dict(role)

        assert "source_dir" not in d
        assert "steward" not in d
        assert "document_sync" not in d
        assert "learning_channels" not in d
        assert "event_subscriptions" not in d
        assert "context_text" not in d

    def test_strips_department_id(self):
        role = _make_role(department_id="marketing")
        d = _role_to_portable_dict(role)
        assert "department_id" not in d

    def test_converts_tuples_to_lists(self):
        role = _make_role(
            connectors=("google_ads",),
            briefing_skills=("skill_a", "skill_b"),
            principles=("P1", "P2"),
            goals=("G1",),
            manages=("sub_role_a",),
            routing_keywords=("keyword",),
            context_files=("*.md",),
        )
        d = _role_to_portable_dict(role)

        assert isinstance(d["connectors"], list)
        assert isinstance(d["briefing_skills"], list)
        assert isinstance(d["principles"], list)
        assert isinstance(d["goals"], list)
        assert isinstance(d["manages"], list)
        assert isinstance(d["routing_keywords"], list)
        assert isinstance(d["context_files"], list)

    def test_removes_empty_optional_fields(self):
        role = _make_role(
            schedule=None,
            synthesis_prompt="",
            heartbeat_schedule=None,
            heartbeat_model="",
        )
        d = _role_to_portable_dict(role)

        assert "schedule" not in d
        assert "synthesis_prompt" not in d
        assert "heartbeat_schedule" not in d
        assert "heartbeat_model" not in d

    def test_keeps_required_fields_even_if_empty(self):
        role = _make_role(id="my_role", name="My Role", persona="I am a role.")
        d = _role_to_portable_dict(role)

        assert d["id"] == "my_role"
        assert d["name"] == "My Role"
        assert d["persona"] == "I am a role."

    def test_keeps_description_even_if_empty(self):
        """description is in the required-keep set."""
        role = _make_role(description="")
        d = _role_to_portable_dict(role)
        assert "description" in d


# ===========================================================================
# TestExportRoleToDir
# ===========================================================================


class TestExportRoleToDir:
    """Tests for export_role_to_dir."""

    def test_creates_bundle_directory(self, tmp_path):
        role = _make_role()
        skills = [_make_skill(id="skill_a")]
        bundle = export_role_to_dir(role, skills, tmp_path)

        assert bundle.exists()
        assert bundle.is_dir()
        assert bundle.name == "test_role"

    def test_contains_role_yaml(self, tmp_path):
        role = _make_role()
        bundle = export_role_to_dir(role, [], tmp_path)

        role_yaml_path = bundle / "_role.yaml"
        assert role_yaml_path.exists()
        data = yaml.safe_load(role_yaml_path.read_text(encoding="utf-8"))
        assert data["id"] == "test_role"
        assert data["name"] == "Test Role"

    def test_contains_manifest_yaml(self, tmp_path):
        role = _make_role()
        skills = [_make_skill(id="skill_x")]
        bundle = export_role_to_dir(role, skills, tmp_path, exported_by="admin")

        manifest_path = bundle / "manifest.yaml"
        assert manifest_path.exists()
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        assert data["role_id"] == "test_role"
        assert data["format_version"] == BUNDLE_FORMAT_VERSION
        assert data["exported_by"] == "admin"
        assert data["skill_count"] == 1
        assert "skill_x" in data["skills"]
        assert data["sha256"]  # non-empty hash

    def test_contains_skills_subdirectories(self, tmp_path):
        role = _make_role()
        skills = [
            _make_skill(id="skill_a"),
            _make_skill(id="skill_b"),
        ]
        bundle = export_role_to_dir(role, skills, tmp_path)

        skills_dir = bundle / "skills"
        assert skills_dir.exists()
        assert (skills_dir / "skill_a" / "skill.yaml").exists()
        assert (skills_dir / "skill_b" / "skill.yaml").exists()

    def test_includes_context_files(self, tmp_path):
        # Set up a source dir with context files for the skill
        src_dir = tmp_path / "source_skill"
        src_dir.mkdir()
        ctx = src_dir / "context"
        ctx.mkdir()
        (ctx / "guide.md").write_text("# Guide content", encoding="utf-8")

        role = _make_role()
        skill = _make_skill(
            id="ctx_skill",
            source_dir=str(src_dir),
            context_files=("context/*.md",),
        )
        bundle = export_role_to_dir(role, [skill], tmp_path)

        exported_ctx = bundle / "skills" / "ctx_skill" / "context" / "context" / "guide.md"
        assert exported_ctx.exists()
        assert exported_ctx.read_text(encoding="utf-8") == "# Guide content"

    def test_includes_rules_yaml(self, tmp_path):
        # Set up a role source dir with _rules.yaml
        role_src = tmp_path / "role_source"
        role_src.mkdir()
        (role_src / "_rules.yaml").write_text("rules: []", encoding="utf-8")

        role = _make_role(source_dir=str(role_src))
        bundle = export_role_to_dir(role, [], tmp_path / "output")

        rules_path = bundle / "_rules.yaml"
        assert rules_path.exists()

    def test_includes_seed_memories(self, tmp_path):
        role = _make_role()
        memories = [
            {"title": "Key insight", "content": "Important fact", "memory_type": "insight"},
            {"title": "A lesson", "content": "Do not do X", "memory_type": "lesson"},
        ]
        bundle = export_role_to_dir(role, [], tmp_path, include_memories=memories)

        mem_path = bundle / "memories" / "seed_memories.yaml"
        assert mem_path.exists()
        loaded = yaml.safe_load(mem_path.read_text(encoding="utf-8"))
        assert len(loaded) == 2
        assert loaded[0]["title"] == "Key insight"


# ===========================================================================
# TestExportRoleToZip
# ===========================================================================


class TestExportRoleToZip:
    """Tests for export_role_to_zip."""

    def test_creates_valid_zip(self, tmp_path):
        role = _make_role()
        skills = [_make_skill(id="skill_z")]
        zip_path = tmp_path / "role_bundle.zip"

        result = export_role_to_zip(role, skills, zip_path)

        assert result.exists()
        assert zipfile.is_zipfile(result)

    def test_zip_contains_expected_files(self, tmp_path):
        role = _make_role()
        skills = [_make_skill(id="my_skill")]
        zip_path = tmp_path / "role_bundle.zip"

        export_role_to_zip(role, skills, zip_path)

        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            # The ZIP paths include the role_id prefix
            assert any("_role.yaml" in n for n in names)
            assert any("manifest.yaml" in n for n in names)
            assert any("skill.yaml" in n for n in names)


# ===========================================================================
# TestValidateRoleBundle
# ===========================================================================


class TestValidateRoleBundle:
    """Tests for validate_role_bundle."""

    def test_valid_bundle_succeeds(self, tmp_path):
        role = _make_role()
        skills = [_make_skill(id="valid_skill")]
        bundle = export_role_to_dir(role, skills, tmp_path)

        result = validate_role_bundle(bundle)

        assert result.success is True
        assert not result.errors
        assert result.role_id == "test_role"
        assert "valid_skill" in result.skills_imported

    def test_missing_role_yaml_errors(self, tmp_path):
        bundle = tmp_path / "bad_bundle"
        bundle.mkdir()
        (bundle / "manifest.yaml").write_text("role_id: x", encoding="utf-8")

        result = validate_role_bundle(bundle)

        assert result.success is False
        assert any("Missing _role.yaml" in e for e in result.errors)

    def test_missing_manifest_yaml_errors(self, tmp_path):
        bundle = tmp_path / "bad_bundle"
        bundle.mkdir()
        (bundle / "_role.yaml").write_text("id: x\nname: X\npersona: hi", encoding="utf-8")

        result = validate_role_bundle(bundle)

        assert result.success is False
        assert any("Missing manifest.yaml" in e for e in result.errors)

    def test_missing_role_id_errors(self, tmp_path):
        role_data = {"name": "No ID Role", "persona": "I have no ID"}
        manifest_data = {"role_id": "", "sha256": ""}
        bundle = _write_role_bundle(tmp_path, role_yaml=role_data, manifest=manifest_data)

        # Recompute sha256 so integrity check doesn't mask the missing-id error
        role_bytes = (bundle / "_role.yaml").read_bytes()
        sha = hashlib.sha256(role_bytes).hexdigest()
        manifest_data["sha256"] = sha
        (bundle / "manifest.yaml").write_text(yaml.dump(manifest_data), encoding="utf-8")

        result = validate_role_bundle(bundle)

        assert result.success is False
        assert any("missing 'id'" in e for e in result.errors)

    def test_sha256_mismatch_errors(self, tmp_path):
        role_data = {"id": "r1", "name": "Role 1", "persona": "I am role 1"}
        manifest_data = {"sha256": "deadbeef" * 8}  # wrong hash
        bundle = _write_role_bundle(tmp_path, role_yaml=role_data, manifest=manifest_data)

        result = validate_role_bundle(bundle)

        assert result.success is False
        assert any("SHA-256 mismatch" in e for e in result.errors)

    def test_skills_directory_validated(self, tmp_path):
        role = _make_role()
        skills = [_make_skill(id="good_skill")]
        bundle = export_role_to_dir(role, skills, tmp_path)

        # Add a skill dir without skill.yaml
        bad_skill_dir = bundle / "skills" / "bad_skill"
        bad_skill_dir.mkdir(parents=True)

        result = validate_role_bundle(bundle)

        assert result.success is True  # warnings don't block success
        assert any("bad_skill" in w and "missing skill.yaml" in w for w in result.warnings)


# ===========================================================================
# TestImportRoleFromBundle (async)
# ===========================================================================


class TestImportRoleFromBundle:
    """Tests for import_role_from_bundle."""

    @pytest.fixture
    def mock_db_service(self):
        mock_ctx = AsyncMock()
        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.db.service.create_org_role", new_callable=AsyncMock) as mock_role,
            patch("src.db.service.create_org_skill", new_callable=AsyncMock) as mock_skill,
            patch("src.db.service.save_memory", new_callable=AsyncMock) as mock_mem,
            patch("src.db.session.get_db_session", return_value=mock_session_cm),
        ):
            # Build a namespace that mimics the module
            mock_svc = type(
                "MockSvc",
                (),
                {
                    "create_org_role": mock_role,
                    "create_org_skill": mock_skill,
                    "save_memory": mock_mem,
                },
            )()
            yield mock_svc

    @pytest.mark.asyncio
    async def test_successful_import(self, tmp_path, mock_db_service):
        mock_svc = mock_db_service

        role = _make_role()
        skills = [_make_skill(id="imp_skill")]
        bundle = export_role_to_dir(role, skills, tmp_path)

        result = await import_role_from_bundle(bundle, target_department_id="new_dept")

        assert result.success is True
        assert result.role_id == "test_role"
        assert result.target_department_id == "new_dept"
        assert "imp_skill" in result.skills_imported
        mock_svc.create_org_role.assert_awaited_once()
        mock_svc.create_org_skill.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_id_override_works(self, tmp_path, mock_db_service):
        mock_svc = mock_db_service

        role = _make_role(id="original_role")
        bundle = export_role_to_dir(role, [], tmp_path)

        result = await import_role_from_bundle(bundle, new_role_id="forked_role")

        assert result.success is True
        assert result.role_id == "forked_role"
        call_kwargs = mock_svc.create_org_role.call_args
        assert call_kwargs[1].get("role_id") == "forked_role" or (
            call_kwargs.kwargs and call_kwargs.kwargs.get("role_id") == "forked_role"
        )

    @pytest.mark.asyncio
    async def test_seed_memories_imported(self, tmp_path, mock_db_service):
        mock_svc = mock_db_service

        role = _make_role()
        memories = [
            {"title": "Seed 1", "content": "Content 1", "memory_type": "insight"},
            {"title": "Seed 2", "content": "Content 2", "memory_type": "lesson"},
        ]
        bundle = export_role_to_dir(role, [], tmp_path, include_memories=memories)

        result = await import_role_from_bundle(bundle)

        assert result.success is True
        assert result.seed_memories_count == 2
        assert mock_svc.save_memory.await_count == 2

    @pytest.mark.asyncio
    async def test_invalid_bundle_returns_error(self, tmp_path):
        """Importing an invalid bundle (no _role.yaml) returns errors."""
        bad_dir = tmp_path / "bad"
        bad_dir.mkdir()
        (bad_dir / "manifest.yaml").write_text("role_id: x", encoding="utf-8")

        result = await import_role_from_bundle(bad_dir)

        assert result.success is False
        assert len(result.errors) > 0


# ===========================================================================
# TestListAndSearch
# ===========================================================================


class TestListAndSearch:
    """Tests for list_role_bundles_in_dir and search_role_bundles."""

    def test_list_finds_bundles(self, tmp_path):
        # Export two roles to the same parent dir
        role_a = _make_role(id="role_a", name="Role A")
        role_b = _make_role(id="role_b", name="Role B")
        export_role_to_dir(role_a, [], tmp_path)
        export_role_to_dir(role_b, [], tmp_path)

        bundles = list_role_bundles_in_dir(tmp_path)

        assert len(bundles) == 2
        ids = {b.role_id for b in bundles}
        assert "role_a" in ids
        assert "role_b" in ids

    def test_search_filters_by_query(self, tmp_path):
        role_a = _make_role(id="role_alpha", name="Alpha Role", description="Handles alpha tasks")
        role_b = _make_role(id="role_beta", name="Beta Role", description="Handles beta tasks")
        export_role_to_dir(role_a, [], tmp_path)
        export_role_to_dir(role_b, [], tmp_path)

        bundles = list_role_bundles_in_dir(tmp_path)
        results = search_role_bundles(bundles, query="alpha")

        assert len(results) == 1
        assert results[0].role_id == "role_alpha"

    def test_search_filters_by_department(self, tmp_path):
        role_mkt = _make_role(id="role_mkt", name="Marketing Role", department_id="marketing")
        role_it = _make_role(id="role_it", name="IT Role", department_id="it")
        export_role_to_dir(role_mkt, [], tmp_path)
        export_role_to_dir(role_it, [], tmp_path)

        bundles = list_role_bundles_in_dir(tmp_path)
        results = search_role_bundles(bundles, department="marketing")

        assert len(results) == 1
        assert results[0].role_id == "role_mkt"
