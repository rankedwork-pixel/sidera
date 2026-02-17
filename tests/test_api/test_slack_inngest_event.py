"""Tests for Inngest event emission from Slack approval handlers.

Verifies that handle_approve and handle_reject emit the
``sidera/approval.decided`` Inngest event after persisting the
decision to the database, and that failures in event emission
do not break the handler flow.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.api.routes.slack import (
    _pending_approvals,
    handle_approve,
    handle_reject,
)

# =====================================================================
# Helpers
# =====================================================================


@pytest.fixture(autouse=True)
def _clear_pending_approvals():
    """Ensure no stale approvals leak between tests."""
    _pending_approvals.clear()
    yield
    _pending_approvals.clear()


def _make_body(
    action_id: str = "sidera_approve",
    approval_id: str = "appr-001",
    user_id: str = "U123USER",
    channel_id: str = "C456CHAN",
    message_ts: str = "1700000000.000100",
) -> dict:
    """Build a minimal Slack interaction body matching Bolt's format."""
    return {
        "actions": [
            {
                "action_id": action_id,
                "value": approval_id,
            }
        ],
        "user": {"id": user_id},
        "channel": {"id": channel_id},
        "container": {"message_ts": message_ts},
    }


def _make_mocks():
    """Create mock ack and client objects for handler tests."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_update = AsyncMock()
    return ack, client


# =====================================================================
# Tests — handle_approve Inngest event emission
# =====================================================================


class TestHandleApproveInngestEvent:
    """Verify handle_approve emits sidera/approval.decided via Inngest."""

    @pytest.mark.asyncio
    async def test_emits_inngest_event_with_correct_data(self):
        """handle_approve should send an Inngest event with approval data."""
        ack, client = _make_mocks()
        body = _make_body(approval_id="appr-42", user_id="UAPPR")

        mock_ic = AsyncMock()

        with (
            patch("src.db.session.get_db_session", side_effect=Exception("skip DB")),
            patch(
                "src.workflows.inngest_client.inngest_client",
                mock_ic,
            ),
        ):
            await handle_approve(ack=ack, body=body, client=client)

        mock_ic.send.assert_awaited_once()
        event = mock_ic.send.call_args[0][0]
        assert event.name == "sidera/approval.decided"
        assert event.data["approval_id"] == "appr-42"
        assert event.data["status"] == "approved"
        assert event.data["decided_by"] == "UAPPR"

    @pytest.mark.asyncio
    async def test_event_name_is_approval_decided(self):
        """The emitted event must use the canonical name."""
        ack, client = _make_mocks()
        body = _make_body(approval_id="appr-name-check")

        mock_ic = AsyncMock()

        with (
            patch("src.db.session.get_db_session", side_effect=Exception("skip DB")),
            patch(
                "src.workflows.inngest_client.inngest_client",
                mock_ic,
            ),
        ):
            await handle_approve(ack=ack, body=body, client=client)

        event = mock_ic.send.call_args[0][0]
        assert event.name == "sidera/approval.decided"

    @pytest.mark.asyncio
    async def test_approval_id_passed_correctly(self):
        """The approval_id in the event data must match the action value."""
        ack, client = _make_mocks()
        body = _make_body(approval_id="appr-id-check-99")

        mock_ic = AsyncMock()

        with (
            patch("src.db.session.get_db_session", side_effect=Exception("skip DB")),
            patch(
                "src.workflows.inngest_client.inngest_client",
                mock_ic,
            ),
        ):
            await handle_approve(ack=ack, body=body, client=client)

        event = mock_ic.send.call_args[0][0]
        assert event.data["approval_id"] == "appr-id-check-99"

    @pytest.mark.asyncio
    async def test_handler_succeeds_when_inngest_fails(self):
        """If Inngest emission raises, the handler must still complete."""
        ack, client = _make_mocks()
        body = _make_body(approval_id="appr-resilient")

        mock_ic = AsyncMock()
        mock_ic.send = AsyncMock(side_effect=Exception("Inngest down"))

        with (
            patch("src.db.session.get_db_session", side_effect=Exception("skip DB")),
            patch(
                "src.workflows.inngest_client.inngest_client",
                mock_ic,
            ),
        ):
            # Should NOT raise — the handler swallows the error
            await handle_approve(ack=ack, body=body, client=client)

        # The in-memory store and Slack message update should still work
        ack.assert_awaited_once()
        client.chat_update.assert_awaited_once()
        assert _pending_approvals["appr-resilient"]["status"] == "approved"

    @pytest.mark.asyncio
    async def test_still_updates_pending_approvals(self):
        """In-memory _pending_approvals must still be set."""
        ack, client = _make_mocks()
        body = _make_body(approval_id="appr-mem", user_id="UMEM")

        mock_ic = AsyncMock()

        with (
            patch("src.db.session.get_db_session", side_effect=Exception("skip DB")),
            patch(
                "src.workflows.inngest_client.inngest_client",
                mock_ic,
            ),
        ):
            await handle_approve(ack=ack, body=body, client=client)

        assert "appr-mem" in _pending_approvals
        assert _pending_approvals["appr-mem"]["status"] == "approved"
        assert _pending_approvals["appr-mem"]["decided_by"] == "UMEM"

    @pytest.mark.asyncio
    async def test_still_updates_slack_message(self):
        """The Slack message must still be updated with approved banner."""
        ack, client = _make_mocks()
        body = _make_body(approval_id="appr-slack", user_id="USLK")

        mock_ic = AsyncMock()

        with (
            patch("src.db.session.get_db_session", side_effect=Exception("skip DB")),
            patch(
                "src.workflows.inngest_client.inngest_client",
                mock_ic,
            ),
        ):
            await handle_approve(ack=ack, body=body, client=client)

        client.chat_update.assert_awaited_once()
        call_kwargs = client.chat_update.call_args.kwargs
        assert call_kwargs["text"] == "Approved by <@USLK>"

    @pytest.mark.asyncio
    async def test_status_field_is_approved(self):
        """The status field in the event data must be 'approved'."""
        ack, client = _make_mocks()
        body = _make_body(approval_id="appr-status")

        mock_ic = AsyncMock()

        with (
            patch("src.db.session.get_db_session", side_effect=Exception("skip DB")),
            patch(
                "src.workflows.inngest_client.inngest_client",
                mock_ic,
            ),
        ):
            await handle_approve(ack=ack, body=body, client=client)

        event = mock_ic.send.call_args[0][0]
        assert event.data["status"] == "approved"


# =====================================================================
# Tests — handle_reject Inngest event emission
# =====================================================================


class TestHandleRejectInngestEvent:
    """Verify handle_reject emits sidera/approval.decided via Inngest."""

    @pytest.mark.asyncio
    async def test_emits_inngest_event_with_correct_data(self):
        """handle_reject should send an Inngest event with rejection data."""
        ack, client = _make_mocks()
        body = _make_body(
            action_id="sidera_reject",
            approval_id="rej-42",
            user_id="UREJ",
        )

        mock_ic = AsyncMock()

        with (
            patch("src.db.session.get_db_session", side_effect=Exception("skip DB")),
            patch(
                "src.workflows.inngest_client.inngest_client",
                mock_ic,
            ),
        ):
            await handle_reject(ack=ack, body=body, client=client)

        mock_ic.send.assert_awaited_once()
        event = mock_ic.send.call_args[0][0]
        assert event.name == "sidera/approval.decided"
        assert event.data["approval_id"] == "rej-42"
        assert event.data["status"] == "rejected"
        assert event.data["decided_by"] == "UREJ"

    @pytest.mark.asyncio
    async def test_event_name_is_approval_decided(self):
        """The emitted event must use the canonical name."""
        ack, client = _make_mocks()
        body = _make_body(
            action_id="sidera_reject",
            approval_id="rej-name-check",
        )

        mock_ic = AsyncMock()

        with (
            patch("src.db.session.get_db_session", side_effect=Exception("skip DB")),
            patch(
                "src.workflows.inngest_client.inngest_client",
                mock_ic,
            ),
        ):
            await handle_reject(ack=ack, body=body, client=client)

        event = mock_ic.send.call_args[0][0]
        assert event.name == "sidera/approval.decided"

    @pytest.mark.asyncio
    async def test_approval_id_passed_correctly(self):
        """The approval_id in the event data must match the action value."""
        ack, client = _make_mocks()
        body = _make_body(
            action_id="sidera_reject",
            approval_id="rej-id-check-77",
        )

        mock_ic = AsyncMock()

        with (
            patch("src.db.session.get_db_session", side_effect=Exception("skip DB")),
            patch(
                "src.workflows.inngest_client.inngest_client",
                mock_ic,
            ),
        ):
            await handle_reject(ack=ack, body=body, client=client)

        event = mock_ic.send.call_args[0][0]
        assert event.data["approval_id"] == "rej-id-check-77"

    @pytest.mark.asyncio
    async def test_handler_succeeds_when_inngest_fails(self):
        """If Inngest emission raises, the handler must still complete."""
        ack, client = _make_mocks()
        body = _make_body(
            action_id="sidera_reject",
            approval_id="rej-resilient",
        )

        mock_ic = AsyncMock()
        mock_ic.send = AsyncMock(side_effect=Exception("Inngest down"))

        with (
            patch("src.db.session.get_db_session", side_effect=Exception("skip DB")),
            patch(
                "src.workflows.inngest_client.inngest_client",
                mock_ic,
            ),
        ):
            # Should NOT raise — the handler swallows the error
            await handle_reject(ack=ack, body=body, client=client)

        ack.assert_awaited_once()
        client.chat_update.assert_awaited_once()
        assert _pending_approvals["rej-resilient"]["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_still_updates_pending_approvals(self):
        """In-memory _pending_approvals must still be set."""
        ack, client = _make_mocks()
        body = _make_body(
            action_id="sidera_reject",
            approval_id="rej-mem",
            user_id="UMEM",
        )

        mock_ic = AsyncMock()

        with (
            patch("src.db.session.get_db_session", side_effect=Exception("skip DB")),
            patch(
                "src.workflows.inngest_client.inngest_client",
                mock_ic,
            ),
        ):
            await handle_reject(ack=ack, body=body, client=client)

        assert "rej-mem" in _pending_approvals
        assert _pending_approvals["rej-mem"]["status"] == "rejected"
        assert _pending_approvals["rej-mem"]["decided_by"] == "UMEM"

    @pytest.mark.asyncio
    async def test_still_updates_slack_message(self):
        """The Slack message must still be updated with rejected banner."""
        ack, client = _make_mocks()
        body = _make_body(
            action_id="sidera_reject",
            approval_id="rej-slack",
            user_id="USLK",
        )

        mock_ic = AsyncMock()

        with (
            patch("src.db.session.get_db_session", side_effect=Exception("skip DB")),
            patch(
                "src.workflows.inngest_client.inngest_client",
                mock_ic,
            ),
        ):
            await handle_reject(ack=ack, body=body, client=client)

        client.chat_update.assert_awaited_once()
        call_kwargs = client.chat_update.call_args.kwargs
        assert call_kwargs["text"] == "Rejected by <@USLK>"

    @pytest.mark.asyncio
    async def test_status_field_is_rejected(self):
        """The status field in the event data must be 'rejected'."""
        ack, client = _make_mocks()
        body = _make_body(
            action_id="sidera_reject",
            approval_id="rej-status",
        )

        mock_ic = AsyncMock()

        with (
            patch("src.db.session.get_db_session", side_effect=Exception("skip DB")),
            patch(
                "src.workflows.inngest_client.inngest_client",
                mock_ic,
            ),
        ):
            await handle_reject(ack=ack, body=body, client=client)

        event = mock_ic.send.call_args[0][0]
        assert event.data["status"] == "rejected"
