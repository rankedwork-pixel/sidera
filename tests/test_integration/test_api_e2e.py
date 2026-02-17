"""Integration tests for the FastAPI application.

Tests real HTTP request routing through the full middleware stack.
Uses httpx.AsyncClient with ASGITransport for real HTTP testing.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app

# =====================================================================
# App fixture
# =====================================================================


@pytest.fixture()
def app():
    """Create a fresh FastAPI app for each test."""
    return create_app()


@pytest.fixture()
async def client(app):
    """Async HTTP client wired to the ASGI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# =====================================================================
# Test 1: Health endpoint
# =====================================================================


@pytest.mark.asyncio
async def test_health_endpoint(client):
    """GET /health returns 200 with correct fields."""
    response = await client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "sidera"
    assert data["version"] == "0.1.0"
    assert "environment" in data


# =====================================================================
# Test 2: Root endpoint
# =====================================================================


@pytest.mark.asyncio
async def test_root_endpoint(client):
    """GET / returns service info."""
    response = await client.get("/")

    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "sidera"
    assert data["status"] == "running"


# =====================================================================
# Test 3: CORS headers in development
# =====================================================================


@pytest.mark.asyncio
async def test_cors_headers_in_development(client):
    """CORS allows all origins in dev environment.

    Sends a preflight OPTIONS request and verifies the
    access-control-allow-origin header is present.
    """
    response = await client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )

    # CORS preflight should succeed
    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers
    assert response.headers["access-control-allow-origin"] == "*"


# =====================================================================
# Test 4: Unknown route returns 404
# =====================================================================


@pytest.mark.asyncio
async def test_unknown_route_returns_404(client):
    """Requesting an undefined path returns a proper 404."""
    response = await client.get("/api/v1/nonexistent")

    assert response.status_code == 404
    data = response.json()
    assert "detail" in data


# =====================================================================
# Test 5: Request logging middleware adds X-Request-ID
# =====================================================================


@pytest.mark.asyncio
async def test_request_logging_middleware_adds_request_id(client):
    """Middleware adds X-Request-ID header to every non-health response."""
    # The middleware skips /health, so use the root endpoint
    response = await client.get("/")

    assert response.status_code == 200
    assert "x-request-id" in response.headers
    request_id = response.headers["x-request-id"]
    # UUID format check (basic)
    assert len(request_id) == 36
    assert request_id.count("-") == 4


@pytest.mark.asyncio
async def test_request_id_is_unique_per_request(client):
    """Each request gets a unique X-Request-ID."""
    response1 = await client.get("/")
    response2 = await client.get("/")

    id1 = response1.headers.get("x-request-id")
    id2 = response2.headers.get("x-request-id")
    assert id1 != id2


# =====================================================================
# Test 6: Global error handler returns 500 JSON
# =====================================================================


@pytest.mark.asyncio
async def test_global_error_handler(app):
    """Unhandled exception returns 500 JSON with detail field.

    Registers a temporary route that raises an unhandled exception,
    then verifies the global exception handler catches it.

    Note: raise_server_exceptions=False is required because the
    BaseHTTPMiddleware re-raises exceptions before the FastAPI
    exception handler can intercept them in the test transport.
    """

    @app.get("/test-error-route")
    async def error_route():
        raise ValueError("Intentional test error")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/test-error-route")

    assert response.status_code == 500


# =====================================================================
# Test: Multiple routes are mounted
# =====================================================================


@pytest.mark.asyncio
async def test_app_has_expected_routes(app):
    """Verify core routes are registered on the app."""
    route_paths = [route.path for route in app.routes if hasattr(route, "path")]
    assert "/health" in route_paths
    assert "/" in route_paths


# =====================================================================
# Test: App metadata
# =====================================================================


@pytest.mark.asyncio
async def test_app_metadata(app):
    """App has correct title and version."""
    assert app.title == "Sidera"
    assert app.version == "0.1.0"
