"""Slack connector for Sidera.

Sends messages from Inngest workflows (outside of request context) using
the slack_sdk.WebClient (synchronous). This is NOT the Slack Bolt event
handler -- that's a separate component for receiving interactive button
clicks. This connector only *sends* messages.

Architecture:
    connector (this file) -> MCP tools / Inngest workflows -> agent loop
    Each method builds Block Kit payloads and calls the Slack Web API.

Usage:
    from src.connectors.slack import SlackConnector

    connector = SlackConnector()  # uses settings singleton
    connector.send_briefing(None, "Daily briefing...", recommendations)
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from src.config import settings
from src.connectors.retry import retry_with_backoff
from src.middleware.sentry_setup import capture_exception

logger = structlog.get_logger(__name__)

# Error strings that indicate auth failures
_AUTH_ERROR_TYPES = frozenset(
    {
        "invalid_auth",
        "not_authed",
        "account_inactive",
        "token_revoked",
        "token_expired",
        "no_permission",
        "missing_scope",
        "not_allowed_token_type",
    }
)


class SlackConnectorError(Exception):
    """Base exception for Slack connector errors."""

    pass


class SlackAuthError(SlackConnectorError):
    """Authentication or authorization failure -- must be surfaced to user."""

    pass


# ---------------------------------------------------------------------------
# Image download helper (async, standalone)
# ---------------------------------------------------------------------------

ALLOWED_IMAGE_TYPES: frozenset[str] = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
    }
)

_MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB


async def download_slack_file(
    url: str,
    bot_token: str,
    *,
    max_size_bytes: int = _MAX_IMAGE_SIZE_BYTES,
    timeout_seconds: float = 30.0,
) -> bytes:
    """Download a file from Slack using bot token auth.

    Used to fetch image attachments that users share in conversation
    threads so the agent can analyze them via Claude's vision capability.

    Args:
        url: The ``url_private_download`` from the Slack file object.
        bot_token: The bot token for the ``Authorization`` header.
        max_size_bytes: Maximum file size to download. Raises
            ``ValueError`` if the response exceeds this.
        timeout_seconds: HTTP request timeout.

    Returns:
        Raw bytes of the downloaded file.

    Raises:
        ValueError: If file exceeds *max_size_bytes*.
        httpx.HTTPStatusError: On non-2xx response.
    """
    import httpx

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.get(
            url,
            headers={"Authorization": f"Bearer {bot_token}"},
            follow_redirects=True,
        )
        response.raise_for_status()

        # Check size before returning
        if len(response.content) > max_size_bytes:
            raise ValueError(
                f"File too large: {len(response.content)} bytes (max {max_size_bytes})"
            )

        return response.content


class SlackConnector:
    """Synchronous Slack client for sending messages from workflows.

    Wraps ``slack_sdk.WebClient`` and exposes clean, dict-based methods
    for sending briefings, approval requests, alerts, and connection tests.

    Args:
        credentials: Optional dict with keys ``bot_token`` and
            ``channel_id``. If omitted, values are read from the
            ``settings`` singleton (env vars / .env file).
    """

    def __init__(self, credentials: dict[str, str] | None = None) -> None:
        creds = credentials if credentials is not None else self._credentials_from_settings()
        self._bot_token = creds.get("bot_token", "")
        self._default_channel_id = creds.get("channel_id", "")
        self._client = WebClient(token=self._bot_token)
        self._log = logger.bind(connector="slack")

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def send_briefing(
        self,
        channel_id: str | None,
        briefing_text: str,
        recommendations: list[dict],
    ) -> dict[str, Any]:
        """Post a daily briefing to Slack with Block Kit formatting.

        Args:
            channel_id: Slack channel ID. Falls back to default if None.
            briefing_text: The main briefing content (markdown).
            recommendations: List of recommendation dicts, each expected
                to have at least ``title`` and ``description`` keys.

        Returns:
            Dict with ``ok``, ``channel``, and ``ts`` keys.

        Raises:
            SlackAuthError: On auth failures.
            SlackConnectorError: On other Slack API errors.
        """
        channel = channel_id or self._default_channel_id
        self._log.info("sending_briefing", channel=channel)

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Sidera Daily Briefing",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": briefing_text,
                },
            },
        ]

        if recommendations:
            blocks.append({"type": "divider"})
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Recommendations ({len(recommendations)}):*",
                    },
                }
            )

            for rec in recommendations:
                title = rec.get("title", "Untitled")
                description = rec.get("description", "")
                rec_text = f"*{title}*"
                if description:
                    rec_text += f"\n{description}"

                blocks.append({"type": "divider"})
                blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": rec_text,
                        },
                    }
                )

        try:
            response = self._client.chat_postMessage(
                channel=channel,
                text="Sidera Daily Briefing",
                blocks=blocks,
            )
            result = {
                "ok": response.get("ok", True),
                "channel": response.get("channel", channel),
                "ts": response.get("ts", ""),
            }
            self._log.info("briefing_sent", channel=channel, ts=result["ts"])
            return result

        except SlackApiError as exc:
            self._handle_slack_error(exc, "send_briefing", channel=channel)
            # _handle_slack_error always raises, but the return keeps mypy happy
            return {}

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def send_approval_request(
        self,
        channel_id: str | None,
        approval_id: str,
        action_type: str,
        description: str,
        reasoning: str,
        projected_impact: str,
        risk_level: str,
        diff_text: str = "",
        thread_ts: str = "",
        task_preview: str = "",
        steward_mention: str = "",
    ) -> dict[str, Any]:
        """Post an interactive approval request with Approve/Reject buttons.

        Args:
            channel_id: Slack channel ID. Falls back to default if None.
            approval_id: Unique ID for this approval request.
            action_type: Type of action (e.g. ``"budget_change"``).
            description: Human-readable description of the proposed action.
            reasoning: Why the agent recommends this action.
            projected_impact: Expected impact if approved.
            risk_level: Risk level (``"low"``, ``"medium"``, ``"high"``).
            diff_text: Optional diff text for skill proposals. Displayed
                in a code block before the Approve/Reject buttons.
            thread_ts: Optional thread timestamp for in-thread approvals.
            task_preview: Optional preview text for Claude Code tasks.
                Displayed in a code block before the Approve/Reject buttons.
            steward_mention: Optional Slack @mention for the role steward
                (e.g. ``"<@U12345>"``). Displayed above the action buttons.

        Returns:
            Dict with ``ok``, ``channel``, and ``ts`` keys.

        Raises:
            SlackAuthError: On auth failures.
            SlackConnectorError: On other Slack API errors.
        """
        channel = channel_id or self._default_channel_id
        self._log.info(
            "sending_approval_request",
            channel=channel,
            approval_id=approval_id,
            action_type=action_type,
        )

        risk_emoji = {
            "low": "large_green_circle",
            "medium": "large_yellow_circle",
            "high": "red_circle",
        }.get(risk_level.lower(), "white_circle")

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Approval Required: {action_type}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Action:* {description}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Reasoning:* {reasoning}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Projected Impact:* {projected_impact}\n"
                        f"*Risk Level:* :{risk_emoji}: {risk_level}"
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Approve",
                        },
                        "style": "primary",
                        "action_id": "sidera_approve",
                        "value": approval_id,
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Reject",
                        },
                        "style": "danger",
                        "action_id": "sidera_reject",
                        "value": approval_id,
                    },
                ],
            },
        ]

        # Insert diff block for skill proposals (before the actions block)
        if diff_text:
            truncated = diff_text[:2900]
            if len(diff_text) > 2900:
                truncated += "\n..."
            blocks.insert(
                -1,
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Proposed Changes:*\n```{truncated}```",
                    },
                },
            )

        # Insert task preview for Claude Code tasks (before the actions block)
        if task_preview:
            truncated_preview = task_preview[:2900]
            if len(task_preview) > 2900:
                truncated_preview += "\n..."
            blocks.insert(
                -1,
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (f"*Task Details:*\n```{truncated_preview}```"),
                    },
                },
            )

        # Insert steward @mention (before the actions block)
        if steward_mention:
            blocks.insert(
                -1,
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f":shield: Steward: {steward_mention}",
                        }
                    ],
                },
            )

        try:
            kwargs: dict[str, Any] = {
                "channel": channel,
                "text": f"Approval required: {action_type} - {description}",
                "blocks": blocks,
            }
            if thread_ts:
                kwargs["thread_ts"] = thread_ts
            response = self._client.chat_postMessage(**kwargs)
            result = {
                "ok": response.get("ok", True),
                "channel": response.get("channel", channel),
                "ts": response.get("ts", ""),
            }
            self._log.info(
                "approval_request_sent",
                channel=channel,
                approval_id=approval_id,
                ts=result["ts"],
            )
            return result

        except SlackApiError as exc:
            self._handle_slack_error(
                exc,
                "send_approval_request",
                channel=channel,
                approval_id=approval_id,
            )
            return {}

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def update_approval_message(
        self,
        channel_id: str,
        message_ts: str,
        approval_id: str,
        status: str,
        decided_by: str,
    ) -> dict[str, Any]:
        """Update an approval message after the user decides.

        Replaces the interactive buttons with a status indicator showing
        who approved or rejected the action.

        Args:
            channel_id: The channel containing the approval message.
            message_ts: The ``ts`` of the original approval message.
            approval_id: The approval ID for logging.
            status: Decision status (``"approved"`` or ``"rejected"``).
            decided_by: Slack user ID or display name of the decider.

        Returns:
            Dict with ``ok``, ``channel``, and ``ts`` keys.

        Raises:
            SlackAuthError: On auth failures.
            SlackConnectorError: On other Slack API errors.
        """
        self._log.info(
            "updating_approval_message",
            channel=channel_id,
            message_ts=message_ts,
            approval_id=approval_id,
            status=status,
        )

        status_emoji = "white_check_mark" if status == "approved" else "x"
        status_label = status.upper()

        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":{status_emoji}: *{status_label}* by <@{decided_by}>\n"
                        f"Approval ID: `{approval_id}`"
                    ),
                },
            },
        ]

        try:
            response = self._client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=f"Approval {status} by {decided_by}",
                blocks=blocks,
            )
            result = {
                "ok": response.get("ok", True),
                "channel": response.get("channel", channel_id),
                "ts": response.get("ts", ""),
            }
            self._log.info(
                "approval_message_updated",
                channel=channel_id,
                approval_id=approval_id,
                status=status,
            )
            return result

        except SlackApiError as exc:
            self._handle_slack_error(
                exc,
                "update_approval_message",
                channel=channel_id,
                approval_id=approval_id,
            )
            return {}

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def send_alert(
        self,
        channel_id: str | None,
        alert_type: str,
        message: str,
        details: dict | None = None,
    ) -> dict[str, Any]:
        """Post an alert message to Slack.

        Used for cost overruns, anomalies, errors, or other important
        notifications outside the daily briefing cycle.

        Args:
            channel_id: Slack channel ID. Falls back to default if None.
            alert_type: Type of alert (e.g. ``"cost_overrun"``,
                ``"anomaly"``, ``"error"``).
            message: Human-readable alert message.
            details: Optional dict with additional context.

        Returns:
            Dict with ``ok``, ``channel``, and ``ts`` keys.

        Raises:
            SlackAuthError: On auth failures.
            SlackConnectorError: On other Slack API errors.
        """
        channel = channel_id or self._default_channel_id
        self._log.info(
            "sending_alert",
            channel=channel,
            alert_type=alert_type,
        )

        alert_emoji = {
            "cost_overrun": "money_with_wings",
            "anomaly": "warning",
            "error": "rotating_light",
            "info": "information_source",
        }.get(alert_type.lower(), "bell")

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Sidera Alert: {alert_type}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":{alert_emoji}: {message}",
                },
            },
        ]

        if details:
            details_text = json.dumps(details, indent=2, default=str)
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"```{details_text}```",
                    },
                }
            )

        try:
            response = self._client.chat_postMessage(
                channel=channel,
                text=f"Sidera Alert ({alert_type}): {message}",
                blocks=blocks,
            )
            result = {
                "ok": response.get("ok", True),
                "channel": response.get("channel", channel),
                "ts": response.get("ts", ""),
            }
            self._log.info("alert_sent", channel=channel, ts=result["ts"])
            return result

        except SlackApiError as exc:
            self._handle_slack_error(
                exc,
                "send_alert",
                channel=channel,
                alert_type=alert_type,
            )
            return {}

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def send_auto_execute_notification(
        self,
        channel_id: str | None,
        action_type: str,
        description: str,
        reasoning: str,
        rule_id: str,
        rule_description: str = "",
        result: dict | None = None,
    ) -> dict[str, Any]:
        """Post a notification that an action was auto-executed.

        Unlike approval requests, this has no buttons — the action has
        already been executed. This is an informational notification.

        Args:
            channel_id: Slack channel ID. Falls back to default if None.
            action_type: Type of action (e.g. ``"pause_campaign"``).
            description: Human-readable action description.
            reasoning: Agent's reasoning for the action.
            rule_id: The auto-execute rule that matched.
            rule_description: Human-readable rule description.
            result: Optional execution result dict.

        Returns:
            Dict with ``ok``, ``channel``, and ``ts`` keys.
        """
        channel = channel_id or self._default_channel_id
        self._log.info(
            "sending_auto_execute_notification",
            channel=channel,
            action_type=action_type,
            rule_id=rule_id,
        )

        fields = [
            {
                "type": "mrkdwn",
                "text": f"*Action:*\n{description}",
            },
            {
                "type": "mrkdwn",
                "text": f"*Reasoning:*\n{reasoning}",
            },
            {
                "type": "mrkdwn",
                "text": f"*Rule:*\n`{rule_id}`",
            },
        ]
        if rule_description:
            fields.append(
                {
                    "type": "mrkdwn",
                    "text": f"*Rule Detail:*\n{rule_description}",
                }
            )

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Auto-Executed: {action_type}",
                },
            },
            {
                "type": "section",
                "fields": fields[:2],
            },
            {
                "type": "section",
                "fields": fields[2:],
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            ":robot_face: This action was automatically "
                            "executed based on pre-approved rules."
                        ),
                    },
                ],
            },
        ]

        if result:
            result_text = json.dumps(result, indent=2, default=str)
            blocks.insert(
                -1,
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Result:*\n```{result_text}```",
                    },
                },
            )

        try:
            response = self._client.chat_postMessage(
                channel=channel,
                text=(f"Auto-Executed: {action_type} — {description}"),
                blocks=blocks,
            )
            result_data = {
                "ok": response.get("ok", True),
                "channel": response.get("channel", channel),
                "ts": response.get("ts", ""),
            }
            self._log.info(
                "auto_execute_notification_sent",
                channel=channel,
                ts=result_data["ts"],
            )
            return result_data

        except SlackApiError as exc:
            self._handle_slack_error(
                exc,
                "send_auto_execute_notification",
                channel=channel,
                action_type=action_type,
            )
            return {}

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def test_connection(self) -> dict[str, Any]:
        """Verify the bot token by calling auth.test().

        Returns:
            Dict with ``ok``, ``team``, ``user``, and ``bot_id`` keys.

        Raises:
            SlackAuthError: On auth failures.
            SlackConnectorError: On other Slack API errors.
        """
        self._log.info("testing_connection")
        try:
            response = self._client.auth_test()
            result = {
                "ok": response.get("ok", True),
                "team": response.get("team", ""),
                "user": response.get("user", ""),
                "bot_id": response.get("bot_id", ""),
            }
            self._log.info(
                "connection_test_passed",
                team=result["team"],
                user=result["user"],
            )
            return result

        except SlackApiError as exc:
            self._handle_slack_error(exc, "test_connection")
            return {}

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def send_thread_reply(
        self,
        channel_id: str,
        thread_ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Post a reply in a specific Slack thread.

        Used by conversation mode to send agent responses within a
        threaded discussion rather than as top-level channel messages.

        Args:
            channel_id: The channel containing the thread.
            thread_ts: The timestamp of the parent message (thread root).
            text: Fallback text for notifications / accessibility.
            blocks: Optional Block Kit blocks for rich formatting.

        Returns:
            Dict with ``ok``, ``channel``, and ``ts`` keys.

        Raises:
            SlackAuthError: On auth failures.
            SlackConnectorError: On other Slack API errors.
        """
        self._log.info(
            "sending_thread_reply",
            channel=channel_id,
            thread_ts=thread_ts,
        )

        try:
            kwargs: dict[str, Any] = {
                "channel": channel_id,
                "thread_ts": thread_ts,
                "text": text,
            }
            if blocks:
                kwargs["blocks"] = blocks

            response = self._client.chat_postMessage(**kwargs)
            result = {
                "ok": response.get("ok", True),
                "channel": response.get("channel", channel_id),
                "ts": response.get("ts", ""),
            }
            self._log.info(
                "thread_reply_sent",
                channel=channel_id,
                thread_ts=thread_ts,
                ts=result["ts"],
            )
            return result

        except SlackApiError as exc:
            self._handle_slack_error(
                exc,
                "send_thread_reply",
                channel=channel_id,
                thread_ts=thread_ts,
            )
            return {}

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def get_thread_history(
        self,
        channel_id: str,
        thread_ts: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Retrieve all messages in a Slack thread.

        Uses Slack's ``conversations.replies()`` API to fetch the full
        thread history. Each message is normalized into a simple dict
        for consumption by the agent prompt builder.

        Args:
            channel_id: The channel containing the thread.
            thread_ts: The timestamp of the parent message.
            limit: Maximum number of messages to retrieve (default 50).

        Returns:
            List of message dicts, each with ``user``, ``text``, ``ts``,
            ``bot_id``, and ``is_bot`` keys.

        Raises:
            SlackAuthError: On auth failures.
            SlackConnectorError: On other Slack API errors.
        """
        self._log.info(
            "fetching_thread_history",
            channel=channel_id,
            thread_ts=thread_ts,
            limit=limit,
        )

        try:
            response = self._client.conversations_replies(
                channel=channel_id,
                ts=thread_ts,
                limit=limit,
            )
            messages = response.get("messages", [])
            result = [
                {
                    "user": m.get("user", ""),
                    "text": m.get("text", ""),
                    "ts": m.get("ts", ""),
                    "bot_id": m.get("bot_id"),
                    "is_bot": bool(m.get("bot_id") or m.get("subtype") == "bot_message"),
                }
                for m in messages
            ]
            self._log.info(
                "thread_history_fetched",
                channel=channel_id,
                thread_ts=thread_ts,
                message_count=len(result),
            )
            return result

        except SlackApiError as exc:
            self._handle_slack_error(
                exc,
                "get_thread_history",
                channel=channel_id,
                thread_ts=thread_ts,
            )
            return []

    def add_reaction(
        self,
        channel_id: str,
        timestamp: str,
        name: str = "eyes",
    ) -> None:
        """Add a reaction emoji to a message.

        Used as a lightweight typing indicator — the bot adds an :eyes:
        reaction when it starts processing a conversation turn and
        removes it when done.

        This method swallows all errors because reactions are
        non-critical UX enhancements.

        Args:
            channel_id: The channel containing the message.
            timestamp: The ``ts`` of the message to react to.
            name: The reaction emoji name (default ``"eyes"``).
        """
        try:
            self._client.reactions_add(
                channel=channel_id,
                timestamp=timestamp,
                name=name,
            )
        except SlackApiError:
            pass  # Non-critical — don't fail on reaction errors

    def remove_reaction(
        self,
        channel_id: str,
        timestamp: str,
        name: str = "eyes",
    ) -> None:
        """Remove a reaction emoji from a message.

        Counterpart to ``add_reaction`` — removes the typing indicator
        after the agent has finished processing.

        This method swallows all errors because reactions are
        non-critical UX enhancements.

        Args:
            channel_id: The channel containing the message.
            timestamp: The ``ts`` of the message to unreact.
            name: The reaction emoji name (default ``"eyes"``).
        """
        try:
            self._client.reactions_remove(
                channel=channel_id,
                timestamp=timestamp,
                name=name,
            )
        except SlackApiError:
            pass  # Non-critical — don't fail on reaction errors

    def post_typing_indicator(
        self,
        channel_id: str,
        thread_ts: str,
        role_name: str = "",
    ) -> str:
        """Post a 'thinking' message as a visible typing indicator.

        Returns the ``ts`` of the posted message so the caller can
        delete it later via ``delete_message()``.

        This method swallows all errors because typing indicators are
        non-critical UX enhancements.

        Args:
            channel_id: The channel containing the thread.
            thread_ts: The timestamp of the parent message (thread root).
            role_name: Optional role name to display (e.g. "Head of Marketing").
                If provided, shows ":thought_balloon: _Head of Marketing is
                typing..._". If empty, shows generic ":thought_balloon:
                _Thinking..._".

        Returns:
            The ``ts`` of the typing indicator message, or empty string
            on failure.
        """
        if role_name:
            text = f":thought_balloon: _{role_name} is typing..._"
        else:
            text = ":thought_balloon: _Thinking..._"
        try:
            response = self._client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=text,
            )
            return response.get("ts", "")
        except SlackApiError:
            return ""

    def delete_message(
        self,
        channel_id: str,
        message_ts: str,
    ) -> None:
        """Delete a message posted by the bot.

        Used to remove typing indicator messages after the real
        response is posted.

        This method swallows all errors because cleanup of typing
        indicators is non-critical.

        Args:
            channel_id: The channel containing the message.
            message_ts: The ``ts`` of the message to delete.
        """
        try:
            self._client.chat_delete(
                channel=channel_id,
                ts=message_ts,
            )
        except SlackApiError:
            pass  # Non-critical — don't fail on delete errors

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def send_meeting_notification(
        self,
        channel_id: str | None,
        meeting_url: str,
        role_name: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Post a meeting status notification to Slack.

        Used to inform the channel when a department head joins or leaves
        a meeting, or when meeting status changes.

        Args:
            channel_id: Slack channel ID. Falls back to default if None.
            meeting_url: The meeting URL (Google Meet, Zoom, etc.).
            role_name: The role participating in the meeting.
            status: Meeting status — ``"joining"``, ``"in_call"``,
                ``"left"``, ``"ended"``, or ``"error"``.
            details: Optional dict with extra context (bot_id, duration, etc.).

        Returns:
            Dict with ``ok``, ``channel``, and ``ts`` keys.

        Raises:
            SlackAuthError: On auth failures.
            SlackConnectorError: On other Slack API errors.
        """
        channel = channel_id or self._default_channel_id
        self._log.info(
            "sending_meeting_notification",
            channel=channel,
            role_name=role_name,
            status=status,
        )

        status_emoji = {
            "joining": ":telephone_receiver:",
            "in_call": ":microphone:",
            "left": ":wave:",
            "ended": ":checkered_flag:",
            "error": ":x:",
        }.get(status, ":bell:")

        status_label = {
            "joining": "Joining meeting",
            "in_call": "In meeting",
            "left": "Left meeting",
            "ended": "Meeting ended",
            "error": "Meeting error",
        }.get(status, status.title())

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{status_label} — {role_name}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{status_emoji} *{role_name}* — {status_label.lower()}\n"
                        f"Meeting: {meeting_url}"
                    ),
                },
            },
        ]

        if details:
            detail_parts = []
            if "bot_id" in details:
                detail_parts.append(f"Bot ID: `{details['bot_id']}`")
            if "duration_minutes" in details:
                detail_parts.append(f"Duration: {details['duration_minutes']} min")
            if "agent_turns" in details:
                detail_parts.append(f"Agent spoke: {details['agent_turns']} times")
            if "cost" in details:
                detail_parts.append(f"Cost: ${details['cost']:.2f}")
            if "error" in details:
                detail_parts.append(f"Error: {details['error']}")

            if detail_parts:
                blocks.append(
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": " · ".join(detail_parts),
                            },
                        ],
                    }
                )

        fallback_text = f"{status_emoji} {role_name}: {status_label} — {meeting_url}"
        try:
            response = self._client.chat_postMessage(
                channel=channel,
                text=fallback_text,
                blocks=blocks,
            )
            result = {
                "ok": response.get("ok", True),
                "channel": response.get("channel", channel),
                "ts": response.get("ts", ""),
            }
            self._log.info(
                "meeting_notification_sent",
                channel=channel,
                ts=result["ts"],
                status=status,
            )
            return result

        except SlackApiError as exc:
            self._handle_slack_error(
                exc,
                "send_meeting_notification",
                channel=channel,
                status=status,
            )
            return {}

    @staticmethod
    def _credentials_from_settings() -> dict[str, str]:
        """Build a credentials dict from the global settings singleton."""
        return {
            "bot_token": settings.slack_bot_token,
            "channel_id": settings.slack_channel_id,
        }

    def _handle_slack_error(
        self,
        exc: SlackApiError,
        operation: str,
        **context: Any,
    ) -> None:
        """Inspect a SlackApiError and raise the appropriate exception.

        Auth errors (invalid_auth, not_authed, account_inactive,
        token_revoked) raise ``SlackAuthError``. All other errors raise
        ``SlackConnectorError``.

        Args:
            exc: The caught SlackApiError.
            operation: A label for the operation that failed.
            **context: Extra fields to include in the structured log.

        Raises:
            SlackAuthError: On authentication / authorization failures.
            SlackConnectorError: On all other API errors.
        """
        capture_exception(exc)

        error_type = exc.response.get("error", "") if exc.response else ""

        self._log.error(
            "slack_api_error",
            operation=operation,
            error=error_type,
            response=str(exc.response) if exc.response else "",
            **context,
        )

        if error_type in _AUTH_ERROR_TYPES:
            raise SlackAuthError(f"Slack auth error during {operation}: {error_type}") from exc

        raise SlackConnectorError(f"Slack API error during {operation}: {error_type}") from exc
