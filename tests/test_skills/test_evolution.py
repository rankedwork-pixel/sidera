"""Tests for src.skills.evolution -- skill evolution validation, diff, and execution.

Covers:
- validate_skill_proposal: empty input, forbidden fields, unknown fields,
  missing required fields for new skills, invalid model/max_turns/category,
  and valid proposals.
- generate_skill_diff: new skill format, modification format, truncation.
- format_proposal_as_recommendation: create vs modify paths, dict structure.
- execute_skill_proposal: create, modify, modify-with-fallback-create,
  unknown proposal_type, and re-validation defense-in-depth.
- Auto-execute hard block: skill_proposal action type always blocked.
- Constants: FORBIDDEN_FIELDS, ALLOWED_FIELDS, VALID_CATEGORIES exist.
- ActionType enum contains SKILL_PROPOSAL.

All DB operations are mocked; no database connection needed.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.skills.evolution import (
    ALLOWED_FIELDS,
    FORBIDDEN_FIELDS,
    REQUIRED_NEW_SKILL_FIELDS,
    VALID_MODELS,
    execute_skill_proposal,
    format_proposal_as_recommendation,
    generate_skill_diff,
    validate_skill_proposal,
)

# =====================================================================
# Helpers
# =====================================================================


def _run(coro):
    """Run an async coroutine synchronously for testing."""
    return asyncio.run(coro)


def _valid_new_skill(**overrides) -> dict:
    """Return a minimal valid proposed_changes dict for a new skill."""
    defaults = {
        "name": "Test Skill",
        "description": "A test skill for unit tests.",
        "category": "analysis",
        "system_supplement": "You are a test analyzer.",
        "prompt_template": "Analyze {topic}.",
        "output_format": "Return JSON with key findings.",
        "business_guidance": "Focus on cost efficiency.",
    }
    defaults.update(overrides)
    return defaults


# =====================================================================
# Constants
# =====================================================================


class TestConstants:
    """Verify that safety constants exist and contain expected values."""

    def test_forbidden_fields_is_frozenset(self):
        """FORBIDDEN_FIELDS is a frozenset."""
        assert isinstance(FORBIDDEN_FIELDS, frozenset)

    def test_forbidden_fields_contains_requires_approval(self):
        """requires_approval is in FORBIDDEN_FIELDS."""
        assert "requires_approval" in FORBIDDEN_FIELDS

    def test_forbidden_fields_contains_manages(self):
        """manages is in FORBIDDEN_FIELDS."""
        assert "manages" in FORBIDDEN_FIELDS

    def test_forbidden_fields_contains_is_active(self):
        """is_active is in FORBIDDEN_FIELDS."""
        assert "is_active" in FORBIDDEN_FIELDS

    def test_forbidden_fields_contains_id(self):
        """id is in FORBIDDEN_FIELDS."""
        assert "id" in FORBIDDEN_FIELDS

    def test_forbidden_fields_contains_created_by(self):
        """created_by is in FORBIDDEN_FIELDS."""
        assert "created_by" in FORBIDDEN_FIELDS

    def test_allowed_fields_is_frozenset(self):
        """ALLOWED_FIELDS is a frozenset."""
        assert isinstance(ALLOWED_FIELDS, frozenset)

    def test_allowed_fields_contains_name(self):
        """name is in ALLOWED_FIELDS."""
        assert "name" in ALLOWED_FIELDS

    def test_allowed_fields_contains_model(self):
        """model is in ALLOWED_FIELDS."""
        assert "model" in ALLOWED_FIELDS

    def test_allowed_fields_contains_max_turns(self):
        """max_turns is in ALLOWED_FIELDS."""
        assert "max_turns" in ALLOWED_FIELDS

    def test_allowed_fields_contains_references(self):
        """references is in ALLOWED_FIELDS."""
        assert "references" in ALLOWED_FIELDS

    def test_no_overlap_between_forbidden_and_allowed(self):
        """FORBIDDEN_FIELDS and ALLOWED_FIELDS must not overlap."""
        overlap = FORBIDDEN_FIELDS & ALLOWED_FIELDS
        assert overlap == set(), f"Overlap detected: {overlap}"

    def test_required_new_skill_fields_subset_of_allowed(self):
        """All required new-skill fields are in ALLOWED_FIELDS."""
        assert REQUIRED_NEW_SKILL_FIELDS <= ALLOWED_FIELDS


class TestActionTypeEnum:
    """Verify ActionType enum contains SKILL_PROPOSAL."""

    def test_skill_proposal_in_action_type(self):
        """SKILL_PROPOSAL exists in the ActionType enum."""
        from src.models.schema import ActionType

        assert hasattr(ActionType, "SKILL_PROPOSAL")
        assert ActionType.SKILL_PROPOSAL.value == "skill_proposal"


# =====================================================================
# validate_skill_proposal
# =====================================================================


class TestValidateSkillProposal:
    """Tests for validate_skill_proposal()."""

    def test_valid_modification(self):
        """Returns (True, '') for a valid modification proposal."""
        ok, err = validate_skill_proposal({"name": "New Name"})
        assert ok is True
        assert err == ""

    def test_valid_new_skill(self):
        """Returns (True, '') for a valid new-skill proposal."""
        ok, err = validate_skill_proposal(_valid_new_skill(), is_new=True)
        assert ok is True
        assert err == ""

    def test_empty_proposed_changes(self):
        """Returns (False, error) when proposed_changes is empty."""
        ok, err = validate_skill_proposal({})
        assert ok is False
        assert "empty" in err.lower()

    def test_forbidden_field_requires_approval(self):
        """Rejects requires_approval as a safety-critical field."""
        ok, err = validate_skill_proposal({"requires_approval": True})
        assert ok is False
        assert "requires_approval" in err

    def test_forbidden_field_manages(self):
        """Rejects manages as a safety-critical field."""
        ok, err = validate_skill_proposal({"manages": ["role_a"]})
        assert ok is False
        assert "manages" in err

    def test_forbidden_field_is_active(self):
        """Rejects is_active as a safety-critical field."""
        ok, err = validate_skill_proposal({"is_active": False})
        assert ok is False
        assert "is_active" in err

    def test_forbidden_field_created_by(self):
        """Rejects created_by as metadata field."""
        ok, err = validate_skill_proposal({"created_by": "agent"})
        assert ok is False
        assert "created_by" in err

    def test_forbidden_field_id(self):
        """Rejects id as identity field."""
        ok, err = validate_skill_proposal({"id": 42})
        assert ok is False
        assert "id" in err

    def test_multiple_forbidden_fields_all_listed(self):
        """Error message lists all forbidden fields present."""
        ok, err = validate_skill_proposal(
            {
                "requires_approval": True,
                "is_active": False,
            }
        )
        assert ok is False
        assert "is_active" in err
        assert "requires_approval" in err

    def test_unknown_field_rejected(self):
        """Rejects fields not in ALLOWED_FIELDS."""
        ok, err = validate_skill_proposal({"totally_made_up_field": "value"})
        assert ok is False
        assert "Unknown fields" in err
        assert "totally_made_up_field" in err

    def test_missing_required_fields_for_new_skill(self):
        """New skill missing required fields returns descriptive error."""
        ok, err = validate_skill_proposal(
            {"name": "Partial Skill"},
            is_new=True,
        )
        assert ok is False
        assert "New skill requires" in err
        # Should mention at least one missing field
        assert "description" in err or "category" in err

    def test_missing_all_required_fields_for_new_skill(self):
        """New skill with only an optional field lists all missing required fields."""
        ok, err = validate_skill_proposal(
            {"tags": ["test"]},
            is_new=True,
        )
        assert ok is False
        for field in REQUIRED_NEW_SKILL_FIELDS:
            assert field in err

    def test_invalid_model(self):
        """Rejects model values not in VALID_MODELS."""
        ok, err = validate_skill_proposal({"model": "gpt-4"})
        assert ok is False
        assert "Invalid model" in err
        assert "gpt-4" in err

    def test_valid_models_accepted(self):
        """All valid models are accepted."""
        for model in VALID_MODELS:
            ok, err = validate_skill_proposal({"model": model})
            assert ok is True, f"Model '{model}' should be valid but got: {err}"

    def test_invalid_max_turns_not_int(self):
        """Rejects max_turns that is not an integer."""
        ok, err = validate_skill_proposal({"max_turns": 5.5})
        assert ok is False
        assert "max_turns" in err

    def test_invalid_max_turns_too_low(self):
        """Rejects max_turns below 1."""
        ok, err = validate_skill_proposal({"max_turns": 0})
        assert ok is False
        assert "max_turns" in err

    def test_invalid_max_turns_too_high(self):
        """Rejects max_turns above 50."""
        ok, err = validate_skill_proposal({"max_turns": 51})
        assert ok is False
        assert "max_turns" in err

    def test_valid_max_turns_boundaries(self):
        """max_turns of 1 and 50 are both valid."""
        ok1, _ = validate_skill_proposal({"max_turns": 1})
        ok50, _ = validate_skill_proposal({"max_turns": 50})
        assert ok1 is True
        assert ok50 is True

    def test_invalid_category(self):
        """Rejects category values not in VALID_CATEGORIES."""
        ok, err = validate_skill_proposal({"category": "underwater_basket_weaving"})
        assert ok is False
        assert "Invalid category" in err

    def test_valid_category(self):
        """Accepts a valid category."""
        ok, err = validate_skill_proposal({"category": "analysis"})
        assert ok is True
        assert err == ""

    def test_modification_does_not_require_all_fields(self):
        """A modification (is_new=False) only needs the fields being changed."""
        ok, err = validate_skill_proposal({"description": "Updated description"})
        assert ok is True
        assert err == ""

    def test_max_turns_string_rejected(self):
        """max_turns as a string is not a valid integer."""
        ok, err = validate_skill_proposal({"max_turns": "10"})
        assert ok is False
        assert "max_turns" in err

    # --- references field validation ---

    def test_references_valid_list_of_dicts(self):
        """Valid references (list of dicts with skill_id) is accepted."""
        ok, err = validate_skill_proposal(
            {
                "references": [
                    {"skill_id": "other_skill", "relationship": "methodology"},
                ]
            }
        )
        assert ok is True
        assert err == ""

    def test_references_not_a_list(self):
        """references as a string is rejected."""
        ok, err = validate_skill_proposal({"references": "other_skill"})
        assert ok is False
        assert "list" in err.lower()

    def test_references_entry_not_a_dict(self):
        """references entries must be dicts."""
        ok, err = validate_skill_proposal({"references": ["other_skill"]})
        assert ok is False
        assert "dict" in err.lower()

    def test_references_entry_missing_skill_id(self):
        """references entries without skill_id are rejected."""
        ok, err = validate_skill_proposal({"references": [{"relationship": "methodology"}]})
        assert ok is False
        assert "skill_id" in err.lower()

    def test_references_empty_list_valid(self):
        """Empty references list (clearing references) is valid."""
        ok, err = validate_skill_proposal({"references": []})
        assert ok is True
        assert err == ""


# =====================================================================
# generate_skill_diff
# =====================================================================


class TestGenerateSkillDiff:
    """Tests for generate_skill_diff()."""

    def test_new_skill_format(self):
        """New skill (existing_skill=None) shows 'New skill: <name>' header."""
        diff = generate_skill_diff(None, {"name": "Budget Optimizer", "category": "budget"})
        assert "New skill: Budget Optimizer" in diff
        assert "category: budget" in diff

    def test_new_skill_unnamed(self):
        """New skill without name uses 'unnamed' as fallback."""
        diff = generate_skill_diff(None, {"category": "analysis"})
        assert "New skill: unnamed" in diff

    def test_modify_skill_format(self):
        """Modification shows 'Modify skill: <id>' with before/after."""
        existing = {"skill_id": "budget_opt", "description": "Old desc"}
        changes = {"description": "New desc"}
        diff = generate_skill_diff(existing, changes)
        assert "Modify skill: budget_opt" in diff
        assert "before: Old desc" in diff
        assert "after:  New desc" in diff

    def test_modify_skill_uses_id_fallback(self):
        """Falls back to 'id' key when 'skill_id' is not present."""
        existing = {"id": "fallback_id", "name": "Test"}
        changes = {"name": "Updated Name"}
        diff = generate_skill_diff(existing, changes)
        assert "Modify skill: fallback_id" in diff

    def test_modify_skill_missing_old_field(self):
        """Shows '<not set>' for fields not present in existing skill."""
        existing = {"skill_id": "test_skill"}
        changes = {"tags": ["new_tag"]}
        diff = generate_skill_diff(existing, changes)
        assert "<not set>" in diff

    def test_long_text_truncated(self):
        """Values longer than 200 characters are truncated with '...'."""
        long_text = "x" * 300
        diff = generate_skill_diff(None, {"name": "Test", "description": long_text})
        assert "..." in diff
        # The truncated value should be at most 203 chars (200 + "...")
        for line in diff.split("\n"):
            if "description:" in line:
                value_part = line.split("description: ")[1]
                assert len(value_part) <= 203

    def test_fields_sorted_alphabetically(self):
        """Fields in diff are sorted alphabetically."""
        changes = {"name": "Z Skill", "category": "analysis", "author": "agent"}
        diff = generate_skill_diff(None, changes)
        lines = [
            x.strip() for x in diff.split("\n") if x.strip() and ":" in x and "New skill" not in x
        ]
        field_names = [x.split(":")[0] for x in lines]
        assert field_names == sorted(field_names)


# =====================================================================
# format_proposal_as_recommendation
# =====================================================================


class TestFormatProposalAsRecommendation:
    """Tests for format_proposal_as_recommendation()."""

    def test_new_skill_recommendation(self):
        """New skill (no skill_id) produces create proposal_type."""
        proposal = {"proposed_changes": {"name": "New Skill", "category": "analysis"}}
        rec = format_proposal_as_recommendation(
            proposal,
            rationale="Performance gap detected.",
            diff="New skill: New Skill",
            source_skill_id="daily_performance",
        )
        assert rec["action_type"] == "skill_proposal"
        assert rec["action_params"]["proposal_type"] == "create"
        assert rec["action_params"]["skill_fields"] == {"name": "New Skill", "category": "analysis"}
        assert "skill_id" not in rec["action_params"]
        assert rec["description"] == "Performance gap detected."
        assert rec["skill_id"] == "daily_performance"
        assert rec["risk_level"] == "low"

    def test_modify_skill_recommendation(self):
        """Modification (skill_id present) produces modify proposal_type."""
        proposal = {
            "skill_id": "budget_optimizer",
            "proposed_changes": {"description": "Updated description"},
        }
        rec = format_proposal_as_recommendation(
            proposal,
            rationale="Skill outdated.",
            diff="Modify skill: budget_optimizer",
            source_skill_id="meta_analysis",
        )
        assert rec["action_params"]["proposal_type"] == "modify"
        assert rec["action_params"]["skill_id"] == "budget_optimizer"
        assert rec["action_params"]["changes"] == {"description": "Updated description"}
        assert "skill_fields" not in rec["action_params"]

    def test_action_label_for_new_skill(self):
        """Action label says 'Create skill: <name>' for new skills."""
        proposal = {"proposed_changes": {"name": "My New Skill"}}
        rec = format_proposal_as_recommendation(proposal, rationale="Needed.")
        assert rec["action"] == "Create skill: My New Skill"

    def test_action_label_for_modification(self):
        """Action label says 'Modify skill: <skill_id>' for modifications."""
        proposal = {"skill_id": "existing_skill", "proposed_changes": {"tags": ["v2"]}}
        rec = format_proposal_as_recommendation(proposal, rationale="Update tags.")
        assert rec["action"] == "Modify skill: existing_skill"

    def test_evidence_memory_ids_default(self):
        """evidence_memory_ids defaults to empty list."""
        proposal = {"proposed_changes": {"name": "X"}}
        rec = format_proposal_as_recommendation(proposal, rationale="R")
        assert rec["action_params"]["evidence_memory_ids"] == []

    def test_evidence_memory_ids_preserved(self):
        """evidence_memory_ids are passed through."""
        proposal = {"proposed_changes": {"name": "X"}}
        rec = format_proposal_as_recommendation(
            proposal,
            rationale="R",
            evidence_memory_ids=[1, 2, 3],
        )
        assert rec["action_params"]["evidence_memory_ids"] == [1, 2, 3]

    def test_diff_included_in_action_params(self):
        """The diff text is included in action_params."""
        proposal = {"proposed_changes": {"name": "X"}}
        rec = format_proposal_as_recommendation(
            proposal,
            rationale="R",
            diff="Some diff text",
        )
        assert rec["action_params"]["diff"] == "Some diff text"

    def test_projected_impact_static(self):
        """projected_impact is the static registry-load message."""
        proposal = {"proposed_changes": {"name": "X"}}
        rec = format_proposal_as_recommendation(proposal, rationale="R")
        assert "registry" in rec["projected_impact"].lower()

    def test_reasoning_matches_rationale(self):
        """Both description and reasoning are set to the rationale."""
        proposal = {"proposed_changes": {"name": "X"}}
        rec = format_proposal_as_recommendation(proposal, rationale="My reason.")
        assert rec["description"] == "My reason."
        assert rec["reasoning"] == "My reason."


# =====================================================================
# execute_skill_proposal
# =====================================================================


class TestExecuteSkillProposal:
    """Tests for execute_skill_proposal() -- async DB operations."""

    def test_create_path(self):
        """Create proposal calls create_org_skill and returns ok=True."""
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
                "src.db.service.create_org_skill",
                mock_create,
            ),
        ):
            result = _run(
                execute_skill_proposal(
                    {
                        "proposal_type": "create",
                        "skill_fields": _valid_new_skill(),
                    }
                )
            )

        assert result["ok"] is True
        assert result["proposal_type"] == "create"
        assert "skill_id" in result
        mock_create.assert_awaited_once()

    def test_create_path_derives_skill_id_from_name(self):
        """Create proposal derives skill_id from name (lowercase, underscored)."""
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
                "src.db.service.create_org_skill",
                mock_create,
            ),
        ):
            result = _run(
                execute_skill_proposal(
                    {
                        "proposal_type": "create",
                        "skill_fields": _valid_new_skill(name="Budget Optimizer"),
                    }
                )
            )

        assert result["skill_id"] == "budget_optimizer"

    def test_create_path_revalidation_failure(self):
        """Create path re-validates and returns error on invalid fields."""
        result = _run(
            execute_skill_proposal(
                {
                    "proposal_type": "create",
                    "skill_fields": {"name": "Incomplete"},  # missing required fields
                }
            )
        )
        assert result["ok"] is False
        assert "Validation failed" in result["error"]

    def test_modify_path(self):
        """Modify proposal calls update_org_skill and returns ok=True."""
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
                "src.db.service.update_org_skill",
                mock_update,
            ),
        ):
            result = _run(
                execute_skill_proposal(
                    {
                        "proposal_type": "modify",
                        "skill_id": "existing_skill",
                        "changes": {"description": "Better description"},
                    }
                )
            )

        assert result["ok"] is True
        assert result["proposal_type"] == "modify"
        assert result["skill_id"] == "existing_skill"
        assert "description" in result["changed_fields"]
        mock_update.assert_awaited_once()

    def test_modify_path_missing_skill_id(self):
        """Modify without skill_id returns error."""
        result = _run(
            execute_skill_proposal(
                {
                    "proposal_type": "modify",
                    "changes": {"description": "No skill_id"},
                }
            )
        )
        assert result["ok"] is False
        assert "skill_id required" in result["error"]

    def test_modify_fallback_to_create(self):
        """Modify path falls back to create when update returns None."""
        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_update = AsyncMock(return_value=None)
        mock_create = AsyncMock(return_value=MagicMock())

        with (
            patch(
                "src.db.session.get_db_session",
                return_value=mock_ctx,
            ),
            patch(
                "src.db.service.update_org_skill",
                mock_update,
            ),
            patch(
                "src.db.service.create_org_skill",
                mock_create,
            ),
        ):
            result = _run(
                execute_skill_proposal(
                    {
                        "proposal_type": "modify",
                        "skill_id": "nonexistent_skill",
                        "changes": {"description": "Override description"},
                    }
                )
            )

        assert result["ok"] is True
        assert result["proposal_type"] == "modify"
        mock_update.assert_awaited_once()
        mock_create.assert_awaited_once()

    def test_modify_revalidation_failure(self):
        """Modify path re-validates and returns error on forbidden fields."""
        result = _run(
            execute_skill_proposal(
                {
                    "proposal_type": "modify",
                    "skill_id": "some_skill",
                    "changes": {"requires_approval": False},
                }
            )
        )
        assert result["ok"] is False
        assert "Validation failed" in result["error"]

    def test_unknown_proposal_type_raises(self):
        """Unknown proposal_type raises ValueError."""
        with pytest.raises(ValueError, match="Unknown proposal_type"):
            _run(
                execute_skill_proposal(
                    {
                        "proposal_type": "delete",
                    }
                )
            )


# =====================================================================
# Auto-execute hard block
# =====================================================================


class TestAutoExecuteHardBlock:
    """Skill proposals must NEVER be auto-executed."""

    def test_skill_proposal_blocked_even_when_enabled(self):
        """should_auto_execute returns False for skill_proposal action type."""
        from src.skills.auto_execute import (
            AutoExecuteRule,
            AutoExecuteRuleSet,
            should_auto_execute,
        )

        settings = SimpleNamespace(
            auto_execute_enabled=True,
            auto_execute_max_per_day=100,
            max_budget_change_ratio=1.5,
        )
        # Create a rule that would match everything
        rule = AutoExecuteRule(
            id="match_all",
            description="matches anything",
            action_types=("skill_proposal",),
        )
        ruleset = AutoExecuteRuleSet(role_id="any", rules=(rule,))

        rec = {
            "action_type": "skill_proposal",
            "action_params": {"proposal_type": "create"},
        }
        decision = _run(should_auto_execute(rec, "any", ruleset, settings))
        assert decision.should_auto_execute is False
        assert any(
            "human approval" in r.lower() or "blocked" in r.lower() for r in decision.reasons
        )
