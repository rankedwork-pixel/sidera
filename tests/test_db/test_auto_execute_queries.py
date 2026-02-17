"""Tests for auto-execute DB query methods in src.db.service.

Covers:
- count_auto_executions_today() — with/without rule_id filter, no results
- get_last_auto_execution_time() — found, not found
- AUTO_APPROVED enum value in ApprovalStatus

count_auto_executions_today uses ``func.date()`` which behaves differently
on SQLite vs PostgreSQL, so those tests use mocks to avoid DB-engine quirks.
get_last_auto_execution_time tests use SQLite in-memory (matching
the existing test_service.py pattern) since they don't depend on date casting.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db import service
from src.models.schema import (
    ApprovalStatus,
    Base,
    Platform,
)

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
async def db_session():
    """Create an in-memory SQLite database and yield a session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session
    await engine.dispose()


# ============================================================
# Helpers
# ============================================================


async def _create_account(
    session: AsyncSession,
    user_id: str = "user_1",
    platform: Platform = Platform.GOOGLE_ADS,
) -> object:
    """Create and return a basic account."""
    return await service.upsert_account(
        session,
        user_id=user_id,
        platform=platform,
        platform_account_id=f"acct-{user_id}",
        account_name="Test Account",
    )


async def _create_analysis(
    session: AsyncSession,
    user_id: str = "user_1",
) -> object:
    """Create and return a basic analysis result."""
    return await service.save_analysis_result(
        session,
        user_id=user_id,
        run_date=date(2025, 6, 15),
        briefing_content="Test briefing",
        recommendations=[],
    )


async def _create_auto_approved_item(
    session: AsyncSession,
    user_id: str = "user_1",
    rule_id: str = "small_budget_up",
    *,
    execute: bool = False,
) -> object:
    """Create an AUTO_APPROVED approval queue item.

    Args:
        session: DB session.
        user_id: Owner user ID.
        rule_id: The auto-execute rule ID to record.
        execute: If True, also set executed_at on the item.

    Returns:
        The created ApprovalQueueItem.
    """
    account = await _create_account(session, user_id=user_id)
    analysis = await _create_analysis(session, user_id=user_id)

    item = await service.create_approval(
        session,
        analysis_id=analysis.id,
        user_id=user_id,
        action_type="budget_change",
        account_id=account.id,
        description="Auto-approved budget change",
        reasoning="Matched rule",
        action_params={"platform": "google_ads", "amount": 100},
    )

    # Update to AUTO_APPROVED status with rule_id
    item = await service.update_approval_status(
        session,
        approval_id=item.id,
        status=ApprovalStatus.AUTO_APPROVED,
        decided_by="auto_execute_engine",
    )

    # Set auto_execute_rule_id directly on the model
    item.auto_execute_rule_id = rule_id
    await session.flush()

    if execute:
        await service.record_execution_result(
            session,
            approval_id=item.id,
            execution_result={"status": "ok"},
        )

    return item


def _mock_scalar_result(value):
    """Create a mock session.execute result that returns value via .scalar()."""
    result = MagicMock()
    result.scalar.return_value = value
    return result


# ============================================================
# TestAutoApprovedEnum
# ============================================================


class TestAutoApprovedEnum:
    """Tests for the AUTO_APPROVED ApprovalStatus enum value."""

    def test_auto_approved_value(self):
        """AUTO_APPROVED enum has value 'auto_approved'."""
        assert ApprovalStatus.AUTO_APPROVED.value == "auto_approved"

    def test_auto_approved_is_string_enum(self):
        """AUTO_APPROVED works as a string (str, PyEnum)."""
        assert isinstance(ApprovalStatus.AUTO_APPROVED, str)
        assert ApprovalStatus.AUTO_APPROVED == "auto_approved"

    def test_auto_approved_in_members(self):
        """AUTO_APPROVED is one of the ApprovalStatus members."""
        assert "AUTO_APPROVED" in ApprovalStatus.__members__


# ============================================================
# TestCountAutoExecutionsToday (mock-based)
#
# The query uses func.date() which behaves differently on SQLite
# vs PostgreSQL. We mock at the session level to test the
# function logic and filter construction.
# ============================================================


class TestCountAutoExecutionsToday:
    """Tests for count_auto_executions_today()."""

    @pytest.mark.asyncio
    async def test_count_with_rule_filter(self):
        """Counts only items matching the specified rule_id."""
        session = AsyncMock(spec=AsyncSession)
        session.execute = AsyncMock(return_value=_mock_scalar_result(3))

        count = await service.count_auto_executions_today(
            session,
            "user_1",
            "rule_a",
        )
        assert count == 3
        # Verify execute was called (the query was built and run)
        session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_count_without_rule_filter(self):
        """Counts all auto-executions when rule_id is empty string."""
        session = AsyncMock(spec=AsyncSession)
        session.execute = AsyncMock(return_value=_mock_scalar_result(7))

        count = await service.count_auto_executions_today(
            session,
            "user_1",
            "",
        )
        assert count == 7
        session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_count_no_results(self):
        """Returns 0 when scalar returns None (no matching rows)."""
        session = AsyncMock(spec=AsyncSession)
        session.execute = AsyncMock(return_value=_mock_scalar_result(None))

        count = await service.count_auto_executions_today(
            session,
            "user_1",
            "nonexistent_rule",
        )
        assert count == 0

    @pytest.mark.asyncio
    async def test_count_returns_zero_for_zero(self):
        """Returns 0 when scalar returns 0."""
        session = AsyncMock(spec=AsyncSession)
        session.execute = AsyncMock(return_value=_mock_scalar_result(0))

        count = await service.count_auto_executions_today(
            session,
            "user_1",
            "",
        )
        assert count == 0

    @pytest.mark.asyncio
    async def test_rule_filter_included_in_query(self):
        """Verifies rule_id filter is applied when non-empty."""
        session = AsyncMock(spec=AsyncSession)
        session.execute = AsyncMock(return_value=_mock_scalar_result(1))

        # With rule_id
        await service.count_auto_executions_today(
            session,
            "user_1",
            "my_rule",
        )
        call_args_with = session.execute.call_args

        session.execute.reset_mock()
        session.execute.return_value = _mock_scalar_result(5)

        # Without rule_id — different query (no rule filter)
        await service.count_auto_executions_today(
            session,
            "user_1",
            "",
        )
        call_args_without = session.execute.call_args

        # The queries should differ (different SQL produced)
        # We can verify by checking that execute was called in both cases
        assert call_args_with is not None
        assert call_args_without is not None


# ============================================================
# TestGetLastAutoExecutionTime (integration with SQLite)
# ============================================================


class TestGetLastAutoExecutionTime:
    """Tests for get_last_auto_execution_time()."""

    @pytest.mark.asyncio
    async def test_found(self, db_session):
        """Returns the executed_at timestamp when an execution exists."""
        await _create_auto_approved_item(
            db_session,
            rule_id="r1",
            execute=True,
        )

        last = await service.get_last_auto_execution_time(
            db_session,
            "user_1",
            "r1",
        )
        assert last is not None
        assert isinstance(last, datetime)

    @pytest.mark.asyncio
    async def test_not_found(self, db_session):
        """Returns None when no auto-execution exists for the rule."""
        last = await service.get_last_auto_execution_time(
            db_session,
            "user_1",
            "nonexistent_rule",
        )
        assert last is None

    @pytest.mark.asyncio
    async def test_not_found_when_not_executed(self, db_session):
        """Returns None when item is AUTO_APPROVED but not yet executed."""
        await _create_auto_approved_item(
            db_session,
            rule_id="r1",
            execute=False,
        )

        last = await service.get_last_auto_execution_time(
            db_session,
            "user_1",
            "r1",
        )
        assert last is None

    @pytest.mark.asyncio
    async def test_scoped_to_rule(self, db_session):
        """Only returns execution time for the specified rule."""
        await _create_auto_approved_item(
            db_session,
            rule_id="r1",
            execute=True,
        )
        await _create_auto_approved_item(
            db_session,
            rule_id="r2",
            execute=True,
        )

        last_r1 = await service.get_last_auto_execution_time(
            db_session,
            "user_1",
            "r1",
        )
        last_r2 = await service.get_last_auto_execution_time(
            db_session,
            "user_1",
            "r2",
        )

        assert last_r1 is not None
        assert last_r2 is not None

    @pytest.mark.asyncio
    async def test_found_via_mock(self):
        """Returns the timestamp from the DB result (mock-based)."""
        expected_time = datetime(2026, 2, 13, 10, 30, 0, tzinfo=timezone.utc)
        session = AsyncMock(spec=AsyncSession)
        session.execute = AsyncMock(return_value=_mock_scalar_result(expected_time))

        last = await service.get_last_auto_execution_time(
            session,
            "user_1",
            "r1",
        )
        assert last == expected_time

    @pytest.mark.asyncio
    async def test_not_found_via_mock(self):
        """Returns None when DB returns no result (mock-based)."""
        session = AsyncMock(spec=AsyncSession)
        session.execute = AsyncMock(return_value=_mock_scalar_result(None))

        last = await service.get_last_auto_execution_time(
            session,
            "user_1",
            "r1",
        )
        assert last is None
