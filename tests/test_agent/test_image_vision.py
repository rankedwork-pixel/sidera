"""Tests for image vision support in conversation mode.

Covers:
- download_slack_file: success, too large, HTTP error
- _extract_and_download_images: filtering, limits, failures, empty
- run_conversation_turn with image_content: multimodal prompt assembly
- run_conversation_turn without images: backward compat (string prompt)
- run_agent_loop accepts list[dict] user_prompt
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. download_slack_file
# ---------------------------------------------------------------------------


def _make_httpx_mock(response_content: bytes, *, raise_on_status: Exception | None = None):
    """Build a mock httpx.AsyncClient context manager."""
    mock_response = MagicMock()
    mock_response.content = response_content
    if raise_on_status:
        mock_response.raise_for_status = MagicMock(side_effect=raise_on_status)
    else:
        mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


class TestDownloadSlackFile:
    """Tests for src.connectors.slack.download_slack_file."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Happy path: downloads file and returns bytes."""
        from src.connectors.slack import download_slack_file

        fake_bytes = b"PNG_FAKE_DATA_1234567890"
        mock_client = _make_httpx_mock(fake_bytes)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await download_slack_file(
                "https://files.slack.com/test.png",
                "xoxb-token",
            )

        assert result == fake_bytes
        mock_client.get.assert_awaited_once()
        call_kwargs = mock_client.get.call_args
        assert "Bearer xoxb-token" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_too_large(self):
        """Raises ValueError when file exceeds max_size_bytes."""
        from src.connectors.slack import download_slack_file

        big_bytes = b"X" * 1000
        mock_client = _make_httpx_mock(big_bytes)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(ValueError, match="too large"):
                await download_slack_file(
                    "https://files.slack.com/big.png",
                    "xoxb-token",
                    max_size_bytes=500,
                )

    @pytest.mark.asyncio
    async def test_http_error(self):
        """Raises on HTTP errors."""
        from src.connectors.slack import download_slack_file

        mock_client = _make_httpx_mock(b"", raise_on_status=Exception("403 Forbidden"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(Exception, match="403"):
                await download_slack_file(
                    "https://files.slack.com/forbidden.png",
                    "xoxb-token",
                )


# ---------------------------------------------------------------------------
# 2. _extract_and_download_images
# ---------------------------------------------------------------------------


class TestExtractAndDownloadImages:
    """Tests for src.api.routes.slack._extract_and_download_images."""

    @pytest.mark.asyncio
    @patch("src.connectors.slack.download_slack_file")
    async def test_extracts_images(self, mock_download):
        """Filters to allowed image types and returns content blocks."""
        from src.api.routes.slack import _extract_and_download_images

        mock_download.return_value = b"FAKEIMG"

        event = {
            "files": [
                {
                    "name": "screenshot.png",
                    "mimetype": "image/png",
                    "url_private_download": "https://files.slack.com/a.png",
                },
                {
                    "name": "photo.jpeg",
                    "mimetype": "image/jpeg",
                    "url_private_download": "https://files.slack.com/b.jpg",
                },
            ],
        }

        blocks = await _extract_and_download_images(event, "xoxb-tok")
        assert len(blocks) == 2
        assert blocks[0]["type"] == "image"
        assert blocks[0]["source"]["type"] == "base64"
        assert blocks[0]["source"]["media_type"] == "image/png"
        # Verify base64 encoding
        assert blocks[0]["source"]["data"] == base64.b64encode(b"FAKEIMG").decode("ascii")

    @pytest.mark.asyncio
    @patch("src.connectors.slack.download_slack_file")
    async def test_filters_non_image_types(self, mock_download):
        """Non-image files (PDFs, docs) are skipped."""
        from src.api.routes.slack import _extract_and_download_images

        mock_download.return_value = b"DATA"

        event = {
            "files": [
                {
                    "name": "report.pdf",
                    "mimetype": "application/pdf",
                    "url_private_download": "https://files.slack.com/r.pdf",
                },
                {
                    "name": "photo.png",
                    "mimetype": "image/png",
                    "url_private_download": "https://files.slack.com/p.png",
                },
            ],
        }

        blocks = await _extract_and_download_images(event, "xoxb-tok")
        assert len(blocks) == 1
        assert blocks[0]["source"]["media_type"] == "image/png"

    @pytest.mark.asyncio
    @patch("src.connectors.slack.download_slack_file")
    async def test_max_images_limit(self, mock_download):
        """Only first max_images images are processed."""
        from src.api.routes.slack import _extract_and_download_images

        mock_download.return_value = b"IMG"

        event = {
            "files": [
                {
                    "name": f"img{i}.png",
                    "mimetype": "image/png",
                    "url_private_download": f"https://f.com/{i}",
                }
                for i in range(5)
            ],
        }

        blocks = await _extract_and_download_images(event, "xoxb-tok", max_images=3)
        assert len(blocks) == 3

    @pytest.mark.asyncio
    @patch("src.connectors.slack.download_slack_file")
    async def test_handles_download_failure_gracefully(self, mock_download):
        """One failed download doesn't kill the rest."""
        from src.api.routes.slack import _extract_and_download_images

        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Network error")
            return b"GOOD"

        mock_download.side_effect = side_effect

        event = {
            "files": [
                {
                    "name": "bad.png",
                    "mimetype": "image/png",
                    "url_private_download": "https://f.com/bad",
                },
                {
                    "name": "good.png",
                    "mimetype": "image/png",
                    "url_private_download": "https://f.com/good",
                },
            ],
        }

        blocks = await _extract_and_download_images(event, "xoxb-tok")
        assert len(blocks) == 1  # Only the successful one

    @pytest.mark.asyncio
    async def test_empty_files_returns_empty(self):
        """No files in event returns empty list."""
        from src.api.routes.slack import _extract_and_download_images

        blocks = await _extract_and_download_images({}, "xoxb-tok")
        assert blocks == []

    @pytest.mark.asyncio
    async def test_no_files_key_returns_empty(self):
        """Event without 'files' key returns empty list."""
        from src.api.routes.slack import _extract_and_download_images

        blocks = await _extract_and_download_images({"text": "hello"}, "xoxb-tok")
        assert blocks == []


# ---------------------------------------------------------------------------
# 3. run_conversation_turn with images
# ---------------------------------------------------------------------------


class TestConversationTurnImages:
    """Verify multimodal prompt assembly in run_conversation_turn."""

    @pytest.mark.asyncio
    @patch("src.agent.core.run_agent_loop")
    async def test_with_images_sends_multimodal_prompt(self, mock_run_loop):
        """When image_content is provided, user_prompt should be a list."""
        from src.agent.api_client import TurnResult
        from src.agent.core import SideraAgent

        mock_run_loop.return_value = TurnResult(
            text="I can see the error in your screenshot.",
            cost={"total_cost_usd": 0.05},
            turn_count=1,
        )

        image_blocks = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "aGVsbG8=",
                },
            },
        ]

        agent = SideraAgent()
        result = await agent.run_conversation_turn(
            role_id="head_of_it",
            role_context="I manage IT systems.",
            thread_history=[],
            current_message="Can you look at this error?",
            user_id="user_1",
            image_content=image_blocks,
        )

        assert result.response_text == "I can see the error in your screenshot."

        # Verify user_prompt was a list (multimodal)
        call_kwargs = mock_run_loop.call_args.kwargs
        user_prompt = call_kwargs["user_prompt"]
        assert isinstance(user_prompt, list)
        assert user_prompt[0]["type"] == "text"
        assert "1 image(s)" in user_prompt[0]["text"]
        assert user_prompt[1] == image_blocks[0]

    @pytest.mark.asyncio
    @patch("src.agent.core.run_agent_loop")
    async def test_without_images_sends_string_prompt(self, mock_run_loop):
        """Without images, user_prompt should remain a plain string."""
        from src.agent.api_client import TurnResult
        from src.agent.core import SideraAgent

        mock_run_loop.return_value = TurnResult(
            text="Everything looks fine.",
            cost={"total_cost_usd": 0.03},
            turn_count=1,
        )

        agent = SideraAgent()
        await agent.run_conversation_turn(
            role_id="head_of_it",
            role_context="I manage IT systems.",
            thread_history=[],
            current_message="How is the system?",
            user_id="user_1",
        )

        call_kwargs = mock_run_loop.call_args.kwargs
        user_prompt = call_kwargs["user_prompt"]
        assert isinstance(user_prompt, str)

    @pytest.mark.asyncio
    @patch("src.agent.core.run_agent_loop")
    async def test_empty_image_content_sends_string_prompt(self, mock_run_loop):
        """Empty image_content list should behave same as None."""
        from src.agent.api_client import TurnResult
        from src.agent.core import SideraAgent

        mock_run_loop.return_value = TurnResult(
            text="All good.",
            cost={"total_cost_usd": 0.03},
            turn_count=1,
        )

        agent = SideraAgent()
        await agent.run_conversation_turn(
            role_id="head_of_it",
            role_context="I manage IT systems.",
            thread_history=[],
            current_message="Status check",
            user_id="user_1",
            image_content=[],
        )

        call_kwargs = mock_run_loop.call_args.kwargs
        user_prompt = call_kwargs["user_prompt"]
        assert isinstance(user_prompt, str)

    @pytest.mark.asyncio
    @patch("src.agent.core.run_agent_loop")
    async def test_multiple_images(self, mock_run_loop):
        """Multiple image blocks are all included in the prompt."""
        from src.agent.api_client import TurnResult
        from src.agent.core import SideraAgent

        mock_run_loop.return_value = TurnResult(
            text="I see both screenshots.",
            cost={"total_cost_usd": 0.08},
            turn_count=1,
        )

        image_blocks = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "aaaa"},
            },
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": "bbbb"},
            },
        ]

        agent = SideraAgent()
        await agent.run_conversation_turn(
            role_id="head_of_it",
            role_context="I manage IT.",
            thread_history=[],
            current_message="Here are two screenshots",
            user_id="user_1",
            image_content=image_blocks,
        )

        call_kwargs = mock_run_loop.call_args.kwargs
        user_prompt = call_kwargs["user_prompt"]
        assert isinstance(user_prompt, list)
        assert len(user_prompt) == 3  # text + 2 images
        assert "2 image(s)" in user_prompt[0]["text"]


# ---------------------------------------------------------------------------
# 4. ALLOWED_IMAGE_TYPES constant
# ---------------------------------------------------------------------------


class TestAllowedImageTypes:
    """Verify the ALLOWED_IMAGE_TYPES constant."""

    def test_contains_expected_types(self):
        from src.connectors.slack import ALLOWED_IMAGE_TYPES

        assert "image/png" in ALLOWED_IMAGE_TYPES
        assert "image/jpeg" in ALLOWED_IMAGE_TYPES
        assert "image/gif" in ALLOWED_IMAGE_TYPES
        assert "image/webp" in ALLOWED_IMAGE_TYPES

    def test_excludes_non_image_types(self):
        from src.connectors.slack import ALLOWED_IMAGE_TYPES

        assert "application/pdf" not in ALLOWED_IMAGE_TYPES
        assert "text/plain" not in ALLOWED_IMAGE_TYPES
        assert "image/svg+xml" not in ALLOWED_IMAGE_TYPES
