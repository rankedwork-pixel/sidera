"""Retry decorator with exponential backoff for Sidera connectors.

Provides a ``@retry_with_backoff`` decorator that retries functions on
transient errors (rate limits, server errors, timeouts) while immediately
re-raising permanent errors (authentication failures).

Usage::

    from src.connectors.retry import retry_with_backoff

    class MyConnector:
        @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
        def call_api(self, ...):
            ...
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import random
import time
from typing import Any, Callable, TypeVar

import structlog

logger = structlog.get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

# Auth error types that should never be retried
_PERMANENT_ERROR_TYPES = (
    "GoogleAdsAuthError",
    "MetaAuthError",
    "BigQueryAuthError",
    "GoogleDriveAuthError",
    "SlackAuthError",
    "RecallAIAuthError",
)

# Slack error strings indicating rate limiting or transient issues
_SLACK_TRANSIENT_ERRORS = frozenset(
    {
        "rate_limited",
        "service_unavailable",
        "request_timeout",
        "fatal_error",
        "internal_error",
    }
)

# Meta auth error codes — these are permanent
_META_AUTH_ERROR_CODES = {190, 102, 10}


def is_transient_error(exc: Exception) -> bool:
    """Classify an exception as transient (retryable) or permanent.

    Transient errors include rate limits, server errors, timeouts, and
    connection failures.  Permanent errors include authentication and
    authorization failures which must be surfaced to the user.

    Args:
        exc: The exception to classify.

    Returns:
        ``True`` if the error is transient and the operation should be
        retried, ``False`` if the error is permanent.
    """
    exc_type_name = type(exc).__name__

    # Our own auth error wrappers — always permanent
    if exc_type_name in _PERMANENT_ERROR_TYPES:
        return False

    # Built-in transient errors
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True

    # Google Ads: GoogleAdsException with non-auth error codes
    if exc_type_name == "GoogleAdsException":
        # Check if any error is auth-related
        if hasattr(exc, "failure") and hasattr(exc.failure, "errors"):
            for error in exc.failure.errors:
                error_code_name = str(getattr(error, "error_code", ""))
                if any(
                    kw in error_code_name.upper()
                    for kw in ("AUTHENTICATION", "AUTHORIZATION", "NOT_WHITELISTED")
                ):
                    return False  # Auth error — permanent
        return True  # Non-auth Google Ads error — transient

    # Meta: FacebookRequestError
    if exc_type_name == "FacebookRequestError":
        error_code = None
        if hasattr(exc, "api_error_code"):
            error_code = exc.api_error_code()
        if error_code is not None:
            if error_code in _META_AUTH_ERROR_CODES:
                return False  # Auth error — permanent
            if 200 <= error_code < 300:
                return False  # Permission error — permanent
        return True  # Other Meta error — transient

    # BigQuery: GoogleAPICallError
    if exc_type_name == "GoogleAPICallError" or (
        hasattr(exc, "grpc_status_code") or hasattr(exc, "code")
    ):
        # Check for Forbidden (403) — auth error
        if exc_type_name == "Forbidden":
            return False
        # Other BQ errors (InternalServerError, ServiceUnavailable, etc.)
        return True

    # Google Drive / Workspace: HttpError
    if exc_type_name == "HttpError":
        status_code = 0
        if hasattr(exc, "resp") and hasattr(exc.resp, "status"):
            status_code = exc.resp.status
        elif hasattr(exc, "status_code"):
            status_code = exc.status_code
        # Auth errors
        if status_code in (401, 403):
            return False
        # Rate limit or server errors
        if status_code in (429, 500, 502, 503, 504):
            return True
        return False  # Other HTTP errors (4xx) — permanent

    # Slack: SlackApiError
    if exc_type_name == "SlackApiError":
        error_type = ""
        if hasattr(exc, "response") and exc.response:
            error_type = exc.response.get("error", "")
        if error_type in _SLACK_TRANSIENT_ERRORS:
            return True
        return False  # Other Slack errors — permanent

    # httpx errors
    if exc_type_name in ("ConnectTimeout", "ReadTimeout", "ConnectError"):
        return True

    # Default: not transient (safer to fail fast than retry forever)
    return False


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> Callable[[F], F]:
    """Decorator that retries a function on transient errors with exponential backoff.

    Supports both sync and async functions.  On each retry, the delay is
    ``base_delay * 2^attempt * jitter`` where jitter is uniform in [0.5, 1.5].
    Permanent errors (authentication failures) are re-raised immediately.

    Args:
        max_retries: Maximum number of retry attempts (default 3).
        base_delay: Base delay in seconds before first retry (default 1.0).
        max_delay: Maximum delay in seconds between retries (default 30.0).

    Returns:
        Decorated function with retry logic.

    Example::

        @retry_with_backoff(max_retries=3, base_delay=1.0)
        def fetch_data(self):
            ...
    """

    def decorator(func: F) -> F:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                last_exc: Exception | None = None
                for attempt in range(max_retries + 1):
                    try:
                        return await func(*args, **kwargs)
                    except Exception as exc:
                        last_exc = exc
                        if not is_transient_error(exc):
                            raise  # Permanent error — do not retry

                        if attempt >= max_retries:
                            logger.warning(
                                "retry.exhausted",
                                func=func.__qualname__,
                                attempt=attempt + 1,
                                max_retries=max_retries,
                                error=str(exc),
                            )
                            raise

                        delay = _compute_delay(attempt, base_delay, max_delay)
                        logger.info(
                            "retry.attempt",
                            func=func.__qualname__,
                            attempt=attempt + 1,
                            max_retries=max_retries,
                            delay_seconds=round(delay, 2),
                            error=str(exc),
                        )
                        await asyncio.sleep(delay)

                # Should not reach here, but satisfy type checker
                if last_exc is not None:
                    raise last_exc  # pragma: no cover

            return async_wrapper  # type: ignore[return-value]

        else:

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                last_exc: Exception | None = None
                for attempt in range(max_retries + 1):
                    try:
                        return func(*args, **kwargs)
                    except Exception as exc:
                        last_exc = exc
                        if not is_transient_error(exc):
                            raise  # Permanent error — do not retry

                        if attempt >= max_retries:
                            logger.warning(
                                "retry.exhausted",
                                func=func.__qualname__,
                                attempt=attempt + 1,
                                max_retries=max_retries,
                                error=str(exc),
                            )
                            raise

                        delay = _compute_delay(attempt, base_delay, max_delay)
                        logger.info(
                            "retry.attempt",
                            func=func.__qualname__,
                            attempt=attempt + 1,
                            max_retries=max_retries,
                            delay_seconds=round(delay, 2),
                            error=str(exc),
                        )
                        time.sleep(delay)

                # Should not reach here, but satisfy type checker
                if last_exc is not None:
                    raise last_exc  # pragma: no cover

            return sync_wrapper  # type: ignore[return-value]

    return decorator


def _compute_delay(attempt: int, base_delay: float, max_delay: float) -> float:
    """Compute the delay for a given retry attempt.

    Uses exponential backoff with ±50% jitter::

        delay = min(base_delay * 2^attempt * uniform(0.5, 1.5), max_delay)

    Args:
        attempt: Zero-based attempt number.
        base_delay: Base delay in seconds.
        max_delay: Maximum delay cap in seconds.

    Returns:
        Delay in seconds.
    """
    jitter = random.uniform(0.5, 1.5)
    delay = base_delay * (2**attempt) * jitter
    return min(delay, max_delay)
