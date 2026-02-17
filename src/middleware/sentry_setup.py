"""Sentry error monitoring setup for Sidera.

Initializes Sentry SDK with FastAPI integration when sentry_dsn is configured.
Gracefully does nothing when DSN is empty (dev/test environments).
"""

from __future__ import annotations

import structlog

from src.config import settings

logger = structlog.get_logger(__name__)

_sentry_initialized = False


def init_sentry() -> None:
    """Initialize Sentry SDK if a DSN is configured.

    Reads settings from ``src.config.settings``. When ``sentry_dsn`` is empty
    (the default), this function logs a message and returns immediately so the
    application can run without Sentry in dev/test environments.
    """
    global _sentry_initialized  # noqa: PLW0603

    try:
        if not settings.sentry_dsn:
            logger.info("sentry.skipped", reason="No DSN configured")
            return

        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration

        traces_sample_rate = 0.1 if settings.is_production else 1.0

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            traces_sample_rate=traces_sample_rate,
            profiles_sample_rate=0.1,
            environment=settings.app_env,
            release="sidera@0.1.0",
            enable_tracing=True,
            integrations=[FastApiIntegration()],
        )

        _sentry_initialized = True
        logger.info(
            "sentry.initialized",
            environment=settings.app_env,
            traces_sample_rate=traces_sample_rate,
        )
    except Exception as exc:
        logger.warning("sentry.init_failed", error=str(exc))


def capture_exception(exc: Exception) -> None:
    """Capture an exception in Sentry if it has been initialized.

    This is a thin wrapper around ``sentry_sdk.capture_exception`` that
    silently does nothing when Sentry is not active, ensuring callers never
    need to check initialization state themselves.
    """
    if not _sentry_initialized:
        return
    try:
        import sentry_sdk

        sentry_sdk.capture_exception(exc)
    except Exception:
        logger.warning("sentry.capture_failed", original_error=str(exc))


def set_user_context(user_id: str) -> None:
    """Set the Sentry user context for error grouping.

    Parameters
    ----------
    user_id:
        An identifier for the current user/account. Sentry uses this to group
        errors by user and provide per-user impact analysis.
    """
    if not _sentry_initialized:
        return
    try:
        import sentry_sdk

        sentry_sdk.set_user({"id": user_id})
    except Exception:
        logger.warning("sentry.set_user_failed", user_id=user_id)
