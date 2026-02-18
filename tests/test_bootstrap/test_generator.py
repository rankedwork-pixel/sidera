"""Tests for the bootstrap plan generator."""

from __future__ import annotations

from src.bootstrap.generator import (
    _compute_agreement_confidence,
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
        result, conflicts = _deduplicate_departments(depts)
        assert len(result) == 2
        assert len(conflicts) == 0

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
        result, conflicts = _deduplicate_departments(depts)
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
        result, _conflicts = _deduplicate_departments(depts)
        assert len(result[0].vocabulary) == 1


class TestDeduplicateRoles:
    def test_no_duplicates(self):
        roles = [
            ExtractedRole(id="swe", name="SWE", department_id="eng", description="Codes"),
            ExtractedRole(id="pm", name="PM", department_id="eng", description="Manages"),
        ]
        result, conflicts = _deduplicate_roles(roles)
        assert len(result) == 2
        assert len(conflicts) == 0

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
        result, _conflicts = _deduplicate_roles(roles)
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
        result, _conflicts = _deduplicate_roles(roles)
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
        assert plan.conflicts == []

    def test_plan_with_conflicts(self):
        """Conflicts detected during dedup should appear in the plan."""
        knowledge = ExtractedKnowledge(
            departments=[
                ExtractedDepartment(
                    id="eng", name="Engineering",
                    description="Builds products",
                    source_docs=["doc1"],
                ),
                ExtractedDepartment(
                    id="eng", name="Engineering",
                    description="Ships software",
                    source_docs=["doc2"],
                ),
            ],
            roles=[],
        )
        plan = generate_plan(knowledge)
        assert len(plan.conflicts) == 1
        assert plan.conflicts[0].entity_type == "department"
        assert plan.conflicts[0].field == "description"
        assert plan.summary()["conflicts"] == 1


class TestConflictDetectionDepartments:
    def test_conflicting_descriptions(self):
        """Two depts same ID, different descriptions → conflict detected."""
        depts = [
            ExtractedDepartment(
                id="eng", name="Engineering",
                description="Builds products",
                source_docs=["doc1"],
            ),
            ExtractedDepartment(
                id="eng", name="Engineering",
                description="Ships software",
                source_docs=["doc2"],
            ),
        ]
        result, conflicts = _deduplicate_departments(depts)
        assert len(result) == 1
        assert len(conflicts) == 1
        assert conflicts[0].entity_type == "department"
        assert conflicts[0].entity_id == "eng"
        assert conflicts[0].field == "description"
        assert len(conflicts[0].values) == 2
        # First value was kept as resolution
        assert conflicts[0].resolution == "Builds products"

    def test_conflicting_contexts(self):
        """Two depts same ID, different contexts → conflict detected."""
        depts = [
            ExtractedDepartment(
                id="eng", name="Engineering",
                description="", context="Context A",
                source_docs=["doc1"],
            ),
            ExtractedDepartment(
                id="eng", name="Engineering",
                description="", context="Context B",
                source_docs=["doc2"],
            ),
        ]
        result, conflicts = _deduplicate_departments(depts)
        assert len(result) == 1
        assert len(conflicts) == 1
        assert conflicts[0].field == "context"

    def test_no_conflict_when_values_match(self):
        """Same description across sources → no conflict."""
        depts = [
            ExtractedDepartment(
                id="eng", name="Engineering",
                description="Builds products",
                source_docs=["doc1"],
            ),
            ExtractedDepartment(
                id="eng", name="Engineering",
                description="Builds products",
                source_docs=["doc2"],
            ),
        ]
        result, conflicts = _deduplicate_departments(depts)
        assert len(result) == 1
        assert len(conflicts) == 0

    def test_no_conflict_when_second_empty(self):
        """Second dept has empty description → no conflict (just merge)."""
        depts = [
            ExtractedDepartment(
                id="eng", name="Engineering",
                description="Builds products",
                source_docs=["doc1"],
            ),
            ExtractedDepartment(
                id="eng", name="Engineering",
                description="",
                source_docs=["doc2"],
            ),
        ]
        result, conflicts = _deduplicate_departments(depts)
        assert len(result) == 1
        assert len(conflicts) == 0
        assert result[0].description == "Builds products"

    def test_multiple_conflicts_same_entity(self):
        """Both description and context conflict on same dept."""
        depts = [
            ExtractedDepartment(
                id="eng", name="Engineering",
                description="Builds products", context="Context A",
                source_docs=["doc1"],
            ),
            ExtractedDepartment(
                id="eng", name="Engineering",
                description="Ships software", context="Context B",
                source_docs=["doc2"],
            ),
        ]
        result, conflicts = _deduplicate_departments(depts)
        assert len(result) == 1
        assert len(conflicts) == 2
        fields = {c.field for c in conflicts}
        assert "description" in fields
        assert "context" in fields


class TestConflictDetectionRoles:
    def test_conflicting_descriptions(self):
        """Two roles same ID, different descriptions → conflict detected."""
        roles = [
            ExtractedRole(
                id="swe", name="SWE", department_id="eng",
                description="Writes code",
                source_docs=["doc1"],
            ),
            ExtractedRole(
                id="swe", name="SWE", department_id="eng",
                description="Builds features",
                source_docs=["doc2"],
            ),
        ]
        result, conflicts = _deduplicate_roles(roles)
        assert len(result) == 1
        assert any(c.field == "description" for c in conflicts)

    def test_conflicting_personas(self):
        """Two roles same ID, different personas → conflict detected."""
        roles = [
            ExtractedRole(
                id="swe", name="SWE", department_id="eng",
                description="Codes", persona="Careful engineer",
                source_docs=["doc1"],
            ),
            ExtractedRole(
                id="swe", name="SWE", department_id="eng",
                description="", persona="Fast builder",
                source_docs=["doc2"],
            ),
        ]
        result, conflicts = _deduplicate_roles(roles)
        assert len(result) == 1
        persona_conflicts = [c for c in conflicts if c.field == "persona"]
        assert len(persona_conflicts) == 1
        assert persona_conflicts[0].entity_type == "role"

    def test_conflicting_department_ids(self):
        """Same role from two docs, different department_id → conflict."""
        roles = [
            ExtractedRole(
                id="swe", name="SWE", department_id="eng",
                description="Codes",
                source_docs=["doc1"],
            ),
            ExtractedRole(
                id="swe", name="SWE", department_id="product",
                description="",
                source_docs=["doc2"],
            ),
        ]
        result, conflicts = _deduplicate_roles(roles)
        assert len(result) == 1
        dept_conflicts = [c for c in conflicts if c.field == "department_id"]
        assert len(dept_conflicts) == 1

    def test_no_conflict_when_values_match(self):
        """Same persona across sources → no conflict."""
        roles = [
            ExtractedRole(
                id="swe", name="SWE", department_id="eng",
                description="Codes", persona="Careful engineer",
                source_docs=["doc1"],
            ),
            ExtractedRole(
                id="swe", name="SWE", department_id="eng",
                description="Codes", persona="Careful engineer",
                source_docs=["doc2"],
            ),
        ]
        result, conflicts = _deduplicate_roles(roles)
        assert len(result) == 1
        assert len(conflicts) == 0


class TestComputeAgreementConfidence:
    def test_all_agree(self):
        entries = [
            {"value": "A", "source_docs": ["d1"]},
            {"value": "A", "source_docs": ["d2"]},
            {"value": "A", "source_docs": ["d3"]},
        ]
        assert _compute_agreement_confidence(entries) == 1.0

    def test_three_vs_one(self):
        """3 agree, 1 disagrees → 0.75."""
        entries = [
            {"value": "A", "source_docs": ["d1"]},
            {"value": "A", "source_docs": ["d2"]},
            {"value": "A", "source_docs": ["d3"]},
            {"value": "B", "source_docs": ["d4"]},
        ]
        assert _compute_agreement_confidence(entries) == 0.75

    def test_even_split(self):
        """2 vs 2 → 0.5."""
        entries = [
            {"value": "A", "source_docs": ["d1"]},
            {"value": "A", "source_docs": ["d2"]},
            {"value": "B", "source_docs": ["d3"]},
            {"value": "B", "source_docs": ["d4"]},
        ]
        assert _compute_agreement_confidence(entries) == 0.5

    def test_empty_entries(self):
        assert _compute_agreement_confidence([]) == 0.0

    def test_single_entry(self):
        entries = [{"value": "A", "source_docs": ["d1"]}]
        assert _compute_agreement_confidence(entries) == 1.0
