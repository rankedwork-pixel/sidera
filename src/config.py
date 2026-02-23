"""Sidera configuration loaded from environment variables."""

import os
from decimal import Decimal

from pydantic import Field
from pydantic_settings import BaseSettings


def _load_dotenv_overrides() -> None:
    """Pre-load .env so that empty shell env vars don't shadow real values.

    pydantic-settings gives env vars priority over .env files.  If a shell
    exports ``ANTHROPIC_API_KEY=""`` (common with tool-managed profiles), the
    empty string wins over the real key stored in ``.env``.  We fix this by
    reading .env *first* and only setting vars that are currently blank.
    """
    try:
        from dotenv import dotenv_values

        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        values = dotenv_values(env_path)
        for key, value in values.items():
            if value and not os.environ.get(key):
                os.environ[key] = value
    except Exception:
        pass  # dotenv not installed or .env missing — pydantic-settings handles it


_load_dotenv_overrides()


class Settings(BaseSettings):
    """Sidera configuration. All values loaded from .env file."""

    # App
    app_env: str = "development"
    app_log_level: str = "INFO"
    app_base_url: str = "http://localhost:8000"

    # Anthropic
    anthropic_api_key: str = ""

    # Supabase / PostgreSQL
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    database_url: str = ""

    # Redis
    redis_url: str = ""

    # Google Ads
    google_ads_developer_token: str = ""
    google_ads_client_id: str = ""
    google_ads_client_secret: str = ""
    google_ads_refresh_token: str = ""
    google_ads_login_customer_id: str = ""

    # Meta Marketing API
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_access_token: str = ""

    # Slack
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_channel_id: str = ""

    # Inngest
    inngest_event_key: str = ""
    inngest_signing_key: str = ""

    # Google BigQuery (Backend Data)
    bigquery_project_id: str = ""
    bigquery_dataset_id: str = ""
    bigquery_credentials_json: str = ""  # Service account JSON string, or empty for ADC
    bigquery_table_goals: str = "goals"
    bigquery_table_orders: str = "orders"
    bigquery_table_channel_performance: str = "channel_performance"
    bigquery_table_budget_pacing: str = "budget_pacing"
    bigquery_table_campaign_attribution: str = "campaign_attribution"

    # Google Drive / Docs / Sheets / Slides (reuses google_ads_client_id/secret)
    google_drive_refresh_token: str = ""

    # Sentry
    sentry_dsn: str = ""

    # Token encryption
    token_encryption_key: str = ""
    token_encryption_key_previous: str = ""  # Old key, kept for decrypt-only during rotation

    # Cost controls
    max_llm_cost_per_account_per_day: Decimal = Field(default=Decimal("10.00"))
    max_tool_calls_per_cycle: int = 20
    cost_alert_threshold: Decimal = Field(default=Decimal("5.00"))

    # Write operation safety
    max_budget_change_ratio: float = 1.5  # Max 50% budget increase per change

    # Auto-execute (graduated trust)
    auto_execute_enabled: bool = False  # Global kill switch — default OFF
    auto_execute_max_per_day: int = 20  # Global daily cap across all rules
    auto_execute_notify_channel: str = ""  # Defaults to slack_channel_id if empty

    # API authentication
    api_key: str = ""  # Required for REST API access in production

    # RBAC
    rbac_default_role: str = "none"  # admin, approver, viewer, none
    rbac_default_clearance: str = "public"  # public, internal, confidential, restricted
    ceo_user_id: str = ""  # Slack user ID for CEO (gets admin + restricted clearance on seed)

    # Stewardship
    stewardship_required: bool = False  # Require steward before role activation (v2)

    # Conversation mode
    conversation_max_turns_per_thread: int = 20
    conversation_thread_timeout_hours: int = 24
    conversation_max_cost_per_thread: Decimal = Field(default=Decimal("5.00"))
    conversation_tool_calls_per_turn: int = 10

    # Data retention (days, 0 = keep forever)
    retention_audit_log_days: int = 365
    retention_analysis_results_days: int = 180
    retention_cost_tracking_days: int = 180
    retention_decided_approvals_days: int = 90
    retention_resolved_failed_runs_days: int = 30
    retention_inactive_threads_days: int = 30
    retention_cold_memories_days: int = 0  # 0 = keep forever (memories are valuable)
    retention_daily_metrics_days: int = 365

    # Recall.ai (meeting bot service)
    recall_ai_api_key: str = ""
    recall_ai_region: str = "us-west-2"  # us-west-2, us-east-1, etc.
    recall_ai_webhook_secret: str = ""  # Shared secret for Recall.ai webhook auth

    # Meeting settings
    meeting_max_duration_minutes: int = 120
    meeting_max_cost_per_session: Decimal = Field(default=Decimal("10.00"))
    meeting_agent_model: str = ""  # Defaults to model_fast (Haiku) if empty
    meeting_transcript_chunk_seconds: int = 10  # Fallback poll interval (seconds)

    # Heartbeat settings (proactive check-ins)
    heartbeat_max_tool_calls: int = 15  # Max tool calls per heartbeat
    heartbeat_max_cost_per_run: Decimal = Field(default=Decimal("0.50"))  # Cost cap
    heartbeat_model: str = ""  # Default model (empty = model_fast/Haiku)
    heartbeat_cooldown_minutes: int = 5  # Min gap between heartbeats for same role
    heartbeat_enabled: bool = True  # Global kill switch

    # Messaging settings (peer-to-peer role communication)
    message_slack_notifications: bool = True  # Post to Slack when roles message each other
    message_max_per_run: int = 3  # Max messages a role can send per run
    message_expiry_days: int = 7  # Messages expire after N days if unread

    # Claude Code task execution
    claude_code_enabled: bool = True  # Global kill switch
    claude_code_max_concurrent: int = 20  # Max simultaneous instances
    claude_code_default_budget_usd: float = 5.0  # Default per-task cost cap
    claude_code_max_budget_usd: float = 25.0  # Hard ceiling per task
    claude_code_default_permission_mode: str = "acceptEdits"
    claude_code_project_dir: str = ""  # Override CWD (empty = auto-detect)

    # Webhook / Always-on monitoring
    webhook_enabled: bool = True  # Global kill switch
    webhook_secret_google_ads: str = ""  # Shared secret for Google Ads Script webhooks
    webhook_secret_custom: str = ""  # Shared secret for custom webhooks
    webhook_dedup_window_hours: int = 1  # Dedup window for identical events
    webhook_auto_investigate_severity: str = "high"  # Min severity for auto-investigation
    webhook_max_investigations_per_hour: int = 10  # Rate limit on agent investigations

    # Timezone for agent time awareness
    agent_timezone: str = "America/New_York"  # IANA timezone name

    # Model configuration
    model_fast: str = "claude-3-haiku-20240307"
    model_standard: str = "claude-sonnet-4-20250514"
    model_reasoning: str = "claude-opus-4-20250514"

    # Extended thinking — gives agents deep reasoning between tool calls
    extended_thinking_enabled: bool = True  # Global toggle — default ON
    extended_thinking_budget_tokens: int = 10000  # Token budget for internal reasoning

    # Hybrid LLM routing (external providers for cheap structured tasks)
    external_llm_enabled: bool = False  # Global kill switch — default OFF
    external_llm_provider: str = "openai_compatible"  # Provider identifier
    external_llm_endpoint: str = ""  # OpenAI-compatible base URL
    external_llm_api_key: str = ""  # Provider API key
    external_llm_model: str = ""  # Model ID for the external provider
    external_llm_timeout: float = 30.0  # HTTP timeout in seconds
    external_llm_tasks: list[str] = Field(
        default_factory=lambda: [
            "skill_routing",
            "role_routing",
            "memory_extraction",
            "reflection",
            "memory_consolidation",
            "memory_versioning",
            "friction_detection",
            "phase_compression",
        ]
    )  # Task types eligible for external routing

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


# Singleton — import this everywhere
settings = Settings()
