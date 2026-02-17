"""Data-driven semantic role router for Sidera conversation mode.

Routes user messages and slash-command queries to the most relevant role
from the registry. Uses a two-tier strategy:

1. **Data-driven pattern matching** — regex patterns are built dynamically
   from ``routing_keywords`` on roles and departments. No hardcoded role
   IDs or patterns.  Adding/renaming roles requires zero code changes —
   just update the YAML ``routing_keywords`` field.
2. **Haiku semantic fallback** — if no pattern matches, sends the query
   plus a compact role index to Claude Haiku for classification.

Usage::

    from src.skills.registry import SkillRegistry
    from src.skills.role_router import RoleRouter

    registry = SkillRegistry()
    registry.load_all()

    router = RoleRouter(registry)
    match = await router.route("What's our ROAS looking like this week?")
    if match:
        print(match.role.id, match.confidence, match.reasoning)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import structlog

from src.llm import TaskType
from src.llm.router import complete_with_fallback
from src.skills.registry import SkillRegistry
from src.skills.schema import DepartmentDefinition, RoleDefinition

logger = structlog.get_logger(__name__)

# =============================================================================
# Constants
# =============================================================================

ROLE_ROUTER_SYSTEM_PROMPT = """\
You are a role router for Sidera, an AI agent framework. Sidera has \
multiple AI employee roles, each with different expertise.

Given a user message and a list of available roles, determine which role \
is best suited to handle the conversation. If no role is a clear match, \
pick the most general one.

Respond with a single JSON object:
{"role_id": "the_role_id", "confidence": 0.85, "reasoning": "Brief explanation"}

Rules:
- confidence should be 0.0-1.0 where 1.0 is a perfect match
- Consider the role's name, description, and persona when matching
- If the query is about data, metrics, or performance, prefer analytical roles
- If the query is about strategy or planning, prefer strategic roles
- If the message is a greeting or filler with no real question \
(e.g. "hi", "yo", "hey"), return confidence 0.1
- If the message references a department by name (e.g. "marketing department", \
"yo marketing"), route to that department's head/manager role with confidence 0.9
- If unclear which role fits, return confidence below 0.3
"""

_CONFIDENCE_THRESHOLD = 0.55

# Shared verb group for "talk to the ..." patterns
_TALK_TO = r"\b(?:talk|speak|chat|ask)\b.*\b(?:to|with)\b.*"

# Greetings that can prefix a department/role name
_GREETINGS = r"(?:yo|hey|hi|hello|sup)"

# Department suffixes like "department", "team", "dept", "group"
_DEPT_SUFFIXES = r"(?:department|team|dept|group)"


# =============================================================================
# Result dataclass
# =============================================================================


@dataclass
class RoleMatch:
    """Result of routing a user message to a role.

    Attributes:
        role: The matched ``RoleDefinition``.
        confidence: How confident the router is in the match (0.0-1.0).
        reasoning: Brief explanation of why this role was selected.
    """

    role: RoleDefinition
    confidence: float
    reasoning: str


# =============================================================================
# Router
# =============================================================================


class RoleRouter:
    """Data-driven two-tier role router for conversation mode.

    At init time, builds regex patterns dynamically from the registry's
    roles and departments.  No hardcoded role IDs — adding or renaming
    roles only requires updating the YAML ``routing_keywords`` field.

    First attempts pattern matching against the dynamically built patterns.
    If no pattern matches, falls back to a Claude Haiku semantic call.

    Args:
        registry: The ``SkillRegistry`` containing loaded roles.
    """

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry
        self._log = logger.bind(component="role_router")
        # Build patterns from registry at init time
        self._patterns = self._build_patterns_from_registry()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def route(
        self,
        message: str,
        available_roles: list[RoleDefinition] | None = None,
    ) -> RoleMatch | None:
        """Route a user message to the best matching role.

        Args:
            message: The user's message text.
            available_roles: Optional explicit list of roles to consider.
                If ``None``, uses all roles from the registry.

        Returns:
            A ``RoleMatch`` if a role matches with sufficient confidence,
            otherwise ``None``.
        """
        roles = available_roles or self._registry.list_roles()
        if not roles:
            self._log.warning("route.no_roles", message_preview=message[:80])
            return None

        # Tier 1: Data-driven pattern matching
        explicit_match = self._match_explicit(message, roles)
        if explicit_match is not None:
            self._log.info(
                "route.explicit_match",
                role_id=explicit_match.role.id,
                confidence=explicit_match.confidence,
                message_preview=message[:80],
            )
            return explicit_match

        # Tier 2: Haiku semantic matching
        semantic_match = await self._match_semantic(message, roles)
        if semantic_match is not None:
            self._log.info(
                "route.semantic_match",
                role_id=semantic_match.role.id,
                confidence=semantic_match.confidence,
                reasoning=semantic_match.reasoning,
                message_preview=message[:80],
            )
            return semantic_match

        self._log.info(
            "route.no_match",
            message_preview=message[:80],
            num_roles=len(roles),
        )
        return None

    def route_by_id(self, role_id: str) -> RoleMatch | None:
        """Directly resolve a role by its ID (for slash commands).

        Args:
            role_id: The exact role ID to look up.

        Returns:
            A ``RoleMatch`` with confidence 1.0 if the role exists,
            otherwise ``None``.
        """
        role = self._registry.get_role(role_id)
        if role is None:
            return None
        return RoleMatch(
            role=role,
            confidence=1.0,
            reasoning=f"Direct role ID lookup: {role_id}",
        )

    def rebuild_patterns(self) -> None:
        """Rebuild routing patterns from the current registry state.

        Call this after modifying roles/departments in the registry
        (e.g., via the dynamic org chart) to pick up new keywords.
        """
        self._patterns = self._build_patterns_from_registry()
        self._log.info(
            "route.patterns_rebuilt",
            pattern_count=len(self._patterns),
        )

    # ------------------------------------------------------------------
    # Pattern building (data-driven)
    # ------------------------------------------------------------------

    def _build_patterns_from_registry(
        self,
    ) -> list[tuple[re.Pattern[str], str]]:
        """Build routing patterns dynamically from registry data.

        Generates patterns from:
        1. Role ``routing_keywords`` → "talk to the X" + direct mention
        2. Role ``name`` → auto-derived patterns
        3. Department ``routing_keywords`` → department mention patterns
           that route to the department's manager (or first role)
        4. Department ``name`` → greeting + department suffix patterns

        Longer/more-specific keyword patterns are added first to avoid
        shorter patterns matching prematurely (e.g., "head of IT"
        before "head").

        Returns:
            Ordered list of ``(compiled_pattern, role_id)`` tuples.
        """
        patterns: list[tuple[re.Pattern[str], str]] = []
        roles = self._registry.list_roles()
        departments = self._registry.list_departments()

        # -- Phase 1: Role-level patterns --
        # Sort roles so that multi-word keyword roles come first
        # (prevents shorter keywords from matching before longer ones)
        role_keyword_pairs: list[tuple[str, str]] = []
        for role in roles:
            for kw in role.routing_keywords:
                role_keyword_pairs.append((kw, role.id))
            # Auto-derive from role name if no keywords
            # (always add name-based patterns as fallback)
            role_keyword_pairs.append((role.name, role.id))

        # Sort by keyword length descending — longer keywords first
        role_keyword_pairs.sort(key=lambda x: len(x[0]), reverse=True)

        seen_patterns: set[str] = set()

        for keyword, role_id in role_keyword_pairs:
            kw_lower = keyword.strip().lower()
            if not kw_lower or kw_lower in seen_patterns:
                continue
            seen_patterns.add(kw_lower)

            # Escape for regex, allow flexible whitespace
            kw_re = re.escape(keyword.strip())
            kw_re = kw_re.replace(r"\ ", r"\s+")

            # "talk to the <keyword>"
            patterns.append(
                (
                    re.compile(_TALK_TO + r"\b" + kw_re + r"\b", re.I),
                    role_id,
                )
            )

            # Direct mention: "<keyword>" as subject
            patterns.append(
                (
                    re.compile(r"\b" + kw_re + r"\b", re.I),
                    role_id,
                )
            )

        # -- Phase 2: Department-level patterns --
        for dept in departments:
            manager_role_id = self._find_department_manager(dept, roles)
            if not manager_role_id:
                continue

            # Collect department keywords (explicit + name-derived)
            dept_keywords: list[str] = list(dept.routing_keywords)
            # Auto-derive from department name words
            # e.g. "Marketing Department" → "marketing"
            # e.g. "IT & Operations Department" → "IT", "Operations"
            for word in dept.name.split():
                word_clean = word.strip("&").strip()
                if (
                    word_clean.lower() not in ("department", "dept", "the", "and", "&", "")
                    and word_clean not in dept_keywords
                ):
                    dept_keywords.append(word_clean)

            for kw in dept_keywords:
                kw_lower = kw.strip().lower()
                dept_pattern_key = f"dept:{kw_lower}"
                if dept_pattern_key in seen_patterns:
                    continue
                seen_patterns.add(dept_pattern_key)

                kw_re = re.escape(kw.strip())
                kw_re = kw_re.replace(r"\ ", r"\s+")

                # "yo/hey <dept_keyword>" — greeting + department name
                patterns.append(
                    (
                        re.compile(
                            r"^" + _GREETINGS + r"\s+" + kw_re + r"\b",
                            re.I,
                        ),
                        manager_role_id,
                    )
                )

                # "<dept_keyword> department/team/dept/group"
                patterns.append(
                    (
                        re.compile(
                            r"\b" + kw_re + r"\s+" + _DEPT_SUFFIXES + r"\b",
                            re.I,
                        ),
                        manager_role_id,
                    )
                )

                # "talk to the <dept_keyword>"
                patterns.append(
                    (
                        re.compile(
                            _TALK_TO + r"\b" + kw_re + r"\b",
                            re.I,
                        ),
                        manager_role_id,
                    )
                )

        self._log.debug(
            "route.patterns_built",
            pattern_count=len(patterns),
            role_count=len(roles),
            dept_count=len(departments),
        )
        return patterns

    @staticmethod
    def _find_department_manager(
        dept: DepartmentDefinition,
        roles: list[RoleDefinition],
    ) -> str | None:
        """Find the manager role for a department.

        Looks for a role in this department that has a non-empty
        ``manages`` field.  If none found, returns the first role
        in the department (best effort).

        Returns:
            The manager role ID, or ``None`` if the department has no roles.
        """
        dept_roles = [r for r in roles if r.department_id == dept.id]
        if not dept_roles:
            return None

        # Prefer manager roles
        for role in dept_roles:
            if role.manages:
                return role.id

        # Fallback to first role in department
        return dept_roles[0].id

    # ------------------------------------------------------------------
    # Tier 1: Explicit pattern matching
    # ------------------------------------------------------------------

    def _match_explicit(
        self,
        message: str,
        roles: list[RoleDefinition],
    ) -> RoleMatch | None:
        """Try to match the message against data-driven regex patterns.

        Returns the first matching role with high confidence (0.95),
        or ``None`` if no pattern matches.
        """
        role_map = {r.id: r for r in roles}

        for pattern, role_id in self._patterns:
            if pattern.search(message):
                role = role_map.get(role_id)
                if role is not None:
                    return RoleMatch(
                        role=role,
                        confidence=0.95,
                        reasoning=f"Keyword match: '{pattern.pattern}'",
                    )

        return None

    # ------------------------------------------------------------------
    # Tier 2: Haiku semantic matching
    # ------------------------------------------------------------------

    async def _match_semantic(
        self,
        message: str,
        roles: list[RoleDefinition],
    ) -> RoleMatch | None:
        """Use Claude Haiku to semantically match the message to a role."""
        role_index = self._build_role_index(roles)
        routing_prompt = self._build_routing_prompt(message, role_index)

        response_text = await self._call_haiku(routing_prompt)
        if response_text is None:
            return None

        parsed = self._parse_response(response_text)
        if parsed is None:
            return None

        role_id = parsed.get("role_id", "")
        confidence = parsed.get("confidence", 0.0)
        reasoning = parsed.get("reasoning", "")

        # Validate confidence
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            self._log.warning(
                "route.invalid_confidence",
                raw_confidence=confidence,
            )
            return None

        confidence = max(0.0, min(1.0, confidence))

        # Look up role
        role_map = {r.id: r for r in roles}
        role = role_map.get(role_id)
        if role is None:
            self._log.warning(
                "route.unknown_role_id",
                role_id=role_id,
                available=[r.id for r in roles],
            )
            return None

        # Apply threshold
        if confidence < _CONFIDENCE_THRESHOLD:
            self._log.info(
                "route.low_confidence",
                role_id=role_id,
                confidence=confidence,
            )
            return None

        return RoleMatch(
            role=role,
            confidence=confidence,
            reasoning=reasoning,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_role_index(roles: list[RoleDefinition]) -> str:
        """Build a compact role index for the routing prompt.

        Format: ``role_id | name | description``
        """
        lines: list[str] = []
        for role in sorted(roles, key=lambda r: r.id):
            lines.append(f"{role.id} | {role.name} | {role.description}")
        return "\n".join(lines)

    @staticmethod
    def _build_routing_prompt(message: str, role_index: str) -> str:
        """Build the user-side routing prompt sent to Haiku."""
        return (
            "Available roles (format: role_id | name | description):\n"
            f"{role_index}\n\n"
            f"User message: {message}\n\n"
            "Which role should handle this conversation? Respond with JSON only."
        )

    async def _call_haiku(self, routing_prompt: str) -> str | None:
        """Call the LLM provider for role routing.

        Uses the hybrid router which may route to a cheaper external
        provider when configured, with automatic fallback to Claude.
        """
        try:
            result = await complete_with_fallback(
                task_type=TaskType.ROLE_ROUTING,
                system_prompt=ROLE_ROUTER_SYSTEM_PROMPT,
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
        """Parse the JSON response from Haiku."""
        text = response_text.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
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
