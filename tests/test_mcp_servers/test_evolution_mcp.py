"""Tests for src.mcp_servers.evolution -- propose_skill_change MCP tool.

Covers input validation (missing/empty fields, forbidden fields, unknown
fields), successful proposals (modification + new skill), evidence memory
IDs, and the pending-proposals lifecycle (accumulate, get, clear).
"""

from __future__ import annotations

import asyncio

import pytest

from src.mcp_servers.evolution import (
    clear_pending_proposals,
    get_pending_proposals,
    propose_skill_change,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine synchronously for testing."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_proposals():
    """Ensure pending proposals are empty before and after each test."""
    clear_pending_proposals()
    yield
    clear_pending_proposals()


# ===========================================================================
# 1. Valid modification proposal
# ===========================================================================


class TestValidModificationProposal:
    """propose_skill_change with an existing skill_id."""

    def test_valid_modification_returns_queued(self):
        """A valid modification proposal should return success with 'queued' text."""
        result = _run(
            propose_skill_change.handler(
                {
                    "skill_id": "daily_spend_analysis",
                    "proposed_changes": {
                        "business_guidance": "Focus on incremental ROAS rather than blended ROAS.",
                    },
                    "rationale": "Blended ROAS masks poor marginal performance on new campaigns.",
                }
            )
        )

        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "Skill proposal queued" in text
        assert "business_guidance" in text

    def test_valid_modification_stored_in_pending(self):
        """A valid proposal should be stored in the pending proposals list."""
        _run(
            propose_skill_change.handler(
                {
                    "skill_id": "daily_spend_analysis",
                    "proposed_changes": {
                        "output_format": "Use bullet points instead of paragraphs.",
                    },
                    "rationale": "Stakeholders prefer scannable bullet-point summaries.",
                }
            )
        )

        proposals = get_pending_proposals()
        assert len(proposals) == 1
        assert proposals[0]["action_type"] == "skill_proposal"
        assert proposals[0]["action_params"]["proposal_type"] == "modify"
        assert proposals[0]["action_params"]["skill_id"] == "daily_spend_analysis"


# ===========================================================================
# 2. Valid new skill proposal
# ===========================================================================


class TestValidNewSkillProposal:
    """propose_skill_change without a skill_id (new skill)."""

    def test_new_skill_returns_queued(self):
        """A new skill proposal (no skill_id) should return success."""
        result = _run(
            propose_skill_change.handler(
                {
                    "proposed_changes": {
                        "name": "Creative Fatigue Detector",
                        "description": "Detect ad creative fatigue from frequency and CTR trends.",
                        "category": "monitoring",
                        "system_supplement": "You are a creative fatigue analyst.",
                        "prompt_template": "Analyze these metrics for creative fatigue: {data}",
                        "output_format": "Markdown summary with severity rating.",
                        "business_guidance": (
                            "Flag creatives with >3.0 frequency and declining CTR."
                        ),
                    },
                    "rationale": "No existing skill monitors creative fatigue systematically.",
                }
            )
        )

        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "Skill proposal queued" in text
        assert "Create new skill" in text

    def test_new_skill_stored_as_create(self):
        """A new skill proposal should have proposal_type 'create'."""
        _run(
            propose_skill_change.handler(
                {
                    "proposed_changes": {
                        "name": "Audience Overlap Checker",
                        "description": "Check for audience overlap across campaigns.",
                        "category": "audience",
                        "system_supplement": "You analyze audience overlap.",
                        "prompt_template": "Find overlapping audiences: {data}",
                        "output_format": "Table of overlapping segments.",
                        "business_guidance": "Flag overlaps exceeding 30% shared users.",
                    },
                    "rationale": "Audience overlap wastes budget on duplicate impressions.",
                }
            )
        )

        proposals = get_pending_proposals()
        assert len(proposals) == 1
        assert proposals[0]["action_params"]["proposal_type"] == "create"
        assert "skill_fields" in proposals[0]["action_params"]


# ===========================================================================
# 3. Missing proposed_changes
# ===========================================================================


class TestMissingProposedChanges:
    """Validation: proposed_changes is required."""

    def test_missing_proposed_changes_returns_error(self):
        """Omitting proposed_changes should return an error."""
        result = _run(
            propose_skill_change.handler(
                {
                    "rationale": "Some rationale.",
                }
            )
        )

        assert result["is_error"] is True
        assert "proposed_changes" in result["content"][0]["text"].lower()

    def test_empty_proposed_changes_returns_error(self):
        """An empty proposed_changes dict should return an error."""
        result = _run(
            propose_skill_change.handler(
                {
                    "proposed_changes": {},
                    "rationale": "Some rationale.",
                }
            )
        )

        assert result["is_error"] is True
        assert "proposed_changes" in result["content"][0]["text"].lower()


# ===========================================================================
# 4. Missing rationale
# ===========================================================================


class TestMissingRationale:
    """Validation: rationale is required."""

    def test_missing_rationale_returns_error(self):
        """Omitting rationale should return an error."""
        result = _run(
            propose_skill_change.handler(
                {
                    "proposed_changes": {"output_format": "New format."},
                }
            )
        )

        assert result["is_error"] is True
        assert "rationale" in result["content"][0]["text"].lower()

    def test_empty_rationale_returns_error(self):
        """A whitespace-only rationale should return an error."""
        result = _run(
            propose_skill_change.handler(
                {
                    "proposed_changes": {"output_format": "New format."},
                    "rationale": "   ",
                }
            )
        )

        assert result["is_error"] is True
        assert "rationale" in result["content"][0]["text"].lower()


# ===========================================================================
# 5. Forbidden fields
# ===========================================================================


class TestForbiddenFields:
    """Validation: safety-critical fields cannot be modified."""

    def test_requires_approval_forbidden(self):
        """Attempting to change requires_approval should return an error."""
        result = _run(
            propose_skill_change.handler(
                {
                    "skill_id": "daily_spend_analysis",
                    "proposed_changes": {
                        "requires_approval": False,
                        "output_format": "Updated format.",
                    },
                    "rationale": "Want to bypass approval.",
                }
            )
        )

        assert result["is_error"] is True
        text = result["content"][0]["text"]
        assert "requires_approval" in text.lower()

    def test_manages_forbidden(self):
        """Attempting to change manages should return an error."""
        result = _run(
            propose_skill_change.handler(
                {
                    "skill_id": "daily_spend_analysis",
                    "proposed_changes": {"manages": ["performance_media_buyer"]},
                    "rationale": "Self-promote to manager.",
                }
            )
        )

        assert result["is_error"] is True
        assert "manages" in result["content"][0]["text"].lower()


# ===========================================================================
# 6. Unknown fields
# ===========================================================================


class TestUnknownFields:
    """Validation: unrecognized fields are rejected."""

    def test_unknown_field_returns_error(self):
        """A field not in ALLOWED_FIELDS should return an error."""
        result = _run(
            propose_skill_change.handler(
                {
                    "skill_id": "daily_spend_analysis",
                    "proposed_changes": {"secret_backdoor": "evil value"},
                    "rationale": "Trying to inject unknown fields.",
                }
            )
        )

        assert result["is_error"] is True
        text = result["content"][0]["text"]
        assert "unknown" in text.lower() or "Unknown" in text


# ===========================================================================
# 7. Evidence memory IDs
# ===========================================================================


class TestEvidenceMemoryIds:
    """evidence_memory_ids should be passed through to pending proposals."""

    def test_evidence_ids_in_pending_proposal(self):
        """Evidence memory IDs should appear in the queued proposal."""
        _run(
            propose_skill_change.handler(
                {
                    "skill_id": "daily_spend_analysis",
                    "proposed_changes": {
                        "business_guidance": "Updated guidance.",
                    },
                    "rationale": "Supported by past observations.",
                    "evidence_memory_ids": [42, 99, 137],
                }
            )
        )

        proposals = get_pending_proposals()
        assert len(proposals) == 1
        evidence = proposals[0]["action_params"].get("evidence_memory_ids", [])
        assert evidence == [42, 99, 137]


# ===========================================================================
# 8. get_pending_proposals returns and clears
# ===========================================================================


class TestGetPendingProposals:
    """get_pending_proposals should return proposals and clear the list."""

    def test_returns_and_clears(self):
        """After calling get_pending_proposals, the list should be empty."""
        _run(
            propose_skill_change.handler(
                {
                    "skill_id": "test_skill",
                    "proposed_changes": {"description": "Updated."},
                    "rationale": "Testing get.",
                }
            )
        )

        proposals = get_pending_proposals()
        assert len(proposals) == 1

        # Second call should return empty
        proposals_again = get_pending_proposals()
        assert len(proposals_again) == 0


# ===========================================================================
# 9. clear_pending_proposals
# ===========================================================================


class TestClearPendingProposals:
    """clear_pending_proposals should discard all proposals."""

    def test_clear_removes_proposals(self):
        """clear_pending_proposals should leave the list empty."""
        _run(
            propose_skill_change.handler(
                {
                    "skill_id": "test_skill",
                    "proposed_changes": {"description": "Updated."},
                    "rationale": "Testing clear.",
                }
            )
        )

        clear_pending_proposals()

        proposals = get_pending_proposals()
        assert len(proposals) == 0


# ===========================================================================
# 10. Multiple proposals accumulate
# ===========================================================================


class TestMultipleProposals:
    """Multiple proposals should accumulate before being collected."""

    def test_multiple_proposals_accumulate(self):
        """Calling propose_skill_change multiple times should accumulate proposals."""
        _run(
            propose_skill_change.handler(
                {
                    "skill_id": "skill_a",
                    "proposed_changes": {"description": "First update."},
                    "rationale": "First reason.",
                }
            )
        )

        _run(
            propose_skill_change.handler(
                {
                    "skill_id": "skill_b",
                    "proposed_changes": {"output_format": "Second update."},
                    "rationale": "Second reason.",
                }
            )
        )

        _run(
            propose_skill_change.handler(
                {
                    "proposed_changes": {
                        "name": "New Skill C",
                        "description": "Brand new skill.",
                        "category": "analysis",
                        "system_supplement": "You are a new analyzer.",
                        "prompt_template": "Analyze: {data}",
                        "output_format": "Summary.",
                        "business_guidance": "Be thorough.",
                    },
                    "rationale": "Third reason.",
                }
            )
        )

        proposals = get_pending_proposals()
        assert len(proposals) == 3

        # Verify mix of modify and create
        types = [p["action_params"]["proposal_type"] for p in proposals]
        assert types.count("modify") == 2
        assert types.count("create") == 1
