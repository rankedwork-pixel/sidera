"""Structlog configuration for Sidera.

Configures structured logging with:
- JSON output in production (machine-parseable for log aggregation)
- Pretty console output in development (human-readable)
- Correlation ID propagation via contextvars
- Standard processors: timestamps, log levels, caller info

Must be called early in app startup (before any logger is used).

Usage:
    from src.middleware.logging_config import configure_logging
    configure_logging()
"""

from __future__ import annotations

import logging
import sys

import structlog

from src.config import settings

_configured = False


def configure_logging() -> None:
    """Configure structlog and stdlib logging for the current environment.

    Safe to call multiple times — only the first call takes effect.
    Skips configuration when running under pytest to preserve caplog
    compatibility with structlog's default processors.
    """
    global _configured
    if _configured:
        return
    _configured = True

    # Skip structlog configuration during tests — pytest's caplog
    # relies on structlog's default processor chain. Configuring
    # structlog with ConsoleRenderer/JSONRenderer breaks caplog capture.
    if "pytest" in sys.modules:
        return

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if settings.is_production:
        # Production: JSON lines for log aggregation (Datadog, CloudWatch, etc.)
        renderer = structlog.processors.JSONRenderer()
    else:
        # Development: colorized, human-readable console output
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Also configure stdlib logging to route through structlog
    log_level = getattr(logging, settings.app_log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # Quiet noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
