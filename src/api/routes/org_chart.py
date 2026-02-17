"""RESTful API endpoints for managing the dynamic org chart.

Provides CRUD operations for departments, roles, and skills stored
in the database. These DB-backed definitions supplement (or override)
the YAML-on-disk skill library, allowing runtime configuration of the
agent's organizational structure without redeploying code.

Endpoints:
- /api/org/departments  — CRUD for departments
- /api/org/roles        — CRUD for roles (filterable by department)
- /api/org/skills       — CRUD for skills (filterable by role/department)
- /api/org/history      — Audit trail for org chart changes
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from src.db import service as db_service
from src.db.session import get_db_session
from src.middleware.auth import require_api_key

logger = structlog.get_logger(__name__)


# ============================================================
# Pydantic request/response models — Departments
# ============================================================


class DepartmentCreate(BaseModel):
    dept_id: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    context: str = ""
    context_text: str = ""
    steward_user_id: str = ""


class DepartmentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    context: str | None = None
    context_text: str | None = None
    steward_user_id: str | None = None


class DepartmentResponse(BaseModel):
    dept_id: str
    name: str
    description: str
    context: str
    context_text: str
    steward_user_id: str = ""
    is_active: bool
    created_by: str
    created_at: str | None = None
    updated_at: str | None = None

    class Config:
        from_attributes = True


# ============================================================
# Pydantic request/response models — Roles
# ============================================================


class RoleCreate(BaseModel):
    role_id: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    department_id: str = Field(..., min_length=1, max_length=100)
    description: str = ""
    persona: str = ""
    connectors: list[str] = []
    briefing_skills: list[str] = []
    schedule: str | None = None
    context_text: str = ""
    principles: list[str] = []
    manages: list[str] = []
    delegation_model: str = "standard"
    synthesis_prompt: str = ""
    clearance_level: str = "internal"
    steward_user_id: str = ""
    learning_channels: list[str] = []
    document_sync: list[dict[str, str]] = []


class RoleUpdate(BaseModel):
    name: str | None = None
    department_id: str | None = None
    description: str | None = None
    persona: str | None = None
    connectors: list[str] | None = None
    briefing_skills: list[str] | None = None
    schedule: str | None = None
    context_text: str | None = None
    principles: list[str] | None = None
    manages: list[str] | None = None
    delegation_model: str | None = None
    synthesis_prompt: str | None = None
    clearance_level: str | None = None
    steward_user_id: str | None = None
    learning_channels: list[str] | None = None
    document_sync: list[dict[str, str]] | None = None


class RoleResponse(BaseModel):
    role_id: str
    name: str
    department_id: str
    description: str
    persona: str
    connectors: list
    briefing_skills: list
    schedule: str | None
    context_text: str
    principles: list = []
    manages: list
    delegation_model: str
    synthesis_prompt: str
    clearance_level: str = "internal"
    steward_user_id: str = ""
    learning_channels: list = []
    document_sync: list = []
    is_active: bool
    created_by: str
    created_at: str | None = None
    updated_at: str | None = None

    class Config:
        from_attributes = True


# ============================================================
# Pydantic request/response models — Skills
# ============================================================


class SkillCreate(BaseModel):
    skill_id: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    category: str = "analysis"
    system_supplement: str = ""
    prompt_template: str = ""
    output_format: str = ""
    business_guidance: str = ""
    platforms: list[str] = []
    tags: list[str] = []
    tools_required: list[str] = []
    model: str = "sonnet"
    max_turns: int = 20
    context_text: str = ""
    schedule: str | None = None
    chain_after: str | None = None
    requires_approval: bool = True
    min_clearance: str = "public"
    department_id: str = ""
    role_id: str = ""
    author: str = "sidera"


class SkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    system_supplement: str | None = None
    prompt_template: str | None = None
    output_format: str | None = None
    business_guidance: str | None = None
    platforms: list[str] | None = None
    tags: list[str] | None = None
    tools_required: list[str] | None = None
    model: str | None = None
    max_turns: int | None = None
    context_text: str | None = None
    schedule: str | None = None
    chain_after: str | None = None
    requires_approval: bool | None = None
    min_clearance: str | None = None
    department_id: str | None = None
    role_id: str | None = None
    author: str | None = None


class SkillResponse(BaseModel):
    skill_id: str
    name: str
    description: str
    category: str
    system_supplement: str
    prompt_template: str
    output_format: str
    business_guidance: str
    platforms: list
    tags: list
    tools_required: list
    model: str
    max_turns: int
    context_text: str
    schedule: str | None
    chain_after: str | None
    requires_approval: bool
    min_clearance: str = "public"
    department_id: str
    role_id: str
    author: str
    is_active: bool
    created_by: str
    created_at: str | None = None
    updated_at: str | None = None

    class Config:
        from_attributes = True


# ============================================================
# Router
# ============================================================

router = APIRouter(
    prefix="/api/org",
    tags=["org-chart"],
    dependencies=[Depends(require_api_key)],
)


# ============================================================
# Helpers — ORM model to response dict
# ============================================================


def _dept_to_response(dept) -> dict:
    return {
        "dept_id": dept.dept_id,
        "name": dept.name,
        "description": dept.description or "",
        "context": dept.context or "",
        "context_text": dept.context_text or "",
        "steward_user_id": dept.steward_user_id or "",
        "is_active": dept.is_active,
        "created_by": dept.created_by or "",
        "created_at": str(dept.created_at) if dept.created_at else None,
        "updated_at": str(dept.updated_at) if dept.updated_at else None,
    }


def _role_to_response(role) -> dict:
    return {
        "role_id": role.role_id,
        "name": role.name,
        "department_id": role.department_id,
        "description": role.description or "",
        "persona": role.persona or "",
        "connectors": role.connectors or [],
        "briefing_skills": role.briefing_skills or [],
        "schedule": role.schedule,
        "context_text": role.context_text or "",
        "manages": role.manages or [],
        "delegation_model": role.delegation_model or "standard",
        "synthesis_prompt": role.synthesis_prompt or "",
        "steward_user_id": role.steward_user_id or "",
        "is_active": role.is_active,
        "created_by": role.created_by or "",
        "created_at": str(role.created_at) if role.created_at else None,
        "updated_at": str(role.updated_at) if role.updated_at else None,
    }


def _skill_to_response(skill) -> dict:
    return {
        "skill_id": skill.skill_id,
        "name": skill.name,
        "description": skill.description or "",
        "category": skill.category or "",
        "system_supplement": skill.system_supplement or "",
        "prompt_template": skill.prompt_template or "",
        "output_format": skill.output_format or "",
        "business_guidance": skill.business_guidance or "",
        "platforms": skill.platforms or [],
        "tags": skill.tags or [],
        "tools_required": skill.tools_required or [],
        "model": skill.model or "sonnet",
        "max_turns": skill.max_turns or 20,
        "context_text": skill.context_text or "",
        "schedule": skill.schedule,
        "chain_after": skill.chain_after,
        "requires_approval": (
            skill.requires_approval if skill.requires_approval is not None else True
        ),
        "department_id": skill.department_id or "",
        "role_id": skill.role_id or "",
        "author": skill.author or "sidera",
        "is_active": skill.is_active,
        "created_by": skill.created_by or "",
        "created_at": str(skill.created_at) if skill.created_at else None,
        "updated_at": str(skill.updated_at) if skill.updated_at else None,
    }


# ============================================================
# Department endpoints
# ============================================================


@router.get("/departments")
async def list_departments(active_only: bool = True):
    """List all dynamic departments."""
    async with get_db_session() as session:
        depts = await db_service.list_org_departments(session, active_only=active_only)
        return [_dept_to_response(d) for d in depts]


@router.get("/departments/{dept_id}")
async def get_department(dept_id: str):
    """Get a single department by ID."""
    async with get_db_session() as session:
        dept = await db_service.get_org_department(session, dept_id)
        if dept is None:
            raise HTTPException(status_code=404, detail=f"Department '{dept_id}' not found")
        return _dept_to_response(dept)


@router.post("/departments", status_code=201)
async def create_department(body: DepartmentCreate):
    """Create a new department."""
    try:
        async with get_db_session() as session:
            dept = await db_service.create_org_department(
                session,
                dept_id=body.dept_id,
                name=body.name,
                description=body.description,
                context=body.context,
                context_text=body.context_text,
                created_by="api",
            )
            return _dept_to_response(dept)
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f"Department '{body.dept_id}' already exists",
        )


@router.put("/departments/{dept_id}")
async def update_department(dept_id: str, body: DepartmentUpdate):
    """Update an existing department."""
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")

    async with get_db_session() as session:
        dept = await db_service.update_org_department(session, dept_id, **updates)
        if dept is None:
            raise HTTPException(status_code=404, detail=f"Department '{dept_id}' not found")
        return _dept_to_response(dept)


@router.delete("/departments/{dept_id}", status_code=204)
async def delete_department(dept_id: str):
    """Soft-delete a department (set is_active=False)."""
    async with get_db_session() as session:
        dept = await db_service.delete_org_department(session, dept_id)
        if dept is None:
            raise HTTPException(status_code=404, detail=f"Department '{dept_id}' not found")
    return None


# ============================================================
# Role endpoints
# ============================================================


@router.get("/roles")
async def list_roles(
    department_id: str | None = None,
    active_only: bool = True,
):
    """List all dynamic roles, optionally filtered by department."""
    async with get_db_session() as session:
        roles = await db_service.list_org_roles(
            session,
            department_id=department_id,
            active_only=active_only,
        )
        return [_role_to_response(r) for r in roles]


@router.get("/roles/{role_id}")
async def get_role(role_id: str):
    """Get a single role by ID."""
    async with get_db_session() as session:
        role = await db_service.get_org_role(session, role_id)
        if role is None:
            raise HTTPException(status_code=404, detail=f"Role '{role_id}' not found")
        return _role_to_response(role)


@router.post("/roles", status_code=201)
async def create_role(body: RoleCreate):
    """Create a new role."""
    try:
        async with get_db_session() as session:
            role = await db_service.create_org_role(
                session,
                role_id=body.role_id,
                name=body.name,
                department_id=body.department_id,
                description=body.description,
                persona=body.persona,
                connectors=body.connectors,
                briefing_skills=body.briefing_skills,
                schedule=body.schedule,
                context_text=body.context_text,
                manages=body.manages,
                delegation_model=body.delegation_model,
                synthesis_prompt=body.synthesis_prompt,
                created_by="api",
            )
            return _role_to_response(role)
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f"Role '{body.role_id}' already exists",
        )


@router.put("/roles/{role_id}")
async def update_role(role_id: str, body: RoleUpdate):
    """Update an existing role."""
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")

    async with get_db_session() as session:
        role = await db_service.update_org_role(session, role_id, **updates)
        if role is None:
            raise HTTPException(status_code=404, detail=f"Role '{role_id}' not found")
        return _role_to_response(role)


@router.delete("/roles/{role_id}", status_code=204)
async def delete_role(role_id: str):
    """Soft-delete a role (set is_active=False)."""
    async with get_db_session() as session:
        role = await db_service.delete_org_role(session, role_id)
        if role is None:
            raise HTTPException(status_code=404, detail=f"Role '{role_id}' not found")
    return None


# ============================================================
# Skill endpoints
# ============================================================


@router.get("/skills")
async def list_skills(
    role_id: str | None = None,
    department_id: str | None = None,
    active_only: bool = True,
):
    """List all dynamic skills, optionally filtered by role and/or department."""
    async with get_db_session() as session:
        skills = await db_service.list_org_skills(
            session,
            role_id=role_id,
            department_id=department_id,
            active_only=active_only,
        )
        return [_skill_to_response(s) for s in skills]


@router.get("/skills/{skill_id}")
async def get_skill(skill_id: str):
    """Get a single skill by ID."""
    async with get_db_session() as session:
        skill = await db_service.get_org_skill(session, skill_id)
        if skill is None:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
        return _skill_to_response(skill)


@router.post("/skills", status_code=201)
async def create_skill(body: SkillCreate):
    """Create a new skill."""
    try:
        async with get_db_session() as session:
            skill = await db_service.create_org_skill(
                session,
                skill_id=body.skill_id,
                name=body.name,
                description=body.description,
                category=body.category,
                system_supplement=body.system_supplement,
                prompt_template=body.prompt_template,
                output_format=body.output_format,
                business_guidance=body.business_guidance,
                platforms=body.platforms,
                tags=body.tags,
                tools_required=body.tools_required,
                model=body.model,
                max_turns=body.max_turns,
                context_text=body.context_text,
                schedule=body.schedule,
                chain_after=body.chain_after,
                requires_approval=body.requires_approval,
                department_id=body.department_id,
                role_id=body.role_id,
                author=body.author,
                created_by="api",
            )
            return _skill_to_response(skill)
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f"Skill '{body.skill_id}' already exists",
        )


@router.put("/skills/{skill_id}")
async def update_skill(skill_id: str, body: SkillUpdate):
    """Update an existing skill."""
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")

    async with get_db_session() as session:
        skill = await db_service.update_org_skill(session, skill_id, **updates)
        if skill is None:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
        return _skill_to_response(skill)


@router.delete("/skills/{skill_id}", status_code=204)
async def delete_skill(skill_id: str):
    """Soft-delete a skill (set is_active=False)."""
    async with get_db_session() as session:
        skill = await db_service.delete_org_skill(session, skill_id)
        if skill is None:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
    return None


# ============================================================
# History / Audit trail endpoint
# ============================================================


@router.get("/history/{entity_type}/{entity_id}")
async def get_history(entity_type: str, entity_id: str, limit: int = 20):
    """Get the audit trail for an org chart entity.

    Args:
        entity_type: One of "department", "role", or "skill".
        entity_id: The entity's unique identifier (dept_id, role_id, or skill_id).
        limit: Maximum number of entries to return (default 20).

    Returns:
        List of audit log entries filtered to the requested entity.
    """
    valid_types = ("department", "role", "skill")
    if entity_type not in valid_types:
        raise HTTPException(
            status_code=422,
            detail=f"entity_type must be one of: {', '.join(valid_types)}",
        )

    async with get_db_session() as session:
        # Fetch org_chart_change audit entries (use "system" as user
        # since org chart changes are logged under the created_by user
        # or "system").
        entries = await db_service.get_audit_trail(
            session,
            user_id="system",
            event_type="org_chart_change",
            limit=limit * 3,  # over-fetch since we filter in Python
        )
        # Also fetch entries from "api" user (create endpoints use "api")
        entries_api = await db_service.get_audit_trail(
            session,
            user_id="api",
            event_type="org_chart_change",
            limit=limit * 3,
        )
        all_entries = entries + entries_api
        # Sort by created_at descending
        all_entries.sort(
            key=lambda e: e.created_at if e.created_at else "",
            reverse=True,
        )

        filtered = [
            {
                "id": e.id,
                "event_type": e.event_type,
                "event_data": e.event_data,
                "source": e.source,
                "user_id": e.user_id,
                "created_at": str(e.created_at) if e.created_at else None,
            }
            for e in all_entries
            if e.event_data
            and e.event_data.get("entity_type") == entity_type
            and e.event_data.get("entity_id") == entity_id
        ]
        return filtered[:limit]
