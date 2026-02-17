"""add stewardship columns

Revision ID: stewardship_001
Revises: role_proposal_001
Create Date: 2026-02-16

Adds ``steward_user_id`` to ``org_roles``, ``org_departments``,
``approval_queue``, and ``audit_log`` tables to support the Agent
Stewardship feature — human accountability for AI role behaviour.
"""

from alembic import op
import sqlalchemy as sa

revision = "stewardship_001"
down_revision = "role_proposal_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Steward assignment on org chart entities
    op.add_column(
        "org_roles",
        sa.Column(
            "steward_user_id",
            sa.String(255),
            nullable=True,
            server_default="",
        ),
    )
    op.add_column(
        "org_departments",
        sa.Column(
            "steward_user_id",
            sa.String(255),
            nullable=True,
            server_default="",
        ),
    )

    # Steward snapshot at approval creation time
    op.add_column(
        "approval_queue",
        sa.Column("steward_user_id", sa.String(255), nullable=True),
    )

    # Steward snapshot at audit event time
    op.add_column(
        "audit_log",
        sa.Column("steward_user_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audit_log", "steward_user_id")
    op.drop_column("approval_queue", "steward_user_id")
    op.drop_column("org_departments", "steward_user_id")
    op.drop_column("org_roles", "steward_user_id")
