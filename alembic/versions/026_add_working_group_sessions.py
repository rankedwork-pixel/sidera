"""Add working_group_sessions table for multi-agent planning.

Revision ID: working_group_001
Revises: learning_channels_001
Create Date: 2026-02-17
"""

revision = "working_group_001"
down_revision = "learning_channels_001"

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.create_table(
        "working_group_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "group_id",
            sa.String(100),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("coordinator_role_id", sa.String(100), nullable=False),
        sa.Column("member_role_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("initiated_by", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "status", sa.String(50), nullable=False, server_default="forming",
        ),
        sa.Column("plan_json", sa.JSON(), nullable=True),
        sa.Column("member_results_json", sa.JSON(), server_default="{}"),
        sa.Column("synthesis", sa.Text(), server_default=""),
        sa.Column("shared_context_json", sa.JSON(), server_default="{}"),
        sa.Column("cost_cap_usd", sa.Numeric(8, 4), server_default="5.0"),
        sa.Column("max_duration_minutes", sa.Integer(), server_default="60"),
        sa.Column("deadline", sa.DateTime(), nullable=True),
        sa.Column("total_cost_usd", sa.Numeric(8, 4), server_default="0"),
        sa.Column("steward_user_id", sa.String(255), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("approved_by", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("slack_channel_id", sa.String(100), nullable=True),
        sa.Column("slack_thread_ts", sa.String(50), nullable=True),
    )

    op.create_index(
        "ix_wg_coordinator",
        "working_group_sessions",
        ["coordinator_role_id"],
    )
    op.create_index(
        "ix_wg_status",
        "working_group_sessions",
        ["status"],
    )
    op.create_index(
        "ix_wg_created",
        "working_group_sessions",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("working_group_sessions")
