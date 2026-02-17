"""Seed the database with sample data for local development.

Usage: python -m scripts.seed_test_data

Inserts realistic marketing data across all 7 tables so the app,
dashboard, and agent have something to work with before real API
keys are connected.  Skips seeding if data already exists.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from src.db.session import get_db_session
from src.models.schema import (
    Account,
    ActionType,
    AnalysisResult,
    ApprovalQueueItem,
    ApprovalStatus,
    AuditLog,
    Campaign,
    CostTracking,
    DailyMetric,
    Platform,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today() -> date:
    return date.today()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _days_ago(n: int) -> datetime:
    return _now() - timedelta(days=n)


def _date_ago(n: int) -> date:
    return _today() - timedelta(days=n)


# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------


async def _count(session, model):
    result = await session.execute(select(func.count()).select_from(model))
    return result.scalar() or 0


async def seed_accounts(session) -> list[Account]:
    """Insert 3 sample ad-platform accounts."""
    if await _count(session, Account) > 0:
        print("  accounts: already seeded, skipping")
        result = await session.execute(select(Account))
        return list(result.scalars().all())

    accounts_data = [
        {
            "user_id": "demo_user",
            "platform": Platform.GOOGLE_ADS,
            "platform_account_id": "123-456-7890",
            "account_name": "Acme Corp - Search",
            "is_active": True,
            "timezone": "America/New_York",
            "target_roas": 4.0,
            "target_cpa": Decimal("45.00"),
            "monthly_budget_cap": Decimal("85000.00"),
            "created_at": _days_ago(60),
        },
        {
            "user_id": "demo_user",
            "platform": Platform.GOOGLE_ADS,
            "platform_account_id": "234-567-8901",
            "account_name": "Acme Corp - Shopping",
            "is_active": True,
            "timezone": "America/New_York",
            "target_roas": 5.5,
            "target_cpa": Decimal("32.00"),
            "monthly_budget_cap": Decimal("120000.00"),
            "created_at": _days_ago(45),
        },
        {
            "user_id": "demo_user",
            "platform": Platform.META,
            "platform_account_id": "act_9876543210",
            "account_name": "Acme Corp - Meta Prospecting",
            "is_active": True,
            "timezone": "America/Los_Angeles",
            "target_roas": 3.2,
            "target_cpa": Decimal("28.00"),
            "monthly_budget_cap": Decimal("65000.00"),
            "created_at": _days_ago(30),
        },
    ]

    accounts = []
    for data in accounts_data:
        account = Account(**data)
        session.add(account)
        accounts.append(account)

    await session.flush()
    print(f"  accounts: inserted {len(accounts)} rows")
    return accounts


async def seed_campaigns(session, accounts: list[Account]) -> list[Campaign]:
    """Insert 5 campaigns across the seeded accounts."""
    if await _count(session, Campaign) > 0:
        print("  campaigns: already seeded, skipping")
        result = await session.execute(select(Campaign))
        return list(result.scalars().all())

    # Map accounts by name fragment for readability
    acct_map = {a.account_name: a for a in accounts}
    search_acct = acct_map.get("Acme Corp - Search", accounts[0])
    shopping_acct = acct_map.get("Acme Corp - Shopping", accounts[1])
    meta_acct = acct_map.get("Acme Corp - Meta Prospecting", accounts[2])

    campaigns_data = [
        {
            "account_id": search_acct.id,
            "platform": Platform.GOOGLE_ADS,
            "platform_campaign_id": "g_campaign_001",
            "campaign_name": "Brand Search - Acme",
            "campaign_type": "search",
            "status": "enabled",
            "daily_budget": Decimal("1200.00"),
            "platform_data": {"bidding_strategy": "target_cpa", "target_cpa_micros": 12400000},
        },
        {
            "account_id": search_acct.id,
            "platform": Platform.GOOGLE_ADS,
            "platform_campaign_id": "g_campaign_002",
            "campaign_name": "Non-Brand Search - Electronics",
            "campaign_type": "search",
            "status": "enabled",
            "daily_budget": Decimal("850.00"),
            "platform_data": {
                "bidding_strategy": "maximize_conversions",
                "max_cpc_micros": 4500000,
            },
        },
        {
            "account_id": shopping_acct.id,
            "platform": Platform.GOOGLE_ADS,
            "platform_campaign_id": "g_campaign_003",
            "campaign_name": "Standard Shopping - All Products",
            "campaign_type": "shopping",
            "status": "enabled",
            "daily_budget": Decimal("950.00"),
            "platform_data": {"bidding_strategy": "target_roas", "target_roas": 5.5},
        },
        {
            "account_id": meta_acct.id,
            "platform": Platform.META,
            "platform_campaign_id": "m_campaign_001",
            "campaign_name": "Prospecting - Lookalike 1%",
            "campaign_type": "advantage_plus",
            "status": "enabled",
            "daily_budget": Decimal("700.00"),
            "platform_data": {
                "objective": "conversions",
                "optimization_goal": "offsite_conversions",
            },
        },
        {
            "account_id": meta_acct.id,
            "platform": Platform.META,
            "platform_campaign_id": "m_campaign_002",
            "campaign_name": "Retargeting - Website Visitors",
            "campaign_type": "advantage_plus",
            "status": "enabled",
            "daily_budget": Decimal("450.00"),
            "platform_data": {
                "objective": "conversions",
                "optimization_goal": "offsite_conversions",
            },
        },
    ]

    campaigns = []
    for data in campaigns_data:
        campaign = Campaign(**data)
        session.add(campaign)
        campaigns.append(campaign)

    await session.flush()
    print(f"  campaigns: inserted {len(campaigns)} rows")
    return campaigns


async def seed_daily_metrics(session, campaigns: list[Campaign]) -> None:
    """Insert 7 days of daily metrics for each campaign (35 rows total)."""
    if await _count(session, DailyMetric) > 0:
        print("  daily_metrics: already seeded, skipping")
        return

    import random

    random.seed(42)  # deterministic for reproducibility

    # Base metrics per campaign (index matches campaign order)
    base_profiles = [
        # Brand Search: high CTR, low CPA
        {"impressions": 18000, "clicks": 2200, "cost": 1200, "conversions": 95, "conv_value": 6100},
        # Non-Brand Search: moderate CTR, higher CPA
        {"impressions": 12000, "clicks": 800, "cost": 850, "conversions": 14, "conv_value": 3200},
        # Shopping: good ROAS
        {"impressions": 45000, "clicks": 1800, "cost": 950, "conversions": 33, "conv_value": 5900},
        # Meta Prospecting: broad reach, moderate CPA
        {"impressions": 85000, "clicks": 2400, "cost": 700, "conversions": 20, "conv_value": 2250},
        # Meta Retargeting: high CTR, strong CPA
        {"impressions": 22000, "clicks": 1100, "cost": 450, "conversions": 28, "conv_value": 2800},
    ]

    count = 0
    for idx, campaign in enumerate(campaigns):
        base = base_profiles[idx]
        for day_offset in range(7):
            metric_date = _date_ago(day_offset)
            # Add +/-15% random variation
            factor = 0.85 + random.random() * 0.30
            impressions = int(base["impressions"] * factor)
            clicks = int(base["clicks"] * factor)
            cost = Decimal(str(round(base["cost"] * factor, 2)))
            conversions = round(base["conversions"] * factor, 1)
            conv_value = Decimal(str(round(base["conv_value"] * factor, 2)))

            ctr = clicks / impressions if impressions else 0
            cpc = cost / clicks if clicks else Decimal("0")
            cpa = cost / Decimal(str(conversions)) if conversions else Decimal("0")
            roas = float(conv_value / cost) if cost else 0

            metric = DailyMetric(
                campaign_id=campaign.id,
                date=metric_date,
                impressions=impressions,
                clicks=clicks,
                cost=cost,
                conversions=conversions,
                conversion_value=conv_value,
                ctr=ctr,
                cpc=cpc,
                cpa=cpa,
                roas=roas,
                platform_metrics={"source": "seed_data"},
            )
            session.add(metric)
            count += 1

    await session.flush()
    print(f"  daily_metrics: inserted {count} rows")


async def seed_analysis_results(session) -> list[AnalysisResult]:
    """Insert 3 analysis results with briefing text."""
    if await _count(session, AnalysisResult) > 0:
        print("  analysis_results: already seeded, skipping")
        result = await session.execute(select(AnalysisResult))
        return list(result.scalars().all())

    analyses_data = [
        {
            "user_id": "demo_user",
            "run_date": _date_ago(0),
            "briefing_content": (
                "## Daily Performance Briefing\n\n"
                "**Period:** Yesterday\n\n"
                "### Cross-Platform Summary\n"
                "Total spend across 3 accounts: $8,742.50 | "
                "Blended ROAS: 4.2x | Blended CPA: $38.12\n\n"
                "### Key Highlights\n"
                "- Brand Search CPA well below target at $12.40\n"
                "- Non-brand CPA rose 18% WoW -- recommend bid reduction\n"
                "- Shopping ROAS at 6.2x (target 5.5x) -- room to scale\n"
                "- Meta frequency at 4.2 on lookalike -- creative fatigue\n"
            ),
            "recommendations": [
                {"action": "Reduce non-brand bids 15%", "platform": "google_ads", "risk": "low"},
                {
                    "action": "Pause zero-conversion products",
                    "platform": "google_ads",
                    "risk": "low",
                },
                {"action": "Refresh Meta lookalike creative", "platform": "meta", "risk": "medium"},
            ],
            "accounts_analyzed": [1, 2, 3],
            "total_ad_spend": Decimal("8742.50"),
            "llm_input_tokens": 48200,
            "llm_output_tokens": 6100,
            "llm_cost_usd": Decimal("2.34"),
            "duration_seconds": 42.7,
            "status": "completed",
        },
        {
            "user_id": "demo_user",
            "run_date": _date_ago(1),
            "briefing_content": (
                "## Daily Performance Briefing\n\n"
                "**Period:** Yesterday\n\n"
                "### Cross-Platform Summary\n"
                "Total spend: $9,120.30 | Blended ROAS: 4.5x | Blended CPA: $36.80\n\n"
                "### Key Highlights\n"
                "- Shopping ROAS up 8% after product feed optimization\n"
                "- Meta CPA improved 5% after audience refresh\n"
            ),
            "recommendations": [
                {"action": "Increase Shopping budget 10%", "platform": "google_ads", "risk": "low"},
                {"action": "Test Advantage+ campaign", "platform": "meta", "risk": "medium"},
            ],
            "accounts_analyzed": [1, 2, 3],
            "total_ad_spend": Decimal("9120.30"),
            "llm_input_tokens": 39500,
            "llm_output_tokens": 5200,
            "llm_cost_usd": Decimal("1.87"),
            "duration_seconds": 38.1,
            "status": "completed",
        },
        {
            "user_id": "demo_user",
            "run_date": _date_ago(2),
            "briefing_content": (
                "## Daily Performance Briefing\n\n"
                "**Period:** Yesterday\n\n"
                "### Cross-Platform Summary\n"
                "Total spend: $8,950.75 | Blended ROAS: 3.9x | Blended CPA: $39.50\n\n"
                "### Alerts\n"
                "- Non-brand CPC increased 12% across Search campaigns\n"
                "- Meta CPM rose 8% (competitive pressure detected)\n"
            ),
            "recommendations": [
                {"action": "Review Search keyword bids", "platform": "google_ads", "risk": "low"},
                {"action": "Add negative keywords", "platform": "google_ads", "risk": "low"},
                {"action": "Enable dayparting for Meta", "platform": "meta", "risk": "medium"},
            ],
            "accounts_analyzed": [1, 2, 3],
            "total_ad_spend": Decimal("8950.75"),
            "llm_input_tokens": 42800,
            "llm_output_tokens": 5600,
            "llm_cost_usd": Decimal("2.10"),
            "duration_seconds": 40.3,
            "status": "completed",
        },
    ]

    analyses = []
    for data in analyses_data:
        analysis = AnalysisResult(**data)
        session.add(analysis)
        analyses.append(analysis)

    await session.flush()
    print(f"  analysis_results: inserted {len(analyses)} rows")
    return analyses


async def seed_approval_queue(session, analyses: list[AnalysisResult]) -> None:
    """Insert 5 approval queue items (mix of pending, approved, rejected)."""
    if await _count(session, ApprovalQueueItem) > 0:
        print("  approval_queue: already seeded, skipping")
        return

    # Use analysis IDs from seeded analyses
    a1 = analyses[0].id if len(analyses) > 0 else 1
    a2 = analyses[1].id if len(analyses) > 1 else 2
    a3 = analyses[2].id if len(analyses) > 2 else 3

    approvals_data = [
        {
            "analysis_id": a1,
            "user_id": "demo_user",
            "action_type": ActionType.BUDGET_CHANGE,
            "account_id": 2,
            "description": "Increase Google Shopping daily budget from $950 to $1,045 (+10%)",
            "reasoning": (
                "Shopping ROAS has been consistently above target (6.2x vs 5.5x target) "
                "for the past 7 days."
            ),
            "action_params": {"campaign_id": "g_campaign_003", "new_budget": 1045.00},
            "risk_assessment": "Low risk. Budget increase is incremental.",
            "projected_impact": "Estimated additional $620/day in revenue.",
            "status": ApprovalStatus.PENDING,
            "created_at": _days_ago(0),
            "expires_at": _days_ago(0) + timedelta(hours=24),
        },
        {
            "analysis_id": a1,
            "user_id": "demo_user",
            "action_type": ActionType.BID_CHANGE,
            "account_id": 1,
            "description": "Reduce non-brand Search max CPC bids by 15%",
            "reasoning": "Non-brand CPA has risen 18% WoW while conversion rate declined.",
            "action_params": {"campaign_id": "g_campaign_002", "bid_modifier": -0.15},
            "risk_assessment": "Low-medium. May reduce impressions 10-15%.",
            "projected_impact": "Projected savings of $480/week.",
            "status": ApprovalStatus.PENDING,
            "created_at": _days_ago(0),
            "expires_at": _days_ago(0) + timedelta(hours=24),
        },
        {
            "analysis_id": a2,
            "user_id": "demo_user",
            "action_type": ActionType.BUDGET_CHANGE,
            "account_id": 2,
            "description": "Increase Google Shopping daily budget from $850 to $950 (+12%)",
            "reasoning": "Shopping ROAS exceeded target by 15% for 5 consecutive days.",
            "action_params": {"campaign_id": "g_campaign_003", "new_budget": 950.00},
            "risk_assessment": "Low risk.",
            "projected_impact": "Additional $540/day revenue.",
            "status": ApprovalStatus.APPROVED,
            "decided_at": _days_ago(1) + timedelta(hours=2),
            "decided_by": "marketing_lead@acme.com",
            "created_at": _days_ago(1),
        },
        {
            "analysis_id": a2,
            "user_id": "demo_user",
            "action_type": ActionType.ENABLE_CAMPAIGN,
            "account_id": 3,
            "description": "Launch new Meta Advantage+ campaign for Electronics",
            "reasoning": "Electronics has strong ROAS on Shopping; Meta may find incremental lift.",
            "action_params": {"campaign_type": "advantage_plus", "daily_budget": 200.00},
            "risk_assessment": "Medium. New campaign type, start with limited budget.",
            "projected_impact": "Test with $200/day budget; expect 2.5-3.5x ROAS.",
            "status": ApprovalStatus.REJECTED,
            "decided_at": _days_ago(1) + timedelta(hours=4),
            "decided_by": "marketing_lead@acme.com",
            "rejection_reason": "Want to wait until Q2 to test new campaign types.",
            "created_at": _days_ago(1),
        },
        {
            "analysis_id": a3,
            "user_id": "demo_user",
            "action_type": ActionType.BID_CHANGE,
            "account_id": 1,
            "description": "Add 45 negative keywords from search terms report",
            "reasoning": "Search terms report shows $320 wasted on irrelevant queries last 7 days.",
            "action_params": {"negative_keywords_count": 45},
            "risk_assessment": "Very low. Only excludes clearly irrelevant terms.",
            "projected_impact": "Save ~$320/week in wasted spend.",
            "status": ApprovalStatus.APPROVED,
            "decided_at": _days_ago(2) + timedelta(hours=1),
            "decided_by": "marketing_lead@acme.com",
            "created_at": _days_ago(2),
        },
    ]

    count = 0
    for data in approvals_data:
        item = ApprovalQueueItem(**data)
        session.add(item)
        count += 1

    await session.flush()
    print(f"  approval_queue: inserted {count} rows")


async def seed_audit_log(session) -> None:
    """Insert 10 audit log entries."""
    if await _count(session, AuditLog) > 0:
        print("  audit_log: already seeded, skipping")
        return

    entries = [
        {
            "user_id": "demo_user",
            "event_type": "analysis_run",
            "event_data": {"accounts": 3, "duration_s": 42.7, "status": "completed"},
            "source": "daily_briefing",
            "agent_model": "claude-sonnet-4-5-20250929",
            "created_at": _days_ago(0),
        },
        {
            "user_id": "demo_user",
            "event_type": "data_pull",
            "event_data": {"platform": "google_ads", "metrics_count": 24},
            "source": "daily_briefing",
            "account_id": 1,
            "created_at": _days_ago(0),
        },
        {
            "user_id": "demo_user",
            "event_type": "data_pull",
            "event_data": {"platform": "meta", "metrics_count": 18},
            "source": "daily_briefing",
            "account_id": 3,
            "created_at": _days_ago(0),
        },
        {
            "user_id": "demo_user",
            "event_type": "recommendation",
            "event_data": {"action": "Reduce non-brand bids 15%", "risk": "low"},
            "source": "daily_briefing",
            "agent_model": "claude-sonnet-4-5-20250929",
            "required_approval": True,
            "approval_status": "pending",
            "created_at": _days_ago(0),
        },
        {
            "user_id": "demo_user",
            "event_type": "approval_requested",
            "event_data": {"approval_id": 1, "action": "Increase Shopping budget +10%"},
            "source": "daily_briefing",
            "account_id": 2,
            "required_approval": True,
            "approval_status": "pending",
            "created_at": _days_ago(0),
        },
        {
            "user_id": "demo_user",
            "event_type": "approval_granted",
            "event_data": {"approval_id": 3, "decided_by": "marketing_lead@acme.com"},
            "source": "approval_workflow",
            "account_id": 2,
            "required_approval": True,
            "approval_status": "approved",
            "approved_by": "marketing_lead@acme.com",
            "created_at": _days_ago(1),
        },
        {
            "user_id": "demo_user",
            "event_type": "action_executed",
            "event_data": {"action": "Budget increased to $950/day", "result": "success"},
            "source": "approval_workflow",
            "account_id": 2,
            "created_at": _days_ago(1),
        },
        {
            "user_id": "demo_user",
            "event_type": "approval_rejected",
            "event_data": {
                "approval_id": 4,
                "decided_by": "marketing_lead@acme.com",
                "reason": "Wait until Q2",
            },
            "source": "approval_workflow",
            "account_id": 3,
            "required_approval": True,
            "approval_status": "rejected",
            "created_at": _days_ago(1),
        },
        {
            "user_id": "demo_user",
            "event_type": "cost_alert",
            "event_data": {"daily_cost": 4.85, "threshold": 5.00, "pct": 97},
            "source": "cost_monitor",
            "created_at": _days_ago(2),
        },
        {
            "user_id": "demo_user",
            "event_type": "error",
            "event_data": {"error": "Meta API rate limit exceeded", "retry_after": 300},
            "source": "daily_briefing",
            "created_at": _days_ago(4),
        },
    ]

    count = 0
    for data in entries:
        entry = AuditLog(**data)
        session.add(entry)
        count += 1

    await session.flush()
    print(f"  audit_log: inserted {count} rows")


async def seed_cost_tracking(session) -> None:
    """Insert 7 days of cost tracking entries (3 per day = 21 rows)."""
    if await _count(session, CostTracking) > 0:
        print("  cost_tracking: already seeded, skipping")
        return

    daily_costs = [
        # (day_offset, model, cost, input_tokens, output_tokens, operation)
        (0, "claude-haiku-4-5-20251001", "0.18", 24000, 3200, "data_parsing"),
        (0, "claude-sonnet-4-5-20250929", "1.82", 38000, 5100, "daily_analysis"),
        (0, "claude-opus-4-6", "0.34", 4200, 800, "budget_optimization"),
        (1, "claude-haiku-4-5-20251001", "0.15", 20000, 2800, "data_parsing"),
        (1, "claude-sonnet-4-5-20250929", "1.48", 32000, 4400, "daily_analysis"),
        (1, "claude-opus-4-6", "0.24", 3000, 600, "budget_optimization"),
        (2, "claude-haiku-4-5-20251001", "0.16", 21000, 2900, "data_parsing"),
        (2, "claude-sonnet-4-5-20250929", "1.65", 35000, 4800, "daily_analysis"),
        (2, "claude-opus-4-6", "0.29", 3600, 700, "budget_optimization"),
        (3, "claude-haiku-4-5-20251001", "0.14", 18500, 2600, "data_parsing"),
        (3, "claude-sonnet-4-5-20250929", "1.55", 33000, 4500, "daily_analysis"),
        (3, "claude-opus-4-6", "0.26", 3200, 650, "budget_optimization"),
        (4, "claude-haiku-4-5-20251001", "0.05", 6000, 800, "data_parsing"),
        (4, "claude-sonnet-4-5-20250929", "0.35", 8000, 1200, "daily_analysis"),
        (4, "claude-opus-4-6", "0.02", 200, 50, "budget_optimization"),
        (5, "claude-haiku-4-5-20251001", "0.17", 22500, 3100, "data_parsing"),
        (5, "claude-sonnet-4-5-20250929", "1.72", 36000, 4900, "daily_analysis"),
        (5, "claude-opus-4-6", "0.31", 3800, 750, "budget_optimization"),
        (6, "claude-haiku-4-5-20251001", "0.13", 17000, 2400, "data_parsing"),
        (6, "claude-sonnet-4-5-20250929", "1.40", 30000, 4100, "daily_analysis"),
        (6, "claude-opus-4-6", "0.22", 2800, 550, "budget_optimization"),
    ]

    count = 0
    for day_offset, model, cost, inp, out, operation in daily_costs:
        entry = CostTracking(
            user_id="demo_user",
            run_date=_date_ago(day_offset),
            model=model,
            cost_usd=Decimal(cost),
            input_tokens=inp,
            output_tokens=out,
            operation=operation,
        )
        session.add(entry)
        count += 1

    await session.flush()
    print(f"  cost_tracking: inserted {count} rows")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    print("Seeding database with sample data...\n")

    async with get_db_session() as session:
        accounts = await seed_accounts(session)
        campaigns = await seed_campaigns(session, accounts)
        await seed_daily_metrics(session, campaigns)
        analyses = await seed_analysis_results(session)
        await seed_approval_queue(session, analyses)
        await seed_audit_log(session)
        await seed_cost_tracking(session)

    print("\nDone! Database seeded successfully.")


if __name__ == "__main__":
    asyncio.run(main())
