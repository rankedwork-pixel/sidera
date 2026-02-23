"""Tests for src.connectors.slack -- SlackConnector.

Covers construction, every public method, and error handling.
All Slack Web API calls are mocked; no network traffic is needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from slack_sdk.errors import SlackApiError

from src.connectors.slack import (
    SlackAuthError,
    SlackConnector,
    SlackConnectorError,
)

# Fake credentials used for explicit-credential tests.
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


def _make_slack_api_error(error: str = "some_error") -> SlackApiError:
    """Build a SlackApiError with the given error string."""
    response = MagicMock()
    response.get.side_effect = lambda key, default="": error if key == "error" else default
    response.__getitem__ = MagicMock(side_effect=lambda key: error if key == "error" else "")
    response.status_code = 200
    response.data = {"ok": False, "error": error}
    exc = SlackApiError(message=f"The request to Slack failed: {error}", response=response)
    return exc


def _make_success_response(**extra):
    """Build a mock Slack success response."""
    response = MagicMock()
    base = {"ok": True, "channel": "C0123456789", "ts": "1234567890.123456"}
    base.update(extra)
    response.get = lambda key, default="": base.get(key, default)
    response.__getitem__ = MagicMock(side_effect=lambda key: base[key])
    return response


# ===========================================================================
# 1. Construction
# ===========================================================================


class TestConstruction:
    """SlackConnector.__init__."""

    def test_explicit_credentials(self, mock_client):
        """Connector should accept explicit credentials."""
        with patch("src.connectors.slack.WebClient", return_value=mock_client):
            conn = SlackConnector(credentials=_FAKE_CREDENTIALS)

        assert conn._bot_token == "xoxb-test-token-123"
        assert conn._default_channel_id == "C0123456789"

    def test_default_credentials_from_settings(self, mock_client):
        """Connector should fall back to settings when no credentials given."""
        with (
            patch("src.connectors.slack.WebClient", return_value=mock_client),
            patch("src.connectors.slack.settings") as mock_settings,
        ):
            mock_settings.slack_bot_token = "xoxb-settings-token"
            mock_settings.slack_channel_id = "C9999999999"
            conn = SlackConnector()

        assert conn._bot_token == "xoxb-settings-token"
        assert conn._default_channel_id == "C9999999999"

    def test_empty_credentials_fallback(self, mock_client):
        """Connector should handle empty credentials dict gracefully."""
        with patch("src.connectors.slack.WebClient", return_value=mock_client):
            conn = SlackConnector(credentials={})

        assert conn._bot_token == ""
        assert conn._default_channel_id == ""


# ===========================================================================
# 2. send_briefing
# ===========================================================================


class TestSendBriefing:
    """SlackConnector.send_briefing."""

    def test_successful_briefing(self, connector):
        """send_briefing should post a Block Kit message and return ok/channel/ts."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        result = connector.send_briefing(
            channel_id=None,
            briefing_text="Today's performance was great!",
            recommendations=[
                {"title": "Increase budget", "description": "Campaign X is performing well."},
            ],
        )

        assert result["ok"] is True
        assert result["channel"] == "C0123456789"
        assert result["ts"] == "1234567890.123456"
        connector._mock_client.chat_postMessage.assert_called_once()

    def test_briefing_with_explicit_channel(self, connector):
        """send_briefing should use the explicit channel_id when provided."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response(
            channel="C_EXPLICIT"
        )

        connector.send_briefing(
            channel_id="C_EXPLICIT",
            briefing_text="Briefing text",
            recommendations=[],
        )

        call_kwargs = connector._mock_client.chat_postMessage.call_args
        assert call_kwargs.kwargs.get("channel") or call_kwargs[1].get("channel") == "C_EXPLICIT"

    def test_briefing_empty_recommendations(self, connector):
        """send_briefing should work with an empty recommendations list."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        result = connector.send_briefing(
            channel_id=None,
            briefing_text="No recommendations today.",
            recommendations=[],
        )

        assert result["ok"] is True
        # Verify blocks don't include a "Recommendations" section
        call_kwargs = connector._mock_client.chat_postMessage.call_args
        blocks = call_kwargs.kwargs.get("blocks") or call_kwargs[1].get("blocks", [])
        # Should only have header + section (no divider for recommendations)
        block_types = [b["type"] for b in blocks]
        assert "divider" not in block_types

    def test_briefing_multiple_recommendations(self, connector):
        """send_briefing should include all recommendations in blocks."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        recs = [
            {"title": "Rec 1", "description": "Desc 1"},
            {"title": "Rec 2", "description": "Desc 2"},
            {"title": "Rec 3", "description": "Desc 3"},
        ]
        connector.send_briefing(None, "Briefing", recs)

        call_kwargs = connector._mock_client.chat_postMessage.call_args
        blocks = call_kwargs.kwargs.get("blocks") or call_kwargs[1].get("blocks", [])
        # Count sections that contain recommendation text
        rec_sections = [
            b
            for b in blocks
            if b.get("type") == "section" and "Rec " in b.get("text", {}).get("text", "")
        ]
        assert len(rec_sections) == 3

    def test_briefing_api_error(self, connector):
        """send_briefing should raise SlackConnectorError on API failure."""
        connector._mock_client.chat_postMessage.side_effect = _make_slack_api_error(
            "channel_not_found"
        )

        with pytest.raises(SlackConnectorError, match="channel_not_found"):
            connector.send_briefing(None, "Briefing", [])


# ===========================================================================
# 3. send_approval_request
# ===========================================================================


class TestSendApprovalRequest:
    """SlackConnector.send_approval_request."""

    def test_successful_approval_request(self, connector):
        """send_approval_request should post a message with Approve/Reject buttons."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        result = connector.send_approval_request(
            channel_id=None,
            approval_id="apr_001",
            action_type="budget_change",
            description="Increase Campaign X budget by 20%",
            reasoning="Campaign X has 3x ROAS",
            projected_impact="+$500/week revenue",
            risk_level="low",
        )

        assert result["ok"] is True
        assert result["ts"] == "1234567890.123456"

    def test_approval_blocks_have_buttons(self, connector):
        """The approval message must have Approve and Reject buttons."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        connector.send_approval_request(
            channel_id=None,
            approval_id="apr_002",
            action_type="pause_campaign",
            description="Pause underperforming campaign",
            reasoning="CPA is 5x target",
            projected_impact="Save $200/day",
            risk_level="medium",
        )

        call_kwargs = connector._mock_client.chat_postMessage.call_args
        blocks = call_kwargs.kwargs.get("blocks") or call_kwargs[1].get("blocks", [])

        # Find the actions block
        actions_blocks = [b for b in blocks if b.get("type") == "actions"]
        assert len(actions_blocks) == 1

        elements = actions_blocks[0]["elements"]
        assert len(elements) == 2

        # Verify action IDs
        action_ids = {e["action_id"] for e in elements}
        assert action_ids == {"sidera_approve", "sidera_reject"}

        # Verify values carry the approval_id
        values = {e["value"] for e in elements}
        assert values == {"apr_002"}

    def test_approval_button_styles(self, connector):
        """Approve should be 'primary' and Reject should be 'danger'."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        connector.send_approval_request(
            channel_id=None,
            approval_id="apr_003",
            action_type="test",
            description="Test",
            reasoning="Test",
            projected_impact="Test",
            risk_level="high",
        )

        call_kwargs = connector._mock_client.chat_postMessage.call_args
        blocks = call_kwargs.kwargs.get("blocks") or call_kwargs[1].get("blocks", [])
        actions_block = [b for b in blocks if b.get("type") == "actions"][0]
        elements = actions_block["elements"]

        approve_btn = next(e for e in elements if e["action_id"] == "sidera_approve")
        reject_btn = next(e for e in elements if e["action_id"] == "sidera_reject")

        assert approve_btn["style"] == "primary"
        assert reject_btn["style"] == "danger"

    def test_approval_request_api_error(self, connector):
        """send_approval_request should raise on API failure."""
        connector._mock_client.chat_postMessage.side_effect = _make_slack_api_error(
            "channel_not_found"
        )

        with pytest.raises(SlackConnectorError):
            connector.send_approval_request(
                channel_id=None,
                approval_id="apr_err",
                action_type="test",
                description="Test",
                reasoning="Test",
                projected_impact="Test",
                risk_level="low",
            )

    def test_approval_with_explicit_channel(self, connector):
        """send_approval_request should use an explicit channel_id."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        connector.send_approval_request(
            channel_id="C_CUSTOM",
            approval_id="apr_004",
            action_type="test",
            description="Test",
            reasoning="Test",
            projected_impact="Test",
            risk_level="low",
        )

        call_kwargs = connector._mock_client.chat_postMessage.call_args
        assert (call_kwargs.kwargs.get("channel") or call_kwargs[1].get("channel")) == "C_CUSTOM"

    def test_approval_with_steward_mention(self, connector):
        """send_approval_request with steward_mention adds a context block."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        connector.send_approval_request(
            channel_id=None,
            approval_id="apr_steward",
            action_type="budget_change",
            description="Increase budget",
            reasoning="Strong performance",
            projected_impact="+$1000",
            risk_level="low",
            steward_mention="<@U_STEWARD>",
        )

        call_kwargs = connector._mock_client.chat_postMessage.call_args
        blocks = call_kwargs.kwargs.get("blocks") or call_kwargs[1].get("blocks", [])

        # Find context blocks mentioning steward
        context_blocks = [
            b
            for b in blocks
            if b.get("type") == "context"
            and any("Steward" in str(e) for e in b.get("elements", []))
        ]
        assert len(context_blocks) == 1
        text = context_blocks[0]["elements"][0]["text"]
        assert "<@U_STEWARD>" in text

    def test_approval_without_steward_mention(self, connector):
        """send_approval_request without steward_mention has no steward block."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        connector.send_approval_request(
            channel_id=None,
            approval_id="apr_no_steward",
            action_type="budget_change",
            description="Increase budget",
            reasoning="Strong performance",
            projected_impact="+$1000",
            risk_level="low",
        )

        call_kwargs = connector._mock_client.chat_postMessage.call_args
        blocks = call_kwargs.kwargs.get("blocks") or call_kwargs[1].get("blocks", [])

        # No context blocks mentioning steward
        context_blocks = [
            b
            for b in blocks
            if b.get("type") == "context"
            and any("Steward" in str(e) for e in b.get("elements", []))
        ]
        assert len(context_blocks) == 0


# ===========================================================================
# 4. update_approval_message
# ===========================================================================


class TestUpdateApprovalMessage:
    """SlackConnector.update_approval_message."""

    def test_update_approved(self, connector):
        """update_approval_message should call chat_update with approved status."""
        connector._mock_client.chat_update.return_value = _make_success_response()

        result = connector.update_approval_message(
            channel_id="C0123456789",
            message_ts="1234567890.123456",
            approval_id="apr_001",
            status="approved",
            decided_by="U_ADMIN",
        )

        assert result["ok"] is True
        connector._mock_client.chat_update.assert_called_once()

        call_kwargs = connector._mock_client.chat_update.call_args
        blocks = call_kwargs.kwargs.get("blocks") or call_kwargs[1].get("blocks", [])
        # Should contain the APPROVED text
        block_text = blocks[0]["text"]["text"]
        assert "APPROVED" in block_text
        assert "U_ADMIN" in block_text

    def test_update_rejected(self, connector):
        """update_approval_message should handle rejected status."""
        connector._mock_client.chat_update.return_value = _make_success_response()

        result = connector.update_approval_message(
            channel_id="C0123456789",
            message_ts="1234567890.123456",
            approval_id="apr_002",
            status="rejected",
            decided_by="U_REVIEWER",
        )

        assert result["ok"] is True
        call_kwargs = connector._mock_client.chat_update.call_args
        blocks = call_kwargs.kwargs.get("blocks") or call_kwargs[1].get("blocks", [])
        block_text = blocks[0]["text"]["text"]
        assert "REJECTED" in block_text

    def test_update_approval_api_error(self, connector):
        """update_approval_message should raise on API failure."""
        connector._mock_client.chat_update.side_effect = _make_slack_api_error("message_not_found")

        with pytest.raises(SlackConnectorError, match="message_not_found"):
            connector.update_approval_message(
                channel_id="C0123456789",
                message_ts="bad_ts",
                approval_id="apr_err",
                status="approved",
                decided_by="U_ADMIN",
            )


# ===========================================================================
# 5. send_alert
# ===========================================================================


class TestSendAlert:
    """SlackConnector.send_alert."""

    def test_successful_alert(self, connector):
        """send_alert should post a Block Kit alert message."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        result = connector.send_alert(
            channel_id=None,
            alert_type="cost_overrun",
            message="Daily spend exceeded $500 limit.",
        )

        assert result["ok"] is True
        assert result["ts"] == "1234567890.123456"
        connector._mock_client.chat_postMessage.assert_called_once()

    def test_alert_with_details(self, connector):
        """send_alert should include a details block when details are provided."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        connector.send_alert(
            channel_id=None,
            alert_type="anomaly",
            message="CTR dropped 40%",
            details={"campaign": "Brand Search", "previous_ctr": 0.05, "current_ctr": 0.03},
        )

        call_kwargs = connector._mock_client.chat_postMessage.call_args
        blocks = call_kwargs.kwargs.get("blocks") or call_kwargs[1].get("blocks", [])
        # Should have 3 blocks: header, message section, details section
        assert len(blocks) == 3
        # Last block should contain the JSON details
        assert "Brand Search" in blocks[2]["text"]["text"]

    def test_alert_without_details(self, connector):
        """send_alert with no details should not include a details block."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        connector.send_alert(
            channel_id=None,
            alert_type="info",
            message="System started.",
        )

        call_kwargs = connector._mock_client.chat_postMessage.call_args
        blocks = call_kwargs.kwargs.get("blocks") or call_kwargs[1].get("blocks", [])
        # Should have 2 blocks: header + message section
        assert len(blocks) == 2

    def test_alert_api_error(self, connector):
        """send_alert should raise on API failure."""
        connector._mock_client.chat_postMessage.side_effect = _make_slack_api_error(
            "channel_not_found"
        )

        with pytest.raises(SlackConnectorError, match="channel_not_found"):
            connector.send_alert(
                channel_id=None,
                alert_type="error",
                message="Something went wrong.",
            )

    def test_alert_with_explicit_channel(self, connector):
        """send_alert should use the explicit channel_id."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        connector.send_alert(
            channel_id="C_ALERTS",
            alert_type="info",
            message="Test alert",
        )

        call_kwargs = connector._mock_client.chat_postMessage.call_args
        assert (call_kwargs.kwargs.get("channel") or call_kwargs[1].get("channel")) == "C_ALERTS"


# ===========================================================================
# 6. test_connection
# ===========================================================================


class TestTestConnection:
    """SlackConnector.test_connection."""

    def test_successful_connection(self, connector):
        """test_connection should return team/user/bot_id from auth.test()."""
        auth_response = MagicMock()
        auth_response.get = lambda key, default="": {
            "ok": True,
            "team": "Sidera Team",
            "user": "sidera-bot",
            "bot_id": "B123",
        }.get(key, default)
        connector._mock_client.auth_test.return_value = auth_response

        result = connector.test_connection()

        assert result["ok"] is True
        assert result["team"] == "Sidera Team"
        assert result["user"] == "sidera-bot"
        assert result["bot_id"] == "B123"

    def test_connection_auth_error(self, connector):
        """test_connection should raise SlackAuthError on invalid_auth."""
        connector._mock_client.auth_test.side_effect = _make_slack_api_error("invalid_auth")

        with pytest.raises(SlackAuthError, match="invalid_auth"):
            connector.test_connection()


# ===========================================================================
# 7. Auth error detection
# ===========================================================================


class TestAuthErrorDetection:
    """Verify that auth-related errors raise SlackAuthError, not generic."""

    @pytest.mark.parametrize(
        "error_type",
        [
            "invalid_auth",
            "not_authed",
            "account_inactive",
            "token_revoked",
            "token_expired",
            "no_permission",
            "missing_scope",
            "not_allowed_token_type",
        ],
    )
    def test_auth_errors_raise_slack_auth_error(self, connector, error_type):
        """Auth-related errors should raise SlackAuthError."""
        connector._mock_client.chat_postMessage.side_effect = _make_slack_api_error(error_type)

        with pytest.raises(SlackAuthError, match=error_type):
            connector.send_alert(None, "test", "test")

    def test_non_auth_error_raises_connector_error(self, connector):
        """Non-auth errors should raise SlackConnectorError."""
        connector._mock_client.chat_postMessage.side_effect = _make_slack_api_error(
            "channel_not_found"
        )

        with pytest.raises(SlackConnectorError):
            connector.send_alert(None, "test", "test")

        # Should NOT be a SlackAuthError
        with pytest.raises(SlackConnectorError) as exc_info:
            connector.send_alert(None, "test", "test")
        assert not isinstance(exc_info.value, SlackAuthError)

    def test_rate_limited_raises_connector_error(self, connector):
        """Rate limit errors should raise SlackConnectorError, not auth."""
        connector._mock_client.chat_postMessage.side_effect = _make_slack_api_error("ratelimited")

        with pytest.raises(SlackConnectorError):
            connector.send_alert(None, "test", "test")

        with pytest.raises(SlackConnectorError) as exc_info:
            connector.send_alert(None, "test", "test")
        assert not isinstance(exc_info.value, SlackAuthError)


# ===========================================================================
# 8. Default channel fallback
# ===========================================================================


class TestDefaultChannelFallback:
    """Verify that methods fall back to self._default_channel_id when None."""

    def test_send_briefing_default_channel(self, connector):
        """send_briefing(channel_id=None) should use the default channel."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        connector.send_briefing(None, "Test", [])

        call_kwargs = connector._mock_client.chat_postMessage.call_args
        assert (call_kwargs.kwargs.get("channel") or call_kwargs[1].get("channel")) == "C0123456789"

    def test_send_approval_default_channel(self, connector):
        """send_approval_request(channel_id=None) should use the default channel."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        connector.send_approval_request(None, "apr_001", "test", "desc", "reason", "impact", "low")

        call_kwargs = connector._mock_client.chat_postMessage.call_args
        assert (call_kwargs.kwargs.get("channel") or call_kwargs[1].get("channel")) == "C0123456789"

    def test_send_alert_default_channel(self, connector):
        """send_alert(channel_id=None) should use the default channel."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        connector.send_alert(None, "info", "Test")

        call_kwargs = connector._mock_client.chat_postMessage.call_args
        assert (call_kwargs.kwargs.get("channel") or call_kwargs[1].get("channel")) == "C0123456789"


# ===========================================================================
# 9. markdown_to_mrkdwn conversion
# ===========================================================================


class TestMarkdownToMrkdwn:
    """Tests for the markdown_to_mrkdwn utility function."""

    def test_double_asterisks_to_single(self):
        from src.connectors.slack import markdown_to_mrkdwn

        assert markdown_to_mrkdwn("**bold text**") == "*bold text*"

    def test_double_underscores_to_bold(self):
        from src.connectors.slack import markdown_to_mrkdwn

        assert markdown_to_mrkdwn("__bold text__") == "*bold text*"

    def test_heading_h1(self):
        from src.connectors.slack import markdown_to_mrkdwn

        assert markdown_to_mrkdwn("# Title") == "*Title*"

    def test_heading_h2(self):
        from src.connectors.slack import markdown_to_mrkdwn

        assert markdown_to_mrkdwn("## Subtitle") == "*Subtitle*"

    def test_heading_h3(self):
        from src.connectors.slack import markdown_to_mrkdwn

        assert markdown_to_mrkdwn("### Section") == "*Section*"

    def test_preserves_single_asterisks(self):
        """Already-correct Slack mrkdwn should not be altered."""
        from src.connectors.slack import markdown_to_mrkdwn

        assert markdown_to_mrkdwn("*already bold*") == "*already bold*"

    def test_preserves_code_blocks(self):
        """Code inside triple backticks should not be converted."""
        from src.connectors.slack import markdown_to_mrkdwn

        text = "before ```**not bold**``` after **bold**"
        result = markdown_to_mrkdwn(text)
        assert "```**not bold**```" in result
        assert "*bold*" in result

    def test_preserves_inline_code(self):
        """Inline code should not be converted."""
        from src.connectors.slack import markdown_to_mrkdwn

        text = "try `**not bold**` but **bold**"
        result = markdown_to_mrkdwn(text)
        assert "`**not bold**`" in result
        assert "*bold*" in result

    def test_mixed_content(self):
        """Test a realistic agent response with mixed formatting."""
        from src.connectors.slack import markdown_to_mrkdwn

        text = (
            "## System Health\n"
            "**Redis** is working fine. **PostgreSQL** too.\n"
            "Here's a code snippet: `SELECT **` — don't touch that."
        )
        result = markdown_to_mrkdwn(text)
        assert result.startswith("*System Health*")
        assert "*Redis*" in result
        assert "*PostgreSQL*" in result
        assert "`SELECT **`" in result

    def test_empty_string(self):
        from src.connectors.slack import markdown_to_mrkdwn

        assert markdown_to_mrkdwn("") == ""

    def test_no_markdown(self):
        """Plain text should pass through unchanged."""
        from src.connectors.slack import markdown_to_mrkdwn

        text = "Just some plain text, no formatting."
        assert markdown_to_mrkdwn(text) == text

    def test_idempotent(self):
        """Running twice should produce the same result (safe for double-apply)."""
        from src.connectors.slack import markdown_to_mrkdwn

        text = "**bold** and ## Heading"
        once = markdown_to_mrkdwn(text)
        twice = markdown_to_mrkdwn(once)
        assert once == twice

    def test_multiline_headings(self):
        from src.connectors.slack import markdown_to_mrkdwn

        text = "## First\nSome text\n### Second\nMore text"
        result = markdown_to_mrkdwn(text)
        assert "*First*" in result
        assert "*Second*" in result
        assert "Some text" in result


class TestConnectorLevelConversion:
    """Verify that connector methods automatically convert Markdown to mrkdwn."""

    def test_send_briefing_converts_bold(self, connector):
        """send_briefing should convert **bold** in briefing_text."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        connector.send_briefing(None, "**Redis is down** — investigate!", [])

        call_kwargs = connector._mock_client.chat_postMessage.call_args
        blocks = call_kwargs.kwargs.get("blocks") or call_kwargs[1].get("blocks", [])
        # The section block should have single asterisks, not double
        section_text = blocks[1]["text"]["text"]
        assert "**" not in section_text
        assert "*Redis is down*" in section_text

    def test_send_thread_reply_converts_bold(self, connector):
        """send_thread_reply should convert **bold** in text."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        connector.send_thread_reply("C123", "ts123", "**Status:** All systems operational")

        call_kwargs = connector._mock_client.chat_postMessage.call_args
        posted_text = call_kwargs.kwargs.get("text") or call_kwargs[1].get("text", "")
        assert "**" not in posted_text
        assert "*Status:*" in posted_text

    def test_send_alert_converts_bold(self, connector):
        """send_alert should convert **bold** in message."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        connector.send_alert(None, "info", "## Summary\n**Redis** is fine")

        call_kwargs = connector._mock_client.chat_postMessage.call_args
        blocks = call_kwargs.kwargs.get("blocks") or call_kwargs[1].get("blocks", [])
        section_text = blocks[1]["text"]["text"]
        assert "**" not in section_text
        assert "*Redis*" in section_text


class TestChunkedThreadReply:
    """Test auto-chunking of long thread replies."""

    def test_short_message_single_post(self, connector):
        """Messages under 3500 chars should be posted as a single message."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        short_text = "A" * 3000
        connector.send_thread_reply("C123", "ts123", short_text)

        assert connector._mock_client.chat_postMessage.call_count == 1

    def test_long_message_auto_chunks(self, connector):
        """Messages over 3500 chars should be split into multiple posts."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        long_text = "A" * 7000
        connector.send_thread_reply("C123", "ts123", long_text)

        assert connector._mock_client.chat_postMessage.call_count >= 2

    def test_chunking_preserves_all_content(self, connector):
        """All content should be posted across chunks, nothing dropped."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        long_text = "\n".join(f"Line {i}: {'x' * 100}" for i in range(50))
        connector.send_thread_reply("C123", "ts123", long_text)

        # Reassemble all posted chunks
        posted = ""
        for call in connector._mock_client.chat_postMessage.call_args_list:
            chunk = call.kwargs.get("text") or call[1].get("text", "")
            posted += chunk

        # Every line should appear in the reassembled output
        for i in range(50):
            assert f"Line {i}:" in posted

    def test_chunking_not_triggered_with_blocks(self, connector):
        """When blocks are provided, chunking should be skipped."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        long_text = "A" * 5000
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "test"}}]
        connector.send_thread_reply("C123", "ts123", long_text, blocks=blocks)

        # Should be single post (blocks mode, no chunking)
        assert connector._mock_client.chat_postMessage.call_count == 1

    def test_chunks_posted_to_correct_thread(self, connector):
        """All chunks should be posted to the same channel and thread."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        connector.send_thread_reply("C123", "ts_parent", "B" * 8000)

        for call in connector._mock_client.chat_postMessage.call_args_list:
            kwargs = call.kwargs
            assert kwargs.get("channel") == "C123"
            assert kwargs.get("thread_ts") == "ts_parent"


class TestDriveRedirect:
    """Test _maybe_redirect_to_drive helper."""

    def test_short_text_returned_unchanged(self):
        """Text under the threshold should pass through unchanged."""
        from src.api.routes.slack import _maybe_redirect_to_drive

        short = "Hello world"
        assert _maybe_redirect_to_drive(short, "Agent") == short

    def test_long_text_with_no_drive_returns_original(self):
        """Long text should pass through when Drive connector unavailable."""
        from src.api.routes.slack import _maybe_redirect_to_drive

        long_text = "A" * 5000

        with patch(
            "src.connectors.google_drive.GoogleDriveConnector",
            side_effect=ImportError("no creds"),
        ):
            result = _maybe_redirect_to_drive(long_text, "Agent")
            assert result == long_text

    def test_drive_failure_returns_original(self):
        """If Drive API fails, original text should be returned."""
        from src.api.routes.slack import _maybe_redirect_to_drive

        long_text = "C" * 5000

        mock_connector = MagicMock()
        mock_connector.create_document.side_effect = Exception("API error")

        with patch(
            "src.connectors.google_drive.GoogleDriveConnector",
            return_value=mock_connector,
        ):
            result = _maybe_redirect_to_drive(long_text, "Agent")
            assert result == long_text

    def test_drive_success_includes_link(self):
        """Successful Drive redirect should include the doc link."""
        from src.api.routes.slack import _maybe_redirect_to_drive

        long_text = "D" * 5000

        mock_connector = MagicMock()
        mock_connector.create_document.return_value = {
            "id": "abc",
            "title": "Agent — 2025",
            "web_view_link": "https://docs.google.com/document/d/abc/edit",
        }

        with patch(
            "src.connectors.google_drive.GoogleDriveConnector",
            return_value=mock_connector,
        ):
            result = _maybe_redirect_to_drive(long_text, "Agent")
            assert "https://docs.google.com/document/d/abc/edit" in result
            assert ":page_facing_up:" in result
            assert len(result) < len(long_text)

    def test_drive_redirect_includes_excerpt(self):
        """Redirected text should include an excerpt of the original."""
        from src.api.routes.slack import _maybe_redirect_to_drive

        long_text = "Important finding: " + "x" * 5000

        mock_connector = MagicMock()
        mock_connector.create_document.return_value = {
            "id": "xyz",
            "title": "Test",
            "web_view_link": "https://docs.google.com/document/d/xyz/edit",
        }

        with patch(
            "src.connectors.google_drive.GoogleDriveConnector",
            return_value=mock_connector,
        ):
            result = _maybe_redirect_to_drive(long_text, "Agent")
            assert "Important finding:" in result
