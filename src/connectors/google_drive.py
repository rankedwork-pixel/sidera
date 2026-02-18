"""Google Drive / Docs / Sheets / Slides connector for Sidera.

Connector that manages files in Google Drive and creates/reads/writes
Google Docs, Sheets, and Slides on behalf of the authenticated user.
Used by the agent to generate reports, store analysis outputs, and
share deliverables with stakeholders.

Architecture:
    connector (this file) -> MCP tools -> agent loop
    Each method calls the appropriate Google Workspace API (Drive v3,
    Docs v1, Sheets v4, or Slides v1), handles errors uniformly,
    and returns clean Python data structures.

Usage:
    from src.connectors.google_drive import GoogleDriveConnector

    connector = GoogleDriveConnector()  # uses settings singleton
    files = connector.list_files(query="name contains 'report'")
    doc = connector.create_document("Weekly Report", content="...")
"""

from __future__ import annotations

from typing import Any

import structlog
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.cache.decorators import cached
from src.cache.service import (
    CACHE_TTL_DRIVE_CONTENT,
    CACHE_TTL_DRIVE_LIST,
    CACHE_TTL_DRIVE_METADATA,
)
from src.config import settings
from src.connectors.retry import retry_with_backoff
from src.middleware.sentry_setup import capture_exception
from src.utils.encryption import decrypt_token

logger = structlog.get_logger(__name__)


class GoogleDriveConnectorError(Exception):
    """Base exception for Google Drive connector errors."""

    pass


class GoogleDriveAuthError(GoogleDriveConnectorError):
    """Authentication or authorization failure -- must be surfaced to user."""

    pass


class GoogleDriveConnector:
    """Client for Google Drive, Docs, Sheets, and Slides APIs.

    Wraps the google-api-python-client library and exposes clean,
    dict-based methods for managing Drive files and creating/reading
    Google Docs, Sheets, and Slides.

    Uses user OAuth credentials (NOT a service account). Reuses the
    same ``client_id`` and ``client_secret`` as Google Ads, with a
    separate ``refresh_token`` scoped to Drive/Docs/Sheets/Slides.

    Args:
        credentials: Optional dict of credentials. If omitted, values
            are read from the ``settings`` singleton (env vars / .env).
            Expected keys: ``client_id``, ``client_secret``,
            ``refresh_token``.
    """

    def __init__(self, credentials: dict[str, str] | None = None) -> None:
        self._credentials = credentials or self._credentials_from_settings()
        self._client_id = self._credentials.get("client_id", "")
        self._client_secret = self._credentials.get("client_secret", "")
        self._refresh_token = self._credentials.get("refresh_token", "")
        self._build_services()
        self._log = logger.bind(connector="google_drive")

    # ------------------------------------------------------------------
    # Public methods — Drive
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    @cached(
        ttl_seconds=CACHE_TTL_DRIVE_LIST,
        key_prefix="google_drive:list_files",
    )
    def list_files(
        self,
        folder_id: str | None = None,
        query: str | None = None,
        max_results: int = 50,
    ) -> list[dict[str, Any]]:
        """Search or list files in Google Drive.

        Builds a query string from the optional ``folder_id`` and
        ``query`` parameters. If neither is provided, lists the most
        recently modified files the user has access to.

        Args:
            folder_id: Optional parent folder ID to scope the listing.
            query: Optional Drive search query (e.g.
                ``"name contains 'report'"``).
            max_results: Maximum number of files to return (default 50).

        Returns:
            List of file dicts with keys: ``id``, ``name``,
            ``mime_type``, ``modified_time``, ``size``,
            ``web_view_link``, ``parents``.
        """
        query_parts: list[str] = ["trashed = false"]
        if folder_id:
            query_parts.append(f"'{folder_id}' in parents")
        if query:
            query_parts.append(query)

        query_str = " and ".join(query_parts)
        self._log.info(
            "google_drive.list_files",
            folder_id=folder_id,
            query=query,
            max_results=max_results,
        )

        try:
            response = (
                self._drive.files()
                .list(
                    q=query_str,
                    fields=("files(id,name,mimeType,modifiedTime,size,webViewLink,parents)"),
                    pageSize=min(max_results, 1000),
                    orderBy="modifiedTime desc",
                )
                .execute()
            )
            files = response.get("files", [])
            self._log.info(
                "google_drive.list_files.done",
                num_results=len(files),
            )
            return [self._format_file(f) for f in files]

        except HttpError as exc:
            self._handle_http_error(exc, "list_files")
            return []

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    @cached(
        ttl_seconds=CACHE_TTL_DRIVE_METADATA,
        key_prefix="google_drive:get_file_metadata",
    )
    def get_file_metadata(self, file_id: str) -> dict[str, Any] | None:
        """Fetch detailed metadata for a single file.

        Retrieves all available fields including sharing information,
        owners, and direct links.

        Args:
            file_id: The Google Drive file ID.

        Returns:
            Dict with file metadata (``id``, ``name``, ``mime_type``,
            ``modified_time``, ``size``, ``web_view_link``, ``owners``,
            ``shared``, ``parents``), or ``None`` on failure.
        """
        self._log.info("google_drive.get_file_metadata", file_id=file_id)

        try:
            result = self._drive.files().get(fileId=file_id, fields="*").execute()
            metadata = {
                "id": result.get("id", ""),
                "name": result.get("name", ""),
                "mime_type": result.get("mimeType", ""),
                "modified_time": result.get("modifiedTime", ""),
                "size": result.get("size", ""),
                "web_view_link": result.get("webViewLink", ""),
                "owners": result.get("owners", []),
                "shared": result.get("shared", False),
                "parents": result.get("parents", []),
            }
            self._log.info(
                "google_drive.get_file_metadata.done",
                file_id=file_id,
                name=metadata["name"],
            )
            return metadata

        except HttpError as exc:
            self._handle_http_error(exc, "get_file_metadata", file_id=file_id)
            return None

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def create_folder(
        self,
        name: str,
        parent_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Create a new folder in Google Drive.

        Args:
            name: The folder name.
            parent_id: Optional parent folder ID. If omitted, the
                folder is created in the user's Drive root.

        Returns:
            Dict with ``id``, ``name``, ``web_view_link`` of the
            created folder, or ``None`` on failure.
        """
        self._log.info(
            "google_drive.create_folder",
            name=name,
            parent_id=parent_id,
        )

        body: dict[str, Any] = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            body["parents"] = [parent_id]

        try:
            result = (
                self._drive.files()
                .create(
                    body=body,
                    fields="id,name,webViewLink",
                )
                .execute()
            )
            folder = {
                "id": result.get("id", ""),
                "name": result.get("name", ""),
                "web_view_link": result.get("webViewLink", ""),
            }
            self._log.info(
                "google_drive.create_folder.done",
                folder_id=folder["id"],
            )
            return folder

        except HttpError as exc:
            self._handle_http_error(exc, "create_folder")
            return None

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def move_file(
        self,
        file_id: str,
        new_parent_id: str,
    ) -> dict[str, Any] | None:
        """Move a file to a different folder.

        Removes the file from its current parent(s) and adds it to
        ``new_parent_id``.

        Args:
            file_id: The file ID to move.
            new_parent_id: The destination folder ID.

        Returns:
            Dict with updated ``id``, ``name``, ``parents``, or
            ``None`` on failure.
        """
        self._log.info(
            "google_drive.move_file",
            file_id=file_id,
            new_parent_id=new_parent_id,
        )

        try:
            # Get current parents to remove
            file_meta = self._drive.files().get(fileId=file_id, fields="parents").execute()
            current_parents = ",".join(file_meta.get("parents", []))

            result = (
                self._drive.files()
                .update(
                    fileId=file_id,
                    addParents=new_parent_id,
                    removeParents=current_parents,
                    fields="id,name,parents",
                )
                .execute()
            )
            moved = {
                "id": result.get("id", ""),
                "name": result.get("name", ""),
                "parents": result.get("parents", []),
            }
            self._log.info(
                "google_drive.move_file.done",
                file_id=file_id,
                new_parent_id=new_parent_id,
            )
            return moved

        except HttpError as exc:
            self._handle_http_error(exc, "move_file", file_id=file_id)
            return None

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def get_shareable_link(self, file_id: str) -> str | None:
        """Get or create a shareable link for a file.

        Creates an ``anyone with the link can view`` permission if
        one does not already exist, then returns the ``webViewLink``.

        Args:
            file_id: The Google Drive file ID.

        Returns:
            The shareable web view link string, or ``None`` on failure.
        """
        self._log.info("google_drive.get_shareable_link", file_id=file_id)

        try:
            # Create anyone-can-view permission
            self._drive.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                fields="id",
            ).execute()

            # Fetch the updated webViewLink
            result = self._drive.files().get(fileId=file_id, fields="webViewLink").execute()
            link = result.get("webViewLink", "")
            self._log.info(
                "google_drive.get_shareable_link.done",
                file_id=file_id,
                link=link,
            )
            return link

        except HttpError as exc:
            self._handle_http_error(exc, "get_shareable_link", file_id=file_id)
            return None

    # ------------------------------------------------------------------
    # Public methods — Google Docs
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def create_document(
        self,
        title: str,
        content: str | None = None,
    ) -> dict[str, Any] | None:
        """Create a new Google Doc, optionally with initial text.

        Args:
            title: The document title.
            content: Optional plain text to insert into the document
                body. If omitted, the doc is created empty.

        Returns:
            Dict with ``id``, ``title``, ``web_view_link`` of the
            created document, or ``None`` on failure.
        """
        self._log.info(
            "google_drive.create_document",
            title=title,
            has_content=content is not None,
        )

        try:
            doc = self._docs.documents().create(body={"title": title}).execute()
            doc_id = doc.get("documentId", "")

            # Insert initial content if provided
            if content:
                self._docs.documents().batchUpdate(
                    documentId=doc_id,
                    body={
                        "requests": [
                            {
                                "insertText": {
                                    "location": {"index": 1},
                                    "text": content,
                                }
                            }
                        ]
                    },
                ).execute()

            # Fetch the webViewLink from Drive
            file_meta = self._drive.files().get(fileId=doc_id, fields="webViewLink").execute()

            result = {
                "id": doc_id,
                "title": doc.get("title", title),
                "web_view_link": file_meta.get("webViewLink", ""),
            }
            self._log.info(
                "google_drive.create_document.done",
                document_id=doc_id,
            )
            return result

        except HttpError as exc:
            self._handle_http_error(exc, "create_document")
            return None

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    @cached(
        ttl_seconds=CACHE_TTL_DRIVE_CONTENT,
        key_prefix="google_drive:read_document",
    )
    def read_document(self, document_id: str) -> dict[str, Any] | None:
        """Read the content of a Google Doc as plain text.

        Traverses the document body's structural elements and extracts
        all text content into a single string.

        Args:
            document_id: The Google Docs document ID.

        Returns:
            Dict with ``id``, ``title``, ``content`` (plain text),
            or ``None`` on failure.
        """
        self._log.info(
            "google_drive.read_document",
            document_id=document_id,
        )

        try:
            doc = self._docs.documents().get(documentId=document_id).execute()
            content = self._extract_doc_text(doc)

            result = {
                "id": doc.get("documentId", ""),
                "title": doc.get("title", ""),
                "content": content,
            }
            self._log.info(
                "google_drive.read_document.done",
                document_id=document_id,
                content_length=len(content),
            )
            return result

        except HttpError as exc:
            self._handle_http_error(exc, "read_document", document_id=document_id)
            return None

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def append_to_document(
        self,
        document_id: str,
        content: str,
    ) -> bool:
        """Append text to the end of a Google Doc.

        Reads the document to determine the current end index, then
        inserts the new content at ``endIndex - 1`` (the last valid
        insertion point before the trailing newline).

        Args:
            document_id: The Google Docs document ID.
            content: The plain text to append.

        Returns:
            ``True`` if the append succeeded, ``False`` on failure.
        """
        self._log.info(
            "google_drive.append_to_document",
            document_id=document_id,
            content_length=len(content),
        )

        try:
            # Get document to find the end index
            doc = self._docs.documents().get(documentId=document_id).execute()
            body = doc.get("body", {})
            body_content = body.get("content", [])

            # The last element's endIndex is the document length
            end_index = 1
            if body_content:
                last_element = body_content[-1]
                end_index = last_element.get("endIndex", 1) - 1

            # Ensure we prepend a newline for clean separation
            text_to_insert = f"\n{content}"

            self._docs.documents().batchUpdate(
                documentId=document_id,
                body={
                    "requests": [
                        {
                            "insertText": {
                                "location": {"index": max(end_index, 1)},
                                "text": text_to_insert,
                            }
                        }
                    ]
                },
            ).execute()

            self._log.info(
                "google_drive.append_to_document.done",
                document_id=document_id,
            )
            return True

        except HttpError as exc:
            self._handle_http_error(
                exc,
                "append_to_document",
                document_id=document_id,
            )
            return False

    # ------------------------------------------------------------------
    # Public methods — Google Sheets
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def create_spreadsheet(
        self,
        title: str,
        sheet_data: list[list[Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Create a new Google Sheet, optionally with initial data.

        Args:
            title: The spreadsheet title.
            sheet_data: Optional list of rows (each row is a list of
                cell values) to populate the first sheet.

        Returns:
            Dict with ``id``, ``title``, ``web_view_link`` of the
            created spreadsheet, or ``None`` on failure.
        """
        self._log.info(
            "google_drive.create_spreadsheet",
            title=title,
            has_data=sheet_data is not None,
        )

        try:
            spreadsheet = (
                self._sheets.spreadsheets().create(body={"properties": {"title": title}}).execute()
            )
            spreadsheet_id = spreadsheet.get("spreadsheetId", "")

            # Write initial data if provided
            if sheet_data:
                self._sheets.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range="Sheet1",
                    valueInputOption="USER_ENTERED",
                    body={"values": sheet_data},
                ).execute()

            # Fetch the webViewLink from Drive
            file_meta = (
                self._drive.files()
                .get(
                    fileId=spreadsheet_id,
                    fields="webViewLink",
                )
                .execute()
            )

            result = {
                "id": spreadsheet_id,
                "title": spreadsheet.get("properties", {}).get("title", title),
                "web_view_link": file_meta.get("webViewLink", ""),
            }
            self._log.info(
                "google_drive.create_spreadsheet.done",
                spreadsheet_id=spreadsheet_id,
            )
            return result

        except HttpError as exc:
            self._handle_http_error(exc, "create_spreadsheet")
            return None

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    @cached(
        ttl_seconds=CACHE_TTL_DRIVE_CONTENT,
        key_prefix="google_drive:read_spreadsheet",
    )
    def read_spreadsheet(
        self,
        spreadsheet_id: str,
        range: str = "Sheet1",
    ) -> dict[str, Any] | None:
        """Read cell values from a Google Sheet.

        Args:
            spreadsheet_id: The Google Sheets spreadsheet ID.
            range: The A1 notation range to read (default
                ``"Sheet1"`` reads the entire first sheet).

        Returns:
            Dict with ``id``, ``range``, ``values`` (list of row
            lists), or ``None`` on failure.
        """
        self._log.info(
            "google_drive.read_spreadsheet",
            spreadsheet_id=spreadsheet_id,
            range=range,
        )

        try:
            result = (
                self._sheets.spreadsheets()
                .values()
                .get(
                    spreadsheetId=spreadsheet_id,
                    range=range,
                )
                .execute()
            )
            values = result.get("values", [])
            data = {
                "id": spreadsheet_id,
                "range": result.get("range", range),
                "values": values,
            }
            self._log.info(
                "google_drive.read_spreadsheet.done",
                spreadsheet_id=spreadsheet_id,
                num_rows=len(values),
            )
            return data

        except HttpError as exc:
            self._handle_http_error(
                exc,
                "read_spreadsheet",
                spreadsheet_id=spreadsheet_id,
            )
            return None

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def write_to_spreadsheet(
        self,
        spreadsheet_id: str,
        range: str,
        values: list[list[Any]],
    ) -> dict[str, Any] | None:
        """Write data to cells in a Google Sheet.

        Overwrites the specified range with the provided values using
        ``USER_ENTERED`` value input option (so formulas and number
        formats are interpreted).

        Args:
            spreadsheet_id: The Google Sheets spreadsheet ID.
            range: The A1 notation range to write to (e.g.
                ``"Sheet1!A1:C10"``).
            values: List of rows, where each row is a list of cell
                values.

        Returns:
            Dict with ``updated_range``, ``updated_rows``,
            ``updated_columns``, ``updated_cells``, or ``None``
            on failure.
        """
        self._log.info(
            "google_drive.write_to_spreadsheet",
            spreadsheet_id=spreadsheet_id,
            range=range,
            num_rows=len(values),
        )

        try:
            result = (
                self._sheets.spreadsheets()
                .values()
                .update(
                    spreadsheetId=spreadsheet_id,
                    range=range,
                    valueInputOption="USER_ENTERED",
                    body={"values": values},
                )
                .execute()
            )
            update_info = {
                "updated_range": result.get("updatedRange", ""),
                "updated_rows": result.get("updatedRows", 0),
                "updated_columns": result.get("updatedColumns", 0),
                "updated_cells": result.get("updatedCells", 0),
            }
            self._log.info(
                "google_drive.write_to_spreadsheet.done",
                spreadsheet_id=spreadsheet_id,
                updated_cells=update_info["updated_cells"],
            )
            return update_info

        except HttpError as exc:
            self._handle_http_error(
                exc,
                "write_to_spreadsheet",
                spreadsheet_id=spreadsheet_id,
            )
            return None

    # ------------------------------------------------------------------
    # Public methods — Google Slides
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def create_presentation(self, title: str) -> dict[str, Any] | None:
        """Create a new Google Slides presentation.

        Args:
            title: The presentation title.

        Returns:
            Dict with ``id``, ``title``, ``web_view_link`` of the
            created presentation, or ``None`` on failure.
        """
        self._log.info("google_drive.create_presentation", title=title)

        try:
            presentation = self._slides.presentations().create(body={"title": title}).execute()
            presentation_id = presentation.get("presentationId", "")

            # Fetch the webViewLink from Drive
            file_meta = (
                self._drive.files()
                .get(
                    fileId=presentation_id,
                    fields="webViewLink",
                )
                .execute()
            )

            result = {
                "id": presentation_id,
                "title": presentation.get("title", title),
                "web_view_link": file_meta.get("webViewLink", ""),
            }
            self._log.info(
                "google_drive.create_presentation.done",
                presentation_id=presentation_id,
            )
            return result

        except HttpError as exc:
            self._handle_http_error(exc, "create_presentation")
            return None

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def add_slide(
        self,
        presentation_id: str,
        layout: str = "BLANK",
        content: str | None = None,
    ) -> dict[str, Any] | None:
        """Add a new slide to an existing presentation.

        Creates a slide with the specified layout. If ``content`` is
        provided, also inserts a text box with that content.

        Args:
            presentation_id: The Google Slides presentation ID.
            layout: Predefined layout name (default ``"BLANK"``).
                Common values: ``"BLANK"``, ``"TITLE"``,
                ``"TITLE_AND_BODY"``.
            content: Optional text to add in a text box on the slide.

        Returns:
            Dict with ``presentation_id``, ``slide_id``, or ``None``
            on failure.
        """
        self._log.info(
            "google_drive.add_slide",
            presentation_id=presentation_id,
            layout=layout,
            has_content=content is not None,
        )

        try:
            # Generate a unique object ID for the new slide
            import uuid

            slide_id = f"slide_{uuid.uuid4().hex[:12]}"

            requests: list[dict[str, Any]] = [
                {
                    "createSlide": {
                        "objectId": slide_id,
                        "slideLayoutReference": {
                            "predefinedLayout": layout,
                        },
                    }
                }
            ]

            # If content is provided, add a text box
            if content:
                textbox_id = f"textbox_{uuid.uuid4().hex[:12]}"
                requests.extend(
                    [
                        {
                            "createShape": {
                                "objectId": textbox_id,
                                "shapeType": "TEXT_BOX",
                                "elementProperties": {
                                    "pageObjectId": slide_id,
                                    "size": {
                                        "width": {
                                            "magnitude": 600,
                                            "unit": "PT",
                                        },
                                        "height": {
                                            "magnitude": 400,
                                            "unit": "PT",
                                        },
                                    },
                                    "transform": {
                                        "scaleX": 1,
                                        "scaleY": 1,
                                        "translateX": 50,
                                        "translateY": 50,
                                        "unit": "PT",
                                    },
                                },
                            }
                        },
                        {
                            "insertText": {
                                "objectId": textbox_id,
                                "text": content,
                                "insertionIndex": 0,
                            }
                        },
                    ]
                )

            self._slides.presentations().batchUpdate(
                presentationId=presentation_id,
                body={"requests": requests},
            ).execute()

            result = {
                "presentation_id": presentation_id,
                "slide_id": slide_id,
            }
            self._log.info(
                "google_drive.add_slide.done",
                presentation_id=presentation_id,
                slide_id=slide_id,
            )
            return result

        except HttpError as exc:
            self._handle_http_error(
                exc,
                "add_slide",
                presentation_id=presentation_id,
            )
            return None

    # ------------------------------------------------------------------
    # Public methods — Bootstrap helpers (PDF + Slides text extraction)
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def download_file_bytes(self, file_id: str) -> bytes | None:
        """Download raw bytes of an uploaded (non-native) file via Drive API.

        Useful for PDFs and other binary files that need client-side
        processing (e.g. text extraction via pdfplumber).

        Args:
            file_id: The Google Drive file ID.

        Returns:
            The file bytes, or ``None`` on failure.
        """
        import io

        from googleapiclient.http import MediaIoBaseDownload

        self._log.info("google_drive.download_file_bytes", file_id=file_id)

        try:
            request = self._drive.files().get_media(fileId=file_id)
            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)

            done = False
            while not done:
                _status, done = downloader.next_chunk()

            self._log.info(
                "google_drive.download_file_bytes.done",
                file_id=file_id,
                size=buffer.tell(),
            )
            return buffer.getvalue()

        except HttpError as exc:
            self._handle_http_error(exc, "download_file_bytes", file_id=file_id)
            return None

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def export_presentation_text(self, presentation_id: str) -> str | None:
        """Extract plain text from all slides in a Google Slides presentation.

        Walks through every slide's page elements, finds shapes with
        text content, and concatenates all text into a readable format
        with ``--- Slide N ---`` delimiters.

        Args:
            presentation_id: The Google Slides presentation ID.

        Returns:
            The combined text content of all slides, or ``None`` on failure.
        """
        self._log.info(
            "google_drive.export_presentation_text",
            presentation_id=presentation_id,
        )

        try:
            presentation = (
                self._slides.presentations()
                .get(presentationId=presentation_id)
                .execute()
            )

            slides = presentation.get("slides", [])
            text_parts: list[str] = []

            for slide_idx, slide in enumerate(slides, 1):
                slide_texts: list[str] = []

                for element in slide.get("pageElements", []):
                    shape = element.get("shape", {})
                    text_content = shape.get("text", {})
                    for text_element in text_content.get("textElements", []):
                        text_run = text_element.get("textRun", {})
                        content = text_run.get("content", "")
                        if content.strip():
                            slide_texts.append(content.strip())

                if slide_texts:
                    text_parts.append(f"--- Slide {slide_idx} ---")
                    text_parts.extend(slide_texts)

            result = "\n".join(text_parts)
            self._log.info(
                "google_drive.export_presentation_text.done",
                presentation_id=presentation_id,
                slides=len(slides),
                chars=len(result),
            )
            return result

        except HttpError as exc:
            self._handle_http_error(
                exc, "export_presentation_text",
                presentation_id=presentation_id,
            )
            return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _credentials_from_settings() -> dict[str, str]:
        """Build a credentials dict from the global settings singleton.

        Reuses ``google_ads_client_id`` and ``google_ads_client_secret``
        (same Google Cloud project) with a Drive-specific refresh token.
        """
        return {
            "client_id": settings.google_ads_client_id,
            "client_secret": settings.google_ads_client_secret,
            "refresh_token": decrypt_token(settings.google_drive_refresh_token),
        }

    def _build_services(self) -> None:
        """Create the four Google API service objects.

        Uses user OAuth credentials (refresh-token flow) to build
        Drive v3, Docs v1, Sheets v4, and Slides v1 service clients.

        Raises:
            GoogleDriveAuthError: If credentials are missing or the
                service objects cannot be created.
        """
        if not self._refresh_token:
            raise GoogleDriveAuthError(
                "Google Drive refresh token is not configured. "
                "Set google_drive_refresh_token in settings or "
                "provide it in the credentials dict."
            )

        try:
            creds = OAuthCredentials(
                token=None,
                refresh_token=self._refresh_token,
                client_id=self._client_id,
                client_secret=self._client_secret,
                token_uri=("https://oauth2.googleapis.com/token"),
            )

            self._drive = build("drive", "v3", credentials=creds)
            self._docs = build("docs", "v1", credentials=creds)
            self._sheets = build("sheets", "v4", credentials=creds)
            self._slides = build("slides", "v1", credentials=creds)

        except Exception as exc:
            raise GoogleDriveAuthError(f"Failed to build Google API services: {exc}") from exc

    @staticmethod
    def _format_file(raw: dict[str, Any]) -> dict[str, Any]:
        """Normalize a raw Drive file dict to a clean output format.

        Args:
            raw: A file resource dict from the Drive API.

        Returns:
            Normalized dict with snake_case keys.
        """
        return {
            "id": raw.get("id", ""),
            "name": raw.get("name", ""),
            "mime_type": raw.get("mimeType", ""),
            "modified_time": raw.get("modifiedTime", ""),
            "size": raw.get("size", ""),
            "web_view_link": raw.get("webViewLink", ""),
            "parents": raw.get("parents", []),
        }

    @staticmethod
    def _extract_doc_text(doc: dict[str, Any]) -> str:
        """Extract plain text from a Google Docs document body.

        Traverses the document's structural elements (paragraphs,
        tables, etc.) and concatenates all text runs into a single
        string.

        Args:
            doc: The full document resource from the Docs API.

        Returns:
            The document content as a plain text string.
        """
        body = doc.get("body", {})
        content_elements = body.get("content", [])
        text_parts: list[str] = []

        for element in content_elements:
            paragraph = element.get("paragraph")
            if paragraph:
                for text_element in paragraph.get("elements", []):
                    text_run = text_element.get("textRun")
                    if text_run:
                        text_parts.append(text_run.get("content", ""))

            # Handle table content
            table = element.get("table")
            if table:
                for row in table.get("tableRows", []):
                    for cell in row.get("tableCells", []):
                        for cell_element in cell.get("content", []):
                            cell_para = cell_element.get("paragraph")
                            if cell_para:
                                for te in cell_para.get("elements", []):
                                    tr = te.get("textRun")
                                    if tr:
                                        text_parts.append(
                                            tr.get(
                                                "content",
                                                "",
                                            )
                                        )

        return "".join(text_parts)

    def _handle_http_error(
        self,
        exc: HttpError,
        operation: str,
        **context: Any,
    ) -> None:
        """Inspect an HttpError and raise or log appropriately.

        Maps HTTP 401/403 status codes to ``GoogleDriveAuthError``
        (which callers must surface to users). Other errors are logged
        and swallowed so methods can return empty/None results.

        Args:
            exc: The caught ``HttpError``.
            operation: A label for the operation that failed.
            **context: Extra fields to include in the structured log.

        Raises:
            GoogleDriveAuthError: On 401 or 403 HTTP status codes.
        """
        capture_exception(exc)

        status_code = exc.resp.status if exc.resp else 0
        error_message = str(exc)

        self._log.error(
            "google_drive_api_error",
            operation=operation,
            status_code=status_code,
            error_message=error_message,
            **context,
        )

        if status_code in (401, 403):
            raise GoogleDriveAuthError(
                f"Google Drive auth error during {operation}: "
                f"{error_message} (status={status_code})"
            ) from exc
