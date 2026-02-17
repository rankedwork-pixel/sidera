"""Tests for the request logging middleware."""

from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.middleware.request_logging import RequestLoggingMiddleware


def _make_app() -> FastAPI:
    """Create a minimal FastAPI app with request logging middleware."""
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    @app.get("/test")
    async def test_endpoint():
        return {"ok": True}

    @app.get("/error")
    async def error_endpoint():
        raise ValueError("boom")

    return app


class TestRequestLoggingMiddleware:
    """Tests for the RequestLoggingMiddleware."""

    def test_adds_request_id_header(self):
        """Response should include an X-Request-ID header."""
        client = TestClient(_make_app())
        resp = client.get("/test")
        assert resp.status_code == 200
        assert "X-Request-ID" in resp.headers
        # Should be a valid UUID-ish string
        request_id = resp.headers["X-Request-ID"]
        assert len(request_id) == 36  # UUID4 format: 8-4-4-4-12

    def test_health_check_skipped(self):
        """Health check requests should NOT get an X-Request-ID (skipped)."""
        client = TestClient(_make_app())
        resp = client.get("/health")
        assert resp.status_code == 200
        # The middleware skips /health, so no X-Request-ID is added
        assert "X-Request-ID" not in resp.headers

    def test_logs_request_start_and_complete(self):
        """Middleware should log request.start and request.complete events."""
        client = TestClient(_make_app())

        with patch("src.middleware.request_logging.logger") as mock_logger:
            resp = client.get("/test")
            assert resp.status_code == 200

            # Verify request.start was logged
            start_calls = [
                c
                for c in mock_logger.info.call_args_list
                if c.args and c.args[0] == "request.start"
            ]
            assert len(start_calls) == 1
            start_kwargs = start_calls[0].kwargs
            assert start_kwargs["method"] == "GET"
            assert start_kwargs["path"] == "/test"

            # Verify request.complete was logged
            complete_calls = [
                c
                for c in mock_logger.info.call_args_list
                if c.args and c.args[0] == "request.complete"
            ]
            assert len(complete_calls) == 1
            complete_kwargs = complete_calls[0].kwargs
            assert complete_kwargs["status_code"] == 200
            assert "duration_ms" in complete_kwargs
            assert complete_kwargs["duration_ms"] >= 0

    def test_timing_is_logged(self):
        """The duration_ms field should be a non-negative number."""
        client = TestClient(_make_app())

        with patch("src.middleware.request_logging.logger") as mock_logger:
            client.get("/test")

            complete_calls = [
                c
                for c in mock_logger.info.call_args_list
                if c.args and c.args[0] == "request.complete"
            ]
            assert len(complete_calls) == 1
            duration = complete_calls[0].kwargs["duration_ms"]
            assert isinstance(duration, float)
            assert duration >= 0

    def test_health_check_not_logged(self):
        """Health check requests should not produce log entries."""
        client = TestClient(_make_app())

        with patch("src.middleware.request_logging.logger") as mock_logger:
            client.get("/health")
            # Neither request.start nor request.complete should be logged
            for call in mock_logger.info.call_args_list:
                if call.args:
                    assert call.args[0] not in ("request.start", "request.complete")

    def test_unique_request_ids(self):
        """Each request should get a unique request ID."""
        client = TestClient(_make_app())
        ids = set()
        for _ in range(5):
            resp = client.get("/test")
            ids.add(resp.headers["X-Request-ID"])
        assert len(ids) == 5
