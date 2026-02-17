"""Tests for webhook event normalizers."""

from __future__ import annotations

from src.webhooks.normalizers import (
    NormalizedWebhookEvent,
    classify_severity,
    normalize_bigquery_event,
    normalize_custom_event,
    normalize_event,
    normalize_google_ads_event,
    normalize_meta_event,
)

# ---------------------------------------------------------------------------
# classify_severity
# ---------------------------------------------------------------------------


class TestClassifySeverity:
    def test_default_severity_mapping(self):
        assert classify_severity("budget_depleted") == "critical"
        assert classify_severity("policy_violation") == "critical"
        assert classify_severity("spend_spike") == "high"
        assert classify_severity("campaign_enabled") == "low"
        assert classify_severity("performance_alert") == "medium"

    def test_override_from_details(self):
        assert classify_severity("spend_spike", {"severity": "low"}) == "low"

    def test_invalid_override_ignored(self):
        assert classify_severity("spend_spike", {"severity": "extreme"}) == "high"

    def test_unknown_event_type(self):
        assert classify_severity("unknown_type") == "medium"


# ---------------------------------------------------------------------------
# Google Ads normalizer
# ---------------------------------------------------------------------------


class TestNormalizeGoogleAds:
    def test_basic_alert(self):
        payload = {
            "alert_type": "budget_threshold",
            "account_id": "1234567890",
            "campaign_id": "123",
            "campaign_name": "Brand Search",
            "description": "Budget 92% used",
            "timestamp": "2026-02-17T14:30:00Z",
        }
        event = normalize_google_ads_event(payload)
        assert event.source == "google_ads"
        assert event.event_type == "budget_threshold"
        assert event.severity == "medium"
        assert event.account_id == "1234567890"
        assert event.campaign_id == "123"
        assert "Brand Search" in event.summary
        assert event.dedup_key  # Not empty

    def test_unknown_alert_type_falls_back(self):
        payload = {"alert_type": "totally_new_type"}
        event = normalize_google_ads_event(payload)
        assert event.event_type == "custom"

    def test_secret_stripped_from_details(self):
        payload = {
            "alert_type": "budget_depleted",
            "secret": "my-secret",
            "account_id": "123",
        }
        event = normalize_google_ads_event(payload)
        assert "secret" not in event.details

    def test_missing_fields_default(self):
        event = normalize_google_ads_event({})
        assert event.source == "google_ads"
        assert event.event_type == "custom"
        assert event.account_id == ""

    def test_returns_normalized_event(self):
        event = normalize_google_ads_event({"alert_type": "spend_spike"})
        assert isinstance(event, NormalizedWebhookEvent)


# ---------------------------------------------------------------------------
# Meta normalizer
# ---------------------------------------------------------------------------


class TestNormalizeMeta:
    def test_basic_meta_event(self):
        payload = {
            "object": "ad_account",
            "entry": [
                {
                    "id": "act_123",
                    "time": 1708000000,
                    "changes": [{"field": "spend_cap", "value": {"spend_cap": 50000}}],
                }
            ],
        }
        event = normalize_meta_event(payload)
        assert event.source == "meta"
        assert event.event_type == "budget_threshold"
        assert event.account_id == "act_123"

    def test_no_entries(self):
        event = normalize_meta_event({"object": "ad_account", "entry": []})
        assert event.severity == "low"
        assert event.event_type == "custom"

    def test_no_changes(self):
        payload = {
            "object": "ad_account",
            "entry": [{"id": "act_123", "changes": []}],
        }
        event = normalize_meta_event(payload)
        assert event.event_type == "custom"

    def test_unknown_field_maps_to_custom(self):
        payload = {
            "object": "ad_account",
            "entry": [
                {
                    "id": "act_123",
                    "changes": [{"field": "unknown_field", "value": {}}],
                }
            ],
        }
        event = normalize_meta_event(payload)
        assert event.event_type == "custom"


# ---------------------------------------------------------------------------
# BigQuery normalizer
# ---------------------------------------------------------------------------


class TestNormalizeBigQuery:
    def test_conversion_drop(self):
        payload = {
            "alert_type": "conversion_drop",
            "metric": "daily_conversions",
            "current_value": 15,
            "baseline_value": 45,
            "description": "Conversions dropped 67%",
        }
        event = normalize_bigquery_event(payload)
        assert event.source == "bigquery"
        assert event.event_type == "conversion_drop"
        assert event.severity == "high"
        assert "67%" in event.summary

    def test_unknown_type(self):
        event = normalize_bigquery_event({"alert_type": "something_new"})
        assert event.event_type == "custom"


# ---------------------------------------------------------------------------
# Custom normalizer
# ---------------------------------------------------------------------------


class TestNormalizeCustom:
    def test_basic_custom(self):
        payload = {
            "event_type": "budget_depleted",
            "summary": "Budget gone",
            "severity": "critical",
        }
        event = normalize_custom_event(payload, source_id="datadog")
        assert event.source == "custom:datadog"
        assert event.event_type == "budget_depleted"
        assert event.severity == "critical"

    def test_missing_required_fields(self):
        event = normalize_custom_event({}, source_id="test")
        assert event.source == "custom:test"
        assert event.event_type == "custom"
        assert "test" in event.summary

    def test_details_not_dict(self):
        payload = {"event_type": "custom", "details": "just a string"}
        event = normalize_custom_event(payload)
        assert event.details == {"raw": "just a string"}


# ---------------------------------------------------------------------------
# Dispatch normalizer
# ---------------------------------------------------------------------------


class TestNormalizeEvent:
    def test_routes_google_ads(self):
        event = normalize_event("google_ads", {"alert_type": "spend_spike"})
        assert event.source == "google_ads"

    def test_routes_meta(self):
        event = normalize_event("meta", {"entry": []})
        assert event.source == "meta"

    def test_routes_bigquery(self):
        event = normalize_event("bigquery", {"alert_type": "custom"})
        assert event.source == "bigquery"

    def test_routes_unknown_to_custom(self):
        event = normalize_event("grafana", {"event_type": "custom"})
        assert event.source == "custom:grafana"


# ---------------------------------------------------------------------------
# NormalizedWebhookEvent
# ---------------------------------------------------------------------------


class TestNormalizedWebhookEvent:
    def test_to_dict(self):
        event = NormalizedWebhookEvent(
            source="test",
            event_type="custom",
            severity="low",
        )
        d = event.to_dict()
        assert d["source"] == "test"
        assert isinstance(d, dict)

    def test_frozen(self):
        event = NormalizedWebhookEvent(source="test", event_type="custom", severity="low")
        try:
            event.source = "changed"  # type: ignore[misc]
            assert False, "Should raise"
        except AttributeError:
            pass
