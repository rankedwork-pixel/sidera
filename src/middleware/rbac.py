"""Role-based access control (RBAC) for Sidera.

Three roles, three permission levels:

    ADMIN    — full access (manage users, org chart, approve, view)
    APPROVER — approve/reject actions, view data, run skills, chat
    VIEWER   — read-only (view briefings, dashboards, audit)

Permission checks are performed by ``check_permission(user_id, action)`` which
looks up the user's role from the DB (with a short TTL cache) and compares
against the action's required role.

For Slack handlers: call ``check_slack_permission(user_id, action)`` which
returns ``(allowed: bool, message: str)``.

For FastAPI routes: use ``require_role("admin")`` as a dependency.

Unknown users (no row in ``users`` table) get the role defined by
``settings.rbac_default_role``. Set to ``"none"`` to block unknown users.

Usage:
    # In Slack handlers:
    allowed, msg = await check_slack_permission(user_id, "approve")
    if not allowed:
        return say(msg)

    # In FastAPI routes:
    @router.post("/api/org/departments", dependencies=[Depends(require_role("admin"))])
"""

from __future__ import annotations

import time

import structlog
from fastapi import HTTPException

from src.config import settings

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Permission matrix: action → minimum required role
# ---------------------------------------------------------------------------

# Role hierarchy: admin > approver > viewer
_ROLE_HIERARCHY: dict[str, int] = {
    "admin": 3,
    "approver": 2,
    "viewer": 1,
}

# What role is needed for each action category
_ACTION_PERMISSIONS: dict[str, str] = {
    # Admin-only
    "manage_users": "admin",
    "manage_org_chart": "admin",
    "update_settings": "admin",
    "delete_data": "admin",
    # Approver (includes admin)
    "approve": "approver",
    "reject": "approver",
    "run_skill": "approver",
    "run_briefing": "approver",
    "chat": "approver",
    "propose_action": "approver",
    # Viewer (includes approver + admin)
    "view": "viewer",
    "list": "viewer",
    "search": "viewer",
}


# ---------------------------------------------------------------------------
# In-memory role cache (TTL = 60s, avoids DB hit on every Slack message)
# ---------------------------------------------------------------------------

_role_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 60.0  # seconds


def _cache_get(user_id: str) -> str | None:
    """Get cached role for user, or None if expired/missing."""
    entry = _role_cache.get(user_id)
    if entry is None:
        return None
    role, ts = entry
    if time.monotonic() - ts > _CACHE_TTL:
        del _role_cache[user_id]
        return None
    return role


def _cache_set(user_id: str, role: str) -> None:
    """Cache a user's role."""
    _role_cache[user_id] = (role, time.monotonic())


def clear_role_cache(user_id: str | None = None) -> None:
    """Clear the role cache. If user_id given, clear only that user."""
    if user_id:
        _role_cache.pop(user_id, None)
    else:
        _role_cache.clear()


# ---------------------------------------------------------------------------
# Core permission check
# ---------------------------------------------------------------------------


async def resolve_user_role(user_id: str) -> str:
    """Resolve a user's effective role.

    1. Check in-memory cache (60s TTL)
    2. Query DB for user row
    3. Fall back to settings.rbac_default_role for unknown users

    Returns role string: "admin", "approver", "viewer", or "none".
    """
    # 1. Cache check
    cached = _cache_get(user_id)
    if cached is not None:
        return cached

    # 2. DB lookup
    role: str | None = None
    try:
        from src.db import service as db_service
        from src.db.session import get_db_session

        async with get_db_session() as session:
            role = await db_service.get_user_role(session, user_id)
    except Exception as exc:
        logger.warning("rbac.db_lookup_failed", user_id=user_id, error=str(exc))
        # DB down — fall through to default role (graceful degradation)

    # 3. Default role for unknown users
    if role is None:
        role = settings.rbac_default_role
        logger.debug("rbac.default_role_applied", user_id=user_id, role=role)

    _cache_set(user_id, role)
    return role


def has_permission(user_role: str, action: str) -> bool:
    """Check if a role has permission for an action.

    Args:
        user_role: The user's role ("admin", "approver", "viewer", "none").
        action: The action to check (key from _ACTION_PERMISSIONS).

    Returns:
        True if the user's role meets or exceeds the required role.
    """
    if user_role == "none":
        return False

    required_role = _ACTION_PERMISSIONS.get(action)
    if required_role is None:
        # Unknown action — default to admin-only for safety
        logger.warning("rbac.unknown_action", action=action)
        required_role = "admin"

    user_level = _ROLE_HIERARCHY.get(user_role, 0)
    required_level = _ROLE_HIERARCHY.get(required_role, 3)
    return user_level >= required_level


async def check_permission(user_id: str, action: str) -> bool:
    """Full permission check: resolve role, then check action permission.

    Returns True if allowed, False if denied.
    """
    role = await resolve_user_role(user_id)
    return has_permission(role, action)


# ---------------------------------------------------------------------------
# Slack-friendly wrapper
# ---------------------------------------------------------------------------


async def check_slack_permission(user_id: str, action: str) -> tuple[bool, str]:
    """Check permission and return a Slack-friendly result.

    Returns:
        (True, "") if allowed.
        (False, message) with a user-facing denial message if denied.
    """
    role = await resolve_user_role(user_id)
    if has_permission(role, action):
        return True, ""

    required = _ACTION_PERMISSIONS.get(action, "admin")
    msg = (
        f":lock: You don't have permission for this action. "
        f"Your role is *{role}*, but *{required}* access is required. "
        f"Ask an admin to upgrade your role with `/sidera users set-role @you {required}`."
    )
    return False, msg


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


def require_role(minimum_role: str):
    """FastAPI dependency that requires a minimum RBAC role.

    Usage:
        @router.post("/endpoint", dependencies=[Depends(require_role("admin"))])

    The user_id is extracted from the X-User-ID header (set by the API
    gateway or test harness). For Slack routes, use check_slack_permission
    instead.
    """

    async def _dependency(
        x_user_id: str | None = None,
    ) -> str:
        # In dev mode without RBAC config, allow through
        if not settings.rbac_default_role or settings.rbac_default_role == "admin":
            if not settings.is_production:
                return "admin"

        if not x_user_id:
            raise HTTPException(
                status_code=401,
                detail="X-User-ID header required for this endpoint.",
            )

        role = await resolve_user_role(x_user_id)
        if not has_permission(role, _role_to_action(minimum_role)):
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient permissions. Required: {minimum_role}, your role: {role}.",
            )
        return role

    return _dependency


def _role_to_action(role: str) -> str:
    """Map a minimum role to a representative action for permission checking."""
    if role == "admin":
        return "manage_users"
    elif role == "approver":
        return "approve"
    return "view"


# ---------------------------------------------------------------------------
# Clearance hierarchy (orthogonal to RBAC roles)
# ---------------------------------------------------------------------------

_CLEARANCE_HIERARCHY: dict[str, int] = {
    "public": 1,
    "internal": 2,
    "confidential": 3,
    "restricted": 4,
}

# Separate cache for clearance (keyed differently from role cache)
_clearance_cache: dict[str, tuple[str, float]] = {}


def _clearance_cache_get(user_id: str) -> str | None:
    """Get cached clearance for user, or None if expired/missing."""
    entry = _clearance_cache.get(user_id)
    if entry is None:
        return None
    clearance, ts = entry
    if time.monotonic() - ts > _CACHE_TTL:
        del _clearance_cache[user_id]
        return None
    return clearance


def _clearance_cache_set(user_id: str, clearance: str) -> None:
    """Cache a user's clearance level."""
    _clearance_cache[user_id] = (clearance, time.monotonic())


def clear_clearance_cache(user_id: str | None = None) -> None:
    """Clear the clearance cache. If user_id given, clear only that user."""
    if user_id:
        _clearance_cache.pop(user_id, None)
    else:
        _clearance_cache.clear()


async def resolve_user_clearance(user_id: str) -> str:
    """Resolve a user's information clearance level.

    1. Check in-memory cache (60s TTL)
    2. Query DB for user row
    3. Fall back to settings.rbac_default_clearance for unknown users

    Returns clearance string: "public", "internal", "confidential", or "restricted".
    """
    cached = _clearance_cache_get(user_id)
    if cached is not None:
        return cached

    clearance: str | None = None
    try:
        from src.db import service as db_service
        from src.db.session import get_db_session

        async with get_db_session() as session:
            clearance = await db_service.get_user_clearance(session, user_id)
    except Exception as exc:
        logger.warning("rbac.clearance_lookup_failed", user_id=user_id, error=str(exc))

    if clearance is None:
        clearance = settings.rbac_default_clearance
        logger.debug("rbac.default_clearance_applied", user_id=user_id, clearance=clearance)

    _clearance_cache_set(user_id, clearance)
    return clearance


def has_clearance(user_clearance: str, required_clearance: str) -> bool:
    """Check if a user's clearance meets or exceeds the required level.

    Args:
        user_clearance: The user's clearance ("public", "internal", etc.).
        required_clearance: The minimum clearance needed.

    Returns:
        True if the user's clearance level is >= the required level.
    """
    user_level = _CLEARANCE_HIERARCHY.get(user_clearance, 0)
    required_level = _CLEARANCE_HIERARCHY.get(required_clearance, 4)
    return user_level >= required_level


async def check_clearance(user_id: str, required_clearance: str) -> bool:
    """Full clearance check: resolve clearance, then compare.

    Returns True if the user has sufficient clearance, False otherwise.
    """
    clearance = await resolve_user_clearance(user_id)
    return has_clearance(clearance, required_clearance)


async def check_slack_clearance(user_id: str, required_clearance: str) -> tuple[bool, str]:
    """Check clearance and return a Slack-friendly result.

    Returns:
        (True, "") if the user has sufficient clearance.
        (False, message) with a user-facing denial message if not.
    """
    clearance = await resolve_user_clearance(user_id)
    if has_clearance(clearance, required_clearance):
        return True, ""

    msg = (
        f":closed_lock_with_key: This information requires *{required_clearance}* "
        f"clearance, but your level is *{clearance}*. "
        f"Ask an admin to update your clearance with "
        f"`/sidera users set-clearance @you {required_clearance}`."
    )
    return False, msg
