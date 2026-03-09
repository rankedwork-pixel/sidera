"""Test script: Run the platform_health_check skill through SideraAgent.

This exercises the full production code path:
    SideraAgent.run_skill()
    → prompt composition (BASE_SYSTEM_PROMPT + skill supplement + output format)
    → run_agent_loop() (direct Anthropic API)
    → ToolRegistry dispatches tool calls to MCP server handlers
    → agent reasons about results → final text output
    → post to Slack

Usage:
    python3 scripts/test_health_check.py
"""

import asyncio
import os
import sys
from datetime import date

# Ensure we can import from src/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
    override=True,
)


async def run():
    # 1. Load skill
    from src.skills.registry import SkillRegistry

    skill_registry = SkillRegistry()
    skill_registry.load_all()

    skill = skill_registry.get("platform_health_check")
    print(f"Skill loaded: {skill.name}")
    print(f"   Model: {skill.model}")
    print(f"   Tools required: {list(skill.tools_required)}")
    print(f"   Max turns: {skill.max_turns}")
    print()

    # 2. Check which tools are registered in the global ToolRegistry
    from src.agent.tool_registry import get_global_registry

    registry = get_global_registry()
    print(f"Tools registered: {len(registry)} total")
    print(f"   Names: {registry.get_tool_names()}")
    print()

    # 3. Run the skill through SideraAgent (the real production code path)
    from src.agent.core import SideraAgent

    agent = SideraAgent()

    test_accounts = [
        {
            "platform": "google_ads",
            "account_id": "TEST-000-000",
            "account_name": "Test Account",
            "currency": "USD",
        },
    ]

    analysis_date = date.today()

    print("=" * 60)
    print(f"RUNNING: {skill.name}")
    print(f"DATE: {analysis_date.isoformat()}")
    print("=" * 60)
    print()

    result = await agent.run_skill(
        skill=skill,
        user_id="test-user",
        account_ids=test_accounts,
        analysis_date=analysis_date,
    )

    print()
    print("=" * 60)
    print("HEALTH CHECK RESULT:")
    print("=" * 60)
    print(result.briefing_text)
    print()
    print(f"Cost: ${result.cost.get('total_cost_usd', 0):.4f}")
    print(f"Turns: {result.cost.get('num_turns', '?')}")
    print(f"Duration: {result.cost.get('duration_ms', '?')}ms")
    print(f"Model: {result.cost.get('model', '?')}")
    print(f"Recommendations found: {len(result.recommendations)}")

    # 4. Post to Slack
    try:
        from slack_sdk import WebClient

        slack_token = os.getenv("SLACK_BOT_TOKEN")
        slack_channel = os.getenv("SLACK_CHANNEL_ID")
        if slack_token and slack_channel:
            slack_client = WebClient(token=slack_token)
            output = result.briefing_text
            if len(output) > 2900:
                output = output[:2900] + "\n\n_(truncated)_"
            slack_client.chat_postMessage(
                channel=slack_channel,
                text=(
                    f":hospital: *Platform Health Check* — {analysis_date.isoformat()}\n\n"
                    f"{output}\n\n"
                    f"_Cost: ${result.cost.get('total_cost_usd', 0):.4f} | "
                    f"Turns: {result.cost.get('num_turns', '?')} | "
                    f"Model: {result.cost.get('model', '?')}_"
                ),
            )
            print("\nPosted to Slack! ✓")
        else:
            print("\nSlack not configured — skipping post.")
    except Exception as e:
        print(f"\nCould not post to Slack: {e}")


if __name__ == "__main__":
    asyncio.run(run())
