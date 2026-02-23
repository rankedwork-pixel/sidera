"""Memory consolidation engine.

Runs a weekly Haiku call to identify duplicate, overlapping, or redundant
memories for a given (user_id, role_id) pair and merges them into single
consolidated entries.

Enhancements over v1:
- **Pre-clustering**: keyword-based similarity grouping (Jaccard) before
  the LLM call produces better merge groups and reduces cognitive load.
- **Confidence boosting**: when multiple memories agree on a finding, the
  consolidated memory gets a higher confidence score.
- **Summary generation**: clusters with 5+ members produce a high-level
  ``[Summary]`` entry that captures the recurring pattern.

The consolidation prompt instructs Haiku to return a JSON array of
consolidation groups.  Each group specifies which source memories to
merge, the resulting type/title/content, and a confidence score.

Usage::

    from src.skills.consolidation import consolidate_role_memories

    memories = await db_service.get_unconsolidated_memories(session, ...)
    stats = await consolidate_role_memories(user_id, role_id, dept_id, memories)
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Stopwords for keyword extraction (lightweight, no ML dependencies)
# ---------------------------------------------------------------------------

_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "are",
        "but",
        "not",
        "you",
        "all",
        "can",
        "had",
        "her",
        "was",
        "one",
        "our",
        "out",
        "has",
        "his",
        "how",
        "its",
        "may",
        "new",
        "now",
        "old",
        "see",
        "way",
        "who",
        "did",
        "get",
        "let",
        "say",
        "she",
        "too",
        "use",
        "from",
        "been",
        "have",
        "into",
        "more",
        "than",
        "that",
        "them",
        "then",
        "they",
        "this",
        "what",
        "when",
        "will",
        "with",
        "each",
        "make",
        "like",
        "over",
        "such",
        "also",
        "back",
        "only",
        "come",
        "made",
        "find",
        "here",
        "many",
        "some",
        "take",
        "want",
        "give",
        "most",
        "very",
        "after",
        "just",
        "about",
        "being",
        "could",
        "would",
        "should",
        "there",
        "their",
        "which",
        "these",
        "other",
        "where",
        "before",
        "still",
        "between",
        "does",
        "were",
        "your",
    }
)


# ---------------------------------------------------------------------------
# Clustering helpers
# ---------------------------------------------------------------------------


def _extract_keywords(text: str) -> set[str]:
    """Extract keywords from text for similarity comparison.

    Lowercases, removes non-alpha characters, filters stopwords and
    short words (< 3 chars).

    Args:
        text: Input text (title + content).

    Returns:
        Set of keyword strings.
    """
    words = re.findall(r"[a-z]+", text.lower())
    return {w for w in words if len(w) >= 3 and w not in _STOPWORDS}


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    """Compute Jaccard similarity between two keyword sets.

    Returns 0.0 if both sets are empty.
    """
    if not a and not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


def cluster_memories_by_similarity(
    memories: list[Any],
    min_cluster_size: int = 2,
    similarity_threshold: float = 0.3,
) -> list[list[Any]]:
    """Group memories by keyword overlap (lightweight similarity).

    Uses a greedy clustering approach:
    1. Extract keywords from each memory's title + content.
    2. For each memory, find the first existing cluster whose centroid
       keywords have Jaccard similarity > threshold.
    3. If found, add to that cluster; otherwise start a new cluster.
    4. Filter out clusters smaller than ``min_cluster_size``.

    O(n^2) but n is capped at ~50 per batch, so performance is fine.

    Args:
        memories: List of RoleMemory ORM objects.
        min_cluster_size: Minimum memories per cluster to keep.
        similarity_threshold: Jaccard similarity threshold for inclusion.

    Returns:
        List of clusters (each cluster is a list of memories).
        Memories not in any cluster are excluded.
    """
    if not memories:
        return []

    # Pre-compute keyword sets
    keyword_sets: list[set[str]] = []
    for mem in memories:
        title = getattr(mem, "title", "")
        content = getattr(mem, "content", "")
        keyword_sets.append(_extract_keywords(f"{title} {content}"))

    # Greedy clustering
    clusters: list[list[int]] = []
    cluster_keywords: list[set[str]] = []

    for idx, kw in enumerate(keyword_sets):
        best_cluster = -1
        best_sim = 0.0

        for ci, ckw in enumerate(cluster_keywords):
            sim = _jaccard_similarity(kw, ckw)
            if sim > similarity_threshold and sim > best_sim:
                best_sim = sim
                best_cluster = ci

        if best_cluster >= 0:
            clusters[best_cluster].append(idx)
            # Update centroid keywords (union)
            cluster_keywords[best_cluster] |= kw
        else:
            clusters.append([idx])
            cluster_keywords.append(set(kw))

    # Filter by min size and convert indices to memory objects
    return [
        [memories[i] for i in cluster] for cluster in clusters if len(cluster) >= min_cluster_size
    ]


# ---------------------------------------------------------------------------
# Confidence boosting
# ---------------------------------------------------------------------------


def apply_confidence_boosting(
    source_count: int,
    max_source_confidence: float,
) -> float:
    """Boost confidence when multiple memories agree.

    Rules:
    - 2 sources: max_confidence + 0.05
    - 3 sources: max_confidence + 0.10
    - 4+ sources: max_confidence + 0.15
    - Always capped at 0.95 (steward notes at 1.0 stay supreme)

    Args:
        source_count: Number of source memories being merged.
        max_source_confidence: Highest confidence among sources.

    Returns:
        Boosted confidence value.
    """
    if source_count <= 1:
        return max_source_confidence

    if source_count == 2:
        boost = 0.05
    elif source_count == 3:
        boost = 0.10
    else:
        boost = 0.15

    return min(0.95, max_source_confidence + boost)


# ---------------------------------------------------------------------------
# Prompt and formatting
# ---------------------------------------------------------------------------


_CONSOLIDATION_PROMPT = """\
You are a memory consolidation system.  You receive a numbered list of memories
belonging to a single AI role, optionally pre-grouped by topic similarity.
Your job is to identify groups of memories that are duplicates, near-duplicates,
or highly overlapping and should be merged into a single consolidated memory.

Rules:
- Each group must have >= 2 source memories.
- Keep the most specific, actionable, and recent information from each source.
- Preserve important details (dates, numbers, names).  Do not generalize away facts.
- Assign each consolidated memory the most appropriate type.
- CONFIDENCE BOOSTING: when multiple memories agree on the same finding,
  set the consolidated confidence HIGHER than any individual source.
  Use: 2 sources = max + 0.05, 3 = max + 0.10, 4+ = max + 0.15 (cap at 0.95).
- SUMMARY GENERATION: when a cluster has 5+ memories about the same topic,
  create a high-level summary that captures the key pattern across all of them.
  Prefix the title with "[Summary]".
- For "relationship" type memories: merge all relationship observations about
  the same person into a single updated relationship profile.  Keep the most
  recent behavioral observations.  This is a living portrait, not a log.
- If a memory is unique and doesn't overlap with others, leave it OUT of any group.
- Return an empty array [] if nothing should be consolidated.

Respond with ONLY a JSON array (no markdown fences).  Each element:
{{
  "source_ids": [<int>, <int>, ...],
  "type": "<decision|anomaly|pattern|insight|lesson|relationship>",
  "title": "<concise title, max 100 chars>",
  "content": "<merged content, max 500 chars>",
  "confidence": <float 0.0-1.0>,
  "is_summary": <boolean, true if this is a high-level summary of 5+ memories>
}}
"""


def _format_memories_for_prompt(
    memories: list[Any],
    clusters: list[list[Any]] | None = None,
) -> str:
    """Format memories as a numbered list for the consolidation prompt.

    When ``clusters`` are provided, memories are organized under
    ``[Cluster N]`` headers with unclustered memories listed separately
    at the end.

    Args:
        memories: List of RoleMemory ORM objects.
        clusters: Optional pre-computed clusters from
            ``cluster_memories_by_similarity()``.

    Returns:
        Formatted string with one memory per numbered line.
    """

    def _fmt_memory(mem: Any) -> str:
        mid = getattr(mem, "id", "?")
        mtype = getattr(mem, "memory_type", "?")
        title = getattr(mem, "title", "")
        content = getattr(mem, "content", "")
        confidence = getattr(mem, "confidence", 1.0)
        created = getattr(mem, "created_at", None)
        date_str = created.strftime("%Y-%m-%d") if created else "?"

        if len(content) > 300:
            content = content[:297] + "..."

        return f"{mid}. [{date_str}] [{mtype}] (conf={confidence:.1f}) {title}: {content}"

    if not clusters:
        return "\n".join(_fmt_memory(mem) for mem in memories)

    # Build clustered output
    lines: list[str] = []
    clustered_ids: set[Any] = set()

    for ci, cluster in enumerate(clusters, 1):
        lines.append(f"\n[Cluster {ci}] ({len(cluster)} memories)")
        for mem in cluster:
            lines.append(_fmt_memory(mem))
            clustered_ids.add(getattr(mem, "id", None))

    # Add unclustered memories
    unclustered = [mem for mem in memories if getattr(mem, "id", None) not in clustered_ids]
    if unclustered:
        lines.append("\n[Unclustered]")
        for mem in unclustered:
            lines.append(_fmt_memory(mem))

    return "\n".join(lines)


def _parse_consolidation_response(raw_text: str) -> list[dict[str, Any]]:
    """Parse the JSON response from the consolidation LLM.

    Handles markdown code fences and extracts the JSON array.

    Args:
        raw_text: Raw LLM response text.

    Returns:
        Parsed list of consolidation group dicts, or empty list on error.
    """
    text = raw_text.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3].strip()

    # Try to find JSON array in the text
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        text = match.group()

    try:
        result = json.loads(text)
        if not isinstance(result, list):
            return []
        return result
    except (json.JSONDecodeError, ValueError):
        return []


# ---------------------------------------------------------------------------
# Main consolidation function
# ---------------------------------------------------------------------------


async def consolidate_role_memories(
    user_id: str,
    role_id: str,
    department_id: str,
    memories: list[Any],
) -> dict[str, Any]:
    """Run consolidation on a batch of memories.

    Enhanced pipeline:
    1. Pre-cluster memories by keyword similarity.
    2. Format with cluster annotations for the LLM.
    3. Call Haiku with enhanced consolidation prompt.
    4. Apply confidence boosting post-hoc (don't trust LLM's math).
    5. Save consolidated + summary memories to DB.

    Args:
        user_id: The user these memories belong to.
        role_id: The role these memories belong to.
        department_id: The department the role belongs to.
        memories: List of RoleMemory ORM objects to consolidate.

    Returns:
        Stats dict with keys: consolidated_count, originals_marked,
        summaries_created, cost_usd, errors.
    """
    stats: dict[str, Any] = {
        "consolidated_count": 0,
        "originals_marked": 0,
        "summaries_created": 0,
        "cost_usd": 0.0,
        "errors": [],
    }

    if len(memories) < 2:
        return stats

    from src.agent.api_client import run_agent_loop
    from src.config import settings
    from src.llm.provider import TaskType

    # Step 1: Pre-cluster memories by keyword similarity
    clusters = cluster_memories_by_similarity(memories)
    if clusters:
        logger.info(
            "consolidation.clusters_found",
            user_id=user_id,
            role_id=role_id,
            num_clusters=len(clusters),
            cluster_sizes=[len(c) for c in clusters],
        )

    # Step 2: Format with cluster annotations
    memory_list = _format_memories_for_prompt(memories, clusters=clusters)
    prompt = _CONSOLIDATION_PROMPT + f"\n\nMemories:\n{memory_list}"

    # Step 3: Call Haiku
    try:
        result = await run_agent_loop(
            system_prompt=(
                "You are a memory consolidation system. Respond only with "
                "a valid JSON array. No markdown, no explanation."
            ),
            user_prompt=prompt,
            model=settings.model_fast,  # Haiku
            tools=None,
            max_turns=1,
            task_type=TaskType.MEMORY_CONSOLIDATION,
        )
        stats["cost_usd"] = result.cost.get("total_cost_usd", 0.0) if result.cost else 0.0
    except Exception as exc:
        logger.warning(
            "consolidation.llm_error",
            user_id=user_id,
            role_id=role_id,
            error=str(exc),
        )
        stats["errors"].append(f"LLM error: {exc}")
        return stats

    # Parse response
    groups = _parse_consolidation_response(result.text)
    if not groups:
        return stats

    # Build valid memory ID set and confidence lookup for validation
    valid_ids = {getattr(m, "id", None) for m in memories}
    valid_ids.discard(None)
    confidence_by_id: dict[Any, float] = {
        getattr(m, "id", None): getattr(m, "confidence", 0.5) for m in memories
    }

    # Save consolidated memories
    from src.db import service as db_service
    from src.db.session import get_db_session

    valid_types = {
        "decision",
        "anomaly",
        "pattern",
        "insight",
        "lesson",
        "relationship",
    }

    for group in groups:
        source_ids = group.get("source_ids", [])
        if not isinstance(source_ids, list):
            continue

        # Validate source IDs exist in the batch
        source_ids = [sid for sid in source_ids if sid in valid_ids]
        if len(source_ids) < 2:
            continue

        mtype = group.get("type", "insight")
        if mtype not in valid_types:
            mtype = "insight"

        title = str(group.get("title", ""))[:100]
        content = str(group.get("content", ""))[:500]
        is_summary = bool(group.get("is_summary", False))

        # Step 4: Apply confidence boosting post-hoc
        max_source_conf = max(
            (confidence_by_id.get(sid, 0.5) for sid in source_ids),
            default=0.5,
        )
        confidence = apply_confidence_boosting(
            source_count=len(source_ids),
            max_source_confidence=max_source_conf,
        )

        # Add [Summary] prefix if flagged and not already present
        if is_summary and not title.startswith("[Summary]"):
            title = f"[Summary] {title}"[:100]

        if not title or not content:
            continue

        try:
            async with get_db_session() as session:
                await db_service.save_consolidated_memory(
                    session=session,
                    user_id=user_id,
                    role_id=role_id,
                    department_id=department_id,
                    memory_type=mtype,
                    title=title,
                    content=content,
                    source_ids=source_ids,
                    confidence=confidence,
                )
            stats["consolidated_count"] += 1
            stats["originals_marked"] += len(source_ids)
            if is_summary:
                stats["summaries_created"] += 1
        except Exception as exc:
            logger.warning(
                "consolidation.save_error",
                user_id=user_id,
                role_id=role_id,
                error=str(exc),
            )
            stats["errors"].append(f"Save error: {exc}")

    logger.info(
        "consolidation.completed",
        user_id=user_id,
        role_id=role_id,
        **{k: v for k, v in stats.items() if k != "errors"},
    )
    return stats
