"""Memory consolidation engine.

Runs a weekly Haiku call to identify duplicate, overlapping, or redundant
memories for a given (user_id, role_id) pair and merges them into single
consolidated entries.

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

_CONSOLIDATION_PROMPT = """\
You are a memory consolidation system.  You receive a numbered list of memories
belonging to a single AI role.  Your job is to identify groups of memories that
are duplicates, near-duplicates, or highly overlapping and should be merged into
a single consolidated memory.

Rules:
- Each group must have >= 2 source memories.
- Keep the most specific, actionable, and recent information from each source.
- Preserve important details (dates, numbers, names).  Do not generalize away facts.
- Assign each consolidated memory the most appropriate type.
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
  "confidence": <float 0.0-1.0>
}}
"""


def _format_memories_for_prompt(memories: list[Any]) -> str:
    """Format memories as a numbered list for the consolidation prompt.

    Args:
        memories: List of RoleMemory ORM objects.

    Returns:
        Formatted string with one memory per numbered line.
    """
    lines: list[str] = []
    for mem in memories:
        mid = getattr(mem, "id", "?")
        mtype = getattr(mem, "memory_type", "?")
        title = getattr(mem, "title", "")
        content = getattr(mem, "content", "")
        confidence = getattr(mem, "confidence", 1.0)
        created = getattr(mem, "created_at", None)
        date_str = created.strftime("%Y-%m-%d") if created else "?"

        # Truncate content to keep prompt short
        if len(content) > 300:
            content = content[:297] + "..."

        lines.append(f"{mid}. [{date_str}] [{mtype}] (conf={confidence:.1f}) {title}: {content}")
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


async def consolidate_role_memories(
    user_id: str,
    role_id: str,
    department_id: str,
    memories: list[Any],
) -> dict[str, Any]:
    """Run consolidation on a batch of memories.

    Calls Haiku to identify mergeable groups, validates the response,
    and saves consolidated memories to the database.

    Args:
        user_id: The user these memories belong to.
        role_id: The role these memories belong to.
        department_id: The department the role belongs to.
        memories: List of RoleMemory ORM objects to consolidate.

    Returns:
        Stats dict with keys: consolidated_count, originals_marked,
        cost_usd, errors.
    """
    stats: dict[str, Any] = {
        "consolidated_count": 0,
        "originals_marked": 0,
        "cost_usd": 0.0,
        "errors": [],
    }

    if len(memories) < 2:
        return stats

    from src.agent.api_client import run_agent_loop
    from src.config import settings
    from src.llm.provider import TaskType

    # Build prompt
    memory_list = _format_memories_for_prompt(memories)
    prompt = _CONSOLIDATION_PROMPT + f"\n\nMemories:\n{memory_list}"

    # Call Haiku
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

    # Build valid memory ID set for validation
    valid_ids = {getattr(m, "id", None) for m in memories}
    valid_ids.discard(None)

    # Save consolidated memories
    from src.db import service as db_service
    from src.db.session import get_db_session

    valid_types = {"decision", "anomaly", "pattern", "insight", "lesson", "relationship"}

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
        confidence = min(1.0, max(0.0, float(group.get("confidence", 0.8))))

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
