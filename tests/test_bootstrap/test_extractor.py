"""Tests for the bootstrap knowledge extractor."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.bootstrap.extractor import (
    _chunk_document,
    _filter_by_categories,
    _merge_context_into_roles,
    _merge_vocabulary_into_departments,
    _parse_json_response,
    _prepare_doc_batches,
    _prepare_doc_content,
    extract_knowledge,
)
from src.bootstrap.models import (
    ClassifiedDocument,
    ExtractedDepartment,
    ExtractedRole,
)


def _make_classified(
    file_id: str = "doc1",
    categories: list[str] | None = None,
    content: str = "Test content",
) -> ClassifiedDocument:
    return ClassifiedDocument(
        file_id=file_id,
        title=f"Doc {file_id}",
        mime_type="doc",
        content=content,
        categories=categories or ["org_structure"],
        confidence=0.9,
        char_count=len(content),
    )


class TestParseJsonResponse:
    def test_clean_json(self):
        text = '{"departments": [{"id": "eng"}]}'
        result = _parse_json_response(text)
        assert result["departments"][0]["id"] == "eng"

    def test_with_markdown_fences(self):
        text = '```json\n{"roles": []}\n```'
        result = _parse_json_response(text)
        assert result["roles"] == []

    def test_invalid_json(self):
        result = _parse_json_response("not json")
        assert result == {}

    def test_non_dict_json(self):
        result = _parse_json_response("[1, 2, 3]")
        assert result == {}


class TestFilterByCategories:
    def test_filter(self):
        docs = [
            _make_classified("d1", ["org_structure"]),
            _make_classified("d2", ["sop_playbook"]),
            _make_classified("d3", ["irrelevant"]),
        ]
        result = _filter_by_categories(docs, {"org_structure"})
        assert len(result) == 1
        assert result[0].file_id == "d1"

    def test_multi_category_match(self):
        docs = [
            _make_classified("d1", ["org_structure", "sop_playbook"]),
        ]
        result = _filter_by_categories(docs, {"sop_playbook"})
        assert len(result) == 1


class TestPrepareDocContent:
    def test_formats_docs(self):
        docs = [_make_classified("d1", content="Hello")]
        result = _prepare_doc_content(docs)
        assert "Doc d1" in result
        assert "Hello" in result

    def test_respects_char_limit(self):
        big_content = "x" * 40_000
        docs = [
            _make_classified("d1", content=big_content),
            _make_classified("d2", content="Small"),
        ]
        result = _prepare_doc_content(docs)
        # d2 might be partially included or excluded based on limit
        assert "Doc d1" in result


class TestMergeContextIntoRoles:
    def test_merge_goals(self):
        roles = [ExtractedRole(id="swe", name="SWE", department_id="eng", description="")]
        context = {
            "role_goals": [{"role_id": "swe", "goals": ["Ship fast", "Test everything"]}],
            "role_principles": [],
        }
        _merge_context_into_roles(roles, context)
        assert "Ship fast" in roles[0].goals
        assert "Test everything" in roles[0].goals

    def test_merge_principles(self):
        roles = [ExtractedRole(id="swe", name="SWE", department_id="eng", description="")]
        context = {
            "role_goals": [],
            "role_principles": [{"role_id": "swe", "principles": ["Always review code"]}],
        }
        _merge_context_into_roles(roles, context)
        assert "Always review code" in roles[0].principles

    def test_skip_unknown_role(self):
        roles = [ExtractedRole(id="swe", name="SWE", department_id="eng", description="")]
        context = {
            "role_goals": [{"role_id": "unknown", "goals": ["Goal"]}],
            "role_principles": [],
        }
        _merge_context_into_roles(roles, context)
        assert roles[0].goals == []


class TestMergeVocabularyIntoDepartments:
    def test_merge_vocab(self):
        depts = [
            ExtractedDepartment(
                id="eng",
                name="Engineering",
                description="",
                vocabulary=[{"term": "SLA", "definition": "Service Level Agreement"}],
            )
        ]
        context = {
            "department_vocabulary": [
                {
                    "department_id": "eng",
                    "vocabulary": [
                        {"term": "CI", "definition": "Continuous Integration"},
                        {"term": "SLA", "definition": "Duplicate"},  # should be deduped
                    ],
                }
            ]
        }
        _merge_vocabulary_into_departments(depts, context)
        terms = [v["term"] for v in depts[0].vocabulary]
        assert "CI" in terms
        assert terms.count("SLA") == 1  # not duplicated


@pytest.mark.asyncio
class TestExtractKnowledge:
    @patch("src.bootstrap.extractor.call_claude_api", new_callable=AsyncMock)
    async def test_full_extraction(self, mock_api):
        # Mock three passes: org, skills, context
        mock_api.side_effect = [
            # Pass 1: Org structure
            {
                "text": json.dumps(
                    {
                        "departments": [
                            {"id": "eng", "name": "Engineering", "description": "Builds stuff"}
                        ],
                        "roles": [
                            {
                                "id": "swe",
                                "name": "Software Engineer",
                                "department_id": "eng",
                                "description": "Writes code",
                                "persona": "A careful engineer",
                            }
                        ],
                    }
                ),
                "cost": {"total_cost_usd": 0.05},
            },
            # Pass 2: Skills
            {
                "text": json.dumps(
                    {
                        "skills": [
                            {
                                "id": "code_review",
                                "name": "Code Review",
                                "role_id": "swe",
                                "department_id": "eng",
                                "description": "Review pull requests",
                                "category": "analysis",
                                "model": "sonnet",
                                "system_supplement": "Review code carefully",
                                "prompt_template": "Review the latest PRs",
                                "output_format": "## Review Summary",
                                "business_guidance": "Focus on correctness",
                            }
                        ]
                    }
                ),
                "cost": {"total_cost_usd": 0.08},
            },
            # Pass 3: Context
            {
                "text": json.dumps(
                    {
                        "role_goals": [{"role_id": "swe", "goals": ["Ship quality code"]}],
                        "role_principles": [{"role_id": "swe", "principles": ["Test first"]}],
                        "department_vocabulary": [],
                        "memories": [
                            {
                                "role_id": "swe",
                                "department_id": "eng",
                                "memory_type": "insight",
                                "title": "Migration note",
                                "content": "Migrated to Python 3.12 in Q1",
                                "confidence": 0.9,
                            }
                        ],
                    }
                ),
                "cost": {"total_cost_usd": 0.04},
            },
        ]

        docs = [
            _make_classified("d1", ["org_structure"]),
            _make_classified("d2", ["sop_playbook"]),
            _make_classified("d3", ["goals_kpis"]),
        ]

        knowledge, cost = await extract_knowledge(docs)

        assert len(knowledge.departments) == 1
        assert knowledge.departments[0].id == "eng"
        assert len(knowledge.roles) == 1
        assert knowledge.roles[0].id == "swe"
        assert "Ship quality code" in knowledge.roles[0].goals
        assert "Test first" in knowledge.roles[0].principles
        assert len(knowledge.skills) == 1
        assert knowledge.skills[0].id == "code_review"
        assert len(knowledge.memories) == 1
        assert cost > 0

    @patch("src.bootstrap.extractor.call_claude_api", new_callable=AsyncMock)
    async def test_extraction_handles_empty_categories(self, mock_api):
        # No org structure docs, no SOP docs, no context docs
        docs = [_make_classified("d1", ["meeting_notes"])]
        knowledge, cost = await extract_knowledge(docs)

        assert knowledge.departments == []
        assert knowledge.roles == []
        assert knowledge.skills == []
        # meeting_notes counted in context pass, but no roles to merge into
        assert mock_api.call_count <= 1

    @patch("src.bootstrap.extractor.call_claude_api", new_callable=AsyncMock)
    async def test_extraction_handles_api_error(self, mock_api):
        mock_api.side_effect = Exception("API timeout")

        docs = [_make_classified("d1", ["org_structure"])]
        knowledge, cost = await extract_knowledge(docs)

        # Should return empty knowledge, not crash
        assert knowledge.departments == []
        assert knowledge.roles == []
        assert cost == 0.0

    @patch("src.bootstrap.extractor.call_claude_api", new_callable=AsyncMock)
    async def test_extraction_multi_batch(self, mock_api):
        """Large docs should be split into batches with multiple API calls."""
        mock_api.return_value = {
            "text": json.dumps(
                {
                    "departments": [
                        {"id": "eng", "name": "Engineering", "description": "Builds stuff"}
                    ],
                    "roles": [
                        {
                            "id": "swe",
                            "name": "Software Engineer",
                            "department_id": "eng",
                            "description": "Writes code",
                            "persona": "A careful engineer",
                        }
                    ],
                }
            ),
            "cost": {"total_cost_usd": 0.05},
        }

        # Create a large document that will be chunked into multiple batches
        big_content = "x" * 60_000
        docs = [
            _make_classified("d1", ["org_structure"], content=big_content),
        ]
        knowledge, cost = await extract_knowledge(docs)

        # Should have made multiple API calls due to chunking
        assert mock_api.call_count >= 2
        # Results should be merged
        assert len(knowledge.departments) >= 1


class TestChunkDocument:
    def test_short_doc_single_chunk(self):
        content = "Short text"
        chunks = _chunk_document(content)
        assert len(chunks) == 1
        assert chunks[0] == content

    def test_exact_limit_single_chunk(self):
        content = "x" * 25_000
        chunks = _chunk_document(content, max_chars=25_000)
        assert len(chunks) == 1

    def test_long_doc_multiple_chunks(self):
        content = "x" * 60_000
        chunks = _chunk_document(content, max_chars=25_000, overlap=2_000)
        assert len(chunks) >= 3
        # Each chunk should be at most max_chars
        for chunk in chunks:
            assert len(chunk) <= 25_000

    def test_overlap_preserved(self):
        """Overlapping regions should contain the same content."""
        content = "ABCDE" * 10_000  # 50K chars
        chunks = _chunk_document(content, max_chars=25_000, overlap=2_000)
        assert len(chunks) >= 2
        # End of first chunk should overlap with start of second chunk
        overlap_from_first = chunks[0][-2_000:]
        overlap_from_second = chunks[1][:2_000]
        assert overlap_from_first == overlap_from_second

    def test_all_content_covered(self):
        """Union of chunks should cover the entire document."""
        content = "".join(str(i % 10) for i in range(55_000))
        chunks = _chunk_document(content, max_chars=25_000, overlap=2_000)
        # First char of content in first chunk, last char in last chunk
        assert chunks[0][0] == content[0]
        assert chunks[-1][-1] == content[-1]

    def test_empty_content(self):
        chunks = _chunk_document("")
        assert len(chunks) == 1
        assert chunks[0] == ""


class TestPrepareDocBatches:
    def test_small_docs_single_batch(self):
        docs = [_make_classified("d1", content="Short")]
        batches = _prepare_doc_batches(docs)
        assert len(batches) == 1
        assert "Doc d1" in batches[0]

    def test_large_doc_split_into_parts(self):
        big_content = "x" * 60_000
        docs = [_make_classified("d1", content=big_content)]
        batches = _prepare_doc_batches(docs)
        # Large doc should be chunked, creating multiple parts
        assert len(batches) >= 2
        # Parts should be labeled
        assert "(part 1/" in batches[0]

    def test_multiple_small_docs_packed(self):
        docs = [_make_classified(f"d{i}", content="Small content") for i in range(5)]
        batches = _prepare_doc_batches(docs)
        # All small docs should fit in one batch
        assert len(batches) == 1

    def test_batches_respect_char_limit(self):
        """Each batch should stay within the character limit."""
        docs = [_make_classified(f"d{i}", content="x" * 10_000) for i in range(5)]
        batches = _prepare_doc_batches(docs, max_chars_per_batch=30_000)
        for batch in batches:
            assert len(batch) <= 35_000  # slight overhead from formatting

    def test_empty_docs(self):
        batches = _prepare_doc_batches([])
        assert len(batches) == 1
        assert batches[0] == ""
