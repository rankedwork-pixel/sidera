"""Tests for role message DB service methods.

Covers:
- RoleMessage schema columns
- MessageStatus enum values
- create_role_message() — creates with correct fields, default expiry
- get_pending_messages() — retrieval, filtering, ordering
- mark_messages_delivered() — bulk status update
- mark_message_read() — single message status update
- get_message_thread() — thread traversal via reply_to_id
- expire_stale_messages() — expiry of old pending messages
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.db import service as db_service
from src.models.schema import Base, MessageStatus, RoleMessage


@pytest.fixture
async def session():
    """Create an in-memory SQLite database and yield a session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with async_session() as s:
        yield s
    await engine.dispose()


# ============================================================
# Schema Tests
# ============================================================


class TestRoleMessageSchema:
    def test_has_expected_columns(self):
        column_names = [c.name for c in RoleMessage.__table__.columns]
        expected = [
            "id",
            "from_role_id",
            "to_role_id",
            "from_department_id",
            "to_department_id",
            "subject",
            "content",
            "status",
            "reply_to_id",
            "created_at",
            "delivered_at",
            "read_at",
            "expires_at",
            "metadata",
        ]
        for col in expected:
            assert col in column_names, f"Missing column: {col}"

    def test_tablename(self):
        assert RoleMessage.__tablename__ == "role_messages"


class TestMessageStatusEnum:
    def test_pending(self):
        assert MessageStatus.PENDING == "pending"

    def test_delivered(self):
        assert MessageStatus.DELIVERED == "delivered"

    def test_read(self):
        assert MessageStatus.READ == "read"

    def test_expired(self):
        assert MessageStatus.EXPIRED == "expired"

    def test_all_values(self):
        values = {s.value for s in MessageStatus}
        assert values == {"pending", "delivered", "read", "expired"}


# ============================================================
# create_role_message tests
# ============================================================


class TestCreateRoleMessage:
    @pytest.mark.asyncio
    async def test_creates_message(self, session):
        msg_id = await db_service.create_role_message(
            session,
            from_role_id="head_of_it",
            to_role_id="sysadmin",
            from_department_id="it",
            to_department_id="it",
            subject="Cost alert",
            content="Costs spiked 3x.",
        )
        assert isinstance(msg_id, int)
        assert msg_id > 0

    @pytest.mark.asyncio
    async def test_default_expiry(self, session):
        msg_id = await db_service.create_role_message(
            session,
            from_role_id="head_of_it",
            to_role_id="sysadmin",
            from_department_id="it",
            to_department_id="it",
            subject="Test",
            content="Content",
        )
        # Retrieve and check expiry is roughly 7 days from now
        from sqlalchemy import select

        stmt = select(RoleMessage).where(RoleMessage.id == msg_id)
        result = await session.execute(stmt)
        msg = result.scalar_one()
        assert msg.expires_at is not None
        # Should be approximately 7 days from now (within 1 minute)
        expected_expiry = datetime.now(timezone.utc) + timedelta(days=7)
        diff = abs((msg.expires_at.replace(tzinfo=timezone.utc) - expected_expiry).total_seconds())
        assert diff < 120  # Within 2 minutes

    @pytest.mark.asyncio
    async def test_custom_expiry(self, session):
        msg_id = await db_service.create_role_message(
            session,
            from_role_id="a",
            to_role_id="b",
            from_department_id="x",
            to_department_id="y",
            subject="Custom",
            content="Content",
            expires_in_days=1,
        )
        from sqlalchemy import select

        stmt = select(RoleMessage).where(RoleMessage.id == msg_id)
        result = await session.execute(stmt)
        msg = result.scalar_one()
        expected_expiry = datetime.now(timezone.utc) + timedelta(days=1)
        diff = abs((msg.expires_at.replace(tzinfo=timezone.utc) - expected_expiry).total_seconds())
        assert diff < 120

    @pytest.mark.asyncio
    async def test_reply_to_id(self, session):
        msg1_id = await db_service.create_role_message(
            session,
            from_role_id="a",
            to_role_id="b",
            from_department_id="x",
            to_department_id="y",
            subject="Original",
            content="Original content",
        )
        msg2_id = await db_service.create_role_message(
            session,
            from_role_id="b",
            to_role_id="a",
            from_department_id="y",
            to_department_id="x",
            subject="Re: Original",
            content="Reply content",
            reply_to_id=msg1_id,
        )
        from sqlalchemy import select

        stmt = select(RoleMessage).where(RoleMessage.id == msg2_id)
        result = await session.execute(stmt)
        msg = result.scalar_one()
        assert msg.reply_to_id == msg1_id

    @pytest.mark.asyncio
    async def test_metadata_stored(self, session):
        msg_id = await db_service.create_role_message(
            session,
            from_role_id="a",
            to_role_id="b",
            from_department_id="x",
            to_department_id="y",
            subject="Meta",
            content="With metadata",
            metadata={"source": "test"},
        )
        from sqlalchemy import select

        stmt = select(RoleMessage).where(RoleMessage.id == msg_id)
        result = await session.execute(stmt)
        msg = result.scalar_one()
        assert msg.metadata_ is not None
        assert msg.metadata_["source"] == "test"


# ============================================================
# get_pending_messages tests
# ============================================================


class TestGetPendingMessages:
    @pytest.mark.asyncio
    async def test_empty_when_none(self, session):
        msgs = await db_service.get_pending_messages(
            session,
            "nonexistent_role",
        )
        assert msgs == []

    @pytest.mark.asyncio
    async def test_returns_pending_for_role(self, session):
        await db_service.create_role_message(
            session,
            from_role_id="a",
            to_role_id="target",
            from_department_id="x",
            to_department_id="y",
            subject="Msg 1",
            content="C1",
        )
        await db_service.create_role_message(
            session,
            from_role_id="b",
            to_role_id="target",
            from_department_id="x",
            to_department_id="y",
            subject="Msg 2",
            content="C2",
        )
        msgs = await db_service.get_pending_messages(session, "target")
        assert len(msgs) == 2

    @pytest.mark.asyncio
    async def test_isolates_by_role(self, session):
        await db_service.create_role_message(
            session,
            from_role_id="a",
            to_role_id="role_a",
            from_department_id="x",
            to_department_id="y",
            subject="For A",
            content="CA",
        )
        await db_service.create_role_message(
            session,
            from_role_id="a",
            to_role_id="role_b",
            from_department_id="x",
            to_department_id="y",
            subject="For B",
            content="CB",
        )
        msgs_a = await db_service.get_pending_messages(session, "role_a")
        msgs_b = await db_service.get_pending_messages(session, "role_b")
        assert len(msgs_a) == 1
        assert msgs_a[0].subject == "For A"
        assert len(msgs_b) == 1
        assert msgs_b[0].subject == "For B"

    @pytest.mark.asyncio
    async def test_limit_enforced(self, session):
        for i in range(5):
            await db_service.create_role_message(
                session,
                from_role_id="a",
                to_role_id="target",
                from_department_id="x",
                to_department_id="y",
                subject=f"Msg {i}",
                content=f"Content {i}",
            )
        msgs = await db_service.get_pending_messages(
            session,
            "target",
            limit=3,
        )
        assert len(msgs) == 3

    @pytest.mark.asyncio
    async def test_excludes_delivered(self, session):
        msg_id = await db_service.create_role_message(
            session,
            from_role_id="a",
            to_role_id="target",
            from_department_id="x",
            to_department_id="y",
            subject="Will deliver",
            content="Content",
        )
        await db_service.mark_messages_delivered(session, [msg_id])
        msgs = await db_service.get_pending_messages(session, "target")
        assert len(msgs) == 0


# ============================================================
# mark_messages_delivered tests
# ============================================================


class TestMarkMessagesDelivered:
    @pytest.mark.asyncio
    async def test_marks_delivered(self, session):
        msg_id = await db_service.create_role_message(
            session,
            from_role_id="a",
            to_role_id="b",
            from_department_id="x",
            to_department_id="y",
            subject="Test",
            content="Content",
        )
        count = await db_service.mark_messages_delivered(session, [msg_id])
        assert count == 1

        from sqlalchemy import select

        stmt = select(RoleMessage).where(RoleMessage.id == msg_id)
        result = await session.execute(stmt)
        msg = result.scalar_one()
        assert msg.status == MessageStatus.DELIVERED.value
        assert msg.delivered_at is not None

    @pytest.mark.asyncio
    async def test_empty_list(self, session):
        count = await db_service.mark_messages_delivered(session, [])
        assert count == 0


# ============================================================
# mark_message_read tests
# ============================================================


class TestMarkMessageRead:
    @pytest.mark.asyncio
    async def test_marks_read(self, session):
        msg_id = await db_service.create_role_message(
            session,
            from_role_id="a",
            to_role_id="b",
            from_department_id="x",
            to_department_id="y",
            subject="Test",
            content="Content",
        )
        await db_service.mark_message_read(session, msg_id)

        from sqlalchemy import select

        stmt = select(RoleMessage).where(RoleMessage.id == msg_id)
        result = await session.execute(stmt)
        msg = result.scalar_one()
        assert msg.status == MessageStatus.READ.value
        assert msg.read_at is not None


# ============================================================
# get_message_thread tests
# ============================================================


class TestGetMessageThread:
    @pytest.mark.asyncio
    async def test_single_message_thread(self, session):
        msg_id = await db_service.create_role_message(
            session,
            from_role_id="a",
            to_role_id="b",
            from_department_id="x",
            to_department_id="y",
            subject="Root",
            content="Root msg",
        )
        thread = await db_service.get_message_thread(session, msg_id)
        assert len(thread) == 1
        assert thread[0].subject == "Root"

    @pytest.mark.asyncio
    async def test_multi_message_thread(self, session):
        msg1 = await db_service.create_role_message(
            session,
            from_role_id="a",
            to_role_id="b",
            from_department_id="x",
            to_department_id="y",
            subject="Thread root",
            content="First",
        )
        msg2 = await db_service.create_role_message(
            session,
            from_role_id="b",
            to_role_id="a",
            from_department_id="y",
            to_department_id="x",
            subject="Re: Thread root",
            content="Reply 1",
            reply_to_id=msg1,
        )
        msg3 = await db_service.create_role_message(
            session,
            from_role_id="a",
            to_role_id="b",
            from_department_id="x",
            to_department_id="y",
            subject="Re: Re: Thread root",
            content="Reply 2",
            reply_to_id=msg2,
        )
        # Get thread from any message in the chain
        thread = await db_service.get_message_thread(session, msg3)
        assert len(thread) == 3

    @pytest.mark.asyncio
    async def test_nonexistent_message(self, session):
        thread = await db_service.get_message_thread(session, 99999)
        assert thread == []


# ============================================================
# expire_stale_messages tests
# ============================================================


class TestExpireStaleMessages:
    @pytest.mark.asyncio
    async def test_expires_old_pending(self, session):
        # Create a message with an already-passed expiry
        msg_id = await db_service.create_role_message(
            session,
            from_role_id="a",
            to_role_id="b",
            from_department_id="x",
            to_department_id="y",
            subject="Old message",
            content="Should expire",
            expires_in_days=-1,  # Already expired
        )
        count = await db_service.expire_stale_messages(session)
        assert count >= 1

        from sqlalchemy import select

        stmt = select(RoleMessage).where(RoleMessage.id == msg_id)
        result = await session.execute(stmt)
        msg = result.scalar_one()
        assert msg.status == MessageStatus.EXPIRED.value

    @pytest.mark.asyncio
    async def test_does_not_expire_fresh(self, session):
        await db_service.create_role_message(
            session,
            from_role_id="a",
            to_role_id="b",
            from_department_id="x",
            to_department_id="y",
            subject="Fresh message",
            content="Should not expire",
            expires_in_days=7,
        )
        count = await db_service.expire_stale_messages(session)
        assert count == 0

    @pytest.mark.asyncio
    async def test_does_not_expire_delivered(self, session):
        msg_id = await db_service.create_role_message(
            session,
            from_role_id="a",
            to_role_id="b",
            from_department_id="x",
            to_department_id="y",
            subject="Delivered",
            content="Already delivered",
            expires_in_days=-1,
        )
        await db_service.mark_messages_delivered(session, [msg_id])
        count = await db_service.expire_stale_messages(session)
        assert count == 0
