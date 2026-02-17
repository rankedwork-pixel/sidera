"""Tests for the Claude Code task manager.

Tests ``ClaudeCodeTaskManager`` — concurrency, DB persistence,
cost recording, batch execution, and status transitions.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.claude_code.executor import ClaudeCodeResult
from src.claude_code.task_manager import (
    ClaudeCodeTask,
    ClaudeCodeTaskManager,
    TaskStatus,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill(skill_id: str = "test_skill") -> MagicMock:
    """Create a minimal mock SkillDefinition."""
    skill = MagicMock()
    skill.id = skill_id
    skill.name = "Test Skill"
    skill.model = "sonnet"
    skill.max_turns = 5
    skill.system_supplement = ""
    skill.prompt_template = "Do the thing."
    skill.output_format = ""
    skill.business_guidance = ""
    skill.context_files = ()
    skill.department_id = "test_dept"
    return skill


def _make_result(
    skill_id: str = "test_skill",
    is_error: bool = False,
    cost_usd: float = 0.05,
) -> ClaudeCodeResult:
    return ClaudeCodeResult(
        skill_id=skill_id,
        user_id="user1",
        output_text="Task done.",
        cost_usd=cost_usd,
        num_turns=3,
        duration_ms=5000,
        session_id="sess_123",
        is_error=is_error,
        error_message="Error occurred" if is_error else "",
    )


def _mock_executor(result: ClaudeCodeResult | None = None) -> MagicMock:
    """Create a mock ClaudeCodeExecutor."""
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=result or _make_result())
    return executor


def _mock_db():
    """Create mock DB session and service patches."""
    mock_session = AsyncMock()
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm, mock_session


# ---------------------------------------------------------------------------
# ClaudeCodeTask tests
# ---------------------------------------------------------------------------


class TestClaudeCodeTask:
    """Tests for the in-memory task dataclass."""

    def test_default_status_is_pending(self):
        task = ClaudeCodeTask(
            task_id="t1",
            skill_id="s1",
            user_id="u1",
            prompt="hello",
        )
        assert task.status == TaskStatus.PENDING
        assert task.result is None
        assert task.started_at is None
        assert task.completed_at is None

    def test_custom_fields(self):
        task = ClaudeCodeTask(
            task_id="t2",
            skill_id="s2",
            user_id="u2",
            prompt="run analysis",
            max_budget_usd=10.0,
            permission_mode="bypassPermissions",
            role_id="head_of_it",
            department_id="it",
        )
        assert task.max_budget_usd == 10.0
        assert task.permission_mode == "bypassPermissions"
        assert task.role_id == "head_of_it"


# ---------------------------------------------------------------------------
# run_task_sync tests
# ---------------------------------------------------------------------------


class TestRunTaskSync:
    """Tests for blocking task execution."""

    @pytest.mark.asyncio
    async def test_returns_result(self):
        """run_task_sync should return the ClaudeCodeResult."""
        expected = _make_result()
        executor = _mock_executor(expected)
        mock_cm, mock_session = _mock_db()

        with (
            patch(
                "src.claude_code.task_manager.ClaudeCodeExecutor",
                return_value=executor,
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=mock_cm,
            ),
            patch(
                "src.db.service.create_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.update_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.record_cost",
                new_callable=AsyncMock,
            ),
        ):
            manager = ClaudeCodeTaskManager(max_concurrent=5, executor=executor)
            result = await manager.run_task_sync(_make_skill(), "Test prompt", "user1")

        assert result.output_text == "Task done."
        assert result.cost_usd == 0.05
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_error_result_still_returned(self):
        """Failed tasks should return error results, not raise."""
        error_result = _make_result(is_error=True)
        executor = _mock_executor(error_result)
        mock_cm, mock_session = _mock_db()

        with (
            patch(
                "src.db.session.get_db_session",
                return_value=mock_cm,
            ),
            patch(
                "src.db.service.create_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.update_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.record_cost",
                new_callable=AsyncMock,
            ),
        ):
            manager = ClaudeCodeTaskManager(max_concurrent=5, executor=executor)
            result = await manager.run_task_sync(_make_skill(), "Test prompt", "user1")

        assert result.is_error is True
        assert result.error_message == "Error occurred"


# ---------------------------------------------------------------------------
# Concurrency tests
# ---------------------------------------------------------------------------


class TestConcurrency:
    """Tests for semaphore-based concurrency control."""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """Only max_concurrent tasks should run simultaneously."""
        running_count = 0
        max_seen = 0
        lock = asyncio.Lock()

        async def slow_execute(*args, **kwargs):
            nonlocal running_count, max_seen
            async with lock:
                running_count += 1
                if running_count > max_seen:
                    max_seen = running_count
            await asyncio.sleep(0.05)
            async with lock:
                running_count -= 1
            return _make_result()

        executor = MagicMock()
        executor.execute = slow_execute
        mock_cm, mock_session = _mock_db()

        with (
            patch(
                "src.db.session.get_db_session",
                return_value=mock_cm,
            ),
            patch(
                "src.db.service.create_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.update_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.record_cost",
                new_callable=AsyncMock,
            ),
        ):
            manager = ClaudeCodeTaskManager(max_concurrent=2, executor=executor)

            skill = _make_skill()
            tasks = [
                (skill, "p1", "u1", {}),
                (skill, "p2", "u2", {}),
                (skill, "p3", "u3", {}),
                (skill, "p4", "u4", {}),
            ]
            results = await manager.run_batch(tasks)

        assert len(results) == 4
        assert max_seen <= 2  # Never more than 2 concurrent


# ---------------------------------------------------------------------------
# run_batch tests
# ---------------------------------------------------------------------------


class TestRunBatch:
    """Tests for concurrent batch execution."""

    @pytest.mark.asyncio
    async def test_returns_in_order(self):
        """Results should be in the same order as input tasks."""
        call_order = []

        async def ordered_execute(skill, prompt, user_id, **kwargs):
            call_order.append(skill.id)
            await asyncio.sleep(0.01)
            return ClaudeCodeResult(
                skill_id=skill.id,
                user_id=user_id,
                output_text=f"Result for {skill.id}",
            )

        executor = MagicMock()
        executor.execute = ordered_execute
        mock_cm, _ = _mock_db()

        with (
            patch(
                "src.db.session.get_db_session",
                return_value=mock_cm,
            ),
            patch(
                "src.db.service.create_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.update_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.record_cost",
                new_callable=AsyncMock,
            ),
        ):
            manager = ClaudeCodeTaskManager(max_concurrent=10, executor=executor)

            skill_a = _make_skill("skill_a")
            skill_b = _make_skill("skill_b")
            tasks = [
                (skill_a, "p1", "u1", {}),
                (skill_b, "p2", "u2", {}),
            ]
            results = await manager.run_batch(tasks)

        assert len(results) == 2
        assert results[0].skill_id == "skill_a"
        assert results[1].skill_id == "skill_b"

    @pytest.mark.asyncio
    async def test_individual_failures_graceful(self):
        """Individual task failures should not crash the batch."""
        call_count = 0

        async def mixed_execute(skill, prompt, user_id, **kwargs):
            nonlocal call_count
            call_count += 1
            if skill.id == "fail_skill":
                raise RuntimeError("Task crashed")
            return _make_result(skill_id=skill.id)

        executor = MagicMock()
        executor.execute = mixed_execute
        mock_cm, _ = _mock_db()

        with (
            patch(
                "src.db.session.get_db_session",
                return_value=mock_cm,
            ),
            patch(
                "src.db.service.create_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.update_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.record_cost",
                new_callable=AsyncMock,
            ),
        ):
            manager = ClaudeCodeTaskManager(max_concurrent=10, executor=executor)

            good_skill = _make_skill("good_skill")
            fail_skill = _make_skill("fail_skill")
            tasks = [
                (good_skill, "p1", "u1", {}),
                (fail_skill, "p2", "u2", {}),
            ]
            results = await manager.run_batch(tasks)

        assert len(results) == 2
        # First should succeed
        assert results[0].output_text == "Task done."
        # Second should be an error
        assert results[1].is_error is True
        assert "Task crashed" in results[1].error_message


# ---------------------------------------------------------------------------
# DB persistence tests
# ---------------------------------------------------------------------------


class TestDBPersistence:
    """Tests for database operations."""

    @pytest.mark.asyncio
    async def test_task_saved_to_db_on_create(self):
        """Creating a task should call create_claude_code_task."""
        executor = _mock_executor()
        mock_cm, mock_session = _mock_db()
        create_mock = AsyncMock()

        with (
            patch(
                "src.db.session.get_db_session",
                return_value=mock_cm,
            ),
            patch(
                "src.db.service.create_claude_code_task",
                create_mock,
            ),
            patch(
                "src.db.service.update_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.record_cost",
                new_callable=AsyncMock,
            ),
        ):
            manager = ClaudeCodeTaskManager(max_concurrent=5, executor=executor)
            await manager.run_task_sync(_make_skill(), "Test", "user1")

        # Should have been called once for the initial save
        assert create_mock.call_count >= 1

    @pytest.mark.asyncio
    async def test_cost_recorded_on_completion(self):
        """Completed tasks should record cost."""
        result = _make_result(cost_usd=0.10)
        executor = _mock_executor(result)
        mock_cm, _ = _mock_db()
        cost_mock = AsyncMock()

        with (
            patch(
                "src.db.session.get_db_session",
                return_value=mock_cm,
            ),
            patch(
                "src.db.service.create_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.update_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.record_cost",
                cost_mock,
            ),
        ):
            manager = ClaudeCodeTaskManager(max_concurrent=5, executor=executor)
            await manager.run_task_sync(_make_skill(), "Test", "user1")

        assert cost_mock.call_count >= 1

    @pytest.mark.asyncio
    async def test_zero_cost_not_recorded(self):
        """Zero-cost results should not trigger cost recording."""
        result = _make_result(cost_usd=0.0)
        executor = _mock_executor(result)
        mock_cm, _ = _mock_db()
        cost_mock = AsyncMock()

        with (
            patch(
                "src.db.session.get_db_session",
                return_value=mock_cm,
            ),
            patch(
                "src.db.service.create_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.update_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.record_cost",
                cost_mock,
            ),
        ):
            manager = ClaudeCodeTaskManager(max_concurrent=5, executor=executor)
            await manager.run_task_sync(_make_skill(), "Test", "user1")

        cost_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


class TestStatusTransitions:
    """Tests for task status lifecycle."""

    @pytest.mark.asyncio
    async def test_pending_to_running_to_completed(self):
        """Successful tasks should transition pending → running → completed."""
        statuses = []

        async def track_update(session, task_id, status=None, **kw):
            if status:
                statuses.append(status)

        executor = _mock_executor()
        mock_cm, _ = _mock_db()

        with (
            patch(
                "src.db.session.get_db_session",
                return_value=mock_cm,
            ),
            patch(
                "src.db.service.create_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.update_claude_code_task",
                side_effect=track_update,
            ),
            patch(
                "src.db.service.record_cost",
                new_callable=AsyncMock,
            ),
        ):
            manager = ClaudeCodeTaskManager(max_concurrent=5, executor=executor)
            await manager.run_task_sync(_make_skill(), "Test", "user1")

        assert "running" in statuses
        assert "completed" in statuses

    @pytest.mark.asyncio
    async def test_pending_to_running_to_failed(self):
        """Failed tasks should transition pending → running → failed."""
        statuses = []

        async def track_update(session, task_id, status=None, **kw):
            if status:
                statuses.append(status)

        error_result = _make_result(is_error=True)
        executor = _mock_executor(error_result)
        mock_cm, _ = _mock_db()

        with (
            patch(
                "src.db.session.get_db_session",
                return_value=mock_cm,
            ),
            patch(
                "src.db.service.create_claude_code_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.update_claude_code_task",
                side_effect=track_update,
            ),
            patch(
                "src.db.service.record_cost",
                new_callable=AsyncMock,
            ),
        ):
            manager = ClaudeCodeTaskManager(max_concurrent=5, executor=executor)
            await manager.run_task_sync(_make_skill(), "Test", "user1")

        assert "running" in statuses
        assert "failed" in statuses


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestProperties:
    """Tests for manager properties."""

    def test_available_slots(self):
        executor = _mock_executor()
        manager = ClaudeCodeTaskManager(max_concurrent=10, executor=executor)
        assert manager.available_slots == 10
        assert manager.active_count == 0
