"""add slack_channel_id and credentials_scope to departments

Revision ID: dept_channels_001
Revises: vocab_goals_001
Create Date: 2026-02-17

Adds ``slack_channel_id`` and ``credentials_scope`` columns to
``org_departments`` to support per-department Slack channel routing
and department-scoped API credential resolution.
"""

from alembic import op
import sqlalchemy as sa

revision = "dept_channels_001"
down_revision = "vocab_goals_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "org_departments",
        sa.Column("slack_channel_id", sa.String(100), nullable=True, server_default=""),
    )
    op.add_column(
        "org_departments",
        sa.Column("credentials_scope", sa.String(50), nullable=True, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("org_departments", "credentials_scope")
    op.drop_column("org_departments", "slack_channel_id")
