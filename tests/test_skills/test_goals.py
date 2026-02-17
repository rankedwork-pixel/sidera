"""Tests for role goals as universal filter.

Verifies that goals on RoleDefinition flow through YAML loading,
compose_role_context injection, and maintain correct ordering.
"""

from __future__ import annotations

from pathlib import Path

from src.skills.executor import compose_role_context
from src.skills.schema import DepartmentDefinition, RoleDefinition, load_role_from_yaml

# ============================================================
# Tests — RoleDefinition field
# ============================================================


class TestGoalsField:
    def test_default_empty(self):
        """goals defaults to empty tuple."""
        role = RoleDefinition(
            id="test",
            name="Test",
            department_id="dept",
            description="Test role",
            briefing_skills=("skill1",),
        )
        assert role.goals == ()

    def test_goals_stored_as_tuple(self):
        """goals should be a tuple of strings."""
        role = RoleDefinition(
            id="test",
            name="Test",
            department_id="dept",
            description="Test role",
            briefing_skills=("skill1",),
            goals=("Maximize ROAS", "Eliminate waste"),
        )
        assert len(role.goals) == 2
        assert role.goals[0] == "Maximize ROAS"
        assert role.goals[1] == "Eliminate waste"


# ============================================================
# Tests — YAML loading
# ============================================================


class TestGoalsYamlLoading:
    def test_yaml_with_goals(self, tmp_path: Path):
        """goals should load from YAML."""
        yaml_content = """\
id: test_role
name: Test Role
department_id: test_dept
description: A test role
goals:
  - "Maximize ROAS"
  - "Eliminate waste"
briefing_skills:
  - skill1
"""
        yaml_path = tmp_path / "_role.yaml"
        yaml_path.write_text(yaml_content)

        role = load_role_from_yaml(yaml_path)
        assert len(role.goals) == 2
        assert role.goals[0] == "Maximize ROAS"
        assert role.goals[1] == "Eliminate waste"

    def test_yaml_without_goals(self, tmp_path: Path):
        """Missing goals should default to empty."""
        yaml_content = """\
id: test_role
name: Test Role
department_id: test_dept
description: A test role
briefing_skills:
  - skill1
"""
        yaml_path = tmp_path / "_role.yaml"
        yaml_path.write_text(yaml_content)

        role = load_role_from_yaml(yaml_path)
        assert role.goals == ()

    def test_goals_order_preserved(self, tmp_path: Path):
        """Goals order should match YAML order."""
        yaml_content = """\
id: test_role
name: Test Role
department_id: test_dept
description: A test role
goals:
  - "First goal"
  - "Second goal"
  - "Third goal"
briefing_skills:
  - skill1
"""
        yaml_path = tmp_path / "_role.yaml"
        yaml_path.write_text(yaml_content)

        role = load_role_from_yaml(yaml_path)
        assert role.goals == ("First goal", "Second goal", "Third goal")


# ============================================================
# Tests — compose_role_context injection
# ============================================================


class TestGoalsInjection:
    def test_goals_section_injected(self):
        """Goals section should appear in role context."""
        dept = DepartmentDefinition(id="marketing", name="Marketing", description="Marketing dept")
        role = RoleDefinition(
            id="buyer",
            name="Media Buyer",
            department_id="marketing",
            description="Buys media",
            briefing_skills=("skill1",),
            goals=("Maximize ROAS", "Eliminate waste"),
        )
        result = compose_role_context(department=dept, role=role)
        assert "# Active Goals" in result
        assert "Maximize ROAS" in result
        assert "Eliminate waste" in result

    def test_no_goals_section_when_empty(self):
        """No goals section when role has no goals."""
        dept = DepartmentDefinition(id="marketing", name="Marketing", description="Marketing dept")
        role = RoleDefinition(
            id="buyer",
            name="Media Buyer",
            department_id="marketing",
            description="Buys media",
            briefing_skills=("skill1",),
        )
        result = compose_role_context(department=dept, role=role)
        assert "Active Goals" not in result

    def test_goals_after_principles(self):
        """Goals section should appear after principles, before context files."""
        dept = DepartmentDefinition(id="marketing", name="Marketing", description="Marketing dept")
        role = RoleDefinition(
            id="buyer",
            name="Media Buyer",
            department_id="marketing",
            description="Buys media",
            persona="I am a buyer.",
            briefing_skills=("skill1",),
            principles=("Principle one",),
            goals=("Goal one",),
        )
        result = compose_role_context(department=dept, role=role)
        principles_pos = result.index("Decision-Making Principles")
        goals_pos = result.index("Active Goals")
        assert principles_pos < goals_pos
