"""Sonnet-powered knowledge extraction for the bootstrap pipeline.

Processes classified documents in three passes:

1. **Org structure** -- departments, roles, hierarchy from org_structure docs
2. **Skills** -- skill definitions from sop_playbook + decision_tree docs
3. **Context** -- goals, principles, vocabulary, memories from goals_kpis + vocabulary docs

Each pass sends relevant documents to ``call_claude_api()`` with Sonnet
and parses the structured JSON response into bootstrap data models.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from src.agent.api_client import call_claude_api
from src.bootstrap.models import (
    ClassifiedDocument,
    DocumentCategory,
    ExtractedDepartment,
    ExtractedKnowledge,
    ExtractedMemory,
    ExtractedRole,
    ExtractedSkill,
)
from src.bootstrap.prompts import (
    EXTRACT_CONTEXT_SYSTEM_PROMPT,
    EXTRACT_CONTEXT_USER_TEMPLATE,
    EXTRACT_ORG_SYSTEM_PROMPT,
    EXTRACT_ORG_USER_TEMPLATE,
    EXTRACT_SKILLS_SYSTEM_PROMPT,
    EXTRACT_SKILLS_USER_TEMPLATE,
)
from src.config import settings
from src.llm.provider import TaskType

logger = structlog.get_logger(__name__)

# Maximum characters of document content to send per extraction call.
# Keeps Sonnet input costs manageable.
_MAX_CONTENT_PER_CALL = 30_000


async def extract_knowledge(
    classified: list[ClassifiedDocument],
) -> tuple[ExtractedKnowledge, float]:
    """Extract structured knowledge from classified documents.

    Parameters
    ----------
    classified:
        Documents that have been classified (irrelevant already filtered).

    Returns
    -------
    tuple[ExtractedKnowledge, float]
        The extracted knowledge and total LLM cost.
    """
    total_cost = 0.0
    knowledge = ExtractedKnowledge()

    # --- Pass 1: Org structure ---
    org_docs = _filter_by_categories(
        classified, {DocumentCategory.ORG_STRUCTURE.value}
    )
    if org_docs:
        depts, roles, cost = await _extract_org_structure(org_docs)
        knowledge.departments = depts
        knowledge.roles = roles
        total_cost += cost
        logger.info(
            "bootstrap.extract_org_complete",
            departments=len(depts),
            roles=len(roles),
            docs_processed=len(org_docs),
        )

    # --- Pass 2: Skills from SOPs ---
    skill_docs = _filter_by_categories(
        classified,
        {
            DocumentCategory.SOP_PLAYBOOK.value,
            DocumentCategory.DECISION_TREE.value,
        },
    )
    if skill_docs and knowledge.roles:
        skills, cost = await _extract_skills(skill_docs, knowledge.roles)
        knowledge.skills = skills
        total_cost += cost
        logger.info(
            "bootstrap.extract_skills_complete",
            skills=len(skills),
            docs_processed=len(skill_docs),
        )

    # --- Pass 3: Context (goals, vocabulary, memories) ---
    context_docs = _filter_by_categories(
        classified,
        {
            DocumentCategory.GOALS_KPIS.value,
            DocumentCategory.VOCABULARY_GLOSSARY.value,
            DocumentCategory.MEETING_NOTES.value,
        },
    )
    if context_docs and knowledge.roles:
        goals_vocab_memories, cost = await _extract_context(
            context_docs, knowledge.departments, knowledge.roles
        )
        # Merge goals/principles into existing roles
        _merge_context_into_roles(knowledge.roles, goals_vocab_memories)
        # Merge vocabulary into existing departments
        _merge_vocabulary_into_departments(
            knowledge.departments, goals_vocab_memories
        )
        knowledge.memories = goals_vocab_memories.get("memories", [])
        total_cost += cost
        logger.info(
            "bootstrap.extract_context_complete",
            memories=len(knowledge.memories),
            docs_processed=len(context_docs),
        )

    logger.info(
        "bootstrap.extract_all_complete",
        departments=len(knowledge.departments),
        roles=len(knowledge.roles),
        skills=len(knowledge.skills),
        memories=len(knowledge.memories),
        total_cost=f"${total_cost:.4f}",
    )

    return knowledge, total_cost


# =====================================================================
# Pass 1: Org structure
# =====================================================================


async def _extract_org_structure(
    docs: list[ClassifiedDocument],
) -> tuple[list[ExtractedDepartment], list[ExtractedRole], float]:
    """Extract departments and roles from org-structure documents.

    Large document sets are split into batches. Results from multiple
    batches are merged (the generator's dedup logic handles overlaps).
    """
    batches = _prepare_doc_batches(docs)
    all_departments: list[ExtractedDepartment] = []
    all_roles: list[ExtractedRole] = []
    total_cost = 0.0

    for batch_idx, doc_content in enumerate(batches):
        user_message = EXTRACT_ORG_USER_TEMPLATE.format(documents=doc_content)

        try:
            result = await call_claude_api(
                model=settings.model_standard,  # Sonnet
                system_prompt=EXTRACT_ORG_SYSTEM_PROMPT,
                user_message=user_message,
                max_tokens=4096,
                task_type=TaskType.GENERAL,
            )
        except Exception as exc:
            logger.warning(
                "bootstrap.extract_org_error",
                error=str(exc),
                batch=batch_idx,
            )
            continue

        total_cost += result.get("cost", {}).get("total_cost_usd", 0.0)
        parsed = _parse_json_response(result.get("text", ""))

        for dept_data in parsed.get("departments", []):
            all_departments.append(
                ExtractedDepartment(
                    id=dept_data.get("id", ""),
                    name=dept_data.get("name", ""),
                    description=dept_data.get("description", ""),
                    context=dept_data.get("context", ""),
                    vocabulary=dept_data.get("vocabulary", []),
                    source_docs=[d.file_id for d in docs],
                )
            )

        for role_data in parsed.get("roles", []):
            all_roles.append(
                ExtractedRole(
                    id=role_data.get("id", ""),
                    name=role_data.get("name", ""),
                    department_id=role_data.get("department_id", ""),
                    description=role_data.get("description", ""),
                    persona=role_data.get("persona", ""),
                    principles=role_data.get("principles", []),
                    goals=role_data.get("goals", []),
                    manages=role_data.get("manages", []),
                    source_docs=[d.file_id for d in docs],
                )
            )

    return all_departments, all_roles, total_cost


# =====================================================================
# Pass 2: Skills
# =====================================================================


async def _extract_skills(
    docs: list[ClassifiedDocument],
    roles: list[ExtractedRole],
) -> tuple[list[ExtractedSkill], float]:
    """Extract skill definitions from SOP/playbook documents.

    Large document sets are split into batches. Results from multiple
    batches are merged (the generator's dedup logic handles overlaps).
    """
    batches = _prepare_doc_batches(docs)

    # Format existing roles for context
    roles_summary = "\n".join(
        f"- {r.id} ({r.name}): {r.description} [dept: {r.department_id}]"
        for r in roles
    )

    all_skills: list[ExtractedSkill] = []
    total_cost = 0.0
    valid_role_ids = {r.id for r in roles}

    for batch_idx, doc_content in enumerate(batches):
        user_message = EXTRACT_SKILLS_USER_TEMPLATE.format(
            documents=doc_content, roles=roles_summary
        )

        try:
            result = await call_claude_api(
                model=settings.model_standard,  # Sonnet
                system_prompt=EXTRACT_SKILLS_SYSTEM_PROMPT,
                user_message=user_message,
                max_tokens=8192,
                task_type=TaskType.GENERAL,
            )
        except Exception as exc:
            logger.warning(
                "bootstrap.extract_skills_error",
                error=str(exc),
                batch=batch_idx,
            )
            continue

        total_cost += result.get("cost", {}).get("total_cost_usd", 0.0)
        parsed = _parse_json_response(result.get("text", ""))

        for skill_data in parsed.get("skills", []):
            role_id = skill_data.get("role_id", "")
            # Validate role_id references an extracted role
            if role_id and role_id not in valid_role_ids:
                logger.debug(
                    "bootstrap.skill_unknown_role",
                    skill_id=skill_data.get("id"),
                    role_id=role_id,
                )
                # Assign to first role in the same department, or skip
                dept_id = skill_data.get("department_id", "")
                dept_roles = [r for r in roles if r.department_id == dept_id]
                role_id = dept_roles[0].id if dept_roles else roles[0].id

            all_skills.append(
                ExtractedSkill(
                    id=skill_data.get("id", ""),
                    name=skill_data.get("name", ""),
                    role_id=role_id,
                    department_id=skill_data.get("department_id", ""),
                    description=skill_data.get("description", ""),
                    category=skill_data.get("category", "general"),
                    system_supplement=skill_data.get("system_supplement", ""),
                    prompt_template=skill_data.get("prompt_template", ""),
                    output_format=skill_data.get("output_format", ""),
                    business_guidance=skill_data.get("business_guidance", ""),
                    model=skill_data.get("model", "sonnet"),
                    tools_required=skill_data.get("tools_required", []),
                    source_docs=[d.file_id for d in docs],
                )
            )

    return all_skills, total_cost


# =====================================================================
# Pass 3: Context (goals, vocabulary, memories)
# =====================================================================


async def _extract_context(
    docs: list[ClassifiedDocument],
    departments: list[ExtractedDepartment],
    roles: list[ExtractedRole],
) -> tuple[dict[str, Any], float]:
    """Extract goals, principles, vocabulary, and memories.

    Large document sets are split into batches. Results from multiple
    batches are merged.
    """
    batches = _prepare_doc_batches(docs)

    # Format existing org structure for context
    org_summary_parts = ["Departments:"]
    for d in departments:
        org_summary_parts.append(f"  - {d.id} ({d.name}): {d.description}")

    org_summary_parts.append("\nRoles:")
    for r in roles:
        manages_str = f", manages: [{', '.join(r.manages)}]" if r.manages else ""
        org_summary_parts.append(
            f"  - {r.id} ({r.name}) [dept: {r.department_id}]{manages_str}"
        )

    org_structure = "\n".join(org_summary_parts)

    all_role_goals: list[dict[str, Any]] = []
    all_role_principles: list[dict[str, Any]] = []
    all_dept_vocab: list[dict[str, Any]] = []
    all_memories: list[ExtractedMemory] = []
    total_cost = 0.0

    for batch_idx, doc_content in enumerate(batches):
        user_message = EXTRACT_CONTEXT_USER_TEMPLATE.format(
            documents=doc_content, org_structure=org_structure
        )

        try:
            result = await call_claude_api(
                model=settings.model_standard,  # Sonnet
                system_prompt=EXTRACT_CONTEXT_SYSTEM_PROMPT,
                user_message=user_message,
                max_tokens=4096,
                task_type=TaskType.GENERAL,
            )
        except Exception as exc:
            logger.warning(
                "bootstrap.extract_context_error",
                error=str(exc),
                batch=batch_idx,
            )
            continue

        total_cost += result.get("cost", {}).get("total_cost_usd", 0.0)
        parsed = _parse_json_response(result.get("text", ""))

        all_role_goals.extend(parsed.get("role_goals", []))
        all_role_principles.extend(parsed.get("role_principles", []))
        all_dept_vocab.extend(parsed.get("department_vocabulary", []))

        # Parse memories
        for mem_data in parsed.get("memories", []):
            all_memories.append(
                ExtractedMemory(
                    role_id=mem_data.get("role_id", ""),
                    department_id=mem_data.get("department_id", ""),
                    memory_type=mem_data.get("memory_type", "insight"),
                    title=mem_data.get("title", ""),
                    content=mem_data.get("content", ""),
                    confidence=mem_data.get("confidence", 0.8),
                    source_doc="bootstrap",
                )
            )

    return {
        "role_goals": all_role_goals,
        "role_principles": all_role_principles,
        "department_vocabulary": all_dept_vocab,
        "memories": all_memories,
    }, total_cost


# =====================================================================
# Merge helpers
# =====================================================================


def _merge_context_into_roles(
    roles: list[ExtractedRole], context: dict[str, Any]
) -> None:
    """Merge extracted goals and principles into existing role objects."""
    role_map = {r.id: r for r in roles}

    for entry in context.get("role_goals", []):
        role_id = entry.get("role_id", "")
        if role_id in role_map:
            role_map[role_id].goals.extend(entry.get("goals", []))

    for entry in context.get("role_principles", []):
        role_id = entry.get("role_id", "")
        if role_id in role_map:
            role_map[role_id].principles.extend(entry.get("principles", []))


def _merge_vocabulary_into_departments(
    departments: list[ExtractedDepartment], context: dict[str, Any]
) -> None:
    """Merge extracted vocabulary into existing department objects."""
    dept_map = {d.id: d for d in departments}

    for entry in context.get("department_vocabulary", []):
        dept_id = entry.get("department_id", "")
        if dept_id in dept_map:
            existing_terms = {v.get("term") for v in dept_map[dept_id].vocabulary}
            for vocab in entry.get("vocabulary", []):
                if vocab.get("term") not in existing_terms:
                    dept_map[dept_id].vocabulary.append(vocab)


# =====================================================================
# Utilities
# =====================================================================


def _filter_by_categories(
    docs: list[ClassifiedDocument], categories: set[str]
) -> list[ClassifiedDocument]:
    """Filter documents that have at least one matching category."""
    return [d for d in docs if set(d.categories) & categories]


def _prepare_doc_content(docs: list[ClassifiedDocument]) -> str:
    """Format document content for an extraction prompt.

    Concatenates documents up to ``_MAX_CONTENT_PER_CALL`` characters.
    """
    parts: list[str] = []
    total_chars = 0

    for doc in docs:
        # Budget remaining space
        remaining = _MAX_CONTENT_PER_CALL - total_chars
        if remaining <= 500:
            break

        content = doc.content[:remaining]
        part = (
            f'---\nDocument: "{doc.title}"\n'
            f"Folder: {doc.folder_path or '(root)'}\n"
            f"Content:\n{content}\n"
        )
        parts.append(part)
        total_chars += len(part)

    return "\n".join(parts)


# Maximum characters per chunk (leaves room for prompt overhead)
_MAX_CHUNK_CHARS = 25_000
_CHUNK_OVERLAP = 2_000


def _chunk_document(
    content: str,
    max_chars: int = _MAX_CHUNK_CHARS,
    overlap: int = _CHUNK_OVERLAP,
) -> list[str]:
    """Split a large document into overlapping chunks.

    Short documents (≤ max_chars) are returned as a single chunk.
    Long documents are split with a sliding window so context at
    chunk boundaries is preserved.

    Parameters
    ----------
    content:
        The raw document text.
    max_chars:
        Maximum characters per chunk.
    overlap:
        Number of overlapping characters between consecutive chunks.

    Returns
    -------
    list[str]
        One or more chunks of document content.
    """
    if len(content) <= max_chars:
        return [content]

    chunks: list[str] = []
    start = 0
    step = max_chars - overlap

    while start < len(content):
        end = start + max_chars
        chunks.append(content[start:end])
        if end >= len(content):
            break
        start += step

    return chunks


def _prepare_doc_batches(
    docs: list[ClassifiedDocument],
    max_chars_per_batch: int = _MAX_CONTENT_PER_CALL,
) -> list[str]:
    """Chunk large documents and pack into batches for LLM calls.

    Each batch is a formatted string of document content that fits
    within ``max_chars_per_batch``.  Large documents are first split
    into overlapping chunks; multi-part chunks are labeled (e.g.
    "Engineering Handbook (part 2/4)").

    Parameters
    ----------
    docs:
        Classified documents to prepare.
    max_chars_per_batch:
        Maximum characters per batch.

    Returns
    -------
    list[str]
        One or more formatted batch strings ready for LLM prompts.
    """
    # Build list of (title, folder_path, content) fragments
    fragments: list[tuple[str, str, str]] = []

    for doc in docs:
        chunks = _chunk_document(doc.content)
        if len(chunks) == 1:
            fragments.append((doc.title, doc.folder_path or "(root)", chunks[0]))
        else:
            for i, chunk in enumerate(chunks, 1):
                title = f"{doc.title} (part {i}/{len(chunks)})"
                fragments.append((title, doc.folder_path or "(root)", chunk))

    # Pack fragments into batches
    batches: list[str] = []
    current_parts: list[str] = []
    current_chars = 0

    for title, folder, content in fragments:
        part = (
            f'---\nDocument: "{title}"\n'
            f"Folder: {folder}\n"
            f"Content:\n{content}\n"
        )
        part_len = len(part)

        if current_chars + part_len > max_chars_per_batch and current_parts:
            batches.append("\n".join(current_parts))
            current_parts = []
            current_chars = 0

        current_parts.append(part)
        current_chars += part_len

    if current_parts:
        batches.append("\n".join(current_parts))

    return batches or [""]


def _parse_json_response(text: str) -> dict[str, Any]:
    """Parse a JSON response from the LLM, stripping markdown fences."""
    cleaned = text.strip()

    if cleaned.startswith("```"):
        first_newline = cleaned.index("\n") if "\n" in cleaned else len(cleaned)
        cleaned = cleaned[first_newline + 1 :]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
        return {}
    except json.JSONDecodeError:
        logger.warning("bootstrap.json_parse_error", raw_text=text[:200])
        return {}
