"""Tests for the bootstrap REST API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.bootstrap.models import BootstrapPlan, BootstrapStatus, ExecutionResult


@pytest.fixture()
def client():
    """Create a test client with the bootstrap router."""
    from fastapi import FastAPI

    from src.api.routes.bootstrap import _plans, router

    app = FastAPI()
    app.include_router(router)
    _plans.clear()

    return TestClient(app)


class TestStartBootstrap:
    @patch("src.api.routes.bootstrap.run_bootstrap", new_callable=AsyncMock)
    def test_start_success(self, mock_run, client):
        plan = BootstrapPlan(
            source_folder_id="folder123",
            documents_crawled=10,
            departments=[],
            roles=[],
        )
        mock_run.return_value = plan

        response = client.post(
            "/api/bootstrap/",
            json={"folder_id": "folder123", "user_id": "admin"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["plan_id"] == plan.id
        assert data["status"] == "draft"

    @patch("src.api.routes.bootstrap.run_bootstrap", new_callable=AsyncMock)
    def test_start_with_errors(self, mock_run, client):
        plan = BootstrapPlan(errors=["No docs found"])
        mock_run.return_value = plan

        response = client.post(
            "/api/bootstrap/",
            json={"folder_id": "empty_folder"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "warning" in data["message"]

    @patch("src.api.routes.bootstrap.run_bootstrap", new_callable=AsyncMock)
    def test_start_api_error(self, mock_run, client):
        mock_run.side_effect = Exception("Drive auth failed")

        response = client.post(
            "/api/bootstrap/",
            json={"folder_id": "bad_folder"},
        )
        assert response.status_code == 500


class TestGetPlan:
    def test_get_existing(self, client):
        from src.api.routes.bootstrap import _plans

        plan = BootstrapPlan(source_folder_id="folder123")
        _plans[plan.id] = plan

        response = client.get(f"/api/bootstrap/{plan.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["source_folder_id"] == "folder123"

    def test_get_not_found(self, client):
        response = client.get("/api/bootstrap/nonexistent")
        assert response.status_code == 404


class TestApprovePlan:
    @patch("src.api.routes.bootstrap.execute_plan", new_callable=AsyncMock)
    def test_approve_success(self, mock_execute, client):
        from src.api.routes.bootstrap import _plans

        plan = BootstrapPlan(status=BootstrapStatus.DRAFT.value)
        _plans[plan.id] = plan

        mock_execute.return_value = ExecutionResult(
            plan_id=plan.id, departments_created=1
        )

        response = client.post(f"/api/bootstrap/{plan.id}/approve")
        assert response.status_code == 200
        data = response.json()
        assert "successfully" in data["message"]

    def test_approve_not_found(self, client):
        response = client.post("/api/bootstrap/nonexistent/approve")
        assert response.status_code == 404

    def test_approve_already_executed(self, client):
        from src.api.routes.bootstrap import _plans

        plan = BootstrapPlan(status=BootstrapStatus.EXECUTED.value)
        _plans[plan.id] = plan

        response = client.post(f"/api/bootstrap/{plan.id}/approve")
        assert response.status_code == 400


class TestRejectPlan:
    def test_reject_success(self, client):
        from src.api.routes.bootstrap import _plans

        plan = BootstrapPlan(status=BootstrapStatus.DRAFT.value)
        _plans[plan.id] = plan

        response = client.post(f"/api/bootstrap/{plan.id}/reject")
        assert response.status_code == 200
        assert plan.status == BootstrapStatus.REJECTED.value

    def test_reject_not_draft(self, client):
        from src.api.routes.bootstrap import _plans

        plan = BootstrapPlan(status=BootstrapStatus.APPROVED.value)
        _plans[plan.id] = plan

        response = client.post(f"/api/bootstrap/{plan.id}/reject")
        assert response.status_code == 400
