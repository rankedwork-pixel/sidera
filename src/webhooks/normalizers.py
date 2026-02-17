"""Webhook payload normalizers for external event sources.

Each external source (Google Ads Scripts, Meta webhooks, BigQuery alerts,
custom monitors) has a different payload format. Normalizers convert raw
payloads into a unified ``NormalizedWebhookEvent`` for downstream processing.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class NormalizedWebhookEvent:
    """Unified representation of an external webhook event."""

    source: str  # "google_ads", "meta", "bigquery", "custom"
    event_type: str  # "budget_depleted", "spend_spike", etc.
    severity: str  # "low", "medium", "high", "critical"
    account_id: str = ""
    campaign_id: str = ""
    campaign_name: str = ""
    summary: str = ""
    details: dict = field(default_factory=dict)
    raw_payload: dict = field(default_factory=dict)
    timestamp: str = ""
    dedup_key: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Event type taxonomy (normalized across all sources)
# ---------------------------------------------------------------------------

KNOWN_EVENT_TYPES = frozenset(
    {
        "budget_depleted",
        "budget_threshold",
        "campaign_paused",
        "campaign_enabled",
        "spend_spike",
        "spend_drop",
        "performance_alert",
        "policy_violation",
        "conversion_drop",
        "system_alert",
        "custom",
    }
)

# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------

# Default severity per event type — normalizers may override based on context
_DEFAULT_SEVERITY: dict[str, str] = {
    "budget_depleted": "critical",
    "policy_violation": "critical",
    "spend_spike": "high",
    "conversion_drop": "high",
    "campaign_paused": "high",
    "performance_alert": "medium",
    "budget_threshold": "medium",
    "spend_drop": "medium",
    "campaign_enabled": "low",
    "system_alert": "medium",
    "custom": "medium",
}


def classify_severity(event_type: str, details: dict | None = None) -> str:
    """Determine severity from event type and optional context details.

    If *details* contains a ``severity`` override, use it (if valid).
    Otherwise fall back to the default mapping.
    """
    valid = {"low", "medium", "high", "critical"}
    if details and details.get("severity") in valid:
        return details["severity"]
    return _DEFAULT_SEVERITY.get(event_type, "medium")


def _make_dedup_key(
    source: str,
    event_type: str,
    account_id: str,
    campaign_id: str,
    hour: str,
) -> str:
    """Build a deterministic dedup key from event dimensions + hour bucket."""
    raw = f"{source}:{event_type}:{account_id}:{campaign_id}:{hour}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_hour() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")


# ---------------------------------------------------------------------------
# Google Ads Script normalizer
# ---------------------------------------------------------------------------


def normalize_google_ads_event(payload: dict) -> NormalizedWebhookEvent:
    """Normalize a Google Ads Script alert payload.

    Expected format::

        {
            "alert_type": "budget_threshold",
            "account_id": "1234567890",
            "campaign_id": "123456789",
            "campaign_name": "Brand - Search",
            "metric": "daily_budget_pct_used",
            "value": 92.5,
            "threshold": 90.0,
            "description": "Campaign has used 92.5% of daily budget",
            "timestamp": "2026-02-17T14:30:00Z"
        }
    """
    event_type = str(payload.get("alert_type", "custom"))
    if event_type not in KNOWN_EVENT_TYPES:
        event_type = "custom"

    account_id = str(payload.get("account_id", ""))
    campaign_id = str(payload.get("campaign_id", ""))
    campaign_name = str(payload.get("campaign_name", ""))
    description = str(payload.get("description", ""))
    ts = str(payload.get("timestamp", _now_iso()))

    details = {k: v for k, v in payload.items() if k not in {"alert_type", "secret", "source"}}

    severity = classify_severity(event_type, details)

    summary = description or f"Google Ads alert: {event_type}"
    if campaign_name:
        summary = f"[{campaign_name}] {summary}"

    return NormalizedWebhookEvent(
        source="google_ads",
        event_type=event_type,
        severity=severity,
        account_id=account_id,
        campaign_id=campaign_id,
        campaign_name=campaign_name,
        summary=summary,
        details=details,
        raw_payload=payload,
        timestamp=ts,
        dedup_key=_make_dedup_key("google_ads", event_type, account_id, campaign_id, _now_hour()),
    )


# ---------------------------------------------------------------------------
# Meta webhook normalizer
# ---------------------------------------------------------------------------


def normalize_meta_event(payload: dict) -> NormalizedWebhookEvent:
    """Normalize a Meta Graph API webhook payload.

    Meta sends webhooks in this structure::

        {
            "object": "ad_account",
            "entry": [
                {
                    "id": "act_123456789",
                    "time": 1708000000,
                    "changes": [
                        {
                            "field": "spend_cap",
                            "value": {...}
                        }
                    ]
                }
            ]
        }
    """
    entries = payload.get("entry", [])
    if not entries:
        return NormalizedWebhookEvent(
            source="meta",
            event_type="custom",
            severity="low",
            summary="Meta webhook with no entries",
            raw_payload=payload,
            timestamp=_now_iso(),
            dedup_key=_make_dedup_key("meta", "custom", "", "", _now_hour()),
        )

    entry = entries[0]
    account_id = str(entry.get("id", ""))
    changes = entry.get("changes", [])

    # Map Meta field changes to our event types
    field_to_event = {
        "spend_cap": "budget_threshold",
        "account_status": "system_alert",
        "effective_status": "campaign_paused",
    }

    if changes:
        change = changes[0]
        field_name = change.get("field", "")
        event_type = field_to_event.get(field_name, "custom")
        value = change.get("value", {})
    else:
        event_type = "custom"
        field_name = ""
        value = {}

    details = {
        "field": field_name,
        "value": value,
        "entry_time": entry.get("time"),
        "object_type": payload.get("object", ""),
    }

    severity = classify_severity(event_type, details)
    summary = f"Meta {payload.get('object', 'webhook')}: {field_name or event_type}"

    return NormalizedWebhookEvent(
        source="meta",
        event_type=event_type,
        severity=severity,
        account_id=account_id,
        campaign_id="",
        campaign_name="",
        summary=summary,
        details=details,
        raw_payload=payload,
        timestamp=_now_iso(),
        dedup_key=_make_dedup_key("meta", event_type, account_id, "", _now_hour()),
    )


# ---------------------------------------------------------------------------
# BigQuery alert normalizer
# ---------------------------------------------------------------------------


def normalize_bigquery_event(payload: dict) -> NormalizedWebhookEvent:
    """Normalize a BigQuery Pub/Sub push notification.

    Expects the decoded message data (after base64 decoding) in this format::

        {
            "alert_type": "conversion_drop",
            "metric": "daily_conversions",
            "current_value": 15,
            "baseline_value": 45,
            "pct_change": -66.7,
            "description": "Daily conversions dropped 67% vs 7-day average",
            "timestamp": "2026-02-17T15:00:00Z"
        }
    """
    event_type = str(payload.get("alert_type", "custom"))
    if event_type not in KNOWN_EVENT_TYPES:
        event_type = "custom"

    description = str(payload.get("description", ""))
    ts = str(payload.get("timestamp", _now_iso()))

    details = {k: v for k, v in payload.items() if k != "secret"}
    severity = classify_severity(event_type, details)

    summary = description or f"BigQuery alert: {event_type}"

    return NormalizedWebhookEvent(
        source="bigquery",
        event_type=event_type,
        severity=severity,
        account_id="",
        campaign_id="",
        campaign_name="",
        summary=summary,
        details=details,
        raw_payload=payload,
        timestamp=ts,
        dedup_key=_make_dedup_key(
            "bigquery",
            event_type,
            payload.get("metric", ""),
            "",
            _now_hour(),
        ),
    )


# ---------------------------------------------------------------------------
# Custom / generic normalizer
# ---------------------------------------------------------------------------


def normalize_custom_event(payload: dict, source_id: str = "custom") -> NormalizedWebhookEvent:
    """Normalize a custom monitoring webhook payload.

    Flexible format — required fields: ``event_type``, ``summary``.
    Optional: ``severity``, ``account_id``, ``campaign_id``, ``details``.
    """
    event_type = str(payload.get("event_type", "custom"))
    if event_type not in KNOWN_EVENT_TYPES:
        event_type = "custom"

    summary = str(payload.get("summary", f"Custom alert from {source_id}"))
    account_id = str(payload.get("account_id", ""))
    campaign_id = str(payload.get("campaign_id", ""))
    details = payload.get("details", {})
    if not isinstance(details, dict):
        details = {"raw": details}

    severity = classify_severity(event_type, payload)
    ts = str(payload.get("timestamp", _now_iso()))

    return NormalizedWebhookEvent(
        source=f"custom:{source_id}",
        event_type=event_type,
        severity=severity,
        account_id=account_id,
        campaign_id=campaign_id,
        campaign_name=str(payload.get("campaign_name", "")),
        summary=summary,
        details=details,
        raw_payload=payload,
        timestamp=ts,
        dedup_key=_make_dedup_key(
            f"custom:{source_id}", event_type, account_id, campaign_id, _now_hour()
        ),
    )


# ---------------------------------------------------------------------------
# Dispatcher — pick normalizer by source
# ---------------------------------------------------------------------------

_NORMALIZERS = {
    "google_ads": normalize_google_ads_event,
    "meta": normalize_meta_event,
    "bigquery": normalize_bigquery_event,
}


def normalize_event(source: str, payload: dict) -> NormalizedWebhookEvent:
    """Normalize a webhook payload from the given source."""
    normalizer = _NORMALIZERS.get(source)
    if normalizer:
        return normalizer(payload)
    return normalize_custom_event(payload, source_id=source)
