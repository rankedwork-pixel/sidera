"""System introspection MCP tools for Sidera.

Provides 8 tools that the agent (head_of_it role) can use to diagnose and
troubleshoot its own infrastructure — database health, failed workflows,
audit trail, approval queue, conversation threads, cost tracking, webhook
events, and a general system status dashboard.

Tools:
    1. get_system_health         - Check DB, Redis, config, and uptime
    2. get_failed_runs           - Query the dead letter queue (DLQ)
    3. resolve_failed_run        - Mark a DLQ entry as resolved
    4. get_recent_audit_events   - Query recent audit log entries
    5. get_approval_queue_status - Pending / expired / stuck approvals
    6. get_conversation_status   - Active conversation threads
    7. get_cost_summary          - LLM cost tracking for the current period
    8. get_webhook_events        - Query recent webhook events

Usage:
    from src.mcp_servers.system import create_system_tools

    tools = create_system_tools()
    # These are registered globally via @tool decorator.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

import structlog

from src.agent.tool_registry import tool
from src.mcp_servers.helpers import error_response, format_currency, text_response

logger = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    """Timezone-naive UTC now for DB queries."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _safe_str(value: Any, max_len: int = 300) -> str:
    """Safely convert a value to a truncated string."""
    if value is None:
        return "N/A"
    s = str(value)
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


def _format_dt(dt: datetime | None) -> str:
    """Format a datetime for display."""
    if dt is None:
        return "N/A"
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


# ---------------------------------------------------------------------------
# Tool 1: System health dashboard
# ---------------------------------------------------------------------------

SYSTEM_HEALTH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
}


@tool(
    name="get_system_health",
    description=(
        "Get a comprehensive system health dashboard showing database connectivity, "
        "Redis status, configuration state, recent error counts, and key metrics. "
        "Use this as the FIRST diagnostic step when something seems wrong."
    ),
    input_schema=SYSTEM_HEALTH_SCHEMA,
)
async def get_system_health(args: dict[str, Any]) -> dict[str, Any]:
    """Check system health across all components."""
    logger.info("tool.get_system_health")
    lines: list[str] = ["# System Health Dashboard\n"]

    # --- Database ---
    db_status = "unknown"
    try:
        from sqlalchemy import text

        from src.db.session import get_db_session

        async with get_db_session() as session:
            result = await session.execute(text("SELECT 1"))
            result.scalar()
            db_status = "healthy"
    except Exception as exc:
        db_status = f"ERROR: {exc}"

    lines.append(f"## Database: {db_status}")

    # --- Redis ---
    redis_status = "unknown"
    try:
        from src.cache.redis_client import get_redis_client

        client = get_redis_client()
        if client:
            await client.ping()
            redis_status = "healthy"
        else:
            redis_status = "not_configured"
    except Exception as exc:
        redis_status = f"ERROR: {exc}"

    lines.append(f"## Redis: {redis_status}")

    # --- Configuration ---
    try:
        from src.config import settings

        lines.append("\n## Configuration")
        lines.append(f"- Environment: {settings.app_env}")
        lines.append(f"- Database configured: {bool(settings.database_url)}")
        lines.append(f"- Slack configured: {bool(settings.slack_bot_token)}")
        lines.append(f"- Anthropic configured: {bool(settings.anthropic_api_key)}")
        lines.append(f"- RBAC default role: {settings.rbac_default_role}")
        lines.append(f"- Auto-execute enabled: {settings.auto_execute_enabled}")
    except Exception as exc:
        lines.append(f"\n## Configuration: ERROR loading — {exc}")

    # --- Recent error counts (from DLQ) ---
    try:
        from src.db import service as db_service
        from src.db.session import get_db_session

        async with get_db_session() as session:
            unresolved = await db_service.get_unresolved_failed_runs(session)
            lines.append("\n## Dead Letter Queue (DLQ)")
            lines.append(f"- Unresolved failures: {len(unresolved)}")
            if unresolved:
                # Group by workflow
                by_workflow: dict[str, int] = {}
                for fr in unresolved:
                    wf = fr.workflow_name or "unknown"
                    by_workflow[wf] = by_workflow.get(wf, 0) + 1
                for wf, count in sorted(by_workflow.items(), key=lambda x: x[1], reverse=True):
                    lines.append(f"  - {wf}: {count}")
    except Exception as exc:
        lines.append(f"\n## DLQ: ERROR querying — {exc}")

    # --- Pending approvals ---
    try:
        from sqlalchemy import func, select

        from src.db.session import get_db_session
        from src.models.schema import ApprovalQueueItem, ApprovalStatus

        async with get_db_session() as session:
            stmt = (
                select(func.count())
                .select_from(ApprovalQueueItem)
                .where(ApprovalQueueItem.status == ApprovalStatus.PENDING)
            )
            result = await session.execute(stmt)
            pending_count = result.scalar() or 0
            lines.append("\n## Approval Queue")
            lines.append(f"- Pending approvals: {pending_count}")
    except Exception as exc:
        lines.append(f"\n## Approval Queue: ERROR — {exc}")

    # --- Active conversations ---
    try:
        from sqlalchemy import func, select

        from src.db.session import get_db_session
        from src.models.schema import ConversationThread

        async with get_db_session() as session:
            stmt = (
                select(func.count())
                .select_from(ConversationThread)
                .where(ConversationThread.is_active.is_(True))
            )
            result = await session.execute(stmt)
            active_threads = result.scalar() or 0
            lines.append("\n## Conversations")
            lines.append(f"- Active threads: {active_threads}")
    except Exception as exc:
        lines.append(f"\n## Conversations: ERROR — {exc}")

    # --- Today's LLM costs ---
    try:
        from src.db import service as db_service
        from src.db.session import get_db_session

        async with get_db_session() as session:
            today = date.today()
            cost = await db_service.get_daily_cost(session, "default", today)
            lines.append("\n## LLM Costs (today)")
            lines.append(f"- Total: {format_currency(cost)}")
    except Exception as exc:
        lines.append(f"\n## LLM Costs: ERROR — {exc}")

    return text_response("\n".join(lines))


# ---------------------------------------------------------------------------
# Tool 2: Query failed runs (DLQ)
# ---------------------------------------------------------------------------

GET_FAILED_RUNS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "include_resolved": {
            "type": "boolean",
            "description": (
                "If true, include resolved failures too. Default false (only unresolved)."
            ),
            "default": False,
        },
        "workflow_name": {
            "type": "string",
            "description": (
                "Optional filter by workflow name "
                "(e.g. 'daily_briefing_workflow', 'conversation_turn_workflow')."
            ),
        },
        "limit": {
            "type": "integer",
            "description": "Max results to return (default 20, max 100).",
            "default": 20,
        },
    },
    "required": [],
}


@tool(
    name="get_failed_runs",
    description=(
        "Query the dead letter queue (DLQ) for workflow failures. Shows recent "
        "failures with their error messages, workflow names, and event data. "
        "Use this to diagnose why scheduled briefings, skill runs, or "
        "conversation turns failed."
    ),
    input_schema=GET_FAILED_RUNS_SCHEMA,
)
async def get_failed_runs(args: dict[str, Any]) -> dict[str, Any]:
    """Query the DLQ for recent failures."""
    include_resolved = args.get("include_resolved", False)
    workflow_filter = args.get("workflow_name", "").strip()
    limit = min(args.get("limit", 20), 100)

    logger.info(
        "tool.get_failed_runs",
        include_resolved=include_resolved,
        workflow_name=workflow_filter,
    )

    try:
        from sqlalchemy import select

        from src.db.session import get_db_session
        from src.models.schema import FailedRun

        async with get_db_session() as session:
            stmt = select(FailedRun)
            if not include_resolved:
                stmt = stmt.where(FailedRun.resolved_at.is_(None))
            if workflow_filter:
                stmt = stmt.where(FailedRun.workflow_name == workflow_filter)
            stmt = stmt.order_by(FailedRun.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            runs = list(result.scalars().all())

        if not runs:
            qualifier = "" if include_resolved else " unresolved"
            return text_response(f"No{qualifier} failed runs found. The system is running cleanly.")

        lines = [f"Found {len(runs)} failed run(s):\n"]
        for r in runs:
            resolved = (
                f"Resolved by {r.resolved_by} at {_format_dt(r.resolved_at)}"
                if r.resolved_at
                else "UNRESOLVED"
            )
            event_summary = ""
            if r.event_data:
                try:
                    event_summary = json.dumps(r.event_data, default=str)[:200]
                except Exception:
                    event_summary = str(r.event_data)[:200]

            lines.append(
                f"---\n"
                f"**ID:** {r.id}\n"
                f"**Workflow:** {r.workflow_name}\n"
                f"**Event:** {r.event_name}\n"
                f"**Error Type:** {r.error_type or 'unknown'}\n"
                f"**Error:** {_safe_str(r.error_message, 500)}\n"
                f"**Created:** {_format_dt(r.created_at)}\n"
                f"**Status:** {resolved}\n"
                f"**Event Data:** {event_summary}"
            )

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error("tool.get_failed_runs.error", error=str(exc))
        return error_response(f"Failed to query DLQ: {exc}")


# ---------------------------------------------------------------------------
# Tool 3: Resolve a failed run
# ---------------------------------------------------------------------------

RESOLVE_FAILED_RUN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "failed_run_id": {
            "type": "integer",
            "description": "The ID of the failed run to mark as resolved.",
        },
        "resolution_note": {
            "type": "string",
            "description": (
                "Brief note explaining how the issue was resolved or why it's being dismissed."
            ),
        },
    },
    "required": ["failed_run_id"],
}


@tool(
    name="resolve_failed_run",
    description=(
        "Mark a failed run (DLQ entry) as resolved. Use this after you've "
        "investigated a failure and either fixed the root cause or determined "
        "it's a transient issue that self-resolved. This clears it from the "
        "unresolved failures list."
    ),
    input_schema=RESOLVE_FAILED_RUN_SCHEMA,
)
async def resolve_failed_run_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Mark a DLQ entry as resolved."""
    failed_run_id = args.get("failed_run_id")
    resolution_note = args.get("resolution_note", "").strip()

    if not failed_run_id:
        return error_response("failed_run_id is required.")

    logger.info(
        "tool.resolve_failed_run",
        failed_run_id=failed_run_id,
    )

    try:
        from src.db import service as db_service
        from src.db.session import get_db_session

        resolver = "head-of-it-agent"
        if resolution_note:
            resolver = f"head-of-it-agent: {resolution_note[:100]}"

        async with get_db_session() as session:
            result = await db_service.resolve_failed_run(
                session, failed_run_id, resolved_by=resolver
            )
            if result is None:
                return error_response(f"Failed run #{failed_run_id} not found.")
            await session.commit()

        return text_response(
            f"Failed run #{failed_run_id} marked as resolved.\nResolved by: {resolver}"
        )

    except Exception as exc:
        logger.error("tool.resolve_failed_run.error", error=str(exc))
        return error_response(f"Failed to resolve: {exc}")


# ---------------------------------------------------------------------------
# Tool 4: Recent audit events
# ---------------------------------------------------------------------------

GET_AUDIT_EVENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "event_type": {
            "type": "string",
            "description": (
                "Optional filter by event type. Common types: "
                "'analysis_run', 'recommendation', 'action_executed', "
                "'approval_decided', 'skill_run', 'error', 'conversation_turn'."
            ),
        },
        "role_id": {
            "type": "string",
            "description": "Optional filter by role ID.",
        },
        "limit": {
            "type": "integer",
            "description": "Max results (default 25, max 100).",
            "default": 25,
        },
    },
    "required": [],
}


@tool(
    name="get_recent_audit_events",
    description=(
        "Query the audit log for recent agent events — skill runs, analysis, "
        "approvals, errors, and conversation turns. Use this to understand "
        "what the system has been doing, diagnose unexpected behavior, or "
        "trace the timeline of a specific issue."
    ),
    input_schema=GET_AUDIT_EVENTS_SCHEMA,
)
async def get_recent_audit_events(args: dict[str, Any]) -> dict[str, Any]:
    """Query recent audit log entries."""
    event_type = args.get("event_type", "").strip()
    role_id = args.get("role_id", "").strip()
    limit = min(args.get("limit", 25), 100)

    logger.info(
        "tool.get_recent_audit_events",
        event_type=event_type,
        role_id=role_id,
    )

    try:
        from sqlalchemy import select

        from src.db.session import get_db_session
        from src.models.schema import AuditLog

        async with get_db_session() as session:
            stmt = select(AuditLog)
            if event_type:
                stmt = stmt.where(AuditLog.event_type == event_type)
            if role_id:
                stmt = stmt.where(AuditLog.role_id == role_id)
            stmt = stmt.order_by(AuditLog.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            entries = list(result.scalars().all())

        if not entries:
            return text_response("No audit events found matching the criteria.")

        lines = [f"Found {len(entries)} audit event(s):\n"]
        for e in entries:
            event_data_summary = ""
            if e.event_data:
                try:
                    event_data_summary = json.dumps(e.event_data, default=str)[:200]
                except Exception:
                    event_data_summary = str(e.event_data)[:200]

            lines.append(
                f"- [{_format_dt(e.created_at)}] "
                f"**{e.event_type}** "
                f"(role={e.role_id or '-'}, skill={e.skill_id or '-'}) "
                f"source={e.source or '-'} "
                f"model={e.agent_model or '-'}"
            )
            if event_data_summary:
                lines.append(f"  Data: {event_data_summary}")

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error("tool.get_recent_audit_events.error", error=str(exc))
        return error_response(f"Failed to query audit log: {exc}")


# ---------------------------------------------------------------------------
# Tool 5: Approval queue status
# ---------------------------------------------------------------------------

APPROVAL_QUEUE_STATUS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status_filter": {
            "type": "string",
            "enum": ["pending", "approved", "rejected", "expired", "all"],
            "description": ("Filter by approval status. Default 'pending'."),
            "default": "pending",
        },
        "limit": {
            "type": "integer",
            "description": "Max results (default 20, max 50).",
            "default": 20,
        },
    },
    "required": [],
}


@tool(
    name="get_approval_queue_status",
    description=(
        "Check the approval queue for pending, stuck, expired, or recently "
        "decided approvals. Use this to diagnose whether actions are stuck "
        "waiting for approval, or if approved actions failed to execute."
    ),
    input_schema=APPROVAL_QUEUE_STATUS_SCHEMA,
)
async def get_approval_queue_status(
    args: dict[str, Any],
) -> dict[str, Any]:
    """Query the approval queue."""
    status_filter = args.get("status_filter", "pending").strip()
    limit = min(args.get("limit", 20), 50)

    logger.info(
        "tool.get_approval_queue_status",
        status_filter=status_filter,
    )

    try:
        from sqlalchemy import select

        from src.db.session import get_db_session
        from src.models.schema import ApprovalQueueItem, ApprovalStatus

        async with get_db_session() as session:
            stmt = select(ApprovalQueueItem)

            if status_filter != "all":
                status_map = {
                    "pending": ApprovalStatus.PENDING,
                    "approved": ApprovalStatus.APPROVED,
                    "rejected": ApprovalStatus.REJECTED,
                    "expired": ApprovalStatus.EXPIRED,
                }
                db_status = status_map.get(status_filter)
                if db_status:
                    stmt = stmt.where(ApprovalQueueItem.status == db_status)

            stmt = stmt.order_by(ApprovalQueueItem.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            items = list(result.scalars().all())

        if not items:
            return text_response(f"No {status_filter} approvals found.")

        lines = [f"Found {len(items)} {status_filter} approval(s):\n"]
        for item in items:
            status_val = item.status.value if hasattr(item.status, "value") else str(item.status)
            action_val = (
                item.action_type.value
                if hasattr(item.action_type, "value")
                else str(item.action_type)
            )
            age = ""
            if item.created_at:
                delta = _utcnow() - item.created_at
                hours = delta.total_seconds() / 3600
                if hours > 24:
                    age = f" ({hours / 24:.1f} days old)"
                else:
                    age = f" ({hours:.1f}h old)"

            executed = ""
            if item.executed_at:
                executed = f" | Executed: {_format_dt(item.executed_at)}"

            lines.append(
                f"---\n"
                f"**ID:** {item.id} | **Status:** {status_val}{age}\n"
                f"**Action:** {action_val}\n"
                f"**Description:** {_safe_str(item.description, 200)}\n"
                f"**Created:** {_format_dt(item.created_at)}"
                f"{executed}"
            )

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error("tool.get_approval_queue_status.error", error=str(exc))
        return error_response(f"Failed to query approval queue: {exc}")


# ---------------------------------------------------------------------------
# Tool 6: Conversation thread status
# ---------------------------------------------------------------------------

CONVERSATION_STATUS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "active_only": {
            "type": "boolean",
            "description": "If true, only show active threads. Default true.",
            "default": True,
        },
        "limit": {
            "type": "integer",
            "description": "Max results (default 20, max 50).",
            "default": 20,
        },
    },
    "required": [],
}


@tool(
    name="get_conversation_status",
    description=(
        "Check the status of conversation threads — which roles are currently "
        "in active conversations, how many turns have been used, and costs. "
        "Use this to diagnose conversation-related issues or monitor usage."
    ),
    input_schema=CONVERSATION_STATUS_SCHEMA,
)
async def get_conversation_status(
    args: dict[str, Any],
) -> dict[str, Any]:
    """Query conversation thread status."""
    active_only = args.get("active_only", True)
    limit = min(args.get("limit", 20), 50)

    logger.info(
        "tool.get_conversation_status",
        active_only=active_only,
    )

    try:
        from sqlalchemy import select

        from src.db.session import get_db_session
        from src.models.schema import ConversationThread

        async with get_db_session() as session:
            stmt = select(ConversationThread)
            if active_only:
                stmt = stmt.where(ConversationThread.is_active.is_(True))
            stmt = stmt.order_by(ConversationThread.last_activity_at.desc()).limit(limit)
            result = await session.execute(stmt)
            threads = list(result.scalars().all())

        if not threads:
            qualifier = "active " if active_only else ""
            return text_response(f"No {qualifier}conversation threads found.")

        lines = [f"Found {len(threads)} thread(s):\n"]
        for t in threads:
            status = "ACTIVE" if t.is_active else "CLOSED"
            cost = format_currency(t.total_cost_usd)
            lines.append(
                f"- [{status}] Role: **{t.role_id}** | "
                f"User: {t.user_id} | "
                f"Turns: {t.turn_count} | "
                f"Cost: {cost} | "
                f"Last activity: {_format_dt(t.last_activity_at)}"
            )

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error("tool.get_conversation_status.error", error=str(exc))
        return error_response(f"Failed to query conversation threads: {exc}")


# ---------------------------------------------------------------------------
# Tool 7: Cost summary
# ---------------------------------------------------------------------------

COST_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "days": {
            "type": "integer",
            "description": ("Number of days to look back (default 7, max 90)."),
            "default": 7,
        },
    },
    "required": [],
}


@tool(
    name="get_cost_summary",
    description=(
        "Get a summary of LLM costs over the specified period, broken down "
        "by day and model. Use this to monitor spending, detect cost "
        "anomalies, or report on usage patterns."
    ),
    input_schema=COST_SUMMARY_SCHEMA,
)
async def get_cost_summary(args: dict[str, Any]) -> dict[str, Any]:
    """Get LLM cost summary."""
    days = min(args.get("days", 7), 90)

    logger.info("tool.get_cost_summary", days=days)

    try:
        from sqlalchemy import func, select

        from src.db.session import get_db_session
        from src.models.schema import CostTracking

        start_date = date.today() - timedelta(days=days)

        async with get_db_session() as session:
            # Daily totals
            stmt = (
                select(
                    CostTracking.run_date,
                    func.sum(CostTracking.cost_usd).label("total_cost"),
                    func.sum(CostTracking.input_tokens).label("total_input"),
                    func.sum(CostTracking.output_tokens).label("total_output"),
                    func.count().label("run_count"),
                )
                .where(CostTracking.run_date >= start_date)
                .group_by(CostTracking.run_date)
                .order_by(CostTracking.run_date.desc())
            )
            result = await session.execute(stmt)
            daily_rows = result.all()

            # Model breakdown
            stmt_model = (
                select(
                    CostTracking.model,
                    func.sum(CostTracking.cost_usd).label("total_cost"),
                    func.count().label("run_count"),
                )
                .where(CostTracking.run_date >= start_date)
                .group_by(CostTracking.model)
                .order_by(func.sum(CostTracking.cost_usd).desc())
            )
            result_model = await session.execute(stmt_model)
            model_rows = result_model.all()

        if not daily_rows:
            return text_response(f"No cost data found for the last {days} days.")

        lines = [f"# LLM Cost Summary (last {days} days)\n"]

        # Grand total
        grand_total = sum(float(r.total_cost or 0) for r in daily_rows)
        total_runs = sum(r.run_count for r in daily_rows)
        lines.append(f"**Total:** {format_currency(grand_total)}")
        lines.append(f"**Total runs:** {total_runs}")
        lines.append(f"**Average/day:** {format_currency(grand_total / max(len(daily_rows), 1))}")

        # By model
        if model_rows:
            lines.append("\n## By Model")
            for m in model_rows:
                lines.append(
                    f"- {m.model or 'unknown'}: "
                    f"{format_currency(m.total_cost)} "
                    f"({m.run_count} runs)"
                )

        # Daily breakdown
        lines.append("\n## Daily Breakdown")
        for r in daily_rows:
            lines.append(
                f"- {r.run_date}: {format_currency(r.total_cost)} "
                f"({r.run_count} runs, "
                f"{r.total_input or 0:,} in / "
                f"{r.total_output or 0:,} out tokens)"
            )

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error("tool.get_cost_summary.error", error=str(exc))
        return error_response(f"Failed to query costs: {exc}")


# ---------------------------------------------------------------------------
# 8. get_webhook_events
# ---------------------------------------------------------------------------


@tool(
    name="get_webhook_events",
    description=(
        "Get recent webhook events received by Sidera from external monitoring "
        "sources (Google Ads Scripts, Meta, BigQuery, custom). Shows source, "
        "event type, severity, status, summary, and timing."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": (
                    "Optional. Filter by source (e.g. 'google_ads', 'meta', "
                    "'bigquery', 'custom:datadog'). Omit for all sources."
                ),
            },
            "severity": {
                "type": "string",
                "description": "Optional. Filter by severity: low, medium, high, critical.",
                "enum": ["low", "medium", "high", "critical"],
            },
            "hours": {
                "type": "integer",
                "description": "Number of hours to look back (default 24).",
                "default": 24,
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default 20, max 50).",
                "default": 20,
            },
        },
    },
)
async def get_webhook_events(args: dict[str, Any]) -> dict[str, Any]:
    """Query recent webhook events from external monitoring sources."""
    try:
        from src.db import service as db_service
        from src.db.session import get_db_session

        source = args.get("source")
        severity = args.get("severity")
        hours = min(args.get("hours", 24), 168)  # Max 7 days
        limit = min(args.get("limit", 20), 50)

        async with get_db_session() as session:
            events = await db_service.get_recent_webhook_events(
                session,
                source=source,
                severity=severity,
                hours=hours,
                limit=limit,
            )

        if not events:
            return text_response(f"No webhook events found in the last {hours} hours.")

        lines = [f"**Webhook Events** (last {hours}h, showing {len(events)})\n"]
        for ev in events:
            ts = getattr(ev, "created_at", None)
            ts_str = ts.strftime("%Y-%m-%d %H:%M") if ts else "?"
            lines.append(
                f"- [{ts_str}] **{ev.severity.upper()}** "
                f"{ev.source}/{ev.event_type} — {ev.summary[:100]} "
                f"(status: {ev.status})"
            )

        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error("tool.get_webhook_events.error", error=str(exc))
        return error_response(f"Failed to query webhook events: {exc}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_system_tools() -> list[Any]:
    """Return the list of system introspection MCP tool definitions.

    Returns:
        List of 8 tool instances.
    """
    return [
        get_system_health,
        get_failed_runs,
        resolve_failed_run_tool,
        get_recent_audit_events,
        get_approval_queue_status,
        get_conversation_status,
        get_cost_summary,
        get_webhook_events,
    ]
