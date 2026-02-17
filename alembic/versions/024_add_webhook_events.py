"""add webhook_events table for always-on monitoring

Revision ID: webhook_events_001
Revises: doc_sync_001
Create Date: 2026-02-17

Adds the webhook_events table for recording inbound webhook events
from external monitoring sources (Google Ads Scripts, Meta, BigQuery, custom).
"""

import sqlalchemy as sa
from alembic import op

revision = "webhook_events_001"
down_revision = "doc_sync_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("normalized_payload", sa.JSON(), nullable=True),
        sa.Column("account_id", sa.String(100), nullable=True),
        sa.Column("campaign_id", sa.String(100), nullable=True),
        sa.Column("dedup_key", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="received"),
        sa.Column("dispatched_event", sa.String(100), nullable=True),
        sa.Column("role_id", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_webhook_source_type", "webhook_events", ["source", "event_type"])
    op.create_index("ix_webhook_status", "webhook_events", ["status"])
    op.create_index("ix_webhook_created", "webhook_events", ["created_at"])
    op.create_index("ix_webhook_dedup", "webhook_events", ["dedup_key"])


def downgrade() -> None:
    op.drop_index("ix_webhook_dedup", table_name="webhook_events")
    op.drop_index("ix_webhook_created", table_name="webhook_events")
    op.drop_index("ix_webhook_status", table_name="webhook_events")
    op.drop_index("ix_webhook_source_type", table_name="webhook_events")
    op.drop_table("webhook_events")
