"""Database schema for Sidera.

Tables:
- accounts: Connected ad platform accounts
- campaigns: Unified campaign data across platforms
- daily_metrics: Daily performance metrics per campaign
- analysis_results: Agent analysis outputs per account per run
- approval_queue: Pending/completed human approval actions
- audit_log: Every agent action, recommendation, and decision
- cost_tracking: LLM cost per account per run
"""

from datetime import datetime, date
from decimal import Decimal
from enum import Enum as PyEnum

from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    Boolean,
    DateTime,
    Date,
    Text,
    Numeric,
    ForeignKey,
    Enum,
    JSON,
    Index,
    func,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# --- Enums ---


class Platform(str, PyEnum):
    GOOGLE_ADS = "google_ads"
    META = "meta"
    BING = "bing"


class ApprovalStatus(str, PyEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ActionType(str, PyEnum):
    BUDGET_CHANGE = "budget_change"
    PAUSE_CAMPAIGN = "pause_campaign"
    ENABLE_CAMPAIGN = "enable_campaign"
    PAUSE_AD_SET = "pause_ad_set"
    BID_CHANGE = "bid_change"
    RECOMMENDATION_ACCEPT = "recommendation_accept"
    RECOMMENDATION_REJECT = "recommendation_reject"


# --- Tables ---


class Account(Base):
    """A connected ad platform account."""

    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), nullable=False, index=True)
    platform = Column(Enum(Platform), nullable=False)
    platform_account_id = Column(String(255), nullable=False)
    account_name = Column(String(500))
    oauth_access_token = Column(Text)  # encrypted at rest via Supabase
    oauth_refresh_token = Column(Text)
    token_expires_at = Column(DateTime)
    is_active = Column(Boolean, default=True)
    timezone = Column(String(50), default="America/New_York")
    # Advertiser goals — set during onboarding
    target_roas = Column(Float)
    target_cpa = Column(Numeric(10, 2))
    monthly_budget_cap = Column(Numeric(12, 2))
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    campaigns = relationship("Campaign", back_populates="account")

    __table_args__ = (
        Index("ix_accounts_user_platform", "user_id", "platform"),
    )


class Campaign(Base):
    """Unified campaign data across platforms.

    Normalizes Google Ads campaigns, Meta campaigns, and Bing campaigns
    into a single schema with platform-specific details in the `platform_data` JSON.
    """

    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    platform = Column(Enum(Platform), nullable=False)
    platform_campaign_id = Column(String(255), nullable=False)
    campaign_name = Column(String(500))
    campaign_type = Column(String(100))  # search, display, shopping, pmax, advantage+, etc.
    status = Column(String(50))  # enabled, paused, removed
    daily_budget = Column(Numeric(10, 2))
    # Platform-specific fields stored as JSON (bid strategy, targeting, etc.)
    platform_data = Column(JSON, default=dict)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    account = relationship("Account", back_populates="campaigns")
    daily_metrics = relationship("DailyMetric", back_populates="campaign")

    __table_args__ = (
        Index("ix_campaigns_account_platform", "account_id", "platform"),
        Index("ix_campaigns_platform_id", "platform", "platform_campaign_id"),
    )


class DailyMetric(Base):
    """Daily performance metrics per campaign.

    All metrics are normalized to a common schema regardless of platform.
    See src/models/normalized.py for the mapping logic.
    """

    __tablename__ = "daily_metrics"

    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False, index=True)
    date = Column(Date, nullable=False)
    # Core metrics (normalized across platforms)
    impressions = Column(Integer, default=0)
    clicks = Column(Integer, default=0)
    cost = Column(Numeric(12, 2), default=0)
    conversions = Column(Float, default=0)
    conversion_value = Column(Numeric(12, 2), default=0)
    # Computed metrics
    ctr = Column(Float)  # clicks / impressions
    cpc = Column(Numeric(10, 2))  # cost / clicks
    cpa = Column(Numeric(10, 2))  # cost / conversions
    roas = Column(Float)  # conversion_value / cost
    # Platform-specific metrics stored as JSON
    platform_metrics = Column(JSON, default=dict)
    pulled_at = Column(DateTime, default=func.now())

    campaign = relationship("Campaign", back_populates="daily_metrics")

    __table_args__ = (
        Index("ix_daily_metrics_campaign_date", "campaign_id", "date", unique=True),
        Index("ix_daily_metrics_date", "date"),
    )


class AnalysisResult(Base):
    """Agent analysis output for a single run.

    Stores the full analysis, briefing content, and recommendations
    generated during a daily analysis cycle.
    """

    __tablename__ = "analysis_results"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), nullable=False, index=True)
    run_date = Column(Date, nullable=False)
    # Analysis content
    briefing_content = Column(Text)  # The formatted daily briefing
    analysis_json = Column(JSON)  # Structured analysis data
    recommendations = Column(JSON)  # List of recommended actions
    # Metadata
    accounts_analyzed = Column(JSON)  # List of account IDs included
    total_ad_spend = Column(Numeric(12, 2))  # Total spend across all accounts for the period
    # Cost tracking
    llm_input_tokens = Column(Integer, default=0)
    llm_output_tokens = Column(Integer, default=0)
    llm_cost_usd = Column(Numeric(8, 4), default=0)
    duration_seconds = Column(Float)
    # Status
    status = Column(String(50), default="completed")  # completed, failed, partial
    error_message = Column(Text)
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("ix_analysis_user_date", "user_id", "run_date"),
    )


class ApprovalQueueItem(Base):
    """Human approval queue for agent-recommended actions.

    The agent proposes actions here. Humans approve/reject via Slack or email.
    Approved actions are then executed by the agent.
    """

    __tablename__ = "approval_queue"

    id = Column(Integer, primary_key=True)
    analysis_id = Column(Integer, ForeignKey("analysis_results.id"), nullable=False)
    user_id = Column(String(255), nullable=False, index=True)
    # What action is proposed
    action_type = Column(Enum(ActionType), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"))
    # Action details
    description = Column(Text, nullable=False)  # Human-readable description
    reasoning = Column(Text)  # Agent's reasoning for this action
    action_params = Column(JSON, nullable=False)  # Parameters to execute the action
    projected_impact = Column(Text)  # Expected outcome if approved
    risk_assessment = Column(Text)  # Potential downsides
    # Approval state
    status = Column(Enum(ApprovalStatus), default=ApprovalStatus.PENDING)
    decided_at = Column(DateTime)
    decided_by = Column(String(255))  # Who approved/rejected
    rejection_reason = Column(Text)
    # Execution state (after approval)
    executed_at = Column(DateTime)
    execution_result = Column(JSON)
    execution_error = Column(Text)
    # Slack/notification tracking
    slack_message_ts = Column(String(100))
    slack_channel_id = Column(String(100))
    created_at = Column(DateTime, default=func.now())
    expires_at = Column(DateTime)  # Auto-expire pending items after N hours

    __table_args__ = (
        Index("ix_approval_user_status", "user_id", "status"),
    )


class AuditLog(Base):
    """Comprehensive audit trail of every agent action and decision.

    This is the legal shield — proof of what the agent did, why, and
    whether a human approved it.
    """

    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"))
    # Event details
    event_type = Column(String(100), nullable=False)  # analysis_run, recommendation, action_executed, etc.
    event_data = Column(JSON)  # Full event payload
    # Source tracking
    source = Column(String(100))  # daily_briefing, manual_query, approval_workflow
    agent_model = Column(String(100))  # Which Claude model was used
    # Human interaction
    required_approval = Column(Boolean, default=False)
    approval_status = Column(String(50))
    approved_by = Column(String(255))
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("ix_audit_user_created", "user_id", "created_at"),
        Index("ix_audit_event_type", "event_type"),
    )


class CostTracking(Base):
    """Per-account, per-run LLM cost tracking for circuit breakers and billing."""

    __tablename__ = "cost_tracking"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"))
    run_date = Column(Date, nullable=False)
    model = Column(String(100), nullable=False)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    cost_usd = Column(Numeric(8, 4), default=0)
    operation = Column(String(100))  # daily_analysis, budget_optimization, nl_query, etc.
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("ix_cost_user_date", "user_id", "run_date"),
        Index("ix_cost_account_date", "account_id", "run_date"),
    )
