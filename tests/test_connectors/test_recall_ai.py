"""Tests for the Recall.ai meeting bot connector."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.connectors.recall_ai import (
    RecallAIAuthError,
    RecallAIConnector,
    RecallAIConnectorError,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def connector():
    """Create a RecallAIConnector with test credentials."""
    return RecallAIConnector(credentials={"api_key": "test-api-key"})


def _mock_response(
    status_code: int = 200,
    json_data: dict | list | None = None,
    text: str = "",
) -> httpx.Response:
    """Build a fake httpx.Response."""
    import json as _json

    if json_data is not None:
        content = _json.dumps(json_data).encode()
        headers = {"content-type": "application/json"}
    elif text:
        content = text.encode()
        headers = {"content-type": "text/plain"}
    else:
        content = b""
        headers = {}

    return httpx.Response(
        status_code=status_code,
        content=content,
        headers=headers,
        request=httpx.Request("GET", "https://api.recall.ai/api/v1/test"),
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    """Tests for connector initialization."""

    def test_explicit_credentials(self):
        conn = RecallAIConnector(credentials={"api_key": "my-key"})
        assert conn._api_key == "my-key"

    def test_missing_api_key_logs_warning(self):
        conn = RecallAIConnector(credentials={"api_key": ""})
        assert conn._api_key == ""

    def test_credentials_from_settings(self):
        with patch("src.connectors.recall_ai.settings") as mock_settings:
            mock_settings.recall_ai_api_key = "settings-key"
            conn = RecallAIConnector()
            assert conn._api_key == "settings-key"


# ---------------------------------------------------------------------------
# create_bot
# ---------------------------------------------------------------------------


class TestCreateBot:
    """Tests for the create_bot method."""

    def test_create_bot_success(self, connector):
        response_data = {"id": "bot-uuid-123", "status": "joining"}
        mock_resp = _mock_response(200, json_data=response_data)

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_resp
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = connector.create_bot("https://meet.google.com/abc-defg-hij")

        assert result["id"] == "bot-uuid-123"
        assert result["status"] == "joining"

    def test_create_bot_with_name(self, connector):
        response_data = {"id": "bot-uuid-456", "status": "joining"}
        mock_resp = _mock_response(200, json_data=response_data)

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_resp
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = connector.create_bot(
                "https://meet.google.com/abc",
                bot_name="Marketing Head",
            )

        assert result["id"] == "bot-uuid-456"
        # Verify the bot_name was sent in the payload
        call_kwargs = mock_client.post.call_args
        payload = call_kwargs.kwargs.get("json", call_kwargs[1].get("json", {}))
        assert payload["bot_name"] == "Marketing Head"

    def test_create_bot_auth_error(self, connector):
        mock_resp = _mock_response(401, text="Invalid API key")

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_resp
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(RecallAIAuthError):
                connector.create_bot("https://meet.google.com/abc")

    def test_create_bot_server_error(self, connector):
        mock_resp = _mock_response(500, text="Internal server error")

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_resp
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(RecallAIConnectorError):
                connector.create_bot("https://meet.google.com/abc")


# ---------------------------------------------------------------------------
# get_bot_status
# ---------------------------------------------------------------------------


class TestGetBotStatus:
    """Tests for the get_bot_status method."""

    def test_get_status_success(self, connector):
        response_data = {"id": "bot-123", "status": "in_call", "meeting_participants": []}
        mock_resp = _mock_response(200, json_data=response_data)

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = connector.get_bot_status("bot-123")

        assert result["status"] == "in_call"

    def test_get_status_auth_error(self, connector):
        mock_resp = _mock_response(403, text="Forbidden")

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(RecallAIAuthError):
                connector.get_bot_status("bot-123")


# ---------------------------------------------------------------------------
# remove_bot
# ---------------------------------------------------------------------------


class TestRemoveBot:
    """Tests for the remove_bot method."""

    def test_remove_bot_success(self, connector):
        mock_resp = _mock_response(204)

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_resp
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = connector.remove_bot("bot-123")

        assert result == {}


# ---------------------------------------------------------------------------
# get_transcript
# ---------------------------------------------------------------------------


class TestGetTranscript:
    """Tests for the get_transcript method."""

    def test_get_transcript_list_response(self, connector):
        transcript_data = [
            {"speaker": "Alice", "words": [{"word": "hello"}], "timestamp": 1.0},
            {"speaker": "Bob", "words": [{"word": "hi"}], "timestamp": 2.0},
        ]
        mock_resp = _mock_response(200, json_data=transcript_data)

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = connector.get_transcript("bot-123")

        assert len(result) == 2
        assert result[0]["speaker"] == "Alice"

    def test_get_transcript_wrapped_response(self, connector):
        wrapped_data = {"results": [{"speaker": "Alice", "words": [], "timestamp": 1.0}]}
        mock_resp = _mock_response(200, json_data=wrapped_data)

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = connector.get_transcript("bot-123")

        assert len(result) == 1


# ---------------------------------------------------------------------------
# get_participants
# ---------------------------------------------------------------------------


class TestGetParticipants:
    """Tests for the get_participants method."""

    def test_get_participants_success(self, connector):
        status_data = {
            "id": "bot-123",
            "status": "in_call",
            "meeting_participants": [
                {"name": "Alice"},
                {"name": "Bob"},
            ],
        }
        mock_resp = _mock_response(200, json_data=status_data)

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = connector.get_participants("bot-123")

        assert len(result) == 2
        assert result[0]["name"] == "Alice"


# ---------------------------------------------------------------------------
# send_audio
# ---------------------------------------------------------------------------


class TestSendAudio:
    """Tests for the send_audio method."""

    def test_send_audio_success(self, connector):
        mock_resp = _mock_response(200, json_data={"status": "ok"})

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_resp
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = connector.send_audio("bot-123", b"\x00\x01\x02\x03", sample_rate=16000)

        assert result.get("status") == "ok"

    def test_send_audio_auth_error(self, connector):
        mock_resp = _mock_response(401, text="Unauthorized")

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_resp
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(RecallAIAuthError):
                connector.send_audio("bot-123", b"\x00\x01")


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Tests for response error classification."""

    def test_handle_response_success(self, connector):
        resp = _mock_response(200, json_data={"ok": True})
        result = connector._handle_response(resp, "test")
        assert result == {"ok": True}

    def test_handle_response_204(self, connector):
        resp = _mock_response(204)
        result = connector._handle_response(resp, "test")
        assert result == {}

    def test_handle_response_401(self, connector):
        resp = _mock_response(401, text="Unauthorized")
        with pytest.raises(RecallAIAuthError):
            connector._handle_response(resp, "test")

    def test_handle_response_403(self, connector):
        resp = _mock_response(403, text="Forbidden")
        with pytest.raises(RecallAIAuthError):
            connector._handle_response(resp, "test")

    def test_handle_response_500(self, connector):
        resp = _mock_response(500, text="Server error")
        with pytest.raises(RecallAIConnectorError):
            connector._handle_response(resp, "test")

    def test_headers_include_api_key(self, connector):
        headers = connector._headers()
        assert headers["Authorization"] == "Token test-api-key"
        assert headers["Content-Type"] == "application/json"
