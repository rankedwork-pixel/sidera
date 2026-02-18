"""Tests for the bootstrap plan generator."""

from __future__ import annotations

from src.bootstrap.generator import (
    _deduplicate_departments,
    _deduplicate_roles,
    _detect_managers,
    _normalize_entity_ids,
    _normalize_slug,
    _validate_memories,
    _validate_skills,
    generate_plan,
)
from src.bootstrap.models import (
    ExtractedDepartment,
    ExtractedKnowledge,
    ExtractedMemory,
    ExtractedRole,
    ExtractedSkill,
)


class TestNormalizeSlug:
    def test_basic(self):
        assert _normalize_slug("Customer Support") == "customer_support"

    def test_special_chars(self):
        assert _normalize_slug("IT / Engineering") == "it_engineering"

    def test_already_slug(self):
        assert _normalize_slug("my_role") == "my_role"

    def test_empty(self):
        assert _normalize_slug("") == "unknown"

    def test_numbers(self):
        assert _normalize_slug("Team 42") == "team_42"

    def test_leading_trailing_special(self):
        assert _normalize_slug("--hello--") == "hello"


class TestDeduplicateDepartments:
    def test_no_duplicates(self):
        depts = [
            ExtractedDepartment(id="eng", name="Engineering", description="Builds"),
            ExtractedDepartment(id="sales", name="Sales", description="Sells"),
        ]
        result = _deduplicate_departments(depts)
        assert len(result) == 2

    def test_merge_duplicates(self):
        depts = [
            ExtractedDepartment(
                id="eng", name="Engineering", description="Builds",
                vocabulary=[{"term": "CI", "definition": "Continuous Integration"}],
            ),
            ExtractedDepartment(
                id="eng", name="Engineering", description="",
                vocabulary=[{"term": "CD", "definition": "Continuous Deployment"}],
            ),
        ]
        result = _deduplicate_departments(depts)
        assert len(result) == 1
        assert result[0].description == "Builds"  # kept from first
        terms = [v["term"] for v in result[0].vocabulary]
        assert "CI" in terms
        assert "CD" in terms

    def test_dedup_vocabulary_within_dept(self):
        depts = [
            ExtractedDepartment(
                id="eng", name="Engineering", description="",
                vocabulary=[
                    {"term": "SLA", "definition": "Service Level Agreement"},
                    {"term": "SLA", "definition": "Duplicate"},
                ],
            ),
        ]
        result = _deduplicate_departments(depts)
        assert len(result[0].vocabulary) == 1


class TestDeduplicateRoles:
    def test_no_duplicates(self):
        roles = [
            ExtractedRole(id="swe", name="SWE", department_id="eng", description="Codes"),
            ExtractedRole(id="pm", name="PM", department_id="eng", description="Manages"),
        ]
        result = _deduplicate_roles(roles)
        assert len(result) == 2

    def test_merge_duplicates(self):
        roles = [
            ExtractedRole(
                id="swe", name="SWE", department_id="eng", description="Codes",
                principles=["Test first"],
            ),
            ExtractedRole(
                id="swe", name="SWE", department_id="eng", description="",
                principles=["Ship fast"],
                goals=["Zero bugs"],
            ),
        ]
        result = _deduplicate_roles(roles)
        assert len(result) == 1
        assert "Test first" in result[0].principles
        assert "Ship fast" in result[0].principles
        assert "Zero bugs" in result[0].goals

    def test_dedup_principles(self):
        roles = [
            ExtractedRole(
                id="swe", name="SWE", department_id="eng", description="",
                principles=["Test first", "Test first", "Ship fast"],
            ),
        ]
        result = _deduplicate_roles(roles)
        assert result[0].principles == ["Test first", "Ship fast"]


class TestNormalizeEntityIds:
    def test_normalize(self):
        depts = [
            ExtractedDepartment(id="Customer Support", name="CS", description=""),
        ]
        id_map = _normalize_entity_ids(depts, "department")
        assert depts[0].id == "customer_support"
        assert id_map == {"Customer Support": "customer_support"}

    def test_collision_handling(self):
        depts = [
            ExtractedDepartment(id="eng", name="Eng", description=""),
            ExtractedDepartment(id="ENG", name="Engineering", description=""),
        ]
        _normalize_entity_ids(depts, "department")
        ids = [d.id for d in depts]
        assert len(set(ids)) == 2  # no duplicates
        assert "eng" in ids
        assert "eng_2" in ids


class TestValidateSkills:
    def test_valid_skills(self):
        skills = [
            ExtractedSkill(
                id="deploy", name="Deploy", role_id="swe",
                department_id="eng", description="Deploy stuff",
                category="operations", model="haiku",
            ),
        ]
        errors: list[str] = []
        result = _validate_skills(skills, {"swe"}, {"eng"}, errors)
        assert len(result) == 1
        assert len(errors) == 0

    def test_invalid_category_fixed(self):
        skills = [
            ExtractedSkill(
                id="deploy", name="Deploy", role_id="swe",
                department_id="eng", description="Deploy",
                category="fake_category",
            ),
        ]
        errors: list[str] = []
        result = _validate_skills(skills, {"swe"}, {"eng"}, errors)
        assert result[0].category == "general"

    def test_invalid_model_fixed(self):
        skills = [
            ExtractedSkill(
                id="deploy", name="Deploy", role_id="swe",
                department_id="eng", description="Deploy",
                model="gpt4",
            ),
        ]
        errors: list[str] = []
        result = _validate_skills(skills, {"swe"}, {"eng"}, errors)
        assert result[0].model == "sonnet"

    def test_unknown_role_logged(self):
        skills = [
            ExtractedSkill(
                id="deploy", name="Deploy", role_id="unknown_role",
                department_id="eng", description="Deploy",
            ),
        ]
        errors: list[str] = []
        _validate_skills(skills, {"swe"}, {"eng"}, errors)
        assert any("unknown role" in e for e in errors)

    def test_id_collision(self):
        skills = [
            ExtractedSkill(
                id="deploy", name="Deploy 1", role_id="swe",
                department_id="eng", description="D1",
            ),
            ExtractedSkill(
                id="deploy", name="Deploy 2", role_id="swe",
                department_id="eng", description="D2",
            ),
        ]
        errors: list[str] = []
        result = _validate_skills(skills, {"swe"}, {"eng"}, errors)
        ids = [s.id for s in result]
        assert len(set(ids)) == 2


class TestValidateMemories:
    def test_valid_memory(self):
        memories = [
            ExtractedMemory(
                role_id="swe", department_id="eng",
                memory_type="insight", title="Key fact",
                content="Important detail",
            ),
        ]
        result = _validate_memories(memories, {"swe"}, {"eng"})
        assert len(result) == 1

    def test_invalid_role_filtered(self):
        memories = [
            ExtractedMemory(
                role_id="unknown", department_id="eng",
                memory_type="insight", title="Fact", content="Detail",
            ),
        ]
        result = _validate_memories(memories, {"swe"}, {"eng"})
        assert len(result) == 0

    def test_invalid_type_fixed(self):
        memories = [
            ExtractedMemory(
                role_id="swe", department_id="eng",
                memory_type="fake_type", title="Fact", content="Detail",
            ),
        ]
        result = _validate_memories(memories, {"swe"}, {"eng"})
        assert result[0].memory_type == "insight"

    def test_empty_title_filtered(self):
        memories = [
            ExtractedMemory(
                role_id="swe", department_id="eng",
                memory_type="insight", title="", content="Detail",
            ),
        ]
        result = _validate_memories(memories, {"swe"}, {"eng"})
        assert len(result) == 0


class TestDetectManagers:
    def test_detect_by_name(self):
        roles = [
            ExtractedRole(
                id="head_of_engineering", name="Head of Engineering",
                department_id="eng", description="Leads eng",
            ),
            ExtractedRole(
                id="backend_engineer", name="Backend Engineer",
                department_id="eng", description="Writes code",
            ),
            ExtractedRole(
                id="frontend_engineer", name="Frontend Engineer",
                department_id="eng", description="Builds UI",
            ),
        ]
        _detect_managers(roles)
        assert "backend_engineer" in roles[0].manages
        assert "frontend_engineer" in roles[0].manages
        assert roles[1].manages == []

    def test_existing_manages_preserved(self):
        roles = [
            ExtractedRole(
                id="head_of_engineering", name="Head of Engineering",
                department_id="eng", description="",
                manages=["backend_engineer"],
            ),
            ExtractedRole(
                id="backend_engineer", name="Backend Engineer",
                department_id="eng", description="",
            ),
        ]
        _detect_managers(roles)
        # Existing manages list should be preserved (only invalid refs cleaned)
        assert "backend_engineer" in roles[0].manages

    def test_self_reference_removed(self):
        roles = [
            ExtractedRole(
                id="lead", name="Lead",
                department_id="eng", description="",
                manages=["lead"],  # self-reference
            ),
        ]
        _detect_managers(roles)
        assert "lead" not in roles[0].manages


class TestGeneratePlan:
    def test_basic_plan(self):
        knowledge = ExtractedKnowledge(
            departments=[
                ExtractedDepartment(
                    id="eng", name="Engineering", description="Builds products"
                )
            ],
            roles=[
                ExtractedRole(
                    id="swe", name="Software Engineer",
                    department_id="eng", description="Writes code",
                )
            ],
            skills=[
                ExtractedSkill(
                    id="code_review", name="Code Review",
                    role_id="swe", department_id="eng",
                    description="Reviews PRs", category="analysis",
                )
            ],
            memories=[
                ExtractedMemory(
                    role_id="swe", department_id="eng",
                    memory_type="insight", title="Fact", content="Detail",
                )
            ],
        )
        plan = generate_plan(knowledge, source_folder_id="folder123")
        assert plan.status == "draft"
        assert plan.source_folder_id == "folder123"
        assert len(plan.departments) == 1
        assert len(plan.roles) == 1
        assert len(plan.skills) == 1
        assert len(plan.memories) == 1

    def test_empty_knowledge(self):
        knowledge = ExtractedKnowledge()
        plan = generate_plan(knowledge)
        assert plan.departments == []
        assert plan.roles == []
        assert plan.skills == []
