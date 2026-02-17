"""Tests for the data-driven role router.

The router builds regex patterns dynamically from ``routing_keywords``
on roles and departments.  No hardcoded role IDs — adding or renaming
roles requires only a YAML change.

Covers:
- Data-driven pattern matching for direct role references
- Department-level routing (marketing department → head_of_marketing)
- Greeting + department routing ("yo marketing" → head_of_marketing)
- IT routing via routing_keywords
- Semantic fallback with confidence threshold
- Edge cases (no roles, unknown role, low confidence)
- Pattern rebuild after registry changes
- Dynamic role addition (no code changes needed)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.skills.role_router import RoleRouter
from src.skills.schema import DepartmentDefinition, RoleDefinition

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_role(
    role_id: str,
    name: str = "",
    department_id: str = "marketing",
    description: str = "",
    routing_keywords: tuple[str, ...] = (),
    **kwargs,
) -> RoleDefinition:
    """Create a minimal RoleDefinition for testing."""
    return RoleDefinition(
        id=role_id,
        name=name or role_id.replace("_", " ").title(),
        department_id=department_id,
        description=description or f"Handles {role_id} tasks.",
        routing_keywords=routing_keywords,
        **kwargs,
    )


def _make_dept(
    dept_id: str,
    name: str = "",
    description: str = "",
    routing_keywords: tuple[str, ...] = (),
) -> DepartmentDefinition:
    """Create a minimal DepartmentDefinition for testing."""
    return DepartmentDefinition(
        id=dept_id,
        name=name or dept_id.replace("_", " ").title(),
        description=description or f"The {dept_id} department.",
        routing_keywords=routing_keywords,
    )


# Standard set of roles with routing_keywords
_MARKETING_ROLES = [
    _make_role(
        "performance_media_buyer",
        name="Performance Media Buyer",
        description="Manages ad campaigns",
        routing_keywords=("media buyer", "media buying", "performance media", "ad buyer"),
    ),
    _make_role(
        "reporting_analyst",
        name="Reporting Analyst",
        description="Produces reports and dashboards",
        routing_keywords=("reporting analyst", "analyst", "reporting", "reports"),
    ),
    _make_role(
        "strategist",
        name="Marketing Strategist",
        description="Develops marketing strategy",
        routing_keywords=("strategist", "strategy", "strategic"),
    ),
    _make_role(
        "head_of_marketing",
        name="Head of Marketing",
        description="Leads the marketing department",
        routing_keywords=(
            "head of marketing",
            "marketing head",
            "marketing director",
            "marketing lead",
            "marketing manager",
        ),
        manages=("performance_media_buyer", "reporting_analyst", "strategist"),
    ),
]

_IT_ROLES = [
    _make_role(
        "head_of_it",
        name="Head of IT",
        department_id="it",
        description="Leads the IT department",
        routing_keywords=(
            "head of IT",
            "IT head",
            "IT manager",
            "IT director",
            "IT lead",
            "sysadmin",
            "system admin",
            "system administrator",
        ),
    ),
]

_ALL_ROLES = _MARKETING_ROLES + _IT_ROLES

# Departments
_MARKETING_DEPT = _make_dept(
    "marketing",
    name="Marketing Department",
    description="Manages all paid acquisition, brand marketing, and reporting",
    routing_keywords=("marketing",),
)

_IT_DEPT = _make_dept(
    "it",
    name="IT & Operations Department",
    description="Manages system health, infrastructure monitoring, and diagnostics",
    routing_keywords=("IT", "devops", "ops", "infrastructure"),
)

_ALL_DEPTS = [_MARKETING_DEPT, _IT_DEPT]


def _mock_registry(
    roles: list[RoleDefinition] | None = None,
    departments: list[DepartmentDefinition] | None = None,
) -> MagicMock:
    """Create a mock SkillRegistry that returns the given roles and departments."""
    registry = MagicMock()
    role_list = roles if roles is not None else _ALL_ROLES
    dept_list = departments if departments is not None else _ALL_DEPTS
    registry.list_roles.return_value = role_list
    registry.list_departments.return_value = dept_list
    role_map = {r.id: r for r in role_list}
    registry.get_role.side_effect = lambda rid: role_map.get(rid)
    dept_map = {d.id: d for d in dept_list}
    registry.get_department.side_effect = lambda did: dept_map.get(did)
    return registry


# ===========================================================================
# 1. Explicit pattern matching — direct role references
# ===========================================================================


class TestExplicitRolePatterns:
    """Verify data-driven patterns match common role references."""

    @pytest.mark.asyncio
    async def test_talk_to_strategist(self):
        """'talk to the strategist' should match strategist."""
        router = RoleRouter(_mock_registry())
        match = await router.route("talk to the strategist")
        assert match is not None
        assert match.role.id == "strategist"
        assert match.confidence == 0.95

    @pytest.mark.asyncio
    async def test_chat_with_media_buyer(self):
        """'chat with the media buyer' should match performance_media_buyer."""
        router = RoleRouter(_mock_registry())
        match = await router.route("chat with the media buyer")
        assert match is not None
        assert match.role.id == "performance_media_buyer"
        assert match.confidence == 0.95

    @pytest.mark.asyncio
    async def test_ask_the_analyst(self):
        """'ask the analyst' should match reporting_analyst."""
        router = RoleRouter(_mock_registry())
        match = await router.route("ask the analyst about ROAS")
        assert match is not None
        assert match.role.id == "reporting_analyst"

    @pytest.mark.asyncio
    async def test_head_of_marketing_keyword(self):
        """'head of marketing' as a keyword should match."""
        router = RoleRouter(_mock_registry())
        match = await router.route("I need the head of marketing")
        assert match is not None
        assert match.role.id == "head_of_marketing"

    @pytest.mark.asyncio
    async def test_sysadmin_keyword_routes_to_head_of_it(self):
        """'sysadmin' keyword should route to head_of_it."""
        router = RoleRouter(_mock_registry())
        match = await router.route("get the sysadmin")
        assert match is not None
        assert match.role.id == "head_of_it"

    @pytest.mark.asyncio
    async def test_head_of_it_keyword(self):
        """'head of IT' keyword should route to head_of_it."""
        router = RoleRouter(_mock_registry())
        match = await router.route("talk to the head of IT")
        assert match is not None
        assert match.role.id == "head_of_it"

    @pytest.mark.asyncio
    async def test_system_admin_keyword(self):
        """'system admin' keyword should route to head_of_it."""
        router = RoleRouter(_mock_registry())
        match = await router.route("need the system admin")
        assert match is not None
        assert match.role.id == "head_of_it"

    @pytest.mark.asyncio
    async def test_strategy_keyword(self):
        """'strategy' keyword should route to strategist."""
        router = RoleRouter(_mock_registry())
        match = await router.route("let's talk strategy")
        assert match is not None
        assert match.role.id == "strategist"

    @pytest.mark.asyncio
    async def test_reports_keyword(self):
        """'reports' keyword should route to reporting_analyst."""
        router = RoleRouter(_mock_registry())
        match = await router.route("I need the latest reports")
        assert match is not None
        assert match.role.id == "reporting_analyst"

    @pytest.mark.asyncio
    async def test_role_name_match(self):
        """Role name 'Marketing Strategist' should match."""
        router = RoleRouter(_mock_registry())
        match = await router.route("talk to the Marketing Strategist")
        assert match is not None
        assert match.role.id == "strategist"


# ===========================================================================
# 2. Department-level routing
# ===========================================================================


class TestDepartmentRouting:
    """Verify department mentions route to the department head."""

    @pytest.mark.asyncio
    async def test_marketing_department(self):
        """'marketing department' should route to head_of_marketing."""
        router = RoleRouter(_mock_registry())
        match = await router.route("yo marketing department")
        assert match is not None
        assert match.role.id == "head_of_marketing"
        assert match.confidence == 0.95

    @pytest.mark.asyncio
    async def test_marketing_team(self):
        """'marketing team' should route to head_of_marketing."""
        router = RoleRouter(_mock_registry())
        match = await router.route("I need the marketing team")
        assert match is not None
        assert match.role.id == "head_of_marketing"

    @pytest.mark.asyncio
    async def test_marketing_dept(self):
        """'marketing dept' should route to head_of_marketing."""
        router = RoleRouter(_mock_registry())
        match = await router.route("marketing dept can you help?")
        assert match is not None
        assert match.role.id == "head_of_marketing"

    @pytest.mark.asyncio
    async def test_marketing_group(self):
        """'marketing group' should route to head_of_marketing."""
        router = RoleRouter(_mock_registry())
        match = await router.route("hey marketing group")
        assert match is not None
        assert match.role.id == "head_of_marketing"

    @pytest.mark.asyncio
    async def test_yo_marketing(self):
        """'yo marketing' (greeting + department) should route to head."""
        router = RoleRouter(_mock_registry())
        match = await router.route("yo marketing")
        assert match is not None
        assert match.role.id == "head_of_marketing"

    @pytest.mark.asyncio
    async def test_hey_marketing(self):
        """'hey marketing' should route to head_of_marketing."""
        router = RoleRouter(_mock_registry())
        match = await router.route("hey marketing")
        assert match is not None
        assert match.role.id == "head_of_marketing"

    @pytest.mark.asyncio
    async def test_hello_marketing(self):
        """'hello marketing' should route to head_of_marketing."""
        router = RoleRouter(_mock_registry())
        match = await router.route("hello marketing")
        assert match is not None
        assert match.role.id == "head_of_marketing"

    @pytest.mark.asyncio
    async def test_sup_marketing(self):
        """'sup marketing' should route to head_of_marketing."""
        router = RoleRouter(_mock_registry())
        match = await router.route("sup marketing")
        assert match is not None
        assert match.role.id == "head_of_marketing"

    @pytest.mark.asyncio
    async def test_it_department(self):
        """'IT department' should route to head_of_it."""
        router = RoleRouter(_mock_registry())
        match = await router.route("IT department help me")
        assert match is not None
        assert match.role.id == "head_of_it"

    @pytest.mark.asyncio
    async def test_it_team(self):
        """'IT team' should route to head_of_it."""
        router = RoleRouter(_mock_registry())
        match = await router.route("need the IT team")
        assert match is not None
        assert match.role.id == "head_of_it"

    @pytest.mark.asyncio
    async def test_yo_it(self):
        """'yo IT' should route to head_of_it."""
        router = RoleRouter(_mock_registry())
        match = await router.route("yo IT")
        assert match is not None
        assert match.role.id == "head_of_it"

    @pytest.mark.asyncio
    async def test_hey_it(self):
        """'hey IT' should route to head_of_it."""
        router = RoleRouter(_mock_registry())
        match = await router.route("hey IT")
        assert match is not None
        assert match.role.id == "head_of_it"


# ===========================================================================
# 3. Semantic fallback
# ===========================================================================


class TestSemanticFallback:
    """Verify Haiku semantic routing works for non-pattern messages."""

    @pytest.mark.asyncio
    @patch("src.skills.role_router.RoleRouter._call_haiku")
    async def test_semantic_match_above_threshold(self, mock_haiku):
        """Semantic match above threshold should return the role."""
        mock_haiku.return_value = (
            '{"role_id": "performance_media_buyer", '
            '"confidence": 0.8, '
            '"reasoning": "Query about ad campaign ROAS"}'
        )
        router = RoleRouter(_mock_registry())
        match = await router.route("what is ROAS for campaign X?")
        assert match is not None
        assert match.role.id == "performance_media_buyer"
        assert match.confidence == 0.8

    @pytest.mark.asyncio
    @patch("src.skills.role_router.RoleRouter._call_haiku")
    async def test_semantic_match_below_threshold(self, mock_haiku):
        """Semantic match below threshold should return None."""
        mock_haiku.return_value = (
            '{"role_id": "strategist", "confidence": 0.3, "reasoning": "Vague greeting"}'
        )
        router = RoleRouter(_mock_registry())
        match = await router.route("what's up")
        assert match is None

    @pytest.mark.asyncio
    @patch("src.skills.role_router.RoleRouter._call_haiku")
    async def test_semantic_api_error(self, mock_haiku):
        """API error in semantic routing should return None."""
        mock_haiku.return_value = None
        router = RoleRouter(_mock_registry())
        match = await router.route("some random question")
        assert match is None

    @pytest.mark.asyncio
    @patch("src.skills.role_router.RoleRouter._call_haiku")
    async def test_semantic_invalid_json(self, mock_haiku):
        """Invalid JSON from Haiku should return None."""
        mock_haiku.return_value = "not valid json"
        router = RoleRouter(_mock_registry())
        match = await router.route("something")
        assert match is None

    @pytest.mark.asyncio
    @patch("src.skills.role_router.RoleRouter._call_haiku")
    async def test_semantic_unknown_role_id(self, mock_haiku):
        """Unknown role_id from Haiku should return None."""
        mock_haiku.return_value = (
            '{"role_id": "nonexistent_role", "confidence": 0.9, "reasoning": "test"}'
        )
        router = RoleRouter(_mock_registry())
        match = await router.route("something specific")
        assert match is None


# ===========================================================================
# 4. Edge cases
# ===========================================================================


class TestEdgeCases:
    """Verify edge case handling."""

    @pytest.mark.asyncio
    async def test_no_roles_returns_none(self):
        """Router with no roles should return None."""
        registry = _mock_registry(roles=[], departments=[])
        router = RoleRouter(registry)
        match = await router.route("hello")
        assert match is None

    def test_route_by_id_found(self):
        """route_by_id should return role with confidence 1.0."""
        router = RoleRouter(_mock_registry())
        match = router.route_by_id("strategist")
        assert match is not None
        assert match.role.id == "strategist"
        assert match.confidence == 1.0

    def test_route_by_id_not_found(self):
        """route_by_id with unknown ID should return None."""
        router = RoleRouter(_mock_registry())
        match = router.route_by_id("nonexistent")
        assert match is None

    @pytest.mark.asyncio
    async def test_case_insensitive_patterns(self):
        """Patterns should be case insensitive."""
        router = RoleRouter(_mock_registry())
        match = await router.route("TALK TO THE STRATEGIST")
        assert match is not None
        assert match.role.id == "strategist"

    @pytest.mark.asyncio
    async def test_case_insensitive_department(self):
        """Department patterns should be case insensitive."""
        router = RoleRouter(_mock_registry())
        match = await router.route("MARKETING DEPARTMENT")
        assert match is not None
        assert match.role.id == "head_of_marketing"


# ===========================================================================
# 5. Data-driven behavior — dynamic role addition
# ===========================================================================


class TestDataDrivenBehavior:
    """Verify that adding roles/departments dynamically works
    without any code changes — only YAML/registry updates needed."""

    @pytest.mark.asyncio
    async def test_new_role_routable_via_keywords(self):
        """A new role with routing_keywords should be routable immediately."""
        new_role = _make_role(
            "social_media_manager",
            name="Social Media Manager",
            department_id="marketing",
            description="Manages social media presence",
            routing_keywords=("social media", "social manager", "social"),
        )
        roles = _ALL_ROLES + [new_role]
        registry = _mock_registry(roles=roles)
        router = RoleRouter(registry)

        match = await router.route("talk to the social media manager")
        assert match is not None
        assert match.role.id == "social_media_manager"

    @pytest.mark.asyncio
    async def test_new_role_routable_via_name(self):
        """A new role without routing_keywords is still routable via name."""
        new_role = _make_role(
            "brand_manager",
            name="Brand Manager",
            department_id="marketing",
            description="Manages brand identity",
            # No routing_keywords — name-based fallback
        )
        roles = _ALL_ROLES + [new_role]
        registry = _mock_registry(roles=roles)
        router = RoleRouter(registry)

        match = await router.route("I need the Brand Manager")
        assert match is not None
        assert match.role.id == "brand_manager"

    @pytest.mark.asyncio
    async def test_new_department_greeting_routing(self):
        """A new department with routing_keywords should support greeting routing."""
        new_dept = _make_dept(
            "finance",
            name="Finance Department",
            description="Manages budgets and financial planning",
            routing_keywords=("finance",),
        )
        new_role = _make_role(
            "cfo",
            name="CFO",
            department_id="finance",
            description="Chief Financial Officer",
            manages=("accountant",),
            routing_keywords=("cfo", "chief financial officer"),
        )
        roles = _ALL_ROLES + [new_role]
        depts = _ALL_DEPTS + [new_dept]
        registry = _mock_registry(roles=roles, departments=depts)
        router = RoleRouter(registry)

        # "yo finance" should route to the finance dept manager
        match = await router.route("yo finance")
        assert match is not None
        assert match.role.id == "cfo"

    @pytest.mark.asyncio
    async def test_new_department_suffix_routing(self):
        """A new department should support '<dept> department/team/group'."""
        new_dept = _make_dept(
            "sales",
            name="Sales Department",
            description="Revenue generation",
            routing_keywords=("sales",),
        )
        new_role = _make_role(
            "vp_sales",
            name="VP of Sales",
            department_id="sales",
            description="Leads the sales department",
            manages=("sales_rep",),
            routing_keywords=("vp of sales", "sales lead"),
        )
        roles = _ALL_ROLES + [new_role]
        depts = _ALL_DEPTS + [new_dept]
        registry = _mock_registry(roles=roles, departments=depts)
        router = RoleRouter(registry)

        match = await router.route("I need the sales team")
        assert match is not None
        assert match.role.id == "vp_sales"

    @pytest.mark.asyncio
    async def test_rebuild_patterns_picks_up_changes(self):
        """rebuild_patterns() should pick up new registry state."""
        registry = _mock_registry()
        router = RoleRouter(registry)

        # Initially no "sales" role
        match = await router.route("talk to the sales lead")
        # Should not match any existing role with "sales lead"
        assert match is None or match.role.id != "sales_lead"

        # Add a new role and rebuild
        new_role = _make_role(
            "sales_lead",
            name="Sales Lead",
            department_id="marketing",
            description="Leads sales effort",
            routing_keywords=("sales lead", "sales"),
        )
        new_roles = _ALL_ROLES + [new_role]
        registry.list_roles.return_value = new_roles
        role_map = {r.id: r for r in new_roles}
        registry.get_role.side_effect = lambda rid: role_map.get(rid)
        router.rebuild_patterns()

        match = await router.route("talk to the sales lead")
        assert match is not None
        assert match.role.id == "sales_lead"

    @pytest.mark.asyncio
    async def test_longer_keywords_match_first(self):
        """Longer keywords should take priority over shorter ones."""
        router = RoleRouter(_mock_registry())

        # "head of marketing" (longer) should match head_of_marketing,
        # not just "head" matching something else
        match = await router.route("head of marketing")
        assert match is not None
        assert match.role.id == "head_of_marketing"

    @pytest.mark.asyncio
    async def test_head_of_it_not_confused_with_head_of_marketing(self):
        """'talk to the head of IT' should route to head_of_it, not marketing."""
        router = RoleRouter(_mock_registry())
        match = await router.route("talk to the head of IT")
        assert match is not None
        assert match.role.id == "head_of_it"

    def test_pattern_count_matches_registry(self):
        """Pattern count should reflect the registry contents."""
        registry = _mock_registry()
        router = RoleRouter(registry)
        # Should have patterns — exact count depends on keywords
        assert len(router._patterns) > 0

    @pytest.mark.asyncio
    async def test_dept_without_manager_falls_back_to_first_role(self):
        """Department routing with no manager should use the first role."""
        dept = _make_dept("research", name="Research", routing_keywords=("research",))
        role = _make_role(
            "researcher",
            name="Researcher",
            department_id="research",
            description="Conducts research",
            # No manages field — not a manager
        )
        registry = _mock_registry(roles=[role], departments=[dept])
        router = RoleRouter(registry)

        match = await router.route("yo research")
        assert match is not None
        assert match.role.id == "researcher"
