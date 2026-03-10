"""Database schema for Sidera.

Tables:
- users: Registered users with RBAC role assignments
- accounts: Connected ad platform accounts
- campaigns: Unified campaign data across platforms
- daily_metrics: Daily performance metrics per campaign
- analysis_results: Agent analysis outputs per account per run
- approval_queue: Pending/completed human approval actions
- audit_log: Every agent action, recommendation, and decision
- cost_tracking: LLM cost per account per run
- failed_runs: Dead letter queue for workflow failures
- role_memory: Persistent memory per AI employee (role)
- org_departments: Dynamic department definitions (DB layer over YAML)
- org_roles: Dynamic role definitions (DB layer over YAML)
- org_skills: Dynamic skill definitions (DB layer over YAML)
- meeting_sessions: Listen-only meeting sessions (Google Meet, etc.)
- role_messages: Peer-to-peer async messages between roles
- working_group_sessions: Ad hoc multi-agent working groups
"""

from enum import Enum as PyEnum

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# --- Enums ---


class UserRole(str, PyEnum):
    """RBAC roles for Sidera users.

    Permissions:
        ADMIN    — full access: manage users, org chart, approve, view all data
        APPROVER — approve/reject actions, view data, run skills, chat with roles
        VIEWER   — read-only: view briefings, dashboards, audit log
    """

    ADMIN = "admin"
    APPROVER = "approver"
    VIEWER = "viewer"


class Platform(str, PyEnum):
    """Platform identifier for connected accounts.

    Extensible — add new platforms as connectors are installed.
    """

    CUSTOM = "custom"


class ApprovalStatus(str, PyEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    AUTO_APPROVED = "auto_approved"


class MemoryType(str, PyEnum):
    DECISION = "decision"
    ANOMALY = "anomaly"
    PATTERN = "pattern"
    INSIGHT = "insight"
    LESSON = "lesson"
    COMMITMENT = "commitment"
    RELATIONSHIP = "relationship"
    STEWARD_NOTE = "steward_note"
    CROSS_ROLE_INSIGHT = "cross_role_insight"


class ClearanceLevel(str, PyEnum):
    """Information access classification for users and agents.

    Orthogonal to UserRole (which controls actions). ClearanceLevel controls
    what information a user or agent can access:

        PUBLIC       — General info, public metrics, system health
        INTERNAL     — Internal company data, aggregated performance
        CONFIDENTIAL — Budgets, strategic plans, competitive analysis
        RESTRICTED   — Financial projections, exec-only, legal
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class ActionType(str, PyEnum):
    """Types of actions that flow through the approval pipeline.

    Core framework types are listed here. Domain-specific action types
    can be added as connectors are installed.
    """

    RECOMMENDATION_ACCEPT = "recommendation_accept"
    RECOMMENDATION_REJECT = "recommendation_reject"
    # Skill evolution
    SKILL_PROPOSAL = "skill_proposal"
    # Claude Code task execution
    CLAUDE_CODE_TASK = "claude_code_task"
    # Role evolution
    ROLE_PROPOSAL = "role_proposal"
    # Generic connector actions
    CUSTOM_ACTION = "custom_action"


# --- Tables ---


class User(Base):
    """A registered Sidera user with an RBAC role.

    Users are identified by their Slack user ID (e.g. ``U0123ABCDEF``)
    which is the primary identity across Slack handlers, audit logs,
    and approval workflows.  The ``role`` field controls what actions
    the user may perform (admin, approver, viewer).

    If no User row exists for a given Slack user_id, access depends on
    ``settings.rbac_default_role``:
      - ``"approver"`` (default): unknown users get approver access
      - ``"viewer"``: unknown users are read-only
      - ``"none"``: unknown users are blocked entirely
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), nullable=False, unique=True, index=True)
    display_name = Column(String(255), default="")
    email = Column(String(255), default="")
    role = Column(
        Enum(
            UserRole,
            values_callable=lambda e: [x.value for x in e],
        ),
        nullable=False,
        default=UserRole.APPROVER,
    )
    clearance_level = Column(
        String(20),
        nullable=False,
        server_default="public",
        default="public",
    )
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    created_by = Column(String(255), default="")  # Who invited this user

    __table_args__ = (
        Index("ix_users_role", "role"),
        Index("ix_users_active", "is_active"),
        Index("ix_users_clearance", "clearance_level"),
    )


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

    __table_args__ = (Index("ix_accounts_user_platform", "user_id", "platform"),)


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
    # Skill tracking (null = legacy daily briefing)
    skill_id = Column(String(100), nullable=True)
    # Hierarchy tracking (null = legacy or unassigned skills)
    department_id = Column(String(100), nullable=True)
    role_id = Column(String(100), nullable=True)
    # Status
    status = Column(String(50), default="completed")  # completed, failed, partial
    error_message = Column(Text)
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("ix_analysis_user_date", "user_id", "run_date"),
        Index("ix_analysis_skill", "skill_id"),
        Index("ix_analysis_role", "role_id"),
        Index("ix_analysis_department", "department_id"),
    )


class ApprovalQueueItem(Base):
    """Human approval queue for agent-recommended actions.

    The agent proposes actions here. Humans approve/reject via Slack or email.
    Approved actions are then executed by the agent.
    """

    __tablename__ = "approval_queue"

    id = Column(Integer, primary_key=True)
    analysis_id = Column(Integer, ForeignKey("analysis_results.id"), nullable=True)
    user_id = Column(String(255), nullable=False, index=True)
    # What action is proposed
    action_type = Column(
        Enum(
            ActionType,
            values_callable=lambda e: [x.value for x in e],
        ),
        nullable=False,
    )
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"))
    # Action details
    description = Column(Text, nullable=False)  # Human-readable description
    reasoning = Column(Text)  # Agent's reasoning for this action
    action_params = Column(JSON, nullable=False)  # Parameters to execute the action
    projected_impact = Column(Text)  # Expected outcome if approved
    risk_assessment = Column(Text)  # Potential downsides
    # Approval state
    status = Column(
        Enum(
            ApprovalStatus,
            values_callable=lambda e: [x.value for x in e],
        ),
        default=ApprovalStatus.PENDING,
    )
    decided_at = Column(DateTime)
    decided_by = Column(String(255))  # Who approved/rejected
    rejection_reason = Column(Text)
    # Execution state (after approval)
    executed_at = Column(DateTime)
    execution_result = Column(JSON)
    execution_error = Column(Text)
    # Auto-execute tracking
    auto_execute_rule_id = Column(String(100))  # rule ID if auto-approved
    # Slack/notification tracking
    slack_message_ts = Column(String(100))
    slack_channel_id = Column(String(100))
    steward_user_id = Column(String(255), nullable=True)  # Steward at time of creation
    created_at = Column(DateTime, default=func.now())
    expires_at = Column(DateTime)  # Auto-expire pending items after N hours

    __table_args__ = (Index("ix_approval_user_status", "user_id", "status"),)


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
    # Types: analysis_run, recommendation, action_executed, etc.
    event_type = Column(String(100), nullable=False)
    event_data = Column(JSON)  # Full event payload
    # Source tracking
    source = Column(String(100))  # daily_briefing, manual_query, approval_workflow, skill_run
    agent_model = Column(String(100))  # Which Claude model was used
    # Skill tracking (null = legacy non-skill events)
    skill_id = Column(String(100), nullable=True)
    # Hierarchy tracking (null = legacy or unassigned)
    department_id = Column(String(100), nullable=True)
    role_id = Column(String(100), nullable=True)
    steward_user_id = Column(String(255), nullable=True)  # Steward at time of event
    # Human interaction
    required_approval = Column(Boolean, default=False)
    approval_status = Column(String(50))
    approved_by = Column(String(255))
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("ix_audit_user_created", "user_id", "created_at"),
        Index("ix_audit_event_type", "event_type"),
        Index("ix_audit_skill", "skill_id"),
        Index("ix_audit_role", "role_id"),
        Index("ix_audit_department", "department_id"),
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


class FailedRun(Base):
    """Dead letter queue for workflow failures.

    Records unrecoverable workflow errors with enough context to replay
    the event or investigate the failure. Each failed workflow invocation
    creates one row. ``resolved_at`` is set when an operator investigates
    and marks the failure as handled.
    """

    __tablename__ = "failed_runs"

    id = Column(Integer, primary_key=True)
    workflow_name = Column(String(255), nullable=False)
    event_name = Column(String(255), nullable=False)
    event_data = Column(JSON)  # Full Inngest event data for replay
    error_message = Column(Text)
    error_type = Column(String(255))  # Exception class name
    user_id = Column(String(255))
    run_id = Column(String(255))  # Inngest run ID
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=func.now())
    resolved_at = Column(DateTime)
    resolved_by = Column(String(255))

    __table_args__ = (
        Index("ix_failed_runs_user", "user_id"),
        Index("ix_failed_runs_workflow", "workflow_name"),
    )


class RoleMemory(Base):
    """Persistent memory for an AI employee (role).

    Stores learnings, decisions, anomalies, and patterns that accumulate
    across role runs. Loaded into role context before each execution so
    the agent can reference past experience.
    """

    __tablename__ = "role_memory"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), nullable=False)
    role_id = Column(String(100), nullable=False)
    department_id = Column(String(100))
    memory_type = Column(String(50), nullable=False)
    title = Column(String(500), nullable=False)
    content = Column(Text, nullable=False)
    confidence = Column(Float, default=1.0)
    source_skill_id = Column(String(200))
    source_run_date = Column(Date)
    evidence = Column(JSON)
    expires_at = Column(DateTime)
    is_archived = Column(Boolean, default=False)
    # Inter-agent relationship tracking (null = user-to-role memory)
    source_role_id = Column(String(100), nullable=True)
    # Memory consolidation / versioning
    supersedes_id = Column(
        Integer,
        ForeignKey("role_memory.id", ondelete="SET NULL"),
        nullable=True,
    )
    consolidated_into_id = Column(
        Integer,
        ForeignKey("role_memory.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_role_memory_lookup", "user_id", "role_id", "is_archived"),
        Index("ix_role_memory_type", "memory_type"),
        Index("ix_role_memory_expiry", "expires_at"),
        Index("ix_role_memory_supersedes_id", "supersedes_id"),
        Index("ix_role_memory_consolidated_into_id", "consolidated_into_id"),
        Index("ix_role_memory_source_role", "source_role_id", "role_id"),
        # Performance indexes for scale (migration 029)
        Index(
            "ix_role_memory_role_active_time",
            "role_id",
            "is_archived",
            "created_at",
        ),
        # NOTE: ix_role_memory_unconsolidated is a partial index
        # (WHERE consolidated_into_id IS NULL) created via raw SQL in
        # migration 029. SQLAlchemy doesn't support partial indexes
        # declaratively, so it's not declared here.
    )


class ConversationThread(Base):
    """Maps a Slack thread to a Sidera role for conversation mode.

    When a user starts a conversation with a role via @mention or
    /sidera chat, a record is created here to track which role owns
    the thread. Subsequent messages in the thread look up this mapping
    to know which role should respond.
    """

    __tablename__ = "conversation_threads"

    id = Column(Integer, primary_key=True)
    thread_ts = Column(String(100), nullable=False, unique=True, index=True)
    channel_id = Column(String(100), nullable=False)
    role_id = Column(String(100), nullable=False)
    user_id = Column(String(255), nullable=False)
    started_at = Column(DateTime, default=func.now())
    last_activity_at = Column(DateTime, default=func.now(), onupdate=func.now())
    turn_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    total_cost_usd = Column(Numeric(8, 4), default=0)

    __table_args__ = (
        Index("ix_conv_thread_channel", "channel_id", "thread_ts"),
        Index("ix_conv_thread_role", "role_id"),
        Index("ix_conv_thread_active", "is_active", "last_activity_at"),
    )


# --- Dynamic Org Chart Tables ---


class OrgDepartment(Base):
    """Dynamic department definition stored in the database.

    DB entries with the same ``dept_id`` as a disk YAML department will
    override it entirely.  New ``dept_id`` values add departments that
    don't exist on disk.  Soft-deleted (``is_active=False``) entries are
    excluded from the merged registry.
    """

    __tablename__ = "org_departments"

    id = Column(Integer, primary_key=True)
    dept_id = Column(String(100), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=False, default="")
    context = Column(Text, default="")
    context_text = Column(Text, default="")
    steward_user_id = Column(String(255), nullable=True, default="")
    slack_channel_id = Column(String(100), nullable=True, default="")
    credentials_scope = Column(String(50), nullable=True, default="")
    is_active = Column(Boolean, default=True)
    created_by = Column(String(255), default="")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (Index("ix_org_dept_active", "is_active"),)


class OrgRole(Base):
    """Dynamic role definition stored in the database.

    DB entries with the same ``role_id`` as a disk YAML role will
    override it entirely.  Supports all role fields including manager
    fields (``manages``, ``delegation_model``, ``synthesis_prompt``).
    """

    __tablename__ = "org_roles"

    id = Column(Integer, primary_key=True)
    role_id = Column(String(100), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=False)
    department_id = Column(String(100), nullable=False, index=True)
    description = Column(Text, nullable=False, default="")
    persona = Column(Text, default="")
    connectors = Column(JSON, default=list)
    briefing_skills = Column(JSON, default=list)
    schedule = Column(String(100), nullable=True)
    context_text = Column(Text, default="")
    principles = Column(JSON, default=list)
    manages = Column(JSON, default=list)
    delegation_model = Column(String(50), default="standard")
    synthesis_prompt = Column(Text, default="")
    clearance_level = Column(String(20), nullable=False, server_default="internal")
    steward_user_id = Column(String(255), nullable=True, default="")
    learning_channels = Column(JSON, default=list)
    document_sync = Column(JSON, default=list)
    is_active = Column(Boolean, default=True)
    created_by = Column(String(255), default="")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (Index("ix_org_role_active", "is_active"),)


class OrgSkill(Base):
    """Dynamic skill definition stored in the database.

    DB entries with the same ``skill_id`` as a disk YAML skill will
    override it entirely.  Uses ``context_text`` (pre-rendered text)
    instead of ``context_files`` (filesystem globs) since DB entries
    have no source directory on disk.
    """

    __tablename__ = "org_skills"

    id = Column(Integer, primary_key=True)
    skill_id = Column(String(100), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=False)
    version = Column(String(20), default="1.0")
    description = Column(Text, nullable=False, default="")
    category = Column(String(50), nullable=False)
    platforms = Column(JSON, default=list)
    tags = Column(JSON, default=list)
    tools_required = Column(JSON, default=list)
    model = Column(String(20), default="sonnet")
    max_turns = Column(Integer, default=20)
    system_supplement = Column(Text, nullable=False, default="")
    prompt_template = Column(Text, nullable=False, default="")
    output_format = Column(Text, nullable=False, default="")
    business_guidance = Column(Text, nullable=False, default="")
    context_text = Column(Text, default="")
    schedule = Column(String(100), nullable=True)
    chain_after = Column(String(100), nullable=True)
    requires_approval = Column(Boolean, default=True)
    min_clearance = Column(String(20), nullable=False, server_default="public")
    references = Column(JSON, default=list)
    department_id = Column(String(100), default="", index=True)
    role_id = Column(String(100), default="", index=True)
    author = Column(String(100), default="sidera")
    is_active = Column(Boolean, default=True)
    created_by = Column(String(255), default="")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (Index("ix_org_skill_active", "is_active"),)


# --- Meeting Sessions ---


class MeetingSession(Base):
    """Tracks a listen-only meeting session where Sidera participates.

    Records the meeting lifecycle from join to end, accumulates the
    transcript, and links to the post-call delegation results.

    Status lifecycle: joining -> in_call -> ended -> delegating -> completed
    """

    __tablename__ = "meeting_sessions"

    id = Column(Integer, primary_key=True)
    meeting_url = Column(String(500), nullable=False)
    role_id = Column(String(100), nullable=False)
    user_id = Column(String(255), nullable=False)
    bot_id = Column(String(255), nullable=True)  # Recall.ai bot UUID
    status = Column(String(50), default="joining")
    started_at = Column(DateTime, default=func.now())
    joined_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)

    # Transcript
    transcript_json = Column(JSON, default=list)
    transcript_summary = Column(Text, default="")

    # Action items extracted post-call
    action_items_json = Column(JSON, default=list)

    # Delegation result (from ManagerExecutor)
    delegation_result_id = Column(Integer, nullable=True)
    delegation_status = Column(String(50), nullable=True)

    # Cost tracking
    total_cost_usd = Column(Numeric(8, 4), default=0)
    agent_turns = Column(Integer, default=0)
    duration_seconds = Column(Integer, default=0)

    # Metadata
    participants_json = Column(JSON, default=list)
    slack_notification_ts = Column(String(100), nullable=True)
    channel_id = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_meeting_role", "role_id"),
        Index("ix_meeting_status", "status"),
        Index("ix_meeting_user", "user_id"),
    )


# --- Role Messages (peer-to-peer async communication) ---


class MessageStatus(str, PyEnum):
    """Status of a role-to-role message."""

    PENDING = "pending"
    DELIVERED = "delivered"
    READ = "read"
    EXPIRED = "expired"


class RoleMessage(Base):
    """Async message between roles for peer-to-peer communication.

    Roles can send messages to each other that get delivered on the
    recipient's next run (heartbeat, briefing, or conversation).
    """

    __tablename__ = "role_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    from_role_id = Column(String(200), nullable=False)
    to_role_id = Column(String(200), nullable=False)
    from_department_id = Column(String(200), nullable=False, default="")
    to_department_id = Column(String(200), nullable=False, default="")
    subject = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    reply_to_id = Column(Integer, ForeignKey("role_messages.id"), nullable=True)
    created_at = Column(DateTime, default=func.now())
    delivered_at = Column(DateTime, nullable=True)
    read_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    metadata_ = Column("metadata", JSON, nullable=True)

    # Self-referential relationship for message threads
    replies = relationship("RoleMessage", backref="parent", remote_side=[id])

    __table_args__ = (
        Index("ix_role_msg_to_status", "to_role_id", "status"),
        Index("ix_role_msg_from_created", "from_role_id", "created_at"),
    )


# --- Claude Code Tasks (headless Claude Code execution tracking) ---


class ClaudeCodeTaskRecord(Base):
    """Tracks a headless Claude Code task execution.

    Records the full lifecycle from submission to completion,
    including cost, result, and error details.

    Status lifecycle: pending → running → completed | failed | cancelled
    """

    __tablename__ = "claude_code_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(100), nullable=False, unique=True, index=True)
    skill_id = Column(String(100), nullable=False)
    user_id = Column(String(255), nullable=False)
    role_id = Column(String(100), default="")
    department_id = Column(String(100), default="")

    # Task configuration
    prompt = Column(Text, nullable=False)
    permission_mode = Column(String(50), default="acceptEdits")
    max_budget_usd = Column(Numeric(8, 4), default=5.0)

    # Status
    status = Column(String(50), default="pending")
    error_message = Column(Text, default="")

    # Result
    result_text = Column(Text, default="")
    structured_output = Column(JSON, nullable=True)

    # Cost & performance
    cost_usd = Column(Numeric(8, 4), default=0)
    num_turns = Column(Integer, default=0)
    duration_ms = Column(Integer, default=0)
    session_id = Column(String(255), default="")
    usage_json = Column(JSON, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=func.now())
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Link to Inngest workflow (if triggered via workflow)
    inngest_run_id = Column(String(255), nullable=True)

    __table_args__ = (
        Index("ix_cc_task_user", "user_id"),
        Index("ix_cc_task_skill", "skill_id"),
        Index("ix_cc_task_status", "status"),
        Index("ix_cc_task_role", "role_id"),
        Index("ix_cc_task_created", "created_at"),
    )


# --- Webhook Events (always-on monitoring) ---


class WebhookEvent(Base):
    """Inbound webhook event from an external monitoring source.

    Records all webhook events for audit, deduplication, and replay.
    Sources include Google Ads Scripts, Meta webhooks, BigQuery alerts,
    and custom monitors.

    Status lifecycle: received → processing → dispatched | ignored | error
    """

    __tablename__ = "webhook_events"

    id = Column(Integer, primary_key=True)
    source = Column(String(50), nullable=False)  # google_ads, meta, bigquery, custom:X
    event_type = Column(String(100), nullable=False)  # budget_depleted, spend_spike, etc.
    severity = Column(String(20), nullable=False)  # low, medium, high, critical
    summary = Column(Text, nullable=False, default="")
    raw_payload = Column(JSON, default=dict)
    normalized_payload = Column(JSON, default=dict)
    account_id = Column(String(100), nullable=True)
    campaign_id = Column(String(100), nullable=True)
    dedup_key = Column(String(255), nullable=True, index=True)
    status = Column(String(20), nullable=False, default="received")
    dispatched_event = Column(String(100), nullable=True)
    role_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("ix_webhook_source_type", "source", "event_type"),
        Index("ix_webhook_status", "status"),
        Index("ix_webhook_created", "created_at"),
    )


# --- Working Group Sessions (multi-agent planning) ---


class WorkingGroupSession(Base):
    """Tracks an ad hoc multi-agent working group session.

    Working groups form around a shared objective, coordinate a plan,
    execute member tasks in parallel, and synthesize a unified result.

    Status lifecycle:
        forming → planning → executing → synthesizing → completed | failed
    """

    __tablename__ = "working_group_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(
        String(100),
        nullable=False,
        unique=True,
        index=True,
    )

    # Configuration
    objective = Column(Text, nullable=False)
    coordinator_role_id = Column(String(100), nullable=False)
    member_role_ids = Column(JSON, nullable=False, default=list)
    initiated_by = Column(String(255), nullable=False, default="")

    # Status
    status = Column(String(50), nullable=False, default="forming")

    # Plan & results
    plan_json = Column(JSON, nullable=True)
    member_results_json = Column(JSON, default=dict)
    synthesis = Column(Text, default="")
    shared_context_json = Column(JSON, default=dict)

    # Constraints
    cost_cap_usd = Column(Numeric(8, 4), default=5.0)
    max_duration_minutes = Column(Integer, default=60)
    deadline = Column(DateTime, nullable=True)

    # Cost tracking
    total_cost_usd = Column(Numeric(8, 4), default=0)

    # Approval
    steward_user_id = Column(String(255), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(String(255), nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=func.now())
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Thread for Slack visibility
    slack_channel_id = Column(String(100), nullable=True)
    slack_thread_ts = Column(String(50), nullable=True)

    __table_args__ = (
        Index("ix_wg_coordinator", "coordinator_role_id"),
        Index("ix_wg_status", "status"),
        Index("ix_wg_created", "created_at"),
    )
