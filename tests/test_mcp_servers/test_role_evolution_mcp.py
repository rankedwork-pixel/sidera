"""Tests for propose_role_change MCP tool in src.mcp_servers.evolution.

Covers input validation (missing/empty fields, forbidden fields, unknown
fields, non-manager rejection, missing proposer context), successful
proposals (new role + modification), and the shared pending-proposals
lifecycle (propose_role_change and propose_skill_change share the same queue).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_servers.evolution import (
    clear_pending_proposals,
    clear_proposer_context,
    get_pending_proposals,
    propose_role_change,
    propose_skill_change,
    set_proposer_context,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine synchronously for testing."""
    return asyncio.run(coro)


def _valid_new_role_args(**overrides):
    """Return valid args for proposing a new role."""
    base = {
        "proposed_changes": {
            "name": "DevOps Engineer",
            "description": "Handles infrastructure monitoring and deployments.",
            "persona": "You are a DevOps engineer focused on reliability.",
        },
        "rationale": "We need dedicated infrastructure monitoring coverage.",
    }
    base.update(overrides)
    return base


def _mock_manager_registry():
    """Return a mock registry where head_of_it IS a manager."""
    registry = MagicMock()
    role = MagicMock()
    role.manages = ("sub_role_1",)
    role.department_id = "it"
    registry.get_role.return_value = role
    return registry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_state():
    """Ensure pending proposals and proposer context are clean."""
    clear_pending_proposals()
    clear_proposer_context()
    # Set a valid proposer context by default (head_of_it in IT department)
    set_proposer_context("head_of_it", "it")
    yield
    clear_pending_proposals()
    clear_proposer_context()


# ---------------------------------------------------------------------------
# Valid new role proposal
# ---------------------------------------------------------------------------


class TestValidNewRoleProposal:
    """A valid new role proposal should be queued successfully."""

    def test_returns_success_message(self):
        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=_mock_manager_registry(),
        ):
            result = _run(propose_role_change.handler(_valid_new_role_args()))
        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "Role proposal queued" in text
        assert "Create new role" in text

    def test_stored_in_pending_proposals(self):
        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=_mock_manager_registry(),
        ):
            _run(propose_role_change.handler(_valid_new_role_args()))
        proposals = get_pending_proposals()
        assert len(proposals) == 1
        assert proposals[0]["action_type"] == "role_proposal"
        assert proposals[0]["action_params"]["proposal_type"] == "create"
        assert proposals[0]["action_params"]["department_id"] == "it"


# ---------------------------------------------------------------------------
# Valid modification proposal
# ---------------------------------------------------------------------------


class TestValidModificationProposal:
    """A valid modification proposal should be queued."""

    def test_modify_returns_success(self):
        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=_mock_manager_registry(),
        ):
            result = _run(
                propose_role_change.handler(
                    {
                        "role_id": "devops_engineer",
                        "proposed_changes": {"persona": "Updated persona."},
                        "rationale": "Better alignment with responsibilities.",
                    }
                )
            )
        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "Modify role" in text
        assert "devops_engineer" in text

    def test_modify_stored_as_modify_type(self):
        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=_mock_manager_registry(),
        ):
            _run(
                propose_role_change.handler(
                    {
                        "role_id": "devops_engineer",
                        "proposed_changes": {"description": "Updated desc"},
                        "rationale": "Better clarity.",
                    }
                )
            )
        proposals = get_pending_proposals()
        assert len(proposals) == 1
        assert proposals[0]["action_params"]["proposal_type"] == "modify"
        assert proposals[0]["action_params"]["role_id"] == "devops_engineer"


# ---------------------------------------------------------------------------
# Missing / empty fields
# ---------------------------------------------------------------------------


class TestMissingProposedChanges:
    """Missing or empty proposed_changes should return an error."""

    def test_missing_proposed_changes(self):
        result = _run(propose_role_change.handler({"rationale": "No changes provided."}))
        assert result.get("is_error") is True

    def test_empty_proposed_changes(self):
        result = _run(
            propose_role_change.handler({"proposed_changes": {}, "rationale": "Empty changes."})
        )
        assert result.get("is_error") is True


class TestMissingRationale:
    """Missing or whitespace-only rationale should return an error."""

    def test_missing_rationale(self):
        result = _run(
            propose_role_change.handler(
                {"proposed_changes": {"name": "X", "description": "Y", "persona": "Z"}}
            )
        )
        assert result.get("is_error") is True

    def test_whitespace_rationale(self):
        result = _run(
            propose_role_change.handler(
                {
                    "proposed_changes": {"name": "X", "description": "Y", "persona": "Z"},
                    "rationale": "   ",
                }
            )
        )
        assert result.get("is_error") is True


# ---------------------------------------------------------------------------
# Forbidden fields
# ---------------------------------------------------------------------------


class TestForbiddenFields:
    """Forbidden fields (manages, is_active, etc.) must be rejected."""

    def test_manages_forbidden(self):
        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=_mock_manager_registry(),
        ):
            args = _valid_new_role_args()
            args["proposed_changes"]["manages"] = ("sub1",)
            result = _run(propose_role_change.handler(args))
        assert result.get("is_error") is True

    def test_is_active_forbidden(self):
        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=_mock_manager_registry(),
        ):
            args = _valid_new_role_args()
            args["proposed_changes"]["is_active"] = True
            result = _run(propose_role_change.handler(args))
        assert result.get("is_error") is True


# ---------------------------------------------------------------------------
# Required fields for new role
# ---------------------------------------------------------------------------


class TestRequiredFieldsForNewRole:
    """New roles must include name, description, and persona."""

    def test_missing_persona(self):
        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=_mock_manager_registry(),
        ):
            result = _run(
                propose_role_change.handler(
                    {
                        "proposed_changes": {"name": "X", "description": "Y"},
                        "rationale": "Missing persona.",
                    }
                )
            )
        assert result.get("is_error") is True


# ---------------------------------------------------------------------------
# Proposer context
# ---------------------------------------------------------------------------


class TestProposerContextRequired:
    """propose_role_change requires a valid proposer context."""

    def test_no_proposer_context(self):
        """Without proposer context set, the tool returns an error."""
        clear_proposer_context()
        result = _run(propose_role_change.handler(_valid_new_role_args()))
        assert result.get("is_error") is True
        text = result["content"][0]["text"]
        assert "identity" in text.lower() or "context" in text.lower()


# ---------------------------------------------------------------------------
# Non-manager rejection via registry
# ---------------------------------------------------------------------------


class TestNonManagerRejected:
    """Non-manager roles should be rejected when registry is available."""

    def test_non_manager_rejected(self):
        """A role without manages is rejected."""
        registry = MagicMock()
        role = MagicMock()
        role.manages = ()  # Not a manager
        registry.get_role.return_value = role

        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=registry,
        ):
            result = _run(propose_role_change.handler(_valid_new_role_args()))
        assert result.get("is_error") is True
        text = result["content"][0]["text"]
        assert "manager" in text.lower()


# ---------------------------------------------------------------------------
# Shared queue with skill proposals
# ---------------------------------------------------------------------------


class TestSharedQueueWithSkillProposals:
    """Role proposals and skill proposals share the same pending queue."""

    def test_both_types_in_same_queue(self):
        """Skill + role proposals accumulate in the same list."""
        # Propose a skill (no registry needed for skill proposals)
        _run(
            propose_skill_change.handler(
                {
                    "skill_id": "daily_spend_analysis",
                    "proposed_changes": {"business_guidance": "Updated guidance"},
                    "rationale": "Better guidance needed.",
                }
            )
        )
        # Propose a role (needs mock registry)
        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=_mock_manager_registry(),
        ):
            _run(propose_role_change.handler(_valid_new_role_args()))

        proposals = get_pending_proposals()
        assert len(proposals) == 2
        types = {p["action_type"] for p in proposals}
        assert "skill_proposal" in types
        assert "role_proposal" in types


# ---------------------------------------------------------------------------
# Evidence memory IDs
# ---------------------------------------------------------------------------


class TestEvidenceMemoryIds:
    """evidence_memory_ids should be passed through to the pending proposal."""

    def test_evidence_ids_in_proposal(self):
        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=_mock_manager_registry(),
        ):
            args = _valid_new_role_args()
            args["evidence_memory_ids"] = [10, 20, 30]
            _run(propose_role_change.handler(args))

        proposals = get_pending_proposals()
        assert len(proposals) == 1
        assert proposals[0]["action_params"]["evidence_memory_ids"] == [10, 20, 30]


# ---------------------------------------------------------------------------
# get_pending_proposals clears after retrieval
# ---------------------------------------------------------------------------


class TestGetPendingProposalsClearsQueue:
    """get_pending_proposals() returns and clears the queue."""

    def test_clears_after_get(self):
        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=_mock_manager_registry(),
        ):
            _run(propose_role_change.handler(_valid_new_role_args()))
        first = get_pending_proposals()
        assert len(first) == 1
        second = get_pending_proposals()
        assert len(second) == 0
