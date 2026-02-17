"""add auto_execute_rule_id and AUTO_APPROVED status

Revision ID: 007_add_auto_execute
Revises: 006_add_role_memory
Create Date: 2026-02-13

Adds AUTO_APPROVED to the approval_status enum and an
auto_execute_rule_id column to the approval_queue table
for graduated trust / auto-execute support.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "007_add_auto_execute"
down_revision = "006_add_role_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add AUTO_APPROVED to the approval_status enum
    # PostgreSQL requires ALTER TYPE to add a new enum value
    op.execute("ALTER TYPE approvalstatus ADD VALUE IF NOT EXISTS 'auto_approved'")

    # Add auto_execute_rule_id column to approval_queue
    op.add_column(
        "approval_queue",
        sa.Column("auto_execute_rule_id", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("approval_queue", "auto_execute_rule_id")
    # Note: PostgreSQL does not support removing enum values directly.
    # The AUTO_APPROVED value will remain in the enum type.
