"""Tests for system introspection MCP tools.

Covers:
- get_system_health — dashboard with DB, Redis, config, DLQ, approvals
- get_failed_runs — DLQ queries with filters
- resolve_failed_run — marking DLQ entries resolved
- get_recent_audit_events — audit log queries with filters
- get_approval_queue_status — approval queue queries
- get_conversation_status — conversation thread queries
- get_cost_summary — LLM cost aggregation
"""

from datetime import date, datetime  # noqa: I001
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_servers.system import (
    get_approval_queue_status,
    get_conversation_status,
    get_cost_summary,
    get_failed_runs,
    get_recent_audit_events,
    get_system_health,
    resolve_failed_run_tool,
)


# ============================================================
# Helpers
# ============================================================


def _text_from(result: dict) -> str:
    """Extract text from an MCP-style response."""
    return result["content"][0]["text"]


def _is_error(result: dict) -> bool:
    """Check if the response is an error."""
    return result.get("is_error", False)


# ============================================================
# get_system_health
# ============================================================


class TestGetSystemHealth:
    """Test the system health dashboard tool."""

    @pytest.mark.asyncio
    async def test_returns_dashboard_text(self):
        """Health check returns a text dashboard even when components fail."""
        # get_system_health uses lazy imports inside try/except blocks,
        # so failures in DB or Redis are caught gracefully. We just call
        # it and verify it produces a dashboard (errors are reported inline).
        with (
            patch(
                "src.db.session.get_db_session",
                side_effect=Exception("no DB in test"),
            ),
        ):
            result = await get_system_health({})

        text = _text_from(result)
        assert "System Health Dashboard" in text
        assert not _is_error(result)

    @pytest.mark.asyncio
    async def test_handles_db_failure_gracefully(self):
        """If DB is down, health check still returns (with error noted)."""
        with (
            patch(
                "src.db.session.get_db_session",
                side_effect=Exception("DB unreachable"),
            ),
        ):
            result = await get_system_health({})

        text = _text_from(result)
        # Should still produce output, just with error noted
        assert "System Health Dashboard" in text
        assert not _is_error(result)


# ============================================================
# get_failed_runs
# ============================================================


class TestGetFailedRuns:
    """Test DLQ query tool."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_failures(self):
        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        with patch("src.db.session.get_db_session", return_value=mock_cm):
            result = await get_failed_runs({})

        text = _text_from(result)
        assert "No" in text
        assert "running cleanly" in text

    @pytest.mark.asyncio
    async def test_returns_failures_with_details(self):
        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_run = MagicMock()
        mock_run.id = 42
        mock_run.workflow_name = "daily_briefing_workflow"
        mock_run.event_name = "sidera/briefing.run"
        mock_run.error_type = "ConnectionError"
        mock_run.error_message = "DB connection refused"
        mock_run.created_at = datetime(2025, 1, 15, 8, 0, 0)
        mock_run.resolved_at = None
        mock_run.resolved_by = None
        mock_run.event_data = {"user_id": "U123"}

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_run]
        mock_session.execute.return_value = mock_result

        with patch("src.db.session.get_db_session", return_value=mock_cm):
            result = await get_failed_runs({})

        text = _text_from(result)
        assert "42" in text
        assert "daily_briefing_workflow" in text
        assert "ConnectionError" in text
        assert "UNRESOLVED" in text

    @pytest.mark.asyncio
    async def test_respects_workflow_filter(self):
        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        with patch("src.db.session.get_db_session", return_value=mock_cm):
            result = await get_failed_runs({"workflow_name": "conversation_turn_workflow"})

        # Should have executed a query (we can't easily inspect the WHERE
        # clause in the mock, but no error means the filter was accepted)
        assert not _is_error(result)


# ============================================================
# resolve_failed_run
# ============================================================


class TestResolveFailedRun:
    """Test DLQ resolution tool."""

    @pytest.mark.asyncio
    async def test_requires_failed_run_id(self):
        result = await resolve_failed_run_tool({})
        assert _is_error(result)
        assert "required" in _text_from(result).lower()

    @pytest.mark.asyncio
    async def test_resolves_existing_entry(self):
        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_run = MagicMock()
        mock_run.id = 42

        with (
            patch("src.db.session.get_db_session", return_value=mock_cm),
            patch(
                "src.db.service.resolve_failed_run",
                AsyncMock(return_value=mock_run),
            ),
        ):
            result = await resolve_failed_run_tool(
                {"failed_run_id": 42, "resolution_note": "Transient error"}
            )

        text = _text_from(result)
        assert "42" in text
        assert "resolved" in text.lower()

    @pytest.mark.asyncio
    async def test_returns_error_for_missing_entry(self):
        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.db.session.get_db_session", return_value=mock_cm),
            patch(
                "src.db.service.resolve_failed_run",
                AsyncMock(return_value=None),
            ),
        ):
            result = await resolve_failed_run_tool({"failed_run_id": 999})

        assert _is_error(result)
        assert "999" in _text_from(result)


# ============================================================
# get_recent_audit_events
# ============================================================


class TestGetRecentAuditEvents:
    """Test audit log query tool."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_events(self):
        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        with patch("src.db.session.get_db_session", return_value=mock_cm):
            result = await get_recent_audit_events({})

        text = _text_from(result)
        assert "No audit events" in text

    @pytest.mark.asyncio
    async def test_returns_events_with_details(self):
        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_event = MagicMock()
        mock_event.created_at = datetime(2025, 1, 15, 10, 30, 0)
        mock_event.event_type = "skill_run"
        mock_event.role_id = "performance_media_buyer"
        mock_event.skill_id = "anomaly_detector"
        mock_event.source = "daily_briefing"
        mock_event.agent_model = "claude-sonnet"
        mock_event.event_data = {"status": "success"}

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_event]
        mock_session.execute.return_value = mock_result

        with patch("src.db.session.get_db_session", return_value=mock_cm):
            result = await get_recent_audit_events({})

        text = _text_from(result)
        assert "skill_run" in text
        assert "anomaly_detector" in text

    @pytest.mark.asyncio
    async def test_respects_event_type_filter(self):
        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        with patch("src.db.session.get_db_session", return_value=mock_cm):
            result = await get_recent_audit_events({"event_type": "error"})

        assert not _is_error(result)

    @pytest.mark.asyncio
    async def test_respects_role_id_filter(self):
        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        with patch("src.db.session.get_db_session", return_value=mock_cm):
            result = await get_recent_audit_events({"role_id": "head_of_it"})

        assert not _is_error(result)


# ============================================================
# get_approval_queue_status
# ============================================================


class TestGetApprovalQueueStatus:
    """Test approval queue query tool."""

    @pytest.mark.asyncio
    async def test_returns_empty_pending(self):
        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        with patch("src.db.session.get_db_session", return_value=mock_cm):
            result = await get_approval_queue_status({})

        text = _text_from(result)
        assert "No pending approvals" in text

    @pytest.mark.asyncio
    async def test_returns_pending_approvals(self):
        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_item = MagicMock()
        mock_item.id = 7
        mock_item.status = MagicMock(value="pending")
        mock_item.action_type = MagicMock(value="budget_change")
        mock_item.description = "Increase Brand Search budget to $50"
        mock_item.created_at = datetime(2025, 1, 15, 6, 0, 0)
        mock_item.executed_at = None

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_item]
        mock_session.execute.return_value = mock_result

        with patch("src.db.session.get_db_session", return_value=mock_cm):
            result = await get_approval_queue_status({})

        text = _text_from(result)
        assert "7" in text
        assert "budget_change" in text

    @pytest.mark.asyncio
    async def test_supports_all_filter(self):
        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        with patch("src.db.session.get_db_session", return_value=mock_cm):
            result = await get_approval_queue_status({"status_filter": "all"})

        assert not _is_error(result)


# ============================================================
# get_conversation_status
# ============================================================


class TestGetConversationStatus:
    """Test conversation thread status tool."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_threads(self):
        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        with patch("src.db.session.get_db_session", return_value=mock_cm):
            result = await get_conversation_status({})

        text = _text_from(result)
        assert "No active conversation" in text

    @pytest.mark.asyncio
    async def test_returns_active_threads(self):
        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_thread = MagicMock()
        mock_thread.is_active = True
        mock_thread.role_id = "performance_media_buyer"
        mock_thread.user_id = "U123"
        mock_thread.turn_count = 5
        mock_thread.total_cost_usd = Decimal("0.75")
        mock_thread.last_activity_at = datetime(2025, 1, 15, 14, 0, 0)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_thread]
        mock_session.execute.return_value = mock_result

        with patch("src.db.session.get_db_session", return_value=mock_cm):
            result = await get_conversation_status({})

        text = _text_from(result)
        assert "performance_media_buyer" in text
        assert "5" in text  # turn count


# ============================================================
# get_cost_summary
# ============================================================


class TestGetCostSummary:
    """Test cost summary tool."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_costs(self):
        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.execute.return_value = mock_result

        with patch("src.db.session.get_db_session", return_value=mock_cm):
            result = await get_cost_summary({})

        text = _text_from(result)
        assert "No cost data" in text

    @pytest.mark.asyncio
    async def test_returns_cost_breakdown(self):
        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        # Mock daily rows
        mock_daily = MagicMock()
        mock_daily.run_date = date(2025, 1, 15)
        mock_daily.total_cost = Decimal("1.52")
        mock_daily.total_input = 50000
        mock_daily.total_output = 5000
        mock_daily.run_count = 3

        mock_daily_result = MagicMock()
        mock_daily_result.all.return_value = [mock_daily]

        # Mock model rows
        mock_model = MagicMock()
        mock_model.model = "claude-sonnet"
        mock_model.total_cost = Decimal("1.20")
        mock_model.run_count = 2

        mock_model_result = MagicMock()
        mock_model_result.all.return_value = [mock_model]

        # execute() returns different results for two calls
        mock_session.execute.side_effect = [
            mock_daily_result,
            mock_model_result,
        ]

        with patch("src.db.session.get_db_session", return_value=mock_cm):
            result = await get_cost_summary({"days": 7})

        text = _text_from(result)
        assert "Cost Summary" in text
        assert "$1.52" in text
        assert "claude-sonnet" in text


# ============================================================
# Tool registration
# ============================================================


class TestToolRegistration:
    """Test that system tools are registered in the global registry."""

    def test_tools_registered(self):
        from src.mcp_servers.system import create_system_tools

        tools = create_system_tools()
        assert len(tools) == 8

        # Check all expected tool names
        tool_names = {t.tool_name if hasattr(t, "tool_name") else t.__name__ for t in tools}
        expected = {
            "get_system_health",
            "get_failed_runs",
            "resolve_failed_run",
            "get_recent_audit_events",
            "get_approval_queue_status",
            "get_conversation_status",
            "get_cost_summary",
            "get_webhook_events",
        }
        assert expected == tool_names

    def test_tools_in_global_registry(self):
        import src.mcp_servers.system  # noqa: F401, I001
        from src.agent.tool_registry import get_global_registry

        registry = get_global_registry()
        assert "get_system_health" in registry
        assert "get_failed_runs" in registry
        assert "resolve_failed_run" in registry
        assert "get_recent_audit_events" in registry
        assert "get_approval_queue_status" in registry
        assert "get_conversation_status" in registry
        assert "get_cost_summary" in registry
        assert "get_webhook_events" in registry


# ============================================================
# IT Department YAML structure
# ============================================================


class TestITDepartmentYAML:
    """Test that the IT department skill files are valid."""

    def test_department_yaml_loads(self):
        from pathlib import Path

        import yaml

        dept_path = Path("src/skills/library/it/_department.yaml")
        assert dept_path.exists(), "IT department YAML missing"
        data = yaml.safe_load(dept_path.read_text())
        assert data["id"] == "it"
        assert "IT" in data["name"]

    def test_head_of_it_role_yaml_loads(self):
        from pathlib import Path

        import yaml

        role_path = Path("src/skills/library/it/head_of_it/_role.yaml")
        assert role_path.exists(), "head_of_it role YAML missing"
        data = yaml.safe_load(role_path.read_text())
        assert data["id"] == "head_of_it"
        assert data["department_id"] == "it"
        assert len(data["briefing_skills"]) >= 3

    def test_all_head_of_it_skills_load(self):
        from pathlib import Path

        import yaml

        skill_dir = Path("src/skills/library/it/head_of_it")
        skill_files = [f for f in skill_dir.glob("*.yaml") if not f.name.startswith("_")]
        assert len(skill_files) >= 3, f"Expected 3+ skills, found {len(skill_files)}"

        for sf in skill_files:
            data = yaml.safe_load(sf.read_text())
            assert "id" in data, f"{sf.name} missing 'id'"
            assert "tools_required" in data, f"{sf.name} missing 'tools_required'"
            assert "system_supplement" in data, f"{sf.name} missing 'system_supplement'"

    def test_skill_tools_reference_system_tools(self):
        """All tools referenced in head_of_it skills should be system tools."""
        from pathlib import Path

        import yaml

        system_tool_names = {
            "get_system_health",
            "get_failed_runs",
            "resolve_failed_run",
            "get_recent_audit_events",
            "get_approval_queue_status",
            "get_conversation_status",
            "get_cost_summary",
            "get_webhook_events",
            "send_slack_alert",
            "send_slack_thread_reply",
        }

        skill_dir = Path("src/skills/library/it/head_of_it")
        for sf in skill_dir.glob("*.yaml"):
            if sf.name.startswith("_"):
                continue
            data = yaml.safe_load(sf.read_text())
            for tool_name in data.get("tools_required", []):
                assert tool_name in system_tool_names, (
                    f"Skill {sf.name} references unknown tool: {tool_name}"
                )
