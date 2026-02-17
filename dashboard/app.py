"""Sidera AI Agent Framework — Streamlit Dashboard.

Run with:
    streamlit run dashboard/app.py

Works in two modes:
    - Live mode: reads from PostgreSQL via the async DB service
    - Demo mode: uses sample data when no database is configured
"""

import asyncio
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Path setup — ensure project root is importable
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dashboard.sample_data import (  # noqa: E402
    SAMPLE_ACCOUNTS,
    SAMPLE_ANALYSES,
    SAMPLE_APPROVALS,
    SAMPLE_AUDIT_LOG,
    get_sample_cost_by_model,
    get_sample_daily_totals,
    get_sample_today_cost,
)

# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Sidera",
    page_icon="\u26a1",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Async helper — run async DB calls from sync Streamlit code
# ---------------------------------------------------------------------------


def run_async(coro):
    """Run an async coroutine from sync Streamlit code."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def get_db_data(fetch_func, *args, default=None):
    """Try to fetch data from DB, return *default* if unavailable."""
    try:

        async def _fetch():
            from src.db.session import get_db_session

            async with get_db_session() as session:
                return await fetch_func(session, *args)

        return run_async(_fetch())
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Connection status helpers
# ---------------------------------------------------------------------------


def _db_connected() -> bool:
    """Return True if the database connection works."""
    try:
        from src.config import settings

        if not settings.database_url:
            return False
        # Attempt a lightweight query
        from sqlalchemy import text

        async def _ping():
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await session.execute(text("SELECT 1"))
            return True

        return run_async(_ping())
    except Exception:
        return False


@st.cache_data(ttl=30)
def check_connections() -> dict[str, bool]:
    """Check which external services are configured."""
    try:
        from src.config import settings
    except Exception:
        return {
            "database": False,
            "google_ads": False,
            "meta": False,
            "slack": False,
        }

    return {
        "database": _db_connected(),
        "google_ads": bool(settings.google_ads_developer_token and settings.google_ads_client_id),
        "meta": bool(settings.meta_app_id and settings.meta_app_secret),
        "slack": bool(settings.slack_bot_token and settings.slack_signing_secret),
    }


def _status_dot(connected: bool) -> str:
    """Return a coloured circle indicating connection status."""
    return "\U0001f7e2" if connected else "\U0001f534"


# ---------------------------------------------------------------------------
# Demo-mode banner
# ---------------------------------------------------------------------------

_USE_DB: bool = False


def _show_demo_banner():
    """Display an info banner when running without a database."""
    st.info(
        "\U0001f4cb **Demo mode** — connect a database for live data. Showing sample data below.",
        icon="\u2139\ufe0f",
    )


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------
PAGES = [
    "Overview",
    "Daily Briefings",
    "Approval Queue",
    "Accounts",
    "Cost Monitor",
    "Audit Log",
]

st.sidebar.title("\u26a1 Sidera")
st.sidebar.caption("AI Agent Framework")
page = st.sidebar.radio("Navigate", PAGES, label_visibility="collapsed")

# Determine whether we have a live DB
_USE_DB = check_connections()["database"]

# ---------------------------------------------------------------------------
# 1. Overview / Home Page
# ---------------------------------------------------------------------------


def page_overview():
    st.title("Sidera — AI Agent Framework")

    if not _USE_DB:
        _show_demo_banner()

    # --- Connection status ---
    st.subheader("Connection Status")
    conns = check_connections()
    cols = st.columns(4)
    cols[0].markdown(f"{_status_dot(conns['database'])} **Database**")
    cols[1].markdown(f"{_status_dot(conns['google_ads'])} **Google Ads**")
    cols[2].markdown(f"{_status_dot(conns['meta'])} **Meta**")
    cols[3].markdown(f"{_status_dot(conns['slack'])} **Slack**")

    st.divider()

    # --- Quick stats ---
    st.subheader("Quick Stats")

    if _USE_DB:
        from src.db import service as db

        accounts = get_db_data(db.get_accounts_for_user, "demo_user", default=[])
        total_accounts = len(accounts)

        today = date.today()
        analyses_today_list = get_db_data(
            db.get_analyses_for_period, "demo_user", today, today, default=[]
        )
        analyses_today = len(analyses_today_list)

        pending = get_db_data(db.get_pending_approvals, "demo_user", default=[])
        pending_count = len(pending)

        cost_today = get_db_data(db.get_daily_cost, "demo_user", today, default=Decimal("0"))
        cost_today_float = float(cost_today)
    else:
        total_accounts = len(SAMPLE_ACCOUNTS)
        analyses_today = sum(1 for a in SAMPLE_ANALYSES if a["run_date"] == date.today())
        pending_count = sum(1 for a in SAMPLE_APPROVALS if a["status"] == "pending")
        cost_today_float = get_sample_today_cost()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Accounts", total_accounts)
    m2.metric("Analyses Today", analyses_today)
    m3.metric("Pending Approvals", pending_count)
    m4.metric("LLM Cost Today", f"${cost_today_float:.2f}")


# ---------------------------------------------------------------------------
# 2. Daily Briefings Page
# ---------------------------------------------------------------------------


def page_daily_briefings():
    st.title("Daily Briefings")

    if not _USE_DB:
        _show_demo_banner()

    if _USE_DB:
        from src.db import service as db

        end = date.today()
        start = end - timedelta(days=14)
        analyses = get_db_data(db.get_analyses_for_period, "demo_user", start, end, default=[])
        # Convert ORM objects to dicts for uniform handling
        rows = []
        for a in analyses:
            rows.append(
                {
                    "id": a.id,
                    "run_date": a.run_date,
                    "status": a.status,
                    "llm_cost_usd": float(a.llm_cost_usd) if a.llm_cost_usd else 0,
                    "total_ad_spend": float(a.total_ad_spend) if a.total_ad_spend else None,
                    "briefing_content": a.briefing_content,
                    "recommendations": a.recommendations,
                    "error_message": getattr(a, "error_message", None),
                    "duration_seconds": a.duration_seconds,
                }
            )
    else:
        rows = SAMPLE_ANALYSES

    if not rows:
        st.info("No analysis results found.")
        return

    # Summary table
    summary_data = []
    for r in rows:
        summary_data.append(
            {
                "Date": r["run_date"],
                "Status": r.get("status", "unknown"),
                "LLM Cost": f"${float(r.get('llm_cost_usd', 0)):.2f}",
                "Ad Spend": (
                    f"${float(r['total_ad_spend']):,.2f}" if r.get("total_ad_spend") else "N/A"
                ),
            }
        )
    st.dataframe(
        pd.DataFrame(summary_data),
        use_container_width=True,
        hide_index=True,
    )

    st.divider()

    # Expandable briefings
    for r in rows:
        run_date = r["run_date"]
        status = r.get("status", "unknown")
        status_icon = (
            "\u2705" if status == "completed" else "\u274c" if status == "failed" else "\u23f3"
        )
        label = f"{status_icon} {run_date} — {status}"

        with st.expander(label, expanded=(r == rows[0])):
            if r.get("briefing_content"):
                st.markdown(r["briefing_content"])
            elif r.get("error_message"):
                st.error(f"**Error:** {r['error_message']}")
            else:
                st.write("No briefing content available.")

            # Recommendations sub-section
            recs = r.get("recommendations")
            if recs:
                st.markdown("---")
                st.markdown("**Recommendations**")
                for rec in recs:
                    rec_status = rec.get("status", "pending")
                    if rec_status == "approved":
                        badge = ":green[APPROVED]"
                    elif rec_status == "rejected":
                        badge = ":red[REJECTED]"
                    else:
                        badge = ":orange[PENDING]"
                    risk = rec.get("risk", "")
                    risk_label = f" | Risk: {risk}" if risk else ""
                    st.markdown(f"- {badge} {rec.get('action', 'N/A')}{risk_label}")


# ---------------------------------------------------------------------------
# 3. Approval Queue Page
# ---------------------------------------------------------------------------


def page_approval_queue():
    st.title("Approval Queue")

    if not _USE_DB:
        _show_demo_banner()

    if _USE_DB:
        from src.db import service as db

        pending = get_db_data(db.get_pending_approvals, "demo_user", default=[])
        all_items = []
        for item in pending:
            all_items.append(
                {
                    "id": item.id,
                    "action_type": (
                        item.action_type.value
                        if hasattr(item.action_type, "value")
                        else str(item.action_type)
                    ),
                    "description": item.description,
                    "risk_assessment": item.risk_assessment or "",
                    "status": "pending",
                    "created_at": item.created_at,
                    "decided_by": None,
                    "decided_at": None,
                    "rejection_reason": None,
                }
            )
    else:
        all_items = SAMPLE_APPROVALS

    # --- Pending approvals ---
    st.subheader("Pending Approvals")
    pending_items = [a for a in all_items if a.get("status") == "pending"]

    if pending_items:
        pending_df = pd.DataFrame(
            [
                {
                    "ID": item["id"],
                    "Action Type": _format_action_type(item["action_type"]),
                    "Description": item["description"],
                    "Risk": _extract_risk_level(item.get("risk_assessment", "")),
                    "Created": _format_datetime(item.get("created_at")),
                }
                for item in pending_items
            ]
        )
        st.dataframe(pending_df, use_container_width=True, hide_index=True)

        # Expandable details
        for item in pending_items:
            with st.expander(f"Details: {item['description'][:80]}..."):
                st.markdown(f"**Reasoning:** {item.get('reasoning', 'N/A')}")
                st.markdown(f"**Risk Assessment:** {item.get('risk_assessment', 'N/A')}")
                st.markdown(f"**Projected Impact:** {item.get('projected_impact', 'N/A')}")
    else:
        st.success("No pending approvals.")

    st.divider()

    # --- History ---
    st.subheader("Approval History")
    history = [a for a in all_items if a.get("status") in ("approved", "rejected")]

    if history:
        history_df = pd.DataFrame(
            [
                {
                    "ID": item["id"],
                    "Action Type": _format_action_type(item["action_type"]),
                    "Description": item["description"],
                    "Status": item["status"].upper(),
                    "Decided By": item.get("decided_by", "N/A"),
                    "Decided At": _format_datetime(item.get("decided_at")),
                }
                for item in history
            ]
        )
        st.dataframe(history_df, use_container_width=True, hide_index=True)
    else:
        st.info("No approval history yet.")

    # --- Expired count ---
    expired = [a for a in all_items if a.get("status") == "expired"]
    if expired:
        st.warning(f"Expired approvals: {len(expired)}")


def _format_action_type(action_type: str) -> str:
    """Make action type human-readable."""
    return action_type.replace("_", " ").title()


def _extract_risk_level(risk_text: str) -> str:
    """Pull the risk level keyword from a risk assessment string."""
    lower = risk_text.lower()
    for level in ("very low", "low-medium", "low", "medium", "high"):
        if level in lower:
            return level.title()
    return "N/A"


def _format_datetime(dt) -> str:
    """Format a datetime for display."""
    if dt is None:
        return "N/A"
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    return str(dt)


# ---------------------------------------------------------------------------
# 4. Accounts Page
# ---------------------------------------------------------------------------

_PLATFORM_COLORS = {
    "google_ads": "#4285F4",
    "meta": "#7B61FF",
    "bing": "#00809D",
}


def page_accounts():
    st.title("Connected Accounts")

    if not _USE_DB:
        _show_demo_banner()

    if _USE_DB:
        from src.db import service as db

        accounts_raw = get_db_data(db.get_accounts_for_user, "demo_user", default=[])
        accounts = []
        for a in accounts_raw:
            accounts.append(
                {
                    "id": a.id,
                    "platform": a.platform.value
                    if hasattr(a.platform, "value")
                    else str(a.platform),
                    "platform_account_id": a.platform_account_id,
                    "account_name": a.account_name or "Unnamed",
                    "is_active": a.is_active,
                    "target_roas": a.target_roas,
                    "target_cpa": float(a.target_cpa) if a.target_cpa else None,
                    "monthly_budget_cap": float(a.monthly_budget_cap)
                    if a.monthly_budget_cap
                    else None,
                    "created_at": a.created_at,
                }
            )
    else:
        accounts = SAMPLE_ACCOUNTS

    if not accounts:
        st.info("No accounts connected yet.")
        return

    # Build display table
    rows = []
    for acct in accounts:
        platform = acct["platform"]
        platform_display = platform.replace("_", " ").title()

        goals = []
        if acct.get("target_roas"):
            goals.append(f"ROAS: {acct['target_roas']}x")
        if acct.get("target_cpa"):
            goals.append(f"CPA: ${float(acct['target_cpa']):.2f}")
        if acct.get("monthly_budget_cap"):
            goals.append(f"Budget: ${float(acct['monthly_budget_cap']):,.0f}/mo")
        goals_str = " | ".join(goals) if goals else "Not set"

        rows.append(
            {
                "Platform": platform_display,
                "Account Name": acct.get("account_name", "N/A"),
                "Account ID": acct.get("platform_account_id", "N/A"),
                "Status": "Active" if acct.get("is_active", True) else "Inactive",
                "Goals": goals_str,
            }
        )

    df = pd.DataFrame(rows)

    # Color-code platform column
    def _highlight_platform(row):
        platform_lower = row["Platform"].lower().replace(" ", "_")
        color = _PLATFORM_COLORS.get(platform_lower, "")
        if color:
            return [
                f"color: {color}; font-weight: bold" if col == "Platform" else ""
                for col in row.index
            ]
        return [""] * len(row)

    styled = df.style.apply(_highlight_platform, axis=1)
    st.dataframe(styled, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# 5. Cost Monitor Page
# ---------------------------------------------------------------------------


def page_cost_monitor():
    st.title("LLM Cost Monitor")

    if not _USE_DB:
        _show_demo_banner()

    # --- Daily limit from config ---
    try:
        from src.config import settings

        daily_limit = float(settings.max_llm_cost_per_account_per_day)
    except Exception:
        daily_limit = 10.00

    if _USE_DB:
        from src.db import service as db

        today = date.today()
        cost_today = float(get_db_data(db.get_daily_cost, "demo_user", today, default=Decimal("0")))
        # For 7-day history we'd need to loop — use sample as fallback
        daily_totals = get_sample_daily_totals()
        cost_by_model = get_sample_cost_by_model()
    else:
        cost_today = get_sample_today_cost()
        daily_totals = get_sample_daily_totals()
        cost_by_model = get_sample_cost_by_model()

    # --- Today's cost vs limit ---
    st.subheader("Today's Usage")
    progress_pct = min(cost_today / daily_limit, 1.0) if daily_limit > 0 else 0
    col_left, col_right = st.columns([3, 1])
    with col_left:
        st.progress(progress_pct, text=f"${cost_today:.2f} / ${daily_limit:.2f}")
    with col_right:
        pct_display = progress_pct * 100
        if pct_display >= 90:
            st.error(f"{pct_display:.0f}% of daily limit")
        elif pct_display >= 70:
            st.warning(f"{pct_display:.0f}% of daily limit")
        else:
            st.success(f"{pct_display:.0f}% of daily limit")

    st.divider()

    # --- 7-day history ---
    st.subheader("7-Day Cost History")
    if daily_totals:
        chart_df = pd.DataFrame(daily_totals)
        chart_df["date"] = pd.to_datetime(chart_df["date"])
        chart_df = chart_df.set_index("date")
        chart_df = chart_df.rename(columns={"total_cost": "LLM Cost ($)"})
        st.bar_chart(chart_df, y="LLM Cost ($)", color="#4285F4")
    else:
        st.info("No cost history available.")

    st.divider()

    # --- Cost per model ---
    st.subheader("Cost by Model")
    if cost_by_model:
        model_rows = []
        for model_name, cost_val in sorted(cost_by_model.items(), key=lambda x: x[1], reverse=True):
            # Shorten model names for display
            display_name = model_name
            if "haiku" in model_name.lower():
                display_name = "Haiku (fast parsing)"
            elif "sonnet" in model_name.lower():
                display_name = "Sonnet (standard analysis)"
            elif "opus" in model_name.lower():
                display_name = "Opus (complex strategy)"
            model_rows.append(
                {
                    "Model": display_name,
                    "Total Cost (7d)": f"${cost_val:.2f}",
                    "Share": f"{cost_val / sum(cost_by_model.values()) * 100:.1f}%",
                }
            )
        st.dataframe(
            pd.DataFrame(model_rows),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No model cost data available.")


# ---------------------------------------------------------------------------
# 6. Audit Log Page
# ---------------------------------------------------------------------------

_AUDIT_EVENT_TYPES = [
    "All",
    "analysis_run",
    "recommendation",
    "action_executed",
    "approval_requested",
    "approval_granted",
    "approval_rejected",
    "approval_expired",
    "data_pull",
    "cost_alert",
    "error",
]


def page_audit_log():
    st.title("Audit Log")

    if not _USE_DB:
        _show_demo_banner()

    # --- Filter ---
    event_filter = st.selectbox("Filter by event type", _AUDIT_EVENT_TYPES)

    if _USE_DB:
        from src.db import service as db

        filter_type = event_filter if event_filter != "All" else None
        entries = get_db_data(db.get_audit_trail, "demo_user", 50, filter_type, default=[])
        rows = []
        for e in entries:
            rows.append(
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "source": e.source,
                    "agent_model": e.agent_model,
                    "event_data": e.event_data,
                    "required_approval": e.required_approval,
                    "approval_status": e.approval_status,
                    "created_at": e.created_at,
                }
            )
    else:
        rows = SAMPLE_AUDIT_LOG
        if event_filter != "All":
            rows = [r for r in rows if r["event_type"] == event_filter]

    if not rows:
        st.info("No audit log entries found for the selected filter.")
        return

    display_data = []
    for r in rows:
        event_data = r.get("event_data", {})
        summary = ""
        if isinstance(event_data, dict):
            # Build a concise summary from event_data
            parts = []
            for k, v in list(event_data.items())[:3]:
                parts.append(f"{k}={v}")
            summary = ", ".join(parts)

        display_data.append(
            {
                "Time": _format_datetime(r.get("created_at")),
                "Event Type": r.get("event_type", "N/A"),
                "Source": r.get("source", "N/A"),
                "Model": _short_model_name(r.get("agent_model")),
                "Details": summary,
                "Approval": r.get("approval_status") or "-",
            }
        )

    st.dataframe(
        pd.DataFrame(display_data),
        use_container_width=True,
        hide_index=True,
    )


def _short_model_name(model: str | None) -> str:
    """Shorten Claude model identifiers for display."""
    if not model:
        return "-"
    if "haiku" in model.lower():
        return "Haiku"
    if "sonnet" in model.lower():
        return "Sonnet"
    if "opus" in model.lower():
        return "Opus"
    return model


# ---------------------------------------------------------------------------
# Page router
# ---------------------------------------------------------------------------

_PAGE_MAP = {
    "Overview": page_overview,
    "Daily Briefings": page_daily_briefings,
    "Approval Queue": page_approval_queue,
    "Accounts": page_accounts,
    "Cost Monitor": page_cost_monitor,
    "Audit Log": page_audit_log,
}

_PAGE_MAP[page]()
