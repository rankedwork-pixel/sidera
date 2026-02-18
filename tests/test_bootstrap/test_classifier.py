"""Tests for the bootstrap document classifier."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.bootstrap.classifier import (
    _make_classified,
    _parse_classification_response,
    classify_documents,
)
from src.bootstrap.models import DocumentCategory, RawDocument


def _make_raw_doc(
    file_id: str = "doc1", title: str = "Test", content: str = "Content",
) -> RawDocument:
    return RawDocument(
        file_id=file_id,
        title=title,
        mime_type="application/vnd.google-apps.document",
        content=content,
        char_count=len(content),
    )


class TestParseClassificationResponse:
    def test_clean_json(self):
        text = json.dumps([
            {"file_id": "doc1", "categories": ["org_structure"], "confidence": 0.9}
        ])
        result = _parse_classification_response(text)
        assert len(result) == 1
        assert result[0]["file_id"] == "doc1"

    def test_with_markdown_fences(self):
        text = (
            '```json\n[{"file_id": "doc1", "categories":'
            ' ["sop_playbook"], "confidence": 0.8}]\n```'
        )
        result = _parse_classification_response(text)
        assert len(result) == 1

    def test_invalid_json(self):
        result = _parse_classification_response("not json at all")
        assert result == []

    def test_non_array_json(self):
        result = _parse_classification_response('{"key": "value"}')
        assert result == []


class TestMakeClassified:
    def test_basic(self):
        doc = _make_raw_doc()
        classified = _make_classified(doc, ["org_structure"], 0.9)
        assert classified.file_id == "doc1"
        assert classified.categories == ["org_structure"]
        assert classified.confidence == 0.9
        assert classified.is_relevant


@pytest.mark.asyncio
class TestClassifyDocuments:
    @patch("src.bootstrap.classifier.call_claude_api", new_callable=AsyncMock)
    async def test_classify_batch(self, mock_api):
        mock_api.return_value = {
            "text": json.dumps([
                {"file_id": "doc1", "categories": ["org_structure"], "confidence": 0.9},
                {"file_id": "doc2", "categories": ["irrelevant"], "confidence": 0.7},
            ]),
            "cost": {"total_cost_usd": 0.001},
        }

        docs = [
            _make_raw_doc("doc1", "Org Chart"),
            _make_raw_doc("doc2", "Lunch Menu"),
        ]
        result = await classify_documents(docs)
        assert len(result) == 2
        # Relevant should sort first
        assert result[0].file_id == "doc1"
        assert result[0].is_relevant
        assert not result[1].is_relevant

    @patch("src.bootstrap.classifier.call_claude_api", new_callable=AsyncMock)
    async def test_classify_handles_api_error(self, mock_api):
        mock_api.side_effect = Exception("API timeout")

        docs = [_make_raw_doc("doc1")]
        result = await classify_documents(docs)
        # Should return irrelevant classifications on error
        assert len(result) == 1
        assert not result[0].is_relevant

    @patch("src.bootstrap.classifier.call_claude_api", new_callable=AsyncMock)
    async def test_classify_batching(self, mock_api):
        # With batch_size=2, 5 docs should make 3 API calls
        mock_api.return_value = {
            "text": json.dumps([
                {"file_id": "doc1", "categories": ["org_structure"], "confidence": 0.8},
                {"file_id": "doc2", "categories": ["sop_playbook"], "confidence": 0.7},
            ]),
            "cost": {"total_cost_usd": 0.001},
        }

        docs = [_make_raw_doc(f"doc{i}") for i in range(5)]
        await classify_documents(docs, batch_size=2)
        assert mock_api.call_count == 3  # ceil(5/2)

    @patch("src.bootstrap.classifier.call_claude_api", new_callable=AsyncMock)
    async def test_classify_validates_categories(self, mock_api):
        mock_api.return_value = {
            "text": json.dumps([
                {"file_id": "doc1", "categories": ["fake_category"], "confidence": 0.9},
            ]),
            "cost": {"total_cost_usd": 0.001},
        }

        docs = [_make_raw_doc("doc1")]
        result = await classify_documents(docs)
        # Invalid category should fall back to irrelevant
        assert result[0].categories == [DocumentCategory.IRRELEVANT.value]

    @patch("src.bootstrap.classifier.call_claude_api", new_callable=AsyncMock)
    async def test_classify_handles_missing_file_ids(self, mock_api):
        # API returns classification for unknown file_id
        mock_api.return_value = {
            "text": json.dumps([
                {"file_id": "unknown", "categories": ["org_structure"], "confidence": 0.9},
            ]),
            "cost": {"total_cost_usd": 0.001},
        }

        docs = [_make_raw_doc("doc1")]
        result = await classify_documents(docs)
        # doc1 should still be classified (as irrelevant since no match)
        assert len(result) == 1
        assert not result[0].is_relevant
