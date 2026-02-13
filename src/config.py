"""Sidera configuration loaded from environment variables."""

from pydantic_settings import BaseSettings
from pydantic import Field
from decimal import Decimal


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

    # Email
    resend_api_key: str = ""

    # Sentry
    sentry_dsn: str = ""

    # Cost controls
    max_llm_cost_per_account_per_day: Decimal = Field(default=Decimal("10.00"))
    max_tool_calls_per_cycle: int = 20
    cost_alert_threshold: Decimal = Field(default=Decimal("5.00"))

    # Model configuration
    model_fast: str = "claude-haiku-4-5-20251001"
    model_standard: str = "claude-sonnet-4-5-20250929"
    model_reasoning: str = "claude-opus-4-6"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


# Singleton — import this everywhere
settings = Settings()
