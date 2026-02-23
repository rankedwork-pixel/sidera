"""Haiku-powered document classifier for the bootstrap pipeline.

Classifies crawled documents into categories (org_structure, sop_playbook,
goals_kpis, etc.) so the extractor only processes relevant documents for
each extraction pass.

Uses ``call_claude_api()`` with Haiku for cheap, fast classification
(~$0.001 per batch of 10 documents).
"""

from __future__ import annotations

import json

import structlog

from src.agent.api_client import call_claude_api
from src.bootstrap.models import ClassifiedDocument, DocumentCategory, RawDocument
from src.bootstrap.prompts import (
    CLASSIFY_SYSTEM_PROMPT,
    CLASSIFY_USER_TEMPLATE,
    format_doc_for_classification,
)
from src.config import settings
from src.llm.provider import TaskType

logger = structlog.get_logger(__name__)

# Number of documents to classify per LLM call.
_BATCH_SIZE = 10


async def classify_documents(
    docs: list[RawDocument],
    *,
    batch_size: int = _BATCH_SIZE,
) -> list[ClassifiedDocument]:
    """Classify a list of raw documents by category.

    Parameters
    ----------
    docs:
        Raw documents from the crawler.
    batch_size:
        Number of documents per LLM call (default 10).

    Returns
    -------
    list[ClassifiedDocument]
        Classified documents sorted by relevance (irrelevant filtered out).
    """
    classified: list[ClassifiedDocument] = []
    total_cost = 0.0

    for i in range(0, len(docs), batch_size):
        batch = docs[i : i + batch_size]
        batch_results, cost = await _classify_batch(batch)
        classified.extend(batch_results)
        total_cost += cost

    # Sort: relevant documents first, then by confidence descending
    classified.sort(key=lambda d: (not d.is_relevant, -d.confidence))

    relevant_count = sum(1 for d in classified if d.is_relevant)
    logger.info(
        "bootstrap.classify_complete",
        total_docs=len(docs),
        classified=len(classified),
        relevant=relevant_count,
        irrelevant=len(classified) - relevant_count,
        cost=f"${total_cost:.4f}",
    )

    return classified


async def _classify_batch(
    batch: list[RawDocument],
) -> tuple[list[ClassifiedDocument], float]:
    """Classify a single batch of documents via Haiku."""
    # Format documents for the prompt
    doc_texts = []
    for doc in batch:
        doc_texts.append(format_doc_for_classification(doc.file_id, doc.title, doc.content))
    documents_block = "\n\n".join(doc_texts)

    user_message = CLASSIFY_USER_TEMPLATE.format(documents=documents_block)

    try:
        result = await call_claude_api(
            model=settings.model_fast,  # Haiku
            system_prompt=CLASSIFY_SYSTEM_PROMPT,
            user_message=user_message,
            max_tokens=2048,
            task_type=TaskType.GENERAL,
        )
    except Exception as exc:
        logger.warning("bootstrap.classify_batch_error", error=str(exc))
        # On failure, mark all docs as irrelevant so pipeline continues
        return [
            _make_classified(doc, [DocumentCategory.IRRELEVANT.value], 0.0) for doc in batch
        ], 0.0

    cost = result.get("cost", {}).get("total_cost_usd", 0.0)
    text = result.get("text", "")

    # Parse the JSON response
    classifications = _parse_classification_response(text)

    # Match classifications back to documents
    classified: list[ClassifiedDocument] = []
    doc_lookup = {doc.file_id: doc for doc in batch}

    for item in classifications:
        file_id = item.get("file_id", "")
        categories = item.get("categories", [])
        confidence = item.get("confidence", 0.5)

        if file_id in doc_lookup:
            doc = doc_lookup.pop(file_id)
            # Validate categories against the enum
            valid_cats = [c for c in categories if c in {e.value for e in DocumentCategory}]
            if not valid_cats:
                valid_cats = [DocumentCategory.IRRELEVANT.value]
            classified.append(_make_classified(doc, valid_cats, confidence))

    # Any docs not in the response get classified as irrelevant
    for doc in doc_lookup.values():
        classified.append(_make_classified(doc, [DocumentCategory.IRRELEVANT.value], 0.0))

    return classified, cost


def _make_classified(
    doc: RawDocument, categories: list[str], confidence: float
) -> ClassifiedDocument:
    """Create a ClassifiedDocument from a RawDocument."""
    return ClassifiedDocument(
        file_id=doc.file_id,
        title=doc.title,
        mime_type=doc.mime_type,
        content=doc.content,
        categories=categories,
        confidence=confidence,
        char_count=doc.char_count,
        folder_path=doc.folder_path,
    )


def _parse_classification_response(text: str) -> list[dict]:
    """Parse the LLM's JSON classification response.

    Handles common issues: markdown fences, trailing commas, etc.
    """
    # Strip markdown fences if present
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Remove opening fence (possibly with language tag)
        first_newline = cleaned.index("\n")
        cleaned = cleaned[first_newline + 1 :]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
        return []
    except json.JSONDecodeError:
        logger.warning("bootstrap.classify_parse_error", raw_text=text[:200])
        return []
