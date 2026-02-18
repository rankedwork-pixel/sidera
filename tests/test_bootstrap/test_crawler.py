"""Tests for the bootstrap Google Drive crawler."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from src.bootstrap.crawler import (
    MAX_DOC_CHARS,
    _read_file_content,
    _read_pdf_content,
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

    async def test_crawl_includes_presentations(self):
        """Google Slides presentations should be crawled."""
        mock_drive = MagicMock()
        mock_drive.list_files.return_value = [
            {
                "id": "pres1",
                "name": "Strategy Deck",
                "mimeType": "application/vnd.google-apps.presentation",
            },
        ]
        mock_drive.export_presentation_text.return_value = "--- Slide 1 ---\nTitle"

        docs = await crawl_folder("folder_id", connector=mock_drive)
        assert len(docs) == 1
        assert docs[0].title == "Strategy Deck"
        assert "Slide 1" in docs[0].content

    async def test_crawl_includes_pdfs(self):
        """PDFs should be crawled when pdfplumber is available."""
        mock_drive = MagicMock()
        mock_drive.list_files.return_value = [
            {
                "id": "pdf1",
                "name": "Org Chart.pdf",
                "mimeType": "application/pdf",
            },
        ]
        mock_drive.download_file_bytes.return_value = b"fake pdf bytes"

        # Mock _read_pdf_content since it uses pdfplumber internals
        with patch(
            "src.bootstrap.crawler._read_pdf_content",
            return_value="Org chart content",
        ):
            docs = await crawl_folder("folder_id", connector=mock_drive)
            assert len(docs) == 1
            assert docs[0].title == "Org Chart.pdf"
            assert "Org chart content" in docs[0].content


class TestReadFileContentSlides:
    def test_read_presentation(self):
        mock_drive = MagicMock()
        mock_drive.export_presentation_text.return_value = "Slide text"

        result = _read_file_content(
            mock_drive, "pres1", "application/vnd.google-apps.presentation"
        )
        assert result == "Slide text"
        mock_drive.export_presentation_text.assert_called_once_with("pres1")

    def test_read_presentation_empty(self):
        mock_drive = MagicMock()
        mock_drive.export_presentation_text.return_value = None

        result = _read_file_content(
            mock_drive, "pres1", "application/vnd.google-apps.presentation"
        )
        assert result == ""

    def test_read_presentation_error(self):
        mock_drive = MagicMock()
        mock_drive.export_presentation_text.side_effect = Exception("API error")

        result = _read_file_content(
            mock_drive, "pres1", "application/vnd.google-apps.presentation"
        )
        assert result == ""


def _mock_pdfplumber_module(mock_pdf_obj):
    """Create a mock pdfplumber module and inject it into sys.modules."""
    mock_module = MagicMock()
    mock_module.open.return_value = mock_pdf_obj
    return mock_module


def _make_mock_pdf(*pages):
    """Create a mock PDF object with context manager support."""
    mock_pdf = MagicMock()
    mock_pdf.pages = list(pages)
    mock_pdf.__enter__ = lambda self: self
    mock_pdf.__exit__ = MagicMock(return_value=False)
    return mock_pdf


class TestReadPdfContent:
    def test_read_pdf_success(self):
        mock_drive = MagicMock()
        mock_drive.download_file_bytes.return_value = b"fake pdf"

        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Hello from PDF"
        mock_page.extract_tables.return_value = []

        mock_pdf = _make_mock_pdf(mock_page)
        mock_pdfplumber = _mock_pdfplumber_module(mock_pdf)

        with patch.dict(sys.modules, {"pdfplumber": mock_pdfplumber}):
            result = _read_pdf_content(mock_drive, "file1")
            assert "Hello from PDF" in result
            assert "Page 1" in result

    def test_read_pdf_with_tables(self):
        mock_drive = MagicMock()
        mock_drive.download_file_bytes.return_value = b"fake pdf"

        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Text"
        mock_page.extract_tables.return_value = [
            [["Name", "Age"], ["Alice", "30"]]
        ]

        mock_pdf = _make_mock_pdf(mock_page)
        mock_pdfplumber = _mock_pdfplumber_module(mock_pdf)

        with patch.dict(sys.modules, {"pdfplumber": mock_pdfplumber}):
            result = _read_pdf_content(mock_drive, "file1")
            assert "Name | Age" in result
            assert "Alice | 30" in result

    def test_read_pdf_no_bytes(self):
        mock_drive = MagicMock()
        mock_drive.download_file_bytes.return_value = None

        # Need pdfplumber mock for the import to succeed
        mock_pdfplumber = MagicMock()
        with patch.dict(sys.modules, {"pdfplumber": mock_pdfplumber}):
            result = _read_pdf_content(mock_drive, "file1")
            assert result == ""

    def test_read_pdf_pdfplumber_not_installed(self):
        """Should return empty string when pdfplumber is not available."""
        mock_drive = MagicMock()

        # Remove pdfplumber from sys.modules and make import fail
        saved = sys.modules.pop("pdfplumber", None)
        try:
            if isinstance(__builtins__, dict):
                original_import = __builtins__["__import__"]
            else:
                original_import = __builtins__.__import__

            def mock_import(name, *args, **kwargs):
                if name == "pdfplumber":
                    raise ImportError("No module named 'pdfplumber'")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                result = _read_pdf_content(mock_drive, "file1")
                assert result == ""
        finally:
            if saved is not None:
                sys.modules["pdfplumber"] = saved

    def test_read_pdf_multiple_pages(self):
        mock_drive = MagicMock()
        mock_drive.download_file_bytes.return_value = b"fake pdf"

        mock_page1 = MagicMock()
        mock_page1.extract_text.return_value = "Page one text"
        mock_page1.extract_tables.return_value = []

        mock_page2 = MagicMock()
        mock_page2.extract_text.return_value = "Page two text"
        mock_page2.extract_tables.return_value = []

        mock_pdf = _make_mock_pdf(mock_page1, mock_page2)
        mock_pdfplumber = _mock_pdfplumber_module(mock_pdf)

        with patch.dict(sys.modules, {"pdfplumber": mock_pdfplumber}):
            result = _read_pdf_content(mock_drive, "file1")
            assert "Page 1" in result
            assert "Page 2" in result
            assert "Page one text" in result
            assert "Page two text" in result
