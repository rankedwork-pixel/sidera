"""Tests for src.mcp_servers.messaging — peer-to-peer role messaging tools.

Covers:
- set_messaging_context / clear_messaging_context lifecycle
- send_message_to_role — validation, self-messaging block, message limit
- check_inbox — empty inbox, pending messages
- reply_to_message — validation, chain depth
- compose_message_context — formatting, empty list, dict/object support
- Anti-loop protection (max messages per run, max chain depth)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.mcp_servers.messaging import (
    _MAX_CHAIN_DEPTH,
    _MAX_MESSAGES_PER_RUN,
    _message_count_var,
    _messaging_context_var,
    check_inbox,
    clear_messaging_context,
    compose_message_context,
    reply_to_message,
    send_message_to_role,
    set_messaging_context,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_error(result: dict) -> bool:
    """Check if an MCP response is an error."""
    return result.get("is_error", False)


def _text(result: dict) -> str:
    """Extract text from an MCP response."""
    return result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Fake types for testing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeRole:
    id: str = "performance_media_buyer"
    name: str = "Performance Media Buyer"
    department_id: str = "marketing"
    description: str = "Buys media"
    persona: str = ""
    manages: tuple[str, ...] = ()
    briefing_skills: tuple[str, ...] = ()


class FakeRegistry:
    """Minimal registry stub for messaging tests."""

    def __init__(self, roles: dict[str, FakeRole] | None = None):
        self._roles = roles or {
            "performance_media_buyer": FakeRole(),
            "head_of_it": FakeRole(
                id="head_of_it",
                name="Head of IT",
                department_id="it",
            ),
            "sysadmin": FakeRole(
                id="sysadmin",
                name="Sysadmin",
                department_id="it",
            ),
        }

    def get_role(self, role_id: str) -> FakeRole | None:
        return self._roles.get(role_id)


@dataclass
class FakeMessage:
    """Mimics RoleMessage model."""

    id: int = 1
    from_role_id: str = "head_of_it"
    to_role_id: str = "performance_media_buyer"
    from_department_id: str = "it"
    to_department_id: str = "marketing"
    subject: str = "Cost spike alert"
    content: str = "I noticed a 3x cost increase today."
    status: str = "pending"
    reply_to_id: int | None = None
    created_at: datetime = None  # type: ignore[assignment]
    delivered_at: datetime | None = None
    read_at: datetime | None = None
    expires_at: datetime | None = None
    metadata_: dict | None = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Context lifecycle tests
# ---------------------------------------------------------------------------


class TestMessagingContextLifecycle:
    def setup_method(self):
        clear_messaging_context()

    def teardown_method(self):
        clear_messaging_context()

    def test_context_starts_none(self):
        assert _messaging_context_var.get() is None

    def test_set_context(self):
        registry = FakeRegistry()
        set_messaging_context("head_of_it", "it", registry)
        ctx = _messaging_context_var.get()
        assert ctx is not None
        assert ctx["role_id"] == "head_of_it"
        assert ctx["department_id"] == "it"
        assert ctx["registry"] is registry

    def test_clear_context(self):
        set_messaging_context("head_of_it", "it", FakeRegistry())
        clear_messaging_context()
        assert _messaging_context_var.get() is None

    def test_message_count_reset_on_set(self):
        _message_count_var.set(5)
        set_messaging_context("head_of_it", "it", FakeRegistry())
        assert _message_count_var.get() == 0

    def test_message_count_reset_on_clear(self):
        _message_count_var.set(3)
        clear_messaging_context()
        assert _message_count_var.get() == 0


# ---------------------------------------------------------------------------
# send_message_to_role tests
# ---------------------------------------------------------------------------


class TestSendMessageToRole:
    def setup_method(self):
        clear_messaging_context()

    def teardown_method(self):
        clear_messaging_context()

    @pytest.mark.asyncio
    async def test_no_context_returns_error(self):
        result = await send_message_to_role(
            {
                "to_role_id": "sysadmin",
                "subject": "Test",
                "content": "Hello",
            }
        )
        assert _is_error(result)
        assert "not available" in _text(result).lower()

    @pytest.mark.asyncio
    async def test_self_messaging_blocked(self):
        set_messaging_context("head_of_it", "it", FakeRegistry())
        result = await send_message_to_role(
            {
                "to_role_id": "head_of_it",
                "subject": "Self",
                "content": "Hello me",
            }
        )
        assert _is_error(result)
        assert "yourself" in _text(result).lower()

    @pytest.mark.asyncio
    async def test_missing_fields_error(self):
        set_messaging_context("head_of_it", "it", FakeRegistry())
        result = await send_message_to_role(
            {
                "to_role_id": "",
                "subject": "",
                "content": "",
            }
        )
        assert _is_error(result)

    @pytest.mark.asyncio
    async def test_nonexistent_target_role_error(self):
        set_messaging_context("head_of_it", "it", FakeRegistry())
        result = await send_message_to_role(
            {
                "to_role_id": "nonexistent_role",
                "subject": "Test",
                "content": "Hello",
            }
        )
        assert _is_error(result)
        assert "not found" in _text(result).lower()

    @pytest.mark.asyncio
    async def test_message_limit_enforced(self):
        set_messaging_context("head_of_it", "it", FakeRegistry())
        _message_count_var.set(_MAX_MESSAGES_PER_RUN)
        result = await send_message_to_role(
            {
                "to_role_id": "sysadmin",
                "subject": "Test",
                "content": "Hello",
            }
        )
        assert _is_error(result)
        assert "maximum" in _text(result).lower()

    @pytest.mark.asyncio
    async def test_successful_send(self):
        set_messaging_context("head_of_it", "it", FakeRegistry())

        with (
            patch(
                "src.db.session.get_db_session",
            ) as mock_session_ctx,
            patch(
                "src.db.service.create_role_message",
                new_callable=AsyncMock,
                return_value=42,
            ),
            patch(
                "src.mcp_servers.messaging._notify_message_sent",
                new_callable=AsyncMock,
            ),
        ):
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=mock_session,
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(
                return_value=False,
            )

            result = await send_message_to_role(
                {
                    "to_role_id": "sysadmin",
                    "subject": "Cost question",
                    "content": "Did you notice increased costs?",
                }
            )

        assert not _is_error(result)
        assert "Sysadmin" in _text(result)
        assert _message_count_var.get() == 1

    @pytest.mark.asyncio
    async def test_message_count_increments(self):
        set_messaging_context("head_of_it", "it", FakeRegistry())
        assert _message_count_var.get() == 0

        with (
            patch(
                "src.db.session.get_db_session",
            ) as mock_session_ctx,
            patch(
                "src.db.service.create_role_message",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "src.mcp_servers.messaging._notify_message_sent",
                new_callable=AsyncMock,
            ),
        ):
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=mock_session,
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(
                return_value=False,
            )

            await send_message_to_role(
                {
                    "to_role_id": "sysadmin",
                    "subject": "First",
                    "content": "Hello",
                }
            )
            assert _message_count_var.get() == 1

            await send_message_to_role(
                {
                    "to_role_id": "sysadmin",
                    "subject": "Second",
                    "content": "Hello again",
                }
            )
            assert _message_count_var.get() == 2


# ---------------------------------------------------------------------------
# check_inbox tests
# ---------------------------------------------------------------------------


class TestCheckInbox:
    def setup_method(self):
        clear_messaging_context()

    def teardown_method(self):
        clear_messaging_context()

    @pytest.mark.asyncio
    async def test_no_context_returns_error(self):
        result = await check_inbox({})
        assert _is_error(result)

    @pytest.mark.asyncio
    async def test_empty_inbox(self):
        set_messaging_context("sysadmin", "it", FakeRegistry())

        with (
            patch(
                "src.db.session.get_db_session",
            ) as mock_session_ctx,
            patch(
                "src.db.service.get_pending_messages",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=mock_session,
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(
                return_value=False,
            )

            result = await check_inbox({})

        assert not _is_error(result)
        assert "no pending" in _text(result).lower()

    @pytest.mark.asyncio
    async def test_inbox_with_messages(self):
        set_messaging_context("sysadmin", "it", FakeRegistry())
        msgs = [
            FakeMessage(id=1, subject="Alert 1"),
            FakeMessage(id=2, subject="Alert 2"),
        ]

        with (
            patch(
                "src.db.session.get_db_session",
            ) as mock_session_ctx,
            patch(
                "src.db.service.get_pending_messages",
                new_callable=AsyncMock,
                return_value=msgs,
            ),
            patch(
                "src.db.service.mark_messages_delivered",
                new_callable=AsyncMock,
            ),
        ):
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=mock_session,
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(
                return_value=False,
            )

            result = await check_inbox({})

        assert not _is_error(result)
        text = _text(result)
        assert "2 pending" in text
        assert "Alert 1" in text
        assert "Alert 2" in text


# ---------------------------------------------------------------------------
# reply_to_message tests
# ---------------------------------------------------------------------------


class TestReplyToMessage:
    def setup_method(self):
        clear_messaging_context()

    def teardown_method(self):
        clear_messaging_context()

    @pytest.mark.asyncio
    async def test_no_context_returns_error(self):
        result = await reply_to_message(
            {
                "message_id": 1,
                "content": "Thanks",
            }
        )
        assert _is_error(result)

    @pytest.mark.asyncio
    async def test_missing_fields(self):
        set_messaging_context("sysadmin", "it", FakeRegistry())
        result = await reply_to_message(
            {
                "message_id": None,
                "content": "",
            }
        )
        assert _is_error(result)

    @pytest.mark.asyncio
    async def test_message_not_found(self):
        set_messaging_context("sysadmin", "it", FakeRegistry())

        with (
            patch(
                "src.db.session.get_db_session",
            ) as mock_session_ctx,
            patch(
                "src.db.service.get_message_thread",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=mock_session,
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(
                return_value=False,
            )

            result = await reply_to_message(
                {
                    "message_id": 999,
                    "content": "Reply",
                }
            )

        assert _is_error(result)
        assert "not found" in _text(result).lower()

    @pytest.mark.asyncio
    async def test_message_limit_shared_with_send(self):
        set_messaging_context("sysadmin", "it", FakeRegistry())
        _message_count_var.set(_MAX_MESSAGES_PER_RUN)

        result = await reply_to_message(
            {
                "message_id": 1,
                "content": "Reply",
            }
        )
        assert _is_error(result)
        assert "maximum" in _text(result).lower()

    @pytest.mark.asyncio
    async def test_successful_reply(self):
        set_messaging_context("sysadmin", "it", FakeRegistry())
        original = FakeMessage(
            id=1,
            from_role_id="head_of_it",
            from_department_id="it",
            subject="Cost spike",
        )

        with (
            patch(
                "src.db.session.get_db_session",
            ) as mock_session_ctx,
            patch(
                "src.db.service.get_message_thread",
                new_callable=AsyncMock,
                return_value=[original],
            ),
            patch(
                "src.db.service.mark_message_read",
                new_callable=AsyncMock,
            ),
            patch(
                "src.db.service.create_role_message",
                new_callable=AsyncMock,
                return_value=2,
            ),
        ):
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=mock_session,
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(
                return_value=False,
            )

            result = await reply_to_message(
                {
                    "message_id": 1,
                    "content": "Looking into it now.",
                }
            )

        assert not _is_error(result)
        assert "head_of_it" in _text(result)
        assert "Re:" in _text(result)
        assert _message_count_var.get() == 1


# ---------------------------------------------------------------------------
# compose_message_context tests
# ---------------------------------------------------------------------------


class TestComposeMessageContext:
    def test_empty_list_returns_empty_string(self):
        assert compose_message_context([]) == ""

    def test_single_message_object(self):
        msg = FakeMessage(
            id=1,
            from_role_id="head_of_it",
            subject="Alert",
            content="Something happened.",
        )
        result = compose_message_context([msg])
        assert "Inbox" in result
        assert "head_of_it" in result
        assert "Alert" in result
        assert "Something happened" in result
        assert "Message ID: 1" in result

    def test_multiple_messages(self):
        msgs = [
            FakeMessage(id=1, from_role_id="a", subject="One", content="C1"),
            FakeMessage(id=2, from_role_id="b", subject="Two", content="C2"),
        ]
        result = compose_message_context(msgs)
        assert "One" in result
        assert "Two" in result
        assert "C1" in result
        assert "C2" in result

    def test_dict_messages_supported(self):
        msgs = [
            {
                "from_role_id": "head_of_it",
                "subject": "Dict test",
                "content": "Dict content",
                "id": 5,
                "created_at": None,
            },
        ]
        result = compose_message_context(msgs)
        assert "Dict test" in result
        assert "Dict content" in result

    def test_reply_instruction_included(self):
        msg = FakeMessage(id=3)
        result = compose_message_context([msg])
        assert "reply_to_message" in result

    def test_date_formatting(self):
        msg = FakeMessage(
            id=1,
            created_at=datetime(2024, 6, 15, 10, 0, tzinfo=timezone.utc),
        )
        result = compose_message_context([msg])
        assert "Jun 15" in result


# ---------------------------------------------------------------------------
# Anti-loop protection tests
# ---------------------------------------------------------------------------


class TestAntiLoopProtection:
    def test_max_messages_per_run_constant(self):
        assert _MAX_MESSAGES_PER_RUN == 3

    def test_max_chain_depth_constant(self):
        assert _MAX_CHAIN_DEPTH == 5

    @pytest.mark.asyncio
    async def test_send_blocked_at_max_count(self):
        set_messaging_context("head_of_it", "it", FakeRegistry())
        _message_count_var.set(_MAX_MESSAGES_PER_RUN)

        result = await send_message_to_role(
            {
                "to_role_id": "sysadmin",
                "subject": "Blocked",
                "content": "Should be blocked",
            }
        )
        assert _is_error(result)
        clear_messaging_context()

    @pytest.mark.asyncio
    async def test_reply_blocked_at_max_count(self):
        set_messaging_context("sysadmin", "it", FakeRegistry())
        _message_count_var.set(_MAX_MESSAGES_PER_RUN)

        result = await reply_to_message(
            {
                "message_id": 1,
                "content": "Blocked",
            }
        )
        assert _is_error(result)
        clear_messaging_context()
