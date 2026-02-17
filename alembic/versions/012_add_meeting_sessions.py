"""add meeting_sessions table + voice_id on org_roles

Revision ID: meeting_001
Revises: rbac_001
Create Date: 2026-02-15

Adds the meeting_sessions table for tracking voice meeting sessions
where Sidera department heads participate via Google Meet. Also adds
a voice_id column to org_roles for ElevenLabs voice assignment.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "meeting_001"
down_revision = "rbac_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- meeting_sessions table ---
    op.create_table(
        "meeting_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("meeting_url", sa.String(500), nullable=False),
        sa.Column("role_id", sa.String(100), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("bot_id", sa.String(255), nullable=True),
        sa.Column("voice_id", sa.String(100), nullable=True),
        sa.Column("status", sa.String(50), server_default="joining"),
        sa.Column("started_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("joined_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("transcript_json", sa.JSON(), server_default="[]"),
        sa.Column("transcript_summary", sa.Text(), server_default=""),
        sa.Column("action_items_json", sa.JSON(), server_default="[]"),
        sa.Column("delegation_result_id", sa.Integer(), nullable=True),
        sa.Column("delegation_status", sa.String(50), nullable=True),
        sa.Column("total_cost_usd", sa.Numeric(8, 4), server_default="0"),
        sa.Column("agent_turns", sa.Integer(), server_default="0"),
        sa.Column("duration_seconds", sa.Integer(), server_default="0"),
        sa.Column("participants_json", sa.JSON(), server_default="[]"),
        sa.Column("slack_notification_ts", sa.String(100), nullable=True),
        sa.Column("channel_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_index("ix_meeting_role", "meeting_sessions", ["role_id"])
    op.create_index("ix_meeting_status", "meeting_sessions", ["status"])
    op.create_index("ix_meeting_user", "meeting_sessions", ["user_id"])

    # --- voice_id on org_roles ---
    op.add_column(
        "org_roles",
        sa.Column("voice_id", sa.String(100), server_default="", nullable=True),
    )


def downgrade() -> None:
    op.drop_column("org_roles", "voice_id")
    op.drop_index("ix_meeting_user", table_name="meeting_sessions")
    op.drop_index("ix_meeting_status", table_name="meeting_sessions")
    op.drop_index("ix_meeting_role", table_name="meeting_sessions")
    op.drop_table("meeting_sessions")
