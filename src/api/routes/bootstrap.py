"""REST API routes for the company bootstrap pipeline.

Endpoints:

- ``POST /api/bootstrap/``          -- Start a bootstrap run
- ``GET  /api/bootstrap/{plan_id}`` -- Get plan details
- ``POST /api/bootstrap/{plan_id}/approve`` -- Approve a plan
- ``POST /api/bootstrap/{plan_id}/reject``  -- Reject a plan
- ``POST /api/bootstrap/{plan_id}/refine``  -- Refine a plan with feedback
- ``PATCH/DELETE/POST`` endpoints for editing individual plan entities
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.bootstrap import execute_plan, run_bootstrap
from src.bootstrap.models import (
    BootstrapPlan,
    BootstrapStatus,
    ExtractedDepartment,
    ExtractedRole,
)
from src.bootstrap.refiner import refine_plan

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


# =====================================================================
# Plan editing — PATCH / DELETE / POST for individual entities
# =====================================================================


class DepartmentPlanUpdate(BaseModel):
    """Partial update for a department in a draft plan."""

    name: str | None = None
    description: str | None = None
    context: str | None = None
    vocabulary: list[dict[str, str]] | None = None


class RolePlanUpdate(BaseModel):
    """Partial update for a role in a draft plan."""

    name: str | None = None
    department_id: str | None = None
    description: str | None = None
    persona: str | None = None
    principles: list[str] | None = None
    goals: list[str] | None = None
    manages: list[str] | None = None


class SkillPlanUpdate(BaseModel):
    """Partial update for a skill in a draft plan."""

    name: str | None = None
    role_id: str | None = None
    department_id: str | None = None
    description: str | None = None
    category: str | None = None
    system_supplement: str | None = None
    prompt_template: str | None = None
    output_format: str | None = None
    business_guidance: str | None = None
    model: str | None = None


class NewDepartmentRequest(BaseModel):
    """Request body for adding a department to a draft plan."""

    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: str = ""
    context: str = ""


class NewRoleRequest(BaseModel):
    """Request body for adding a role to a draft plan."""

    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    department_id: str = Field(..., min_length=1)
    description: str = ""
    persona: str = ""


def _get_draft_plan(plan_id: str) -> BootstrapPlan:
    """Look up a plan and verify it is in 'draft' status.

    Raises:
        HTTPException: 404 if not found, 400 if not draft.
    """
    plan = _plans.get(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan '{plan_id}' not found")
    if plan.status != BootstrapStatus.DRAFT.value:
        raise HTTPException(
            status_code=400,
            detail=f"Plan is '{plan.status}', can only edit 'draft' plans",
        )
    return plan


# --- PATCH endpoints ---


@router.patch("/{plan_id}/departments/{dept_id}")
async def update_department(
    plan_id: str, dept_id: str, body: DepartmentPlanUpdate
) -> dict[str, Any]:
    """Update fields on a department in a draft plan."""
    plan = _get_draft_plan(plan_id)

    dept = next((d for d in plan.departments if d.id == dept_id), None)
    if not dept:
        raise HTTPException(status_code=404, detail=f"Department '{dept_id}' not found")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")

    for field_name, value in updates.items():
        setattr(dept, field_name, value)

    return {"message": f"Department '{dept_id}' updated", "updated_fields": list(updates.keys())}


@router.patch("/{plan_id}/roles/{role_id}")
async def update_role(
    plan_id: str, role_id: str, body: RolePlanUpdate
) -> dict[str, Any]:
    """Update fields on a role in a draft plan."""
    plan = _get_draft_plan(plan_id)

    role = next((r for r in plan.roles if r.id == role_id), None)
    if not role:
        raise HTTPException(status_code=404, detail=f"Role '{role_id}' not found")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")

    # Validate department_id reference if being changed
    if "department_id" in updates:
        valid_dept_ids = {d.id for d in plan.departments}
        if updates["department_id"] not in valid_dept_ids:
            raise HTTPException(
                status_code=422,
                detail=f"Department '{updates['department_id']}' does not exist in plan",
            )

    for field_name, value in updates.items():
        setattr(role, field_name, value)

    return {"message": f"Role '{role_id}' updated", "updated_fields": list(updates.keys())}


@router.patch("/{plan_id}/skills/{skill_id}")
async def update_skill(
    plan_id: str, skill_id: str, body: SkillPlanUpdate
) -> dict[str, Any]:
    """Update fields on a skill in a draft plan."""
    plan = _get_draft_plan(plan_id)

    skill = next((s for s in plan.skills if s.id == skill_id), None)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")

    for field_name, value in updates.items():
        setattr(skill, field_name, value)

    return {"message": f"Skill '{skill_id}' updated", "updated_fields": list(updates.keys())}


# --- DELETE endpoints ---


@router.delete("/{plan_id}/departments/{dept_id}")
async def delete_department(plan_id: str, dept_id: str) -> dict[str, Any]:
    """Remove a department from a draft plan.

    Cascades: removes all roles in that department, plus their skills
    and memories.
    """
    plan = _get_draft_plan(plan_id)

    dept = next((d for d in plan.departments if d.id == dept_id), None)
    if not dept:
        raise HTTPException(status_code=404, detail=f"Department '{dept_id}' not found")

    # Find roles to cascade
    cascade_role_ids = {r.id for r in plan.roles if r.department_id == dept_id}

    plan.departments = [d for d in plan.departments if d.id != dept_id]
    plan.roles = [r for r in plan.roles if r.department_id != dept_id]
    plan.skills = [
        s for s in plan.skills
        if s.department_id != dept_id and s.role_id not in cascade_role_ids
    ]
    plan.memories = [
        m for m in plan.memories
        if m.department_id != dept_id and m.role_id not in cascade_role_ids
    ]

    # Clean manages references
    for role in plan.roles:
        role.manages = [m for m in role.manages if m not in cascade_role_ids]

    return {
        "message": f"Department '{dept_id}' deleted",
        "cascaded_roles": len(cascade_role_ids),
    }


@router.delete("/{plan_id}/roles/{role_id}")
async def delete_role(plan_id: str, role_id: str) -> dict[str, Any]:
    """Remove a role from a draft plan.

    Cascades: removes all skills and memories for that role, and
    cleans manages references from other roles.
    """
    plan = _get_draft_plan(plan_id)

    role = next((r for r in plan.roles if r.id == role_id), None)
    if not role:
        raise HTTPException(status_code=404, detail=f"Role '{role_id}' not found")

    plan.roles = [r for r in plan.roles if r.id != role_id]
    skills_removed = len([s for s in plan.skills if s.role_id == role_id])
    plan.skills = [s for s in plan.skills if s.role_id != role_id]
    plan.memories = [m for m in plan.memories if m.role_id != role_id]

    # Clean manages references
    for r in plan.roles:
        r.manages = [m for m in r.manages if m != role_id]

    return {
        "message": f"Role '{role_id}' deleted",
        "cascaded_skills": skills_removed,
    }


@router.delete("/{plan_id}/skills/{skill_id}")
async def delete_skill(plan_id: str, skill_id: str) -> dict[str, Any]:
    """Remove a skill from a draft plan."""
    plan = _get_draft_plan(plan_id)

    skill = next((s for s in plan.skills if s.id == skill_id), None)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")

    plan.skills = [s for s in plan.skills if s.id != skill_id]

    return {"message": f"Skill '{skill_id}' deleted"}


# --- POST endpoints (add new entities) ---


@router.post("/{plan_id}/departments")
async def add_department(
    plan_id: str, body: NewDepartmentRequest
) -> dict[str, Any]:
    """Add a new department to a draft plan."""
    plan = _get_draft_plan(plan_id)

    # Check for duplicate ID
    if any(d.id == body.id for d in plan.departments):
        raise HTTPException(
            status_code=409, detail=f"Department '{body.id}' already exists in plan"
        )

    dept = ExtractedDepartment(
        id=body.id,
        name=body.name,
        description=body.description,
        context=body.context,
    )
    plan.departments.append(dept)

    return {
        "message": f"Department '{body.id}' added",
        "summary": plan.summary(),
    }


@router.post("/{plan_id}/roles")
async def add_role(plan_id: str, body: NewRoleRequest) -> dict[str, Any]:
    """Add a new role to a draft plan."""
    plan = _get_draft_plan(plan_id)

    # Check for duplicate ID
    if any(r.id == body.id for r in plan.roles):
        raise HTTPException(
            status_code=409, detail=f"Role '{body.id}' already exists in plan"
        )

    # Validate department exists
    valid_dept_ids = {d.id for d in plan.departments}
    if body.department_id not in valid_dept_ids:
        raise HTTPException(
            status_code=422,
            detail=f"Department '{body.department_id}' does not exist in plan",
        )

    role = ExtractedRole(
        id=body.id,
        name=body.name,
        department_id=body.department_id,
        description=body.description,
        persona=body.persona,
    )
    plan.roles.append(role)

    return {
        "message": f"Role '{body.id}' added",
        "summary": plan.summary(),
    }


# =====================================================================
# Conversational refinement
# =====================================================================


class RefineRequest(BaseModel):
    """Request body for refining a draft plan with feedback."""

    feedback: str = Field(..., min_length=1, max_length=5000)


@router.post("/{plan_id}/refine")
async def refine_plan_endpoint(
    plan_id: str, body: RefineRequest
) -> dict[str, Any]:
    """Refine a draft plan using natural language feedback.

    Sends the current plan + feedback to Sonnet, which returns
    structured modifications (add/remove/modify operations).
    """
    plan = _get_draft_plan(plan_id)

    updated_plan, changes, cost = await refine_plan(plan, body.feedback)

    # Add LLM cost to plan total
    updated_plan.estimated_cost += cost

    return {
        "plan_id": plan.id,
        "status": plan.status,
        "changes_applied": changes,
        "summary": plan.summary(),
        "refinement_cost": f"${cost:.4f}",
    }
