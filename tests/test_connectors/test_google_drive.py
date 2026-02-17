"""Tests for src.connectors.google_drive -- GoogleDriveConnector.

Covers construction, every public method (Drive, Docs, Sheets, Slides),
and error handling. All Google API calls are mocked; no network traffic
is needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Patch the @cached decorator to be a no-op BEFORE importing the connector.
# The decorator wraps sync methods and tries to talk to Redis via
# asyncio.run, which would fail in test isolation. Replacing it with a
# passthrough avoids that entirely.
# ---------------------------------------------------------------------------
import src.cache.decorators

_original_cached = src.cache.decorators.cached


def _noop_cached(**_kwargs):
    """Passthrough decorator -- returns the function unchanged."""

    def decorator(func):
        return func

    return decorator


src.cache.decorators.cached = _noop_cached

# Now safe to import the connector (decorator is already neutered)
from googleapiclient.errors import HttpError  # noqa: E402

from src.connectors.google_drive import (  # noqa: E402
    GoogleDriveAuthError,
    GoogleDriveConnector,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_http_error(status=404, reason="Not Found"):
    """Create a mock HttpError with the given status code."""
    resp = MagicMock()
    resp.status = status
    error = HttpError(resp=resp, content=b"error")
    return error


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def connector():
    """Create a connector with mocked services."""
    with patch.object(GoogleDriveConnector, "_build_services"):
        c = GoogleDriveConnector(
            credentials={
                "client_id": "test-id",
                "client_secret": "test-secret",
                "refresh_token": "test-token",
            }
        )
        c._drive = MagicMock()
        c._docs = MagicMock()
        c._sheets = MagicMock()
        c._slides = MagicMock()
        yield c


# ===========================================================================
# 1. Construction
# ===========================================================================


class TestConstruction:
    """GoogleDriveConnector.__init__ and _build_services."""

    def test_construction_with_explicit_credentials(self):
        """Pass credentials dict, verify they are stored."""
        with patch.object(GoogleDriveConnector, "_build_services"):
            c = GoogleDriveConnector(
                credentials={
                    "client_id": "my-id",
                    "client_secret": "my-secret",
                    "refresh_token": "my-token",
                }
            )

        assert c._credentials["client_id"] == "my-id"
        assert c._credentials["client_secret"] == "my-secret"
        assert c._credentials["refresh_token"] == "my-token"
        assert c._client_id == "my-id"
        assert c._client_secret == "my-secret"
        assert c._refresh_token == "my-token"

    def test_construction_from_settings(self):
        """Patch settings, verify _credentials_from_settings pulls."""
        with (
            patch.object(GoogleDriveConnector, "_build_services"),
            patch("src.connectors.google_drive.settings") as mock_settings,
        ):
            mock_settings.google_ads_client_id = "settings-id"
            mock_settings.google_ads_client_secret = "settings-secret"
            mock_settings.google_drive_refresh_token = "settings-token"

            c = GoogleDriveConnector()  # no credentials arg

        assert c._client_id == "settings-id"
        assert c._client_secret == "settings-secret"
        assert c._refresh_token == "settings-token"

    def test_construction_missing_refresh_token(self):
        """No refresh token raises GoogleDriveAuthError."""
        with pytest.raises(
            GoogleDriveAuthError,
            match="refresh token is not configured",
        ):
            GoogleDriveConnector(
                credentials={
                    "client_id": "id",
                    "client_secret": "secret",
                    "refresh_token": "",
                }
            )


# ===========================================================================
# 2. list_files
# ===========================================================================


class TestListFiles:
    def test_list_files_basic(self, connector):
        """Returns list of formatted files."""
        raw_files = [
            {
                "id": "f1",
                "name": "Report.docx",
                "mimeType": "application/vnd.google-apps.document",
                "modifiedTime": "2024-01-15T10:00:00Z",
                "size": "1024",
                "webViewLink": "https://docs.google.com/f1",
                "parents": ["root"],
            }
        ]
        (connector._drive.files().list().execute.return_value) = {"files": raw_files}

        result = connector.list_files()

        assert len(result) == 1
        assert result[0]["id"] == "f1"
        assert result[0]["name"] == "Report.docx"
        assert result[0]["mime_type"] == ("application/vnd.google-apps.document")
        assert result[0]["web_view_link"] == ("https://docs.google.com/f1")

    def test_list_files_with_folder_id(self, connector):
        """folder_id is included in query."""
        (connector._drive.files().list().execute.return_value) = {"files": []}

        connector.list_files(folder_id="folder_abc")

        call_kwargs = connector._drive.files().list.call_args
        query_str = call_kwargs[1]["q"]
        assert "'folder_abc' in parents" in query_str

    def test_list_files_with_query(self, connector):
        """Custom query is appended."""
        (connector._drive.files().list().execute.return_value) = {"files": []}

        connector.list_files(query="name contains 'report'")

        call_kwargs = connector._drive.files().list.call_args
        query_str = call_kwargs[1]["q"]
        assert "name contains 'report'" in query_str
        assert "trashed = false" in query_str

    def test_list_files_empty(self, connector):
        """Returns empty list when no files."""
        (connector._drive.files().list().execute.return_value) = {"files": []}

        result = connector.list_files()

        assert result == []

    def test_list_files_http_error(self, connector):
        """HttpError is caught, returns empty list."""
        connector._drive.files().list().execute.side_effect = _make_http_error(500, "Server Error")

        result = connector.list_files()

        assert result == []


# ===========================================================================
# 3. get_file_metadata
# ===========================================================================


class TestGetFileMetadata:
    def test_get_file_metadata_success(self, connector):
        """Returns normalized metadata."""
        raw = {
            "id": "file_1",
            "name": "Analysis.xlsx",
            "mimeType": "application/vnd.google-apps.spreadsheet",
            "modifiedTime": "2024-01-15T12:00:00Z",
            "size": "2048",
            "webViewLink": "https://sheets.google.com/file_1",
            "owners": [{"displayName": "Test User"}],
            "shared": True,
            "parents": ["parent_folder"],
        }
        (connector._drive.files().get().execute.return_value) = raw

        result = connector.get_file_metadata("file_1")

        assert result is not None
        assert result["id"] == "file_1"
        assert result["name"] == "Analysis.xlsx"
        assert result["shared"] is True
        assert result["parents"] == ["parent_folder"]

    def test_get_file_metadata_not_found(self, connector):
        """HttpError returns None."""
        connector._drive.files().get().execute.side_effect = _make_http_error(404, "Not Found")

        result = connector.get_file_metadata("missing_id")

        assert result is None

    def test_get_file_metadata_auth_error(self, connector):
        """401 HttpError raises GoogleDriveAuthError."""
        connector._drive.files().get().execute.side_effect = _make_http_error(401, "Unauthorized")

        with pytest.raises(GoogleDriveAuthError):
            connector.get_file_metadata("file_1")


# ===========================================================================
# 4. create_folder
# ===========================================================================


class TestCreateFolder:
    def test_create_folder_basic(self, connector):
        """Creates folder with name only."""
        (connector._drive.files().create().execute.return_value) = {
            "id": "folder_1",
            "name": "Reports",
            "webViewLink": "https://drive.google.com/folder_1",
        }

        result = connector.create_folder("Reports")

        assert result is not None
        assert result["id"] == "folder_1"
        assert result["name"] == "Reports"

    def test_create_folder_with_parent(self, connector):
        """Creates folder with parent_id in body."""
        (connector._drive.files().create().execute.return_value) = {
            "id": "folder_2",
            "name": "Sub",
            "webViewLink": "https://drive.google.com/folder_2",
        }

        connector.create_folder("Sub", parent_id="parent_1")

        call_kwargs = connector._drive.files().create.call_args
        body = call_kwargs[1]["body"]
        assert body["parents"] == ["parent_1"]
        assert body["name"] == "Sub"
        assert body["mimeType"] == "application/vnd.google-apps.folder"

    def test_create_folder_http_error(self, connector):
        """Returns None on error."""
        (connector._drive.files().create().execute.side_effect) = _make_http_error(
            500, "Internal Error"
        )

        result = connector.create_folder("Bad Folder")

        assert result is None


# ===========================================================================
# 5. move_file
# ===========================================================================


class TestMoveFile:
    def test_move_file_success(self, connector):
        """Moves file, removes old parent."""
        # Mock get to return current parents
        (connector._drive.files().get().execute.return_value) = {"parents": ["old_parent"]}

        # Mock update to return new state
        (connector._drive.files().update().execute.return_value) = {
            "id": "file_1",
            "name": "Doc.pdf",
            "parents": ["new_parent"],
        }

        result = connector.move_file("file_1", "new_parent")

        assert result is not None
        assert result["id"] == "file_1"
        assert result["parents"] == ["new_parent"]

    def test_move_file_no_current_parents(self, connector):
        """Handles empty parents."""
        (connector._drive.files().get().execute.return_value) = {"parents": []}

        (connector._drive.files().update().execute.return_value) = {
            "id": "file_1",
            "name": "Doc.pdf",
            "parents": ["new_parent"],
        }

        result = connector.move_file("file_1", "new_parent")

        assert result is not None
        assert result["parents"] == ["new_parent"]

        # Verify removeParents was empty string
        call_kwargs = connector._drive.files().update.call_args
        assert call_kwargs[1]["removeParents"] == ""

    def test_move_file_http_error(self, connector):
        """Returns None on error."""
        connector._drive.files().get().execute.side_effect = _make_http_error(500, "Server Error")

        result = connector.move_file("file_1", "new_parent")

        assert result is None


# ===========================================================================
# 6. get_shareable_link
# ===========================================================================


class TestGetShareableLink:
    def test_get_shareable_link_success(self, connector):
        """Creates permission and returns link."""
        (connector._drive.permissions().create().execute.return_value) = {"id": "perm_1"}

        (connector._drive.files().get().execute.return_value) = {
            "webViewLink": ("https://drive.google.com/file/d/abc/view")
        }

        result = connector.get_shareable_link("abc")

        assert result == "https://drive.google.com/file/d/abc/view"

    def test_get_shareable_link_auth_error(self, connector):
        """403 raises GoogleDriveAuthError."""
        (connector._drive.permissions().create().execute.side_effect) = _make_http_error(
            403, "Forbidden"
        )

        with pytest.raises(GoogleDriveAuthError):
            connector.get_shareable_link("abc")


# ===========================================================================
# 7. create_document
# ===========================================================================


class TestCreateDocument:
    def test_create_document_empty(self, connector):
        """Creates doc with no content."""
        (connector._docs.documents().create().execute.return_value) = {
            "documentId": "doc_1",
            "title": "Empty Doc",
        }

        (connector._drive.files().get().execute.return_value) = {
            "webViewLink": "https://docs.google.com/doc_1"
        }

        result = connector.create_document("Empty Doc")

        assert result is not None
        assert result["id"] == "doc_1"
        assert result["title"] == "Empty Doc"
        assert result["web_view_link"] == "https://docs.google.com/doc_1"
        # batchUpdate should NOT have been called
        connector._docs.documents().batchUpdate.assert_not_called()

    def test_create_document_with_content(self, connector):
        """Creates doc, then batch updates with text."""
        (connector._docs.documents().create().execute.return_value) = {
            "documentId": "doc_2",
            "title": "Content Doc",
        }

        (connector._docs.documents().batchUpdate().execute.return_value) = {}

        (connector._drive.files().get().execute.return_value) = {
            "webViewLink": "https://docs.google.com/doc_2"
        }

        result = connector.create_document("Content Doc", content="Hello world")

        assert result is not None
        assert result["id"] == "doc_2"
        # Verify batchUpdate was called
        connector._docs.documents().batchUpdate.assert_called()
        call_kwargs = connector._docs.documents().batchUpdate.call_args
        body = call_kwargs[1]["body"]
        requests = body["requests"]
        assert len(requests) == 1
        assert requests[0]["insertText"]["text"] == ("Hello world")

    def test_create_document_http_error(self, connector):
        """Returns None on error."""
        (connector._docs.documents().create().execute.side_effect) = _make_http_error(
            500, "Server Error"
        )

        result = connector.create_document("Fail Doc")

        assert result is None


# ===========================================================================
# 8. read_document
# ===========================================================================


class TestReadDocument:
    def test_read_document_success(self, connector):
        """Returns id, title, extracted text."""
        doc_response = {
            "documentId": "doc_1",
            "title": "My Document",
            "body": {
                "content": [
                    {"paragraph": {"elements": [{"textRun": {"content": ("Hello world\n")}}]}},
                    {"paragraph": {"elements": [{"textRun": {"content": ("Second line\n")}}]}},
                ]
            },
        }
        (connector._docs.documents().get().execute.return_value) = doc_response

        result = connector.read_document("doc_1")

        assert result is not None
        assert result["id"] == "doc_1"
        assert result["title"] == "My Document"
        assert "Hello world" in result["content"]
        assert "Second line" in result["content"]

    def test_read_document_with_table(self, connector):
        """Extracts text from table elements."""
        doc_response = {
            "documentId": "doc_2",
            "title": "Table Doc",
            "body": {
                "content": [
                    {
                        "table": {
                            "tableRows": [
                                {
                                    "tableCells": [
                                        {
                                            "content": [
                                                {
                                                    "paragraph": {
                                                        "elements": [
                                                            {"textRun": {"content": "Cell A1"}}
                                                        ]
                                                    }
                                                }
                                            ]
                                        },
                                        {
                                            "content": [
                                                {
                                                    "paragraph": {
                                                        "elements": [
                                                            {"textRun": {"content": "Cell B1"}}
                                                        ]
                                                    }
                                                }
                                            ]
                                        },
                                    ]
                                }
                            ]
                        }
                    }
                ]
            },
        }
        (connector._docs.documents().get().execute.return_value) = doc_response

        result = connector.read_document("doc_2")

        assert result is not None
        assert "Cell A1" in result["content"]
        assert "Cell B1" in result["content"]

    def test_read_document_empty(self, connector):
        """Returns empty content string."""
        doc_response = {
            "documentId": "doc_3",
            "title": "Empty Doc",
            "body": {"content": []},
        }
        (connector._docs.documents().get().execute.return_value) = doc_response

        result = connector.read_document("doc_3")

        assert result is not None
        assert result["content"] == ""

    def test_read_document_auth_error(self, connector):
        """401 raises GoogleDriveAuthError."""
        (connector._docs.documents().get().execute.side_effect) = _make_http_error(
            401, "Unauthorized"
        )

        with pytest.raises(GoogleDriveAuthError):
            connector.read_document("doc_1")


# ===========================================================================
# 9. append_to_document
# ===========================================================================


class TestAppendToDocument:
    def test_append_to_document_success(self, connector):
        """Gets doc, inserts at endIndex - 1."""
        doc_response = {
            "body": {
                "content": [
                    {"endIndex": 50},
                ]
            }
        }
        (connector._docs.documents().get().execute.return_value) = doc_response

        (connector._docs.documents().batchUpdate().execute.return_value) = {}

        result = connector.append_to_document("doc_1", "Appended text")

        assert result is True
        call_kwargs = connector._docs.documents().batchUpdate.call_args
        body = call_kwargs[1]["body"]
        insert_req = body["requests"][0]["insertText"]
        assert insert_req["location"]["index"] == 49
        assert "\nAppended text" in insert_req["text"]

    def test_append_to_document_empty_doc(self, connector):
        """Handles empty doc body."""
        doc_response = {"body": {"content": []}}
        (connector._docs.documents().get().execute.return_value) = doc_response

        (connector._docs.documents().batchUpdate().execute.return_value) = {}

        result = connector.append_to_document("doc_1", "First content")

        assert result is True
        # When body_content is empty, end_index stays 1
        call_kwargs = connector._docs.documents().batchUpdate.call_args
        body = call_kwargs[1]["body"]
        insert_req = body["requests"][0]["insertText"]
        assert insert_req["location"]["index"] == 1

    def test_append_to_document_http_error(self, connector):
        """Returns False on error."""
        (connector._docs.documents().get().execute.side_effect) = _make_http_error(
            500, "Server Error"
        )

        result = connector.append_to_document("doc_1", "Some text")

        assert result is False


# ===========================================================================
# 10. create_spreadsheet
# ===========================================================================


class TestCreateSpreadsheet:
    def test_create_spreadsheet_basic(self, connector):
        """Creates sheet with no data."""
        (connector._sheets.spreadsheets().create().execute.return_value) = {
            "spreadsheetId": "sheet_1",
            "properties": {"title": "Budget"},
        }

        (connector._drive.files().get().execute.return_value) = {
            "webViewLink": ("https://sheets.google.com/sheet_1")
        }

        result = connector.create_spreadsheet("Budget")

        assert result is not None
        assert result["id"] == "sheet_1"
        assert result["title"] == "Budget"
        # values().update should NOT have been called
        (connector._sheets.spreadsheets().values().update.assert_not_called())

    def test_create_spreadsheet_with_data(self, connector):
        """Creates sheet then writes initial values."""
        (connector._sheets.spreadsheets().create().execute.return_value) = {
            "spreadsheetId": "sheet_2",
            "properties": {"title": "Data Sheet"},
        }

        (connector._sheets.spreadsheets().values().update().execute.return_value) = {}

        (connector._drive.files().get().execute.return_value) = {
            "webViewLink": ("https://sheets.google.com/sheet_2")
        }

        data = [["Name", "Value"], ["A", 1], ["B", 2]]
        result = connector.create_spreadsheet("Data Sheet", sheet_data=data)

        assert result is not None
        assert result["id"] == "sheet_2"
        # Verify values().update was called
        (connector._sheets.spreadsheets().values().update.assert_called())
        call_kwargs = connector._sheets.spreadsheets().values().update.call_args
        assert call_kwargs[1]["valueInputOption"] == ("USER_ENTERED")
        assert call_kwargs[1]["body"]["values"] == data

    def test_create_spreadsheet_http_error(self, connector):
        """Returns None on error."""
        (connector._sheets.spreadsheets().create().execute.side_effect) = _make_http_error(
            500, "Internal Error"
        )

        result = connector.create_spreadsheet("Fail Sheet")

        assert result is None


# ===========================================================================
# 11. read_spreadsheet
# ===========================================================================


class TestReadSpreadsheet:
    def test_read_spreadsheet_success(self, connector):
        """Returns values and range."""
        (connector._sheets.spreadsheets().values().get().execute.return_value) = {
            "range": "Sheet1!A1:C3",
            "values": [
                ["Name", "Value"],
                ["A", "1"],
                ["B", "2"],
            ],
        }

        result = connector.read_spreadsheet("sheet_1")

        assert result is not None
        assert result["id"] == "sheet_1"
        assert result["range"] == "Sheet1!A1:C3"
        assert len(result["values"]) == 3

    def test_read_spreadsheet_empty(self, connector):
        """Returns empty values list."""
        (connector._sheets.spreadsheets().values().get().execute.return_value) = {"range": "Sheet1"}

        result = connector.read_spreadsheet("sheet_1")

        assert result is not None
        assert result["values"] == []

    def test_read_spreadsheet_custom_range(self, connector):
        """Passes custom range."""
        (connector._sheets.spreadsheets().values().get().execute.return_value) = {
            "range": "Sheet2!B2:D10",
            "values": [["x", "y", "z"]],
        }

        result = connector.read_spreadsheet("sheet_1", range="Sheet2!B2:D10")

        assert result is not None
        call_kwargs = connector._sheets.spreadsheets().values().get.call_args
        assert call_kwargs[1]["range"] == "Sheet2!B2:D10"


# ===========================================================================
# 12. write_to_spreadsheet
# ===========================================================================


class TestWriteToSpreadsheet:
    def test_write_to_spreadsheet_success(self, connector):
        """Writes values with USER_ENTERED."""
        (connector._sheets.spreadsheets().values().update().execute.return_value) = {
            "updatedRange": "Sheet1!A1:B3",
            "updatedRows": 3,
            "updatedColumns": 2,
            "updatedCells": 6,
        }

        values = [["a", "b"], ["c", "d"], ["e", "f"]]
        result = connector.write_to_spreadsheet("sheet_1", "Sheet1!A1:B3", values)

        assert result is not None
        assert result["updated_range"] == "Sheet1!A1:B3"
        assert result["updated_rows"] == 3
        assert result["updated_columns"] == 2
        assert result["updated_cells"] == 6

        call_kwargs = connector._sheets.spreadsheets().values().update.call_args
        assert call_kwargs[1]["valueInputOption"] == ("USER_ENTERED")

    def test_write_to_spreadsheet_http_error(self, connector):
        """Returns None on error."""
        (connector._sheets.spreadsheets().values().update().execute.side_effect) = _make_http_error(
            500, "Internal Error"
        )

        result = connector.write_to_spreadsheet("sheet_1", "Sheet1!A1", [["x"]])

        assert result is None


# ===========================================================================
# 13. create_presentation
# ===========================================================================


class TestCreatePresentation:
    def test_create_presentation_success(self, connector):
        """Creates presentation, returns id + title + link."""
        (connector._slides.presentations().create().execute.return_value) = {
            "presentationId": "pres_1",
            "title": "Q1 Review",
        }

        (connector._drive.files().get().execute.return_value) = {
            "webViewLink": ("https://slides.google.com/pres_1")
        }

        result = connector.create_presentation("Q1 Review")

        assert result is not None
        assert result["id"] == "pres_1"
        assert result["title"] == "Q1 Review"
        assert result["web_view_link"] == "https://slides.google.com/pres_1"

    def test_create_presentation_http_error(self, connector):
        """Returns None on error."""
        (connector._slides.presentations().create().execute.side_effect) = _make_http_error(
            500, "Internal Error"
        )

        result = connector.create_presentation("Fail Pres")

        assert result is None


# ===========================================================================
# 14. add_slide
# ===========================================================================


class TestAddSlide:
    def test_add_slide_blank(self, connector):
        """Adds blank slide only."""
        (connector._slides.presentations().batchUpdate().execute.return_value) = {}

        result = connector.add_slide("pres_1")

        assert result is not None
        assert result["presentation_id"] == "pres_1"
        assert result["slide_id"].startswith("slide_")

        # Verify only 1 request (createSlide, no textbox)
        call_kwargs = connector._slides.presentations().batchUpdate.call_args
        body = call_kwargs[1]["body"]
        assert len(body["requests"]) == 1
        assert "createSlide" in body["requests"][0]

    def test_add_slide_with_content(self, connector):
        """Adds slide + text box with content."""
        (connector._slides.presentations().batchUpdate().execute.return_value) = {}

        result = connector.add_slide("pres_1", content="Slide text here")

        assert result is not None
        assert result["presentation_id"] == "pres_1"

        # Verify 3 requests: createSlide + createShape + insertText
        call_kwargs = connector._slides.presentations().batchUpdate.call_args
        body = call_kwargs[1]["body"]
        assert len(body["requests"]) == 3
        assert "createSlide" in body["requests"][0]
        assert "createShape" in body["requests"][1]
        assert "insertText" in body["requests"][2]
        assert body["requests"][2]["insertText"]["text"] == "Slide text here"

    def test_add_slide_http_error(self, connector):
        """Returns None on error."""
        (connector._slides.presentations().batchUpdate().execute.side_effect) = _make_http_error(
            500, "Internal Error"
        )

        result = connector.add_slide("pres_1")

        assert result is None


# ===========================================================================
# Teardown: restore original @cached decorator
# ===========================================================================


def teardown_module():
    """Restore the original @cached decorator after all tests run."""
    src.cache.decorators.cached = _original_cached
