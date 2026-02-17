"""Semantic skill router for Sidera.

Uses Claude Haiku to match user queries to the most relevant skill
from the registry. The router is intentionally lightweight — it calls
the Anthropic API directly rather than going through SideraAgent.

Usage::

    from src.skills.registry import SkillRegistry
    from src.skills.router import SkillRouter

    registry = SkillRegistry()
    registry.load_all()

    router = SkillRouter(registry)
    match = await router.route("Why did my CPA spike yesterday?")
    if match:
        print(match.skill.id, match.confidence, match.reasoning)
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import structlog

from src.llm import TaskType
from src.llm.router import complete_with_fallback
from src.skills.registry import SkillRegistry
from src.skills.schema import SkillDefinition

logger = structlog.get_logger(__name__)

# =============================================================================
# Constants
# =============================================================================

ROUTER_SYSTEM_PROMPT = """\
You are a skill router for Sidera, an AI performance marketing agent.

Given a user query and a list of available skills, determine which skill \
best matches the user's intent. If no skill is a good match, indicate \
low confidence.

Respond with a single JSON object:
{"skill_id": "the_skill_id", "confidence": 0.85, "reasoning": "Brief explanation"}

Rules:
- confidence should be 0.0-1.0 where 1.0 is a perfect match
- If the query doesn't match any skill well, use confidence < 0.5
- Consider synonyms and related concepts, not just exact keyword matches
- A query about "cutting creatives" matches "creative_analysis" even \
without that exact phrase
"""

_CONFIDENCE_THRESHOLD = 0.5


# =============================================================================
# Result dataclass
# =============================================================================


@dataclass
class SkillMatch:
    """Result of routing a user query to a skill.

    Attributes:
        skill: The matched ``SkillDefinition``.
        confidence: How confident the router is in the match (0.0-1.0).
        reasoning: Brief explanation of why this skill was selected.
    """

    skill: SkillDefinition
    confidence: float
    reasoning: str


# =============================================================================
# Router
# =============================================================================


class SkillRouter:
    """Semantic router that uses Claude Haiku to match queries to skills.

    Builds a compact routing index from the registry and sends it alongside
    the user's query to Haiku for classification. Returns a ``SkillMatch``
    if confidence meets the threshold, otherwise ``None``.

    Args:
        registry: The ``SkillRegistry`` containing loaded skills.
    """

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry
        self._log = logger.bind(component="skill_router")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def route(
        self,
        query: str,
        user_context: dict[str, object] | None = None,
    ) -> SkillMatch | None:
        """Route a user query to the best matching skill.

        Uses Claude Haiku to semantically match the query against the
        skill routing index.

        Args:
            query: The user's natural-language query.
            user_context: Optional dict of additional context (e.g.
                ``account_ids``, ``platform``). Included in the routing
                prompt when provided.

        Returns:
            A ``SkillMatch`` if a skill matches with confidence >= 0.5,
            otherwise ``None``.
        """
        routing_index = self._registry.build_routing_index()
        if not routing_index:
            self._log.warning("route.no_skills", query_preview=query[:80])
            return None

        routing_prompt = self._build_routing_prompt(
            query=query,
            routing_index=routing_index,
            user_context=user_context,
        )

        self._log.debug(
            "route.start",
            query_preview=query[:80],
            num_skills=self._registry.count,
        )

        # Call Haiku for classification
        response_text = await self._call_haiku(routing_prompt)
        if response_text is None:
            return None

        # Parse JSON response
        parsed = self._parse_response(response_text)
        if parsed is None:
            return None

        skill_id = parsed.get("skill_id", "")
        confidence = parsed.get("confidence", 0.0)
        reasoning = parsed.get("reasoning", "")

        # Validate confidence is a number in range
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            self._log.warning(
                "route.invalid_confidence",
                raw_confidence=confidence,
                query_preview=query[:80],
            )
            return None

        confidence = max(0.0, min(1.0, confidence))

        # Look up skill in registry
        skill = self._registry.get(skill_id)
        if skill is None:
            self._log.warning(
                "route.unknown_skill_id",
                skill_id=skill_id,
                query_preview=query[:80],
            )
            return None

        # Apply confidence threshold
        if confidence < _CONFIDENCE_THRESHOLD:
            self._log.info(
                "route.low_confidence",
                skill_id=skill_id,
                confidence=confidence,
                query_preview=query[:80],
            )
            return None

        self._log.info(
            "route.matched",
            skill_id=skill_id,
            confidence=confidence,
            reasoning=reasoning,
            query_preview=query[:80],
        )

        return SkillMatch(
            skill=skill,
            confidence=confidence,
            reasoning=reasoning,
        )

    async def route_batch(
        self,
        queries: list[str],
    ) -> list[SkillMatch | None]:
        """Route multiple queries (convenience wrapper).

        Calls ``route()`` for each query sequentially.

        Args:
            queries: List of user queries to route.

        Returns:
            List of ``SkillMatch | None`` in the same order as the input.
        """
        results: list[SkillMatch | None] = []
        for q in queries:
            match = await self.route(q)
            results.append(match)
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_routing_prompt(
        self,
        query: str,
        routing_index: str,
        user_context: dict[str, object] | None = None,
    ) -> str:
        """Build the user-side routing prompt sent to Haiku.

        Args:
            query: The user's natural-language query.
            routing_index: The compact skill index from the registry.
            user_context: Optional additional context dict.

        Returns:
            The formatted prompt string.
        """
        parts = [
            "Available skills (format: skill_id | description | tags):",
            routing_index,
            "",
            f"User query: {query}",
        ]

        if user_context:
            context_str = json.dumps(user_context, default=str)
            parts.append(f"User context: {context_str}")

        parts.append("")
        parts.append("Which skill best matches this query? Respond with JSON only.")

        return "\n".join(parts)

    async def _call_haiku(self, routing_prompt: str) -> str | None:
        """Call the LLM provider for skill routing.

        Uses the hybrid router which may route to a cheaper external
        provider (e.g. Groq, MiniMax) when configured, with automatic
        fallback to Claude on failure.

        Args:
            routing_prompt: The formatted prompt to send.

        Returns:
            The text response, or ``None`` on error.
        """
        try:
            result = await complete_with_fallback(
                task_type=TaskType.SKILL_ROUTING,
                system_prompt=ROUTER_SYSTEM_PROMPT,
                user_message=routing_prompt,
                max_tokens=200,
            )
            if result.is_fallback:
                self._log.info(
                    "route.used_fallback",
                    original_provider=result.metadata.get("original_provider"),
                )
        except Exception as exc:
            self._log.error(
                "route.api_error",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

        if not result.text:
            self._log.warning("route.empty_response")
            return None

        return result.text

    def _parse_response(self, response_text: str) -> dict[str, object] | None:
        """Parse the JSON response from Haiku.

        Handles cases where Haiku wraps JSON in markdown code fences.

        Args:
            response_text: Raw text response from the API.

        Returns:
            Parsed dict, or ``None`` if parsing fails.
        """
        text = response_text.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first line (```json or ```) and last line (```)
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            self._log.warning(
                "route.json_parse_error",
                error=str(exc),
                response_preview=response_text[:200],
            )
            return None

        if not isinstance(parsed, dict):
            self._log.warning(
                "route.unexpected_json_type",
                json_type=type(parsed).__name__,
            )
            return None

        return parsed
