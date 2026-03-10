"""Tool allowlist for the Sidera MCP stdio server.

Defines which of Sidera's internal tools are exposed directly to
Claude Code via the MCP protocol.  Only **stateless** tools that need no
``contextvars`` setup are included — write tools (which require approval
IDs) and context-dependent tools (memory, delegation, messaging, actions,
evolution) are excluded.

Write operations are accessed indirectly via the ``talk_to_role`` meta-tool,
which sets up the proper context before running an agent turn.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Direct tools — stateless, pass-through to ToolRegistry.dispatch()
# ---------------------------------------------------------------------------

DIRECT_TOOLS: frozenset[str] = frozenset(
    [
        # Slack — 6
        "send_slack_alert",
        "send_slack_briefing_preview",
        "check_slack_connection",
        "send_slack_thread_reply",
        "react_to_message",
        "search_role_memory_archive",
        # Memory detail (read-only) — 1
        "load_memory_detail",
        # Code execution (stateless) — 1
        "run_skill_code",
        # Web research — 2
        "fetch_web_page",
        "web_search",
        # System introspection — 8
        "get_system_health",
        "get_failed_runs",
        "resolve_failed_run",
        "get_recent_audit_events",
        "get_approval_queue_status",
        "get_conversation_status",
        "get_cost_summary",
        "get_webhook_events",
    ]
)

# ---------------------------------------------------------------------------
# Excluded tools (for reference / tests)
# ---------------------------------------------------------------------------

WRITE_TOOLS: frozenset[str] = frozenset([])

CONTEXT_DEPENDENT_TOOLS: frozenset[str] = frozenset(
    [
        # Actions (contextvars)
        "propose_action",
        # Evolution (contextvars)
        "propose_skill_change",
        "propose_role_change",
        # Claude Code task proposals (contextvars)
        "propose_claude_code_task",
        # Memory (contextvars)
        "save_memory",
        # Messaging (contextvars)
        "check_inbox",
        "reply_to_message",
        "send_message_to_role",
        "push_learning_to_role",
        # Delegation (contextvars)
        "delegate_to_role",
        "consult_peer",
        # Skill runner (contextvars)
        "run_skill",
        # Orchestration (contextvars)
        "orchestrate_task",
        # Context loading (needs skill context)
        "load_skill_context",
        "load_referenced_skill_context",
    ]
)

# Headless context tools — subset of CONTEXT_DEPENDENT_TOOLS that are safe
# for headless Claude Code task execution when contextvars are set up.
HEADLESS_CONTEXT_TOOLS: frozenset[str] = frozenset(
    [
        "propose_action",
        "propose_skill_change",
        "propose_role_change",
        "save_memory",
        "check_inbox",
        "reply_to_message",
        "send_message_to_role",
        "load_skill_context",
        "load_referenced_skill_context",
    ]
)

# Meta-tool names (defined in meta_tools.py, not in ToolRegistry)
META_TOOL_NAMES: frozenset[str] = frozenset(
    [
        "talk_to_role",
        "run_role",
        "list_roles",
        "review_pending_approvals",
        "decide_approval",
        "run_claude_code_task",
        "orchestrate",
        "load_plugin",
        "unload_plugin",
        "list_loaded_plugins",
    ]
)
