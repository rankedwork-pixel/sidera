"""Tests for code-backed skill schema fields on SkillDefinition.

Covers the four new fields (skill_type, code_entrypoint,
code_timeout_seconds, code_output_patterns) added to SkillDefinition,
their parsing in load_skill_from_yaml(), and validation in validate_skill().
"""

from __future__ import annotations

from pathlib import Path

from src.skills.schema import SkillDefinition, load_skill_from_yaml, validate_skill

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_kwargs(**overrides) -> dict:
    """Return minimal valid SkillDefinition kwargs, with optional overrides."""
    defaults = dict(
        id="test_skill",
        name="Test Skill",
        version="1.0",
        description="A test skill",
        category="analysis",
        platforms=("meta",),
        tags=("t1", "t2", "t3", "t4", "t5"),
        tools_required=("run_skill_code",),
        model="sonnet",
        max_turns=5,
        system_supplement="test supplement",
        prompt_template="test template",
        output_format="test format",
        business_guidance="test guidance",
    )
    defaults.update(overrides)
    return defaults


def _make_code_backed(**overrides) -> SkillDefinition:
    """Return a valid code-backed SkillDefinition with optional overrides."""
    kwargs = _base_kwargs(
        skill_type="code_backed",
        code_entrypoint="code/run.py",
        code_timeout_seconds=120,
        code_output_patterns=("output/*.csv",),
    )
    kwargs.update(overrides)
    return SkillDefinition(**kwargs)


# ---------------------------------------------------------------------------
# 1. Default skill_type is "llm"
# ---------------------------------------------------------------------------


class TestDefaultSkillType:
    def test_default_skill_type_is_llm(self):
        skill = SkillDefinition(**_base_kwargs())
        assert skill.skill_type == "llm"

    def test_default_code_entrypoint_is_empty(self):
        skill = SkillDefinition(**_base_kwargs())
        assert skill.code_entrypoint == ""

    def test_default_code_timeout_is_300(self):
        skill = SkillDefinition(**_base_kwargs())
        assert skill.code_timeout_seconds == 300

    def test_default_code_output_patterns_is_empty(self):
        skill = SkillDefinition(**_base_kwargs())
        assert skill.code_output_patterns == ()


# ---------------------------------------------------------------------------
# 2. Backward compatibility — existing LLM skills validate fine
# ---------------------------------------------------------------------------


class TestLLMBackwardCompat:
    def test_llm_skill_validates_clean(self):
        """An LLM skill with no code-backed fields should have zero errors."""
        skill = SkillDefinition(
            **_base_kwargs(
                tools_required=("get_google_ads_campaigns",),
            )
        )
        errors = validate_skill(skill)
        assert errors == []

    def test_llm_skill_ignores_code_fields(self):
        """Setting code-backed fields on an LLM skill should not trigger
        code-backed validation errors (only skill_type='code_backed' triggers
        those checks)."""
        skill = SkillDefinition(
            **_base_kwargs(
                skill_type="llm",
                code_entrypoint="some/path.py",
                code_timeout_seconds=10,
                code_output_patterns=("*.csv",),
                tools_required=("get_google_ads_campaigns",),
            )
        )
        errors = validate_skill(skill)
        assert errors == []


# ---------------------------------------------------------------------------
# 3. Valid code_backed skill validates cleanly
# ---------------------------------------------------------------------------


class TestValidCodeBacked:
    def test_valid_code_backed_no_errors(self):
        skill = _make_code_backed()
        errors = validate_skill(skill)
        assert errors == []

    def test_valid_code_backed_with_source_dir_and_file(self, tmp_path: Path):
        """When source_dir exists and the entrypoint file is present,
        validation passes."""
        code_dir = tmp_path / "code"
        code_dir.mkdir()
        (code_dir / "run.py").write_text("print('hello')")

        skill = _make_code_backed(source_dir=str(tmp_path))
        errors = validate_skill(skill)
        assert errors == []


# ---------------------------------------------------------------------------
# 4. Invalid skill_type rejected
# ---------------------------------------------------------------------------


class TestInvalidSkillType:
    def test_invalid_skill_type_produces_error(self):
        skill = SkillDefinition(**_base_kwargs(skill_type="other"))
        errors = validate_skill(skill)
        matching = [e for e in errors if "Invalid skill_type" in e]
        assert len(matching) == 1
        assert "'other'" in matching[0]

    def test_empty_skill_type_produces_error(self):
        skill = SkillDefinition(**_base_kwargs(skill_type=""))
        errors = validate_skill(skill)
        matching = [e for e in errors if "Invalid skill_type" in e]
        assert len(matching) == 1


# ---------------------------------------------------------------------------
# 5. code_backed missing entrypoint
# ---------------------------------------------------------------------------


class TestMissingEntrypoint:
    def test_code_backed_no_entrypoint_error(self):
        skill = _make_code_backed(code_entrypoint="")
        errors = validate_skill(skill)
        matching = [e for e in errors if "code_entrypoint" in e]
        assert len(matching) == 1
        assert "must specify code_entrypoint" in matching[0]


# ---------------------------------------------------------------------------
# 6. code_backed missing run_skill_code in tools_required
# ---------------------------------------------------------------------------


class TestMissingRunSkillCodeTool:
    def test_code_backed_without_run_skill_code_error(self):
        skill = _make_code_backed(
            tools_required=("get_google_ads_campaigns",),
        )
        errors = validate_skill(skill)
        matching = [e for e in errors if "run_skill_code" in e]
        assert len(matching) == 1
        assert "tools_required" in matching[0]

    def test_code_backed_with_run_skill_code_no_error(self):
        skill = _make_code_backed(
            tools_required=("run_skill_code",),
        )
        errors = validate_skill(skill)
        tool_errors = [e for e in errors if "run_skill_code" in e]
        assert tool_errors == []


# ---------------------------------------------------------------------------
# 7. code_backed entrypoint file not found (when source_dir exists)
# ---------------------------------------------------------------------------


class TestEntrypointNotFound:
    def test_entrypoint_missing_on_disk(self, tmp_path: Path):
        """source_dir exists but the entrypoint file does not — expect error."""
        skill = _make_code_backed(
            source_dir=str(tmp_path),
            code_entrypoint="code/run.py",
        )
        errors = validate_skill(skill)
        matching = [e for e in errors if "does not exist" in e]
        assert len(matching) == 1
        assert "code/run.py" in matching[0]

    def test_entrypoint_not_checked_when_source_dir_missing(self):
        """When source_dir does not point to a real directory, we skip
        the file-existence check (no false negatives for DB-defined skills)."""
        skill = _make_code_backed(
            source_dir="/nonexistent/path",
            code_entrypoint="code/run.py",
        )
        errors = validate_skill(skill)
        file_errors = [e for e in errors if "does not exist" in e]
        assert file_errors == []

    def test_entrypoint_not_checked_when_source_dir_empty(self):
        """When source_dir is empty string, skip the file-existence check."""
        skill = _make_code_backed(
            source_dir="",
            code_entrypoint="code/run.py",
        )
        errors = validate_skill(skill)
        file_errors = [e for e in errors if "does not exist" in e]
        assert file_errors == []


# ---------------------------------------------------------------------------
# 8. code_backed timeout too low
# ---------------------------------------------------------------------------


class TestTimeoutTooLow:
    def test_timeout_zero_error(self):
        skill = _make_code_backed(code_timeout_seconds=0)
        errors = validate_skill(skill)
        matching = [e for e in errors if "code_timeout_seconds" in e]
        assert len(matching) == 1
        assert "1-3600" in matching[0]

    def test_timeout_negative_error(self):
        skill = _make_code_backed(code_timeout_seconds=-10)
        errors = validate_skill(skill)
        matching = [e for e in errors if "code_timeout_seconds" in e]
        assert len(matching) == 1


# ---------------------------------------------------------------------------
# 9. code_backed timeout too high
# ---------------------------------------------------------------------------


class TestTimeoutTooHigh:
    def test_timeout_5000_error(self):
        skill = _make_code_backed(code_timeout_seconds=5000)
        errors = validate_skill(skill)
        matching = [e for e in errors if "code_timeout_seconds" in e]
        assert len(matching) == 1
        assert "1-3600" in matching[0]

    def test_timeout_3601_error(self):
        skill = _make_code_backed(code_timeout_seconds=3601)
        errors = validate_skill(skill)
        matching = [e for e in errors if "code_timeout_seconds" in e]
        assert len(matching) == 1

    def test_timeout_boundary_valid(self):
        """1 and 3600 are both valid boundary values."""
        for value in (1, 3600):
            skill = _make_code_backed(code_timeout_seconds=value)
            errors = validate_skill(skill)
            timeout_errors = [e for e in errors if "code_timeout_seconds" in e]
            assert timeout_errors == [], f"Unexpected error for timeout={value}"


# ---------------------------------------------------------------------------
# 10. YAML loading parses new fields
# ---------------------------------------------------------------------------


_CODE_BACKED_YAML = """\
id: test_code
name: Test Code Skill
version: "1.0"
description: A test code-backed skill
category: analysis
platforms: [meta]
tags: [test, test2, test3, test4, test5]
tools_required: [run_skill_code]
model: sonnet
max_turns: 5
skill_type: code_backed
code_entrypoint: code/run.py
code_timeout_seconds: 120
code_output_patterns:
  - "output/*.csv"
  - "output/*.docx"
system_supplement: Test
prompt_template: Test
output_format: Test
business_guidance: Test
"""


class TestYAMLLoading:
    def test_yaml_parses_code_backed_fields(self, tmp_path: Path):
        yaml_file = tmp_path / "skill.yaml"
        yaml_file.write_text(_CODE_BACKED_YAML)

        skill = load_skill_from_yaml(yaml_file)

        assert skill.skill_type == "code_backed"
        assert skill.code_entrypoint == "code/run.py"
        assert skill.code_timeout_seconds == 120
        assert skill.code_output_patterns == ("output/*.csv", "output/*.docx")

    def test_yaml_defaults_when_code_fields_absent(self, tmp_path: Path):
        """A YAML with no code-backed keys should produce LLM defaults."""
        yaml_content = """\
id: test_llm
name: Test LLM Skill
version: "1.0"
description: An LLM skill
category: analysis
platforms: [meta]
tags: [a, b, c, d, e]
tools_required: [get_meta_campaigns]
model: sonnet
max_turns: 5
system_supplement: Test
prompt_template: Test
output_format: Test
business_guidance: Test
"""
        yaml_file = tmp_path / "skill.yaml"
        yaml_file.write_text(yaml_content)

        skill = load_skill_from_yaml(yaml_file)

        assert skill.skill_type == "llm"
        assert skill.code_entrypoint == ""
        assert skill.code_timeout_seconds == 300
        assert skill.code_output_patterns == ()

    def test_yaml_source_dir_set_to_parent(self, tmp_path: Path):
        yaml_file = tmp_path / "skill.yaml"
        yaml_file.write_text(_CODE_BACKED_YAML)

        skill = load_skill_from_yaml(yaml_file)
        assert skill.source_dir == str(tmp_path)

    def test_loaded_code_backed_validates(self, tmp_path: Path):
        """A loaded code-backed YAML should pass validation (except the
        entrypoint file not being on disk — create it so it passes)."""
        # Create the entrypoint file so file-existence check passes
        code_dir = tmp_path / "code"
        code_dir.mkdir()
        (code_dir / "run.py").write_text("print('analysis')")

        yaml_file = tmp_path / "skill.yaml"
        yaml_file.write_text(_CODE_BACKED_YAML)

        skill = load_skill_from_yaml(yaml_file)
        errors = validate_skill(skill)
        assert errors == []
