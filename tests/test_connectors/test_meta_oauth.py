"""Tests for the Meta (Facebook) OAuth2 routes.

Covers:
- GET /oauth/meta/authorize   (redirect to Facebook Login)
- GET /oauth/meta/callback    (two-step code exchange, error handling, state validation)
- POST /oauth/meta/refresh    (long-lived token refresh)
- GET /oauth/meta/status      (connection health check via /me + /me/adaccounts)
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes import meta_oauth
from src.api.routes.meta_oauth import router

# ---------------------------------------------------------------------------
# Test app + async client fixture
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(router)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _clear_pending_states():
    """Ensure no stale state tokens leak between tests."""
    meta_oauth._pending_states.clear()
    yield
    meta_oauth._pending_states.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _inject_valid_state(token: str = "valid-state-token") -> str:
    """Insert a valid state token into the pending states dict and return it."""
    meta_oauth._pending_states[token] = {"created_at": time.time()}
    return token


def _mock_meta_response(
    status_code: int = 200,
    json_body: dict | None = None,
    content_type: str = "application/json",
    method: str = "GET",
    url: str = "https://graph.facebook.com/v21.0/oauth/access_token",
) -> httpx.Response:
    """Build a fake httpx.Response mimicking a Meta Graph API endpoint."""
    if json_body is None:
        json_body = {
            "access_token": "EAAx.fake-short-lived-token",
            "token_type": "bearer",
            "expires_in": 3600,
        }
    return httpx.Response(
        status_code=status_code,
        json=json_body,
        headers={"content-type": content_type},
        request=httpx.Request(method, url),
    )


def _build_mock_httpx_client(
    response: httpx.Response | None = None,
    side_effect=None,
    responses: list[httpx.Response] | None = None,
):
    """Build a mock that replaces ``httpx.AsyncClient`` used as an async context manager.

    The route code does::

        async with httpx.AsyncClient(timeout=...) as client:
            resp = await client.get(url, params=params)

    We mock the entire ``httpx.AsyncClient`` constructor so the test
    client's own transport (``ASGITransport``) is never affected.

    Parameters
    ----------
    response : httpx.Response, optional
        Single response returned for every call to ``mock_get``.
    side_effect : exception or callable, optional
        Side effect for ``mock_get`` (e.g. ``httpx.ConnectError``).
    responses : list[httpx.Response], optional
        Ordered list of responses for sequential calls to ``mock_get``.
        Use this when the code makes multiple GET requests within a single
        ``async with`` block (e.g. the status endpoint calls /me then
        /me/adaccounts).

    Returns (mock_class, mock_get) so tests can inspect ``mock_get.call_args``.
    """
    mock_get = AsyncMock()
    if side_effect is not None:
        mock_get.side_effect = side_effect
    elif responses is not None:
        mock_get.side_effect = responses
    elif response is not None:
        mock_get.return_value = response

    mock_client_instance = AsyncMock()
    mock_client_instance.get = mock_get

    # AsyncMock supports ``async with`` out of the box:
    # ``async with mock() as m:``  ->  m is ``__aenter__`` return value.
    mock_class = MagicMock()
    mock_class.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_class.return_value.__aexit__ = AsyncMock(return_value=False)

    return mock_class, mock_get


# ---------------------------------------------------------------------------
# 1. GET /oauth/meta/authorize
# ---------------------------------------------------------------------------


class TestAuthorize:
    """Tests for the /authorize endpoint."""

    async def test_returns_302_redirect(self, client: AsyncClient):
        resp = await client.get("/oauth/meta/authorize", follow_redirects=False)
        assert resp.status_code == 302

    async def test_redirects_to_facebook(self, client: AsyncClient):
        resp = await client.get("/oauth/meta/authorize", follow_redirects=False)
        location = resp.headers["location"]
        assert location.startswith("https://www.facebook.com/")

    async def test_redirect_url_includes_required_params(self, client: AsyncClient):
        resp = await client.get("/oauth/meta/authorize", follow_redirects=False)
        location = resp.headers["location"]

        assert "client_id=" in location
        assert "redirect_uri=" in location
        assert "scope=" in location
        assert "state=" in location
        assert "response_type=code" in location

    async def test_state_token_stored_in_pending_states(self, client: AsyncClient):
        assert len(meta_oauth._pending_states) == 0
        await client.get("/oauth/meta/authorize", follow_redirects=False)
        assert len(meta_oauth._pending_states) == 1


# ---------------------------------------------------------------------------
# 2. GET /oauth/meta/callback
# ---------------------------------------------------------------------------


class TestCallback:
    """Tests for the /callback endpoint.

    Meta callback does two sequential token exchanges:
    1. code -> short-lived token
    2. short-lived token -> long-lived token (~60 days)
    """

    async def test_missing_code_returns_400(self, client: AsyncClient):
        resp = await client.get(
            "/oauth/meta/callback",
            params={"state": "some-state"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error"] == "invalid_request"

    async def test_missing_state_returns_400(self, client: AsyncClient):
        resp = await client.get(
            "/oauth/meta/callback",
            params={"code": "some-code"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error"] == "invalid_request"

    async def test_invalid_state_returns_400(self, client: AsyncClient):
        resp = await client.get(
            "/oauth/meta/callback",
            params={"code": "auth-code-123", "state": "nonexistent-state"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error"] == "invalid_state"

    async def test_expired_state_returns_400(self, client: AsyncClient):
        # Insert a state that was created 15 minutes ago (beyond 10-min TTL)
        token = "expired-state"
        meta_oauth._pending_states[token] = {
            "created_at": time.time() - 900,
        }

        resp = await client.get(
            "/oauth/meta/callback",
            params={"code": "auth-code-123", "state": token},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error"] == "expired_state"

    async def test_facebook_error_parameter_returns_400(self, client: AsyncClient):
        resp = await client.get(
            "/oauth/meta/callback",
            params={"error": "access_denied", "error_description": "User denied"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error"] == "access_denied"
        assert body["detail"]["error_description"] == "User denied"

    async def test_successful_exchange(self, client: AsyncClient):
        """Mock both short-lived AND long-lived token exchanges.

        The callback makes two separate ``async with httpx.AsyncClient``
        blocks, so the mock class is instantiated twice. Each instantiation
        creates a new ``__aenter__`` call that returns the same mock client.
        We use ``side_effect`` on ``mock_get`` to return the short-lived
        response first, then the long-lived response second.
        """
        state = _inject_valid_state()

        short_lived_resp = _mock_meta_response(
            json_body={
                "access_token": "EAAx.short-lived-token",
                "token_type": "bearer",
                "expires_in": 3600,
            }
        )
        long_lived_resp = _mock_meta_response(
            json_body={
                "access_token": "EAAx.long-lived-token",
                "token_type": "bearer",
                "expires_in": 5184000,
            }
        )

        mock_class, mock_get = _build_mock_httpx_client(
            responses=[short_lived_resp, long_lived_resp],
        )

        with patch("src.api.routes.meta_oauth.httpx.AsyncClient", mock_class):
            resp = await client.get(
                "/oauth/meta/callback",
                params={"code": "real-auth-code", "state": state},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"] == "EAAx.long-lived-token"
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == 5184000

    async def test_token_endpoint_failure_returns_502(self, client: AsyncClient):
        """Short-lived token exchange returns a non-200 status."""
        state = _inject_valid_state()
        error_response = _mock_meta_response(
            status_code=400,
            json_body={
                "error": {
                    "message": "Invalid verification code.",
                    "type": "OAuthException",
                    "code": 100,
                }
            },
        )
        mock_class, _ = _build_mock_httpx_client(response=error_response)

        with patch("src.api.routes.meta_oauth.httpx.AsyncClient", mock_class):
            resp = await client.get(
                "/oauth/meta/callback",
                params={"code": "stale-code", "state": state},
            )

        assert resp.status_code == 502

    async def test_successful_exchange_consumes_state(self, client: AsyncClient):
        _inject_valid_state("one-time-state")

        short_lived_resp = _mock_meta_response(
            json_body={
                "access_token": "EAAx.short-lived-token",
                "token_type": "bearer",
                "expires_in": 3600,
            }
        )
        long_lived_resp = _mock_meta_response(
            json_body={
                "access_token": "EAAx.long-lived-token",
                "token_type": "bearer",
                "expires_in": 5184000,
            }
        )

        mock_class, _ = _build_mock_httpx_client(
            responses=[short_lived_resp, long_lived_resp],
        )

        with patch("src.api.routes.meta_oauth.httpx.AsyncClient", mock_class):
            await client.get(
                "/oauth/meta/callback",
                params={"code": "code-1", "state": "one-time-state"},
            )

        # State should be consumed (popped)
        assert "one-time-state" not in meta_oauth._pending_states


# ---------------------------------------------------------------------------
# 3. POST /oauth/meta/refresh
# ---------------------------------------------------------------------------


class TestRefresh:
    """Tests for the /refresh endpoint.

    Meta refresh takes an access_token (not refresh_token) and exchanges
    the current long-lived token for a new one.
    """

    async def test_successful_refresh(self, client: AsyncClient):
        mock_resp = _mock_meta_response(
            json_body={
                "access_token": "EAAx.refreshed-long-lived-token",
                "token_type": "bearer",
                "expires_in": 5184000,
            }
        )
        mock_class, _ = _build_mock_httpx_client(response=mock_resp)

        with patch("src.api.routes.meta_oauth.httpx.AsyncClient", mock_class):
            resp = await client.post(
                "/oauth/meta/refresh",
                json={"access_token": "EAAx.current-long-lived-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"] == "EAAx.refreshed-long-lived-token"
        assert body["expires_in"] == 5184000
        assert body["token_type"] == "bearer"

    async def test_failed_refresh_returns_400(self, client: AsyncClient):
        error_response = _mock_meta_response(
            status_code=400,
            json_body={
                "error": {
                    "message": "Error validating access token: Session has expired.",
                    "type": "OAuthException",
                    "code": 190,
                }
            },
        )
        mock_class, _ = _build_mock_httpx_client(response=error_response)

        with patch("src.api.routes.meta_oauth.httpx.AsyncClient", mock_class):
            resp = await client.post(
                "/oauth/meta/refresh",
                json={"access_token": "EAAx.expired-token"},
            )

        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error"] == "refresh_error"

    async def test_network_error_returns_502(self, client: AsyncClient):
        mock_class, _ = _build_mock_httpx_client(
            side_effect=httpx.ConnectError("Connection refused"),
        )

        with patch("src.api.routes.meta_oauth.httpx.AsyncClient", mock_class):
            resp = await client.post(
                "/oauth/meta/refresh",
                json={"access_token": "EAAx.some-token"},
            )

        assert resp.status_code == 502
        body = resp.json()
        assert body["detail"]["error"] == "refresh_failed"

    async def test_missing_body_returns_422(self, client: AsyncClient):
        resp = await client.post("/oauth/meta/refresh", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 4. GET /oauth/meta/status
# ---------------------------------------------------------------------------


class TestStatus:
    """Tests for the /status endpoint.

    Meta status calls /me (to verify token) and /me/adaccounts (to count
    ad accounts), both within a single httpx client session.
    """

    async def test_valid_connection(self, client: AsyncClient):
        """Mock both /me and /me/adaccounts responses."""
        me_response = _mock_meta_response(
            json_body={
                "id": "123456789",
                "name": "Test User",
            },
            url="https://graph.facebook.com/v21.0/me",
        )
        adaccounts_response = _mock_meta_response(
            json_body={
                "data": [
                    {"id": "act_111111"},
                    {"id": "act_222222"},
                    {"id": "act_333333"},
                ],
            },
            url="https://graph.facebook.com/v21.0/me/adaccounts",
        )
        mock_class, _ = _build_mock_httpx_client(
            responses=[me_response, adaccounts_response],
        )

        with patch("src.api.routes.meta_oauth.httpx.AsyncClient", mock_class):
            resp = await client.get(
                "/oauth/meta/status",
                params={"access_token": "EAAx.valid-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["connected"] is True
        assert body["user_id"] == "123456789"
        assert body["user_name"] == "Test User"
        assert body["ad_accounts_count"] == 3
        assert body["error"] is None

    async def test_failed_connection_returns_connected_false(self, client: AsyncClient):
        """Token is invalid; /me returns non-200."""
        error_response = _mock_meta_response(
            status_code=401,
            json_body={
                "error": {
                    "message": "Invalid OAuth access token.",
                    "type": "OAuthException",
                    "code": 190,
                }
            },
            url="https://graph.facebook.com/v21.0/me",
        )
        mock_class, _ = _build_mock_httpx_client(response=error_response)

        with patch("src.api.routes.meta_oauth.httpx.AsyncClient", mock_class):
            resp = await client.get(
                "/oauth/meta/status",
                params={"access_token": "EAAx.expired-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["connected"] is False
        assert "401" in body["error"]

    async def test_network_error_returns_connected_false(self, client: AsyncClient):
        mock_class, _ = _build_mock_httpx_client(
            side_effect=httpx.ConnectError("DNS resolution failed"),
        )

        with patch("src.api.routes.meta_oauth.httpx.AsyncClient", mock_class):
            resp = await client.get(
                "/oauth/meta/status",
                params={"access_token": "EAAx.some-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["connected"] is False
        assert "Could not reach" in body["error"]

    async def test_missing_access_token_returns_422(self, client: AsyncClient):
        resp = await client.get("/oauth/meta/status")
        assert resp.status_code == 422

    async def test_valid_connection_with_no_ad_accounts(self, client: AsyncClient):
        """Token is valid but user has zero ad accounts."""
        me_response = _mock_meta_response(
            json_body={
                "id": "999888777",
                "name": "No Ads User",
            },
            url="https://graph.facebook.com/v21.0/me",
        )
        adaccounts_response = _mock_meta_response(
            json_body={"data": []},
            url="https://graph.facebook.com/v21.0/me/adaccounts",
        )
        mock_class, _ = _build_mock_httpx_client(
            responses=[me_response, adaccounts_response],
        )

        with patch("src.api.routes.meta_oauth.httpx.AsyncClient", mock_class):
            resp = await client.get(
                "/oauth/meta/status",
                params={"access_token": "EAAx.valid-no-ads"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["connected"] is True
        assert body["user_id"] == "999888777"
        assert body["ad_accounts_count"] == 0

    async def test_adaccounts_failure_still_connected(self, client: AsyncClient):
        """If /me succeeds but /me/adaccounts fails, user is still connected."""
        me_response = _mock_meta_response(
            json_body={
                "id": "111222333",
                "name": "Partial User",
            },
            url="https://graph.facebook.com/v21.0/me",
        )
        adaccounts_error = _mock_meta_response(
            status_code=403,
            json_body={
                "error": {
                    "message": "Insufficient permissions.",
                    "type": "OAuthException",
                    "code": 200,
                }
            },
            url="https://graph.facebook.com/v21.0/me/adaccounts",
        )
        mock_class, _ = _build_mock_httpx_client(
            responses=[me_response, adaccounts_error],
        )

        with patch("src.api.routes.meta_oauth.httpx.AsyncClient", mock_class):
            resp = await client.get(
                "/oauth/meta/status",
                params={"access_token": "EAAx.partial-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["connected"] is True
        assert body["user_id"] == "111222333"
        # ad_accounts_count stays 0 because the accounts call returned non-200
        assert body["ad_accounts_count"] == 0
