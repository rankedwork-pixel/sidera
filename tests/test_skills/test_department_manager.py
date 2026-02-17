"""Tests for DepartmentExecutor manager-role handling.

Verifies that DepartmentExecutor correctly:
- Identifies manager roles via registry.list_managers()
- Delegates manager roles to ManagerExecutor.execute_manager()
- Skips managed sub-roles to prevent double-execution
- Runs remaining non-managed roles via RoleExecutor.execute_role()
- Maintains backward compatibility when no manager_executor is provided
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.skills.executor import (
    DepartmentExecutor,
    DepartmentNotFoundError,
    DepartmentResult,
    RoleExecutor,
    RoleResult,
)
from src.skills.schema import DepartmentDefinition, RoleDefinition

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dept(
    dept_id: str = "marketing",
    name: str = "Marketing",
) -> DepartmentDefinition:
    return DepartmentDefinition(
        id=dept_id,
        name=name,
        description=f"{name} department",
    )


def _make_role(
    role_id: str,
    department_id: str = "marketing",
    name: str | None = None,
    manages: tuple[str, ...] = (),
) -> RoleDefinition:
    return RoleDefinition(
        id=role_id,
        name=name or role_id.replace("_", " ").title(),
        department_id=department_id,
        description=f"Role {role_id}",
        briefing_skills=("skill_a",) if not manages else (),
        manages=manages,
    )


def _make_role_result(
    role_id: str,
    department_id: str = "marketing",
    cost_usd: float = 0.10,
) -> RoleResult:
    return RoleResult(
        role_id=role_id,
        department_id=department_id,
        user_id="user_1",
        combined_output=f"Output from {role_id}",
        total_cost={
            "total_cost_usd": cost_usd,
            "num_turns": 3,
            "duration_ms": 1000,
        },
        session_id="sess_test",
    )


def _build_executor(
    dept: DepartmentDefinition,
    roles: list[RoleDefinition],
    manager_executor: object | None = None,
) -> tuple[DepartmentExecutor, AsyncMock, MagicMock]:
    """Build a DepartmentExecutor with mocked role_executor and registry.

    Returns:
        (dept_executor, mock_role_executor_execute_role, mock_registry)
    """
    mock_registry = MagicMock()
    mock_registry.get_department.return_value = dept
    mock_registry.list_roles.return_value = roles

    # Compute managers from the role list
    managers = [r for r in roles if r.manages]
    mock_registry.list_managers.return_value = managers

    mock_role_executor = MagicMock(spec=RoleExecutor)
    mock_role_executor.execute_role = AsyncMock()

    executor = DepartmentExecutor(
        role_executor=mock_role_executor,
        registry=mock_registry,
        manager_executor=manager_executor,
    )
    return executor, mock_role_executor.execute_role, mock_registry


_ACCOUNTS = [{"platform": "meta", "account_id": "act_1"}]


# ===========================================================================
# 1. Backward compatibility: no manager_executor provided
# ===========================================================================


class TestBackwardCompat:
    """When no manager_executor is provided, all roles run normally."""

    async def test_no_manager_executor_runs_all_roles(self):
        """Without manager_executor, every role is run via RoleExecutor."""
        dept = _make_dept()
        mgr_role = _make_role("mgr", manages=("worker",))
        worker_role = _make_role("worker")
        indie_role = _make_role("indie")

        executor, mock_exec_role, _ = _build_executor(
            dept=dept,
            roles=[mgr_role, worker_role, indie_role],
            manager_executor=None,
        )

        # Every call returns a result
        mock_exec_role.side_effect = lambda **kw: _make_role_result(
            kw["role_id"],
        )

        result = await executor.execute_department(
            department_id="marketing",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        assert isinstance(result, DepartmentResult)
        # All three roles should have been run
        assert mock_exec_role.await_count == 3
        called_ids = {c.kwargs["role_id"] for c in mock_exec_role.call_args_list}
        assert called_ids == {"mgr", "worker", "indie"}

    async def test_no_manager_executor_logs_warning(self):
        """A warning is logged when managers exist but no executor is given."""
        dept = _make_dept()
        mgr_role = _make_role("mgr", manages=("worker",))
        worker_role = _make_role("worker")

        executor, mock_exec_role, _ = _build_executor(
            dept=dept,
            roles=[mgr_role, worker_role],
            manager_executor=None,
        )
        mock_exec_role.side_effect = lambda **kw: _make_role_result(
            kw["role_id"],
        )

        # Should not raise — just logs warning and runs all normally
        result = await executor.execute_department(
            department_id="marketing",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )
        assert len(result.role_results) == 2


# ===========================================================================
# 2. Managers identified and run via ManagerExecutor
# ===========================================================================


class TestManagerExecution:
    """Manager roles are run via ManagerExecutor.execute_manager()."""

    async def test_manager_run_via_manager_executor(self):
        """Manager roles are delegated to manager_executor.execute_manager."""
        dept = _make_dept()
        mgr_role = _make_role("mgr", manages=("worker",))
        worker_role = _make_role("worker")

        mock_mgr_executor = MagicMock()
        mgr_result = _make_role_result("mgr")
        mock_mgr_executor.execute_manager = AsyncMock(return_value=mgr_result)

        executor, mock_exec_role, _ = _build_executor(
            dept=dept,
            roles=[mgr_role, worker_role],
            manager_executor=mock_mgr_executor,
        )

        result = await executor.execute_department(
            department_id="marketing",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        # ManagerExecutor should have been called once for the manager
        mock_mgr_executor.execute_manager.assert_awaited_once()
        mgr_call = mock_mgr_executor.execute_manager.call_args
        assert mgr_call.kwargs["role_id"] == "mgr"
        assert mgr_call.kwargs["user_id"] == "user_1"
        assert mgr_call.kwargs["accounts"] == _ACCOUNTS

        # The manager result should appear in results
        assert mgr_result in result.role_results


# ===========================================================================
# 3. Managed roles are NOT double-executed
# ===========================================================================


class TestNoDoubleExecution:
    """Roles managed by a manager must not be run independently."""

    async def test_managed_role_not_run_via_role_executor(self):
        """A role in a manager's 'manages' list is skipped by RoleExecutor."""
        dept = _make_dept()
        mgr_role = _make_role("mgr", manages=("worker_a", "worker_b"))
        worker_a = _make_role("worker_a")
        worker_b = _make_role("worker_b")
        indie = _make_role("indie")

        mock_mgr_executor = MagicMock()
        mock_mgr_executor.execute_manager = AsyncMock(
            return_value=_make_role_result("mgr"),
        )

        executor, mock_exec_role, _ = _build_executor(
            dept=dept,
            roles=[mgr_role, worker_a, worker_b, indie],
            manager_executor=mock_mgr_executor,
        )
        mock_exec_role.side_effect = lambda **kw: _make_role_result(
            kw["role_id"],
        )

        result = await executor.execute_department(
            department_id="marketing",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        # Only indie should be run via RoleExecutor
        assert mock_exec_role.await_count == 1
        assert mock_exec_role.call_args.kwargs["role_id"] == "indie"

        # Manager was run via ManagerExecutor
        mock_mgr_executor.execute_manager.assert_awaited_once()

        # Total results: 1 from manager + 1 from indie = 2
        assert len(result.role_results) == 2


# ===========================================================================
# 4. Non-managed roles still run via RoleExecutor
# ===========================================================================


class TestNonManagedRoles:
    """Roles not covered by any manager run normally via RoleExecutor."""

    async def test_independent_roles_run_normally(self):
        """Roles outside any manager's manages list run via RoleExecutor."""
        dept = _make_dept()
        mgr_role = _make_role("mgr", manages=("worker",))
        worker = _make_role("worker")
        analyst = _make_role("analyst")
        strategist = _make_role("strategist")

        mock_mgr_executor = MagicMock()
        mock_mgr_executor.execute_manager = AsyncMock(
            return_value=_make_role_result("mgr"),
        )

        executor, mock_exec_role, _ = _build_executor(
            dept=dept,
            roles=[mgr_role, worker, analyst, strategist],
            manager_executor=mock_mgr_executor,
        )
        mock_exec_role.side_effect = lambda **kw: _make_role_result(
            kw["role_id"],
        )

        result = await executor.execute_department(
            department_id="marketing",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        # analyst and strategist run via RoleExecutor
        called_ids = {c.kwargs["role_id"] for c in mock_exec_role.call_args_list}
        assert called_ids == {"analyst", "strategist"}

        # Total: manager(1) + analyst + strategist = 3
        assert len(result.role_results) == 3


# ===========================================================================
# 5. Mixed department: some managed, some not
# ===========================================================================


class TestMixedDepartment:
    """Department with multiple managers and independent roles."""

    async def test_two_managers_and_independent_roles(self):
        """Two managers handle disjoint sets; remaining roles run normally."""
        dept = _make_dept()
        mgr_a = _make_role("mgr_a", manages=("w1", "w2"))
        mgr_b = _make_role("mgr_b", manages=("w3",))
        w1 = _make_role("w1")
        w2 = _make_role("w2")
        w3 = _make_role("w3")
        indie = _make_role("indie")

        mock_mgr_executor = MagicMock()
        mock_mgr_executor.execute_manager = AsyncMock(
            side_effect=lambda **kw: _make_role_result(kw["role_id"]),
        )

        executor, mock_exec_role, _ = _build_executor(
            dept=dept,
            roles=[mgr_a, mgr_b, w1, w2, w3, indie],
            manager_executor=mock_mgr_executor,
        )
        mock_exec_role.side_effect = lambda **kw: _make_role_result(
            kw["role_id"],
        )

        result = await executor.execute_department(
            department_id="marketing",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        # Two managers run via ManagerExecutor
        assert mock_mgr_executor.execute_manager.await_count == 2
        mgr_called = {c.kwargs["role_id"] for c in mock_mgr_executor.execute_manager.call_args_list}
        assert mgr_called == {"mgr_a", "mgr_b"}

        # Only indie runs via RoleExecutor (w1, w2, w3 are managed)
        assert mock_exec_role.await_count == 1
        assert mock_exec_role.call_args.kwargs["role_id"] == "indie"

        # Total results: mgr_a + mgr_b + indie = 3
        assert len(result.role_results) == 3


# ===========================================================================
# 6. Manager with missing sub-role (not in dept)
# ===========================================================================


class TestManagerMissingSubRole:
    """Manager references a role that does not exist in the department."""

    async def test_missing_managed_role_still_skipped(self):
        """If a manager manages a role not in the dept, execution proceeds.

        The missing role ID is still in the managed set, so it won't be
        run independently (it can't be — it doesn't exist). But this
        should not cause an error.
        """
        dept = _make_dept()
        # mgr manages "ghost" which isn't a real role in the department
        mgr_role = _make_role("mgr", manages=("ghost",))
        indie = _make_role("indie")

        mock_mgr_executor = MagicMock()
        mock_mgr_executor.execute_manager = AsyncMock(
            return_value=_make_role_result("mgr"),
        )

        # list_roles only returns mgr + indie (no "ghost")
        executor, mock_exec_role, _ = _build_executor(
            dept=dept,
            roles=[mgr_role, indie],
            manager_executor=mock_mgr_executor,
        )
        mock_exec_role.side_effect = lambda **kw: _make_role_result(
            kw["role_id"],
        )

        result = await executor.execute_department(
            department_id="marketing",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        # Manager executed
        mock_mgr_executor.execute_manager.assert_awaited_once()

        # indie runs via RoleExecutor
        assert mock_exec_role.await_count == 1

        # Total: mgr + indie = 2
        assert len(result.role_results) == 2


# ===========================================================================
# 7. Department with no managers (all regular roles)
# ===========================================================================


class TestNoManagers:
    """Department with no manager roles — pure regular execution."""

    async def test_all_roles_run_normally(self):
        """When no managers exist, all roles run via RoleExecutor."""
        dept = _make_dept()
        role_a = _make_role("role_a")
        role_b = _make_role("role_b")
        role_c = _make_role("role_c")

        mock_mgr_executor = MagicMock()
        mock_mgr_executor.execute_manager = AsyncMock()

        executor, mock_exec_role, _ = _build_executor(
            dept=dept,
            roles=[role_a, role_b, role_c],
            manager_executor=mock_mgr_executor,
        )
        mock_exec_role.side_effect = lambda **kw: _make_role_result(
            kw["role_id"],
        )

        result = await executor.execute_department(
            department_id="marketing",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        # No manager calls
        mock_mgr_executor.execute_manager.assert_not_awaited()

        # All roles run via RoleExecutor
        assert mock_exec_role.await_count == 3
        assert len(result.role_results) == 3


# ===========================================================================
# 8. Cost accumulation includes manager results
# ===========================================================================


class TestCostAccumulation:
    """Total cost includes both manager and regular role results."""

    async def test_cost_includes_manager_and_regular(self):
        """total_cost aggregates costs from managers and regular roles."""
        dept = _make_dept()
        mgr_role = _make_role("mgr", manages=("worker",))
        worker = _make_role("worker")
        indie = _make_role("indie")

        mock_mgr_executor = MagicMock()
        mock_mgr_executor.execute_manager = AsyncMock(
            return_value=_make_role_result("mgr", cost_usd=0.50),
        )

        executor, mock_exec_role, _ = _build_executor(
            dept=dept,
            roles=[mgr_role, worker, indie],
            manager_executor=mock_mgr_executor,
        )
        mock_exec_role.side_effect = lambda **kw: _make_role_result(
            kw["role_id"],
            cost_usd=0.25,
        )

        result = await executor.execute_department(
            department_id="marketing",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        # 0.50 (manager) + 0.25 (indie) = 0.75
        assert result.total_cost["total_cost_usd"] == pytest.approx(0.75)
        assert "mgr" in result.total_cost["role_costs"]
        assert "indie" in result.total_cost["role_costs"]


# ===========================================================================
# 9. Manager failure does not block other roles
# ===========================================================================


class TestManagerFailure:
    """ManagerExecutor failure is caught; remaining roles still run."""

    async def test_manager_failure_graceful(self):
        """If execute_manager raises, regular roles still execute."""
        dept = _make_dept()
        mgr_role = _make_role("mgr", manages=("worker",))
        worker = _make_role("worker")
        indie = _make_role("indie")

        mock_mgr_executor = MagicMock()
        mock_mgr_executor.execute_manager = AsyncMock(
            side_effect=RuntimeError("Manager exploded"),
        )

        executor, mock_exec_role, _ = _build_executor(
            dept=dept,
            roles=[mgr_role, worker, indie],
            manager_executor=mock_mgr_executor,
        )
        mock_exec_role.side_effect = lambda **kw: _make_role_result(
            kw["role_id"],
        )

        result = await executor.execute_department(
            department_id="marketing",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        # Manager failed, but indie still ran
        assert mock_exec_role.await_count == 1
        assert len(result.role_results) == 1
        assert result.role_results[0].role_id == "indie"


# ===========================================================================
# 10. Department not found raises DepartmentNotFoundError
# ===========================================================================


class TestDepartmentNotFound:
    """DepartmentNotFoundError is raised for unknown departments."""

    async def test_raises_department_not_found(self):
        """execute_department raises DepartmentNotFoundError for bad ID."""
        mock_registry = MagicMock()
        mock_registry.get_department.return_value = None

        mock_role_executor = MagicMock(spec=RoleExecutor)

        executor = DepartmentExecutor(
            role_executor=mock_role_executor,
            registry=mock_registry,
        )

        with pytest.raises(DepartmentNotFoundError, match="nonexistent"):
            await executor.execute_department(
                department_id="nonexistent",
                user_id="user_1",
                accounts=_ACCOUNTS,
            )


# ===========================================================================
# 11. Manager itself is not run as a regular role
# ===========================================================================


class TestManagerNotDuplicatedAsRegular:
    """Manager role is run via ManagerExecutor, not also via RoleExecutor."""

    async def test_manager_not_run_twice(self):
        """Manager role appears once in results (from ManagerExecutor only)."""
        dept = _make_dept()
        mgr_role = _make_role("mgr", manages=("worker",))
        worker = _make_role("worker")

        mock_mgr_executor = MagicMock()
        mock_mgr_executor.execute_manager = AsyncMock(
            return_value=_make_role_result("mgr"),
        )

        executor, mock_exec_role, _ = _build_executor(
            dept=dept,
            roles=[mgr_role, worker],
            manager_executor=mock_mgr_executor,
        )

        result = await executor.execute_department(
            department_id="marketing",
            user_id="user_1",
            accounts=_ACCOUNTS,
        )

        # RoleExecutor should NOT be called for either mgr or worker
        mock_exec_role.assert_not_awaited()

        # Only the manager result from ManagerExecutor
        assert len(result.role_results) == 1
        assert result.role_results[0].role_id == "mgr"
