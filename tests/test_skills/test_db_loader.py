"""Tests for src.skills.db_loader -- load_registry_with_db().

Covers:
- Falls back to disk-only when DB is unavailable
- Returns a SkillRegistry instance
- Loads disk definitions even when DB fails
- Merges DB definitions when DB is available
- Passes through custom skills_dir parameter

The function imports ``get_db_session`` and ``db_service`` inside the
try block (lazy import), so we patch them at their source modules.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from src.skills.db_loader import load_registry_with_db
from src.skills.registry import SkillRegistry

# ---------------------------------------------------------------------------
# Shared mock tools
# ---------------------------------------------------------------------------

_MOCK_ALL_TOOLS = [
    "get_meta_campaigns",
    "get_meta_performance",
    "get_google_ads_performance",
]


# ---------------------------------------------------------------------------
# YAML content helpers
# ---------------------------------------------------------------------------


def _skill_yaml(
    skill_id: str = "disk_skill",
    name: str = "Disk Skill",
) -> str:
    """Return a YAML string for a valid skill definition."""
    data = {
        "id": skill_id,
        "name": name,
        "version": "1.0",
        "description": f"Description for {name}",
        "category": "analysis",
        "platforms": ["google_ads"],
        "tags": ["test"],
        "tools_required": ["get_meta_campaigns"],
        "model": "sonnet",
        "max_turns": 10,
        "system_supplement": f"System supplement for {name}.",
        "prompt_template": f"Run {name} analysis.",
        "output_format": "## Results\nShow results.",
        "business_guidance": "Follow best practices.",
        "requires_approval": True,
        "author": "sidera",
    }
    return yaml.dump(data, default_flow_style=False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def skills_dir(tmp_path: Path) -> Path:
    """Create a temp directory with one valid skill YAML."""
    p = tmp_path / "disk_skill.yaml"
    p.write_text(_skill_yaml(), encoding="utf-8")
    return tmp_path


# ===========================================================================
# Tests
# ===========================================================================


class TestLoadRegistryWithDb:
    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    async def test_returns_skill_registry_instance(self, skills_dir: Path):
        """load_registry_with_db returns a SkillRegistry."""
        # Patch get_db_session at its source module so the lazy import finds it
        with patch("src.db.session.get_db_session", side_effect=Exception("DB down")):
            result = await load_registry_with_db(skills_dir=skills_dir)

        assert isinstance(result, SkillRegistry)

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    async def test_fallback_to_disk_when_db_unavailable(self, skills_dir: Path):
        """When get_db_session raises, falls back to disk-only and still loads skills."""
        with patch("src.db.session.get_db_session", side_effect=Exception("Connection refused")):
            registry = await load_registry_with_db(skills_dir=skills_dir)

        # Disk skill should be loaded
        assert registry.count >= 1
        assert registry.get("disk_skill") is not None

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    async def test_loads_disk_definitions_on_db_failure(self, skills_dir: Path):
        """Disk definitions are loaded correctly even when DB fails."""
        with patch("src.db.session.get_db_session", side_effect=RuntimeError("No DB")):
            registry = await load_registry_with_db(skills_dir=skills_dir)

        skill = registry.get("disk_skill")
        assert skill is not None
        assert skill.name == "Disk Skill"
        assert skill.model == "sonnet"
        assert skill.category == "analysis"

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    async def test_merges_db_definitions_when_available(self, skills_dir: Path):
        """When DB is available, DB definitions are merged into the registry."""
        # Create mock DB ORM objects with __dict__ (like SQLAlchemy instances)
        mock_dept = MagicMock()
        mock_dept.__dict__ = {
            "dept_id": "db_dept",
            "name": "DB Department",
            "description": "From DB",
            "context": "",
            "context_text": "",
        }

        mock_session = AsyncMock()

        # Create an async context manager for get_db_session
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_session)
        mock_context.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.db.session.get_db_session", return_value=mock_context),
            patch(
                "src.db.service.list_org_departments",
                new_callable=AsyncMock,
                return_value=[mock_dept],
            ),
            patch(
                "src.db.service.list_org_roles",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "src.db.service.list_org_skills",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            registry = await load_registry_with_db(skills_dir=skills_dir)

        # Disk skill loaded
        assert registry.get("disk_skill") is not None
        # DB department merged (the mock_dept __dict__ has required fields)
        dept = registry.get_department("db_dept")
        assert dept is not None
        assert dept.name == "DB Department"

    @patch("src.skills.schema.ALL_TOOLS", _MOCK_ALL_TOOLS)
    async def test_passes_custom_skills_dir(self, skills_dir: Path):
        """The custom skills_dir parameter is respected."""
        with patch("src.db.session.get_db_session", side_effect=Exception("DB down")):
            registry = await load_registry_with_db(skills_dir=skills_dir)

        assert registry.skills_dir == skills_dir
