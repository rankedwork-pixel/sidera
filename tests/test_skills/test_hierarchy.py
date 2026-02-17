"""Tests for the department → role → skill hierarchy.

Covers:
- Schema: DepartmentDefinition and RoleDefinition dataclasses
- Schema: load_department_from_yaml() and load_role_from_yaml()
- Schema: validate_department() and validate_role()
- Schema: resolve_hierarchy_context_files() and load_hierarchy_context_text()
- Schema: SkillDefinition.department_id and .role_id fields
- Registry: three-level discovery (dept → role → skill)
- Registry: backward-compatible loose skill loading
- Registry: hierarchy lookup methods
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from src.skills.schema import (
    DepartmentDefinition,
    RoleDefinition,
    SkillDefinition,
    SkillLoadError,
    load_department_from_yaml,
    load_hierarchy_context_text,
    load_role_from_yaml,
    resolve_hierarchy_context_files,
    validate_department,
    validate_role,
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
# YAML helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> Path:
    """Write text to a file, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _department_yaml(
    dept_id: str = "marketing",
    name: str = "Marketing Department",
    description: str = "Manages paid acquisition",
    context: str = "",
    context_files: list[str] | None = None,
    **overrides: object,
) -> str:
    data: dict[str, object] = {
        "id": dept_id,
        "name": name,
        "description": description,
    }
    if context:
        data["context"] = context
    if context_files is not None:
        data["context_files"] = context_files
    data.update(overrides)
    return yaml.dump(data, default_flow_style=False)


def _role_yaml(
    role_id: str = "media_buyer",
    name: str = "Media Buyer",
    department_id: str = "marketing",
    description: str = "Manages media buying",
    persona: str = "",
    connectors: list[str] | None = None,
    briefing_skills: list[str] | None = None,
    schedule: str | None = None,
    **overrides: object,
) -> str:
    data: dict[str, object] = {
        "id": role_id,
        "name": name,
        "department_id": department_id,
        "description": description,
    }
    if persona:
        data["persona"] = persona
    if connectors is not None:
        data["connectors"] = connectors
    if briefing_skills is not None:
        data["briefing_skills"] = briefing_skills
    else:
        data["briefing_skills"] = ["skill_a", "skill_b"]
    if schedule is not None:
        data["schedule"] = schedule
    data.update(overrides)
    return yaml.dump(data, default_flow_style=False)


def _skill_yaml(
    skill_id: str = "test_skill",
    name: str = "Test Skill",
    **overrides: object,
) -> str:
    data: dict[str, object] = {
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
        "system_supplement": f"System for {name}.",
        "prompt_template": f"Run {name}.",
        "output_format": "## Results\nShow results.",
        "business_guidance": "Follow best practices.",
    }
    data.update(overrides)
    return yaml.dump(data, default_flow_style=False)


# ===========================================================================
# 1. DepartmentDefinition dataclass
# ===========================================================================


class TestDepartmentDefinition:
    """Verify DepartmentDefinition dataclass behavior."""

    def test_basic_creation(self):
        dept = DepartmentDefinition(
            id="mktg",
            name="Marketing",
            description="Desc",
        )
        assert dept.id == "mktg"
        assert dept.name == "Marketing"
        assert dept.description == "Desc"
        assert dept.context == ""
        assert dept.context_files == ()
        assert dept.source_dir == ""

    def test_all_fields(self):
        dept = DepartmentDefinition(
            id="mktg",
            name="Marketing",
            description="D",
            context="Shared context",
            context_files=("*.md",),
            source_dir="/tmp/mktg",
        )
        assert dept.context == "Shared context"
        assert dept.context_files == ("*.md",)
        assert dept.source_dir == "/tmp/mktg"

    def test_frozen(self):
        dept = DepartmentDefinition(
            id="x",
            name="X",
            description="D",
        )
        with pytest.raises(AttributeError):
            dept.id = "y"  # type: ignore[misc]


# ===========================================================================
# 2. RoleDefinition dataclass
# ===========================================================================


class TestRoleDefinition:
    """Verify RoleDefinition dataclass behavior."""

    def test_basic_creation(self):
        role = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="Buys media",
        )
        assert role.id == "buyer"
        assert role.department_id == "mktg"
        assert role.persona == ""
        assert role.connectors == ()
        assert role.briefing_skills == ()
        assert role.schedule is None

    def test_all_fields(self):
        role = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="Buys media",
            persona="You are a media buyer.",
            connectors=("google_ads", "meta"),
            briefing_skills=("anomaly_detector", "pacing"),
            schedule="0 7 * * 1-5",
            context_files=("context/*.md",),
            source_dir="/tmp/buyer",
        )
        assert role.persona == "You are a media buyer."
        assert len(role.connectors) == 2
        assert len(role.briefing_skills) == 2
        assert role.schedule == "0 7 * * 1-5"

    def test_frozen(self):
        role = RoleDefinition(
            id="x",
            name="X",
            department_id="d",
            description="D",
        )
        with pytest.raises(AttributeError):
            role.id = "y"  # type: ignore[misc]


# ===========================================================================
# 3. SkillDefinition hierarchy fields
# ===========================================================================


class TestSkillDefinitionHierarchyFields:
    """Verify department_id and role_id on SkillDefinition."""

    def test_defaults_empty(self):
        skill = SkillDefinition(
            id="s",
            name="S",
            version="1.0",
            description="D",
            category="analysis",
            platforms=("google_ads",),
            tags=("t",),
            tools_required=("get_meta_campaigns",),
            model="sonnet",
            max_turns=10,
            system_supplement="sup",
            prompt_template="tmpl",
            output_format="fmt",
            business_guidance="guide",
        )
        assert skill.department_id == ""
        assert skill.role_id == ""

    def test_set_hierarchy(self):
        skill = SkillDefinition(
            id="s",
            name="S",
            version="1.0",
            description="D",
            category="analysis",
            platforms=("google_ads",),
            tags=("t",),
            tools_required=("get_meta_campaigns",),
            model="sonnet",
            max_turns=10,
            system_supplement="sup",
            prompt_template="tmpl",
            output_format="fmt",
            business_guidance="guide",
            department_id="mktg",
            role_id="buyer",
        )
        assert skill.department_id == "mktg"
        assert skill.role_id == "buyer"


# ===========================================================================
# 4. load_department_from_yaml
# ===========================================================================


class TestLoadDepartmentFromYaml:
    """Test department YAML loading."""

    def test_load_valid(self, tmp_path: Path):
        _write(tmp_path / "_department.yaml", _department_yaml())
        dept = load_department_from_yaml(tmp_path / "_department.yaml")
        assert dept.id == "marketing"
        assert dept.name == "Marketing Department"
        assert dept.source_dir == str(tmp_path)

    def test_load_with_context(self, tmp_path: Path):
        _write(
            tmp_path / "_department.yaml",
            _department_yaml(
                context="Q1 goal: $2M revenue",
            ),
        )
        dept = load_department_from_yaml(tmp_path / "_department.yaml")
        assert dept.context == "Q1 goal: $2M revenue"

    def test_load_with_context_files(self, tmp_path: Path):
        _write(
            tmp_path / "_department.yaml",
            _department_yaml(
                context_files=["context/*.md"],
            ),
        )
        dept = load_department_from_yaml(tmp_path / "_department.yaml")
        assert dept.context_files == ("context/*.md",)

    def test_load_missing_file(self, tmp_path: Path):
        with pytest.raises(SkillLoadError, match="not found"):
            load_department_from_yaml(tmp_path / "nope.yaml")

    def test_load_wrong_extension(self, tmp_path: Path):
        _write(tmp_path / "dept.json", "{}")
        with pytest.raises(SkillLoadError, match="Expected .yaml"):
            load_department_from_yaml(tmp_path / "dept.json")

    def test_load_invalid_yaml(self, tmp_path: Path):
        _write(tmp_path / "_department.yaml", "{{bad yaml")
        with pytest.raises(SkillLoadError, match="Invalid YAML"):
            load_department_from_yaml(tmp_path / "_department.yaml")

    def test_load_non_dict(self, tmp_path: Path):
        _write(tmp_path / "_department.yaml", "- list\n- items\n")
        with pytest.raises(SkillLoadError, match="Expected a YAML mapping"):
            load_department_from_yaml(tmp_path / "_department.yaml")

    def test_load_missing_required_fields(self, tmp_path: Path):
        _write(tmp_path / "_department.yaml", yaml.dump({"id": "x"}))
        with pytest.raises(SkillLoadError, match="Missing required"):
            load_department_from_yaml(tmp_path / "_department.yaml")

    def test_load_yml_extension(self, tmp_path: Path):
        _write(tmp_path / "_department.yml", _department_yaml())
        dept = load_department_from_yaml(tmp_path / "_department.yml")
        assert dept.id == "marketing"


# ===========================================================================
# 5. load_role_from_yaml
# ===========================================================================


class TestLoadRoleFromYaml:
    """Test role YAML loading."""

    def test_load_valid(self, tmp_path: Path):
        _write(tmp_path / "_role.yaml", _role_yaml())
        role = load_role_from_yaml(tmp_path / "_role.yaml")
        assert role.id == "media_buyer"
        assert role.department_id == "marketing"
        assert role.source_dir == str(tmp_path)

    def test_load_with_persona(self, tmp_path: Path):
        _write(
            tmp_path / "_role.yaml",
            _role_yaml(
                persona="You are a media buyer.",
            ),
        )
        role = load_role_from_yaml(tmp_path / "_role.yaml")
        assert "media buyer" in role.persona

    def test_load_with_connectors(self, tmp_path: Path):
        _write(
            tmp_path / "_role.yaml",
            _role_yaml(
                connectors=["google_ads", "meta", "bigquery"],
            ),
        )
        role = load_role_from_yaml(tmp_path / "_role.yaml")
        assert role.connectors == ("google_ads", "meta", "bigquery")

    def test_load_with_schedule(self, tmp_path: Path):
        _write(
            tmp_path / "_role.yaml",
            _role_yaml(
                schedule="0 7 * * 1-5",
            ),
        )
        role = load_role_from_yaml(tmp_path / "_role.yaml")
        assert role.schedule == "0 7 * * 1-5"

    def test_load_missing_file(self, tmp_path: Path):
        with pytest.raises(SkillLoadError, match="not found"):
            load_role_from_yaml(tmp_path / "nope.yaml")

    def test_load_wrong_extension(self, tmp_path: Path):
        _write(tmp_path / "role.json", "{}")
        with pytest.raises(SkillLoadError, match="Expected .yaml"):
            load_role_from_yaml(tmp_path / "role.json")

    def test_load_invalid_yaml(self, tmp_path: Path):
        _write(tmp_path / "_role.yaml", "{{bad yaml")
        with pytest.raises(SkillLoadError, match="Invalid YAML"):
            load_role_from_yaml(tmp_path / "_role.yaml")

    def test_load_missing_required_fields(self, tmp_path: Path):
        _write(
            tmp_path / "_role.yaml",
            yaml.dump(
                {
                    "id": "x",
                    "name": "X",
                }
            ),
        )
        with pytest.raises(SkillLoadError, match="Missing required"):
            load_role_from_yaml(tmp_path / "_role.yaml")

    def test_load_yml_extension(self, tmp_path: Path):
        _write(tmp_path / "_role.yml", _role_yaml())
        role = load_role_from_yaml(tmp_path / "_role.yml")
        assert role.id == "media_buyer"


# ===========================================================================
# 6. validate_department
# ===========================================================================


class TestValidateDepartment:
    """Test department validation."""

    def test_valid_department(self):
        dept = DepartmentDefinition(
            id="marketing",
            name="Marketing",
            description="Paid acquisition",
        )
        assert validate_department(dept) == []

    def test_empty_id(self):
        dept = DepartmentDefinition(
            id="",
            name="Marketing",
            description="D",
        )
        errors = validate_department(dept)
        assert any("ID is empty" in e for e in errors)

    def test_invalid_id_chars(self):
        dept = DepartmentDefinition(
            id="bad id!",
            name="X",
            description="D",
        )
        errors = validate_department(dept)
        assert any("invalid characters" in e for e in errors)

    def test_empty_name(self):
        dept = DepartmentDefinition(
            id="x",
            name="",
            description="D",
        )
        errors = validate_department(dept)
        assert any("name is empty" in e for e in errors)

    def test_empty_description(self):
        dept = DepartmentDefinition(
            id="x",
            name="X",
            description="",
        )
        errors = validate_department(dept)
        assert any("description is empty" in e for e in errors)

    def test_context_files_without_source_dir(self):
        dept = DepartmentDefinition(
            id="x",
            name="X",
            description="D",
            context_files=("*.md",),
            source_dir="",
        )
        errors = validate_department(dept)
        assert any("source_dir is empty" in e for e in errors)

    def test_hyphen_and_underscore_in_id(self):
        dept = DepartmentDefinition(
            id="my-dept_01",
            name="X",
            description="D",
        )
        assert validate_department(dept) == []


# ===========================================================================
# 7. validate_role
# ===========================================================================


class TestValidateRole:
    """Test role validation."""

    def test_valid_role(self):
        role = RoleDefinition(
            id="buyer",
            name="Buyer",
            department_id="mktg",
            description="D",
            briefing_skills=("skill_a",),
        )
        assert validate_role(role) == []

    def test_empty_id(self):
        role = RoleDefinition(
            id="",
            name="X",
            department_id="m",
            description="D",
            briefing_skills=("s",),
        )
        errors = validate_role(role)
        assert any("ID is empty" in e for e in errors)

    def test_invalid_id_chars(self):
        role = RoleDefinition(
            id="bad role!",
            name="X",
            department_id="m",
            description="D",
            briefing_skills=("s",),
        )
        errors = validate_role(role)
        assert any("invalid characters" in e for e in errors)

    def test_empty_name(self):
        role = RoleDefinition(
            id="x",
            name="",
            department_id="m",
            description="D",
            briefing_skills=("s",),
        )
        errors = validate_role(role)
        assert any("name is empty" in e for e in errors)

    def test_empty_department_id(self):
        role = RoleDefinition(
            id="x",
            name="X",
            department_id="",
            description="D",
            briefing_skills=("s",),
        )
        errors = validate_role(role)
        assert any("department_id is empty" in e for e in errors)

    def test_empty_description(self):
        role = RoleDefinition(
            id="x",
            name="X",
            department_id="m",
            description="",
            briefing_skills=("s",),
        )
        errors = validate_role(role)
        assert any("description is empty" in e for e in errors)

    def test_no_briefing_skills(self):
        role = RoleDefinition(
            id="x",
            name="X",
            department_id="m",
            description="D",
        )
        errors = validate_role(role)
        assert any("no briefing_skills" in e for e in errors)

    def test_context_files_without_source_dir(self):
        role = RoleDefinition(
            id="x",
            name="X",
            department_id="m",
            description="D",
            briefing_skills=("s",),
            context_files=("*.md",),
            source_dir="",
        )
        errors = validate_role(role)
        assert any("source_dir is empty" in e for e in errors)


# ===========================================================================
# 8. Hierarchy context resolution
# ===========================================================================


class TestHierarchyContextResolution:
    """Test resolve_hierarchy_context_files and load_hierarchy_context_text."""

    def test_resolve_empty_patterns(self, tmp_path: Path):
        result = resolve_hierarchy_context_files((), str(tmp_path))
        assert result == []

    def test_resolve_empty_source_dir(self):
        result = resolve_hierarchy_context_files(("*.md",), "")
        assert result == []

    def test_resolve_nonexistent_dir(self):
        result = resolve_hierarchy_context_files(
            ("*.md",),
            "/nonexistent/dir",
        )
        assert result == []

    def test_resolve_matching_files(self, tmp_path: Path):
        _write(tmp_path / "context" / "a.md", "Alpha")
        _write(tmp_path / "context" / "b.md", "Beta")
        _write(tmp_path / "context" / "c.txt", "Ignored")

        result = resolve_hierarchy_context_files(
            ("context/*.md",),
            str(tmp_path),
        )
        assert len(result) == 2
        assert all(p.suffix == ".md" for p in result)

    def test_resolve_deduplicated(self, tmp_path: Path):
        _write(tmp_path / "notes.md", "Content")
        result = resolve_hierarchy_context_files(
            ("*.md", "notes.md"),
            str(tmp_path),
        )
        assert len(result) == 1

    def test_load_text_empty(self, tmp_path: Path):
        text = load_hierarchy_context_text((), str(tmp_path))
        assert text == ""

    def test_load_text_with_files(self, tmp_path: Path):
        _write(tmp_path / "context" / "info.md", "Department info here")
        text = load_hierarchy_context_text(
            ("context/*.md",),
            str(tmp_path),
        )
        assert "# Context: context/info.md" in text
        assert "Department info here" in text

    def test_load_text_multiple_files(self, tmp_path: Path):
        _write(tmp_path / "a.md", "First")
        _write(tmp_path / "b.md", "Second")
        text = load_hierarchy_context_text(("*.md",), str(tmp_path))
        assert "First" in text
        assert "Second" in text

    def test_load_text_skips_empty_files(self, tmp_path: Path):
        _write(tmp_path / "a.md", "Content")
        _write(tmp_path / "b.md", "")  # empty
        text = load_hierarchy_context_text(("*.md",), str(tmp_path))
        assert "Content" in text
        assert "b.md" not in text


# ===========================================================================
# 9. Registry — three-level discovery
# ===========================================================================


class TestRegistryHierarchyDiscovery:
    """Test SkillRegistry three-level hierarchy loading."""

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_full_hierarchy(self, tmp_path: Path):
        """Dept → role → skill fully wired."""
        from src.skills.registry import SkillRegistry

        # Department
        dept_dir = tmp_path / "marketing"
        dept_dir.mkdir()
        _write(dept_dir / "_department.yaml", _department_yaml())

        # Role
        role_dir = dept_dir / "buyer"
        role_dir.mkdir()
        _write(
            role_dir / "_role.yaml",
            _role_yaml(
                role_id="buyer",
                briefing_skills=["skill_a"],
            ),
        )

        # Skill inside the role
        _write(
            role_dir / "skill_a.yaml",
            _skill_yaml(
                skill_id="skill_a",
                name="Skill A",
            ),
        )

        reg = SkillRegistry(skills_dir=tmp_path)
        count = reg.load_all()

        assert count == 1
        assert reg.department_count == 1
        assert reg.role_count == 1

        skill = reg.get("skill_a")
        assert skill is not None
        assert skill.department_id == "marketing"
        assert skill.role_id == "buyer"

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_multiple_roles_in_department(self, tmp_path: Path):
        from src.skills.registry import SkillRegistry

        dept_dir = tmp_path / "marketing"
        dept_dir.mkdir()
        _write(dept_dir / "_department.yaml", _department_yaml())

        # Role 1
        r1 = dept_dir / "alpha_role"
        r1.mkdir()
        _write(
            r1 / "_role.yaml",
            _role_yaml(
                role_id="alpha",
                briefing_skills=["s1"],
            ),
        )
        _write(r1 / "s1.yaml", _skill_yaml(skill_id="s1", name="S1"))

        # Role 2
        r2 = dept_dir / "beta_role"
        r2.mkdir()
        _write(
            r2 / "_role.yaml",
            _role_yaml(
                role_id="beta",
                briefing_skills=["s2"],
            ),
        )
        _write(r2 / "s2.yaml", _skill_yaml(skill_id="s2", name="S2"))

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        assert reg.role_count == 2
        assert reg.count == 2

        roles = reg.list_roles("marketing")
        assert len(roles) == 2
        assert {r.id for r in roles} == {"alpha", "beta"}

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_loose_skills_still_work(self, tmp_path: Path):
        """Skills not inside a dept/role hierarchy load as loose."""
        from src.skills.registry import SkillRegistry

        # Loose flat skill
        _write(
            tmp_path / "loose.yaml",
            _skill_yaml(
                skill_id="loose_skill",
                name="Loose",
            ),
        )

        reg = SkillRegistry(skills_dir=tmp_path)
        count = reg.load_all()

        assert count == 1
        skill = reg.get("loose_skill")
        assert skill.department_id == ""
        assert skill.role_id == ""

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_loose_folder_skills_still_work(self, tmp_path: Path):
        """Folder-based skills not inside a dept load as loose."""
        from src.skills.registry import SkillRegistry

        folder = tmp_path / "my_skill"
        folder.mkdir()
        _write(
            folder / "skill.yaml",
            _skill_yaml(
                skill_id="folder_loose",
                name="Folder Loose",
            ),
        )

        reg = SkillRegistry(skills_dir=tmp_path)
        count = reg.load_all()

        assert count == 1
        skill = reg.get("folder_loose")
        assert skill.department_id == ""
        assert skill.role_id == ""

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_mixed_hierarchy_and_loose(self, tmp_path: Path):
        """Dept skills and loose skills coexist."""
        from src.skills.registry import SkillRegistry

        # Dept + role + skill
        dept_dir = tmp_path / "marketing"
        dept_dir.mkdir()
        _write(dept_dir / "_department.yaml", _department_yaml())
        role_dir = dept_dir / "buyer"
        role_dir.mkdir()
        _write(
            role_dir / "_role.yaml",
            _role_yaml(
                role_id="buyer",
                briefing_skills=["skill_h"],
            ),
        )
        _write(
            role_dir / "skill_h.yaml",
            _skill_yaml(
                skill_id="skill_h",
                name="Hierarchy Skill",
            ),
        )

        # Loose skill
        _write(
            tmp_path / "standalone.yaml",
            _skill_yaml(
                skill_id="standalone",
                name="Standalone",
            ),
        )

        reg = SkillRegistry(skills_dir=tmp_path)
        count = reg.load_all()

        assert count == 2
        assert reg.get("skill_h").department_id == "marketing"
        assert reg.get("standalone").department_id == ""

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_folder_skill_inside_role(self, tmp_path: Path):
        """Folder-based skill inside a role dir gets hierarchy wired."""
        from src.skills.registry import SkillRegistry

        dept_dir = tmp_path / "marketing"
        dept_dir.mkdir()
        _write(dept_dir / "_department.yaml", _department_yaml())

        role_dir = dept_dir / "buyer"
        role_dir.mkdir()
        _write(
            role_dir / "_role.yaml",
            _role_yaml(
                role_id="buyer",
                briefing_skills=["deep_skill"],
            ),
        )

        # Folder-based skill
        skill_dir = role_dir / "deep_skill"
        skill_dir.mkdir()
        _write(
            skill_dir / "skill.yaml",
            _skill_yaml(
                skill_id="deep_skill",
                name="Deep Skill",
            ),
        )

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        skill = reg.get("deep_skill")
        assert skill is not None
        assert skill.department_id == "marketing"
        assert skill.role_id == "buyer"

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_department_without_roles(self, tmp_path: Path):
        """Department with no roles — skills still get department_id."""
        from src.skills.registry import SkillRegistry

        dept_dir = tmp_path / "ops"
        dept_dir.mkdir()
        _write(
            dept_dir / "_department.yaml",
            _department_yaml(
                dept_id="ops",
                name="Operations",
            ),
        )

        # Flat skill directly in dept dir (no role)
        _write(
            dept_dir / "task.yaml",
            _skill_yaml(
                skill_id="task",
                name="Task",
            ),
        )

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        skill = reg.get("task")
        assert skill.department_id == "ops"
        assert skill.role_id == ""

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_invalid_department_skipped(self, tmp_path: Path):
        """Invalid department YAML is skipped gracefully."""
        from src.skills.registry import SkillRegistry

        dept_dir = tmp_path / "bad_dept"
        dept_dir.mkdir()
        _write(dept_dir / "_department.yaml", yaml.dump({"id": ""}))

        reg = SkillRegistry(skills_dir=tmp_path)
        count = reg.load_all()

        assert count == 0
        assert reg.department_count == 0

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_invalid_role_skipped(self, tmp_path: Path):
        """Invalid role YAML is skipped gracefully."""
        from src.skills.registry import SkillRegistry

        dept_dir = tmp_path / "marketing"
        dept_dir.mkdir()
        _write(dept_dir / "_department.yaml", _department_yaml())

        role_dir = dept_dir / "bad_role"
        role_dir.mkdir()
        _write(role_dir / "_role.yaml", yaml.dump({"id": ""}))

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        assert reg.department_count == 1
        assert reg.role_count == 0

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_context_examples_dirs_skipped(self, tmp_path: Path):
        """context/, examples/, guidelines/ dirs are not treated as roles."""
        from src.skills.registry import SkillRegistry

        dept_dir = tmp_path / "marketing"
        dept_dir.mkdir()
        _write(dept_dir / "_department.yaml", _department_yaml())

        # These should NOT be treated as role dirs
        for name in ("context", "examples", "guidelines"):
            d = dept_dir / name
            d.mkdir()
            _write(d / "info.md", "Some content")

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        assert reg.role_count == 0
        assert reg.count == 0


# ===========================================================================
# 10. Registry — hierarchy lookup methods
# ===========================================================================


class TestRegistryHierarchyLookup:
    """Test new lookup methods on SkillRegistry."""

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_get_department(self, tmp_path: Path):
        from src.skills.registry import SkillRegistry

        dept_dir = tmp_path / "marketing"
        dept_dir.mkdir()
        _write(dept_dir / "_department.yaml", _department_yaml())

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        dept = reg.get_department("marketing")
        assert dept is not None
        assert dept.name == "Marketing Department"

        assert reg.get_department("nonexistent") is None

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_list_departments(self, tmp_path: Path):
        from src.skills.registry import SkillRegistry

        for name in ("alpha", "beta"):
            d = tmp_path / name
            d.mkdir()
            _write(
                d / "_department.yaml",
                _department_yaml(
                    dept_id=name,
                    name=name.title(),
                ),
            )

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        depts = reg.list_departments()
        assert len(depts) == 2
        assert depts[0].id == "alpha"
        assert depts[1].id == "beta"

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_get_role(self, tmp_path: Path):
        from src.skills.registry import SkillRegistry

        dept_dir = tmp_path / "marketing"
        dept_dir.mkdir()
        _write(dept_dir / "_department.yaml", _department_yaml())

        role_dir = dept_dir / "buyer"
        role_dir.mkdir()
        _write(
            role_dir / "_role.yaml",
            _role_yaml(
                role_id="buyer",
                briefing_skills=["s"],
            ),
        )
        _write(role_dir / "s.yaml", _skill_yaml(skill_id="s"))

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        role = reg.get_role("buyer")
        assert role is not None
        assert role.name == "Media Buyer"

        assert reg.get_role("nonexistent") is None

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_list_roles_all(self, tmp_path: Path):
        from src.skills.registry import SkillRegistry

        dept_dir = tmp_path / "marketing"
        dept_dir.mkdir()
        _write(dept_dir / "_department.yaml", _department_yaml())

        for rid in ("alpha", "beta"):
            r = dept_dir / rid
            r.mkdir()
            _write(
                r / "_role.yaml",
                _role_yaml(
                    role_id=rid,
                    briefing_skills=["s"],
                ),
            )

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        all_roles = reg.list_roles()
        assert len(all_roles) == 2

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_list_roles_filtered(self, tmp_path: Path):
        from src.skills.registry import SkillRegistry

        # Dept 1
        d1 = tmp_path / "marketing"
        d1.mkdir()
        _write(
            d1 / "_department.yaml",
            _department_yaml(
                dept_id="marketing",
            ),
        )
        r1 = d1 / "buyer"
        r1.mkdir()
        _write(
            r1 / "_role.yaml",
            _role_yaml(
                role_id="buyer",
                department_id="marketing",
                briefing_skills=["s"],
            ),
        )

        # Dept 2
        d2 = tmp_path / "ops"
        d2.mkdir()
        _write(
            d2 / "_department.yaml",
            _department_yaml(
                dept_id="ops",
                name="Operations",
            ),
        )
        r2 = d2 / "support"
        r2.mkdir()
        _write(
            r2 / "_role.yaml",
            _role_yaml(
                role_id="support",
                department_id="ops",
                briefing_skills=["s"],
            ),
        )

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        mktg_roles = reg.list_roles("marketing")
        assert len(mktg_roles) == 1
        assert mktg_roles[0].id == "buyer"

        ops_roles = reg.list_roles("ops")
        assert len(ops_roles) == 1
        assert ops_roles[0].id == "support"

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_list_skills_for_role(self, tmp_path: Path):
        from src.skills.registry import SkillRegistry

        dept_dir = tmp_path / "marketing"
        dept_dir.mkdir()
        _write(dept_dir / "_department.yaml", _department_yaml())

        role_dir = dept_dir / "buyer"
        role_dir.mkdir()
        _write(
            role_dir / "_role.yaml",
            _role_yaml(
                role_id="buyer",
                briefing_skills=["s1", "s2"],
            ),
        )
        _write(
            role_dir / "s1.yaml",
            _skill_yaml(
                skill_id="s1",
                name="S1",
            ),
        )
        _write(
            role_dir / "s2.yaml",
            _skill_yaml(
                skill_id="s2",
                name="S2",
            ),
        )

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        skills = reg.list_skills_for_role("buyer")
        assert len(skills) == 2
        assert {s.id for s in skills} == {"s1", "s2"}

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_list_skills_for_department(self, tmp_path: Path):
        from src.skills.registry import SkillRegistry

        dept_dir = tmp_path / "marketing"
        dept_dir.mkdir()
        _write(dept_dir / "_department.yaml", _department_yaml())

        # Role with skills
        role_dir = dept_dir / "buyer"
        role_dir.mkdir()
        _write(
            role_dir / "_role.yaml",
            _role_yaml(
                role_id="buyer",
                briefing_skills=["s1"],
            ),
        )
        _write(
            role_dir / "s1.yaml",
            _skill_yaml(
                skill_id="s1",
                name="S1",
            ),
        )

        # Loose skill in dept
        _write(
            dept_dir / "loose.yaml",
            _skill_yaml(
                skill_id="loose_dept",
                name="Loose in Dept",
            ),
        )

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        dept_skills = reg.list_skills_for_department("marketing")
        assert len(dept_skills) == 2
        assert {s.id for s in dept_skills} == {"s1", "loose_dept"}

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_repr_includes_counts(self, tmp_path: Path):
        from src.skills.registry import SkillRegistry

        dept_dir = tmp_path / "marketing"
        dept_dir.mkdir()
        _write(dept_dir / "_department.yaml", _department_yaml())

        role_dir = dept_dir / "buyer"
        role_dir.mkdir()
        _write(
            role_dir / "_role.yaml",
            _role_yaml(
                role_id="buyer",
                briefing_skills=["s1"],
            ),
        )
        _write(role_dir / "s1.yaml", _skill_yaml(skill_id="s1"))

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        r = repr(reg)
        assert "departments=1" in r
        assert "roles=1" in r
        assert "skills=1" in r

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    def test_reload_clears_hierarchy(self, tmp_path: Path):
        """Reload clears departments and roles too."""
        from src.skills.registry import SkillRegistry

        dept_dir = tmp_path / "marketing"
        dept_dir.mkdir()
        _write(dept_dir / "_department.yaml", _department_yaml())

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()
        assert reg.department_count == 1

        # Remove the department file
        (dept_dir / "_department.yaml").unlink()
        reg.reload()
        assert reg.department_count == 0
