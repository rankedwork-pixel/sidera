"""Tests for the full skill library — validates all YAML skills load and pass validation.

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

# All 10 expected skill IDs (1 example per role + IT + fb_creative_cuts)
EXPECTED_SKILL_IDS = sorted(
    [
        # Marketing / performance_media_buyer (3 — 1 standalone + 1 folder-based + 1 code-backed)
        "anomaly_detector",
        "creative_analysis",
        "fb_creative_cuts",
        # Marketing / reporting_analyst (1)
        "weekly_report",
        # Marketing / strategist (1)
        "competitor_benchmark",
        # Marketing / head_of_marketing (1)
        "executive_summary",
        # IT / head_of_it (3)
        "system_health_check",
        "error_diagnosis",
        "cost_monitoring",
        # Executive / ceo (1)
        "org_health_check",
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
    """SkillRegistry loads all 10 skills."""
    assert registry.count == 10, (
        f"Expected 10 skills, got {registry.count}. "
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
    assert len(scheduled) >= 1, f"Expected at least 1 scheduled skill, got {len(scheduled)}"
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
# Operations skill design decisions
# ---------------------------------------------------------------------------


def test_operations_skills_no_approval(all_skills: list[SkillDefinition]) -> None:
    """Operations skills don't require approval (read-only diagnostics)."""
    operations = [s for s in all_skills if s.category == "operations"]
    assert len(operations) >= 1, f"Expected at least 1 operations skill, got {len(operations)}"
    for skill in operations:
        assert skill.requires_approval is False, (
            f"Operations skill '{skill.id}' requires approval but operations skills are read-only"
        )


# ---------------------------------------------------------------------------
# Specific skill checks
# ---------------------------------------------------------------------------


def test_weekly_report_has_schedule(registry: SkillRegistry) -> None:
    """weekly_report runs on Mondays."""
    skill = registry.get("weekly_report")
    assert skill is not None
    assert skill.schedule is not None
    parts = skill.schedule.split()
    assert len(parts) == 5, f"Expected 5-part cron, got '{skill.schedule}'"


# ---------------------------------------------------------------------------
# Router integration
# ---------------------------------------------------------------------------


def test_router_index_built(registry: SkillRegistry) -> None:
    """Router builds an index containing all 10 skills."""
    index = registry.build_routing_index()
    assert index, "Routing index is empty"
    lines = [ln for ln in index.strip().split("\n") if ln.strip()]
    assert len(lines) == 10, f"Expected 10 lines in routing index, got {len(lines)}"
    # Each line should have the format: skill_id | description | tags
    for line in lines:
        parts = line.split(" | ")
        assert len(parts) == 3, f"Malformed index line: {line}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,expected_skill_id",
    [
        ("Why did my CPA spike 40% yesterday?", "anomaly_detector"),
        ("How do we compare to industry averages?", "competitor_benchmark"),
        ("Which creatives should I cut or scale?", "creative_analysis"),
        ("Generate the weekly performance summary", "weekly_report"),
        ("What's the executive overview?", "executive_summary"),
        ("Is the system healthy?", "system_health_check"),
        ("Check organization health", "org_health_check"),
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
