"""Shared fixtures for workflow tests.

Provides constants and helpers used by both ``test_daily_briefing.py``
and ``test_skill_workflows.py``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

# =====================================================================
# Constants
# =====================================================================

SAMPLE_ACCOUNTS = [
    {
        "platform": "google_ads",
        "account_id": "1234567890",
        "account_name": "Acme Store",
    },
]

SAMPLE_RECOMMENDATIONS = [
    {
        "action": "Increase search budget by 15%",
        "reasoning": "Strong ROAS trend over last 7 days",
        "projected_impact": "+$2,400 revenue/week",
        "risk_level": "low",
    },
    {
        "action": "Pause underperforming ad set",
        "reasoning": "CPA 3x above target with declining CTR",
        "projected_impact": "Save $500/week",
        "risk_level": "medium",
    },
]


# =====================================================================
# Helpers
# =====================================================================


def _make_mock_context(
    event_data: dict | None = None,
    *,
    run_id: str = "test-run-123",
) -> MagicMock:
    """Build a mock Inngest Context with a working step.run.

    ``step.run`` calls the handler and returns its result so that
    downstream logic can use the value.  ``step.wait_for_event``
    defaults to ``None`` (timeout / expiry).
    """
    ctx = MagicMock()
    ctx.event = MagicMock()
    ctx.event.data = event_data or {}
    ctx.run_id = run_id

    # step.run should call the async handler and return its result
    async def mock_step_run(step_id: str, handler, *args):
        if asyncio.iscoroutinefunction(handler):
            return await handler(*args)
        return handler(*args)

    ctx.step.run = AsyncMock(side_effect=mock_step_run)
    ctx.step.wait_for_event = AsyncMock(return_value=None)
    ctx.step.send_event = AsyncMock(return_value=["event-id-1"])

    return ctx
