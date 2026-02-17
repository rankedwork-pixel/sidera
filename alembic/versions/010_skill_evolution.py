"""add skill_proposal to ActionType enum, make approval_queue FKs nullable

Revision ID: skill_evo_001
Revises: org_chart_001
Create Date: 2026-02-14

Supports the Skill Evolution feature: agents propose skill changes through
the approval queue.  Skill proposals don't belong to an account or analysis,
so the FK columns become nullable.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "skill_evo_001"
down_revision = "org_chart_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add 'skill_proposal' to the ActionType enum
    op.execute("ALTER TYPE actiontype ADD VALUE IF NOT EXISTS 'skill_proposal'")

    # Make account_id and analysis_id nullable for non-platform actions
    op.alter_column(
        "approval_queue",
        "account_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.alter_column(
        "approval_queue",
        "analysis_id",
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade() -> None:
    # Restore NOT NULL on account_id and analysis_id
    # (set orphan rows to 0 first to avoid constraint violation)
    op.execute(
        "UPDATE approval_queue SET account_id = 0 WHERE account_id IS NULL"
    )
    op.execute(
        "UPDATE approval_queue SET analysis_id = 0 WHERE analysis_id IS NULL"
    )
    op.alter_column(
        "approval_queue",
        "account_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "approval_queue",
        "analysis_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    # Note: PostgreSQL doesn't support removing enum values directly.
    # The 'skill_proposal' value will remain in the enum after downgrade.
