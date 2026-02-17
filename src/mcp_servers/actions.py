"""Action proposal MCP tools for Sidera conversation mode.

Provides a ``propose_action`` tool that agents call during conversations
to propose write operations (budget changes, enable/pause campaigns, etc.).
Proposals are collected per-turn using ``contextvars.ContextVar`` so that
concurrent conversations never leak actions between users.

The conversation runner creates DB approval items and posts Approve/Reject
buttons in the Slack thread.

Tools:
    1. propose_action — Propose a campaign/ad change for human approval

Usage:
    from src.mcp_servers.actions import (
        get_pending_actions, clear_pending_actions,
    )
"""

from __future__ import annotations

import contextvars
from typing import Any

import structlog

from src.agent.tool_registry import tool
from src.mcp_servers.helpers import error_response, text_response

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Pending actions — per-async-task via contextvars (concurrency-safe)
# ---------------------------------------------------------------------------

_pending_actions_var: contextvars.ContextVar[list[dict[str, Any]]] = contextvars.ContextVar(
    "pending_actions", default=[]
)


def _get_actions_list() -> list[dict[str, Any]]:
    """Get the current task's pending actions list, creating if needed."""
    try:
        return _pending_actions_var.get()
    except LookupError:
        actions: list[dict[str, Any]] = []
        _pending_actions_var.set(actions)
        return actions


def get_pending_actions() -> list[dict[str, Any]]:
    """Return and clear all pending action proposals for this turn.

    Called by the conversation runner after an agent turn completes to
    collect any actions the agent proposed via the MCP tool.
    Scoped to the current async task — safe for concurrent use.

    Returns:
        List of recommendation dicts ready for approval processing.
    """
    actions = list(_get_actions_list())
    _pending_actions_var.set([])
    return actions


def clear_pending_actions() -> None:
    """Clear pending actions without returning them."""
    _pending_actions_var.set([])


# ---------------------------------------------------------------------------
# Valid action types and their required action_params fields
# ---------------------------------------------------------------------------

_VALID_ACTION_TYPES: dict[str, list[str]] = {
    "budget_change": ["platform", "customer_id", "campaign_id", "new_budget_micros"],
    "enable_campaign": ["platform", "customer_id", "campaign_id"],
    "pause_campaign": ["platform", "customer_id", "campaign_id"],
    "bid_change": ["platform", "customer_id", "campaign_id"],
    "add_negative_keywords": ["platform", "customer_id", "campaign_id", "keywords"],
    "update_ad_schedule": ["platform", "customer_id", "campaign_id"],
    "update_ad_status": ["platform", "customer_id"],
    "update_adset_budget": ["platform", "account_id"],
    "update_adset_bid": ["platform", "account_id"],
    "create_campaign": ["platform", "customer_id", "name", "daily_budget_micros"],
}


# ---------------------------------------------------------------------------
# Tool: propose_action
# ---------------------------------------------------------------------------

PROPOSE_ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action_type": {
            "type": "string",
            "enum": list(_VALID_ACTION_TYPES.keys()),
            "description": (
                "Type of action to perform. "
                "budget_change: set new daily budget (new_budget_micros = dollars × 1,000,000). "
                "enable_campaign / pause_campaign: change campaign status. "
                "create_campaign: create a new campaign."
            ),
        },
        "description": {
            "type": "string",
            "description": (
                "Human-readable description of what this action does. "
                "Example: 'Set Brand Search daily budget to $10'"
            ),
        },
        "action_params": {
            "type": "object",
            "description": (
                "Parameters for the action. Must include 'platform' "
                "(google_ads or meta) and relevant IDs. For budget_change: "
                "include customer_id, campaign_id, new_budget_micros. "
                "For enable/pause: include customer_id, campaign_id. "
                "new_budget_micros = dollar amount × 1,000,000 "
                "(e.g. $10/day = 10000000)."
            ),
        },
        "reasoning": {
            "type": "string",
            "description": "Why this action is recommended.",
        },
        "projected_impact": {
            "type": "string",
            "description": (
                "Expected impact of this action (e.g. 'Daily spend will become $10/day')."
            ),
        },
        "risk_level": {
            "type": "string",
            "enum": ["low", "medium", "high"],
            "description": "Risk assessment: low, medium, or high.",
        },
    },
    "required": ["action_type", "description", "action_params", "reasoning"],
}


@tool(
    name="propose_action",
    description=(
        "Propose a campaign change for human approval. Use this tool "
        "whenever the user asks you to make changes — budget adjustments, "
        "enabling/pausing campaigns, bid changes, etc. Each call proposes "
        "ONE action. Call multiple times for multiple changes. The system "
        "will post Approve/Reject buttons in the Slack thread. "
        "You MUST call this tool to make any changes — do not just describe "
        "what you would do."
    ),
    input_schema=PROPOSE_ACTION_SCHEMA,
)
async def propose_action(args: dict[str, Any]) -> dict[str, Any]:
    """Propose a campaign action — queued for human approval."""
    action_type = (args.get("action_type") or "").strip()
    description = (args.get("description") or "").strip()
    action_params = args.get("action_params") or {}
    reasoning = (args.get("reasoning") or "").strip()
    projected_impact = (args.get("projected_impact") or "").strip()
    risk_level = (args.get("risk_level") or "medium").strip()

    # --- Validation ---
    if not action_type:
        return error_response("action_type is required.")

    if action_type not in _VALID_ACTION_TYPES:
        return error_response(
            f"Invalid action_type '{action_type}'. "
            f"Valid types: {', '.join(sorted(_VALID_ACTION_TYPES.keys()))}"
        )

    if not description:
        return error_response("description is required.")

    if not action_params:
        return error_response("action_params is required and cannot be empty.")

    if not reasoning:
        return error_response("reasoning is required.")

    # Check required params for this action type
    required_params = _VALID_ACTION_TYPES[action_type]
    missing = [p for p in required_params if not action_params.get(p)]
    if missing:
        return error_response(
            f"Missing required action_params for {action_type}: {', '.join(missing)}"
        )

    logger.info(
        "tool.propose_action",
        action_type=action_type,
        description=description,
        platform=action_params.get("platform", ""),
    )

    # --- Queue as recommendation ---
    recommendation = {
        "action_type": action_type,
        "description": description,
        "reasoning": reasoning,
        "action_params": action_params,
        "projected_impact": projected_impact,
        "risk_level": risk_level,
    }

    _get_actions_list().append(recommendation)

    return text_response(
        f"Action proposal queued for human approval.\n"
        f"Type: {action_type}\n"
        f"Description: {description}\n"
        f"The user will see Approve/Reject buttons in this thread."
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_action_tools() -> list[Any]:
    """Return the list of Action Proposal MCP tool definitions.

    Returns:
        List of 1 tool definition.
    """
    return [propose_action]
