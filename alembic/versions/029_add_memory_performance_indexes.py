"""Add performance indexes to role_memory for scale.

At 100+ memories/day across all roles, the existing indexes don't
cover the most common query patterns:

- get_role_memories: filters on consolidated_into_id IS NULL,
  orders by confidence DESC, created_at DESC
- search_role_memories: ILIKE on title/content, orders by
  created_at DESC — the (user_id, role_id) prefix from
  ix_role_memory_lookup helps narrow rows, but sorting still
  needs a sequential scan
- archive_expired_memories: filters on expires_at <= now AND
  is_archived = False — partially covered by ix_role_memory_expiry
  but adding is_archived makes it a covering index

New indexes:
1. (role_id, is_archived, created_at DESC) — covers hot memory
   loading + time-ordered retrieval per role
2. (role_id, consolidated_into_id) — covers the IS NULL filter
   in get_role_memories (partial index on NULL)

Revision ID: memory_indexes_001
Revises: skill_references_001
Create Date: 2026-02-20
"""

revision = "memory_indexes_001"
down_revision = "skill_references_001"

from alembic import op


def upgrade():
    # Covers get_role_memories + compose_memory_context hot tier loading
    # with time-ordered retrieval. The DESC on created_at matches the
    # ORDER BY created_at DESC in queries.
    op.create_index(
        "ix_role_memory_role_active_time",
        "role_memory",
        ["role_id", "is_archived", "created_at"],
    )

    # Partial index: only rows where consolidated_into_id IS NULL.
    # get_role_memories always filters consolidated_into_id IS NULL,
    # and after consolidation runs, many rows have non-NULL values
    # that are never queried. Partial index keeps the index small.
    op.execute(
        "CREATE INDEX ix_role_memory_unconsolidated "
        "ON role_memory (role_id, confidence DESC) "
        "WHERE consolidated_into_id IS NULL"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_role_memory_unconsolidated")
    op.drop_index("ix_role_memory_role_active_time", table_name="role_memory")
