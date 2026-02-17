"""Tests for Google Drive document sync (living documents)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.skills.document_sync import (
    _get_document_sync_config,
    format_drive_entry,
    sync_role_output_to_drive,
)
from src.skills.schema import RoleDefinition

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_role(
    role_id: str = "test_role",
    name: str = "Test Role",
    document_sync: tuple[tuple[str, str], ...] = (),
) -> RoleDefinition:
    return RoleDefinition(
        id=role_id,
        name=name,
        department_id="test_dept",
        description="A test role",
        document_sync=document_sync,
    )


# ---------------------------------------------------------------------------
# format_drive_entry
# ---------------------------------------------------------------------------


class TestFormatDriveEntry:
    def test_basic_format(self):
        ts = datetime(2026, 2, 17, 14, 30, 0, tzinfo=timezone.utc)
        result = format_drive_entry(
            role_name="Media Buyer",
            output_type="briefings",
            content="Some analysis text.",
            timestamp=ts,
        )
        assert "2026-02-17 14:30 UTC" in result
        assert "Media Buyer Briefing" in result
        assert "Some analysis text." in result
        assert "---" in result

    def test_with_all_metadata(self):
        result = format_drive_entry(
            role_name="Strategist",
            output_type="meetings",
            content="Meeting notes.",
            metadata={
                "cost_usd": 1.23,
                "skills_run": 3,
                "duration_seconds": 120,
                "action_items_count": 5,
            },
        )
        assert "Cost: $1.23" in result
        assert "Skills: 3" in result
        assert "Duration: 120s" in result
        assert "Action items: 5" in result

    def test_no_metadata(self):
        result = format_drive_entry(
            role_name="Role",
            output_type="briefings",
            content="Content.",
        )
        # No footer metadata line
        assert "Cost:" not in result
        assert "Content." in result

    def test_output_type_label_transformation(self):
        result = format_drive_entry(
            role_name="Role",
            output_type="briefings",
            content="",
        )
        assert "Briefing" in result
        assert "briefings" not in result

    def test_meetings_label(self):
        result = format_drive_entry(
            role_name="Role",
            output_type="meetings",
            content="",
        )
        assert "Meeting" in result

    def test_custom_timestamp(self):
        ts = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        result = format_drive_entry(
            role_name="R",
            output_type="briefings",
            content="",
            timestamp=ts,
        )
        assert "2025-01-01 00:00 UTC" in result

    def test_separator_present(self):
        result = format_drive_entry(
            role_name="R",
            output_type="briefings",
            content="X",
        )
        assert "\n---\n" in result


# ---------------------------------------------------------------------------
# _get_document_sync_config
# ---------------------------------------------------------------------------


class TestGetDocumentSyncConfig:
    def test_role_with_config(self):
        registry = MagicMock()
        registry.get_role.return_value = _make_role(
            document_sync=(("briefings", "doc123"), ("meetings", "doc456")),
        )
        result = _get_document_sync_config("test_role", registry)
        assert result == {"briefings": "doc123", "meetings": "doc456"}

    def test_role_without_config(self):
        registry = MagicMock()
        registry.get_role.return_value = _make_role(document_sync=())
        result = _get_document_sync_config("test_role", registry)
        assert result == {}

    def test_role_not_found(self):
        registry = MagicMock()
        registry.get_role.return_value = None
        result = _get_document_sync_config("nonexistent", registry)
        assert result == {}


# ---------------------------------------------------------------------------
# sync_role_output_to_drive
# ---------------------------------------------------------------------------


class TestSyncRoleOutputToDrive:
    @pytest.mark.asyncio
    async def test_success_path(self):
        mock_registry = MagicMock()
        mock_registry.get_role.return_value = _make_role(
            name="Media Buyer",
            document_sync=(("briefings", "doc_abc"),),
        )
        mock_connector = MagicMock()
        mock_connector.append_to_document.return_value = True

        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch(
                "src.connectors.google_drive.GoogleDriveConnector",
                return_value=mock_connector,
            ),
        ):
            result = await sync_role_output_to_drive(
                role_id="test_role",
                output_type="briefings",
                content="Daily analysis.",
                metadata={"cost_usd": 0.52},
            )

        assert result["synced"] is True
        assert result["doc_id"] == "doc_abc"
        mock_connector.append_to_document.assert_called_once()
        call_args = mock_connector.append_to_document.call_args
        assert "doc_abc" == call_args[0][0]
        assert "Daily analysis." in call_args[0][1]

    @pytest.mark.asyncio
    async def test_no_config(self):
        mock_registry = MagicMock()
        mock_registry.get_role.return_value = _make_role(document_sync=())

        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ):
            result = await sync_role_output_to_drive(
                role_id="test_role",
                output_type="briefings",
                content="text",
            )

        assert result["synced"] is False
        assert result["reason"] == "no_config"

    @pytest.mark.asyncio
    async def test_no_doc_for_type(self):
        mock_registry = MagicMock()
        mock_registry.get_role.return_value = _make_role(
            document_sync=(("meetings", "doc_xyz"),),
        )

        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ):
            result = await sync_role_output_to_drive(
                role_id="test_role",
                output_type="briefings",
                content="text",
            )

        assert result["synced"] is False
        assert result["reason"] == "no_doc_for_type"

    @pytest.mark.asyncio
    async def test_connector_returns_false(self):
        mock_registry = MagicMock()
        mock_registry.get_role.return_value = _make_role(
            document_sync=(("briefings", "doc_abc"),),
        )
        mock_connector = MagicMock()
        mock_connector.append_to_document.return_value = False

        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch(
                "src.connectors.google_drive.GoogleDriveConnector",
                return_value=mock_connector,
            ),
        ):
            result = await sync_role_output_to_drive(
                role_id="test_role",
                output_type="briefings",
                content="text",
            )

        assert result["synced"] is False
        assert result["reason"] == "append_returned_false"

    @pytest.mark.asyncio
    async def test_connector_raises(self):
        mock_registry = MagicMock()
        mock_registry.get_role.return_value = _make_role(
            document_sync=(("briefings", "doc_abc"),),
        )

        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch(
                "src.connectors.google_drive.GoogleDriveConnector",
                side_effect=Exception("No refresh token"),
            ),
        ):
            result = await sync_role_output_to_drive(
                role_id="test_role",
                output_type="briefings",
                content="text",
            )

        assert result["synced"] is False
        assert "No refresh token" in result["error"]

    @pytest.mark.asyncio
    async def test_registry_unavailable(self):
        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            side_effect=Exception("DB connection failed"),
        ):
            result = await sync_role_output_to_drive(
                role_id="test_role",
                output_type="briefings",
                content="text",
            )

        assert result["synced"] is False
        assert "DB connection failed" in result["error"]

    @pytest.mark.asyncio
    async def test_uses_role_name_from_param(self):
        mock_registry = MagicMock()
        mock_registry.get_role.return_value = _make_role(
            name="Media Buyer",
            document_sync=(("briefings", "doc_abc"),),
        )
        mock_connector = MagicMock()
        mock_connector.append_to_document.return_value = True

        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch(
                "src.connectors.google_drive.GoogleDriveConnector",
                return_value=mock_connector,
            ),
        ):
            await sync_role_output_to_drive(
                role_id="test_role",
                output_type="briefings",
                content="text",
                role_name="Custom Name",
            )

        appended_text = mock_connector.append_to_document.call_args[0][1]
        assert "Custom Name" in appended_text
        assert "Media Buyer" not in appended_text

    @pytest.mark.asyncio
    async def test_uses_role_name_from_registry(self):
        mock_registry = MagicMock()
        mock_registry.get_role.return_value = _make_role(
            name="Head of Marketing",
            document_sync=(("briefings", "doc_abc"),),
        )
        mock_connector = MagicMock()
        mock_connector.append_to_document.return_value = True

        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch(
                "src.connectors.google_drive.GoogleDriveConnector",
                return_value=mock_connector,
            ),
        ):
            await sync_role_output_to_drive(
                role_id="test_role",
                output_type="briefings",
                content="text",
            )

        appended_text = mock_connector.append_to_document.call_args[0][1]
        assert "Head of Marketing" in appended_text


# ---------------------------------------------------------------------------
# Schema integration
# ---------------------------------------------------------------------------


class TestDocumentSyncSchema:
    def test_role_definition_has_field(self):
        role = _make_role(document_sync=(("briefings", "doc1"),))
        assert role.document_sync == (("briefings", "doc1"),)

    def test_role_definition_default_empty(self):
        role = _make_role()
        assert role.document_sync == ()

    def test_document_sync_config_lookup(self):
        role = _make_role(
            document_sync=(("briefings", "abc"), ("meetings", "xyz")),
        )
        config = dict(role.document_sync)
        assert config["briefings"] == "abc"
        assert config["meetings"] == "xyz"


# ---------------------------------------------------------------------------
# Role evolution protection
# ---------------------------------------------------------------------------


class TestDocumentSyncForbiddenField:
    def test_document_sync_in_forbidden_fields(self):
        from src.skills.role_evolution import ROLE_FORBIDDEN_FIELDS

        assert "document_sync" in ROLE_FORBIDDEN_FIELDS
