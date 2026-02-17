"""__Channel__ OAuth2 routes for Sidera.

Handles the full OAuth2 flow for connecting __Channel__ ad accounts:
1. /authorize — Redirect user to __Channel__ login
2. /callback — Exchange authorization code for access token
3. /refresh — Refresh an expired access token
4. /status — Verify a __Channel__ connection is active

TODO: Update endpoints, scopes, and token exchange logic for __Channel__'s
specific OAuth implementation.
"""

import secrets
import time
from typing import Optional
from urllib.parse import urlencode

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from src.cache.service import build_cache_key, cache_delete, cache_get, cache_set
from src.config import settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(
    prefix="/oauth/__CHANNEL__",
    tags=["__Channel__ OAuth"],
)

# ---------------------------------------------------------------------------
# Constants — TODO: Replace with __Channel__'s actual endpoints
# ---------------------------------------------------------------------------

# TODO: Update these URLs for __Channel__'s OAuth2 endpoints
__CHANNEL_UPPER___AUTH_ENDPOINT = "https://www.__CHANNEL__.com/oauth/authorize"
__CHANNEL_UPPER___TOKEN_ENDPOINT = "https://api.__CHANNEL__.com/oauth/token"
__CHANNEL_UPPER___API_BASE = "https://api.__CHANNEL__.com/v1"

# TODO: Set the required OAuth scopes for __Channel__
__CHANNEL_UPPER___OAUTH_SCOPES = "ads.read,accounts.read"

# ---------------------------------------------------------------------------
# OAuth state storage — Redis-backed with in-memory fallback
# ---------------------------------------------------------------------------

_pending_states: dict[str, dict] = {}
_STATE_TTL_SECONDS = 600


async def _save_oauth_state(state: str, data: dict) -> None:
    """Persist an OAuth state token to Redis (and in-memory fallback)."""
    _pending_states[state] = data
    try:
        key = build_cache_key("oauth_state", "__CHANNEL__", state=state)
        await cache_set(key, data, ttl_seconds=_STATE_TTL_SECONDS)
    except Exception:
        logger.debug("oauth.__CHANNEL__.state.redis_save_failed", state_prefix=state[:8])


async def _get_oauth_state(state: str) -> dict | None:
    """Retrieve an OAuth state token. Tries Redis first, falls back to in-memory."""
    try:
        key = build_cache_key("oauth_state", "__CHANNEL__", state=state)
        cached = await cache_get(key)
        if cached is not None:
            await cache_delete(key)
            _pending_states.pop(state, None)
            return cached
    except Exception:
        logger.debug("oauth.__CHANNEL__.state.redis_get_failed", state_prefix=state[:8])

    return _pending_states.pop(state, None)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class __Channel__TokenResponse(BaseModel):
    """Successful token exchange response."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = 0
    refresh_token: Optional[str] = None


class __Channel__RefreshRequest(BaseModel):
    """Request body for the token refresh endpoint."""

    refresh_token: str = Field(
        ...,
        description="The refresh token obtained during initial authorization.",
    )


class __Channel__ConnectionStatus(BaseModel):
    """Response for the connection status check."""

    connected: bool
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    ad_accounts_count: Optional[int] = None
    error: Optional[str] = None


class __Channel__OAuthError(BaseModel):
    """Structured error response for OAuth failures."""

    error: str
    error_description: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_redirect_uri() -> str:
    """Construct the OAuth callback URI from the configured base URL."""
    return f"{settings.app_base_url}/oauth/__CHANNEL__/callback"


def _purge_expired_states() -> None:
    """Remove state tokens older than the TTL."""
    now = time.time()
    expired = [
        token
        for token, meta in _pending_states.items()
        if now - meta["created_at"] > _STATE_TTL_SECONDS
    ]
    for token in expired:
        _pending_states.pop(token, None)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/authorize", summary="Initiate __Channel__ OAuth flow")
async def authorize() -> RedirectResponse:
    """Redirect the user to __Channel__ login."""
    _purge_expired_states()

    state = secrets.token_urlsafe(32)
    state_data = {"created_at": time.time()}
    await _save_oauth_state(state, state_data)

    params = {
        # TODO: Update these parameters for __Channel__'s OAuth
        "client_id": settings.__CHANNEL___client_id,
        "redirect_uri": _build_redirect_uri(),
        "response_type": "code",
        "scope": __CHANNEL_UPPER___OAUTH_SCOPES,
        "state": state,
    }

    auth_url = f"{__CHANNEL_UPPER___AUTH_ENDPOINT}?{urlencode(params)}"

    logger.info(
        "oauth.__CHANNEL__.authorize.redirect",
        redirect_uri=_build_redirect_uri(),
        state_prefix=state[:8],
    )

    return RedirectResponse(url=auth_url, status_code=302)


@router.get(
    "/callback",
    response_model=__Channel__TokenResponse,
    responses={400: {"model": __Channel__OAuthError}, 502: {"model": __Channel__OAuthError}},
    summary="Handle OAuth callback from __Channel__",
)
async def callback(
    code: Optional[str] = Query(None, description="Authorization code"),
    state: Optional[str] = Query(None, description="CSRF state token"),
    error: Optional[str] = Query(None, description="Error code if user denied access"),
    error_description: Optional[str] = Query(None, description="Error description"),
) -> __Channel__TokenResponse:
    """Exchange the authorization code for an access token."""
    # Handle errors from the platform
    if error:
        logger.warning(
            "oauth.__CHANNEL__.callback.error",
            error=error,
            description=error_description,
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": error,
                "error_description": error_description or "Authorization denied.",
            },
        )

    if not code or not state:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_request",
                "error_description": "Missing code or state parameter.",
            },
        )

    # Validate CSRF state
    stored = await _get_oauth_state(state)
    if stored is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_state",
                "error_description": "State token is invalid or expired.",
            },
        )

    elapsed = time.time() - stored["created_at"]
    if elapsed > _STATE_TTL_SECONDS:
        raise HTTPException(
            status_code=400,
            detail={"error": "expired_state", "error_description": "State token expired."},
        )

    # Exchange code for token
    token_params = {
        # TODO: Update these parameters for __Channel__'s token exchange
        "grant_type": "authorization_code",
        "client_id": settings.__CHANNEL___client_id,
        "client_secret": settings.__CHANNEL___client_secret,
        "redirect_uri": _build_redirect_uri(),
        "code": code,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(__CHANNEL_UPPER___TOKEN_ENDPOINT, data=token_params)
        except httpx.RequestError as exc:
            logger.error("oauth.__CHANNEL__.callback.token_exchange_failed", error=str(exc))
            raise HTTPException(
                status_code=502,
                detail={"error": "token_exchange_failed", "error_description": str(exc)},
            )

    if resp.status_code != 200:
        logger.error("oauth.__CHANNEL__.callback.rejected", status=resp.status_code)
        raise HTTPException(
            status_code=502,
            detail={
                "error": "token_exchange_error",
                "error_description": f"HTTP {resp.status_code}",
            },
        )

    data = resp.json()
    logger.info("oauth.__CHANNEL__.callback.success", expires_in=data.get("expires_in"))

    return __Channel__TokenResponse(
        access_token=data["access_token"],
        token_type=data.get("token_type", "bearer"),
        expires_in=data.get("expires_in", 0),
        refresh_token=data.get("refresh_token"),
    )


@router.post(
    "/refresh",
    response_model=__Channel__TokenResponse,
    responses={400: {"model": __Channel__OAuthError}, 502: {"model": __Channel__OAuthError}},
    summary="Refresh __Channel__ access token",
)
async def refresh(body: __Channel__RefreshRequest) -> __Channel__TokenResponse:
    """Exchange a refresh token for a new access token."""
    refresh_params = {
        # TODO: Update for __Channel__'s refresh flow
        "grant_type": "refresh_token",
        "client_id": settings.__CHANNEL___client_id,
        "client_secret": settings.__CHANNEL___client_secret,
        "refresh_token": body.refresh_token,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(__CHANNEL_UPPER___TOKEN_ENDPOINT, data=refresh_params)
        except httpx.RequestError as exc:
            logger.error("oauth.__CHANNEL__.refresh.failed", error=str(exc))
            raise HTTPException(
                status_code=502,
                detail={"error": "refresh_failed", "error_description": str(exc)},
            )

    if resp.status_code != 200:
        logger.error("oauth.__CHANNEL__.refresh.rejected", status=resp.status_code)
        raise HTTPException(
            status_code=400,
            detail={"error": "refresh_error", "error_description": f"HTTP {resp.status_code}"},
        )

    data = resp.json()
    logger.info("oauth.__CHANNEL__.refresh.success", expires_in=data.get("expires_in"))

    return __Channel__TokenResponse(
        access_token=data["access_token"],
        token_type=data.get("token_type", "bearer"),
        expires_in=data.get("expires_in", 0),
        refresh_token=data.get("refresh_token"),
    )


@router.get(
    "/status",
    response_model=__Channel__ConnectionStatus,
    summary="Check __Channel__ connection status",
)
async def status(
    access_token: str = Query(..., description="__Channel__ access token to validate"),
) -> __Channel__ConnectionStatus:
    """Verify that a __Channel__ connection is active."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            # TODO: Call a lightweight endpoint to verify the token
            resp = await client.get(
                f"{__CHANNEL_UPPER___API_BASE}/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.RequestError as exc:
            logger.error("oauth.__CHANNEL__.status.request_failed", error=str(exc))
            return __Channel__ConnectionStatus(
                connected=False,
                error=f"Could not reach __Channel__ API: {exc}",
            )

        if resp.status_code != 200:
            return __Channel__ConnectionStatus(
                connected=False,
                error=f"__Channel__ API returned HTTP {resp.status_code}",
            )

        me_data = resp.json()

    logger.info("oauth.__CHANNEL__.status.connected")

    return __Channel__ConnectionStatus(
        connected=True,
        user_id=me_data.get("id"),
        user_name=me_data.get("name"),
        # TODO: Query for ad accounts count if supported
        ad_accounts_count=0,
    )
