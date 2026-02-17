"""Tests for src.api.app — FastAPI application assembly.

Verifies that the application starts correctly, all routes are mounted,
middleware is configured, and error handling works as expected.
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers — mock heavy dependencies before importing the app module
# ---------------------------------------------------------------------------


def _install_slack_bolt_stubs() -> dict[str, MagicMock]:
    """Install minimal stubs for slack_bolt so the Slack router can import.

    ``src.api.routes.slack`` does ``from slack_bolt.async_app import AsyncApp``
    and ``from slack_bolt.adapter.fastapi.async_handler import
    AsyncSlackRequestHandler`` at module level.  We need these to be
    importable without the real ``slack-bolt`` package being fully
    functional (it may raise if tokens are empty).
    """
    mocks: dict[str, MagicMock] = {}

    # Create a fake AsyncApp that is callable (constructor)
    mock_async_app_cls = MagicMock()
    mock_async_app_instance = MagicMock()
    # Make action() a decorator that returns the function unchanged
    mock_async_app_instance.action = MagicMock(side_effect=lambda _id: lambda fn: fn)
    mock_async_app_cls.return_value = mock_async_app_instance

    # Create a fake AsyncSlackRequestHandler
    mock_handler_cls = MagicMock()
    mock_handler_instance = MagicMock()
    # handle() is awaited in the route, so it must return a coroutine
    from starlette.responses import Response as StarletteResponse

    mock_handler_instance.handle = AsyncMock(
        return_value=StarletteResponse(content=b"ok", status_code=200)
    )
    mock_handler_cls.return_value = mock_handler_instance

    # Build the module tree
    bolt_mod = ModuleType("slack_bolt")
    bolt_async_mod = ModuleType("slack_bolt.async_app")
    bolt_adapter_mod = ModuleType("slack_bolt.adapter")
    bolt_adapter_fastapi_mod = ModuleType("slack_bolt.adapter.fastapi")
    bolt_adapter_fastapi_async_mod = ModuleType("slack_bolt.adapter.fastapi.async_handler")

    bolt_async_mod.AsyncApp = mock_async_app_cls
    bolt_adapter_fastapi_async_mod.AsyncSlackRequestHandler = mock_handler_cls

    sys.modules["slack_bolt"] = bolt_mod
    sys.modules["slack_bolt.async_app"] = bolt_async_mod
    sys.modules["slack_bolt.adapter"] = bolt_adapter_mod
    sys.modules["slack_bolt.adapter.fastapi"] = bolt_adapter_fastapi_mod
    sys.modules["slack_bolt.adapter.fastapi.async_handler"] = bolt_adapter_fastapi_async_mod

    mocks["async_app_cls"] = mock_async_app_cls
    mocks["async_app_instance"] = mock_async_app_instance
    mocks["handler_cls"] = mock_handler_cls
    mocks["handler_instance"] = mock_handler_instance

    return mocks


def _cleanup_slack_bolt_stubs() -> None:
    """Remove the stub modules so they don't leak between tests."""
    keys_to_remove = [k for k in sys.modules if k.startswith("slack_bolt")]
    for k in keys_to_remove:
        sys.modules.pop(k, None)

    # Also remove cached route modules so they re-import cleanly
    keys_to_remove = [k for k in sys.modules if k.startswith("src.api.routes.slack")]
    for k in keys_to_remove:
        sys.modules.pop(k, None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def _slack_stubs():
    """Install and tear down slack_bolt stubs around each test."""
    _install_slack_bolt_stubs()
    yield
    _cleanup_slack_bolt_stubs()


@pytest.fixture()
def fresh_app(_slack_stubs):
    """Create a fresh Sidera app with mocked external dependencies.

    Clears the cached app module so ``create_app()`` runs from scratch.
    """
    # Remove previously-cached app module so create_app re-runs
    sys.modules.pop("src.api.app", None)

    from src.api.app import create_app

    application = create_app()
    return application


@pytest.fixture()
def client(fresh_app):
    """Return a ``TestClient`` wrapping the fresh app."""
    return TestClient(fresh_app)


# ===========================================================================
# 1. Health check
# ===========================================================================


class TestHealthCheck:
    """GET /health endpoint."""

    def test_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_response_has_expected_fields(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["service"] == "sidera"
        assert data["version"] == "0.1.0"
        assert "environment" in data

    def test_environment_matches_settings(self, client):
        resp = client.get("/health")
        data = resp.json()
        # Default settings.app_env is "development"
        assert data["environment"] == "development"


# ===========================================================================
# 2. Root endpoint
# ===========================================================================


class TestRootEndpoint:
    """GET / endpoint."""

    def test_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_response_body(self, client):
        resp = client.get("/")
        data = resp.json()
        assert data["service"] == "sidera"
        assert data["status"] == "running"


# ===========================================================================
# 3. Google Ads OAuth router is mounted
# ===========================================================================


class TestGoogleAdsOAuthMounted:
    """Verify the Google Ads OAuth router is reachable."""

    def test_authorize_returns_redirect(self, client):
        # /oauth/google-ads/authorize should redirect to Google
        resp = client.get("/oauth/google-ads/authorize", follow_redirects=False)
        assert resp.status_code == 302
        assert "accounts.google.com" in resp.headers.get("location", "")

    def test_callback_without_params_returns_400(self, client):
        # Missing code and state should give an error
        resp = client.get("/oauth/google-ads/callback")
        assert resp.status_code == 400


# ===========================================================================
# 4. Meta OAuth router is mounted
# ===========================================================================


class TestMetaOAuthMounted:
    """Verify the Meta OAuth router is reachable."""

    def test_authorize_returns_redirect(self, client):
        resp = client.get("/oauth/meta/authorize", follow_redirects=False)
        assert resp.status_code == 302
        assert "facebook.com" in resp.headers.get("location", "")

    def test_callback_without_params_returns_400(self, client):
        resp = client.get("/oauth/meta/callback")
        assert resp.status_code == 400


# ===========================================================================
# 5. Slack router is mounted
# ===========================================================================


class TestSlackRouterMounted:
    """Verify the Slack events route exists."""

    def test_slack_events_route_exists(self, fresh_app):
        """The /slack/events path should be registered."""
        paths = [route.path for route in fresh_app.routes]
        assert "/slack/events" in paths

    def test_slack_events_post_returns_response(self, client):
        """POST /slack/events should not return 404 (route exists).

        Without a valid Slack signature we expect 4xx or 5xx, but
        crucially NOT 404 — that would mean the route isn't mounted.
        """
        resp = client.post("/slack/events", content=b"{}")
        assert resp.status_code != 404


# ===========================================================================
# 6. Production docs disabled
# ===========================================================================


class TestProductionDocsDisabled:
    """When is_production is True, /docs and /redoc should return 404."""

    def test_docs_disabled_in_production(self, _slack_stubs):
        sys.modules.pop("src.api.app", None)

        with patch("src.config.settings") as mock_settings:
            mock_settings.is_production = True
            mock_settings.app_env = "production"
            mock_settings.slack_bot_token = ""
            mock_settings.slack_signing_secret = ""
            mock_settings.app_base_url = "https://sidera.example.com"
            mock_settings.google_ads_client_id = ""
            mock_settings.google_ads_client_secret = ""
            mock_settings.google_ads_developer_token = ""
            mock_settings.google_ads_login_customer_id = ""
            mock_settings.google_ads_refresh_token = ""
            mock_settings.meta_app_id = ""
            mock_settings.meta_app_secret = ""

            from src.api.app import create_app

            prod_app = create_app()

        prod_client = TestClient(prod_app)
        resp_docs = prod_client.get("/docs")
        assert resp_docs.status_code == 404

    def test_redoc_disabled_in_production(self, _slack_stubs):
        sys.modules.pop("src.api.app", None)

        with patch("src.config.settings") as mock_settings:
            mock_settings.is_production = True
            mock_settings.app_env = "production"
            mock_settings.slack_bot_token = ""
            mock_settings.slack_signing_secret = ""
            mock_settings.app_base_url = "https://sidera.example.com"
            mock_settings.google_ads_client_id = ""
            mock_settings.google_ads_client_secret = ""
            mock_settings.google_ads_developer_token = ""
            mock_settings.google_ads_login_customer_id = ""
            mock_settings.google_ads_refresh_token = ""
            mock_settings.meta_app_id = ""
            mock_settings.meta_app_secret = ""

            from src.api.app import create_app

            prod_app = create_app()

        prod_client = TestClient(prod_app)
        resp_redoc = prod_client.get("/redoc")
        assert resp_redoc.status_code == 404

    def test_docs_available_in_development(self, client):
        """In development (default), /docs should return 200."""
        resp = client.get("/docs")
        assert resp.status_code == 200


# ===========================================================================
# 7. Global error handler
# ===========================================================================


class TestGlobalErrorHandler:
    """The app-level exception handler should return 500 JSON."""

    def test_unhandled_exception_returns_500_json(self, fresh_app):
        """Add a route that raises, verify we get structured 500."""

        @fresh_app.get("/test-error")
        async def raise_error():
            raise RuntimeError("deliberate test error")

        error_client = TestClient(fresh_app, raise_server_exceptions=False)
        resp = error_client.get("/test-error")
        assert resp.status_code == 500
        data = resp.json()
        assert data["detail"] == "Internal server error"


# ===========================================================================
# 8. App is a FastAPI instance
# ===========================================================================


class TestAppInstance:
    """Verify structural properties of the created app."""

    def test_create_app_returns_fastapi(self, fresh_app):
        assert isinstance(fresh_app, FastAPI)

    def test_app_title(self, fresh_app):
        assert fresh_app.title == "Sidera"

    def test_app_version(self, fresh_app):
        assert fresh_app.version == "0.1.0"


# ===========================================================================
# 9. Inngest graceful degradation
# ===========================================================================


class TestInngestGracefulDegradation:
    """The app should start even if Inngest is not configured."""

    def test_app_starts_without_inngest(self, fresh_app):
        """Inngest modules don't exist yet, but app still creates."""
        assert fresh_app is not None
        # Health check still works
        test_client = TestClient(fresh_app)
        resp = test_client.get("/health")
        assert resp.status_code == 200
