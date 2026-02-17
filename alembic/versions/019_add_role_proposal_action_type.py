"""add role_proposal action type

Revision ID: role_proposal_001
Revises: claude_code_002
Create Date: 2025-02-16

Adds ``role_proposal`` to the ``actiontype`` PostgreSQL enum so that
department heads can propose new roles through the approval queue.
"""

from alembic import op

revision = "role_proposal_001"
down_revision = "claude_code_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE actiontype ADD VALUE IF NOT EXISTS 'role_proposal'"
    )


def downgrade() -> None:
    # PostgreSQL does not support removing enum values
    pass
