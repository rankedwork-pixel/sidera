"""Tests for department vocabulary transformation manifests.

Verifies that vocabulary terms on DepartmentDefinition flow through
YAML loading, compose_role_context injection, and DB loader handling.
"""

from __future__ import annotations

from pathlib import Path

from src.skills.executor import compose_role_context
from src.skills.schema import DepartmentDefinition, RoleDefinition, load_department_from_yaml

# ============================================================
# Tests — DepartmentDefinition field
# ============================================================


class TestVocabularyField:
    def test_default_empty(self):
        """vocabulary defaults to empty tuple."""
        dept = DepartmentDefinition(id="test", name="Test", description="Test dept")
        assert dept.vocabulary == ()

    def test_vocabulary_stored_as_tuples(self):
        """vocabulary should be a tuple of (term, definition) tuples."""
        dept = DepartmentDefinition(
            id="test",
            name="Test",
            description="Test dept",
            vocabulary=(("ROAS", "Return on ad spend"), ("CPA", "Cost per acquisition")),
        )
        assert len(dept.vocabulary) == 2
        assert dept.vocabulary[0] == ("ROAS", "Return on ad spend")
        assert dept.vocabulary[1] == ("CPA", "Cost per acquisition")


# ============================================================
# Tests — YAML loading
# ============================================================


class TestVocabularyYamlLoading:
    def test_yaml_with_vocabulary(self, tmp_path: Path):
        """vocabulary should load from YAML."""
        yaml_content = """\
id: test_dept
name: Test Department
description: A test department
vocabulary:
  - term: "ROAS"
    definition: "Return on ad spend"
  - term: "CPA"
    definition: "Cost per acquisition"
"""
        yaml_path = tmp_path / "_department.yaml"
        yaml_path.write_text(yaml_content)

        dept = load_department_from_yaml(yaml_path)
        assert len(dept.vocabulary) == 2
        assert dept.vocabulary[0] == ("ROAS", "Return on ad spend")
        assert dept.vocabulary[1] == ("CPA", "Cost per acquisition")

    def test_yaml_without_vocabulary(self, tmp_path: Path):
        """Missing vocabulary should default to empty."""
        yaml_content = """\
id: test_dept
name: Test Department
description: A test department
"""
        yaml_path = tmp_path / "_department.yaml"
        yaml_path.write_text(yaml_content)

        dept = load_department_from_yaml(yaml_path)
        assert dept.vocabulary == ()

    def test_yaml_skips_invalid_entries(self, tmp_path: Path):
        """Vocabulary entries without a term should be skipped."""
        yaml_content = """\
id: test_dept
name: Test Department
description: A test department
vocabulary:
  - term: "ROAS"
    definition: "Return on ad spend"
  - definition: "Missing term entry"
  - "just a string"
"""
        yaml_path = tmp_path / "_department.yaml"
        yaml_path.write_text(yaml_content)

        dept = load_department_from_yaml(yaml_path)
        assert len(dept.vocabulary) == 1
        assert dept.vocabulary[0][0] == "ROAS"


# ============================================================
# Tests — compose_role_context injection
# ============================================================


class TestVocabularyInjection:
    def test_vocabulary_section_injected(self):
        """Vocabulary section should appear in role context."""
        dept = DepartmentDefinition(
            id="marketing",
            name="Marketing",
            description="Marketing dept",
            vocabulary=(("ROAS", "Return on ad spend"), ("CPA", "Cost per acquisition")),
        )
        role = RoleDefinition(
            id="buyer",
            name="Media Buyer",
            department_id="marketing",
            description="Buys media",
            briefing_skills=("skill1",),
        )
        result = compose_role_context(department=dept, role=role)
        assert "# Department Vocabulary" in result
        assert "**ROAS**" in result
        assert "Return on ad spend" in result
        assert "**CPA**" in result

    def test_no_vocabulary_section_when_empty(self):
        """No vocabulary section when department has no vocabulary."""
        dept = DepartmentDefinition(
            id="marketing",
            name="Marketing",
            description="Marketing dept",
        )
        role = RoleDefinition(
            id="buyer",
            name="Media Buyer",
            department_id="marketing",
            description="Buys media",
            briefing_skills=("skill1",),
        )
        result = compose_role_context(department=dept, role=role)
        assert "Department Vocabulary" not in result

    def test_vocabulary_before_role_persona(self):
        """Vocabulary section should appear after dept context, before role persona."""
        dept = DepartmentDefinition(
            id="marketing",
            name="Marketing",
            description="Marketing dept",
            context="Department context here.",
            vocabulary=(("ROAS", "Return on ad spend"),),
        )
        role = RoleDefinition(
            id="buyer",
            name="Media Buyer",
            department_id="marketing",
            description="Buys media",
            persona="I am a media buyer.",
            briefing_skills=("skill1",),
        )
        result = compose_role_context(department=dept, role=role)
        vocab_pos = result.index("Department Vocabulary")
        persona_pos = result.index("# Role: Media Buyer")
        assert vocab_pos < persona_pos
