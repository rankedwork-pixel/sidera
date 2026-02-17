"""initial schema

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-02-13

Creates all 7 Sidera tables:
- accounts: Connected ad platform accounts
- campaigns: Unified campaign data across platforms
- daily_metrics: Daily performance metrics per campaign
- analysis_results: Agent analysis outputs per account per run
- approval_queue: Pending/completed human approval actions
- audit_log: Every agent action, recommendation, and decision
- cost_tracking: LLM cost per account per run
"""

import sqlalchemy as sa

from alembic import op

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None

# Enum types used across tables
platform_enum = sa.Enum("google_ads", "meta", "bing", name="platform")
approval_status_enum = sa.Enum("pending", "approved", "rejected", "expired", name="approvalstatus")
action_type_enum = sa.Enum(
    "budget_change",
    "pause_campaign",
    "enable_campaign",
    "pause_ad_set",
    "bid_change",
    "recommendation_accept",
    "recommendation_reject",
    name="actiontype",
)


def upgrade() -> None:
    # --- accounts ---
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("platform", platform_enum, nullable=False),
        sa.Column("platform_account_id", sa.String(255), nullable=False),
        sa.Column("account_name", sa.String(500), nullable=True),
        sa.Column("oauth_access_token", sa.Text(), nullable=True),
        sa.Column("oauth_refresh_token", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("timezone", sa.String(50), default="America/New_York"),
        sa.Column("target_roas", sa.Float(), nullable=True),
        sa.Column("target_cpa", sa.Numeric(10, 2), nullable=True),
        sa.Column("monthly_budget_cap", sa.Numeric(12, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_accounts_user_id", "accounts", ["user_id"])
    op.create_index("ix_accounts_user_platform", "accounts", ["user_id", "platform"])

    # --- campaigns ---
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("platform", platform_enum, nullable=False),
        sa.Column("platform_campaign_id", sa.String(255), nullable=False),
        sa.Column("campaign_name", sa.String(500), nullable=True),
        sa.Column("campaign_type", sa.String(100), nullable=True),
        sa.Column("status", sa.String(50), nullable=True),
        sa.Column("daily_budget", sa.Numeric(10, 2), nullable=True),
        sa.Column("platform_data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_campaigns_account_id", "campaigns", ["account_id"])
    op.create_index("ix_campaigns_account_platform", "campaigns", ["account_id", "platform"])
    op.create_index(
        "ix_campaigns_platform_id", "campaigns", ["platform", "platform_campaign_id"]
    )

    # --- daily_metrics ---
    op.create_table(
        "daily_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("impressions", sa.Integer(), default=0),
        sa.Column("clicks", sa.Integer(), default=0),
        sa.Column("cost", sa.Numeric(12, 2), default=0),
        sa.Column("conversions", sa.Float(), default=0),
        sa.Column("conversion_value", sa.Numeric(12, 2), default=0),
        sa.Column("ctr", sa.Float(), nullable=True),
        sa.Column("cpc", sa.Numeric(10, 2), nullable=True),
        sa.Column("cpa", sa.Numeric(10, 2), nullable=True),
        sa.Column("roas", sa.Float(), nullable=True),
        sa.Column("platform_metrics", sa.JSON(), nullable=True),
        sa.Column("pulled_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_daily_metrics_campaign_id", "daily_metrics", ["campaign_id"])
    op.create_index(
        "ix_daily_metrics_campaign_date",
        "daily_metrics",
        ["campaign_id", "date"],
        unique=True,
    )
    op.create_index("ix_daily_metrics_date", "daily_metrics", ["date"])

    # --- analysis_results ---
    op.create_table(
        "analysis_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("briefing_content", sa.Text(), nullable=True),
        sa.Column("analysis_json", sa.JSON(), nullable=True),
        sa.Column("recommendations", sa.JSON(), nullable=True),
        sa.Column("accounts_analyzed", sa.JSON(), nullable=True),
        sa.Column("total_ad_spend", sa.Numeric(12, 2), nullable=True),
        sa.Column("llm_input_tokens", sa.Integer(), default=0),
        sa.Column("llm_output_tokens", sa.Integer(), default=0),
        sa.Column("llm_cost_usd", sa.Numeric(8, 4), default=0),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("status", sa.String(50), default="completed"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_analysis_results_user_id", "analysis_results", ["user_id"])
    op.create_index("ix_analysis_user_date", "analysis_results", ["user_id", "run_date"])

    # --- approval_queue ---
    op.create_table(
        "approval_queue",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "analysis_id", sa.Integer(), sa.ForeignKey("analysis_results.id"), nullable=False
        ),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("action_type", action_type_enum, nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("action_params", sa.JSON(), nullable=False),
        sa.Column("projected_impact", sa.Text(), nullable=True),
        sa.Column("risk_assessment", sa.Text(), nullable=True),
        sa.Column("status", approval_status_enum, server_default="pending"),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decided_by", sa.String(255), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.Column("execution_result", sa.JSON(), nullable=True),
        sa.Column("execution_error", sa.Text(), nullable=True),
        sa.Column("slack_message_ts", sa.String(100), nullable=True),
        sa.Column("slack_channel_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_approval_queue_user_id", "approval_queue", ["user_id"])
    op.create_index("ix_approval_user_status", "approval_queue", ["user_id", "status"])

    # --- audit_log ---
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("event_data", sa.JSON(), nullable=True),
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column("agent_model", sa.String(100), nullable=True),
        sa.Column("required_approval", sa.Boolean(), default=False),
        sa.Column("approval_status", sa.String(50), nullable=True),
        sa.Column("approved_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])
    op.create_index("ix_audit_user_created", "audit_log", ["user_id", "created_at"])
    op.create_index("ix_audit_event_type", "audit_log", ["event_type"])

    # --- cost_tracking ---
    op.create_table(
        "cost_tracking",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=True),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), default=0),
        sa.Column("output_tokens", sa.Integer(), default=0),
        sa.Column("cost_usd", sa.Numeric(8, 4), default=0),
        sa.Column("operation", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_cost_user_date", "cost_tracking", ["user_id", "run_date"])
    op.create_index("ix_cost_account_date", "cost_tracking", ["account_id", "run_date"])


def downgrade() -> None:
    # Drop tables in reverse dependency order
    op.drop_table("cost_tracking")
    op.drop_table("audit_log")
    op.drop_table("approval_queue")
    op.drop_table("analysis_results")
    op.drop_table("daily_metrics")
    op.drop_table("campaigns")
    op.drop_table("accounts")

    # Drop enum types
    action_type_enum.drop(op.get_bind(), checkfirst=True)
    approval_status_enum.drop(op.get_bind(), checkfirst=True)
    platform_enum.drop(op.get_bind(), checkfirst=True)
