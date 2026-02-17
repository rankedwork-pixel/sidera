"""add conversation_threads table for conversation mode

Revision ID: conv_threads_001
Revises: 007_add_auto_execute
Create Date: 2026-02-13

Adds the conversation_threads table which maps Slack threads to
Sidera roles for interactive conversation mode.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "conv_threads_001"
down_revision = "007_add_auto_execute"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_threads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("thread_ts", sa.String(100), nullable=False, unique=True),
        sa.Column("channel_id", sa.String(100), nullable=False),
        sa.Column("role_id", sa.String(100), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("started_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("last_activity_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("turn_count", sa.Integer(), server_default="0"),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("total_cost_usd", sa.Numeric(8, 4), server_default="0"),
    )

    # Indexes
    op.create_index("ix_conv_thread_ts", "conversation_threads", ["thread_ts"])
    op.create_index(
        "ix_conv_thread_channel",
        "conversation_threads",
        ["channel_id", "thread_ts"],
    )
    op.create_index("ix_conv_thread_role", "conversation_threads", ["role_id"])
    op.create_index(
        "ix_conv_thread_active",
        "conversation_threads",
        ["is_active", "last_activity_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_conv_thread_active", table_name="conversation_threads")
    op.drop_index("ix_conv_thread_role", table_name="conversation_threads")
    op.drop_index("ix_conv_thread_channel", table_name="conversation_threads")
    op.drop_index("ix_conv_thread_ts", table_name="conversation_threads")
    op.drop_table("conversation_threads")
