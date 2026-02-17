"""add department_id and role_id to analysis_results and audit_log

Revision ID: 005_add_hierarchy_columns
Revises: 004_add_action_types
Create Date: 2026-02-13

Adds hierarchy tracking columns for the Department → Role → Skill
organizational structure. Both columns are nullable for backward
compatibility with existing data.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "005_add_hierarchy_columns"
down_revision = "004_add_action_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # analysis_results
    op.add_column(
        "analysis_results",
        sa.Column("department_id", sa.String(100), nullable=True),
    )
    op.add_column(
        "analysis_results",
        sa.Column("role_id", sa.String(100), nullable=True),
    )
    op.create_index(
        "ix_analysis_role", "analysis_results", ["role_id"],
    )
    op.create_index(
        "ix_analysis_department", "analysis_results", ["department_id"],
    )

    # audit_log
    op.add_column(
        "audit_log",
        sa.Column("department_id", sa.String(100), nullable=True),
    )
    op.add_column(
        "audit_log",
        sa.Column("role_id", sa.String(100), nullable=True),
    )
    op.create_index(
        "ix_audit_role", "audit_log", ["role_id"],
    )
    op.create_index(
        "ix_audit_department", "audit_log", ["department_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_department", table_name="audit_log")
    op.drop_index("ix_audit_role", table_name="audit_log")
    op.drop_column("audit_log", "role_id")
    op.drop_column("audit_log", "department_id")

    op.drop_index("ix_analysis_department", table_name="analysis_results")
    op.drop_index("ix_analysis_role", table_name="analysis_results")
    op.drop_column("analysis_results", "role_id")
    op.drop_column("analysis_results", "department_id")
