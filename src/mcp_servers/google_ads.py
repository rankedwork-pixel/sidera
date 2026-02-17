"""Google Ads MCP server tools for Sidera.

Provides 7 tools that the Claude agent can call — 5 read-only data tools
plus 2 write tools that require prior human approval.

Tools (read):
    1. list_google_ads_accounts       - List connected accounts
    2. get_google_ads_campaigns       - Get campaigns for an account
    3. get_google_ads_performance     - Get performance metrics for a date range
    4. get_google_ads_changes         - Get recent change history
    5. get_google_ads_recommendations - Get Google's own recommendations

Tools (write — approval required):
    6. update_google_ads_campaign     - Create, pause/enable, update budget or bid target
    7. update_google_ads_keywords     - Add negative keywords

Usage:
    from src.mcp_servers.google_ads import create_google_ads_mcp_server

    server_config = create_google_ads_mcp_server()
    # Pass to ClaudeAgentOptions.mcp_servers
"""

from __future__ import annotations

import json
import traceback
from decimal import Decimal
from typing import Any

import structlog

from src.agent.tool_registry import tool
from src.connectors.google_ads import GoogleAdsConnector
from src.mcp_servers.helpers import (
    error_response,
    format_currency,
    format_number,
    format_percentage,
    text_response,
)
from src.models.normalized import normalize_google_ads_metrics

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_connector() -> GoogleAdsConnector:
    """Create a fresh GoogleAdsConnector instance."""
    return GoogleAdsConnector()


# ---------------------------------------------------------------------------
# Tool 1: List accounts
# ---------------------------------------------------------------------------

LIST_ACCOUNTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
}


@tool(
    name="list_google_ads_accounts",
    description=(
        "Lists all Google Ads accounts the user has connected. Returns each "
        "account's customer ID, descriptive name, and basic metadata. Use this "
        "to discover which accounts are available before pulling campaign or "
        "performance data."
    ),
    input_schema=LIST_ACCOUNTS_SCHEMA,
)
async def list_google_ads_accounts(args: dict[str, Any]) -> dict[str, Any]:
    """List all accessible Google Ads accounts with MCC hierarchy."""
    logger.info("tool.list_google_ads_accounts")
    try:
        connector = _get_connector()
        customer_ids = connector.get_accessible_accounts()

        if not customer_ids:
            return text_response(
                "No Google Ads accounts found. The user may need to connect their "
                "Google Ads account through the OAuth flow first."
            )

        lines = [f"Found {len(customer_ids)} accessible account(s):\n"]
        for cid in customer_ids:
            # Try to fetch account details; fall back to just the ID
            info = connector.get_account_info(cid)
            if info:
                name = info.get("descriptive_name") or info.get("name") or "Unnamed"
                currency = info.get("currency", "")
                timezone = info.get("timezone", "")
                lines.append(f"  - {name} (ID: {cid})")
                if currency:
                    lines.append(f"      Currency: {currency}")
                if timezone:
                    lines.append(f"      Timezone: {timezone}")
            else:
                # Likely an MCC — try listing child accounts
                lines.append(f"  - Manager Account / MCC (ID: {cid})")

            # Check for child accounts (MCC hierarchy)
            children = connector.get_child_accounts(cid)
            if children:
                # Separate managers (sub-MCCs) from client accounts
                client_children = [c for c in children if not c.get("manager")]
                manager_children = [c for c in children if c.get("manager")]
                if client_children:
                    lines.append(f"      Client accounts ({len(client_children)}):")
                    for child in client_children:
                        child_name = child.get("descriptive_name") or "Unnamed"
                        lines.append(
                            f"        - {child_name} (ID: {child['id']}) ← USE THIS for campaigns"
                        )
                if manager_children:
                    lines.append(f"      Sub-manager accounts ({len(manager_children)}):")
                    for child in manager_children:
                        child_name = child.get("descriptive_name") or "Unnamed"
                        lines.append(f"        - {child_name} (ID: {child['id']}) [MCC]")

        lines.append(
            "\nNote: Campaigns live in client accounts, not manager (MCC) accounts. "
            "Use the client account ID when querying campaigns."
        )
        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error("tool.list_google_ads_accounts.error", error=str(exc))
        return error_response(f"Failed to list Google Ads accounts: {exc}")


# ---------------------------------------------------------------------------
# Tool 2: Get campaigns
# ---------------------------------------------------------------------------

GET_CAMPAIGNS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "customer_id": {
            "type": "string",
            "description": (
                "Google Ads customer ID (10 digits, e.g. '1234567890'). "
                "Use list_google_ads_accounts to find available IDs."
            ),
        },
    },
    "required": ["customer_id"],
}


@tool(
    name="get_google_ads_campaigns",
    description=(
        "Gets all campaigns for a Google Ads account. Returns campaign names, "
        "types (search, display, shopping, pmax, etc.), status (enabled/paused/"
        "removed), daily budget, and bid strategy. Use this to understand the "
        "account structure before analyzing performance."
    ),
    input_schema=GET_CAMPAIGNS_SCHEMA,
)
async def get_google_ads_campaigns(args: dict[str, Any]) -> dict[str, Any]:
    """Get all campaigns for a Google Ads account."""
    customer_id = args.get("customer_id", "").strip()
    if not customer_id:
        return error_response("customer_id is required.")

    logger.info("tool.get_google_ads_campaigns", customer_id=customer_id)
    try:
        connector = _get_connector()
        campaigns = connector.get_campaigns(customer_id)

        if not campaigns:
            return text_response(
                f"No campaigns found for account {customer_id}. "
                "The account may be empty or the credentials may lack access."
            )

        lines = [f"Account {customer_id} has {len(campaigns)} campaign(s):\n"]
        for camp in campaigns:
            campaign_id = camp.get("id", camp.get("campaign_id", "unknown"))
            name = camp.get("name", camp.get("campaign_name", "Unnamed"))
            status = camp.get("status", "unknown").lower()

            # The connector already maps types and converts micros to dollars
            campaign_type = camp.get("type", "unknown")
            budget_str = format_currency(camp.get("daily_budget"))
            bid_strategy = camp.get("bid_strategy", "unknown")

            lines.append(f"  Campaign: {name}")
            lines.append(f"    ID: {campaign_id}")
            lines.append(f"    Type: {campaign_type}")
            lines.append(f"    Status: {status}")
            lines.append(f"    Daily budget: {budget_str}")
            lines.append(f"    Bid strategy: {bid_strategy}")
            lines.append("")

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error("tool.get_google_ads_campaigns.error", customer_id=customer_id, error=str(exc))
        return error_response(f"Failed to get campaigns for account {customer_id}: {exc}")


# ---------------------------------------------------------------------------
# Tool 3: Get performance metrics
# ---------------------------------------------------------------------------

GET_PERFORMANCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "customer_id": {
            "type": "string",
            "description": "Google Ads customer ID (10 digits).",
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
    "required": ["customer_id", "start_date", "end_date"],
}


@tool(
    name="get_google_ads_performance",
    description=(
        "Gets performance metrics for a Google Ads account over a date range. "
        "Returns daily spend, clicks, impressions, conversions, conversion value, "
        "CTR, CPC, CPA, and ROAS. Optionally filter by a specific campaign ID. "
        "All monetary values are in the account's currency. Dates must be in "
        "YYYY-MM-DD format."
    ),
    input_schema=GET_PERFORMANCE_SCHEMA,
)
async def get_google_ads_performance(args: dict[str, Any]) -> dict[str, Any]:
    """Get performance metrics for a date range, optionally filtered by campaign."""
    customer_id = args.get("customer_id", "").strip()
    start_date = args.get("start_date", "").strip()
    end_date = args.get("end_date", "").strip()
    campaign_id = args.get("campaign_id")
    if campaign_id:
        campaign_id = str(campaign_id).strip()

    # Validate required fields
    if not customer_id:
        return error_response("customer_id is required.")
    if not start_date:
        return error_response("start_date is required (YYYY-MM-DD).")
    if not end_date:
        return error_response("end_date is required (YYYY-MM-DD).")

    logger.info(
        "tool.get_google_ads_performance",
        customer_id=customer_id,
        start_date=start_date,
        end_date=end_date,
        campaign_id=campaign_id,
    )
    try:
        connector = _get_connector()

        if campaign_id:
            raw_rows = connector.get_campaign_metrics(
                customer_id, campaign_id, start_date, end_date
            )
            scope = f"campaign {campaign_id}"
        else:
            raw_rows = connector.get_account_metrics(customer_id, start_date, end_date)
            scope = "all campaigns"

        if not raw_rows:
            return text_response(
                f"No performance data found for account {customer_id} ({scope}) "
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
            # Attempt normalization; if the row schema doesn't match, fall
            # back to raw output so the agent still gets useful data.
            try:
                row_date_str = row.get("date", row.get("segments.date", ""))
                nm = normalize_google_ads_metrics(
                    campaign_id=0,  # placeholder — we only need the numbers
                    metric_date=row_date_str,  # type: ignore[arg-type]
                    raw=row,
                )
                total_impressions += nm.impressions
                total_clicks += nm.clicks
                total_cost += nm.cost
                total_conversions += nm.conversions
                total_conv_value += nm.conversion_value

                campaign_label = row.get("campaign.name", row.get("campaign_name", ""))
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
            f"Performance for account {customer_id} ({scope})\n"
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
            "tool.get_google_ads_performance.error",
            customer_id=customer_id,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        return error_response(f"Failed to get performance data for account {customer_id}: {exc}")


# ---------------------------------------------------------------------------
# Tool 4: Get change history
# ---------------------------------------------------------------------------

GET_CHANGES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "customer_id": {
            "type": "string",
            "description": "Google Ads customer ID (10 digits).",
        },
        "days": {
            "type": "integer",
            "description": (
                "Number of days of change history to retrieve. Defaults to 7. Maximum 30."
            ),
        },
    },
    "required": ["customer_id"],
}


@tool(
    name="get_google_ads_changes",
    description=(
        "Gets recent changes made to campaigns in a Google Ads account. "
        "Shows budget changes, status changes (paused/enabled), bid strategy "
        "changes, and other modifications. Helps the agent understand what "
        "happened recently before making recommendations. Defaults to 7 days."
    ),
    input_schema=GET_CHANGES_SCHEMA,
)
async def get_google_ads_changes(args: dict[str, Any]) -> dict[str, Any]:
    """Get recent change history for a Google Ads account."""
    customer_id = args.get("customer_id", "").strip()
    days = args.get("days", 7)

    if not customer_id:
        return error_response("customer_id is required.")

    # Clamp days to a reasonable range
    try:
        days = int(days)
        days = max(1, min(days, 30))
    except (TypeError, ValueError):
        days = 7

    logger.info("tool.get_google_ads_changes", customer_id=customer_id, days=days)
    try:
        connector = _get_connector()
        changes = connector.get_change_history(customer_id, days=days)

        if not changes:
            return text_response(
                f"No changes found for account {customer_id} in the last {days} day(s). "
                "The account has been stable with no modifications."
            )

        lines = [
            f"Change history for account {customer_id} (last {days} day(s)):\n"
            f"{len(changes)} change(s) found:\n"
        ]
        for change in changes:
            change_time = change.get("change_date_time", change.get("timestamp", "unknown"))
            change_type = change.get("change_resource_type", change.get("resource_type", "unknown"))
            resource_name = change.get("resource_name", change.get("campaign_name", ""))
            operation = change.get("operation", change.get("change_type", "unknown"))

            lines.append(f"  [{change_time}] {operation} on {change_type}")
            if resource_name:
                lines.append(f"    Resource: {resource_name}")

            # Show old → new values if available
            old_value = change.get("old_value", change.get("old_resource"))
            new_value = change.get("new_value", change.get("new_resource"))
            if old_value is not None:
                lines.append(f"    Old: {json.dumps(old_value, default=str)}")
            if new_value is not None:
                lines.append(f"    New: {json.dumps(new_value, default=str)}")

            # Feed info if present
            feed = change.get("feed", change.get("user_email"))
            if feed:
                lines.append(f"    Changed by: {feed}")
            lines.append("")

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error(
            "tool.get_google_ads_changes.error",
            customer_id=customer_id,
            error=str(exc),
        )
        return error_response(f"Failed to get change history for account {customer_id}: {exc}")


# ---------------------------------------------------------------------------
# Tool 5: Get Google's recommendations
# ---------------------------------------------------------------------------

GET_RECOMMENDATIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "customer_id": {
            "type": "string",
            "description": "Google Ads customer ID (10 digits).",
        },
    },
    "required": ["customer_id"],
}


@tool(
    name="get_google_ads_recommendations",
    description=(
        "Gets Google's own optimization recommendations for a Google Ads account. "
        "These are signals to consider, NOT actions to follow blindly. Google "
        "optimizes for platform revenue; Sidera optimizes for the advertiser's "
        "business outcomes. The agent should evaluate each recommendation "
        "against the advertiser's actual goals (target ROAS, CPA, budget caps) "
        "before deciding whether to endorse, modify, or reject it."
    ),
    input_schema=GET_RECOMMENDATIONS_SCHEMA,
)
async def get_google_ads_recommendations(args: dict[str, Any]) -> dict[str, Any]:
    """Get Google's optimization recommendations for an account."""
    customer_id = args.get("customer_id", "").strip()
    if not customer_id:
        return error_response("customer_id is required.")

    logger.info("tool.get_google_ads_recommendations", customer_id=customer_id)
    try:
        connector = _get_connector()
        recommendations = connector.get_recommendations(customer_id)

        if not recommendations:
            return text_response(
                f"No recommendations from Google for account {customer_id}. "
                "This could mean the account is well-optimized according to "
                "Google's criteria, or that the account has insufficient data."
            )

        lines = [
            f"Google's recommendations for account {customer_id}:\n"
            f"{len(recommendations)} recommendation(s) found.\n"
            f"NOTE: These are Google's suggestions. Evaluate each against the "
            f"advertiser's actual goals before endorsing.\n"
        ]
        for i, rec in enumerate(recommendations, 1):
            rec_type = rec.get("type", rec.get("recommendation_type", "unknown"))
            campaign_name = rec.get("campaign_name", rec.get("campaign", ""))
            impact = rec.get("impact", rec.get("estimated_impact", {}))
            description = rec.get("description", rec.get("text", ""))

            lines.append(f"  {i}. Type: {rec_type}")
            if campaign_name:
                lines.append(f"     Campaign: {campaign_name}")
            if description:
                lines.append(f"     Description: {description}")

            # Estimated impact
            if isinstance(impact, dict):
                impact_parts = []
                for k, v in impact.items():
                    impact_parts.append(f"{k}={v}")
                if impact_parts:
                    lines.append(f"     Estimated impact: {', '.join(impact_parts)}")
            elif impact:
                lines.append(f"     Estimated impact: {impact}")

            # Show any extra fields
            skip_keys = {
                "type",
                "recommendation_type",
                "campaign_name",
                "campaign",
                "impact",
                "estimated_impact",
                "description",
                "text",
            }
            extras = {k: v for k, v in rec.items() if k not in skip_keys and v is not None}
            if extras:
                lines.append(f"     Details: {json.dumps(extras, default=str)}")
            lines.append("")

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error(
            "tool.get_google_ads_recommendations.error",
            customer_id=customer_id,
            error=str(exc),
        )
        return error_response(f"Failed to get recommendations for account {customer_id}: {exc}")


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
            "description": (
                "Action to perform. One of: 'create', 'pause', 'enable', "
                "'update_budget', 'update_bid_target'."
            ),
            "enum": ["create", "pause", "enable", "update_budget", "update_bid_target"],
        },
        "customer_id": {
            "type": "string",
            "description": "Google Ads customer ID (10 digits).",
        },
        "campaign_id": {
            "type": "string",
            "description": ("Campaign ID to modify. Required for all actions except 'create'."),
        },
        "name": {
            "type": "string",
            "description": ("Campaign name. Required for 'create' action."),
        },
        "channel_type": {
            "type": "string",
            "description": ("Advertising channel type for 'create' action. Defaults to SEARCH."),
            "enum": [
                "SEARCH",
                "DISPLAY",
                "SHOPPING",
                "VIDEO",
                "PERFORMANCE_MAX",
                "DEMAND_GEN",
                "APP",
            ],
        },
        "bidding_strategy": {
            "type": "string",
            "description": ("Bidding strategy for 'create' action. Defaults to MAXIMIZE_CLICKS."),
            "enum": [
                "MAXIMIZE_CLICKS",
                "MAXIMIZE_CONVERSIONS",
                "MAXIMIZE_CONVERSION_VALUE",
                "MANUAL_CPC",
            ],
        },
        "new_budget_micros": {
            "type": "integer",
            "description": (
                "Daily budget in micros (1 dollar = 1,000,000 micros). "
                "Required for 'update_budget' and 'create' actions."
            ),
        },
        "target_cpa_micros": {
            "type": "integer",
            "description": ("New target CPA in micros. Used with 'update_bid_target' action."),
        },
        "target_roas": {
            "type": "number",
            "description": (
                "New target ROAS as a float (e.g. 4.0 = 400%). "
                "Used with 'update_bid_target' action."
            ),
        },
    },
    "required": ["approval_id", "action", "customer_id"],
}


@tool(
    name="update_google_ads_campaign",
    description=(
        "Executes an APPROVED action on a Google Ads campaign. The agent must "
        "have a valid approval_id from the approval queue before calling this "
        "tool. Supports: create, pause, enable, update_budget, update_bid_target. "
        "All changes are logged to the audit trail."
    ),
    input_schema=UPDATE_CAMPAIGN_SCHEMA,
)
async def update_google_ads_campaign(args: dict[str, Any]) -> dict[str, Any]:
    """Execute an approved write action on a Google Ads campaign."""
    from src.mcp_servers.write_safety import (
        log_execution_start,
        record_execution_outcome,
        verify_and_load_approval,
    )

    approval_id = args.get("approval_id")
    action = args.get("action", "").strip()
    customer_id = args.get("customer_id", "").strip()
    campaign_id = args.get("campaign_id", "").strip()

    if not approval_id:
        return error_response("approval_id is required for write operations.")
    if not action:
        return error_response(
            "action is required (create, pause, enable, update_budget, update_bid_target)."
        )
    if not customer_id:
        return error_response("customer_id is required.")
    if action != "create" and not campaign_id:
        return error_response("campaign_id is required for this action.")

    # Step 1: Verify approval
    item, err = await verify_and_load_approval(int(approval_id))
    if item is None:
        return error_response(f"Approval verification failed: {err}")

    user_id = item.user_id
    action_type = item.action_type.value if item.action_type else action

    logger.info(
        "tool.update_google_ads_campaign",
        approval_id=approval_id,
        action=action,
        customer_id=customer_id,
        campaign_id=campaign_id,
    )

    # Step 2: Log execution start
    action_params = {
        "platform": "google_ads",
        "action": action,
        "customer_id": customer_id,
        "campaign_id": campaign_id,
    }
    await log_execution_start(int(approval_id), user_id, action_type, action_params)

    # Step 3: Execute via connector
    try:
        connector = _get_connector()

        if action == "create":
            campaign_name = args.get("name", "").strip()
            channel_type = args.get("channel_type", "SEARCH").strip().upper()
            bidding_strategy = args.get("bidding_strategy", "MAXIMIZE_CLICKS").strip().upper()
            daily_budget_micros = args.get("new_budget_micros")
            if not campaign_name:
                return error_response("name is required for create action.")
            if not daily_budget_micros:
                return error_response("new_budget_micros is required for create action.")
            result = connector.create_campaign(
                customer_id,
                campaign_name,
                channel_type=channel_type,
                daily_budget_micros=int(daily_budget_micros),
                bidding_strategy=bidding_strategy,
            )
            budget_dollars = int(daily_budget_micros) / 1_000_000
            new_campaign_id = result.get("campaign_id", "unknown")
            summary = (
                f"Campaign '{campaign_name}' created (ID: {new_campaign_id}, "
                f"type: {channel_type}, budget: ${budget_dollars:,.2f}/day)."
            )
        elif action == "pause":
            result = connector.update_campaign_status(customer_id, campaign_id, "PAUSED")
            summary = f"Campaign {campaign_id} paused successfully."
        elif action == "enable":
            result = connector.update_campaign_status(customer_id, campaign_id, "ENABLED")
            summary = f"Campaign {campaign_id} enabled successfully."
        elif action == "update_budget":
            new_budget_micros = args.get("new_budget_micros")
            if not new_budget_micros:
                return error_response("new_budget_micros is required for update_budget action.")
            result = connector.update_campaign_budget(
                customer_id, campaign_id, int(new_budget_micros)
            )
            budget_dollars = int(new_budget_micros) / 1_000_000
            summary = f"Campaign {campaign_id} budget updated to ${budget_dollars:,.2f}/day."
        elif action == "update_bid_target":
            target_cpa_micros = args.get("target_cpa_micros")
            target_roas = args.get("target_roas")
            if not target_cpa_micros and not target_roas:
                return error_response(
                    "At least one of target_cpa_micros or target_roas is required."
                )
            result = connector.update_bid_strategy_target(
                customer_id,
                campaign_id,
                target_cpa_micros=int(target_cpa_micros) if target_cpa_micros else None,
                target_roas=float(target_roas) if target_roas else None,
            )
            summary = f"Campaign {campaign_id} bid target updated."
        else:
            return error_response(f"Unknown action '{action}'.")

        # Step 4: Record success
        await record_execution_outcome(int(approval_id), user_id, action_type, result=result)

        # Format response
        lines = [
            f"✓ {summary}",
            f"  Approval ID: {approval_id}",
            f"  Customer: {customer_id}",
        ]
        if isinstance(result, dict):
            for key, val in result.items():
                lines.append(f"  {key}: {val}")
        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error(
            "tool.update_google_ads_campaign.error",
            approval_id=approval_id,
            error=str(exc),
        )
        await record_execution_outcome(int(approval_id), user_id, action_type, error=str(exc))
        return error_response(f"Write operation failed: {exc}")


# ---------------------------------------------------------------------------
# Tool 7: Add negative keywords (write — approval required)
# ---------------------------------------------------------------------------

UPDATE_KEYWORDS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "approval_id": {
            "type": "integer",
            "description": ("ID of the approved action from the approval queue. Required."),
        },
        "customer_id": {
            "type": "string",
            "description": "Google Ads customer ID (10 digits).",
        },
        "campaign_id": {
            "type": "string",
            "description": "Campaign ID to add negative keywords to.",
        },
        "keywords": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "List of negative keyword strings to add. Duplicates are silently skipped."
            ),
        },
    },
    "required": ["approval_id", "customer_id", "campaign_id", "keywords"],
}


@tool(
    name="update_google_ads_keywords",
    description=(
        "Adds negative keywords to a Google Ads campaign. Requires a valid "
        "approval_id. Duplicate keywords are silently skipped. All changes "
        "are logged to the audit trail."
    ),
    input_schema=UPDATE_KEYWORDS_SCHEMA,
)
async def update_google_ads_keywords(args: dict[str, Any]) -> dict[str, Any]:
    """Add negative keywords to a Google Ads campaign (approval required)."""
    from src.mcp_servers.write_safety import (
        log_execution_start,
        record_execution_outcome,
        verify_and_load_approval,
    )

    approval_id = args.get("approval_id")
    customer_id = args.get("customer_id", "").strip()
    campaign_id = args.get("campaign_id", "").strip()
    keywords = args.get("keywords", [])

    if not approval_id:
        return error_response("approval_id is required for write operations.")
    if not customer_id:
        return error_response("customer_id is required.")
    if not campaign_id:
        return error_response("campaign_id is required.")
    if not keywords:
        return error_response("keywords list must not be empty.")

    # Step 1: Verify approval
    item, err = await verify_and_load_approval(int(approval_id))
    if item is None:
        return error_response(f"Approval verification failed: {err}")

    user_id = item.user_id
    action_type = item.action_type.value if item.action_type else "add_negative_keywords"

    logger.info(
        "tool.update_google_ads_keywords",
        approval_id=approval_id,
        customer_id=customer_id,
        campaign_id=campaign_id,
        keyword_count=len(keywords),
    )

    action_params = {
        "platform": "google_ads",
        "action": "add_negative_keywords",
        "customer_id": customer_id,
        "campaign_id": campaign_id,
        "keywords": keywords,
    }
    await log_execution_start(int(approval_id), user_id, action_type, action_params)

    try:
        connector = _get_connector()
        result = connector.add_negative_keywords(customer_id, campaign_id, keywords)

        await record_execution_outcome(int(approval_id), user_id, action_type, result=result)

        added = result.get("keywords_added", 0)
        skipped = result.get("duplicates_skipped", 0)
        lines = [
            f"✓ Added {added} negative keyword(s) to campaign {campaign_id}.",
            f"  Duplicates skipped: {skipped}",
            f"  Approval ID: {approval_id}",
            f"  Customer: {customer_id}",
        ]
        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error(
            "tool.update_google_ads_keywords.error",
            approval_id=approval_id,
            error=str(exc),
        )
        await record_execution_outcome(int(approval_id), user_id, action_type, error=str(exc))
        return error_response(f"Failed to add negative keywords: {exc}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_google_ads_tools() -> list[Any]:
    """Return the list of Google Ads MCP tool definitions.

    These can be passed to ``create_sdk_mcp_server(tools=...)`` or used
    individually for testing.

    Returns:
        List of 7 SdkMcpTool instances (5 read + 2 write).
    """
    return [
        list_google_ads_accounts,
        get_google_ads_campaigns,
        get_google_ads_performance,
        get_google_ads_changes,
        get_google_ads_recommendations,
        update_google_ads_campaign,
        update_google_ads_keywords,
    ]
