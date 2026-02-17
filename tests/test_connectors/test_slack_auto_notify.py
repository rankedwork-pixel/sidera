"""Tests for SlackConnector.send_auto_execute_notification().

Covers:
- Block Kit message structure (header, fields, context)
- Default channel fallback when channel_id is None
- Optional rule_description field inclusion
- Optional result section inclusion
- Error handling (raises SlackConnectorError on API failure)

All Slack Web API calls are mocked; no network traffic is needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from slack_sdk.errors import SlackApiError

from src.connectors.slack import SlackConnector, SlackConnectorError

# ---------------------------------------------------------------------------
# Fake credentials
# ---------------------------------------------------------------------------

_FAKE_CREDENTIALS = {
    "bot_token": "xoxb-test-token-123",
    "channel_id": "C0123456789",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_client():
    """Return a MagicMock standing in for slack_sdk.WebClient."""
    return MagicMock()


@pytest.fixture()
def connector(mock_client):
    """Build a SlackConnector with a mocked WebClient."""
    with patch("src.connectors.slack.WebClient", return_value=mock_client):
        conn = SlackConnector(credentials=_FAKE_CREDENTIALS)
    conn._mock_client = mock_client
    return conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_success_response(**extra):
    """Build a mock Slack success response."""
    response = MagicMock()
    base = {"ok": True, "channel": "C0123456789", "ts": "1234567890.123456"}
    base.update(extra)
    response.get = lambda key, default="": base.get(key, default)
    response.__getitem__ = MagicMock(side_effect=lambda key: base[key])
    return response


def _make_slack_api_error(error: str = "some_error") -> SlackApiError:
    """Build a SlackApiError with the given error string."""
    response = MagicMock()
    response.get.side_effect = lambda key, default="": error if key == "error" else default
    response.__getitem__ = MagicMock(
        side_effect=lambda key: error if key == "error" else "",
    )
    response.status_code = 200
    response.data = {"ok": False, "error": error}
    return SlackApiError(
        message=f"The request to Slack failed: {error}",
        response=response,
    )


# ===========================================================================
# Tests
# ===========================================================================


class TestSendAutoExecuteNotification:
    """SlackConnector.send_auto_execute_notification()."""

    def test_sends_block_kit_with_correct_header_and_fields(self, connector):
        """Message should contain a header block and section fields."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        result = connector.send_auto_execute_notification(
            channel_id="C0123456789",
            action_type="pause_campaign",
            description="Paused campaign X",
            reasoning="ROAS below threshold",
            rule_id="pause_low_roas_ads",
        )

        assert result["ok"] is True
        assert result["ts"] == "1234567890.123456"

        call_kwargs = connector._mock_client.chat_postMessage.call_args.kwargs
        blocks = call_kwargs["blocks"]

        # Header block
        header = blocks[0]
        assert header["type"] == "header"
        assert "Auto-Executed" in header["text"]["text"]
        assert "pause_campaign" in header["text"]["text"]

        # Section blocks contain Action, Reasoning, Rule fields
        all_field_texts = []
        for block in blocks:
            if block["type"] == "section" and "fields" in block:
                for field in block["fields"]:
                    all_field_texts.append(field["text"])

        assert any("*Action:*" in t for t in all_field_texts)
        assert any("*Reasoning:*" in t for t in all_field_texts)
        assert any("*Rule:*" in t for t in all_field_texts)

        # Context block present
        context_blocks = [b for b in blocks if b["type"] == "context"]
        assert len(context_blocks) == 1
        assert "automatically executed" in (context_blocks[0]["elements"][0]["text"])

    def test_falls_back_to_default_channel(self, connector):
        """When channel_id is None, the default channel should be used."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        connector.send_auto_execute_notification(
            channel_id=None,
            action_type="pause_campaign",
            description="Paused campaign X",
            reasoning="ROAS below threshold",
            rule_id="pause_low_roas_ads",
        )

        call_kwargs = connector._mock_client.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "C0123456789"

    def test_includes_rule_description_when_provided(self, connector):
        """When rule_description is given, a 'Rule Detail' field appears."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        connector.send_auto_execute_notification(
            channel_id="C0123456789",
            action_type="pause_campaign",
            description="Paused campaign X",
            reasoning="ROAS below threshold",
            rule_id="pause_low_roas_ads",
            rule_description="Auto-pause ads with ROAS < 0.5x",
        )

        call_kwargs = connector._mock_client.chat_postMessage.call_args.kwargs
        blocks = call_kwargs["blocks"]

        all_field_texts = []
        for block in blocks:
            if block["type"] == "section" and "fields" in block:
                for field in block["fields"]:
                    all_field_texts.append(field["text"])

        assert any("*Rule Detail:*" in t for t in all_field_texts)
        assert any("Auto-pause ads with ROAS < 0.5x" in t for t in all_field_texts)

    def test_includes_result_section_when_provided(self, connector):
        """When result dict is given, a Result section should appear."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        connector.send_auto_execute_notification(
            channel_id="C0123456789",
            action_type="pause_campaign",
            description="Paused campaign X",
            reasoning="ROAS below threshold",
            rule_id="pause_low_roas_ads",
            result={"status": "success", "campaign_id": "12345"},
        )

        call_kwargs = connector._mock_client.chat_postMessage.call_args.kwargs
        blocks = call_kwargs["blocks"]

        result_sections = [
            b
            for b in blocks
            if b["type"] == "section" and "text" in b and "*Result:*" in b["text"].get("text", "")
        ]
        assert len(result_sections) == 1
        assert "success" in result_sections[0]["text"]["text"]

        # Result block should appear before the context block
        result_idx = blocks.index(result_sections[0])
        context_idx = next(i for i, b in enumerate(blocks) if b["type"] == "context")
        assert result_idx < context_idx

    def test_raises_connector_error_on_api_failure(self, connector):
        """Should raise SlackConnectorError on a non-auth SlackApiError."""
        connector._mock_client.chat_postMessage.side_effect = _make_slack_api_error(
            "channel_not_found"
        )

        with pytest.raises(SlackConnectorError, match="channel_not_found"):
            connector.send_auto_execute_notification(
                channel_id="C0123456789",
                action_type="pause_campaign",
                description="Paused campaign X",
                reasoning="ROAS below threshold",
                rule_id="pause_low_roas_ads",
            )
