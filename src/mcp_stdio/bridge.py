"""Tool allowlist for the Sidera MCP stdio server.

Defines which of Sidera's 72 internal tools are exposed directly to
Claude Code via the MCP protocol.  Only **stateless** tools that need no
``contextvars`` setup are included — write tools (which require approval
IDs) and context-dependent tools (memory, delegation, messaging, actions,
evolution, meeting) are excluded.

Write operations are accessed indirectly via the ``talk_to_role`` meta-tool,
which sets up the proper context before running an agent turn.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Direct tools — stateless, pass-through to ToolRegistry.dispatch()
# ---------------------------------------------------------------------------

DIRECT_TOOLS: frozenset[str] = frozenset(
    [
        # Google Ads (read-only) — 5
        "list_google_ads_accounts",
        "get_google_ads_campaigns",
        "get_google_ads_performance",
        "get_google_ads_changes",
        "get_google_ads_recommendations",
        # Meta (read-only) — 5
        "list_meta_ad_accounts",
        "get_meta_campaigns",
        "get_meta_performance",
        "get_meta_audience_insights",
        "get_meta_account_activity",
        # BigQuery — 5
        "discover_bigquery_tables",
        "get_business_goals",
        "get_backend_performance",
        "get_budget_pacing",
        "get_campaign_attribution",
        # Google Drive — 8
        "search_google_drive",
        "get_drive_file_info",
        "manage_drive_folders",
        "create_google_doc",
        "read_google_doc",
        "edit_google_doc",
        "manage_google_sheets",
        "manage_google_slides",
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
        # SSH remote server — 6
        "run_remote_command",
        "read_remote_file",
        "list_remote_directory",
        "get_remote_system_info",
        "list_remote_processes",
        "tail_remote_log",
        # Computer Use — 3
        "run_computer_use_task",
        "get_computer_use_session",
        "stop_computer_use_session",
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

WRITE_TOOLS: frozenset[str] = frozenset(
    [
        "update_google_ads_campaign",
        "update_google_ads_keywords",
        "update_meta_campaign",
        "update_meta_ad",
    ]
)

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
        # Meeting (needs active session)
        "get_meeting_transcript",
        "get_meeting_participants",
        "end_meeting_participation",
    ]
)

# Headless context tools — subset of CONTEXT_DEPENDENT_TOOLS that are safe
# for headless Claude Code task execution when contextvars are set up.
# Excludes delegation, meeting, and context loading tools.
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
