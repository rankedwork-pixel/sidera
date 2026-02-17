"""Tests for stewardship DB service methods.

Covers assign, release, resolve (role-level, dept fallback, both empty),
list_stewardships, get_steward_roles, and steward_user_id persistence
on approval_queue and audit_log.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db import service
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


# ============================================================
# Helpers
# ============================================================


async def _create_role(session, role_id="test_role", dept_id="test_dept", steward=""):
    """Helper to create a role in the DB."""
    return await service.create_org_role(
        session,
        role_id=role_id,
        name=f"Role {role_id}",
        description=f"Test role {role_id}",
        department_id=dept_id,
        created_by="test",
        steward_user_id=steward,
    )


async def _create_dept(session, dept_id="test_dept", steward=""):
    """Helper to create a department in the DB."""
    return await service.create_org_department(
        session,
        dept_id=dept_id,
        name=f"Dept {dept_id}",
        description=f"Test dept {dept_id}",
        created_by="test",
        steward_user_id=steward,
    )


# ============================================================
# get_steward_for_role
# ============================================================


class TestGetStewardForRole:
    async def test_returns_steward_when_set(self, db_session):
        await _create_role(db_session, steward="U_ALICE")
        result = await service.get_steward_for_role(db_session, "test_role")
        assert result == "U_ALICE"

    async def test_returns_none_when_empty(self, db_session):
        await _create_role(db_session, steward="")
        result = await service.get_steward_for_role(db_session, "test_role")
        assert result is None

    async def test_returns_none_for_nonexistent_role(self, db_session):
        result = await service.get_steward_for_role(db_session, "no_such_role")
        assert result is None


# ============================================================
# get_steward_for_department
# ============================================================


class TestGetStewardForDepartment:
    async def test_returns_steward_when_set(self, db_session):
        await _create_dept(db_session, steward="U_BOB")
        result = await service.get_steward_for_department(db_session, "test_dept")
        assert result == "U_BOB"

    async def test_returns_none_when_empty(self, db_session):
        await _create_dept(db_session, steward="")
        result = await service.get_steward_for_department(db_session, "test_dept")
        assert result is None


# ============================================================
# resolve_steward
# ============================================================


class TestResolveSteward:
    async def test_returns_role_steward_first(self, db_session):
        await _create_dept(db_session, steward="U_DEPT")
        await _create_role(db_session, steward="U_ROLE")

        result = await service.resolve_steward(db_session, "test_role", "test_dept")
        assert result == "U_ROLE"

    async def test_falls_back_to_department_steward(self, db_session):
        await _create_dept(db_session, steward="U_DEPT")
        await _create_role(db_session, steward="")

        result = await service.resolve_steward(db_session, "test_role", "test_dept")
        assert result == "U_DEPT"

    async def test_returns_none_when_neither_set(self, db_session):
        await _create_dept(db_session, steward="")
        await _create_role(db_session, steward="")

        result = await service.resolve_steward(db_session, "test_role", "test_dept")
        assert result is None

    async def test_returns_none_for_empty_role_id(self, db_session):
        result = await service.resolve_steward(db_session, "", "")
        assert result is None


# ============================================================
# assign_steward
# ============================================================


class TestAssignSteward:
    async def test_assign_to_role(self, db_session):
        await _create_role(db_session)
        ok = await service.assign_steward(db_session, "role", "test_role", "U_NEW", "admin")
        assert ok is True

        steward = await service.get_steward_for_role(db_session, "test_role")
        assert steward == "U_NEW"

    async def test_assign_to_department(self, db_session):
        await _create_dept(db_session)
        ok = await service.assign_steward(db_session, "department", "test_dept", "U_NEW", "admin")
        assert ok is True

        steward = await service.get_steward_for_department(db_session, "test_dept")
        assert steward == "U_NEW"

    async def test_assign_to_nonexistent_returns_false(self, db_session):
        ok = await service.assign_steward(db_session, "role", "no_such", "U_NEW")
        assert ok is False

    async def test_assign_replaces_existing(self, db_session):
        await _create_role(db_session, steward="U_OLD")
        ok = await service.assign_steward(db_session, "role", "test_role", "U_NEW")
        assert ok is True

        steward = await service.get_steward_for_role(db_session, "test_role")
        assert steward == "U_NEW"

    async def test_assign_invalid_scope_type_returns_false(self, db_session):
        ok = await service.assign_steward(db_session, "skill", "test_skill", "U_NEW")
        assert ok is False


# ============================================================
# release_steward
# ============================================================


class TestReleaseSteward:
    async def test_release_from_role(self, db_session):
        await _create_role(db_session, steward="U_ALICE")
        ok = await service.release_steward(db_session, "role", "test_role", "admin")
        assert ok is True

        steward = await service.get_steward_for_role(db_session, "test_role")
        assert steward is None

    async def test_release_from_department(self, db_session):
        await _create_dept(db_session, steward="U_BOB")
        ok = await service.release_steward(db_session, "department", "test_dept", "admin")
        assert ok is True

        steward = await service.get_steward_for_department(db_session, "test_dept")
        assert steward is None

    async def test_release_nonexistent_returns_false(self, db_session):
        ok = await service.release_steward(db_session, "role", "no_such")
        assert ok is False


# ============================================================
# list_stewardships
# ============================================================


class TestListStewardships:
    async def test_returns_roles_and_departments(self, db_session):
        await _create_dept(db_session, dept_id="dept_a", steward="U_A")
        await _create_role(db_session, role_id="role_b", dept_id="dept_a", steward="U_B")

        items = await service.list_stewardships(db_session)
        assert len(items) == 2

        scope_types = {i["scope_type"] for i in items}
        assert scope_types == {"role", "department"}

    async def test_excludes_empty_stewards(self, db_session):
        await _create_dept(db_session, steward="")
        await _create_role(db_session, steward="")

        items = await service.list_stewardships(db_session)
        assert len(items) == 0

    async def test_excludes_inactive_entities(self, db_session):
        role = await _create_role(db_session, steward="U_ACTIVE")
        role.is_active = False
        await db_session.flush()

        items = await service.list_stewardships(db_session)
        # Should not include the inactive role
        role_items = [i for i in items if i["scope_type"] == "role"]
        assert len(role_items) == 0


# ============================================================
# get_steward_roles
# ============================================================


class TestGetStewardRoles:
    async def test_returns_all_entities_for_user(self, db_session):
        await _create_dept(db_session, dept_id="dept_1", steward="U_ALICE")
        await _create_role(db_session, role_id="role_1", dept_id="dept_1", steward="U_ALICE")
        await _create_role(db_session, role_id="role_2", dept_id="dept_1", steward="U_BOB")

        items = await service.get_steward_roles(db_session, "U_ALICE")
        assert len(items) == 2  # dept_1 + role_1

    async def test_returns_empty_when_no_assignments(self, db_session):
        items = await service.get_steward_roles(db_session, "U_NOBODY")
        assert items == []


# ============================================================
# steward_user_id on log_event and create_approval
# ============================================================


class TestStewardOnAuditAndApproval:
    async def test_log_event_stores_steward(self, db_session):
        log = await service.log_event(
            db_session,
            user_id="test_user",
            event_type="test_event",
            event_data={"key": "val"},
            steward_user_id="U_STEWARD",
        )
        assert log.steward_user_id == "U_STEWARD"

    async def test_create_approval_stores_steward(self, db_session):
        item = await service.create_approval(
            db_session,
            analysis_id=0,
            user_id="test_user",
            action_type="budget_change",
            account_id=0,
            description="Test",
            reasoning="Test reason",
            action_params={"platform": "google_ads"},
            steward_user_id="U_STEWARD",
        )
        assert item.steward_user_id == "U_STEWARD"

    async def test_create_approval_default_empty_steward(self, db_session):
        item = await service.create_approval(
            db_session,
            analysis_id=0,
            user_id="test_user",
            action_type="budget_change",
            account_id=0,
            description="Test",
            reasoning="Test reason",
            action_params={"platform": "google_ads"},
        )
        # Default is empty or None
        assert item.steward_user_id in ("", None)


# ============================================================
# steward_note TTL override in save_memory
# ============================================================


class TestStewardNoteTTL:
    async def test_steward_note_never_expires(self, db_session):
        """Steward notes should have expires_at=None (never expire)."""
        mem = await service.save_memory(
            db_session,
            user_id="steward_user",
            role_id="test_role",
            department_id="test_dept",
            memory_type="steward_note",
            title="Stay aggressive on ROAS",
            content="Always prioritize ROAS over volume for this account.",
            confidence=1.0,
            ttl_days=90,  # Caller passes 90, but should be overridden to 0
        )
        # ttl_days=0 → expires_at is None (never archived)
        assert mem.expires_at is None

    async def test_normal_memory_has_expiry(self, db_session):
        """Non-steward memories should have a non-None expires_at."""
        mem = await service.save_memory(
            db_session,
            user_id="test_user",
            role_id="test_role",
            department_id="test_dept",
            memory_type="insight",
            title="Some insight",
            content="Interesting pattern noticed.",
            confidence=0.8,
            ttl_days=90,
        )
        assert mem.expires_at is not None
