"""Zero-config demo: run a Sidera agent skill with mock data.

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
# Mock tool handlers — return sample data instead of calling real APIs
# ---------------------------------------------------------------------------


def _text_response(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": False}


async def mock_system_health(args: dict[str, Any]) -> dict[str, Any]:
    return _text_response(
        json.dumps(
            {
                "status": "healthy",
                "database": "connected",
                "redis": "connected",
                "uptime_hours": 72,
                "active_roles": 1,
                "pending_approvals": 0,
            },
            indent=2,
        )
    )


async def mock_cost_summary(args: dict[str, Any]) -> dict[str, Any]:
    return _text_response(
        json.dumps(
            {
                "today_total_usd": 0.52,
                "daily_limit_usd": 10.0,
                "utilization_pct": 5.2,
                "top_roles": [{"role": "ceo", "cost_usd": 0.52}],
            },
            indent=2,
        )
    )


async def mock_noop(args: dict[str, Any]) -> dict[str, Any]:
    return _text_response("Demo mode: action logged but not executed.")


MOCK_TOOLS: dict[str, Any] = {
    "get_system_health": mock_system_health,
    "get_cost_summary": mock_cost_summary,
    "send_slack_alert": mock_noop,
    "send_slack_briefing_preview": mock_noop,
}


def _register_demo_tools() -> None:
    """Override real tool handlers with mock versions."""
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
    print("  Sidera Demo — Zero-Config Agent Briefing")
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

    # 2. Register demo tools
    print("Registering demo tools...", end=" ", flush=True)
    from src.agent.core import SideraAgent

    _register_demo_tools()
    print("done")

    # 3. Pick a skill to run
    skill_id = "org_health_check"
    skill = registry.get(skill_id)
    if not skill:
        skill_id = skill_ids[0]
        skill = registry.get(skill_id)

    role_id = None
    role_context = ""
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
        account_ids=[],
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


if __name__ == "__main__":
    asyncio.run(run_demo())
