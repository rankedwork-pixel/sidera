"""add skill columns

Revision ID: 002_add_skill_columns
Revises: 001_initial_schema
Create Date: 2026-02-13

Adds skill_id columns to analysis_results and audit_log tables
for tracking which skill produced each analysis or audit event.
Null values indicate legacy (non-skill) entries for backward compatibility.
"""

import sqlalchemy as sa

from alembic import op

revision = "002_add_skill_columns"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- analysis_results ---
    op.add_column(
        "analysis_results",
        sa.Column("skill_id", sa.String(100), nullable=True),
    )
    op.create_index(
        "ix_analysis_skill",
        "analysis_results",
        ["skill_id"],
    )

    # --- audit_log ---
    op.add_column(
        "audit_log",
        sa.Column("skill_id", sa.String(100), nullable=True),
    )
    op.create_index(
        "ix_audit_skill",
        "audit_log",
        ["skill_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_skill", table_name="audit_log")
    op.drop_column("audit_log", "skill_id")
    op.drop_index("ix_analysis_skill", table_name="analysis_results")
    op.drop_column("analysis_results", "skill_id")
