"""Tests for the run_skill_code MCP tool (src/mcp_servers/code_execution.py).

Covers:
- Successful code execution with stdout capture
- Skill not found in registry
- Non-code-backed skill rejection
- Missing code_entrypoint
- Subprocess timeout handling
- Path traversal prevention in entrypoint
- Path traversal prevention in args
- Binary output file handling (metadata only)
- Text output file content capture
- Output truncation for large stdout and files
- Non-zero exit code handling
- Input file argument passthrough
- Missing source_dir handling
- Stderr capture
- Multiple output files via glob patterns
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.skills.schema import SkillDefinition

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill(tmp_path: Path, **overrides) -> SkillDefinition:
    """Create a minimal code-backed SkillDefinition for testing."""
    defaults = dict(
        id="test_skill",
        name="Test Skill",
        version="1.0",
        description="A test code-backed skill",
        category="analysis",
        platforms=("meta",),
        tags=("test",),
        tools_required=("run_skill_code",),
        model="haiku",
        max_turns=1,
        system_supplement="test supplement",
        prompt_template="test template",
        output_format="test format",
        business_guidance="test guidance",
        skill_type="code_backed",
        code_entrypoint="run.py",
        code_timeout_seconds=10,
        code_output_patterns=("output/*.csv",),
        source_dir=str(tmp_path),
    )
    defaults.update(overrides)
    return SkillDefinition(**defaults)


def _mock_registry(skill: SkillDefinition | None):
    """Return a patch context manager that mocks SkillRegistry to return *skill*.

    The function under test does a local import:
        from src.skills.registry import SkillRegistry
    so we patch at the source module, not the consumer.
    """
    mock_reg = MagicMock()
    mock_reg.get.return_value = skill
    return patch(
        "src.skills.registry.SkillRegistry",
        return_value=mock_reg,
    )


def _write_script(tmp_path: Path, filename: str, content: str) -> Path:
    """Write a Python script into tmp_path and return its path."""
    script = tmp_path / filename
    script.write_text(content, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


async def _run(skill_id: str = "test_skill", input_file: str = "", args=None) -> dict:
    """Import and call run_skill_code, returning the parsed JSON result."""
    from src.mcp_servers.code_execution import run_skill_code

    raw = await run_skill_code(skill_id=skill_id, input_file=input_file, args=args)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_execution(tmp_path: Path):
    """A simple script that prints to stdout should return status='success'."""
    _write_script(tmp_path, "run.py", 'print("hello from skill")\n')
    skill = _make_skill(tmp_path)

    with _mock_registry(skill):
        result = await _run()

    assert result["status"] == "success"
    assert result["exit_code"] == 0
    assert "hello from skill" in result["stdout"]
    assert result["stderr"] == ""
    assert isinstance(result["execution_time_seconds"], (int, float))
    assert result["execution_time_seconds"] >= 0


@pytest.mark.asyncio
async def test_skill_not_found():
    """When the registry returns None the tool should return an error."""
    with _mock_registry(None):
        result = await _run(skill_id="nonexistent_skill")

    assert result["status"] == "error"
    assert "not found" in result["error"].lower()
    assert "nonexistent_skill" in result["error"]


@pytest.mark.asyncio
async def test_non_code_backed_skill_rejected(tmp_path: Path):
    """An LLM-type skill should be rejected with a clear error."""
    skill = _make_skill(tmp_path, skill_type="llm")

    with _mock_registry(skill):
        result = await _run()

    assert result["status"] == "error"
    assert "not 'code_backed'" in result["error"]


@pytest.mark.asyncio
async def test_missing_code_entrypoint(tmp_path: Path):
    """A code-backed skill with empty code_entrypoint should error."""
    skill = _make_skill(tmp_path, code_entrypoint="")

    with _mock_registry(skill):
        result = await _run()

    assert result["status"] == "error"
    assert "no code_entrypoint" in result["error"].lower()


@pytest.mark.asyncio
async def test_timeout_handling(tmp_path: Path):
    """A script that sleeps longer than the timeout should be killed."""
    _write_script(tmp_path, "run.py", "import time; time.sleep(60)\n")
    skill = _make_skill(tmp_path, code_timeout_seconds=1)

    with _mock_registry(skill):
        result = await _run()

    assert result["status"] == "timeout"
    assert "timed out" in result["error"].lower()
    assert isinstance(result["execution_time_seconds"], (int, float))


@pytest.mark.asyncio
async def test_path_traversal_in_entrypoint(tmp_path: Path):
    """An entrypoint like '../../../etc/passwd' should be rejected."""
    skill = _make_skill(tmp_path, code_entrypoint="../../../etc/passwd")

    with _mock_registry(skill):
        result = await _run()

    assert result["status"] == "error"
    assert "escapes" in result["error"].lower() or "not found" in result["error"].lower()


@pytest.mark.asyncio
async def test_path_traversal_in_args(tmp_path: Path):
    """Args containing '..' should be rejected."""
    _write_script(tmp_path, "run.py", "print('ok')\n")
    skill = _make_skill(tmp_path)

    with _mock_registry(skill):
        result = await _run(args=["--config", "../../etc/secret.yaml"])

    assert result["status"] == "error"
    assert "path traversal" in result["error"].lower()


@pytest.mark.asyncio
async def test_binary_output_file_metadata_only(tmp_path: Path):
    """Binary files (e.g. .docx) should be returned as metadata, not content."""
    # Script creates a fake .docx in output/
    script_content = """\
import os, pathlib
out = pathlib.Path("output")
out.mkdir(exist_ok=True)
(out / "report.docx").write_bytes(b"\\x50\\x4b\\x03\\x04fakecontent")
print("done")
"""
    _write_script(tmp_path, "run.py", script_content)
    skill = _make_skill(tmp_path, code_output_patterns=("output/*.docx",))

    with _mock_registry(skill):
        result = await _run()

    assert result["status"] == "success"
    assert len(result["output_files"]) == 1

    file_info = result["output_files"][0]
    assert file_info["name"] == "output/report.docx"
    assert "Binary file" in file_info["content"]
    assert file_info["size_bytes"] > 0


@pytest.mark.asyncio
async def test_text_output_file_content_returned(tmp_path: Path):
    """Text files (e.g. .csv) should have their content returned in full."""
    script_content = """\
import os, pathlib
out = pathlib.Path("output")
out.mkdir(exist_ok=True)
(out / "results.csv").write_text("campaign,spend,roas\\nA,100,3.2\\nB,200,2.8\\n")
print("done")
"""
    _write_script(tmp_path, "run.py", script_content)
    skill = _make_skill(tmp_path, code_output_patterns=("output/*.csv",))

    with _mock_registry(skill):
        result = await _run()

    assert result["status"] == "success"
    assert len(result["output_files"]) == 1

    file_info = result["output_files"][0]
    assert file_info["name"] == "output/results.csv"
    assert "campaign,spend,roas" in file_info["content"]
    assert file_info["truncated"] is False


@pytest.mark.asyncio
async def test_stdout_truncation(tmp_path: Path):
    """Stdout exceeding _MAX_STDIO_BYTES (20KB) should be truncated."""
    # Generate ~30KB of output
    script_content = """\
print("X" * 30000)
"""
    _write_script(tmp_path, "run.py", script_content)
    skill = _make_skill(tmp_path)

    with _mock_registry(skill):
        result = await _run()

    assert result["status"] == "success"
    assert result["stdout_truncated"] is True
    # Stdout should be at most 20KB
    assert len(result["stdout"]) <= 20_480


@pytest.mark.asyncio
async def test_output_file_truncation(tmp_path: Path):
    """Text file content exceeding _MAX_FILE_BYTES (50KB) should be truncated."""
    # Generate a ~60KB CSV
    script_content = """\
import pathlib
out = pathlib.Path("output")
out.mkdir(exist_ok=True)
(out / "big.csv").write_text("row\\n" + "data\\n" * 20000)
print("done")
"""
    _write_script(tmp_path, "run.py", script_content)
    skill = _make_skill(tmp_path, code_output_patterns=("output/*.csv",))

    with _mock_registry(skill):
        result = await _run()

    assert result["status"] == "success"
    assert len(result["output_files"]) == 1

    file_info = result["output_files"][0]
    assert file_info["truncated"] is True
    # Content should be at most 50KB
    assert len(file_info["content"]) <= 51_200


@pytest.mark.asyncio
async def test_nonzero_exit_code(tmp_path: Path):
    """A script that exits with a non-zero code should return status='error'."""
    _write_script(tmp_path, "run.py", "import sys; sys.exit(42)\n")
    skill = _make_skill(tmp_path)

    with _mock_registry(skill):
        result = await _run()

    assert result["status"] == "error"
    assert result["exit_code"] == 42


@pytest.mark.asyncio
async def test_input_file_passthrough(tmp_path: Path):
    """The input_file argument should be resolved under data/ and passed to the script."""
    # Create the data directory and an input file
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    input_csv = data_dir / "accounts.csv"
    input_csv.write_text("id,name\n1,Acme\n")

    # Script that reads the first CLI argument and prints it
    script_content = """\
import sys
if len(sys.argv) > 1:
    print(f"input_path={sys.argv[1]}")
else:
    print("no input")
"""
    _write_script(tmp_path, "run.py", script_content)
    skill = _make_skill(tmp_path)

    with _mock_registry(skill):
        result = await _run(input_file="accounts.csv")

    assert result["status"] == "success"
    assert "input_path=" in result["stdout"]
    # The resolved path should contain 'data/accounts.csv'
    assert "accounts.csv" in result["stdout"]


@pytest.mark.asyncio
async def test_missing_source_dir():
    """A skill whose source_dir does not exist should return an error."""
    skill = _make_skill(
        Path("/nonexistent/path/that/does/not/exist"),
        source_dir="/nonexistent/path/that/does/not/exist",
    )

    with _mock_registry(skill):
        result = await _run()

    assert result["status"] == "error"
    assert "does not exist" in result["error"].lower()


@pytest.mark.asyncio
async def test_stderr_captured(tmp_path: Path):
    """Stderr output from the subprocess should be captured."""
    _write_script(
        tmp_path,
        "run.py",
        'import sys; print("err msg", file=sys.stderr)\n',
    )
    skill = _make_skill(tmp_path)

    with _mock_registry(skill):
        result = await _run()

    assert result["status"] == "success"
    assert "err msg" in result["stderr"]


@pytest.mark.asyncio
async def test_multiple_output_files(tmp_path: Path):
    """Multiple output files matching the glob should all be returned."""
    script_content = """\
import pathlib
out = pathlib.Path("output")
out.mkdir(exist_ok=True)
(out / "a.csv").write_text("col\\n1\\n")
(out / "b.csv").write_text("col\\n2\\n")
(out / "c.csv").write_text("col\\n3\\n")
print("done")
"""
    _write_script(tmp_path, "run.py", script_content)
    skill = _make_skill(tmp_path, code_output_patterns=("output/*.csv",))

    with _mock_registry(skill):
        result = await _run()

    assert result["status"] == "success"
    assert len(result["output_files"]) == 3
    names = {f["name"] for f in result["output_files"]}
    assert names == {"output/a.csv", "output/b.csv", "output/c.csv"}


@pytest.mark.asyncio
async def test_path_traversal_in_input_file(tmp_path: Path):
    """Input file paths with '..' that escape source_dir should be rejected."""
    _write_script(tmp_path, "run.py", "print('ok')\n")
    skill = _make_skill(tmp_path)

    with _mock_registry(skill):
        result = await _run(input_file="../../etc/passwd")

    assert result["status"] == "error"
    assert "escapes" in result["error"].lower()


@pytest.mark.asyncio
async def test_no_output_patterns(tmp_path: Path):
    """A skill with no code_output_patterns should return an empty output_files list."""
    _write_script(tmp_path, "run.py", 'print("no files")\n')
    skill = _make_skill(tmp_path, code_output_patterns=())

    with _mock_registry(skill):
        result = await _run()

    assert result["status"] == "success"
    assert result["output_files"] == []


@pytest.mark.asyncio
async def test_entrypoint_not_existing(tmp_path: Path):
    """If the entrypoint file does not exist on disk, the tool should error."""
    # Do NOT create run.py
    skill = _make_skill(tmp_path, code_entrypoint="run.py")

    with _mock_registry(skill):
        result = await _run()

    assert result["status"] == "error"
    assert "not found" in result["error"].lower() or "escapes" in result["error"].lower()
