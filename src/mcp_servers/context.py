"""On-demand context file loading MCP tool for Sidera.

Provides a tool that lets the agent load skill context files (examples,
guidelines, reference material) on demand rather than having them always
injected into the system prompt upfront.

When a skill has context files, the executor injects a lightweight manifest
listing available files + sizes instead of the full text. The agent can
then call ``load_skill_context`` to pull in specific files when needed.

This reduces token usage on runs where the agent doesn't need all context
(e.g., when the data is straightforward and no examples are needed) while
keeping full context available for complex situations.

Usage::

    # In the skill system prompt, the agent sees:
    # "Available context files: examples/good_analysis.md (2.1KB), ..."
    # "Use the load_skill_context tool to load any of these when needed."

    # Agent calls:
    load_skill_context(skill_id="creative_analysis", file_pattern="examples/*")
"""

from __future__ import annotations

import contextvars
from pathlib import Path
from typing import Any

import structlog

from src.agent.tool_registry import tool

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Traversal budget: max referenced skills loaded per agent turn
# ---------------------------------------------------------------------------
_MAX_REFERENCE_LOADS_PER_TURN = 3
_MAX_REFERENCE_CHARS_PER_TURN = 12_000  # Total chars across ALL loads

_reference_load_count: contextvars.ContextVar[int] = contextvars.ContextVar(
    "reference_load_count",
    default=0,
)
_reference_chars_loaded: contextvars.ContextVar[int] = contextvars.ContextVar(
    "reference_chars_loaded",
    default=0,
)


def reset_reference_load_count() -> None:
    """Reset the per-turn reference load and char counters.

    Call at the start of each agent turn (``run_skill``,
    ``run_conversation_turn``, ``run_heartbeat_turn``).
    """
    _reference_load_count.set(0)
    _reference_chars_loaded.set(0)


def get_reference_load_count() -> int:
    """Return the current number of reference loads this turn."""
    try:
        return _reference_load_count.get()
    except LookupError:
        return 0


def get_reference_chars_loaded() -> int:
    """Return the total chars loaded from references this turn."""
    try:
        return _reference_chars_loaded.get()
    except LookupError:
        return 0


@tool(
    name="load_skill_context",
    description=(
        "Load context files (examples, guidelines, reference material) for a "
        "skill on demand. Use this when you need specific examples or "
        "guidelines to handle a complex situation. Pass a skill_id and "
        "optionally a file_pattern to filter which files to load. "
        "Returns the text content of matching files."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "skill_id": {
                "type": "string",
                "description": "The ID of the skill whose context files to load.",
            },
            "file_pattern": {
                "type": "string",
                "description": (
                    "Glob pattern to filter which files to load "
                    "(e.g. 'examples/*', 'guidelines/*.md'). "
                    "Defaults to '**/*' (all files)."
                ),
            },
        },
        "required": ["skill_id"],
    },
)
async def load_skill_context_handler(args: dict) -> dict[str, Any]:
    """Load context files for a skill on demand.

    Args:
        args: Dict with ``skill_id`` (required) and ``file_pattern``
            (optional, defaults to ``"**/*"`` to load all context files).

    Returns:
        MCP tool response with the loaded context text.
    """
    skill_id = args.get("skill_id", "")
    file_pattern = args.get("file_pattern", "**/*")

    if not skill_id:
        return {
            "content": [{"type": "text", "text": "Error: skill_id is required"}],
        }

    try:
        from src.skills.registry import SkillRegistry

        # Use a fresh registry load (in-process, no DB needed for disk files)
        registry = SkillRegistry()
        registry.load_all()

        skill = registry.get(skill_id)
        if skill is None:
            return {
                "content": [
                    {"type": "text", "text": f"Skill '{skill_id}' not found"},
                ],
            }

        # If skill has pre-rendered context_text (DB-defined), return it
        if skill.context_text:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (f"# Context for {skill.name}\n\n{skill.context_text}"),
                    },
                ],
            }

        # Resolve context files from disk
        if not skill.context_files or not skill.source_dir:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"No context files configured for skill '{skill_id}'",
                    },
                ],
            }

        source = Path(skill.source_dir)
        if not source.exists():
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Source directory not found: {skill.source_dir}",
                    },
                ],
            }

        # Match files against the requested pattern
        matched_files: list[Path] = []
        for match in sorted(source.glob(file_pattern)):
            if match.is_file() and match.suffix in (
                ".md",
                ".txt",
                ".yaml",
                ".yml",
                ".json",
            ):
                matched_files.append(match)

        if not matched_files:
            # Fall back to loading all context_files
            from src.skills.schema import resolve_context_files

            matched_files = resolve_context_files(skill)

        if not matched_files:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"No files matched pattern '{file_pattern}' in {skill.source_dir}"
                        ),
                    },
                ],
            }

        # Read and combine matched files
        sections: list[str] = []
        total_chars = 0
        max_chars = 8000  # Cap to avoid token explosion

        for fpath in matched_files:
            try:
                content = fpath.read_text(encoding="utf-8").strip()
                if not content:
                    continue
                relative = fpath.relative_to(source)
                section = f"## {relative}\n\n{content}"

                if total_chars + len(section) > max_chars:
                    sections.append(
                        f"\n[Truncated — {len(matched_files) - len(sections)} more files available]"
                    )
                    break

                sections.append(section)
                total_chars += len(section)
            except OSError:
                continue

        result_text = f"# Context Files for {skill.name}\n\n" + "\n\n".join(sections)

        logger.info(
            "context.loaded",
            skill_id=skill_id,
            files_loaded=len(sections),
            total_chars=total_chars,
        )

        return {
            "content": [{"type": "text", "text": result_text}],
        }

    except Exception as exc:
        logger.warning(
            "context.load_error",
            skill_id=skill_id,
            error=str(exc),
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error loading context: {exc}",
                },
            ],
        }


@tool(
    name="load_referenced_skill_context",
    description=(
        "Load context from a skill that the current skill references. "
        "Use this when you need methodology, guidelines, or domain knowledge "
        "from a related skill to handle the current situation. "
        "The reference must be declared in the current skill's references list. "
        "Limited to 3 referenced skills AND 12,000 total chars per turn. "
        "Load the most relevant reference first."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "skill_id": {
                "type": "string",
                "description": "The ID of the CURRENT skill you are executing.",
            },
            "reference_skill_id": {
                "type": "string",
                "description": "The ID of the REFERENCED skill to load context from.",
            },
        },
        "required": ["skill_id", "reference_skill_id"],
    },
)
def load_referenced_skill_context_handler(args: dict) -> dict[str, Any]:
    """Load context from a referenced skill on demand.

    Validates that the reference exists in the skill's references list,
    then loads the referenced skill's system_supplement, business_guidance,
    and context files. Enforces a per-turn traversal budget.
    """
    skill_id = args.get("skill_id", "")
    reference_skill_id = args.get("reference_skill_id", "")

    if not skill_id:
        return _error("skill_id is required")
    if not reference_skill_id:
        return _error("reference_skill_id is required")

    # Check traversal budget (load count)
    current_count = get_reference_load_count()
    if current_count >= _MAX_REFERENCE_LOADS_PER_TURN:
        return _error(
            f"Reference load budget exhausted ({_MAX_REFERENCE_LOADS_PER_TURN} "
            f"per turn). Prioritize the most relevant references."
        )

    # Check traversal budget (total chars)
    chars_so_far = get_reference_chars_loaded()
    if chars_so_far >= _MAX_REFERENCE_CHARS_PER_TURN:
        return _error(
            f"Character budget exhausted "
            f"({chars_so_far}/{_MAX_REFERENCE_CHARS_PER_TURN} chars "
            f"used this turn). Prioritize the most relevant references."
        )

    try:
        from src.skills.registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_all()

        # Validate the source skill exists
        skill = registry.get(skill_id)
        if skill is None:
            return _error(f"Skill '{skill_id}' not found")

        # Validate the reference is declared
        valid_ref = False
        ref_relationship = ""
        ref_reason = ""
        for ref_sid, rel, reason in skill.references:
            if ref_sid == reference_skill_id:
                valid_ref = True
                ref_relationship = rel
                ref_reason = reason
                break

        if not valid_ref:
            declared_refs = [r[0] for r in skill.references] if skill.references else []
            return _error(
                f"Skill '{skill_id}' does not reference '{reference_skill_id}'. "
                f"Declared references: {declared_refs}"
            )

        # Load the referenced skill
        ref_skill = registry.get(reference_skill_id)
        if ref_skill is None:
            return _error(f"Referenced skill '{reference_skill_id}' not found in registry")

        # Load context from the referenced skill
        from src.skills.schema import load_context_text

        context = load_context_text(ref_skill, lazy=False)

        # Build combined output
        sections: list[str] = []
        header = f"# Referenced Skill: {ref_skill.name}"
        if ref_relationship:
            header += f" (relationship: {ref_relationship})"
        sections.append(header)

        if ref_reason:
            sections.append(f"*Reference reason: {ref_reason}*")

        if ref_skill.system_supplement:
            supplement = ref_skill.system_supplement[:2000]
            sections.append(f"## System Context\n\n{supplement}")

        if ref_skill.business_guidance:
            guidance = ref_skill.business_guidance[:2000]
            sections.append(f"## Business Guidance\n\n{guidance}")

        if context:
            # Cap context at 4000 chars to avoid token explosion
            if len(context) > 4000:
                context = context[:4000] + "\n\n[... truncated ...]"
            sections.append(f"## Context Files\n\n{context}")

        result_text = "\n\n".join(sections)

        # Truncate if this load would exceed the remaining char budget
        remaining_budget = _MAX_REFERENCE_CHARS_PER_TURN - chars_so_far
        if len(result_text) > remaining_budget:
            result_text = (
                result_text[:remaining_budget] + f"\n\n[Truncated — char budget reached "
                f"({_MAX_REFERENCE_CHARS_PER_TURN}"
                f"/{_MAX_REFERENCE_CHARS_PER_TURN} chars this turn)]"
            )

        # Increment load and char counters
        _reference_load_count.set(current_count + 1)
        _reference_chars_loaded.set(chars_so_far + len(result_text))

        logger.info(
            "reference_context.loaded",
            skill_id=skill_id,
            reference_skill_id=reference_skill_id,
            relationship=ref_relationship,
            result_chars=len(result_text),
            total_chars_this_turn=chars_so_far + len(result_text),
            loads_this_turn=current_count + 1,
        )

        return {"content": [{"type": "text", "text": result_text}]}

    except Exception as exc:
        logger.warning(
            "reference_context.load_error",
            skill_id=skill_id,
            reference_skill_id=reference_skill_id,
            error=str(exc),
        )
        return _error(f"Error loading referenced context: {exc}")


def _error(msg: str) -> dict[str, Any]:
    """Return a tool error response."""
    return {"content": [{"type": "text", "text": f"Error: {msg}"}]}


def build_context_manifest(
    skill_id: str,
    source_dir: str,
    context_files: tuple[str, ...],
    descriptions: dict[str, str] | None = None,
    references: tuple[tuple[str, str, str], ...] = (),
) -> str:
    """Build a lightweight manifest of available context files.

    Optionally includes a "Related Skills" section listing cross-skill
    references when ``references`` is provided.

    Instead of injecting all context file content into the system prompt,
    this creates a small manifest listing available files and their sizes.
    The agent can then use ``load_skill_context`` to fetch specific files.

    Args:
        skill_id: The skill ID (for the tool call).
        source_dir: Base directory for resolving files.
        context_files: Glob patterns configured on the skill.
        descriptions: Optional mapping of glob patterns to human-readable
            descriptions. When provided, matching files get a description
            suffix (e.g. ``examples/good.md (2.1KB) — Real-world examples``).

    Returns:
        Manifest string to inject into the system prompt, or empty string
        if no context files are configured.
    """
    sections: list[str] = []

    # --- Context files section ---
    if context_files and source_dir:
        source = Path(source_dir)
        if source.exists():
            entries: list[str] = []
            for pattern in context_files:
                # Look up description for this pattern
                desc = descriptions.get(pattern, "") if descriptions else ""
                for match in sorted(source.glob(pattern)):
                    if match.is_file():
                        try:
                            size_bytes = match.stat().st_size
                            relative = match.relative_to(source)
                            if size_bytes < 1024:
                                size_str = f"{size_bytes}B"
                            else:
                                size_str = f"{size_bytes / 1024:.1f}KB"
                            entry = f"  - {relative} ({size_str})"
                            if desc:
                                entry += f" — {desc}"
                            entries.append(entry)
                        except OSError:
                            continue

            if entries:
                sections.append(
                    "# Available Context Files\n\n"
                    "This skill has reference files you can load on demand:\n"
                    + "\n".join(entries)
                    + "\n\n"
                    f'Use the `load_skill_context` tool with skill_id="{skill_id}" '
                    f"to load these when you need examples, guidelines, or reference "
                    f"material to handle complex situations. You don't always need "
                    f"them — use your judgment."
                )

    # --- Related skills section ---
    if references:
        ref_entries: list[str] = []
        for ref_skill_id, relationship, reason in references:
            if not ref_skill_id:
                continue
            parts = [f"  - **{ref_skill_id}**"]
            if relationship:
                parts.append(f" ({relationship})")
            if reason:
                parts.append(f" — {reason}")
            ref_entries.append("".join(parts))

        if ref_entries:
            sections.append(
                "# Related Skills\n\n"
                "This skill references domain knowledge from other skills. "
                "Load their context when you need their methodology or "
                "guidelines:\n" + "\n".join(ref_entries) + "\n\n"
                f"Use the `load_referenced_skill_context` tool with "
                f'skill_id="{skill_id}" and the target reference_skill_id '
                f"to load their context on demand. Limited to 3 loads "
                f"and 12,000 total chars per turn — load the most "
                f"relevant reference first."
            )

    if not sections:
        return ""

    return "\n" + "\n\n".join(sections) + "\n"
