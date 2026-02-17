"""Tests for heartbeat fields on RoleDefinition.

Covers:
- heartbeat_schedule and heartbeat_model fields
- YAML loading of heartbeat fields
- Default values (None and empty string)
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from src.skills.schema import (
    RoleDefinition,
    load_role_from_yaml,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_role(**overrides) -> RoleDefinition:
    """Create a RoleDefinition with sensible defaults, applying overrides."""
    defaults = {
        "id": "test_role",
        "name": "Test Role",
        "department_id": "test_dept",
        "description": "A test role",
        "briefing_skills": ("skill_a",),
    }
    defaults.update(overrides)
    return RoleDefinition(**defaults)


def _write_role_yaml(tmp_path: Path, content: str) -> Path:
    """Write a _role.yaml file into tmp_path and return the path."""
    yaml_path = tmp_path / "_role.yaml"
    yaml_path.write_text(textwrap.dedent(content), encoding="utf-8")
    return yaml_path


# ── Dataclass fields ─────────────────────────────────────────────────────────


class TestHeartbeatFields:
    def test_default_heartbeat_schedule_is_none(self):
        role = _make_role()
        assert role.heartbeat_schedule is None

    def test_default_heartbeat_model_is_empty(self):
        role = _make_role()
        assert role.heartbeat_model == ""

    def test_heartbeat_schedule_set(self):
        role = _make_role(heartbeat_schedule="*/15 * * * *")
        assert role.heartbeat_schedule == "*/15 * * * *"

    def test_heartbeat_model_set(self):
        role = _make_role(heartbeat_model="claude-3-haiku-20240307")
        assert role.heartbeat_model == "claude-3-haiku-20240307"

    def test_heartbeat_schedule_none_means_no_heartbeat(self):
        role = _make_role()
        assert role.heartbeat_schedule is None  # No heartbeat configured

    def test_frozen_heartbeat_schedule(self):
        role = _make_role(heartbeat_schedule="*/30 * * * *")
        try:
            role.heartbeat_schedule = "0 * * * *"  # type: ignore[misc]
            assert False, "Should raise FrozenInstanceError"
        except AttributeError:
            pass  # Expected — frozen dataclass


# ── YAML loading ─────────────────────────────────────────────────────────────


class TestHeartbeatYamlLoading:
    def test_heartbeat_schedule_from_yaml(self, tmp_path):
        yaml_path = _write_role_yaml(
            tmp_path,
            """\
            id: it_sysadmin
            name: Sysadmin
            department_id: it
            description: IT sysadmin
            heartbeat_schedule: "*/15 * * * *"
            heartbeat_model: "haiku"
            briefing_skills:
              - system_health_check
        """,
        )
        role = load_role_from_yaml(yaml_path)
        assert role.heartbeat_schedule == "*/15 * * * *"
        assert role.heartbeat_model == "haiku"

    def test_missing_heartbeat_fields_default(self, tmp_path):
        yaml_path = _write_role_yaml(
            tmp_path,
            """\
            id: basic_role
            name: Basic Role
            department_id: test
            description: No heartbeat configured
            briefing_skills:
              - some_skill
        """,
        )
        role = load_role_from_yaml(yaml_path)
        assert role.heartbeat_schedule is None
        assert role.heartbeat_model == ""

    def test_heartbeat_schedule_only_no_model(self, tmp_path):
        yaml_path = _write_role_yaml(
            tmp_path,
            """\
            id: monitor_role
            name: Monitor Role
            department_id: ops
            description: Has heartbeat but uses default model
            heartbeat_schedule: "0 * * * *"
            briefing_skills:
              - check_health
        """,
        )
        role = load_role_from_yaml(yaml_path)
        assert role.heartbeat_schedule == "0 * * * *"
        assert role.heartbeat_model == ""  # Falls back to config default

    def test_real_head_of_it_yaml(self):
        """Verify the actual head_of_it role YAML has heartbeat_schedule."""
        from pathlib import Path as _PathLib

        yaml_path = (
            _PathLib(__file__).resolve().parents[2]
            / "src"
            / "skills"
            / "library"
            / "it"
            / "head_of_it"
            / "_role.yaml"
        )
        if yaml_path.exists():
            role = load_role_from_yaml(yaml_path)
            assert role.heartbeat_schedule is not None
            assert "15" in role.heartbeat_schedule  # Every 15 min

    def test_real_head_of_marketing_yaml(self):
        """Verify the actual head_of_marketing role YAML has heartbeat_schedule."""
        from pathlib import Path as _PathLib

        yaml_path = (
            _PathLib(__file__).resolve().parents[2]
            / "src"
            / "skills"
            / "library"
            / "marketing"
            / "head_of_marketing"
            / "_role.yaml"
        )
        if yaml_path.exists():
            role = load_role_from_yaml(yaml_path)
            assert role.heartbeat_schedule is not None
            assert "30" in role.heartbeat_schedule  # Every 30 min
