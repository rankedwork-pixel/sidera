"""Tests for the bootstrap plan executor."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bootstrap.executor import (
    _create_department,
    _create_role,
    _create_skill,
    _seed_memory,
    execute_plan,
)
from src.bootstrap.models import (
    BootstrapPlan,
    BootstrapStatus,
    ExecutionResult,
    ExtractedDepartment,
    ExtractedMemory,
    ExtractedRole,
    ExtractedSkill,
)


@pytest.fixture()
def mock_session():
    return AsyncMock()


@pytest.fixture(autouse=True)
def _no_real_db():
    """Prevent any real DB connections in executor tests."""

    @asynccontextmanager
    async def _mock_session():
        yield AsyncMock()

    with patch("src.bootstrap.executor.get_db_session", side_effect=_mock_session):
        yield


class TestCreateDepartment:
    @pytest.mark.asyncio
    async def test_create_new(self, mock_session):
        with patch("src.bootstrap.executor.db_service") as mock_db:
            mock_db.get_org_department = AsyncMock(return_value=None)
            mock_db.create_org_department = AsyncMock()
            mock_db.update_org_department = AsyncMock()

            dept = ExtractedDepartment(
                id="eng", name="Engineering", description="Builds products",
                vocabulary=[{"term": "CI", "definition": "Continuous Integration"}],
            )
            result = ExecutionResult(plan_id="test")
            await _create_department(mock_session, dept, "user1", result)

            assert result.departments_created == 1
            mock_db.create_org_department.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_existing(self, mock_session):
        with patch("src.bootstrap.executor.db_service") as mock_db:
            mock_db.get_org_department = AsyncMock(return_value=MagicMock())

            dept = ExtractedDepartment(id="eng", name="Eng", description="")
            result = ExecutionResult(plan_id="test")
            await _create_department(mock_session, dept, "user1", result)

            assert result.departments_skipped == 1
            assert result.departments_created == 0

    @pytest.mark.asyncio
    async def test_handle_error(self, mock_session):
        with patch("src.bootstrap.executor.db_service") as mock_db:
            mock_db.get_org_department = AsyncMock(side_effect=Exception("DB error"))

            dept = ExtractedDepartment(id="eng", name="Eng", description="")
            result = ExecutionResult(plan_id="test")
            await _create_department(mock_session, dept, "user1", result)

            assert len(result.errors) == 1


class TestCreateRole:
    @pytest.mark.asyncio
    async def test_create_new(self, mock_session):
        with patch("src.bootstrap.executor.db_service") as mock_db:
            mock_db.get_org_role = AsyncMock(return_value=None)
            mock_db.create_org_role = AsyncMock()
            mock_db.update_org_role = AsyncMock()

            role = ExtractedRole(
                id="swe", name="SWE", department_id="eng", description="Writes code",
                persona="A careful engineer", goals=["Ship quality"],
                principles=["Test first"],
            )
            result = ExecutionResult(plan_id="test")
            await _create_role(mock_session, role, ["code_review"], "user1", result)

            assert result.roles_created == 1
            mock_db.create_org_role.assert_called_once()
            # Goals/principles should be set via update
            mock_db.update_org_role.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_existing(self, mock_session):
        with patch("src.bootstrap.executor.db_service") as mock_db:
            mock_db.get_org_role = AsyncMock(return_value=MagicMock())

            role = ExtractedRole(
                id="swe", name="SWE", department_id="eng", description=""
            )
            result = ExecutionResult(plan_id="test")
            await _create_role(mock_session, role, [], "user1", result)

            assert result.roles_skipped == 1


class TestCreateSkill:
    @pytest.mark.asyncio
    async def test_create_new(self, mock_session):
        with patch("src.bootstrap.executor.db_service") as mock_db:
            mock_db.get_org_skill = AsyncMock(return_value=None)
            mock_db.create_org_skill = AsyncMock()

            skill = ExtractedSkill(
                id="review", name="Code Review", role_id="swe",
                department_id="eng", description="Review PRs",
                category="analysis", model="sonnet",
                system_supplement="Review carefully",
                prompt_template="Review latest PRs",
                output_format="## Summary",
                business_guidance="Focus on correctness",
            )
            result = ExecutionResult(plan_id="test")
            await _create_skill(mock_session, skill, "user1", result)

            assert result.skills_created == 1
            mock_db.create_org_skill.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_existing(self, mock_session):
        with patch("src.bootstrap.executor.db_service") as mock_db:
            mock_db.get_org_skill = AsyncMock(return_value=MagicMock())

            skill = ExtractedSkill(
                id="review", name="Review", role_id="swe",
                department_id="eng", description="",
            )
            result = ExecutionResult(plan_id="test")
            await _create_skill(mock_session, skill, "user1", result)

            assert result.skills_skipped == 1


class TestSeedMemory:
    @pytest.mark.asyncio
    async def test_seed(self, mock_session):
        with patch("src.bootstrap.executor.db_service") as mock_db:
            mock_db.save_memory = AsyncMock()

            memory = ExtractedMemory(
                role_id="swe", department_id="eng",
                memory_type="insight", title="Key fact",
                content="Important detail", confidence=0.9,
            )
            result = ExecutionResult(plan_id="test")
            await _seed_memory(mock_session, memory, "user1", result)

            assert result.memories_seeded == 1
            mock_db.save_memory.assert_called_once()
            call_kwargs = mock_db.save_memory.call_args
            # Title should be prefixed with [Bootstrap]
            assert "[Bootstrap]" in call_kwargs.kwargs.get(
                "title", call_kwargs[1].get("title", "")
            )

    @pytest.mark.asyncio
    async def test_seed_error(self, mock_session):
        with patch("src.bootstrap.executor.db_service") as mock_db:
            mock_db.save_memory = AsyncMock(side_effect=Exception("DB error"))

            memory = ExtractedMemory(
                role_id="swe", department_id="eng",
                memory_type="insight", title="Fact", content="Detail",
            )
            result = ExecutionResult(plan_id="test")
            await _seed_memory(mock_session, memory, "user1", result)

            assert len(result.errors) == 1


class TestExecutePlan:
    @pytest.mark.asyncio
    async def test_reject_non_approved(self):
        plan = BootstrapPlan(status="draft")
        result = await execute_plan(plan)
        assert not result.success
        assert "expected 'approved'" in result.errors[0]

    @pytest.mark.asyncio
    async def test_execute_approved(self):
        with patch("src.bootstrap.executor.db_service") as mock_db:
            mock_db.get_org_department = AsyncMock(return_value=None)
            mock_db.create_org_department = AsyncMock()
            mock_db.update_org_department = AsyncMock()
            mock_db.get_org_role = AsyncMock(return_value=None)
            mock_db.create_org_role = AsyncMock()
            mock_db.update_org_role = AsyncMock()
            mock_db.get_org_skill = AsyncMock(return_value=None)
            mock_db.create_org_skill = AsyncMock()
            mock_db.save_memory = AsyncMock()

            plan = BootstrapPlan(
                status=BootstrapStatus.APPROVED.value,
                departments=[
                    ExtractedDepartment(
                        id="eng", name="Engineering", description=""
                    )
                ],
                roles=[
                    ExtractedRole(
                        id="swe", name="SWE", department_id="eng", description=""
                    )
                ],
                skills=[
                    ExtractedSkill(
                        id="review", name="Review", role_id="swe",
                        department_id="eng", description="",
                    )
                ],
                memories=[
                    ExtractedMemory(
                        role_id="swe", department_id="eng",
                        memory_type="insight", title="Fact", content="Detail",
                    )
                ],
            )

            result = await execute_plan(plan, user_id="admin")
            assert result.success
            assert result.departments_created == 1
            assert result.roles_created == 1
            assert result.skills_created == 1
            assert result.memories_seeded == 1
            assert plan.status == BootstrapStatus.EXECUTED.value
