"""Tests for SkillRegistry.merge_db_definitions() and get_source().

Covers DB-to-disk override, new entity addition, invalid entry skipping,
source tracking, cross-source manager validation, context_text handling,
and multiple merge operations.

ALL_TOOLS is patched so tests don't depend on the actual prompts module.
Temporary YAML files are created via tmp_path fixtures.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from src.skills.registry import SkillRegistry

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
# YAML content helpers
# ---------------------------------------------------------------------------


def _department_yaml(
    dept_id: str = "test_dept",
    name: str = "Test Department",
    description: str = "A test department",
    context: str = "Department context.",
) -> str:
    """Return YAML string for a valid _department.yaml."""
    data = {
        "id": dept_id,
        "name": name,
        "description": description,
        "context": context,
    }
    return yaml.dump(data, default_flow_style=False)


def _role_yaml(
    role_id: str = "test_role",
    name: str = "Test Role",
    department_id: str = "test_dept",
    description: str = "A test role",
    persona: str = "You are a test role.",
    briefing_skills: list[str] | None = None,
    manages: list[str] | None = None,
) -> str:
    """Return YAML string for a valid _role.yaml."""
    data = {
        "id": role_id,
        "name": name,
        "department_id": department_id,
        "description": description,
        "persona": persona,
        "connectors": ["google_ads"],
        "briefing_skills": briefing_skills or ["alpha_skill"],
    }
    if manages:
        data["manages"] = manages
    return yaml.dump(data, default_flow_style=False)


def _skill_yaml(
    skill_id: str = "alpha_skill",
    name: str = "Alpha Skill",
    category: str = "analysis",
    platforms: list[str] | None = None,
    tags: list[str] | None = None,
    tools: list[str] | None = None,
    model: str = "sonnet",
) -> str:
    """Return YAML string for a valid skill definition."""
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
        "max_turns": 10,
        "system_supplement": f"System supplement for {name}.",
        "prompt_template": f"Run {name} analysis.",
        "output_format": "## Results\nShow results.",
        "business_guidance": "Follow best practices.",
        "requires_approval": True,
        "author": "sidera",
        "created_at": "2025-01-01",
        "updated_at": "2025-01-01",
    }
    return yaml.dump(data, default_flow_style=False)


def _write_file(path: Path, content: str) -> Path:
    """Write content to path, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# DB dict helpers (simulate what _rows_to_dicts returns)
# ---------------------------------------------------------------------------


def _db_department(
    dept_id: str = "db_dept",
    name: str = "DB Department",
    description: str = "A DB department",
    context: str = "",
    context_text: str = "",
) -> dict:
    """Return a dict simulating a DB department row."""
    return {
        "dept_id": dept_id,
        "name": name,
        "description": description,
        "context": context,
        "context_text": context_text,
        "context_files": [],
        "source_dir": "",
    }


def _db_role(
    role_id: str = "db_role",
    name: str = "DB Role",
    department_id: str = "db_dept",
    description: str = "A DB role",
    persona: str = "You are a DB role.",
    briefing_skills: list[str] | None = None,
    manages: list[str] | None = None,
    delegation_model: str = "standard",
    synthesis_prompt: str = "",
    context_text: str = "",
) -> dict:
    """Return a dict simulating a DB role row."""
    return {
        "role_id": role_id,
        "name": name,
        "department_id": department_id,
        "description": description,
        "persona": persona,
        "connectors": ["google_ads"],
        "briefing_skills": briefing_skills or ["some_skill"],
        "schedule": None,
        "context_files": [],
        "source_dir": "",
        "context_text": context_text,
        "manages": manages or [],
        "delegation_model": delegation_model,
        "synthesis_prompt": synthesis_prompt,
    }


def _db_skill(
    skill_id: str = "db_skill",
    name: str = "DB Skill",
    description: str = "A DB skill",
    category: str = "analysis",
    model: str = "sonnet",
    platforms: list[str] | None = None,
    tags: list[str] | None = None,
    tools_required: list[str] | None = None,
    context_text: str = "",
    department_id: str = "",
    role_id: str = "",
) -> dict:
    """Return a dict simulating a DB skill row."""
    return {
        "skill_id": skill_id,
        "name": name,
        "version": "1.0",
        "description": description,
        "category": category,
        "platforms": platforms or ["google_ads"],
        "tags": tags or ["db"],
        "tools_required": tools_required or ["get_meta_campaigns"],
        "model": model,
        "max_turns": 20,
        "system_supplement": f"System supplement for {name}.",
        "prompt_template": f"Run {name}.",
        "output_format": "## Results\nResults here.",
        "business_guidance": "Follow guidance.",
        "context_files": [],
        "source_dir": "",
        "context_text": context_text,
        "schedule": None,
        "chain_after": None,
        "requires_approval": True,
        "department_id": department_id,
        "role_id": role_id,
        "author": "sidera",
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def hierarchy_dir(tmp_path: Path) -> Path:
    """Create a temp directory with a department, role, and two skills."""
    dept_dir = tmp_path / "test_dept"
    dept_dir.mkdir()
    _write_file(
        dept_dir / "_department.yaml",
        _department_yaml(dept_id="test_dept", name="Test Department"),
    )

    role_dir = dept_dir / "test_role"
    role_dir.mkdir()
    _write_file(
        role_dir / "_role.yaml",
        _role_yaml(
            role_id="test_role",
            department_id="test_dept",
            briefing_skills=["alpha_skill"],
        ),
    )

    _write_file(
        role_dir / "alpha.yaml",
        _skill_yaml(skill_id="alpha_skill", name="Alpha Skill"),
    )
    _write_file(
        role_dir / "beta.yaml",
        _skill_yaml(
            skill_id="beta_skill",
            name="Beta Skill",
            category="reporting",
        ),
    )

    return tmp_path


@pytest.fixture()
def loaded_registry(hierarchy_dir: Path) -> SkillRegistry:
    """Return a registry loaded from the hierarchy_dir fixture."""
    with patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS):
        reg = SkillRegistry(skills_dir=hierarchy_dir)
        reg.load_all()
    return reg


# ===========================================================================
# 1. DB department overrides disk department
# ===========================================================================


class TestDepartmentOverride:
    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_db_dept_overrides_disk(self, loaded_registry: SkillRegistry):
        """DB department with same ID replaces disk department."""
        assert loaded_registry.get_department("test_dept") is not None
        assert loaded_registry.get_department("test_dept").name == "Test Department"

        loaded_registry.merge_db_definitions(
            db_departments=[
                _db_department(
                    dept_id="test_dept",
                    name="Overridden Department",
                    description="Overridden from DB",
                )
            ],
            db_roles=[],
            db_skills=[],
        )

        dept = loaded_registry.get_department("test_dept")
        assert dept is not None
        assert dept.name == "Overridden Department"
        assert dept.description == "Overridden from DB"

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_db_dept_adds_new(self, loaded_registry: SkillRegistry):
        """DB department with new ID is added to registry."""
        assert loaded_registry.get_department("new_dept") is None

        loaded_registry.merge_db_definitions(
            db_departments=[
                _db_department(
                    dept_id="new_dept",
                    name="New Department",
                    description="Brand new from DB",
                )
            ],
            db_roles=[],
            db_skills=[],
        )

        dept = loaded_registry.get_department("new_dept")
        assert dept is not None
        assert dept.name == "New Department"

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_invalid_db_dept_skipped(self, loaded_registry: SkillRegistry):
        """DB department with empty name is skipped (validation fails)."""
        initial_count = loaded_registry.department_count

        loaded_registry.merge_db_definitions(
            db_departments=[
                _db_department(
                    dept_id="bad_dept",
                    name="",  # empty name fails validation
                    description="Valid description",
                )
            ],
            db_roles=[],
            db_skills=[],
        )

        assert loaded_registry.department_count == initial_count
        assert loaded_registry.get_department("bad_dept") is None


# ===========================================================================
# 2. DB role overrides disk role
# ===========================================================================


class TestRoleOverride:
    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_db_role_overrides_disk(self, loaded_registry: SkillRegistry):
        """DB role with same ID replaces disk role."""
        assert loaded_registry.get_role("test_role") is not None
        assert loaded_registry.get_role("test_role").name == "Test Role"

        loaded_registry.merge_db_definitions(
            db_departments=[],
            db_roles=[
                _db_role(
                    role_id="test_role",
                    name="Overridden Role",
                    department_id="test_dept",
                    description="Overridden from DB",
                )
            ],
            db_skills=[],
        )

        role = loaded_registry.get_role("test_role")
        assert role is not None
        assert role.name == "Overridden Role"
        assert role.description == "Overridden from DB"

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_db_role_adds_new(self, loaded_registry: SkillRegistry):
        """DB role with new ID is added to registry."""
        assert loaded_registry.get_role("new_role") is None

        loaded_registry.merge_db_definitions(
            db_departments=[],
            db_roles=[
                _db_role(
                    role_id="new_role",
                    name="New Role",
                    department_id="test_dept",
                    description="Brand new from DB",
                )
            ],
            db_skills=[],
        )

        role = loaded_registry.get_role("new_role")
        assert role is not None
        assert role.name == "New Role"

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_invalid_db_role_skipped(self, loaded_registry: SkillRegistry):
        """DB role with empty description is skipped (validation fails)."""
        initial_count = loaded_registry.role_count

        loaded_registry.merge_db_definitions(
            db_departments=[],
            db_roles=[
                _db_role(
                    role_id="bad_role",
                    name="Bad Role",
                    department_id="test_dept",
                    description="",  # empty description fails validation
                )
            ],
            db_skills=[],
        )

        assert loaded_registry.role_count == initial_count
        assert loaded_registry.get_role("bad_role") is None


# ===========================================================================
# 3. DB skill overrides disk skill
# ===========================================================================


class TestSkillOverride:
    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_db_skill_overrides_disk(self, loaded_registry: SkillRegistry):
        """DB skill with same ID replaces disk skill."""
        assert loaded_registry.get("alpha_skill") is not None
        assert loaded_registry.get("alpha_skill").name == "Alpha Skill"

        loaded_registry.merge_db_definitions(
            db_departments=[],
            db_roles=[],
            db_skills=[
                _db_skill(
                    skill_id="alpha_skill",
                    name="Overridden Alpha",
                    description="Overridden from DB",
                )
            ],
        )

        skill = loaded_registry.get("alpha_skill")
        assert skill is not None
        assert skill.name == "Overridden Alpha"
        assert skill.description == "Overridden from DB"

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_db_skill_adds_new(self, loaded_registry: SkillRegistry):
        """DB skill with new ID is added to registry."""
        assert loaded_registry.get("new_skill") is None

        loaded_registry.merge_db_definitions(
            db_departments=[],
            db_roles=[],
            db_skills=[
                _db_skill(
                    skill_id="new_skill",
                    name="New Skill",
                    description="Brand new from DB",
                )
            ],
        )

        skill = loaded_registry.get("new_skill")
        assert skill is not None
        assert skill.name == "New Skill"
        # Total skill count should increase by 1
        assert "new_skill" in loaded_registry

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_invalid_db_skill_bad_model_skipped(self, loaded_registry: SkillRegistry):
        """DB skill with invalid model is skipped."""
        initial_count = loaded_registry.count

        loaded_registry.merge_db_definitions(
            db_departments=[],
            db_roles=[],
            db_skills=[
                _db_skill(
                    skill_id="bad_skill",
                    name="Bad Skill",
                    description="Has invalid model",
                    model="gpt4",  # not in VALID_MODELS
                )
            ],
        )

        assert loaded_registry.count == initial_count
        assert loaded_registry.get("bad_skill") is None


# ===========================================================================
# 4. Source tracking
# ===========================================================================


class TestGetSource:
    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_disk_loaded_returns_disk(self, loaded_registry: SkillRegistry):
        """get_source() returns 'disk' for disk-loaded entities."""
        assert loaded_registry.get_source("dept", "test_dept") == "disk"
        assert loaded_registry.get_source("role", "test_role") == "disk"
        assert loaded_registry.get_source("skill", "alpha_skill") == "disk"

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_db_loaded_returns_db(self, loaded_registry: SkillRegistry):
        """get_source() returns 'db' for DB-loaded entities."""
        loaded_registry.merge_db_definitions(
            db_departments=[_db_department(dept_id="db_dept")],
            db_roles=[_db_role(role_id="db_role")],
            db_skills=[_db_skill(skill_id="db_skill")],
        )

        assert loaded_registry.get_source("dept", "db_dept") == "db"
        assert loaded_registry.get_source("role", "db_role") == "db"
        assert loaded_registry.get_source("skill", "db_skill") == "db"

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_override_changes_source_to_db(self, loaded_registry: SkillRegistry):
        """Overriding a disk entity with DB changes source to 'db'."""
        assert loaded_registry.get_source("dept", "test_dept") == "disk"

        loaded_registry.merge_db_definitions(
            db_departments=[
                _db_department(
                    dept_id="test_dept",
                    name="Overridden",
                    description="From DB",
                )
            ],
            db_roles=[],
            db_skills=[],
        )

        assert loaded_registry.get_source("dept", "test_dept") == "db"

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_unknown_entity_returns_unknown(self, loaded_registry: SkillRegistry):
        """get_source() returns 'unknown' for nonexistent entities."""
        assert loaded_registry.get_source("dept", "nonexistent") == "unknown"
        assert loaded_registry.get_source("role", "nonexistent") == "unknown"
        assert loaded_registry.get_source("skill", "nonexistent") == "unknown"


# ===========================================================================
# 5. Cross-source manager validation
# ===========================================================================


class TestCrossSourceManagerValidation:
    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_disk_manager_references_db_role(self, hierarchy_dir: Path):
        """A disk manager can reference a DB-defined managed role."""
        # Create a disk manager role that manages "db_sub_role"
        role_dir = hierarchy_dir / "test_dept" / "manager_role"
        role_dir.mkdir()
        _write_file(
            role_dir / "_role.yaml",
            _role_yaml(
                role_id="manager_role",
                department_id="test_dept",
                manages=["db_sub_role"],
                briefing_skills=["alpha_skill"],
            ),
        )

        with patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS):
            reg = SkillRegistry(skills_dir=hierarchy_dir)
            reg.load_all()

        # The managed role does not exist on disk, so it would warn
        assert reg.get_role("db_sub_role") is None

        # Now merge a DB role that satisfies the reference
        reg.merge_db_definitions(
            db_departments=[],
            db_roles=[
                _db_role(
                    role_id="db_sub_role",
                    name="DB Sub Role",
                    department_id="test_dept",
                    description="Managed by disk manager",
                )
            ],
            db_skills=[],
        )

        # The reference is now valid
        assert reg.get_role("db_sub_role") is not None
        managed = reg.get_managed_roles("manager_role")
        assert len(managed) == 1
        assert managed[0].id == "db_sub_role"

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_db_manager_references_disk_role(self, loaded_registry: SkillRegistry):
        """A DB manager can reference a disk-loaded managed role."""
        loaded_registry.merge_db_definitions(
            db_departments=[],
            db_roles=[
                _db_role(
                    role_id="db_manager",
                    name="DB Manager",
                    department_id="test_dept",
                    description="Manages disk role",
                    manages=["test_role"],
                    briefing_skills=[],
                )
            ],
            db_skills=[],
        )

        managed = loaded_registry.get_managed_roles("db_manager")
        assert len(managed) == 1
        assert managed[0].id == "test_role"


# ===========================================================================
# 6. briefing_skills referencing DB-defined skills
# ===========================================================================


class TestBriefingSkillReferences:
    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_briefing_skills_reference_db_skill(self, loaded_registry: SkillRegistry):
        """A role's briefing_skills can reference a DB-defined skill."""
        loaded_registry.merge_db_definitions(
            db_departments=[],
            db_roles=[
                _db_role(
                    role_id="new_role",
                    name="New Role",
                    department_id="test_dept",
                    description="Uses DB skill",
                    briefing_skills=["db_skill"],
                )
            ],
            db_skills=[
                _db_skill(
                    skill_id="db_skill",
                    name="DB Skill",
                    description="A skill from DB",
                )
            ],
        )

        role = loaded_registry.get_role("new_role")
        assert role is not None
        assert "db_skill" in role.briefing_skills

        # The referenced skill exists in the registry
        skill = loaded_registry.get("db_skill")
        assert skill is not None
        assert skill.name == "DB Skill"


# ===========================================================================
# 7. context_text handling
# ===========================================================================


class TestContextText:
    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_db_department_with_context_text(self, loaded_registry: SkillRegistry):
        """DB department can carry pre-rendered context_text."""
        loaded_registry.merge_db_definitions(
            db_departments=[
                _db_department(
                    dept_id="ctx_dept",
                    name="Context Dept",
                    description="Has context text",
                    context_text="This is pre-rendered department context.",
                )
            ],
            db_roles=[],
            db_skills=[],
        )

        dept = loaded_registry.get_department("ctx_dept")
        assert dept is not None
        assert dept.context_text == "This is pre-rendered department context."

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_db_role_with_context_text(self, loaded_registry: SkillRegistry):
        """DB role can carry pre-rendered context_text."""
        loaded_registry.merge_db_definitions(
            db_departments=[],
            db_roles=[
                _db_role(
                    role_id="ctx_role",
                    name="Context Role",
                    department_id="test_dept",
                    description="Has context text",
                    context_text="Pre-rendered role context from DB.",
                )
            ],
            db_skills=[],
        )

        role = loaded_registry.get_role("ctx_role")
        assert role is not None
        assert role.context_text == "Pre-rendered role context from DB."

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_db_skill_with_context_text(self, loaded_registry: SkillRegistry):
        """DB skill can carry pre-rendered context_text."""
        loaded_registry.merge_db_definitions(
            db_departments=[],
            db_roles=[],
            db_skills=[
                _db_skill(
                    skill_id="ctx_skill",
                    name="Context Skill",
                    description="Has context text",
                    context_text="Pre-rendered skill context from DB.",
                )
            ],
        )

        skill = loaded_registry.get("ctx_skill")
        assert skill is not None
        assert skill.context_text == "Pre-rendered skill context from DB."


# ===========================================================================
# 8. Multiple merges
# ===========================================================================


class TestMultipleMerges:
    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_second_merge_overwrites_first(self, loaded_registry: SkillRegistry):
        """A second merge_db_definitions call overwrites data from the first."""
        # First merge: add a DB department
        loaded_registry.merge_db_definitions(
            db_departments=[
                _db_department(
                    dept_id="dynamic_dept",
                    name="First Version",
                    description="First pass",
                )
            ],
            db_roles=[],
            db_skills=[],
        )

        dept = loaded_registry.get_department("dynamic_dept")
        assert dept.name == "First Version"

        # Second merge: overwrite the same department
        loaded_registry.merge_db_definitions(
            db_departments=[
                _db_department(
                    dept_id="dynamic_dept",
                    name="Second Version",
                    description="Second pass",
                )
            ],
            db_roles=[],
            db_skills=[],
        )

        dept = loaded_registry.get_department("dynamic_dept")
        assert dept.name == "Second Version"
        assert dept.description == "Second pass"

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_merge_preserves_disk_entries_not_overridden(
        self,
        loaded_registry: SkillRegistry,
    ):
        """Merge only replaces matching IDs, leaving other disk entries intact."""
        initial_skill_count = loaded_registry.count

        loaded_registry.merge_db_definitions(
            db_departments=[],
            db_roles=[],
            db_skills=[
                _db_skill(
                    skill_id="extra_skill",
                    name="Extra",
                    description="A new DB skill",
                )
            ],
        )

        # Original disk skills still present, plus the new one
        assert loaded_registry.count == initial_skill_count + 1
        assert loaded_registry.get("alpha_skill") is not None
        assert loaded_registry.get("beta_skill") is not None
        assert loaded_registry.get("extra_skill") is not None


# ===========================================================================
# 9. Edge cases
# ===========================================================================


class TestEdgeCases:
    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_empty_merge_is_noop(self, loaded_registry: SkillRegistry):
        """Merging empty lists changes nothing."""
        dept_count = loaded_registry.department_count
        role_count = loaded_registry.role_count
        skill_count = loaded_registry.count

        loaded_registry.merge_db_definitions(
            db_departments=[],
            db_roles=[],
            db_skills=[],
        )

        assert loaded_registry.department_count == dept_count
        assert loaded_registry.role_count == role_count
        assert loaded_registry.count == skill_count

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_db_dept_missing_description_skipped(self, loaded_registry: SkillRegistry):
        """DB department with empty description is skipped by validation."""
        loaded_registry.merge_db_definitions(
            db_departments=[
                {
                    "dept_id": "no_desc",
                    "name": "No Description",
                    "description": "",
                    "context": "",
                    "context_text": "",
                    "context_files": [],
                    "source_dir": "",
                }
            ],
            db_roles=[],
            db_skills=[],
        )

        assert loaded_registry.get_department("no_desc") is None

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_db_skill_empty_prompt_template_skipped(
        self,
        loaded_registry: SkillRegistry,
    ):
        """DB skill with empty prompt_template is skipped by validation."""
        initial = loaded_registry.count

        loaded_registry.merge_db_definitions(
            db_departments=[],
            db_roles=[],
            db_skills=[
                {
                    "skill_id": "bad_prompt",
                    "name": "Bad Prompt",
                    "version": "1.0",
                    "description": "Has empty prompt",
                    "category": "analysis",
                    "platforms": ["google_ads"],
                    "tags": ["test"],
                    "tools_required": ["get_meta_campaigns"],
                    "model": "sonnet",
                    "max_turns": 10,
                    "system_supplement": "Supplement.",
                    "prompt_template": "",  # empty
                    "output_format": "Format.",
                    "business_guidance": "Guidance.",
                    "context_files": [],
                    "source_dir": "",
                    "context_text": "",
                    "schedule": None,
                    "chain_after": None,
                    "requires_approval": True,
                    "department_id": "",
                    "role_id": "",
                    "author": "sidera",
                }
            ],
        )

        assert loaded_registry.count == initial
        assert loaded_registry.get("bad_prompt") is None

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_malformed_db_entry_dict_skipped(self, loaded_registry: SkillRegistry):
        """A completely malformed DB dict is skipped without crashing."""
        initial_dept_count = loaded_registry.department_count

        loaded_registry.merge_db_definitions(
            db_departments=[{"garbage": True}],  # missing all fields
            db_roles=[],
            db_skills=[],
        )

        # Should not crash, and the bad entry should be skipped
        assert loaded_registry.department_count == initial_dept_count

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_db_role_with_manages_and_briefing(self, loaded_registry: SkillRegistry):
        """DB role with both manages and briefing_skills is valid (manager role)."""
        loaded_registry.merge_db_definitions(
            db_departments=[],
            db_roles=[
                _db_role(
                    role_id="full_manager",
                    name="Full Manager",
                    department_id="test_dept",
                    description="Has both manages and briefing_skills",
                    manages=["test_role"],
                    briefing_skills=["alpha_skill"],
                    delegation_model="fast",
                    synthesis_prompt="Combine all results.",
                )
            ],
            db_skills=[],
        )

        role = loaded_registry.get_role("full_manager")
        assert role is not None
        assert role.manages == ("test_role",)
        assert role.briefing_skills == ("alpha_skill",)
        assert role.delegation_model == "fast"
        assert role.synthesis_prompt == "Combine all results."
        assert loaded_registry.is_manager("full_manager")

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_db_skill_with_department_and_role(self, loaded_registry: SkillRegistry):
        """DB skill assigned to a department and role is stored correctly."""
        loaded_registry.merge_db_definitions(
            db_departments=[],
            db_roles=[],
            db_skills=[
                _db_skill(
                    skill_id="assigned_skill",
                    name="Assigned Skill",
                    description="Belongs to dept and role",
                    department_id="test_dept",
                    role_id="test_role",
                )
            ],
        )

        skill = loaded_registry.get("assigned_skill")
        assert skill is not None
        assert skill.department_id == "test_dept"
        assert skill.role_id == "test_role"
