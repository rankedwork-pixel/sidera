"""REST API routes for the company bootstrap pipeline.

Endpoints:

- ``POST /api/bootstrap/``          -- Start a bootstrap run
- ``GET  /api/bootstrap/{plan_id}`` -- Get plan details
- ``POST /api/bootstrap/{plan_id}/approve`` -- Approve a plan
- ``POST /api/bootstrap/{plan_id}/reject``  -- Reject a plan
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.bootstrap import execute_plan, run_bootstrap
from src.bootstrap.models import BootstrapPlan, BootstrapStatus

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/bootstrap", tags=["bootstrap"])

# In-memory plan store.  For production, consider persisting to DB.
_plans: dict[str, BootstrapPlan] = {}


# =====================================================================
# Request / response models
# =====================================================================


class BootstrapRequest(BaseModel):
    """Request body for starting a bootstrap run."""

    folder_id: str = Field(..., description="Google Drive folder ID to crawl")
    user_id: str = Field(default="bootstrap", description="User initiating the run")
    max_docs: int = Field(default=100, ge=1, le=500, description="Max documents to crawl")


class BootstrapResponse(BaseModel):
    """Response for bootstrap operations."""

    plan_id: str
    status: str
    summary: dict[str, Any]
    message: str = ""


# =====================================================================
# Endpoints
# =====================================================================


@router.post("/", response_model=BootstrapResponse)
async def start_bootstrap(request: BootstrapRequest) -> dict[str, Any]:
    """Start a new bootstrap run.

    Crawls the specified Google Drive folder, classifies documents,
    extracts organizational knowledge, and generates a draft plan
    for human review.
    """
    logger.info(
        "bootstrap.api.start",
        folder_id=request.folder_id,
        user_id=request.user_id,
        max_docs=request.max_docs,
    )

    try:
        plan = await run_bootstrap(
            request.folder_id,
            user_id=request.user_id,
            max_docs=request.max_docs,
        )
    except Exception as exc:
        logger.error("bootstrap.api.start_error", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    _plans[plan.id] = plan

    return {
        "plan_id": plan.id,
        "status": plan.status,
        "summary": plan.summary(),
        "message": (
            "Bootstrap plan generated. Review and approve to execute."
            if not plan.errors
            else f"Bootstrap completed with {len(plan.errors)} warning(s)."
        ),
    }


@router.get("/{plan_id}", response_model=None)
async def get_plan(plan_id: str) -> dict[str, Any]:
    """Get the full details of a bootstrap plan."""
    plan = _plans.get(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan '{plan_id}' not found")

    return plan.to_dict()


@router.post("/{plan_id}/approve", response_model=BootstrapResponse)
async def approve_plan(plan_id: str) -> dict[str, Any]:
    """Approve a bootstrap plan and execute it.

    Writes all departments, roles, skills, and memories to the database.
    """
    plan = _plans.get(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan '{plan_id}' not found")

    if plan.status != BootstrapStatus.DRAFT.value:
        raise HTTPException(
            status_code=400,
            detail=f"Plan is '{plan.status}', can only approve 'draft' plans",
        )

    plan.status = BootstrapStatus.APPROVED.value

    try:
        result = await execute_plan(plan, user_id="bootstrap")
    except Exception as exc:
        plan.status = BootstrapStatus.FAILED.value
        logger.error("bootstrap.api.execute_error", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "plan_id": plan.id,
        "status": plan.status,
        "summary": result.summary(),
        "message": (
            "Bootstrap executed successfully."
            if result.success
            else f"Bootstrap executed with {len(result.errors)} error(s)."
        ),
    }


@router.post("/{plan_id}/reject", response_model=BootstrapResponse)
async def reject_plan(plan_id: str) -> dict[str, Any]:
    """Reject a bootstrap plan."""
    plan = _plans.get(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan '{plan_id}' not found")

    if plan.status != BootstrapStatus.DRAFT.value:
        raise HTTPException(
            status_code=400,
            detail=f"Plan is '{plan.status}', can only reject 'draft' plans",
        )

    plan.status = BootstrapStatus.REJECTED.value

    return {
        "plan_id": plan.id,
        "status": plan.status,
        "summary": plan.summary(),
        "message": "Bootstrap plan rejected.",
    }
