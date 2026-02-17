"""RESTful API endpoints for managing agent stewardship.

Provides endpoints for assigning, releasing, querying, and auditing
stewardship — the human accountability layer for AI roles and departments.

Endpoints:
- GET  /api/stewardship/                      — List all assignments
- GET  /api/stewardship/{scope_type}/{scope_id} — Get steward for entity
- GET  /api/stewardship/user/{user_id}        — Get all entities a user stewards
- POST /api/stewardship/assign                — Assign a steward
- POST /api/stewardship/release               — Release stewardship
- GET  /api/stewardship/history/{scope_type}/{scope_id} — Audit trail
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.db import service as db_service
from src.db.session import get_db_session
from src.middleware.auth import require_api_key
from src.models.schema import AuditLog

logger = structlog.get_logger(__name__)


# ============================================================
# Pydantic request/response models
# ============================================================


class StewardAssign(BaseModel):
    scope_type: str = Field(..., pattern="^(role|department)$")
    scope_id: str = Field(..., min_length=1, max_length=100)
    user_id: str = Field(..., min_length=1, max_length=255)


class StewardRelease(BaseModel):
    scope_type: str = Field(..., pattern="^(role|department)$")
    scope_id: str = Field(..., min_length=1, max_length=100)


class StewardResponse(BaseModel):
    scope_type: str
    scope_id: str
    steward_user_id: str
    name: str


class StewardHistoryEntry(BaseModel):
    operation: str
    changes: dict
    created_by: str
    created_at: str | None = None


# ============================================================
# Router
# ============================================================

router = APIRouter(
    prefix="/api/stewardship",
    tags=["stewardship"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/", response_model=list[StewardResponse])
async def list_stewardships():
    """List all active stewardship assignments."""
    async with get_db_session() as session:
        assignments = await db_service.list_stewardships(session)
    return assignments


@router.get("/user/{user_id}", response_model=list[StewardResponse])
async def get_steward_roles(user_id: str):
    """Get all roles and departments a user is steward of."""
    async with get_db_session() as session:
        assignments = await db_service.get_steward_roles(session, user_id)
    # StewardResponse expects steward_user_id; add it
    for a in assignments:
        a["steward_user_id"] = user_id
    return assignments


@router.get("/{scope_type}/{scope_id}")
async def get_steward(scope_type: str, scope_id: str):
    """Get the steward for a specific role or department."""
    if scope_type not in ("role", "department"):
        raise HTTPException(status_code=422, detail="scope_type must be 'role' or 'department'")

    async with get_db_session() as session:
        if scope_type == "role":
            steward = await db_service.get_steward_for_role(session, scope_id)
        else:
            steward = await db_service.get_steward_for_department(session, scope_id)

    if steward is None:
        raise HTTPException(status_code=404, detail=f"No steward found for {scope_type}:{scope_id}")

    return {"scope_type": scope_type, "scope_id": scope_id, "steward_user_id": steward}


@router.post("/assign", status_code=200)
async def assign_steward(body: StewardAssign):
    """Assign a steward to a role or department."""
    async with get_db_session() as session:
        ok = await db_service.assign_steward(
            session,
            scope_type=body.scope_type,
            scope_id=body.scope_id,
            user_id=body.user_id,
            assigned_by="api",
        )
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"{body.scope_type} '{body.scope_id}' not found in org chart DB",
        )
    return {
        "ok": True,
        "scope_type": body.scope_type,
        "scope_id": body.scope_id,
        "steward_user_id": body.user_id,
    }


@router.post("/release", status_code=200)
async def release_steward(body: StewardRelease):
    """Release stewardship from a role or department."""
    async with get_db_session() as session:
        ok = await db_service.release_steward(
            session,
            scope_type=body.scope_type,
            scope_id=body.scope_id,
            released_by="api",
        )
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"{body.scope_type} '{body.scope_id}' not found in org chart DB",
        )
    return {"ok": True, "scope_type": body.scope_type, "scope_id": body.scope_id}


@router.get("/history/{scope_type}/{scope_id}", response_model=list[StewardHistoryEntry])
async def get_steward_history(scope_type: str, scope_id: str, limit: int = 50):
    """Get the stewardship audit trail for an entity."""
    if scope_type not in ("role", "department"):
        raise HTTPException(status_code=422, detail="scope_type must be 'role' or 'department'")

    async with get_db_session() as session:
        stmt = (
            select(AuditLog)
            .where(
                AuditLog.event_type == "org_chart_change",
                AuditLog.event_data["entity_type"].as_string() == scope_type,
                AuditLog.event_data["entity_id"].as_string() == scope_id,
                AuditLog.event_data["operation"]
                .as_string()
                .in_(["steward_assigned", "steward_released"]),
            )
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        entries = result.scalars().all()

    return [
        {
            "operation": e.event_data.get("operation", ""),
            "changes": e.event_data.get("changes", {}),
            "created_by": e.user_id,
            "created_at": str(e.created_at) if e.created_at else None,
        }
        for e in entries
    ]
