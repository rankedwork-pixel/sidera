"""Meta (Facebook) OAuth2 routes for Sidera.

Handles the full OAuth2 flow for connecting Meta ad accounts:
1. /authorize — Redirect user to Facebook Login
2. /callback — Exchange authorization code for short-lived token, then
   exchange for long-lived token (~60 days)
3. /refresh — Exchange a valid long-lived token for a new one
4. /status — Verify a Meta connection is active

Meta's OAuth flow differs from Google's:
- Short-lived tokens last ~1 hour
- Long-lived tokens last ~60 days
- Long-lived tokens can be refreshed before expiry (no refresh_token concept)
- Scopes: ads_management, ads_read, read_insights
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
from src.utils.encryption import encrypt_token

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(
    prefix="/oauth/meta",
    tags=["Meta OAuth"],
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

META_AUTH_ENDPOINT = "https://www.facebook.com/v21.0/dialog/oauth"
META_TOKEN_ENDPOINT = "https://graph.facebook.com/v21.0/oauth/access_token"
META_GRAPH_API_BASE = "https://graph.facebook.com/v21.0"

# Scopes required for reading ad account data
META_OAUTH_SCOPES = "ads_management,ads_read,read_insights"

# ---------------------------------------------------------------------------
# OAuth state storage — Redis-backed with in-memory fallback
# ---------------------------------------------------------------------------

# In-memory fallback for environments without Redis.
_pending_states: dict[str, dict] = {}

# State tokens expire after 10 minutes.
_STATE_TTL_SECONDS = 600


async def _save_oauth_state(state: str, data: dict) -> None:
    """Persist an OAuth state token to Redis (and in-memory fallback)."""
    _pending_states[state] = data
    try:
        key = build_cache_key("oauth_state", "meta", state=state)
        await cache_set(key, data, ttl_seconds=_STATE_TTL_SECONDS)
    except Exception:
        logger.debug("oauth.meta.state.redis_save_failed", state_prefix=state[:8])


async def _get_oauth_state(state: str) -> dict | None:
    """Retrieve an OAuth state token. Tries Redis first, falls back to in-memory."""
    try:
        key = build_cache_key("oauth_state", "meta", state=state)
        cached = await cache_get(key)
        if cached is not None:
            # Clean up Redis (one-time use)
            await cache_delete(key)
            # Also remove from in-memory
            _pending_states.pop(state, None)
            return cached
    except Exception:
        logger.debug("oauth.meta.state.redis_get_failed", state_prefix=state[:8])

    # Fall back to in-memory
    result = _pending_states.pop(state, None)
    if result is not None:
        logger.warning(
            "oauth.state.inmemory_fallback",
            provider="meta",
            state_prefix=state[:8],
        )
    return result


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------


class MetaTokenResponse(BaseModel):
    """Successful token exchange response."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = 0


class MetaLongLivedTokenResponse(BaseModel):
    """Response after exchanging for a long-lived token."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = 0  # ~5184000 seconds (60 days)


class MetaRefreshRequest(BaseModel):
    """Request body for the token refresh endpoint."""

    access_token: str = Field(
        ...,
        description=(
            "A valid, non-expired long-lived access token. "
            "Meta does not use refresh_tokens — you exchange the "
            "current long-lived token for a new one."
        ),
    )


class MetaConnectionStatus(BaseModel):
    """Response for the connection status check."""

    connected: bool
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    ad_accounts_count: Optional[int] = None
    error: Optional[str] = None


class MetaOAuthError(BaseModel):
    """Structured error response for OAuth failures."""

    error: str
    error_description: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_redirect_uri() -> str:
    """Construct the OAuth callback URI from the configured base URL."""
    return f"{settings.app_base_url}/oauth/meta/callback"


def _purge_expired_states() -> None:
    """Remove state tokens older than the TTL. Best-effort cleanup."""
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


@router.get("/authorize", summary="Initiate Meta OAuth flow")
async def authorize() -> RedirectResponse:
    """Redirect the user to Facebook Login.

    Generates a random state token for CSRF protection, stores it
    in-memory, and builds the authorization URL for Facebook Login
    with the required ad account scopes.
    """
    _purge_expired_states()

    state = secrets.token_urlsafe(32)
    state_data = {"created_at": time.time()}
    await _save_oauth_state(state, state_data)

    params = {
        "client_id": settings.meta_app_id,
        "redirect_uri": _build_redirect_uri(),
        "response_type": "code",
        "scope": META_OAUTH_SCOPES,
        "state": state,
    }

    auth_url = f"{META_AUTH_ENDPOINT}?{urlencode(params)}"

    logger.info(
        "oauth.meta.authorize.redirect",
        redirect_uri=_build_redirect_uri(),
        state_prefix=state[:8],
    )

    return RedirectResponse(url=auth_url, status_code=302)


@router.get(
    "/callback",
    response_model=MetaLongLivedTokenResponse,
    responses={400: {"model": MetaOAuthError}, 502: {"model": MetaOAuthError}},
    summary="Handle OAuth callback from Facebook",
)
async def callback(
    code: Optional[str] = Query(None, description="Authorization code from Facebook"),
    state: Optional[str] = Query(None, description="CSRF state token"),
    error: Optional[str] = Query(None, description="Error code if user denied access"),
    error_description: Optional[str] = Query(
        None, description="Human-readable error from Facebook"
    ),
    error_reason: Optional[str] = Query(None, description="Error reason from Facebook"),
) -> MetaLongLivedTokenResponse:
    """Exchange the authorization code for a long-lived access token.

    This performs a two-step exchange:
    1. Exchange auth code for a short-lived token (~1 hour)
    2. Exchange short-lived token for a long-lived token (~60 days)

    Validates the state parameter against our in-memory store for CSRF
    protection.
    """
    # --- Handle user-denied or Facebook-side errors ---
    if error:
        logger.warning(
            "oauth.meta.callback.error_from_facebook",
            error=error,
            description=error_description,
            reason=error_reason,
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": error,
                "error_description": error_description or "Authorization denied.",
            },
        )

    if not code or not state:
        logger.warning(
            "oauth.meta.callback.missing_params",
            has_code=bool(code),
            has_state=bool(state),
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_request",
                "error_description": "Missing code or state parameter.",
            },
        )

    # --- Validate CSRF state ---
    stored = await _get_oauth_state(state)
    if stored is None:
        logger.warning("oauth.meta.callback.invalid_state", state_prefix=state[:8])
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_state",
                "error_description": (
                    "State token is invalid or expired. Please restart the OAuth flow."
                ),
            },
        )

    elapsed = time.time() - stored["created_at"]
    if elapsed > _STATE_TTL_SECONDS:
        logger.warning("oauth.meta.callback.expired_state", elapsed_seconds=elapsed)
        raise HTTPException(
            status_code=400,
            detail={
                "error": "expired_state",
                "error_description": "State token expired. Please restart the OAuth flow.",
            },
        )

    # --- Step 1: Exchange code for short-lived token ---
    token_params = {
        "client_id": settings.meta_app_id,
        "client_secret": settings.meta_app_secret,
        "redirect_uri": _build_redirect_uri(),
        "code": code,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(META_TOKEN_ENDPOINT, params=token_params)
        except httpx.RequestError as exc:
            logger.error("oauth.meta.callback.token_exchange_failed", error=str(exc))
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "token_exchange_failed",
                    "error_description": f"Failed to reach Meta token endpoint: {exc}",
                },
            )

    if resp.status_code != 200:
        body = _parse_error_response(resp)
        logger.error(
            "oauth.meta.callback.token_exchange_rejected",
            status=resp.status_code,
            meta_error=body.get("error"),
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error": body.get("error", {}).get("message", "token_exchange_error")
                if isinstance(body.get("error"), dict)
                else body.get("error", "token_exchange_error"),
                "error_description": (
                    body.get("error", {}).get("message", "")
                    if isinstance(body.get("error"), dict)
                    else f"Meta returned HTTP {resp.status_code}."
                ),
            },
        )

    short_lived_data = resp.json()
    short_lived_token = short_lived_data.get("access_token")

    if not short_lived_token:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "no_access_token",
                "error_description": "Meta did not return an access token.",
            },
        )

    logger.info(
        "oauth.meta.callback.short_lived_token_obtained",
        expires_in=short_lived_data.get("expires_in"),
    )

    # --- Step 2: Exchange for long-lived token ---
    long_lived_params = {
        "grant_type": "fb_exchange_token",
        "client_id": settings.meta_app_id,
        "client_secret": settings.meta_app_secret,
        "fb_exchange_token": short_lived_token,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(META_TOKEN_ENDPOINT, params=long_lived_params)
        except httpx.RequestError as exc:
            logger.error("oauth.meta.callback.long_lived_exchange_failed", error=str(exc))
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "long_lived_exchange_failed",
                    "error_description": (f"Failed to exchange for long-lived token: {exc}"),
                },
            )

    if resp.status_code != 200:
        body = _parse_error_response(resp)
        logger.error(
            "oauth.meta.callback.long_lived_exchange_rejected",
            status=resp.status_code,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error": "long_lived_exchange_error",
                "error_description": (
                    f"Meta returned HTTP {resp.status_code} during long-lived token exchange."
                ),
            },
        )

    long_lived_data = resp.json()
    logger.info(
        "oauth.meta.callback.success",
        expires_in=long_lived_data.get("expires_in"),
    )

    return MetaLongLivedTokenResponse(
        access_token=encrypt_token(long_lived_data["access_token"]),
        token_type=long_lived_data.get("token_type", "bearer"),
        expires_in=long_lived_data.get("expires_in", 0),
    )


@router.post(
    "/refresh",
    response_model=MetaLongLivedTokenResponse,
    responses={400: {"model": MetaOAuthError}, 502: {"model": MetaOAuthError}},
    summary="Refresh a long-lived access token",
)
async def refresh(body: MetaRefreshRequest) -> MetaLongLivedTokenResponse:
    """Exchange a valid long-lived token for a new long-lived token.

    Meta does not use refresh tokens like Google. Instead, you exchange
    a valid (non-expired) long-lived token for a new one. The new token
    has a fresh ~60 day expiry.

    This should be called periodically (e.g. every 50 days) to keep the
    connection active without requiring the user to re-authenticate.
    """
    long_lived_params = {
        "grant_type": "fb_exchange_token",
        "client_id": settings.meta_app_id,
        "client_secret": settings.meta_app_secret,
        "fb_exchange_token": body.access_token,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(META_TOKEN_ENDPOINT, params=long_lived_params)
        except httpx.RequestError as exc:
            logger.error("oauth.meta.refresh.request_failed", error=str(exc))
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "refresh_failed",
                    "error_description": f"Failed to reach Meta token endpoint: {exc}",
                },
            )

    if resp.status_code != 200:
        body_json = _parse_error_response(resp)
        error_msg = ""
        if isinstance(body_json.get("error"), dict):
            error_msg = body_json["error"].get("message", "")
        else:
            error_msg = f"Meta returned HTTP {resp.status_code}."

        logger.error(
            "oauth.meta.refresh.rejected",
            status=resp.status_code,
            meta_error=error_msg,
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": "refresh_error",
                "error_description": error_msg,
            },
        )

    data = resp.json()
    logger.info("oauth.meta.refresh.success", expires_in=data.get("expires_in"))

    return MetaLongLivedTokenResponse(
        access_token=encrypt_token(data["access_token"]),
        token_type=data.get("token_type", "bearer"),
        expires_in=data.get("expires_in", 0),
    )


@router.get(
    "/status",
    response_model=MetaConnectionStatus,
    summary="Check Meta connection status",
)
async def status(
    access_token: str = Query(..., description="Meta access token to validate"),
) -> MetaConnectionStatus:
    """Verify that a Meta connection is active.

    Makes a lightweight call to ``/me`` to confirm the token is valid,
    then checks ad account access.
    """
    # --- Step 1: Verify token by calling /me ---
    # --- Step 2: Check ad account access ---
    # Both steps share a single httpx client session.
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(
                f"{META_GRAPH_API_BASE}/me",
                params={
                    "fields": "id,name",
                    "access_token": access_token,
                },
            )
        except httpx.RequestError as exc:
            logger.error("oauth.meta.status.request_failed", error=str(exc))
            return MetaConnectionStatus(
                connected=False,
                error=f"Could not reach Meta Graph API: {exc}",
            )

        if resp.status_code != 200:
            error_detail = ""
            try:
                error_body = resp.json()
                error_obj = error_body.get("error", {})
                error_detail = error_obj.get("message", str(error_body))
            except Exception:
                error_detail = resp.text[:200]

            logger.warning(
                "oauth.meta.status.token_invalid",
                status=resp.status_code,
                error=error_detail,
            )
            return MetaConnectionStatus(
                connected=False,
                error=f"Meta API returned HTTP {resp.status_code}: {error_detail}",
            )

        me_data = resp.json()
        user_id = me_data.get("id", "")
        user_name = me_data.get("name", "")

        ad_accounts_count = 0
        try:
            resp_accounts = await client.get(
                f"{META_GRAPH_API_BASE}/me/adaccounts",
                params={
                    "fields": "id",
                    "limit": 100,
                    "access_token": access_token,
                },
            )
            if resp_accounts.status_code == 200:
                accounts_data = resp_accounts.json()
                ad_accounts_count = len(accounts_data.get("data", []))
        except Exception as exc:
            logger.warning("oauth.meta.status.ad_accounts_check_failed", error=str(exc))

    logger.info(
        "oauth.meta.status.connected",
        user_id=user_id,
        user_name=user_name,
        ad_accounts_count=ad_accounts_count,
    )

    return MetaConnectionStatus(
        connected=True,
        user_id=user_id,
        user_name=user_name,
        ad_accounts_count=ad_accounts_count,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_error_response(resp: httpx.Response) -> dict:
    """Safely parse an error response body from Meta."""
    content_type = resp.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        try:
            return resp.json()
        except Exception:
            return {}
    return {}
