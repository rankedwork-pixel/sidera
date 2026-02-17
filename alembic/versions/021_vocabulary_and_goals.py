"""add vocabulary to departments, goals to roles

Revision ID: vocab_goals_001
Revises: stewardship_001
Create Date: 2026-02-17

Adds ``vocabulary`` JSON column to ``org_departments`` and ``goals``
JSON column to ``org_roles`` to support department-level terminology
manifests and role-level goal injection.
"""

from alembic import op
import sqlalchemy as sa

revision = "vocab_goals_001"
down_revision = "stewardship_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "org_departments",
        sa.Column("vocabulary", sa.JSON(), nullable=True),
    )
    op.add_column(
        "org_roles",
        sa.Column("goals", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("org_roles", "goals")
    op.drop_column("org_departments", "vocabulary")
