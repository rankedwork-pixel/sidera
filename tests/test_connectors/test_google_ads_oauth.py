"""Tests for the Google Ads OAuth2 routes.

Covers:
- GET /oauth/google-ads/authorize  (redirect to Google consent page)
- GET /oauth/google-ads/callback   (code exchange, error handling, state validation)
- POST /oauth/google-ads/refresh   (token refresh)
- GET /oauth/google-ads/status     (connection health check)
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes import google_ads_oauth
from src.api.routes.google_ads_oauth import router

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
    google_ads_oauth._pending_states.clear()
    yield
    google_ads_oauth._pending_states.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _inject_valid_state(token: str = "valid-state-token") -> str:
    """Insert a valid state token into the pending states dict and return it."""
    google_ads_oauth._pending_states[token] = {"created_at": time.time()}
    return token


def _mock_google_token_response(
    status_code: int = 200,
    json_body: dict | None = None,
    content_type: str = "application/json",
) -> httpx.Response:
    """Build a fake httpx.Response mimicking Google's token endpoint."""
    if json_body is None:
        json_body = {
            "access_token": "ya29.fake-access-token",
            "refresh_token": "1//fake-refresh-token",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": "https://www.googleapis.com/auth/adwords",
        }
    response = httpx.Response(
        status_code=status_code,
        json=json_body,
        headers={"content-type": content_type},
        request=httpx.Request("POST", "https://oauth2.googleapis.com/token"),
    )
    return response


def _build_mock_httpx_client(response: httpx.Response | None = None, side_effect=None):
    """Build a mock that replaces ``httpx.AsyncClient`` used as an async context manager.

    The route code does::

        async with httpx.AsyncClient(timeout=...) as client:
            resp = await client.post(url, data=payload)

    We mock the entire ``httpx.AsyncClient`` constructor so the test
    client's own transport (``ASGITransport``) is never affected.

    Returns (mock_class, mock_post) so tests can inspect ``mock_post.call_args``.
    """
    mock_post = AsyncMock()
    if side_effect is not None:
        mock_post.side_effect = side_effect
    elif response is not None:
        mock_post.return_value = response

    mock_client_instance = AsyncMock()
    mock_client_instance.post = mock_post

    # AsyncMock supports ``async with`` out of the box:
    # ``async with mock() as m:``  →  m is ``__aenter__`` return value.
    mock_class = MagicMock()
    mock_class.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_class.return_value.__aexit__ = AsyncMock(return_value=False)

    return mock_class, mock_post


# ---------------------------------------------------------------------------
# 1. GET /oauth/google-ads/authorize
# ---------------------------------------------------------------------------


class TestAuthorize:
    """Tests for the /authorize endpoint."""

    async def test_returns_302_redirect(self, client: AsyncClient):
        resp = await client.get("/oauth/google-ads/authorize", follow_redirects=False)
        assert resp.status_code == 302

    async def test_redirects_to_google_accounts(self, client: AsyncClient):
        resp = await client.get("/oauth/google-ads/authorize", follow_redirects=False)
        location = resp.headers["location"]
        assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth")

    async def test_redirect_url_includes_required_params(self, client: AsyncClient):
        resp = await client.get("/oauth/google-ads/authorize", follow_redirects=False)
        location = resp.headers["location"]

        assert "client_id=" in location
        assert "redirect_uri=" in location
        assert "scope=" in location
        assert "access_type=offline" in location
        assert "prompt=consent" in location
        assert "response_type=code" in location
        assert "state=" in location

    async def test_state_token_stored_in_pending_states(self, client: AsyncClient):
        assert len(google_ads_oauth._pending_states) == 0
        await client.get("/oauth/google-ads/authorize", follow_redirects=False)
        assert len(google_ads_oauth._pending_states) == 1

    async def test_redirect_uri_uses_app_base_url(self, client: AsyncClient):
        resp = await client.get("/oauth/google-ads/authorize", follow_redirects=False)
        location = resp.headers["location"]
        # The redirect_uri param should point back to our callback
        assert "redirect_uri=" in location
        assert (
            "%2Foauth%2Fgoogle-ads%2Fcallback" in location
            or "/oauth/google-ads/callback" in location
        )


# ---------------------------------------------------------------------------
# 2. GET /oauth/google-ads/callback
# ---------------------------------------------------------------------------


class TestCallback:
    """Tests for the /callback endpoint."""

    async def test_missing_code_returns_400(self, client: AsyncClient):
        resp = await client.get(
            "/oauth/google-ads/callback",
            params={"state": "some-state"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error"] == "invalid_request"

    async def test_missing_state_returns_400(self, client: AsyncClient):
        resp = await client.get(
            "/oauth/google-ads/callback",
            params={"code": "some-code"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error"] == "invalid_request"

    async def test_missing_both_code_and_state_returns_400(self, client: AsyncClient):
        resp = await client.get("/oauth/google-ads/callback")
        assert resp.status_code == 400

    async def test_invalid_state_token_returns_400(self, client: AsyncClient):
        resp = await client.get(
            "/oauth/google-ads/callback",
            params={"code": "auth-code-123", "state": "nonexistent-state"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error"] == "invalid_state"

    async def test_expired_state_token_returns_400(self, client: AsyncClient):
        # Insert a state that was created 15 minutes ago (beyond 10-min TTL)
        token = "expired-state"
        google_ads_oauth._pending_states[token] = {
            "created_at": time.time() - 900,
        }

        resp = await client.get(
            "/oauth/google-ads/callback",
            params={"code": "auth-code-123", "state": token},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error"] == "expired_state"

    async def test_google_error_parameter_returns_400(self, client: AsyncClient):
        resp = await client.get(
            "/oauth/google-ads/callback",
            params={"error": "access_denied", "error_description": "User denied"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error"] == "access_denied"
        assert body["detail"]["error_description"] == "User denied"

    async def test_google_error_without_description(self, client: AsyncClient):
        resp = await client.get(
            "/oauth/google-ads/callback",
            params={"error": "access_denied"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error"] == "access_denied"
        assert body["detail"]["error_description"] == "Authorization denied."

    async def test_successful_code_exchange(self, client: AsyncClient):
        state = _inject_valid_state()
        mock_resp = _mock_google_token_response()
        mock_class, _ = _build_mock_httpx_client(response=mock_resp)

        with patch("src.api.routes.google_ads_oauth.httpx.AsyncClient", mock_class):
            resp = await client.get(
                "/oauth/google-ads/callback",
                params={"code": "real-auth-code", "state": state},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"] == "ya29.fake-access-token"
        assert body["refresh_token"] == "1//fake-refresh-token"
        assert body["expires_in"] == 3600
        assert body["token_type"] == "Bearer"

    async def test_successful_exchange_consumes_state(self, client: AsyncClient):
        _inject_valid_state("one-time-state")
        mock_resp = _mock_google_token_response()
        mock_class, _ = _build_mock_httpx_client(response=mock_resp)

        with patch("src.api.routes.google_ads_oauth.httpx.AsyncClient", mock_class):
            await client.get(
                "/oauth/google-ads/callback",
                params={"code": "code-1", "state": "one-time-state"},
            )

        # State should be consumed (popped)
        assert "one-time-state" not in google_ads_oauth._pending_states

    async def test_token_endpoint_failure_returns_502(self, client: AsyncClient):
        state = _inject_valid_state()
        error_response = _mock_google_token_response(
            status_code=400,
            json_body={
                "error": "invalid_grant",
                "error_description": "Code has already been used.",
            },
        )
        mock_class, _ = _build_mock_httpx_client(response=error_response)

        with patch("src.api.routes.google_ads_oauth.httpx.AsyncClient", mock_class):
            resp = await client.get(
                "/oauth/google-ads/callback",
                params={"code": "stale-code", "state": state},
            )

        assert resp.status_code == 502
        body = resp.json()
        assert body["detail"]["error"] == "invalid_grant"

    async def test_token_endpoint_network_error_returns_502(self, client: AsyncClient):
        state = _inject_valid_state()
        mock_class, _ = _build_mock_httpx_client(
            side_effect=httpx.ConnectError("Connection refused"),
        )

        with patch("src.api.routes.google_ads_oauth.httpx.AsyncClient", mock_class):
            resp = await client.get(
                "/oauth/google-ads/callback",
                params={"code": "auth-code", "state": state},
            )

        assert resp.status_code == 502
        body = resp.json()
        assert body["detail"]["error"] == "token_exchange_failed"

    async def test_successful_exchange_without_refresh_token(self, client: AsyncClient):
        state = _inject_valid_state()
        # Some token responses omit refresh_token
        mock_resp = _mock_google_token_response(
            json_body={
                "access_token": "ya29.access-only",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": "https://www.googleapis.com/auth/adwords",
            }
        )
        mock_class, _ = _build_mock_httpx_client(response=mock_resp)

        with patch("src.api.routes.google_ads_oauth.httpx.AsyncClient", mock_class):
            resp = await client.get(
                "/oauth/google-ads/callback",
                params={"code": "code-no-refresh", "state": state},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"] == "ya29.access-only"
        assert body["refresh_token"] is None


# ---------------------------------------------------------------------------
# 3. POST /oauth/google-ads/refresh
# ---------------------------------------------------------------------------


class TestRefresh:
    """Tests for the /refresh endpoint."""

    async def test_successful_refresh(self, client: AsyncClient):
        mock_resp = _mock_google_token_response(
            json_body={
                "access_token": "ya29.refreshed-token",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": "https://www.googleapis.com/auth/adwords",
            }
        )
        mock_class, _ = _build_mock_httpx_client(response=mock_resp)

        with patch("src.api.routes.google_ads_oauth.httpx.AsyncClient", mock_class):
            resp = await client.post(
                "/oauth/google-ads/refresh",
                json={"refresh_token": "1//valid-refresh-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"] == "ya29.refreshed-token"
        assert body["expires_in"] == 3600
        # Refresh responses typically don't include a new refresh_token
        assert body["refresh_token"] is None

    async def test_failed_refresh_returns_400(self, client: AsyncClient):
        error_response = _mock_google_token_response(
            status_code=400,
            json_body={
                "error": "invalid_grant",
                "error_description": "Token has been revoked.",
            },
        )
        mock_class, _ = _build_mock_httpx_client(response=error_response)

        with patch("src.api.routes.google_ads_oauth.httpx.AsyncClient", mock_class):
            resp = await client.post(
                "/oauth/google-ads/refresh",
                json={"refresh_token": "1//revoked-token"},
            )

        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error"] == "invalid_grant"
        assert "revoked" in body["detail"]["error_description"].lower()

    async def test_refresh_network_error_returns_502(self, client: AsyncClient):
        mock_class, _ = _build_mock_httpx_client(
            side_effect=httpx.ConnectError("Connection refused"),
        )

        with patch("src.api.routes.google_ads_oauth.httpx.AsyncClient", mock_class):
            resp = await client.post(
                "/oauth/google-ads/refresh",
                json={"refresh_token": "1//some-token"},
            )

        assert resp.status_code == 502
        body = resp.json()
        assert body["detail"]["error"] == "refresh_failed"

    async def test_refresh_missing_body_returns_422(self, client: AsyncClient):
        resp = await client.post("/oauth/google-ads/refresh", json={})
        assert resp.status_code == 422

    async def test_refresh_sends_correct_payload(self, client: AsyncClient):
        mock_resp = _mock_google_token_response()
        mock_class, mock_post = _build_mock_httpx_client(response=mock_resp)

        with patch("src.api.routes.google_ads_oauth.httpx.AsyncClient", mock_class):
            await client.post(
                "/oauth/google-ads/refresh",
                json={"refresh_token": "1//my-refresh-token"},
            )

        # Verify the outbound call to Google
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        sent_data = call_kwargs.kwargs.get("data")
        assert sent_data["grant_type"] == "refresh_token"
        assert sent_data["refresh_token"] == "1//my-refresh-token"


# ---------------------------------------------------------------------------
# 4. GET /oauth/google-ads/status
# ---------------------------------------------------------------------------


class TestStatus:
    """Tests for the /status endpoint."""

    async def test_valid_connection(self, client: AsyncClient):
        google_ads_response = httpx.Response(
            status_code=200,
            json=[
                {
                    "results": [
                        {
                            "customer": {
                                "descriptiveName": "My Test Account",
                                "id": "1234567890",
                            }
                        }
                    ]
                }
            ],
            headers={"content-type": "application/json"},
            request=httpx.Request("POST", "https://googleads.googleapis.com/"),
        )
        mock_class, _ = _build_mock_httpx_client(response=google_ads_response)

        with patch("src.api.routes.google_ads_oauth.httpx.AsyncClient", mock_class):
            resp = await client.get(
                "/oauth/google-ads/status",
                params={
                    "customer_id": "1234567890",
                    "access_token": "ya29.valid-token",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["connected"] is True
        assert body["customer_id"] == "1234567890"
        assert body["account_name"] == "My Test Account"
        assert body["error"] is None

    async def test_failed_connection_returns_connected_false(self, client: AsyncClient):
        error_response = httpx.Response(
            status_code=401,
            json={
                "error": {
                    "message": "Request had invalid authentication credentials.",
                    "status": "UNAUTHENTICATED",
                }
            },
            headers={"content-type": "application/json"},
            request=httpx.Request("POST", "https://googleads.googleapis.com/"),
        )
        mock_class, _ = _build_mock_httpx_client(response=error_response)

        with patch("src.api.routes.google_ads_oauth.httpx.AsyncClient", mock_class):
            resp = await client.get(
                "/oauth/google-ads/status",
                params={
                    "customer_id": "1234567890",
                    "access_token": "ya29.expired-token",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["connected"] is False
        assert body["customer_id"] == "1234567890"
        assert "401" in body["error"]

    async def test_invalid_customer_id_too_short_returns_400(self, client: AsyncClient):
        resp = await client.get(
            "/oauth/google-ads/status",
            params={
                "customer_id": "12345",
                "access_token": "ya29.some-token",
            },
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error"] == "invalid_customer_id"

    async def test_invalid_customer_id_non_numeric_returns_400(self, client: AsyncClient):
        resp = await client.get(
            "/oauth/google-ads/status",
            params={
                "customer_id": "abcdefghij",
                "access_token": "ya29.some-token",
            },
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error"] == "invalid_customer_id"

    async def test_customer_id_with_dashes_is_normalized(self, client: AsyncClient):
        google_ads_response = httpx.Response(
            status_code=200,
            json=[
                {
                    "results": [
                        {
                            "customer": {
                                "descriptiveName": "Dashed Account",
                                "id": "1234567890",
                            }
                        }
                    ]
                }
            ],
            headers={"content-type": "application/json"},
            request=httpx.Request("POST", "https://googleads.googleapis.com/"),
        )
        mock_class, _ = _build_mock_httpx_client(response=google_ads_response)

        with patch("src.api.routes.google_ads_oauth.httpx.AsyncClient", mock_class):
            resp = await client.get(
                "/oauth/google-ads/status",
                params={
                    "customer_id": "123-456-7890",
                    "access_token": "ya29.valid-token",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["connected"] is True
        assert body["customer_id"] == "1234567890"

    async def test_network_error_returns_connected_false(self, client: AsyncClient):
        mock_class, _ = _build_mock_httpx_client(
            side_effect=httpx.ConnectError("DNS resolution failed"),
        )

        with patch("src.api.routes.google_ads_oauth.httpx.AsyncClient", mock_class):
            resp = await client.get(
                "/oauth/google-ads/status",
                params={
                    "customer_id": "1234567890",
                    "access_token": "ya29.some-token",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["connected"] is False
        assert "Could not reach" in body["error"]

    async def test_missing_customer_id_returns_422(self, client: AsyncClient):
        resp = await client.get(
            "/oauth/google-ads/status",
            params={"access_token": "ya29.some-token"},
        )
        assert resp.status_code == 422

    async def test_missing_access_token_returns_422(self, client: AsyncClient):
        resp = await client.get(
            "/oauth/google-ads/status",
            params={"customer_id": "1234567890"},
        )
        assert resp.status_code == 422

    async def test_status_sends_developer_token_header(self, client: AsyncClient):
        google_ads_response = httpx.Response(
            status_code=200,
            json=[{"results": []}],
            headers={"content-type": "application/json"},
            request=httpx.Request("POST", "https://googleads.googleapis.com/"),
        )
        mock_class, mock_post = _build_mock_httpx_client(response=google_ads_response)

        with patch("src.api.routes.google_ads_oauth.httpx.AsyncClient", mock_class):
            await client.get(
                "/oauth/google-ads/status",
                params={
                    "customer_id": "1234567890",
                    "access_token": "ya29.test-token",
                },
            )

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        headers = call_kwargs.kwargs.get("headers")
        assert "developer-token" in headers
        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer ya29.test-token"

    async def test_status_with_empty_results_still_connected(self, client: AsyncClient):
        google_ads_response = httpx.Response(
            status_code=200,
            json=[{"results": []}],
            headers={"content-type": "application/json"},
            request=httpx.Request("POST", "https://googleads.googleapis.com/"),
        )
        mock_class, _ = _build_mock_httpx_client(response=google_ads_response)

        with patch("src.api.routes.google_ads_oauth.httpx.AsyncClient", mock_class):
            resp = await client.get(
                "/oauth/google-ads/status",
                params={
                    "customer_id": "1234567890",
                    "access_token": "ya29.valid-token",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["connected"] is True
        assert body["account_name"] is None
