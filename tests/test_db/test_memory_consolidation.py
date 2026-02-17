"""Tests for memory consolidation DB service methods.

Covers:
- get_role_memories (updated) — excludes memories with consolidated_into_id set
- get_distinct_memory_role_pairs — returns unique (user_id, role_id) pairs
- get_unconsolidated_memories — returns eligible memories for consolidation
- save_consolidated_memory — creates merged memory and marks originals
- get_superseded_memory_ids — finds IDs pointed to by supersedes_id
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db import service as db_service
from src.models.schema import Base


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
# Helpers
# ============================================================


async def _save(session, *, user_id="user_1", role_id="analyst", **kwargs):
    """Shorthand for creating a memory with sensible defaults."""
    defaults = dict(
        department_id="marketing",
        memory_type="decision",
        title="Memory",
        content="Some content.",
    )
    defaults.update(kwargs)
    return await db_service.save_memory(session, user_id=user_id, role_id=role_id, **defaults)


# ============================================================
# get_role_memories — consolidated_into_id exclusion
# ============================================================


class TestGetRoleMemoriesConsolidation:
    """Verify that get_role_memories excludes originals that were folded
    into a consolidated memory (consolidated_into_id is set)."""

    @pytest.mark.asyncio
    async def test_excludes_consolidated_originals(self, session):
        """A memory with consolidated_into_id set should NOT appear."""
        # Create a "consolidated" target memory
        target = await _save(session, title="Consolidated summary")

        # Create an original that was folded into the target
        original = await _save(session, title="Original detail")
        original.consolidated_into_id = target.id
        await session.flush()

        # Also create a normal memory that should appear
        await _save(session, title="Normal memory")

        memories = await db_service.get_role_memories(session, user_id="user_1", role_id="analyst")

        titles = [m.title for m in memories]
        assert "Consolidated summary" in titles
        assert "Normal memory" in titles
        assert "Original detail" not in titles
        assert len(memories) == 2

    @pytest.mark.asyncio
    async def test_consolidated_still_visible_with_include_archived(self, session):
        """Even with include_archived=True, consolidated originals are excluded."""
        target = await _save(session, title="Target")
        original = await _save(session, title="Folded original")
        original.consolidated_into_id = target.id
        await session.flush()

        memories = await db_service.get_role_memories(
            session, user_id="user_1", role_id="analyst", include_archived=True
        )

        titles = [m.title for m in memories]
        assert "Target" in titles
        assert "Folded original" not in titles


# ============================================================
# get_distinct_memory_role_pairs
# ============================================================


class TestGetDistinctMemoryRolePairs:
    @pytest.mark.asyncio
    async def test_returns_distinct_pairs(self, session):
        """With multiple memories across roles, returns unique (user_id, role_id) pairs."""
        await _save(session, user_id="user_1", role_id="analyst", title="M1")
        await _save(session, user_id="user_1", role_id="analyst", title="M2")
        await _save(session, user_id="user_1", role_id="optimizer", title="M3")
        await _save(session, user_id="user_2", role_id="analyst", title="M4")

        pairs = await db_service.get_distinct_memory_role_pairs(session)

        assert len(pairs) == 3
        pair_set = set(pairs)
        assert ("user_1", "analyst") in pair_set
        assert ("user_1", "optimizer") in pair_set
        assert ("user_2", "analyst") in pair_set

    @pytest.mark.asyncio
    async def test_excludes_consolidated(self, session):
        """Memories with consolidated_into_id set are excluded from the pairs."""
        target = await _save(session, user_id="user_1", role_id="analyst", title="Target")
        original = await _save(session, user_id="user_1", role_id="strategist", title="Original")
        original.consolidated_into_id = target.id
        await session.flush()

        # strategist only had one memory, and it's consolidated -> no pair for it
        # analyst has the target which is unconsolidated -> pair for it
        pairs = await db_service.get_distinct_memory_role_pairs(session)

        pair_set = set(pairs)
        assert ("user_1", "analyst") in pair_set
        assert ("user_1", "strategist") not in pair_set

    @pytest.mark.asyncio
    async def test_excludes_archived(self, session):
        """Archived memories are excluded from the pairs."""
        mem = await _save(session, user_id="user_1", role_id="analyst", title="Archived")
        mem.is_archived = True
        await session.flush()

        pairs = await db_service.get_distinct_memory_role_pairs(session)

        assert len(pairs) == 0

    @pytest.mark.asyncio
    async def test_empty_when_no_memories(self, session):
        pairs = await db_service.get_distinct_memory_role_pairs(session)
        assert pairs == []


# ============================================================
# get_unconsolidated_memories
# ============================================================


class TestGetUnconsolidatedMemories:
    @pytest.mark.asyncio
    async def test_returns_unconsolidated_only(self, session):
        """Only memories with consolidated_into_id IS NULL are returned."""
        target = await _save(session, title="Target")
        # Back-date so it passes the min_age_days filter
        target.created_at = datetime(2024, 1, 1)
        await session.flush()

        original = await _save(session, title="Original")
        original.created_at = datetime(2024, 1, 2)
        original.consolidated_into_id = target.id
        await session.flush()

        results = await db_service.get_unconsolidated_memories(
            session,
            user_id="user_1",
            role_id="analyst",
            min_age_days=0,
        )

        titles = [m.title for m in results]
        assert "Target" in titles
        assert "Original" not in titles

    @pytest.mark.asyncio
    async def test_min_age_days_filter(self, session):
        """Memories newer than min_age_days are excluded."""
        old_mem = await _save(session, title="Old enough")
        old_mem.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
        await session.flush()

        recent_mem = await _save(session, title="Too recent")
        recent_mem.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
        await session.flush()

        results = await db_service.get_unconsolidated_memories(
            session,
            user_id="user_1",
            role_id="analyst",
            min_age_days=7,
        )

        titles = [m.title for m in results]
        assert "Old enough" in titles
        assert "Too recent" not in titles

    @pytest.mark.asyncio
    async def test_excludes_archived(self, session):
        """Archived memories are excluded."""
        mem = await _save(session, title="Archived")
        mem.is_archived = True
        mem.created_at = datetime(2024, 1, 1)
        await session.flush()

        results = await db_service.get_unconsolidated_memories(
            session,
            user_id="user_1",
            role_id="analyst",
            min_age_days=0,
        )

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_oldest_first(self, session):
        """Results are ordered by created_at ASC (oldest first)."""
        mem_old = await _save(session, title="Oldest")
        mem_old.created_at = datetime(2024, 1, 1)
        await session.flush()

        mem_mid = await _save(session, title="Middle")
        mem_mid.created_at = datetime(2024, 6, 1)
        await session.flush()

        mem_new = await _save(session, title="Newest")
        mem_new.created_at = datetime(2024, 12, 1)
        await session.flush()

        results = await db_service.get_unconsolidated_memories(
            session,
            user_id="user_1",
            role_id="analyst",
            min_age_days=0,
        )

        assert len(results) == 3
        assert results[0].title == "Oldest"
        assert results[1].title == "Middle"
        assert results[2].title == "Newest"

    @pytest.mark.asyncio
    async def test_limit_respected(self, session):
        """Respects the limit parameter."""
        for i in range(10):
            mem = await _save(session, title=f"Memory {i}")
            mem.created_at = datetime(2024, 1, 1) + timedelta(days=i)
        await session.flush()

        results = await db_service.get_unconsolidated_memories(
            session,
            user_id="user_1",
            role_id="analyst",
            limit=3,
            min_age_days=0,
        )

        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_filters_by_user_and_role(self, session):
        """Only returns memories for the specified user_id and role_id."""
        mem = await _save(session, user_id="user_1", role_id="analyst", title="Match")
        mem.created_at = datetime(2024, 1, 1)
        await session.flush()

        other = await _save(session, user_id="user_2", role_id="optimizer", title="Other")
        other.created_at = datetime(2024, 1, 1)
        await session.flush()

        results = await db_service.get_unconsolidated_memories(
            session,
            user_id="user_1",
            role_id="analyst",
            min_age_days=0,
        )

        assert len(results) == 1
        assert results[0].title == "Match"


# ============================================================
# save_consolidated_memory
# ============================================================


class TestSaveConsolidatedMemory:
    @pytest.mark.asyncio
    async def test_creates_memory_and_marks_originals(self, session):
        """Creates a new consolidated memory and updates originals'
        consolidated_into_id to point at the new memory."""
        orig1 = await _save(session, title="Original 1")
        orig2 = await _save(session, title="Original 2")

        consolidated = await db_service.save_consolidated_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="pattern",
            title="Merged pattern",
            content="Combination of original 1 and 2.",
            source_ids=[orig1.id, orig2.id],
        )

        assert consolidated.id is not None
        assert consolidated.title == "Merged pattern"
        assert consolidated.memory_type == "pattern"

        # Verify originals are marked
        refreshed_1 = await db_service.get_memory_by_id(session, orig1.id)
        refreshed_2 = await db_service.get_memory_by_id(session, orig2.id)
        assert refreshed_1.consolidated_into_id == consolidated.id
        assert refreshed_2.consolidated_into_id == consolidated.id

    @pytest.mark.asyncio
    async def test_never_expires(self, session):
        """Consolidated memories should have expires_at = None."""
        consolidated = await db_service.save_consolidated_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="insight",
            title="Evergreen insight",
            content="This should never expire.",
            source_ids=[],
        )

        assert consolidated.expires_at is None

    @pytest.mark.asyncio
    async def test_evidence_has_source_ids(self, session):
        """Evidence dict includes source_ids and consolidation flag."""
        orig = await _save(session, title="Source")

        consolidated = await db_service.save_consolidated_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="Consolidated decision",
            content="Merged content.",
            source_ids=[orig.id],
        )

        assert consolidated.evidence is not None
        assert consolidated.evidence["source_ids"] == [orig.id]
        assert consolidated.evidence["consolidation"] is True

    @pytest.mark.asyncio
    async def test_supersedes_id_saved(self, session):
        """The supersedes_id field is correctly persisted."""
        older = await _save(session, title="Previous version")

        consolidated = await db_service.save_consolidated_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="pattern",
            title="Updated pattern",
            content="New version of pattern.",
            source_ids=[],
            supersedes_id=older.id,
        )

        assert consolidated.supersedes_id == older.id

    @pytest.mark.asyncio
    async def test_confidence_default(self, session):
        """Default confidence is 1.0."""
        consolidated = await db_service.save_consolidated_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="insight",
            title="Full confidence",
            content="Should default to 1.0.",
            source_ids=[],
        )

        assert consolidated.confidence == 1.0

    @pytest.mark.asyncio
    async def test_custom_confidence(self, session):
        """Custom confidence is stored."""
        consolidated = await db_service.save_consolidated_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="insight",
            title="Lower confidence",
            content="Partial confidence.",
            source_ids=[],
            confidence=0.75,
        )

        assert consolidated.confidence == 0.75

    @pytest.mark.asyncio
    async def test_empty_source_ids(self, session):
        """Works correctly with an empty source_ids list (no originals to mark)."""
        consolidated = await db_service.save_consolidated_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="decision",
            title="No sources",
            content="Created from scratch.",
            source_ids=[],
        )

        assert consolidated.id is not None
        assert consolidated.evidence["source_ids"] == []

    @pytest.mark.asyncio
    async def test_originals_excluded_from_get_role_memories(self, session):
        """After consolidation, originals no longer appear in get_role_memories."""
        orig1 = await _save(session, title="Original A")
        orig2 = await _save(session, title="Original B")

        await db_service.save_consolidated_memory(
            session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            memory_type="pattern",
            title="Merged AB",
            content="Merged A and B.",
            source_ids=[orig1.id, orig2.id],
        )

        memories = await db_service.get_role_memories(session, user_id="user_1", role_id="analyst")

        titles = [m.title for m in memories]
        assert "Merged AB" in titles
        assert "Original A" not in titles
        assert "Original B" not in titles


# ============================================================
# get_superseded_memory_ids
# ============================================================


class TestGetSupersededMemoryIds:
    @pytest.mark.asyncio
    async def test_returns_correct_ids(self, session):
        """Returns IDs pointed to by supersedes_id from active memories."""
        old_v1 = await _save(session, title="Pattern v1")
        new_v2 = await _save(session, title="Pattern v2")
        new_v2.supersedes_id = old_v1.id
        await session.flush()

        superseded = await db_service.get_superseded_memory_ids(
            session, user_id="user_1", role_id="analyst"
        )

        assert old_v1.id in superseded
        assert new_v2.id not in superseded

    @pytest.mark.asyncio
    async def test_empty_when_no_chains(self, session):
        """Returns empty set when no supersedes relationships exist."""
        await _save(session, title="Standalone 1")
        await _save(session, title="Standalone 2")

        superseded = await db_service.get_superseded_memory_ids(
            session, user_id="user_1", role_id="analyst"
        )

        assert superseded == set()

    @pytest.mark.asyncio
    async def test_ignores_archived(self, session):
        """Supersedes from archived memories are excluded."""
        old_v1 = await _save(session, title="Old version")
        new_v2 = await _save(session, title="New version")
        new_v2.supersedes_id = old_v1.id
        new_v2.is_archived = True
        await session.flush()

        superseded = await db_service.get_superseded_memory_ids(
            session, user_id="user_1", role_id="analyst"
        )

        # new_v2 is archived, so its supersedes_id should not be in the result
        assert old_v1.id not in superseded
        assert superseded == set()

    @pytest.mark.asyncio
    async def test_multiple_chains(self, session):
        """Multiple supersession chains return all superseded IDs."""
        v1 = await _save(session, title="Pattern v1")
        v2 = await _save(session, title="Pattern v2")
        v2.supersedes_id = v1.id
        await session.flush()

        a1 = await _save(session, title="Insight v1")
        a2 = await _save(session, title="Insight v2")
        a2.supersedes_id = a1.id
        await session.flush()

        superseded = await db_service.get_superseded_memory_ids(
            session, user_id="user_1", role_id="analyst"
        )

        assert v1.id in superseded
        assert a1.id in superseded
        assert v2.id not in superseded
        assert a2.id not in superseded
        assert len(superseded) == 2

    @pytest.mark.asyncio
    async def test_scoped_to_user_and_role(self, session):
        """Only returns superseded IDs for the specified user_id and role_id."""
        v1 = await _save(session, user_id="user_1", role_id="analyst", title="V1")
        v2 = await _save(session, user_id="user_1", role_id="analyst", title="V2")
        v2.supersedes_id = v1.id
        await session.flush()

        other_v1 = await _save(session, user_id="user_2", role_id="optimizer", title="Other V1")
        other_v2 = await _save(session, user_id="user_2", role_id="optimizer", title="Other V2")
        other_v2.supersedes_id = other_v1.id
        await session.flush()

        # Query for user_1/analyst only
        superseded = await db_service.get_superseded_memory_ids(
            session, user_id="user_1", role_id="analyst"
        )

        assert v1.id in superseded
        assert other_v1.id not in superseded
        assert len(superseded) == 1
