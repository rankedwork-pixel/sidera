"""Persistent role memory — extraction and injection.

Provides two capabilities:

1. **Extraction** — Pulls structured memory entries from skill results
   after a role run completes. v1 extracts ``decision`` memories from
   approval outcomes and ``anomaly`` memories from keyword detection
   in skill output text. No LLM call required.

2. **Injection** — Formats memories into a prompt section that gets
   inserted into the role context before each skill execution. Respects
   a token budget to avoid bloating the context window.

Usage::

    from src.skills.memory import extract_memories_from_results, compose_memory_context

    # After a role run — extract what was learned
    entries = extract_memories_from_results(
        role_id="performance_media_buyer",
        department_id="marketing",
        skill_results=skill_results,
        approval_outcomes=approval_outcomes,
    )

    # Before the next role run — inject memories into context
    context = compose_memory_context(memories, token_budget=2000)
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

_MEMORY_CONTENT_PATTERN = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2})\]\s*\[Conversation\]"
    r"(?:\s*\(from\s+([^)]+)\))?\s*(.*)",
    re.DOTALL,
)

_MONTH_NAMES = [
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]

_ANOMALY_KEYWORDS = re.compile(
    r"\b(anomal\w*|spike\w*|drop\w*|surge\w*|alert\w*|unusual|"
    r"abnormal\w*|unexpected\w*|sudden increase|sudden decrease|"
    r"significant change|outlier\w*)\b",
    re.IGNORECASE,
)

_TOKEN_ESTIMATE_RATIO = 4  # ~4 chars per token

# Time decay half-life in days — a memory at this age gets 50% weight.
# 30 days means a 3-day-old memory is ~93% weight, a 60-day-old is ~25%.
_DECAY_HALF_LIFE_DAYS = 30.0


def _time_decay_score(
    confidence: float,
    created_at: Any,
    *,
    half_life_days: float = _DECAY_HALF_LIFE_DAYS,
    now: datetime | None = None,
) -> float:
    """Compute a combined relevance score using confidence × time decay.

    Recent memories outrank older ones at equal confidence. The decay
    follows the formula::

        decay = 1.0 / (1.0 + age_days / half_life_days)

    So a memory at ``half_life_days`` old gets 50% weight, while a
    brand-new memory gets ~100%. The final score is
    ``confidence × decay``, ranging from 0.0 to 1.0.

    Steward notes (confidence == 1.0 conventionally) still rank highest
    because they carry the maximum confidence multiplier.

    Args:
        confidence: Memory confidence score (0.0 to 1.0).
        created_at: Datetime object or None.
        half_life_days: Number of days for 50% decay (default 30).
        now: Override for current time (useful in tests).

    Returns:
        Combined score (higher is more relevant).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    if created_at is None:
        age_days = half_life_days  # Unknown age → treat as half-life
    elif isinstance(created_at, datetime):
        # Handle both tz-aware and tz-naive datetimes
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - created_at).total_seconds() / 86400)
    else:
        age_days = half_life_days

    decay = 1.0 / (1.0 + age_days / half_life_days)
    return confidence * decay


# =====================================================================
# Supersedes filtering
# =====================================================================


def filter_superseded_memories(
    memories: list[Any],
    superseded_ids: set[int],
) -> list[Any]:
    """Remove memories that have been superseded by newer versions.

    A memory is superseded if its ID appears in ``superseded_ids``
    (i.e., another memory's ``supersedes_id`` points to it). Only the
    latest version in a supersession chain should be injected into
    the prompt.

    Works with both ORM ``RoleMemory`` objects and plain dicts.

    Args:
        memories: List of memory objects or dicts.
        superseded_ids: Set of memory IDs that are superseded.

    Returns:
        Filtered list preserving original order.
    """
    if not superseded_ids:
        return memories

    result = []
    for m in memories:
        mid = m.get("id") if isinstance(m, dict) else getattr(m, "id", None)
        if mid is not None and mid in superseded_ids:
            continue
        result.append(m)
    return result


# =====================================================================
# Token estimation
# =====================================================================


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 characters per token)."""
    return max(1, len(text) // _TOKEN_ESTIMATE_RATIO)


# =====================================================================
# Extraction — decision memories
# =====================================================================


def _extract_decision_memories(
    recommendations: list[dict[str, Any]],
    approval_outcomes: list[dict[str, Any]],
    role_id: str,
    department_id: str,
    run_date: date | None = None,
) -> list[dict[str, Any]]:
    """Extract decision memories from paired recommendations + outcomes.

    Matches recommendations to approval outcomes by index. Each
    approved or rejected recommendation becomes a ``decision`` memory.

    Args:
        recommendations: Structured recommendations from skill results.
        approval_outcomes: List of dicts with at least ``status`` key
            (``approved`` or ``rejected``) and optional ``description``.
        role_id: The role that produced these recommendations.
        department_id: The department the role belongs to.
        run_date: When the analysis ran.

    Returns:
        List of dicts ready for ``save_memory()``.
    """
    memories: list[dict[str, Any]] = []

    for i, outcome in enumerate(approval_outcomes):
        status = outcome.get("status", "")
        if status not in ("approved", "rejected"):
            continue

        rec = recommendations[i] if i < len(recommendations) else {}
        description = (
            outcome.get("description") or rec.get("description", "") or rec.get("action", "")
        )
        if not description:
            continue

        action_type = rec.get("action_type", "action")
        status_label = "Approved" if status == "approved" else "Rejected"
        title = f"{status_label}: {description[:200]}"
        content = f"[{run_date or 'unknown date'}] {title}"

        if rec.get("reasoning"):
            content += f"\nReasoning: {rec['reasoning']}"
        if rec.get("projected_impact"):
            content += f"\nExpected impact: {rec['projected_impact']}"

        memories.append(
            {
                "role_id": role_id,
                "department_id": department_id,
                "memory_type": "decision",
                "title": title,
                "content": content,
                "confidence": 1.0 if status == "approved" else 0.8,
                "source_skill_id": rec.get("skill_id"),
                "source_run_date": run_date,
                "evidence": {
                    "action_type": action_type,
                    "status": status,
                    "recommendation_index": i,
                },
            }
        )

    return memories


# =====================================================================
# Extraction — anomaly memories
# =====================================================================


def _extract_anomaly_memories(
    skill_results: list[Any],
    role_id: str,
    department_id: str,
    run_date: date | None = None,
) -> list[dict[str, Any]]:
    """Extract anomaly memories from skill output text.

    Scans each skill result's ``output_text`` for anomaly keywords.
    When found, extracts the surrounding sentence as a memory entry.

    Args:
        skill_results: List of ``SkillResult`` objects.
        role_id: The role that produced these results.
        department_id: The department the role belongs to.
        run_date: When the analysis ran.

    Returns:
        List of dicts ready for ``save_memory()``.
    """
    memories: list[dict[str, Any]] = []
    seen_titles: set[str] = set()

    for sr in skill_results:
        output = getattr(sr, "output_text", "") or ""
        if not _ANOMALY_KEYWORDS.search(output):
            continue

        # Extract sentences containing anomaly keywords
        sentences = re.split(r"(?<=[.!?\n])\s+", output)
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence or len(sentence) < 20:
                continue
            if not _ANOMALY_KEYWORDS.search(sentence):
                continue

            # Use first 100 chars as title, deduplicate
            title = sentence[:100].rstrip(".")
            if title in seen_titles:
                continue
            seen_titles.add(title)

            skill_id = getattr(sr, "skill_id", None)

            memories.append(
                {
                    "role_id": role_id,
                    "department_id": department_id,
                    "memory_type": "anomaly",
                    "title": title,
                    "content": f"[{run_date or 'unknown date'}] {sentence[:500]}",
                    "confidence": 0.9,
                    "source_skill_id": skill_id,
                    "source_run_date": run_date,
                    "evidence": {"source_skill": skill_id},
                }
            )

            # Max 3 anomaly memories per role run
            if len(memories) >= 3:
                return memories

    return memories


# =====================================================================
# Extraction — main entry point
# =====================================================================


def extract_memories_from_results(
    role_id: str,
    department_id: str,
    skill_results: list[Any],
    approval_outcomes: list[dict[str, Any]] | None = None,
    run_date: date | None = None,
) -> list[dict[str, Any]]:
    """Extract memory entries from a completed role run.

    Combines decision memories (from approval outcomes) and anomaly
    memories (from keyword detection in skill output). Returns dicts
    ready to be passed to ``db_service.save_memory()``.

    Args:
        role_id: The role that was executed.
        department_id: The department the role belongs to.
        skill_results: List of ``SkillResult`` objects from the run.
        approval_outcomes: Optional list of approval outcome dicts.
        run_date: When the analysis ran.

    Returns:
        List of memory entry dicts.
    """
    memories: list[dict[str, Any]] = []

    # Decision memories from approval outcomes
    if approval_outcomes:
        all_recs: list[dict[str, Any]] = []
        for sr in skill_results:
            recs = getattr(sr, "recommendations", []) or []
            all_recs.extend(recs)

        decisions = _extract_decision_memories(
            all_recs,
            approval_outcomes,
            role_id,
            department_id,
            run_date,
        )
        memories.extend(decisions)

    # Anomaly memories from output text
    anomalies = _extract_anomaly_memories(
        skill_results,
        role_id,
        department_id,
        run_date,
    )
    memories.extend(anomalies)

    return memories


# =====================================================================
# Injection — compose memory context for prompt
# =====================================================================


def _format_memory_line(
    content: str,
    source_role_id: str | None = None,
) -> str:
    """Format a memory content string with WHO/WHEN attribution.

    Parses the content format ``[YYYY-MM-DD] [Conversation] (from Name) text``
    and converts to a human-readable form like ``[Feb 12] Name told you: text``.

    For inter-agent memories (``source_role_id`` is set), uses a distinct
    format: ``[Feb 12] Re: performance_media_buyer — delegated Q4 analysis``.

    Falls back to the raw content if the format doesn't match.

    Args:
        content: Raw memory content string.
        source_role_id: If non-null, this is an inter-agent relationship
            memory about the role identified by ``source_role_id``.

    Returns:
        Formatted line suitable for prompt injection.
    """
    # Inter-agent memories: format with peer role attribution
    if source_role_id:
        return f"- Re: {source_role_id} — {content[:200]}"

    match = _MEMORY_CONTENT_PATTERN.match(content)
    if not match:
        return f"- {content}"

    date_str, source_name, body = match.groups()
    body = (body or "").strip()

    # Format date as "Feb 12" instead of "2025-02-12"
    try:
        parts = date_str.split("-")
        month_idx = int(parts[1])
        day = int(parts[2])
        short_date = f"{_MONTH_NAMES[month_idx]} {day}"
    except (IndexError, ValueError):
        short_date = date_str

    if source_name:
        return f"- [{short_date}] {source_name} told you: {body}"
    return f"- [{short_date}] {body}"


_MEMORY_INDEX_THRESHOLD = 20


def compose_memory_index(memories: list[Any]) -> str:
    """Build a lightweight index of memories (titles + IDs only).

    Used when the number of hot memories exceeds ``_MEMORY_INDEX_THRESHOLD``.
    Instead of injecting full content (which would exceed the token budget),
    this generates a compact listing. The agent can then use the
    ``load_memory_detail`` MCP tool to load specific memories by ID.

    Args:
        memories: List of ``RoleMemory`` ORM objects or dicts.

    Returns:
        Formatted index string with type-grouped memory titles.
    """
    if not memories:
        return ""

    # Group by type
    by_type: dict[str, list[str]] = {}
    for mem in memories:
        if isinstance(mem, dict):
            mtype = mem.get("memory_type", "insight")
            mid = mem.get("id", "?")
            title = mem.get("title", mem.get("content", "")[:60])
            created = mem.get("created_at")
        else:
            mtype = getattr(mem, "memory_type", "insight")
            mid = getattr(mem, "id", "?")
            title = getattr(mem, "title", "") or getattr(mem, "content", "")[:60]
            created = getattr(mem, "created_at", None)

        # Format date compactly
        date_str = ""
        if created:
            try:
                if hasattr(created, "strftime"):
                    date_str = created.strftime("%b %d")
                else:
                    parts = str(created).split("-")
                    month_idx = int(parts[1])
                    day = int(parts[2][:2])
                    date_str = f"{_MONTH_NAMES[month_idx]} {day}"
            except (IndexError, ValueError):
                pass

        date_part = f" ({date_str})" if date_str else ""
        by_type.setdefault(mtype, []).append(f"  [{mid}] {title[:80]}{date_part}")

    _type_headers = {
        "steward_note": "Steward Guidance",
        "relationship": "Relationship Context",
        "cross_role_insight": "Learnings from Peers",
        "commitment": "Active Commitments",
        "decision": "Recent Decisions",
        "anomaly": "Known Anomalies",
        "pattern": "Recognized Patterns",
        "insight": "Key Insights",
        "lesson": "Lessons Learned",
    }

    sections = [
        "# Role Memory (Index)\n\n"
        "You have many memories. This is a compact index — use the "
        "`load_memory_detail` tool with memory IDs to load full content "
        "when needed.\n"
    ]
    for mtype in (
        "steward_note",
        "relationship",
        "cross_role_insight",
        "commitment",
        "decision",
        "anomaly",
        "pattern",
        "insight",
        "lesson",
    ):
        entries = by_type.get(mtype)
        if not entries:
            continue
        header = _type_headers.get(mtype, mtype.title())
        sections.append(f"## {header}\n" + "\n".join(entries))

    return "\n\n".join(sections)


def compose_memory_context(
    memories: list[Any],
    token_budget: int = 2000,
    force_index: bool = False,
) -> str:
    """Format memories for injection into the role context prompt.

    Sorts by confidence (descending) then creation date (descending).
    Truncates to fit within the token budget. Groups memories by type
    with section headers.

    When the number of memories exceeds ``_MEMORY_INDEX_THRESHOLD`` (or
    ``force_index=True``), delegates to ``compose_memory_index()`` which
    returns a compact title-only listing. The agent can then load full
    details via the ``load_memory_detail`` MCP tool.

    Memory content with WHO/WHEN attribution (format
    ``[YYYY-MM-DD] [Conversation] (from Name) text``) is rendered as
    ``[Feb 12] Name told you: text`` so the agent can naturally say
    "you told me on Feb 12 that ..."

    Args:
        memories: List of ``RoleMemory`` ORM objects or dicts with
            ``memory_type``, ``title``, ``content``, ``confidence``,
            ``created_at`` fields.
        token_budget: Maximum tokens to use (default 2000).
        force_index: If True, always use the compact index format
            regardless of memory count.

    Returns:
        Formatted string with ``# Role Memory`` header, or empty
        string if no memories to inject.
    """
    if not memories:
        return ""

    # Delegate to compact index when memory count is high
    if force_index or len(memories) > _MEMORY_INDEX_THRESHOLD:
        return compose_memory_index(memories)

    # Sort by time-decayed relevance score (confidence × recency).
    # Recent memories outrank older ones at equal confidence.
    _now = datetime.now(timezone.utc)

    def _sort_key(m: Any) -> float:
        if isinstance(m, dict):
            conf = m.get("confidence", 0.0)
            created = m.get("created_at")
        else:
            conf = getattr(m, "confidence", 0.0)
            created = getattr(m, "created_at", None)
        return -_time_decay_score(conf, created, now=_now)

    sorted_memories = sorted(memories, key=_sort_key)

    # Group by type
    by_type: dict[str, list[str]] = {}
    total_tokens = 0
    header = (
        "# Role Memory\n\n"
        "You have persistent memory from previous runs. Use these to inform\n"
        "your analysis but don't repeat them verbatim — reference them when relevant.\n"
        "When recalling something a user told you, cite who said it and when\n"
        '(e.g., "you told me on Feb 12 that ...").\n\n'
        "These are your recent memories (last 90 days). You also have an archive\n"
        "of older memories. If you suspect a current situation resembles something\n"
        "from the past, use the `search_role_memory_archive` tool to search your\n"
        "full history by keyword.\n"
    )
    total_tokens += _estimate_tokens(header)

    for mem in sorted_memories:
        if isinstance(mem, dict):
            mtype = mem.get("memory_type", "insight")
            content = mem.get("content", "")
            source_role_id = mem.get("source_role_id")
        else:
            mtype = getattr(mem, "memory_type", "insight")
            content = getattr(mem, "content", "")
            source_role_id = getattr(mem, "source_role_id", None)

        line = _format_memory_line(content, source_role_id=source_role_id)
        line_tokens = _estimate_tokens(line)

        if total_tokens + line_tokens > token_budget:
            break

        by_type.setdefault(mtype, []).append(line)
        total_tokens += line_tokens

    if not by_type:
        return ""

    # Build output with type headers
    _type_headers = {
        "steward_note": "Steward Guidance",
        "relationship": "Relationship Context",
        "cross_role_insight": "Learnings from Peers",
        "commitment": "Active Commitments",
        "decision": "Recent Decisions",
        "anomaly": "Known Anomalies",
        "pattern": "Recognized Patterns",
        "insight": "Key Insights",
        "lesson": "Lessons Learned",
    }

    # Steward notes first (highest priority), then relationship memories
    sections = [header]
    for mtype in (
        "steward_note",
        "relationship",
        "cross_role_insight",
        "commitment",
        "decision",
        "anomaly",
        "pattern",
        "insight",
        "lesson",
    ):
        lines = by_type.get(mtype)
        if not lines:
            continue
        section_header = _type_headers.get(mtype, mtype.title())
        sections.append(f"## {section_header}\n" + "\n".join(lines))

    return "\n\n".join(sections)
