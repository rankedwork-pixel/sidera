"""add document_sync to org_roles

Revision ID: doc_sync_001
Revises: dept_channels_001
Create Date: 2026-02-17

Adds document_sync JSON column to org_roles for living document configuration.
Maps output types (briefings, meetings) to Google Doc IDs.
"""

import sqlalchemy as sa
from alembic import op

revision = "doc_sync_001"
down_revision = "dept_channels_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "org_roles",
        sa.Column("document_sync", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("org_roles", "document_sync")
