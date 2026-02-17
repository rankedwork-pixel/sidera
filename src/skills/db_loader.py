"""DB-aware registry loader for Sidera.

Loads skill/role/department definitions from disk (YAML), then overlays
any definitions stored in PostgreSQL.  DB entries with the same ID as
disk entries **replace** them entirely; new IDs are **added**.

If the database is unavailable (connection error, missing tables, etc.)
the loader falls back silently to disk-only mode.

Usage::

    from src.skills.db_loader import load_registry_with_db

    registry = await load_registry_with_db()
    # registry now contains disk + DB definitions merged
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from src.skills.registry import SkillRegistry

logger = structlog.get_logger(__name__)


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Convert a SQLAlchemy model instance to a plain dict.

    Handles both mapped instances (with ``__dict__``) and Row objects.
    Strips SQLAlchemy internal keys (prefixed with ``_``).
    """
    if hasattr(row, "__dict__"):
        return {k: v for k, v in row.__dict__.items() if not k.startswith("_")}
    # Fallback for Row/RowProxy
    return dict(row._mapping) if hasattr(row, "_mapping") else {}


def _rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    """Convert a list of SQLAlchemy rows to plain dicts."""
    return [_row_to_dict(r) for r in rows]


async def load_registry_with_db(
    skills_dir: Path | None = None,
) -> SkillRegistry:
    """Load from disk, then overlay DB definitions.

    Args:
        skills_dir: Optional override for the YAML skills directory.

    Returns:
        A fully merged ``SkillRegistry`` instance.
    """
    registry = SkillRegistry(skills_dir=skills_dir)
    registry.load_all()

    try:
        from src.db import service as db_service
        from src.db.session import get_db_session

        async with get_db_session() as session:
            depts = await db_service.list_org_departments(session, active_only=True)
            roles = await db_service.list_org_roles(session, active_only=True)
            skills = await db_service.list_org_skills(session, active_only=True)

        registry.merge_db_definitions(
            _rows_to_dicts(depts),
            _rows_to_dicts(roles),
            _rows_to_dicts(skills),
        )
        logger.info(
            "registry.db_loaded",
            db_departments=len(depts),
            db_roles=len(roles),
            db_skills=len(skills),
        )

    except Exception as exc:
        # DB not available — disk-only mode
        logger.warning(
            "registry.db_unavailable",
            error=str(exc),
            fallback="disk_only",
        )

    return registry
