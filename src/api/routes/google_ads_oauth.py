"""Google Ads OAuth2 routes for Sidera.

Handles the full OAuth2 flow for connecting Google Ads accounts:
1. /authorize — Redirect user to Google's consent page
2. /callback — Exchange authorization code for tokens
3. /refresh — Refresh an expired access token
4. /status — Verify a Google Ads connection is active

The flow uses offline access (refresh tokens) so the agent can pull data
on scheduled runs without user interaction.
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
    prefix="/oauth/google-ads",
    tags=["Google Ads OAuth"],
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_ADS_API_SCOPE = "https://www.googleapis.com/auth/adwords"
GOOGLE_ADS_API_VERSION = "v18"

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
        key = build_cache_key("oauth_state", "google_ads", state=state)
        await cache_set(key, data, ttl_seconds=_STATE_TTL_SECONDS)
    except Exception:
        logger.debug("oauth.state.redis_save_failed", state_prefix=state[:8])


async def _get_oauth_state(state: str) -> dict | None:
    """Retrieve an OAuth state token. Tries Redis first, falls back to in-memory."""
    try:
        key = build_cache_key("oauth_state", "google_ads", state=state)
        cached = await cache_get(key)
        if cached is not None:
            # Clean up Redis (one-time use)
            await cache_delete(key)
            # Also remove from in-memory
            _pending_states.pop(state, None)
            return cached
    except Exception:
        logger.debug("oauth.state.redis_get_failed", state_prefix=state[:8])

    # Fall back to in-memory
    return _pending_states.pop(state, None)


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------


class TokenResponse(BaseModel):
    """Successful token exchange response."""

    access_token: str
    refresh_token: Optional[str] = None
    expires_in: int
    token_type: str = "Bearer"
    scope: str = ""


class RefreshRequest(BaseModel):
    """Request body for the token refresh endpoint."""

    refresh_token: str = Field(..., description="The OAuth2 refresh token issued by Google.")


class ConnectionStatus(BaseModel):
    """Response for the connection status check."""

    connected: bool
    customer_id: Optional[str] = None
    account_name: Optional[str] = None
    error: Optional[str] = None


class OAuthError(BaseModel):
    """Structured error response for OAuth failures."""

    error: str
    error_description: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_redirect_uri() -> str:
    """Construct the OAuth callback URI from the configured base URL."""
    return f"{settings.app_base_url}/oauth/google-ads/callback"


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


@router.get("/authorize", summary="Initiate Google Ads OAuth flow")
async def authorize() -> RedirectResponse:
    """Redirect the user to Google's OAuth2 consent page.

    Generates a random state token for CSRF protection, stores it
    in-memory, and builds the authorization URL with offline access
    so we receive a refresh_token.
    """
    _purge_expired_states()

    state = secrets.token_urlsafe(32)
    state_data = {"created_at": time.time()}
    await _save_oauth_state(state, state_data)

    params = {
        "client_id": settings.google_ads_client_id,
        "redirect_uri": _build_redirect_uri(),
        "response_type": "code",
        "scope": GOOGLE_ADS_API_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }

    auth_url = f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"

    logger.info(
        "oauth.authorize.redirect",
        redirect_uri=_build_redirect_uri(),
        state_prefix=state[:8],
    )

    return RedirectResponse(url=auth_url, status_code=302)


@router.get(
    "/callback",
    response_model=TokenResponse,
    responses={400: {"model": OAuthError}, 502: {"model": OAuthError}},
    summary="Handle OAuth callback from Google",
)
async def callback(
    code: Optional[str] = Query(None, description="Authorization code from Google"),
    state: Optional[str] = Query(None, description="CSRF state token"),
    error: Optional[str] = Query(None, description="Error code if user denied access"),
    error_description: Optional[str] = Query(None, description="Human-readable error from Google"),
) -> TokenResponse:
    """Exchange the authorization code for access and refresh tokens.

    Validates the state parameter against our in-memory store, then
    performs the code-for-token exchange via Google's token endpoint.
    """
    # --- Handle user-denied or Google-side errors ---
    if error:
        logger.warning(
            "oauth.callback.error_from_google",
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
        logger.warning("oauth.callback.missing_params", has_code=bool(code), has_state=bool(state))
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
        logger.warning("oauth.callback.invalid_state", state_prefix=state[:8])
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
        logger.warning("oauth.callback.expired_state", elapsed_seconds=elapsed)
        raise HTTPException(
            status_code=400,
            detail={
                "error": "expired_state",
                "error_description": "State token expired. Please restart the OAuth flow.",
            },
        )

    # --- Exchange code for tokens ---
    token_payload = {
        "client_id": settings.google_ads_client_id,
        "client_secret": settings.google_ads_client_secret,
        "code": code,
        "redirect_uri": _build_redirect_uri(),
        "grant_type": "authorization_code",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(GOOGLE_TOKEN_ENDPOINT, data=token_payload)
        except httpx.RequestError as exc:
            logger.error("oauth.callback.token_exchange_failed", error=str(exc))
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "token_exchange_failed",
                    "error_description": f"Failed to reach Google token endpoint: {exc}",
                },
            )

    if resp.status_code != 200:
        content_type = resp.headers.get("content-type", "")
        body = resp.json() if content_type.startswith("application/json") else {}
        logger.error(
            "oauth.callback.token_exchange_rejected",
            status=resp.status_code,
            google_error=body.get("error"),
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error": body.get("error", "token_exchange_error"),
                "error_description": body.get(
                    "error_description", f"Google returned HTTP {resp.status_code}."
                ),
            },
        )

    data = resp.json()
    logger.info(
        "oauth.callback.success",
        has_refresh_token=("refresh_token" in data),
        expires_in=data.get("expires_in"),
    )

    raw_refresh = data.get("refresh_token")
    return TokenResponse(
        access_token=encrypt_token(data["access_token"]),
        refresh_token=encrypt_token(raw_refresh) if raw_refresh else None,
        expires_in=data.get("expires_in", 3600),
        token_type=data.get("token_type", "Bearer"),
        scope=data.get("scope", ""),
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    responses={400: {"model": OAuthError}, 502: {"model": OAuthError}},
    summary="Refresh an expired access token",
)
async def refresh(body: RefreshRequest) -> TokenResponse:
    """Use a refresh token to obtain a new access token.

    Called when the current access_token has expired (typically after 1 hour).
    Google does not issue a new refresh_token on refresh calls — the original
    refresh_token remains valid until the user revokes access.
    """
    token_payload = {
        "client_id": settings.google_ads_client_id,
        "client_secret": settings.google_ads_client_secret,
        "refresh_token": body.refresh_token,
        "grant_type": "refresh_token",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(GOOGLE_TOKEN_ENDPOINT, data=token_payload)
        except httpx.RequestError as exc:
            logger.error("oauth.refresh.request_failed", error=str(exc))
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "refresh_failed",
                    "error_description": f"Failed to reach Google token endpoint: {exc}",
                },
            )

    if resp.status_code != 200:
        ct = resp.headers.get("content-type", "")
        body_json = resp.json() if ct.startswith("application/json") else {}
        logger.error(
            "oauth.refresh.rejected",
            status=resp.status_code,
            google_error=body_json.get("error"),
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": body_json.get("error", "refresh_error"),
                "error_description": body_json.get(
                    "error_description", f"Google returned HTTP {resp.status_code}."
                ),
            },
        )

    data = resp.json()
    logger.info("oauth.refresh.success", expires_in=data.get("expires_in"))

    raw_refresh = data.get("refresh_token")
    return TokenResponse(
        access_token=encrypt_token(data["access_token"]),
        refresh_token=encrypt_token(raw_refresh) if raw_refresh else None,
        expires_in=data.get("expires_in", 3600),
        token_type=data.get("token_type", "Bearer"),
        scope=data.get("scope", ""),
    )


@router.get(
    "/status",
    response_model=ConnectionStatus,
    summary="Check Google Ads connection status",
)
async def status(
    customer_id: str = Query(..., description="Google Ads customer ID (10 digits, no dashes)"),
    access_token: str = Query(..., description="Current OAuth access token to validate"),
) -> ConnectionStatus:
    """Verify that a Google Ads connection is active.

    Makes a lightweight query against the Google Ads REST API to confirm
    the access token is valid and the customer ID is accessible.
    """
    # Normalize customer_id — strip dashes, spaces
    clean_cid = customer_id.replace("-", "").replace(" ", "")

    if not clean_cid.isdigit() or len(clean_cid) != 10:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_customer_id",
                "error_description": "Customer ID must be exactly 10 digits.",
            },
        )

    # Use the Google Ads REST API to fetch the account name as a health check.
    # This is a minimal GAQL query that returns quickly.
    gaql_query = "SELECT customer.descriptive_name, customer.id FROM customer LIMIT 1"

    url = (
        f"https://googleads.googleapis.com/{GOOGLE_ADS_API_VERSION}"
        f"/customers/{clean_cid}/googleAds:searchStream"
    )

    headers = {
        "Authorization": f"Bearer {access_token}",
        "developer-token": settings.google_ads_developer_token,
        "Content-Type": "application/json",
    }

    # If a login-customer-id (MCC) is configured, include it.
    if settings.google_ads_login_customer_id:
        headers["login-customer-id"] = settings.google_ads_login_customer_id.replace("-", "")

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(url, headers=headers, json={"query": gaql_query})
        except httpx.RequestError as exc:
            logger.error("oauth.status.request_failed", customer_id=clean_cid, error=str(exc))
            return ConnectionStatus(
                connected=False,
                customer_id=clean_cid,
                error=f"Could not reach Google Ads API: {exc}",
            )

    if resp.status_code != 200:
        error_detail = ""
        try:
            error_body = resp.json()
            error_detail = error_body.get("error", {}).get("message", "") or str(error_body)
        except Exception:
            error_detail = resp.text[:200]

        logger.warning(
            "oauth.status.unhealthy",
            customer_id=clean_cid,
            status=resp.status_code,
            error=error_detail,
        )
        return ConnectionStatus(
            connected=False,
            customer_id=clean_cid,
            error=f"Google Ads API returned HTTP {resp.status_code}: {error_detail}",
        )

    # Parse the streamed response — Google Ads searchStream returns an array
    # of result batches.
    account_name: Optional[str] = None
    try:
        batches = resp.json()
        for batch in batches:
            for result in batch.get("results", []):
                customer = result.get("customer", {})
                account_name = customer.get("descriptiveName")
    except Exception as exc:
        logger.warning("oauth.status.parse_error", error=str(exc))

    logger.info("oauth.status.connected", customer_id=clean_cid, account_name=account_name)

    return ConnectionStatus(
        connected=True,
        customer_id=clean_cid,
        account_name=account_name,
    )
