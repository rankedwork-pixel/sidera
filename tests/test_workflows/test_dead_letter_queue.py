"""Tests for the dead letter queue (DLQ) system.

Covers:
- Database service methods: record_failed_run, get_unresolved_failed_runs,
  resolve_failed_run
- The DLQ wrapper pattern used in Inngest workflows (daily_briefing,
  cost_monitor, skill_runner)
- The FailedRun SQLAlchemy model column definitions

All database interactions are mocked -- these tests never hit a real database.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import inngest
import pytest

from src.db import service as db_service
from src.models.schema import FailedRun
from src.workflows.daily_briefing import (
    daily_briefing_workflow,
)
from tests.test_workflows.conftest import (
    SAMPLE_ACCOUNTS,
    _make_mock_context,
)

# =====================================================================
# Helpers
# =====================================================================


def _make_mock_session():
    """Create a mock AsyncSession that tracks add/flush calls."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()
    return session


def _make_failed_run(
    *,
    id: int = 1,
    workflow_name: str = "daily_briefing",
    event_name: str = "sidera/daily.run",
    event_data: dict | None = None,
    error_message: str = "Something failed",
    error_type: str = "RuntimeError",
    user_id: str = "user-42",
    run_id: str = "run-abc-123",
    resolved_at: datetime | None = None,
    resolved_by: str | None = None,
) -> MagicMock:
    """Create a mock FailedRun row."""
    row = MagicMock(spec=FailedRun)
    row.id = id
    row.workflow_name = workflow_name
    row.event_name = event_name
    row.event_data = event_data or {"user_id": user_id}
    row.error_message = error_message
    row.error_type = error_type
    row.user_id = user_id
    row.run_id = run_id
    row.retry_count = 0
    row.created_at = datetime(2025, 6, 15, 7, 0, 0, tzinfo=timezone.utc)
    row.resolved_at = resolved_at
    row.resolved_by = resolved_by
    return row


# =====================================================================
# 1. record_failed_run
# =====================================================================


class TestRecordFailedRun:
    """Tests for db_service.record_failed_run."""

    @pytest.mark.asyncio
    async def test_creates_failed_run_with_all_fields(self) -> None:
        """record_failed_run should add a FailedRun with all fields populated."""
        session = _make_mock_session()

        await db_service.record_failed_run(
            session=session,
            workflow_name="daily_briefing",
            event_name="sidera/daily.run",
            event_data={"user_id": "user-42", "accounts": []},
            error_message="Connection timeout",
            error_type="TimeoutError",
            user_id="user-42",
            run_id="run-xyz-789",
        )

        # Should have called session.add with a FailedRun instance
        session.add.assert_called_once()
        added_obj = session.add.call_args[0][0]
        assert isinstance(added_obj, FailedRun)
        assert added_obj.workflow_name == "daily_briefing"
        assert added_obj.event_name == "sidera/daily.run"
        assert added_obj.event_data == {"user_id": "user-42", "accounts": []}
        assert added_obj.error_message == "Connection timeout"
        assert added_obj.error_type == "TimeoutError"
        assert added_obj.user_id == "user-42"
        assert added_obj.run_id == "run-xyz-789"

        # Should have flushed to get an ID
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_the_created_row(self) -> None:
        """record_failed_run should return the FailedRun instance."""
        session = _make_mock_session()

        result = await db_service.record_failed_run(
            session=session,
            workflow_name="cost_monitor",
            event_name="sidera/cost.check",
            error_message="DB unavailable",
            error_type="OperationalError",
        )

        assert isinstance(result, FailedRun)
        assert result.workflow_name == "cost_monitor"
        assert result.error_message == "DB unavailable"

    @pytest.mark.asyncio
    async def test_default_empty_strings(self) -> None:
        """Omitted optional fields should default to empty strings."""
        session = _make_mock_session()

        result = await db_service.record_failed_run(
            session=session,
            workflow_name="skill_runner",
            event_name="sidera/skill.run",
        )

        assert result.error_message == ""
        assert result.error_type == ""
        assert result.user_id == ""
        assert result.run_id == ""


# =====================================================================
# 2. get_unresolved_failed_runs
# =====================================================================


class TestGetUnresolvedFailedRuns:
    """Tests for db_service.get_unresolved_failed_runs."""

    @pytest.mark.asyncio
    async def test_returns_only_unresolved_items(self) -> None:
        """Should return FailedRun rows where resolved_at IS NULL."""
        unresolved = [
            _make_failed_run(id=1, resolved_at=None),
            _make_failed_run(id=2, resolved_at=None),
        ]

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = unresolved

        session = _make_mock_session()
        session.execute.return_value = mock_result

        rows = await db_service.get_unresolved_failed_runs(session)

        assert len(rows) == 2
        assert all(r.resolved_at is None for r in rows)
        # Verify execute was called (the WHERE clause filters resolved_at IS NULL)
        session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_filters_by_user_id(self) -> None:
        """When user_id is provided, only that user's failures are returned."""
        user_rows = [_make_failed_run(id=3, user_id="user-42")]

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = user_rows

        session = _make_mock_session()
        session.execute.return_value = mock_result

        rows = await db_service.get_unresolved_failed_runs(session, user_id="user-42")

        assert len(rows) == 1
        assert rows[0].user_id == "user-42"

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_all_resolved(self) -> None:
        """Should return empty list when no unresolved runs exist."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        session = _make_mock_session()
        session.execute.return_value = mock_result

        rows = await db_service.get_unresolved_failed_runs(session)
        assert rows == []


# =====================================================================
# 3. resolve_failed_run
# =====================================================================


class TestResolveFailedRun:
    """Tests for db_service.resolve_failed_run."""

    @pytest.mark.asyncio
    async def test_sets_resolved_at_and_resolved_by(self) -> None:
        """Should set resolved_at timestamp and resolved_by on the row."""
        failed_row = _make_failed_run(id=5, resolved_at=None, resolved_by=None)

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = failed_row

        session = _make_mock_session()
        session.execute.return_value = mock_result

        result = await db_service.resolve_failed_run(
            session, failed_run_id=5, resolved_by="ops-admin"
        )

        assert result is not None
        assert result.resolved_by == "ops-admin"
        assert result.resolved_at is not None
        # resolved_at should be a recent UTC datetime
        assert isinstance(result.resolved_at, datetime)

    @pytest.mark.asyncio
    async def test_returns_none_for_nonexistent_id(self) -> None:
        """Should return None when the failed_run_id does not exist."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None

        session = _make_mock_session()
        session.execute.return_value = mock_result

        result = await db_service.resolve_failed_run(
            session, failed_run_id=9999, resolved_by="ops-admin"
        )

        assert result is None


# =====================================================================
# 4. DLQ wrapper in workflow
# =====================================================================


class TestDLQWrapperInWorkflow:
    """Tests for the DLQ try/except pattern in daily_briefing_workflow."""

    @pytest.mark.asyncio
    async def test_exception_triggers_record_failed_run(self) -> None:
        """When the workflow body raises, record_failed_run is called with correct args."""
        ctx = _make_mock_context(
            event_data={
                "user_id": "user-42",
                "accounts": SAMPLE_ACCOUNTS,
                "channel_id": "C0123SLACK",
            }
        )
        ctx.event.name = "sidera/daily.run"

        # Make the load-accounts step raise a non-inngest exception
        original_error = RuntimeError("Unexpected failure in analysis")

        async def failing_step_run(step_id: str, handler, *args):
            if step_id == "load-accounts":
                # Return valid accounts so we get past the first step
                return {"accounts": SAMPLE_ACCOUNTS, "source": "event"}
            if step_id == "check-existing-briefing":
                return {"exists": False}
            if step_id == "run-analysis":
                raise original_error
            return {}

        ctx.step.run = AsyncMock(side_effect=failing_step_run)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_record = AsyncMock()

        with (
            patch(
                "src.db.service.record_failed_run",
                mock_record,
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=mock_session,
            ),
            patch(
                "src.middleware.sentry_setup.capture_exception",
            ),
        ):
            with pytest.raises(RuntimeError, match="Unexpected failure"):
                await daily_briefing_workflow._handler(ctx)

            # record_failed_run should have been called with the correct args
            mock_record.assert_awaited_once()
            call_kwargs = mock_record.call_args.kwargs
            assert call_kwargs["workflow_name"] == "daily_briefing"
            assert call_kwargs["event_name"] == "sidera/daily.run"
            assert call_kwargs["error_message"] == "Unexpected failure in analysis"
            assert call_kwargs["error_type"] == "RuntimeError"
            assert call_kwargs["user_id"] == "user-42"

    @pytest.mark.asyncio
    async def test_non_retriable_error_bypasses_dlq(self) -> None:
        """inngest.NonRetriableError should NOT be recorded to DLQ."""
        ctx = _make_mock_context(
            event_data={
                "user_id": "user-42",
                "accounts": [],  # Will trigger NonRetriableError
                "channel_id": "C0123SLACK",
            }
        )
        ctx.event.name = "sidera/daily.run"

        # load-accounts returns empty accounts list -> NonRetriableError
        async def step_run_empty_accounts(step_id: str, handler, *args):
            if step_id == "load-accounts":
                return {"accounts": [], "source": "event"}
            return {}

        ctx.step.run = AsyncMock(side_effect=step_run_empty_accounts)

        mock_record = AsyncMock()

        with (
            patch(
                "src.db.service.record_failed_run",
                mock_record,
            ),
            patch(
                "src.middleware.sentry_setup.capture_exception",
            ),
        ):
            with pytest.raises(inngest.NonRetriableError):
                await daily_briefing_workflow._handler(ctx)

            # record_failed_run should NOT have been called
            mock_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_dlq_reraises_original_exception(self) -> None:
        """After DLQ recording, the original exception is re-raised."""
        ctx = _make_mock_context(
            event_data={
                "user_id": "user-42",
                "accounts": SAMPLE_ACCOUNTS,
                "channel_id": "C0123SLACK",
            }
        )
        ctx.event.name = "sidera/daily.run"

        original_error = ValueError("Bad metric value")

        async def failing_step_run(step_id: str, handler, *args):
            if step_id == "load-accounts":
                return {"accounts": SAMPLE_ACCOUNTS, "source": "event"}
            if step_id == "check-existing-briefing":
                return {"exists": False}
            if step_id == "run-analysis":
                raise original_error
            return {}

        ctx.step.run = AsyncMock(side_effect=failing_step_run)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "src.db.session.get_db_session",
                return_value=mock_session,
            ),
            patch(
                "src.db.service.record_failed_run",
                AsyncMock(),
            ),
            patch(
                "src.middleware.sentry_setup.capture_exception",
            ),
        ):
            with pytest.raises(ValueError, match="Bad metric value"):
                await daily_briefing_workflow._handler(ctx)

    @pytest.mark.asyncio
    async def test_db_failure_during_dlq_recording_does_not_mask_original(
        self,
    ) -> None:
        """If record_failed_run itself fails, the original error still propagates."""
        ctx = _make_mock_context(
            event_data={
                "user_id": "user-42",
                "accounts": SAMPLE_ACCOUNTS,
                "channel_id": "C0123SLACK",
            }
        )
        ctx.event.name = "sidera/daily.run"

        original_error = RuntimeError("Phase 2 explosion")

        async def failing_step_run(step_id: str, handler, *args):
            if step_id == "load-accounts":
                return {"accounts": SAMPLE_ACCOUNTS, "source": "event"}
            if step_id == "check-existing-briefing":
                return {"exists": False}
            if step_id == "run-analysis":
                raise original_error
            return {}

        ctx.step.run = AsyncMock(side_effect=failing_step_run)

        # Make get_db_session raise so DLQ recording fails
        with (
            patch(
                "src.db.session.get_db_session",
                side_effect=ConnectionError("DB is down"),
            ),
            patch(
                "src.middleware.sentry_setup.capture_exception",
            ),
        ):
            # The original RuntimeError should still surface, not ConnectionError
            with pytest.raises(RuntimeError, match="Phase 2 explosion"):
                await daily_briefing_workflow._handler(ctx)


# =====================================================================
# 5. FailedRun model
# =====================================================================


class TestFailedRunModel:
    """Tests for the FailedRun SQLAlchemy model columns."""

    def test_has_correct_table_name(self) -> None:
        """FailedRun should map to the 'failed_runs' table."""
        assert FailedRun.__tablename__ == "failed_runs"

    def test_has_required_columns(self) -> None:
        """FailedRun should have all expected columns."""
        column_names = {c.name for c in FailedRun.__table__.columns}
        expected = {
            "id",
            "workflow_name",
            "event_name",
            "event_data",
            "error_message",
            "error_type",
            "user_id",
            "run_id",
            "retry_count",
            "created_at",
            "resolved_at",
            "resolved_by",
        }
        assert expected.issubset(column_names), f"Missing columns: {expected - column_names}"

    def test_workflow_name_is_not_nullable(self) -> None:
        """workflow_name column should be NOT NULL."""
        col = FailedRun.__table__.c.workflow_name
        assert col.nullable is False

    def test_event_name_is_not_nullable(self) -> None:
        """event_name column should be NOT NULL."""
        col = FailedRun.__table__.c.event_name
        assert col.nullable is False

    def test_id_is_primary_key(self) -> None:
        """id column should be the primary key."""
        col = FailedRun.__table__.c.id
        assert col.primary_key is True

    def test_retry_count_defaults_to_zero(self) -> None:
        """retry_count should have a default of 0."""
        col = FailedRun.__table__.c.retry_count
        assert col.default is not None
        assert col.default.arg == 0
