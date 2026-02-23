"""Tests for manager-related registry methods.

Phase 2 of the Manager Roles feature: ``is_manager()``,
``get_managed_roles()``, ``list_managers()``, cross-validation
of ``manages`` references, and circular reference detection.
"""

from __future__ import annotations

import io
import re
import textwrap
from pathlib import Path

import pytest
import structlog

from src.skills.registry import SkillRegistry

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture()
def log_output():
    """Capture structlog output to a StringIO buffer.

    Structlog's PrintLoggerFactory caches the file reference at config time,
    which defeats both capsys and capfd after the MCP stdio server module
    reconfigures structlog globally.  This fixture creates a fresh buffer
    and reconfigures structlog to write to it.
    """
    buf = io.StringIO()
    structlog.reset_defaults()
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=buf),
        cache_logger_on_first_use=False,
    )
    yield buf


# ── helpers ──────────────────────────────────────────────────────────


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))


def _make_dept(root: Path, dept_id: str = "marketing") -> None:
    _write(
        root / dept_id / "_department.yaml",
        f"""\
        id: {dept_id}
        name: {dept_id.replace("_", " ").title()}
        description: Test department
        """,
    )


def _make_role(
    root: Path,
    dept_id: str,
    role_id: str,
    *,
    manages: list[str] | None = None,
    delegation_model: str = "standard",
    synthesis_prompt: str = "",
    briefing_skills: list[str] | None = None,
) -> None:
    lines = [
        f"id: {role_id}",
        f"name: {role_id.replace('_', ' ').title()}",
        f"department_id: {dept_id}",
        f"description: Test role {role_id}",
        "persona: A test persona",
    ]
    if manages:
        lines.append("manages:")
        for m in manages:
            lines.append(f"  - {m}")
    lines.append(f"delegation_model: {delegation_model}")
    if synthesis_prompt:
        lines.append(f'synthesis_prompt: "{synthesis_prompt}"')
    lines.append("connectors:")
    lines.append("  - google_ads")
    if briefing_skills:
        lines.append("briefing_skills:")
        for s in briefing_skills:
            lines.append(f"  - {s}")

    path = root / dept_id / role_id / "_role.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def _make_skill(
    root: Path,
    dept_id: str,
    role_id: str,
    skill_id: str,
) -> None:
    _write(
        root / dept_id / role_id / f"{skill_id}.yaml",
        f"""\
        id: {skill_id}
        name: {skill_id.replace("_", " ").title()}
        version: "1.0"
        description: Test skill {skill_id}
        category: analysis
        platforms: [google_ads]
        tags: [test]
        tools_required: [get_google_ads_campaigns]
        model: sonnet
        max_turns: 5
        system_supplement: "Do analysis"
        output_format: "markdown"
        business_guidance: "Test guidance"
        prompt_template: "Analyze the data"
        """,
    )


# ── is_manager ───────────────────────────────────────────────────────


class TestIsManager:
    def test_returns_true_for_manager(self, tmp_path: Path) -> None:
        _make_dept(tmp_path)
        _make_role(tmp_path, "marketing", "buyer", briefing_skills=["s1"])
        _make_role(tmp_path, "marketing", "head", manages=["buyer"])
        _make_skill(tmp_path, "marketing", "buyer", "s1")
        _make_skill(tmp_path, "marketing", "head", "s2")

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()
        assert reg.is_manager("head") is True

    def test_returns_false_for_non_manager(self, tmp_path: Path) -> None:
        _make_dept(tmp_path)
        _make_role(tmp_path, "marketing", "buyer", briefing_skills=["s1"])
        _make_skill(tmp_path, "marketing", "buyer", "s1")

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()
        assert reg.is_manager("buyer") is False

    def test_returns_false_for_unknown_role(self, tmp_path: Path) -> None:
        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()
        assert reg.is_manager("nonexistent") is False


# ── get_managed_roles ────────────────────────────────────────────────


class TestGetManagedRoles:
    def test_returns_managed_roles(self, tmp_path: Path) -> None:
        _make_dept(tmp_path)
        _make_role(tmp_path, "marketing", "buyer", briefing_skills=["s1"])
        _make_role(tmp_path, "marketing", "analyst", briefing_skills=["s2"])
        _make_role(
            tmp_path,
            "marketing",
            "head",
            manages=["buyer", "analyst"],
        )
        _make_skill(tmp_path, "marketing", "buyer", "s1")
        _make_skill(tmp_path, "marketing", "analyst", "s2")
        _make_skill(tmp_path, "marketing", "head", "s3")

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        managed = reg.get_managed_roles("head")
        ids = [r.id for r in managed]
        assert "buyer" in ids
        assert "analyst" in ids
        assert len(managed) == 2

    def test_skips_missing_managed_role(self, tmp_path: Path) -> None:
        _make_dept(tmp_path)
        _make_role(tmp_path, "marketing", "buyer", briefing_skills=["s1"])
        _make_role(
            tmp_path,
            "marketing",
            "head",
            manages=["buyer", "nonexistent_role"],
        )
        _make_skill(tmp_path, "marketing", "buyer", "s1")
        _make_skill(tmp_path, "marketing", "head", "s3")

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        managed = reg.get_managed_roles("head")
        assert len(managed) == 1
        assert managed[0].id == "buyer"

    def test_returns_empty_for_non_manager(self, tmp_path: Path) -> None:
        _make_dept(tmp_path)
        _make_role(tmp_path, "marketing", "buyer", briefing_skills=["s1"])
        _make_skill(tmp_path, "marketing", "buyer", "s1")

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()
        assert reg.get_managed_roles("buyer") == []

    def test_returns_empty_for_unknown_role(self, tmp_path: Path) -> None:
        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()
        assert reg.get_managed_roles("ghost") == []


# ── list_managers ────────────────────────────────────────────────────


class TestListManagers:
    def test_returns_all_managers(self, tmp_path: Path) -> None:
        _make_dept(tmp_path, "marketing")
        _make_dept(tmp_path, "sales")
        _make_role(tmp_path, "marketing", "buyer", briefing_skills=["s1"])
        _make_role(tmp_path, "marketing", "m_head", manages=["buyer"])
        _make_role(tmp_path, "sales", "sdr", briefing_skills=["s2"])
        _make_role(tmp_path, "sales", "s_head", manages=["sdr"])
        _make_skill(tmp_path, "marketing", "buyer", "s1")
        _make_skill(tmp_path, "marketing", "m_head", "s3")
        _make_skill(tmp_path, "sales", "sdr", "s2")
        _make_skill(tmp_path, "sales", "s_head", "s4")

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        managers = reg.list_managers()
        ids = [r.id for r in managers]
        assert "m_head" in ids
        assert "s_head" in ids
        assert len(managers) == 2

    def test_filters_by_department(self, tmp_path: Path) -> None:
        _make_dept(tmp_path, "marketing")
        _make_dept(tmp_path, "sales")
        _make_role(tmp_path, "marketing", "buyer", briefing_skills=["s1"])
        _make_role(tmp_path, "marketing", "m_head", manages=["buyer"])
        _make_role(tmp_path, "sales", "sdr", briefing_skills=["s2"])
        _make_role(tmp_path, "sales", "s_head", manages=["sdr"])
        _make_skill(tmp_path, "marketing", "buyer", "s1")
        _make_skill(tmp_path, "marketing", "m_head", "s3")
        _make_skill(tmp_path, "sales", "sdr", "s2")
        _make_skill(tmp_path, "sales", "s_head", "s4")

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        managers = reg.list_managers(department_id="marketing")
        assert len(managers) == 1
        assert managers[0].id == "m_head"

    def test_returns_empty_when_no_managers(self, tmp_path: Path) -> None:
        _make_dept(tmp_path)
        _make_role(tmp_path, "marketing", "buyer", briefing_skills=["s1"])
        _make_skill(tmp_path, "marketing", "buyer", "s1")

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()
        assert reg.list_managers() == []

    def test_sorted_by_id(self, tmp_path: Path) -> None:
        _make_dept(tmp_path)
        _make_role(tmp_path, "marketing", "zz_lead", manages=["buyer"])
        _make_role(tmp_path, "marketing", "aa_lead", manages=["buyer"])
        _make_role(tmp_path, "marketing", "buyer", briefing_skills=["s1"])
        _make_skill(tmp_path, "marketing", "buyer", "s1")
        _make_skill(tmp_path, "marketing", "zz_lead", "s2")
        _make_skill(tmp_path, "marketing", "aa_lead", "s3")

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        managers = reg.list_managers()
        assert managers[0].id == "aa_lead"
        assert managers[1].id == "zz_lead"


# ── cross-validation ─────────────────────────────────────────────────


class TestCrossValidation:
    def test_warns_on_missing_managed_role(
        self,
        tmp_path: Path,
        log_output: io.StringIO,
    ) -> None:
        _make_dept(tmp_path)
        _make_role(
            tmp_path,
            "marketing",
            "head",
            manages=["nonexistent_role"],
        )
        _make_skill(tmp_path, "marketing", "head", "s1")

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        output = _ANSI_RE.sub("", log_output.getvalue())
        assert "managed_role_not_found" in output

    def test_warns_on_self_reference(
        self,
        tmp_path: Path,
        log_output: io.StringIO,
    ) -> None:
        _make_dept(tmp_path)
        _make_role(
            tmp_path,
            "marketing",
            "head",
            manages=["head"],
        )
        _make_skill(tmp_path, "marketing", "head", "s1")

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        output = _ANSI_RE.sub("", log_output.getvalue())
        assert "self_reference" in output

    def test_warns_on_circular_reference(
        self,
        tmp_path: Path,
        log_output: io.StringIO,
    ) -> None:
        _make_dept(tmp_path)
        _make_role(
            tmp_path,
            "marketing",
            "role_a",
            manages=["role_b"],
        )
        _make_role(
            tmp_path,
            "marketing",
            "role_b",
            manages=["role_a"],
        )
        _make_skill(tmp_path, "marketing", "role_a", "s1")
        _make_skill(tmp_path, "marketing", "role_b", "s2")

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        output = _ANSI_RE.sub("", log_output.getvalue())
        assert "circular_reference" in output

    def test_no_warning_for_valid_references(
        self,
        tmp_path: Path,
        log_output: io.StringIO,
    ) -> None:
        _make_dept(tmp_path)
        _make_role(tmp_path, "marketing", "buyer", briefing_skills=["s1"])
        _make_role(
            tmp_path,
            "marketing",
            "head",
            manages=["buyer"],
        )
        _make_skill(tmp_path, "marketing", "buyer", "s1")
        _make_skill(tmp_path, "marketing", "head", "s2")

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        output = _ANSI_RE.sub("", log_output.getvalue())
        assert "managed_role_not_found" not in output
        assert "self_reference" not in output
        assert "circular_reference" not in output


# ── depth limit ──────────────────────────────────────────────────────


class TestDepthLimit:
    def test_warns_on_deep_nesting(
        self,
        tmp_path: Path,
        log_output: io.StringIO,
    ) -> None:
        """5 levels of management (depth > 3) triggers depth_exceeded."""
        _make_dept(tmp_path)
        # chain: ceo → vp → director → lead → senior → worker
        _make_role(tmp_path, "marketing", "worker", briefing_skills=["s1"])
        _make_role(tmp_path, "marketing", "senior", manages=["worker"])
        _make_role(tmp_path, "marketing", "lead", manages=["senior"])
        _make_role(tmp_path, "marketing", "director", manages=["lead"])
        _make_role(tmp_path, "marketing", "vp", manages=["director"])
        _make_role(tmp_path, "marketing", "ceo", manages=["vp"])

        _make_skill(tmp_path, "marketing", "worker", "s1")
        _make_skill(tmp_path, "marketing", "senior", "s2")
        _make_skill(tmp_path, "marketing", "lead", "s3")
        _make_skill(tmp_path, "marketing", "director", "s4")
        _make_skill(tmp_path, "marketing", "vp", "s5")
        _make_skill(tmp_path, "marketing", "ceo", "s6")

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        output = _ANSI_RE.sub("", log_output.getvalue())
        assert "depth_exceeded" in output

    def test_three_levels_ok(
        self,
        tmp_path: Path,
        log_output: io.StringIO,
    ) -> None:
        """3 levels of management is within limits (no depth warning)."""
        _make_dept(tmp_path)
        _make_role(tmp_path, "marketing", "worker", briefing_skills=["s1"])
        _make_role(tmp_path, "marketing", "lead", manages=["worker"])
        _make_role(tmp_path, "marketing", "director", manages=["lead"])
        _make_role(tmp_path, "marketing", "vp", manages=["director"])

        _make_skill(tmp_path, "marketing", "worker", "s1")
        _make_skill(tmp_path, "marketing", "lead", "s2")
        _make_skill(tmp_path, "marketing", "director", "s3")
        _make_skill(tmp_path, "marketing", "vp", "s4")

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        output = _ANSI_RE.sub("", log_output.getvalue())
        assert "depth_exceeded" not in output


# ── manager + existing registry features ─────────────────────────────


class TestManagerWithExistingFeatures:
    def test_manager_appears_in_list_roles(self, tmp_path: Path) -> None:
        _make_dept(tmp_path)
        _make_role(tmp_path, "marketing", "buyer", briefing_skills=["s1"])
        _make_role(tmp_path, "marketing", "head", manages=["buyer"])
        _make_skill(tmp_path, "marketing", "buyer", "s1")
        _make_skill(tmp_path, "marketing", "head", "s2")

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        all_roles = reg.list_roles()
        ids = [r.id for r in all_roles]
        assert "head" in ids
        assert "buyer" in ids

    def test_manager_role_has_skills(self, tmp_path: Path) -> None:
        _make_dept(tmp_path)
        _make_role(tmp_path, "marketing", "buyer", briefing_skills=["s1"])
        _make_role(
            tmp_path,
            "marketing",
            "head",
            manages=["buyer"],
            briefing_skills=["s2"],
        )
        _make_skill(tmp_path, "marketing", "buyer", "s1")
        _make_skill(tmp_path, "marketing", "head", "s2")

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        skills = reg.list_skills_for_role("head")
        assert len(skills) == 1
        assert skills[0].id == "s2"

    def test_search_finds_manager_skills(self, tmp_path: Path) -> None:
        _make_dept(tmp_path)
        _make_role(tmp_path, "marketing", "buyer", briefing_skills=["s1"])
        _make_role(tmp_path, "marketing", "head", manages=["buyer"])
        _make_skill(tmp_path, "marketing", "buyer", "s1")
        _make_skill(tmp_path, "marketing", "head", "s2")

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        results = reg.search("s2")
        assert any(s.id == "s2" for s in results)

    def test_reload_preserves_manager_info(self, tmp_path: Path) -> None:
        _make_dept(tmp_path)
        _make_role(tmp_path, "marketing", "buyer", briefing_skills=["s1"])
        _make_role(tmp_path, "marketing", "head", manages=["buyer"])
        _make_skill(tmp_path, "marketing", "buyer", "s1")
        _make_skill(tmp_path, "marketing", "head", "s2")

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()
        assert reg.is_manager("head") is True

        reg.reload()
        assert reg.is_manager("head") is True
        assert len(reg.get_managed_roles("head")) == 1


# ── load_all log includes manager count ──────────────────────────────


class TestLoadAllLogging:
    def test_log_includes_managers_count(
        self,
        tmp_path: Path,
        log_output: io.StringIO,
    ) -> None:
        _make_dept(tmp_path)
        _make_role(tmp_path, "marketing", "buyer", briefing_skills=["s1"])
        _make_role(tmp_path, "marketing", "head", manages=["buyer"])
        _make_skill(tmp_path, "marketing", "buyer", "s1")
        _make_skill(tmp_path, "marketing", "head", "s2")

        reg = SkillRegistry(skills_dir=tmp_path)
        reg.load_all()

        output = _ANSI_RE.sub("", log_output.getvalue())
        assert "registry.loaded" in output
        assert "managers=1" in output
