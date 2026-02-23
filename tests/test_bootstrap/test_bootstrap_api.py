"""Tests for the bootstrap REST API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.bootstrap.models import (
    BootstrapPlan,
    BootstrapStatus,
    ExecutionResult,
    ExtractedDepartment,
    ExtractedMemory,
    ExtractedRole,
    ExtractedSkill,
)


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

        mock_execute.return_value = ExecutionResult(plan_id=plan.id, departments_created=1)

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


# =====================================================================
# Plan editing tests
# =====================================================================


def _make_draft_plan() -> BootstrapPlan:
    """Create a draft plan with departments, roles, skills, and memories."""
    return BootstrapPlan(
        departments=[
            ExtractedDepartment(id="eng", name="Engineering", description="Builds stuff"),
            ExtractedDepartment(id="sales", name="Sales", description="Sells stuff"),
        ],
        roles=[
            ExtractedRole(
                id="swe",
                name="SWE",
                department_id="eng",
                description="Codes",
                manages=["qa"],
            ),
            ExtractedRole(
                id="qa",
                name="QA",
                department_id="eng",
                description="Tests",
            ),
            ExtractedRole(
                id="ae",
                name="Account Exec",
                department_id="sales",
                description="Sells",
            ),
        ],
        skills=[
            ExtractedSkill(
                id="code_review",
                name="Code Review",
                role_id="swe",
                department_id="eng",
                description="Reviews PRs",
            ),
            ExtractedSkill(
                id="test_plan",
                name="Test Plan",
                role_id="qa",
                department_id="eng",
                description="Plans tests",
            ),
        ],
        memories=[
            ExtractedMemory(
                role_id="swe",
                department_id="eng",
                memory_type="insight",
                title="Fact",
                content="Detail",
            ),
        ],
    )


class TestPatchDepartment:
    def test_update_success(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        _plans[plan.id] = plan

        response = client.patch(
            f"/api/bootstrap/{plan.id}/departments/eng",
            json={"description": "New description"},
        )
        assert response.status_code == 200
        assert plan.departments[0].description == "New description"

    def test_update_not_found(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        _plans[plan.id] = plan

        response = client.patch(
            f"/api/bootstrap/{plan.id}/departments/unknown",
            json={"description": "New"},
        )
        assert response.status_code == 404

    def test_update_no_fields(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        _plans[plan.id] = plan

        response = client.patch(
            f"/api/bootstrap/{plan.id}/departments/eng",
            json={},
        )
        assert response.status_code == 422

    def test_update_not_draft(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        plan.status = BootstrapStatus.APPROVED.value
        _plans[plan.id] = plan

        response = client.patch(
            f"/api/bootstrap/{plan.id}/departments/eng",
            json={"description": "New"},
        )
        assert response.status_code == 400


class TestPatchRole:
    def test_update_success(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        _plans[plan.id] = plan

        response = client.patch(
            f"/api/bootstrap/{plan.id}/roles/swe",
            json={"persona": "Expert coder", "description": "Writes great code"},
        )
        assert response.status_code == 200
        role = next(r for r in plan.roles if r.id == "swe")
        assert role.persona == "Expert coder"
        assert role.description == "Writes great code"

    def test_update_invalid_department(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        _plans[plan.id] = plan

        response = client.patch(
            f"/api/bootstrap/{plan.id}/roles/swe",
            json={"department_id": "nonexistent"},
        )
        assert response.status_code == 422

    def test_update_valid_department(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        _plans[plan.id] = plan

        response = client.patch(
            f"/api/bootstrap/{plan.id}/roles/swe",
            json={"department_id": "sales"},
        )
        assert response.status_code == 200
        role = next(r for r in plan.roles if r.id == "swe")
        assert role.department_id == "sales"


class TestPatchSkill:
    def test_update_success(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        _plans[plan.id] = plan

        response = client.patch(
            f"/api/bootstrap/{plan.id}/skills/code_review",
            json={"category": "operations", "model": "haiku"},
        )
        assert response.status_code == 200
        skill = next(s for s in plan.skills if s.id == "code_review")
        assert skill.category == "operations"
        assert skill.model == "haiku"

    def test_update_not_found(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        _plans[plan.id] = plan

        response = client.patch(
            f"/api/bootstrap/{plan.id}/skills/unknown",
            json={"description": "New"},
        )
        assert response.status_code == 404


class TestDeleteDepartment:
    def test_delete_with_cascade(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        _plans[plan.id] = plan

        response = client.delete(f"/api/bootstrap/{plan.id}/departments/eng")
        assert response.status_code == 200
        data = response.json()
        assert data["cascaded_roles"] == 2  # swe + qa

        # Engineering dept gone
        assert not any(d.id == "eng" for d in plan.departments)
        # Sales dept still there
        assert any(d.id == "sales" for d in plan.departments)
        # eng roles gone
        assert not any(r.id == "swe" for r in plan.roles)
        assert not any(r.id == "qa" for r in plan.roles)
        # eng skills gone
        assert not any(s.id == "code_review" for s in plan.skills)
        # eng memories gone
        assert len(plan.memories) == 0
        # ae still there
        assert any(r.id == "ae" for r in plan.roles)

    def test_delete_not_found(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        _plans[plan.id] = plan

        response = client.delete(f"/api/bootstrap/{plan.id}/departments/unknown")
        assert response.status_code == 404


class TestDeleteRole:
    def test_delete_with_cascade(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        _plans[plan.id] = plan

        response = client.delete(f"/api/bootstrap/{plan.id}/roles/swe")
        assert response.status_code == 200
        data = response.json()
        assert data["cascaded_skills"] == 1  # code_review

        # swe gone
        assert not any(r.id == "swe" for r in plan.roles)
        # code_review skill gone
        assert not any(s.id == "code_review" for s in plan.skills)
        # swe memory gone
        assert len(plan.memories) == 0
        # qa still has its test_plan skill
        assert any(s.id == "test_plan" for s in plan.skills)

    def test_delete_cleans_manages(self, client):
        """Deleting a role should remove it from other roles' manages lists."""
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        _plans[plan.id] = plan
        # swe manages qa
        assert "qa" in plan.roles[0].manages

        client.delete(f"/api/bootstrap/{plan.id}/roles/qa")
        swe = next(r for r in plan.roles if r.id == "swe")
        assert "qa" not in swe.manages


class TestDeleteSkill:
    def test_delete_success(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        _plans[plan.id] = plan

        response = client.delete(f"/api/bootstrap/{plan.id}/skills/code_review")
        assert response.status_code == 200
        assert not any(s.id == "code_review" for s in plan.skills)

    def test_delete_not_found(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        _plans[plan.id] = plan

        response = client.delete(f"/api/bootstrap/{plan.id}/skills/unknown")
        assert response.status_code == 404


class TestAddDepartment:
    def test_add_success(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        _plans[plan.id] = plan

        response = client.post(
            f"/api/bootstrap/{plan.id}/departments",
            json={"id": "ops", "name": "Operations", "description": "Runs things"},
        )
        assert response.status_code == 200
        assert any(d.id == "ops" for d in plan.departments)

    def test_add_duplicate(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        _plans[plan.id] = plan

        response = client.post(
            f"/api/bootstrap/{plan.id}/departments",
            json={"id": "eng", "name": "Engineering"},
        )
        assert response.status_code == 409


class TestAddRole:
    def test_add_success(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        _plans[plan.id] = plan

        response = client.post(
            f"/api/bootstrap/{plan.id}/roles",
            json={
                "id": "devops",
                "name": "DevOps Engineer",
                "department_id": "eng",
                "description": "Deploys",
            },
        )
        assert response.status_code == 200
        assert any(r.id == "devops" for r in plan.roles)

    def test_add_duplicate(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        _plans[plan.id] = plan

        response = client.post(
            f"/api/bootstrap/{plan.id}/roles",
            json={"id": "swe", "name": "SWE", "department_id": "eng"},
        )
        assert response.status_code == 409

    def test_add_invalid_department(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        _plans[plan.id] = plan

        response = client.post(
            f"/api/bootstrap/{plan.id}/roles",
            json={"id": "new_role", "name": "New", "department_id": "nonexistent"},
        )
        assert response.status_code == 422

    def test_add_not_draft(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        plan.status = BootstrapStatus.EXECUTED.value
        _plans[plan.id] = plan

        response = client.post(
            f"/api/bootstrap/{plan.id}/roles",
            json={"id": "new", "name": "New", "department_id": "eng"},
        )
        assert response.status_code == 400


# =====================================================================
# Refine endpoint tests
# =====================================================================


class TestRefineEndpoint:
    @patch("src.api.routes.bootstrap.refine_plan", new_callable=AsyncMock)
    def test_refine_success(self, mock_refine, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        _plans[plan.id] = plan

        mock_refine.return_value = (
            plan,
            ["Added department 'ops'", "LLM explanation: Added ops team"],
            0.005,
        )

        response = client.post(
            f"/api/bootstrap/{plan.id}/refine",
            json={"feedback": "Add an ops team"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["plan_id"] == plan.id
        assert data["status"] == "draft"
        assert len(data["changes_applied"]) == 2
        assert data["refinement_cost"] == "$0.0050"
        mock_refine.assert_called_once()

    def test_refine_not_found(self, client):
        response = client.post(
            "/api/bootstrap/nonexistent/refine",
            json={"feedback": "Add ops team"},
        )
        assert response.status_code == 404

    def test_refine_not_draft(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        plan.status = BootstrapStatus.APPROVED.value
        _plans[plan.id] = plan

        response = client.post(
            f"/api/bootstrap/{plan.id}/refine",
            json={"feedback": "Add ops team"},
        )
        assert response.status_code == 400

    def test_refine_empty_feedback(self, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        _plans[plan.id] = plan

        response = client.post(
            f"/api/bootstrap/{plan.id}/refine",
            json={"feedback": ""},
        )
        assert response.status_code == 422  # Pydantic validation (min_length=1)

    @patch("src.api.routes.bootstrap.refine_plan", new_callable=AsyncMock)
    def test_refine_adds_cost_to_plan(self, mock_refine, client):
        from src.api.routes.bootstrap import _plans

        plan = _make_draft_plan()
        plan.estimated_cost = 1.00
        _plans[plan.id] = plan

        mock_refine.return_value = (plan, ["Modified eng"], 0.005)

        response = client.post(
            f"/api/bootstrap/{plan.id}/refine",
            json={"feedback": "Update eng description"},
        )
        assert response.status_code == 200
        # Cost should be accumulated
        assert plan.estimated_cost == 1.005
