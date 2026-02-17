"""Meta (Facebook) Marketing API connector for Sidera.

Read-only connector that pulls campaign data, metrics, audience insights,
and account activity from Meta ad accounts. Spend values arrive as string
decimals (e.g. "123.45") and conversions are nested in actions[] arrays --
both are converted to clean Python types at the connector boundary.

Architecture:
    connector (this file) -> MCP tools -> agent loop
    Each method uses the facebook_business SDK (synchronous), converts
    API objects to plain dicts, and returns clean Python data structures.

Usage:
    from src.connectors.meta import MetaConnector

    connector = MetaConnector()  # uses settings singleton
    accounts = connector.get_ad_accounts()
    campaigns = connector.get_campaigns(account_id="act_123456789")
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from facebook_business.adobjects.ad import Ad
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.adset import AdSet
from facebook_business.adobjects.adsinsights import AdsInsights
from facebook_business.adobjects.campaign import Campaign
from facebook_business.adobjects.user import User
from facebook_business.api import FacebookAdsApi
from facebook_business.exceptions import FacebookRequestError

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
from src.models.normalized import META_CAMPAIGN_OBJECTIVE_MAP
from src.utils.encryption import decrypt_token

logger = structlog.get_logger(__name__)


class MetaConnectorError(Exception):
    """Base exception for Meta connector errors."""

    pass


class MetaAuthError(MetaConnectorError):
    """Authentication or authorization failure -- must be surfaced to user."""

    pass


class MetaWriteError(MetaConnectorError):
    """A write operation failed after approval."""

    pass


class MetaConnector:
    """Read-only client for the Meta Marketing API.

    Wraps the facebook_business SDK and exposes clean, dict-based methods
    for pulling account info, campaigns, performance metrics, audience
    insights, and account activity.

    Args:
        credentials: Optional dict of credentials. If omitted, values are
            read from the ``settings`` singleton (env vars / .env file).
            Expected keys: ``access_token``, ``app_id``, ``app_secret``.
    """

    # Graph API version
    API_VERSION = "v21.0"

    def __init__(self, credentials: dict[str, str] | None = None) -> None:
        self._credentials = credentials or self._credentials_from_settings()
        self._api = self._init_api(self._credentials)
        self._log = logger.bind(connector="meta")

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def get_ad_accounts(self) -> list[dict[str, Any]]:
        """Return ad accounts accessible by the current access token.

        Uses the ``/me/adaccounts`` endpoint to discover which ad accounts
        the token has access to.

        Returns:
            List of dicts with ``id``, ``name``, ``account_status``,
            ``currency``, ``timezone_name``.
        """
        self._log.info("listing_ad_accounts")
        try:
            me = User(fbid="me", api=self._api)
            accounts = me.get_ad_accounts(
                fields=[
                    AdAccount.Field.id,
                    AdAccount.Field.name,
                    AdAccount.Field.account_status,
                    AdAccount.Field.currency,
                    AdAccount.Field.timezone_name,
                ],
            )

            results: list[dict[str, Any]] = []
            for acct in accounts:
                results.append(
                    {
                        "id": acct.get("id", ""),
                        "name": acct.get("name", ""),
                        "account_status": acct.get("account_status"),
                        "currency": acct.get("currency", ""),
                        "timezone_name": acct.get("timezone_name", ""),
                    }
                )

            self._log.info("ad_accounts_found", count=len(results))
            return results

        except FacebookRequestError as exc:
            self._handle_facebook_error(exc, "get_ad_accounts")
            return []

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    @cached(ttl_seconds=CACHE_TTL_ACCOUNT_INFO, key_prefix="meta:get_account_info")
    def get_account_info(self, account_id: str) -> dict[str, Any] | None:
        """Fetch metadata for a single ad account.

        Args:
            account_id: Meta ad account ID (e.g. ``"act_123456789"``).

        Returns:
            Dict with ``id``, ``name``, ``currency``, ``timezone``,
            ``account_status``, ``spend_cap``, ``business_name``,
            or ``None`` on transient failure.
        """
        account_id = self._ensure_act_prefix(account_id)
        self._log.info("getting_account_info", account_id=account_id)
        try:
            account = self._get_account(account_id)
            info = account.api_get(
                fields=[
                    AdAccount.Field.id,
                    AdAccount.Field.name,
                    AdAccount.Field.currency,
                    AdAccount.Field.timezone_name,
                    AdAccount.Field.account_status,
                    AdAccount.Field.spend_cap,
                    AdAccount.Field.business_name,
                ],
            )

            return {
                "id": info.get("id", ""),
                "name": info.get("name", ""),
                "currency": info.get("currency", ""),
                "timezone": info.get("timezone_name", ""),
                "account_status": info.get("account_status"),
                "spend_cap": info.get("spend_cap", ""),
                "business_name": info.get("business_name", ""),
            }

        except FacebookRequestError as exc:
            self._handle_facebook_error(exc, "get_account_info", account_id=account_id)
            return None

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    @cached(ttl_seconds=CACHE_TTL_CAMPAIGNS, key_prefix="meta:get_campaigns")
    def get_campaigns(self, account_id: str) -> list[dict[str, Any]]:
        """Fetch all campaigns (non-deleted) for an account.

        Args:
            account_id: Meta ad account ID (e.g. ``"act_123456789"``).

        Returns:
            List of campaign dicts with keys: ``id``, ``name``, ``objective``,
            ``status``, ``daily_budget``, ``lifetime_budget``, ``bid_strategy``,
            ``platform_data``.
        """
        account_id = self._ensure_act_prefix(account_id)
        self._log.info("getting_campaigns", account_id=account_id)
        try:
            account = self._get_account(account_id)
            campaigns_cursor = account.get_campaigns(
                fields=[
                    Campaign.Field.id,
                    Campaign.Field.name,
                    Campaign.Field.objective,
                    Campaign.Field.status,
                    Campaign.Field.daily_budget,
                    Campaign.Field.lifetime_budget,
                    Campaign.Field.bid_strategy,
                ],
                params={
                    "filtering": [
                        {
                            "field": "effective_status",
                            "operator": "NOT_IN",
                            "value": ["DELETED"],
                        }
                    ],
                },
            )

            campaigns: list[dict[str, Any]] = []
            for camp in campaigns_cursor:
                raw_objective = camp.get("objective", "")
                mapped_objective = META_CAMPAIGN_OBJECTIVE_MAP.get(
                    raw_objective,
                    raw_objective.lower() if raw_objective else "unknown",
                )

                # Meta returns budgets as string cents (e.g. "5000" = $50.00)
                daily_budget_raw = camp.get("daily_budget")
                lifetime_budget_raw = camp.get("lifetime_budget")
                daily_budget = (
                    float(Decimal(daily_budget_raw) / Decimal("100")) if daily_budget_raw else None
                )
                lifetime_budget = (
                    float(Decimal(lifetime_budget_raw) / Decimal("100"))
                    if lifetime_budget_raw
                    else None
                )

                campaigns.append(
                    {
                        "id": camp.get("id", ""),
                        "name": camp.get("name", ""),
                        "objective": mapped_objective,
                        "status": (camp.get("status", "")).lower(),
                        "daily_budget": daily_budget,
                        "lifetime_budget": lifetime_budget,
                        "bid_strategy": (camp.get("bid_strategy", "") or "").lower(),
                        "platform_data": {
                            "raw_objective": raw_objective,
                        },
                    }
                )

            self._log.info(
                "campaigns_fetched",
                account_id=account_id,
                count=len(campaigns),
            )
            return campaigns

        except FacebookRequestError as exc:
            self._handle_facebook_error(exc, "get_campaigns", account_id=account_id)
            return []

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    @cached(ttl_seconds=CACHE_TTL_METRICS, key_prefix="meta:get_campaign_metrics")
    def get_campaign_metrics(
        self,
        account_id: str,
        campaign_id: str,
        start_date: date | str,
        end_date: date | str,
    ) -> list[dict[str, Any]]:
        """Fetch daily metrics for a single campaign over a date range.

        Args:
            account_id: Meta ad account ID (e.g. ``"act_123456789"``).
            campaign_id: The campaign ID to filter on.
            start_date: Start of date range (inclusive).
            end_date: End of date range (inclusive).

        Returns:
            List of daily metric dicts with cleaned values.
        """
        account_id = self._ensure_act_prefix(account_id)
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
            account = self._get_account(account_id)
            insights = account.get_insights(
                fields=[
                    AdsInsights.Field.campaign_id,
                    AdsInsights.Field.campaign_name,
                    AdsInsights.Field.impressions,
                    AdsInsights.Field.clicks,
                    AdsInsights.Field.spend,
                    AdsInsights.Field.actions,
                    AdsInsights.Field.action_values,
                    AdsInsights.Field.cpm,
                    AdsInsights.Field.cpp,
                    AdsInsights.Field.frequency,
                    AdsInsights.Field.reach,
                    AdsInsights.Field.date_start,
                    AdsInsights.Field.date_stop,
                ],
                params={
                    "time_range": {
                        "since": start_str,
                        "until": end_str,
                    },
                    "time_increment": 1,  # Daily breakdown
                    "filtering": [
                        {
                            "field": "campaign.id",
                            "operator": "EQUAL",
                            "value": str(campaign_id),
                        }
                    ],
                },
            )

            return [self._format_insights_row(row) for row in insights]

        except FacebookRequestError as exc:
            self._handle_facebook_error(
                exc, "get_campaign_metrics", account_id=account_id, campaign_id=campaign_id
            )
            return []

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    @cached(ttl_seconds=CACHE_TTL_METRICS, key_prefix="meta:get_account_metrics")
    def get_account_metrics(
        self,
        account_id: str,
        start_date: date | str,
        end_date: date | str,
    ) -> list[dict[str, Any]]:
        """Fetch daily metrics for ALL campaigns in an account.

        Returns one row per campaign per day over the date range.

        Args:
            account_id: Meta ad account ID.
            start_date: Start of date range (inclusive).
            end_date: End of date range (inclusive).

        Returns:
            List of daily metric dicts (one per campaign per day).
        """
        account_id = self._ensure_act_prefix(account_id)
        start_str = start_date.isoformat() if isinstance(start_date, date) else str(start_date)
        end_str = end_date.isoformat() if isinstance(end_date, date) else str(end_date)

        self._log.info(
            "getting_account_metrics",
            account_id=account_id,
            start_date=start_str,
            end_date=end_str,
        )
        try:
            account = self._get_account(account_id)
            insights = account.get_insights(
                fields=[
                    AdsInsights.Field.campaign_id,
                    AdsInsights.Field.campaign_name,
                    AdsInsights.Field.impressions,
                    AdsInsights.Field.clicks,
                    AdsInsights.Field.spend,
                    AdsInsights.Field.actions,
                    AdsInsights.Field.action_values,
                    AdsInsights.Field.cpm,
                    AdsInsights.Field.cpp,
                    AdsInsights.Field.frequency,
                    AdsInsights.Field.reach,
                    AdsInsights.Field.date_start,
                    AdsInsights.Field.date_stop,
                ],
                params={
                    "time_range": {
                        "since": start_str,
                        "until": end_str,
                    },
                    "time_increment": 1,
                    "level": "campaign",
                },
            )

            return [self._format_insights_row(row) for row in insights]

        except FacebookRequestError as exc:
            self._handle_facebook_error(exc, "get_account_metrics", account_id=account_id)
            return []

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    @cached(ttl_seconds=CACHE_TTL_RECOMMENDATIONS, key_prefix="meta:get_campaign_insights")
    def get_campaign_insights(
        self,
        account_id: str,
        campaign_id: str,
        breakdowns: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch detailed insights for a campaign with optional breakdowns.

        Supports breakdowns by age, gender, placement, and device_platform
        for the agent to identify audience/placement optimization
        opportunities.

        Args:
            account_id: Meta ad account ID.
            campaign_id: The campaign ID to analyze.
            breakdowns: Optional list of breakdowns. Supported values:
                ``"age"``, ``"gender"``, ``"publisher_platform"``,
                ``"device_platform"``.

        Returns:
            List of insight dicts broken down by the requested dimensions.
        """
        account_id = self._ensure_act_prefix(account_id)
        self._log.info(
            "getting_campaign_insights",
            account_id=account_id,
            campaign_id=campaign_id,
            breakdowns=breakdowns,
        )

        # Validate breakdowns
        valid_breakdowns = {"age", "gender", "publisher_platform", "device_platform"}
        if breakdowns:
            invalid = set(breakdowns) - valid_breakdowns
            if invalid:
                raise MetaConnectorError(
                    f"Invalid breakdowns: {invalid}. Supported: {valid_breakdowns}"
                )

        try:
            account = self._get_account(account_id)

            params: dict[str, Any] = {
                "time_range": {
                    "since": (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%d"),
                    "until": datetime.now(UTC).strftime("%Y-%m-%d"),
                },
                "filtering": [
                    {
                        "field": "campaign.id",
                        "operator": "EQUAL",
                        "value": str(campaign_id),
                    }
                ],
            }

            if breakdowns:
                params["breakdowns"] = breakdowns

            insights = account.get_insights(
                fields=[
                    AdsInsights.Field.campaign_id,
                    AdsInsights.Field.campaign_name,
                    AdsInsights.Field.impressions,
                    AdsInsights.Field.clicks,
                    AdsInsights.Field.spend,
                    AdsInsights.Field.actions,
                    AdsInsights.Field.action_values,
                    AdsInsights.Field.cpm,
                    AdsInsights.Field.frequency,
                    AdsInsights.Field.reach,
                    AdsInsights.Field.date_start,
                    AdsInsights.Field.date_stop,
                ],
                params=params,
            )

            results: list[dict[str, Any]] = []
            for row in insights:
                formatted = self._format_insights_row(row)
                # Add breakdown dimensions to the result
                if breakdowns:
                    for bd in breakdowns:
                        formatted[bd] = row.get(bd, "")
                results.append(formatted)

            self._log.info(
                "campaign_insights_fetched",
                account_id=account_id,
                campaign_id=campaign_id,
                count=len(results),
            )
            return results

        except FacebookRequestError as exc:
            self._handle_facebook_error(
                exc,
                "get_campaign_insights",
                account_id=account_id,
                campaign_id=campaign_id,
            )
            return []

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def get_account_activity(
        self,
        account_id: str,
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """Fetch recent account activity by comparing metrics over time.

        Pulls daily metrics for the requested window and the preceding
        window of the same length, then flags significant changes in
        spend, status, or performance.

        Args:
            account_id: Meta ad account ID.
            days: Number of days of history to analyze (default 7).

        Returns:
            List of activity dicts describing recent changes.
        """
        account_id = self._ensure_act_prefix(account_id)
        self._log.info(
            "getting_account_activity",
            account_id=account_id,
            days=days,
        )

        try:
            now = datetime.now(UTC)
            current_start = (now - timedelta(days=days)).strftime("%Y-%m-%d")
            current_end = now.strftime("%Y-%m-%d")
            prev_start = (now - timedelta(days=days * 2)).strftime("%Y-%m-%d")
            prev_end = (now - timedelta(days=days + 1)).strftime("%Y-%m-%d")

            # Get current period metrics per campaign
            current_metrics = self.get_account_metrics(account_id, current_start, current_end)
            # Get previous period for comparison
            prev_metrics = self.get_account_metrics(account_id, prev_start, prev_end)

            # Aggregate by campaign for each period
            current_by_campaign = self._aggregate_by_campaign(current_metrics)
            prev_by_campaign = self._aggregate_by_campaign(prev_metrics)

            activities: list[dict[str, Any]] = []

            # Detect changes
            all_campaign_ids = set(current_by_campaign.keys()) | set(prev_by_campaign.keys())
            for cid in all_campaign_ids:
                curr = current_by_campaign.get(cid)
                prev = prev_by_campaign.get(cid)

                if curr and not prev:
                    activities.append(
                        {
                            "type": "new_campaign",
                            "campaign_id": cid,
                            "campaign_name": curr.get("campaign_name", ""),
                            "description": "Campaign started running in the current period.",
                            "current_spend": curr.get("spend", "0"),
                        }
                    )
                elif prev and not curr:
                    activities.append(
                        {
                            "type": "stopped_campaign",
                            "campaign_id": cid,
                            "campaign_name": prev.get("campaign_name", ""),
                            "description": "Campaign stopped running in the current period.",
                            "previous_spend": prev.get("spend", "0"),
                        }
                    )
                elif curr and prev:
                    # Detect significant spend changes (>20%)
                    curr_spend = float(curr.get("spend", 0))
                    prev_spend = float(prev.get("spend", 0))
                    if prev_spend > 0:
                        change_pct = ((curr_spend - prev_spend) / prev_spend) * 100
                        if abs(change_pct) > 20:
                            activities.append(
                                {
                                    "type": "spend_change",
                                    "campaign_id": cid,
                                    "campaign_name": curr.get("campaign_name", ""),
                                    "description": (
                                        f"Spend changed by {change_pct:+.1f}% "
                                        f"(${prev_spend:.2f} -> ${curr_spend:.2f})"
                                    ),
                                    "change_pct": round(change_pct, 1),
                                    "current_spend": curr_spend,
                                    "previous_spend": prev_spend,
                                }
                            )

            self._log.info(
                "account_activity_fetched",
                account_id=account_id,
                count=len(activities),
            )
            return activities

        except FacebookRequestError as exc:
            self._handle_facebook_error(exc, "get_account_activity", account_id=account_id)
            return []

    # ------------------------------------------------------------------
    # Write methods (approval-gated)
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def update_campaign_status(
        self,
        account_id: str,
        campaign_id: str,
        status: str,
    ) -> dict[str, Any]:
        """Enable or pause a Meta campaign.

        Args:
            account_id: Meta ad account ID (e.g. ``act_123456789``).
            campaign_id: Campaign to update.
            status: Target status — ``"ACTIVE"`` or ``"PAUSED"``.

        Returns:
            Dict with ``campaign_id``, ``previous_status``, ``new_status``.

        Raises:
            MetaWriteError: If the API update fails.
            ValueError: If *status* is not ACTIVE or PAUSED.
        """
        if status not in ("ACTIVE", "PAUSED"):
            raise ValueError(f"Invalid status '{status}'. Must be ACTIVE or PAUSED.")

        self._log.info(
            "update_campaign_status.start",
            account_id=account_id,
            campaign_id=campaign_id,
            target_status=status,
        )

        try:
            campaign = Campaign(campaign_id, api=self._api)
            current = campaign.api_get(fields=[Campaign.Field.status])
            previous_status = current.get("status", "UNKNOWN")

            campaign.api_update(params={Campaign.Field.status: status})

            self._log.info(
                "update_campaign_status.success",
                campaign_id=campaign_id,
                previous_status=previous_status,
                new_status=status,
            )
            return {
                "campaign_id": campaign_id,
                "previous_status": previous_status,
                "new_status": status,
            }
        except Exception as exc:
            self._log.error(
                "update_campaign_status.failed",
                campaign_id=campaign_id,
                error=str(exc),
            )
            capture_exception(exc)
            raise MetaWriteError(f"Failed to update campaign status: {exc}") from exc

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def update_campaign_budget(
        self,
        account_id: str,
        campaign_id: str,
        new_budget_cents: int,
        budget_type: str = "daily",
        *,
        validate_cap: bool = True,
    ) -> dict[str, Any]:
        """Update a campaign's daily or lifetime budget.

        Args:
            account_id: Meta ad account ID.
            campaign_id: Campaign to update.
            new_budget_cents: New budget in cents (5000 = $50.00).
            budget_type: ``"daily"`` or ``"lifetime"``.
            validate_cap: If True, reject changes exceeding
                ``settings.max_budget_change_ratio``.

        Returns:
            Dict with ``campaign_id``, ``previous_budget_cents``,
            ``new_budget_cents``, ``budget_type``.

        Raises:
            MetaWriteError: If the API update fails.
            ValueError: If budget change exceeds cap or budget_type invalid.
        """
        if budget_type not in ("daily", "lifetime"):
            raise ValueError(f"Invalid budget_type '{budget_type}'. Must be 'daily' or 'lifetime'.")

        self._log.info(
            "update_campaign_budget.start",
            account_id=account_id,
            campaign_id=campaign_id,
            new_budget_cents=new_budget_cents,
            budget_type=budget_type,
        )

        try:
            campaign = Campaign(campaign_id, api=self._api)
            current = campaign.api_get(
                fields=[Campaign.Field.daily_budget, Campaign.Field.lifetime_budget],
            )

            field_name = f"{budget_type}_budget"
            previous_raw = current.get(field_name)
            previous_budget_cents = int(previous_raw) if previous_raw else 0

            if validate_cap and previous_budget_cents > 0:
                ratio = new_budget_cents / previous_budget_cents
                max_ratio = settings.max_budget_change_ratio
                if ratio > max_ratio or ratio < (1 / max_ratio):
                    raise ValueError(
                        f"Budget change ratio {ratio:.2f} exceeds max allowed "
                        f"{max_ratio:.2f}. Previous: {previous_budget_cents}, "
                        f"New: {new_budget_cents}."
                    )

            campaign.api_update(params={field_name: str(new_budget_cents)})

            self._log.info(
                "update_campaign_budget.success",
                campaign_id=campaign_id,
                previous_budget_cents=previous_budget_cents,
                new_budget_cents=new_budget_cents,
                budget_type=budget_type,
            )
            return {
                "campaign_id": campaign_id,
                "previous_budget_cents": previous_budget_cents,
                "new_budget_cents": new_budget_cents,
                "budget_type": budget_type,
            }
        except ValueError:
            raise
        except Exception as exc:
            self._log.error(
                "update_campaign_budget.failed",
                campaign_id=campaign_id,
                error=str(exc),
            )
            capture_exception(exc)
            raise MetaWriteError(f"Failed to update campaign budget: {exc}") from exc

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def update_adset_status(
        self,
        account_id: str,
        adset_id: str,
        status: str,
    ) -> dict[str, Any]:
        """Enable or pause a Meta ad set.

        Args:
            account_id: Meta ad account ID.
            adset_id: Ad set to update.
            status: ``"ACTIVE"`` or ``"PAUSED"``.

        Returns:
            Dict with ``adset_id``, ``previous_status``, ``new_status``.

        Raises:
            MetaWriteError: If the API update fails.
            ValueError: If *status* is not ACTIVE or PAUSED.
        """
        if status not in ("ACTIVE", "PAUSED"):
            raise ValueError(f"Invalid status '{status}'. Must be ACTIVE or PAUSED.")

        self._log.info(
            "update_adset_status.start",
            account_id=account_id,
            adset_id=adset_id,
            target_status=status,
        )

        try:
            adset = AdSet(adset_id, api=self._api)
            current = adset.api_get(fields=[AdSet.Field.status])
            previous_status = current.get("status", "UNKNOWN")

            adset.api_update(params={AdSet.Field.status: status})

            self._log.info(
                "update_adset_status.success",
                adset_id=adset_id,
                previous_status=previous_status,
                new_status=status,
            )
            return {
                "adset_id": adset_id,
                "previous_status": previous_status,
                "new_status": status,
            }
        except Exception as exc:
            self._log.error(
                "update_adset_status.failed",
                adset_id=adset_id,
                error=str(exc),
            )
            capture_exception(exc)
            raise MetaWriteError(f"Failed to update ad set status: {exc}") from exc

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def update_adset_budget(
        self,
        account_id: str,
        adset_id: str,
        new_budget_cents: int,
        budget_type: str = "daily",
        *,
        validate_cap: bool = True,
    ) -> dict[str, Any]:
        """Update an ad set's daily or lifetime budget.

        Same pattern as ``update_campaign_budget`` but operates on an ad set.

        Args:
            account_id: Meta ad account ID.
            adset_id: Ad set to update.
            new_budget_cents: New budget in cents (5000 = $50.00).
            budget_type: ``"daily"`` or ``"lifetime"``.
            validate_cap: If True, reject changes exceeding
                ``settings.max_budget_change_ratio``.

        Returns:
            Dict with ``adset_id``, ``previous_budget_cents``,
            ``new_budget_cents``, ``budget_type``.

        Raises:
            MetaWriteError: If the API update fails.
            ValueError: If budget change exceeds cap or budget_type invalid.
        """
        if budget_type not in ("daily", "lifetime"):
            raise ValueError(f"Invalid budget_type '{budget_type}'. Must be 'daily' or 'lifetime'.")

        self._log.info(
            "update_adset_budget.start",
            account_id=account_id,
            adset_id=adset_id,
            new_budget_cents=new_budget_cents,
            budget_type=budget_type,
        )

        try:
            adset = AdSet(adset_id, api=self._api)
            current = adset.api_get(
                fields=[AdSet.Field.daily_budget, AdSet.Field.lifetime_budget],
            )

            field_name = f"{budget_type}_budget"
            previous_raw = current.get(field_name)
            previous_budget_cents = int(previous_raw) if previous_raw else 0

            if validate_cap and previous_budget_cents > 0:
                ratio = new_budget_cents / previous_budget_cents
                max_ratio = settings.max_budget_change_ratio
                if ratio > max_ratio or ratio < (1 / max_ratio):
                    raise ValueError(
                        f"Budget change ratio {ratio:.2f} exceeds max allowed "
                        f"{max_ratio:.2f}. Previous: {previous_budget_cents}, "
                        f"New: {new_budget_cents}."
                    )

            adset.api_update(params={field_name: str(new_budget_cents)})

            self._log.info(
                "update_adset_budget.success",
                adset_id=adset_id,
                previous_budget_cents=previous_budget_cents,
                new_budget_cents=new_budget_cents,
                budget_type=budget_type,
            )
            return {
                "adset_id": adset_id,
                "previous_budget_cents": previous_budget_cents,
                "new_budget_cents": new_budget_cents,
                "budget_type": budget_type,
            }
        except ValueError:
            raise
        except Exception as exc:
            self._log.error(
                "update_adset_budget.failed",
                adset_id=adset_id,
                error=str(exc),
            )
            capture_exception(exc)
            raise MetaWriteError(f"Failed to update ad set budget: {exc}") from exc

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def update_ad_status(
        self,
        account_id: str,
        ad_id: str,
        status: str,
    ) -> dict[str, Any]:
        """Enable or pause an individual Meta ad.

        Useful for pausing fatigued creatives without touching the ad set.

        Args:
            account_id: Meta ad account ID.
            ad_id: Ad to update.
            status: ``"ACTIVE"`` or ``"PAUSED"``.

        Returns:
            Dict with ``ad_id``, ``previous_status``, ``new_status``.

        Raises:
            MetaWriteError: If the API update fails.
            ValueError: If *status* is not ACTIVE or PAUSED.
        """
        if status not in ("ACTIVE", "PAUSED"):
            raise ValueError(f"Invalid status '{status}'. Must be ACTIVE or PAUSED.")

        self._log.info(
            "update_ad_status.start",
            account_id=account_id,
            ad_id=ad_id,
            target_status=status,
        )

        try:
            ad = Ad(ad_id, api=self._api)
            current = ad.api_get(fields=[Ad.Field.status])
            previous_status = current.get("status", "UNKNOWN")

            ad.api_update(params={Ad.Field.status: status})

            self._log.info(
                "update_ad_status.success",
                ad_id=ad_id,
                previous_status=previous_status,
                new_status=status,
            )
            return {
                "ad_id": ad_id,
                "previous_status": previous_status,
                "new_status": status,
            }
        except Exception as exc:
            self._log.error(
                "update_ad_status.failed",
                ad_id=ad_id,
                error=str(exc),
            )
            capture_exception(exc)
            raise MetaWriteError(f"Failed to update ad status: {exc}") from exc

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def update_adset_bid(
        self,
        account_id: str,
        adset_id: str,
        bid_amount_cents: int,
    ) -> dict[str, Any]:
        """Update the bid amount for an ad set.

        Args:
            account_id: Meta ad account ID.
            adset_id: Ad set to update.
            bid_amount_cents: New bid amount in cents.

        Returns:
            Dict with ``adset_id``, ``previous_bid_cents``,
            ``new_bid_cents``.

        Raises:
            MetaWriteError: If the API update fails.
        """
        self._log.info(
            "update_adset_bid.start",
            account_id=account_id,
            adset_id=adset_id,
            bid_amount_cents=bid_amount_cents,
        )

        try:
            adset = AdSet(adset_id, api=self._api)
            current = adset.api_get(fields=[AdSet.Field.bid_amount])
            previous_raw = current.get("bid_amount")
            previous_bid_cents = int(previous_raw) if previous_raw else 0

            adset.api_update(params={AdSet.Field.bid_amount: str(bid_amount_cents)})

            self._log.info(
                "update_adset_bid.success",
                adset_id=adset_id,
                previous_bid_cents=previous_bid_cents,
                new_bid_cents=bid_amount_cents,
            )
            return {
                "adset_id": adset_id,
                "previous_bid_cents": previous_bid_cents,
                "new_bid_cents": bid_amount_cents,
            }
        except Exception as exc:
            self._log.error(
                "update_adset_bid.failed",
                adset_id=adset_id,
                error=str(exc),
            )
            capture_exception(exc)
            raise MetaWriteError(f"Failed to update ad set bid: {exc}") from exc

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _credentials_from_settings() -> dict[str, str]:
        """Build a credentials dict from the global settings singleton."""
        return {
            "access_token": decrypt_token(settings.meta_access_token),
            "app_id": settings.meta_app_id,
            "app_secret": settings.meta_app_secret,
        }

    @staticmethod
    def _init_api(credentials: dict[str, str]) -> FacebookAdsApi:
        """Initialize the Facebook Ads API with credentials.

        Args:
            credentials: Dict with ``access_token``, ``app_id``,
                ``app_secret``.

        Returns:
            Initialized ``FacebookAdsApi`` instance.

        Raises:
            MetaAuthError: If credentials are missing or initialization fails.
        """
        access_token = credentials.get("access_token", "")
        app_id = credentials.get("app_id", "")
        app_secret = credentials.get("app_secret", "")

        if not access_token:
            raise MetaAuthError(
                "Meta access token is required. Connect via OAuth or "
                "set META_ACCESS_TOKEN in environment."
            )

        try:
            api = FacebookAdsApi.init(
                app_id=app_id,
                app_secret=app_secret,
                access_token=access_token,
                api_version=MetaConnector.API_VERSION,
            )
            return api
        except Exception as exc:
            raise MetaAuthError(f"Failed to initialize Meta API: {exc}") from exc

    def _get_account(self, account_id: str) -> AdAccount:
        """Return an AdAccount object for the given ID.

        Args:
            account_id: Meta ad account ID (must include ``act_`` prefix).

        Returns:
            An ``AdAccount`` instance bound to our API session.
        """
        return AdAccount(fbid=account_id, api=self._api)

    @staticmethod
    def _ensure_act_prefix(account_id: str) -> str:
        """Ensure the account ID has the ``act_`` prefix.

        Meta ad account IDs must be prefixed with ``act_``. This helper
        adds it if missing.

        Args:
            account_id: Raw account ID string.

        Returns:
            Account ID with ``act_`` prefix.
        """
        account_id = account_id.strip()
        if not account_id.startswith("act_"):
            account_id = f"act_{account_id}"
        return account_id

    @staticmethod
    def _format_insights_row(row: Any) -> dict[str, Any]:
        """Convert a Meta insights row to a clean dict.

        Handles Meta's quirks:
        - ``spend`` is a string decimal (e.g. ``"123.45"``)
        - ``impressions`` and ``clicks`` are string integers
        - ``actions`` is a list of ``{action_type, value}`` dicts
        - ``action_values`` follows the same structure

        Args:
            row: An ``AdsInsights`` object or dict-like from the SDK.

        Returns:
            Clean dict with typed values.
        """
        # Extract conversions from actions array
        actions = row.get("actions")
        action_values = row.get("action_values")

        # Sum all purchase/lead actions for a general conversion count
        conversions = 0.0
        if actions:
            for action in actions:
                action_type = action.get("action_type", "")
                conversion_types = (
                    "purchase",
                    "lead",
                    "complete_registration",
                    "offsite_conversion.fb_pixel_purchase",
                )
                if action_type in conversion_types:
                    conversions += float(action.get("value", 0))

        conversion_value = Decimal("0")
        if action_values:
            for av in action_values:
                action_type = av.get("action_type", "")
                if action_type in ("purchase", "offsite_conversion.fb_pixel_purchase"):
                    conversion_value += Decimal(str(av.get("value", "0")))

        spend_str = row.get("spend", "0") or "0"
        spend = float(Decimal(spend_str))

        return {
            "campaign_id": row.get("campaign_id", ""),
            "campaign_name": row.get("campaign_name", ""),
            "date": row.get("date_start", ""),
            "date_stop": row.get("date_stop", ""),
            "impressions": int(row.get("impressions", 0) or 0),
            "clicks": int(row.get("clicks", 0) or 0),
            "spend": spend,
            "conversions": conversions,
            "conversion_value": float(conversion_value),
            "cpm": float(row.get("cpm", 0) or 0),
            "cpp": float(row.get("cpp", 0) or 0),
            "frequency": float(row.get("frequency", 0) or 0),
            "reach": int(row.get("reach", 0) or 0),
            # Preserve raw arrays for the normalization layer
            "actions": actions,
            "action_values": action_values,
        }

    @staticmethod
    def _aggregate_by_campaign(
        metrics: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Aggregate daily metrics into per-campaign totals.

        Used by ``get_account_activity`` to compare periods.

        Args:
            metrics: List of daily metric dicts.

        Returns:
            Dict mapping campaign_id to aggregated totals.
        """
        by_campaign: dict[str, dict[str, Any]] = {}
        for row in metrics:
            cid = row.get("campaign_id", "")
            if not cid:
                continue
            if cid not in by_campaign:
                by_campaign[cid] = {
                    "campaign_id": cid,
                    "campaign_name": row.get("campaign_name", ""),
                    "spend": 0.0,
                    "impressions": 0,
                    "clicks": 0,
                    "conversions": 0.0,
                }
            agg = by_campaign[cid]
            agg["spend"] += float(row.get("spend", 0))
            agg["impressions"] += int(row.get("impressions", 0))
            agg["clicks"] += int(row.get("clicks", 0))
            agg["conversions"] += float(row.get("conversions", 0))
        return by_campaign

    def _handle_facebook_error(
        self,
        exc: FacebookRequestError,
        operation: str,
        **context: Any,
    ) -> None:
        """Inspect a FacebookRequestError and raise or log appropriately.

        Auth failures (error codes 190, 102) are raised as ``MetaAuthError``
        so callers can surface them to users. Other errors are logged and
        swallowed (methods return empty results).

        Auth error codes:
        - 190: Invalid or expired access token
        - 102: API session expired
        - 10: Application does not have permission
        - 200-299: Permission errors

        Args:
            exc: The caught exception.
            operation: A label for the operation that failed.
            **context: Extra fields to include in the structured log.

        Raises:
            MetaAuthError: On authentication / authorization failures.
        """
        capture_exception(exc)

        error_code = exc.api_error_code()
        error_subcode = exc.api_error_subcode()
        error_message = exc.api_error_message()

        self._log.error(
            "meta_api_error",
            operation=operation,
            error_code=error_code,
            error_subcode=error_subcode,
            error_message=error_message,
            **context,
        )

        # Check for auth errors
        auth_error_codes = {190, 102, 10}
        if error_code in auth_error_codes or (200 <= (error_code or 0) < 300):
            raise MetaAuthError(
                f"Meta auth error during {operation}: "
                f"{error_message} (code={error_code}, subcode={error_subcode})"
            ) from exc
