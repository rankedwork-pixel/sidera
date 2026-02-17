"""Tests for conversation-mode write operations.

Covers:
- _extract_recommendations() — JSON block parsing from agent response text
- _process_recommendations_inline() — DB approval creation + Slack buttons
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import the functions under test
from src.api.routes.slack import _extract_recommendations


class TestExtractRecommendations:
    """Test JSON recommendation extraction from agent response text."""

    def test_no_json_block(self):
        """Text without JSON block returns empty list."""
        text = "I've analyzed your campaigns and they look good."
        clean, recs = _extract_recommendations(text)
        assert clean == text
        assert recs == []

    def test_valid_json_block(self):
        """Valid JSON block is extracted and removed from text."""
        text = (
            "I recommend changing the budget.\n\n"
            "```json\n"
            '{"recommendations": [{"action_type": "budget_change", '
            '"description": "Set to $10"}]}\n'
            "```"
        )
        clean, recs = _extract_recommendations(text)
        assert "```json" not in clean
        assert len(recs) == 1
        assert recs[0]["action_type"] == "budget_change"

    def test_multiple_recommendations(self):
        """Multiple recommendations in one JSON block are all extracted."""
        recs_data = {
            "recommendations": [
                {"action_type": "enable_campaign", "description": "Enable A"},
                {"action_type": "budget_change", "description": "Set B to $10"},
            ]
        }
        text = f"Here are my suggestions:\n\n```json\n{json.dumps(recs_data)}\n```"
        clean, recs = _extract_recommendations(text)
        assert len(recs) == 2

    def test_invalid_json_returns_empty(self):
        """Malformed JSON returns empty list and original text."""
        text = "Here:\n```json\n{not valid json}\n```"
        clean, recs = _extract_recommendations(text)
        assert recs == []
        assert clean == text

    def test_clean_text_strips_json_block(self):
        """The clean text has the JSON block removed and is trimmed."""
        text = (
            "Analysis complete. Budget needs adjustment.\n\n"
            '```json\n{"recommendations": [{"action_type": "budget_change"}]}\n```'
        )
        clean, recs = _extract_recommendations(text)
        assert "```json" not in clean
        assert clean.endswith("adjustment.")

    def test_non_list_recommendations_ignored(self):
        """If recommendations is not a list, returns empty."""
        text = '```json\n{"recommendations": "not a list"}\n```'
        clean, recs = _extract_recommendations(text)
        assert recs == []


class TestProcessRecommendationsInline:
    """Test inline approval creation and Slack button posting."""

    @pytest.mark.asyncio
    async def test_creates_db_approval_for_each_recommendation(self):
        """Each recommendation should create one DB approval."""
        from src.api.routes.slack import _process_recommendations_inline

        mock_item = MagicMock()
        mock_item.id = 42

        # Mock the context manager for get_db_session
        mock_session = AsyncMock()

        async def mock_get_db_session():
            return mock_session

        # Create a proper async context manager
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        recs = [
            {
                "action_type": "enable_campaign",
                "description": "Enable Brand Search",
                "reasoning": "User requested",
                "action_params": {
                    "platform": "google_ads",
                    "customer_id": "123",
                    "campaign_id": "456",
                },
                "projected_impact": "Campaign goes live",
                "risk_level": "low",
            }
        ]

        mock_create = AsyncMock(return_value=mock_item)
        mock_slack = MagicMock()
        mock_slack.send_approval_request.return_value = {"ok": True}

        with (
            patch("src.db.session.get_db_session", return_value=mock_cm),
            patch("src.db.service.create_approval", mock_create),
            patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
        ):
            results = await _process_recommendations_inline(
                recommendations=recs,
                channel_id="C123",
                thread_ts="1234.5678",
                user_id="U123",
                role_id="media_buyer",
            )

        assert len(results) == 1
        assert results[0]["db_id"] == 42
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_posts_approval_buttons_to_thread(self):
        """Approval buttons should be posted to the Slack thread."""
        from src.api.routes.slack import _process_recommendations_inline

        mock_item = MagicMock()
        mock_item.id = 99

        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        recs = [
            {
                "action_type": "budget_change",
                "description": "Set budget to $10",
                "reasoning": "Optimization",
                "action_params": {
                    "platform": "google_ads",
                    "customer_id": "123",
                    "campaign_id": "456",
                    "new_budget_micros": 10000000,
                },
            }
        ]

        mock_create = AsyncMock(return_value=mock_item)
        mock_slack = MagicMock()
        mock_slack.send_approval_request.return_value = {"ok": True}

        with (
            patch("src.db.session.get_db_session", return_value=mock_cm),
            patch("src.db.service.create_approval", mock_create),
            patch("src.connectors.slack.SlackConnector", return_value=mock_slack),
        ):
            await _process_recommendations_inline(
                recommendations=recs,
                channel_id="C123",
                thread_ts="1234.5678",
                user_id="U123",
                role_id="media_buyer",
            )

        mock_slack.send_approval_request.assert_called_once()
        call_kwargs = mock_slack.send_approval_request.call_args
        assert call_kwargs.kwargs.get("thread_ts") == "1234.5678"
