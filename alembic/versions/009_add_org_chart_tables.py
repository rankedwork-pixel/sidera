"""add org_departments, org_roles, org_skills tables for dynamic org chart

Revision ID: org_chart_001
Revises: conv_threads_001
Create Date: 2026-02-14

Adds three tables for runtime management of the Department -> Role -> Skill
hierarchy.  DB entries overlay or extend disk-based YAML definitions.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "org_chart_001"
down_revision = "conv_threads_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # org_departments
    # -----------------------------------------------------------------
    op.create_table(
        "org_departments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dept_id", sa.String(100), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("context", sa.Text(), server_default=""),
        sa.Column("context_text", sa.Text(), server_default=""),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("created_by", sa.String(255), server_default=""),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_org_dept_dept_id", "org_departments", ["dept_id"])
    op.create_index("ix_org_dept_active", "org_departments", ["is_active"])

    # -----------------------------------------------------------------
    # org_roles
    # -----------------------------------------------------------------
    op.create_table(
        "org_roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role_id", sa.String(100), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("department_id", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("persona", sa.Text(), server_default=""),
        sa.Column("connectors", sa.JSON(), server_default="[]"),
        sa.Column("briefing_skills", sa.JSON(), server_default="[]"),
        sa.Column("schedule", sa.String(100), nullable=True),
        sa.Column("context_text", sa.Text(), server_default=""),
        sa.Column("manages", sa.JSON(), server_default="[]"),
        sa.Column("delegation_model", sa.String(50), server_default="standard"),
        sa.Column("synthesis_prompt", sa.Text(), server_default=""),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("created_by", sa.String(255), server_default=""),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_org_role_role_id", "org_roles", ["role_id"])
    op.create_index("ix_org_role_department", "org_roles", ["department_id"])
    op.create_index("ix_org_role_active", "org_roles", ["is_active"])

    # -----------------------------------------------------------------
    # org_skills
    # -----------------------------------------------------------------
    op.create_table(
        "org_skills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("skill_id", sa.String(100), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version", sa.String(20), server_default="1.0"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("platforms", sa.JSON(), server_default="[]"),
        sa.Column("tags", sa.JSON(), server_default="[]"),
        sa.Column("tools_required", sa.JSON(), server_default="[]"),
        sa.Column("model", sa.String(20), server_default="sonnet"),
        sa.Column("max_turns", sa.Integer(), server_default="20"),
        sa.Column("system_supplement", sa.Text(), nullable=False, server_default=""),
        sa.Column("prompt_template", sa.Text(), nullable=False, server_default=""),
        sa.Column("output_format", sa.Text(), nullable=False, server_default=""),
        sa.Column("business_guidance", sa.Text(), nullable=False, server_default=""),
        sa.Column("context_text", sa.Text(), server_default=""),
        sa.Column("schedule", sa.String(100), nullable=True),
        sa.Column("chain_after", sa.String(100), nullable=True),
        sa.Column("requires_approval", sa.Boolean(), server_default="true"),
        sa.Column("department_id", sa.String(100), server_default=""),
        sa.Column("role_id", sa.String(100), server_default=""),
        sa.Column("author", sa.String(100), server_default="sidera"),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("created_by", sa.String(255), server_default=""),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_org_skill_skill_id", "org_skills", ["skill_id"])
    op.create_index("ix_org_skill_department", "org_skills", ["department_id"])
    op.create_index("ix_org_skill_role", "org_skills", ["role_id"])
    op.create_index("ix_org_skill_active", "org_skills", ["is_active"])


def downgrade() -> None:
    # org_skills
    op.drop_index("ix_org_skill_active", table_name="org_skills")
    op.drop_index("ix_org_skill_role", table_name="org_skills")
    op.drop_index("ix_org_skill_department", table_name="org_skills")
    op.drop_index("ix_org_skill_skill_id", table_name="org_skills")
    op.drop_table("org_skills")

    # org_roles
    op.drop_index("ix_org_role_active", table_name="org_roles")
    op.drop_index("ix_org_role_department", table_name="org_roles")
    op.drop_index("ix_org_role_role_id", table_name="org_roles")
    op.drop_table("org_roles")

    # org_departments
    op.drop_index("ix_org_dept_active", table_name="org_departments")
    op.drop_index("ix_org_dept_dept_id", table_name="org_departments")
    op.drop_table("org_departments")
