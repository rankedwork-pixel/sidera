"""add role_messages table for peer-to-peer communication

Revision ID: messages_001
Revises: lesson_001
Create Date: 2026-02-16

Adds a role_messages table for async peer-to-peer messaging between
roles. Messages are created when one role sends a message to another,
delivered on the recipient's next run, and expire after 7 days.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "messages_001"
down_revision = "lesson_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "role_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("from_role_id", sa.String(200), nullable=False),
        sa.Column("to_role_id", sa.String(200), nullable=False),
        sa.Column("from_department_id", sa.String(200), nullable=False, server_default=""),
        sa.Column("to_department_id", sa.String(200), nullable=False, server_default=""),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column(
            "reply_to_id",
            sa.Integer(),
            sa.ForeignKey("role_messages.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
    )

    # Composite index for recipient inbox queries
    op.create_index(
        "ix_role_msg_to_status",
        "role_messages",
        ["to_role_id", "status"],
    )

    # Index for sender history
    op.create_index(
        "ix_role_msg_from_created",
        "role_messages",
        ["from_role_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_role_msg_from_created", table_name="role_messages")
    op.drop_index("ix_role_msg_to_status", table_name="role_messages")
    op.drop_table("role_messages")
