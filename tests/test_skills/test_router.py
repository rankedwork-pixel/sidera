"""Tests for src.skills.router -- SkillRouter, SkillMatch, ROUTER_SYSTEM_PROMPT.

Covers SkillMatch dataclass creation, SkillRouter initialization, route() with
high-confidence match, low-confidence match, empty registry, unknown skill_id,
API errors, empty API response, invalid JSON, user_context, route_batch(),
_parse_response() markdown code fence handling, and _build_routing_prompt()
format.

The ``complete_with_fallback`` function is mocked throughout so no real API
calls are made.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.provider import LLMResult, TaskType
from src.skills.router import (
    ROUTER_SYSTEM_PROMPT,
    SkillMatch,
    SkillRouter,
)
from src.skills.schema import SkillDefinition

# Patch target for complete_with_fallback used in all route() tests
_CWF = "src.skills.router.complete_with_fallback"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill(**overrides) -> SkillDefinition:
    """Build a SkillDefinition with sensible defaults, applying overrides."""
    defaults = {
        "id": "creative_analysis",
        "name": "Creative Analysis",
        "version": "1.0",
        "description": "Analyze ad creative performance across platforms",
        "category": "analysis",
        "platforms": ("google_ads", "meta"),
        "tags": ("creative", "performance", "ads"),
        "tools_required": ("get_meta_campaigns",),
        "model": "sonnet",
        "max_turns": 10,
        "system_supplement": "You are a creative analyst.",
        "prompt_template": "Analyze creatives for {account}.",
        "output_format": "## Creative Report",
        "business_guidance": "Focus on ROAS impact.",
    }
    defaults.update(overrides)
    return SkillDefinition(**defaults)


def _make_llm_result(text: str) -> LLMResult:
    """Build an LLMResult with the given text."""
    return LLMResult(text=text, model="test", provider="anthropic")


def _mock_registry(skills: dict[str, SkillDefinition] | None = None) -> MagicMock:
    """Build a mock SkillRegistry with configurable skills.

    Args:
        skills: Mapping of skill_id -> SkillDefinition. If None, an empty
            registry is created.
    """
    skills = skills or {}
    registry = MagicMock()
    registry.get.side_effect = lambda sid: skills.get(sid)
    registry.count = len(skills)

    # build_routing_index returns a compact text index
    if skills:
        lines = []
        for skill in sorted(skills.values(), key=lambda s: s.id):
            tags_str = ", ".join(skill.tags)
            lines.append(f"{skill.id} | {skill.description} | {tags_str}")
        registry.build_routing_index.return_value = "\n".join(lines)
    else:
        registry.build_routing_index.return_value = ""

    return registry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def creative_skill() -> SkillDefinition:
    """A sample creative_analysis skill."""
    return _make_skill()


@pytest.fixture()
def budget_skill() -> SkillDefinition:
    """A sample budget_reallocation skill."""
    return _make_skill(
        id="budget_reallocation",
        name="Budget Reallocation",
        description="Recommend cross-platform budget shifts",
        category="budget",
        tags=("budget", "optimization", "reallocation"),
    )


@pytest.fixture()
def registry_with_skills(
    creative_skill: SkillDefinition,
    budget_skill: SkillDefinition,
) -> MagicMock:
    """A mock registry containing two skills."""
    return _mock_registry(
        {
            creative_skill.id: creative_skill,
            budget_skill.id: budget_skill,
        }
    )


@pytest.fixture()
def empty_registry() -> MagicMock:
    """A mock registry with no loaded skills."""
    return _mock_registry()


# ===========================================================================
# 1. SkillMatch dataclass
# ===========================================================================


class TestSkillMatch:
    """SkillMatch is a simple dataclass holding routing results."""

    def test_creation(self, creative_skill: SkillDefinition):
        """SkillMatch stores skill, confidence, and reasoning."""
        match = SkillMatch(
            skill=creative_skill,
            confidence=0.92,
            reasoning="Query is about ad creative performance",
        )
        assert match.skill is creative_skill
        assert match.confidence == 0.92
        assert match.reasoning == "Query is about ad creative performance"

    def test_attributes_accessible(self, creative_skill: SkillDefinition):
        """The nested skill's attributes are reachable through the match."""
        match = SkillMatch(skill=creative_skill, confidence=0.8, reasoning="test")
        assert match.skill.id == "creative_analysis"
        assert match.skill.category == "analysis"


# ===========================================================================
# 2. SkillRouter.__init__
# ===========================================================================


class TestSkillRouterInit:
    """SkillRouter stores the registry on initialization."""

    def test_stores_registry(self, registry_with_skills: MagicMock):
        """The router stores the registry reference internally."""
        router = SkillRouter(registry_with_skills)
        assert router._registry is registry_with_skills


# ===========================================================================
# 3. SkillRouter.route() -- high confidence match
# ===========================================================================


class TestRouteHighConfidence:
    """route() returns a SkillMatch when Haiku returns a high confidence."""

    @pytest.mark.asyncio
    async def test_returns_skill_match(
        self, registry_with_skills: MagicMock, creative_skill: SkillDefinition
    ):
        """A confidence >= 0.5 and valid skill_id returns a SkillMatch."""
        result = _make_llm_result(
            json.dumps(
                {
                    "skill_id": "creative_analysis",
                    "confidence": 0.92,
                    "reasoning": "Query asks about creative performance",
                }
            )
        )

        router = SkillRouter(registry_with_skills)

        with patch(_CWF, new_callable=AsyncMock, return_value=result):
            match = await router.route("Why did my creative CTR drop?")

        assert match is not None
        assert isinstance(match, SkillMatch)
        assert match.skill is creative_skill
        assert match.confidence == 0.92
        assert "creative" in match.reasoning.lower()

    @pytest.mark.asyncio
    async def test_calls_with_correct_task_type(self, registry_with_skills: MagicMock):
        """The LLM call uses TaskType.SKILL_ROUTING and max_tokens=200."""
        result = _make_llm_result(
            json.dumps(
                {
                    "skill_id": "creative_analysis",
                    "confidence": 0.85,
                    "reasoning": "Match",
                }
            )
        )

        router = SkillRouter(registry_with_skills)

        with patch(_CWF, new_callable=AsyncMock, return_value=result) as mock_cwf:
            await router.route("test query")

            mock_cwf.assert_called_once()
            call_kwargs = mock_cwf.call_args.kwargs
            assert call_kwargs["task_type"] == TaskType.SKILL_ROUTING
            assert call_kwargs["max_tokens"] == 200
            assert call_kwargs["system_prompt"] == ROUTER_SYSTEM_PROMPT


# ===========================================================================
# 4. SkillRouter.route() -- low confidence
# ===========================================================================


class TestRouteLowConfidence:
    """route() returns None when Haiku reports low confidence (< 0.5)."""

    @pytest.mark.asyncio
    async def test_returns_none_for_low_confidence(self, registry_with_skills: MagicMock):
        """A confidence of 0.3 is below the 0.5 threshold."""
        result = _make_llm_result(
            json.dumps(
                {
                    "skill_id": "creative_analysis",
                    "confidence": 0.3,
                    "reasoning": "Weak match",
                }
            )
        )

        router = SkillRouter(registry_with_skills)

        with patch(_CWF, new_callable=AsyncMock, return_value=result):
            match = await router.route("What's the weather today?")

        assert match is None


# ===========================================================================
# 5. SkillRouter.route() -- empty registry
# ===========================================================================


class TestRouteEmptyRegistry:
    """route() returns None immediately when no skills are loaded."""

    @pytest.mark.asyncio
    async def test_returns_none_empty_registry(self, empty_registry: MagicMock):
        """An empty routing index short-circuits to None without an API call."""
        router = SkillRouter(empty_registry)

        # No need to mock anthropic -- it should never be called
        match = await router.route("Anything at all")

        assert match is None
        # Verify no API call was attempted
        empty_registry.build_routing_index.assert_called_once()


# ===========================================================================
# 6. SkillRouter.route() -- unknown skill_id
# ===========================================================================


class TestRouteUnknownSkillId:
    """route() returns None when Haiku returns a skill_id not in the registry."""

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_id(self, registry_with_skills: MagicMock):
        """An unrecognized skill_id (not in registry) returns None."""
        result = _make_llm_result(
            json.dumps(
                {
                    "skill_id": "nonexistent_skill",
                    "confidence": 0.95,
                    "reasoning": "Hallucinated skill",
                }
            )
        )

        router = SkillRouter(registry_with_skills)

        with patch(_CWF, new_callable=AsyncMock, return_value=result):
            match = await router.route("Some query")

        assert match is None


# ===========================================================================
# 7. SkillRouter.route() -- API error
# ===========================================================================


class TestRouteApiError:
    """route() returns None when the Anthropic API raises an error."""

    @pytest.mark.asyncio
    async def test_returns_none_on_api_error(self, registry_with_skills: MagicMock):
        """An error during the LLM call returns None."""
        router = SkillRouter(registry_with_skills)

        with patch(_CWF, new_callable=AsyncMock, side_effect=RuntimeError("Service unavailable")):
            match = await router.route("Some query")

        assert match is None

    @pytest.mark.asyncio
    async def test_returns_none_on_unexpected_error(self, registry_with_skills: MagicMock):
        """A generic exception during the LLM call returns None."""
        router = SkillRouter(registry_with_skills)

        with patch(_CWF, new_callable=AsyncMock, side_effect=RuntimeError("timeout")):
            match = await router.route("Some query")

        assert match is None


# ===========================================================================
# 8. SkillRouter.route() -- empty API response
# ===========================================================================


class TestRouteEmptyResponse:
    """route() returns None when the API returns an empty content list."""

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_response(self, registry_with_skills: MagicMock):
        """An empty text response from the LLM returns None."""
        result = _make_llm_result("")

        router = SkillRouter(registry_with_skills)

        with patch(_CWF, new_callable=AsyncMock, return_value=result):
            match = await router.route("Some query")

        assert match is None


# ===========================================================================
# 9. SkillRouter.route() -- invalid JSON response
# ===========================================================================


class TestRouteInvalidJson:
    """route() returns None when Haiku returns non-JSON text."""

    @pytest.mark.asyncio
    async def test_returns_none_on_invalid_json(self, registry_with_skills: MagicMock):
        """Completely invalid JSON text returns None."""
        result = _make_llm_result("This is not JSON at all.")

        router = SkillRouter(registry_with_skills)

        with patch(_CWF, new_callable=AsyncMock, return_value=result):
            match = await router.route("Some query")

        assert match is None

    @pytest.mark.asyncio
    async def test_returns_none_on_json_array(self, registry_with_skills: MagicMock):
        """A JSON array (not object) returns None."""
        result = _make_llm_result('[{"skill_id": "creative_analysis"}]')

        router = SkillRouter(registry_with_skills)

        with patch(_CWF, new_callable=AsyncMock, return_value=result):
            match = await router.route("Some query")

        assert match is None


# ===========================================================================
# 10. SkillRouter.route() -- user_context
# ===========================================================================


class TestRouteWithUserContext:
    """route() includes user_context in the routing prompt when provided."""

    @pytest.mark.asyncio
    async def test_user_context_included_in_prompt(
        self, registry_with_skills: MagicMock, creative_skill: SkillDefinition
    ):
        """Passing user_context adds it to the prompt sent to the LLM."""
        result = _make_llm_result(
            json.dumps(
                {
                    "skill_id": "creative_analysis",
                    "confidence": 0.88,
                    "reasoning": "Meta-specific creative question",
                }
            )
        )

        router = SkillRouter(registry_with_skills)
        user_context = {"platform": "meta", "account_ids": ["act_12345"]}

        with patch(_CWF, new_callable=AsyncMock, return_value=result) as mock_cwf:
            match = await router.route(
                "Why did my creatives underperform?",
                user_context=user_context,
            )

            # Verify user_context was included in the prompt
            call_kwargs = mock_cwf.call_args.kwargs
            user_message = call_kwargs["user_message"]
            assert "meta" in user_message
            assert "act_12345" in user_message

        assert match is not None
        assert match.skill is creative_skill


# ===========================================================================
# 11. SkillRouter.route_batch()
# ===========================================================================


class TestRouteBatch:
    """route_batch() processes multiple queries sequentially."""

    @pytest.mark.asyncio
    async def test_batch_returns_list_of_results(self, registry_with_skills: MagicMock):
        """route_batch() returns one result per query in order."""
        # First query matches creative_analysis, second matches nothing
        responses = [
            _make_llm_result(
                json.dumps(
                    {
                        "skill_id": "creative_analysis",
                        "confidence": 0.9,
                        "reasoning": "Creative match",
                    }
                )
            ),
            _make_llm_result(
                json.dumps(
                    {
                        "skill_id": "creative_analysis",
                        "confidence": 0.2,
                        "reasoning": "No real match",
                    }
                )
            ),
        ]

        router = SkillRouter(registry_with_skills)

        with patch(_CWF, new_callable=AsyncMock, side_effect=responses):
            results = await router.route_batch(["Analyze my creatives", "What's the weather?"])

        assert len(results) == 2
        assert results[0] is not None
        assert isinstance(results[0], SkillMatch)
        assert results[0].skill.id == "creative_analysis"
        assert results[1] is None

    @pytest.mark.asyncio
    async def test_batch_empty_list(self, registry_with_skills: MagicMock):
        """route_batch() with an empty list returns an empty list."""
        router = SkillRouter(registry_with_skills)
        results = await router.route_batch([])
        assert results == []


# ===========================================================================
# 12. _parse_response() -- markdown code fences
# ===========================================================================


class TestParseResponse:
    """_parse_response() handles JSON wrapped in markdown code fences."""

    def test_plain_json(self, registry_with_skills: MagicMock):
        """Plain JSON without code fences parses correctly."""
        router = SkillRouter(registry_with_skills)
        result = router._parse_response(
            '{"skill_id": "creative_analysis", "confidence": 0.9, "reasoning": "ok"}'
        )
        assert result is not None
        assert result["skill_id"] == "creative_analysis"
        assert result["confidence"] == 0.9

    def test_json_with_code_fences(self, registry_with_skills: MagicMock):
        """JSON wrapped in ```json ... ``` code fences parses correctly."""
        router = SkillRouter(registry_with_skills)
        fenced = (
            "```json\n"
            '{"skill_id": "budget_reallocation", "confidence": 0.8, '
            '"reasoning": "budget question"}\n'
            "```"
        )
        result = router._parse_response(fenced)
        assert result is not None
        assert result["skill_id"] == "budget_reallocation"
        assert result["confidence"] == 0.8

    def test_json_with_bare_fences(self, registry_with_skills: MagicMock):
        """JSON wrapped in bare ``` ... ``` fences (no language tag) parses."""
        router = SkillRouter(registry_with_skills)
        fenced = (
            '```\n{"skill_id": "creative_analysis", "confidence": 0.7, "reasoning": "maybe"}\n```'
        )
        result = router._parse_response(fenced)
        assert result is not None
        assert result["skill_id"] == "creative_analysis"

    def test_invalid_json_returns_none(self, registry_with_skills: MagicMock):
        """Unparseable text returns None."""
        router = SkillRouter(registry_with_skills)
        result = router._parse_response("not json")
        assert result is None

    def test_non_dict_json_returns_none(self, registry_with_skills: MagicMock):
        """A JSON array returns None (expected a dict)."""
        router = SkillRouter(registry_with_skills)
        result = router._parse_response("[1, 2, 3]")
        assert result is None


# ===========================================================================
# 13. _build_routing_prompt() format
# ===========================================================================


class TestBuildRoutingPrompt:
    """_build_routing_prompt() formats the query, index, and context."""

    def test_basic_prompt_structure(self, registry_with_skills: MagicMock):
        """The prompt includes the routing index, query, and instructions."""
        router = SkillRouter(registry_with_skills)
        routing_index = registry_with_skills.build_routing_index()

        prompt = router._build_routing_prompt(
            query="Why did CPA spike?",
            routing_index=routing_index,
        )

        assert "Available skills" in prompt
        assert "Why did CPA spike?" in prompt
        assert "Respond with JSON only" in prompt
        # The routing index content should be included
        assert "creative_analysis" in prompt

    def test_prompt_includes_user_context(self, registry_with_skills: MagicMock):
        """When user_context is provided, it appears in the prompt."""
        router = SkillRouter(registry_with_skills)
        routing_index = registry_with_skills.build_routing_index()

        prompt = router._build_routing_prompt(
            query="Shift budget",
            routing_index=routing_index,
            user_context={"platform": "google_ads", "monthly_spend": 100000},
        )

        assert "User context:" in prompt
        assert "google_ads" in prompt
        assert "100000" in prompt

    def test_prompt_without_user_context(self, registry_with_skills: MagicMock):
        """When user_context is None, no context line appears."""
        router = SkillRouter(registry_with_skills)
        routing_index = registry_with_skills.build_routing_index()

        prompt = router._build_routing_prompt(
            query="Analyze creatives",
            routing_index=routing_index,
            user_context=None,
        )

        assert "User context:" not in prompt
