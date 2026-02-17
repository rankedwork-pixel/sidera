"""Tests for the event_reactor_workflow."""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Schema integration
# ---------------------------------------------------------------------------


class TestWebhookEventModel:
    def test_model_has_required_columns(self):
        from src.models.schema import WebhookEvent

        assert hasattr(WebhookEvent, "source")
        assert hasattr(WebhookEvent, "event_type")
        assert hasattr(WebhookEvent, "severity")
        assert hasattr(WebhookEvent, "dedup_key")
        assert hasattr(WebhookEvent, "status")
        assert hasattr(WebhookEvent, "raw_payload")
        assert hasattr(WebhookEvent, "normalized_payload")
        assert hasattr(WebhookEvent, "role_id")
        assert hasattr(WebhookEvent, "dispatched_event")

    def test_tablename(self):
        from src.models.schema import WebhookEvent

        assert WebhookEvent.__tablename__ == "webhook_events"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestWebhookConfig:
    def test_default_settings(self):
        from src.config import Settings

        s = Settings(
            _env_file=None,
            anthropic_api_key="test",
        )
        assert s.webhook_enabled is True
        assert s.webhook_dedup_window_hours == 1
        assert s.webhook_auto_investigate_severity == "high"
        assert s.webhook_max_investigations_per_hour == 10
        assert s.webhook_secret_google_ads == ""
        assert s.webhook_secret_custom == ""


# ---------------------------------------------------------------------------
# RoleDefinition event_subscriptions field
# ---------------------------------------------------------------------------


class TestEventSubscriptions:
    def test_role_definition_has_field(self):
        from src.skills.schema import RoleDefinition

        role = RoleDefinition(
            id="test",
            name="Test",
            department_id="dept",
            description="desc",
            event_subscriptions=("budget_depleted", "spend_spike"),
        )
        assert role.event_subscriptions == ("budget_depleted", "spend_spike")

    def test_role_definition_default_empty(self):
        from src.skills.schema import RoleDefinition

        role = RoleDefinition(
            id="test",
            name="Test",
            department_id="dept",
            description="desc",
        )
        assert role.event_subscriptions == ()

    def test_media_buyer_has_subscriptions(self):
        """Verify the YAML role file includes event_subscriptions."""
        from pathlib import Path

        from src.skills.schema import load_role_from_yaml

        role_path = Path("src/skills/library/marketing/performance_media_buyer/_role.yaml")
        if role_path.exists():
            role = load_role_from_yaml(role_path)
            assert "budget_depleted" in role.event_subscriptions
            assert "spend_spike" in role.event_subscriptions
        else:
            pytest.skip("Role YAML not found")

    def test_head_of_it_has_system_alert(self):
        """Verify head_of_it subscribes to system_alert."""
        from pathlib import Path

        from src.skills.schema import load_role_from_yaml

        role_path = Path("src/skills/library/it/head_of_it/_role.yaml")
        if role_path.exists():
            role = load_role_from_yaml(role_path)
            assert "system_alert" in role.event_subscriptions
        else:
            pytest.skip("Role YAML not found")


# ---------------------------------------------------------------------------
# Prompt supplement
# ---------------------------------------------------------------------------


class TestWebhookPrompts:
    def test_supplement_exists(self):
        from src.agent.prompts import WEBHOOK_REACTION_SUPPLEMENT

        assert "real-time event" in WEBHOOK_REACTION_SUPPLEMENT
        assert "false alarm" in WEBHOOK_REACTION_SUPPLEMENT

    def test_build_webhook_reaction_prompt(self):
        from src.agent.prompts import build_webhook_reaction_prompt

        prompt = build_webhook_reaction_prompt(
            role_name="Media Buyer",
            event_type="budget_depleted",
            severity="critical",
            source="google_ads",
            summary="Budget exhausted at 2:30 PM",
            campaign_name="Brand Search",
            account_id="1234567890",
        )
        assert "CRITICAL" in prompt
        assert "budget_depleted" in prompt
        assert "Brand Search" in prompt
        assert "1234567890" in prompt
        assert "Media Buyer" in prompt

    def test_build_prompt_with_details(self):
        from src.agent.prompts import build_webhook_reaction_prompt

        prompt = build_webhook_reaction_prompt(
            role_name="Role",
            event_type="custom",
            severity="medium",
            source="test",
            summary="Test",
            details={"metric": "cpa", "value": 25.0},
        )
        assert "cpa" in prompt
        assert "25.0" in prompt

    def test_build_prompt_minimal(self):
        from src.agent.prompts import build_webhook_reaction_prompt

        prompt = build_webhook_reaction_prompt(
            role_name="R",
            event_type="custom",
            severity="low",
            source="test",
            summary="Minimal",
        )
        assert "Minimal" in prompt
        assert "Investigate" in prompt


# ---------------------------------------------------------------------------
# Heartbeat user_prompt_override
# ---------------------------------------------------------------------------


class TestHeartbeatOverride:
    def test_run_heartbeat_turn_accepts_override(self):
        """Verify the signature accepts user_prompt_override."""
        import inspect

        from src.agent.core import SideraAgent

        sig = inspect.signature(SideraAgent.run_heartbeat_turn)
        assert "user_prompt_override" in sig.parameters


# ---------------------------------------------------------------------------
# DB service methods
# ---------------------------------------------------------------------------


class TestWebhookDBService:
    def test_service_has_methods(self):
        from src.db import service

        assert hasattr(service, "record_webhook_event")
        assert hasattr(service, "check_webhook_dedup")
        assert hasattr(service, "update_webhook_event_status")
        assert hasattr(service, "get_recent_webhook_events")
        assert hasattr(service, "get_webhook_event")


# ---------------------------------------------------------------------------
# MCP tool
# ---------------------------------------------------------------------------


class TestWebhookMCPTool:
    def test_system_tools_includes_webhook(self):
        from src.mcp_servers.system import create_system_tools

        tools = create_system_tools()
        names = [getattr(t, "name", getattr(t, "__name__", "")) for t in tools]
        assert "get_webhook_events" in names

    def test_tool_count_is_8(self):
        from src.mcp_servers.system import create_system_tools

        assert len(create_system_tools()) == 8


# ---------------------------------------------------------------------------
# Workflow exports
# ---------------------------------------------------------------------------


class TestWorkflowExport:
    def test_event_reactor_in_all_workflows(self):
        from src.workflows.daily_briefing import all_workflows

        # Check that we have 17 workflows
        assert len(all_workflows) == 17


# ---------------------------------------------------------------------------
# Alembic migration
# ---------------------------------------------------------------------------


class TestAlembicMigration:
    def test_migration_file_exists(self):
        from pathlib import Path

        migration = Path("alembic/versions/024_add_webhook_events.py")
        assert migration.exists()

    def test_migration_revision_chain(self):
        from pathlib import Path

        content = Path("alembic/versions/024_add_webhook_events.py").read_text()
        assert 'down_revision = "doc_sync_001"' in content
        assert 'revision = "webhook_events_001"' in content
