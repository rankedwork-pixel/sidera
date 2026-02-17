"""add role_memory table for persistent AI employee memory

Revision ID: 006_add_role_memory
Revises: 005_add_hierarchy_columns
Create Date: 2026-02-13

Adds the role_memory table where each AI employee (role) stores
learnings, decisions, anomalies, and patterns across runs.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "006_add_role_memory"
down_revision = "005_add_hierarchy_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "role_memory",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("role_id", sa.String(100), nullable=False),
        sa.Column("department_id", sa.String(100)),
        sa.Column("memory_type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, default=1.0),
        sa.Column("source_skill_id", sa.String(200)),
        sa.Column("source_run_date", sa.Date),
        sa.Column("evidence", sa.JSON),
        sa.Column("expires_at", sa.DateTime),
        sa.Column("is_archived", sa.Boolean, default=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_role_memory_lookup",
        "role_memory",
        ["user_id", "role_id", "is_archived"],
    )
    op.create_index(
        "ix_role_memory_type", "role_memory", ["memory_type"],
    )
    op.create_index(
        "ix_role_memory_expiry", "role_memory", ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_role_memory_expiry", table_name="role_memory")
    op.drop_index("ix_role_memory_type", table_name="role_memory")
    op.drop_index("ix_role_memory_lookup", table_name="role_memory")
    op.drop_table("role_memory")
