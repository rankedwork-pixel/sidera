"""__Channel__ API connector for Sidera.

Read-only connector that pulls campaign data, metrics, and account info
from __Channel__ ad accounts.

Architecture:
    connector (this file) -> MCP tools -> agent loop

Usage:
    from src.connectors.__CHANNEL__ import __Channel__Connector

    connector = __Channel__Connector()
    accounts = connector.get_ad_accounts()
"""

from __future__ import annotations

from datetime import date
from typing import Any

import structlog

from src.cache.decorators import cached
from src.cache.service import (
    CACHE_TTL_ACCOUNT_INFO,
    CACHE_TTL_CAMPAIGNS,
    CACHE_TTL_METRICS,
)
from src.config import settings  # noqa: F401 — used in _credentials_from_settings

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class __Channel__ConnectorError(Exception):
    """Base exception for __Channel__ connector errors."""

    pass


class __Channel__AuthError(__Channel__ConnectorError):
    """Authentication or authorization failure -- must be surfaced to user."""

    pass


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class __Channel__Connector:
    """Read-only client for the __Channel__ API.

    Wraps the __Channel__ SDK and exposes clean, dict-based methods for
    pulling account info, campaigns, and performance metrics.

    Args:
        credentials: Optional dict of credentials. If omitted, values are
            read from the ``settings`` singleton (env vars / .env file).
    """

    # TODO: Set the API version for this platform
    API_VERSION = "v1"

    def __init__(self, credentials: dict[str, str] | None = None) -> None:
        self._credentials = credentials or self._credentials_from_settings()
        self._client = self._build_client(self._credentials)
        self._log = logger.bind(connector="__CHANNEL__")

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def get_ad_accounts(self) -> list[dict[str, Any]]:
        """Return ad accounts accessible by the current credentials.

        Returns:
            List of dicts with ``id``, ``name``, ``status``, ``currency``,
            ``timezone``.
        """
        self._log.info("listing_ad_accounts")
        try:
            # TODO: Call the platform API to list accounts
            # raw_accounts = self._client.list_accounts()
            raise NotImplementedError("Implement __Channel__ account listing")
        except __Channel__AuthError:
            raise
        except Exception as exc:
            self._log.error("get_ad_accounts_error", error=str(exc))
            return []

    @cached(ttl_seconds=CACHE_TTL_ACCOUNT_INFO, key_prefix="__CHANNEL__:get_account_info")
    def get_account_info(self, account_id: str) -> dict[str, Any] | None:
        """Fetch metadata for a single ad account.

        Args:
            account_id: Platform account ID.

        Returns:
            Dict with account metadata, or ``None`` on transient failure.
        """
        self._log.info("getting_account_info", account_id=account_id)
        try:
            # TODO: Call the platform API to get account details
            raise NotImplementedError("Implement __Channel__ account info")
        except __Channel__AuthError:
            raise
        except Exception as exc:
            self._log.error("get_account_info_error", account_id=account_id, error=str(exc))
            return None

    @cached(ttl_seconds=CACHE_TTL_CAMPAIGNS, key_prefix="__CHANNEL__:get_campaigns")
    def get_campaigns(self, account_id: str) -> list[dict[str, Any]]:
        """Fetch all campaigns for an account.

        Args:
            account_id: Platform account ID.

        Returns:
            List of campaign dicts with ``id``, ``name``, ``objective``,
            ``status``, ``daily_budget``, ``lifetime_budget``.
        """
        self._log.info("getting_campaigns", account_id=account_id)
        try:
            # TODO: Call the platform API to list campaigns
            # Remember to normalize monetary values:
            #   - Google Ads: micros (÷ 1,000,000)
            #   - Meta: string cents (÷ 100)
            #   - Your platform: ???
            raise NotImplementedError("Implement __Channel__ campaign listing")
        except __Channel__AuthError:
            raise
        except Exception as exc:
            self._log.error("get_campaigns_error", account_id=account_id, error=str(exc))
            return []

    @cached(ttl_seconds=CACHE_TTL_METRICS, key_prefix="__CHANNEL__:get_campaign_metrics")
    def get_campaign_metrics(
        self,
        account_id: str,
        campaign_id: str,
        start_date: date | str,
        end_date: date | str,
    ) -> list[dict[str, Any]]:
        """Fetch daily metrics for a single campaign over a date range.

        Args:
            account_id: Platform account ID.
            campaign_id: The campaign ID to filter on.
            start_date: Start of date range (inclusive).
            end_date: End of date range (inclusive).

        Returns:
            List of daily metric dicts.
        """
        start_str = start_date.isoformat() if isinstance(start_date, date) else str(start_date)
        end_str = end_date.isoformat() if isinstance(end_date, date) else str(end_date)

        self._log.info(
            "getting_campaign_metrics",
            account_id=account_id,
            campaign_id=campaign_id,
            start_date=start_str,
            end_date=end_str,
        )
        try:
            # TODO: Call the platform API to get campaign metrics
            # Return dicts with: campaign_id, campaign_name, date,
            # impressions, clicks, spend, conversions, conversion_value
            raise NotImplementedError("Implement __Channel__ campaign metrics")
        except __Channel__AuthError:
            raise
        except Exception as exc:
            self._log.error(
                "get_campaign_metrics_error",
                account_id=account_id,
                campaign_id=campaign_id,
                error=str(exc),
            )
            return []

    @cached(ttl_seconds=CACHE_TTL_METRICS, key_prefix="__CHANNEL__:get_account_metrics")
    def get_account_metrics(
        self,
        account_id: str,
        start_date: date | str,
        end_date: date | str,
    ) -> list[dict[str, Any]]:
        """Fetch daily metrics for ALL campaigns in an account.

        Args:
            account_id: Platform account ID.
            start_date: Start of date range (inclusive).
            end_date: End of date range (inclusive).

        Returns:
            List of daily metric dicts (one per campaign per day).
        """
        start_str = start_date.isoformat() if isinstance(start_date, date) else str(start_date)
        end_str = end_date.isoformat() if isinstance(end_date, date) else str(end_date)

        self._log.info(
            "getting_account_metrics",
            account_id=account_id,
            start_date=start_str,
            end_date=end_str,
        )
        try:
            # TODO: Call the platform API to get account-level metrics
            raise NotImplementedError("Implement __Channel__ account metrics")
        except __Channel__AuthError:
            raise
        except Exception as exc:
            self._log.error(
                "get_account_metrics_error",
                account_id=account_id,
                error=str(exc),
            )
            return []

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _credentials_from_settings() -> dict[str, str]:
        """Build a credentials dict from the global settings singleton."""
        return {
            # TODO: Map settings fields to credential keys
            # "access_token": settings.__CHANNEL___access_token,
            # "client_id": settings.__CHANNEL___client_id,
            # "client_secret": settings.__CHANNEL___client_secret,
        }

    @staticmethod
    def _build_client(credentials: dict[str, str]) -> Any:
        """Initialize the platform SDK client.

        Args:
            credentials: Dict with platform-specific credential keys.

        Returns:
            Initialized SDK client instance.

        Raises:
            __Channel__AuthError: If credentials are missing or invalid.
        """
        # TODO: Validate credentials and create the SDK client
        # Example:
        #   access_token = credentials.get("access_token", "")
        #   if not access_token:
        #       raise __Channel__AuthError(
        #           "__Channel__ access token is required. Connect via OAuth or "
        #           "set __CHANNEL_UPPER___ACCESS_TOKEN in environment."
        #       )
        #   return SomeSDKClient(access_token=access_token)
        raise NotImplementedError("Implement __Channel__ client initialization")

    def _handle_api_error(self, exc: Exception, operation: str, **context: Any) -> None:
        """Inspect a platform API error and raise or log appropriately.

        Auth failures should be raised as ``__Channel__AuthError`` so callers
        can surface them to users. Other errors are logged and swallowed
        (methods return empty results).

        Args:
            exc: The caught exception.
            operation: A label for the operation that failed.
            **context: Extra fields for the structured log.

        Raises:
            __Channel__AuthError: On authentication / authorization failures.
        """
        self._log.error(
            "__CHANNEL___api_error",
            operation=operation,
            error=str(exc),
            **context,
        )

        # TODO: Detect auth errors by checking error codes / types
        # Example:
        #   if hasattr(exc, "error_code") and exc.error_code in {401, 403}:
        #       raise __Channel__AuthError(
        #           f"__Channel__ auth error during {operation}: {exc}"
        #       ) from exc
