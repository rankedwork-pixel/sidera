"""Tests for data retention purge methods in db/service.py."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.db import service as db_service


def _utcnow() -> datetime:
    """Naive UTC datetime for tests (matches db/service._utcnow)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_mock_session(rowcount: int = 5) -> AsyncMock:
    """Create a mock AsyncSession whose execute returns the given rowcount."""
    mock_result = MagicMock()
    mock_result.rowcount = rowcount

    session = AsyncMock()
    session.execute.return_value = mock_result
    session.flush = AsyncMock()
    return session


# ===================================================================
# purge_old_audit_logs
# ===================================================================


class TestPurgeAuditLogs:
    @pytest.mark.asyncio
    async def test_deletes_old_entries(self):
        session = _make_mock_session(rowcount=42)
        cutoff = _utcnow() - timedelta(days=365)

        count = await db_service.purge_old_audit_logs(session, cutoff)

        assert count == 42
        session.execute.assert_called_once()
        session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_zero_when_nothing_to_delete(self):
        session = _make_mock_session(rowcount=0)
        cutoff = _utcnow() - timedelta(days=365)

        count = await db_service.purge_old_audit_logs(session, cutoff)
        assert count == 0


# ===================================================================
# purge_old_analysis_results
# ===================================================================


class TestPurgeAnalysisResults:
    @pytest.mark.asyncio
    async def test_deletes_old_entries(self):
        session = _make_mock_session(rowcount=15)
        cutoff = _utcnow() - timedelta(days=180)

        count = await db_service.purge_old_analysis_results(session, cutoff)
        assert count == 15


# ===================================================================
# purge_old_cost_tracking
# ===================================================================


class TestPurgeCostTracking:
    @pytest.mark.asyncio
    async def test_deletes_old_entries(self):
        session = _make_mock_session(rowcount=8)
        cutoff = _utcnow() - timedelta(days=180)

        count = await db_service.purge_old_cost_tracking(session, cutoff)
        assert count == 8


# ===================================================================
# purge_decided_approvals
# ===================================================================


class TestPurgeDecidedApprovals:
    @pytest.mark.asyncio
    async def test_deletes_non_pending_old_entries(self):
        session = _make_mock_session(rowcount=20)
        cutoff = _utcnow() - timedelta(days=90)

        count = await db_service.purge_decided_approvals(session, cutoff)
        assert count == 20


# ===================================================================
# purge_resolved_failed_runs
# ===================================================================


class TestPurgeResolvedFailedRuns:
    @pytest.mark.asyncio
    async def test_deletes_resolved_old_entries(self):
        session = _make_mock_session(rowcount=3)
        cutoff = _utcnow() - timedelta(days=30)

        count = await db_service.purge_resolved_failed_runs(session, cutoff)
        assert count == 3


# ===================================================================
# purge_old_daily_metrics
# ===================================================================


class TestPurgeDailyMetrics:
    @pytest.mark.asyncio
    async def test_deletes_old_entries(self):
        session = _make_mock_session(rowcount=100)
        cutoff_date = date.today() - timedelta(days=365)

        count = await db_service.purge_old_daily_metrics(session, cutoff_date)
        assert count == 100


# ===================================================================
# purge_inactive_threads
# ===================================================================


class TestPurgeInactiveThreads:
    @pytest.mark.asyncio
    async def test_deletes_inactive_old_threads(self):
        session = _make_mock_session(rowcount=7)
        cutoff = _utcnow() - timedelta(days=30)

        count = await db_service.purge_inactive_threads(session, cutoff)
        assert count == 7


# ===================================================================
# purge_archived_memories
# ===================================================================


class TestPurgeArchivedMemories:
    @pytest.mark.asyncio
    async def test_deletes_old_memories(self):
        session = _make_mock_session(rowcount=12)
        cutoff = _utcnow() - timedelta(days=365)

        count = await db_service.purge_archived_memories(session, cutoff)
        assert count == 12

    @pytest.mark.asyncio
    async def test_returns_zero_when_nothing_to_delete(self):
        session = _make_mock_session(rowcount=0)
        cutoff = _utcnow() - timedelta(days=365)

        count = await db_service.purge_archived_memories(session, cutoff)
        assert count == 0


# ===================================================================
# GDPR export_user_data
# ===================================================================


class TestExportUserData:
    @pytest.mark.asyncio
    async def test_exports_all_user_data(self):
        """export_user_data returns structured data from all tables."""
        mock_user = MagicMock()
        mock_user.user_id = "U123"
        mock_user.display_name = "Test User"
        mock_user.email = "test@example.com"
        mock_user.role = MagicMock(value="admin")
        mock_user.is_active = True
        mock_user.created_at = _utcnow()

        # Mock session that returns empty result sets for related tables
        session = AsyncMock()

        # get_user is a module-level function, patch at module level
        with patch(
            "src.db.service.get_user",
            new_callable=AsyncMock,
            return_value=mock_user,
        ):
            # Mock the select queries — return empty scalars for accounts/logs/approvals/threads
            mock_scalars = MagicMock()
            mock_scalars.all.return_value = []
            mock_result = MagicMock()
            mock_result.scalars.return_value = mock_scalars
            session.execute.return_value = mock_result

            data = await db_service.export_user_data(session, "U123")

        assert data["user_id"] == "U123"
        assert data["user"]["display_name"] == "Test User"
        assert data["user"]["email"] == "test@example.com"
        assert "accounts" in data
        assert "audit_log" in data
        assert "approvals" in data
        assert "conversation_threads" in data


# ===================================================================
# GDPR delete_user_data
# ===================================================================


class TestDeleteUserData:
    @pytest.mark.asyncio
    async def test_deletes_and_anonymizes(self):
        """delete_user_data removes user data and anonymizes audit log."""
        mock_result = MagicMock()
        mock_result.rowcount = 3

        session = AsyncMock()
        session.execute.return_value = mock_result
        session.flush = AsyncMock()

        counts = await db_service.delete_user_data(session, "U123")

        assert counts["conversation_threads"] == 3
        assert counts["approvals"] == 3
        assert counts["accounts"] == 3
        assert counts["audit_log_anonymized"] == 3
        assert counts["user"] == 3
        # 5 execute calls: delete threads, delete approvals, delete accounts,
        # update audit log, delete user
        assert session.execute.call_count == 5
