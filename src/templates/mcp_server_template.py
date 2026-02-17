"""__Channel__ MCP server tools for Sidera.

Provides 5 read-only tools that the Claude agent can call to pull data from
__Channel__ ad accounts. All tools return formatted text that Claude can
reason about directly.

Tools:
    1. list___CHANNEL___accounts        - List connected accounts
    2. get___CHANNEL___campaigns        - Get campaigns for an account
    3. get___CHANNEL___performance      - Get performance metrics for a date range
    4. get___CHANNEL___insights         - Get platform-specific breakdowns
    5. get___CHANNEL___account_activity - Get recent account changes

Usage:
    from src.mcp_servers.__CHANNEL__ import create___CHANNEL___mcp_server

    server_config = create___CHANNEL___mcp_server()
"""

from __future__ import annotations

from typing import Any

import structlog
from src.agent.tool_registry import tool

from src.connectors.__CHANNEL__ import __Channel__Connector
from src.mcp_servers.helpers import (
    error_response,
    format_currency,
    format_number,
    format_percentage,  # noqa: F401 — available for performance formatting
    text_response,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_connector() -> __Channel__Connector:
    """Create a fresh __Channel__Connector instance."""
    return __Channel__Connector()


# ---------------------------------------------------------------------------
# Tool 1: List accounts
# ---------------------------------------------------------------------------

LIST_ACCOUNTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
}


@tool(
    name="list___CHANNEL___accounts",
    description=(
        "Lists all __Channel__ ad accounts the user has connected. "
        "Returns each account's ID, name, status, currency, and timezone. "
        "Use this to discover which accounts are available before pulling "
        "campaign or performance data."
    ),
    input_schema=LIST_ACCOUNTS_SCHEMA,
)
async def list___CHANNEL___accounts(args: dict[str, Any]) -> dict[str, Any]:
    """List all accessible __Channel__ ad accounts."""
    logger.info("tool.list___CHANNEL___accounts")
    try:
        connector = _get_connector()
        accounts = connector.get_ad_accounts()

        if not accounts:
            return text_response(
                "No __Channel__ ad accounts found. The user may need to connect "
                "their __Channel__ account through the OAuth flow first."
            )

        lines = [f"Found {len(accounts)} __Channel__ ad account(s):\n"]
        for acct in accounts:
            acct_id = acct.get("id", "unknown")
            name = acct.get("name", "Unnamed")
            status = acct.get("status", "unknown")
            currency = acct.get("currency", "")
            timezone = acct.get("timezone", "")

            lines.append(f"  - {name} (ID: {acct_id})")
            lines.append(f"      Status: {status}")
            if currency:
                lines.append(f"      Currency: {currency}")
            if timezone:
                lines.append(f"      Timezone: {timezone}")

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error("tool.list___CHANNEL___accounts.error", error=str(exc))
        return error_response(f"Failed to list __Channel__ accounts: {exc}")


# ---------------------------------------------------------------------------
# Tool 2: Get campaigns
# ---------------------------------------------------------------------------

GET_CAMPAIGNS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "account_id": {
            "type": "string",
            "description": (
                "__Channel__ ad account ID. "
                "Use list___CHANNEL___accounts to find available IDs."
            ),
        },
    },
    "required": ["account_id"],
}


@tool(
    name="get___CHANNEL___campaigns",
    description=(
        "Gets all campaigns for a __Channel__ ad account. Returns campaign "
        "names, objectives, status (active/paused), daily budget, lifetime "
        "budget, and bid strategy. Use this to understand account structure "
        "before analyzing performance."
    ),
    input_schema=GET_CAMPAIGNS_SCHEMA,
)
async def get___CHANNEL___campaigns(args: dict[str, Any]) -> dict[str, Any]:
    """Get all campaigns for a __Channel__ ad account."""
    account_id = args.get("account_id", "").strip()
    if not account_id:
        return error_response("account_id is required.")

    logger.info("tool.get___CHANNEL___campaigns", account_id=account_id)
    try:
        connector = _get_connector()
        campaigns = connector.get_campaigns(account_id)

        if not campaigns:
            return text_response(
                f"No campaigns found for account {account_id}."
            )

        lines = [f"Account {account_id} has {len(campaigns)} campaign(s):\n"]
        for camp in campaigns:
            campaign_id = camp.get("id", "unknown")
            name = camp.get("name", "Unnamed")
            status = camp.get("status", "unknown")
            objective = camp.get("objective", "unknown")
            daily_budget = camp.get("daily_budget")
            lifetime_budget = camp.get("lifetime_budget")

            lines.append(f"  Campaign: {name}")
            lines.append(f"    ID: {campaign_id}")
            lines.append(f"    Objective: {objective}")
            lines.append(f"    Status: {status}")
            if daily_budget is not None:
                lines.append(f"    Daily budget: {format_currency(daily_budget)}")
            if lifetime_budget is not None:
                lines.append(f"    Lifetime budget: {format_currency(lifetime_budget)}")
            lines.append("")

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error("tool.get___CHANNEL___campaigns.error", account_id=account_id, error=str(exc))
        return error_response(f"Failed to get campaigns for {account_id}: {exc}")


# ---------------------------------------------------------------------------
# Tool 3: Get performance metrics
# ---------------------------------------------------------------------------

GET_PERFORMANCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "account_id": {
            "type": "string",
            "description": "__Channel__ ad account ID.",
        },
        "start_date": {
            "type": "string",
            "description": "Start date in YYYY-MM-DD format.",
        },
        "end_date": {
            "type": "string",
            "description": "End date in YYYY-MM-DD format.",
        },
        "campaign_id": {
            "type": "string",
            "description": (
                "Optional. Campaign ID to filter by. If omitted, returns "
                "metrics for all campaigns in the account."
            ),
        },
    },
    "required": ["account_id", "start_date", "end_date"],
}


@tool(
    name="get___CHANNEL___performance",
    description=(
        "Gets performance metrics for a __Channel__ ad account over a date "
        "range. Returns daily spend, clicks, impressions, conversions, "
        "conversion value, CTR, CPC, CPA, and ROAS. Optionally filter by a "
        "specific campaign ID. Dates must be in YYYY-MM-DD format."
    ),
    input_schema=GET_PERFORMANCE_SCHEMA,
)
async def get___CHANNEL___performance(args: dict[str, Any]) -> dict[str, Any]:
    """Get performance metrics for a date range."""
    account_id = args.get("account_id", "").strip()
    start_date = args.get("start_date", "").strip()
    end_date = args.get("end_date", "").strip()
    campaign_id = args.get("campaign_id")
    if campaign_id:
        campaign_id = str(campaign_id).strip()

    if not account_id:
        return error_response("account_id is required.")
    if not start_date:
        return error_response("start_date is required (YYYY-MM-DD).")
    if not end_date:
        return error_response("end_date is required (YYYY-MM-DD).")

    logger.info(
        "tool.get___CHANNEL___performance",
        account_id=account_id,
        start_date=start_date,
        end_date=end_date,
        campaign_id=campaign_id,
    )
    try:
        connector = _get_connector()

        if campaign_id:
            raw_rows = connector.get_campaign_metrics(
                account_id, campaign_id, start_date, end_date
            )
            scope = f"campaign {campaign_id}"
        else:
            raw_rows = connector.get_account_metrics(account_id, start_date, end_date)
            scope = "all campaigns"

        if not raw_rows:
            return text_response(
                f"No performance data found for account {account_id} ({scope}) "
                f"between {start_date} and {end_date}."
            )

        # TODO: Format metrics into readable text
        # Follow the pattern in src/mcp_servers/meta.py:get_meta_performance
        # Aggregate totals and build daily breakdown lines
        lines = [
            f"Performance for account {account_id} ({scope})",
            f"Date range: {start_date} to {end_date}",
            f"{len(raw_rows)} data row(s)",
            "",
        ]

        for row in raw_rows:
            spend = row.get("spend", 0)
            clicks = row.get("clicks", 0)
            impressions = row.get("impressions", 0)
            row_date = row.get("date", "")
            campaign_name = row.get("campaign_name", "")

            prefix = f"  {row_date}"
            if campaign_name:
                prefix += f" | {campaign_name}"

            lines.append(
                f"{prefix}: "
                f"Spend={format_currency(spend)}  "
                f"Clicks={format_number(clicks)}  "
                f"Impr={format_number(impressions)}"
            )

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error(
            "tool.get___CHANNEL___performance.error",
            account_id=account_id,
            error=str(exc),
        )
        return error_response(f"Failed to get performance for {account_id}: {exc}")


# ---------------------------------------------------------------------------
# Tool 4: Get insights / breakdowns
# ---------------------------------------------------------------------------

GET_INSIGHTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "account_id": {
            "type": "string",
            "description": "__Channel__ ad account ID.",
        },
        "campaign_id": {
            "type": "string",
            "description": "Campaign ID to analyze.",
        },
        # TODO: Add platform-specific breakdown options
        # "breakdown": {
        #     "type": "string",
        #     "description": "Dimension to break down by.",
        #     "enum": ["age", "gender", "placement", "device"],
        # },
    },
    "required": ["account_id", "campaign_id"],
}


@tool(
    name="get___CHANNEL___insights",
    description=(
        "Gets detailed insights or breakdown data for a __Channel__ campaign. "
        "Use this to identify which audiences, placements, or creatives are "
        "driving the best performance and where budget might be wasted."
    ),
    input_schema=GET_INSIGHTS_SCHEMA,
)
async def get___CHANNEL___insights(args: dict[str, Any]) -> dict[str, Any]:
    """Get detailed insights for a campaign."""
    account_id = args.get("account_id", "").strip()
    campaign_id = args.get("campaign_id", "").strip()

    if not account_id:
        return error_response("account_id is required.")
    if not campaign_id:
        return error_response("campaign_id is required.")

    logger.info(
        "tool.get___CHANNEL___insights",
        account_id=account_id,
        campaign_id=campaign_id,
    )
    try:
        # TODO: Call connector method for platform-specific insights
        # connector = _get_connector()
        # insights = connector.get_campaign_insights(account_id, campaign_id)
        return text_response(
            f"Insights for campaign {campaign_id}: TODO — implement "
            "platform-specific insight retrieval."
        )

    except Exception as exc:
        logger.error(
            "tool.get___CHANNEL___insights.error",
            account_id=account_id,
            campaign_id=campaign_id,
            error=str(exc),
        )
        return error_response(f"Failed to get insights for campaign {campaign_id}: {exc}")


# ---------------------------------------------------------------------------
# Tool 5: Get account activity
# ---------------------------------------------------------------------------

GET_ACTIVITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "account_id": {
            "type": "string",
            "description": "__Channel__ ad account ID.",
        },
        "days": {
            "type": "integer",
            "description": (
                "Number of days of activity to retrieve. Defaults to 7. "
                "Maximum 30."
            ),
        },
    },
    "required": ["account_id"],
}


@tool(
    name="get___CHANNEL___account_activity",
    description=(
        "Gets recent changes and activity for a __Channel__ ad account. "
        "Shows campaigns that started or stopped, significant spend changes, "
        "and other notable shifts. Defaults to 7 days of history."
    ),
    input_schema=GET_ACTIVITY_SCHEMA,
)
async def get___CHANNEL___account_activity(args: dict[str, Any]) -> dict[str, Any]:
    """Get recent account activity and changes."""
    account_id = args.get("account_id", "").strip()
    days = args.get("days", 7)

    if not account_id:
        return error_response("account_id is required.")

    try:
        days = int(days)
        days = max(1, min(days, 30))
    except (TypeError, ValueError):
        days = 7

    logger.info("tool.get___CHANNEL___account_activity", account_id=account_id, days=days)
    try:
        # TODO: Call connector method for account activity
        # connector = _get_connector()
        # activities = connector.get_account_activity(account_id, days=days)
        return text_response(
            f"Account activity for {account_id} (last {days} days): "
            "TODO — implement platform-specific activity retrieval."
        )

    except Exception as exc:
        logger.error(
            "tool.get___CHANNEL___account_activity.error",
            account_id=account_id,
            error=str(exc),
        )
        return error_response(f"Failed to get activity for {account_id}: {exc}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create___CHANNEL___tools() -> list[Any]:
    """Return the list of __Channel__ MCP tool definitions.

    Returns:
        List of 5 SdkMcpTool instances (all read-only).
    """
    return [
        list___CHANNEL___accounts,
        get___CHANNEL___campaigns,
        get___CHANNEL___performance,
        get___CHANNEL___insights,
        get___CHANNEL___account_activity,
    ]
