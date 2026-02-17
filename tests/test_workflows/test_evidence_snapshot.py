"""Tests for evidence snapshot capture before action execution.

Verifies that _capture_evidence_snapshot correctly captures pre-action
state for different action types and handles errors gracefully.
"""

from __future__ import annotations

from src.workflows.daily_briefing import _capture_evidence_snapshot


class TestCaptureEvidenceSnapshot:
    def test_budget_change_captures_current_and_proposed(self):
        """Budget change should capture current and proposed budgets."""
        snapshot = _capture_evidence_snapshot(
            "budget_change",
            {
                "platform": "google_ads",
                "current_budget_micros": 5000000,
                "new_budget_micros": 7500000,
                "campaign_id": "123",
            },
        )
        assert snapshot["current_budget"] == 5000000
        assert snapshot["proposed_budget"] == 7500000
        assert snapshot["platform"] == "google_ads"
        assert "captured_at" in snapshot

    def test_pause_campaign_captures_status(self):
        """Pause campaign should capture current status."""
        snapshot = _capture_evidence_snapshot(
            "pause_campaign",
            {
                "platform": "meta",
                "campaign_id": "456",
                "current_status": "ACTIVE",
            },
        )
        assert snapshot["current_status"] == "ACTIVE"
        assert snapshot["campaign_id"] == "456"

    def test_enable_campaign_captures_status(self):
        """Enable campaign should capture current status."""
        snapshot = _capture_evidence_snapshot(
            "enable_campaign",
            {
                "platform": "google_ads",
                "campaign_id": "789",
                "current_status": "PAUSED",
            },
        )
        assert snapshot["current_status"] == "PAUSED"

    def test_bid_change_captures_current_targets(self):
        """Bid change should capture current CPA/ROAS targets."""
        snapshot = _capture_evidence_snapshot(
            "bid_change",
            {
                "platform": "google_ads",
                "current_cpa_micros": 2000000,
                "current_roas": 4.5,
            },
        )
        assert snapshot["current_cpa_micros"] == 2000000
        assert snapshot["current_roas"] == 4.5

    def test_unknown_action_type_still_captures_basics(self):
        """Unknown action types should still capture timestamp and platform."""
        snapshot = _capture_evidence_snapshot(
            "add_negative_keywords",
            {
                "platform": "google_ads",
                "keywords": ["bad keyword"],
            },
        )
        assert "captured_at" in snapshot
        assert snapshot["platform"] == "google_ads"
        assert snapshot["action_type"] == "add_negative_keywords"

    def test_missing_params_handled_gracefully(self):
        """Missing optional params should not cause errors."""
        snapshot = _capture_evidence_snapshot(
            "budget_change",
            {"platform": "google_ads"},
        )
        assert snapshot["current_budget"] is None
        assert snapshot["proposed_budget"] is None

    def test_skill_proposal_captures_basics(self):
        """Skill proposals should capture basics without platform-specific data."""
        snapshot = _capture_evidence_snapshot(
            "skill_proposal",
            {"skill_id": "test_skill", "changes": {"name": "New Name"}},
        )
        assert snapshot["action_type"] == "skill_proposal"
        assert "captured_at" in snapshot

    def test_adset_budget_captures_budgets(self):
        """update_adset_budget should capture budget fields."""
        snapshot = _capture_evidence_snapshot(
            "update_adset_budget",
            {
                "platform": "meta",
                "current_budget_cents": 5000,
                "new_budget_cents": 7500,
            },
        )
        assert snapshot["current_budget"] == 5000
        assert snapshot["proposed_budget"] == 7500
