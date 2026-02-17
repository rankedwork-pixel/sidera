"""Tests for Sentry error monitoring setup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_sentry_state():
    """Reset the module-level _sentry_initialized flag between tests."""
    import src.middleware.sentry_setup as mod

    original = mod._sentry_initialized
    mod._sentry_initialized = False
    yield
    mod._sentry_initialized = original


# ------------------------------------------------------------------
# init_sentry
# ------------------------------------------------------------------


class TestInitSentry:
    """Tests for init_sentry()."""

    def test_init_sentry_with_dsn(self):
        """When sentry_dsn is set, sentry_sdk.init() should be called."""
        mock_sentry_sdk = MagicMock()
        mock_fastapi_integration = MagicMock()
        mock_settings = MagicMock()
        mock_settings.sentry_dsn = "https://examplePublicKey@o0.ingest.sentry.io/0"
        mock_settings.is_production = True
        mock_settings.app_env = "production"

        import src.middleware.sentry_setup as mod

        with (
            patch("src.middleware.sentry_setup.settings", mock_settings),
            patch.dict(
                "sys.modules",
                {
                    "sentry_sdk": mock_sentry_sdk,
                    "sentry_sdk.integrations.fastapi": mock_fastapi_integration,
                },
            ),
        ):
            mod._sentry_initialized = False
            mod.init_sentry()

        assert mod._sentry_initialized is True
        mock_sentry_sdk.init.assert_called_once()

    def test_init_sentry_without_dsn(self):
        """When sentry_dsn is empty, sentry_sdk.init() should NOT be called."""
        mock_settings = MagicMock()
        mock_settings.sentry_dsn = ""

        import src.middleware.sentry_setup as mod

        with (
            patch("src.middleware.sentry_setup.settings", mock_settings),
            patch.dict("sys.modules", {"sentry_sdk": MagicMock()}) as _,
        ):
            mod._sentry_initialized = False
            mod.init_sentry()

        assert mod._sentry_initialized is False

    def test_init_sentry_traces_rate_dev(self):
        """In development, traces_sample_rate should be 1.0."""
        mock_sentry_sdk = MagicMock()
        mock_fastapi_integration = MagicMock()
        mock_settings = MagicMock()
        mock_settings.sentry_dsn = "https://key@sentry.io/1"
        mock_settings.is_production = False
        mock_settings.app_env = "development"

        import src.middleware.sentry_setup as mod

        with (
            patch("src.middleware.sentry_setup.settings", mock_settings),
            patch.dict(
                "sys.modules",
                {
                    "sentry_sdk": mock_sentry_sdk,
                    "sentry_sdk.integrations.fastapi": mock_fastapi_integration,
                },
            ),
        ):
            mod._sentry_initialized = False
            mod.init_sentry()
            call_kwargs = mock_sentry_sdk.init.call_args
            assert call_kwargs is not None
            assert call_kwargs.kwargs.get("traces_sample_rate") == 1.0

    def test_init_sentry_traces_rate_prod(self):
        """In production, traces_sample_rate should be 0.1."""
        mock_sentry_sdk = MagicMock()
        mock_fastapi_integration = MagicMock()
        mock_settings = MagicMock()
        mock_settings.sentry_dsn = "https://key@sentry.io/1"
        mock_settings.is_production = True
        mock_settings.app_env = "production"

        import src.middleware.sentry_setup as mod

        with (
            patch("src.middleware.sentry_setup.settings", mock_settings),
            patch.dict(
                "sys.modules",
                {
                    "sentry_sdk": mock_sentry_sdk,
                    "sentry_sdk.integrations.fastapi": mock_fastapi_integration,
                },
            ),
        ):
            mod._sentry_initialized = False
            mod.init_sentry()
            call_kwargs = mock_sentry_sdk.init.call_args
            assert call_kwargs is not None
            assert call_kwargs.kwargs.get("traces_sample_rate") == 0.1

    def test_init_sentry_handles_exception(self):
        """init_sentry should not raise even if sentry_sdk.init() throws."""
        mock_settings = MagicMock()
        mock_settings.sentry_dsn = "https://key@sentry.io/1"
        mock_settings.is_production = False
        mock_settings.app_env = "development"

        mock_sentry_sdk = MagicMock()
        mock_sentry_sdk.init.side_effect = RuntimeError("Sentry boom")
        mock_fastapi_integration = MagicMock()

        import src.middleware.sentry_setup as mod

        with (
            patch("src.middleware.sentry_setup.settings", mock_settings),
            patch.dict(
                "sys.modules",
                {
                    "sentry_sdk": mock_sentry_sdk,
                    "sentry_sdk.integrations.fastapi": mock_fastapi_integration,
                },
            ),
        ):
            mod._sentry_initialized = False
            # Should not raise
            mod.init_sentry()

        assert mod._sentry_initialized is False


# ------------------------------------------------------------------
# capture_exception
# ------------------------------------------------------------------


class TestCaptureException:
    """Tests for the capture_exception wrapper."""

    def test_capture_when_not_initialized(self):
        """Should silently do nothing when Sentry is not initialized."""
        import src.middleware.sentry_setup as mod

        mock_sentry_sdk = MagicMock()
        with patch.dict("sys.modules", {"sentry_sdk": mock_sentry_sdk}):
            mod._sentry_initialized = False
            mod.capture_exception(ValueError("test"))
            mock_sentry_sdk.capture_exception.assert_not_called()

    def test_capture_when_initialized(self):
        """Should forward to sentry_sdk.capture_exception when initialized."""
        import src.middleware.sentry_setup as mod

        mock_sentry_sdk = MagicMock()
        with patch.dict("sys.modules", {"sentry_sdk": mock_sentry_sdk}):
            mod._sentry_initialized = True
            exc = ValueError("test error")
            mod.capture_exception(exc)
            mock_sentry_sdk.capture_exception.assert_called_once_with(exc)

    def test_capture_handles_exception_gracefully(self):
        """Should not raise if sentry_sdk.capture_exception itself throws."""
        import src.middleware.sentry_setup as mod

        mock_sentry_sdk = MagicMock()
        mock_sentry_sdk.capture_exception.side_effect = RuntimeError("sentry down")
        with patch.dict("sys.modules", {"sentry_sdk": mock_sentry_sdk}):
            mod._sentry_initialized = True
            # Should not raise
            mod.capture_exception(ValueError("original"))


# ------------------------------------------------------------------
# set_user_context
# ------------------------------------------------------------------


class TestSetUserContext:
    """Tests for set_user_context."""

    def test_set_user_when_not_initialized(self):
        """Should silently do nothing when Sentry is not initialized."""
        import src.middleware.sentry_setup as mod

        mock_sentry_sdk = MagicMock()
        with patch.dict("sys.modules", {"sentry_sdk": mock_sentry_sdk}):
            mod._sentry_initialized = False
            mod.set_user_context("user-123")
            mock_sentry_sdk.set_user.assert_not_called()

    def test_set_user_when_initialized(self):
        """Should call sentry_sdk.set_user with the correct payload."""
        import src.middleware.sentry_setup as mod

        mock_sentry_sdk = MagicMock()
        with patch.dict("sys.modules", {"sentry_sdk": mock_sentry_sdk}):
            mod._sentry_initialized = True
            mod.set_user_context("user-456")
            mock_sentry_sdk.set_user.assert_called_once_with({"id": "user-456"})

    def test_set_user_handles_exception_gracefully(self):
        """Should not raise if sentry_sdk.set_user throws."""
        import src.middleware.sentry_setup as mod

        mock_sentry_sdk = MagicMock()
        mock_sentry_sdk.set_user.side_effect = RuntimeError("boom")
        with patch.dict("sys.modules", {"sentry_sdk": mock_sentry_sdk}):
            mod._sentry_initialized = True
            # Should not raise
            mod.set_user_context("user-789")
