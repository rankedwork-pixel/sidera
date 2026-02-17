"""BigQuery MCP server tools for Sidera.

Provides 5 read-only tools that the Claude agent can call to pull BACKEND data
from the advertiser's BigQuery data warehouse. This is the SOURCE OF TRUTH for
business outcomes — revenue, orders, goals, budget pacing — as opposed to
platform-reported metrics from Google Ads or Meta.

Tools:
    1. discover_bigquery_tables     - List available tables in the data warehouse
    2. get_business_goals           - Get revenue/CPA/ROAS targets and budget plans
    3. get_backend_performance      - Get backend revenue, orders, AOV with channel breakdown
    4. get_budget_pacing            - Get budget pacing status per channel/campaign
    5. get_campaign_attribution     - Get backend-attributed revenue/orders per campaign

Usage:
    from src.mcp_servers.bigquery import create_bigquery_mcp_server

    server_config = create_bigquery_mcp_server()
    # Pass to ClaudeAgentOptions.mcp_servers
"""

from __future__ import annotations

import traceback
from decimal import Decimal
from typing import Any

import structlog

from src.agent.tool_registry import tool
from src.connectors.bigquery import BigQueryConnector
from src.mcp_servers.helpers import (
    error_response,
    format_currency,
    format_number,
    format_percentage,
    text_response,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_connector() -> BigQueryConnector:
    """Create a fresh BigQueryConnector instance."""
    return BigQueryConnector()


# ---------------------------------------------------------------------------
# Tool 1: Discover tables
# ---------------------------------------------------------------------------

DISCOVER_TABLES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
}


@tool(
    name="discover_bigquery_tables",
    description=(
        "Lists all available tables in the advertiser's BigQuery data warehouse. "
        "Returns table names, types (table/view), row counts, and descriptions. "
        "This is BACKEND data — the advertiser's own source of truth for business "
        "metrics, not platform-reported data. Use this to understand what data is "
        "available before querying specific tables."
    ),
    input_schema=DISCOVER_TABLES_SCHEMA,
)
async def discover_bigquery_tables(args: dict[str, Any]) -> dict[str, Any]:
    """List all available tables in the BigQuery data warehouse."""
    logger.info("tool.discover_bigquery_tables")
    try:
        connector = _get_connector()
        tables = connector.discover_tables()

        if not tables:
            return text_response(
                "No tables found in the BigQuery data warehouse. The dataset may "
                "be empty or the service account may lack access."
            )

        lines = [f"Found {len(tables)} table(s) in the BigQuery data warehouse:\n"]
        for tbl in tables:
            table_name = tbl.get("table_name", "unknown")
            table_type = tbl.get("table_type", "unknown")
            row_count = tbl.get("row_count")
            description = tbl.get("description", "")

            row_count_str = format_number(row_count) if row_count is not None else "N/A"
            lines.append(f"  - {table_name}")
            lines.append(f"      Type: {table_type}")
            lines.append(f"      Rows: {row_count_str}")
            if description:
                lines.append(f"      Description: {description}")

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error("tool.discover_bigquery_tables.error", error=str(exc))
        return error_response(f"Failed to discover BigQuery tables: {exc}")


# ---------------------------------------------------------------------------
# Tool 2: Get business goals
# ---------------------------------------------------------------------------

GET_BUSINESS_GOALS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "period": {
            "type": "string",
            "description": (
                "Optional. Time period to filter goals (e.g. '2024-Q1', '2024-01', "
                "'2024-W05'). If omitted, returns all available goal periods."
            ),
        },
        "channel": {
            "type": "string",
            "description": (
                "Optional. Channel to filter by (e.g. 'google_ads', 'meta', 'all'). "
                "If omitted, returns goals for all channels."
            ),
        },
    },
    "required": [],
}


@tool(
    name="get_business_goals",
    description=(
        "Gets the advertiser's business goals and targets from their BACKEND data "
        "warehouse. Returns revenue targets, CPA targets, ROAS targets, and planned "
        "budgets per period and channel. This is the SOURCE OF TRUTH for what the "
        "advertiser is trying to achieve — use these targets to evaluate whether "
        "platform performance (Google Ads, Meta) is meeting business objectives. "
        "Always compare platform-reported metrics against these backend goals."
    ),
    input_schema=GET_BUSINESS_GOALS_SCHEMA,
)
async def get_business_goals(args: dict[str, Any]) -> dict[str, Any]:
    """Get business goals and targets from the data warehouse."""
    period = args.get("period")
    channel = args.get("channel")
    if period:
        period = str(period).strip()
    if channel:
        channel = str(channel).strip()

    logger.info("tool.get_business_goals", period=period, channel=channel)
    try:
        connector = _get_connector()
        kwargs: dict[str, Any] = {}
        if period:
            kwargs["period"] = period
        if channel:
            kwargs["channel"] = channel

        goals = connector.get_goals(**kwargs)

        if not goals:
            filter_desc = ""
            if period:
                filter_desc += f" for period '{period}'"
            if channel:
                filter_desc += f" for channel '{channel}'"
            return text_response(
                f"No business goals found{filter_desc}. Goals may not be configured "
                "in the data warehouse yet."
            )

        lines = [f"Business Goals ({len(goals)} target(s) found):\n"]
        for goal in goals:
            goal_period = goal.get("period", "unknown")
            goal_channel = goal.get("channel", "all")
            revenue_target = goal.get("revenue_target")
            cpa_target = goal.get("cpa_target")
            roas_target = goal.get("roas_target")
            budget_planned = goal.get("budget_planned")

            lines.append(f"  Period: {goal_period}  |  Channel: {goal_channel}")
            if revenue_target is not None:
                lines.append(f"    Revenue target:  {format_currency(revenue_target)}")
            if cpa_target is not None:
                lines.append(f"    CPA target:      {format_currency(cpa_target)}")
            if roas_target is not None:
                lines.append(f"    ROAS target:     {float(roas_target):.2f}x")
            if budget_planned is not None:
                lines.append(f"    Budget planned:  {format_currency(budget_planned)}")
            lines.append("")

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error(
            "tool.get_business_goals.error",
            period=period,
            channel=channel,
            error=str(exc),
        )
        return error_response(f"Failed to get business goals: {exc}")


# ---------------------------------------------------------------------------
# Tool 3: Get backend performance
# ---------------------------------------------------------------------------

GET_BACKEND_PERFORMANCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "start_date": {
            "type": "string",
            "description": "Start date in YYYY-MM-DD format.",
        },
        "end_date": {
            "type": "string",
            "description": "End date in YYYY-MM-DD format.",
        },
        "include_channel_breakdown": {
            "type": "boolean",
            "description": (
                "Whether to include a per-channel breakdown (Google Ads, Meta, etc.) "
                "in addition to the totals. Defaults to true."
            ),
        },
    },
    "required": ["start_date", "end_date"],
}


@tool(
    name="get_backend_performance",
    description=(
        "Gets BACKEND business performance metrics from the advertiser's data "
        "warehouse. Returns total revenue, orders, AOV, and conversion rate — "
        "plus an optional per-channel breakdown showing revenue, orders, cost, "
        "ROAS, and CPA by channel. This is the SOURCE OF TRUTH for actual "
        "business outcomes. Platform-reported conversions and revenue may differ "
        "from these backend numbers due to attribution differences. Always trust "
        "these backend metrics over platform self-reported data."
    ),
    input_schema=GET_BACKEND_PERFORMANCE_SCHEMA,
)
async def get_backend_performance(args: dict[str, Any]) -> dict[str, Any]:
    """Get backend business metrics with optional channel breakdown."""
    start_date = args.get("start_date", "").strip()
    end_date = args.get("end_date", "").strip()
    include_channel = args.get("include_channel_breakdown", True)

    if not start_date:
        return error_response("start_date is required (YYYY-MM-DD).")
    if not end_date:
        return error_response("end_date is required (YYYY-MM-DD).")

    logger.info(
        "tool.get_backend_performance",
        start_date=start_date,
        end_date=end_date,
        include_channel=include_channel,
    )
    try:
        connector = _get_connector()

        # Fetch aggregate business metrics
        business_rows = connector.get_business_metrics(start_date, end_date)

        if not business_rows:
            return text_response(
                f"No backend business data found between {start_date} and {end_date}. "
                "The data warehouse may not have data for this period."
            )

        # Aggregate totals across all dates
        total_revenue = Decimal("0")
        total_orders = 0
        total_aov_sum = Decimal("0")
        total_conv_rate_sum = 0.0
        date_count = 0

        daily_lines: list[str] = []
        for row in business_rows:
            row_date = row.get("date", "")
            revenue = row.get("total_revenue", 0)
            orders = row.get("total_orders", 0)
            aov = row.get("aov", 0)
            conv_rate = row.get("conversion_rate", 0)

            total_revenue += Decimal(str(revenue))
            total_orders += int(orders)
            total_aov_sum += Decimal(str(aov))
            total_conv_rate_sum += float(conv_rate)
            date_count += 1

            daily_lines.append(
                f"  {row_date}: "
                f"Revenue={format_currency(revenue)}  "
                f"Orders={format_number(int(orders))}  "
                f"AOV={format_currency(aov)}  "
                f"Conv Rate={format_percentage(float(conv_rate) if conv_rate else None)}"
            )

        # Calculate summary averages
        avg_aov = float(total_aov_sum / date_count) if date_count > 0 else None
        avg_conv_rate = total_conv_rate_sum / date_count if date_count > 0 else None

        output_parts = [
            f"Backend Performance (SOURCE OF TRUTH)\n"
            f"Date range: {start_date} to {end_date}\n"
            f"{date_count} day(s) of data\n"
            f"\n"
            f"TOTALS:\n"
            f"  Total revenue:    {format_currency(total_revenue)}\n"
            f"  Total orders:     {format_number(total_orders)}\n"
            f"  Avg AOV:          {format_currency(avg_aov)}\n"
            f"  Avg conv rate:    {format_percentage(avg_conv_rate)}\n"
            f"\nDAILY BREAKDOWN:\n" + "\n".join(daily_lines)
        ]

        # Optional channel breakdown
        if include_channel:
            channel_rows = connector.get_channel_performance(start_date, end_date)

            if channel_rows:
                # Aggregate by channel
                channel_agg: dict[str, dict[str, Any]] = {}
                for row in channel_rows:
                    ch = row.get("channel", "unknown")
                    if ch not in channel_agg:
                        channel_agg[ch] = {
                            "revenue": Decimal("0"),
                            "orders": 0,
                            "cost": Decimal("0"),
                            "aov_sum": Decimal("0"),
                            "day_count": 0,
                        }
                    channel_agg[ch]["revenue"] += Decimal(str(row.get("revenue", 0)))
                    channel_agg[ch]["orders"] += int(row.get("orders", 0))
                    channel_agg[ch]["cost"] += Decimal(str(row.get("cost", 0)))
                    channel_agg[ch]["aov_sum"] += Decimal(str(row.get("aov", 0)))
                    channel_agg[ch]["day_count"] += 1

                channel_lines = ["\n\nCHANNEL BREAKDOWN (backend-attributed):"]
                for ch, agg in sorted(channel_agg.items()):
                    ch_revenue = agg["revenue"]
                    ch_orders = agg["orders"]
                    ch_cost = agg["cost"]
                    ch_roas = float(ch_revenue / ch_cost) if ch_cost > 0 else None
                    ch_cpa = float(ch_cost / ch_orders) if ch_orders > 0 else None
                    ch_avg_aov = (
                        float(agg["aov_sum"] / agg["day_count"]) if agg["day_count"] > 0 else None
                    )

                    roas_str = f"{ch_roas:.2f}x" if ch_roas is not None else "N/A"
                    channel_lines.append(f"\n  Channel: {ch}")
                    channel_lines.append(f"    Revenue:  {format_currency(ch_revenue)}")
                    channel_lines.append(f"    Orders:   {format_number(ch_orders)}")
                    channel_lines.append(f"    Cost:     {format_currency(ch_cost)}")
                    channel_lines.append(f"    ROAS:     {roas_str}")
                    channel_lines.append(f"    CPA:      {format_currency(ch_cpa)}")
                    channel_lines.append(f"    Avg AOV:  {format_currency(ch_avg_aov)}")

                output_parts.append("\n".join(channel_lines))
            else:
                output_parts.append(
                    "\n\nCHANNEL BREAKDOWN: No channel-level data available for this period."
                )

        return text_response("\n".join(output_parts))

    except Exception as exc:
        logger.error(
            "tool.get_backend_performance.error",
            start_date=start_date,
            end_date=end_date,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        return error_response(f"Failed to get backend performance data: {exc}")


# ---------------------------------------------------------------------------
# Tool 4: Get budget pacing
# ---------------------------------------------------------------------------

GET_BUDGET_PACING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "period": {
            "type": "string",
            "description": (
                "Optional. Time period to check pacing for (e.g. '2024-Q1', "
                "'2024-01'). If omitted, returns pacing for the current period."
            ),
        },
        "channel": {
            "type": "string",
            "description": (
                "Optional. Channel to filter by (e.g. 'google_ads', 'meta'). "
                "If omitted, returns pacing for all channels."
            ),
        },
    },
    "required": [],
}


@tool(
    name="get_budget_pacing",
    description=(
        "Gets budget pacing data from the advertiser's BACKEND data warehouse. "
        "Shows how each channel and campaign is tracking against its planned budget — "
        "including budget planned, budget spent, days elapsed/remaining, projected "
        "spend, and pacing status (ON TRACK / OVERSPEND / UNDERSPEND). This is the "
        "SOURCE OF TRUTH for spend pacing — platform-reported spend may not include "
        "all costs. Use this to identify campaigns that need budget adjustments."
    ),
    input_schema=GET_BUDGET_PACING_SCHEMA,
)
async def get_budget_pacing(args: dict[str, Any]) -> dict[str, Any]:
    """Get budget pacing status from the data warehouse."""
    period = args.get("period")
    channel = args.get("channel")
    if period:
        period = str(period).strip()
    if channel:
        channel = str(channel).strip()

    logger.info("tool.get_budget_pacing", period=period, channel=channel)
    try:
        connector = _get_connector()
        kwargs: dict[str, Any] = {}
        if period:
            kwargs["period"] = period
        if channel:
            kwargs["channel"] = channel

        pacing_rows = connector.get_budget_pacing(**kwargs)

        if not pacing_rows:
            filter_desc = ""
            if period:
                filter_desc += f" for period '{period}'"
            if channel:
                filter_desc += f" for channel '{channel}'"
            return text_response(
                f"No budget pacing data found{filter_desc}. Budget plans may not "
                "be configured in the data warehouse yet."
            )

        # Map pacing status to indicators
        status_indicators = {
            "on_track": "ON TRACK",
            "overspend": "OVERSPEND",
            "underspend": "UNDERSPEND",
        }

        lines = [f"Budget Pacing ({len(pacing_rows)} entry/entries):\n"]
        for row in pacing_rows:
            row_period = row.get("period", "unknown")
            row_channel = row.get("channel", "unknown")
            campaign_name = row.get("campaign_name", "")
            budget_planned = row.get("budget_planned")
            budget_spent = row.get("budget_spent")
            days_elapsed = row.get("days_elapsed")
            days_remaining = row.get("days_remaining")
            projected_spend = row.get("projected_spend")
            pacing_status = row.get("pacing_status", "unknown")

            # Format the status indicator
            status_raw = str(pacing_status).lower().replace(" ", "_")
            status_label = status_indicators.get(status_raw, str(pacing_status).upper())

            # Calculate spend percentage
            spend_pct = None
            if budget_planned and float(budget_planned) > 0 and budget_spent is not None:
                spend_pct = float(budget_spent) / float(budget_planned)

            label = f"{row_channel}"
            if campaign_name:
                label += f" / {campaign_name}"

            lines.append(f"  [{status_label}] {label}")
            lines.append(f"    Period: {row_period}")
            lines.append(f"    Budget planned:   {format_currency(budget_planned)}")
            lines.append(
                f"    Budget spent:     {format_currency(budget_spent)}"
                + (f"  ({format_percentage(spend_pct)} of plan)" if spend_pct is not None else "")
            )
            if days_elapsed is not None and days_remaining is not None:
                total_days = int(days_elapsed) + int(days_remaining)
                lines.append(
                    f"    Days elapsed:     {days_elapsed} of {total_days} "
                    f"({days_remaining} remaining)"
                )
            if projected_spend is not None:
                lines.append(f"    Projected spend:  {format_currency(projected_spend)}")
                # Show projected over/under
                if budget_planned and float(budget_planned) > 0:
                    diff = float(projected_spend) - float(budget_planned)
                    diff_pct = diff / float(budget_planned)
                    if abs(diff) > 0.01:
                        direction = "over" if diff > 0 else "under"
                        lines.append(
                            f"    Projected {direction} by: "
                            f"{format_currency(abs(diff))} "
                            f"({format_percentage(abs(diff_pct))})"
                        )
            lines.append("")

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error(
            "tool.get_budget_pacing.error",
            period=period,
            channel=channel,
            error=str(exc),
        )
        return error_response(f"Failed to get budget pacing data: {exc}")


# ---------------------------------------------------------------------------
# Tool 5: Get campaign attribution
# ---------------------------------------------------------------------------

GET_CAMPAIGN_ATTRIBUTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "start_date": {
            "type": "string",
            "description": "Start date in YYYY-MM-DD format.",
        },
        "end_date": {
            "type": "string",
            "description": "End date in YYYY-MM-DD format.",
        },
        "channel": {
            "type": "string",
            "description": (
                "Optional. Channel to filter by (e.g. 'google_ads', 'meta'). "
                "If omitted, returns attribution data for all channels."
            ),
        },
    },
    "required": ["start_date", "end_date"],
}


@tool(
    name="get_campaign_attribution",
    description=(
        "Gets BACKEND campaign-level attribution data from the advertiser's data "
        "warehouse. Shows revenue, orders, and cost attributed to each campaign "
        "based on the advertiser's own attribution model — NOT platform self-reported "
        "conversions. This is the SOURCE OF TRUTH for campaign-level ROI. Compare "
        "these numbers against platform-reported conversions to identify campaigns "
        "where platforms over- or under-report their performance."
    ),
    input_schema=GET_CAMPAIGN_ATTRIBUTION_SCHEMA,
)
async def get_campaign_attribution(args: dict[str, Any]) -> dict[str, Any]:
    """Get backend campaign attribution data."""
    start_date = args.get("start_date", "").strip()
    end_date = args.get("end_date", "").strip()
    channel = args.get("channel")
    if channel:
        channel = str(channel).strip()

    if not start_date:
        return error_response("start_date is required (YYYY-MM-DD).")
    if not end_date:
        return error_response("end_date is required (YYYY-MM-DD).")

    logger.info(
        "tool.get_campaign_attribution",
        start_date=start_date,
        end_date=end_date,
        channel=channel,
    )
    try:
        connector = _get_connector()
        kwargs: dict[str, Any] = {"start_date": start_date, "end_date": end_date}
        if channel:
            kwargs["channel"] = channel

        rows = connector.get_campaign_attribution(**kwargs)

        if not rows:
            filter_desc = f" for channel '{channel}'" if channel else ""
            return text_response(
                f"No campaign attribution data found between {start_date} and "
                f"{end_date}{filter_desc}. The data warehouse may not have "
                "attribution data for this period."
            )

        # Aggregate by campaign (across dates)
        campaign_agg: dict[str, dict[str, Any]] = {}
        for row in rows:
            campaign_name = row.get("campaign_name", "unknown")
            row_channel = row.get("channel", "unknown")
            platform_id = row.get("platform_campaign_id", "")
            key = f"{row_channel}|{campaign_name}"

            if key not in campaign_agg:
                campaign_agg[key] = {
                    "channel": row_channel,
                    "campaign_name": campaign_name,
                    "platform_campaign_id": platform_id,
                    "revenue": Decimal("0"),
                    "orders": 0,
                    "cost": Decimal("0"),
                }
            campaign_agg[key]["revenue"] += Decimal(str(row.get("revenue", 0)))
            campaign_agg[key]["orders"] += int(row.get("orders", 0))
            campaign_agg[key]["cost"] += Decimal(str(row.get("cost", 0)))

        # Sort by revenue descending
        sorted_campaigns = sorted(
            campaign_agg.values(),
            key=lambda x: float(x["revenue"]),
            reverse=True,
        )

        # Calculate grand totals
        grand_revenue = sum(c["revenue"] for c in sorted_campaigns)
        grand_orders = sum(c["orders"] for c in sorted_campaigns)
        grand_cost = sum(c["cost"] for c in sorted_campaigns)
        grand_roas = float(grand_revenue / grand_cost) if grand_cost > 0 else None
        grand_cpa = float(grand_cost / grand_orders) if grand_orders > 0 else None

        scope = f"channel '{channel}'" if channel else "all channels"
        roas_str = f"{grand_roas:.2f}x" if grand_roas is not None else "N/A"

        lines = [
            f"Campaign Attribution — BACKEND SOURCE OF TRUTH\n"
            f"Date range: {start_date} to {end_date}\n"
            f"Scope: {scope}\n"
            f"{len(sorted_campaigns)} campaign(s)\n"
            f"\n"
            f"TOTALS:\n"
            f"  Revenue:  {format_currency(grand_revenue)}\n"
            f"  Orders:   {format_number(grand_orders)}\n"
            f"  Cost:     {format_currency(grand_cost)}\n"
            f"  ROAS:     {roas_str}\n"
            f"  CPA:      {format_currency(grand_cpa)}\n"
            f"\nCAMPAIGN DETAIL:\n"
        ]

        for camp in sorted_campaigns:
            c_revenue = camp["revenue"]
            c_orders = camp["orders"]
            c_cost = camp["cost"]
            c_roas = float(c_revenue / c_cost) if c_cost > 0 else None
            c_cpa = float(c_cost / c_orders) if c_orders > 0 else None

            c_roas_str = f"{c_roas:.2f}x" if c_roas is not None else "N/A"
            platform_id_str = (
                f" (Platform ID: {camp['platform_campaign_id']})"
                if camp["platform_campaign_id"]
                else ""
            )

            lines.append(f"  [{camp['channel']}] {camp['campaign_name']}{platform_id_str}")
            lines.append(
                f"    Revenue: {format_currency(c_revenue)}  "
                f"Orders: {format_number(c_orders)}  "
                f"Cost: {format_currency(c_cost)}"
            )
            lines.append(f"    ROAS: {c_roas_str}  CPA: {format_currency(c_cpa)}")

            # Revenue share
            if grand_revenue > 0:
                rev_share = float(c_revenue / grand_revenue)
                lines.append(f"    Revenue share: {format_percentage(rev_share)}")
            lines.append("")

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error(
            "tool.get_campaign_attribution.error",
            start_date=start_date,
            end_date=end_date,
            channel=channel,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        return error_response(f"Failed to get campaign attribution data: {exc}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_bigquery_tools() -> list[Any]:
    """Return the list of BigQuery MCP tool definitions.

    These can be passed to ``create_sdk_mcp_server(tools=...)`` or used
    individually for testing.

    Returns:
        List of 5 SdkMcpTool instances (all read-only).
    """
    return [
        discover_bigquery_tables,
        get_business_goals,
        get_backend_performance,
        get_budget_pacing,
        get_campaign_attribution,
    ]
