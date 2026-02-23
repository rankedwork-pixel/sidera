"""Webhook routes for external service callbacks.

Handles:
- Recall.ai real-time transcript events (POST /webhooks/recall/transcript/{bot_id})
- Google Ads Script alerts (POST /webhooks/google_ads)
- Meta webhook subscriptions (POST /webhooks/meta, GET /webhooks/meta for verification)
- BigQuery scheduled query alerts (POST /webhooks/bigquery)
- Custom monitoring endpoints (POST /webhooks/custom/{source_id})
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import structlog
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Maximum webhook payload size (100 KB). Prevents memory exhaustion from
# oversized payloads and limits blast radius of malformed requests.
_MAX_WEBHOOK_PAYLOAD_BYTES = 102_400


# ------------------------------------------------------------------
# Recall.ai transcript webhook
#
# Recall.ai real-time transcript events include the bot ID nested at
# data.bot.id in the JSON payload. The webhook handler extracts it
# from there. A bot_id path parameter variant is also provided for
# future use or explicit routing. Single-session fallback is the
# last resort when bot_id cannot be resolved.
# ------------------------------------------------------------------


@router.head("/recall/transcript/{bot_id}")
async def recall_transcript_webhook_head(bot_id: str) -> JSONResponse:
    """Respond to HEAD requests (webhook verification by Recall.ai)."""
    return JSONResponse({"status": "ok"})


@router.get("/recall/transcript/{bot_id}")
async def recall_transcript_webhook_get(bot_id: str) -> JSONResponse:
    """Respond to GET requests (webhook verification by Recall.ai)."""
    return JSONResponse({"status": "ok"})


# Also keep the generic path as a fallback (no bot_id)
@router.head("/recall/transcript")
async def recall_transcript_webhook_head_generic() -> JSONResponse:
    """Respond to HEAD requests (webhook verification by Recall.ai)."""
    return JSONResponse({"status": "ok"})


@router.get("/recall/transcript")
async def recall_transcript_webhook_get_generic() -> JSONResponse:
    """Respond to GET requests (webhook verification by Recall.ai)."""
    return JSONResponse({"status": "ok"})


@router.post("/recall/transcript/{bot_id}")
async def recall_transcript_webhook_with_bot_id(
    bot_id: str,
    request: Request,
) -> JSONResponse:
    """Receive real-time transcript events from Recall.ai (bot_id in path).

    Recall.ai posts ``transcript.data`` and ``transcript.partial_data``
    events during the call.  The actual payload format is::

        {
            "event": "transcript.data",
            "data": {
                "data": {
                    "words": [
                        {"text": "Testing.", "start_timestamp": {...}, "end_timestamp": {...}},
                        ...
                    ]
                },
                "is_final": true,
                "language": "en",
                "original_transcript_id": "..."
            }
        }

    Note: ``bot_id`` is NOT included in the payload body — it is encoded
    in the webhook URL path.
    """
    return await _handle_recall_transcript(request, bot_id)


@router.post("/recall/transcript")
async def recall_transcript_webhook_generic(request: Request) -> JSONResponse:
    """Fallback: receive transcript events without bot_id in path.

    Attempts to extract bot_id from the payload body or match against
    the single active session.
    """
    return await _handle_recall_transcript(request, bot_id=None)


async def _handle_recall_transcript(
    request: Request,
    bot_id: str | None,
) -> JSONResponse:
    """Shared handler for Recall.ai transcript webhook events."""
    try:
        raw_body = await request.body()
        if len(raw_body) > _MAX_WEBHOOK_PAYLOAD_BYTES:
            return JSONResponse(
                {"status": "payload_too_large"},
                status_code=413,
            )
        body: dict[str, Any] = await request.json()
    except Exception:
        logger.warning(
            "webhook.recall.invalid_json",
            raw=raw_body[:500] if raw_body else b"",
        )
        return JSONResponse({"status": "invalid_json"}, status_code=400)

    # --- Webhook authentication ---
    from src.config import settings

    if settings.recall_ai_webhook_secret:
        header_secret = request.headers.get("X-Webhook-Secret", "")
        if not hmac.compare_digest(
            header_secret,
            settings.recall_ai_webhook_secret,
        ):
            logger.warning("webhook.recall.auth_failed")
            return JSONResponse({"status": "unauthorized"}, status_code=401)

    # Log full payload for debugging (temporary)
    import json as _json

    logger.info(
        "webhook.recall.raw_payload",
        payload=_json.dumps(body, default=str)[:2000],
        path_bot_id=bot_id or "(none)",
    )

    event_type = body.get("event", "")
    data = body.get("data", {})

    # Resolve bot_id: prefer URL path, then data.bot.id (Recall.ai format),
    # then data.bot_id (legacy), then single-session fallback
    resolved_bot_id = bot_id
    if not resolved_bot_id:
        # Recall.ai puts bot info at data.bot.id (not data.bot_id)
        bot_obj = data.get("bot", {})
        if isinstance(bot_obj, dict) and bot_obj.get("id"):
            resolved_bot_id = bot_obj["id"]
    if not resolved_bot_id:
        resolved_bot_id = data.get("bot_id", body.get("bot_id", ""))
    if not resolved_bot_id:
        # Last resort: if there's exactly one active session, use it
        from src.meetings.session import get_meeting_manager

        manager = get_meeting_manager()
        sessions = manager.get_all_active_sessions()
        if len(sessions) == 1:
            resolved_bot_id = next(iter(sessions))
            logger.info(
                "webhook.recall.fallback_bot_id",
                resolved_bot_id=resolved_bot_id,
            )

    # --- Validate bot_id against active sessions ---
    if resolved_bot_id:
        from src.meetings.session import get_meeting_manager as _get_mgr

        _mgr = _get_mgr()
        if _mgr.get_active_session(resolved_bot_id) is None:
            logger.warning(
                "webhook.recall.unknown_bot_id",
                bot_id=resolved_bot_id,
            )
            return JSONResponse(
                {"status": "unknown_bot_id"},
                status_code=404,
            )

    # Parse transcript from the real Recall.ai payload format.
    # Format: data.data.words[] + data.data.participant.name
    inner_data = data.get("data", {})
    transcript_data: dict[str, Any] = {}

    if inner_data and isinstance(inner_data, dict) and inner_data.get("words"):
        # Real Recall.ai format: data.data.words + data.data.participant
        participant = inner_data.get("participant", {})
        speaker = "Unknown"
        if isinstance(participant, dict):
            speaker = participant.get("name", "Unknown") or "Unknown"
        elif isinstance(inner_data.get("speaker"), str):
            speaker = inner_data["speaker"]

        transcript_data = {
            "words": inner_data["words"],
            "speaker": speaker,
            "is_final": data.get("is_final", True),
            "text": inner_data.get("text", ""),
        }
    elif data.get("transcript") and isinstance(data["transcript"], dict):
        # Alternative format: data.transcript
        transcript_data = data["transcript"]
    elif data.get("words"):
        # Flat format: words directly in data
        transcript_data = data
    else:
        # Unknown format — log and return
        logger.warning(
            "webhook.recall.unknown_format",
            data_keys=list(data.keys()) if isinstance(data, dict) else str(type(data)),
        )
        return JSONResponse({"status": "unknown_format"}, status_code=200)

    logger.info(
        "webhook.recall.transcript",
        event_type=event_type,
        bot_id=resolved_bot_id or "(unknown)",
        has_words=bool(transcript_data.get("words")),
        is_final=transcript_data.get("is_final"),
    )

    if not resolved_bot_id:
        logger.warning("webhook.recall.no_bot_id")
        return JSONResponse({"status": "no_bot_id"}, status_code=200)

    # Route to the meeting session manager
    try:
        from src.meetings.session import get_meeting_manager

        manager = get_meeting_manager()
        manager.receive_transcript_event(resolved_bot_id, {"data": transcript_data})
    except Exception as exc:
        logger.error(
            "webhook.recall.process_error",
            bot_id=resolved_bot_id,
            error=str(exc),
        )

    # Always return 200 to Recall.ai so it doesn't retry
    return JSONResponse({"status": "ok"})


# ------------------------------------------------------------------
# Shared webhook event pipeline
# ------------------------------------------------------------------


async def _process_webhook_event(
    source: str,
    payload: dict,
) -> JSONResponse:
    """Normalize, deduplicate, record, and dispatch a webhook event.

    This is the shared pipeline for all external monitoring webhooks.
    Returns a JSON response suitable for the calling route.
    """
    from src.config import settings

    if not settings.webhook_enabled:
        return JSONResponse({"status": "disabled"}, status_code=200)

    from src.webhooks.normalizers import normalize_event

    event = normalize_event(source, payload)

    # Dedup + persist
    try:
        from src.db import service as db_service
        from src.db.session import get_db_session

        async with get_db_session() as session:
            # Check dedup
            if event.dedup_key:
                is_dup = await db_service.check_webhook_dedup(
                    session,
                    event.dedup_key,
                    window_hours=settings.webhook_dedup_window_hours,
                )
                if is_dup:
                    logger.info(
                        "webhook.dedup_hit",
                        source=source,
                        event_type=event.event_type,
                        dedup_key=event.dedup_key[:16],
                    )
                    return JSONResponse({"status": "duplicate"}, status_code=200)

            # Record to DB
            db_event = await db_service.record_webhook_event(
                session,
                source=event.source,
                event_type=event.event_type,
                severity=event.severity,
                summary=event.summary,
                raw_payload=event.raw_payload,
                normalized_payload=event.to_dict(),
                account_id=event.account_id,
                campaign_id=event.campaign_id,
                dedup_key=event.dedup_key,
            )
            event_id = db_event.id
    except Exception as exc:
        logger.error(
            "webhook.db_error",
            source=source,
            error=str(exc),
        )
        # Continue without DB persistence — still dispatch the event
        event_id = None

    # Dispatch Inngest event
    try:
        import inngest

        from src.workflows.inngest_client import inngest_client

        await inngest_client.send(
            inngest.Event(
                name="sidera/webhook.received",
                data={
                    "webhook_event_id": event_id,
                    "source": event.source,
                    "event_type": event.event_type,
                    "severity": event.severity,
                    "account_id": event.account_id,
                    "campaign_id": event.campaign_id,
                    "campaign_name": event.campaign_name,
                    "summary": event.summary,
                    "details": event.details,
                },
            )
        )
    except Exception as exc:
        logger.warning(
            "webhook.dispatch_error",
            source=source,
            error=str(exc),
        )
        # If Inngest dispatch fails, fall through to alert-only path
        # (the event is already recorded in DB)

    logger.info(
        "webhook.received",
        source=source,
        event_type=event.event_type,
        severity=event.severity,
        event_id=event_id,
    )
    return JSONResponse({"status": "received", "event_id": event_id})


# ------------------------------------------------------------------
# Google Ads Script webhook
# ------------------------------------------------------------------


@router.post("/google_ads")
async def google_ads_webhook(request: Request) -> JSONResponse:
    """Receive alerts from Google Ads Scripts.

    Google Ads Scripts run inside the Google Ads account and POST
    alerts to this endpoint when conditions are detected (budget
    threshold, campaign paused, spend spike, etc.).

    Authenticated via shared secret in payload or header.
    """
    raw_body = await request.body()
    if len(raw_body) > _MAX_WEBHOOK_PAYLOAD_BYTES:
        return JSONResponse(
            {"status": "payload_too_large"},
            status_code=413,
        )

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"status": "invalid_json"}, status_code=400)

    # Verify shared secret
    from src.config import settings

    if settings.webhook_secret_google_ads:
        payload_secret = body.pop("secret", "")
        header_secret = request.headers.get("X-Webhook-Secret", "")
        if not hmac.compare_digest(
            payload_secret or header_secret,
            settings.webhook_secret_google_ads,
        ):
            logger.warning("webhook.google_ads.auth_failed")
            return JSONResponse({"status": "unauthorized"}, status_code=401)

    return await _process_webhook_event("google_ads", body)


# ------------------------------------------------------------------
# Meta webhook
# ------------------------------------------------------------------


@router.get("/meta")
async def meta_webhook_verify(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_challenge: str = Query("", alias="hub.challenge"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
) -> PlainTextResponse:
    """Meta webhook verification endpoint.

    Meta sends a GET request with hub.mode=subscribe, hub.challenge,
    and hub.verify_token. We respond with the challenge to confirm.
    """
    from src.config import settings

    # Use meta_app_secret as the verify token (or a dedicated setting)
    expected_token = settings.meta_app_secret
    if hub_mode == "subscribe" and hub_verify_token == expected_token:
        return PlainTextResponse(hub_challenge)

    return PlainTextResponse("Verification failed", status_code=403)


@router.post("/meta")
async def meta_webhook(request: Request) -> JSONResponse:
    """Receive real-time events from Meta Graph API webhooks.

    Meta sends events for ad account changes (spend cap, status),
    campaign status changes, and ad disapprovals. Verified via
    X-Hub-Signature-256 HMAC.
    """
    raw_body = await request.body()
    if len(raw_body) > _MAX_WEBHOOK_PAYLOAD_BYTES:
        return JSONResponse(
            {"status": "payload_too_large"},
            status_code=413,
        )

    # Verify HMAC signature
    from src.config import settings

    if settings.meta_app_secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if signature:
            expected = (
                "sha256="
                + hmac.new(
                    settings.meta_app_secret.encode(),
                    raw_body,
                    hashlib.sha256,
                ).hexdigest()
            )
            if not hmac.compare_digest(signature, expected):
                logger.warning("webhook.meta.signature_mismatch")
                return JSONResponse({"status": "unauthorized"}, status_code=401)

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"status": "invalid_json"}, status_code=400)

    return await _process_webhook_event("meta", body)


# ------------------------------------------------------------------
# BigQuery alert webhook
# ------------------------------------------------------------------


@router.post("/bigquery")
async def bigquery_webhook(request: Request) -> JSONResponse:
    """Receive alerts from BigQuery scheduled queries via Pub/Sub push.

    BigQuery scheduled queries can trigger Cloud Pub/Sub notifications.
    The Pub/Sub subscription pushes to this endpoint. Payload is the
    decoded message data (after base64 decoding by Pub/Sub).
    """
    raw_body = await request.body()
    if len(raw_body) > _MAX_WEBHOOK_PAYLOAD_BYTES:
        return JSONResponse(
            {"status": "payload_too_large"},
            status_code=413,
        )

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"status": "invalid_json"}, status_code=400)

    # If this is a Pub/Sub push wrapper, unwrap the message
    if "message" in body and isinstance(body["message"], dict):
        import base64
        import json

        msg_data = body["message"].get("data", "")
        if msg_data:
            try:
                decoded = base64.b64decode(msg_data).decode("utf-8")
                body = json.loads(decoded)
            except Exception:
                pass  # Use raw body as-is

    return await _process_webhook_event("bigquery", body)


# ------------------------------------------------------------------
# Custom monitoring webhook
# ------------------------------------------------------------------


@router.post("/custom/{source_id}")
async def custom_webhook(source_id: str, request: Request) -> JSONResponse:
    """Receive alerts from custom monitoring tools.

    Flexible endpoint for Datadog, Grafana, custom scripts, etc.
    Authenticated via X-Webhook-Secret header.

    Required fields: ``event_type``, ``summary``.
    Optional: ``severity``, ``account_id``, ``campaign_id``, ``details``.
    """
    raw_body = await request.body()
    if len(raw_body) > _MAX_WEBHOOK_PAYLOAD_BYTES:
        return JSONResponse(
            {"status": "payload_too_large"},
            status_code=413,
        )

    # Verify shared secret
    from src.config import settings

    if settings.webhook_secret_custom:
        header_secret = request.headers.get("X-Webhook-Secret", "")
        if not hmac.compare_digest(
            header_secret,
            settings.webhook_secret_custom,
        ):
            return JSONResponse({"status": "unauthorized"}, status_code=401)

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"status": "invalid_json"}, status_code=400)

    return await _process_webhook_event(f"custom:{source_id}", body)
