"""Sample data for Sidera dashboard demo mode.

Provides realistic marketing data when no database is connected.
All data is generated relative to today's date so the dashboard
always looks current.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal


def _today() -> date:
    return date.today()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _days_ago(n: int) -> datetime:
    return _now() - timedelta(days=n)


def _date_ago(n: int) -> date:
    return _today() - timedelta(days=n)


# ------------------------------------------------------------------
# Accounts
# ------------------------------------------------------------------

SAMPLE_ACCOUNTS = [
    {
        "id": 1,
        "user_id": "demo_user",
        "platform": "google_ads",
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
        "id": 2,
        "user_id": "demo_user",
        "platform": "google_ads",
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
        "id": 3,
        "user_id": "demo_user",
        "platform": "meta",
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


# ------------------------------------------------------------------
# Analysis Results
# ------------------------------------------------------------------

SAMPLE_ANALYSES = [
    {
        "id": 1,
        "user_id": "demo_user",
        "run_date": _date_ago(0),
        "status": "completed",
        "llm_cost_usd": Decimal("2.34"),
        "llm_input_tokens": 48200,
        "llm_output_tokens": 6100,
        "duration_seconds": 42.7,
        "total_ad_spend": Decimal("8742.50"),
        "accounts_analyzed": [1, 2, 3],
        "briefing_content": (
            "## Daily Performance Briefing\n\n"
            "**Period:** Yesterday\n\n"
            "### Cross-Platform Summary\n"
            "Total spend across 3 accounts: $8,742.50 | "
            "Blended ROAS: 4.2x | Blended CPA: $38.12\n\n"
            "### Google Ads - Search (Acme Corp)\n"
            "- Spend: $3,210.00 | ROAS: 5.1x | CPA: $41.20\n"
            "- Brand campaigns driving 68% of conversions at $12.40 CPA\n"
            "- Non-brand CPA rose 18% WoW to $62.30 -- recommend bid reduction\n\n"
            "### Google Ads - Shopping (Acme Corp)\n"
            "- Spend: $2,890.50 | ROAS: 6.2x | CPA: $28.90\n"
            "- Top product category 'Electronics' saw 22% conversion rate increase\n"
            "- 3 products with zero conversions and $180+ spend -- flag for review\n\n"
            "### Meta - Prospecting (Acme Corp)\n"
            "- Spend: $2,642.00 | ROAS: 2.8x | CPA: $35.60\n"
            "- Advantage+ creative delivering 40% lower CPA than manual creatives\n"
            "- Audience fatigue detected in lookalike 1% segment (frequency 4.2)\n\n"
            "### Recommendations\n"
            "1. Reduce non-brand Search bids by 15% (projected savings: $480/week)\n"
            "2. Pause 3 zero-conversion Shopping products\n"
            "3. Refresh Meta lookalike creative (frequency above threshold)\n"
            "4. Shift $500/day from Meta broad to Google Shopping (higher ROAS)\n"
        ),
        "recommendations": [
            {
                "action": "Reduce non-brand Search bids by 15%",
                "platform": "google_ads",
                "risk": "low",
                "projected_savings": "$480/week",
                "status": "pending",
            },
            {
                "action": "Pause 3 zero-conversion Shopping products",
                "platform": "google_ads",
                "risk": "low",
                "projected_savings": "$180/week",
                "status": "approved",
            },
            {
                "action": "Refresh Meta lookalike creative set",
                "platform": "meta",
                "risk": "medium",
                "projected_savings": "Improve CPA by ~12%",
                "status": "pending",
            },
            {
                "action": "Reallocate $500/day Meta broad -> Google Shopping",
                "platform": "cross_platform",
                "risk": "medium",
                "projected_savings": "$1,200/week improvement",
                "status": "pending",
            },
        ],
        "created_at": _days_ago(0),
    },
    {
        "id": 2,
        "user_id": "demo_user",
        "run_date": _date_ago(1),
        "status": "completed",
        "llm_cost_usd": Decimal("1.87"),
        "llm_input_tokens": 39500,
        "llm_output_tokens": 5200,
        "duration_seconds": 38.1,
        "total_ad_spend": Decimal("9120.30"),
        "accounts_analyzed": [1, 2, 3],
        "briefing_content": (
            "## Daily Performance Briefing\n\n"
            "**Period:** Yesterday\n\n"
            "### Cross-Platform Summary\n"
            "Total spend: $9,120.30 | Blended ROAS: 4.5x | Blended CPA: $36.80\n\n"
            "### Key Highlights\n"
            "- Google Search brand terms converting at $11.80 CPA (target: $45)\n"
            "- Shopping ROAS up 8% after product feed optimization\n"
            "- Meta CPA improved 5% after audience refresh\n\n"
            "### Recommendations\n"
            "1. Increase Shopping budget by 10% (ROAS well above target)\n"
            "2. Test new Meta Advantage+ campaign for top products\n"
        ),
        "recommendations": [
            {
                "action": "Increase Shopping budget by 10%",
                "platform": "google_ads",
                "risk": "low",
                "status": "approved",
            },
            {
                "action": "Test new Meta Advantage+ campaign",
                "platform": "meta",
                "risk": "medium",
                "status": "rejected",
            },
        ],
        "created_at": _days_ago(1),
    },
    {
        "id": 3,
        "user_id": "demo_user",
        "run_date": _date_ago(2),
        "status": "completed",
        "llm_cost_usd": Decimal("2.10"),
        "llm_input_tokens": 42800,
        "llm_output_tokens": 5600,
        "duration_seconds": 40.3,
        "total_ad_spend": Decimal("8950.75"),
        "accounts_analyzed": [1, 2, 3],
        "briefing_content": (
            "## Daily Performance Briefing\n\n"
            "**Period:** Yesterday\n\n"
            "### Cross-Platform Summary\n"
            "Total spend: $8,950.75 | Blended ROAS: 3.9x | Blended CPA: $39.50\n\n"
            "### Alerts\n"
            "- Non-brand CPC increased 12% across Search campaigns\n"
            "- Meta CPM rose 8% (competitive pressure detected)\n\n"
            "### Recommendations\n"
            "1. Review Search keyword bids for top-of-funnel terms\n"
            "2. Add negative keywords from search terms report\n"
            "3. Consider dayparting for Meta campaigns (low conversion 11pm-6am)\n"
        ),
        "recommendations": [
            {
                "action": "Review Search keyword bids",
                "platform": "google_ads",
                "risk": "low",
                "status": "approved",
            },
            {
                "action": "Add negative keywords from search terms report",
                "platform": "google_ads",
                "risk": "low",
                "status": "approved",
            },
            {
                "action": "Enable dayparting for Meta campaigns",
                "platform": "meta",
                "risk": "medium",
                "status": "pending",
            },
        ],
        "created_at": _days_ago(2),
    },
    {
        "id": 4,
        "user_id": "demo_user",
        "run_date": _date_ago(3),
        "status": "completed",
        "llm_cost_usd": Decimal("1.95"),
        "llm_input_tokens": 40100,
        "llm_output_tokens": 4800,
        "duration_seconds": 36.9,
        "total_ad_spend": Decimal("8500.00"),
        "accounts_analyzed": [1, 2, 3],
        "briefing_content": (
            "## Daily Performance Briefing\n\n"
            "**Period:** Yesterday\n\n"
            "### Cross-Platform Summary\n"
            "Total spend: $8,500.00 | Blended ROAS: 4.1x | Blended CPA: $37.90\n\n"
            "All KPIs within target ranges. No urgent actions required.\n"
        ),
        "recommendations": [],
        "created_at": _days_ago(3),
    },
    {
        "id": 5,
        "user_id": "demo_user",
        "run_date": _date_ago(4),
        "status": "failed",
        "llm_cost_usd": Decimal("0.42"),
        "llm_input_tokens": 12000,
        "llm_output_tokens": 800,
        "duration_seconds": 8.2,
        "total_ad_spend": None,
        "accounts_analyzed": [1],
        "briefing_content": None,
        "recommendations": None,
        "error_message": (
            "Meta API rate limit exceeded. Partial data collected for Google Ads only."
        ),
        "created_at": _days_ago(4),
    },
]


# ------------------------------------------------------------------
# Approval Queue
# ------------------------------------------------------------------

SAMPLE_APPROVALS = [
    {
        "id": 1,
        "analysis_id": 1,
        "user_id": "demo_user",
        "action_type": "budget_change",
        "account_id": 2,
        "description": "Increase Google Shopping daily budget from $950 to $1,045 (+10%)",
        "reasoning": (
            "Shopping ROAS has been consistently above target (6.2x vs 5.5x target) "
            "for the past 7 days. Incrementally increasing budget to capture more "
            "high-ROAS traffic."
        ),
        "risk_assessment": "Low risk. Budget increase is incremental and ROAS buffer is 13%.",
        "projected_impact": "Estimated additional $620/day in revenue at current ROAS.",
        "status": "pending",
        "created_at": _days_ago(0),
        "expires_at": _days_ago(0) + timedelta(hours=24),
    },
    {
        "id": 2,
        "analysis_id": 1,
        "user_id": "demo_user",
        "action_type": "bid_change",
        "account_id": 1,
        "description": "Reduce non-brand Search max CPC bids by 15% across 12 ad groups",
        "reasoning": (
            "Non-brand CPA has risen 18% WoW while conversion rate declined. "
            "Reducing bids to improve efficiency without significantly impacting volume."
        ),
        "risk_assessment": "Low-medium. May reduce impressions 10-15% but improve ROAS.",
        "projected_impact": "Projected savings of $480/week with <5% conversion volume loss.",
        "status": "pending",
        "created_at": _days_ago(0),
        "expires_at": _days_ago(0) + timedelta(hours=24),
    },
    {
        "id": 3,
        "analysis_id": 1,
        "user_id": "demo_user",
        "action_type": "pause_campaign",
        "account_id": 3,
        "description": "Pause Meta Lookalike 1% audience set due to creative fatigue",
        "reasoning": (
            "Frequency has reached 4.2 (threshold: 3.0). CTR dropped 35% over "
            "14 days. Continuing to spend will waste budget on diminishing returns."
        ),
        "risk_assessment": "Medium. Pausing removes a traffic source; need creative refresh first.",
        "projected_impact": "Save ~$380/day while creative team prepares refreshed assets.",
        "status": "pending",
        "created_at": _days_ago(0),
        "expires_at": _days_ago(0) + timedelta(hours=24),
    },
    {
        "id": 4,
        "analysis_id": 2,
        "user_id": "demo_user",
        "action_type": "budget_change",
        "account_id": 2,
        "description": "Increase Google Shopping daily budget from $850 to $950 (+12%)",
        "reasoning": "Shopping ROAS exceeded target by 15% for 5 consecutive days.",
        "risk_assessment": "Low risk.",
        "projected_impact": "Additional $540/day revenue.",
        "status": "approved",
        "decided_at": _days_ago(1) + timedelta(hours=2),
        "decided_by": "marketing_lead@acme.com",
        "created_at": _days_ago(1),
    },
    {
        "id": 5,
        "analysis_id": 2,
        "user_id": "demo_user",
        "action_type": "enable_campaign",
        "account_id": 3,
        "description": "Launch new Meta Advantage+ campaign for Electronics category",
        "reasoning": (
            "Electronics has strong ROAS on Shopping; "
            "Meta Advantage+ may find incremental conversions."
        ),
        "risk_assessment": "Medium. New campaign type, start with limited budget.",
        "projected_impact": "Test with $200/day budget; expect 2.5-3.5x ROAS.",
        "status": "rejected",
        "decided_at": _days_ago(1) + timedelta(hours=4),
        "decided_by": "marketing_lead@acme.com",
        "rejection_reason": "Want to wait until Q2 to test new campaign types.",
        "created_at": _days_ago(1),
    },
    {
        "id": 6,
        "analysis_id": 3,
        "user_id": "demo_user",
        "action_type": "bid_change",
        "account_id": 1,
        "description": "Add 45 negative keywords from search terms report",
        "reasoning": "Search terms report shows $320 wasted on irrelevant queries last 7 days.",
        "risk_assessment": "Very low. Only excludes clearly irrelevant terms.",
        "projected_impact": "Save ~$320/week in wasted spend.",
        "status": "approved",
        "decided_at": _days_ago(2) + timedelta(hours=1),
        "decided_by": "marketing_lead@acme.com",
        "created_at": _days_ago(2),
    },
    {
        "id": 7,
        "analysis_id": 3,
        "user_id": "demo_user",
        "action_type": "pause_ad_set",
        "account_id": 3,
        "description": "Enable dayparting on Meta campaigns (pause 11pm-6am)",
        "reasoning": "Only 2% of conversions occur 11pm-6am but 11% of spend.",
        "risk_assessment": "Low. Small volume window with poor efficiency.",
        "projected_impact": "Save ~$290/week with minimal conversion loss.",
        "status": "expired",
        "created_at": _days_ago(3),
        "expires_at": _days_ago(2),
    },
    {
        "id": 8,
        "analysis_id": 4,
        "user_id": "demo_user",
        "action_type": "budget_change",
        "account_id": 1,
        "description": "Reduce Search brand campaign budget by 5% (overdelivering)",
        "reasoning": (
            "Brand campaigns hitting budget cap by 2pm; reducing slightly to smooth delivery."
        ),
        "risk_assessment": "Very low. Impression share already >95%.",
        "projected_impact": "Smoother daily pacing, no impact on conversions.",
        "status": "approved",
        "decided_at": _days_ago(3) + timedelta(hours=3),
        "decided_by": "marketing_lead@acme.com",
        "created_at": _days_ago(3),
    },
]


# ------------------------------------------------------------------
# Audit Log
# ------------------------------------------------------------------

_EVENT_TYPES = [
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

SAMPLE_AUDIT_LOG = [
    {
        "id": i + 1,
        "user_id": "demo_user",
        "account_id": acct,
        "event_type": evt,
        "event_data": data,
        "source": src,
        "agent_model": model,
        "required_approval": req_appr,
        "approval_status": appr_status,
        "created_at": _days_ago(day) + timedelta(hours=hour),
    }
    for i, (evt, acct, data, src, model, req_appr, appr_status, day, hour) in enumerate(
        [
            (
                "analysis_run",
                None,
                {"accounts": 3, "duration_s": 42.7, "status": "completed"},
                "daily_briefing",
                "claude-sonnet-4-5-20250929",
                False,
                None,
                0,
                6,
            ),
            (
                "data_pull",
                1,
                {"platform": "google_ads", "metrics_count": 24, "date_range": "yesterday"},
                "daily_briefing",
                None,
                False,
                None,
                0,
                6,
            ),
            (
                "data_pull",
                3,
                {"platform": "meta", "metrics_count": 18, "date_range": "yesterday"},
                "daily_briefing",
                None,
                False,
                None,
                0,
                6,
            ),
            (
                "recommendation",
                1,
                {"action": "Reduce non-brand bids 15%", "risk": "low"},
                "daily_briefing",
                "claude-sonnet-4-5-20250929",
                True,
                "pending",
                0,
                6,
            ),
            (
                "approval_requested",
                2,
                {"approval_id": 1, "action": "Increase Shopping budget +10%"},
                "daily_briefing",
                None,
                True,
                "pending",
                0,
                6,
            ),
            (
                "approval_granted",
                2,
                {"approval_id": 4, "decided_by": "marketing_lead@acme.com"},
                "approval_workflow",
                None,
                True,
                "approved",
                1,
                8,
            ),
            (
                "action_executed",
                2,
                {"action": "Budget increased to $950/day", "result": "success"},
                "approval_workflow",
                None,
                False,
                None,
                1,
                8,
            ),
            (
                "analysis_run",
                None,
                {"accounts": 3, "duration_s": 38.1, "status": "completed"},
                "daily_briefing",
                "claude-sonnet-4-5-20250929",
                False,
                None,
                1,
                6,
            ),
            (
                "approval_rejected",
                3,
                {
                    "approval_id": 5,
                    "decided_by": "marketing_lead@acme.com",
                    "reason": "Wait until Q2",
                },
                "approval_workflow",
                None,
                True,
                "rejected",
                1,
                10,
            ),
            (
                "data_pull",
                1,
                {"platform": "google_ads", "metrics_count": 24, "date_range": "yesterday"},
                "daily_briefing",
                None,
                False,
                None,
                1,
                6,
            ),
            (
                "analysis_run",
                None,
                {"accounts": 3, "duration_s": 40.3, "status": "completed"},
                "daily_briefing",
                "claude-sonnet-4-5-20250929",
                False,
                None,
                2,
                6,
            ),
            (
                "approval_granted",
                1,
                {"approval_id": 6, "decided_by": "marketing_lead@acme.com"},
                "approval_workflow",
                None,
                True,
                "approved",
                2,
                7,
            ),
            (
                "action_executed",
                1,
                {"action": "Added 45 negative keywords", "result": "success"},
                "approval_workflow",
                None,
                False,
                None,
                2,
                7,
            ),
            (
                "cost_alert",
                None,
                {"daily_cost": 4.85, "threshold": 5.00, "pct": 97},
                "cost_monitor",
                None,
                False,
                None,
                2,
                14,
            ),
            (
                "analysis_run",
                None,
                {"accounts": 3, "duration_s": 36.9, "status": "completed"},
                "daily_briefing",
                "claude-sonnet-4-5-20250929",
                False,
                None,
                3,
                6,
            ),
            (
                "approval_expired",
                3,
                {"approval_id": 7, "action": "Enable dayparting"},
                "approval_workflow",
                None,
                True,
                "expired",
                2,
                6,
            ),
            (
                "error",
                None,
                {"error": "Meta API rate limit exceeded", "retry_after": 300},
                "daily_briefing",
                None,
                False,
                None,
                4,
                6,
            ),
            (
                "analysis_run",
                None,
                {"accounts": 1, "duration_s": 8.2, "status": "failed"},
                "daily_briefing",
                "claude-sonnet-4-5-20250929",
                False,
                None,
                4,
                6,
            ),
            (
                "data_pull",
                2,
                {"platform": "google_ads", "metrics_count": 22, "date_range": "yesterday"},
                "daily_briefing",
                None,
                False,
                None,
                3,
                6,
            ),
            (
                "approval_granted",
                1,
                {"approval_id": 8, "decided_by": "marketing_lead@acme.com"},
                "approval_workflow",
                None,
                True,
                "approved",
                3,
                9,
            ),
        ]
    )
]


# ------------------------------------------------------------------
# Cost Tracking (7 days)
# ------------------------------------------------------------------

SAMPLE_COST_DATA = [
    {
        "run_date": _date_ago(i),
        "costs": costs,
    }
    for i, costs in enumerate(
        [
            # Today
            [
                {
                    "model": "claude-haiku-4-5-20251001",
                    "cost_usd": Decimal("0.18"),
                    "input_tokens": 24000,
                    "output_tokens": 3200,
                    "operation": "data_parsing",
                },
                {
                    "model": "claude-sonnet-4-5-20250929",
                    "cost_usd": Decimal("1.82"),
                    "input_tokens": 38000,
                    "output_tokens": 5100,
                    "operation": "daily_analysis",
                },
                {
                    "model": "claude-opus-4-6",
                    "cost_usd": Decimal("0.34"),
                    "input_tokens": 4200,
                    "output_tokens": 800,
                    "operation": "budget_optimization",
                },
            ],
            # 1 day ago
            [
                {
                    "model": "claude-haiku-4-5-20251001",
                    "cost_usd": Decimal("0.15"),
                    "input_tokens": 20000,
                    "output_tokens": 2800,
                    "operation": "data_parsing",
                },
                {
                    "model": "claude-sonnet-4-5-20250929",
                    "cost_usd": Decimal("1.48"),
                    "input_tokens": 32000,
                    "output_tokens": 4400,
                    "operation": "daily_analysis",
                },
                {
                    "model": "claude-opus-4-6",
                    "cost_usd": Decimal("0.24"),
                    "input_tokens": 3000,
                    "output_tokens": 600,
                    "operation": "budget_optimization",
                },
            ],
            # 2 days ago
            [
                {
                    "model": "claude-haiku-4-5-20251001",
                    "cost_usd": Decimal("0.16"),
                    "input_tokens": 21000,
                    "output_tokens": 2900,
                    "operation": "data_parsing",
                },
                {
                    "model": "claude-sonnet-4-5-20250929",
                    "cost_usd": Decimal("1.65"),
                    "input_tokens": 35000,
                    "output_tokens": 4800,
                    "operation": "daily_analysis",
                },
                {
                    "model": "claude-opus-4-6",
                    "cost_usd": Decimal("0.29"),
                    "input_tokens": 3600,
                    "output_tokens": 700,
                    "operation": "budget_optimization",
                },
            ],
            # 3 days ago
            [
                {
                    "model": "claude-haiku-4-5-20251001",
                    "cost_usd": Decimal("0.14"),
                    "input_tokens": 18500,
                    "output_tokens": 2600,
                    "operation": "data_parsing",
                },
                {
                    "model": "claude-sonnet-4-5-20250929",
                    "cost_usd": Decimal("1.55"),
                    "input_tokens": 33000,
                    "output_tokens": 4500,
                    "operation": "daily_analysis",
                },
                {
                    "model": "claude-opus-4-6",
                    "cost_usd": Decimal("0.26"),
                    "input_tokens": 3200,
                    "output_tokens": 650,
                    "operation": "budget_optimization",
                },
            ],
            # 4 days ago
            [
                {
                    "model": "claude-haiku-4-5-20251001",
                    "cost_usd": Decimal("0.05"),
                    "input_tokens": 6000,
                    "output_tokens": 800,
                    "operation": "data_parsing",
                },
                {
                    "model": "claude-sonnet-4-5-20250929",
                    "cost_usd": Decimal("0.35"),
                    "input_tokens": 8000,
                    "output_tokens": 1200,
                    "operation": "daily_analysis",
                },
                {
                    "model": "claude-opus-4-6",
                    "cost_usd": Decimal("0.02"),
                    "input_tokens": 200,
                    "output_tokens": 50,
                    "operation": "budget_optimization",
                },
            ],
            # 5 days ago
            [
                {
                    "model": "claude-haiku-4-5-20251001",
                    "cost_usd": Decimal("0.17"),
                    "input_tokens": 22500,
                    "output_tokens": 3100,
                    "operation": "data_parsing",
                },
                {
                    "model": "claude-sonnet-4-5-20250929",
                    "cost_usd": Decimal("1.72"),
                    "input_tokens": 36000,
                    "output_tokens": 4900,
                    "operation": "daily_analysis",
                },
                {
                    "model": "claude-opus-4-6",
                    "cost_usd": Decimal("0.31"),
                    "input_tokens": 3800,
                    "output_tokens": 750,
                    "operation": "budget_optimization",
                },
            ],
            # 6 days ago
            [
                {
                    "model": "claude-haiku-4-5-20251001",
                    "cost_usd": Decimal("0.13"),
                    "input_tokens": 17000,
                    "output_tokens": 2400,
                    "operation": "data_parsing",
                },
                {
                    "model": "claude-sonnet-4-5-20250929",
                    "cost_usd": Decimal("1.40"),
                    "input_tokens": 30000,
                    "output_tokens": 4100,
                    "operation": "daily_analysis",
                },
                {
                    "model": "claude-opus-4-6",
                    "cost_usd": Decimal("0.22"),
                    "input_tokens": 2800,
                    "output_tokens": 550,
                    "operation": "budget_optimization",
                },
            ],
        ]
    )
]


def get_sample_daily_totals() -> list[dict]:
    """Return a list of {date, total_cost} for the 7-day chart."""
    totals = []
    for day_data in SAMPLE_COST_DATA:
        total = sum(c["cost_usd"] for c in day_data["costs"])
        totals.append(
            {
                "date": day_data["run_date"],
                "total_cost": float(total),
            }
        )
    return list(reversed(totals))


def get_sample_cost_by_model() -> dict[str, float]:
    """Return total cost per model across all sample data."""
    by_model: dict[str, float] = {}
    for day_data in SAMPLE_COST_DATA:
        for entry in day_data["costs"]:
            model = entry["model"]
            by_model[model] = by_model.get(model, 0.0) + float(entry["cost_usd"])
    return by_model


def get_sample_today_cost() -> float:
    """Return today's total LLM cost from sample data."""
    if SAMPLE_COST_DATA:
        return float(sum(c["cost_usd"] for c in SAMPLE_COST_DATA[0]["costs"]))
    return 0.0
