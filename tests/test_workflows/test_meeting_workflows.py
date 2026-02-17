"""Tests for meeting join and meeting end Inngest workflows."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import inngest
import pytest

from src.workflows.daily_briefing import (
    meeting_end_workflow,
    meeting_join_workflow,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(event_name: str, event_data: dict[str, Any]) -> MagicMock:
    """Build a minimal mock Inngest context."""
    ctx = MagicMock()
    ctx.event = MagicMock()
    ctx.event.name = event_name
    ctx.event.data = event_data
    ctx.run_id = "test-run-id"

    # step.run should await the async function it's given
    async def _step_run(step_name: str, func):
        return await func()

    ctx.step = MagicMock()
    ctx.step.run = AsyncMock(side_effect=_step_run)
    ctx.step.send_event = AsyncMock()
    return ctx


# ---------------------------------------------------------------------------
# meeting_join_workflow
# ---------------------------------------------------------------------------


class TestMeetingJoinWorkflow:
    """Tests for the meeting join workflow."""

    @pytest.mark.asyncio
    async def test_missing_meeting_url(self):
        ctx = _make_ctx(
            "sidera/meeting.join",
            {
                "role_id": "head_of_marketing",
                "user_id": "U123",
            },
        )
        with pytest.raises(inngest.NonRetriableError, match="meeting_url"):
            await meeting_join_workflow._handler(ctx)

    @pytest.mark.asyncio
    async def test_missing_role_id(self):
        ctx = _make_ctx(
            "sidera/meeting.join",
            {
                "meeting_url": "https://meet.google.com/abc",
                "user_id": "U123",
            },
        )
        with pytest.raises(inngest.NonRetriableError, match="role_id"):
            await meeting_join_workflow._handler(ctx)

    @pytest.mark.asyncio
    async def test_role_not_found(self):
        mock_registry = MagicMock()
        mock_registry.get_role.return_value = None

        ctx = _make_ctx(
            "sidera/meeting.join",
            {
                "meeting_url": "https://meet.google.com/abc",
                "role_id": "nonexistent",
                "user_id": "U123",
            },
        )

        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = mock_registry
            with pytest.raises(inngest.NonRetriableError, match="not found"):
                await meeting_join_workflow._handler(ctx)

    @pytest.mark.asyncio
    async def test_successful_join(self):
        mock_role = MagicMock()
        mock_role.name = "Head of Marketing"
        mock_role.manages = ("media_buyer",)

        mock_registry = MagicMock()
        mock_registry.get_role.return_value = mock_role

        mock_meeting_ctx = MagicMock()
        mock_meeting_ctx.bot_id = "bot-123"
        mock_meeting_ctx.meeting_id = 42
        mock_meeting_ctx.role_name = "Head of Marketing"

        mock_manager = MagicMock()
        mock_manager.join = AsyncMock(return_value=mock_meeting_ctx)

        ctx = _make_ctx(
            "sidera/meeting.join",
            {
                "meeting_url": "https://meet.google.com/abc",
                "role_id": "head_of_marketing",
                "user_id": "U123",
                "channel_id": "C456",
            },
        )

        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = mock_registry
            with patch(
                "src.meetings.session.get_meeting_manager",
                return_value=mock_manager,
            ):
                result = await meeting_join_workflow._handler(ctx)

        assert result["status"] == "joined"
        assert result["bot_id"] == "bot-123"
        assert result["role_name"] == "Head of Marketing"
        mock_manager.join.assert_called_once()


# ---------------------------------------------------------------------------
# meeting_end_workflow
# ---------------------------------------------------------------------------


class TestMeetingEndWorkflow:
    """Tests for the meeting end workflow."""

    @pytest.mark.asyncio
    async def test_missing_bot_id(self):
        ctx = _make_ctx(
            "sidera/meeting.ended",
            {
                "meeting_id": 42,
                "role_id": "head_of_marketing",
                "user_id": "U123",
            },
        )
        with pytest.raises(inngest.NonRetriableError, match="bot_id"):
            await meeting_end_workflow._handler(ctx)

    @pytest.mark.asyncio
    async def test_missing_meeting_id(self):
        ctx = _make_ctx(
            "sidera/meeting.ended",
            {
                "bot_id": "bot-123",
                "role_id": "head_of_marketing",
                "user_id": "U123",
            },
        )
        with pytest.raises(inngest.NonRetriableError, match="meeting_id"):
            await meeting_end_workflow._handler(ctx)

    @pytest.mark.asyncio
    async def test_meeting_not_found(self):
        mock_db_session = AsyncMock()

        ctx = _make_ctx(
            "sidera/meeting.ended",
            {
                "bot_id": "bot-123",
                "meeting_id": 999,
                "role_id": "head_of_marketing",
                "user_id": "U123",
            },
        )

        with patch("src.db.service.get_meeting_session", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            with patch("src.db.session.get_db_session") as mock_db:
                mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_db_session)
                mock_db.return_value.__aexit__ = AsyncMock(return_value=False)
                with pytest.raises(inngest.NonRetriableError, match="not found"):
                    await meeting_end_workflow._handler(ctx)

    @pytest.mark.asyncio
    async def test_successful_end_with_delegation(self):
        """Full workflow: load meeting -> summarize -> save -> post -> delegate."""
        mock_meeting = MagicMock()
        mock_meeting.id = 42
        mock_meeting.meeting_url = "https://meet.google.com/abc"
        mock_meeting.role_id = "head_of_marketing"
        mock_meeting.transcript_json = [
            {"speaker": "Alice", "text": "We need to increase budget"},
            {"speaker": "Bob", "text": "ROAS is down 15%"},
        ]
        mock_meeting.transcript_summary = None
        mock_meeting.duration_seconds = 1800
        mock_meeting.agent_turns = 0
        mock_meeting.total_cost_usd = 0.50
        mock_meeting.participants_json = [{"name": "Alice"}, {"name": "Bob"}]
        mock_meeting.action_items_json = None

        mock_role = MagicMock()
        mock_role.name = "Head of Marketing"
        mock_role.manages = ("media_buyer", "reporting_analyst")
        mock_role.persona = "You are the Head of Marketing."

        mock_registry = MagicMock()
        mock_registry.get_role.return_value = mock_role

        mock_turn_result = MagicMock()
        mock_turn_result.response_text = (
            "## Summary\n- Discussed budget increase\n- ROAS declining\n\n"
            "## Action Items\n1. Review Meta campaign budgets (Alice)\n"
            "2. Pull latest ROAS data (Bob)\n\n"
            "## Department Follow-ups\n- Monitor next week"
        )
        mock_turn_result.cost = {"total_cost_usd": 0.15}

        mock_db_session = AsyncMock()
        mock_db_session.execute = AsyncMock()
        mock_db_session.commit = AsyncMock()

        mock_slack = MagicMock()
        mock_slack.send_alert.return_value = {"ok": True}

        ctx = _make_ctx(
            "sidera/meeting.ended",
            {
                "bot_id": "bot-123",
                "meeting_id": 42,
                "role_id": "head_of_marketing",
                "user_id": "U123",
                "channel_id": "C456",
            },
        )

        with (
            patch(
                "src.db.service.get_meeting_session",
                new_callable=AsyncMock,
                return_value=mock_meeting,
            ),
            patch(
                "src.db.service.update_meeting_transcript",
                new_callable=AsyncMock,
            ),
            patch("src.db.service.log_event", new_callable=AsyncMock),
            patch("src.db.session.get_db_session") as mock_db_ctx,
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch("src.agent.core.SideraAgent") as mock_agent_cls,
            patch(
                "src.connectors.slack.SlackConnector",
                return_value=mock_slack,
            ),
        ):
            mock_db_ctx.return_value.__aenter__ = AsyncMock(
                return_value=mock_db_session,
            )
            mock_db_ctx.return_value.__aexit__ = AsyncMock(
                return_value=False,
            )

            mock_agent = MagicMock()
            mock_agent.run_conversation_turn = AsyncMock(
                return_value=mock_turn_result,
            )
            mock_agent_cls.return_value = mock_agent

            result = await meeting_end_workflow._handler(ctx)

        assert result["status"] == "completed"
        assert result["meeting_id"] == 42
        assert result["action_items"] == 2
        assert result["delegated"] is True

    @pytest.mark.asyncio
    async def test_end_non_manager_no_delegation(self):
        """Non-manager roles should not trigger delegation."""
        mock_meeting = MagicMock()
        mock_meeting.id = 42
        mock_meeting.meeting_url = "https://meet.google.com/abc"
        mock_meeting.role_id = "performance_media_buyer"
        mock_meeting.transcript_json = [
            {"speaker": "Alice", "text": "Check the ads"},
        ]
        mock_meeting.transcript_summary = None
        mock_meeting.duration_seconds = 600
        mock_meeting.agent_turns = 0
        mock_meeting.total_cost_usd = 0.50
        mock_meeting.participants_json = [{"name": "Alice"}]
        mock_meeting.action_items_json = None

        mock_role = MagicMock()
        mock_role.name = "Performance Media Buyer"
        mock_role.manages = ()
        mock_role.persona = "You buy media."

        mock_registry = MagicMock()
        mock_registry.get_role.return_value = mock_role

        mock_turn_result = MagicMock()
        mock_turn_result.response_text = (
            "## Summary\n- Discussed ads\n\n## Action Items\n1. Check budgets"
        )
        mock_turn_result.cost = {"total_cost_usd": 0.10}

        mock_db_session = AsyncMock()
        mock_db_session.execute = AsyncMock()
        mock_db_session.commit = AsyncMock()

        mock_slack = MagicMock()
        mock_slack.send_alert.return_value = {"ok": True}

        ctx = _make_ctx(
            "sidera/meeting.ended",
            {
                "bot_id": "bot-456",
                "meeting_id": 42,
                "role_id": "performance_media_buyer",
                "user_id": "U123",
                "channel_id": "C456",
            },
        )

        with (
            patch(
                "src.db.service.get_meeting_session",
                new_callable=AsyncMock,
                return_value=mock_meeting,
            ),
            patch(
                "src.db.service.update_meeting_transcript",
                new_callable=AsyncMock,
            ),
            patch("src.db.service.log_event", new_callable=AsyncMock),
            patch("src.db.session.get_db_session") as mock_db_ctx,
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch("src.agent.core.SideraAgent") as mock_agent_cls,
            patch(
                "src.connectors.slack.SlackConnector",
                return_value=mock_slack,
            ),
        ):
            mock_db_ctx.return_value.__aenter__ = AsyncMock(
                return_value=mock_db_session,
            )
            mock_db_ctx.return_value.__aexit__ = AsyncMock(
                return_value=False,
            )

            mock_agent = MagicMock()
            mock_agent.run_conversation_turn = AsyncMock(
                return_value=mock_turn_result,
            )
            mock_agent_cls.return_value = mock_agent

            result = await meeting_end_workflow._handler(ctx)

        assert result["status"] == "completed"
        assert result["delegated"] is False

    @pytest.mark.asyncio
    async def test_empty_transcript(self):
        """Empty transcript should still produce a summary."""
        mock_meeting = MagicMock()
        mock_meeting.id = 42
        mock_meeting.meeting_url = "https://meet.google.com/abc"
        mock_meeting.role_id = "head_of_marketing"
        mock_meeting.transcript_json = []
        mock_meeting.transcript_summary = None
        mock_meeting.duration_seconds = 60
        mock_meeting.agent_turns = 0
        mock_meeting.total_cost_usd = 0
        mock_meeting.participants_json = []
        mock_meeting.action_items_json = None

        mock_role = MagicMock()
        mock_role.name = "Head of Marketing"
        mock_role.manages = ("media_buyer",)
        mock_role.persona = ""

        mock_registry = MagicMock()
        mock_registry.get_role.return_value = mock_role

        mock_db_session = AsyncMock()
        mock_db_session.execute = AsyncMock()
        mock_db_session.commit = AsyncMock()

        mock_slack = MagicMock()
        mock_slack.send_alert.return_value = {"ok": True}

        ctx = _make_ctx(
            "sidera/meeting.ended",
            {
                "bot_id": "bot-789",
                "meeting_id": 42,
                "role_id": "head_of_marketing",
                "user_id": "U123",
            },
        )

        with (
            patch(
                "src.db.service.get_meeting_session",
                new_callable=AsyncMock,
                return_value=mock_meeting,
            ),
            patch(
                "src.db.service.update_meeting_transcript",
                new_callable=AsyncMock,
            ),
            patch("src.db.service.log_event", new_callable=AsyncMock),
            patch("src.db.session.get_db_session") as mock_db_ctx,
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch(
                "src.connectors.slack.SlackConnector",
                return_value=mock_slack,
            ),
        ):
            mock_db_ctx.return_value.__aenter__ = AsyncMock(
                return_value=mock_db_session,
            )
            mock_db_ctx.return_value.__aexit__ = AsyncMock(
                return_value=False,
            )

            result = await meeting_end_workflow._handler(ctx)

        assert result["status"] == "completed"
