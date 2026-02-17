"""Tests for steward routing in the approval flow.

Verifies that the steward is resolved and included in approval items.
Uses direct DB calls (same SQLite pattern as other DB tests) rather than
mocking the full process_recommendations Inngest pipeline.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db import service as db_service
from src.models.schema import Base


@pytest.fixture
async def db_session():
    """Create an in-memory SQLite database and yield a session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


class TestStewardResolveInApproval:
    """Steward resolution logic used by the approval flow."""

    async def test_resolve_steward_for_role(self, db_session):
        """resolve_steward returns role-level steward."""
        await db_service.create_org_department(
            db_session, dept_id="marketing", name="Marketing", description="Test"
        )
        await db_service.create_org_role(
            db_session,
            role_id="media_buyer",
            name="Media Buyer",
            description="Test",
            department_id="marketing",
            steward_user_id="U_ROLE_STEWARD",
        )

        steward = await db_service.resolve_steward(db_session, "media_buyer", "marketing")
        assert steward == "U_ROLE_STEWARD"

    async def test_resolve_steward_dept_fallback(self, db_session):
        """resolve_steward falls back to department steward when role has none."""
        await db_service.create_org_department(
            db_session,
            dept_id="marketing",
            name="Marketing",
            description="Test",
            steward_user_id="U_DEPT_STEWARD",
        )
        await db_service.create_org_role(
            db_session,
            role_id="media_buyer",
            name="Media Buyer",
            description="Test",
            department_id="marketing",
            steward_user_id="",
        )

        steward = await db_service.resolve_steward(db_session, "media_buyer", "marketing")
        assert steward == "U_DEPT_STEWARD"

    async def test_resolve_steward_none_when_both_empty(self, db_session):
        """resolve_steward returns None when neither role nor dept has steward."""
        await db_service.create_org_department(
            db_session, dept_id="marketing", name="Marketing", description="Test"
        )
        await db_service.create_org_role(
            db_session,
            role_id="media_buyer",
            name="Media Buyer",
            description="Test",
            department_id="marketing",
        )

        steward = await db_service.resolve_steward(db_session, "media_buyer", "marketing")
        assert steward is None

    async def test_approval_item_stores_steward(self, db_session):
        """create_approval should store steward_user_id on the item."""
        item = await db_service.create_approval(
            db_session,
            analysis_id=0,
            user_id="test_user",
            action_type="budget_change",
            account_id=0,
            description="Increase budget",
            reasoning="Strong ROAS",
            action_params={"platform": "google_ads"},
            steward_user_id="U_STEWARD",
        )
        assert item.steward_user_id == "U_STEWARD"

    async def test_approval_item_empty_steward_by_default(self, db_session):
        """create_approval defaults to empty steward when not provided."""
        item = await db_service.create_approval(
            db_session,
            analysis_id=0,
            user_id="test_user",
            action_type="budget_change",
            account_id=0,
            description="Increase budget",
            reasoning="Strong ROAS",
            action_params={"platform": "google_ads"},
        )
        assert item.steward_user_id in ("", None)


class TestStewardMentionInSlack:
    """Verify Slack connector accepts steward_mention parameter."""

    def test_send_approval_request_accepts_steward_mention(self):
        """The connector method signature accepts steward_mention."""
        import inspect

        from src.connectors.slack import SlackConnector

        sig = inspect.signature(SlackConnector.send_approval_request)
        assert "steward_mention" in sig.parameters

    def test_steward_mention_default_is_empty(self):
        """steward_mention defaults to empty string."""
        import inspect

        from src.connectors.slack import SlackConnector

        sig = inspect.signature(SlackConnector.send_approval_request)
        default = sig.parameters["steward_mention"].default
        assert default == ""
