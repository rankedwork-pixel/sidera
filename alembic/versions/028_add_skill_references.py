"""Add references JSON column to org_skills.

Stores cross-skill references as a JSON array of objects:
[{"skill_id": "...", "relationship": "...", "reason": "..."}]

This enables skill graphs — skills can declare lateral knowledge
relationships to other skills, and agents can load referenced
context on demand via the load_referenced_skill_context MCP tool.

Revision ID: skill_references_001
Revises: rls_001
Create Date: 2026-02-20
"""

revision = "skill_references_001"
down_revision = "rls_001"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.add_column(
        "org_skills",
        sa.Column("references", sa.JSON(), server_default="[]", nullable=True),
    )


def downgrade():
    op.drop_column("org_skills", "references")
