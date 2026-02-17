"""Concurrent task manager for Claude Code executions.

Manages:
- Concurrency limits via ``asyncio.Semaphore``
- Per-task and aggregate cost tracking
- Task lifecycle (pending → running → completed/failed)
- DB persistence of task records

Usage::

    manager = ClaudeCodeTaskManager(max_concurrent=20)

    # Synchronous (blocking) — for Inngest steps
    result = await manager.run_task_sync(skill, prompt, user_id)

    # Fire-and-forget
    task = await manager.submit_task(skill, prompt, user_id)

    # Concurrent batch
    results = await manager.run_batch([(skill1, p1, u1, {}), ...])
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import structlog

from src.claude_code.executor import ClaudeCodeExecutor, ClaudeCodeResult
from src.config import settings

logger = structlog.get_logger(__name__)


# =============================================================================
# Task status constants
# =============================================================================


class TaskStatus:
    """Status constants for Claude Code tasks."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# =============================================================================
# Task dataclass (in-memory representation)
# =============================================================================


@dataclass
class ClaudeCodeTask:
    """In-memory representation of a Claude Code task."""

    task_id: str
    skill_id: str
    user_id: str
    prompt: str
    status: str = TaskStatus.PENDING
    result: ClaudeCodeResult | None = None
    error_message: str = ""
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )
    started_at: datetime | None = None
    completed_at: datetime | None = None
    # Config
    max_budget_usd: float = 5.0
    permission_mode: str = "acceptEdits"
    role_id: str = ""
    department_id: str = ""


# =============================================================================
# Task Manager
# =============================================================================


class ClaudeCodeTaskManager:
    """Manages concurrent Claude Code task execution.

    Uses an ``asyncio.Semaphore`` to limit the number of Claude Code
    instances running simultaneously.  Each task is tracked in the
    ``claude_code_tasks`` database table for observability and audit.

    Args:
        max_concurrent: Maximum simultaneous Claude Code instances.
        executor: Optional pre-configured executor instance.
    """

    def __init__(
        self,
        max_concurrent: int | None = None,
        executor: ClaudeCodeExecutor | None = None,
    ) -> None:
        self._max_concurrent = max_concurrent or settings.claude_code_max_concurrent
        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        self._executor = executor or ClaudeCodeExecutor()
        self._active_tasks: dict[str, ClaudeCodeTask] = {}
        self._log = logger.bind(component="claude_code_task_manager")

    @property
    def active_count(self) -> int:
        """Number of currently running tasks."""
        return len(self._active_tasks)

    @property
    def available_slots(self) -> int:
        """Number of available execution slots."""
        return self._max_concurrent - self.active_count

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    async def run_task_sync(
        self,
        skill: Any,
        prompt: str,
        user_id: str,
        *,
        role_context: str = "",
        memory_context: str = "",
        role_id: str = "",
        department_id: str = "",
        max_budget_usd: float | None = None,
        permission_mode: str = "",
        params: dict[str, Any] | None = None,
    ) -> ClaudeCodeResult:
        """Run a task synchronously (blocking).

        Use this in Inngest workflow steps where you need the result
        immediately.

        Returns:
            ``ClaudeCodeResult`` with output, cost, and metadata.
        """
        task_id = str(uuid.uuid4())
        budget = max_budget_usd or settings.claude_code_default_budget_usd

        task = ClaudeCodeTask(
            task_id=task_id,
            skill_id=skill.id,
            user_id=user_id,
            prompt=prompt,
            max_budget_usd=budget,
            permission_mode=permission_mode or settings.claude_code_default_permission_mode,
            role_id=role_id,
            department_id=department_id,
        )

        self._active_tasks[task_id] = task
        await self._save_task_to_db(task)

        await self._run_task(
            task,
            skill,
            prompt,
            user_id,
            role_context=role_context,
            memory_context=memory_context,
            params=params,
        )

        if task.result is None:
            return ClaudeCodeResult(
                skill_id=skill.id,
                user_id=user_id,
                output_text="",
                is_error=True,
                error_message=task.error_message or "Unknown error",
            )

        return task.result

    async def submit_task(
        self,
        skill: Any,
        prompt: str,
        user_id: str,
        *,
        role_context: str = "",
        memory_context: str = "",
        role_id: str = "",
        department_id: str = "",
        max_budget_usd: float | None = None,
        permission_mode: str = "",
        params: dict[str, Any] | None = None,
    ) -> ClaudeCodeTask:
        """Submit a task for background execution (fire-and-forget).

        Returns immediately with the task handle.  The task runs in
        the background, limited by the concurrency semaphore.

        Returns:
            ``ClaudeCodeTask`` with ``task_id`` for status tracking.
        """
        task_id = str(uuid.uuid4())
        budget = max_budget_usd or settings.claude_code_default_budget_usd

        task = ClaudeCodeTask(
            task_id=task_id,
            skill_id=skill.id,
            user_id=user_id,
            prompt=prompt,
            max_budget_usd=budget,
            permission_mode=permission_mode or settings.claude_code_default_permission_mode,
            role_id=role_id,
            department_id=department_id,
        )

        self._active_tasks[task_id] = task
        await self._save_task_to_db(task)

        # Fire-and-forget — runs within the semaphore
        asyncio.create_task(
            self._run_task(
                task,
                skill,
                prompt,
                user_id,
                role_context=role_context,
                memory_context=memory_context,
                params=params,
            )
        )

        return task

    async def run_batch(
        self,
        tasks: list[tuple[Any, str, str, dict[str, Any]]],
        *,
        role_context: str = "",
        memory_context: str = "",
    ) -> list[ClaudeCodeResult]:
        """Run multiple tasks concurrently.

        Each tuple in ``tasks`` is ``(skill, prompt, user_id, kwargs)``
        where kwargs may include ``role_id``, ``department_id``, etc.

        Returns results in the same order as input tasks.
        """

        async def _run_one(
            skill: Any,
            prompt: str,
            user_id: str,
            kwargs: dict[str, Any],
        ) -> ClaudeCodeResult:
            return await self.run_task_sync(
                skill,
                prompt,
                user_id,
                role_context=role_context,
                memory_context=memory_context,
                **kwargs,
            )

        results = await asyncio.gather(
            *[_run_one(s, p, u, kw) for s, p, u, kw in tasks],
            return_exceptions=True,
        )

        # Convert exceptions to error results
        final: list[ClaudeCodeResult] = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                skill, _, uid, _ = tasks[i]
                final.append(
                    ClaudeCodeResult(
                        skill_id=getattr(skill, "id", "unknown"),
                        user_id=uid,
                        output_text="",
                        is_error=True,
                        error_message=str(r),
                    )
                )
            else:
                final.append(r)
        return final

    # -----------------------------------------------------------------
    # Internal execution
    # -----------------------------------------------------------------

    async def _run_task(
        self,
        task: ClaudeCodeTask,
        skill: Any,
        prompt: str,
        user_id: str,
        *,
        role_context: str = "",
        memory_context: str = "",
        params: dict[str, Any] | None = None,
    ) -> None:
        """Execute a task within the concurrency semaphore."""
        async with self._semaphore:
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await self._update_task_status(task)

            try:
                result = await self._executor.execute(
                    skill=skill,
                    prompt=prompt,
                    user_id=user_id,
                    role_context=role_context,
                    memory_context=memory_context,
                    max_budget_usd=task.max_budget_usd,
                    permission_mode=task.permission_mode,
                    params=params,
                )

                task.result = result
                task.status = TaskStatus.COMPLETED if not result.is_error else TaskStatus.FAILED
                task.error_message = result.error_message
                task.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)

                # Record cost
                await self._record_cost(task, result)

            except Exception as exc:
                task.status = TaskStatus.FAILED
                task.error_message = str(exc)
                task.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                self._log.exception("task_manager.task_failed", task_id=task.task_id)

            finally:
                await self._update_task_status(task)
                self._active_tasks.pop(task.task_id, None)

    # -----------------------------------------------------------------
    # DB persistence helpers
    # -----------------------------------------------------------------

    async def _save_task_to_db(self, task: ClaudeCodeTask) -> None:
        """Create a new task record in the database."""
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.create_claude_code_task(
                    session=session,
                    task_id=task.task_id,
                    skill_id=task.skill_id,
                    user_id=task.user_id,
                    prompt=task.prompt,
                    status=task.status,
                    role_id=task.role_id,
                    department_id=task.department_id,
                    max_budget_usd=task.max_budget_usd,
                    permission_mode=task.permission_mode,
                )
        except Exception as exc:
            self._log.warning("task_manager.db_save_failed", error=str(exc))

    async def _update_task_status(self, task: ClaudeCodeTask) -> None:
        """Update task status and result in the database."""
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.update_claude_code_task(
                    session=session,
                    task_id=task.task_id,
                    status=task.status,
                    error_message=task.error_message,
                    started_at=task.started_at,
                    completed_at=task.completed_at,
                    result_text=(task.result.output_text if task.result else None),
                    structured_output=(task.result.structured_output if task.result else None),
                    cost_usd=(task.result.cost_usd if task.result else None),
                    num_turns=(task.result.num_turns if task.result else None),
                    duration_ms=(task.result.duration_ms if task.result else None),
                    session_id=(task.result.session_id if task.result else None),
                )
        except Exception as exc:
            self._log.warning("task_manager.db_update_failed", error=str(exc))

    async def _record_cost(
        self,
        task: ClaudeCodeTask,
        result: ClaudeCodeResult,
    ) -> None:
        """Record the task's LLM cost in the cost_tracking table."""
        if result.cost_usd <= 0:
            return
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.record_cost(
                    session=session,
                    user_id=task.user_id,
                    run_date=date.today(),
                    model="claude_code",
                    cost_usd=Decimal(str(result.cost_usd)),
                    input_tokens=result.usage.get("input_tokens", 0),
                    output_tokens=result.usage.get("output_tokens", 0),
                    operation=f"claude_code:{task.skill_id}",
                )
        except Exception as exc:
            self._log.warning("task_manager.cost_record_failed", error=str(exc))
