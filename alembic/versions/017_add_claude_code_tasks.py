"""add claude_code_tasks table

Revision ID: claude_code_001
Revises: clearance_001
Create Date: 2025-02-16

Adds the ``claude_code_tasks`` table for tracking headless Claude Code
task executions — full lifecycle from submission to completion,
including cost, result, and error details.
"""

import sqlalchemy as sa

from alembic import op

revision = "claude_code_001"
down_revision = "clearance_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "claude_code_tasks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "task_id", sa.String(100), nullable=False, unique=True, index=True
        ),
        sa.Column("skill_id", sa.String(100), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("role_id", sa.String(100), server_default=""),
        sa.Column("department_id", sa.String(100), server_default=""),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("permission_mode", sa.String(50), server_default="acceptEdits"),
        sa.Column("max_budget_usd", sa.Numeric(8, 4), server_default="5.0"),
        sa.Column("status", sa.String(50), server_default="pending"),
        sa.Column("error_message", sa.Text(), server_default=""),
        sa.Column("result_text", sa.Text(), server_default=""),
        sa.Column("structured_output", sa.JSON(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(8, 4), server_default="0"),
        sa.Column("num_turns", sa.Integer(), server_default="0"),
        sa.Column("duration_ms", sa.Integer(), server_default="0"),
        sa.Column("session_id", sa.String(255), server_default=""),
        sa.Column("usage_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("inngest_run_id", sa.String(255), nullable=True),
    )

    # Additional indexes (task_id already has unique index above)
    op.create_index("ix_cc_task_user", "claude_code_tasks", ["user_id"])
    op.create_index("ix_cc_task_skill", "claude_code_tasks", ["skill_id"])
    op.create_index("ix_cc_task_status", "claude_code_tasks", ["status"])
    op.create_index("ix_cc_task_role", "claude_code_tasks", ["role_id"])
    op.create_index("ix_cc_task_created", "claude_code_tasks", ["created_at"])


def downgrade() -> None:
    op.drop_table("claude_code_tasks")
