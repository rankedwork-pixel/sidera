"""add learning_channels column to org_roles

Revision ID: learning_channels_001
Revises: webhook_events_001
Create Date: 2026-02-17

Adds the learning_channels JSON column to org_roles for agent-to-agent learning.
Stores a list of role IDs that are allowed to push structured learnings
(cross_role_insight memories) to this role.
"""

import sqlalchemy as sa
from alembic import op

revision = "learning_channels_001"
down_revision = "webhook_events_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "org_roles",
        sa.Column("learning_channels", sa.JSON(), server_default="[]", nullable=True),
    )


def downgrade() -> None:
    op.drop_column("org_roles", "learning_channels")
