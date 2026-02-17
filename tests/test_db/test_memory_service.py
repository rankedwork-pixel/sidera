"""Tests for the role memory DB service methods.

Covers:
- save_memory() — persists RoleMemory with all fields, defaults, TTL calculation
- get_role_memories() — retrieval with ordering, filters, archival, isolation
- search_role_memories() — full-archive search (hot + cold)
- archive_expired_memories() — bulk archival of expired memories
- update_memory_confidence() — confidence updates with clamping
- delete_memory() — permanent deletion
- get_memory_by_id() — single record lookup
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db import service as db_service
from src.models.schema import Base, MemoryType, RoleMemory


@pytest.fixture
async def session():
    """Create an in-memory SQLite database and yield a session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        yield s
    await engine.dispose()


# ============================================================
# Schema Column Tests
# ============================================================


class TestSchemaColumns:
    """Verify that the RoleMemory model exposes expected columns and
    the MemoryType enum contains the right values."""

    def test_role_memory_has_expected_columns(self):
        column_names = [c.name for c in RoleMemory.__table__.columns]
        expected = [
            "id",
            "user_id",
            "role_id",
            "department_id",
            "memory_type",
            "title",
            "content",
            "confidence",
            "source_skill_id",
            "source_run_date",
            "evidence",
            "expires_at",
            "is_archived",
            "created_at",
            "updated_at",
        ]
        for col in expected:
            assert col in column_names, f"Missing column: {col}"

    def test_memory_type_enum_values(self):
        assert MemoryType.DECISION == "decision"
        assert MemoryType.ANOMALY == "anomaly"
        assert MemoryType.PATTERN == "pattern"
        assert MemoryType.INSIGHT == "insight"
        assert MemoryType.LESSON == "lesson"

    def test_memory_type_enum_count(self):
        # decision, anomaly, pattern, insight, lesson, commitment, relationship,
        # steward_note, cross_role_insight
        assert len(MemoryType) == 9

    def test_role_memory_is_archived_defaults_false(self):
        col = RoleMemory.__table__.columns["is_archived"]
        assert col.default is not None


# ============================================================
# save_memory Tests
# ============================================================


class TestSaveMemory:
    async def test_basic_save(self, session):
        mem = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Paused campaign X",
            content="Campaign X was paused due to high CPA.",
        )

        assert mem.id is not None
        assert mem.user_id == "user_1"
        assert mem.role_id == "analyst"
        assert mem.department_id == "marketing"
        assert mem.memory_type == "decision"
        assert mem.title == "Paused campaign X"
        assert mem.content == "Campaign X was paused due to high CPA."

    async def test_all_fields(self, session):
        evidence = {"campaign_id": "C123", "cpa": 45.2}
        mem = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="optimizer",
            department_id="performance",
            memory_type="anomaly",
            title="CPA spike",
            content="CPA spiked 300% on Tuesday.",
            confidence=0.85,
            source_skill_id="budget_analysis",
            source_run_date=date(2025, 3, 15),
            evidence=evidence,
            ttl_days=30,
        )

        assert mem.confidence == 0.85
        assert mem.source_skill_id == "budget_analysis"
        assert mem.source_run_date == date(2025, 3, 15)
        assert mem.evidence == evidence
        assert mem.expires_at is not None

    async def test_default_confidence_is_one(self, session):
        mem = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="insight",
            title="Insight",
            content="Some insight.",
        )

        assert mem.confidence == 1.0

    async def test_expires_at_with_ttl_days_90(self, session):
        before = datetime.now(timezone.utc)
        mem = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="pattern",
            title="Pattern",
            content="Observed pattern.",
            ttl_days=90,
        )
        after = datetime.now(timezone.utc)

        assert mem.expires_at is not None
        expected_low = before + timedelta(days=90)
        expected_high = after + timedelta(days=90)
        # Make both offset-naive for comparison (SQLite stores naive)
        expires = mem.expires_at
        if expires.tzinfo is not None:
            expires = expires.replace(tzinfo=None)
        expected_low = expected_low.replace(tzinfo=None)
        expected_high = expected_high.replace(tzinfo=None)
        assert expected_low <= expires <= expected_high

    async def test_no_expiry_when_ttl_days_zero(self, session):
        mem = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Permanent decision",
            content="This should never expire.",
            ttl_days=0,
        )

        assert mem.expires_at is None

    async def test_evidence_json(self, session):
        evidence = {
            "metrics": {"cpa": 12.5, "roas": 3.2},
            "campaigns": ["C1", "C2"],
            "nested": {"a": {"b": 1}},
        }
        mem = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="insight",
            title="Complex evidence",
            content="Memory with complex evidence.",
            evidence=evidence,
        )

        assert mem.evidence == evidence
        assert mem.evidence["metrics"]["cpa"] == 12.5
        assert mem.evidence["campaigns"] == ["C1", "C2"]

    async def test_no_evidence_defaults_to_none(self, session):
        mem = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="No evidence",
            content="No supporting data.",
        )

        assert mem.evidence is None

    async def test_is_archived_defaults_to_false(self, session):
        mem = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Active memory",
            content="Should be active.",
        )

        assert mem.is_archived is False


# ============================================================
# get_role_memories Tests
# ============================================================


class TestGetRoleMemories:
    async def test_basic_retrieval(self, session):
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Memory 1",
            content="Content 1",
        )
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="insight",
            title="Memory 2",
            content="Content 2",
        )

        memories = await db_service.get_role_memories(session, user_id="user_1", role_id="analyst")

        assert len(memories) == 2

    async def test_ordering_confidence_desc(self, session):
        """Higher confidence should come first."""
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Low confidence",
            content="Low",
            confidence=0.3,
        )
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="High confidence",
            content="High",
            confidence=0.9,
        )

        memories = await db_service.get_role_memories(session, user_id="user_1", role_id="analyst")

        assert memories[0].title == "High confidence"
        assert memories[1].title == "Low confidence"

    async def test_ordering_recency_within_same_confidence(self, session):
        """Among same-confidence memories, newer should come first."""
        mem1 = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Older",
            content="First created",
            confidence=0.8,
        )
        # Manually set created_at in the past so ordering is deterministic
        mem1.created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        await session.flush()

        mem2 = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Newer",
            content="Second created",
            confidence=0.8,
        )
        mem2.created_at = datetime(2025, 6, 1, tzinfo=timezone.utc)
        await session.flush()

        memories = await db_service.get_role_memories(session, user_id="user_1", role_id="analyst")

        assert len(memories) == 2
        # Both have same confidence; newer (higher created_at) first
        assert memories[0].id == mem2.id
        assert memories[1].id == mem1.id

    async def test_memory_type_filter(self, session):
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Decision",
            content="A decision.",
        )
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="anomaly",
            title="Anomaly",
            content="An anomaly.",
        )

        decisions = await db_service.get_role_memories(
            session, user_id="user_1", role_id="analyst", memory_type="decision"
        )
        anomalies = await db_service.get_role_memories(
            session, user_id="user_1", role_id="analyst", memory_type="anomaly"
        )

        assert len(decisions) == 1
        assert decisions[0].title == "Decision"
        assert len(anomalies) == 1
        assert anomalies[0].title == "Anomaly"

    async def test_min_confidence_filter(self, session):
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Low",
            content="Low confidence.",
            confidence=0.2,
        )
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="High",
            content="High confidence.",
            confidence=0.8,
        )

        memories = await db_service.get_role_memories(
            session, user_id="user_1", role_id="analyst", min_confidence=0.5
        )

        assert len(memories) == 1
        assert memories[0].title == "High"

    async def test_exclude_archived_by_default(self, session):
        mem = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Archived",
            content="Old memory.",
        )
        # Manually archive it
        mem.is_archived = True
        await session.flush()

        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Active",
            content="Fresh memory.",
        )

        memories = await db_service.get_role_memories(session, user_id="user_1", role_id="analyst")

        assert len(memories) == 1
        assert memories[0].title == "Active"

    async def test_include_archived(self, session):
        mem = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Archived",
            content="Old memory.",
        )
        mem.is_archived = True
        await session.flush()

        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Active",
            content="Fresh memory.",
        )

        memories = await db_service.get_role_memories(
            session,
            user_id="user_1",
            role_id="analyst",
            include_archived=True,
        )

        assert len(memories) == 2

    async def test_limit(self, session):
        for i in range(10):
            await db_service.save_memory(
                session,
                user_id="user_1",
                role_id="analyst",
                department_id="marketing",
                memory_type="decision",
                title=f"Memory {i}",
                content=f"Content {i}",
            )

        memories = await db_service.get_role_memories(
            session, user_id="user_1", role_id="analyst", limit=3
        )

        assert len(memories) == 3

    async def test_different_users_isolated(self, session):
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="User 1 memory",
            content="Belongs to user 1.",
        )
        await db_service.save_memory(
            session,
            user_id="user_2",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="User 2 memory",
            content="Belongs to user 2.",
        )

        u1 = await db_service.get_role_memories(session, user_id="user_1", role_id="analyst")
        u2 = await db_service.get_role_memories(session, user_id="user_2", role_id="analyst")

        assert len(u1) == 1
        assert u1[0].title == "User 1 memory"
        assert len(u2) == 1
        assert u2[0].title == "User 2 memory"

    async def test_different_roles_isolated(self, session):
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Analyst memory",
            content="Analyst.",
        )
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="optimizer",
            department_id="marketing",
            memory_type="decision",
            title="Optimizer memory",
            content="Optimizer.",
        )

        analyst = await db_service.get_role_memories(session, user_id="user_1", role_id="analyst")
        optimizer = await db_service.get_role_memories(
            session, user_id="user_1", role_id="optimizer"
        )

        assert len(analyst) == 1
        assert analyst[0].title == "Analyst memory"
        assert len(optimizer) == 1
        assert optimizer[0].title == "Optimizer memory"

    async def test_empty_result(self, session):
        memories = await db_service.get_role_memories(
            session, user_id="nonexistent", role_id="no_such_role"
        )

        assert memories == []


# ============================================================
# archive_expired_memories Tests
# ============================================================


class TestArchiveExpiredMemories:
    async def test_archives_expired_memories(self, session):
        mem = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Expired",
            content="Should be archived.",
            ttl_days=0,  # no auto-expiry
        )
        # Manually set expires_at to the past
        mem.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        await session.flush()

        count = await db_service.archive_expired_memories(session)

        assert count == 1
        refreshed = await db_service.get_memory_by_id(session, mem.id)
        assert refreshed.is_archived is True

    async def test_skips_non_expired(self, session):
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Future expiry",
            content="Still valid.",
            ttl_days=90,
        )

        count = await db_service.archive_expired_memories(session)

        assert count == 0

    async def test_skips_already_archived(self, session):
        mem = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Already archived",
            content="Already done.",
            ttl_days=0,
        )
        mem.expires_at = datetime.now(timezone.utc) - timedelta(days=5)
        mem.is_archived = True
        await session.flush()

        count = await db_service.archive_expired_memories(session)

        assert count == 0

    async def test_returns_count(self, session):
        for i in range(3):
            mem = await db_service.save_memory(
                session,
                user_id="user_1",
                role_id="analyst",
                department_id="marketing",
                memory_type="decision",
                title=f"Expired {i}",
                content=f"Content {i}",
                ttl_days=0,
            )
            mem.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        await session.flush()

        count = await db_service.archive_expired_memories(session)

        assert count == 3

    async def test_noop_when_nothing_to_archive(self, session):
        # No memories at all
        count = await db_service.archive_expired_memories(session)

        assert count == 0


# ============================================================
# update_memory_confidence Tests
# ============================================================


class TestUpdateMemoryConfidence:
    async def test_updates_confidence(self, session):
        mem = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Memory",
            content="Content.",
            confidence=0.5,
        )

        await db_service.update_memory_confidence(session, mem.id, 0.9)

        refreshed = await db_service.get_memory_by_id(session, mem.id)
        assert refreshed.confidence == 0.9

    async def test_clamps_to_zero_one_range(self, session):
        mem = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Memory",
            content="Content.",
            confidence=0.5,
        )

        # Over 1.0 should clamp to 1.0
        await db_service.update_memory_confidence(session, mem.id, 1.5)
        refreshed = await db_service.get_memory_by_id(session, mem.id)
        assert refreshed.confidence == 1.0

        # Below 0.0 should clamp to 0.0
        await db_service.update_memory_confidence(session, mem.id, -0.3)
        refreshed = await db_service.get_memory_by_id(session, mem.id)
        assert refreshed.confidence == 0.0

    async def test_noop_for_nonexistent_id(self, session):
        # Should not raise — just do nothing
        await db_service.update_memory_confidence(session, 99999, 0.7)


# ============================================================
# delete_memory Tests
# ============================================================


class TestDeleteMemory:
    async def test_deletes_memory(self, session):
        mem = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="To delete",
            content="Will be gone.",
        )

        await db_service.delete_memory(session, mem.id)

        result = await db_service.get_memory_by_id(session, mem.id)
        assert result is None

    async def test_noop_for_nonexistent_id(self, session):
        # Should not raise
        await db_service.delete_memory(session, 99999)

    async def test_verify_gone_after_delete(self, session):
        mem = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Ephemeral",
            content="Temporary.",
        )
        memory_id = mem.id

        # Verify it exists
        found = await db_service.get_memory_by_id(session, memory_id)
        assert found is not None

        # Delete and verify gone
        await db_service.delete_memory(session, memory_id)
        gone = await db_service.get_memory_by_id(session, memory_id)
        assert gone is None

        # Also should not appear in role memories
        memories = await db_service.get_role_memories(session, user_id="user_1", role_id="analyst")
        assert all(m.id != memory_id for m in memories)


# ============================================================
# get_memory_by_id Tests
# ============================================================


class TestGetMemoryById:
    async def test_returns_memory(self, session):
        mem = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="insight",
            title="Lookup test",
            content="Should be findable.",
        )

        found = await db_service.get_memory_by_id(session, mem.id)

        assert found is not None
        assert found.id == mem.id
        assert found.title == "Lookup test"
        assert found.content == "Should be findable."
        assert found.user_id == "user_1"
        assert found.role_id == "analyst"

    async def test_returns_none_for_nonexistent_id(self, session):
        result = await db_service.get_memory_by_id(session, 99999)

        assert result is None


# ============================================================
# search_role_memories Tests
# ============================================================


class TestSearchRoleMemories:
    """Tests for search_role_memories — searches ALL memories (hot + cold)."""

    async def test_finds_active_memories(self, session):
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Budget increase approved",
            content="Increased Campaign X budget by 20%.",
        )

        results = await db_service.search_role_memories(
            session, user_id="user_1", role_id="analyst", query="budget"
        )

        assert len(results) == 1
        assert "budget" in results[0].title.lower()

    async def test_finds_archived_memories(self, session):
        """Archived memories should be searchable (the whole point)."""
        mem = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="anomaly",
            title="CPA spike in March",
            content="CPA spiked 300% due to competitor activity.",
        )
        mem.is_archived = True
        await session.flush()

        results = await db_service.search_role_memories(
            session, user_id="user_1", role_id="analyst", query="CPA spike"
        )

        assert len(results) == 1
        assert results[0].is_archived is True

    async def test_finds_both_hot_and_cold(self, session):
        """Should return both active and archived memories."""
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Active budget decision",
            content="Budget related active memory.",
        )
        archived = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Old budget decision",
            content="Budget related archived memory.",
        )
        archived.is_archived = True
        await session.flush()

        results = await db_service.search_role_memories(
            session, user_id="user_1", role_id="analyst", query="budget"
        )

        assert len(results) == 2

    async def test_empty_query_returns_recent(self, session):
        """Empty query returns most recent memories across all time."""
        for i in range(3):
            await db_service.save_memory(
                session,
                user_id="user_1",
                role_id="analyst",
                department_id="marketing",
                memory_type="decision",
                title=f"Memory {i}",
                content=f"Content {i}",
            )

        results = await db_service.search_role_memories(
            session, user_id="user_1", role_id="analyst"
        )

        assert len(results) == 3

    async def test_query_matches_title(self, session):
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Paused Campaign Alpha",
            content="Paused due to low ROAS.",
        )
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="insight",
            title="Weekend performance dip",
            content="Conversions drop on weekends.",
        )

        results = await db_service.search_role_memories(
            session, user_id="user_1", role_id="analyst", query="Campaign Alpha"
        )

        assert len(results) == 1
        assert "Alpha" in results[0].title

    async def test_query_matches_content(self, session):
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="anomaly",
            title="Anomaly detected",
            content="Competitor launched aggressive Black Friday campaign.",
        )

        results = await db_service.search_role_memories(
            session, user_id="user_1", role_id="analyst", query="Black Friday"
        )

        assert len(results) == 1

    async def test_case_insensitive_search(self, session):
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="ROAS Optimization",
            content="Optimized for ROAS target of 3.5x.",
        )

        results = await db_service.search_role_memories(
            session, user_id="user_1", role_id="analyst", query="roas optimization"
        )

        assert len(results) == 1

    async def test_memory_type_filter(self, session):
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Budget decision",
            content="Budget change approved.",
        )
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="anomaly",
            title="Budget anomaly",
            content="Budget anomaly detected.",
        )

        results = await db_service.search_role_memories(
            session,
            user_id="user_1",
            role_id="analyst",
            query="budget",
            memory_type="anomaly",
        )

        assert len(results) == 1
        assert results[0].memory_type == "anomaly"

    async def test_limit_respected(self, session):
        for i in range(10):
            await db_service.save_memory(
                session,
                user_id="user_1",
                role_id="analyst",
                department_id="marketing",
                memory_type="decision",
                title=f"Decision {i}",
                content=f"Content {i}",
            )

        results = await db_service.search_role_memories(
            session, user_id="user_1", role_id="analyst", limit=3
        )

        assert len(results) == 3

    async def test_role_isolation(self, session):
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Analyst memory",
            content="Analyst only.",
        )
        await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="optimizer",
            department_id="marketing",
            memory_type="decision",
            title="Optimizer memory",
            content="Optimizer only.",
        )

        results = await db_service.search_role_memories(
            session, user_id="user_1", role_id="analyst"
        )

        assert len(results) == 1
        assert results[0].role_id == "analyst"

    async def test_no_results(self, session):
        results = await db_service.search_role_memories(
            session,
            user_id="user_1",
            role_id="analyst",
            query="nonexistent_query_xyz",
        )

        assert results == []

    async def test_ordered_newest_first(self, session):
        mem1 = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Older",
            content="Old content.",
        )
        mem1.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        await session.flush()

        mem2 = await db_service.save_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Newer",
            content="New content.",
        )
        mem2.created_at = datetime(2025, 6, 1, tzinfo=timezone.utc)
        await session.flush()

        results = await db_service.search_role_memories(
            session, user_id="user_1", role_id="analyst"
        )

        assert len(results) == 2
        assert results[0].id == mem2.id  # Newer first
        assert results[1].id == mem1.id
