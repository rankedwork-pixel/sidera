"""add claude_code_task action type

Revision ID: claude_code_002
Revises: claude_code_001
Create Date: 2025-02-16

Adds ``claude_code_task`` to the ``actiontype`` PostgreSQL enum so that
Claude Code task proposals can flow through the approval queue.
"""

from alembic import op

revision = "claude_code_002"
down_revision = "claude_code_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE actiontype ADD VALUE IF NOT EXISTS 'claude_code_task'"
    )


def downgrade() -> None:
    # PostgreSQL does not support removing enum values
    pass
