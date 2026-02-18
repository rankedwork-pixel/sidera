"""Code execution MCP tool for code-backed Sidera skills.

Provides the ``run_skill_code`` tool that executes a code-backed skill's
Python entrypoint in a subprocess.  The tool is stateless — it looks up
the skill from the registry at call time and runs the code in isolation.

The agent instance (``ClaudeCodeExecutor``) calls this tool to execute
the deterministic Python logic, then interprets the output using its
full connector access (Google Drive, Slack, BigQuery, etc.).

Safety:
    - Path traversal prevention (entrypoint must be within source_dir)
    - Timeout enforcement (configurable per skill, max 3600s)
    - Output truncation (stdout/stderr 20KB, files 50KB)
    - Subprocess isolation (no shell=True)
    - Binary files returned as metadata only (name + size)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import structlog

from src.agent.tool_registry import tool

logger = structlog.get_logger(__name__)

# Max bytes for stdout/stderr capture
_MAX_STDIO_BYTES = 20_480  # 20 KB

# Max bytes for individual output file content
_MAX_FILE_BYTES = 51_200  # 50 KB

# Text file extensions (content returned in full up to limit)
_TEXT_EXTENSIONS = frozenset(
    {
        ".csv",
        ".txt",
        ".md",
        ".json",
        ".yaml",
        ".yml",
        ".log",
        ".tsv",
        ".html",
        ".xml",
        ".ini",
        ".cfg",
    }
)


def _is_text_file(path: Path) -> bool:
    """Check if a file is a text file based on extension."""
    return path.suffix.lower() in _TEXT_EXTENSIONS


def _safe_resolve(base: Path, relative: str) -> Path | None:
    """Resolve a relative path within base, preventing traversal."""
    try:
        resolved = (base / relative).resolve()
        base_resolved = base.resolve()
        if str(resolved).startswith(str(base_resolved)):
            return resolved
    except (OSError, ValueError):
        pass
    return None


@tool(
    name="run_skill_code",
    description=(
        "Execute a code-backed skill's Python entrypoint in a subprocess. "
        "Returns stdout, stderr, exit code, and output file contents. "
        "Use this tool to run deterministic Python analysis code that "
        "produces structured output (CSV, DOCX, etc.)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "skill_id": {
                "type": "string",
                "description": "The code-backed skill to execute.",
            },
            "input_file": {
                "type": "string",
                "description": (
                    "Optional input file path relative to the skill's data/ "
                    "directory. Passed as a CLI argument to the entrypoint."
                ),
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional additional CLI arguments for the entrypoint.",
            },
        },
        "required": ["skill_id"],
    },
)
async def run_skill_code(
    skill_id: str,
    input_file: str = "",
    args: list[str] | None = None,
) -> str:
    """Execute a code-backed skill's Python entrypoint."""
    from src.skills.registry import SkillRegistry

    # Load the skill from registry
    registry = SkillRegistry()
    registry.load_all()
    skill = registry.get(skill_id)

    if skill is None:
        return json.dumps(
            {
                "status": "error",
                "error": f"Skill '{skill_id}' not found in registry.",
            }
        )

    if skill.skill_type != "code_backed":
        return json.dumps(
            {
                "status": "error",
                "error": (
                    f"Skill '{skill_id}' is type '{skill.skill_type}', "
                    "not 'code_backed'. Only code-backed skills can be executed."
                ),
            }
        )

    if not skill.code_entrypoint:
        return json.dumps(
            {
                "status": "error",
                "error": f"Skill '{skill_id}' has no code_entrypoint configured.",
            }
        )

    source_dir = Path(skill.source_dir)
    if not source_dir.exists():
        return json.dumps(
            {
                "status": "error",
                "error": f"Skill source directory does not exist: {source_dir}",
            }
        )

    # Resolve entrypoint with path traversal check
    entrypoint = _safe_resolve(source_dir, skill.code_entrypoint)
    if entrypoint is None or not entrypoint.exists():
        return json.dumps(
            {
                "status": "error",
                "error": (
                    f"Code entrypoint '{skill.code_entrypoint}' not found "
                    f"or escapes source directory."
                ),
            }
        )

    # Build command
    cmd = [sys.executable, str(entrypoint)]

    # Add input_file as first argument if provided
    if input_file:
        input_path = _safe_resolve(source_dir, f"data/{input_file}")
        if input_path is None:
            return json.dumps(
                {
                    "status": "error",
                    "error": f"Input file path '{input_file}' escapes source directory.",
                }
            )
        cmd.append(str(input_path))

    # Add extra args with comprehensive path-traversal defense
    if args:
        import urllib.parse

        for arg in args:
            # Reject absolute paths
            if os.path.isabs(arg):
                return json.dumps({
                    "status": "error",
                    "error": "Absolute path argument rejected.",
                })
            # Check URL-decoded form for traversal sequences
            decoded = urllib.parse.unquote(arg)
            if ".." in decoded:
                return json.dumps({
                    "status": "error",
                    "error": "Path traversal in argument rejected.",
                })
            # Resolve and verify arg stays within source directory
            resolved = (source_dir / decoded).resolve()
            if not str(resolved).startswith(
                str(source_dir.resolve()),
            ):
                return json.dumps({
                    "status": "error",
                    "error": "Argument escapes source directory.",
                })
        cmd.extend(args)

    logger.info(
        "run_skill_code.start",
        skill_id=skill_id,
        entrypoint=str(entrypoint),
        timeout=skill.code_timeout_seconds,
    )

    start_time = time.monotonic()

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(source_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=skill.code_timeout_seconds,
        )

        execution_time = time.monotonic() - start_time

        # Truncate output
        stdout = stdout_bytes[:_MAX_STDIO_BYTES].decode("utf-8", errors="replace")
        stderr = stderr_bytes[:_MAX_STDIO_BYTES].decode("utf-8", errors="replace")

        stdout_truncated = len(stdout_bytes) > _MAX_STDIO_BYTES
        stderr_truncated = len(stderr_bytes) > _MAX_STDIO_BYTES

    except asyncio.TimeoutError:
        execution_time = time.monotonic() - start_time
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        logger.warning(
            "run_skill_code.timeout",
            skill_id=skill_id,
            timeout=skill.code_timeout_seconds,
        )
        return json.dumps(
            {
                "status": "timeout",
                "error": (
                    f"Execution timed out after {skill.code_timeout_seconds}s. "
                    "The process was killed."
                ),
                "execution_time_seconds": round(execution_time, 2),
            }
        )

    except Exception as exc:
        execution_time = time.monotonic() - start_time
        logger.exception("run_skill_code.error", skill_id=skill_id)
        return json.dumps(
            {
                "status": "error",
                "error": f"Failed to execute: {exc}",
                "execution_time_seconds": round(execution_time, 2),
            }
        )

    # Collect output files
    output_files: list[dict] = []
    if skill.code_output_patterns:
        for pattern in skill.code_output_patterns:
            for match in sorted(source_dir.glob(pattern)):
                if not match.is_file():
                    continue
                file_info: dict = {
                    "name": str(match.relative_to(source_dir)),
                    "size_bytes": match.stat().st_size,
                }
                if _is_text_file(match):
                    try:
                        raw = match.read_bytes()[:_MAX_FILE_BYTES]
                        file_info["content"] = raw.decode("utf-8", errors="replace")
                        file_info["truncated"] = match.stat().st_size > _MAX_FILE_BYTES
                    except OSError:
                        file_info["content"] = "[Error reading file]"
                else:
                    file_info["content"] = (
                        f"[Binary file: {match.suffix}, {match.stat().st_size} bytes]"
                    )
                output_files.append(file_info)

    exit_code = proc.returncode or 0
    status = "success" if exit_code == 0 else "error"

    logger.info(
        "run_skill_code.complete",
        skill_id=skill_id,
        exit_code=exit_code,
        execution_time=round(execution_time, 2),
        output_file_count=len(output_files),
    )

    result = {
        "status": status,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "output_files": output_files,
        "execution_time_seconds": round(execution_time, 2),
    }

    return json.dumps(result, default=str)
