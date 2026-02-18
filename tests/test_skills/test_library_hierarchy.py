"""Integration tests for the reorganized skill library on disk.

Validates that the three-level hierarchy (department -> role -> skill)
is properly structured and that the SkillRegistry discovers all entities
correctly from the real YAML files.

These tests load actual files from ``src/skills/library/`` -- no mocking.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.skills.registry import SkillRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

LIBRARY_DIR = Path(__file__).parent.parent.parent / "src" / "skills" / "library"


@pytest.fixture(scope="module")
def registry() -> SkillRegistry:
    """Load the real skill library once for the entire module."""
    reg = SkillRegistry(skills_dir=LIBRARY_DIR)
    count = reg.load_all()
    assert count > 0, "Registry loaded zero skills -- library may be missing"
    return reg


# ===========================================================================
# 1. Top-level counts
# ===========================================================================


class TestTopLevelCounts:
    """Verify the registry discovers exactly 3 departments, 6 roles, 10 skills."""

    def test_department_count(self, registry: SkillRegistry) -> None:
        assert registry.department_count == 3, (
            f"Expected 3 departments, got {registry.department_count}. "
            f"Loaded: {[d.id for d in registry.list_departments()]}"
        )

    def test_role_count(self, registry: SkillRegistry) -> None:
        assert registry.role_count == 6, (
            f"Expected 6 roles, got {registry.role_count}. "
            f"Loaded: {[r.id for r in registry.list_roles()]}"
        )

    def test_skill_count(self, registry: SkillRegistry) -> None:
        assert registry.count == 10, (
            f"Expected 10 skills, got {registry.count}. "
            f"Loaded: {sorted(s.id for s in registry.list_all())}"
        )


# ===========================================================================
# 2. Department definition
# ===========================================================================


class TestMarketingDepartment:
    """Verify the 'marketing' department has the right name and description."""

    def test_department_exists(self, registry: SkillRegistry) -> None:
        dept = registry.get_department("marketing")
        assert dept is not None, "Department 'marketing' not found"

    def test_department_name(self, registry: SkillRegistry) -> None:
        dept = registry.get_department("marketing")
        assert dept.name == "Marketing Department"

    def test_department_description(self, registry: SkillRegistry) -> None:
        dept = registry.get_department("marketing")
        assert dept.description == ("Manages all paid acquisition, brand marketing, and reporting")


# ===========================================================================
# 3. Role definitions -- department_id
# ===========================================================================


EXPECTED_ROLE_IDS = sorted(
    [
        "ceo",
        "head_of_marketing",
        "performance_media_buyer",
        "reporting_analyst",
        "strategist",
        "head_of_it",
    ]
)


EXPECTED_ROLE_DEPTS: dict[str, str] = {
    "ceo": "executive",
    "head_of_marketing": "marketing",
    "performance_media_buyer": "marketing",
    "reporting_analyst": "marketing",
    "strategist": "marketing",
    "head_of_it": "it",
}

EXPECTED_MARKETING_ROLE_IDS = sorted(
    [
        "head_of_marketing",
        "performance_media_buyer",
        "reporting_analyst",
        "strategist",
    ]
)


class TestRoleDepartmentAssignment:
    """Every role belongs to its expected department."""

    def test_all_expected_roles_present(self, registry: SkillRegistry) -> None:
        loaded_ids = sorted(r.id for r in registry.list_roles())
        assert loaded_ids == EXPECTED_ROLE_IDS, (
            f"Missing: {set(EXPECTED_ROLE_IDS) - set(loaded_ids)}, "
            f"Extra: {set(loaded_ids) - set(EXPECTED_ROLE_IDS)}"
        )

    @pytest.mark.parametrize("role_id", EXPECTED_ROLE_IDS)
    def test_role_department_id(
        self,
        registry: SkillRegistry,
        role_id: str,
    ) -> None:
        role = registry.get_role(role_id)
        assert role is not None, f"Role '{role_id}' not found"
        expected_dept = EXPECTED_ROLE_DEPTS[role_id]
        assert role.department_id == expected_dept, (
            f"Role '{role_id}' has department_id='{role.department_id}', expected '{expected_dept}'"
        )

    def test_list_roles_filtered_by_marketing(
        self,
        registry: SkillRegistry,
    ) -> None:
        roles = registry.list_roles("marketing")
        assert len(roles) == 4

    def test_list_roles_filtered_by_it(
        self,
        registry: SkillRegistry,
    ) -> None:
        roles = registry.list_roles("it")
        assert len(roles) == 1
        assert roles[0].id == "head_of_it"

    def test_list_roles_filtered_by_executive(
        self,
        registry: SkillRegistry,
    ) -> None:
        roles = registry.list_roles("executive")
        assert len(roles) == 1
        assert roles[0].id == "ceo"


# ===========================================================================
# 4. Role briefing_skills lists
# ===========================================================================


class TestRoleBriefingSkills:
    """Verify each role declares the correct briefing_skills."""

    # performance_media_buyer: 3 briefing skills
    EXPECTED_PMB_BRIEFING = sorted(
        [
            "anomaly_detector",
            "creative_analysis",
            "fb_creative_cuts",
        ]
    )

    # reporting_analyst: 1 briefing skill
    EXPECTED_RA_BRIEFING = sorted(
        [
            "weekly_report",
        ]
    )

    # ceo: 1 briefing skill
    EXPECTED_CEO_BRIEFING = sorted(
        [
            "org_health_check",
        ]
    )

    # head_of_marketing: 1 briefing skill
    EXPECTED_HOM_BRIEFING = sorted(
        [
            "executive_summary",
        ]
    )

    # strategist: 1 briefing skill
    EXPECTED_STRAT_BRIEFING = sorted(
        [
            "competitor_benchmark",
        ]
    )

    def test_ceo_count(
        self,
        registry: SkillRegistry,
    ) -> None:
        role = registry.get_role("ceo")
        assert len(role.briefing_skills) == 1, (
            f"Expected 1 briefing_skills, got {len(role.briefing_skills)}: "
            f"{list(role.briefing_skills)}"
        )

    def test_ceo_skills(
        self,
        registry: SkillRegistry,
    ) -> None:
        role = registry.get_role("ceo")
        assert sorted(role.briefing_skills) == self.EXPECTED_CEO_BRIEFING

    def test_head_of_marketing_count(
        self,
        registry: SkillRegistry,
    ) -> None:
        role = registry.get_role("head_of_marketing")
        assert len(role.briefing_skills) == 1, (
            f"Expected 1 briefing_skills, got {len(role.briefing_skills)}: "
            f"{list(role.briefing_skills)}"
        )

    def test_head_of_marketing_skills(
        self,
        registry: SkillRegistry,
    ) -> None:
        role = registry.get_role("head_of_marketing")
        assert sorted(role.briefing_skills) == self.EXPECTED_HOM_BRIEFING

    def test_performance_media_buyer_count(
        self,
        registry: SkillRegistry,
    ) -> None:
        role = registry.get_role("performance_media_buyer")
        assert len(role.briefing_skills) == 3, (
            f"Expected 3 briefing_skills, got {len(role.briefing_skills)}: "
            f"{list(role.briefing_skills)}"
        )

    def test_performance_media_buyer_skills(
        self,
        registry: SkillRegistry,
    ) -> None:
        role = registry.get_role("performance_media_buyer")
        assert sorted(role.briefing_skills) == self.EXPECTED_PMB_BRIEFING

    def test_reporting_analyst_count(
        self,
        registry: SkillRegistry,
    ) -> None:
        role = registry.get_role("reporting_analyst")
        assert len(role.briefing_skills) == 1, (
            f"Expected 1 briefing_skills, got {len(role.briefing_skills)}: "
            f"{list(role.briefing_skills)}"
        )

    def test_reporting_analyst_skills(
        self,
        registry: SkillRegistry,
    ) -> None:
        role = registry.get_role("reporting_analyst")
        assert sorted(role.briefing_skills) == self.EXPECTED_RA_BRIEFING

    def test_strategist_count(
        self,
        registry: SkillRegistry,
    ) -> None:
        role = registry.get_role("strategist")
        assert len(role.briefing_skills) == 1, (
            f"Expected 1 briefing_skills, got {len(role.briefing_skills)}: "
            f"{list(role.briefing_skills)}"
        )

    def test_strategist_skills(
        self,
        registry: SkillRegistry,
    ) -> None:
        role = registry.get_role("strategist")
        assert sorted(role.briefing_skills) == self.EXPECTED_STRAT_BRIEFING


# ===========================================================================
# 5. Skill hierarchy wiring (department_id + role_id)
# ===========================================================================


# Expected mapping: skill_id -> (department_id, role_id)
SKILL_HIERARCHY = {
    # ceo (1 skill)
    "org_health_check": ("executive", "ceo"),
    # head_of_marketing (1 skill)
    "executive_summary": ("marketing", "head_of_marketing"),
    # performance_media_buyer (3 skills)
    "anomaly_detector": ("marketing", "performance_media_buyer"),
    "creative_analysis": ("marketing", "performance_media_buyer"),
    "fb_creative_cuts": ("marketing", "performance_media_buyer"),
    # reporting_analyst (1 skill)
    "weekly_report": ("marketing", "reporting_analyst"),
    # strategist (1 skill)
    "competitor_benchmark": ("marketing", "strategist"),
}


class TestSkillHierarchyWiring:
    """Every skill has the correct department_id and role_id set."""

    @pytest.mark.parametrize(
        "skill_id",
        sorted(SKILL_HIERARCHY.keys()),
    )
    def test_skill_department_id(
        self,
        registry: SkillRegistry,
        skill_id: str,
    ) -> None:
        skill = registry.get(skill_id)
        assert skill is not None, f"Skill '{skill_id}' not found"
        expected_dept = SKILL_HIERARCHY[skill_id][0]
        assert skill.department_id == expected_dept, (
            f"Skill '{skill_id}' has department_id='{skill.department_id}', "
            f"expected '{expected_dept}'"
        )

    @pytest.mark.parametrize(
        "skill_id",
        sorted(SKILL_HIERARCHY.keys()),
    )
    def test_skill_role_id(
        self,
        registry: SkillRegistry,
        skill_id: str,
    ) -> None:
        skill = registry.get(skill_id)
        assert skill is not None, f"Skill '{skill_id}' not found"
        expected_role = SKILL_HIERARCHY[skill_id][1]
        assert skill.role_id == expected_role, (
            f"Skill '{skill_id}' has role_id='{skill.role_id}', expected '{expected_role}'"
        )


class TestSkillsPerRole:
    """Verify list_skills_for_role returns the right sets."""

    def test_ceo_skills(
        self,
        registry: SkillRegistry,
    ) -> None:
        skills = registry.list_skills_for_role("ceo")
        ids = sorted(s.id for s in skills)
        expected = sorted(k for k, v in SKILL_HIERARCHY.items() if v[1] == "ceo")
        assert ids == expected, (
            f"Missing: {set(expected) - set(ids)}, Extra: {set(ids) - set(expected)}"
        )
        assert len(skills) == 1

    def test_head_of_marketing_skills(
        self,
        registry: SkillRegistry,
    ) -> None:
        skills = registry.list_skills_for_role("head_of_marketing")
        ids = sorted(s.id for s in skills)
        expected = sorted(k for k, v in SKILL_HIERARCHY.items() if v[1] == "head_of_marketing")
        assert ids == expected, (
            f"Missing: {set(expected) - set(ids)}, Extra: {set(ids) - set(expected)}"
        )
        assert len(skills) == 1

    def test_performance_media_buyer_skills(
        self,
        registry: SkillRegistry,
    ) -> None:
        skills = registry.list_skills_for_role("performance_media_buyer")
        ids = sorted(s.id for s in skills)
        expected = sorted(
            k for k, v in SKILL_HIERARCHY.items() if v[1] == "performance_media_buyer"
        )
        assert ids == expected, (
            f"Missing: {set(expected) - set(ids)}, Extra: {set(ids) - set(expected)}"
        )
        assert len(skills) == 3

    def test_reporting_analyst_skills(
        self,
        registry: SkillRegistry,
    ) -> None:
        skills = registry.list_skills_for_role("reporting_analyst")
        ids = sorted(s.id for s in skills)
        expected = sorted(k for k, v in SKILL_HIERARCHY.items() if v[1] == "reporting_analyst")
        assert ids == expected
        assert len(skills) == 1

    def test_strategist_skills(
        self,
        registry: SkillRegistry,
    ) -> None:
        skills = registry.list_skills_for_role("strategist")
        ids = sorted(s.id for s in skills)
        expected = sorted(k for k, v in SKILL_HIERARCHY.items() if v[1] == "strategist")
        assert ids == expected
        assert len(skills) == 1

    def test_all_skills_belong_to_marketing_department(
        self,
        registry: SkillRegistry,
    ) -> None:
        dept_skills = registry.list_skills_for_department("marketing")
        assert len(dept_skills) == 6, (
            f"Expected 6 skills in marketing department, got {len(dept_skills)}"
        )

    def test_all_skills_belong_to_executive_department(
        self,
        registry: SkillRegistry,
    ) -> None:
        dept_skills = registry.list_skills_for_department("executive")
        assert len(dept_skills) == 1, (
            f"Expected 1 skill in executive department, got {len(dept_skills)}"
        )


# ===========================================================================
# 6. Folder-based skill: creative_analysis
# ===========================================================================


class TestCreativeAnalysisFolderSkill:
    """Verify creative_analysis is recognized as folder-based with context files."""

    def test_creative_analysis_exists(
        self,
        registry: SkillRegistry,
    ) -> None:
        skill = registry.get("creative_analysis")
        assert skill is not None

    def test_has_context_files(
        self,
        registry: SkillRegistry,
    ) -> None:
        skill = registry.get("creative_analysis")
        assert len(skill.context_files) > 0, (
            "creative_analysis should have context_files defined (folder-based skill)"
        )

    def test_context_files_patterns(
        self,
        registry: SkillRegistry,
    ) -> None:
        skill = registry.get("creative_analysis")
        patterns = list(skill.context_files)
        assert "examples/*.md" in patterns
        assert "context/*.md" in patterns
        assert "guidelines/*.md" in patterns

    def test_has_source_dir(
        self,
        registry: SkillRegistry,
    ) -> None:
        skill = registry.get("creative_analysis")
        assert skill.source_dir, (
            "creative_analysis should have a source_dir set for context file resolution"
        )

    def test_source_dir_exists_on_disk(
        self,
        registry: SkillRegistry,
    ) -> None:
        skill = registry.get("creative_analysis")
        assert Path(skill.source_dir).is_dir(), (
            f"source_dir '{skill.source_dir}' does not exist on disk"
        )

    def test_context_subdirs_exist(
        self,
        registry: SkillRegistry,
    ) -> None:
        """The context/, examples/, guidelines/ subdirectories exist."""
        skill = registry.get("creative_analysis")
        source = Path(skill.source_dir)
        for subdir in ("context", "examples", "guidelines"):
            assert (source / subdir).is_dir(), (
                f"Expected subdirectory '{subdir}' in {skill.source_dir}"
            )


# ===========================================================================
# 7. No loose (unassigned) skills
# ===========================================================================


class TestNoLooseSkills:
    """Every skill in the library is assigned to a department and role."""

    def test_no_skills_without_department(
        self,
        registry: SkillRegistry,
    ) -> None:
        loose = [s.id for s in registry.list_all() if not s.department_id]
        assert loose == [], f"Found skills without department_id: {loose}"

    def test_no_skills_without_role(
        self,
        registry: SkillRegistry,
    ) -> None:
        loose = [s.id for s in registry.list_all() if not s.role_id]
        assert loose == [], f"Found skills without role_id: {loose}"

    def test_no_top_level_yaml_files(self) -> None:
        """No loose .yaml skill files directly in the library root."""
        top_level_yaml = [
            p.name for p in LIBRARY_DIR.glob("*.yaml") if not p.name.startswith("_")
        ] + [p.name for p in LIBRARY_DIR.glob("*.yml") if not p.name.startswith("_")]
        assert top_level_yaml == [], (
            f"Found loose YAML files at library root: {top_level_yaml}. "
            "All skills should be inside a department/role directory."
        )

    def test_no_top_level_skill_folders(self) -> None:
        """No loose folder-based skills directly in the library root."""
        loose_folders = [
            d.name for d in LIBRARY_DIR.iterdir() if d.is_dir() and (d / "skill.yaml").exists()
        ]
        assert loose_folders == [], (
            f"Found loose skill folders at library root: {loose_folders}. "
            "All skills should be inside a department/role directory."
        )


# ===========================================================================
# 8. Executive department & CEO role
# ===========================================================================


class TestExecutiveDepartment:
    """Verify the 'executive' department is correctly loaded."""

    def test_department_exists(self, registry: SkillRegistry) -> None:
        dept = registry.get_department("executive")
        assert dept is not None, "Department 'executive' not found"

    def test_department_name(self, registry: SkillRegistry) -> None:
        dept = registry.get_department("executive")
        assert dept.name == "Executive Leadership"


class TestCEORole:
    """Verify the CEO role has the right configuration as top-level manager."""

    def test_role_exists(self, registry: SkillRegistry) -> None:
        role = registry.get_role("ceo")
        assert role is not None, "Role 'ceo' not found"

    def test_role_name(self, registry: SkillRegistry) -> None:
        role = registry.get_role("ceo")
        assert role.name == "Chief Executive Officer"

    def test_role_department(self, registry: SkillRegistry) -> None:
        role = registry.get_role("ceo")
        assert role.department_id == "executive"

    def test_manages_department_heads(self, registry: SkillRegistry) -> None:
        role = registry.get_role("ceo")
        manages = sorted(role.manages)
        assert manages == ["head_of_it", "head_of_marketing"], (
            f"CEO should manage head_of_marketing and head_of_it, got {manages}"
        )

    def test_is_manager(self, registry: SkillRegistry) -> None:
        role = registry.get_role("ceo")
        assert len(role.manages) > 0, "CEO must be a manager role"

    def test_heartbeat_model_is_opus(self, registry: SkillRegistry) -> None:
        role = registry.get_role("ceo")
        assert role.heartbeat_model == "opus", (
            f"CEO heartbeat_model should be 'opus', got '{role.heartbeat_model}'"
        )

    def test_has_heartbeat_schedule(self, registry: SkillRegistry) -> None:
        role = registry.get_role("ceo")
        assert role.heartbeat_schedule is not None, "CEO must have a heartbeat_schedule"

    def test_has_principles(self, registry: SkillRegistry) -> None:
        role = registry.get_role("ceo")
        assert len(role.principles) >= 5, (
            f"CEO should have at least 5 principles, got {len(role.principles)}"
        )

    def test_has_goals(self, registry: SkillRegistry) -> None:
        role = registry.get_role("ceo")
        assert len(role.goals) >= 4, f"CEO should have at least 4 goals, got {len(role.goals)}"

    def test_clearance_level(self, registry: SkillRegistry) -> None:
        role = registry.get_role("ceo")
        assert role.clearance_level == "restricted"

    def test_learning_channels(self, registry: SkillRegistry) -> None:
        role = registry.get_role("ceo")
        channels = sorted(role.learning_channels)
        assert channels == ["head_of_it", "head_of_marketing"], (
            f"CEO should learn from both department heads, got {channels}"
        )

    def test_connectors(self, registry: SkillRegistry) -> None:
        role = registry.get_role("ceo")
        connectors = sorted(role.connectors)
        assert connectors == ["bigquery", "google_ads", "meta"]

    def test_briefing_skill_is_org_health_check(self, registry: SkillRegistry) -> None:
        role = registry.get_role("ceo")
        assert list(role.briefing_skills) == ["org_health_check"]

    def test_has_synthesis_prompt(self, registry: SkillRegistry) -> None:
        role = registry.get_role("ceo")
        assert role.synthesis_prompt, "CEO should have a synthesis_prompt"
        assert "cross-department" in role.synthesis_prompt.lower()


class TestCEOSkills:
    """Verify CEO skills are correctly configured."""

    def test_org_health_check_model(self, registry: SkillRegistry) -> None:
        skill = registry.get("org_health_check")
        assert skill is not None, "Skill 'org_health_check' not found"
        assert skill.model == "sonnet"

    def test_org_health_check_has_system_tools(self, registry: SkillRegistry) -> None:
        skill = registry.get("org_health_check")
        tools = set(skill.tools_required)
        assert "get_system_health" in tools
        assert "get_failed_runs" in tools
        assert "get_approval_queue_status" in tools
        assert "get_cost_summary" in tools

    def test_org_health_check_no_approval_required(self, registry: SkillRegistry) -> None:
        skill = registry.get("org_health_check")
        assert not skill.requires_approval, (
            "CEO skill 'org_health_check' should not require approval"
        )


class TestCEOContextFiles:
    """Verify CEO context files exist on disk."""

    def test_context_directory_exists(self) -> None:
        ctx_dir = LIBRARY_DIR / "executive" / "ceo" / "context"
        assert ctx_dir.is_dir(), f"CEO context directory not found at {ctx_dir}"

    def test_operational_playbook_exists(self) -> None:
        playbook = LIBRARY_DIR / "executive" / "ceo" / "context" / "operational_playbook.md"
        assert playbook.is_file(), f"Operational playbook not found at {playbook}"

    def test_cross_department_guidelines_exists(self) -> None:
        guidelines = (
            LIBRARY_DIR / "executive" / "ceo" / "context" / "cross_department_guidelines.md"
        )
        assert guidelines.is_file(), f"Cross-department guidelines not found at {guidelines}"
