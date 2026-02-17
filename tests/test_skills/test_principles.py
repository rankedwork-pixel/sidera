"""Tests for the principles field on RoleDefinition.

Verifies that:
- RoleDefinition accepts a principles tuple
- load_role_from_yaml reads principles from YAML
- compose_role_context injects principles into the prompt
- Principles appear between persona and context files
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from src.skills.executor import compose_role_context
from src.skills.schema import (
    DepartmentDefinition,
    RoleDefinition,
    load_role_from_yaml,
)


class TestRoleDefinitionPrinciples:
    def test_default_empty(self):
        role = RoleDefinition(
            id="test",
            name="Test",
            department_id="dept",
            description="desc",
            briefing_skills=("skill1",),
        )
        assert role.principles == ()

    def test_with_principles(self):
        role = RoleDefinition(
            id="test",
            name="Test",
            department_id="dept",
            description="desc",
            briefing_skills=("skill1",),
            principles=(
                "Always verify data before acting",
                "Prefer conservative changes",
            ),
        )
        assert len(role.principles) == 2
        assert "verify data" in role.principles[0]

    def test_frozen(self):
        role = RoleDefinition(
            id="test",
            name="Test",
            department_id="dept",
            description="desc",
            briefing_skills=("skill1",),
            principles=("p1",),
        )
        with pytest.raises(AttributeError):
            role.principles = ("new",)  # type: ignore


class TestLoadRoleFromYaml:
    def test_loads_principles(self, tmp_path: Path):
        yaml_content = dedent("""\
            id: test_role
            name: Test Role
            department_id: marketing
            description: A test role
            persona: You are a test role.
            principles:
              - "Always verify before acting"
              - "Prefer safety over speed"
            connectors: []
            briefing_skills:
              - some_skill
        """)
        yaml_file = tmp_path / "_role.yaml"
        yaml_file.write_text(yaml_content)

        role = load_role_from_yaml(yaml_file)
        assert len(role.principles) == 2
        assert role.principles[0] == "Always verify before acting"
        assert role.principles[1] == "Prefer safety over speed"

    def test_no_principles_defaults_empty(self, tmp_path: Path):
        yaml_content = dedent("""\
            id: test_role
            name: Test Role
            department_id: marketing
            description: A test role
            connectors: []
            briefing_skills:
              - some_skill
        """)
        yaml_file = tmp_path / "_role.yaml"
        yaml_file.write_text(yaml_content)

        role = load_role_from_yaml(yaml_file)
        assert role.principles == ()


class TestComposeRoleContextWithPrinciples:
    def test_principles_injected(self):
        dept = DepartmentDefinition(
            id="marketing",
            name="Marketing",
            description="Marketing dept",
        )
        role = RoleDefinition(
            id="buyer",
            name="Media Buyer",
            department_id="marketing",
            description="desc",
            persona="You are a media buyer.",
            principles=(
                "Always verify data",
                "Prefer conservative changes",
            ),
            briefing_skills=("skill1",),
        )
        context = compose_role_context(dept, role)
        assert "# Decision-Making Principles" in context
        assert "- Always verify data" in context
        assert "- Prefer conservative changes" in context
        assert "ambiguous situations" in context

    def test_principles_after_persona(self):
        role = RoleDefinition(
            id="buyer",
            name="Media Buyer",
            department_id="marketing",
            description="desc",
            persona="You are a media buyer.",
            principles=("Principle 1",),
            briefing_skills=("skill1",),
        )
        context = compose_role_context(None, role)
        persona_pos = context.find("You are a media buyer")
        principles_pos = context.find("Decision-Making Principles")
        assert persona_pos < principles_pos

    def test_no_principles_no_section(self):
        role = RoleDefinition(
            id="buyer",
            name="Media Buyer",
            department_id="marketing",
            description="desc",
            persona="You are a media buyer.",
            briefing_skills=("skill1",),
        )
        context = compose_role_context(None, role)
        assert "Decision-Making Principles" not in context

    def test_principles_before_memory(self):
        role = RoleDefinition(
            id="buyer",
            name="Media Buyer",
            department_id="marketing",
            description="desc",
            persona="You are a media buyer.",
            principles=("Principle 1",),
            briefing_skills=("skill1",),
        )
        context = compose_role_context(
            None,
            role,
            memory_context="# Role Memory\n\nSome memories",
        )
        principles_pos = context.find("Decision-Making Principles")
        memory_pos = context.find("Role Memory")
        assert principles_pos < memory_pos
