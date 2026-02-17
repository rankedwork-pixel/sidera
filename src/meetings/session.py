"""Meeting Session Manager — orchestrates listen-only meeting participation.

This is the core engine for meeting-aware agents. It manages a Recall.ai
bot that joins a live video call, captures the real-time transcript, and
feeds context back into the agent system for post-call processing.

    Recall.ai bot (joins call)
        → real-time transcript (via webhook or polling)
        → transcript buffer → stored in DB
        → post-call: summary + action items → manager delegation

The manager runs as a long-lived asyncio task within the FastAPI server,
NOT as an Inngest workflow (meetings require continuous monitoring).
Inngest is used only for discrete events: meeting.join, meeting.ended,
and post-call delegation.

Usage:
    manager = get_meeting_manager()
    ctx = await manager.join(
        meeting_url="https://meet.google.com/abc-defg-hij",
        role_id="head_of_marketing",
        user_id="U123",
    )
    # ... meeting runs asynchronously (listen-only) ...
    await manager.leave(ctx.bot_id)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from src.config import settings
from src.connectors.recall_ai import RecallAIConnector

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class MeetingContext:
    """Mutable state for an active meeting session."""

    meeting_id: int  # DB primary key
    bot_id: str  # Recall.ai bot UUID
    role_id: str
    role_name: str
    user_id: str
    channel_id: str
    meeting_url: str

    # Accumulated transcript
    transcript_buffer: list[dict[str, Any]] = field(default_factory=list)

    # Participants
    participants: list[dict[str, Any]] = field(default_factory=list)

    # Counters
    agent_turns: int = 0
    total_cost_usd: float = 0.0

    # State
    is_active: bool = True
    last_processed_at: float = 0.0  # monotonic time

    # Asyncio tasks
    audio_task: asyncio.Task[None] | None = None
    processor_task: asyncio.Task[None] | None = None


# Maximum transcript context chars for any single processing pass
_MAX_TRANSCRIPT_CONTEXT = 3000


# ---------------------------------------------------------------------------
# Meeting Session Manager
# ---------------------------------------------------------------------------


class MeetingSessionManager:
    """Orchestrates listen-only meeting sessions.

    Lifecycle:
        1. join()  — Create Recall.ai bot, start monitoring tasks
        2. _audio_loop() — Poll Recall.ai for bot status, detect meeting end
        3. _transcript_processor() — Periodically persist transcript to DB
        4. leave() — Disconnect, persist transcript, trigger post-call delegation

    Thread-safe: supports multiple concurrent meetings via _active_sessions
    dict keyed by bot_id.
    """

    def __init__(self) -> None:
        self._recall = RecallAIConnector()
        self._active_sessions: dict[str, MeetingContext] = {}
        self._log = logger.bind(component="meeting_session")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def join(
        self,
        meeting_url: str,
        role_id: str,
        user_id: str,
        channel_id: str = "",
    ) -> MeetingContext:
        """Join a meeting as a listen-only participant.

        1. Look up the role to get name
        2. Create Recall.ai bot
        3. Create DB meeting_session record
        4. Start monitoring tasks
        5. Post Slack notification

        Args:
            meeting_url: Full video call URL.
            role_id: The Sidera role observing the meeting.
            user_id: The user who initiated the join.
            channel_id: Slack channel for status notifications.

        Returns:
            The MeetingContext for the active session.
        """
        from src.db import service as db_service
        from src.db.session import get_db_session
        from src.skills.db_loader import load_registry_with_db

        self._log.info(
            "meeting.joining",
            meeting_url=meeting_url,
            role_id=role_id,
        )

        # Resolve the role
        registry = await load_registry_with_db()
        role = registry.get_role(role_id)
        if role is None:
            raise ValueError(f"Role '{role_id}' not found")

        role_name = role.name

        # Build the webhook URL for real-time transcript delivery.
        base_url = settings.app_base_url.rstrip("/")
        transcript_webhook_url = f"{base_url}/webhooks/recall/transcript"

        # Create Recall.ai bot with real-time transcription enabled
        bot_result = self._recall.create_bot(
            meeting_url=meeting_url,
            bot_name=role_name,
            webhook_url=transcript_webhook_url,
        )
        bot_id = bot_result.get("id", "")

        # Create DB record
        meeting_id = 0
        try:
            async with get_db_session() as session:
                meeting = await db_service.create_meeting_session(
                    session,
                    meeting_url=meeting_url,
                    role_id=role_id,
                    user_id=user_id,
                    bot_id=bot_id,
                    channel_id=channel_id,
                )
                meeting_id = meeting.id
        except Exception as exc:
            self._log.error("meeting.db_create_failed", error=str(exc))

        # Build context
        ctx = MeetingContext(
            meeting_id=meeting_id,
            bot_id=bot_id,
            role_id=role_id,
            role_name=role_name,
            user_id=user_id,
            channel_id=channel_id,
            meeting_url=meeting_url,
        )

        # Register session
        self._active_sessions[bot_id] = ctx

        # Start background tasks
        ctx.audio_task = asyncio.create_task(self._audio_loop(ctx))
        ctx.processor_task = asyncio.create_task(self._transcript_processor(ctx))

        # Post Slack notification
        await self._notify_slack(
            ctx,
            "joining",
            f":ear: *{role_name}* is joining the meeting (listen-only)...",
        )

        self._log.info(
            "meeting.joined",
            bot_id=bot_id,
            role_id=role_id,
            role_name=role_name,
            meeting_id=meeting_id,
        )

        return ctx

    async def leave(self, bot_id: str) -> dict[str, Any]:
        """Leave a meeting and trigger post-call processing.

        1. Stop monitoring tasks
        2. Remove Recall.ai bot
        3. Persist final transcript to DB
        4. Post summary to Slack
        5. Emit meeting.ended Inngest event for post-call delegation

        Args:
            bot_id: The Recall.ai bot UUID.

        Returns:
            Dict with meeting summary info.
        """
        ctx = self._active_sessions.get(bot_id)
        if ctx is None:
            self._log.warning("meeting.leave.no_session", bot_id=bot_id)
            return {"error": "No active session for this bot"}

        ctx.is_active = False

        # Cancel background tasks
        for task in [ctx.audio_task, ctx.processor_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Remove Recall.ai bot
        try:
            self._recall.remove_bot(bot_id)
        except Exception as exc:
            self._log.warning("meeting.leave.recall_error", error=str(exc))

        # Persist transcript to DB
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.update_meeting_transcript(
                    session,
                    meeting_id=ctx.meeting_id,
                    transcript_json=ctx.transcript_buffer,
                )
                await db_service.update_meeting_status(
                    session,
                    meeting_id=ctx.meeting_id,
                    status="ended",
                    agent_turns=ctx.agent_turns,
                    total_cost_usd=ctx.total_cost_usd,
                )
        except Exception as exc:
            self._log.error("meeting.leave.db_error", error=str(exc))

        # Post Slack notification
        await self._notify_slack(
            ctx,
            "ended",
            f":white_check_mark: *{ctx.role_name}* has left the meeting. "
            f"({len(ctx.transcript_buffer)} transcript entries captured)",
        )

        # Emit Inngest event for post-call delegation
        await self._emit_meeting_ended(ctx)

        # Clean up
        del self._active_sessions[bot_id]

        self._log.info(
            "meeting.left",
            bot_id=bot_id,
            role_id=ctx.role_id,
            transcript_entries=len(ctx.transcript_buffer),
        )

        return {
            "meeting_id": ctx.meeting_id,
            "role_id": ctx.role_id,
            "agent_turns": ctx.agent_turns,
            "transcript_entries": len(ctx.transcript_buffer),
            "total_cost_usd": ctx.total_cost_usd,
        }

    def receive_transcript_event(
        self,
        bot_id: str,
        event: dict[str, Any],
    ) -> None:
        """Process a real-time transcript event from Recall.ai webhook.

        Called by the ``/webhooks/recall/transcript`` FastAPI route whenever
        Recall.ai pushes a ``transcript.data`` or ``transcript.partial_data``
        event.

        Args:
            bot_id: The Recall.ai bot UUID.
            event: The transcript event payload from Recall.ai.
        """
        ctx = self._active_sessions.get(bot_id)
        if ctx is None:
            self._log.warning(
                "meeting.transcript_event.no_session",
                bot_id=bot_id,
            )
            return

        # Extract transcript data from the webhook payload
        data = event.get("data", {})
        if not data:
            return

        # Recall.ai sends transcript.data with speaker + words
        speaker = data.get("speaker", "Unknown")
        words = data.get("words", [])
        is_final = data.get("is_final", True)

        if words:
            text = " ".join(w.get("text", w.get("word", "")) for w in words if isinstance(w, dict))
        elif isinstance(data.get("text"), str):
            text = data["text"]
        else:
            text = ""

        if not text.strip():
            return

        entry = {
            "speaker": speaker,
            "text": text.strip(),
            "is_final": is_final,
            "words": words,
            "timestamp": time.time(),
        }
        ctx.transcript_buffer.append(entry)

        self._log.info(
            "meeting.transcript_received",
            bot_id=bot_id,
            speaker=speaker,
            text_len=len(text),
            is_final=is_final,
            buffer_size=len(ctx.transcript_buffer),
        )

    def get_active_session(self, bot_id: str) -> MeetingContext | None:
        """Get an active meeting session by bot ID."""
        return self._active_sessions.get(bot_id)

    def get_all_active_sessions(self) -> dict[str, MeetingContext]:
        """Get all active meeting sessions, keyed by bot_id."""
        return dict(self._active_sessions)

    # ------------------------------------------------------------------
    # Background: Bot status monitoring loop
    # ------------------------------------------------------------------

    async def _audio_loop(self, ctx: MeetingContext) -> None:
        """Monitor bot status and detect when the meeting ends.

        Real-time transcripts are delivered via webhook (see
        ``receive_transcript_event``), so this loop only needs to:
        1. Check bot status periodically
        2. Update DB when bot transitions to in_call
        3. Detect when the meeting ends (bot status ``done``)

        Runs as an asyncio.Task for the meeting duration.
        """
        poll_interval = 10  # seconds between status polls
        status_updated = False

        self._log.info("meeting.audio_loop.started", bot_id=ctx.bot_id)

        while ctx.is_active:
            try:
                await asyncio.sleep(poll_interval)

                if not ctx.is_active:
                    break

                # Check bot status via Recall.ai
                status = self._recall.get_bot_status(ctx.bot_id)
                status_changes = status.get("status_changes", [])
                latest_status = status_changes[-1].get("code", "") if status_changes else ""

                # Also check for meeting_participants in status
                meeting_participants = status.get("meeting_participants", [])
                if meeting_participants and not ctx.participants:
                    ctx.participants = meeting_participants
                    self._log.info(
                        "meeting.participants_updated",
                        bot_id=ctx.bot_id,
                        count=len(meeting_participants),
                    )

                self._log.info(
                    "meeting.status_poll",
                    bot_id=ctx.bot_id,
                    status=latest_status,
                    transcript_entries=len(ctx.transcript_buffer),
                    participants=len(ctx.participants),
                )

                # Bot finished (left meeting or meeting ended)
                if latest_status in ("done", "fatal"):
                    self._log.info("meeting.bot_done", bot_id=ctx.bot_id)
                    asyncio.create_task(self.leave(ctx.bot_id))
                    break

                # Bot is actively in the call
                if latest_status.startswith("in_call") and not status_updated:
                    try:
                        from src.db import service as db_service
                        from src.db.session import get_db_session

                        async with get_db_session() as session:
                            await db_service.update_meeting_status(
                                session,
                                meeting_id=ctx.meeting_id,
                                status="in_call",
                            )
                        status_updated = True
                    except Exception:
                        pass

                    # Post confirmation to Slack
                    await self._notify_slack(
                        ctx,
                        "in_call",
                        f":white_check_mark: *{ctx.role_name}* is now "
                        f"in the meeting and listening.",
                    )

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._log.warning(
                    "meeting.audio_loop.error",
                    bot_id=ctx.bot_id,
                    error=str(exc),
                )
                await asyncio.sleep(poll_interval)

        self._log.info("meeting.audio_loop.stopped", bot_id=ctx.bot_id)

    # ------------------------------------------------------------------
    # Background: Transcript persistence
    # ------------------------------------------------------------------

    async def _transcript_processor(self, ctx: MeetingContext) -> None:
        """Periodically persist transcript to DB.

        Runs every ``meeting_transcript_chunk_seconds`` seconds to save
        the accumulated transcript buffer to the database, ensuring
        transcript data is not lost if the process crashes.
        """
        chunk_seconds = settings.meeting_transcript_chunk_seconds

        self._log.info(
            "meeting.processor.started",
            bot_id=ctx.bot_id,
            interval=chunk_seconds,
        )

        while ctx.is_active:
            try:
                await asyncio.sleep(chunk_seconds)

                if not ctx.is_active:
                    break

                # Persist current transcript to DB
                if ctx.transcript_buffer:
                    try:
                        from src.db import service as db_service
                        from src.db.session import get_db_session

                        async with get_db_session() as session:
                            await db_service.update_meeting_transcript(
                                session,
                                meeting_id=ctx.meeting_id,
                                transcript_json=ctx.transcript_buffer,
                            )
                    except Exception as exc:
                        self._log.warning(
                            "meeting.processor.db_error",
                            bot_id=ctx.bot_id,
                            error=str(exc),
                        )

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._log.error(
                    "meeting.processor.error",
                    bot_id=ctx.bot_id,
                    error=str(exc),
                )

        self._log.info("meeting.processor.stopped", bot_id=ctx.bot_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_recent_transcript_text(self, ctx: MeetingContext) -> str:
        """Build a text string from recent transcript entries.

        Returns the last ~3000 chars of transcript formatted as:
        ``Speaker Name: what they said``
        """
        lines: list[str] = []
        total_chars = 0

        # Walk backward through transcript
        for entry in reversed(ctx.transcript_buffer):
            speaker = entry.get("speaker", "Unknown")
            text = ""
            if isinstance(entry.get("words"), list):
                text = " ".join(w.get("text", w.get("word", "")) for w in entry["words"])
            elif isinstance(entry.get("text"), str):
                text = entry["text"]

            if not text.strip():
                continue

            line = f"{speaker}: {text.strip()}"
            total_chars += len(line)
            lines.append(line)

            if total_chars > _MAX_TRANSCRIPT_CONTEXT:
                break

        lines.reverse()
        return "\n".join(lines)

    async def _notify_slack(
        self,
        ctx: MeetingContext,
        status: str,
        text: str,
    ) -> None:
        """Post a meeting status notification to Slack."""
        if not ctx.channel_id:
            return

        try:
            from src.connectors.slack import SlackConnector

            connector = SlackConnector()
            connector.send_thread_reply(
                channel_id=ctx.channel_id,
                thread_ts="",  # Top-level message, not a thread
                text=text,
            )
        except Exception as exc:
            self._log.warning("meeting.slack_notify_failed", error=str(exc))

    async def _emit_meeting_ended(self, ctx: MeetingContext) -> None:
        """Emit an Inngest event for post-call delegation.

        The meeting_end_workflow picks this up to:
        1. Summarize the transcript
        2. Extract action items
        3. Trigger the manager delegation pipeline
        """
        try:
            import inngest as inngest_mod

            from src.workflows.inngest_client import inngest_client as ic

            await ic.send(
                inngest_mod.Event(
                    name="sidera/meeting.ended",
                    data={
                        "meeting_id": ctx.meeting_id,
                        "role_id": ctx.role_id,
                        "user_id": ctx.user_id,
                        "channel_id": ctx.channel_id,
                        "bot_id": ctx.bot_id,
                        "agent_turns": ctx.agent_turns,
                        "transcript_entries": len(ctx.transcript_buffer),
                    },
                )
            )
        except Exception as exc:
            self._log.warning("meeting.inngest_emit_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_meeting_manager: MeetingSessionManager | None = None


def get_meeting_manager() -> MeetingSessionManager:
    """Get the singleton MeetingSessionManager instance."""
    global _meeting_manager
    if _meeting_manager is None:
        _meeting_manager = MeetingSessionManager()
    return _meeting_manager
