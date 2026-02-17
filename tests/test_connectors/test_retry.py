"""Tests for src.connectors.retry — retry_with_backoff decorator and error classification.

Covers the retry decorator for both sync and async functions, the
``is_transient_error`` classifier for every supported platform, backoff
timing/jitter, structlog logging, and edge cases.

All external dependencies (sleep, asyncio.sleep, structlog) are mocked.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors.retry import (
    _compute_delay,
    is_transient_error,
    retry_with_backoff,
)

# ---------------------------------------------------------------------------
# Helpers — fake exception classes for each platform
# ---------------------------------------------------------------------------


def _make_exc(name: str, bases: tuple = (Exception,), attrs: dict | None = None):
    """Dynamically create an exception class with the given name."""
    return type(name, bases, attrs or {})


# Google Ads
GoogleAdsException = _make_exc("GoogleAdsException")
GoogleAdsAuthError = _make_exc("GoogleAdsAuthError")

# Meta
FacebookRequestError = _make_exc("FacebookRequestError")
MetaAuthError = _make_exc("MetaAuthError")

# BigQuery
GoogleAPICallError = _make_exc("GoogleAPICallError")
Forbidden = _make_exc("Forbidden")
BigQueryAuthError = _make_exc("BigQueryAuthError")

# Google Drive
HttpError = _make_exc("HttpError")
GoogleDriveAuthError = _make_exc("GoogleDriveAuthError")

# Slack
SlackApiError = _make_exc("SlackApiError")
SlackAuthError = _make_exc("SlackAuthError")

# httpx
ConnectTimeout = _make_exc("ConnectTimeout")
ReadTimeout = _make_exc("ReadTimeout")
ConnectError = _make_exc("ConnectError")


# ---------------------------------------------------------------------------
# 1. is_transient_error — platform error classifiers
# ---------------------------------------------------------------------------


class TestIsTransientError:
    """Tests for the ``is_transient_error`` function."""

    # -- Built-in transient errors ------------------------------------------

    def test_connection_error_is_transient(self):
        assert is_transient_error(ConnectionError("connection reset")) is True

    def test_timeout_error_is_transient(self):
        assert is_transient_error(TimeoutError("timed out")) is True

    def test_os_error_is_transient(self):
        assert is_transient_error(OSError("network unreachable")) is True

    # -- Default unknown exception ------------------------------------------

    def test_unknown_exception_is_not_transient(self):
        assert is_transient_error(ValueError("unexpected")) is False

    def test_plain_runtime_error_is_not_transient(self):
        assert is_transient_error(RuntimeError("oops")) is False

    # -- Permanent auth error wrappers --------------------------------------

    def test_google_ads_auth_error_is_permanent(self):
        assert is_transient_error(GoogleAdsAuthError("bad token")) is False

    def test_meta_auth_error_is_permanent(self):
        assert is_transient_error(MetaAuthError("bad token")) is False

    def test_bigquery_auth_error_is_permanent(self):
        assert is_transient_error(BigQueryAuthError("bad token")) is False

    def test_google_drive_auth_error_is_permanent(self):
        assert is_transient_error(GoogleDriveAuthError("bad token")) is False

    def test_slack_auth_error_is_permanent(self):
        assert is_transient_error(SlackAuthError("bad token")) is False

    # -- Google Ads: GoogleAdsException -------------------------------------

    def test_google_ads_exception_without_auth_is_transient(self):
        exc = GoogleAdsException("rate limited")
        # No failure attribute -> treated as transient
        assert is_transient_error(exc) is True

    def test_google_ads_exception_with_non_auth_errors_is_transient(self):
        error = MagicMock()
        error.error_code = "RESOURCE_EXHAUSTED"
        exc = GoogleAdsException("rate limited")
        exc.failure = SimpleNamespace(errors=[error])
        assert is_transient_error(exc) is True

    def test_google_ads_exception_with_auth_error_is_permanent(self):
        error = MagicMock()
        error.error_code = "AUTHENTICATION_ERROR"
        exc = GoogleAdsException("auth failed")
        exc.failure = SimpleNamespace(errors=[error])
        assert is_transient_error(exc) is False

    def test_google_ads_exception_with_authorization_error_is_permanent(self):
        error = MagicMock()
        error.error_code = "AUTHORIZATION_ERROR"
        exc = GoogleAdsException("not authorized")
        exc.failure = SimpleNamespace(errors=[error])
        assert is_transient_error(exc) is False

    def test_google_ads_exception_with_not_whitelisted_error_is_permanent(self):
        error = MagicMock()
        error.error_code = "NOT_WHITELISTED"
        exc = GoogleAdsException("not whitelisted")
        exc.failure = SimpleNamespace(errors=[error])
        assert is_transient_error(exc) is False

    # -- Meta: FacebookRequestError -----------------------------------------

    def test_facebook_request_error_without_code_is_transient(self):
        exc = FacebookRequestError("rate limited")
        assert is_transient_error(exc) is True

    def test_facebook_request_error_transient_code_is_transient(self):
        exc = FacebookRequestError("throttled")
        exc.api_error_code = lambda: 4  # Throttling code, not in auth set
        assert is_transient_error(exc) is True

    def test_facebook_request_error_auth_code_190_is_permanent(self):
        exc = FacebookRequestError("auth error")
        exc.api_error_code = lambda: 190
        assert is_transient_error(exc) is False

    def test_facebook_request_error_auth_code_102_is_permanent(self):
        exc = FacebookRequestError("auth error")
        exc.api_error_code = lambda: 102
        assert is_transient_error(exc) is False

    def test_facebook_request_error_auth_code_10_is_permanent(self):
        exc = FacebookRequestError("auth error")
        exc.api_error_code = lambda: 10
        assert is_transient_error(exc) is False

    def test_facebook_request_error_permission_code_200_is_permanent(self):
        exc = FacebookRequestError("permission error")
        exc.api_error_code = lambda: 200
        assert is_transient_error(exc) is False

    def test_facebook_request_error_permission_code_250_is_permanent(self):
        exc = FacebookRequestError("permission error")
        exc.api_error_code = lambda: 250
        assert is_transient_error(exc) is False

    # -- BigQuery: GoogleAPICallError / Forbidden ---------------------------

    def test_google_api_call_error_is_transient(self):
        exc = GoogleAPICallError("server error")
        assert is_transient_error(exc) is True

    def test_forbidden_error_is_permanent(self):
        exc = Forbidden("forbidden")
        assert is_transient_error(exc) is False

    def test_exception_with_grpc_status_code_is_transient(self):
        """Any exception with grpc_status_code attr is treated as BQ error."""
        exc = Exception("bq error")
        exc.grpc_status_code = 14  # UNAVAILABLE
        assert is_transient_error(exc) is True

    # -- Google Drive: HttpError --------------------------------------------

    def test_http_error_429_is_transient(self):
        exc = HttpError("too many requests")
        exc.resp = SimpleNamespace(status=429)
        assert is_transient_error(exc) is True

    def test_http_error_500_is_transient(self):
        exc = HttpError("internal server error")
        exc.resp = SimpleNamespace(status=500)
        assert is_transient_error(exc) is True

    def test_http_error_502_is_transient(self):
        exc = HttpError("bad gateway")
        exc.resp = SimpleNamespace(status=502)
        assert is_transient_error(exc) is True

    def test_http_error_503_is_transient(self):
        exc = HttpError("service unavailable")
        exc.resp = SimpleNamespace(status=503)
        assert is_transient_error(exc) is True

    def test_http_error_504_is_transient(self):
        exc = HttpError("gateway timeout")
        exc.resp = SimpleNamespace(status=504)
        assert is_transient_error(exc) is True

    def test_http_error_401_is_permanent(self):
        exc = HttpError("unauthorized")
        exc.resp = SimpleNamespace(status=401)
        assert is_transient_error(exc) is False

    def test_http_error_403_is_permanent(self):
        exc = HttpError("forbidden")
        exc.resp = SimpleNamespace(status=403)
        assert is_transient_error(exc) is False

    def test_http_error_404_is_permanent(self):
        exc = HttpError("not found")
        exc.resp = SimpleNamespace(status=404)
        assert is_transient_error(exc) is False

    def test_http_error_with_status_code_attr(self):
        """HttpError using ``status_code`` instead of ``resp.status``."""
        exc = HttpError("rate limited")
        exc.status_code = 429
        assert is_transient_error(exc) is True

    def test_http_error_no_status_info_is_permanent(self):
        """HttpError with no resp or status_code falls to status=0 -> permanent."""
        exc = HttpError("unknown")
        assert is_transient_error(exc) is False

    # -- Slack: SlackApiError -----------------------------------------------

    def test_slack_rate_limited_is_transient(self):
        exc = SlackApiError("rate limited")
        exc.response = {"error": "rate_limited"}
        assert is_transient_error(exc) is True

    def test_slack_service_unavailable_is_transient(self):
        exc = SlackApiError("service unavailable")
        exc.response = {"error": "service_unavailable"}
        assert is_transient_error(exc) is True

    def test_slack_request_timeout_is_transient(self):
        exc = SlackApiError("timeout")
        exc.response = {"error": "request_timeout"}
        assert is_transient_error(exc) is True

    def test_slack_fatal_error_is_transient(self):
        exc = SlackApiError("fatal")
        exc.response = {"error": "fatal_error"}
        assert is_transient_error(exc) is True

    def test_slack_internal_error_is_transient(self):
        exc = SlackApiError("internal")
        exc.response = {"error": "internal_error"}
        assert is_transient_error(exc) is True

    def test_slack_other_error_is_permanent(self):
        exc = SlackApiError("channel not found")
        exc.response = {"error": "channel_not_found"}
        assert is_transient_error(exc) is False

    def test_slack_error_no_response_is_permanent(self):
        exc = SlackApiError("bad response")
        exc.response = None
        assert is_transient_error(exc) is False

    # -- httpx transient errors ---------------------------------------------

    def test_connect_timeout_is_transient(self):
        assert is_transient_error(ConnectTimeout("timed out")) is True

    def test_read_timeout_is_transient(self):
        assert is_transient_error(ReadTimeout("timed out")) is True

    def test_connect_error_is_transient(self):
        assert is_transient_error(ConnectError("connection failed")) is True


# ---------------------------------------------------------------------------
# 2. _compute_delay — backoff timing and jitter
# ---------------------------------------------------------------------------


class TestComputeDelay:
    """Tests for the ``_compute_delay`` helper."""

    @patch("src.connectors.retry.random.uniform", return_value=1.0)
    def test_attempt_zero_no_jitter(self, mock_uniform):
        # delay = 1.0 * 2^0 * 1.0 = 1.0
        assert _compute_delay(0, base_delay=1.0, max_delay=30.0) == 1.0

    @patch("src.connectors.retry.random.uniform", return_value=1.0)
    def test_attempt_one_doubles(self, mock_uniform):
        # delay = 1.0 * 2^1 * 1.0 = 2.0
        assert _compute_delay(1, base_delay=1.0, max_delay=30.0) == 2.0

    @patch("src.connectors.retry.random.uniform", return_value=1.0)
    def test_attempt_two_quadruples(self, mock_uniform):
        # delay = 1.0 * 2^2 * 1.0 = 4.0
        assert _compute_delay(2, base_delay=1.0, max_delay=30.0) == 4.0

    @patch("src.connectors.retry.random.uniform", return_value=1.5)
    def test_max_jitter(self, mock_uniform):
        # delay = 1.0 * 2^0 * 1.5 = 1.5
        assert _compute_delay(0, base_delay=1.0, max_delay=30.0) == 1.5

    @patch("src.connectors.retry.random.uniform", return_value=0.5)
    def test_min_jitter(self, mock_uniform):
        # delay = 1.0 * 2^0 * 0.5 = 0.5
        assert _compute_delay(0, base_delay=1.0, max_delay=30.0) == 0.5

    @patch("src.connectors.retry.random.uniform", return_value=1.5)
    def test_delay_capped_at_max(self, mock_uniform):
        # delay = 1.0 * 2^10 * 1.5 = 1536.0, capped at 30.0
        assert _compute_delay(10, base_delay=1.0, max_delay=30.0) == 30.0

    @patch("src.connectors.retry.random.uniform", return_value=1.0)
    def test_custom_base_delay(self, mock_uniform):
        # delay = 2.5 * 2^1 * 1.0 = 5.0
        assert _compute_delay(1, base_delay=2.5, max_delay=60.0) == 5.0

    def test_jitter_within_bounds(self):
        """Run many iterations and check jitter is always in [0.5, 1.5]."""
        for _ in range(100):
            delay = _compute_delay(0, base_delay=1.0, max_delay=100.0)
            assert 0.5 <= delay <= 1.5


# ---------------------------------------------------------------------------
# 3. retry_with_backoff — sync functions
# ---------------------------------------------------------------------------


class TestRetryWithBackoffSync:
    """Tests for the retry decorator wrapping synchronous functions."""

    @patch("src.connectors.retry.time.sleep")
    def test_success_no_retry(self, mock_sleep):
        """Function succeeds on first call; no retry or sleep needed."""

        @retry_with_backoff(max_retries=3)
        def ok():
            return "done"

        assert ok() == "done"
        mock_sleep.assert_not_called()

    @patch("src.connectors.retry.time.sleep")
    def test_retry_on_transient_then_succeed(self, mock_sleep):
        """Function fails once with transient error, succeeds on second call."""
        call_count = 0

        @retry_with_backoff(max_retries=3, base_delay=1.0)
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("first call fails")
            return "recovered"

        assert flaky() == "recovered"
        assert call_count == 2
        assert mock_sleep.call_count == 1

    @patch("src.connectors.retry.time.sleep")
    def test_permanent_error_no_retry(self, mock_sleep):
        """Permanent errors are raised immediately without retry."""

        @retry_with_backoff(max_retries=3)
        def fail_auth():
            raise GoogleAdsAuthError("bad token")

        with pytest.raises(GoogleAdsAuthError, match="bad token"):
            fail_auth()

        mock_sleep.assert_not_called()

    @patch("src.connectors.retry.time.sleep")
    def test_max_retries_exceeded(self, mock_sleep):
        """After exhausting retries, the last transient error is raised."""

        @retry_with_backoff(max_retries=2, base_delay=0.1)
        def always_fail():
            raise ConnectionError("down")

        with pytest.raises(ConnectionError, match="down"):
            always_fail()

        # 2 retries => 2 sleeps (attempt 0 -> sleep, attempt 1 -> sleep, attempt 2 -> raise)
        assert mock_sleep.call_count == 2

    @patch("src.connectors.retry.time.sleep")
    def test_succeed_on_last_retry(self, mock_sleep):
        """Function succeeds on the very last allowed attempt."""
        call_count = 0

        @retry_with_backoff(max_retries=3, base_delay=0.1)
        def last_chance():
            nonlocal call_count
            call_count += 1
            if call_count <= 3:  # Fails attempts 0, 1, 2; succeeds on attempt 3
                raise TimeoutError("still timing out")
            return "finally"

        assert last_chance() == "finally"
        assert call_count == 4
        assert mock_sleep.call_count == 3

    @patch("src.connectors.retry.time.sleep")
    def test_custom_parameters(self, mock_sleep):
        """Custom max_retries, base_delay, and max_delay are respected."""
        call_count = 0

        @retry_with_backoff(max_retries=5, base_delay=0.5, max_delay=10.0)
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count <= 4:
                raise ConnectionError("not yet")
            return "ok"

        assert flaky() == "ok"
        assert call_count == 5
        assert mock_sleep.call_count == 4

    @patch("src.connectors.retry.time.sleep")
    def test_always_fails_sync(self, mock_sleep):
        """Function that always fails raises after max retries."""

        @retry_with_backoff(max_retries=1, base_delay=0.1)
        def doom():
            raise OSError("permanent doom")

        with pytest.raises(OSError, match="permanent doom"):
            doom()

        assert mock_sleep.call_count == 1

    @patch("src.connectors.retry.time.sleep")
    def test_preserves_return_value(self, mock_sleep):
        """Decorated function preserves return value."""

        @retry_with_backoff()
        def get_data():
            return {"key": "value", "count": 42}

        result = get_data()
        assert result == {"key": "value", "count": 42}

    @patch("src.connectors.retry.time.sleep")
    def test_preserves_function_name(self, mock_sleep):
        """functools.wraps preserves __name__ and __qualname__."""

        @retry_with_backoff()
        def my_function():
            pass

        assert my_function.__name__ == "my_function"

    @patch("src.connectors.retry.time.sleep")
    def test_passes_args_and_kwargs(self, mock_sleep):
        """Arguments and keyword arguments are passed through correctly."""

        @retry_with_backoff()
        def add(a, b, extra=0):
            return a + b + extra

        assert add(1, 2, extra=3) == 6

    @patch("src.connectors.retry.time.sleep")
    @patch("src.connectors.retry.random.uniform", return_value=1.0)
    def test_backoff_delay_increases(self, mock_uniform, mock_sleep):
        """Verify sleep is called with increasing delays."""
        call_count = 0

        @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=100.0)
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise ConnectionError("fail")
            return "ok"

        flaky()

        delays = [call.args[0] for call in mock_sleep.call_args_list]
        # With jitter=1.0: attempt 0 -> 1.0, attempt 1 -> 2.0, attempt 2 -> 4.0
        assert delays == [1.0, 2.0, 4.0]


# ---------------------------------------------------------------------------
# 4. retry_with_backoff — async functions
# ---------------------------------------------------------------------------


class TestRetryWithBackoffAsync:
    """Tests for the retry decorator wrapping asynchronous functions."""

    @pytest.mark.asyncio
    @patch("src.connectors.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_async_success_no_retry(self, mock_sleep):
        """Async function succeeds on first call; no retry."""

        @retry_with_backoff(max_retries=3)
        async def ok():
            return "async done"

        result = await ok()
        assert result == "async done"
        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.connectors.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_async_retry_on_transient_then_succeed(self, mock_sleep):
        """Async function fails once, then succeeds on retry."""
        call_count = 0

        @retry_with_backoff(max_retries=3, base_delay=1.0)
        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("first call fails")
            return "recovered"

        result = await flaky()
        assert result == "recovered"
        assert call_count == 2
        assert mock_sleep.call_count == 1

    @pytest.mark.asyncio
    @patch("src.connectors.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_async_permanent_error_no_retry(self, mock_sleep):
        """Async: permanent errors raised immediately."""

        @retry_with_backoff(max_retries=3)
        async def fail_auth():
            raise MetaAuthError("invalid token")

        with pytest.raises(MetaAuthError, match="invalid token"):
            await fail_auth()

        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.connectors.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_async_max_retries_exceeded(self, mock_sleep):
        """Async: after exhausting retries, last error is raised."""

        @retry_with_backoff(max_retries=2, base_delay=0.1)
        async def always_fail():
            raise TimeoutError("async timeout")

        with pytest.raises(TimeoutError, match="async timeout"):
            await always_fail()

        assert mock_sleep.call_count == 2

    @pytest.mark.asyncio
    @patch("src.connectors.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_async_succeed_on_last_retry(self, mock_sleep):
        """Async function succeeds on the very last allowed attempt."""
        call_count = 0

        @retry_with_backoff(max_retries=2, base_delay=0.1)
        async def last_chance():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ConnectionError("not yet")
            return "made it"

        result = await last_chance()
        assert result == "made it"
        assert call_count == 3
        assert mock_sleep.call_count == 2

    @pytest.mark.asyncio
    @patch("src.connectors.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_async_always_fails(self, mock_sleep):
        """Async function that always fails raises after max retries."""

        @retry_with_backoff(max_retries=1, base_delay=0.1)
        async def doom():
            raise OSError("async doom")

        with pytest.raises(OSError, match="async doom"):
            await doom()

        assert mock_sleep.call_count == 1

    @pytest.mark.asyncio
    @patch("src.connectors.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_async_passes_args_and_kwargs(self, mock_sleep):
        """Async: arguments and keyword arguments are passed through."""

        @retry_with_backoff()
        async def multiply(a, b, factor=1):
            return a * b * factor

        result = await multiply(3, 4, factor=2)
        assert result == 24

    @pytest.mark.asyncio
    @patch("src.connectors.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_async_preserves_function_name(self, mock_sleep):
        """functools.wraps preserves __name__ for async functions."""

        @retry_with_backoff()
        async def my_async_func():
            pass

        assert my_async_func.__name__ == "my_async_func"

    @pytest.mark.asyncio
    @patch("src.connectors.retry.asyncio.sleep", new_callable=AsyncMock)
    @patch("src.connectors.retry.random.uniform", return_value=1.0)
    async def test_async_backoff_delay_increases(self, mock_uniform, mock_sleep):
        """Verify asyncio.sleep is called with increasing delays."""
        call_count = 0

        @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=100.0)
        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise ConnectionError("fail")
            return "ok"

        await flaky()

        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [1.0, 2.0, 4.0]


# ---------------------------------------------------------------------------
# 5. structlog logging
# ---------------------------------------------------------------------------


class TestRetryLogging:
    """Tests that structlog messages are emitted on retries and exhaustion."""

    @patch("src.connectors.retry.time.sleep")
    @patch("src.connectors.retry.logger")
    def test_logs_retry_attempt(self, mock_logger, mock_sleep):
        """An info log is emitted on each retry attempt."""
        call_count = 0

        @retry_with_backoff(max_retries=3, base_delay=0.1)
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("fail once")
            return "ok"

        flaky()

        mock_logger.info.assert_called_once()
        call_kwargs = mock_logger.info.call_args
        assert call_kwargs.args[0] == "retry.attempt"
        assert call_kwargs.kwargs["attempt"] == 1
        assert call_kwargs.kwargs["max_retries"] == 3

    @patch("src.connectors.retry.time.sleep")
    @patch("src.connectors.retry.logger")
    def test_logs_exhausted_on_max_retries(self, mock_logger, mock_sleep):
        """A warning log is emitted when retries are exhausted."""

        @retry_with_backoff(max_retries=1, base_delay=0.1)
        def always_fail():
            raise ConnectionError("always fails")

        with pytest.raises(ConnectionError):
            always_fail()

        mock_logger.warning.assert_called_once()
        call_kwargs = mock_logger.warning.call_args
        assert call_kwargs.args[0] == "retry.exhausted"
        assert call_kwargs.kwargs["attempt"] == 2  # attempt is 1-indexed in log
        assert call_kwargs.kwargs["max_retries"] == 1

    @patch("src.connectors.retry.time.sleep")
    @patch("src.connectors.retry.logger")
    def test_no_log_on_permanent_error(self, mock_logger, mock_sleep):
        """No retry or exhaustion log when a permanent error is raised."""

        @retry_with_backoff(max_retries=3)
        def fail_auth():
            raise SlackAuthError("invalid token")

        with pytest.raises(SlackAuthError):
            fail_auth()

        mock_logger.info.assert_not_called()
        mock_logger.warning.assert_not_called()

    @patch("src.connectors.retry.time.sleep")
    @patch("src.connectors.retry.logger")
    def test_no_log_on_success(self, mock_logger, mock_sleep):
        """No logs emitted when function succeeds on first attempt."""

        @retry_with_backoff()
        def ok():
            return "fine"

        ok()

        mock_logger.info.assert_not_called()
        mock_logger.warning.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.connectors.retry.asyncio.sleep", new_callable=AsyncMock)
    @patch("src.connectors.retry.logger")
    async def test_async_logs_retry_attempt(self, mock_logger, mock_sleep):
        """Async: info log on retry."""
        call_count = 0

        @retry_with_backoff(max_retries=2, base_delay=0.1)
        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise TimeoutError("slow")
            return "done"

        await flaky()

        mock_logger.info.assert_called_once()
        assert mock_logger.info.call_args.args[0] == "retry.attempt"

    @pytest.mark.asyncio
    @patch("src.connectors.retry.asyncio.sleep", new_callable=AsyncMock)
    @patch("src.connectors.retry.logger")
    async def test_async_logs_exhausted(self, mock_logger, mock_sleep):
        """Async: warning log on exhaustion."""

        @retry_with_backoff(max_retries=1, base_delay=0.1)
        async def always_fail():
            raise ConnectionError("gone")

        with pytest.raises(ConnectionError):
            await always_fail()

        mock_logger.warning.assert_called_once()
        assert mock_logger.warning.call_args.args[0] == "retry.exhausted"


# ---------------------------------------------------------------------------
# 6. Decorator with custom parameters
# ---------------------------------------------------------------------------


class TestCustomDecoratorParameters:
    """Tests for the decorator with non-default parameters."""

    @patch("src.connectors.retry.time.sleep")
    def test_zero_retries_raises_immediately(self, mock_sleep):
        """max_retries=0 means no retries at all."""

        @retry_with_backoff(max_retries=0)
        def fail():
            raise ConnectionError("no retries")

        with pytest.raises(ConnectionError, match="no retries"):
            fail()

        mock_sleep.assert_not_called()

    @patch("src.connectors.retry.time.sleep")
    @patch("src.connectors.retry.random.uniform", return_value=1.5)
    def test_max_delay_caps_large_backoff(self, mock_uniform, mock_sleep):
        """Delay never exceeds max_delay even with high jitter."""
        call_count = 0

        @retry_with_backoff(max_retries=5, base_delay=10.0, max_delay=15.0)
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise ConnectionError("fail")
            return "ok"

        flaky()

        for call in mock_sleep.call_args_list:
            assert call.args[0] <= 15.0

    @patch("src.connectors.retry.time.sleep")
    def test_default_parameters(self, mock_sleep):
        """Default parameters: max_retries=3, base_delay=1.0, max_delay=30.0."""
        call_count = 0

        @retry_with_backoff()
        def fail_three_times():
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise ConnectionError("fail")
            return "ok"

        result = fail_three_times()
        assert result == "ok"
        assert call_count == 4
        assert mock_sleep.call_count == 3


# ---------------------------------------------------------------------------
# 7. Mixed error scenarios
# ---------------------------------------------------------------------------


class TestMixedErrorScenarios:
    """Tests for sequences of transient then permanent errors, and vice versa."""

    @patch("src.connectors.retry.time.sleep")
    def test_transient_then_permanent_raises_permanent(self, mock_sleep):
        """If transient error is followed by permanent, permanent is raised."""
        call_count = 0

        @retry_with_backoff(max_retries=3)
        def mixed():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("transient")
            raise GoogleAdsAuthError("permanent")

        with pytest.raises(GoogleAdsAuthError, match="permanent"):
            mixed()

        assert call_count == 2
        assert mock_sleep.call_count == 1  # Slept after first transient

    @patch("src.connectors.retry.time.sleep")
    def test_permanent_on_first_call_no_sleep(self, mock_sleep):
        """Permanent error on first call: no sleep, immediate raise."""

        @retry_with_backoff(max_retries=3)
        def fail_fast():
            raise MetaAuthError("bad")

        with pytest.raises(MetaAuthError):
            fail_fast()

        mock_sleep.assert_not_called()

    @patch("src.connectors.retry.time.sleep")
    def test_different_transient_errors(self, mock_sleep):
        """Different transient errors on each attempt all trigger retry."""
        call_count = 0
        errors = [
            ConnectionError("conn"),
            TimeoutError("timeout"),
            OSError("os error"),
        ]

        @retry_with_backoff(max_retries=3, base_delay=0.1)
        def varied():
            nonlocal call_count
            if call_count < len(errors):
                exc = errors[call_count]
                call_count += 1
                raise exc
            call_count += 1
            return "recovered"

        result = varied()
        assert result == "recovered"
        assert call_count == 4
        assert mock_sleep.call_count == 3

    @pytest.mark.asyncio
    @patch("src.connectors.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_async_transient_then_permanent(self, mock_sleep):
        """Async: transient followed by permanent raises permanent."""
        call_count = 0

        @retry_with_backoff(max_retries=3)
        async def mixed():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError("slow")
            raise SlackAuthError("bad token")

        with pytest.raises(SlackAuthError, match="bad token"):
            await mixed()

        assert call_count == 2
        assert mock_sleep.call_count == 1
