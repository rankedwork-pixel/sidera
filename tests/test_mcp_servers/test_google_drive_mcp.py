"""Tests for src.mcp_servers.google_drive -- Google Drive MCP tools.

Covers all 8 tools: search_google_drive, get_drive_file_info,
manage_drive_folders, create_google_doc, read_google_doc,
edit_google_doc, manage_google_sheets, manage_google_slides.

All connector calls are mocked via _get_connector(); no network traffic needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.connectors.google_drive import (
    GoogleDriveAuthError,
    GoogleDriveConnectorError,
)
from src.mcp_servers.google_drive import (
    create_google_doc,
    edit_google_doc,
    get_drive_file_info,
    manage_drive_folders,
    manage_google_sheets,
    manage_google_slides,
    read_google_doc,
    search_google_drive,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PATCH_TARGET = "src.mcp_servers.google_drive._get_connector"


@pytest.fixture()
def mock_connector():
    """Return a MagicMock standing in for GoogleDriveConnector."""
    with patch(PATCH_TARGET) as mock_get:
        connector = MagicMock()
        mock_get.return_value = connector
        yield connector


# ---------------------------------------------------------------------------
# Tool 1: search_google_drive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_files_basic(mock_connector):
    """List files with no filters returns formatted file list."""
    mock_connector.list_files.return_value = [
        {
            "id": "file1",
            "name": "Report Q1",
            "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2025-01-15T10:00:00Z",
            "size": None,
        },
        {
            "id": "file2",
            "name": "Budget Sheet",
            "mimeType": "application/vnd.google-apps.spreadsheet",
            "modifiedTime": "2025-01-16T12:00:00Z",
            "size": "2048",
        },
    ]

    result = await search_google_drive.handler({})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "2 file(s)" in text
    assert "Report Q1" in text
    assert "Budget Sheet" in text
    assert "file1" in text
    assert "file2" in text
    assert "Google Doc" in text
    assert "Google Sheet" in text


@pytest.mark.asyncio
async def test_search_files_with_query(mock_connector):
    """Text query adds fullText filter to the Drive query."""
    mock_connector.list_files.return_value = [
        {
            "id": "abc",
            "name": "Test Doc",
            "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2025-01-10T08:00:00Z",
            "size": None,
        },
    ]

    result = await search_google_drive.handler({"query": "test"})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "1 file(s)" in text
    assert "Test Doc" in text
    # Verify the connector was called with a query containing fullText
    call_kwargs = mock_connector.list_files.call_args
    query_arg = call_kwargs.kwargs.get("query", call_kwargs[1].get("query", ""))
    assert "fullText contains" in query_arg


@pytest.mark.asyncio
async def test_search_files_with_file_type(mock_connector):
    """file_type filter adds mimeType clause to the Drive query."""
    mock_connector.list_files.return_value = []

    await search_google_drive.handler({"file_type": "spreadsheet"})

    call_kwargs = mock_connector.list_files.call_args
    query_arg = call_kwargs.kwargs.get("query", call_kwargs[1].get("query", ""))
    assert "mimeType=" in query_arg
    assert "spreadsheet" in query_arg


@pytest.mark.asyncio
async def test_search_files_empty_results(mock_connector):
    """Returns 'No files found' message when list is empty."""
    mock_connector.list_files.return_value = []

    result = await search_google_drive.handler({})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "No files found" in text


@pytest.mark.asyncio
async def test_search_files_auth_error(mock_connector):
    """GoogleDriveAuthError returns an is_error response."""
    mock_connector.list_files.side_effect = GoogleDriveAuthError("Token expired")

    result = await search_google_drive.handler({})

    assert result["is_error"] is True
    assert "ERROR:" in result["content"][0]["text"]
    assert "Token expired" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Tool 2: get_drive_file_info
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_file_info_success(mock_connector):
    """Returns formatted metadata for a valid file."""
    mock_connector.get_file_metadata.return_value = {
        "name": "Annual Report",
        "mimeType": "application/pdf",
        "createdTime": "2025-01-01T00:00:00Z",
        "modifiedTime": "2025-02-01T00:00:00Z",
        "size": 1048576,
        "owners": [{"displayName": "Alice"}],
        "shared": True,
        "webViewLink": "https://drive.google.com/file/d/abc",
        "parents": ["folder123"],
    }

    result = await get_drive_file_info.handler({"file_id": "abc123"})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "Annual Report" in text
    assert "abc123" in text
    assert "PDF" in text
    assert "Alice" in text
    assert "Yes" in text  # shared
    assert "folder123" in text
    assert "https://drive.google.com/file/d/abc" in text


@pytest.mark.asyncio
async def test_get_file_info_not_found(mock_connector):
    """None result returns appropriate 'not found' message."""
    mock_connector.get_file_metadata.return_value = None

    result = await get_drive_file_info.handler({"file_id": "missing"})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "No metadata returned" in text


@pytest.mark.asyncio
async def test_get_file_info_missing_id(mock_connector):
    """Returns error when file_id is not provided."""
    result = await get_drive_file_info.handler({})

    assert result["is_error"] is True
    assert "file_id is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_file_info_connector_error(mock_connector):
    """GoogleDriveConnectorError returns an is_error response."""
    mock_connector.get_file_metadata.side_effect = GoogleDriveConnectorError("API unavailable")

    result = await get_drive_file_info.handler({"file_id": "abc123"})

    assert result["is_error"] is True
    assert "API unavailable" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Tool 3: manage_drive_folders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_folder(mock_connector):
    """action='create' calls create_folder and returns info."""
    mock_connector.create_folder.return_value = {
        "id": "folder_new",
        "name": "Reports",
        "webViewLink": "https://drive.google.com/folders/folder_new",
    }

    result = await manage_drive_folders.handler({"action": "create", "name": "Reports"})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "Folder Created" in text
    assert "Reports" in text
    assert "folder_new" in text
    mock_connector.create_folder.assert_called_once_with(name="Reports", parent_id=None)


@pytest.mark.asyncio
async def test_create_folder_missing_name(mock_connector):
    """Returns error without name for action='create'."""
    result = await manage_drive_folders.handler({"action": "create"})

    assert result["is_error"] is True
    assert "name is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_move_file(mock_connector):
    """action='move' calls move_file and returns info."""
    mock_connector.move_file.return_value = {
        "id": "file_x",
        "name": "My File",
        "parents": ["dest_folder"],
    }

    result = await manage_drive_folders.handler(
        {
            "action": "move",
            "file_id": "file_x",
            "new_parent_id": "dest_folder",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "File Moved" in text
    assert "file_x" in text
    assert "My File" in text
    assert "dest_folder" in text


@pytest.mark.asyncio
async def test_move_file_missing_params(mock_connector):
    """Returns error without file_id or new_parent_id."""
    result = await manage_drive_folders.handler({"action": "move"})
    assert result["is_error"] is True
    assert "file_id is required" in result["content"][0]["text"]

    result2 = await manage_drive_folders.handler({"action": "move", "file_id": "abc"})
    assert result2["is_error"] is True
    assert "new_parent_id is required" in result2["content"][0]["text"]


@pytest.mark.asyncio
async def test_get_link(mock_connector):
    """action='get_link' calls get_shareable_link and returns info."""
    # MCP code calls result.get('name'), result.get('link') etc.
    # so mock must return a dict, not a string.
    mock_connector.get_shareable_link.return_value = {
        "name": "Shared File",
        "file_id": "file_z",
        "link": "https://drive.google.com/file/d/file_z",
    }

    result = await manage_drive_folders.handler({"action": "get_link", "file_id": "file_z"})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "Shareable Link" in text
    assert "file_z" in text
    assert "https://drive.google.com/file/d/file_z" in text


# ---------------------------------------------------------------------------
# Tool 4: create_google_doc
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_doc_basic(mock_connector):
    """Creates doc with title only, no initial content."""
    mock_connector.create_document.return_value = {
        "documentId": "doc_abc",
        "title": "My Doc",
        "webViewLink": "https://docs.google.com/document/d/doc_abc",
    }

    result = await create_google_doc.handler({"title": "My Doc"})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "Google Doc Created" in text
    assert "My Doc" in text
    assert "doc_abc" in text
    assert "Empty document" in text
    mock_connector.create_document.assert_called_once_with(title="My Doc", content=None)


@pytest.mark.asyncio
async def test_create_doc_with_content(mock_connector):
    """Creates doc with initial text content."""
    mock_connector.create_document.return_value = {
        "documentId": "doc_xyz",
        "title": "Report",
        "webViewLink": "https://docs.google.com/document/d/doc_xyz",
    }

    result = await create_google_doc.handler({"title": "Report", "content": "Hello world"})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "Report" in text
    assert "doc_xyz" in text
    assert "Hello world" in text
    mock_connector.create_document.assert_called_once_with(title="Report", content="Hello world")


@pytest.mark.asyncio
async def test_create_doc_missing_title(mock_connector):
    """Returns error without title."""
    result = await create_google_doc.handler({})

    assert result["is_error"] is True
    assert "title is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_create_doc_auth_error(mock_connector):
    """Auth error returns error response."""
    mock_connector.create_document.side_effect = GoogleDriveAuthError("Unauthorized")

    result = await create_google_doc.handler({"title": "Fail Doc"})

    assert result["is_error"] is True
    assert "Unauthorized" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Tool 5: read_google_doc
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_doc_success(mock_connector):
    """Returns title and content for a valid document."""
    mock_connector.read_document.return_value = {
        "document_id": "doc_read",
        "title": "My Notes",
        "content": "Some notes here.",
    }

    result = await read_google_doc.handler({"document_id": "doc_read"})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "My Notes" in text
    assert "doc_read" in text
    assert "Some notes here." in text


@pytest.mark.asyncio
async def test_read_doc_truncation(mock_connector):
    """Long content is truncated at 50K characters."""
    long_content = "x" * 60_000
    mock_connector.read_document.return_value = {
        "document_id": "doc_long",
        "title": "Long Doc",
        "content": long_content,
    }

    result = await read_google_doc.handler({"document_id": "doc_long"})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "truncated" in text.lower()
    assert "50,000" in text


@pytest.mark.asyncio
async def test_read_doc_missing_id(mock_connector):
    """Returns error without document_id."""
    result = await read_google_doc.handler({})

    assert result["is_error"] is True
    assert "document_id is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_read_doc_connector_error(mock_connector):
    """Connector error returns error response."""
    mock_connector.read_document.side_effect = GoogleDriveConnectorError("Read failed")

    result = await read_google_doc.handler({"document_id": "doc_fail"})

    assert result["is_error"] is True
    assert "Read failed" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Tool 6: edit_google_doc
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_doc_success(mock_connector):
    """Appends content successfully and returns info."""
    # MCP code calls result.get("title"), result.get("document_id")
    # so we mock a dict return, not a bool.
    mock_connector.append_to_document.return_value = {
        "document_id": "doc_edit",
        "title": "Editable Doc",
    }

    result = await edit_google_doc.handler({"document_id": "doc_edit", "content": "New paragraph."})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "Content Appended" in text
    assert "Editable Doc" in text
    assert "doc_edit" in text
    assert "New paragraph." in text


@pytest.mark.asyncio
async def test_edit_doc_missing_id(mock_connector):
    """Returns error without document_id."""
    result = await edit_google_doc.handler({"content": "text"})

    assert result["is_error"] is True
    assert "document_id is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_edit_doc_missing_content(mock_connector):
    """Returns error without content."""
    result = await edit_google_doc.handler({"document_id": "doc_x"})

    assert result["is_error"] is True
    assert "content is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_edit_doc_unexpected_error(mock_connector):
    """General Exception returns error with traceback."""
    mock_connector.append_to_document.side_effect = ValueError("something broke")

    result = await edit_google_doc.handler({"document_id": "doc_err", "content": "data"})

    assert result["is_error"] is True
    text = result["content"][0]["text"]
    assert "something broke" in text
    assert "Traceback" in text


# ---------------------------------------------------------------------------
# Tool 7: manage_google_sheets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sheets_create(mock_connector):
    """action='create' creates a spreadsheet."""
    mock_connector.create_spreadsheet.return_value = {
        "spreadsheetId": "ss_abc",
        "title": "My Sheet",
        "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/ss_abc",
    }

    result = await manage_google_sheets.handler({"action": "create", "title": "My Sheet"})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "Spreadsheet Created" in text
    assert "My Sheet" in text
    assert "ss_abc" in text


@pytest.mark.asyncio
async def test_sheets_read(mock_connector):
    """action='read' reads spreadsheet data."""
    mock_connector.read_spreadsheet.return_value = {
        "spreadsheet_id": "ss_read",
        "range": "Sheet1!A1:B2",
        "values": [["Name", "Score"], ["Alice", "95"]],
        "num_rows": 2,
        "num_cols": 2,
    }

    result = await manage_google_sheets.handler(
        {
            "action": "read",
            "spreadsheet_id": "ss_read",
            "range": "Sheet1!A1:B2",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "Spreadsheet Data" in text
    assert "ss_read" in text
    assert "2 row(s)" in text
    assert "Name" in text
    assert "Alice" in text


@pytest.mark.asyncio
async def test_sheets_write(mock_connector):
    """action='write' writes data to cells."""
    mock_connector.write_to_spreadsheet.return_value = {
        "updatedRange": "Sheet1!A1:B2",
        "updatedRows": 2,
        "updatedColumns": 2,
        "updatedCells": 4,
    }

    values = [["Name", "Score"], ["Bob", "88"]]
    result = await manage_google_sheets.handler(
        {
            "action": "write",
            "spreadsheet_id": "ss_write",
            "range": "Sheet1!A1:B2",
            "values": values,
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "Data Written" in text
    assert "ss_write" in text
    assert "2 row(s)" in text
    assert "2 column(s)" in text
    assert "4" in text  # updated cells


@pytest.mark.asyncio
async def test_sheets_create_missing_title(mock_connector):
    """Returns error for action='create' without title."""
    result = await manage_google_sheets.handler({"action": "create"})

    assert result["is_error"] is True
    assert "title is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_sheets_read_missing_id(mock_connector):
    """Returns error for action='read' without spreadsheet_id."""
    result = await manage_google_sheets.handler({"action": "read"})

    assert result["is_error"] is True
    assert "spreadsheet_id is required" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Tool 8: manage_google_slides
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slides_create(mock_connector):
    """action='create' creates a presentation."""
    mock_connector.create_presentation.return_value = {
        "presentationId": "pres_abc",
        "title": "Quarterly Review",
        "webViewLink": "https://docs.google.com/presentation/d/pres_abc",
    }

    result = await manage_google_slides.handler({"action": "create", "title": "Quarterly Review"})
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "Presentation Created" in text
    assert "Quarterly Review" in text
    assert "pres_abc" in text


@pytest.mark.asyncio
async def test_slides_add_slide(mock_connector):
    """action='add_slide' adds a slide to an existing presentation."""
    mock_connector.add_slide.return_value = {
        "presentation_id": "pres_xyz",
        "slideId": "slide_001",
    }

    result = await manage_google_slides.handler(
        {
            "action": "add_slide",
            "presentation_id": "pres_xyz",
            "layout": "TITLE",
            "content": "Slide text content",
        }
    )
    text = result["content"][0]["text"]

    assert "is_error" not in result
    assert "Slide Added" in text
    assert "pres_xyz" in text
    assert "slide_001" in text
    assert "TITLE" in text
    assert "Slide text content" in text


@pytest.mark.asyncio
async def test_slides_add_slide_missing_id(mock_connector):
    """Returns error without presentation_id."""
    result = await manage_google_slides.handler({"action": "add_slide"})

    assert result["is_error"] is True
    assert "presentation_id is required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_slides_unknown_action(mock_connector):
    """Returns error for an invalid action."""
    result = await manage_google_slides.handler({"action": "delete"})

    assert result["is_error"] is True
    assert "Unknown action" in result["content"][0]["text"]
