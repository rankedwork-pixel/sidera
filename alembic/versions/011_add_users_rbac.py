"""add users table for RBAC

Revision ID: rbac_001
Revises: skill_evo_001
Create Date: 2026-02-14

Adds the ``users`` table with role-based access control.
Users are identified by Slack user ID with roles: admin, approver, viewer.
"""

import sqlalchemy as sa

from alembic import op

revision = "rbac_001"
down_revision = "skill_evo_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.String(255), nullable=False, unique=True, index=True,
        ),
        sa.Column("display_name", sa.String(255), server_default=""),
        sa.Column("email", sa.String(255), server_default=""),
        sa.Column(
            "role",
            sa.Enum("admin", "approver", "viewer", name="userrole"),
            nullable=False,
            server_default="approver",
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(),
        ),
        sa.Column("created_by", sa.String(255), server_default=""),
    )

    op.create_index("ix_users_role", "users", ["role"])
    op.create_index("ix_users_active", "users", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_users_active", table_name="users")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_table("users")

    op.execute("DROP TYPE IF EXISTS userrole")
