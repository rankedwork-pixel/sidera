"""GDPR data export and deletion endpoints.

Provides Article 15 (right of access / data export) and Article 17
(right to erasure / data deletion) compliance endpoints.

Both endpoints require ``admin`` RBAC role for access.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from src.config import settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/gdpr", tags=["gdpr"])


def _require_admin(request: Request) -> str:
    """Verify the request comes from an admin user.

    For production, checks the API key and maps to a user with admin role.
    For development, checks the X-User-Role header.
    """
    # Production: require API key
    if settings.is_production:
        api_key = request.headers.get("X-API-Key", "")
        if not api_key or api_key != settings.api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")

    # Check role header (set by auth middleware or directly in dev)
    role = request.headers.get("X-User-Role", "")
    if role != "admin":
        raise HTTPException(
            status_code=403,
            detail="GDPR operations require admin role",
        )

    return request.headers.get("X-User-ID", "admin")


@router.get("/export/{user_id}")
async def export_user_data(
    user_id: str,
    admin_id: str = Depends(_require_admin),
):
    """Export all data associated with a user (GDPR Article 15).

    Returns a JSON object with all user data across tables:
    user record, accounts, audit log, approvals, conversation threads.
    """
    from src.db import service as db_service
    from src.db.session import get_db_session

    try:
        async with get_db_session() as session:
            data = await db_service.export_user_data(session, user_id)

        logger.info(
            "gdpr.export_requested",
            user_id=user_id,
            requested_by=admin_id,
        )
        return {
            "status": "success",
            "user_id": user_id,
            "data": data,
        }
    except Exception as exc:
        logger.error("gdpr.export_failed", user_id=user_id, error=str(exc))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to export user data: {exc}",
        ) from exc


@router.delete("/delete/{user_id}")
async def delete_user_data(
    user_id: str,
    admin_id: str = Depends(_require_admin),
):
    """Hard-delete user data and anonymize audit trail (GDPR Article 17).

    This is an irreversible operation. Deletes:
    - User record
    - Accounts (and cascaded data)
    - Approval queue items
    - Conversation threads

    Anonymizes (keeps for compliance):
    - Audit log entries (user_id set to 'deleted')
    """
    from src.db import service as db_service
    from src.db.session import get_db_session

    try:
        async with get_db_session() as session:
            counts = await db_service.delete_user_data(session, user_id)
            await session.commit()

        logger.info(
            "gdpr.delete_completed",
            user_id=user_id,
            requested_by=admin_id,
            counts=counts,
        )
        return {
            "status": "deleted",
            "user_id": user_id,
            "counts": counts,
        }
    except Exception as exc:
        logger.error("gdpr.delete_failed", user_id=user_id, error=str(exc))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete user data: {exc}",
        ) from exc
