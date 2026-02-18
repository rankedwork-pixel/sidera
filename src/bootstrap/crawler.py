"""Google Drive document crawler for the bootstrap pipeline.

Recursively crawls a Google Drive folder, reads all supported documents
(Docs and Sheets), and returns them as ``RawDocument`` instances ready
for classification.

Reuses the existing ``GoogleDriveConnector`` for all API calls.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.bootstrap.models import RawDocument
from src.connectors.google_drive import GoogleDriveConnector

logger = structlog.get_logger(__name__)

# Maximum characters to keep from a single document.  Documents longer
# than this are truncated with a marker so the LLM still sees the
# beginning and end.
MAX_DOC_CHARS = 50_000
_TRUNCATION_KEEP = 10_000  # chars to keep from each end

# Google Drive MIME types we can read content from.
_READABLE_MIME_TYPES = {
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.spreadsheet",
}


def _truncate_content(content: str, max_chars: int = MAX_DOC_CHARS) -> str:
    """Truncate long content, keeping the first and last portions."""
    if len(content) <= max_chars:
        return content
    head = content[:_TRUNCATION_KEEP]
    tail = content[-_TRUNCATION_KEEP:]
    return f"{head}\n\n[... content truncated ...]\n\n{tail}"


async def crawl_folder(
    folder_id: str,
    *,
    max_docs: int = 100,
    connector: GoogleDriveConnector | None = None,
) -> list[RawDocument]:
    """Recursively crawl a Google Drive folder and read all documents.

    Parameters
    ----------
    folder_id:
        The Google Drive folder ID to crawl.
    max_docs:
        Maximum number of documents to read (prevents runaway costs).
    connector:
        Optional pre-configured connector.  Created from settings if
        not provided.

    Returns
    -------
    list[RawDocument]
        All readable documents found in the folder tree.
    """
    drive = connector or GoogleDriveConnector()
    documents: list[RawDocument] = []

    await _crawl_recursive(
        drive=drive,
        folder_id=folder_id,
        folder_path="",
        documents=documents,
        max_docs=max_docs,
    )

    logger.info(
        "bootstrap.crawl_complete",
        folder_id=folder_id,
        documents_found=len(documents),
        total_chars=sum(d.char_count for d in documents),
    )
    return documents


async def _crawl_recursive(
    *,
    drive: GoogleDriveConnector,
    folder_id: str,
    folder_path: str,
    documents: list[RawDocument],
    max_docs: int,
) -> None:
    """Recursively list files in a folder, reading docs and descending into subfolders."""
    if len(documents) >= max_docs:
        return

    files = drive.list_files(folder_id=folder_id, max_results=200)

    for file_info in files:
        if len(documents) >= max_docs:
            return

        mime = file_info.get("mimeType", "")
        file_id = file_info.get("id", "")
        title = file_info.get("name", "Untitled")

        # Recurse into subfolders
        if mime == "application/vnd.google-apps.folder":
            sub_path = f"{folder_path}/{title}" if folder_path else title
            await _crawl_recursive(
                drive=drive,
                folder_id=file_id,
                folder_path=sub_path,
                documents=documents,
                max_docs=max_docs,
            )
            continue

        # Skip unreadable file types
        if mime not in _READABLE_MIME_TYPES:
            logger.debug(
                "bootstrap.skip_file",
                file_id=file_id,
                title=title,
                mime_type=mime,
                reason="unsupported_mime_type",
            )
            continue

        # Read document content
        content = _read_file_content(drive, file_id, mime)
        if not content:
            continue

        content = _truncate_content(content)

        documents.append(
            RawDocument(
                file_id=file_id,
                title=title,
                mime_type=mime,
                content=content,
                char_count=len(content),
                folder_path=folder_path,
            )
        )

        logger.debug(
            "bootstrap.read_document",
            file_id=file_id,
            title=title,
            chars=len(content),
            truncated="[... content truncated ...]" in content,
        )


def _read_file_content(
    drive: GoogleDriveConnector, file_id: str, mime_type: str
) -> str:
    """Read content from a Google Doc or Sheet, returning plain text."""
    try:
        if mime_type == "application/vnd.google-apps.document":
            result = drive.read_document(file_id)
            if result and result.get("content"):
                return result["content"]

        elif mime_type == "application/vnd.google-apps.spreadsheet":
            result = drive.read_spreadsheet(file_id)
            if result and result.get("values"):
                return _sheet_to_text(result["values"])

    except Exception as exc:
        logger.warning(
            "bootstrap.read_error",
            file_id=file_id,
            mime_type=mime_type,
            error=str(exc),
        )
    return ""


def _sheet_to_text(values: list[list[Any]]) -> str:
    """Convert spreadsheet cell values to readable plain text."""
    lines: list[str] = []
    for row in values:
        line = " | ".join(str(cell) for cell in row if cell is not None)
        if line.strip():
            lines.append(line)
    return "\n".join(lines)
