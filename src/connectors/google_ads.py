"""Google Ads API connector for Sidera.

Connector that pulls campaign data, metrics, change history, and
recommendations from Google Ads accounts, and applies approved write
operations (budget changes, status updates, bid adjustments, etc.).

All monetary values from the API arrive in micros (1/1,000,000 of
currency unit) and are converted to decimal at the connector boundary.
Write operations are always gated by human approval in the agent loop.

Architecture:
    connector (this file) -> MCP tools -> agent loop
    Each read method executes a GAQL query, converts protobuf rows to
    plain dicts, and returns clean Python data structures.
    Each write method fetches current state (for rollback), validates
    safety caps, then executes a mutate operation.

Usage:
    from src.connectors.google_ads import GoogleAdsConnector

    connector = GoogleAdsConnector()  # uses settings singleton
    accounts = connector.get_accessible_accounts()
    campaigns = connector.get_campaigns(customer_id="1234567890")
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google.api_core import protobuf_helpers
from google.protobuf.json_format import MessageToDict

from src.cache.decorators import cached
from src.cache.service import (
    CACHE_TTL_ACCOUNT_INFO,
    CACHE_TTL_CAMPAIGNS,
    CACHE_TTL_METRICS,
    CACHE_TTL_RECOMMENDATIONS,
)
from src.config import settings
from src.connectors.retry import retry_with_backoff
from src.middleware.sentry_setup import capture_exception
from src.models.normalized import (
    GOOGLE_ADS_CAMPAIGN_TYPE_MAP,
    GOOGLE_ADS_METRIC_MAP,
)
from src.utils.encryption import decrypt_token

logger = structlog.get_logger(__name__)

# Micros divisor for monetary conversions
_MICROS = Decimal("1000000")


class GoogleAdsConnectorError(Exception):
    """Base exception for Google Ads connector errors."""

    pass


class GoogleAdsAuthError(GoogleAdsConnectorError):
    """Authentication or authorization failure -- must be surfaced to user."""

    pass


class GoogleAdsWriteError(GoogleAdsConnectorError):
    """A write operation (mutate) failed after approval."""

    pass


class GoogleAdsConnector:
    """Client for the Google Ads API (read + approved writes).

    Wraps the google-ads Python library and exposes clean, dict-based
    methods for pulling account info, campaigns, performance metrics,
    change history, and recommendations.  Also provides write methods
    for budget changes, status updates, bid adjustments, negative
    keywords, ad schedules, and geo bid modifiers -- all gated by
    human approval in the agent loop.

    Args:
        credentials: Optional dict of credentials. If omitted, values are
            read from the ``settings`` singleton (env vars / .env file).
            Expected keys: ``developer_token``, ``client_id``,
            ``client_secret``, ``refresh_token``, ``login_customer_id``.
    """

    # API version to use across all service calls
    API_VERSION = "v23"

    def __init__(self, credentials: dict[str, str] | None = None) -> None:
        self._credentials = credentials or self._credentials_from_settings()
        self._client = self._build_client(self._credentials)
        self._log = logger.bind(connector="google_ads")

    @staticmethod
    def _unwrap_pb(obj: Any) -> Any:
        """Unwrap a proto-plus wrapper to its raw protobuf message.

        Newer versions of the google-ads library return raw protobuf
        messages from ``get_type()`` operations (no ``._pb`` wrapper).
        Older versions use proto-plus wrappers that require ``._pb``.
        This helper handles both cases.
        """
        return obj._pb if hasattr(obj, "_pb") else obj

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def get_accessible_accounts(self) -> list[str]:
        """Return customer IDs the refresh token has access to.

        Uses ``CustomerService.list_accessible_customers`` which does not
        require a ``customer_id`` argument (it operates on the OAuth token).

        Returns:
            List of customer ID strings (digits only, no dashes).
        """
        self._log.info("listing_accessible_accounts")
        try:
            customer_service = self._client.get_service("CustomerService", version=self.API_VERSION)
            response = customer_service.list_accessible_customers()
            # resource_names look like "customers/1234567890"
            customer_ids = [rn.split("/")[-1] for rn in response.resource_names]
            self._log.info("accessible_accounts_found", count=len(customer_ids))
            return customer_ids
        except GoogleAdsException as exc:
            self._handle_google_ads_exception(exc, "list_accessible_accounts")
            return []

    @cached(ttl_seconds=CACHE_TTL_ACCOUNT_INFO, key_prefix="google_ads:get_account_info")
    def get_account_info(self, customer_id: str) -> dict[str, Any] | None:
        """Fetch basic account metadata.

        Args:
            customer_id: Google Ads customer ID (digits, no dashes).

        Returns:
            Dict with ``id``, ``name``, ``currency``, ``timezone``,
            ``descriptive_name``, or ``None`` on transient failure.
        """
        query = """
            SELECT
                customer.id,
                customer.descriptive_name,
                customer.currency_code,
                customer.time_zone
            FROM customer
            LIMIT 1
        """
        self._log.info("getting_account_info", customer_id=customer_id)
        rows = self._execute_query(customer_id, query)
        if not rows:
            return None

        row = rows[0]
        return {
            "id": str(row.get("customer_id", "")),
            "name": row.get("customer_descriptive_name", ""),
            "descriptive_name": row.get("customer_descriptive_name", ""),
            "currency": row.get("customer_currency_code", ""),
            "timezone": row.get("customer_time_zone", ""),
        }

    def get_child_accounts(self, manager_id: str) -> list[dict[str, Any]]:
        """List client accounts managed by an MCC (manager account).

        Args:
            manager_id: The manager (MCC) customer ID (digits, no dashes).

        Returns:
            List of dicts with ``id``, ``descriptive_name``, ``manager``
            for each child account.  Empty list if the account is not a
            manager or on error.
        """
        query = """
            SELECT
                customer_client.id,
                customer_client.descriptive_name,
                customer_client.manager,
                customer_client.status
            FROM customer_client
            WHERE customer_client.status = 'ENABLED'
        """
        self._log.info("listing_child_accounts", manager_id=manager_id)
        try:
            rows = self._execute_query(manager_id, query)
            if not rows:
                return []
            children = []
            for row in rows:
                children.append(
                    {
                        "id": str(row.get("customer_client_id", "")),
                        "descriptive_name": row.get("customer_client_descriptive_name", ""),
                        "manager": row.get("customer_client_manager", False),
                    }
                )
            self._log.info("child_accounts_found", count=len(children))
            return children
        except Exception as exc:
            self._log.warning("child_accounts_error", error=str(exc))
            return []

    @cached(ttl_seconds=CACHE_TTL_CAMPAIGNS, key_prefix="google_ads:get_campaigns")
    def get_campaigns(self, customer_id: str) -> list[dict[str, Any]]:
        """Fetch all campaigns (non-removed) for an account.

        Args:
            customer_id: Google Ads customer ID.

        Returns:
            List of campaign dicts with keys: ``id``, ``name``, ``type``,
            ``status``, ``daily_budget``, ``bid_strategy``, ``platform_data``.
        """
        query = """
            SELECT
                campaign.id,
                campaign.name,
                campaign.status,
                campaign.advertising_channel_type,
                campaign.bidding_strategy_type,
                campaign_budget.amount_micros,
                campaign_budget.delivery_method
            FROM campaign
            WHERE campaign.status != 'REMOVED'
            ORDER BY campaign.name
        """
        self._log.info("getting_campaigns", customer_id=customer_id)
        rows = self._execute_query(customer_id, query)
        if rows is None:
            return []

        campaigns: list[dict[str, Any]] = []
        for row in rows:
            channel_type = row.get("campaign_advertising_channel_type", "")
            campaign_type = GOOGLE_ADS_CAMPAIGN_TYPE_MAP.get(
                channel_type, channel_type.lower() if channel_type else "unknown"
            )
            budget_micros = row.get("campaign_budget_amount_micros", 0) or 0
            daily_budget = Decimal(str(budget_micros)) / _MICROS

            campaigns.append(
                {
                    "id": str(row.get("campaign_id", "")),
                    "name": row.get("campaign_name", ""),
                    "type": campaign_type,
                    "status": (row.get("campaign_status", "")).lower(),
                    "daily_budget": float(daily_budget),
                    "bid_strategy": (row.get("campaign_bidding_strategy_type", "")).lower(),
                    "platform_data": {
                        "channel_type": channel_type,
                        "delivery_method": row.get("campaign_budget_delivery_method", ""),
                    },
                }
            )

        self._log.info(
            "campaigns_fetched",
            customer_id=customer_id,
            count=len(campaigns),
        )
        return campaigns

    @cached(ttl_seconds=CACHE_TTL_METRICS, key_prefix="google_ads:get_campaign_metrics")
    def get_campaign_metrics(
        self,
        customer_id: str,
        campaign_id: str,
        start_date: date | str,
        end_date: date | str,
    ) -> list[dict[str, Any]]:
        """Fetch daily metrics for a single campaign over a date range.

        Args:
            customer_id: Google Ads customer ID.
            campaign_id: The campaign ID to filter on.
            start_date: Start of date range (inclusive).
            end_date: End of date range (inclusive).

        Returns:
            List of daily metric dicts. Each dict has a ``date`` key plus
            all metrics from ``GOOGLE_ADS_METRIC_MAP`` (monetary values
            already converted from micros to decimals).
        """
        metric_fields = ", ".join(GOOGLE_ADS_METRIC_MAP.keys())
        start_str = start_date.isoformat() if isinstance(start_date, date) else str(start_date)
        end_str = end_date.isoformat() if isinstance(end_date, date) else str(end_date)
        query = f"""
            SELECT
                campaign.id,
                campaign.name,
                segments.date,
                {metric_fields}
            FROM campaign
            WHERE campaign.id = {campaign_id}
              AND segments.date BETWEEN '{start_str}' AND '{end_str}'
            ORDER BY segments.date
        """
        self._log.info(
            "getting_campaign_metrics",
            customer_id=customer_id,
            campaign_id=campaign_id,
            start_date=start_str,
            end_date=end_str,
        )
        rows = self._execute_query(customer_id, query)
        if rows is None:
            return []

        return [self._format_metric_row(row) for row in rows]

    @cached(ttl_seconds=CACHE_TTL_METRICS, key_prefix="google_ads:get_account_metrics")
    def get_account_metrics(
        self,
        customer_id: str,
        start_date: date | str,
        end_date: date | str,
    ) -> list[dict[str, Any]]:
        """Fetch daily metrics for ALL campaigns in an account.

        Args:
            customer_id: Google Ads customer ID.
            start_date: Start of date range (inclusive).
            end_date: End of date range (inclusive).

        Returns:
            List of daily metric dicts (one per campaign per day), each
            including ``campaign_id``, ``campaign_name``, ``date``, and
            all metrics from ``GOOGLE_ADS_METRIC_MAP``.
        """
        metric_fields = ", ".join(GOOGLE_ADS_METRIC_MAP.keys())
        start_str = start_date.isoformat() if isinstance(start_date, date) else str(start_date)
        end_str = end_date.isoformat() if isinstance(end_date, date) else str(end_date)
        query = f"""
            SELECT
                campaign.id,
                campaign.name,
                segments.date,
                {metric_fields}
            FROM campaign
            WHERE campaign.status != 'REMOVED'
              AND segments.date BETWEEN '{start_str}' AND '{end_str}'
            ORDER BY campaign.id, segments.date
        """
        self._log.info(
            "getting_account_metrics",
            customer_id=customer_id,
            start_date=start_str,
            end_date=end_str,
        )
        rows = self._execute_query(customer_id, query)
        if rows is None:
            return []

        return [self._format_metric_row(row) for row in rows]

    def get_change_history(
        self,
        customer_id: str,
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """Fetch recent change events for campaigns in the account.

        Queries the ``change_event`` resource to surface budget changes,
        status changes, bid strategy changes, etc. that happened in the
        past ``days`` days.

        Args:
            customer_id: Google Ads customer ID.
            days: How many days of history to pull (default 7).

        Returns:
            List of change-event dicts with keys: ``change_date_time``,
            ``change_resource_type``, ``resource_change_operation``,
            ``changed_fields``, ``campaign_id``, ``campaign_name``,
            ``old_resource``, ``new_resource``.
        """
        since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
        query = f"""
            SELECT
                change_event.change_date_time,
                change_event.change_resource_type,
                change_event.resource_change_operation,
                change_event.changed_fields,
                change_event.old_resource,
                change_event.new_resource,
                campaign.id,
                campaign.name
            FROM change_event
            WHERE change_event.change_date_time >= '{since}'
              AND change_event.change_resource_type IN (
                  'CAMPAIGN', 'CAMPAIGN_BUDGET', 'AD_GROUP', 'AD_GROUP_BID_MODIFIER'
              )
            ORDER BY change_event.change_date_time DESC
            LIMIT 100
        """
        self._log.info(
            "getting_change_history",
            customer_id=customer_id,
            days=days,
        )
        rows = self._execute_query(customer_id, query)
        if rows is None:
            return []

        changes: list[dict[str, Any]] = []
        for row in rows:
            changes.append(
                {
                    "change_date_time": row.get("change_event_change_date_time", ""),
                    "change_resource_type": row.get("change_event_change_resource_type", ""),
                    "resource_change_operation": row.get(
                        "change_event_resource_change_operation", ""
                    ),
                    "changed_fields": row.get("change_event_changed_fields", ""),
                    "old_resource": row.get("change_event_old_resource", {}),
                    "new_resource": row.get("change_event_new_resource", {}),
                    "campaign_id": str(row.get("campaign_id", "")),
                    "campaign_name": row.get("campaign_name", ""),
                }
            )

        self._log.info(
            "change_history_fetched",
            customer_id=customer_id,
            count=len(changes),
        )
        return changes

    @cached(ttl_seconds=CACHE_TTL_RECOMMENDATIONS, key_prefix="google_ads:get_recommendations")
    def get_recommendations(self, customer_id: str) -> list[dict[str, Any]]:
        """Fetch Google's own recommendations for the account.

        These are signals the agent can consider -- but should never
        blindly follow, since Google optimises for platform revenue,
        not the advertiser's P&L.

        Args:
            customer_id: Google Ads customer ID.

        Returns:
            List of recommendation dicts with keys: ``id``, ``type``,
            ``impact``, ``campaign_id``, ``campaign_name``, ``dismissed``.
        """
        query = """
            SELECT
                recommendation.resource_name,
                recommendation.type,
                recommendation.impact,
                recommendation.campaign,
                recommendation.dismissed
            FROM recommendation
            WHERE recommendation.dismissed = FALSE
            LIMIT 50
        """
        self._log.info("getting_recommendations", customer_id=customer_id)
        rows = self._execute_query(customer_id, query)
        if rows is None:
            return []

        recommendations: list[dict[str, Any]] = []
        for row in rows:
            # Extract campaign ID from the campaign resource name if present
            campaign_resource = row.get("recommendation_campaign", "")
            campaign_id = ""
            if campaign_resource and "/" in str(campaign_resource):
                parts = str(campaign_resource).split("/")
                # Format: customers/{customer_id}/campaigns/{campaign_id}
                if len(parts) >= 4:
                    campaign_id = parts[3]

            impact = row.get("recommendation_impact", {})
            if isinstance(impact, str):
                impact = {"raw": impact}

            recommendations.append(
                {
                    "id": row.get("recommendation_resource_name", ""),
                    "type": row.get("recommendation_type", ""),
                    "impact": impact,
                    "campaign_id": campaign_id,
                    "dismissed": row.get("recommendation_dismissed", False),
                }
            )

        self._log.info(
            "recommendations_fetched",
            customer_id=customer_id,
            count=len(recommendations),
        )
        return recommendations

    # ------------------------------------------------------------------
    # Write methods (all gated by human approval in agent loop)
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def update_campaign_budget(
        self,
        customer_id: str,
        campaign_id: str,
        new_budget_micros: int,
        *,
        validate_cap: bool = True,
    ) -> dict[str, Any]:
        """Update a campaign's daily budget.

        Fetches the current budget first to store previous value for
        rollback tracking and to enforce the budget change cap.

        Args:
            customer_id: Google Ads customer ID (no dashes).
            campaign_id: Campaign whose budget to update.
            new_budget_micros: New daily budget in micros.
            validate_cap: If True, reject changes exceeding
                ``settings.max_budget_change_ratio`` (default 50% increase).

        Returns:
            Dict with ``resource_name``, ``previous_budget_micros``,
            ``new_budget_micros``.

        Raises:
            GoogleAdsWriteError: If the mutation fails.
            ValueError: If budget change exceeds the configured cap.
        """
        self._log.info(
            "update_campaign_budget.start",
            customer_id=customer_id,
            campaign_id=campaign_id,
            new_budget_micros=new_budget_micros,
        )

        # Fetch current budget for rollback value and cap validation
        query = (
            f"SELECT campaign.id, campaign_budget.resource_name, "
            f"campaign_budget.amount_micros "
            f"FROM campaign WHERE campaign.id = '{campaign_id}'"
        )
        rows = self._execute_query(customer_id, query)
        if not rows:
            raise GoogleAdsWriteError(f"Campaign {campaign_id} not found in account {customer_id}")

        row = rows[0]
        budget_resource_name = row.get("campaignBudget_resource_name") or row.get(
            "campaign_budget_resource_name", ""
        )
        previous_budget_micros = int(
            row.get("campaignBudget_amount_micros") or row.get("campaign_budget_amount_micros", 0)
        )

        # Enforce safety cap
        if validate_cap and previous_budget_micros > 0:
            ratio = new_budget_micros / previous_budget_micros
            if ratio > settings.max_budget_change_ratio:
                raise ValueError(
                    f"Budget change ratio {ratio:.2f}x exceeds cap "
                    f"{settings.max_budget_change_ratio}x. Current: "
                    f"{previous_budget_micros}, requested: {new_budget_micros}"
                )

        # Build mutate operation
        budget_service = self._client.get_service("CampaignBudgetService", version=self.API_VERSION)
        operation = self._client.get_type("CampaignBudgetOperation")
        budget = operation.update
        budget.resource_name = budget_resource_name
        budget.amount_micros = new_budget_micros
        self._client.copy_from(
            operation.update_mask,
            protobuf_helpers.field_mask(None, self._unwrap_pb(budget)),
        )

        response = self._execute_mutate(
            customer_id, budget_service.mutate_campaign_budgets, [operation]
        )

        result = {
            "resource_name": response.results[0].resource_name,
            "previous_budget_micros": previous_budget_micros,
            "new_budget_micros": new_budget_micros,
        }
        self._log.info(
            "update_campaign_budget.done",
            customer_id=customer_id,
            campaign_id=campaign_id,
            **result,
        )
        return result

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def update_campaign_status(
        self,
        customer_id: str,
        campaign_id: str,
        status: str,
    ) -> dict[str, Any]:
        """Enable or pause a campaign.

        Args:
            customer_id: Google Ads customer ID.
            campaign_id: Campaign to update.
            status: Target status -- ``"ENABLED"`` or ``"PAUSED"``.

        Returns:
            Dict with ``resource_name``, ``previous_status``, ``new_status``.

        Raises:
            GoogleAdsWriteError: If the mutation fails.
            ValueError: If *status* is not ENABLED or PAUSED.
        """
        status = status.upper()
        if status not in ("ENABLED", "PAUSED"):
            raise ValueError(f"status must be 'ENABLED' or 'PAUSED', got '{status}'")

        self._log.info(
            "update_campaign_status.start",
            customer_id=customer_id,
            campaign_id=campaign_id,
            target_status=status,
        )

        # Fetch current status for rollback tracking
        query = (
            f"SELECT campaign.id, campaign.status, campaign.resource_name "
            f"FROM campaign WHERE campaign.id = '{campaign_id}'"
        )
        rows = self._execute_query(customer_id, query)
        if not rows:
            raise GoogleAdsWriteError(f"Campaign {campaign_id} not found in account {customer_id}")

        row = rows[0]
        previous_status = (row.get("campaign_status", "")).upper()

        # Build mutate operation
        campaign_service = self._client.get_service("CampaignService", version=self.API_VERSION)
        operation = self._client.get_type("CampaignOperation")
        campaign = operation.update
        campaign.resource_name = f"customers/{customer_id}/campaigns/{campaign_id}"
        status_enum = self._client.enums.CampaignStatusEnum.CampaignStatus
        campaign.status = (
            status_enum.Value(status) if hasattr(status_enum, "Value") else status_enum[status]
        )
        self._client.copy_from(
            operation.update_mask,
            protobuf_helpers.field_mask(None, self._unwrap_pb(campaign)),
        )

        response = self._execute_mutate(customer_id, campaign_service.mutate_campaigns, [operation])

        result = {
            "resource_name": response.results[0].resource_name,
            "previous_status": previous_status,
            "new_status": status,
        }
        self._log.info(
            "update_campaign_status.done",
            customer_id=customer_id,
            campaign_id=campaign_id,
            **result,
        )
        return result

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def update_bid_strategy_target(
        self,
        customer_id: str,
        campaign_id: str,
        target_cpa_micros: int | None = None,
        target_roas: float | None = None,
    ) -> dict[str, Any]:
        """Update the tCPA or tROAS target for a campaign's bid strategy.

        At least one of *target_cpa_micros* or *target_roas* must be
        provided.

        Args:
            customer_id: Google Ads customer ID.
            campaign_id: Campaign to update.
            target_cpa_micros: New target CPA in micros.
            target_roas: New target ROAS (e.g. 4.0 for 400% ROAS).

        Returns:
            Dict with ``resource_name``, ``previous_targets``, ``new_targets``.

        Raises:
            GoogleAdsWriteError: If the mutation fails.
            ValueError: If neither target is provided.
        """
        if target_cpa_micros is None and target_roas is None:
            raise ValueError("At least one of target_cpa_micros or target_roas must be provided.")

        self._log.info(
            "update_bid_strategy_target.start",
            customer_id=customer_id,
            campaign_id=campaign_id,
            target_cpa_micros=target_cpa_micros,
            target_roas=target_roas,
        )

        # Fetch current bid strategy targets for rollback
        query = (
            f"SELECT campaign.id, campaign.resource_name, "
            f"campaign.target_cpa.target_cpa_micros, "
            f"campaign.target_roas.target_roas "
            f"FROM campaign WHERE campaign.id = '{campaign_id}'"
        )
        rows = self._execute_query(customer_id, query)
        if not rows:
            raise GoogleAdsWriteError(f"Campaign {campaign_id} not found in account {customer_id}")

        row = rows[0]
        previous_targets: dict[str, Any] = {
            "target_cpa_micros": row.get("campaign_target_cpa_target_cpa_micros"),
            "target_roas": row.get("campaign_target_roas_target_roas"),
        }

        # Build mutate operation
        campaign_service = self._client.get_service("CampaignService", version=self.API_VERSION)
        operation = self._client.get_type("CampaignOperation")
        campaign = operation.update
        campaign.resource_name = f"customers/{customer_id}/campaigns/{campaign_id}"

        new_targets: dict[str, Any] = {}
        if target_cpa_micros is not None:
            campaign.target_cpa.target_cpa_micros = target_cpa_micros
            new_targets["target_cpa_micros"] = target_cpa_micros
        if target_roas is not None:
            campaign.target_roas.target_roas = target_roas
            new_targets["target_roas"] = target_roas

        self._client.copy_from(
            operation.update_mask,
            protobuf_helpers.field_mask(None, self._unwrap_pb(campaign)),
        )

        response = self._execute_mutate(customer_id, campaign_service.mutate_campaigns, [operation])

        result = {
            "resource_name": response.results[0].resource_name,
            "previous_targets": previous_targets,
            "new_targets": new_targets,
        }
        self._log.info(
            "update_bid_strategy_target.done",
            customer_id=customer_id,
            campaign_id=campaign_id,
            **result,
        )
        return result

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def add_negative_keywords(
        self,
        customer_id: str,
        campaign_id: str,
        keywords: list[str],
    ) -> dict[str, Any]:
        """Add campaign-level negative keywords.

        Duplicate keywords that already exist are silently skipped.

        Args:
            customer_id: Google Ads customer ID.
            campaign_id: Campaign to add negative keywords to.
            keywords: List of keyword strings to add as negatives.

        Returns:
            Dict with ``campaign_id``, ``keywords_added``, ``resource_names``,
            ``duplicates_skipped``.

        Raises:
            GoogleAdsWriteError: If the mutation fails (except duplicates).
            ValueError: If *keywords* is empty.
        """
        if not keywords:
            raise ValueError("keywords list must not be empty")

        self._log.info(
            "add_negative_keywords.start",
            customer_id=customer_id,
            campaign_id=campaign_id,
            keyword_count=len(keywords),
        )

        # Build operations
        criterion_service = self._client.get_service(
            "CampaignCriterionService", version=self.API_VERSION
        )
        operations = []
        for kw in keywords:
            op = self._client.get_type("CampaignCriterionOperation")
            criterion = op.create
            criterion.campaign = f"customers/{customer_id}/campaigns/{campaign_id}"
            criterion.negative = True
            criterion.keyword.text = kw
            criterion.keyword.match_type = (
                self._client.enums.KeywordMatchTypeEnum.KeywordMatchType.BROAD
            )
            operations.append(op)

        # Execute with partial_failure to handle duplicates gracefully
        response = self._execute_mutate(
            customer_id,
            criterion_service.mutate_campaign_criteria,
            operations,
            partial_failure=True,
        )

        # Count successes vs duplicates
        resource_names: list[str] = []
        duplicates_skipped = 0

        if response.partial_failure_error:
            # Inspect partial failure details
            for i, detail in enumerate(response.partial_failure_error.details):
                # Operations that failed are duplicates or other errors
                duplicates_skipped += 1

        for result in response.results:
            if result.resource_name:
                resource_names.append(result.resource_name)

        keywords_added = len(resource_names)
        # If no explicit partial_failure_error, all succeeded
        if not response.partial_failure_error:
            duplicates_skipped = 0

        result_dict = {
            "campaign_id": campaign_id,
            "keywords_added": keywords_added,
            "resource_names": resource_names,
            "duplicates_skipped": duplicates_skipped,
        }
        self._log.info(
            "add_negative_keywords.done",
            customer_id=customer_id,
            **result_dict,
        )
        return result_dict

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def update_ad_schedule(
        self,
        customer_id: str,
        campaign_id: str,
        schedule: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Set ad schedule bid modifiers for a campaign.

        Each entry in *schedule* should have: ``day_of_week`` (MONDAY,
        TUESDAY, etc.), ``start_hour``, ``start_minute`` (ZERO or FIFTEEN
        or THIRTY or FORTY_FIVE), ``end_hour``, ``end_minute``,
        ``bid_modifier`` (float, e.g. 1.2 for +20%).

        Args:
            customer_id: Google Ads customer ID.
            campaign_id: Campaign to set ad schedules on.
            schedule: List of schedule entry dicts.

        Returns:
            Dict with ``campaign_id``, ``schedules_set``.

        Raises:
            GoogleAdsWriteError: If the mutation fails.
            ValueError: If *schedule* is empty.
        """
        if not schedule:
            raise ValueError("schedule list must not be empty")

        self._log.info(
            "update_ad_schedule.start",
            customer_id=customer_id,
            campaign_id=campaign_id,
            schedule_count=len(schedule),
        )

        # Build operations using CampaignCriterionService
        criterion_service = self._client.get_service(
            "CampaignCriterionService", version=self.API_VERSION
        )
        operations = []
        for entry in schedule:
            op = self._client.get_type("CampaignCriterionOperation")
            criterion = op.create
            criterion.campaign = f"customers/{customer_id}/campaigns/{campaign_id}"

            # Set ad schedule fields
            ad_schedule = criterion.ad_schedule
            day_enum = self._client.enums.DayOfWeekEnum.DayOfWeek
            day_val = entry["day_of_week"].upper()
            ad_schedule.day_of_week = (
                day_enum.Value(day_val) if hasattr(day_enum, "Value") else day_enum[day_val]
            )
            ad_schedule.start_hour = entry["start_hour"]
            minute_enum = self._client.enums.MinuteOfHourEnum.MinuteOfHour
            start_min = entry.get("start_minute", "ZERO").upper()
            ad_schedule.start_minute = (
                minute_enum.Value(start_min)
                if hasattr(minute_enum, "Value")
                else minute_enum[start_min]
            )
            ad_schedule.end_hour = entry["end_hour"]
            end_min = entry.get("end_minute", "ZERO").upper()
            ad_schedule.end_minute = (
                minute_enum.Value(end_min)
                if hasattr(minute_enum, "Value")
                else minute_enum[end_min]
            )

            # Bid modifier (1.0 = no change)
            bid_modifier = entry.get("bid_modifier")
            if bid_modifier is not None:
                criterion.bid_modifier = bid_modifier

            operations.append(op)

        response = self._execute_mutate(
            customer_id,
            criterion_service.mutate_campaign_criteria,
            operations,
        )

        result = {
            "campaign_id": campaign_id,
            "schedules_set": len(response.results),
        }
        self._log.info(
            "update_ad_schedule.done",
            customer_id=customer_id,
            **result,
        )
        return result

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def update_geo_bid_modifier(
        self,
        customer_id: str,
        campaign_id: str,
        geo_target_id: int,
        bid_modifier: float,
    ) -> dict[str, Any]:
        """Update a geographic bid modifier for a campaign.

        If the geo criterion already exists, updates the bid modifier.
        If not found, creates a new geo criterion with the given modifier.

        Args:
            customer_id: Google Ads customer ID.
            campaign_id: Campaign to update.
            geo_target_id: Google geo target constant ID (e.g. 1014044
                for New York).
            bid_modifier: Multiplier (1.0 = no change, 1.2 = +20%,
                0.8 = -20%).

        Returns:
            Dict with ``resource_name``, ``geo_target_id``,
            ``previous_modifier``, ``new_modifier``.

        Raises:
            GoogleAdsWriteError: If the mutation fails.
        """
        self._log.info(
            "update_geo_bid_modifier.start",
            customer_id=customer_id,
            campaign_id=campaign_id,
            geo_target_id=geo_target_id,
            bid_modifier=bid_modifier,
        )

        # Check if geo criterion already exists
        geo_constant_resource = f"geoTargetConstants/{geo_target_id}"
        query = (
            f"SELECT campaign_criterion.resource_name, "
            f"campaign_criterion.bid_modifier, "
            f"campaign_criterion.location.geo_target_constant "
            f"FROM campaign_criterion "
            f"WHERE campaign.id = '{campaign_id}' "
            f"AND campaign_criterion.type = 'LOCATION' "
            f"AND campaign_criterion.location.geo_target_constant = "
            f"'{geo_constant_resource}'"
        )
        rows = self._execute_query(customer_id, query)

        criterion_service = self._client.get_service(
            "CampaignCriterionService", version=self.API_VERSION
        )

        previous_modifier: float | None = None

        if rows:
            # Update existing criterion
            row = rows[0]
            previous_modifier = row.get("campaign_criterion_bid_modifier")
            criterion_resource_name = row.get("campaign_criterion_resource_name", "")

            operation = self._client.get_type("CampaignCriterionOperation")
            criterion = operation.update
            criterion.resource_name = criterion_resource_name
            criterion.bid_modifier = bid_modifier
            self._client.copy_from(
                operation.update_mask,
                protobuf_helpers.field_mask(None, self._unwrap_pb(criterion)),
            )
        else:
            # Create new geo criterion
            operation = self._client.get_type("CampaignCriterionOperation")
            criterion = operation.create
            criterion.campaign = f"customers/{customer_id}/campaigns/{campaign_id}"
            criterion.location.geo_target_constant = geo_constant_resource
            criterion.bid_modifier = bid_modifier

        response = self._execute_mutate(
            customer_id,
            criterion_service.mutate_campaign_criteria,
            [operation],
        )

        result = {
            "resource_name": response.results[0].resource_name,
            "geo_target_id": geo_target_id,
            "previous_modifier": previous_modifier,
            "new_modifier": bid_modifier,
        }
        self._log.info(
            "update_geo_bid_modifier.done",
            customer_id=customer_id,
            campaign_id=campaign_id,
            **result,
        )
        return result

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def create_campaign(
        self,
        customer_id: str,
        name: str,
        channel_type: str = "SEARCH",
        daily_budget_micros: int = 10_000_000,
        status: str = "PAUSED",
        bidding_strategy: str = "MAXIMIZE_CLICKS",
    ) -> dict[str, Any]:
        """Create a new campaign with a budget.

        Creates a CampaignBudget first, then a Campaign linked to it.
        Defaults to PAUSED status for safety.

        Args:
            customer_id: Google Ads customer ID (no dashes).
            name: Campaign name.
            channel_type: Advertising channel type — SEARCH, DISPLAY,
                SHOPPING, VIDEO, PERFORMANCE_MAX, DEMAND_GEN, APP.
            daily_budget_micros: Daily budget in micros (default $10).
            status: Initial status — ``"PAUSED"`` (default) or ``"ENABLED"``.
            bidding_strategy: Bidding strategy — ``"MAXIMIZE_CLICKS"``
                (default), ``"MAXIMIZE_CONVERSIONS"``,
                ``"MAXIMIZE_CONVERSION_VALUE"``, ``"MANUAL_CPC"``.

        Returns:
            Dict with ``campaign_id``, ``campaign_resource_name``,
            ``budget_resource_name``, ``name``, ``channel_type``,
            ``daily_budget_micros``, ``status``.

        Raises:
            GoogleAdsWriteError: If the mutation fails.
            ValueError: If channel_type or status is invalid.
        """
        # Validate inputs
        valid_channels = {
            "SEARCH",
            "DISPLAY",
            "SHOPPING",
            "VIDEO",
            "PERFORMANCE_MAX",
            "DEMAND_GEN",
            "APP",
        }
        channel_type = channel_type.upper()
        if channel_type not in valid_channels:
            raise ValueError(f"channel_type must be one of {valid_channels}, got '{channel_type}'")

        status = status.upper()
        if status not in ("ENABLED", "PAUSED"):
            raise ValueError(f"status must be 'ENABLED' or 'PAUSED', got '{status}'")

        valid_strategies = {
            "MAXIMIZE_CLICKS",
            "MAXIMIZE_CONVERSIONS",
            "MAXIMIZE_CONVERSION_VALUE",
            "MANUAL_CPC",
        }
        bidding_strategy = bidding_strategy.upper()
        if bidding_strategy not in valid_strategies:
            raise ValueError(
                f"bidding_strategy must be one of {valid_strategies}, got '{bidding_strategy}'"
            )

        self._log.info(
            "create_campaign.start",
            customer_id=customer_id,
            name=name,
            channel_type=channel_type,
            daily_budget_micros=daily_budget_micros,
            status=status,
            bidding_strategy=bidding_strategy,
        )

        # Step 1: Create CampaignBudget
        budget_service = self._client.get_service("CampaignBudgetService", version=self.API_VERSION)
        budget_operation = self._client.get_type("CampaignBudgetOperation")
        budget = budget_operation.create
        budget.name = f"{name} Budget"
        budget.amount_micros = daily_budget_micros
        # Use .Value() for raw protobuf enums (use_proto_plus=False)
        delivery_enum = self._client.enums.BudgetDeliveryMethodEnum.BudgetDeliveryMethod
        budget.delivery_method = (
            delivery_enum.Value("STANDARD")
            if hasattr(delivery_enum, "Value")
            else delivery_enum.STANDARD
        )

        budget_response = self._execute_mutate(
            customer_id, budget_service.mutate_campaign_budgets, [budget_operation]
        )
        budget_resource_name = budget_response.results[0].resource_name

        # Step 2: Create Campaign
        campaign_service = self._client.get_service("CampaignService", version=self.API_VERSION)
        campaign_operation = self._client.get_type("CampaignOperation")
        campaign = campaign_operation.create
        campaign.name = name
        campaign.campaign_budget = budget_resource_name

        # Use .Value() for raw protobuf enums (use_proto_plus=False)
        status_enum = self._client.enums.CampaignStatusEnum.CampaignStatus
        campaign.status = (
            status_enum.Value(status) if hasattr(status_enum, "Value") else status_enum[status]
        )
        channel_enum = self._client.enums.AdvertisingChannelTypeEnum.AdvertisingChannelType
        campaign.advertising_channel_type = (
            channel_enum.Value(channel_type)
            if hasattr(channel_enum, "Value")
            else channel_enum[channel_type]
        )

        # Required in v23+: EU political advertising disclosure
        # Enum: 3 = DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
        eu_enum = self._client.enums.EuPoliticalAdvertisingStatusEnum.EuPoliticalAdvertisingStatus
        campaign.contains_eu_political_advertising = (
            eu_enum.Value("DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING")
            if hasattr(eu_enum, "Value")
            else eu_enum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
        )

        # Network settings — default to Search + Search Partners for SEARCH
        network_settings = campaign.network_settings
        if channel_type == "SEARCH":
            network_settings.target_google_search = True
            network_settings.target_search_network = True
            network_settings.target_content_network = False
            network_settings.target_partner_search_network = False
        elif channel_type == "DISPLAY":
            network_settings.target_google_search = False
            network_settings.target_search_network = False
            network_settings.target_content_network = True
            network_settings.target_partner_search_network = False

        # Set bidding strategy via proto field assignment.
        # In v23 with use_proto_plus=False, get_type() returns raw
        # protobuf messages (no ._pb wrapper), and "maximize_clicks"
        # maps to target_spend on the Campaign proto.
        if bidding_strategy == "MAXIMIZE_CLICKS":
            campaign.target_spend.CopyFrom(self._client.get_type("TargetSpend"))
        elif bidding_strategy == "MAXIMIZE_CONVERSIONS":
            campaign.maximize_conversions.CopyFrom(self._client.get_type("MaximizeConversions"))
        elif bidding_strategy == "MAXIMIZE_CONVERSION_VALUE":
            campaign.maximize_conversion_value.CopyFrom(
                self._client.get_type("MaximizeConversionValue")
            )
        elif bidding_strategy == "MANUAL_CPC":
            campaign.manual_cpc.CopyFrom(self._client.get_type("ManualCpc"))

        campaign_response = self._execute_mutate(
            customer_id, campaign_service.mutate_campaigns, [campaign_operation]
        )
        campaign_resource_name = campaign_response.results[0].resource_name
        # Extract campaign ID from resource name: customers/{cid}/campaigns/{id}
        campaign_id = campaign_resource_name.split("/")[-1]

        result = {
            "campaign_id": campaign_id,
            "campaign_resource_name": campaign_resource_name,
            "budget_resource_name": budget_resource_name,
            "name": name,
            "channel_type": channel_type,
            "daily_budget_micros": daily_budget_micros,
            "status": status,
        }
        self._log.info(
            "create_campaign.done",
            customer_id=customer_id,
            **result,
        )
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _credentials_from_settings() -> dict[str, str]:
        """Build a credentials dict from the global settings singleton."""
        return {
            "developer_token": settings.google_ads_developer_token,
            "client_id": settings.google_ads_client_id,
            "client_secret": settings.google_ads_client_secret,
            "refresh_token": decrypt_token(settings.google_ads_refresh_token),
            "login_customer_id": settings.google_ads_login_customer_id,
        }

    @staticmethod
    def _build_client(credentials: dict[str, str]) -> GoogleAdsClient:
        """Create a ``GoogleAdsClient`` from a plain credentials dict.

        We use ``load_from_dict`` rather than YAML so we can support
        per-account credentials for multi-user SaaS operation.

        Args:
            credentials: Dict with ``developer_token``, ``client_id``,
                ``client_secret``, ``refresh_token``, and optionally
                ``login_customer_id``.

        Returns:
            Configured ``GoogleAdsClient`` instance.

        Raises:
            GoogleAdsAuthError: If credentials are missing or invalid.
        """
        config = {
            "developer_token": credentials.get("developer_token", ""),
            "client_id": credentials.get("client_id", ""),
            "client_secret": credentials.get("client_secret", ""),
            "refresh_token": credentials.get("refresh_token", ""),
            "use_proto_plus": False,
        }
        login_customer_id = credentials.get("login_customer_id", "")
        if login_customer_id:
            config["login_customer_id"] = login_customer_id

        try:
            return GoogleAdsClient.load_from_dict(config)
        except Exception as exc:
            raise GoogleAdsAuthError(f"Failed to create Google Ads client: {exc}") from exc

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def _execute_query(self, customer_id: str, query: str) -> list[dict[str, Any]] | None:
        """Execute a GAQL query via ``search_stream`` and return plain dicts.

        Iterates over batches from ``search_stream``, converts each
        protobuf row to a flat dictionary via ``_proto_to_dict``, and
        collects them into a list.

        Args:
            customer_id: Google Ads customer ID (no dashes).
            query: A valid GAQL query string.

        Returns:
            List of row dicts on success, or ``None`` on transient failure
            (auth errors are raised).
        """
        self._log.debug(
            "executing_gaql_query",
            customer_id=customer_id,
            query=query.strip()[:120],
        )
        try:
            ga_service = self._client.get_service("GoogleAdsService", version=self.API_VERSION)
            stream = ga_service.search_stream(customer_id=customer_id, query=query)

            rows: list[dict[str, Any]] = []
            for batch in stream:
                for row in batch.results:
                    rows.append(self._proto_to_dict(row))

            self._log.debug("query_returned_rows", count=len(rows))
            return rows

        except GoogleAdsException as exc:
            self._handle_google_ads_exception(exc, "execute_query", customer_id=customer_id)
            # _handle_google_ads_exception raises on auth errors, so if we
            # reach here the error was transient.
            return None

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def _execute_mutate(
        self,
        customer_id: str,
        mutate_fn: Any,
        operations: list,
        *,
        partial_failure: bool = False,
    ) -> Any:
        """Execute a mutate operation via the Google Ads API.

        Wraps the common try/except/log pattern for all write operations.
        Unlike ``_execute_query``, this raises ``GoogleAdsWriteError`` on
        failure rather than returning ``None``, because write failures must
        be surfaced to the caller.

        Args:
            customer_id: Google Ads customer ID (no dashes).
            mutate_fn: Bound method on a Google Ads service (e.g.
                ``campaign_service.mutate_campaigns``).
            operations: List of operation protos to send.
            partial_failure: If True, allow partial failures (e.g.
                duplicate negative keywords).

        Returns:
            The raw mutate response object.

        Raises:
            GoogleAdsWriteError: On any API error.
            GoogleAdsAuthError: On authentication/authorization errors.
        """
        try:
            kwargs: dict[str, Any] = {
                "customer_id": customer_id,
                "operations": operations,
            }
            if partial_failure:
                kwargs["partial_failure"] = True
            response = mutate_fn(**kwargs)
            self._log.info(
                "mutate.success",
                customer_id=customer_id,
                results_count=len(response.results),
            )
            return response
        except GoogleAdsException as exc:
            error_code = ""
            if exc.failure and exc.failure.errors:
                error_code = str(exc.failure.errors[0].error_code)
            # Check for auth errors
            if any(
                kw in error_code.lower()
                for kw in ("authentication", "authorization", "not_whitelisted")
            ):
                raise GoogleAdsAuthError(f"Auth error during mutate: {error_code}") from exc
            self._log.error(
                "mutate.failed",
                customer_id=customer_id,
                error_code=error_code,
                error=str(exc),
            )
            try:
                from src.middleware.sentry_setup import capture_exception as _capture

                _capture(exc)
            except ImportError:
                pass
            raise GoogleAdsWriteError(f"Mutate failed: {error_code} — {exc}") from exc
        except Exception as exc:
            self._log.error(
                "mutate.unexpected_error",
                customer_id=customer_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            try:
                from src.middleware.sentry_setup import capture_exception as _capture

                _capture(exc)
            except ImportError:
                pass
            raise GoogleAdsWriteError(f"Unexpected error during mutate: {exc}") from exc

    @staticmethod
    def _proto_to_dict(row: Any) -> dict[str, Any]:
        """Convert a protobuf ``GoogleAdsRow`` to a flat Python dict.

        Uses ``MessageToDict`` to get a nested dict, then flattens it so
        keys like ``campaign.id`` become ``campaign_id`` and
        ``metrics.clicks`` become ``metrics_clicks``.

        Args:
            row: A protobuf GoogleAdsRow object.

        Returns:
            Flat dictionary with ``section_field`` keys.
        """
        nested = MessageToDict(row._pb if hasattr(row, "_pb") else row)
        flat: dict[str, Any] = {}
        for section, fields in nested.items():
            if isinstance(fields, dict):
                for field_name, value in fields.items():
                    # camelCase -> snake_case for common fields
                    key = f"{section}_{_camel_to_snake(field_name)}"
                    flat[key] = value
            else:
                flat[section] = fields
        return flat

    def _format_metric_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Transform a raw metric row dict into a clean output dict.

        Converts monetary micro values to decimals and maps Google Ads
        metric names to the normalised names from ``GOOGLE_ADS_METRIC_MAP``.

        Args:
            row: Flat dict from ``_proto_to_dict``.

        Returns:
            Dict with ``campaign_id``, ``campaign_name``, ``date``,
            and normalised metric fields.
        """
        result: dict[str, Any] = {
            "campaign_id": str(row.get("campaign_id", "")),
            "campaign_name": row.get("campaign_name", ""),
            "date": row.get("segments_date", ""),
        }

        # Map each metric using the GOOGLE_ADS_METRIC_MAP
        for google_key, our_key in GOOGLE_ADS_METRIC_MAP.items():
            # google_key = "metrics.cost_micros"
            # In our flat dict, it becomes "metrics_cost_micros"
            flat_key = google_key.replace(".", "_")
            value = row.get(flat_key)

            if value is None:
                result[our_key] = (
                    0
                    if our_key
                    in (
                        "impressions",
                        "clicks",
                        "conversions",
                        "all_conversions",
                        "view_through_conversions",
                    )
                    else 0.0
                )
                continue

            # Convert micros to decimal for monetary fields
            if "micros" in flat_key:
                result[our_key] = float(Decimal(str(value)) / _MICROS)
            else:
                result[our_key] = value

        return result

    def _handle_google_ads_exception(
        self,
        exc: GoogleAdsException,
        operation: str,
        **context: Any,
    ) -> None:
        """Inspect a GoogleAdsException and raise or log appropriately.

        Auth failures (AUTHENTICATION_ERROR, AUTHORIZATION_ERROR) are
        raised as ``GoogleAdsAuthError`` so callers can surface them to
        users. Other errors are logged and swallowed (methods return
        empty results).

        Args:
            exc: The caught exception.
            operation: A label for the operation that failed.
            **context: Extra fields to include in the structured log.

        Raises:
            GoogleAdsAuthError: On authentication / authorization failures.
        """
        capture_exception(exc)

        errors = []
        for error in exc.failure.errors:
            errors.append(
                {
                    "error_code": str(error.error_code),
                    "message": error.message,
                }
            )

        self._log.error(
            "google_ads_api_error",
            operation=operation,
            request_id=exc.request_id,
            errors=errors,
            **context,
        )

        # Check for auth errors -- these must propagate
        for error in exc.failure.errors:
            error_code_name = str(error.error_code)
            if any(
                auth_keyword in error_code_name.upper()
                for auth_keyword in ("AUTHENTICATION", "AUTHORIZATION", "NOT_WHITELISTED")
            ):
                raise GoogleAdsAuthError(
                    f"Google Ads auth error during {operation}: "
                    f"{error.message} (request_id={exc.request_id})"
                ) from exc


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _camel_to_snake(name: str) -> str:
    """Convert a camelCase string to snake_case.

    Examples:
        >>> _camel_to_snake("descriptiveName")
        'descriptive_name'
        >>> _camel_to_snake("currencyCode")
        'currency_code'
        >>> _camel_to_snake("costMicros")
        'cost_micros'
    """
    result: list[str] = []
    for i, char in enumerate(name):
        if char.isupper() and i > 0:
            result.append("_")
        result.append(char.lower())
    return "".join(result)
