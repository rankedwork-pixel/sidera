"""add memory consolidation columns to role_memory

Revision ID: consolidation_001
Revises: messages_001
Create Date: 2026-02-16

Adds two self-referential FK columns to role_memory for memory
consolidation and versioning:
- supersedes_id: "this memory replaces that older one"
- consolidated_into_id: "this memory was folded into that consolidated one"
"""

import sqlalchemy as sa

from alembic import op

revision = "consolidation_001"
down_revision = "messages_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "role_memory",
        sa.Column(
            "supersedes_id",
            sa.Integer(),
            sa.ForeignKey("role_memory.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "role_memory",
        sa.Column(
            "consolidated_into_id",
            sa.Integer(),
            sa.ForeignKey("role_memory.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_role_memory_supersedes_id",
        "role_memory",
        ["supersedes_id"],
    )
    op.create_index(
        "ix_role_memory_consolidated_into_id",
        "role_memory",
        ["consolidated_into_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_role_memory_consolidated_into_id", table_name="role_memory")
    op.drop_index("ix_role_memory_supersedes_id", table_name="role_memory")
    op.drop_column("role_memory", "consolidated_into_id")
    op.drop_column("role_memory", "supersedes_id")
