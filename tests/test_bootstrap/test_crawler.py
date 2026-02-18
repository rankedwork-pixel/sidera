"""Tests for the bootstrap Google Drive crawler."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.bootstrap.crawler import (
    MAX_DOC_CHARS,
    _read_file_content,
    _sheet_to_text,
    _truncate_content,
    crawl_folder,
)


class TestTruncateContent:
    def test_short_content_unchanged(self):
        text = "Short text"
        assert _truncate_content(text) == text

    def test_long_content_truncated(self):
        text = "x" * (MAX_DOC_CHARS + 1000)
        result = _truncate_content(text)
        assert len(result) < len(text)
        assert "[... content truncated ...]" in result

    def test_truncation_preserves_ends(self):
        text = "START" + "x" * (MAX_DOC_CHARS + 1000) + "END"
        result = _truncate_content(text)
        assert result.startswith("START")
        assert result.endswith("END")


class TestSheetToText:
    def test_basic_rows(self):
        values = [["Name", "Age"], ["Alice", 30], ["Bob", 25]]
        result = _sheet_to_text(values)
        assert "Name | Age" in result
        assert "Alice | 30" in result

    def test_empty_cells(self):
        values = [["A", None, "C"]]
        result = _sheet_to_text(values)
        assert "A | C" in result

    def test_empty_rows_skipped(self):
        values = [["Header"], [None, None], ["Data"]]
        result = _sheet_to_text(values)
        lines = result.strip().split("\n")
        assert len(lines) == 2  # empty row skipped


class TestReadFileContent:
    def test_read_google_doc(self):
        mock_drive = MagicMock()
        mock_drive.read_document.return_value = {"content": "Hello world"}

        result = _read_file_content(
            mock_drive, "file123", "application/vnd.google-apps.document"
        )
        assert result == "Hello world"
        mock_drive.read_document.assert_called_once_with("file123")

    def test_read_spreadsheet(self):
        mock_drive = MagicMock()
        mock_drive.read_spreadsheet.return_value = {
            "values": [["A", "B"], ["1", "2"]]
        }

        result = _read_file_content(
            mock_drive, "file456", "application/vnd.google-apps.spreadsheet"
        )
        assert "A | B" in result

    def test_read_doc_returns_empty_on_error(self):
        mock_drive = MagicMock()
        mock_drive.read_document.side_effect = Exception("API error")

        result = _read_file_content(
            mock_drive, "file789", "application/vnd.google-apps.document"
        )
        assert result == ""

    def test_read_doc_returns_empty_when_no_content(self):
        mock_drive = MagicMock()
        mock_drive.read_document.return_value = {"content": ""}

        result = _read_file_content(
            mock_drive, "file000", "application/vnd.google-apps.document"
        )
        assert result == ""


@pytest.mark.asyncio
class TestCrawlFolder:
    async def test_crawl_basic(self):
        mock_drive = MagicMock()
        mock_drive.list_files.return_value = [
            {
                "id": "doc1",
                "name": "Handbook",
                "mimeType": "application/vnd.google-apps.document",
            },
            {
                "id": "sheet1",
                "name": "Goals",
                "mimeType": "application/vnd.google-apps.spreadsheet",
            },
        ]
        mock_drive.read_document.return_value = {"content": "Handbook content"}
        mock_drive.read_spreadsheet.return_value = {
            "values": [["Goal", "Target"]]
        }

        docs = await crawl_folder("folder_id", connector=mock_drive)
        assert len(docs) == 2
        assert docs[0].title == "Handbook"
        assert docs[1].title == "Goals"

    async def test_crawl_skips_binary_files(self):
        mock_drive = MagicMock()
        mock_drive.list_files.return_value = [
            {"id": "img1", "name": "photo.jpg", "mimeType": "image/jpeg"},
            {
                "id": "doc1",
                "name": "Notes",
                "mimeType": "application/vnd.google-apps.document",
            },
        ]
        mock_drive.read_document.return_value = {"content": "Notes content"}

        docs = await crawl_folder("folder_id", connector=mock_drive)
        assert len(docs) == 1
        assert docs[0].title == "Notes"

    async def test_crawl_respects_max_docs(self):
        mock_drive = MagicMock()
        files = [
            {
                "id": f"doc{i}",
                "name": f"Doc {i}",
                "mimeType": "application/vnd.google-apps.document",
            }
            for i in range(20)
        ]
        mock_drive.list_files.return_value = files
        mock_drive.read_document.return_value = {"content": "Content"}

        docs = await crawl_folder("folder_id", max_docs=5, connector=mock_drive)
        assert len(docs) == 5

    async def test_crawl_recurses_into_subfolders(self):
        mock_drive = MagicMock()

        # Root folder has a subfolder and a doc
        def list_side_effect(folder_id=None, max_results=200):
            if folder_id == "root":
                return [
                    {
                        "id": "subfolder",
                        "name": "Team Docs",
                        "mimeType": "application/vnd.google-apps.folder",
                    },
                    {
                        "id": "doc1",
                        "name": "Root Doc",
                        "mimeType": "application/vnd.google-apps.document",
                    },
                ]
            elif folder_id == "subfolder":
                return [
                    {
                        "id": "doc2",
                        "name": "Sub Doc",
                        "mimeType": "application/vnd.google-apps.document",
                    },
                ]
            return []

        mock_drive.list_files.side_effect = list_side_effect
        mock_drive.read_document.return_value = {"content": "Content"}

        docs = await crawl_folder("root", connector=mock_drive)
        assert len(docs) == 2
        titles = {d.title for d in docs}
        assert "Root Doc" in titles
        assert "Sub Doc" in titles

    async def test_crawl_empty_folder(self):
        mock_drive = MagicMock()
        mock_drive.list_files.return_value = []

        docs = await crawl_folder("empty_folder", connector=mock_drive)
        assert len(docs) == 0
