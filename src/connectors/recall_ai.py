"""Recall.ai Meeting Bot API connector for Sidera.

Provides a client for the Recall.ai REST API which deploys bots to join
Google Meet (and other video conference) calls. The bot captures audio,
provides real-time transcription, and exposes participant information.

Architecture:
    connector (this file) -> MeetingSessionManager -> agent loop
    All methods use httpx for HTTP requests and return clean Python dicts.

Usage:
    from src.connectors.recall_ai import RecallAIConnector

    connector = RecallAIConnector()
    bot = connector.create_bot("https://meet.google.com/abc-defg-hij")
    status = connector.get_bot_status(bot["id"])
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from src.config import settings
from src.connectors.retry import retry_with_backoff

logger = structlog.get_logger(__name__)


class RecallAIConnectorError(Exception):
    """Base exception for Recall.ai connector errors."""

    pass


class RecallAIAuthError(RecallAIConnectorError):
    """Authentication or authorization failure -- must be surfaced to user."""

    pass


class RecallAIConnector:
    """Client for the Recall.ai Meeting Bot REST API.

    Wraps Recall.ai's v1 API to manage meeting bots: create bots that
    join video calls, query their status, retrieve transcripts, manage
    participants, and send audio output.

    Args:
        credentials: Optional dict with ``api_key``. If omitted, values
            are read from the ``settings`` singleton.
    """

    def __init__(self, credentials: dict[str, str] | None = None) -> None:
        creds = credentials or self._credentials_from_settings()
        self._api_key = creds.get("api_key", "")
        region = creds.get("region", "")
        # Build region-specific base URL (Recall.ai uses regional endpoints)
        if region:
            self._base_url = f"https://{region}.recall.ai/api/v1"
        else:
            self._base_url = "https://us-west-2.recall.ai/api/v1"
        self._log = logger.bind(connector="recall_ai")

        if not self._api_key:
            self._log.warning("recall_ai.no_api_key")

    @staticmethod
    def _credentials_from_settings() -> dict[str, str]:
        """Read credentials from the global settings singleton."""
        return {
            "api_key": settings.recall_ai_api_key,
            "region": settings.recall_ai_region,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        """Build request headers with API key authorization."""
        return {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _handle_response(self, response: httpx.Response, operation: str) -> Any:
        """Handle an HTTP response, raising typed errors as needed.

        Args:
            response: The httpx Response object.
            operation: Human-readable operation name for logging.

        Returns:
            Parsed JSON response body.

        Raises:
            RecallAIAuthError: On 401 or 403 responses.
            RecallAIConnectorError: On other error responses.
        """
        if response.status_code in (401, 403):
            msg = f"Recall.ai auth error ({response.status_code}): {response.text}"
            self._log.error(
                "recall_ai.auth_error",
                operation=operation,
                status=response.status_code,
            )
            raise RecallAIAuthError(msg)

        if response.status_code >= 400:
            msg = f"Recall.ai API error ({response.status_code}): {response.text}"
            self._log.error(
                "recall_ai.api_error",
                operation=operation,
                status=response.status_code,
                body=response.text[:500],
            )
            raise RecallAIConnectorError(msg)

        if response.status_code == 204:
            return {}

        return response.json()

    # ------------------------------------------------------------------
    # Bot lifecycle
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def create_bot(
        self,
        meeting_url: str,
        bot_name: str = "Sidera",
        *,
        join_at: str | None = None,
        webhook_url: str | None = None,
    ) -> dict[str, Any]:
        """Create a Recall.ai bot that joins a meeting.

        The bot will attempt to join the meeting at the given URL.
        Google Meet, Zoom, and Microsoft Teams URLs are supported.

        When ``webhook_url`` is provided, the bot is configured with
        Recall.ai's built-in streaming transcription and will POST
        real-time transcript events (``transcript.data`` and
        ``transcript.partial_data``) to that URL during the call.

        Args:
            meeting_url: Full meeting URL (e.g. ``https://meet.google.com/abc-defg-hij``).
            bot_name: Display name for the bot in the meeting (default "Sidera").
            join_at: Optional ISO 8601 timestamp to schedule the join.
            webhook_url: URL to receive real-time transcript webhooks.

        Returns:
            Dict with at least ``{"id": "<bot_uuid>", "status": "joining", ...}``.

        Raises:
            RecallAIAuthError: If the API key is invalid.
            RecallAIConnectorError: On other API errors.
        """
        payload: dict[str, Any] = {
            "meeting_url": meeting_url,
            "bot_name": bot_name,
        }
        if join_at:
            payload["join_at"] = join_at

        # Configure real-time transcription via Recall.ai's built-in
        # streaming provider and webhook delivery.
        if webhook_url:
            payload["recording_config"] = {
                "transcript": {
                    "provider": {
                        "recallai_streaming": {
                            "mode": "prioritize_low_latency",
                            "language_code": "en",
                        },
                    },
                },
                "realtime_endpoints": [
                    {
                        "type": "webhook",
                        "url": webhook_url,
                        "events": [
                            "transcript.data",
                            "transcript.partial_data",
                        ],
                    },
                ],
            }

        # Enable audio output so the bot can speak in the meeting.
        # Recall.ai requires automatic_audio_output to be set at creation
        # time for the output_audio endpoint to work. We use a proper
        # silent MP3 frame (no audible sound on join).
        #
        # Valid MPEG1 Layer3 frame: header FF FB 90 00 = 128kbps, 44100Hz,
        # joint stereo, no CRC. Frame size = 417 bytes (4 header + 413 zero).
        # We include 3 frames (1251 bytes) for decoder warm-up.
        import base64 as _b64

        _silent_frame = b"\xff\xfb\x90\x00" + b"\x00" * 413  # 417 bytes
        silent_mp3_b64 = _b64.b64encode(_silent_frame * 3).decode("ascii")
        payload["automatic_audio_output"] = {
            "in_call_recording": {
                "data": {
                    "kind": "mp3",
                    "b64_data": silent_mp3_b64,
                },
            },
        }

        self._log.info("recall_ai.create_bot", meeting_url=meeting_url, bot_name=bot_name)

        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{self._base_url}/bot/",
                headers=self._headers(),
                json=payload,
            )

        result = self._handle_response(response, "create_bot")
        self._log.info(
            "recall_ai.bot_created",
            bot_id=result.get("id"),
            status=result.get("status"),
        )
        return result

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def get_bot_status(self, bot_id: str) -> dict[str, Any]:
        """Get the current status of a Recall.ai bot.

        Args:
            bot_id: The UUID of the bot.

        Returns:
            Dict with bot status fields including
            ``{"id": ..., "status": "joining"|"in_call"|"done"|..., ...}``.

        Raises:
            RecallAIAuthError: If the API key is invalid.
            RecallAIConnectorError: On other API errors.
        """
        self._log.debug("recall_ai.get_status", bot_id=bot_id)

        with httpx.Client(timeout=15.0) as client:
            response = client.get(
                f"{self._base_url}/bot/{bot_id}/",
                headers=self._headers(),
            )

        return self._handle_response(response, "get_bot_status")

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def remove_bot(self, bot_id: str) -> dict[str, Any]:
        """Remove a bot from a meeting (make it leave).

        Args:
            bot_id: The UUID of the bot.

        Returns:
            Empty dict on success.

        Raises:
            RecallAIAuthError: If the API key is invalid.
            RecallAIConnectorError: On other API errors.
        """
        self._log.info("recall_ai.remove_bot", bot_id=bot_id)

        with httpx.Client(timeout=15.0) as client:
            response = client.post(
                f"{self._base_url}/bot/{bot_id}/leave_call/",
                headers=self._headers(),
            )

        return self._handle_response(response, "remove_bot")

    # ------------------------------------------------------------------
    # Transcript & participants
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def get_transcript(self, bot_id: str) -> list[dict[str, Any]]:
        """Get the transcript from a meeting (in-progress or completed).

        Args:
            bot_id: The UUID of the bot.

        Returns:
            List of transcript entries, each with at least
            ``{"speaker": str, "words": list, "timestamp": float}``.

        Raises:
            RecallAIAuthError: If the API key is invalid.
            RecallAIConnectorError: On other API errors.
        """
        self._log.debug("recall_ai.get_transcript", bot_id=bot_id)

        with httpx.Client(timeout=30.0) as client:
            response = client.get(
                f"{self._base_url}/bot/{bot_id}/transcript/",
                headers=self._headers(),
            )

        result = self._handle_response(response, "get_transcript")
        # Recall.ai may return a list directly or wrapped in a dict
        if isinstance(result, list):
            return result
        return result.get("results", result.get("transcript", []))

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def get_participants(self, bot_id: str) -> list[dict[str, Any]]:
        """Get the participants of a meeting.

        Args:
            bot_id: The UUID of the bot.

        Returns:
            List of participant dicts with at least ``{"name": str}``.

        Raises:
            RecallAIAuthError: If the API key is invalid.
            RecallAIConnectorError: On other API errors.
        """
        self._log.debug("recall_ai.get_participants", bot_id=bot_id)

        # Recall.ai exposes participants via the bot status endpoint
        status = self.get_bot_status(bot_id)
        return status.get("meeting_participants", [])

    # ------------------------------------------------------------------
    # Audio output (TTS → meeting)
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def send_audio(
        self,
        bot_id: str,
        audio_data: bytes,
        *,
        sample_rate: int = 16000,
    ) -> dict[str, Any]:
        """Send audio data to a meeting via the Recall.ai bot.

        Uses the ``output_audio`` endpoint which plays a single audio
        clip into the meeting. Audio must be MP3-encoded bytes.
        Requires ``automatic_audio_output`` set at bot creation time.

        Args:
            bot_id: The UUID of the bot (must be in_call status).
            audio_data: MP3-encoded audio bytes.
            sample_rate: Ignored (kept for backward compat). MP3 carries
                its own sample rate.

        Returns:
            Dict with confirmation or empty on success.

        Raises:
            RecallAIAuthError: If the API key is invalid.
            RecallAIConnectorError: On other API errors.
        """
        import base64

        b64_audio = base64.b64encode(audio_data).decode("ascii")

        self._log.info(
            "recall_ai.send_audio",
            bot_id=bot_id,
            audio_size=len(audio_data),
            b64_size=len(b64_audio),
        )

        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                f"{self._base_url}/bot/{bot_id}/output_audio/",
                headers=self._headers(),
                json={
                    "kind": "mp3",
                    "b64_data": b64_audio,
                },
            )

        return self._handle_response(response, "send_audio")
