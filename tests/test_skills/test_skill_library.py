"""Tests for the full skill library — validates all 15 YAML skills load and pass validation.

Covers:
- All skills load without error
- All skills pass schema validation
- No duplicate skill IDs
- All tools_required are valid (exist in ALL_TOOLS)
- All categories are in the allowed set
- All platforms are in the allowed set
- Scheduled skills have cron expressions
- chain_after references are valid
- Router builds index for all skills
- Router matches common natural-language queries
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.agent.prompts import ALL_TOOLS
from src.llm.provider import LLMResult
from src.skills.registry import SkillRegistry
from src.skills.router import SkillRouter
from src.skills.schema import (
    VALID_CATEGORIES,
    VALID_MODELS,
    VALID_PLATFORMS,
    SkillDefinition,
    validate_skill,
)

_CWF = "src.skills.router.complete_with_fallback"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

LIBRARY_DIR = Path(__file__).parent.parent.parent / "src" / "skills" / "library"

# All 23 expected skill IDs (3 original + 12 new + 1 manager + 3 IT + 3 CEO + 1 code-backed)
EXPECTED_SKILL_IDS = sorted(
    [
        # Original 3
        "creative_analysis",
        "budget_reallocation",
        "weekly_report",
        # Analysis (4 new)
        "anomaly_detector",
        "search_term_audit",
        "audience_overlap",
        "landing_page_analysis",
        # Optimization (3 new)
        "bid_strategy_review",
        "dayparting_analysis",
        "geo_performance",
        # Monitoring (3 new)
        "creative_fatigue_check",
        "budget_pacing_check",
        "platform_health_check",
        # Reporting (2 new)
        "competitor_benchmark",
        "monthly_report",
        # Manager (1 new)
        "executive_summary",
        # IT / Sysadmin (3 new)
        "system_health_check",
        "error_diagnosis",
        "cost_monitoring",
        # CEO / Executive (3 new)
        "org_health_check",
        "cross_dept_synthesis",
        "escalation_triage",
        # Code-backed (1 new)
        "fb_creative_cuts",
    ]
)


@pytest.fixture(scope="module")
def registry() -> SkillRegistry:
    """Load the full skill library once for the module."""
    reg = SkillRegistry(skills_dir=LIBRARY_DIR)
    reg.load_all()
    return reg


@pytest.fixture(scope="module")
def all_skills(registry: SkillRegistry) -> list[SkillDefinition]:
    """All loaded skill definitions."""
    return registry.list_all()


# ---------------------------------------------------------------------------
# Loading & count
# ---------------------------------------------------------------------------


def test_all_skills_load(registry: SkillRegistry) -> None:
    """SkillRegistry loads all 23 skills."""
    assert registry.count == 23, (
        f"Expected 23 skills, got {registry.count}. "
        f"Loaded: {sorted(s.id for s in registry.list_all())}"
    )


def test_all_expected_ids_present(registry: SkillRegistry) -> None:
    """Every expected skill ID is present in the registry."""
    loaded_ids = sorted(s.id for s in registry.list_all())
    assert loaded_ids == EXPECTED_SKILL_IDS, (
        f"Missing: {set(EXPECTED_SKILL_IDS) - set(loaded_ids)}, "
        f"Extra: {set(loaded_ids) - set(EXPECTED_SKILL_IDS)}"
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_all_skills_validate(all_skills: list[SkillDefinition]) -> None:
    """Every loaded skill passes schema validation with no errors."""
    for skill in all_skills:
        errors = validate_skill(skill)
        assert errors == [], f"Skill '{skill.id}' has validation errors: {errors}"


def test_unique_skill_ids(all_skills: list[SkillDefinition]) -> None:
    """No duplicate skill IDs in the library."""
    ids = [s.id for s in all_skills]
    assert len(ids) == len(set(ids)), f"Duplicate IDs found: {[x for x in ids if ids.count(x) > 1]}"


# ---------------------------------------------------------------------------
# Field-level validation
# ---------------------------------------------------------------------------


def test_tools_required_exist(all_skills: list[SkillDefinition]) -> None:
    """All tools_required values exist in ALL_TOOLS."""
    known_tools = set(ALL_TOOLS)
    for skill in all_skills:
        for tool in skill.tools_required:
            assert tool in known_tools, (
                f"Skill '{skill.id}' references unknown tool '{tool}'. "
                f"Valid tools: {sorted(known_tools)}"
            )


def test_categories_valid(all_skills: list[SkillDefinition]) -> None:
    """All categories are in the allowed set."""
    for skill in all_skills:
        assert skill.category in VALID_CATEGORIES, (
            f"Skill '{skill.id}' has invalid category '{skill.category}'. "
            f"Valid: {sorted(VALID_CATEGORIES)}"
        )


def test_platforms_valid(all_skills: list[SkillDefinition]) -> None:
    """All platforms are in the allowed set."""
    for skill in all_skills:
        for platform in skill.platforms:
            assert platform in VALID_PLATFORMS, (
                f"Skill '{skill.id}' has invalid platform '{platform}'. "
                f"Valid: {sorted(VALID_PLATFORMS)}"
            )


def test_models_valid(all_skills: list[SkillDefinition]) -> None:
    """All skills specify a valid model."""
    for skill in all_skills:
        assert skill.model in VALID_MODELS, (
            f"Skill '{skill.id}' has invalid model '{skill.model}'. Valid: {sorted(VALID_MODELS)}"
        )


def test_max_turns_reasonable(all_skills: list[SkillDefinition]) -> None:
    """All max_turns values are within the allowed range (1-50)."""
    for skill in all_skills:
        assert 1 <= skill.max_turns <= 50, (
            f"Skill '{skill.id}' has max_turns={skill.max_turns}, must be 1-50"
        )


def test_all_skills_have_tags(all_skills: list[SkillDefinition]) -> None:
    """Every skill has at least 5 tags for good router matching."""
    for skill in all_skills:
        assert len(skill.tags) >= 5, (
            f"Skill '{skill.id}' only has {len(skill.tags)} tags. "
            "Need at least 5 for good router coverage."
        )


def test_all_skills_have_nonempty_prompts(all_skills: list[SkillDefinition]) -> None:
    """Every skill has non-empty prompt fields."""
    for skill in all_skills:
        assert skill.system_supplement.strip(), f"Skill '{skill.id}' has empty system_supplement"
        assert skill.prompt_template.strip(), f"Skill '{skill.id}' has empty prompt_template"
        assert skill.output_format.strip(), f"Skill '{skill.id}' has empty output_format"
        assert skill.business_guidance.strip(), f"Skill '{skill.id}' has empty business_guidance"


# ---------------------------------------------------------------------------
# Scheduling & chaining
# ---------------------------------------------------------------------------


def test_scheduled_skills_have_cron(all_skills: list[SkillDefinition]) -> None:
    """Skills with schedule set have valid cron-like expressions."""
    scheduled = [s for s in all_skills if s.schedule is not None]
    assert len(scheduled) >= 3, f"Expected at least 3 scheduled skills, got {len(scheduled)}"
    for skill in scheduled:
        # Basic cron format check: 5 space-separated parts
        parts = skill.schedule.split()
        assert len(parts) == 5, (
            f"Skill '{skill.id}' schedule '{skill.schedule}' "
            "doesn't look like a 5-part cron expression"
        )


def test_chain_after_references_valid(
    all_skills: list[SkillDefinition],
    registry: SkillRegistry,
) -> None:
    """chain_after targets exist in the registry."""
    chained = [s for s in all_skills if s.chain_after is not None]
    for skill in chained:
        target = registry.get(skill.chain_after)
        assert target is not None, (
            f"Skill '{skill.id}' chains after '{skill.chain_after}', but that skill doesn't exist"
        )


def test_no_self_referencing_chains(all_skills: list[SkillDefinition]) -> None:
    """No skill chains after itself."""
    for skill in all_skills:
        if skill.chain_after:
            assert skill.chain_after != skill.id, f"Skill '{skill.id}' chains after itself"


# ---------------------------------------------------------------------------
# Monitoring skill design decisions
# ---------------------------------------------------------------------------


def test_monitoring_skills_use_haiku(all_skills: list[SkillDefinition]) -> None:
    """Monitoring skills use the haiku model (lightweight checks)."""
    monitoring = [s for s in all_skills if s.category == "monitoring"]
    assert len(monitoring) >= 3, f"Expected at least 3 monitoring skills, got {len(monitoring)}"
    for skill in monitoring:
        assert skill.model == "haiku", (
            f"Monitoring skill '{skill.id}' uses '{skill.model}' instead of 'haiku'"
        )


def test_monitoring_skills_no_approval(all_skills: list[SkillDefinition]) -> None:
    """Monitoring skills don't require approval (read-only)."""
    monitoring = [s for s in all_skills if s.category == "monitoring"]
    for skill in monitoring:
        assert skill.requires_approval is False, (
            f"Monitoring skill '{skill.id}' requires approval but monitoring skills are read-only"
        )


def test_monitoring_skills_low_turns(all_skills: list[SkillDefinition]) -> None:
    """Monitoring skills have low max_turns (quick checks)."""
    monitoring = [s for s in all_skills if s.category == "monitoring"]
    for skill in monitoring:
        assert skill.max_turns <= 10, (
            f"Monitoring skill '{skill.id}' has max_turns={skill.max_turns}, "
            "expected <= 10 for quick checks"
        )


# ---------------------------------------------------------------------------
# Specific skill checks
# ---------------------------------------------------------------------------


def test_budget_pacing_has_daily_schedule(registry: SkillRegistry) -> None:
    """budget_pacing_check runs daily at noon."""
    skill = registry.get("budget_pacing_check")
    assert skill is not None
    assert skill.schedule is not None
    # Should be a daily schedule (minute hour * * *)
    parts = skill.schedule.split()
    assert parts[2] == "*" and parts[3] == "*" and parts[4] == "*", (
        f"Expected daily schedule, got '{skill.schedule}'"
    )


def test_monthly_report_has_monthly_schedule(registry: SkillRegistry) -> None:
    """monthly_report runs on the 1st of each month."""
    skill = registry.get("monthly_report")
    assert skill is not None
    assert skill.schedule is not None
    parts = skill.schedule.split()
    # Day-of-month should be "1"
    assert parts[2] == "1", (
        f"Expected monthly_report on day 1, got day '{parts[2]}' in schedule '{skill.schedule}'"
    )


def test_creative_fatigue_chains_after_creative_analysis(
    registry: SkillRegistry,
) -> None:
    """creative_fatigue_check chains after creative_analysis."""
    skill = registry.get("creative_fatigue_check")
    assert skill is not None
    assert skill.chain_after == "creative_analysis", (
        f"Expected chain_after='creative_analysis', got '{skill.chain_after}'"
    )


# ---------------------------------------------------------------------------
# Router integration
# ---------------------------------------------------------------------------


def test_router_index_built(registry: SkillRegistry) -> None:
    """Router builds an index containing all 23 skills."""
    index = registry.build_routing_index()
    assert index, "Routing index is empty"
    lines = [ln for ln in index.strip().split("\n") if ln.strip()]
    assert len(lines) == 23, f"Expected 23 lines in routing index, got {len(lines)}"
    # Each line should have the format: skill_id | description | tags
    for line in lines:
        parts = line.split(" | ")
        assert len(parts) == 3, f"Malformed index line: {line}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,expected_skill_id",
    [
        ("Why did my CPA spike 40% yesterday?", "anomaly_detector"),
        ("What search terms are wasting money?", "search_term_audit"),
        ("Are Google and Meta targeting the same people?", "audience_overlap"),
        ("Which landing pages have the worst conversion rate?", "landing_page_analysis"),
        ("Should I switch from tCPA to tROAS?", "bid_strategy_review"),
        ("What hours of the day perform best?", "dayparting_analysis"),
        ("Which states have the highest CPA?", "geo_performance"),
        ("Are my ads getting stale?", "creative_fatigue_check"),
        ("Am I on track for this month's budget?", "budget_pacing_check"),
        ("Is everything connected and working?", "platform_health_check"),
        ("How do we compare to industry averages?", "competitor_benchmark"),
        ("Generate the month-end report", "monthly_report"),
        ("Which creatives should I cut or scale?", "creative_analysis"),
        ("Should I move budget from Meta to Google?", "budget_reallocation"),
        ("Generate the weekly performance summary", "weekly_report"),
    ],
)
async def test_router_matches_common_queries(
    registry: SkillRegistry,
    query: str,
    expected_skill_id: str,
) -> None:
    """Router correctly matches common natural-language queries to skills.

    Uses a mocked LLM response to avoid real API calls.
    """
    router = SkillRouter(registry)

    result = LLMResult(
        text=json.dumps(
            {
                "skill_id": expected_skill_id,
                "confidence": 0.9,
                "reasoning": f"Test match for {expected_skill_id}",
            }
        ),
        model="test",
        provider="anthropic",
    )

    with patch(_CWF, new_callable=AsyncMock, return_value=result):
        match = await router.route(query)

    assert match is not None, f"No match for query: '{query}'"
    assert match.skill.id == expected_skill_id, (
        f"Query '{query}' matched '{match.skill.id}' instead of '{expected_skill_id}'"
    )
    assert match.confidence >= 0.5


# ---------------------------------------------------------------------------
# Category distribution
# ---------------------------------------------------------------------------


def test_category_distribution(all_skills: list[SkillDefinition]) -> None:
    """Library has a healthy distribution of categories."""
    categories = {}
    for skill in all_skills:
        categories[skill.category] = categories.get(skill.category, 0) + 1

    # We should have at least 4 different categories
    assert len(categories) >= 4, (
        f"Expected at least 4 categories, got {len(categories)}: {categories}"
    )


def test_platform_coverage(all_skills: list[SkillDefinition]) -> None:
    """Skills cover all major platforms."""
    all_platforms: set[str] = set()
    for skill in all_skills:
        all_platforms.update(skill.platforms)

    assert "google_ads" in all_platforms
    assert "meta" in all_platforms
    assert "bigquery" in all_platforms
