"""Data structures for the company bootstrap pipeline.

The bootstrap pipeline ingests a company's existing documents (from Google
Drive) and extracts structured knowledge to pre-configure Sidera's
departments, roles, skills, vocabulary, goals, and seed memories.

These dataclasses represent the intermediate and final states of the
pipeline: raw documents, classified documents, extracted knowledge, and
the complete ``BootstrapPlan`` that gets reviewed by a human before
execution.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# =====================================================================
# Document categories
# =====================================================================


class DocumentCategory(str, Enum):
    """Categories assigned to documents during classification."""

    ORG_STRUCTURE = "org_structure"
    SOP_PLAYBOOK = "sop_playbook"
    GOALS_KPIS = "goals_kpis"
    VOCABULARY_GLOSSARY = "vocabulary_glossary"
    DECISION_TREE = "decision_tree"
    MEETING_NOTES = "meeting_notes"
    IRRELEVANT = "irrelevant"


class BootstrapStatus(str, Enum):
    """Lifecycle states for a bootstrap plan."""

    DRAFT = "draft"
    APPROVED = "approved"
    EXECUTED = "executed"
    FAILED = "failed"
    REJECTED = "rejected"


# =====================================================================
# Pipeline stage 1: Raw documents from crawling
# =====================================================================


@dataclass
class RawDocument:
    """A document retrieved from Google Drive before classification."""

    file_id: str
    title: str
    mime_type: str
    content: str
    char_count: int
    folder_path: str = ""  # e.g. "Company Docs/Engineering"

    @property
    def is_truncated(self) -> bool:
        return "[... content truncated ...]" in self.content


# =====================================================================
# Pipeline stage 2: Classified documents
# =====================================================================


@dataclass
class ClassifiedDocument:
    """A document after Haiku classification with assigned categories."""

    file_id: str
    title: str
    mime_type: str
    content: str
    categories: list[str]  # list of DocumentCategory values
    confidence: float  # 0.0 - 1.0
    char_count: int
    folder_path: str = ""

    @property
    def is_relevant(self) -> bool:
        return DocumentCategory.IRRELEVANT.value not in self.categories


# =====================================================================
# Pipeline stage 3: Extracted knowledge
# =====================================================================


@dataclass
class ExtractedDepartment:
    """A department extracted from company documents."""

    id: str  # slug: "engineering", "customer_support"
    name: str
    description: str
    context: str = ""  # department-level context paragraph
    vocabulary: list[dict[str, str]] = field(default_factory=list)
    source_docs: list[str] = field(default_factory=list)  # file_ids


@dataclass
class ExtractedRole:
    """A role extracted from company documents."""

    id: str
    name: str
    department_id: str
    description: str
    persona: str = ""  # system prompt personality text
    principles: list[str] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)
    manages: list[str] = field(default_factory=list)  # sub-role IDs
    connectors: list[str] = field(default_factory=list)
    source_docs: list[str] = field(default_factory=list)


@dataclass
class ExtractedSkill:
    """A skill definition extracted from SOPs / playbooks."""

    id: str
    name: str
    role_id: str
    department_id: str
    description: str
    category: str = "general"  # from SkillDefinition category enum
    system_supplement: str = ""
    prompt_template: str = ""
    output_format: str = ""
    business_guidance: str = ""
    model: str = "sonnet"  # haiku / sonnet / opus
    tools_required: list[str] = field(default_factory=list)
    source_docs: list[str] = field(default_factory=list)


@dataclass
class ExtractedMemory:
    """A seed memory to inject into a role's context."""

    role_id: str
    department_id: str
    memory_type: str  # "insight", "decision", "relationship", "steward_note"
    title: str
    content: str
    confidence: float = 0.8
    source_doc: str = ""


@dataclass
class ExtractedKnowledge:
    """All knowledge extracted from a batch of classified documents."""

    departments: list[ExtractedDepartment] = field(default_factory=list)
    roles: list[ExtractedRole] = field(default_factory=list)
    skills: list[ExtractedSkill] = field(default_factory=list)
    memories: list[ExtractedMemory] = field(default_factory=list)


# =====================================================================
# Pipeline stage 4: Complete bootstrap plan
# =====================================================================


@dataclass
class BootstrapPlan:
    """Complete proposed configuration -- reviewed by human before execution.

    Nothing is written to the database until the plan is approved.
    """

    id: str = field(default_factory=lambda: f"bootstrap_{uuid.uuid4().hex[:12]}")
    source_folder_id: str = ""
    documents_crawled: int = 0
    documents_classified: int = 0
    documents_extracted: int = 0
    departments: list[ExtractedDepartment] = field(default_factory=list)
    roles: list[ExtractedRole] = field(default_factory=list)
    skills: list[ExtractedSkill] = field(default_factory=list)
    memories: list[ExtractedMemory] = field(default_factory=list)
    estimated_cost: float = 0.0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    status: str = BootstrapStatus.DRAFT.value
    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """Return a concise summary for Slack / API display."""
        return {
            "id": self.id,
            "status": self.status,
            "source_folder_id": self.source_folder_id,
            "documents_crawled": self.documents_crawled,
            "departments": len(self.departments),
            "roles": len(self.roles),
            "skills": len(self.skills),
            "memories": len(self.memories),
            "estimated_cost": f"${self.estimated_cost:.2f}",
            "errors": len(self.errors),
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize the full plan to a dictionary."""
        return {
            "id": self.id,
            "source_folder_id": self.source_folder_id,
            "documents_crawled": self.documents_crawled,
            "documents_classified": self.documents_classified,
            "documents_extracted": self.documents_extracted,
            "departments": [_dept_to_dict(d) for d in self.departments],
            "roles": [_role_to_dict(r) for r in self.roles],
            "skills": [_skill_to_dict(s) for s in self.skills],
            "memories": [_memory_to_dict(m) for m in self.memories],
            "estimated_cost": self.estimated_cost,
            "created_at": self.created_at,
            "status": self.status,
            "errors": self.errors,
        }


# =====================================================================
# Execution result
# =====================================================================


@dataclass
class ExecutionResult:
    """Result of executing an approved BootstrapPlan."""

    plan_id: str
    departments_created: int = 0
    roles_created: int = 0
    skills_created: int = 0
    memories_seeded: int = 0
    departments_skipped: int = 0
    roles_skipped: int = 0
    skills_skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "success": self.success,
            "created": {
                "departments": self.departments_created,
                "roles": self.roles_created,
                "skills": self.skills_created,
                "memories": self.memories_seeded,
            },
            "skipped": {
                "departments": self.departments_skipped,
                "roles": self.roles_skipped,
                "skills": self.skills_skipped,
            },
            "errors": self.errors,
        }


# =====================================================================
# Serialization helpers
# =====================================================================


def _dept_to_dict(d: ExtractedDepartment) -> dict[str, Any]:
    return {
        "id": d.id,
        "name": d.name,
        "description": d.description,
        "context": d.context,
        "vocabulary": d.vocabulary,
        "source_docs": d.source_docs,
    }


def _role_to_dict(r: ExtractedRole) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "department_id": r.department_id,
        "description": r.description,
        "persona": r.persona,
        "principles": r.principles,
        "goals": r.goals,
        "manages": r.manages,
        "connectors": r.connectors,
        "source_docs": r.source_docs,
    }


def _skill_to_dict(s: ExtractedSkill) -> dict[str, Any]:
    return {
        "id": s.id,
        "name": s.name,
        "role_id": s.role_id,
        "department_id": s.department_id,
        "description": s.description,
        "category": s.category,
        "system_supplement": s.system_supplement,
        "prompt_template": s.prompt_template,
        "output_format": s.output_format,
        "business_guidance": s.business_guidance,
        "model": s.model,
        "tools_required": s.tools_required,
        "source_docs": s.source_docs,
    }


def _memory_to_dict(m: ExtractedMemory) -> dict[str, Any]:
    return {
        "role_id": m.role_id,
        "department_id": m.department_id,
        "memory_type": m.memory_type,
        "title": m.title,
        "content": m.content,
        "confidence": m.confidence,
        "source_doc": m.source_doc,
    }
