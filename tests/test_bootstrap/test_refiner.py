"""Tests for the bootstrap plan refiner."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.bootstrap.models import (
    BootstrapPlan,
    ExtractedDepartment,
    ExtractedMemory,
    ExtractedRole,
    ExtractedSkill,
    PlanConflict,
)
from src.bootstrap.refiner import (
    _apply_modifications,
    _format_plan_for_llm,
    _parse_refinement_response,
    refine_plan,
)


def _make_plan() -> BootstrapPlan:
    """Create a plan with some entities for testing."""
    return BootstrapPlan(
        departments=[
            ExtractedDepartment(id="eng", name="Engineering", description="Builds stuff"),
            ExtractedDepartment(id="sales", name="Sales", description="Sells stuff"),
        ],
        roles=[
            ExtractedRole(
                id="swe",
                name="SWE",
                department_id="eng",
                description="Codes",
                persona="Expert coder",
                manages=["qa"],
            ),
            ExtractedRole(
                id="qa",
                name="QA",
                department_id="eng",
                description="Tests",
            ),
            ExtractedRole(
                id="ae",
                name="Account Exec",
                department_id="sales",
                description="Sells",
            ),
        ],
        skills=[
            ExtractedSkill(
                id="code_review",
                name="Code Review",
                role_id="swe",
                department_id="eng",
                description="Reviews PRs",
            ),
            ExtractedSkill(
                id="test_plan",
                name="Test Plan",
                role_id="qa",
                department_id="eng",
                description="Plans tests",
            ),
        ],
        memories=[
            ExtractedMemory(
                role_id="swe",
                department_id="eng",
                memory_type="insight",
                title="Fact",
                content="Detail",
            ),
        ],
        conflicts=[
            PlanConflict(
                entity_type="department",
                entity_id="eng",
                field="description",
                values=[
                    {"source_docs": ["d1"], "value": "Builds stuff"},
                    {"source_docs": ["d2"], "value": "Makes stuff"},
                ],
                resolution="Builds stuff",
                confidence=0.5,
            ),
        ],
    )


class TestFormatPlanForLLM:
    def test_includes_departments(self):
        plan = _make_plan()
        result = _format_plan_for_llm(plan)
        assert "Departments:" in result
        assert "eng (Engineering)" in result
        assert "sales (Sales)" in result

    def test_includes_roles(self):
        plan = _make_plan()
        result = _format_plan_for_llm(plan)
        assert "Roles:" in result
        assert "swe (SWE) [dept: eng]" in result
        assert "manages: [qa]" in result
        assert "qa (QA) [dept: eng]" in result

    def test_includes_persona(self):
        plan = _make_plan()
        result = _format_plan_for_llm(plan)
        assert "Persona: Expert coder" in result

    def test_includes_skills(self):
        plan = _make_plan()
        result = _format_plan_for_llm(plan)
        assert "Skills:" in result
        assert "code_review (Code Review)" in result
        assert "[role: swe, dept: eng]" in result

    def test_includes_counts(self):
        plan = _make_plan()
        result = _format_plan_for_llm(plan)
        assert "Memories: 1" in result
        assert "Conflicts: 1" in result

    def test_empty_plan(self):
        plan = BootstrapPlan()
        result = _format_plan_for_llm(plan)
        assert "Departments:" in result
        assert "Memories: 0" in result
        assert "Conflicts: 0" in result

    def test_long_persona_truncated(self):
        plan = BootstrapPlan(
            roles=[
                ExtractedRole(
                    id="r1",
                    name="Role",
                    department_id="d1",
                    description="Desc",
                    persona="x" * 200,
                ),
            ],
        )
        result = _format_plan_for_llm(plan)
        # Persona should be truncated to 100 chars
        assert "Persona: " + "x" * 100 in result
        assert "x" * 200 not in result


class TestParseRefinementResponse:
    def test_clean_json(self):
        text = json.dumps(
            {
                "changes": [{"action": "add", "entity_type": "department", "entity_id": "ops"}],
                "explanation": "Added ops team",
            }
        )
        result = _parse_refinement_response(text)
        assert "changes" in result
        assert len(result["changes"]) == 1

    def test_json_with_markdown_fences(self):
        inner = json.dumps(
            {
                "changes": [{"action": "remove", "entity_type": "role", "entity_id": "qa"}],
            }
        )
        text = f"```json\n{inner}\n```"
        result = _parse_refinement_response(text)
        assert "changes" in result
        assert result["changes"][0]["action"] == "remove"

    def test_json_with_generic_fences(self):
        inner = json.dumps({"changes": []})
        text = f"```\n{inner}\n```"
        result = _parse_refinement_response(text)
        assert "changes" in result

    def test_invalid_json(self):
        result = _parse_refinement_response("this is not json at all")
        assert result == {}

    def test_valid_json_missing_changes_key(self):
        text = json.dumps({"modifications": [{"action": "add"}]})
        result = _parse_refinement_response(text)
        assert result == {}

    def test_empty_string(self):
        result = _parse_refinement_response("")
        assert result == {}

    def test_json_array_instead_of_object(self):
        text = json.dumps([{"action": "add"}])
        result = _parse_refinement_response(text)
        assert result == {}

    def test_whitespace_around_fences(self):
        inner = json.dumps(
            {
                "changes": [
                    {
                        "action": "modify",
                        "entity_type": "department",
                        "entity_id": "eng",
                        "fields": {"name": "Eng"},
                    }
                ],
            }
        )
        text = f"  ```json\n{inner}\n```  "
        result = _parse_refinement_response(text)
        assert "changes" in result


class TestApplyModificationsAdd:
    def test_add_department(self):
        plan = _make_plan()
        mods = {
            "changes": [
                {
                    "action": "add",
                    "entity_type": "department",
                    "entity_id": "ops",
                    "fields": {"name": "Operations", "description": "Runs things"},
                }
            ]
        }
        changes = _apply_modifications(plan, mods)
        assert any("Added department 'ops'" in c for c in changes)
        assert any(d.id == "ops" for d in plan.departments)
        assert len(plan.departments) == 3

    def test_add_role(self):
        plan = _make_plan()
        mods = {
            "changes": [
                {
                    "action": "add",
                    "entity_type": "role",
                    "entity_id": "devops",
                    "fields": {
                        "name": "DevOps",
                        "department_id": "eng",
                        "description": "Deploys",
                    },
                }
            ]
        }
        changes = _apply_modifications(plan, mods)
        assert any("Added role 'devops'" in c for c in changes)
        assert any(r.id == "devops" for r in plan.roles)

    def test_add_skill(self):
        plan = _make_plan()
        mods = {
            "changes": [
                {
                    "action": "add",
                    "entity_type": "skill",
                    "entity_id": "deploy",
                    "fields": {
                        "name": "Deploy",
                        "role_id": "swe",
                        "department_id": "eng",
                        "description": "Deploys code",
                        "category": "operations",
                    },
                }
            ]
        }
        changes = _apply_modifications(plan, mods)
        assert any("Added skill 'deploy'" in c for c in changes)
        skill = next(s for s in plan.skills if s.id == "deploy")
        assert skill.category == "operations"

    def test_add_duplicate_department(self):
        plan = _make_plan()
        mods = {
            "changes": [
                {
                    "action": "add",
                    "entity_type": "department",
                    "entity_id": "eng",
                    "fields": {"name": "Engineering"},
                }
            ]
        }
        changes = _apply_modifications(plan, mods)
        assert any("already exists" in c for c in changes)
        assert len(plan.departments) == 2  # unchanged

    def test_add_duplicate_role(self):
        plan = _make_plan()
        mods = {
            "changes": [
                {
                    "action": "add",
                    "entity_type": "role",
                    "entity_id": "swe",
                    "fields": {"name": "SWE", "department_id": "eng"},
                }
            ]
        }
        changes = _apply_modifications(plan, mods)
        assert any("already exists" in c for c in changes)

    def test_add_duplicate_skill(self):
        plan = _make_plan()
        mods = {
            "changes": [
                {
                    "action": "add",
                    "entity_type": "skill",
                    "entity_id": "code_review",
                    "fields": {"name": "Code Review", "role_id": "swe", "department_id": "eng"},
                }
            ]
        }
        changes = _apply_modifications(plan, mods)
        assert any("already exists" in c for c in changes)

    def test_add_unknown_entity_type(self):
        plan = _make_plan()
        mods = {
            "changes": [
                {
                    "action": "add",
                    "entity_type": "widget",
                    "entity_id": "w1",
                    "fields": {},
                }
            ]
        }
        changes = _apply_modifications(plan, mods)
        assert any("Unknown entity type" in c for c in changes)

    def test_add_defaults(self):
        """Add entities with minimal fields — defaults should apply."""
        plan = BootstrapPlan()
        mods = {
            "changes": [
                {
                    "action": "add",
                    "entity_type": "department",
                    "entity_id": "ops",
                    "fields": {},
                },
                {
                    "action": "add",
                    "entity_type": "role",
                    "entity_id": "admin",
                    "fields": {},
                },
                {
                    "action": "add",
                    "entity_type": "skill",
                    "entity_id": "s1",
                    "fields": {},
                },
            ]
        }
        changes = _apply_modifications(plan, mods)
        assert len(changes) == 3
        # Defaults applied
        dept = plan.departments[0]
        assert dept.name == "ops"  # falls back to entity_id
        skill = plan.skills[0]
        assert skill.category == "general"
        assert skill.model == "sonnet"


class TestApplyModificationsRemove:
    def test_remove_department_cascades(self):
        plan = _make_plan()
        mods = {"changes": [{"action": "remove", "entity_type": "department", "entity_id": "eng"}]}
        changes = _apply_modifications(plan, mods)
        assert any("Removed department 'eng'" in c for c in changes)
        assert any("cascaded 2 roles" in c for c in changes)
        # eng gone
        assert not any(d.id == "eng" for d in plan.departments)
        # eng roles gone
        assert not any(r.id == "swe" for r in plan.roles)
        assert not any(r.id == "qa" for r in plan.roles)
        # eng skills gone
        assert not any(s.id == "code_review" for s in plan.skills)
        assert not any(s.id == "test_plan" for s in plan.skills)
        # eng memories gone
        assert len(plan.memories) == 0
        # sales still there
        assert any(d.id == "sales" for d in plan.departments)
        assert any(r.id == "ae" for r in plan.roles)

    def test_remove_role_cascades(self):
        plan = _make_plan()
        mods = {"changes": [{"action": "remove", "entity_type": "role", "entity_id": "swe"}]}
        changes = _apply_modifications(plan, mods)
        assert any("Removed role 'swe'" in c for c in changes)
        assert not any(r.id == "swe" for r in plan.roles)
        assert not any(s.id == "code_review" for s in plan.skills)
        # qa's test_plan still there
        assert any(s.id == "test_plan" for s in plan.skills)

    def test_remove_skill(self):
        plan = _make_plan()
        mods = {
            "changes": [{"action": "remove", "entity_type": "skill", "entity_id": "code_review"}]
        }
        changes = _apply_modifications(plan, mods)
        assert any("Removed skill 'code_review'" in c for c in changes)
        assert not any(s.id == "code_review" for s in plan.skills)
        assert any(s.id == "test_plan" for s in plan.skills)

    def test_remove_not_found(self):
        plan = _make_plan()
        mods = {
            "changes": [
                {"action": "remove", "entity_type": "department", "entity_id": "nonexistent"},
                {"action": "remove", "entity_type": "role", "entity_id": "nonexistent"},
                {"action": "remove", "entity_type": "skill", "entity_id": "nonexistent"},
            ]
        }
        changes = _apply_modifications(plan, mods)
        assert all("not found" in c for c in changes)

    def test_remove_unknown_entity_type(self):
        plan = _make_plan()
        mods = {"changes": [{"action": "remove", "entity_type": "widget", "entity_id": "w1"}]}
        changes = _apply_modifications(plan, mods)
        assert any("Unknown entity type" in c for c in changes)

    def test_remove_role_cleans_manages(self):
        plan = _make_plan()
        # swe manages qa
        assert "qa" in plan.roles[0].manages
        mods = {"changes": [{"action": "remove", "entity_type": "role", "entity_id": "qa"}]}
        _apply_modifications(plan, mods)
        swe = next(r for r in plan.roles if r.id == "swe")
        assert "qa" not in swe.manages


class TestApplyModificationsModify:
    def test_modify_department(self):
        plan = _make_plan()
        mods = {
            "changes": [
                {
                    "action": "modify",
                    "entity_type": "department",
                    "entity_id": "eng",
                    "fields": {"description": "Builds great stuff", "context": "Tech team"},
                }
            ]
        }
        changes = _apply_modifications(plan, mods)
        assert any("Modified department 'eng'" in c for c in changes)
        dept = next(d for d in plan.departments if d.id == "eng")
        assert dept.description == "Builds great stuff"
        assert dept.context == "Tech team"

    def test_modify_role(self):
        plan = _make_plan()
        mods = {
            "changes": [
                {
                    "action": "modify",
                    "entity_type": "role",
                    "entity_id": "swe",
                    "fields": {"persona": "Senior engineer", "description": "Writes code"},
                }
            ]
        }
        changes = _apply_modifications(plan, mods)
        assert any("Modified role 'swe'" in c for c in changes)
        role = next(r for r in plan.roles if r.id == "swe")
        assert role.persona == "Senior engineer"
        assert role.description == "Writes code"

    def test_modify_skill(self):
        plan = _make_plan()
        mods = {
            "changes": [
                {
                    "action": "modify",
                    "entity_type": "skill",
                    "entity_id": "code_review",
                    "fields": {"model": "haiku", "category": "operations"},
                }
            ]
        }
        changes = _apply_modifications(plan, mods)
        assert any("Modified skill 'code_review'" in c for c in changes)
        skill = next(s for s in plan.skills if s.id == "code_review")
        assert skill.model == "haiku"
        assert skill.category == "operations"

    def test_modify_not_found(self):
        plan = _make_plan()
        mods = {
            "changes": [
                {
                    "action": "modify",
                    "entity_type": "department",
                    "entity_id": "nonexistent",
                    "fields": {"description": "New"},
                },
                {
                    "action": "modify",
                    "entity_type": "role",
                    "entity_id": "nonexistent",
                    "fields": {"description": "New"},
                },
                {
                    "action": "modify",
                    "entity_type": "skill",
                    "entity_id": "nonexistent",
                    "fields": {"description": "New"},
                },
            ]
        }
        changes = _apply_modifications(plan, mods)
        assert all("not found" in c for c in changes)

    def test_modify_unknown_entity_type(self):
        plan = _make_plan()
        mods = {
            "changes": [
                {
                    "action": "modify",
                    "entity_type": "widget",
                    "entity_id": "w1",
                    "fields": {"name": "X"},
                }
            ]
        }
        changes = _apply_modifications(plan, mods)
        assert any("Unknown entity type" in c for c in changes)

    def test_modify_ignores_unknown_fields(self):
        """Fields not on the dataclass are silently ignored."""
        plan = _make_plan()
        mods = {
            "changes": [
                {
                    "action": "modify",
                    "entity_type": "department",
                    "entity_id": "eng",
                    "fields": {"nonexistent_field": "value", "description": "Updated"},
                }
            ]
        }
        _apply_modifications(plan, mods)
        dept = next(d for d in plan.departments if d.id == "eng")
        assert dept.description == "Updated"
        assert not hasattr(dept, "nonexistent_field") or (
            getattr(dept, "nonexistent_field", None) is None
        )


class TestApplyModificationsMixed:
    def test_multiple_operations(self):
        plan = _make_plan()
        mods = {
            "changes": [
                {
                    "action": "add",
                    "entity_type": "department",
                    "entity_id": "ops",
                    "fields": {"name": "Operations"},
                },
                {
                    "action": "modify",
                    "entity_type": "role",
                    "entity_id": "swe",
                    "fields": {"persona": "New persona"},
                },
                {
                    "action": "remove",
                    "entity_type": "skill",
                    "entity_id": "test_plan",
                },
            ],
            "explanation": "Added ops, updated SWE persona, removed test plan.",
        }
        changes = _apply_modifications(plan, mods)
        assert len(changes) == 4  # 3 changes + 1 explanation
        assert any("Added department 'ops'" in c for c in changes)
        assert any("Modified role 'swe'" in c for c in changes)
        assert any("Removed skill 'test_plan'" in c for c in changes)
        assert any("LLM explanation:" in c for c in changes)

    def test_unknown_action(self):
        plan = _make_plan()
        mods = {"changes": [{"action": "merge", "entity_type": "department", "entity_id": "eng"}]}
        changes = _apply_modifications(plan, mods)
        assert any("Unknown action" in c for c in changes)

    def test_incomplete_change_skipped(self):
        """Changes missing required fields are silently skipped."""
        plan = _make_plan()
        mods = {
            "changes": [
                {"action": "add", "entity_type": "department"},  # missing entity_id
                {"action": "remove", "entity_id": "eng"},  # missing entity_type
                {"entity_type": "role", "entity_id": "swe"},  # missing action
                {
                    "action": "add",
                    "entity_type": "department",
                    "entity_id": "ops",
                    "fields": {"name": "Ops"},
                },  # valid
            ]
        }
        changes = _apply_modifications(plan, mods)
        # Only the valid change should produce output
        assert len(changes) == 1
        assert "Added department 'ops'" in changes[0]

    def test_empty_changes(self):
        plan = _make_plan()
        mods = {"changes": []}
        changes = _apply_modifications(plan, mods)
        assert changes == []

    def test_explanation_only(self):
        plan = _make_plan()
        mods = {"changes": [], "explanation": "No changes needed"}
        changes = _apply_modifications(plan, mods)
        assert len(changes) == 1
        assert "LLM explanation: No changes needed" in changes[0]


@pytest.mark.asyncio
class TestRefinePlan:
    @patch("src.bootstrap.refiner.call_claude_api", new_callable=AsyncMock)
    async def test_successful_refinement(self, mock_api):
        plan = _make_plan()
        mock_api.return_value = {
            "text": json.dumps(
                {
                    "changes": [
                        {
                            "action": "add",
                            "entity_type": "department",
                            "entity_id": "ops",
                            "fields": {"name": "Operations", "description": "Runs things"},
                        }
                    ],
                    "explanation": "Added ops team as requested.",
                }
            ),
            "cost": {"total_cost_usd": 0.005},
        }

        result_plan, changes, cost = await refine_plan(plan, "Add an ops team")

        assert result_plan is plan  # mutated in place
        assert any(d.id == "ops" for d in plan.departments)
        assert any("Added department 'ops'" in c for c in changes)
        assert cost == 0.005
        mock_api.assert_called_once()

    @patch("src.bootstrap.refiner.call_claude_api", new_callable=AsyncMock)
    async def test_api_error_returns_plan_unchanged(self, mock_api):
        plan = _make_plan()
        orig_dept_count = len(plan.departments)
        mock_api.side_effect = Exception("API timeout")

        result_plan, changes, cost = await refine_plan(plan, "Add ops team")

        assert result_plan is plan
        assert len(plan.departments) == orig_dept_count
        assert any("Refinement failed" in c for c in changes)
        assert cost == 0.0

    @patch("src.bootstrap.refiner.call_claude_api", new_callable=AsyncMock)
    async def test_bad_json_returns_plan_unchanged(self, mock_api):
        plan = _make_plan()
        mock_api.return_value = {
            "text": "Sorry, I can't help with that.",
            "cost": {"total_cost_usd": 0.003},
        }

        result_plan, changes, cost = await refine_plan(plan, "Do something")

        assert result_plan is plan
        assert any("Could not parse" in c for c in changes)
        assert cost == 0.003

    @patch("src.bootstrap.refiner.call_claude_api", new_callable=AsyncMock)
    async def test_cost_extracted_from_result(self, mock_api):
        plan = _make_plan()
        mock_api.return_value = {
            "text": json.dumps({"changes": []}),
            "cost": {"total_cost_usd": 0.012},
        }

        _, _, cost = await refine_plan(plan, "Just checking")
        assert cost == 0.012

    @patch("src.bootstrap.refiner.call_claude_api", new_callable=AsyncMock)
    async def test_missing_cost_defaults_to_zero(self, mock_api):
        plan = _make_plan()
        mock_api.return_value = {
            "text": json.dumps({"changes": []}),
        }

        _, _, cost = await refine_plan(plan, "Just checking")
        assert cost == 0.0

    @patch("src.bootstrap.refiner.call_claude_api", new_callable=AsyncMock)
    async def test_prompt_includes_plan_and_feedback(self, mock_api):
        plan = _make_plan()
        mock_api.return_value = {
            "text": json.dumps({"changes": []}),
            "cost": {"total_cost_usd": 0.0},
        }

        await refine_plan(plan, "Add an ops team")

        call_kwargs = mock_api.call_args[1] if mock_api.call_args[1] else {}
        call_args = mock_api.call_args[0] if mock_api.call_args[0] else ()

        # Check via kwargs or positional — the user_message should contain both
        # plan summary and feedback
        all_args = str(call_kwargs) + str(call_args)
        assert "eng" in all_args  # plan content
        assert "Add an ops team" in all_args  # feedback

    @patch("src.bootstrap.refiner.call_claude_api", new_callable=AsyncMock)
    async def test_markdown_fenced_response(self, mock_api):
        """LLM wraps response in markdown fences — should still work."""
        plan = _make_plan()
        inner = json.dumps(
            {
                "changes": [
                    {
                        "action": "modify",
                        "entity_type": "department",
                        "entity_id": "eng",
                        "fields": {"description": "Engineering excellence"},
                    }
                ]
            }
        )
        mock_api.return_value = {
            "text": f"```json\n{inner}\n```",
            "cost": {"total_cost_usd": 0.004},
        }

        result_plan, changes, cost = await refine_plan(plan, "Update eng description")

        dept = next(d for d in result_plan.departments if d.id == "eng")
        assert dept.description == "Engineering excellence"
        assert any("Modified department 'eng'" in c for c in changes)
