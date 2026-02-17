"""Google Drive / Docs / Sheets / Slides MCP server tools for Sidera.

Provides 8 tools that the Claude agent can call to interact with Google Drive
and the Google Workspace document editors.  Tools cover file search, metadata
retrieval, folder management, and full CRUD for Docs, Sheets, and Slides.

Tools:
    1. search_google_drive     - Search / list files in Google Drive
    2. get_drive_file_info     - Get detailed file metadata
    3. manage_drive_folders    - Create folders, move files, get shareable links
    4. create_google_doc       - Create a new Google Doc
    5. read_google_doc         - Read a Google Doc's content as text
    6. edit_google_doc         - Append text to a Google Doc
    7. manage_google_sheets    - Create, read, or write Google Sheets
    8. manage_google_slides    - Create presentations, add slides

Usage:
    from src.mcp_servers.google_drive import create_google_drive_mcp_server

    server_config = create_google_drive_mcp_server()
    # Pass to ClaudeAgentOptions.mcp_servers
"""

from __future__ import annotations

import traceback
from typing import Any

import structlog

from src.agent.tool_registry import tool
from src.connectors.google_drive import (
    GoogleDriveAuthError,
    GoogleDriveConnector,
    GoogleDriveConnectorError,
)
from src.mcp_servers.helpers import error_response, text_response

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_connector() -> GoogleDriveConnector:
    """Create a fresh GoogleDriveConnector instance."""
    return GoogleDriveConnector()


def _format_file_size(size_bytes: int | str | None) -> str:
    """Format a file size in bytes to a human-readable string."""
    if size_bytes is None:
        return "N/A"
    try:
        size = int(size_bytes)
    except (TypeError, ValueError):
        return "N/A"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.2f} GB"


# Mime-type mapping for file_type filter
_MIME_TYPE_MAP: dict[str, str] = {
    "document": "application/vnd.google-apps.document",
    "spreadsheet": "application/vnd.google-apps.spreadsheet",
    "presentation": "application/vnd.google-apps.presentation",
    "folder": "application/vnd.google-apps.folder",
}

# Reverse mapping for display
_MIME_TYPE_LABELS: dict[str, str] = {
    "application/vnd.google-apps.document": "Google Doc",
    "application/vnd.google-apps.spreadsheet": "Google Sheet",
    "application/vnd.google-apps.presentation": "Google Slides",
    "application/vnd.google-apps.folder": "Folder",
    "application/vnd.google-apps.form": "Google Form",
    "application/pdf": "PDF",
    "image/png": "PNG Image",
    "image/jpeg": "JPEG Image",
}


def _mime_label(mime_type: str | None) -> str:
    """Return a human-readable label for a MIME type."""
    if not mime_type:
        return "Unknown"
    return _MIME_TYPE_LABELS.get(mime_type, mime_type)


# ---------------------------------------------------------------------------
# Tool 1: Search Google Drive
# ---------------------------------------------------------------------------

SEARCH_GOOGLE_DRIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Optional. Text to search for in file names or content. "
                "If omitted, lists files without a text filter."
            ),
        },
        "folder_id": {
            "type": "string",
            "description": (
                "Optional. Google Drive folder ID to search within. "
                "If omitted, searches across the entire Drive."
            ),
        },
        "file_type": {
            "type": "string",
            "enum": [
                "document",
                "spreadsheet",
                "presentation",
                "folder",
                "all",
            ],
            "description": (
                "Optional. Filter by file type: 'document' (Google Docs), "
                "'spreadsheet' (Google Sheets), 'presentation' (Google "
                "Slides), 'folder', or 'all'. Defaults to 'all'."
            ),
        },
    },
    "required": [],
}


@tool(
    name="search_google_drive",
    description=(
        "Searches or lists files in the user's Google Drive. Can filter by "
        "text query, folder, and file type (document, spreadsheet, "
        "presentation, folder). Returns file names, IDs, types, and "
        "modification dates. Use this to discover files before reading "
        "or editing them."
    ),
    input_schema=SEARCH_GOOGLE_DRIVE_SCHEMA,
)
async def search_google_drive(
    args: dict[str, Any],
) -> dict[str, Any]:
    """Search or list files in Google Drive."""
    query = args.get("query")
    folder_id = args.get("folder_id")
    file_type = args.get("file_type")

    if query:
        query = str(query).strip()
    if folder_id:
        folder_id = str(folder_id).strip()
    if file_type:
        file_type = str(file_type).strip().lower()

    logger.info(
        "tool.search_google_drive",
        query=query,
        folder_id=folder_id,
        file_type=file_type,
    )
    try:
        connector = _get_connector()

        # Build the Drive query string from the parameters
        query_parts: list[str] = []

        # Add mimeType filter if file_type is specified and not "all"
        if file_type and file_type != "all":
            mime_type = _MIME_TYPE_MAP.get(file_type)
            if mime_type:
                query_parts.append(f"mimeType='{mime_type}'")

        # Add text search filter
        if query:
            safe_query = query.replace("'", "\\'")
            query_parts.append(f"fullText contains '{safe_query}'")

        drive_query = " and ".join(query_parts) if query_parts else None

        files = connector.list_files(
            folder_id=folder_id,
            query=drive_query,
            max_results=50,
        )

        if not files:
            filter_desc = ""
            if query:
                filter_desc += f" matching '{query}'"
            if folder_id:
                filter_desc += f" in folder {folder_id}"
            if file_type and file_type != "all":
                filter_desc += f" of type '{file_type}'"
            return text_response(
                f"No files found{filter_desc}. The Drive may be "
                "empty or the search criteria too narrow."
            )

        lines = [f"Found {len(files)} file(s) in Google Drive:\n"]
        for f in files:
            name = f.get("name", "Untitled")
            file_id = f.get("id", "unknown")
            mime = f.get("mimeType", "")
            modified = f.get("modifiedTime", "")
            size = f.get("size")

            lines.append(f"  - {name}")
            lines.append(f"      ID: {file_id}")
            lines.append(f"      Type: {_mime_label(mime)}")
            if modified:
                lines.append(f"      Modified: {modified}")
            if size:
                lines.append(f"      Size: {_format_file_size(size)}")

        return text_response("\n".join(lines))

    except GoogleDriveAuthError as exc:
        logger.error(
            "tool.search_google_drive.auth_error",
            error=str(exc),
        )
        return error_response(f"Google Drive auth error: {exc}")
    except GoogleDriveConnectorError as exc:
        logger.error(
            "tool.search_google_drive.connector_error",
            error=str(exc),
        )
        return error_response(f"Google Drive error: {exc}")
    except Exception as exc:
        logger.error(
            "tool.search_google_drive.unexpected_error",
            error=str(exc),
        )
        return error_response(f"Unexpected error: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Tool 2: Get Drive file info
# ---------------------------------------------------------------------------

GET_DRIVE_FILE_INFO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file_id": {
            "type": "string",
            "description": (
                "The Google Drive file ID to retrieve metadata for. "
                "Use search_google_drive to find file IDs."
            ),
        },
    },
    "required": ["file_id"],
}


@tool(
    name="get_drive_file_info",
    description=(
        "Gets detailed metadata for a specific file in Google Drive. "
        "Returns the file name, type, size, creation and modification "
        "dates, owner, and sharing status. Use this to inspect a file "
        "before reading or modifying it."
    ),
    input_schema=GET_DRIVE_FILE_INFO_SCHEMA,
)
async def get_drive_file_info(
    args: dict[str, Any],
) -> dict[str, Any]:
    """Get detailed metadata for a Google Drive file."""
    file_id = args.get("file_id", "").strip()
    if not file_id:
        return error_response("file_id is required.")

    logger.info("tool.get_drive_file_info", file_id=file_id)
    try:
        connector = _get_connector()
        meta = connector.get_file_metadata(file_id)

        if not meta:
            return text_response(
                f"No metadata returned for file {file_id}. "
                "The file may not exist or you may lack access."
            )

        name = meta.get("name", "Untitled")
        mime = meta.get("mimeType", "")
        created = meta.get("createdTime", "N/A")
        modified = meta.get("modifiedTime", "N/A")
        size = meta.get("size")
        owners = meta.get("owners", [])
        shared = meta.get("shared", False)
        web_link = meta.get("webViewLink", "")
        parent_ids = meta.get("parents", [])

        lines = [
            f"## File: {name}\n",
            f"**ID:** {file_id}",
            f"**Type:** {_mime_label(mime)}",
            f"**MIME type:** {mime}",
            f"**Size:** {_format_file_size(size)}",
            f"**Created:** {created}",
            f"**Modified:** {modified}",
            f"**Shared:** {'Yes' if shared else 'No'}",
        ]

        if owners:
            owner_names = [o.get("displayName", o.get("emailAddress", "?")) for o in owners]
            lines.append(f"**Owner(s):** {', '.join(owner_names)}")

        if parent_ids:
            lines.append(f"**Parent folder(s):** {', '.join(parent_ids)}")

        if web_link:
            lines.append(f"**Web link:** {web_link}")

        return text_response("\n".join(lines))

    except GoogleDriveAuthError as exc:
        logger.error(
            "tool.get_drive_file_info.auth_error",
            error=str(exc),
        )
        return error_response(f"Google Drive auth error: {exc}")
    except GoogleDriveConnectorError as exc:
        logger.error(
            "tool.get_drive_file_info.connector_error",
            error=str(exc),
        )
        return error_response(f"Google Drive error: {exc}")
    except Exception as exc:
        logger.error(
            "tool.get_drive_file_info.unexpected_error",
            error=str(exc),
        )
        return error_response(f"Unexpected error: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Tool 3: Manage Drive folders
# ---------------------------------------------------------------------------

MANAGE_DRIVE_FOLDERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["create", "move", "get_link"],
            "description": (
                "Action to perform: 'create' a new folder, 'move' a "
                "file to a different folder, or 'get_link' to get a "
                "shareable link for a file."
            ),
        },
        "name": {
            "type": "string",
            "description": ("Folder name. Required for action='create'."),
        },
        "parent_id": {
            "type": "string",
            "description": (
                "Parent folder ID for the new folder. Optional for "
                "action='create' (defaults to Drive root)."
            ),
        },
        "file_id": {
            "type": "string",
            "description": (
                "File ID to operate on. Required for action='move' and action='get_link'."
            ),
        },
        "new_parent_id": {
            "type": "string",
            "description": ("Destination folder ID. Required for action='move'."),
        },
    },
    "required": ["action"],
}


@tool(
    name="manage_drive_folders",
    description=(
        "Manages Google Drive folders and file organisation. Supports "
        "three actions: 'create' a new folder (optionally inside a "
        "parent folder), 'move' a file to a different folder, or "
        "'get_link' to retrieve a shareable link for a file."
    ),
    input_schema=MANAGE_DRIVE_FOLDERS_SCHEMA,
)
async def manage_drive_folders(
    args: dict[str, Any],
) -> dict[str, Any]:
    """Create folders, move files, or get shareable links."""
    action = args.get("action", "").strip().lower()
    if not action:
        return error_response("action is required ('create', 'move', or 'get_link').")

    logger.info("tool.manage_drive_folders", action=action)
    try:
        connector = _get_connector()

        # -- CREATE folder -------------------------------------------
        if action == "create":
            name = args.get("name", "").strip()
            if not name:
                return error_response("name is required for action='create'.")
            parent_id = args.get("parent_id")
            if parent_id:
                parent_id = str(parent_id).strip()

            result = connector.create_folder(name=name, parent_id=parent_id)

            lines = [
                "## Folder Created\n",
                f"**Name:** {result.get('name', name)}",
                f"**ID:** {result.get('id', 'N/A')}",
            ]
            if parent_id:
                lines.append(f"**Parent folder:** {parent_id}")
            web_link = result.get("webViewLink", "")
            if web_link:
                lines.append(f"**Link:** {web_link}")
            return text_response("\n".join(lines))

        # -- MOVE file -----------------------------------------------
        if action == "move":
            file_id = args.get("file_id", "").strip()
            new_parent_id = args.get("new_parent_id", "").strip()
            if not file_id:
                return error_response("file_id is required for action='move'.")
            if not new_parent_id:
                return error_response("new_parent_id is required for action='move'.")

            result = connector.move_file(
                file_id=file_id,
                new_parent_id=new_parent_id,
            )

            lines = [
                "## File Moved\n",
                f"**File ID:** {file_id}",
                f"**Name:** {result.get('name', 'N/A')}",
                f"**New parent:** {new_parent_id}",
            ]
            return text_response("\n".join(lines))

        # -- GET_LINK ------------------------------------------------
        if action == "get_link":
            file_id = args.get("file_id", "").strip()
            if not file_id:
                return error_response("file_id is required for action='get_link'.")

            result = connector.get_shareable_link(file_id=file_id)

            lines = [
                "## Shareable Link\n",
                f"**File:** {result.get('name', 'N/A')}",
                f"**ID:** {result.get('file_id', file_id)}",
                f"**Link:** {result.get('link', 'N/A')}",
            ]
            return text_response("\n".join(lines))

        # -- Unknown action ------------------------------------------
        return error_response(
            f"Unknown action '{action}'. Must be 'create', 'move', or 'get_link'."
        )

    except GoogleDriveAuthError as exc:
        logger.error(
            "tool.manage_drive_folders.auth_error",
            error=str(exc),
        )
        return error_response(f"Google Drive auth error: {exc}")
    except GoogleDriveConnectorError as exc:
        logger.error(
            "tool.manage_drive_folders.connector_error",
            error=str(exc),
        )
        return error_response(f"Google Drive error: {exc}")
    except Exception as exc:
        logger.error(
            "tool.manage_drive_folders.unexpected_error",
            error=str(exc),
        )
        return error_response(f"Unexpected error: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Tool 4: Create Google Doc
# ---------------------------------------------------------------------------

CREATE_GOOGLE_DOC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "Title of the new Google Doc.",
        },
        "content": {
            "type": "string",
            "description": (
                "Optional. Initial text content to insert into the "
                "document. If omitted, creates an empty document."
            ),
        },
    },
    "required": ["title"],
}


@tool(
    name="create_google_doc",
    description=(
        "Creates a new Google Doc in the user's Drive. Optionally "
        "populates it with initial text content. Returns the document "
        "ID and link so it can be read or edited later."
    ),
    input_schema=CREATE_GOOGLE_DOC_SCHEMA,
)
async def create_google_doc(
    args: dict[str, Any],
) -> dict[str, Any]:
    """Create a new Google Doc."""
    title = args.get("title", "").strip()
    if not title:
        return error_response("title is required.")

    content = args.get("content")
    if content:
        content = str(content)

    logger.info("tool.create_google_doc", title=title)
    try:
        connector = _get_connector()
        result = connector.create_document(title=title, content=content)

        doc_id = result.get("documentId", result.get("id", "N/A"))
        doc_title = result.get("title", title)
        doc_link = result.get("webViewLink", "")

        lines = [
            "## Google Doc Created\n",
            f"**Title:** {doc_title}",
            f"**Document ID:** {doc_id}",
        ]
        if doc_link:
            lines.append(f"**Link:** {doc_link}")
        if content:
            preview = content[:200] + "..." if len(content) > 200 else content
            lines.append(f"**Initial content:** {preview}")
        else:
            lines.append("**Content:** Empty document")

        return text_response("\n".join(lines))

    except GoogleDriveAuthError as exc:
        logger.error(
            "tool.create_google_doc.auth_error",
            error=str(exc),
        )
        return error_response(f"Google Drive auth error: {exc}")
    except GoogleDriveConnectorError as exc:
        logger.error(
            "tool.create_google_doc.connector_error",
            error=str(exc),
        )
        return error_response(f"Google Drive error: {exc}")
    except Exception as exc:
        logger.error(
            "tool.create_google_doc.unexpected_error",
            error=str(exc),
        )
        return error_response(f"Unexpected error: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Tool 5: Read Google Doc
# ---------------------------------------------------------------------------

READ_GOOGLE_DOC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "document_id": {
            "type": "string",
            "description": (
                "The Google Doc document ID. Use search_google_drive to find document IDs."
            ),
        },
    },
    "required": ["document_id"],
}


@tool(
    name="read_google_doc",
    description=(
        "Reads the full text content of a Google Doc. Returns the "
        "document title and plain-text body. Use search_google_drive "
        "to find document IDs first."
    ),
    input_schema=READ_GOOGLE_DOC_SCHEMA,
)
async def read_google_doc(
    args: dict[str, Any],
) -> dict[str, Any]:
    """Read a Google Doc's content as plain text."""
    document_id = args.get("document_id", "").strip()
    if not document_id:
        return error_response("document_id is required.")

    logger.info("tool.read_google_doc", document_id=document_id)
    try:
        connector = _get_connector()
        result = connector.read_document(document_id=document_id)

        doc_title = result.get("title", "Untitled")
        doc_content = result.get("content", "")
        doc_id = result.get("document_id", document_id)

        # Truncate very long documents for the agent
        max_chars = 50_000
        truncated = False
        if len(doc_content) > max_chars:
            doc_content = doc_content[:max_chars]
            truncated = True

        lines = [
            f"## {doc_title}\n",
            f"**Document ID:** {doc_id}",
            f"**Length:** {len(doc_content):,} characters",
        ]
        if truncated:
            lines.append(f"**Note:** Content truncated to first {max_chars:,} characters.")
        lines.append(f"\n---\n\n{doc_content}")

        return text_response("\n".join(lines))

    except GoogleDriveAuthError as exc:
        logger.error(
            "tool.read_google_doc.auth_error",
            error=str(exc),
        )
        return error_response(f"Google Drive auth error: {exc}")
    except GoogleDriveConnectorError as exc:
        logger.error(
            "tool.read_google_doc.connector_error",
            error=str(exc),
        )
        return error_response(f"Google Drive error: {exc}")
    except Exception as exc:
        logger.error(
            "tool.read_google_doc.unexpected_error",
            error=str(exc),
        )
        return error_response(f"Unexpected error: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Tool 6: Edit Google Doc (append)
# ---------------------------------------------------------------------------

EDIT_GOOGLE_DOC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "document_id": {
            "type": "string",
            "description": ("The Google Doc document ID to append text to."),
        },
        "content": {
            "type": "string",
            "description": ("Text content to append to the end of the document."),
        },
    },
    "required": ["document_id", "content"],
}


@tool(
    name="edit_google_doc",
    description=(
        "Appends text content to an existing Google Doc. The new text "
        "is added at the end of the document. Use read_google_doc "
        "first to see current content before appending."
    ),
    input_schema=EDIT_GOOGLE_DOC_SCHEMA,
)
async def edit_google_doc(
    args: dict[str, Any],
) -> dict[str, Any]:
    """Append text content to a Google Doc."""
    document_id = args.get("document_id", "").strip()
    content = args.get("content", "").strip()

    if not document_id:
        return error_response("document_id is required.")
    if not content:
        return error_response("content is required.")

    logger.info("tool.edit_google_doc", document_id=document_id)
    try:
        connector = _get_connector()
        result = connector.append_to_document(document_id=document_id, content=content)

        doc_title = result.get("title", "")
        doc_id = result.get("document_id", document_id)

        preview = content[:200] + "..." if len(content) > 200 else content

        lines = [
            "## Content Appended to Google Doc\n",
            f"**Document:** {doc_title or doc_id}",
            f"**Document ID:** {doc_id}",
            f"**Appended text:** {preview}",
            f"**Characters added:** {len(content):,}",
        ]
        return text_response("\n".join(lines))

    except GoogleDriveAuthError as exc:
        logger.error(
            "tool.edit_google_doc.auth_error",
            error=str(exc),
        )
        return error_response(f"Google Drive auth error: {exc}")
    except GoogleDriveConnectorError as exc:
        logger.error(
            "tool.edit_google_doc.connector_error",
            error=str(exc),
        )
        return error_response(f"Google Drive error: {exc}")
    except Exception as exc:
        logger.error(
            "tool.edit_google_doc.unexpected_error",
            error=str(exc),
        )
        return error_response(f"Unexpected error: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Tool 7: Manage Google Sheets
# ---------------------------------------------------------------------------

MANAGE_GOOGLE_SHEETS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["create", "read", "write"],
            "description": (
                "Action to perform: 'create' a new spreadsheet, "
                "'read' data from a spreadsheet, or 'write' data "
                "to a spreadsheet."
            ),
        },
        "title": {
            "type": "string",
            "description": ("Spreadsheet title. Required for action='create'."),
        },
        "spreadsheet_id": {
            "type": "string",
            "description": ("Spreadsheet ID. Required for action='read' and action='write'."),
        },
        "range": {
            "type": "string",
            "description": (
                "Cell range in A1 notation (e.g. 'Sheet1!A1:D10'). "
                "Optional for action='read' (defaults to 'Sheet1'). "
                "Required for action='write'."
            ),
        },
        "values": {
            "type": "array",
            "items": {
                "type": "array",
                "items": {},
            },
            "description": (
                "2D array of values to write. Required for "
                "action='write'. Each inner array is a row. "
                "Example: [['Name','Score'],['Alice',95]]"
            ),
        },
    },
    "required": ["action"],
}


@tool(
    name="manage_google_sheets",
    description=(
        "Creates, reads, or writes Google Sheets. Actions: 'create' a "
        "new spreadsheet (optionally with initial data), 'read' data "
        "from a range, or 'write' data to a range. For reading and "
        "writing, use A1 notation for ranges (e.g. 'Sheet1!A1:D10')."
    ),
    input_schema=MANAGE_GOOGLE_SHEETS_SCHEMA,
)
async def manage_google_sheets(
    args: dict[str, Any],
) -> dict[str, Any]:
    """Create, read, or write Google Sheets."""
    action = args.get("action", "").strip().lower()
    if not action:
        return error_response("action is required ('create', 'read', or 'write').")

    logger.info("tool.manage_google_sheets", action=action)
    try:
        connector = _get_connector()

        # -- CREATE spreadsheet --------------------------------------
        if action == "create":
            title = args.get("title", "").strip()
            if not title:
                return error_response("title is required for action='create'.")
            sheet_data = args.get("values")

            result = connector.create_spreadsheet(title=title, sheet_data=sheet_data)

            ss_id = result.get(
                "spreadsheetId",
                result.get("id", "N/A"),
            )
            ss_url = result.get("spreadsheetUrl", "")

            lines = [
                "## Spreadsheet Created\n",
                f"**Title:** {result.get('title', title)}",
                f"**Spreadsheet ID:** {ss_id}",
            ]
            if ss_url:
                lines.append(f"**Link:** {ss_url}")
            if sheet_data:
                rows = len(sheet_data)
                cols = max(len(r) for r in sheet_data) if sheet_data else 0
                lines.append(f"**Initial data:** {rows} row(s), {cols} column(s)")
            return text_response("\n".join(lines))

        # -- READ spreadsheet ----------------------------------------
        if action == "read":
            spreadsheet_id = args.get("spreadsheet_id", "").strip()
            if not spreadsheet_id:
                return error_response("spreadsheet_id is required for action='read'.")
            range_str = args.get("range", "Sheet1").strip()

            result = connector.read_spreadsheet(
                spreadsheet_id=spreadsheet_id,
                range=range_str,
            )

            values = result.get("values", [])
            num_rows = result.get("num_rows", len(values))
            num_cols = result.get("num_cols", 0)

            lines = [
                "## Spreadsheet Data\n",
                f"**Spreadsheet ID:** {result.get('spreadsheet_id', spreadsheet_id)}",
                f"**Range:** {result.get('range', range_str)}",
                f"**Dimensions:** {num_rows} row(s) x {num_cols} column(s)\n",
            ]

            if not values:
                lines.append("No data in the specified range.")
            else:
                lines.append(_format_sheet_table(values))

            return text_response("\n".join(lines))

        # -- WRITE to spreadsheet ------------------------------------
        if action == "write":
            spreadsheet_id = args.get("spreadsheet_id", "").strip()
            range_str = args.get("range", "").strip()
            values = args.get("values")

            if not spreadsheet_id:
                return error_response("spreadsheet_id is required for action='write'.")
            if not range_str:
                return error_response(
                    "range is required for action='write' (e.g. 'Sheet1!A1:D10')."
                )
            if not values or not isinstance(values, list):
                return error_response(
                    "values is required for action='write' (2D array of cell values)."
                )

            result = connector.write_to_spreadsheet(
                spreadsheet_id=spreadsheet_id,
                range=range_str,
                values=values,
            )

            rows_written = len(values)
            cols_written = max(len(r) for r in values) if values else 0

            lines = [
                "## Data Written to Spreadsheet\n",
                f"**Spreadsheet ID:** {spreadsheet_id}",
                f"**Range:** {range_str}",
                f"**Written:** {rows_written} row(s), {cols_written} column(s)",
            ]

            updated_range = result.get("updatedRange")
            if updated_range:
                lines.append(f"**Updated range:** {updated_range}")
            updated_cells = result.get("updatedCells")
            if updated_cells is not None:
                lines.append(f"**Cells updated:** {updated_cells}")

            return text_response("\n".join(lines))

        # -- Unknown action ------------------------------------------
        return error_response(f"Unknown action '{action}'. Must be 'create', 'read', or 'write'.")

    except GoogleDriveAuthError as exc:
        logger.error(
            "tool.manage_google_sheets.auth_error",
            error=str(exc),
        )
        return error_response(f"Google Drive auth error: {exc}")
    except GoogleDriveConnectorError as exc:
        logger.error(
            "tool.manage_google_sheets.connector_error",
            error=str(exc),
        )
        return error_response(f"Google Drive error: {exc}")
    except Exception as exc:
        logger.error(
            "tool.manage_google_sheets.unexpected_error",
            error=str(exc),
        )
        return error_response(f"Unexpected error: {exc}\n{traceback.format_exc()}")


def _format_sheet_table(values: list[list]) -> str:
    """Format a 2D array as a readable text table.

    Produces a pipe-delimited table with a header separator
    for the first row.
    """
    if not values:
        return "(empty)"

    # Convert all cells to strings and find column widths
    str_rows: list[list[str]] = []
    col_widths: list[int] = []
    for row in values:
        str_row = [str(cell) if cell is not None else "" for cell in row]
        str_rows.append(str_row)
        for i, cell in enumerate(str_row):
            if i >= len(col_widths):
                col_widths.append(len(cell))
            else:
                col_widths[i] = max(col_widths[i], len(cell))

    # Cap column widths for readability
    col_widths = [min(w, 30) for w in col_widths]

    def _format_row(row: list[str]) -> str:
        cells = []
        for i, cell in enumerate(row):
            width = col_widths[i] if i < len(col_widths) else 10
            cells.append(cell[:width].ljust(width))
        return "| " + " | ".join(cells) + " |"

    lines = [_format_row(str_rows[0])]
    # Header separator
    sep_cells = ["-" * col_widths[i] for i in range(len(col_widths))]
    lines.append("| " + " | ".join(sep_cells) + " |")
    for row in str_rows[1:]:
        lines.append(_format_row(row))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 8: Manage Google Slides
# ---------------------------------------------------------------------------

MANAGE_GOOGLE_SLIDES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["create", "add_slide"],
            "description": (
                "Action to perform: 'create' a new presentation or "
                "'add_slide' to an existing presentation."
            ),
        },
        "title": {
            "type": "string",
            "description": ("Presentation title. Required for action='create'."),
        },
        "presentation_id": {
            "type": "string",
            "description": ("Presentation ID. Required for action='add_slide'."),
        },
        "layout": {
            "type": "string",
            "description": (
                "Slide layout to use (e.g. 'BLANK', 'TITLE', "
                "'TITLE_AND_BODY'). Optional for action='add_slide'. "
                "Defaults to 'BLANK'."
            ),
        },
        "content": {
            "type": "string",
            "description": (
                "Optional text content for the new slide. Only used with action='add_slide'."
            ),
        },
    },
    "required": ["action"],
}


@tool(
    name="manage_google_slides",
    description=(
        "Creates Google Slides presentations and adds slides. Actions: "
        "'create' a new presentation with a title, or 'add_slide' to "
        "add a new slide to an existing presentation with optional "
        "layout and text content."
    ),
    input_schema=MANAGE_GOOGLE_SLIDES_SCHEMA,
)
async def manage_google_slides(
    args: dict[str, Any],
) -> dict[str, Any]:
    """Create presentations or add slides."""
    action = args.get("action", "").strip().lower()
    if not action:
        return error_response("action is required ('create' or 'add_slide').")

    logger.info("tool.manage_google_slides", action=action)
    try:
        connector = _get_connector()

        # -- CREATE presentation -------------------------------------
        if action == "create":
            title = args.get("title", "").strip()
            if not title:
                return error_response("title is required for action='create'.")

            result = connector.create_presentation(title=title)

            pres_id = result.get(
                "presentationId",
                result.get("id", "N/A"),
            )
            pres_title = result.get("title", title)
            pres_url = result.get("webViewLink", "")

            lines = [
                "## Presentation Created\n",
                f"**Title:** {pres_title}",
                f"**Presentation ID:** {pres_id}",
            ]
            if pres_url:
                lines.append(f"**Link:** {pres_url}")
            return text_response("\n".join(lines))

        # -- ADD SLIDE -----------------------------------------------
        if action == "add_slide":
            presentation_id = args.get("presentation_id", "").strip()
            if not presentation_id:
                return error_response("presentation_id is required for action='add_slide'.")

            layout = args.get("layout", "BLANK").strip()
            content = args.get("content")
            if content:
                content = str(content)

            result = connector.add_slide(
                presentation_id=presentation_id,
                layout=layout,
                content=content,
            )

            slide_id = result.get("slideId", "N/A")

            lines = [
                "## Slide Added\n",
                f"**Presentation ID:** {presentation_id}",
                f"**Slide ID:** {slide_id}",
                f"**Layout:** {layout}",
            ]
            if content:
                preview = content[:200] + "..." if len(content) > 200 else content
                lines.append(f"**Content:** {preview}")
            return text_response("\n".join(lines))

        # -- Unknown action ------------------------------------------
        return error_response(f"Unknown action '{action}'. Must be 'create' or 'add_slide'.")

    except GoogleDriveAuthError as exc:
        logger.error(
            "tool.manage_google_slides.auth_error",
            error=str(exc),
        )
        return error_response(f"Google Drive auth error: {exc}")
    except GoogleDriveConnectorError as exc:
        logger.error(
            "tool.manage_google_slides.connector_error",
            error=str(exc),
        )
        return error_response(f"Google Drive error: {exc}")
    except Exception as exc:
        logger.error(
            "tool.manage_google_slides.unexpected_error",
            error=str(exc),
        )
        return error_response(f"Unexpected error: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_google_drive_tools() -> list[Any]:
    """Return the list of Google Drive MCP tool definitions.

    These can be passed to ``create_sdk_mcp_server(tools=...)``
    or used individually for testing.

    Returns:
        List of 8 SdkMcpTool instances.
    """
    return [
        search_google_drive,
        get_drive_file_info,
        manage_drive_folders,
        create_google_doc,
        read_google_doc,
        edit_google_doc,
        manage_google_sheets,
        manage_google_slides,
    ]
