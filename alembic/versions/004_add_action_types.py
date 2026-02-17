"""add write action types to ActionType enum

Revision ID: 004_add_action_types
Revises: 003_add_failed_runs
Create Date: 2026-02-13

Extends the ActionType enum with new values for write operations:
add_negative_keywords, update_ad_schedule, update_geo_bid_modifier,
update_ad_status, update_adset_budget, update_adset_bid.
"""

from alembic import op

revision = "004_add_action_types"
down_revision = "003_add_failed_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add new ActionType enum values for write operations."""
    op.execute("ALTER TYPE actiontype ADD VALUE IF NOT EXISTS 'add_negative_keywords'")
    op.execute("ALTER TYPE actiontype ADD VALUE IF NOT EXISTS 'update_ad_schedule'")
    op.execute("ALTER TYPE actiontype ADD VALUE IF NOT EXISTS 'update_geo_bid_modifier'")
    op.execute("ALTER TYPE actiontype ADD VALUE IF NOT EXISTS 'update_ad_status'")
    op.execute("ALTER TYPE actiontype ADD VALUE IF NOT EXISTS 'update_adset_budget'")
    op.execute("ALTER TYPE actiontype ADD VALUE IF NOT EXISTS 'update_adset_bid'")


def downgrade() -> None:
    """PostgreSQL does not support removing enum values.

    To revert, you would need to create a new enum type without these
    values and migrate all columns. In practice, extra enum values are
    harmless and this is a no-op downgrade.
    """
    pass
