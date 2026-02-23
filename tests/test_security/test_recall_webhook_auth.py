"""Tests for Recall.ai webhook authentication and bot_id validation.

Covers:
- Shared secret authentication (X-Webhook-Secret header)
- Bot ID validation against active meeting sessions
- Passthrough when secret not configured
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.webhooks import _handle_recall_transcript


def _fake_request(
    body: dict | None = None,
    headers: dict | None = None,
) -> MagicMock:
    """Build a minimal mock Request."""
    req = MagicMock()
    payload = body or {
        "event": "transcript.data",
        "data": {
            "data": {
                "words": [{"text": "hello"}],
                "participant": {"name": "Alice"},
            },
            "is_final": True,
        },
    }
    import json

    raw = json.dumps(payload).encode()
    req.body = AsyncMock(return_value=raw)
    req.json = AsyncMock(return_value=payload)
    req.headers = headers or {}
    return req


# ---------------------------------------------------------------------------
# Shared secret authentication
# ---------------------------------------------------------------------------


class TestRecallWebhookAuth:
    """Tests for X-Webhook-Secret based authentication."""

    @pytest.mark.asyncio
    async def test_rejects_missing_secret(self) -> None:
        """When secret is configured but header is missing → 401."""
        req = _fake_request(headers={})
        with (
            patch(
                "src.api.routes.webhooks.settings",
                new=MagicMock(recall_ai_webhook_secret="my-secret"),
                create=True,
            ),
            patch(
                "src.config.settings",
                new=MagicMock(
                    recall_ai_webhook_secret="my-secret",
                ),
            ),
        ):
            resp = await _handle_recall_transcript(req, bot_id="bot123")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_rejects_wrong_secret(self) -> None:
        """When header secret doesn't match configured secret → 401."""
        req = _fake_request(headers={"X-Webhook-Secret": "wrong-secret"})
        with patch(
            "src.config.settings",
            new=MagicMock(
                recall_ai_webhook_secret="correct-secret",
            ),
        ):
            resp = await _handle_recall_transcript(req, bot_id="bot123")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_accepts_correct_secret(self) -> None:
        """When header matches configured secret → proceeds."""
        req = _fake_request(headers={"X-Webhook-Secret": "my-secret"})
        mock_manager = MagicMock()
        mock_manager.get_active_session.return_value = MagicMock()
        mock_manager.receive_transcript_event = MagicMock()

        with (
            patch(
                "src.config.settings",
                new=MagicMock(
                    recall_ai_webhook_secret="my-secret",
                ),
            ),
            patch(
                "src.meetings.session.get_meeting_manager",
                return_value=mock_manager,
            ),
        ):
            resp = await _handle_recall_transcript(req, bot_id="bot123")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_passes_when_secret_not_configured(self) -> None:
        """When recall_ai_webhook_secret is empty → no auth check."""
        req = _fake_request(headers={})
        mock_manager = MagicMock()
        mock_manager.get_active_session.return_value = MagicMock()
        mock_manager.receive_transcript_event = MagicMock()

        with (
            patch(
                "src.config.settings",
                new=MagicMock(
                    recall_ai_webhook_secret="",
                ),
            ),
            patch(
                "src.meetings.session.get_meeting_manager",
                return_value=mock_manager,
            ),
        ):
            resp = await _handle_recall_transcript(req, bot_id="bot123")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Bot ID validation
# ---------------------------------------------------------------------------


class TestRecallBotIdValidation:
    """Tests for bot_id validation against active sessions."""

    @pytest.mark.asyncio
    async def test_rejects_unknown_bot_id(self) -> None:
        """When bot_id doesn't match any active session → 404."""
        req = _fake_request(headers={})
        mock_manager = MagicMock()
        mock_manager.get_active_session.return_value = None

        with (
            patch(
                "src.config.settings",
                new=MagicMock(
                    recall_ai_webhook_secret="",
                ),
            ),
            patch(
                "src.meetings.session.get_meeting_manager",
                return_value=mock_manager,
            ),
        ):
            resp = await _handle_recall_transcript(req, bot_id="unknown_bot")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_accepts_known_bot_id(self) -> None:
        """When bot_id matches an active session → 200."""
        req = _fake_request(headers={})
        mock_manager = MagicMock()
        mock_manager.get_active_session.return_value = MagicMock()
        mock_manager.receive_transcript_event = MagicMock()

        with (
            patch(
                "src.config.settings",
                new=MagicMock(
                    recall_ai_webhook_secret="",
                ),
            ),
            patch(
                "src.meetings.session.get_meeting_manager",
                return_value=mock_manager,
            ),
        ):
            resp = await _handle_recall_transcript(req, bot_id="known_bot")
        assert resp.status_code == 200
