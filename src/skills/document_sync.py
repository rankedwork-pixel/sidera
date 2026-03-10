"""Google Drive document sync for living documents.

Formats and appends role outputs (briefings, meeting summaries) to
designated Google Docs.  Each entry includes a timestamp header and
metadata footer.  All operations are non-fatal -- failures are logged
and never block the main workflow.

Usage::

    from src.skills.document_sync import sync_role_output_to_drive

    result = await sync_role_output_to_drive(
        role_id="performance_media_buyer",
        output_type="briefings",
        content="## Daily Briefing\\n...",
        metadata={"cost_usd": 0.52, "skills_run": 3},
    )
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _get_document_sync_config(role_id: str, registry: Any) -> dict[str, str]:
    """Resolve document_sync config for a role.

    Returns a dict mapping output_type -> doc_id.
    """
    role = registry.get_role(role_id)
    if role is None:
        return {}
    if not role.document_sync:
        return {}
    return {output_type: doc_id for output_type, doc_id in role.document_sync}


def format_drive_entry(
    role_name: str,
    output_type: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> str:
    """Format a Drive document entry with header, content, and footer.

    Args:
        role_name: Human-readable role name.
        output_type: ``"briefings"``, ``"meetings"``, etc.
        content: The main text to append.
        metadata: Optional dict with cost, skills_run, duration, etc.
        timestamp: Override for the entry timestamp (defaults to now UTC).

    Returns:
        Formatted text ready for ``append_to_document()``.
    """
    ts = timestamp or datetime.now(timezone.utc)
    date_str = ts.strftime("%Y-%m-%d %H:%M UTC")
    type_label = output_type.rstrip("s").title()  # "briefings" -> "Briefing"

    header = f"\n---\n## {date_str} -- {role_name} {type_label}\n\n"

    footer_parts: list[str] = []
    if metadata:
        if "cost_usd" in metadata:
            footer_parts.append(f"Cost: ${metadata['cost_usd']:.2f}")
        if "skills_run" in metadata:
            footer_parts.append(f"Skills: {metadata['skills_run']}")
        if "duration_seconds" in metadata:
            footer_parts.append(f"Duration: {metadata['duration_seconds']}s")
        if "action_items_count" in metadata:
            footer_parts.append(f"Action items: {metadata['action_items_count']}")

    footer = ""
    if footer_parts:
        footer = f"\n\n_{' | '.join(footer_parts)}_"

    return f"{header}{content}{footer}\n"


async def sync_role_output_to_drive(
    role_id: str,
    output_type: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    role_name: str | None = None,
) -> dict[str, Any]:
    """Append formatted output to the designated Google Doc.

    Loads the role's ``document_sync`` config from the registry, formats
    the content with a timestamp header, and calls
    ``append_to_document()``.

    Non-fatal: all exceptions are caught, logged, and returned as
    error dicts.  Never raises.

    Args:
        role_id: The role whose document_sync config to use.
        output_type: Key in document_sync dict (``"briefings"``,
            ``"meetings"``).
        content: The text to append.
        metadata: Optional metadata dict for the footer.
        role_name: Optional human-readable name (derived from role if
            absent).

    Returns:
        Dict with ``"synced"`` (bool), ``"doc_id"`` (str), and
        optionally ``"error"`` (str).
    """
    try:
        from src.skills.db_loader import load_registry_with_db

        registry = await load_registry_with_db()
        sync_config = _get_document_sync_config(role_id, registry)

        if not sync_config:
            logger.debug(
                "document_sync.no_config",
                role_id=role_id,
                output_type=output_type,
            )
            return {"synced": False, "doc_id": "", "reason": "no_config"}

        doc_id = sync_config.get(output_type)
        if not doc_id:
            logger.debug(
                "document_sync.no_doc_for_type",
                role_id=role_id,
                output_type=output_type,
            )
            return {"synced": False, "doc_id": "", "reason": "no_doc_for_type"}

        # Resolve role name
        display_name = role_name or role_id
        if not role_name:
            role = registry.get_role(role_id)
            if role:
                display_name = role.name

        # Format the entry
        entry_text = format_drive_entry(
            role_name=display_name,
            output_type=output_type,
            content=content,
            metadata=metadata,
        )

        # Append to doc — requires Google Drive connector (install separately)
        try:
            from src.connectors.google_drive import GoogleDriveConnector
        except ImportError:
            logger.info("document_sync.skipped", reason="google_drive connector not installed")
            return {"synced": False, "reason": "google_drive connector not installed"}

        connector = GoogleDriveConnector()
        success = connector.append_to_document(doc_id, entry_text)

        if success:
            logger.info(
                "document_sync.appended",
                role_id=role_id,
                output_type=output_type,
                doc_id=doc_id,
                content_length=len(entry_text),
            )
            return {"synced": True, "doc_id": doc_id}

        logger.warning(
            "document_sync.append_failed",
            role_id=role_id,
            output_type=output_type,
            doc_id=doc_id,
        )
        return {"synced": False, "doc_id": doc_id, "reason": "append_returned_false"}

    except Exception as exc:
        logger.warning(
            "document_sync.error",
            role_id=role_id,
            output_type=output_type,
            error=str(exc),
        )
        return {"synced": False, "doc_id": "", "error": str(exc)}
