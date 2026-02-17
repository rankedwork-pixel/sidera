"""add failed_runs table (dead letter queue)

Revision ID: 003_add_failed_runs
Revises: 002_add_skill_columns
Create Date: 2026-02-13

Adds a dead letter queue table for recording workflow failures.
Failed runs are stored with enough context (event_data JSON) to
replay or investigate failures.
"""

import sqlalchemy as sa

from alembic import op

revision = "003_add_failed_runs"
down_revision = "002_add_skill_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "failed_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("workflow_name", sa.String(255), nullable=False),
        sa.Column("event_name", sa.String(255), nullable=False),
        sa.Column("event_data", sa.JSON),
        sa.Column("error_message", sa.Text),
        sa.Column("error_type", sa.String(255)),
        sa.Column("user_id", sa.String(255)),
        sa.Column("run_id", sa.String(255)),
        sa.Column("retry_count", sa.Integer, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime,
            server_default=sa.func.now(),
        ),
        sa.Column("resolved_at", sa.DateTime),
        sa.Column("resolved_by", sa.String(255)),
    )

    op.create_index("ix_failed_runs_user", "failed_runs", ["user_id"])
    op.create_index("ix_failed_runs_workflow", "failed_runs", ["workflow_name"])


def downgrade() -> None:
    op.drop_index("ix_failed_runs_workflow", table_name="failed_runs")
    op.drop_index("ix_failed_runs_user", table_name="failed_runs")
    op.drop_table("failed_runs")
