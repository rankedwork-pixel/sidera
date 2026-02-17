"""Tests for GDPR API endpoints."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.gdpr import router


def _make_app() -> FastAPI:
    """Create a minimal app with the GDPR router."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client():
    return TestClient(_make_app())


ADMIN_HEADERS = {"X-User-Role": "admin", "X-User-ID": "admin-1"}
NON_ADMIN_HEADERS = {"X-User-Role": "approver", "X-User-ID": "user-1"}


@asynccontextmanager
async def _mock_db_session(mock_session):
    """Async context manager that yields mock_session."""
    yield mock_session


# ===================================================================
# Auth / RBAC
# ===================================================================


class TestGDPRAuth:
    def test_export_requires_admin(self, client):
        """Non-admin users should get 403."""
        resp = client.get("/api/gdpr/export/U123", headers=NON_ADMIN_HEADERS)
        assert resp.status_code == 403
        assert "admin role" in resp.json()["detail"]

    def test_delete_requires_admin(self, client):
        """Non-admin users should get 403."""
        resp = client.delete("/api/gdpr/delete/U123", headers=NON_ADMIN_HEADERS)
        assert resp.status_code == 403

    def test_export_no_role_header(self, client):
        """Missing role header should get 403."""
        resp = client.get("/api/gdpr/export/U123")
        assert resp.status_code == 403


# ===================================================================
# Export endpoint
# ===================================================================


class TestGDPRExport:
    def test_export_returns_user_data(self, client):
        """Admin can export user data."""
        mock_data = {
            "user_id": "U123",
            "user": {"display_name": "Test"},
            "accounts": [],
            "audit_log": [],
            "approvals": [],
            "conversation_threads": [],
        }

        mock_session = AsyncMock()
        mock_export = AsyncMock(return_value=mock_data)

        with (
            patch(
                "src.db.service.export_user_data",
                mock_export,
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=_mock_db_session(mock_session),
            ),
        ):
            resp = client.get("/api/gdpr/export/U123", headers=ADMIN_HEADERS)

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["user_id"] == "U123"
        assert "data" in body

    def test_export_handles_db_error(self, client):
        """Database error returns 500."""

        @asynccontextmanager
        async def _raise(*a, **kw):
            raise Exception("DB down")
            yield  # noqa: unreachable

        with patch(
            "src.db.session.get_db_session",
            return_value=_raise(),
        ):
            resp = client.get("/api/gdpr/export/U123", headers=ADMIN_HEADERS)
        assert resp.status_code == 500


# ===================================================================
# Delete endpoint
# ===================================================================


class TestGDPRDelete:
    def test_delete_returns_counts(self, client):
        """Admin can delete user data and get counts."""
        mock_counts = {
            "conversation_threads": 2,
            "approvals": 5,
            "accounts": 1,
            "audit_log_anonymized": 10,
            "user": 1,
        }

        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_delete = AsyncMock(return_value=mock_counts)

        with (
            patch(
                "src.db.service.delete_user_data",
                mock_delete,
            ),
            patch(
                "src.db.session.get_db_session",
                return_value=_mock_db_session(mock_session),
            ),
        ):
            resp = client.delete("/api/gdpr/delete/U123", headers=ADMIN_HEADERS)

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "deleted"
        assert body["counts"]["user"] == 1
        assert body["counts"]["approvals"] == 5

    def test_delete_handles_db_error(self, client):
        """Database error returns 500."""

        @asynccontextmanager
        async def _raise(*a, **kw):
            raise Exception("DB down")
            yield  # noqa: unreachable

        with patch(
            "src.db.session.get_db_session",
            return_value=_raise(),
        ):
            resp = client.delete("/api/gdpr/delete/U123", headers=ADMIN_HEADERS)
        assert resp.status_code == 500
