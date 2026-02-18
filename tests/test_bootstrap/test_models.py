"""Tests for bootstrap data models."""

from __future__ import annotations

from src.bootstrap.models import (
    BootstrapPlan,
    BootstrapStatus,
    ClassifiedDocument,
    DocumentCategory,
    ExecutionResult,
    ExtractedDepartment,
    ExtractedKnowledge,
    ExtractedMemory,
    ExtractedRole,
    ExtractedSkill,
    RawDocument,
)


class TestRawDocument:
    def test_basic_creation(self):
        doc = RawDocument(
            file_id="abc123",
            title="Test Doc",
            mime_type="application/vnd.google-apps.document",
            content="Hello world",
            char_count=11,
        )
        assert doc.file_id == "abc123"
        assert doc.char_count == 11
        assert not doc.is_truncated

    def test_truncation_detection(self):
        doc = RawDocument(
            file_id="abc",
            title="Big Doc",
            mime_type="doc",
            content="start [... content truncated ...] end",
            char_count=100,
        )
        assert doc.is_truncated


class TestClassifiedDocument:
    def test_relevant(self):
        doc = ClassifiedDocument(
            file_id="abc",
            title="Org Chart",
            mime_type="doc",
            content="...",
            categories=["org_structure"],
            confidence=0.9,
            char_count=100,
        )
        assert doc.is_relevant

    def test_irrelevant(self):
        doc = ClassifiedDocument(
            file_id="abc",
            title="Spam",
            mime_type="doc",
            content="...",
            categories=["irrelevant"],
            confidence=0.8,
            char_count=50,
        )
        assert not doc.is_relevant

    def test_mixed_categories(self):
        doc = ClassifiedDocument(
            file_id="abc",
            title="Handbook",
            mime_type="doc",
            content="...",
            categories=["org_structure", "sop_playbook"],
            confidence=0.85,
            char_count=200,
        )
        assert doc.is_relevant


class TestDocumentCategory:
    def test_values(self):
        assert DocumentCategory.ORG_STRUCTURE.value == "org_structure"
        assert DocumentCategory.SOP_PLAYBOOK.value == "sop_playbook"
        assert DocumentCategory.IRRELEVANT.value == "irrelevant"


class TestBootstrapPlan:
    def test_default_creation(self):
        plan = BootstrapPlan()
        assert plan.id.startswith("bootstrap_")
        assert plan.status == "draft"
        assert plan.departments == []
        assert plan.roles == []
        assert plan.skills == []
        assert plan.memories == []

    def test_summary(self):
        plan = BootstrapPlan(
            departments=[
                ExtractedDepartment(id="eng", name="Engineering", description="")
            ],
            roles=[
                ExtractedRole(
                    id="swe", name="SWE", department_id="eng", description=""
                )
            ],
            skills=[],
            memories=[],
            estimated_cost=0.35,
        )
        summary = plan.summary()
        assert summary["departments"] == 1
        assert summary["roles"] == 1
        assert summary["skills"] == 0
        assert summary["estimated_cost"] == "$0.35"

    def test_to_dict(self):
        plan = BootstrapPlan(
            source_folder_id="folder123",
            departments=[
                ExtractedDepartment(
                    id="eng",
                    name="Engineering",
                    description="Builds stuff",
                    vocabulary=[{"term": "SLA", "definition": "Service Level Agreement"}],
                )
            ],
        )
        d = plan.to_dict()
        assert d["source_folder_id"] == "folder123"
        assert len(d["departments"]) == 1
        assert d["departments"][0]["vocabulary"] == [
            {"term": "SLA", "definition": "Service Level Agreement"}
        ]

    def test_status_enum(self):
        assert BootstrapStatus.DRAFT.value == "draft"
        assert BootstrapStatus.APPROVED.value == "approved"
        assert BootstrapStatus.EXECUTED.value == "executed"
        assert BootstrapStatus.FAILED.value == "failed"
        assert BootstrapStatus.REJECTED.value == "rejected"


class TestExecutionResult:
    def test_success(self):
        result = ExecutionResult(
            plan_id="plan_abc",
            departments_created=2,
            roles_created=3,
            skills_created=5,
            memories_seeded=10,
        )
        assert result.success
        assert result.summary()["created"]["departments"] == 2

    def test_failure(self):
        result = ExecutionResult(
            plan_id="plan_abc",
            errors=["Something broke"],
        )
        assert not result.success

    def test_skipped_counts(self):
        result = ExecutionResult(
            plan_id="plan_abc",
            departments_created=1,
            departments_skipped=1,
            roles_created=2,
            roles_skipped=0,
        )
        assert result.summary()["skipped"]["departments"] == 1


class TestExtractedKnowledge:
    def test_empty(self):
        k = ExtractedKnowledge()
        assert k.departments == []
        assert k.roles == []
        assert k.skills == []
        assert k.memories == []

    def test_populated(self):
        k = ExtractedKnowledge(
            departments=[
                ExtractedDepartment(id="eng", name="Eng", description="")
            ],
            roles=[
                ExtractedRole(
                    id="swe", name="SWE", department_id="eng", description=""
                )
            ],
            skills=[
                ExtractedSkill(
                    id="deploy",
                    name="Deploy",
                    role_id="swe",
                    department_id="eng",
                    description="Deploy stuff",
                )
            ],
            memories=[
                ExtractedMemory(
                    role_id="swe",
                    department_id="eng",
                    memory_type="insight",
                    title="Key insight",
                    content="The service was migrated in Q1",
                )
            ],
        )
        assert len(k.departments) == 1
        assert len(k.skills) == 1
        assert k.memories[0].title == "Key insight"
