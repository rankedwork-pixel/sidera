"""Tests for src.skills.role_evolution -- role evolution validation, diff, and execution.

Covers:
- validate_role_proposal: empty input, forbidden fields, unknown fields,
  missing required fields for new roles, valid create/modify proposals,
  non-manager rejection, wrong department rejection.
- generate_role_diff: new role format, modification format.
- format_role_proposal_as_recommendation: create vs modify paths, dict structure.
- execute_role_proposal: create + manages auto-update, modify, validation failure.
- Auto-execute hard block: role_proposal action type always blocked.
- Constants: ROLE_FORBIDDEN_FIELDS, ROLE_ALLOWED_FIELDS exist.
- ActionType enum contains ROLE_PROPOSAL.

All DB operations are mocked; no database connection needed.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.skills.role_evolution import (
    REQUIRED_NEW_ROLE_FIELDS,
    ROLE_ALLOWED_FIELDS,
    ROLE_FORBIDDEN_FIELDS,
    execute_role_proposal,
    format_role_proposal_as_recommendation,
    generate_role_diff,
    validate_role_proposal,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine synchronously for testing."""
    return asyncio.run(coro)


def _valid_new_role(**overrides):
    """Return a minimal valid new-role proposal dict."""
    base = {
        "name": "DevOps Engineer",
        "description": "Handles infrastructure monitoring and deployments.",
        "persona": "You are a DevOps engineer focused on reliability and automation.",
    }
    base.update(overrides)
    return base


def _mock_registry(*, role_exists=False, role_manages=("sub1",), department_id="it"):
    """Return a mock SkillRegistry."""
    registry = MagicMock()
    if role_exists:
        role = MagicMock()
        role.manages = role_manages
        role.department_id = department_id
        registry.get_role.return_value = role
    else:
        registry.get_role.return_value = None
    return registry


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify module-level constants are well-formed."""

    def test_forbidden_fields_is_frozenset(self):
        assert isinstance(ROLE_FORBIDDEN_FIELDS, frozenset)

    def test_allowed_fields_is_frozenset(self):
        assert isinstance(ROLE_ALLOWED_FIELDS, frozenset)

    def test_required_new_role_fields_is_frozenset(self):
        assert isinstance(REQUIRED_NEW_ROLE_FIELDS, frozenset)

    def test_no_overlap_between_forbidden_and_allowed(self):
        overlap = ROLE_FORBIDDEN_FIELDS & ROLE_ALLOWED_FIELDS
        assert not overlap, f"Overlap: {overlap}"

    def test_manages_is_forbidden(self):
        assert "manages" in ROLE_FORBIDDEN_FIELDS

    def test_is_active_is_forbidden(self):
        assert "is_active" in ROLE_FORBIDDEN_FIELDS

    def test_name_is_allowed(self):
        assert "name" in ROLE_ALLOWED_FIELDS

    def test_persona_is_allowed(self):
        assert "persona" in ROLE_ALLOWED_FIELDS

    def test_required_fields_subset_of_allowed(self):
        assert REQUIRED_NEW_ROLE_FIELDS.issubset(ROLE_ALLOWED_FIELDS)


class TestActionTypeEnum:
    """Verify ActionType enum contains ROLE_PROPOSAL."""

    def test_role_proposal_exists(self):
        from src.models.schema import ActionType

        assert hasattr(ActionType, "ROLE_PROPOSAL")
        assert ActionType.ROLE_PROPOSAL.value == "role_proposal"


# ---------------------------------------------------------------------------
# validate_role_proposal
# ---------------------------------------------------------------------------


class TestValidateRoleProposal:
    """Tests for validate_role_proposal()."""

    def test_forbidden_field_manages(self):
        """Proposing 'manages' field is rejected."""
        ok, err = validate_role_proposal(
            {"name": "X", "manages": ("sub1",)},
            is_new=True,
            proposer_role_id="head_of_it",
            proposer_department_id="it",
        )
        assert ok is False
        assert "manages" in err.lower()

    def test_forbidden_field_is_active(self):
        """Proposing 'is_active' field is rejected."""
        ok, err = validate_role_proposal(
            {"name": "X", "is_active": True},
            is_new=True,
            proposer_role_id="head_of_it",
            proposer_department_id="it",
        )
        assert ok is False
        assert "is_active" in err.lower()

    def test_unknown_field_rejected(self):
        """Fields not in ROLE_ALLOWED_FIELDS are rejected."""
        ok, err = validate_role_proposal(
            {"name": "X", "nonexistent_field": "val"},
            is_new=True,
            proposer_role_id="head_of_it",
            proposer_department_id="it",
        )
        assert ok is False
        assert "unknown" in err.lower() or "nonexistent" in err.lower()

    def test_new_role_missing_required_fields(self):
        """New roles must include name, description, persona."""
        ok, err = validate_role_proposal(
            {"name": "DevOps"},  # missing description, persona
            is_new=True,
            proposer_role_id="head_of_it",
            proposer_department_id="it",
        )
        assert ok is False
        assert "requires" in err.lower()

    def test_valid_new_role(self):
        """Returns (True, '') for a valid new-role proposal."""
        ok, err = validate_role_proposal(
            _valid_new_role(),
            is_new=True,
            proposer_role_id="head_of_it",
            proposer_department_id="it",
        )
        assert ok is True
        assert err == ""

    def test_valid_modification(self):
        """Modifying a single allowed field passes validation."""
        registry = _mock_registry(role_exists=True, department_id="it")
        ok, err = validate_role_proposal(
            {"persona": "Updated persona text."},
            is_new=False,
            proposer_role_id="head_of_it",
            proposer_department_id="it",
            target_role_id="devops_engineer",
            registry=registry,
        )
        assert ok is True
        assert err == ""

    def test_non_manager_rejected(self):
        """A role without manages cannot propose role changes."""
        registry = MagicMock()
        proposer = MagicMock()
        proposer.manages = ()  # Not a manager
        registry.get_role.return_value = proposer

        ok, err = validate_role_proposal(
            _valid_new_role(),
            is_new=True,
            proposer_role_id="regular_role",
            proposer_department_id="it",
            registry=registry,
        )
        assert ok is False
        assert "manager" in err.lower()

    def test_wrong_department_modify(self):
        """Cannot modify a role in a different department."""
        registry = MagicMock()
        # Proposer is a manager in IT
        proposer = MagicMock()
        proposer.manages = ("sub1",)
        proposer.department_id = "it"
        # Target role is in marketing
        target = MagicMock()
        target.department_id = "marketing"
        registry.get_role.side_effect = lambda rid: proposer if rid == "head_of_it" else target

        ok, err = validate_role_proposal(
            {"description": "Updated"},
            is_new=False,
            proposer_role_id="head_of_it",
            proposer_department_id="it",
            target_role_id="marketing_analyst",
            registry=registry,
        )
        assert ok is False
        assert "department" in err.lower()

    def test_invalid_delegation_model(self):
        """Invalid delegation_model values are rejected."""
        ok, err = validate_role_proposal(
            _valid_new_role(delegation_model="turbo"),
            is_new=True,
            proposer_role_id="head_of_it",
            proposer_department_id="it",
        )
        assert ok is False
        assert "delegation_model" in err.lower()

    def test_valid_delegation_model(self):
        """Valid delegation_model 'fast' passes validation."""
        ok, err = validate_role_proposal(
            _valid_new_role(delegation_model="fast"),
            is_new=True,
            proposer_role_id="head_of_it",
            proposer_department_id="it",
        )
        assert ok is True


# ---------------------------------------------------------------------------
# generate_role_diff
# ---------------------------------------------------------------------------


class TestGenerateRoleDiff:
    """Tests for generate_role_diff()."""

    def test_new_role_diff(self):
        """New role shows all proposed fields."""
        diff = generate_role_diff(None, _valid_new_role())
        assert "new role" in diff.lower()
        assert "DevOps Engineer" in diff

    def test_modification_diff(self):
        """Modification shows before/after for changed fields."""
        existing = {"persona": "Old persona", "description": "Old desc"}
        proposed = {"persona": "New persona"}
        diff = generate_role_diff(existing, proposed)
        assert "Old persona" in diff
        assert "New persona" in diff

    def test_diff_truncates_long_values(self):
        """Values longer than 200 chars are truncated in the diff."""
        long_val = "x" * 300
        diff = generate_role_diff(None, {"name": "Test", "persona": long_val})
        assert "..." in diff
        assert len(diff) < 1000  # Reasonable size


# ---------------------------------------------------------------------------
# format_role_proposal_as_recommendation
# ---------------------------------------------------------------------------


class TestFormatRoleProposalAsRecommendation:
    """Tests for format_role_proposal_as_recommendation()."""

    def test_create_proposal_structure(self):
        """Create proposal has correct action_type and proposal_type."""
        rec = format_role_proposal_as_recommendation(
            proposal={"proposed_changes": _valid_new_role()},
            rationale="Need monitoring coverage.",
            evidence_memory_ids=[],
            diff="(diff text)",
            proposer_role_id="head_of_it",
            department_id="it",
        )
        assert rec["action_type"] == "role_proposal"
        assert rec["action_params"]["proposal_type"] == "create"
        assert rec["action_params"]["department_id"] == "it"
        assert rec["action_params"]["proposer_role_id"] == "head_of_it"
        # Should have a description
        assert len(rec["description"]) > 0

    def test_modify_proposal_structure(self):
        """Modify proposal includes role_id and proposal_type 'modify'."""
        rec = format_role_proposal_as_recommendation(
            proposal={
                "role_id": "devops_engineer",
                "proposed_changes": {"persona": "Updated persona"},
            },
            rationale="Better persona.",
            evidence_memory_ids=[42],
            diff="(diff text)",
            proposer_role_id="head_of_it",
            department_id="it",
        )
        assert rec["action_params"]["proposal_type"] == "modify"
        assert rec["action_params"]["role_id"] == "devops_engineer"

    def test_evidence_memory_ids_included(self):
        """Evidence memory IDs are passed through."""
        rec = format_role_proposal_as_recommendation(
            proposal={"proposed_changes": _valid_new_role()},
            rationale="test",
            evidence_memory_ids=[1, 2, 3],
            diff="",
            proposer_role_id="head_of_it",
            department_id="it",
        )
        assert rec["action_params"]["evidence_memory_ids"] == [1, 2, 3]


# ---------------------------------------------------------------------------
# execute_role_proposal
# ---------------------------------------------------------------------------


class TestExecuteRoleProposal:
    """Tests for execute_role_proposal() -- async DB operations."""

    def test_create_path(self):
        """Create proposal calls create_org_role and returns ok=True."""
        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_create = AsyncMock(return_value=MagicMock())

        with (
            patch(
                "src.db.session.get_db_session",
                return_value=mock_ctx,
            ),
            patch(
                "src.db.service.create_org_role",
                mock_create,
            ),
            patch(
                "src.skills.role_evolution._update_manager_manages",
                new_callable=AsyncMock,
            ) as mock_update_manages,
        ):
            result = _run(
                execute_role_proposal(
                    {
                        "proposal_type": "create",
                        "department_id": "it",
                        "proposer_role_id": "head_of_it",
                        "role_fields": _valid_new_role(),
                    }
                )
            )

        assert result["ok"] is True
        assert result["proposal_type"] == "create"
        assert "role_id" in result
        mock_create.assert_awaited_once()
        mock_update_manages.assert_awaited_once()

    def test_modify_path(self):
        """Modify proposal calls update_org_role and returns ok=True."""
        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_update = AsyncMock(return_value=MagicMock())

        with (
            patch(
                "src.db.session.get_db_session",
                return_value=mock_ctx,
            ),
            patch(
                "src.db.service.update_org_role",
                mock_update,
            ),
        ):
            result = _run(
                execute_role_proposal(
                    {
                        "proposal_type": "modify",
                        "role_id": "devops_engineer",
                        "changes": {"persona": "Updated persona"},
                    }
                )
            )

        assert result["ok"] is True
        assert result["proposal_type"] == "modify"
        mock_update.assert_awaited_once()

    def test_create_derives_role_id_from_name(self):
        """Create proposal derives role_id from name (lowercase, underscored)."""
        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "src.db.session.get_db_session",
                return_value=mock_ctx,
            ),
            patch(
                "src.db.service.create_org_role",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
            patch(
                "src.skills.role_evolution._update_manager_manages",
                new_callable=AsyncMock,
            ),
        ):
            result = _run(
                execute_role_proposal(
                    {
                        "proposal_type": "create",
                        "department_id": "it",
                        "proposer_role_id": "head_of_it",
                        "role_fields": _valid_new_role(name="Senior DevOps Lead"),
                    }
                )
            )

        assert result["role_id"] == "senior_devops_lead"

    def test_create_revalidation_failure(self):
        """Create path re-validates and returns error on forbidden fields."""
        result = _run(
            execute_role_proposal(
                {
                    "proposal_type": "create",
                    "department_id": "it",
                    "proposer_role_id": "head_of_it",
                    "role_fields": {"name": "Bad", "manages": ("sub1",)},
                }
            )
        )
        assert result["ok"] is False
        assert "error" in result

    def test_unknown_proposal_type_raises(self):
        """Unknown proposal_type raises ValueError."""
        with pytest.raises(ValueError, match="Unknown proposal_type"):
            _run(execute_role_proposal({"proposal_type": "delete"}))


# ---------------------------------------------------------------------------
# Auto-execute hard block
# ---------------------------------------------------------------------------


class TestAutoExecuteHardBlock:
    """Verify role_proposal action type is hard-blocked from auto-execute."""

    @pytest.mark.asyncio
    async def test_role_proposal_blocked_even_when_enabled(self):
        """should_auto_execute returns False for role_proposal action type."""
        from src.skills.auto_execute import (
            AutoExecuteRule,
            AutoExecuteRuleSet,
            should_auto_execute,
        )

        settings = SimpleNamespace(
            auto_execute_enabled=True,
            auto_execute_max_per_day=20,
            max_budget_change_ratio=1.5,
        )
        rule = AutoExecuteRule(
            id="match_all",
            description="Would match any role_proposal",
            action_types=("role_proposal",),
        )
        ruleset = AutoExecuteRuleSet(role_id="head_of_it", rules=(rule,))
        rec = {
            "action_type": "role_proposal",
            "action_params": {"proposal_type": "create"},
        }

        decision = await should_auto_execute(rec, "head_of_it", ruleset, settings)
        assert decision.should_auto_execute is False
        assert any(
            "human approval" in r.lower() or "blocked" in r.lower() for r in decision.reasons
        )
