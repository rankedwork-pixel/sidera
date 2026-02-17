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

from pathlib import Path
from typing import Any

import structlog

from src.agent.tool_registry import tool

logger = structlog.get_logger(__name__)


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


def build_context_manifest(
    skill_id: str,
    source_dir: str,
    context_files: tuple[str, ...],
    descriptions: dict[str, str] | None = None,
) -> str:
    """Build a lightweight manifest of available context files.

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
    if not context_files or not source_dir:
        return ""

    source = Path(source_dir)
    if not source.exists():
        return ""

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

    if not entries:
        return ""

    return (
        "\n# Available Context Files\n\n"
        "This skill has reference files you can load on demand:\n" + "\n".join(entries) + "\n\n"
        f'Use the `load_skill_context` tool with skill_id="{skill_id}" '
        f"to load these when you need examples, guidelines, or reference "
        f"material to handle complex situations. You don't always need them — "
        f"use your judgment.\n"
    )
