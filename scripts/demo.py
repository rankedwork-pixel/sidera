"""Zero-config demo: run a full Sidera agent briefing with sample data.

Requires only Python and an Anthropic API key. No database, no external
services, no Slack. Demonstrates the core agent loop:

    context composition -> LLM call -> tool use -> analysis -> recommendations

Usage:
    export ANTHROPIC_API_KEY="your-key-here"
    python scripts/demo.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# Sample data (from test fixtures)
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent

GOOGLE_ADS_DATA = json.loads((ROOT / "tests/fixtures/google_ads_sample.json").read_text())
META_DATA = json.loads((ROOT / "tests/fixtures/meta_sample.json").read_text())
BIGQUERY_DATA = json.loads((ROOT / "tests/fixtures/bigquery_sample.json").read_text())

DEMO_ACCOUNTS = [
    {
        "platform": "google_ads",
        "account_id": "DEMO-001",
        "account_name": "Demo Store (Google Ads)",
        "target_roas": 4.5,
        "target_cpa": 45.00,
        "monthly_budget_cap": 35000,
        "currency": "USD",
    },
    {
        "platform": "meta",
        "account_id": "DEMO-002",
        "account_name": "Demo Store (Meta)",
        "target_roas": 5.0,
        "target_cpa": 38.00,
        "monthly_budget_cap": 22000,
        "currency": "USD",
    },
]


# ---------------------------------------------------------------------------
# Mock tool handlers — return fixture data instead of calling real APIs
# ---------------------------------------------------------------------------


def _text_response(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": False}


async def mock_google_ads_campaigns(args: dict[str, Any]) -> dict[str, Any]:
    return _text_response(json.dumps(GOOGLE_ADS_DATA["campaigns"], indent=2))


async def mock_google_ads_performance(args: dict[str, Any]) -> dict[str, Any]:
    """Return campaign performance data with period context."""
    days = args.get("days", 7)
    campaigns = GOOGLE_ADS_DATA["campaigns"]
    result = []
    for c in campaigns:
        result.append(
            {
                "campaign": c["campaign.name"],
                "status": c["campaign.status"],
                "period": f"Last {days} days",
                "impressions": int(c["metrics.impressions"]) * (days / 7),
                "clicks": int(c["metrics.clicks"]) * (days / 7),
                "cost": float(c["metrics.cost_micros"]) / 1_000_000 * (days / 7),
                "conversions": float(c["metrics.conversions"]) * (days / 7),
                "conversion_value": float(c["metrics.conversions_value"]) * (days / 7),
            }
        )
    return _text_response(json.dumps(result, indent=2))


async def mock_google_ads_changes(args: dict[str, Any]) -> dict[str, Any]:
    return _text_response(
        json.dumps(
            [
                {
                    "change_date": "2024-01-14",
                    "change_type": "BUDGET",
                    "resource": "Brand Search",
                    "old_value": "$40/day",
                    "new_value": "$50/day",
                    "user": "admin@demo.com",
                },
            ],
            indent=2,
        )
    )


async def mock_meta_campaigns(args: dict[str, Any]) -> dict[str, Any]:
    return _text_response(json.dumps(META_DATA["campaigns"], indent=2))


async def mock_meta_performance(args: dict[str, Any]) -> dict[str, Any]:
    days = args.get("days", 7)
    campaigns = META_DATA["campaigns"]
    result = []
    for c in campaigns:
        purchases = next(
            (a for a in c.get("actions", []) if a["action_type"] == "purchase"),
            {"value": "0"},
        )
        revenue = next(
            (a for a in c.get("action_values", []) if a["action_type"] == "purchase"),
            {"value": "0"},
        )
        result.append(
            {
                "campaign": c["name"],
                "status": c["status"],
                "period": f"Last {days} days",
                "impressions": int(c["impressions"]) * (days / 7),
                "clicks": int(c["clicks"]) * (days / 7),
                "spend": float(c["spend"]) * (days / 7),
                "purchases": int(purchases["value"]) * (days / 7),
                "revenue": float(revenue["value"]) * (days / 7),
            }
        )
    return _text_response(json.dumps(result, indent=2))


async def mock_meta_activity(args: dict[str, Any]) -> dict[str, Any]:
    return _text_response(json.dumps([], indent=2))


async def mock_backend_performance(args: dict[str, Any]) -> dict[str, Any]:
    return _text_response(json.dumps(BIGQUERY_DATA["channel_performance"], indent=2))


async def mock_business_goals(args: dict[str, Any]) -> dict[str, Any]:
    return _text_response(json.dumps(BIGQUERY_DATA["goals"], indent=2))


async def mock_budget_pacing(args: dict[str, Any]) -> dict[str, Any]:
    return _text_response(json.dumps(BIGQUERY_DATA["budget_pacing"], indent=2))


async def mock_campaign_attribution(args: dict[str, Any]) -> dict[str, Any]:
    return _text_response(json.dumps(BIGQUERY_DATA["campaign_attribution"], indent=2))


async def mock_google_ads_recommendations(args: dict[str, Any]) -> dict[str, Any]:
    return _text_response(
        json.dumps(
            [
                {
                    "type": "KEYWORD",
                    "campaign": "Non-Brand Search",
                    "recommendation": "Add keyword: 'organic skincare products'",
                    "estimated_impact": "+12% impressions",
                },
            ],
            indent=2,
        )
    )


async def mock_noop(args: dict[str, Any]) -> dict[str, Any]:
    return _text_response("Demo mode: action logged but not executed.")


# Mapping of real tool names -> mock handlers
MOCK_TOOLS: dict[str, Any] = {
    "list_google_ads_accounts": mock_google_ads_campaigns,
    "get_google_ads_campaigns": mock_google_ads_campaigns,
    "get_google_ads_performance": mock_google_ads_performance,
    "get_google_ads_changes": mock_google_ads_changes,
    "get_google_ads_recommendations": mock_google_ads_recommendations,
    "get_meta_campaigns": mock_meta_campaigns,
    "get_meta_performance": mock_meta_performance,
    "get_meta_account_activity": mock_meta_activity,
    "get_backend_performance": mock_backend_performance,
    "get_business_goals": mock_business_goals,
    "get_budget_pacing": mock_budget_pacing,
    "get_campaign_attribution": mock_campaign_attribution,
    "send_slack_alert": mock_noop,
    "send_slack_briefing_preview": mock_noop,
}


def _register_demo_tools() -> None:
    """Override real tool handlers with mock versions that return fixture data."""
    from src.agent.tool_registry import ToolDefinition, get_global_registry

    registry = get_global_registry()
    for tool_name, handler in MOCK_TOOLS.items():
        if tool_name in registry:
            existing = registry._tools[tool_name]
            registry._tools[tool_name] = ToolDefinition(
                name=existing.name,
                description=existing.description + " [DEMO MODE]",
                input_schema=existing.input_schema,
                handler=handler,
            )


# ---------------------------------------------------------------------------
# Main demo
# ---------------------------------------------------------------------------


async def run_demo() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY environment variable is required.")
        print()
        print("  export ANTHROPIC_API_KEY='your-key-here'")
        print("  python scripts/demo.py")
        sys.exit(1)

    print()
    print("  Sidera Demo - Zero-Config Agent Briefing")
    print("=" * 50)
    print()

    # 1. Load skills from disk YAML
    print("Loading skills from disk...", end=" ", flush=True)
    from src.skills.registry import SkillRegistry

    registry = SkillRegistry()
    registry.load_all()

    dept_count = len(registry.get_departments())
    role_count = len(registry.get_roles())
    skill_ids = list(registry.get_all_skill_ids())
    print(f"{len(skill_ids)} skills across {dept_count} departments, {role_count} roles")

    # 2. Register demo tools (override real API tools with mock data)
    print("Registering demo tools...", end=" ", flush=True)
    # Importing SideraAgent triggers all @tool registrations
    from src.agent.core import SideraAgent

    _register_demo_tools()
    print("done")

    # 3. Pick a skill to run
    skill_id = "anomaly_detector"
    skill = registry.get(skill_id)
    if not skill:
        # Fallback to first available skill
        skill_id = skill_ids[0]
        skill = registry.get(skill_id)

    role_id = None
    role_context = ""
    # Find the role that owns this skill and compose context
    for r in registry.get_roles():
        role_def = registry.get_role(r)
        if role_def and skill_id in (role_def.briefing_skills or []):
            role_id = r
            from src.skills.executor import compose_role_context

            role_context = compose_role_context(role_def, registry)
            break

    print()
    print(f"  Skill: {skill.name} ({skill_id})")
    print(f"  Model: {skill.model}")
    print(f"  Role:  {role_id or 'standalone'}")
    print(f"  Tools: {', '.join(skill.tools_required)}")
    print()
    print("-" * 50)
    print()

    # 4. Run the skill
    agent = SideraAgent()
    start = time.time()

    result = await agent.run_skill(
        skill=skill,
        user_id="demo-user",
        account_ids=DEMO_ACCOUNTS,
        analysis_date=date.today(),
        role_context=role_context,
    )

    elapsed = time.time() - start

    # 5. Print results
    print("BRIEFING OUTPUT")
    print("=" * 50)
    print()
    print(result.briefing_text)
    print()

    if result.recommendations:
        print("-" * 50)
        print(f"RECOMMENDATIONS ({len(result.recommendations)})")
        print("-" * 50)
        for i, rec in enumerate(result.recommendations, 1):
            action = rec.get("action", rec.get("type", "Unknown"))
            reasoning = rec.get("reasoning", rec.get("description", ""))
            print(f"  {i}. {action}")
            if reasoning:
                print(f"     {reasoning}")
        print()

    # 6. Cost summary
    cost = result.cost
    print("-" * 50)
    print("COST SUMMARY")
    print("-" * 50)
    total = cost.get("total_cost_usd", 0)
    model = cost.get("model", "unknown")
    turns = cost.get("num_turns", "?")
    input_tokens = cost.get("input_tokens", "?")
    output_tokens = cost.get("output_tokens", "?")
    print(f"  Model:         {model}")
    print(f"  Turns:         {turns}")
    print(f"  Input tokens:  {input_tokens}")
    print(f"  Output tokens: {output_tokens}")
    print(f"  Cost:          ${total:.4f}")
    print(f"  Time:          {elapsed:.1f}s")
    print()
    print("Demo complete. See QUICKSTART.md for full setup with real data sources.")
    print()


if __name__ == "__main__":
    asyncio.run(run_demo())
