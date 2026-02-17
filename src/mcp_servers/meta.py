"""Meta (Facebook) Marketing API MCP server tools for Sidera.

Provides 7 tools that the Claude agent can call — 5 read-only data tools
plus 2 write tools that require prior human approval.

Tools (read):
    1. list_meta_ad_accounts       - List connected Meta ad accounts
    2. get_meta_campaigns          - Get campaigns for an account
    3. get_meta_performance        - Get performance metrics for a date range
    4. get_meta_audience_insights  - Get audience/placement breakdowns
    5. get_meta_account_activity   - Get recent account changes

Tools (write — approval required):
    6. update_meta_campaign        - Pause/enable or update campaign budget
    7. update_meta_ad              - Pause/enable campaign, ad set, or ad

Usage:
    from src.mcp_servers.meta import create_meta_mcp_server

    server_config = create_meta_mcp_server()
    # Pass to ClaudeAgentOptions.mcp_servers
"""

from __future__ import annotations

import json
import traceback
from decimal import Decimal
from typing import Any

import structlog

from src.agent.tool_registry import tool
from src.connectors.meta import MetaConnector
from src.mcp_servers.helpers import (
    error_response,
    format_currency,
    format_number,
    format_percentage,
    text_response,
)
from src.models.normalized import normalize_meta_metrics

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_connector() -> MetaConnector:
    """Create a fresh MetaConnector instance."""
    return MetaConnector()


# ---------------------------------------------------------------------------
# Tool 1: List ad accounts
# ---------------------------------------------------------------------------

LIST_ACCOUNTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
}


@tool(
    name="list_meta_ad_accounts",
    description=(
        "Lists all Meta (Facebook/Instagram) ad accounts the user has connected. "
        "Returns each account's ID, name, status, currency, and timezone. Use this "
        "to discover which accounts are available before pulling campaign or "
        "performance data."
    ),
    input_schema=LIST_ACCOUNTS_SCHEMA,
)
async def list_meta_ad_accounts(args: dict[str, Any]) -> dict[str, Any]:
    """List all accessible Meta ad accounts."""
    logger.info("tool.list_meta_ad_accounts")
    try:
        connector = _get_connector()
        accounts = connector.get_ad_accounts()

        if not accounts:
            return text_response(
                "No Meta ad accounts found. The user may need to connect their "
                "Meta ad account through the OAuth flow first."
            )

        lines = [f"Found {len(accounts)} Meta ad account(s):\n"]
        for acct in accounts:
            acct_id = acct.get("id", "unknown")
            name = acct.get("name", "Unnamed")
            currency = acct.get("currency", "")
            timezone = acct.get("timezone_name", "")
            status = acct.get("account_status", "")

            # Map status codes to human-readable
            status_map = {
                1: "active",
                2: "disabled",
                3: "unsettled",
                7: "pending_risk_review",
                9: "in_grace_period",
                100: "pending_closure",
                101: "closed",
            }
            status_label = (
                status_map.get(status, str(status)) if isinstance(status, int) else str(status)
            )

            lines.append(f"  - {name} (ID: {acct_id})")
            lines.append(f"      Status: {status_label}")
            if currency:
                lines.append(f"      Currency: {currency}")
            if timezone:
                lines.append(f"      Timezone: {timezone}")

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error("tool.list_meta_ad_accounts.error", error=str(exc))
        return error_response(f"Failed to list Meta ad accounts: {exc}")


# ---------------------------------------------------------------------------
# Tool 2: Get campaigns
# ---------------------------------------------------------------------------

GET_CAMPAIGNS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "account_id": {
            "type": "string",
            "description": (
                "Meta ad account ID (e.g. 'act_123456789'). "
                "Use list_meta_ad_accounts to find available IDs."
            ),
        },
    },
    "required": ["account_id"],
}


@tool(
    name="get_meta_campaigns",
    description=(
        "Gets all campaigns for a Meta ad account. Returns campaign names, "
        "objectives (sales, leads, traffic, awareness, etc.), status "
        "(active/paused), daily budget, lifetime budget, and bid strategy. "
        "Use this to understand the account structure before analyzing performance."
    ),
    input_schema=GET_CAMPAIGNS_SCHEMA,
)
async def get_meta_campaigns(args: dict[str, Any]) -> dict[str, Any]:
    """Get all campaigns for a Meta ad account."""
    account_id = args.get("account_id", "").strip()
    if not account_id:
        return error_response("account_id is required.")

    logger.info("tool.get_meta_campaigns", account_id=account_id)
    try:
        connector = _get_connector()
        campaigns = connector.get_campaigns(account_id)

        if not campaigns:
            return text_response(
                f"No campaigns found for account {account_id}. "
                "The account may be empty or the credentials may lack access."
            )

        lines = [f"Account {account_id} has {len(campaigns)} campaign(s):\n"]
        for camp in campaigns:
            campaign_id = camp.get("id", "unknown")
            name = camp.get("name", "Unnamed")
            status = camp.get("status", "unknown")
            objective = camp.get("objective", "unknown")
            daily_budget = camp.get("daily_budget")
            lifetime_budget = camp.get("lifetime_budget")
            bid_strategy = camp.get("bid_strategy", "unknown")

            lines.append(f"  Campaign: {name}")
            lines.append(f"    ID: {campaign_id}")
            lines.append(f"    Objective: {objective}")
            lines.append(f"    Status: {status}")
            if daily_budget is not None:
                lines.append(f"    Daily budget: {format_currency(daily_budget)}")
            if lifetime_budget is not None:
                lines.append(f"    Lifetime budget: {format_currency(lifetime_budget)}")
            lines.append(f"    Bid strategy: {bid_strategy}")
            lines.append("")

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error("tool.get_meta_campaigns.error", account_id=account_id, error=str(exc))
        return error_response(f"Failed to get campaigns for account {account_id}: {exc}")


# ---------------------------------------------------------------------------
# Tool 3: Get performance metrics
# ---------------------------------------------------------------------------

GET_PERFORMANCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "account_id": {
            "type": "string",
            "description": "Meta ad account ID (e.g. 'act_123456789').",
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
    name="get_meta_performance",
    description=(
        "Gets performance metrics for a Meta ad account over a date range. "
        "Returns daily spend, clicks, impressions, conversions, conversion value, "
        "CTR, CPC, CPA, and ROAS. Optionally filter by a specific campaign ID. "
        "All monetary values are in the account's currency. Dates must be in "
        "YYYY-MM-DD format."
    ),
    input_schema=GET_PERFORMANCE_SCHEMA,
)
async def get_meta_performance(args: dict[str, Any]) -> dict[str, Any]:
    """Get performance metrics for a date range, optionally filtered by campaign."""
    account_id = args.get("account_id", "").strip()
    start_date = args.get("start_date", "").strip()
    end_date = args.get("end_date", "").strip()
    campaign_id = args.get("campaign_id")
    if campaign_id:
        campaign_id = str(campaign_id).strip()

    # Validate required fields
    if not account_id:
        return error_response("account_id is required.")
    if not start_date:
        return error_response("start_date is required (YYYY-MM-DD).")
    if not end_date:
        return error_response("end_date is required (YYYY-MM-DD).")

    logger.info(
        "tool.get_meta_performance",
        account_id=account_id,
        start_date=start_date,
        end_date=end_date,
        campaign_id=campaign_id,
    )
    try:
        connector = _get_connector()

        if campaign_id:
            raw_rows = connector.get_campaign_metrics(account_id, campaign_id, start_date, end_date)
            scope = f"campaign {campaign_id}"
        else:
            raw_rows = connector.get_account_metrics(account_id, start_date, end_date)
            scope = "all campaigns"

        if not raw_rows:
            return text_response(
                f"No performance data found for account {account_id} ({scope}) "
                f"between {start_date} and {end_date}."
            )

        # Normalize and aggregate
        total_impressions = 0
        total_clicks = 0
        total_cost = Decimal("0")
        total_conversions = 0.0
        total_conv_value = Decimal("0")

        daily_lines: list[str] = []
        for row in raw_rows:
            try:
                row_date_str = row.get("date", "")
                nm = normalize_meta_metrics(
                    campaign_id=0,  # placeholder -- we only need the numbers
                    metric_date=row_date_str,  # type: ignore[arg-type]
                    raw=row,
                )
                total_impressions += nm.impressions
                total_clicks += nm.clicks
                total_cost += nm.cost
                total_conversions += nm.conversions
                total_conv_value += nm.conversion_value

                campaign_label = row.get("campaign_name", "")
                date_label = str(row_date_str) if row_date_str else ""
                prefix = f"  {date_label}"
                if campaign_label:
                    prefix += f" | {campaign_label}"

                roas_str = f"{nm.roas:.2f}x" if nm.roas is not None else "N/A"
                daily_lines.append(
                    f"{prefix}: "
                    f"Spend={format_currency(nm.cost)}  "
                    f"Clicks={format_number(nm.clicks)}  "
                    f"Impr={format_number(nm.impressions)}  "
                    f"Conv={format_number(nm.conversions)}  "
                    f"Value={format_currency(nm.conversion_value)}  "
                    f"CPA={format_currency(nm.cpa)}  "
                    f"ROAS={roas_str}"
                )
            except Exception:
                # Fallback: dump the raw row so the agent isn't blind
                daily_lines.append(f"  {json.dumps(row, default=str)}")

        # Summary
        summary_ctr = (total_clicks / total_impressions) if total_impressions > 0 else None
        summary_cpc = (total_cost / total_clicks) if total_clicks > 0 else None
        summary_cpa = (
            total_cost / Decimal(str(total_conversions)) if total_conversions > 0 else None
        )
        summary_roas = (float(total_conv_value / total_cost)) if total_cost > 0 else None

        header = (
            f"Performance for account {account_id} ({scope})\n"
            f"Date range: {start_date} to {end_date}\n"
            f"{len(raw_rows)} data row(s)\n"
            f"\n"
            f"TOTALS:\n"
            f"  Spend:       {format_currency(total_cost)}\n"
            f"  Clicks:      {format_number(total_clicks)}\n"
            f"  Impressions: {format_number(total_impressions)}\n"
            f"  CTR:         {format_percentage(summary_ctr)}\n"
            f"  Avg CPC:     {format_currency(summary_cpc)}\n"
            f"  Conversions: {format_number(total_conversions)}\n"
            f"  Conv value:  {format_currency(total_conv_value)}\n"
            f"  CPA:         {format_currency(summary_cpa)}\n"
            f"  ROAS:        {f'{summary_roas:.2f}x' if summary_roas is not None else 'N/A'}\n"
            f"\nDAILY BREAKDOWN:\n"
        )
        return text_response(header + "\n".join(daily_lines))

    except Exception as exc:
        logger.error(
            "tool.get_meta_performance.error",
            account_id=account_id,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        return error_response(f"Failed to get performance data for account {account_id}: {exc}")


# ---------------------------------------------------------------------------
# Tool 4: Get audience insights
# ---------------------------------------------------------------------------

GET_AUDIENCE_INSIGHTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "account_id": {
            "type": "string",
            "description": "Meta ad account ID (e.g. 'act_123456789').",
        },
        "campaign_id": {
            "type": "string",
            "description": "Campaign ID to analyze.",
        },
        "breakdown": {
            "type": "string",
            "description": (
                "Dimension to break down by. One of: 'age', 'gender', "
                "'publisher_platform' (Facebook/Instagram/Audience Network), "
                "'device_platform' (mobile/desktop)."
            ),
            "enum": ["age", "gender", "publisher_platform", "device_platform"],
        },
    },
    "required": ["account_id", "campaign_id", "breakdown"],
}


@tool(
    name="get_meta_audience_insights",
    description=(
        "Gets audience or placement breakdown insights for a Meta campaign. "
        "Shows performance metrics broken down by age, gender, platform "
        "(Facebook/Instagram), or device (mobile/desktop). Use this to identify "
        "which audiences or placements are driving the best performance and "
        "where budget might be wasted."
    ),
    input_schema=GET_AUDIENCE_INSIGHTS_SCHEMA,
)
async def get_meta_audience_insights(args: dict[str, Any]) -> dict[str, Any]:
    """Get audience/placement breakdown for a Meta campaign."""
    account_id = args.get("account_id", "").strip()
    campaign_id = args.get("campaign_id", "").strip()
    breakdown = args.get("breakdown", "").strip()

    if not account_id:
        return error_response("account_id is required.")
    if not campaign_id:
        return error_response("campaign_id is required.")
    if not breakdown:
        return error_response(
            "breakdown is required (age, gender, publisher_platform, or device_platform)."
        )

    valid_breakdowns = {"age", "gender", "publisher_platform", "device_platform"}
    if breakdown not in valid_breakdowns:
        return error_response(
            f"Invalid breakdown '{breakdown}'. "
            f"Must be one of: {', '.join(sorted(valid_breakdowns))}"
        )

    logger.info(
        "tool.get_meta_audience_insights",
        account_id=account_id,
        campaign_id=campaign_id,
        breakdown=breakdown,
    )
    try:
        connector = _get_connector()
        insights = connector.get_campaign_insights(account_id, campaign_id, breakdowns=[breakdown])

        if not insights:
            return text_response(
                f"No audience insights found for campaign {campaign_id} "
                f"with breakdown by {breakdown}."
            )

        campaign_name = insights[0].get("campaign_name", campaign_id) if insights else campaign_id

        lines = [
            f"Audience insights for campaign: {campaign_name}\n"
            f"Breakdown by: {breakdown}\n"
            f"Period: last 30 days\n"
            f"{len(insights)} segment(s) found:\n"
        ]

        # Sort by spend descending to show highest-spend segments first
        sorted_insights = sorted(
            insights,
            key=lambda x: float(x.get("spend", 0)),
            reverse=True,
        )

        for row in sorted_insights:
            segment_value = row.get(breakdown, "unknown")
            spend = float(row.get("spend", 0))
            impressions = int(row.get("impressions", 0))
            clicks = int(row.get("clicks", 0))
            conversions = float(row.get("conversions", 0))
            conv_value = float(row.get("conversion_value", 0))

            ctr = (clicks / impressions * 100) if impressions > 0 else 0
            cpc = (spend / clicks) if clicks > 0 else 0
            cpa = (spend / conversions) if conversions > 0 else 0
            roas = (conv_value / spend) if spend > 0 else 0

            lines.append(f"  {breakdown}: {segment_value}")
            spend_s = format_currency(spend)
            impr_s = format_number(impressions)
            click_s = format_number(clicks)
            conv_s = format_number(conversions)
            cpa_s = format_currency(cpa)
            cv_s = format_currency(conv_value)
            lines.append(f"    Spend: {spend_s}  |  Impr: {impr_s}  |  Clicks: {click_s}")
            lines.append(f"    CTR: {ctr:.2f}%  |  CPC: {format_currency(cpc)}  |  Conv: {conv_s}")
            lines.append(f"    CPA: {cpa_s}  |  ROAS: {roas:.2f}x  |  Conv Value: {cv_s}")
            lines.append("")

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error(
            "tool.get_meta_audience_insights.error",
            account_id=account_id,
            campaign_id=campaign_id,
            error=str(exc),
        )
        return error_response(f"Failed to get audience insights for campaign {campaign_id}: {exc}")


# ---------------------------------------------------------------------------
# Tool 5: Get account activity
# ---------------------------------------------------------------------------

GET_ACTIVITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "account_id": {
            "type": "string",
            "description": "Meta ad account ID (e.g. 'act_123456789').",
        },
        "days": {
            "type": "integer",
            "description": ("Number of days of activity to retrieve. Defaults to 7. Maximum 30."),
        },
    },
    "required": ["account_id"],
}


@tool(
    name="get_meta_account_activity",
    description=(
        "Gets recent changes and activity for a Meta ad account. Shows "
        "campaigns that started or stopped running, significant spend changes "
        "(>20%), and other notable shifts. Helps the agent understand what "
        "happened recently before making recommendations. Defaults to 7 days."
    ),
    input_schema=GET_ACTIVITY_SCHEMA,
)
async def get_meta_account_activity(args: dict[str, Any]) -> dict[str, Any]:
    """Get recent account activity and changes."""
    account_id = args.get("account_id", "").strip()
    days = args.get("days", 7)

    if not account_id:
        return error_response("account_id is required.")

    # Clamp days to a reasonable range
    try:
        days = int(days)
        days = max(1, min(days, 30))
    except (TypeError, ValueError):
        days = 7

    logger.info("tool.get_meta_account_activity", account_id=account_id, days=days)
    try:
        connector = _get_connector()
        activities = connector.get_account_activity(account_id, days=days)

        if not activities:
            return text_response(
                f"No significant changes found for account {account_id} in the "
                f"last {days} day(s). The account has been stable with no notable "
                "spend changes or campaign status changes."
            )

        lines = [
            f"Account activity for {account_id} (last {days} day(s)):\n"
            f"{len(activities)} notable change(s) found:\n"
        ]
        for activity in activities:
            activity_type = activity.get("type", "unknown")
            campaign_name = activity.get("campaign_name", "")
            campaign_id_val = activity.get("campaign_id", "")
            description = activity.get("description", "")

            type_labels = {
                "new_campaign": "NEW CAMPAIGN",
                "stopped_campaign": "STOPPED CAMPAIGN",
                "spend_change": "SPEND CHANGE",
            }
            label = type_labels.get(activity_type, activity_type.upper())

            lines.append(f"  [{label}] {campaign_name} (ID: {campaign_id_val})")
            if description:
                lines.append(f"    {description}")

            # Show spend details if available
            current_spend = activity.get("current_spend")
            previous_spend = activity.get("previous_spend")
            change_pct = activity.get("change_pct")
            if current_spend is not None:
                lines.append(f"    Current period spend: {format_currency(current_spend)}")
            if previous_spend is not None:
                lines.append(f"    Previous period spend: {format_currency(previous_spend)}")
            if change_pct is not None:
                lines.append(f"    Change: {change_pct:+.1f}%")
            lines.append("")

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error(
            "tool.get_meta_account_activity.error",
            account_id=account_id,
            error=str(exc),
        )
        return error_response(f"Failed to get account activity for {account_id}: {exc}")


# ---------------------------------------------------------------------------
# Tool 6: Update campaign (write — approval required)
# ---------------------------------------------------------------------------

UPDATE_CAMPAIGN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "approval_id": {
            "type": "integer",
            "description": (
                "ID of the approved action from the approval queue. Required — "
                "the agent CANNOT call this tool without a valid approval."
            ),
        },
        "action": {
            "type": "string",
            "description": ("Action to perform. One of: 'pause', 'enable', 'update_budget'."),
            "enum": ["pause", "enable", "update_budget"],
        },
        "account_id": {
            "type": "string",
            "description": "Meta ad account ID (e.g. 'act_123456789').",
        },
        "campaign_id": {
            "type": "string",
            "description": "Campaign ID to modify.",
        },
        "new_budget_cents": {
            "type": "integer",
            "description": (
                "New budget in cents (5000 = $50.00). Required for 'update_budget' action."
            ),
        },
        "budget_type": {
            "type": "string",
            "description": (
                "Budget type: 'daily' or 'lifetime'. Required for 'update_budget' action."
            ),
            "enum": ["daily", "lifetime"],
        },
    },
    "required": ["approval_id", "action", "account_id", "campaign_id"],
}


@tool(
    name="update_meta_campaign",
    description=(
        "Executes an APPROVED action on a Meta campaign. The agent must "
        "have a valid approval_id from the approval queue before calling this "
        "tool. Supports: pause, enable, update_budget. "
        "All changes are logged to the audit trail."
    ),
    input_schema=UPDATE_CAMPAIGN_SCHEMA,
)
async def update_meta_campaign(args: dict[str, Any]) -> dict[str, Any]:
    """Execute an approved write action on a Meta campaign."""
    from src.mcp_servers.write_safety import (
        log_execution_start,
        record_execution_outcome,
        verify_and_load_approval,
    )

    approval_id = args.get("approval_id")
    action = args.get("action", "").strip()
    account_id = args.get("account_id", "").strip()
    campaign_id = args.get("campaign_id", "").strip()

    if not approval_id:
        return error_response("approval_id is required for write operations.")
    if not action:
        return error_response("action is required (pause, enable, update_budget).")
    if not account_id:
        return error_response("account_id is required.")
    if not campaign_id:
        return error_response("campaign_id is required.")

    # Step 1: Verify approval
    item, err = await verify_and_load_approval(int(approval_id))
    if item is None:
        return error_response(f"Approval verification failed: {err}")

    user_id = item.user_id
    action_type = item.action_type.value if item.action_type else action

    logger.info(
        "tool.update_meta_campaign",
        approval_id=approval_id,
        action=action,
        account_id=account_id,
        campaign_id=campaign_id,
    )

    # Step 2: Log execution start
    action_params = {
        "platform": "meta",
        "action": action,
        "account_id": account_id,
        "campaign_id": campaign_id,
    }
    await log_execution_start(int(approval_id), user_id, action_type, action_params)

    # Step 3: Execute via connector
    try:
        connector = _get_connector()

        if action == "pause":
            result = connector.update_campaign_status(account_id, campaign_id, "PAUSED")
            summary = f"Campaign {campaign_id} paused successfully."
        elif action == "enable":
            result = connector.update_campaign_status(account_id, campaign_id, "ACTIVE")
            summary = f"Campaign {campaign_id} enabled successfully."
        elif action == "update_budget":
            new_budget_cents = args.get("new_budget_cents")
            budget_type = args.get("budget_type", "daily").strip()
            if not new_budget_cents:
                return error_response("new_budget_cents is required for update_budget action.")
            result = connector.update_campaign_budget(
                account_id, campaign_id, int(new_budget_cents), budget_type
            )
            budget_dollars = int(new_budget_cents) / 100
            summary = (
                f"Campaign {campaign_id} {budget_type} budget updated to ${budget_dollars:,.2f}."
            )
        else:
            return error_response(f"Unknown action '{action}'.")

        # Step 4: Record success
        await record_execution_outcome(int(approval_id), user_id, action_type, result=result)

        lines = [
            f"✓ {summary}",
            f"  Approval ID: {approval_id}",
            f"  Account: {account_id}",
        ]
        if isinstance(result, dict):
            for key, val in result.items():
                lines.append(f"  {key}: {val}")
        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error(
            "tool.update_meta_campaign.error",
            approval_id=approval_id,
            error=str(exc),
        )
        await record_execution_outcome(int(approval_id), user_id, action_type, error=str(exc))
        return error_response(f"Write operation failed: {exc}")


# ---------------------------------------------------------------------------
# Tool 7: Update ad entity (write — approval required)
# ---------------------------------------------------------------------------

UPDATE_AD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "approval_id": {
            "type": "integer",
            "description": ("ID of the approved action from the approval queue. Required."),
        },
        "action": {
            "type": "string",
            "description": "Action to perform: 'pause' or 'enable'.",
            "enum": ["pause", "enable"],
        },
        "account_id": {
            "type": "string",
            "description": "Meta ad account ID (e.g. 'act_123456789').",
        },
        "entity_type": {
            "type": "string",
            "description": ("Type of entity to update: 'campaign', 'adset', or 'ad'."),
            "enum": ["campaign", "adset", "ad"],
        },
        "entity_id": {
            "type": "string",
            "description": "ID of the campaign, ad set, or ad to update.",
        },
    },
    "required": ["approval_id", "action", "account_id", "entity_type", "entity_id"],
}


@tool(
    name="update_meta_ad",
    description=(
        "Pauses or enables a Meta campaign, ad set, or individual ad. "
        "Requires a valid approval_id. Useful for pausing fatigued creatives "
        "or underperforming ad sets. All changes are logged to the audit trail."
    ),
    input_schema=UPDATE_AD_SCHEMA,
)
async def update_meta_ad(args: dict[str, Any]) -> dict[str, Any]:
    """Pause or enable a Meta campaign, ad set, or ad (approval required)."""
    from src.mcp_servers.write_safety import (
        log_execution_start,
        record_execution_outcome,
        verify_and_load_approval,
    )

    approval_id = args.get("approval_id")
    action = args.get("action", "").strip()
    account_id = args.get("account_id", "").strip()
    entity_type = args.get("entity_type", "").strip()
    entity_id = args.get("entity_id", "").strip()

    if not approval_id:
        return error_response("approval_id is required for write operations.")
    if not action:
        return error_response("action is required (pause, enable).")
    if not account_id:
        return error_response("account_id is required.")
    if not entity_type:
        return error_response("entity_type is required (campaign, adset, ad).")
    if not entity_id:
        return error_response("entity_id is required.")

    # Step 1: Verify approval
    item, err = await verify_and_load_approval(int(approval_id))
    if item is None:
        return error_response(f"Approval verification failed: {err}")

    user_id = item.user_id
    action_type = item.action_type.value if item.action_type else f"{action}_{entity_type}"

    logger.info(
        "tool.update_meta_ad",
        approval_id=approval_id,
        action=action,
        account_id=account_id,
        entity_type=entity_type,
        entity_id=entity_id,
    )

    action_params = {
        "platform": "meta",
        "action": action,
        "account_id": account_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
    }
    await log_execution_start(int(approval_id), user_id, action_type, action_params)

    # Map action to Meta status
    status = "PAUSED" if action == "pause" else "ACTIVE"

    try:
        connector = _get_connector()

        if entity_type == "campaign":
            result = connector.update_campaign_status(account_id, entity_id, status)
        elif entity_type == "adset":
            result = connector.update_adset_status(account_id, entity_id, status)
        elif entity_type == "ad":
            result = connector.update_ad_status(account_id, entity_id, status)
        else:
            return error_response(f"Unknown entity_type '{entity_type}'.")

        await record_execution_outcome(int(approval_id), user_id, action_type, result=result)

        verb = "paused" if action == "pause" else "enabled"
        lines = [
            f"✓ {entity_type.title()} {entity_id} {verb} successfully.",
            f"  Approval ID: {approval_id}",
            f"  Account: {account_id}",
        ]
        if isinstance(result, dict):
            for key, val in result.items():
                lines.append(f"  {key}: {val}")
        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error(
            "tool.update_meta_ad.error",
            approval_id=approval_id,
            error=str(exc),
        )
        await record_execution_outcome(int(approval_id), user_id, action_type, error=str(exc))
        return error_response(f"Write operation failed: {exc}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_meta_tools() -> list[Any]:
    """Return the list of Meta MCP tool definitions.

    These can be passed to ``create_sdk_mcp_server(tools=...)`` or used
    individually for testing.

    Returns:
        List of 7 SdkMcpTool instances (5 read + 2 write).
    """
    return [
        list_meta_ad_accounts,
        get_meta_campaigns,
        get_meta_performance,
        get_meta_audience_insights,
        get_meta_account_activity,
        update_meta_campaign,
        update_meta_ad,
    ]
