"""Tests for code execution argument validation hardening.

Covers:
- Rejects absolute path arguments
- Rejects URL-encoded path traversal (%2e%2e)
- Rejects arguments that resolve outside source directory
- Accepts valid arguments (--verbose, input.csv, etc.)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_servers.code_execution import run_skill_code


def _mock_skill(source_dir: str) -> MagicMock:
    """Build a mock SkillDefinition for code-backed skill."""
    skill = MagicMock()
    skill.skill_id = "test_skill"
    skill.skill_type = "code_backed"
    skill.code_entrypoint = "main.py"
    skill.code_timeout_seconds = 30
    skill.source_dir = source_dir
    return skill


def _patch_registry(skill: MagicMock):
    """Patch SkillRegistry so run_skill_code finds our mock skill."""
    mock_registry_instance = MagicMock()
    mock_registry_instance.get.return_value = skill

    mock_cls = MagicMock(return_value=mock_registry_instance)
    return patch("src.skills.registry.SkillRegistry", mock_cls)


class TestCodeExecutionArgValidation:
    """Tests for argument validation in run_skill_code."""

    @pytest.mark.asyncio
    async def test_rejects_absolute_path_arg(self, tmp_path: Path) -> None:
        """Absolute paths in args should be rejected."""
        source_dir = tmp_path / "skills" / "test"
        source_dir.mkdir(parents=True)
        (source_dir / "main.py").write_text("print('hi')")

        skill = _mock_skill(str(source_dir))

        with _patch_registry(skill):
            result = await run_skill_code(
                skill_id="test_skill",
                args=["/etc/passwd"],
            )
        data = json.loads(result)
        assert data["status"] == "error"
        assert "Absolute path" in data["error"]

    @pytest.mark.asyncio
    async def test_rejects_url_encoded_traversal(
        self, tmp_path: Path,
    ) -> None:
        """URL-encoded '..' (%2e%2e) should be caught."""
        source_dir = tmp_path / "skills" / "test"
        source_dir.mkdir(parents=True)
        (source_dir / "main.py").write_text("print('hi')")

        skill = _mock_skill(str(source_dir))

        with _patch_registry(skill):
            result = await run_skill_code(
                skill_id="test_skill",
                args=["%2e%2e/%2e%2e/etc/passwd"],
            )
        data = json.loads(result)
        assert data["status"] == "error"
        assert "traversal" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_rejects_arg_resolving_outside_source(
        self, tmp_path: Path,
    ) -> None:
        """Args that resolve outside source_dir should be rejected."""
        source_dir = tmp_path / "skills" / "test"
        source_dir.mkdir(parents=True)
        (source_dir / "main.py").write_text("print('hi')")

        skill = _mock_skill(str(source_dir))

        with _patch_registry(skill):
            result = await run_skill_code(
                skill_id="test_skill",
                args=["../../../etc/passwd"],
            )
        data = json.loads(result)
        assert data["status"] == "error"

    @pytest.mark.asyncio
    async def test_accepts_valid_flag_args(self, tmp_path: Path) -> None:
        """Valid CLI flags like --verbose should be accepted."""
        source_dir = tmp_path / "skills" / "test"
        source_dir.mkdir(parents=True)
        entrypoint = source_dir / "main.py"
        entrypoint.write_text("import sys; print(sys.argv)")

        skill = _mock_skill(str(source_dir))

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"ok", b"")
        mock_proc.returncode = 0

        with (
            _patch_registry(skill),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
        ):
            result = await run_skill_code(
                skill_id="test_skill",
                args=["--verbose", "--count=10"],
            )
        data = json.loads(result)
        assert data["status"] == "success"

    @pytest.mark.asyncio
    async def test_accepts_valid_filename_args(
        self, tmp_path: Path,
    ) -> None:
        """Valid filenames within source_dir should be accepted."""
        source_dir = tmp_path / "skills" / "test"
        source_dir.mkdir(parents=True)
        (source_dir / "main.py").write_text("print('hi')")
        (source_dir / "input.csv").write_text("a,b,c")

        skill = _mock_skill(str(source_dir))

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"ok", b"")
        mock_proc.returncode = 0

        with (
            _patch_registry(skill),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
        ):
            result = await run_skill_code(
                skill_id="test_skill",
                args=["input.csv"],
            )
        data = json.loads(result)
        assert data["status"] == "success"
