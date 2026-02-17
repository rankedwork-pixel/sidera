"""Tests for the multi-agent working group system.

Covers:
- Working group coordinator: plan parsing, validation, member descriptions
- MCP tools: form_working_group, get_working_group_status
- Context management: set/clear/get_pending
- DB service methods: create, get, update, save_member_result, list
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.skills.working_group import (
    MemberTaskResult,
    WorkingGroupPlan,
    WorkingGroupResult,
    build_member_descriptions,
    format_member_outputs,
    generate_group_id,
    parse_plan,
    validate_working_group_request,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _MockRole:
    id: str = "test_role"
    name: str = "Test Role"
    description: str = "A test role"
    persona: str = "You are a test role."
    department_id: str = "test_dept"
    manages: tuple[str, ...] = ()
    routing_keywords: tuple[str, ...] = ()


class _MockRegistry:
    def __init__(
        self,
        roles: dict[str, _MockRole] | None = None,
    ) -> None:
        self._roles = roles or {}

    def get_role(self, role_id: str) -> _MockRole | None:
        return self._roles.get(role_id)


# ===========================================================================
# 1. generate_group_id
# ===========================================================================


class TestGenerateGroupId:
    def test_starts_with_wg(self):
        gid = generate_group_id()
        assert gid.startswith("wg-")

    def test_unique_ids(self):
        ids = {generate_group_id() for _ in range(50)}
        assert len(ids) == 50

    def test_reasonable_length(self):
        gid = generate_group_id()
        assert 10 <= len(gid) <= 20


# ===========================================================================
# 2. validate_working_group_request
# ===========================================================================


class TestValidateRequest:
    def _registry(self) -> _MockRegistry:
        return _MockRegistry(
            {
                "mgr": _MockRole(
                    id="mgr",
                    name="Manager",
                    manages=("a", "b"),
                ),
                "a": _MockRole(id="a", name="Role A"),
                "b": _MockRole(id="b", name="Role B"),
            }
        )

    def test_valid_request(self):
        errors = validate_working_group_request(
            "mgr",
            ["a", "b"],
            "Analyze performance",
            self._registry(),
        )
        assert errors == []

    def test_empty_objective(self):
        errors = validate_working_group_request(
            "mgr",
            ["a"],
            "",
            self._registry(),
        )
        assert any("empty" in e.lower() for e in errors)

    def test_no_members(self):
        errors = validate_working_group_request(
            "mgr",
            [],
            "Objective",
            self._registry(),
        )
        assert any("no member" in e.lower() for e in errors)

    def test_coordinator_not_manager(self):
        errors = validate_working_group_request(
            "a",
            ["b"],
            "Objective",
            self._registry(),
        )
        assert any("not a manager" in e.lower() for e in errors)

    def test_coordinator_not_found(self):
        errors = validate_working_group_request(
            "nonexistent",
            ["a"],
            "Objective",
            self._registry(),
        )
        assert any("not found" in e.lower() for e in errors)

    def test_member_not_found(self):
        errors = validate_working_group_request(
            "mgr",
            ["a", "nonexistent"],
            "Objective",
            self._registry(),
        )
        assert any("nonexistent" in e for e in errors)

    def test_coordinator_in_members(self):
        errors = validate_working_group_request(
            "mgr",
            ["a", "mgr"],
            "Objective",
            self._registry(),
        )
        assert any("should not be in the member" in e.lower() for e in errors)

    def test_duplicate_members(self):
        errors = validate_working_group_request(
            "mgr",
            ["a", "a"],
            "Objective",
            self._registry(),
        )
        assert any("duplicate" in e.lower() for e in errors)

    def test_too_many_members(self):
        members = [f"role_{i}" for i in range(11)]
        reg = _MockRegistry(
            {
                "mgr": _MockRole(id="mgr", manages=("x",)),
                **{f"role_{i}": _MockRole(id=f"role_{i}") for i in range(11)},
            }
        )
        errors = validate_working_group_request("mgr", members, "X", reg)
        assert any("too many" in e.lower() for e in errors)


# ===========================================================================
# 3. build_member_descriptions
# ===========================================================================


class TestBuildMemberDescriptions:
    def test_formats_roles(self):
        roles = [
            _MockRole(id="analyst", name="Analyst", description="Analyzes data"),
            _MockRole(id="buyer", name="Buyer", description="Buys media"),
        ]
        result = build_member_descriptions(roles)
        assert "analyst" in result
        assert "Buyer" in result
        assert "Analyzes data" in result

    def test_empty_list(self):
        result = build_member_descriptions([])
        assert result == ""


# ===========================================================================
# 4. parse_plan
# ===========================================================================


class TestParsePlan:
    def test_parse_json(self):
        text = json.dumps(
            {
                "plan_summary": "Test plan",
                "assignments": [
                    {"role_id": "a", "task": "Do A", "priority": "high"},
                ],
            }
        )
        plan = parse_plan(text)
        assert plan.plan_summary == "Test plan"
        assert len(plan.assignments) == 1
        assert plan.assignments[0]["role_id"] == "a"

    def test_parse_with_code_fences(self):
        text = (
            "Here is the plan:\n```json\n"
            + json.dumps(
                {
                    "plan_summary": "Fenced plan",
                    "assignments": [],
                }
            )
            + "\n```"
        )
        plan = parse_plan(text)
        assert plan.plan_summary == "Fenced plan"

    def test_parse_with_surrounding_text(self):
        text = (
            "I'll create this plan: "
            + json.dumps(
                {
                    "plan_summary": "Embedded",
                    "assignments": [{"role_id": "x", "task": "T"}],
                }
            )
            + " That should work."
        )
        plan = parse_plan(text)
        assert plan.plan_summary == "Embedded"

    def test_invalid_json(self):
        plan = parse_plan("This is not JSON at all")
        assert "failed" in plan.plan_summary.lower()

    def test_empty_string(self):
        plan = parse_plan("")
        assert "failed" in plan.plan_summary.lower()


# ===========================================================================
# 5. format_member_outputs
# ===========================================================================


class TestFormatMemberOutputs:
    def test_formats_successful(self):
        results = [
            MemberTaskResult(
                role_id="a",
                task="Analyze X",
                output="X is good",
                cost_usd=0.1,
                success=True,
            ),
        ]
        text = format_member_outputs(results)
        assert "a" in text
        assert "Analyze X" in text
        assert "X is good" in text
        assert "completed" in text

    def test_formats_failed(self):
        results = [
            MemberTaskResult(
                role_id="b",
                task="Do Y",
                output="",
                success=False,
                error="Timed out",
            ),
        ]
        text = format_member_outputs(results)
        assert "FAILED" in text
        assert "Timed out" in text

    def test_multiple_results(self):
        results = [
            MemberTaskResult(role_id="a", task="T1", output="O1"),
            MemberTaskResult(role_id="b", task="T2", output="O2"),
        ]
        text = format_member_outputs(results)
        assert "a" in text and "b" in text
        assert "---" in text  # Separator


# ===========================================================================
# 6. Data structures
# ===========================================================================


class TestDataStructures:
    def test_working_group_plan_defaults(self):
        plan = WorkingGroupPlan()
        assert plan.plan_summary == ""
        assert plan.assignments == []

    def test_member_task_result_defaults(self):
        r = MemberTaskResult()
        assert r.role_id == ""
        assert r.success is True
        assert r.cost_usd == 0.0

    def test_working_group_result_defaults(self):
        r = WorkingGroupResult()
        assert r.group_id == ""
        assert r.success is True
        assert r.member_results == []
        assert r.synthesis == ""


# ===========================================================================
# 7. MCP tool: form_working_group
# ===========================================================================


class TestFormWorkingGroupTool:
    @pytest.mark.asyncio
    async def test_requires_context(self):
        from src.mcp_servers.working_group import (
            clear_working_group_context,
            form_working_group,
        )

        clear_working_group_context()
        result = await form_working_group(
            {
                "objective": "Test",
                "member_role_ids": ["a"],
            }
        )
        text = result["content"][0]["text"]
        assert result.get("is_error") is True
        assert "error" in text.lower() or "require" in text.lower()

    @pytest.mark.asyncio
    async def test_valid_proposal(self):
        from src.mcp_servers.working_group import (
            clear_working_group_context,
            form_working_group,
            get_pending_working_groups,
            set_working_group_context,
        )

        registry = _MockRegistry(
            {
                "mgr": _MockRole(id="mgr", manages=("a", "b")),
                "a": _MockRole(id="a"),
                "b": _MockRole(id="b"),
            }
        )
        set_working_group_context("mgr", registry)

        result = await form_working_group(
            {
                "objective": "Analyze Q4 performance",
                "member_role_ids": ["a", "b"],
            }
        )
        text = result["content"][0]["text"]
        assert "proposed" in text.lower()

        pending = get_pending_working_groups()
        assert len(pending) == 1
        assert pending[0]["objective"] == "Analyze Q4 performance"
        assert pending[0]["coordinator_role_id"] == "mgr"

        clear_working_group_context()

    @pytest.mark.asyncio
    async def test_validation_error(self):
        from src.mcp_servers.working_group import (
            clear_working_group_context,
            form_working_group,
            set_working_group_context,
        )

        registry = _MockRegistry(
            {
                "mgr": _MockRole(id="mgr", manages=("a",)),
            }
        )
        set_working_group_context("mgr", registry)

        result = await form_working_group(
            {
                "objective": "Test",
                "member_role_ids": ["nonexistent"],
            }
        )
        text = result["content"][0]["text"]
        assert "error" in text.lower() or "failed" in text.lower()

        clear_working_group_context()


# ===========================================================================
# 8. Context management
# ===========================================================================


class TestContextManagement:
    def test_set_and_clear(self):
        from src.mcp_servers.working_group import (
            clear_working_group_context,
            get_pending_working_groups,
            set_working_group_context,
        )

        registry = _MockRegistry({})
        set_working_group_context("mgr", registry)

        # Should start empty
        assert get_pending_working_groups() == []

        clear_working_group_context()

    def test_pending_groups_cleared_on_get(self):
        from src.mcp_servers.working_group import (
            _pending_groups_var,
            clear_working_group_context,
            get_pending_working_groups,
            set_working_group_context,
        )

        registry = _MockRegistry({})
        set_working_group_context("mgr", registry)

        _pending_groups_var.set([{"group_id": "wg-test"}])
        groups = get_pending_working_groups()
        assert len(groups) == 1

        # Should be cleared now
        groups2 = get_pending_working_groups()
        assert len(groups2) == 0

        clear_working_group_context()


# ===========================================================================
# 9. DB service methods
# ===========================================================================


class TestDBServiceMethods:
    @pytest.mark.asyncio
    async def test_create_working_group(self):
        from unittest.mock import AsyncMock

        from src.db.service import create_working_group

        session = AsyncMock()
        session.flush = AsyncMock()

        wg = await create_working_group(
            session,
            group_id="wg-test-123",
            objective="Analyze Q4",
            coordinator_role_id="head_of_marketing",
            member_role_ids=["analyst", "buyer"],
            initiated_by="user1",
        )

        assert wg.group_id == "wg-test-123"
        assert wg.objective == "Analyze Q4"
        assert wg.coordinator_role_id == "head_of_marketing"
        assert wg.member_role_ids == ["analyst", "buyer"]
        assert wg.status == "forming"
        session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_working_group(self):
        from src.db.service import get_working_group
        from src.models.schema import WorkingGroupSession

        mock_wg = WorkingGroupSession(
            group_id="wg-abc",
            objective="Test",
            coordinator_role_id="mgr",
            member_role_ids=["a"],
            status="executing",
        )

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_wg
        session.execute = AsyncMock(return_value=mock_result)

        result = await get_working_group(session, "wg-abc")
        assert result is not None
        assert result.group_id == "wg-abc"

    @pytest.mark.asyncio
    async def test_get_working_group_not_found(self):
        from src.db.service import get_working_group

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        result = await get_working_group(session, "nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_working_group_status(self):
        from src.db.service import update_working_group_status
        from src.models.schema import WorkingGroupSession

        mock_wg = WorkingGroupSession(
            group_id="wg-upd",
            objective="Test",
            coordinator_role_id="mgr",
            member_role_ids=["a"],
            status="forming",
        )

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_wg
        session.execute = AsyncMock(return_value=mock_result)
        session.flush = AsyncMock()

        result = await update_working_group_status(
            session,
            "wg-upd",
            "executing",
        )
        assert result is not None
        assert result.status == "executing"

    @pytest.mark.asyncio
    async def test_save_member_result(self):
        from src.db.service import save_member_result
        from src.models.schema import WorkingGroupSession

        mock_wg = WorkingGroupSession(
            group_id="wg-mbr",
            objective="Test",
            coordinator_role_id="mgr",
            member_role_ids=["a", "b"],
            status="executing",
            member_results_json={},
            total_cost_usd=0.0,
        )

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_wg
        session.execute = AsyncMock(return_value=mock_result)
        session.flush = AsyncMock()

        result = await save_member_result(
            session,
            "wg-mbr",
            "a",
            "Analysis complete",
            0.15,
        )
        assert result is not None
        assert "a" in result.member_results_json
        assert result.member_results_json["a"]["output"] == "Analysis complete"
        assert float(result.total_cost_usd) == 0.15

    @pytest.mark.asyncio
    async def test_list_working_groups(self):
        from src.db.service import list_working_groups
        from src.models.schema import WorkingGroupSession

        wg1 = WorkingGroupSession(
            group_id="wg-1",
            status="completed",
            coordinator_role_id="mgr",
        )
        wg2 = WorkingGroupSession(
            group_id="wg-2",
            status="forming",
            coordinator_role_id="mgr",
        )

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [wg1, wg2]
        session.execute = AsyncMock(return_value=mock_result)

        results = await list_working_groups(session)
        assert len(results) == 2
