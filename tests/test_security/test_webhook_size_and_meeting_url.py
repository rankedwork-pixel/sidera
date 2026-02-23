"""Tests for webhook payload size limits and meeting URL sanitization.

Covers:
- Oversized webhook payloads rejected with 413
- Meeting URL query parameters stripped
- Meeting URL fragments stripped
- Meeting URL path preserved
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.routes.slack import _detect_meeting_url, _sanitize_meeting_url
from src.api.routes.webhooks import (
    _MAX_WEBHOOK_PAYLOAD_BYTES,
    _handle_recall_transcript,
)

# ---------------------------------------------------------------------------
# Webhook payload size limits
# ---------------------------------------------------------------------------


class TestWebhookPayloadSizeLimit:
    """Tests for payload size enforcement on webhook endpoints."""

    @pytest.mark.asyncio
    async def test_rejects_oversized_payload(self) -> None:
        """Payload exceeding _MAX_WEBHOOK_PAYLOAD_BYTES → 413."""
        oversized = b"x" * (_MAX_WEBHOOK_PAYLOAD_BYTES + 1)
        req = MagicMock()
        req.body = AsyncMock(return_value=oversized)
        req.json = AsyncMock(return_value={})
        req.headers = {}

        resp = await _handle_recall_transcript(req, bot_id="bot123")
        assert resp.status_code == 413

    @pytest.mark.asyncio
    async def test_accepts_normal_payload(self) -> None:
        """Payload within limit should proceed (may fail on other checks)."""
        import json

        payload = {
            "event": "transcript.data",
            "data": {"data": {"words": [{"text": "hi"}]}},
        }
        raw = json.dumps(payload).encode()
        assert len(raw) < _MAX_WEBHOOK_PAYLOAD_BYTES

        req = MagicMock()
        req.body = AsyncMock(return_value=raw)
        req.json = AsyncMock(return_value=payload)
        req.headers = {}

        mock_manager = MagicMock()
        mock_manager.get_active_session.return_value = MagicMock()
        mock_manager.receive_transcript_event = MagicMock()
        mock_manager.get_all_active_sessions.return_value = {}

        from unittest.mock import patch

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
        # Should not be 413
        assert resp.status_code != 413

    def test_max_payload_constant_is_100kb(self) -> None:
        """Verify the constant is set to 100 KB."""
        assert _MAX_WEBHOOK_PAYLOAD_BYTES == 102_400


# ---------------------------------------------------------------------------
# Meeting URL sanitization
# ---------------------------------------------------------------------------


class TestMeetingUrlSanitization:
    """Tests for _sanitize_meeting_url and _detect_meeting_url."""

    def test_strips_query_params(self) -> None:
        url = "https://zoom.us/j/12345?pwd=SECRET_TOKEN"
        result = _sanitize_meeting_url(url)
        assert result == "https://zoom.us/j/12345"
        assert "SECRET" not in result

    def test_strips_fragment(self) -> None:
        url = "https://meet.google.com/abc-defg-hij#extra-data"
        result = _sanitize_meeting_url(url)
        assert result == "https://meet.google.com/abc-defg-hij"
        assert "#" not in result

    def test_preserves_path(self) -> None:
        url = "https://teams.microsoft.com/l/meetup-join/19%3ameeting"
        result = _sanitize_meeting_url(url)
        assert "meetup-join" in result
        assert result.startswith("https://teams.microsoft.com/")

    def test_detect_meeting_url_returns_sanitized(self) -> None:
        """_detect_meeting_url should return a sanitized URL."""
        text = "Join here: https://zoom.us/j/999?pwd=HIDDEN rest"
        result = _detect_meeting_url(text)
        assert result is not None
        assert "pwd=" not in result
        assert result == "https://zoom.us/j/999"

    def test_detect_meeting_url_returns_none_for_no_match(self) -> None:
        assert _detect_meeting_url("no meeting links here") is None

    def test_google_meet_preserved(self) -> None:
        url = "https://meet.google.com/abc-defg-hij"
        result = _sanitize_meeting_url(url)
        assert result == url
