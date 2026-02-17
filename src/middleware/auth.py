"""API key authentication for Sidera REST endpoints.

Provides a FastAPI dependency that validates an API key from the
``Authorization: Bearer <key>`` header or ``X-API-Key: <key>`` header.

In development mode (APP_ENV != production), authentication is optional —
requests without a key are allowed through. In production, every request
must carry a valid key.

Usage:
    from src.middleware.auth import require_api_key

    router = APIRouter(dependencies=[Depends(require_api_key)])
"""

from __future__ import annotations

import hmac

import structlog
from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from src.config import settings

logger = structlog.get_logger(__name__)

# Accept key from either header
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_auth_header = APIKeyHeader(name="Authorization", auto_error=False)


async def require_api_key(
    api_key: str | None = Security(_api_key_header),
    auth_header: str | None = Security(_auth_header),
) -> str | None:
    """Validate the API key from request headers.

    Accepts either:
        - ``X-API-Key: <key>``
        - ``Authorization: Bearer <key>``

    In development, missing keys are allowed (returns None).
    In production, missing or invalid keys raise 401/403.

    Returns:
        The validated API key, or None in dev mode without a key.
    """
    # Extract key from whichever header is present
    key = api_key
    if not key and auth_header:
        # Strip "Bearer " prefix if present
        if auth_header.lower().startswith("bearer "):
            key = auth_header[7:].strip()
        else:
            key = auth_header.strip()

    configured_key = settings.api_key

    # Dev mode: skip auth if no API key is configured
    if not configured_key:
        if settings.is_production:
            logger.error("api_key.not_configured_in_production")
            raise HTTPException(
                status_code=500,
                detail="Server misconfiguration: API_KEY not set",
            )
        return None  # Dev mode, no key configured — allow through

    # Key required but not provided
    if not key:
        raise HTTPException(
            status_code=401,
            detail="API key required. Use X-API-Key header or Authorization: Bearer <key>.",
        )

    # Constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(key, configured_key):
        logger.warning("api_key.invalid_attempt")
        raise HTTPException(
            status_code=403,
            detail="Invalid API key.",
        )

    return key
