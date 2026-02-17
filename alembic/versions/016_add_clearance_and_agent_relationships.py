"""add clearance levels and inter-agent relationship tracking

Revision ID: clearance_001
Revises: consolidation_001
Create Date: 2026-02-16

Adds:
- clearance_level column to users table (PUBLIC/INTERNAL/CONFIDENTIAL/RESTRICTED)
- source_role_id column to role_memory table (for inter-agent relationship memories)
- min_clearance column to org_skills table
- clearance_level column to org_roles table
"""

import sqlalchemy as sa

from alembic import op

revision = "clearance_001"
down_revision = "consolidation_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- users: add clearance_level --
    op.add_column(
        "users",
        sa.Column("clearance_level", sa.String(20), nullable=False, server_default="public"),
    )
    op.create_index("ix_users_clearance", "users", ["clearance_level"])

    # -- role_memory: add source_role_id for inter-agent memories --
    op.add_column(
        "role_memory",
        sa.Column("source_role_id", sa.String(100), nullable=True),
    )
    op.create_index(
        "ix_role_memory_source_role",
        "role_memory",
        ["source_role_id", "role_id"],
    )

    # -- org_skills: add min_clearance --
    op.add_column(
        "org_skills",
        sa.Column("min_clearance", sa.String(20), nullable=False, server_default="public"),
    )

    # -- org_roles: add clearance_level --
    op.add_column(
        "org_roles",
        sa.Column("clearance_level", sa.String(20), nullable=False, server_default="internal"),
    )


def downgrade() -> None:
    op.drop_column("org_roles", "clearance_level")
    op.drop_column("org_skills", "min_clearance")
    op.drop_index("ix_role_memory_source_role", table_name="role_memory")
    op.drop_column("role_memory", "source_role_id")
    op.drop_index("ix_users_clearance", table_name="users")
    op.drop_column("users", "clearance_level")
