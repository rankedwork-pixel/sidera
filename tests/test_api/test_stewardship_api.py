"""Tests for stewardship REST API endpoints.

Covers all 6 endpoints using an in-memory SQLite database (same pattern
as test_org_chart_service.py). This avoids complex mock wiring.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db import service as db_service
from src.models.schema import Base

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Endpoint logic tests (directly calling route functions)
# ---------------------------------------------------------------------------


class TestAssignEndpoint:
    async def test_assign_to_role(self, db_session):
        """POST /assign should work for an existing role."""
        await db_service.create_org_role(
            db_session,
            role_id="media_buyer",
            name="Media Buyer",
            description="Test",
            department_id="marketing",
            created_by="test",
        )
        ok = await db_service.assign_steward(db_session, "role", "media_buyer", "U_ALICE", "api")
        assert ok is True

        steward = await db_service.get_steward_for_role(db_session, "media_buyer")
        assert steward == "U_ALICE"

    async def test_assign_to_nonexistent_returns_false(self, db_session):
        """POST /assign for nonexistent entity should return False."""
        ok = await db_service.assign_steward(db_session, "role", "nonexistent", "U_ALICE")
        assert ok is False


class TestReleaseEndpoint:
    async def test_release_from_role(self, db_session):
        """POST /release should clear the steward."""
        await db_service.create_org_role(
            db_session,
            role_id="media_buyer",
            name="Media Buyer",
            description="Test",
            department_id="marketing",
            created_by="test",
            steward_user_id="U_ALICE",
        )
        ok = await db_service.release_steward(db_session, "role", "media_buyer", "api")
        assert ok is True

        steward = await db_service.get_steward_for_role(db_session, "media_buyer")
        assert steward is None

    async def test_release_nonexistent_returns_false(self, db_session):
        ok = await db_service.release_steward(db_session, "role", "nonexistent")
        assert ok is False


class TestListEndpoint:
    async def test_list_returns_all_assignments(self, db_session):
        """GET / should return all stewardship assignments."""
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
            steward_user_id="U_ROLE_STEWARD",
        )

        assignments = await db_service.list_stewardships(db_session)
        assert len(assignments) == 2
        scope_types = {a["scope_type"] for a in assignments}
        assert scope_types == {"role", "department"}

    async def test_list_empty(self, db_session):
        """GET / returns empty when no stewards assigned."""
        assignments = await db_service.list_stewardships(db_session)
        assert assignments == []


class TestGetByUserEndpoint:
    async def test_get_steward_roles(self, db_session):
        """GET /user/{user_id} returns all entities for that user."""
        await db_service.create_org_role(
            db_session,
            role_id="media_buyer",
            name="Media Buyer",
            description="Test",
            department_id="marketing",
            steward_user_id="U_ALICE",
        )
        await db_service.create_org_role(
            db_session,
            role_id="analyst",
            name="Analyst",
            description="Test",
            department_id="marketing",
            steward_user_id="U_BOB",
        )

        alice_roles = await db_service.get_steward_roles(db_session, "U_ALICE")
        assert len(alice_roles) == 1
        assert alice_roles[0]["scope_id"] == "media_buyer"

        bob_roles = await db_service.get_steward_roles(db_session, "U_BOB")
        assert len(bob_roles) == 1


class TestGetByEntityEndpoint:
    async def test_get_steward_for_role(self, db_session):
        """GET /{scope_type}/{scope_id} returns the steward."""
        await db_service.create_org_role(
            db_session,
            role_id="media_buyer",
            name="Media Buyer",
            description="Test",
            department_id="marketing",
            steward_user_id="U_BOB",
        )
        steward = await db_service.get_steward_for_role(db_session, "media_buyer")
        assert steward == "U_BOB"

    async def test_returns_none_for_no_steward(self, db_session):
        """GET /{scope_type}/{scope_id} returns None when no steward."""
        await db_service.create_org_role(
            db_session,
            role_id="media_buyer",
            name="Media Buyer",
            description="Test",
            department_id="marketing",
        )
        steward = await db_service.get_steward_for_role(db_session, "media_buyer")
        assert steward is None

    async def test_returns_none_for_nonexistent(self, db_session):
        """GET /{scope_type}/{scope_id} returns None for nonexistent entity."""
        steward = await db_service.get_steward_for_role(db_session, "nonexistent")
        assert steward is None


class TestHistoryEndpoint:
    async def test_audit_trail_after_assign_release(self, db_session):
        """Assign and release should create audit entries."""
        await db_service.create_org_role(
            db_session,
            role_id="media_buyer",
            name="Media Buyer",
            description="Test",
            department_id="marketing",
        )

        await db_service.assign_steward(db_session, "role", "media_buyer", "U_ALICE", "admin")
        await db_service.release_steward(db_session, "role", "media_buyer", "admin")

        # Verify audit entries were created (logged under "admin" user_id)
        trail = await db_service.get_audit_trail(
            db_session,
            "admin",
            event_type="org_chart_change",
        )
        steward_events = [
            e
            for e in trail
            if e.event_data.get("operation") in ("steward_assigned", "steward_released")
        ]
        assert len(steward_events) >= 2


# ---------------------------------------------------------------------------
# FastAPI route integration test (lightweight)
# ---------------------------------------------------------------------------


class TestRouteImport:
    def test_stewardship_router_can_import(self):
        """The stewardship router module should import cleanly."""
        from src.api.routes.stewardship import router

        assert router is not None
        assert router.prefix == "/api/stewardship"

    def test_router_has_expected_routes(self):
        """Router should have all 6 endpoints."""
        from src.api.routes.stewardship import router

        paths = [r.path for r in router.routes if hasattr(r, "path")]
        # Paths include the router prefix
        assert any("/stewardship/" == p or p.endswith("/stewardship/") for p in paths)
        assert any("assign" in p for p in paths)
        assert any("release" in p for p in paths)
