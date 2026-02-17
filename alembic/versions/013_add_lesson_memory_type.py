"""add lesson memory type + principles column on org_roles

Revision ID: lesson_001
Revises: meeting_001
Create Date: 2026-02-15

Adds 'lesson' to the MemoryType enum and a 'principles' TEXT column
to org_roles for storing role decision-making heuristics.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "lesson_001"
down_revision = "meeting_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add 'lesson' to the memorytype enum (only if using a PG enum type).
    # If role_memory.memory_type is VARCHAR, any string value is already valid.
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = 'memorytype'")
    )
    if result.fetchone():
        op.execute("ALTER TYPE memorytype ADD VALUE IF NOT EXISTS 'lesson'")

    # Add principles column to org_roles (only if it doesn't exist yet)
    result2 = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='org_roles' AND column_name='principles'"
        )
    )
    if not result2.fetchone():
        op.add_column(
            "org_roles",
            sa.Column("principles", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("org_roles", "principles")
    # Note: PostgreSQL does not support removing enum values easily.
    # The 'lesson' value will remain in the enum type after downgrade.
