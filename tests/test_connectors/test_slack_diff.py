"""Tests for the diff_text parameter on SlackConnector.send_approval_request.

Verifies that the optional diff_text parameter correctly inserts a
Block Kit section before the actions block, truncates long text, and
does nothing when empty.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.connectors.slack import SlackConnector

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FAKE_CREDENTIALS = {
    "bot_token": "xoxb-test-token-diff",
    "channel_id": "C_DIFF_TEST",
}

_BASE_APPROVAL_KWARGS = {
    "channel_id": None,
    "approval_id": "apr_diff_001",
    "action_type": "skill_proposal",
    "description": "Modify skill daily_spend_analysis",
    "reasoning": "Blended ROAS masks marginal performance",
    "projected_impact": "More accurate budget decisions",
    "risk_level": "low",
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


def _make_success_response(**extra):
    """Build a mock Slack success response."""
    response = MagicMock()
    base = {"ok": True, "channel": "C_DIFF_TEST", "ts": "9999999999.000001"}
    base.update(extra)
    response.get = lambda key, default="": base.get(key, default)
    response.__getitem__ = MagicMock(side_effect=lambda key: base[key])
    return response


def _get_posted_blocks(mock_client) -> list[dict]:
    """Extract the blocks list from the most recent chat_postMessage call."""
    call_kwargs = mock_client.chat_postMessage.call_args
    return call_kwargs.kwargs.get("blocks") or call_kwargs[1].get("blocks", [])


# ===========================================================================
# 1. No diff_text -- baseline block count
# ===========================================================================


class TestNoDiffText:
    """When diff_text is omitted or empty, the message has 5 blocks."""

    def test_no_diff_text_has_five_blocks(self, connector):
        """Without diff_text the blocks list should have exactly 5 elements."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        connector.send_approval_request(**_BASE_APPROVAL_KWARGS)

        blocks = _get_posted_blocks(connector._mock_client)
        assert len(blocks) == 5

        # Verify expected block types in order
        assert blocks[0]["type"] == "header"
        assert blocks[1]["type"] == "section"  # action description
        assert blocks[2]["type"] == "section"  # reasoning
        assert blocks[3]["type"] == "section"  # impact + risk
        assert blocks[4]["type"] == "actions"  # buttons

    def test_empty_string_diff_text_same_as_omitted(self, connector):
        """An empty string diff_text should produce the same 5 blocks."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        connector.send_approval_request(**_BASE_APPROVAL_KWARGS, diff_text="")

        blocks = _get_posted_blocks(connector._mock_client)
        assert len(blocks) == 5


# ===========================================================================
# 2. With diff_text -- extra block inserted
# ===========================================================================


class TestWithDiffText:
    """When diff_text is non-empty, a diff section is inserted before actions."""

    def test_diff_text_adds_sixth_block(self, connector):
        """Providing diff_text should produce 6 blocks total."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        connector.send_approval_request(
            **_BASE_APPROVAL_KWARGS,
            diff_text="  business_guidance:\n    before: old\n    after: new",
        )

        blocks = _get_posted_blocks(connector._mock_client)
        assert len(blocks) == 6

        # The diff block should be at index 4 (before actions at index 5)
        diff_block = blocks[4]
        assert diff_block["type"] == "section"

        # Actions should now be last
        assert blocks[5]["type"] == "actions"

    def test_diff_block_content_format(self, connector):
        """The diff block should use mrkdwn with 'Proposed Changes' header and code block."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        diff = "  output_format:\n    before: paragraphs\n    after: bullets"
        connector.send_approval_request(
            **_BASE_APPROVAL_KWARGS,
            diff_text=diff,
        )

        blocks = _get_posted_blocks(connector._mock_client)
        diff_block = blocks[4]

        text = diff_block["text"]["text"]
        assert text.startswith("*Proposed Changes:*\n```")
        assert text.endswith("```")
        assert diff in text
        assert diff_block["text"]["type"] == "mrkdwn"


# ===========================================================================
# 3. Truncation of long diff text
# ===========================================================================


class TestDiffTextTruncation:
    """Long diff text should be truncated to 2900 characters."""

    def test_long_diff_truncated(self, connector):
        """Diff text exceeding 2900 chars should be truncated with '...' appended."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        long_diff = "x" * 5000
        connector.send_approval_request(
            **_BASE_APPROVAL_KWARGS,
            diff_text=long_diff,
        )

        blocks = _get_posted_blocks(connector._mock_client)
        diff_block = blocks[4]
        text = diff_block["text"]["text"]

        # The text inside the code block should be truncated
        # Format: "*Proposed Changes:*\n```{truncated}\n...```"
        # The truncated portion is 2900 chars + "\n..."
        assert "..." in text
        # The raw content between ``` markers should not exceed ~2900 + overhead
        inner_start = text.index("```") + 3
        inner_end = text.rindex("```")
        inner = text[inner_start:inner_end]
        # Inner contains the 2900 truncated chars + "\n..."
        assert len(inner) <= 2900 + 10  # small margin for the "\n..."

    def test_short_diff_not_truncated(self, connector):
        """Diff text under 2900 chars should not be truncated."""
        connector._mock_client.chat_postMessage.return_value = _make_success_response()

        short_diff = "x" * 100
        connector.send_approval_request(
            **_BASE_APPROVAL_KWARGS,
            diff_text=short_diff,
        )

        blocks = _get_posted_blocks(connector._mock_client)
        diff_block = blocks[4]
        text = diff_block["text"]["text"]

        # The full diff should be present without "..."
        assert "..." not in text
        assert short_diff in text
