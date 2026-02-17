"""Postgres integration tests — real DB round-trips.

These tests connect to the ACTUAL Postgres database configured in .env
and verify that critical operations work end-to-end. They catch issues
like enum casing mismatches, timezone handling, and FK constraints that
SQLite in-memory tests cannot detect.

Run with: pytest tests/test_integration/test_postgres_roundtrip.py -v

Skip when no DB configured: tests auto-skip if DATABASE_URL is not set.
"""

import pytest

from src.config import settings

# Skip the entire module if no database is configured
pytestmark = pytest.mark.skipif(
    not settings.database_url,
    reason="DATABASE_URL not configured — skipping Postgres integration tests",
)


@pytest.fixture(autouse=True)
def _reset_db_engine():
    """Reset the cached SQLAlchemy engine between tests.

    The engine caches a connection pool bound to the event loop from the
    first test. Since pytest-asyncio creates a new loop per test, we
    need to dispose the old engine so a fresh one is created.
    """
    yield
    import src.db.session as sess

    if sess._engine is not None:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if not loop.is_closed():
                loop.run_until_complete(sess._engine.dispose())
        except Exception:
            pass
        sess._engine = None
        sess._session_factory = None


class TestApprovalCreationRoundTrip:
    """Test that approvals can be created and read back from real Postgres."""

    @pytest.mark.asyncio
    async def test_create_approval_with_enable_campaign(self):
        """Catch enum casing bugs: enable_campaign must round-trip."""
        from src.db import service as db_service
        from src.db.session import get_db_session

        async with get_db_session() as session:
            item = await db_service.create_approval(
                session=session,
                analysis_id=None,
                user_id="test-integration-user",
                action_type="enable_campaign",
                account_id=None,
                description="Integration test: enable campaign",
                reasoning="Testing enum round-trip",
                action_params={
                    "platform": "google_ads",
                    "customer_id": "1234567890",
                    "campaign_id": "99999",
                },
                projected_impact="Test impact",
                risk_assessment="low",
            )
            assert item.id > 0
            db_id = item.id

        # Read it back in a fresh session
        async with get_db_session() as session:
            retrieved = await db_service.get_approval_by_id(
                session,
                db_id,
            )
            assert retrieved is not None
            assert retrieved.action_type.value == "enable_campaign"
            assert retrieved.description == "Integration test: enable campaign"
            assert retrieved.action_params["platform"] == "google_ads"

    @pytest.mark.asyncio
    async def test_create_approval_with_budget_change(self):
        """Verify budget_change enum value round-trips correctly."""
        from src.db import service as db_service
        from src.db.session import get_db_session

        async with get_db_session() as session:
            item = await db_service.create_approval(
                session=session,
                analysis_id=None,
                user_id="test-integration-user",
                action_type="budget_change",
                account_id=None,
                description="Integration test: budget change",
                reasoning="Testing budget enum",
                action_params={
                    "platform": "google_ads",
                    "customer_id": "1234567890",
                    "campaign_id": "99999",
                    "new_budget_micros": 10000000,
                },
            )
            assert item.id > 0

    @pytest.mark.asyncio
    async def test_create_approval_all_action_types(self):
        """Every ActionType enum value must insert without error."""
        from src.db import service as db_service
        from src.db.session import get_db_session
        from src.models.schema import ActionType

        for action_type in ActionType:
            async with get_db_session() as session:
                item = await db_service.create_approval(
                    session=session,
                    analysis_id=None,
                    user_id="test-integration-user",
                    action_type=action_type.value,
                    account_id=None,
                    description=f"Test {action_type.value}",
                    reasoning="Enum round-trip test",
                    action_params={"test": True},
                )
                assert item.id > 0, (
                    f"Failed to create approval with action_type={action_type.value}"
                )

    @pytest.mark.asyncio
    async def test_approval_status_update_round_trip(self):
        """Verify ApprovalStatus enum values work for updates."""
        from src.db import service as db_service
        from src.db.session import get_db_session
        from src.models.schema import ApprovalStatus

        async with get_db_session() as session:
            # Create
            item = await db_service.create_approval(
                session=session,
                analysis_id=None,
                user_id="test-integration-user",
                action_type="budget_change",
                account_id=None,
                description="Test status update",
                reasoning="Testing",
                action_params={"test": True},
            )
            db_id = item.id
            assert db_id > 0

            # Update to APPROVED in same session
            updated = await db_service.update_approval_status(
                session=session,
                approval_id=db_id,
                status=ApprovalStatus.APPROVED,
                decided_by="test-user",
            )
            assert updated is not None
            assert updated.status == ApprovalStatus.APPROVED

            # Read back and verify
            retrieved = await db_service.get_approval_by_id(
                session,
                db_id,
            )
            assert retrieved is not None
            assert retrieved.status.value == "approved"


class TestConversationThreadRoundTrip:
    """Test that conversation threads work with real Postgres."""

    @pytest.mark.asyncio
    async def test_create_and_update_thread(self):
        """Verify thread creation and activity update (timezone safe)."""
        import time

        from src.db import service as db_service
        from src.db.session import get_db_session

        test_ts = f"test-{time.time()}"

        # Create
        async with get_db_session() as session:
            thread = await db_service.create_conversation_thread(
                session=session,
                thread_ts=test_ts,
                channel_id="C_TEST",
                role_id="test_role",
                user_id="U_TEST",
            )
            assert thread.id > 0

        # Update activity (this is where the timezone bug was)
        async with get_db_session() as session:
            await db_service.update_conversation_thread_activity(
                session=session,
                thread_ts=test_ts,
                cost_increment=0.05,
            )

        # Read back
        async with get_db_session() as session:
            retrieved = await db_service.get_conversation_thread(
                session,
                test_ts,
            )
            assert retrieved is not None
            assert retrieved.turn_count == 1
            assert float(retrieved.total_cost_usd) == pytest.approx(
                0.05,
                abs=0.001,
            )
