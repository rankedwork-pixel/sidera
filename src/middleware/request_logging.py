"""Structured HTTP request/response logging middleware for Sidera.

Logs every API request with timing, status code, and relevant metadata
using structlog for consistent structured output.
"""

from __future__ import annotations

import time
import uuid

import structlog
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = structlog.get_logger(__name__)

# Paths that should not be logged to avoid noise.
_SKIP_PATHS = frozenset({"/health"})


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request with timing information.

    For each request this middleware:
    * Generates a unique ``X-Request-ID`` and attaches it to the response.
    * Binds the request ID to the structlog context so all log lines emitted
      during the request carry it.
    * Logs the request start (method, path, client IP).
    * Logs the request completion (status code, duration in milliseconds).
    * Skips logging entirely for health-check endpoints to reduce noise.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Skip noisy health-check endpoints
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)

        request_id = str(uuid.uuid4())
        client_ip = request.client.host if request.client else "unknown"

        # Bind request_id so downstream log calls include it automatically
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        logger.info(
            "request.start",
            method=request.method,
            path=request.url.path,
            client_ip=client_ip,
        )

        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.monotonic() - start) * 1000, 2)
            logger.error(
                "request.error",
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
            )
            raise
        finally:
            structlog.contextvars.unbind_contextvars("request_id")

        duration_ms = round((time.monotonic() - start) * 1000, 2)

        logger.info(
            "request.complete",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

        response.headers["X-Request-ID"] = request_id
        return response
