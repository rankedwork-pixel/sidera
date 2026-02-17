"""Tests for head_of_marketing example YAML files.

Validates that the head_of_marketing role (a manager role) and its
executive_summary skill load correctly from disk and pass all validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.skills.schema import (
    load_role_from_yaml,
    load_skill_from_yaml,
    validate_role,
    validate_skill,
)

MARKETING_DIR = Path(__file__).parent.parent.parent / "src" / "skills" / "library" / "marketing"
HOM_DIR = MARKETING_DIR / "head_of_marketing"


# ===========================================================================
# 1. Role YAML loading
# ===========================================================================


class TestHeadOfMarketingRoleLoading:
    """Verify the _role.yaml loads and parses correctly."""

    def test_role_yaml_exists(self) -> None:
        assert (HOM_DIR / "_role.yaml").exists(), f"Expected _role.yaml at {HOM_DIR / '_role.yaml'}"

    def test_role_loads_without_error(self) -> None:
        role = load_role_from_yaml(HOM_DIR / "_role.yaml")
        assert role is not None

    def test_role_id(self) -> None:
        role = load_role_from_yaml(HOM_DIR / "_role.yaml")
        assert role.id == "head_of_marketing"

    def test_role_name(self) -> None:
        role = load_role_from_yaml(HOM_DIR / "_role.yaml")
        assert role.name == "Head of Marketing"

    def test_role_department_id(self) -> None:
        role = load_role_from_yaml(HOM_DIR / "_role.yaml")
        assert role.department_id == "marketing"

    def test_role_description_nonempty(self) -> None:
        role = load_role_from_yaml(HOM_DIR / "_role.yaml")
        assert len(role.description) > 0

    def test_role_persona_nonempty(self) -> None:
        role = load_role_from_yaml(HOM_DIR / "_role.yaml")
        assert len(role.persona) > 0

    def test_role_schedule(self) -> None:
        role = load_role_from_yaml(HOM_DIR / "_role.yaml")
        assert role.schedule == "0 9 * * 1-5"


# ===========================================================================
# 2. Manager fields
# ===========================================================================


class TestHeadOfMarketingManagerFields:
    """Verify the manages, delegation_model, and synthesis_prompt fields."""

    def test_manages_contains_three_roles(self) -> None:
        role = load_role_from_yaml(HOM_DIR / "_role.yaml")
        assert len(role.manages) == 3

    def test_manages_contains_performance_media_buyer(self) -> None:
        role = load_role_from_yaml(HOM_DIR / "_role.yaml")
        assert "performance_media_buyer" in role.manages

    def test_manages_contains_reporting_analyst(self) -> None:
        role = load_role_from_yaml(HOM_DIR / "_role.yaml")
        assert "reporting_analyst" in role.manages

    def test_manages_contains_strategist(self) -> None:
        role = load_role_from_yaml(HOM_DIR / "_role.yaml")
        assert "strategist" in role.manages

    def test_manages_exact_ids(self) -> None:
        role = load_role_from_yaml(HOM_DIR / "_role.yaml")
        assert role.manages == (
            "performance_media_buyer",
            "reporting_analyst",
            "strategist",
        )

    def test_delegation_model_is_standard(self) -> None:
        role = load_role_from_yaml(HOM_DIR / "_role.yaml")
        assert role.delegation_model == "standard"

    def test_synthesis_prompt_is_nonempty(self) -> None:
        role = load_role_from_yaml(HOM_DIR / "_role.yaml")
        assert len(role.synthesis_prompt.strip()) > 0

    def test_synthesis_prompt_mentions_cross_channel(self) -> None:
        role = load_role_from_yaml(HOM_DIR / "_role.yaml")
        assert "cross-channel" in role.synthesis_prompt.lower()


# ===========================================================================
# 3. Role connectors and briefing_skills
# ===========================================================================


class TestHeadOfMarketingConnectors:
    """Verify connectors and briefing_skills are set correctly."""

    def test_connectors(self) -> None:
        role = load_role_from_yaml(HOM_DIR / "_role.yaml")
        assert role.connectors == ("google_ads", "meta", "bigquery")

    def test_briefing_skills(self) -> None:
        role = load_role_from_yaml(HOM_DIR / "_role.yaml")
        assert role.briefing_skills == ("executive_summary",)


# ===========================================================================
# 4. Role validation
# ===========================================================================


class TestHeadOfMarketingValidation:
    """Verify validate_role returns no errors for head_of_marketing."""

    def test_validates_without_errors(self) -> None:
        role = load_role_from_yaml(HOM_DIR / "_role.yaml")
        errors = validate_role(role)
        assert errors == [], f"Validation errors: {errors}"


# ===========================================================================
# 5. Executive summary skill loading
# ===========================================================================


class TestExecutiveSummarySkillLoading:
    """Verify the executive_summary.yaml skill loads correctly."""

    def test_skill_yaml_exists(self) -> None:
        assert (HOM_DIR / "executive_summary.yaml").exists(), (
            f"Expected executive_summary.yaml at {HOM_DIR}"
        )

    def test_skill_loads_without_error(self) -> None:
        skill = load_skill_from_yaml(HOM_DIR / "executive_summary.yaml")
        assert skill is not None

    def test_skill_id(self) -> None:
        skill = load_skill_from_yaml(HOM_DIR / "executive_summary.yaml")
        assert skill.id == "executive_summary"

    def test_skill_name(self) -> None:
        skill = load_skill_from_yaml(HOM_DIR / "executive_summary.yaml")
        assert skill.name == "Executive Summary"

    def test_skill_version(self) -> None:
        skill = load_skill_from_yaml(HOM_DIR / "executive_summary.yaml")
        assert skill.version == "1.0"

    def test_skill_category(self) -> None:
        skill = load_skill_from_yaml(HOM_DIR / "executive_summary.yaml")
        assert skill.category == "analysis"

    def test_skill_model(self) -> None:
        skill = load_skill_from_yaml(HOM_DIR / "executive_summary.yaml")
        assert skill.model == "haiku"

    def test_skill_max_turns(self) -> None:
        skill = load_skill_from_yaml(HOM_DIR / "executive_summary.yaml")
        assert skill.max_turns == 10

    def test_skill_requires_approval_false(self) -> None:
        skill = load_skill_from_yaml(HOM_DIR / "executive_summary.yaml")
        assert skill.requires_approval is False


# ===========================================================================
# 6. Executive summary tools_required
# ===========================================================================


EXPECTED_TOOLS = (
    "get_google_ads_campaigns",
    "get_google_ads_performance",
    "get_meta_campaigns",
    "get_meta_performance",
    "get_business_goals",
    "get_budget_pacing",
)


class TestExecutiveSummaryTools:
    """Verify tools_required contains exactly the expected tools."""

    def test_tools_required_count(self) -> None:
        skill = load_skill_from_yaml(HOM_DIR / "executive_summary.yaml")
        assert len(skill.tools_required) == 6

    def test_tools_required_exact(self) -> None:
        skill = load_skill_from_yaml(HOM_DIR / "executive_summary.yaml")
        assert skill.tools_required == EXPECTED_TOOLS

    @pytest.mark.parametrize("tool", EXPECTED_TOOLS)
    def test_individual_tool_present(self, tool: str) -> None:
        skill = load_skill_from_yaml(HOM_DIR / "executive_summary.yaml")
        assert tool in skill.tools_required


# ===========================================================================
# 7. Executive summary platforms and tags
# ===========================================================================


class TestExecutiveSummaryPlatformsAndTags:
    """Verify platforms and tags are set correctly."""

    def test_platforms(self) -> None:
        skill = load_skill_from_yaml(HOM_DIR / "executive_summary.yaml")
        assert skill.platforms == ("google_ads", "meta", "bigquery")

    def test_tags_include_executive(self) -> None:
        skill = load_skill_from_yaml(HOM_DIR / "executive_summary.yaml")
        assert "executive" in skill.tags

    def test_tags_include_manager(self) -> None:
        skill = load_skill_from_yaml(HOM_DIR / "executive_summary.yaml")
        assert "manager" in skill.tags

    def test_tags_include_cross_channel(self) -> None:
        skill = load_skill_from_yaml(HOM_DIR / "executive_summary.yaml")
        assert "cross-channel" in skill.tags


# ===========================================================================
# 8. Executive summary skill validation
# ===========================================================================


class TestExecutiveSummaryValidation:
    """Verify validate_skill returns no errors for executive_summary."""

    def test_validates_without_errors(self) -> None:
        skill = load_skill_from_yaml(HOM_DIR / "executive_summary.yaml")
        errors = validate_skill(skill)
        assert errors == [], f"Validation errors: {errors}"

    def test_system_supplement_nonempty(self) -> None:
        skill = load_skill_from_yaml(HOM_DIR / "executive_summary.yaml")
        assert len(skill.system_supplement.strip()) > 0

    def test_prompt_template_nonempty(self) -> None:
        skill = load_skill_from_yaml(HOM_DIR / "executive_summary.yaml")
        assert len(skill.prompt_template.strip()) > 0

    def test_output_format_nonempty(self) -> None:
        skill = load_skill_from_yaml(HOM_DIR / "executive_summary.yaml")
        assert len(skill.output_format.strip()) > 0

    def test_business_guidance_nonempty(self) -> None:
        skill = load_skill_from_yaml(HOM_DIR / "executive_summary.yaml")
        assert len(skill.business_guidance.strip()) > 0
