"""Tests for the Slack interactive routes.

Covers:
- handle_approve action handler (ack, message update, approval store)
- handle_reject action handler (ack, message update, rejection store)
- get_approval_status helper
- _pending_approvals state management
- FastAPI router endpoint registration
- Slack events endpoint integration
"""

from unittest.mock import AsyncMock

import pytest

from src.api.routes import slack as slack_module
from src.api.routes.slack import (
    _pending_approvals,
    get_approval_status,
    handle_approve,
    handle_reject,
    router,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
    """Create mock ack, body, and client objects for handler tests."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_update = AsyncMock()
    return ack, client


# ---------------------------------------------------------------------------
# 1. handle_approve tests
# ---------------------------------------------------------------------------


class TestHandleApprove:
    """Tests for the sidera_approve action handler."""

    async def test_calls_ack(self):
        ack, client = _make_mocks()
        body = _make_body()
        await handle_approve(ack=ack, body=body, client=client)
        ack.assert_awaited_once()

    async def test_updates_message_with_approved_text(self):
        ack, client = _make_mocks()
        body = _make_body(user_id="U999")
        await handle_approve(ack=ack, body=body, client=client)

        client.chat_update.assert_awaited_once()
        call_kwargs = client.chat_update.call_args.kwargs
        assert call_kwargs["text"] == "Approved by <@U999>"

    async def test_updates_message_in_correct_channel(self):
        ack, client = _make_mocks()
        body = _make_body(channel_id="CABC123", message_ts="1700000001.000200")
        await handle_approve(ack=ack, body=body, client=client)

        call_kwargs = client.chat_update.call_args.kwargs
        assert call_kwargs["channel"] == "CABC123"
        assert call_kwargs["ts"] == "1700000001.000200"

    async def test_message_blocks_contain_approved_status(self):
        ack, client = _make_mocks()
        body = _make_body(user_id="UXYZ")
        await handle_approve(ack=ack, body=body, client=client)

        call_kwargs = client.chat_update.call_args.kwargs
        blocks = call_kwargs["blocks"]
        assert len(blocks) == 2
        assert ":white_check_mark:" in blocks[0]["text"]["text"]
        assert "*Approved*" in blocks[0]["text"]["text"]
        assert "<@UXYZ>" in blocks[0]["text"]["text"]

    async def test_message_blocks_contain_approval_id(self):
        ack, client = _make_mocks()
        body = _make_body(approval_id="appr-test-42")
        await handle_approve(ack=ack, body=body, client=client)

        call_kwargs = client.chat_update.call_args.kwargs
        context_block = call_kwargs["blocks"][1]
        assert context_block["type"] == "context"
        assert "`appr-test-42`" in context_block["elements"][0]["text"]

    async def test_stores_approved_status(self):
        ack, client = _make_mocks()
        body = _make_body(approval_id="appr-store-1", user_id="UABC")
        await handle_approve(ack=ack, body=body, client=client)

        assert "appr-store-1" in _pending_approvals
        entry = _pending_approvals["appr-store-1"]
        assert entry["status"] == "approved"
        assert entry["decided_by"] == "UABC"


# ---------------------------------------------------------------------------
# 2. handle_reject tests
# ---------------------------------------------------------------------------


class TestHandleReject:
    """Tests for the sidera_reject action handler."""

    async def test_calls_ack(self):
        ack, client = _make_mocks()
        body = _make_body(action_id="sidera_reject")
        await handle_reject(ack=ack, body=body, client=client)
        ack.assert_awaited_once()

    async def test_updates_message_with_rejected_text(self):
        ack, client = _make_mocks()
        body = _make_body(action_id="sidera_reject", user_id="U555")
        await handle_reject(ack=ack, body=body, client=client)

        client.chat_update.assert_awaited_once()
        call_kwargs = client.chat_update.call_args.kwargs
        assert call_kwargs["text"] == "Rejected by <@U555>"

    async def test_updates_message_in_correct_channel(self):
        ack, client = _make_mocks()
        body = _make_body(
            action_id="sidera_reject",
            channel_id="CREJ456",
            message_ts="1700000002.000300",
        )
        await handle_reject(ack=ack, body=body, client=client)

        call_kwargs = client.chat_update.call_args.kwargs
        assert call_kwargs["channel"] == "CREJ456"
        assert call_kwargs["ts"] == "1700000002.000300"

    async def test_message_blocks_contain_rejected_status(self):
        ack, client = _make_mocks()
        body = _make_body(action_id="sidera_reject", user_id="UREJ")
        await handle_reject(ack=ack, body=body, client=client)

        call_kwargs = client.chat_update.call_args.kwargs
        blocks = call_kwargs["blocks"]
        assert len(blocks) == 2
        assert ":x:" in blocks[0]["text"]["text"]
        assert "*Rejected*" in blocks[0]["text"]["text"]
        assert "<@UREJ>" in blocks[0]["text"]["text"]

    async def test_message_blocks_contain_approval_id(self):
        ack, client = _make_mocks()
        body = _make_body(action_id="sidera_reject", approval_id="rej-99")
        await handle_reject(ack=ack, body=body, client=client)

        call_kwargs = client.chat_update.call_args.kwargs
        context_block = call_kwargs["blocks"][1]
        assert "`rej-99`" in context_block["elements"][0]["text"]

    async def test_stores_rejected_status(self):
        ack, client = _make_mocks()
        body = _make_body(
            action_id="sidera_reject",
            approval_id="rej-store-1",
            user_id="UDEF",
        )
        await handle_reject(ack=ack, body=body, client=client)

        assert "rej-store-1" in _pending_approvals
        entry = _pending_approvals["rej-store-1"]
        assert entry["status"] == "rejected"
        assert entry["decided_by"] == "UDEF"


# ---------------------------------------------------------------------------
# 3. get_approval_status tests
# ---------------------------------------------------------------------------


class TestGetApprovalStatus:
    """Tests for the get_approval_status helper function."""

    def test_returns_none_for_unknown_id(self):
        result = get_approval_status("nonexistent-id")
        assert result is None

    def test_returns_approval_dict_for_known_id(self):
        _pending_approvals["known-id"] = {
            "status": "approved",
            "decided_by": "UTEST",
        }
        result = get_approval_status("known-id")
        assert result is not None
        assert result["status"] == "approved"
        assert result["decided_by"] == "UTEST"

    def test_returns_rejection_dict(self):
        _pending_approvals["rej-id"] = {
            "status": "rejected",
            "decided_by": "UREJ",
        }
        result = get_approval_status("rej-id")
        assert result["status"] == "rejected"


# ---------------------------------------------------------------------------
# 4. _pending_approvals state tests
# ---------------------------------------------------------------------------


class TestPendingApprovals:
    """Tests for the _pending_approvals in-memory store."""

    def test_starts_empty(self):
        assert len(_pending_approvals) == 0

    async def test_multiple_approvals_stored_independently(self):
        ack, client = _make_mocks()

        body1 = _make_body(approval_id="multi-1", user_id="U001")
        body2 = _make_body(
            action_id="sidera_reject",
            approval_id="multi-2",
            user_id="U002",
        )

        await handle_approve(ack=ack, body=body1, client=client)
        await handle_reject(ack=ack, body=body2, client=client)

        assert len(_pending_approvals) == 2
        assert _pending_approvals["multi-1"]["status"] == "approved"
        assert _pending_approvals["multi-2"]["status"] == "rejected"

    async def test_overwrite_previous_decision(self):
        ack, client = _make_mocks()

        # First approve, then reject the same ID
        body_approve = _make_body(approval_id="flip-1", user_id="U001")
        body_reject = _make_body(
            action_id="sidera_reject",
            approval_id="flip-1",
            user_id="U002",
        )

        await handle_approve(ack=ack, body=body_approve, client=client)
        assert _pending_approvals["flip-1"]["status"] == "approved"

        await handle_reject(ack=ack, body=body_reject, client=client)
        assert _pending_approvals["flip-1"]["status"] == "rejected"
        assert _pending_approvals["flip-1"]["decided_by"] == "U002"


# ---------------------------------------------------------------------------
# 5. Router and module exports tests
# ---------------------------------------------------------------------------


class TestRouterAndExports:
    """Tests for the FastAPI router and module-level exports."""

    def test_router_has_slack_events_route(self):
        route_paths = [route.path for route in router.routes]
        assert "/slack/events" in route_paths

    def test_slack_events_route_accepts_post(self):
        for route in router.routes:
            if route.path == "/slack/events":
                assert "POST" in route.methods
                break
        else:
            pytest.fail("/slack/events route not found")

    def test_module_exports_slack_app(self):
        assert hasattr(slack_module, "slack_app")

    def test_module_exports_slack_handler(self):
        assert hasattr(slack_module, "slack_handler")

    def test_module_exports_router(self):
        assert hasattr(slack_module, "router")

    def test_module_exports_pending_approvals(self):
        assert hasattr(slack_module, "_pending_approvals")
        assert isinstance(slack_module._pending_approvals, dict)

    def test_module_exports_get_approval_status(self):
        assert callable(slack_module.get_approval_status)
