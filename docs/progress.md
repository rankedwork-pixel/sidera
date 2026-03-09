# Sidera — Build Progress Log

## Session 33 — Feb 15, 2026

### Summary
**Full project cleanup** — Comprehensive audit of all files, documentation, configuration, and dependencies. Updated all stale counts and references across CLAUDE.md, MEMORY.md, .env.example, and pyproject.toml to reflect current state (2354 tests, 12 workflows, 12 migrations, 82 DB methods, 45 MCP tools, 8 connectors).

### What Was Done
- **CLAUDE.md** — Updated all stale counts (tests 2162→2354, workflows 9→12, migrations 10→12, DB methods 57→82, Slack methods 10→16), added voice meeting feature as built (moved from Potential Features), added meetings module to file structure, added RBAC/auth middleware mention, added voice meeting architecture decision
- **MEMORY.md** — Same count updates, added voice meeting architecture pattern, updated Inngest workflow list, updated tool count (37→45), added meeting slash commands
- **.env.example** — Added Recall.ai, Deepgram, ElevenLabs API key sections + meeting settings (duration, cost cap, proactivity, chunk interval)
- **pyproject.toml** — Added `deepgram-sdk>=3.0.0` and `elevenlabs>=1.0.0` to dependencies
- **docs/progress.md** — Added session 31-33 entries

---

## Session 31-32 — Feb 15, 2026

### Summary
**Voice-enabled department heads (7-phase build)** — Manager roles can now join live video calls as voice participants via Recall.ai + Deepgram + ElevenLabs. Real-time audio pipeline: bot joins meeting → STT transcription → Claude processes context → TTS voice output. Post-call: transcript summary → action item extraction → manager delegation pipeline.

### What Was Built
- **Phase 1: Connectors** — `src/connectors/recall_ai.py` (meeting bot lifecycle via httpx), `src/connectors/deepgram.py` (Nova-3 real-time STT via WebSocket), `src/connectors/elevenlabs.py` (Flash v2.5 TTS, per-role voice_id)
- **Phase 2: DB schema** — `meeting_sessions` table (Alembic migration 012), `voice_id` field on `RoleDefinition`, 7 CRUD methods in db/service.py
- **Phase 3: Meeting session manager** — `src/meetings/session.py` MeetingSessionManager singleton, `MeetingContext` dataclass, async tasks for STT + periodic agent processing
- **Phase 4: MCP tools** — `src/mcp_servers/meeting.py` with 4 tools: `get_meeting_transcript`, `get_meeting_participants`, `speak_in_meeting`, `end_meeting_participation`
- **Phase 5: Meeting prompts** — `src/meetings/prompts.py` with `MEETING_SYSTEM_PROMPT` + `MEETING_SUMMARY_PROMPT`
- **Phase 6: Inngest workflows** — `meeting_join_workflow` (`sidera/meeting.join`), `meeting_end_workflow` (`sidera/meeting.ended`), modified `manager_runner_workflow` to accept `meeting_context` for post-call delegation
- **Phase 7: Slack integration** — `/sidera meeting join|status|leave` subcommands, `send_meeting_notification()` on SlackConnector

### Files Created (10)
- `src/connectors/recall_ai.py`, `src/connectors/deepgram.py`, `src/connectors/elevenlabs.py`
- `src/meetings/__init__.py`, `src/meetings/session.py`, `src/meetings/prompts.py`
- `src/mcp_servers/meeting.py`
- `alembic/versions/012_add_meeting_sessions.py`
- `tests/test_workflows/test_meeting_workflows.py` (11 tests)
- `tests/test_api/test_slack_meeting.py` (17 tests)

### Files Modified (8)
- `src/models/schema.py` — MeetingSession model
- `src/skills/schema.py` — voice_id field on RoleDefinition
- `src/db/service.py` — 7 meeting session CRUD methods
- `src/agent/core.py` — Meeting tools wired in
- `src/connectors/slack.py` — send_meeting_notification()
- `src/api/routes/slack.py` — /sidera meeting commands
- `src/workflows/daily_briefing.py` — 2 new workflows + meeting_context in manager_runner

### Test Summary
- **Before:** 2162 tests
- **After:** 2354 tests (+192 new)
- **All new tests passing**, ruff lint clean

---

## Session 30 — Feb 14, 2026

### Summary
**IT Department + Self-Healing Agent + Protobuf Fix** — Built a second department (IT & Operations) with a head_of_it role (formerly sysadmin) that can diagnose and fix system issues via Slack conversation. Created 7 system introspection MCP tools. Fixed critical protobuf `_pb` AttributeError that was blocking all write operation execution.

### What Was Built
- **System MCP tools** (`src/mcp_servers/system.py`) — 7 new tools for agent self-introspection: `get_system_health`, `get_failed_runs`, `resolve_failed_run`, `get_recent_audit_events`, `get_approval_queue_status`, `get_conversation_status`, `get_cost_summary`
- **IT Department** (`src/skills/library/it/`) — Second department alongside marketing. Contains `head_of_it` role with 3 skills: system_health_check (Sonnet, 8 turns), error_diagnosis (Sonnet, 12 turns), cost_monitoring (Haiku, 5 turns)
- **Protobuf `_pb` fix** (`src/connectors/google_ads.py`) — Newer google-ads library + protobuf 6.33.5 returns raw protobuf messages from `client.get_type()` instead of proto-plus wrappers. Added `_unwrap_pb()` helper: `obj._pb if hasattr(obj, "_pb") else obj`. Fixed 4 occurrences in budget update, campaign status, bid strategy, and geo bid modifier methods.
- **25 new tests** (`tests/test_mcp_servers/test_system_tools.py`) — Coverage for all 7 system tools

### E2E Testing Results
- Head of IT role reachable via `/sidera chat head_of_it` or `@Sidera talk to the head of IT`
- Media buyer correctly stayed in its lane when asked about IT topics (one-role-per-thread design)
- Head of IT diagnosed Redis connection issues and stuck approvals from system introspection tools
- Approval → execution flow was blocked by `_pb` error — now fixed, awaiting re-test

### Files Created (6)
- `src/mcp_servers/system.py` — 7 system introspection MCP tools (~600 lines)
- `src/skills/library/it/_department.yaml` — IT & Operations department definition
- `src/skills/library/it/head_of_it/_role.yaml` — Head of IT role
- `src/skills/library/it/head_of_it/system_health_check.yaml` — Infrastructure health audit skill
- `src/skills/library/it/head_of_it/error_diagnosis.yaml` — Error investigation skill
- `src/skills/library/it/head_of_it/cost_monitoring.yaml` — LLM cost monitoring skill
- `tests/test_mcp_servers/test_system_tools.py` — 25 tests

### Files Modified (7)
- `src/agent/core.py` — Added system tools import
- `src/agent/prompts.py` — Added SYSTEM_TOOLS to ALL_TOOLS
- `src/skills/schema.py` — Added "operations" to VALID_CATEGORIES
- `src/connectors/google_ads.py` — Added `_unwrap_pb()` helper, fixed 4 `._pb` calls
- `tests/test_skills/test_library_hierarchy.py` — Updated counts (2 depts, 5 roles, 19 skills)
- `tests/test_skills/test_skill_library.py` — Updated counts + expected IDs
- `tests/test_skills/test_folder_skills.py` — Updated count

### Test Summary
- **Before:** 2069 tests
- **After:** 2162 tests (+93 new, including RBAC + system tools)
- 2162 passed, 7 pre-existing failures

---

## Session 29 — Feb 14, 2026

### Summary
**Conversation-mode write operations** — agents can now propose budget changes, enable/pause campaigns, and other write ops directly from Slack thread conversations, with Approve/Reject buttons appearing in-thread. On approval, actions execute inline and results post back to the thread.

### What Was Done
- **Prompt update** — Replaced CONVERSATION_SUPPLEMENT rule 7 ("No approval-gated actions") with structured recommendation format. Agent now emits JSON `{"recommendations": [...]}` blocks when user requests changes.
- **Thread-scoped approval buttons** — Added `thread_ts` parameter to `SlackConnector.send_approval_request()` so buttons appear inside the conversation thread, not as top-level channel messages.
- **Recommendation extraction** — `_extract_recommendations()` parses JSON blocks from agent response, strips them from visible text. `_process_recommendations_inline()` creates DB approval items and posts buttons to thread.
- **Inline execution on approval** — `_execute_approved_action_inline()` runs when Approve is clicked in a thread: loads action from DB, routes to connector via `_execute_action()`, posts result back to thread.
- **Execution router** — Added `create_campaign` action type to `_execute_action()`. Fixed `campaign_id` KeyError (`.get()` instead of `["campaign_id"]`).

### Files Modified (4)
- `src/agent/prompts.py` — Rule 7 rewrite
- `src/connectors/slack.py` — `thread_ts` on `send_approval_request()`
- `src/api/routes/slack.py` — `_extract_recommendations()`, `_process_recommendations_inline()`, `_execute_approved_action_inline()`, wired into inline runner + approval handler
- `src/workflows/daily_briefing.py` — `create_campaign` route + `campaign_id` fix

---

## Session 28 — Feb 14, 2026

### Summary
**E2E testing with real API keys** — connected Google Ads (test account), Slack, and Anthropic API. First live conversation between a human and the AI marketing team via Slack threads.

### What Was Done
- **Google Ads live connection** — API v23, test MCC → client account. Created 3 campaigns, ad groups, RSAs, keywords, extensions via `create_campaign` tool.
- **Slack bot setup** — Configured Slack app with Event Subscriptions, Interactivity, slash commands. ngrok tunnel for local dev.
- **Slash command fix** — Dynamic `{cmd}` variable replaced ~50 hardcoded `/sidera` references in user-facing strings.
- **Env var fix** — Empty shell `ANTHROPIC_API_KEY=""` overriding `.env` value. Added `_load_dotenv_overrides()` in `src/config.py` to pre-load .env for blank shell vars.
- **Inngest import fix** — `inngest.serve()` → `inngest.fast_api.serve`. Added `is_production` flag, default `event_key` for dev mode.
- **Inline conversation runner** — Built `_run_conversation_turn_inline()` as dev-mode fallback when Inngest isn't running. `_dispatch_or_run_inline()` tries Inngest first, falls back to inline.
- **MCC hierarchy fix** — Agent was querying MCC instead of client account. Added `get_child_accounts()` method. Updated `list_google_ads_accounts` MCP tool to show hierarchy with "USE THIS for campaigns" hint.
- **Private channel fix** — `message.groups` event subscription needed for private Slack channels.
- **First live conversation** — Agent responded in-character as reporting_analyst, called Google Ads and Meta tools, found campaigns, respected approval gates.

### Files Modified (6)
- `src/api/routes/slack.py` — Dual command decorator, cosmetic fixes, inline runner, dispatch helper
- `src/config.py` — `_load_dotenv_overrides()`
- `src/api/app.py` — `inngest.fast_api.serve` import fix
- `src/workflows/inngest_client.py` — `is_production`, default event_key
- `src/connectors/google_ads.py` — `get_child_accounts()`
- `src/mcp_servers/google_ads.py` — Account hierarchy display

### Test Account Structure
```
Test MCC: XXX-XXX-XXXX ← LOGIN_CUSTOMER_ID
  └── Client: XXX-XXX-XXXX ("Test Client")
        ├── Brand Search — PAUSED, $15/day
        ├── Search Campaign — PAUSED, $20/day
        └── Display Campaign — PAUSED, $10/day
```

---

## Session 27 — Feb 14, 2026

### Summary
Implemented **Skill Evolution** — agents can now propose changes to their own skill definitions via MCP tool. Proposals route through the existing human approval flow with Slack diff view. Agents CANNOT modify safety-critical fields and skill proposals NEVER auto-execute.

### What Was Built
- **Phase 1: Schema + Safety Gate** — `SKILL_PROPOSAL` ActionType, nullable `account_id`/`analysis_id` on approval queue, Alembic migration, auto-execute hard block
- **Phase 2: Core Evolution Engine** (`src/skills/evolution.py`) — `validate_skill_proposal()`, `generate_skill_diff()`, `execute_skill_proposal()`, `format_proposal_as_recommendation()` with FORBIDDEN_FIELDS/ALLOWED_FIELDS safety controls
- **Phase 3: MCP Tool** (`src/mcp_servers/evolution.py`) — `propose_skill_change` tool with pending proposals collection pattern
- **Phase 4: Slack Diff View** — `diff_text` parameter on `send_approval_request()`, before/after code block in Slack messages
- **Phase 5: Execution Pipeline Wiring** — `_execute_action()` routing for skill proposals, proposal collection in role_runner and manager_runner workflows, evolution MCP server wired into agent core
- **Phase 6: Tests** — 89 new tests across 4 test files

### Files Created (7)
- `alembic/versions/010_skill_evolution.py` — Migration
- `src/skills/evolution.py` — Core evolution engine (~300 lines)
- `src/mcp_servers/evolution.py` — MCP tool module (~200 lines)
- `tests/test_skills/test_evolution.py` — 60 tests
- `tests/test_mcp_servers/test_evolution_mcp.py` — 15 tests
- `tests/test_connectors/test_slack_diff.py` — 6 tests
- `tests/test_workflows/test_skill_proposal_execution.py` — 8 tests

### Files Modified (6)
- `src/models/schema.py` — SKILL_PROPOSAL ActionType, nullable account_id/analysis_id
- `src/skills/auto_execute.py` — Hard block for skill proposals
- `src/connectors/slack.py` — diff_text param on send_approval_request()
- `src/workflows/approval_flow.py` — Pass diff to Slack for skill proposals
- `src/workflows/daily_briefing.py` — skill_proposal routing in _execute_action(), proposal collection in role_runner + manager_runner
- `src/agent/core.py` — Wire evolution MCP server into agent tools

### Test Summary
- **Before:** 1980 tests
- **After:** 2069 tests (+89 new)
- **All passing**, ruff lint clean

### Safety Design
- FORBIDDEN_FIELDS: `requires_approval`, `manages`, `is_active`, `created_by`, `id`, `skill_id`
- Hard block in `should_auto_execute()` — skill proposals always require human review
- Human reviews diff in Slack before approving
- Agents can suggest, humans decide

---

## Session 25 — Feb 14, 2026

### Summary
Implemented **Dynamic Org Chart** — the Department → Role → Skill hierarchy is now manageable at runtime via PostgreSQL, Slack commands, and REST API. DB entries override or extend disk YAML. No code changes or restarts needed to reshape the AI workforce. 7-phase build: DB tables → CRUD methods → schema/registry merge → Slack `/sidera org` commands → 16 REST endpoints → wire callers → docs.

### What Was Done

- **Phase 1 — DB Tables + Models:**
  - Created `alembic/versions/009_add_org_chart_tables.py` migration — 3 new tables: `org_departments`, `org_roles`, `org_skills`
  - Added `OrgDepartment`, `OrgRole`, `OrgSkill` SQLAlchemy models to `src/models/schema.py`
  - Each table has `is_active` (soft delete), `created_by`, `created_at`, `updated_at`

- **Phase 2 — DB Service (15 CRUD methods):**
  - Added 15 async CRUD methods to `src/db/service.py`: create/update/delete/get/list × 3 entity types
  - Plus `_log_org_chart_change()` private helper for audit trail
  - All mutations logged to `audit_log` with `event_type="org_chart_change"`
  - Soft delete (is_active=False), active-only filtering, duplicate ID prevention

- **Phase 3 — Schema + Registry Merge:**
  - Added `context_text: str = ""` field to `DepartmentDefinition`, `RoleDefinition`, `SkillDefinition` in `src/skills/schema.py`
  - Updated `load_context_text()` and `load_hierarchy_context_text()` to return `context_text` directly when non-empty (DB entries skip filesystem)
  - Added `merge_db_definitions()` to `SkillRegistry` — overlays DB definitions onto disk-loaded registry (DB wins on ID conflicts)
  - Internal `_sources` dict tracks origin (`"disk"` or `"db"`) per entity
  - Created `src/skills/db_loader.py` — `load_registry_with_db()` loads disk first, overlays DB, falls back silently if DB unavailable

- **Phase 4 — Slack Commands (`/sidera org`):**
  - Added `_handle_org_command()` to `src/api/routes/slack.py`
  - 8 subcommands: `list`, `show <type> <id>`, `add-dept`, `add-role`, `add-skill`, `update <type> <id> <field> <value>`, `remove <type> <id>`, `history <type> <id>`
  - Updated `/sidera help` to include `org` commands

- **Phase 5 — API Routes (16 REST endpoints):**
  - Created `src/api/routes/org_chart.py` — full RESTful CRUD for departments, roles, skills
  - GET/POST/PUT/DELETE × 3 entity types + GET audit history = 16 endpoints
  - Pydantic request/response models, 404 handling, validation
  - Wired into `src/api/app.py`

- **Phase 6 — Wire Callers:**
  - Replaced all `SkillRegistry(); registry.load_all()` with `await load_registry_with_db()` in:
    - `src/api/routes/slack.py` — 9 occurrences
    - `src/workflows/daily_briefing.py` — 4 workflow functions
    - `src/workflows/approval_flow.py` — 1 occurrence
  - Updated ~50 test mock sites across 8 test files from `patch("src.skills.registry.SkillRegistry")` to `patch("src.skills.db_loader.load_registry_with_db", new_callable=AsyncMock)`

- **Phase 7 — Tests:**
  - 83 new tests: `test_org_chart_service.py` (CRUD + audit + soft delete), `test_registry_db_merge.py` (DB overrides disk, adds new, inactive skipped, validation, manager refs), `test_db_loader.py` (integration + fallback)
  - Fixed `all_workflows` count assertions: 8 → 9 (conversation_turn_workflow)
  - Fixed scheduler mock: `__len__` return value for `len(registry)` pattern

### Files Changed

**New files (6):**
- `alembic/versions/009_add_org_chart_tables.py`
- `src/skills/db_loader.py`
- `src/api/routes/org_chart.py`
- `tests/test_db/test_org_chart_service.py`
- `tests/test_skills/test_registry_db_merge.py`
- `tests/test_skills/test_db_loader.py`

**Modified files (13):**
- `src/models/schema.py` — 3 new SQLAlchemy models
- `src/db/service.py` — 15 new CRUD methods + audit helper
- `src/skills/schema.py` — `context_text` field on 3 dataclasses
- `src/skills/registry.py` — `merge_db_definitions()` + `_sources` tracking
- `src/api/routes/slack.py` — `/sidera org` commands + `load_registry_with_db()` swap
- `src/api/app.py` — org_chart router
- `src/workflows/daily_briefing.py` — `load_registry_with_db()` swap
- `src/workflows/approval_flow.py` — `load_registry_with_db()` swap
- `tests/test_api/test_slack_hierarchy.py` — mock migration
- `tests/test_api/test_slack_manager.py` — mock migration
- `tests/test_workflows/test_approval_flow.py` — mock migration
- `tests/test_workflows/test_skill_workflows.py` — mock migration
- `tests/test_workflows/test_hierarchy_workflows.py` — mock migration + count fix

### Test Summary
- **Before:** 1880 tests
- **After:** 1963 tests (+83 new)
- **All passing**, ruff lint clean

### Architecture Note
Dynamic Org Chart merge strategy: YAML on disk is the baseline/seed data. DB entries with the same ID **replace** disk entries entirely. DB entries with new IDs are **added**. Inactive (soft-deleted) entries not loaded into registry. DB unavailable → disk-only fallback (silent, logged warning). The `load_registry_with_db()` function is now the standard entry point for all callers (workflows, Slack routes, API). Plain `SkillRegistry().load_all()` still works for disk-only mode (tests, offline use).

### Next Session Should Start With
1. **E2E testing** with real API keys
2. **Deploy to Railway**
3. **Add more YAML skills** — scale toward 50-100+

## Session 24 — Feb 14, 2026

### Summary
Added **Conversational Mode** — every role is now both autonomous (scheduled briefings) AND conversational (Slack thread back-and-forth). Users can `@Sidera talk to the media buyer about yesterday's spend spike` and get a threaded conversation with the media buyer role, in-character, with full tool access. Each thread is pinned to one role, stateless per-turn (thread history from Slack API), with safety limits (20 turns, 24h timeout, $5/thread cost cap).

### What Was Done

- **Phase 1 — Database (`ConversationThread` table):**
  - Added `ConversationThread` model to `src/models/schema.py` — columns: `id`, `thread_ts` (unique, indexed), `channel_id`, `role_id`, `user_id`, `started_at`, `last_activity_at`, `turn_count`, `is_active`, `total_cost_usd` + 3 composite indexes
  - Created `alembic/versions/008_add_conversation_threads.py` migration (revision `conv_threads_001`)
  - Added 4 CRUD methods to `src/db/service.py`: `create_conversation_thread()`, `get_conversation_thread()`, `update_conversation_thread_activity()`, `deactivate_stale_threads()`

- **Phase 2 — SlackConnector Thread Methods:**
  - Added 4 new methods to `src/connectors/slack.py`:
    - `send_thread_reply(channel_id, thread_ts, text, blocks)` — with `@retry_with_backoff`
    - `get_thread_history(channel_id, thread_ts, limit=50)` — with `@retry_with_backoff`
    - `add_reaction(channel_id, timestamp, name="eyes")` — swallows errors
    - `remove_reaction(channel_id, timestamp, name="eyes")` — swallows errors

- **Phase 3 — Conversation Prompts:**
  - Added `CONVERSATION_SUPPLEMENT` to `src/agent/prompts.py` — 8 rules: stay in character, be conversational (100-300 words), use tools proactively, reference thread history, ask clarifying questions, stay in lane, no approval-gated actions
  - Added `build_conversation_prompt(thread_history, current_message, bot_user_id)` — formats thread as chronological `[You]:`/`[<@user>]:` log

- **Phase 4 — Agent Core:**
  - Added `ConversationTurnResult` dataclass to `src/agent/core.py` (role_id, response_text, cost, session_id, turn_number)
  - Added `run_conversation_turn()` method — composes `BASE_SYSTEM_PROMPT + role_context + CONVERSATION_SUPPLEMENT`, builds conversation prompt from thread history, runs via `query()` with Sonnet and `conversation_tool_calls_per_turn` max turns

- **Phase 5 — RoleRouter:**
  - Created `src/skills/role_router.py` (377 lines) — two-tier role identification for conversations:
    - Tier 1: `_EXPLICIT_PATTERNS` — 8 regex patterns matching "talk to the strategist", "ask the media buyer", etc.
    - Tier 2: Haiku semantic matching via Anthropic API (same pattern as SkillRouter)
  - `RoleMatch` dataclass with role, confidence, reasoning
  - `RoleRouter` class with `route(message, available_roles)` and `route_by_id(role_id)` methods
  - Confidence threshold: 0.4

- **Phase 6 — Config Settings:**
  - Added 4 conversation settings to `src/config.py`:
    - `conversation_max_turns_per_thread: int = 20`
    - `conversation_thread_timeout_hours: int = 24`
    - `conversation_max_cost_per_thread: Decimal = Decimal("5.00")`
    - `conversation_tool_calls_per_turn: int = 10`

- **Phase 7 — Inngest Workflow (`conversation_turn_workflow`):**
  - Added `conversation_turn_workflow` to `src/workflows/daily_briefing.py` — triggered by `sidera/conversation.turn`, retries=1
  - 7 Inngest steps: `load-thread` → `check-limits` → `build-context` → `get-history` → `run-turn` → `post-reply` → `update-thread` + `log-audit`
  - Checks: turn count, cost cap, timeout, active status vs config limits
  - Builds role context from SkillRegistry (department + role + memory)
  - Error handling: posts error to Slack thread, records to DLQ, captures in Sentry
  - Added to `all_workflows` list (8 → 9)

- **Phase 8 — Slack Event Handlers:**
  - Added `handle_app_mention` event handler — strips bot mention, checks existing thread, routes via RoleRouter (regex + Haiku), dispatches `sidera/conversation.turn`, adds eyes reaction
  - Added `handle_thread_message` event handler — filters non-threads/bots/mentions, checks DB for known Sidera thread, dispatches turn
  - Added `/sidera chat <role_id> [message]` command handler — creates thread, dispatches turn or posts greeting
  - Updated help text with `chat` command

- **Phase 9 — MCP Tools:**
  - Added `send_slack_thread_reply` tool to `src/mcp_servers/slack.py` — requires channel_id, thread_ts, message; allows agent to post supplementary data within conversation threads
  - Updated `SLACK_TOOLS` list in `src/agent/prompts.py` (3 → 4 tools)

- **Phase 10 — Wire Up & Docs:**
  - Updated CLAUDE.md — conversational mode in Current Status, Architecture Decisions, File Structure, test/migration counts
  - Updated MEMORY.md — conversational mode patterns, conventions, architecture decision
  - Updated docs/progress.md — this session entry

### Files Created
- `src/skills/role_router.py` — RoleRouter (two-tier: regex + Haiku semantic)
- `alembic/versions/008_add_conversation_threads.py` — ConversationThread migration

### Files Modified
- `src/models/schema.py` — Added `ConversationThread` model
- `src/db/service.py` — Added 4 conversation thread CRUD methods (37 → 41)
- `src/connectors/slack.py` — Added 4 thread methods (6 → 10 methods)
- `src/agent/prompts.py` — Added `CONVERSATION_SUPPLEMENT`, `build_conversation_prompt()`, `send_slack_thread_reply` to SLACK_TOOLS
- `src/agent/core.py` — Added `ConversationTurnResult` dataclass + `run_conversation_turn()` method
- `src/config.py` — Added 4 conversation config settings
- `src/workflows/daily_briefing.py` — Added `conversation_turn_workflow` + all_workflows (8→9)
- `src/api/routes/slack.py` — Added `app_mention`, `message` handlers, `/sidera chat` command
- `src/mcp_servers/slack.py` — Added `send_slack_thread_reply` tool (3→4 tools)
- `CLAUDE.md` — Updated all counts and architecture docs
- `MEMORY.md` — Updated state and patterns

### Next Session Should Start With
1. Write tests for all 9 phases (~100 new tests as estimated in plan)
2. End-to-end testing with real API keys
3. Deploy to Railway

## Session 23 — Feb 13, 2026

### Summary
Added hierarchical manager roles — a manager is a special role (not a new layer) that can run its own skills, decide which sub-roles to activate via LLM delegation, wait for sub-roles to complete, and synthesize all outputs into a unified report. This enables a "Head of Marketing" to direct media buyers, analysts, and strategists as a team, producing a single unified briefing.

### What Was Done

- **Phase 1 — Schema Changes (5 tests):**
  - Added `manages`, `delegation_model`, `synthesis_prompt` fields to `RoleDefinition` in `src/skills/schema.py`
  - `manages: tuple[str, ...]` — sub-role IDs this manager directs
  - `delegation_model: str` — "standard" (Sonnet) or "fast" (Haiku)
  - `synthesis_prompt: str` — custom synthesis instructions
  - Created `tests/test_skills/test_schema_manager.py`

- **Phase 2 — Registry Changes (22 tests):**
  - Added `is_manager()`, `get_managed_roles()`, `list_managers()` methods to `SkillRegistry`
  - Added `_validate_manager_references()` cross-validation in `load_all()` — warns on missing, self-referencing, or circular manager chains
  - Depth limit detection (max 3 levels of nesting)
  - Created `tests/test_skills/test_registry_manager.py`

- **Phase 3+7 — ManagerExecutor + Memory (~35 tests):**
  - Created `src/skills/manager.py` — `ManagerExecutor` with four-phase pipeline:
    1. Own skills — run manager's `briefing_skills` via `RoleExecutor`
    2. Delegation decision — single LLM call (Sonnet/Haiku), returns JSON `{activate, skip}`, fallback activates ALL on failure
    3. Sub-role execution — each activated sub-role via `RoleExecutor`, errors captured per-role, others continue
    4. Synthesis — single LLM call (Sonnet), merges all results into unified output
  - Memory: loaded before execution via `compose_memory_context()`, saved after via `extract_memories_from_results()`
  - `ManagerResult` dataclass, `ManagerRoleNotFoundError` and `NotAManagerError` exceptions
  - Helper functions: `_format_own_results_summary`, `_format_sub_role_results`, `_parse_delegation_decision`, `_resolve_delegation_model`, `_merge_phase_cost`
  - Created `tests/test_skills/test_manager.py`

- **Phase 4 — Manager Workflow (~16 tests):**
  - Added `manager_runner_workflow` to `src/workflows/daily_briefing.py` — triggered by `sidera/manager.run`
  - Inngest durable function with checkpointed steps: load-manager → load-accounts → run-own-skills → delegation-decision → run-sub-role-{id} → synthesis → store-results → send-briefing → process-recommendations → save-memory
  - Sub-roles run inline (not separate Inngest events) so results stay in scope for synthesis
  - Recommendations collected from own skills + all sub-roles, processed through shared approval pipeline
  - DLQ recording and Sentry capture on unhandled exceptions
  - `all_workflows` list updated (7 → 8)
  - Created `tests/test_workflows/test_manager_workflow.py`

- **Phase 5 — Agent Core (10 tests):**
  - Added `run_delegation_decision()` and `run_synthesis()` to `SideraAgent` in `src/agent/core.py`
  - Both use existing `_query_model` pattern (no tools, max_turns=1)
  - `_fallback_activate_all()` helper for delegation parse failures
  - Added `DELEGATION_DECISION_PROMPT` and `SYNTHESIS_PROMPT` templates to `src/agent/prompts.py`
  - Created `tests/test_agent/test_manager_agent.py`

- **Phase 6 — Slack Integration (8 tests):**
  - Updated `/sidera` slash command in `src/api/routes/slack.py`:
    - `run manager:<role_id>` — validates role is a manager, emits `sidera/manager.run`
    - `run role:<role_id>` — auto-redirects to `sidera/manager.run` if role is a manager
    - `list` — includes "Managers" section in output
  - Created `tests/test_api/test_slack_manager.py`

- **Phase 8 — Example YAML (3 tests):**
  - Created `src/skills/library/marketing/head_of_marketing/` with:
    - `_role.yaml` — manages: [performance_media_buyer, reporting_analyst, strategist]
    - `executive_summary.yaml` — the manager's own skill for high-level analysis
  - Created `tests/test_skills/test_manager_yaml.py`

- **Phase 9 — DepartmentExecutor Update (12 tests):**
  - Updated `DepartmentExecutor.execute_department()` in `src/skills/executor.py`:
    - Identifies managers, runs them via `ManagerExecutor`
    - Collects managed role IDs, skips from regular execution (no double-execution)
    - Backward compatible: if no `ManagerExecutor` available, all roles run normally
  - Created `tests/test_skills/test_department_manager.py`

- **Phase 10 — Documentation:**
  - Updated CLAUDE.md — manager roles in Current Status, Architecture Decisions, File Structure, test count (1673 → 1880)
  - Updated MEMORY.md — manager patterns, conventions, cost estimate
  - Updated README.md — manager hierarchy in features, test count
  - Updated docs/progress.md — this session entry

### Test Count
1880 tests (was 1673), all passing, ruff lint clean. +207 new tests across 7 new test files.

### Files Created
- `src/skills/manager.py` — ManagerExecutor (four-phase pipeline)
- `src/skills/library/marketing/head_of_marketing/_role.yaml` — Example manager role
- `src/skills/library/marketing/head_of_marketing/executive_summary.yaml` — Manager's own skill
- `tests/test_skills/test_schema_manager.py` (~5 tests)
- `tests/test_skills/test_registry_manager.py` (~22 tests)
- `tests/test_skills/test_manager.py` (~35 tests)
- `tests/test_skills/test_department_manager.py` (~12 tests)
- `tests/test_skills/test_manager_yaml.py` (~3 tests)
- `tests/test_agent/test_manager_agent.py` (~10 tests)
- `tests/test_api/test_slack_manager.py` (~8 tests)
- `tests/test_workflows/test_manager_workflow.py` (~16 tests)

### Files Modified
- `src/skills/schema.py` — manages, delegation_model, synthesis_prompt fields on RoleDefinition
- `src/skills/registry.py` — is_manager, get_managed_roles, list_managers, cross-validation
- `src/skills/executor.py` — DepartmentExecutor routes managers via ManagerExecutor
- `src/agent/core.py` — run_delegation_decision(), run_synthesis()
- `src/agent/prompts.py` — DELEGATION_DECISION_PROMPT, SYNTHESIS_PROMPT
- `src/workflows/daily_briefing.py` — manager_runner_workflow + all_workflows (7→8)
- `src/api/routes/slack.py` — run manager:X, auto-redirect, list managers section
- `tests/test_workflows/test_daily_briefing.py` — Updated all_workflows count (7→8)
- `CLAUDE.md` — Manager roles documentation
- `docs/progress.md` — This entry

### Architecture Notes
- A manager is just a `RoleDefinition` with a `manages` field — no new DB tables, no new abstractions
- Four-phase execution: own skills → delegation (single LLM call) → sub-role execution → synthesis (single LLM call)
- Delegation model: Sonnet by default ("standard"), Haiku option ("fast"), no tools, max_turns=1
- Delegation fallback: if LLM call fails → activate ALL sub-roles (safe default)
- Sub-roles run inline in the Inngest workflow (not separate events) so synthesis has access to all results
- Each sub-role step is checkpointed (`ctx.step.run`) for durability
- Recursive managers supported (manager manages another manager) with depth limit (max 3)
- Backward compatible: roles without `manages` field are unchanged, DepartmentExecutor works as before
- Cost estimate: Head of Marketing with 3 sub-roles ~$1.84 total (~$0.28 overhead vs running 3 roles independently)

---

## Session 22 — Feb 13, 2026

### Summary
Added graduated trust / auto-execute rules for write operations. The agent can now auto-execute routine writes that match pre-defined YAML policies (e.g., "pause ads with ROAS < 0.5x") without waiting for human approval. Three-tier trust model: Tier 1 (read-only), Tier 2 (auto-execute with guardrails, NEW), Tier 3 (human approval, existing). Also fixed two existing gaps: approval queue items now created in DB before Slack messages, and Inngest events now emitted from Slack approval handlers.

### What Was Done

- **Phase 1 — Rule Engine + Foundation (~55 tests):**
  - Created `src/skills/auto_execute.py` — rule engine with dataclasses (RuleCondition, RuleConstraints, AutoExecuteRule, AutoExecuteRuleSet, AutoExecuteDecision), YAML loader, validator, 10 operators (eq/ne/gt/gte/lt/lte/in/not_in/contains/regex), condition evaluator, `should_auto_execute()` decision function, budget cap safety check
  - Added `AUTO_APPROVED` to ApprovalStatus enum, `auto_execute_rule_id` column to ApprovalQueueItem
  - Alembic migration `007_add_auto_execute`
  - 3 new config settings: `auto_execute_enabled` (default False), `auto_execute_max_per_day` (20), `auto_execute_notify_channel`
  - 2 new DB methods: `count_auto_executions_today()`, `get_last_auto_execution_time()`
  - Updated `write_safety.verify_and_load_approval()` to accept AUTO_APPROVED status
  - Updated SkillRegistry to load `_rules.yaml` files from role directories

- **Phase 2 — Gap Fixes (14 tests):**
  - Modified `src/api/routes/slack.py` — `handle_approve` and `handle_reject` now emit `sidera/approval.decided` Inngest events (with graceful degradation)
  - Approval queue items created in DB via `process_recommendations()` helper

- **Phase 3 — Workflow Integration (~25 tests):**
  - Created `src/workflows/approval_flow.py` — shared `process_recommendations()` helper replacing triplicated approval logic
  - For each recommendation: create DB approval → evaluate auto-execute rules → auto path (execute + notify) or manual path (Slack request → wait for event → execute)
  - Returns summary dict with counts: auto_executed, sent_for_approval, approved, rejected, expired, executed, failed, errors

- **Phase 4 — Slack Notification + Docs (~10 tests):**
  - Added `send_auto_execute_notification()` to Slack connector — Block Kit notification (no buttons, informational only)
  - Created example `_rules.yaml` for performance_media_buyer role (4 rules: pause_low_roas_ads, pause_high_cpa_ads, add_obvious_negatives, small_budget_increase)
  - Updated CLAUDE.md, progress.md, MEMORY.md

### Test Count
1600+ tests (was 1505), all passing, ruff lint clean

### Files Created
- `src/skills/auto_execute.py` — Rule engine for graduated trust
- `src/workflows/approval_flow.py` — Shared approval flow helper
- `alembic/versions/007_add_auto_execute.py` — Migration for auto-execute support
- `src/skills/library/marketing/performance_media_buyer/_rules.yaml` — Example auto-execute rules
- `tests/test_skills/test_auto_execute.py` (~40 tests)
- `tests/test_db/test_auto_execute_queries.py` (~12 tests)
- `tests/test_skills/test_registry_rules.py` (~5 tests)
- `tests/test_api/test_slack_inngest_event.py` (14 tests)
- `tests/test_workflows/test_approval_flow.py` (~25 tests)
- `tests/test_connectors/test_slack_auto_notify.py` (~5 tests)
- `tests/test_skills/test_rules_library.py` (~5 tests)

### Files Modified
- `src/models/schema.py` — AUTO_APPROVED enum value + auto_execute_rule_id column
- `src/config.py` — 3 new auto-execute settings
- `src/db/service.py` — 2 new auto-execute query methods (35 → 37 total)
- `src/skills/registry.py` — Rules loading from `_rules.yaml` + get_rules() + list_rulesets()
- `src/mcp_servers/write_safety.py` — Accept AUTO_APPROVED status
- `src/connectors/slack.py` — send_auto_execute_notification method
- `src/api/routes/slack.py` — Inngest event emission from approve/reject handlers
- `CLAUDE.md` — Updated with graduated trust feature
- `docs/progress.md` — This entry

### Architecture Notes
- Three-tier trust: Tier 1 (read-only) → Tier 2 (auto-execute with rules, NEW) → Tier 3 (human approval)
- Global kill switch: `auto_execute_enabled` defaults to False — zero behavior change unless explicitly enabled
- Rules per role: `_rules.yaml` alongside `_role.yaml` in the hierarchy
- Rule evaluation: first-match-wins (YAML order), AND logic for conditions, 10 operators
- Constraints: daily limits per rule, cooldowns, platform restrictions, global daily cap
- Budget cap safety: auto-execute blocked if recommendation exceeds `max_budget_change_ratio`
- Inngest gap fix: approval handlers now emit `sidera/approval.decided` so workflows unblock immediately instead of timing out after 24h

---

## Session 21 — Feb 13, 2026

### Summary
Added persistent role memory — each AI employee (role) now accumulates structured memories across runs. Before each execution, relevant memories are loaded into the prompt context. After execution, new memories are extracted from the results. No LLM call required for extraction (v1 uses structured data + keyword detection).

### What Was Done

- **Phase 1 — Schema + DB Service (36 tests):**
  - Added `MemoryType` enum (decision, anomaly, pattern, insight) to schema.py
  - Added `RoleMemory` ORM model with 15 columns + 3 indexes
  - Alembic migration `006_add_role_memory` creating `role_memory` table
  - 6 new CRUD methods in db/service.py: `save_memory`, `get_role_memories`, `archive_expired_memories`, `update_memory_confidence`, `delete_memory`, `get_memory_by_id`

- **Phase 2 — Memory Extraction + Injection (35 tests):**
  - Created `src/skills/memory.py` — extraction and injection module
  - `extract_memories_from_results()` — pulls decision memories from approval outcomes + anomaly memories from keyword detection in output text
  - `compose_memory_context()` — formats memories for prompt injection, groups by type, respects 2000-token budget
  - No LLM call needed (v1 — structured extraction only)

- **Phase 3 — Wiring into Executor + Workflow (16 tests):**
  - Modified `compose_role_context()` to accept `memory_context` parameter
  - Modified `RoleExecutor.execute_role()` to pass memory through
  - Added `load-role-memory` step to `role_runner_workflow` (before execution)
  - Added `extract-and-save-memories` step to `role_runner_workflow` (after execution)
  - Both steps have graceful degradation (try/except, memory failures don't block runs)

- **Phase 4 — Slack + Docs (5 tests):**
  - Added `/sidera memory <role_id>` command showing last 5 memories
  - Updated CLAUDE.md, progress.md, MEMORY.md

### Test Count
1505 tests (was 1317+), all passing, ruff lint clean

### Files Created
- `src/skills/memory.py` — Memory extraction + injection module
- `alembic/versions/006_add_role_memory.py` — Migration for role_memory table
- `tests/test_db/test_memory_service.py` (36 tests)
- `tests/test_skills/test_memory.py` (35 tests)
- `tests/test_skills/test_memory_integration.py` (7 tests)
- `tests/test_workflows/test_memory_workflow.py` (9 tests)
- `tests/test_api/test_slack_memory.py` (5 tests)

### Files Modified
- `src/models/schema.py` — MemoryType enum + RoleMemory ORM model
- `src/db/service.py` — 6 new memory CRUD methods (29 → 35 total)
- `src/skills/executor.py` — memory_context parameter on compose_role_context + RoleExecutor.execute_role
- `src/workflows/daily_briefing.py` — load-role-memory + extract-and-save-memories steps
- `src/api/routes/slack.py` — /sidera memory command + updated help text
- `CLAUDE.md` — Updated with memory system, test count, DB method count
- `docs/progress.md` — This entry

### Architecture Notes
- Memory injection point in system prompt: after role persona/context_files, before skill context
- Token budget: 2000 tokens max per injection, sorted by confidence desc then recency desc
- Memory types v1: `decision` (from approval outcomes) + `anomaly` (keyword detection in output)
- Memory types v2 (future): `pattern` (repeated findings) + `insight` (LLM extraction)
- Memory lifecycle: 90-day TTL, confidence 0.0–1.0, soft archival via is_archived flag

---

## Session 20 — Feb 13, 2026

### Summary
Implemented Department -> Role -> Skill hierarchy. Skills are now organized into a three-level structure mirroring a company org chart: departments contain roles, roles contain skills. This enables running individual skills, entire roles (all skills for a job function), or full departments.

### What Was Done
- **Phase 1 — Schema + Discovery (69 tests):**
  - Added DepartmentDefinition and RoleDefinition frozen dataclasses to schema.py
  - Added YAML loaders, validators, and context file resolution for departments/roles
  - Rewrote SkillRegistry for three-level discovery (department -> role -> skill)
  - Added department_id/role_id fields to SkillDefinition (set by registry based on disk location)
  - New lookup methods: get_department, get_role, list_departments, list_roles, list_skills_for_role, list_skills_for_department

- **Phase 2 — Execution + Context (29 tests):**
  - Added role_context parameter to SideraAgent.run_skill() and SkillExecutor.execute()
  - Context inheritance: department.context -> role.persona -> skill.system_supplement
  - Added RoleExecutor (runs all briefing_skills sequentially, merges output)
  - Added DepartmentExecutor (runs all roles, merges into department report)
  - compose_role_context() builds combined context from department + role definitions

- **Phase 3 — Workflows + Slack (33 tests):**
  - Added role_runner_workflow (sidera/role.run event trigger)
  - Added department_runner_workflow (sidera/department.run event trigger)
  - Updated skill_scheduler_workflow to dispatch scheduled roles
  - Extended /sidera Slack commands: list departments, list roles, run role:X, run dept:X
  - all_workflows now exports 7 workflows (was 5)

- **Phase 4 — Database + Library reorganization:**
  - Added department_id and role_id columns to AnalysisResult and AuditLog
  - Alembic migration 005_add_hierarchy_columns with indexes
  - Added save_role_result, get_role_history, log_role_event service methods
  - Moved all 15 skills into marketing/ hierarchy on disk
  - Created _department.yaml for marketing, _role.yaml for 3 roles

### Test Count
1317+ tests (was 1284), all passing, ruff lint clean

### Files Created
- `src/skills/library/marketing/_department.yaml`
- `src/skills/library/marketing/performance_media_buyer/_role.yaml`
- `src/skills/library/marketing/reporting_analyst/_role.yaml`
- `src/skills/library/marketing/strategist/_role.yaml`
- `alembic/versions/005_add_hierarchy_columns.py`
- `tests/test_skills/test_hierarchy.py` (69 tests)
- `tests/test_skills/test_hierarchy_execution.py` (29 tests)
- `tests/test_workflows/test_hierarchy_workflows.py` (20 tests)
- `tests/test_api/test_slack_hierarchy.py` (13 tests)

### Files Modified
- `src/skills/schema.py` — DepartmentDefinition, RoleDefinition, loaders, validators
- `src/skills/registry.py` — three-level discovery rewrite
- `src/skills/executor.py` — RoleExecutor, DepartmentExecutor, compose_role_context
- `src/agent/core.py` — role_context parameter on run_skill
- `src/workflows/daily_briefing.py` — 2 new workflows + scheduler update + all_workflows
- `src/api/routes/slack.py` — hierarchy Slack commands
- `src/models/schema.py` — department_id/role_id columns
- `src/db/service.py` — 3 new service methods
- `docs/progress.md` — this entry

---

## Session 19 — Feb 13, 2026

### Summary
Rebranded Sidera from "Performance Marketing Agent" to "AI Agent Framework." The architecture was always general-purpose — this session updates all branding, documentation, and identity language to reflect that Sidera is a framework for building AI employees in any domain, with performance marketing as the first use case.

### What Was Done
- Rebranded README.md — new tagline ("Connect APIs. Teach skills. Approve actions. Automate any role."), framework-first description, "What You Can Build" table showing 5 example domains, "Adding a New Domain" section, reframed features as generic capabilities
- Rebranded CLAUDE.md — framework description, broadened target users, new design principles ("Domain-agnostic core", "Skills are the product"), updated file structure descriptions with "extensible" annotations
- Updated memory/MEMORY.md — project description reflects framework identity
- Updated src/agent/prompts.py — module docstring rewritten, domain comment added above BASE_SYSTEM_PROMPT marking it as the marketing domain config, DATA_COLLECTION_SYSTEM updated
- Updated src/api/app.py — FastAPI description changed to "AI Agent Framework API"
- Updated dashboard/app.py — sidebar caption, page title, module docstring all rebranded
- Updated pyproject.toml — project description
- Updated Dockerfile — header comment and OCI image labels
- Updated docker-compose.yml — service comment

### What Was NOT Changed
- The name "Sidera" — kept everywhere
- BASE_SYSTEM_PROMPT marketing content — still the active domain config
- All code logic — zero functional changes
- All tests — zero test changes (1146 still passing)
- Internal identifiers (service names, Redis keys, Slack command, postgres user)

### Files Modified
- `README.md` — full rewrite of identity sections
- `CLAUDE.md` — full rewrite of identity sections
- `memory/MEMORY.md` — project description
- `src/agent/prompts.py` — docstring + domain comment + DATA_COLLECTION_SYSTEM
- `src/api/app.py` — FastAPI description
- `dashboard/app.py` — docstring + sidebar + title
- `pyproject.toml` — description field
- `Dockerfile` — header + labels
- `docker-compose.yml` — service comment
- `docs/progress.md` — this entry

---

## Session 1 — Feb 13, 2026

### Summary
Full product strategy and architecture planned. Project foundation complete — all scaffolding files created and ready to build.

### What Was Done
- Researched market opportunity, pricing models, technical architecture, legal/compliance, and competitive landscape
- Designed full product blueprint (plan file: `.claude/plans/valiant-sparking-abelson.md`)
- Key decisions made:
  - Product: First-principles performance marketing analysis agent (not a platform rec auditor)
  - Highest-value feature: Cross-platform budget reallocation
  - Delivery: Daily Slack briefings with interactive approve/reject
  - Architecture: Inngest durable functions (not always-on process), checkpointed steps
  - Deploy: Railway, Database: Supabase, Cache: Upstash Redis
- Created full project directory structure
- Initialized git repo
- Created all foundation files (see list below)
- Set up session handoff system (CLAUDE.md, progress.md, memory files)

### Files Created
- `CLAUDE.md` — Project context for new sessions
- `.gitignore` — Python + secrets exclusions
- `pyproject.toml` — Dependencies: Claude Agent SDK, FastAPI, SQLAlchemy, Inngest, google-ads, facebook-business, slack-bolt, etc.
- `.env.example` — All required env vars documented
- `src/config.py` — Pydantic settings with model routing config, cost controls, all API keys
- `src/models/schema.py` — Full database schema: accounts, campaigns, daily_metrics, analysis_results, approval_queue, audit_log, cost_tracking
- `src/models/normalized.py` — Cross-platform metric normalization: Google Ads (micros, enum types) and Meta (string decimals, action arrays) → unified NormalizedMetrics dataclass
- `docs/progress.md` — This file
- All `__init__.py` files across the project tree
- `.claude/plans/valiant-sparking-abelson.md` — Full product blueprint

### Architecture Notes
- Database schema uses typed columns for core metrics + JSON for platform-specific data
- Google Ads money is in micros (÷ 1,000,000). Meta spend is string decimal.
- Meta conversions are in nested actions[] array, filtered by action_type
- All write operations gated by approval_queue
- audit_log captures every agent event with model used, approval status, timestamps

### Next Session Should Start With
1. **Google Ads connector** (`src/connectors/google_ads.py`) — OAuth2 flow, data pull for campaigns + metrics + recommendations
2. **Google Ads MCP server** (`src/mcp_servers/google_ads.py`) — Tools the agent can call
3. **First agent loop** (`src/agent/core.py`) — Claude Agent SDK with performance marketing system prompt
4. Test agent against a real Google Ads account
5. This maps to **Week 1, Days 3-5** in the build plan

## Session 2 — Feb 13, 2026

### Summary
Explored Claude Code's agent system. Ran parallel agents to survey codebase, plan Google Ads connector, and check project status. Established agent briefing practices for better context handoff.

### What Was Done
- Ran 3 parallel sub-agents as a demo: Explore (codebase survey), Plan (Google Ads connector design), Bash (git + dependency check)
- Plan agent produced detailed 3-layer Google Ads connector architecture (saved to memory/architecture.md)
- Created memory/agent-practices.md — template and rules for briefing sub-agents with conversation context
- Updated memory/MEMORY.md with agent briefing section
- Updated memory/architecture.md with codebase status and connector plan

### Key Findings from Agents
- 24 files total, foundation solid, all core directories empty awaiting implementation
- Google Ads connector: 3-layer design (connector → MCP tools → OAuth routes), 7 connector methods, 5 read-only MCP tools
- No virtual environment exists yet, dependencies not installed

### Next Session Should Start With
1. Create virtual environment and install dependencies
2. Build `src/connectors/google_ads.py` — GoogleAdsConnector class with GAQL queries
3. Build `src/mcp_servers/google_ads.py` — 5 read-only Agent SDK tools
4. Write unit tests with mocked Google Ads responses
5. This is **Week 1, Days 3-5** in the build plan

## Session 3 — Feb 13, 2026

### Summary
Built the complete Google Ads connector layer: API client, MCP tools, OAuth routes, and 92 unit tests. All lint clean, all tests passing.

### What Was Done
- Created virtual environment and installed all dependencies (google-ads v29.1.0, claude-agent-sdk v0.1.35, etc.)
- Fixed `pyproject.toml` hatch build config (`packages = ["src"]`)
- Built 3 parallel agents to create the 3-layer Google Ads integration simultaneously:

#### `src/connectors/google_ads.py` — Google Ads API Connector
- `GoogleAdsConnector` class with 7 read-only methods: `get_accessible_accounts`, `get_account_info`, `get_campaigns`, `get_campaign_metrics`, `get_account_metrics`, `get_change_history`, `get_recommendations`
- All GAQL queries via `search_stream()`, protobuf→dict conversion via `MessageToDict()`
- Micros→decimal conversion at connector boundary
- Auth errors raised as `GoogleAdsAuthError`, transient errors swallowed
- Per-account credentials via `load_from_dict()` (not YAML) for multi-user

#### `src/mcp_servers/google_ads.py` — 5 Read-Only MCP Tools
- `list_google_ads_accounts`, `get_google_ads_campaigns`, `get_google_ads_performance`, `get_google_ads_changes`, `get_google_ads_recommendations`
- Uses Claude Agent SDK `@tool` decorator + `create_sdk_mcp_server()`
- Performance tool runs normalization and computes totals + daily breakdown
- All responses formatted as readable text for Claude to reason about

#### `src/api/routes/google_ads_oauth.py` — FastAPI OAuth2 Flow
- 4 routes: `/authorize` (redirect to Google), `/callback` (code exchange), `/refresh` (token refresh), `/status` (connection health check)
- CSRF protection via state tokens with 10-minute TTL
- Uses `httpx.AsyncClient` for all Google API calls
- Pydantic request/response models

#### Tests (92 total, all passing)
- `tests/test_connectors/test_google_ads.py` — 60 tests across 11 classes covering all connector methods, error handling, proto conversion, metric formatting
- `tests/test_connectors/test_google_ads_oauth.py` — 32 tests across 4 endpoint groups covering all OAuth routes, CSRF validation, error paths

### Post-Build Fixes
- Fixed connector to accept both `date` objects and strings for date params
- Fixed MCP `list_accounts` tool to match connector's `list[str]` return type
- Removed double-conversion of budget micros in MCP campaign tool
- Fixed `datetime.utcnow()` deprecation → `datetime.now(UTC)`
- Fixed all ruff lint issues (line length, import sorting, unused imports)

### Files Created/Modified
- `src/connectors/google_ads.py` (new, 679 lines)
- `src/mcp_servers/google_ads.py` (new, 628 lines)
- `src/api/routes/google_ads_oauth.py` (new, 427 lines)
- `tests/test_connectors/test_google_ads.py` (new, 60 tests)
- `tests/test_connectors/test_google_ads_oauth.py` (new, 32 tests)
- `pyproject.toml` (modified — added hatch build config)
- `src/models/schema.py` (minor lint fixes)
- `src/config.py` (minor lint fixes)
- `src/models/normalized.py` (minor lint fixes)

### Next Session Should Start With
1. **First agent loop** (`src/agent/core.py`) — Claude Agent SDK with performance marketing system prompt, wired to Google Ads MCP tools
2. **Meta connector** (`src/connectors/meta.py`) — same pattern as Google Ads
3. **Slack integration** — daily briefing delivery
4. Test against a real Google Ads account (need dev token + test account)
5. This maps to **Week 1, Day 5 → Week 2** in the build plan

## Session 4 — Feb 13, 2026

### Summary
Built the core agent loop, Meta connector (full 3-layer integration), and comprehensive tests for both. All 174 tests passing, all lint clean.

### What Was Done
Built 2 major systems in parallel using sub-agents:

#### Core Agent Loop (`src/agent/core.py` + `src/agent/prompts.py`)
- `SideraAgent` class — main orchestrator using Claude Agent SDK
- `ClaudeSDKClient` for daily briefings (multi-turn, Sonnet→Opus escalation at 8+ turns)
- `query()` for one-shot ad-hoc analysis queries
- Result dataclasses: `BriefingResult`, `QueryResult`
- System prompt: Sidera identity, 8-step analysis framework, structured daily briefing output format
- Prompt templates: `build_daily_briefing_prompt()`, `build_analysis_prompt()`
- Tool hooks for logging, permission mode set to bypass (all tools read-only)
- Recommendation extraction parser handles both plain text and markdown-bold format
- Both Google Ads and Meta MCP servers wired in

#### Meta Connector (`src/connectors/meta.py`)
- `MetaConnector` class with 7 methods mirroring Google Ads patterns
- Uses `facebook_business` SDK: `AdAccount`, `Campaign`, `AdsInsights`, `User`
- Methods: `get_ad_accounts`, `get_account_info`, `get_campaigns`, `get_campaign_metrics`, `get_account_metrics`, `get_campaign_insights`, `get_account_activity`
- Handles Meta quirks: `act_` prefix, spend as string decimal, budget in cents, actions[] array
- Custom exceptions: `MetaConnectorError`, `MetaAuthError`

#### Meta MCP Tools (`src/mcp_servers/meta.py`)
- 5 read-only tools: `list_meta_ad_accounts`, `get_meta_campaigns`, `get_meta_performance`, `get_meta_audience_insights`, `get_meta_account_activity`
- Uses `normalize_meta_metrics()` from normalized.py
- Same factory pattern as Google Ads: `create_meta_tools()`, `create_meta_mcp_server()`

#### Meta OAuth Routes (`src/api/routes/meta_oauth.py`)
- 4 routes: `/authorize`, `/callback`, `/refresh`, `/status`
- Two-step token exchange: code → short-lived → long-lived token (Meta-specific)
- Refresh uses `access_token` (not `refresh_token` — Meta convention)
- Status calls `/me` then `/me/adaccounts`

#### Tests (82 new, 174 total — all passing)
- `tests/test_agent/test_core.py` — 29 tests (construction, options, text extraction, recommendation parsing, hooks, daily briefing, query, prompt templates)
- `tests/test_connectors/test_meta.py` — 31 tests (all connector methods, error handling, spend conversion, actions array, act_ prefix)
- `tests/test_connectors/test_meta_oauth.py` — 22 tests (all OAuth routes, CSRF, token exchange, error paths)

### Files Created
- `src/agent/core.py` — SideraAgent orchestrator
- `src/agent/prompts.py` — System prompt + prompt templates + tool constants
- `src/connectors/meta.py` — Meta Marketing API connector
- `src/mcp_servers/meta.py` — 5 Meta MCP tools
- `src/api/routes/meta_oauth.py` — Meta OAuth2 flow
- `tests/test_agent/test_core.py` — 29 tests
- `tests/test_connectors/test_meta.py` — 31 tests
- `tests/test_connectors/test_meta_oauth.py` — 22 tests

### Next Session Should Start With
1. **Slack integration** (`src/connectors/slack.py` + `src/mcp_servers/slack.py`) — Daily briefing delivery with approve/reject buttons
2. **Inngest workflows** (`src/workflows/`) — Durable functions for daily cron trigger, approval flow, cost monitoring
3. **FastAPI app assembly** (`src/api/app.py`) — Wire all routes together, add health check
4. **Database integration** — Store analysis results, approval queue, audit log
5. This maps to **Week 2** in the build plan

## Session 5 — Feb 13, 2026

### Summary
Built Slack integration (connector + MCP tools + interactive routes), Inngest workflows (daily briefing + cost monitor), and FastAPI app assembly. Wired Slack MCP server into agent core. 289 tests passing, all lint clean.

### What Was Done
Built 4 systems in parallel using background sub-agents:

#### Slack Connector (`src/connectors/slack.py`)
- `SlackConnector` class using `slack_sdk.WebClient` for standalone messaging
- 5 methods: `send_briefing`, `send_approval_request`, `update_approval_message`, `send_alert`, `test_connection`
- Block Kit formatting, approval buttons with `sidera_approve`/`sidera_reject` action IDs
- Custom exceptions: `SlackConnectorError`, `SlackAuthError`

#### Slack MCP Tools (`src/mcp_servers/slack.py`)
- 3 tools: `send_slack_alert`, `send_slack_briefing_preview`, `check_slack_connection`
- Agent deliberately does NOT have approval-sending tools (Inngest's job)

#### Slack Interactive Routes (`src/api/routes/slack.py`)
- Slack Bolt `AsyncApp` + `AsyncSlackRequestHandler` for FastAPI
- Handlers for approve/reject button clicks → update message → store in `_pending_approvals`

#### Inngest Workflows (`src/workflows/daily_briefing.py`)
- `daily_briefing_workflow`: Cron `0 7 * * MON-FRI`, 6 checkpointed steps, 24h approval timeout
- `cost_monitor_workflow`: Cron `*/30 * * * *`, alerts at 80% cost threshold

#### FastAPI App (`src/api/app.py`)
- `create_app()` factory, health check, all routes wired, Inngest serve, CORS

#### Integration
- Wired Slack MCP server into `src/agent/core.py` and `src/agent/prompts.py`
- Updated all test mocks for 3 MCP servers

### Tests (115 new, 289 total — all passing)
- `tests/test_connectors/test_slack.py` — 36 tests
- `tests/test_mcp_servers/test_slack_mcp.py` — 14 tests
- `tests/test_api/test_slack_routes.py` — 25 tests
- `tests/test_workflows/test_daily_briefing.py` — 21 tests
- `tests/test_api/test_app.py` — 19 tests

### Next Session Should Start With
1. **Database integration** — PostgreSQL via Supabase
2. **Wire Inngest approval events** to Slack button decisions
3. **End-to-end testing** with real API keys
4. **Streamlit dashboard MVP**

## Session 6 — Feb 13, 2026

### Summary
Built the complete database layer: async session management, full CRUD service (18 methods), Alembic migrations, and wired DB persistence into existing workflows and Slack routes. 329 tests passing, all lint clean.

### What Was Done
Built 3 systems in parallel using background sub-agents:

#### Database Session (`src/db/session.py`)
- Lazy async engine creation from `settings.database_url`
- `async_sessionmaker` with `expire_on_commit=False`
- `get_db_session()` async context manager with auto-commit/rollback
- `init_db()` for dev table creation, `close_db()` for shutdown
- Graceful fallback when `database_url` is empty (logs warning, raises clear RuntimeError)

#### Database Service (`src/db/service.py`)
- 18 async CRUD methods organized by domain:
  - **Accounts:** `get_accounts_for_user`, `get_account_by_platform_id`, `upsert_account`, `update_account_tokens`
  - **Analysis:** `save_analysis_result`, `get_latest_analysis`, `get_analyses_for_period`
  - **Approvals:** `create_approval`, `update_approval_status`, `get_pending_approvals`, `get_approval_by_id`, `expire_old_approvals`
  - **Audit:** `log_event`, `get_audit_trail`
  - **Costs:** `record_cost`, `get_daily_cost`, `get_daily_cost_all_users`
  - **Campaigns:** `upsert_campaign`, `save_daily_metrics`
- All methods use SQLAlchemy 2.0 `select()` + `session.execute()` pattern
- Proper upsert (query-then-update-or-create) for accounts, campaigns, metrics

#### Alembic Migrations (`alembic/`)
- `alembic.ini` configured with empty URL (overridden from settings at runtime)
- `alembic/env.py` with async engine support, imports Base metadata
- `alembic/versions/001_initial_schema.py` — Creates all 7 tables with indexes
- `scripts/create_tables.py` — Convenience script for dev/test

#### Workflow + Route DB Wiring
- **`src/workflows/daily_briefing.py`:**
  - Step 1 (load accounts) now tries DB first, falls back to event data
  - New Step 2b saves analysis result, records LLM cost, logs audit event
  - Step 6 persists approval decisions to DB
  - Cost monitor queries real cost_tracking table
  - All DB calls wrapped in try/except for graceful degradation
- **`src/api/routes/slack.py`:**
  - Both approve/reject handlers persist to DB as durable backup
  - In-memory `_pending_approvals` dict retained for fast Inngest lookup
- **`src/api/app.py`:**
  - Added lifespan handler logging DB connection status on startup

### Tests (40 new, 329 total — all passing)
- `tests/test_db/test_service.py` — 40 tests across 8 classes
  - Account CRUD, analysis save/retrieval, approval lifecycle, audit trail, cost tracking, campaign/metrics, edge cases
  - Uses SQLite in-memory with aiosqlite for isolation

### Files Created
- `src/db/__init__.py`
- `src/db/session.py` (124 lines)
- `src/db/service.py` (511 lines)
- `alembic.ini`
- `alembic/env.py` (86 lines)
- `alembic/versions/001_initial_schema.py`
- `scripts/create_tables.py`
- `tests/test_db/__init__.py`
- `tests/test_db/test_service.py` (40 tests)

### Files Modified
- `src/workflows/daily_briefing.py` — DB integration for accounts, analysis, costs, approvals
- `src/api/routes/slack.py` — DB persistence in approve/reject handlers
- `src/api/app.py` — Lifespan handler for DB status logging

### Next Session Should Start With
1. **End-to-end testing** with real API keys (Google Ads, Meta, Slack)
2. **Streamlit dashboard MVP** — basic UI for viewing briefings and managing connections
3. **Redis caching** via Upstash for API response caching
4. **Production hardening** — error monitoring (Sentry), rate limiting, proper secrets management

## Session 7 — Feb 13, 2026

### Summary
Built Streamlit dashboard (6-page MVP), Redis caching layer (client + service + decorator), and production middleware (Sentry + rate limiter + request logging) — all in parallel. 401 tests passing, all lint clean.

### What Was Done
Built 3 systems in parallel using background sub-agents:

#### Streamlit Dashboard (`dashboard/`)
- `dashboard/app.py` — 6-page MVP (23KB):
  - **Overview:** Key metrics, recent alerts, quick stats
  - **Daily Briefings:** Full briefing viewer with expandable recommendations
  - **Approval Queue:** Pending/approved/rejected items with status filters
  - **Accounts:** Connected ad accounts with platform details
  - **Cost Monitor:** Daily LLM cost chart with budget limit line
  - **Audit Log:** Filterable event history
- `dashboard/sample_data.py` — Realistic demo data (26KB): 3 accounts, 5 analyses, 8 approvals, 20 audit entries, 7 days cost history
- Dual-mode: Live database queries or sample data fallback with "Demo mode" banner
- `run_async()` helper for sync-to-async bridge in Streamlit

#### Redis Caching Layer (`src/cache/`)
- `src/cache/redis_client.py` — Lazy singleton `get_redis_client()` using `redis.asyncio.Redis`
  - URL password masking for safe logging
  - `close_redis()` and `reset_redis_client()` for lifecycle management
- `src/cache/service.py` — Cache operations:
  - `cache_get`, `cache_set`, `cache_delete`, `cache_delete_pattern`
  - `build_cache_key()` with namespace prefixing
  - TTL constants: CAMPAIGNS=3600s, METRICS=300s, RECOMMENDATIONS=1800s, ACCOUNT_INFO=7200s
  - Uses SCAN (not KEYS) for safe pattern deletion in production
  - `json.dumps(default=str)` for Decimal/date serialization
- `src/cache/decorators.py` — `@cached(ttl_seconds, key_prefix)` decorator:
  - Works with both sync and async functions
  - MD5 hash of args for cache keys, skips `self` for class methods
  - `bypass_cache=True` kwarg support, never caches `None`

#### Production Middleware (`src/middleware/`)
- `src/middleware/sentry_setup.py` — Sentry error monitoring:
  - `init_sentry()` — FastAPI integration, different trace rates for dev (1.0) vs prod (0.1)
  - `capture_exception()` — safe wrapper (no-op if Sentry not configured)
  - `set_user_context()` — attach user info to error reports
- `src/middleware/rate_limiter.py` — Token bucket rate limiter:
  - In-memory `RateLimiter` class with per-minute and per-hour limits
  - Thread-safe with locks, 429 responses with rate limit headers
  - Configurable via `RateLimitConfig` dataclass
- `src/middleware/request_logging.py` — Structured request/response logging:
  - `RequestLoggingMiddleware` (BaseHTTPMiddleware)
  - UUID request IDs, response timing, skips /health endpoint
  - Logs method, path, status, duration via structlog

#### App Integration
- `src/api/app.py` updated: `init_sentry()` called on startup, `RequestLoggingMiddleware` added

### Tests (72 new, 401 total — all passing)
- `tests/test_cache/test_cache.py` — 46 tests (client singleton, get/set/delete, pattern ops, decorator sync/async, bypass, TTL, error handling)
  - Uses `fakeredis` for isolation
- `tests/test_middleware/test_sentry_setup.py` — 11 tests (init, capture, user context, no-op when unconfigured)
- `tests/test_middleware/test_rate_limiter.py` — 9 tests (token bucket, per-minute/hour limits, headers, concurrent access)
- `tests/test_middleware/test_request_logging.py` — 6 tests (request ID generation, timing, health skip, error logging)

### Files Created
- `dashboard/__init__.py`
- `dashboard/app.py` (23KB, 6-page Streamlit dashboard)
- `dashboard/sample_data.py` (26KB, realistic demo data)
- `src/cache/__init__.py`
- `src/cache/redis_client.py`
- `src/cache/service.py`
- `src/cache/decorators.py`
- `src/middleware/__init__.py`
- `src/middleware/sentry_setup.py`
- `src/middleware/rate_limiter.py`
- `src/middleware/request_logging.py`
- `tests/test_cache/__init__.py`
- `tests/test_cache/test_cache.py` (46 tests)
- `tests/test_middleware/__init__.py`
- `tests/test_middleware/test_sentry_setup.py` (11 tests)
- `tests/test_middleware/test_rate_limiter.py` (9 tests)
- `tests/test_middleware/test_request_logging.py` (6 tests)

### Files Modified
- `src/api/app.py` — Added Sentry init + request logging middleware

### Next Session Should Start With
1. **End-to-end testing** with real API keys (Google Ads, Meta, Slack)
2. **Wire Redis cache** into existing connectors for API response caching
3. **Deploy to Railway** — production deployment configuration

## Session 8 — Feb 13, 2026

### Summary
Built everything remaining that doesn't require real API credentials: wired Redis cache into connectors, moved OAuth state to Redis, deployment config, CI/CD, integration tests, operational scripts, README, dashboard config, and fixed all TODOs. 418 tests passing, all lint clean.

### What Was Done
Built 5 systems in parallel using background sub-agents:

#### Redis Cache Wiring
- Added `@cached` decorator to 5 Google Ads connector methods (campaigns, metrics, account info, recommendations)
- Added `@cached` decorator to 5 Meta connector methods (same pattern)
- TTLs: account_info=2h, campaigns=1h, recommendations=30m, metrics=5m
- Left uncached: `get_accessible_accounts`, `get_change_history`, `get_ad_accounts`, `get_account_activity`
- Moved Google Ads + Meta OAuth state stores from in-memory to Redis with in-memory fallback
- Fixed `accounts_analyzed` TODO in daily briefing workflow

#### Deployment Configuration
- `Dockerfile` — Multi-stage build (builder + slim runtime), non-root user, health check
- `railway.toml` — Railway deployment config with health check and restart policy
- `Procfile` — Heroku-compatible process definition
- `docker-compose.yml` — 5 services (app, postgres, redis, dashboard, inngest) with health checks
- `.dockerignore` — Excludes venv, tests, docs, cache files

#### CI/CD + Integration Tests
- `.github/workflows/ci.yml` — Python 3.12+3.13 matrix, pip caching, ruff lint, pytest with coverage
- `tests/test_integration/test_daily_briefing_e2e.py` — 8 E2E tests (full workflow, no-accounts, DB failure, approval flow, cost monitor)
- `tests/test_integration/test_api_e2e.py` — 9 API tests (health, root, CORS, 404, request ID, error handler)
- `tests/fixtures/google_ads_sample.json` + `tests/fixtures/meta_sample.json` — Recorded API response fixtures

#### Operational Scripts
- `scripts/seed_test_data.py` — Seeds DB with 3 accounts, 5 campaigns, 35 metrics, 3 analyses, 5 approvals, 10 audit entries, 21 cost records
- `scripts/trigger_briefing.py` — Manually trigger daily briefing via Inngest API
- `scripts/check_connections.py` — Health check all services (DB, Redis, Slack, Google Ads, Meta, Inngest)
- `scripts/clear_cache.py` — Clear Redis cache by pattern with confirmation
- `scripts/export_audit_log.py` — Export audit log to CSV for compliance

#### Documentation + Config
- `README.md` — Public-facing project docs with architecture diagram, quick start, Docker Compose guide
- `dashboard/.streamlit/config.toml` — Dark theme, headless mode, no usage stats
- `.env.example` — Enhanced with "where to get" URLs for every credential, required vs optional labels
- `pyproject.toml` — Added `fakeredis` and `aiosqlite` to dev dependencies

### Tests (17 new, 418 total — all passing)
- `tests/test_integration/test_daily_briefing_e2e.py` — 8 tests
- `tests/test_integration/test_api_e2e.py` — 9 tests
- Fixed test isolation issue in approval flow E2E test (module-level reference vs import-time reference)

### Files Created
- `Dockerfile`, `railway.toml`, `Procfile`, `docker-compose.yml`, `.dockerignore`
- `.github/workflows/ci.yml`
- `README.md`
- `dashboard/.streamlit/config.toml`
- `tests/test_integration/__init__.py`
- `tests/test_integration/test_daily_briefing_e2e.py` (8 tests)
- `tests/test_integration/test_api_e2e.py` (9 tests)
- `tests/fixtures/google_ads_sample.json`, `tests/fixtures/meta_sample.json`
- `scripts/seed_test_data.py`, `scripts/trigger_briefing.py`, `scripts/check_connections.py`
- `scripts/clear_cache.py`, `scripts/export_audit_log.py`

### Files Modified
- `src/connectors/google_ads.py` — Added `@cached` decorators to 5 methods
- `src/connectors/meta.py` — Added `@cached` decorators to 5 methods
- `src/api/routes/google_ads_oauth.py` — Redis-backed OAuth state with fallback
- `src/api/routes/meta_oauth.py` — Redis-backed OAuth state with fallback
- `src/workflows/daily_briefing.py` — Fixed `accounts_analyzed` TODO
- `pyproject.toml` — Added `fakeredis`, `aiosqlite` to dev deps
- `.env.example` — Enhanced with credential source URLs

### Next Session Should Start With
1. **End-to-end testing** with real API keys (Google Ads, Meta, Slack)
2. **Deploy to Railway** — production deployment

## Session 9 — Feb 13, 2026

### Summary
Audit + fix session: found and fixed 4 issues from a comprehensive codebase audit. Wrote 69 new MCP tool tests (previously 0 coverage), wired rate limiter into app, fixed Dockerfile missing dashboard/, removed unused `resend` dependency. 487 tests passing, all lint clean.

### What Was Done

#### Bug Fixes
- **Dockerfile** — Added `COPY dashboard/ ./dashboard/` to runtime stage; without this, the docker-compose dashboard service would fail at runtime
- **Rate limiter wiring** — Added `RateLimitMiddleware` to `src/api/app.py`; the middleware was fully built and tested but never wired into the application
- **Unused dependency** — Removed `resend>=2.0.0` from `pyproject.toml` (never imported anywhere)

#### MCP Tool Tests (69 new tests)
- `tests/test_mcp_servers/test_google_ads_mcp.py` — 33 tests covering all 5 Google Ads MCP tools:
  - `list_google_ads_accounts` (5 tests): happy path, empty, no account info, connector error, partial info
  - `get_google_ads_campaigns` (6 tests): happy path, empty, missing/empty customer_id, connector error, alt keys
  - `get_google_ads_performance` (8 tests): happy path, with campaign_id, empty, missing fields (x3), error, ROAS calc
  - `get_google_ads_changes` (8 tests): happy path, empty, missing id, custom days, clamped max/min, invalid days, error
  - `get_google_ads_recommendations` (6 tests): happy path, empty, missing id, error, string impact, extra fields
- `tests/test_mcp_servers/test_meta_mcp.py` — 36 tests covering all 5 Meta MCP tools:
  - `list_meta_ad_accounts` (5 tests): happy path, empty, error, unknown status code, string status
  - `get_meta_campaigns` (6 tests): happy path, empty, missing/empty id, error, no budgets
  - `get_meta_performance` (8 tests): happy path, with campaign_id, empty, missing fields (x3), error, no conversions
  - `get_meta_audience_insights` (8 tests): happy path, empty, missing fields (x3), invalid breakdown, error, platform breakdown
  - `get_meta_account_activity` (9 tests): happy path, empty, missing id, custom days, clamped max/min, invalid days, error, stopped campaign

### Tests (69 new, 487 total — all passing)
- `tests/test_mcp_servers/test_google_ads_mcp.py` — 33 tests
- `tests/test_mcp_servers/test_meta_mcp.py` — 36 tests

### Files Created
- `tests/test_mcp_servers/test_google_ads_mcp.py` (33 tests)
- `tests/test_mcp_servers/test_meta_mcp.py` (36 tests)

### Files Modified
- `Dockerfile` — Added `COPY dashboard/ ./dashboard/` to runtime stage
- `src/api/app.py` — Added `RateLimitMiddleware`
- `pyproject.toml` — Removed unused `resend` dependency

### Next Session Should Start With
1. **End-to-end testing** with real API keys (Google Ads, Meta, Slack)
2. **Deploy to Railway** — production deployment

## Session 10 — Feb 13, 2026

### Summary
Added BigQuery integration — the advertiser's backend data warehouse is now the agent's source of truth for business outcomes. Built connector (7 methods), MCP tools (5 tools), wired into agent core with backend cross-referencing prompts, wrote 81 tests. 568 tests passing, all lint clean.

### What Was Done

#### BigQuery Connector (`src/connectors/bigquery.py`)
- 3 exception classes: `BigQueryConnectorError`, `BigQueryAuthError`, `BigQueryTableNotFoundError`
- `BigQueryConnector` class with 7 public methods:
  1. `discover_tables()` — list tables/views in dataset (no cache)
  2. `get_goals(period?, channel?)` — revenue/CPA/ROAS targets (TTL 1h)
  3. `get_budget_pacing(period?, channel?)` — planned vs actual spend (TTL 10m)
  4. `get_business_metrics(start_date, end_date, granularity?)` — revenue, orders, AOV (TTL 5m)
  5. `get_channel_performance(start_date, end_date)` — backend-attributed revenue by channel (TTL 5m)
  6. `get_campaign_attribution(start_date, end_date, channel?)` — campaign-level backend conversions (TTL 5m)
  7. `run_custom_query(sql, params?, max_rows?)` — ad-hoc SELECT only, max 5000 rows (no cache)
- 3 auth modes: raw JSON service account, base64-encoded JSON (Railway), Application Default Credentials
- Configurable table names per client via env vars (e.g., `BIGQUERY_TABLE_GOALS=my_goals_v2`)
- All queries use parameterized SQL, date range filters, LIMIT clauses
- `@cached` decorator applied with appropriate TTLs

#### BigQuery MCP Tools (`src/mcp_servers/bigquery.py`)
- 5 read-only tools for the Claude agent:
  1. `discover_bigquery_tables` — list available tables
  2. `get_business_goals` — goals/targets by period and channel
  3. `get_backend_performance` — top-line metrics + optional channel breakdown
  4. `get_budget_pacing` — pacing status with ON TRACK/OVERSPEND/UNDERSPEND indicators
  5. `get_campaign_attribution` — campaign-level backend conversions, aggregated by campaign, sorted by revenue
- Each tool emphasizes "BACKEND SOURCE OF TRUTH" in descriptions

#### Agent Wiring + Prompt Updates
- `src/agent/core.py` — BigQuery MCP server wired into `_build_mcp_servers()`
- `src/agent/prompts.py`:
  - Added `BIGQUERY_TOOLS` list (5 tools) to `ALL_TOOLS`
  - Added Principle 5: "Backend is truth — platform-reported conversions are estimates inflated by attribution models"
  - Added Analysis Framework steps 7 (Backend cross-reference) and 8 (Goal & pacing checks)
  - Added "Backend Reality Check" section to daily briefing output format
  - Updated `build_daily_briefing_prompt()` with BigQuery data pulling instructions

#### Configuration
- `src/config.py` — 8 new BigQuery settings (project_id, dataset_id, credentials_json, 5 table names)
- `src/cache/service.py` — 3 new TTL constants (BQ_GOALS=1h, BQ_PACING=10m, BQ_METRICS=5m)
- `.env.example` — BigQuery section with documentation URLs
- `pyproject.toml` — Added `google-cloud-bigquery>=3.25.0`

### Tests (81 new, 568 total — all passing)
- `tests/test_connectors/test_bigquery.py` — 45 tests covering:
  - Construction (6): explicit creds, settings fallback, JSON auth, base64 auth, invalid creds, ADC mode
  - Table config (4): default names, fully-qualified passthrough, bare names, unknown table error
  - discover_tables (3): happy path, empty, API error
  - get_goals (4): no filters, period+channel filter, empty, error
  - get_budget_pacing (3): happy path, filters, empty
  - get_business_metrics (5): daily/weekly/monthly granularity, date objects, empty
  - get_channel_performance (3): happy path, empty, error
  - get_campaign_attribution (4): happy path, channel filter, empty, date objects
  - run_custom_query (6): valid SELECT, non-SELECT rejection, INSERT rejection, row cap, auto-LIMIT, existing LIMIT
  - Error handling (4): Forbidden→AuthError, NotFound→TableNotFound, BadRequest logged, generic swallowed
  - _execute_query (3): returns dicts, transient error→None, Forbidden→raises
- `tests/test_mcp_servers/test_bigquery_mcp.py` — 36 tests covering:
  - discover_bigquery_tables (6): happy path, empty, error, partial data, no args, no description
  - get_business_goals (7): happy path, empty, period filter, channel filter, error, partial targets, filter desc
  - get_backend_performance (7): happy path, no channel breakdown, empty, missing dates (x2), no channel data, error
  - get_budget_pacing (7): happy path, empty, filters, overspend, error, underspend, filter desc
  - get_campaign_attribution (9): happy path, empty, missing dates (x2), channel filter, error, aggregation, sort, filter desc
- `tests/fixtures/bigquery_sample.json` — Sample data for all 5 BQ table types

### Files Created
- `src/connectors/bigquery.py` (744 lines, BigQuery connector with 7 methods)
- `src/mcp_servers/bigquery.py` (793 lines, 5 MCP tools)
- `tests/test_connectors/test_bigquery.py` (45 tests)
- `tests/test_mcp_servers/test_bigquery_mcp.py` (36 tests)
- `tests/fixtures/bigquery_sample.json` (sample data)

### Files Modified
- `src/config.py` — Added 8 BigQuery settings
- `src/cache/service.py` — Added 3 BigQuery TTL constants
- `src/agent/core.py` — Wired BigQuery MCP server
- `src/agent/prompts.py` — Backend cross-referencing prompts, new analysis steps, daily briefing format
- `.env.example` — Added BigQuery section
- `pyproject.toml` — Added `google-cloud-bigquery>=3.25.0`
- `CLAUDE.md` — Updated with BigQuery info, 568 test count

### Next Session Should Start With
1. **Google Drive integration** — full Drive/Docs/Sheets/Slides connector + MCP tools

---

## Session 11 — Feb 13, 2026

### Summary
Google Drive / Docs / Sheets / Slides integration. Full connector with 13 methods (5 Drive, 3 Docs, 3 Sheets, 2 Slides), 8 compound MCP tools, OAuth2 flow, agent wiring, and 77 new tests. Total test count: 645.

### What Was Done
- **Planning:** Explored codebase patterns and Google Workspace API requirements, designed plan with 5 files to create + 7 to modify, user approved
- **Config + deps:** Added `google_drive_refresh_token` to settings, 3 Drive cache TTLs, 4 new pip dependencies (`google-api-python-client`, `google-auth`, `google-auth-httplib2`, `google-auth-oauthlib`), `.env.example` section
- **Connector:** Built `src/connectors/google_drive.py` — 13 public methods across Drive (list, metadata, folder, move, share link), Docs (create, read, append), Sheets (create, read, write), Slides (create, add slide). User OAuth with `@cached` on read methods
- **OAuth:** Built `src/api/routes/google_drive_oauth.py` — authorize/callback/refresh/status endpoints, 4 Drive/Docs/Sheets/Slides scopes, reuses Google Ads client credentials
- **MCP tools:** Built `src/mcp_servers/google_drive.py` — 8 compound tools (search_google_drive, get_drive_file_info, manage_drive_folders, create_google_doc, read_google_doc, edit_google_doc, manage_google_sheets, manage_google_slides)
- **Agent wiring:** Updated `core.py` (import + MCP server), `prompts.py` (8-tool list, system prompt with Drive instructions), `app.py` (mounted OAuth router)
- **Tests:** 42 connector tests (construction, all 13 methods, error handling) + 35 MCP tool tests (all 8 tools, validation, error paths)
- **Docs:** Updated CLAUDE.md (645 tests, Drive in file structure), progress.md (this entry)

### Test Breakdown
- `tests/test_connectors/test_google_drive.py` — 42 tests covering:
  - Construction (3): explicit creds, settings fallback, missing refresh token
  - list_files (5): basic, folder_id, query, empty, HttpError
  - get_file_metadata (3): success, not found, auth error
  - create_folder (3): basic, with parent, HttpError
  - move_file (3): success, empty parents, HttpError
  - get_shareable_link (2): success, auth error
  - create_document (3): empty, with content, HttpError
  - read_document (4): paragraphs, tables, empty, auth error
  - append_to_document (3): success, empty doc, HttpError
  - create_spreadsheet (3): no data, with data, HttpError
  - read_spreadsheet (3): success, empty, custom range
  - write_to_spreadsheet (2): success, HttpError
  - create_presentation (2): success, HttpError
  - add_slide (3): blank, with content, HttpError
- `tests/test_mcp_servers/test_google_drive_mcp.py` — 35 tests covering:
  - search_google_drive (5): basic, query filter, file_type, empty, auth error
  - get_drive_file_info (4): success, not found, missing id, connector error
  - manage_drive_folders (5): create, create missing name, move, move missing params, get_link
  - create_google_doc (4): basic, with content, missing title, auth error
  - read_google_doc (4): success, truncation, missing id, connector error
  - edit_google_doc (4): success, missing id, missing content, unexpected error
  - manage_google_sheets (5): create, read, write, create missing title, read missing id
  - manage_google_slides (4): create, add_slide, add_slide missing id, unknown action

### Files Created
- `src/connectors/google_drive.py` (1,121 lines, 13 methods)
- `src/mcp_servers/google_drive.py` (1,381 lines, 8 compound tools)
- `src/api/routes/google_drive_oauth.py` (OAuth2 flow, 4 endpoints)
- `tests/test_connectors/test_google_drive.py` (42 tests)
- `tests/test_mcp_servers/test_google_drive_mcp.py` (35 tests)

### Files Modified
- `src/config.py` — Added `google_drive_refresh_token`
- `src/cache/service.py` — Added 3 Drive cache TTLs
- `src/agent/core.py` — Wired Google Drive MCP server
- `src/agent/prompts.py` — Added 8 Drive tools, system prompt instructions for file management
- `src/api/app.py` — Mounted Google Drive OAuth router
- `.env.example` — Added Google Drive section
- `pyproject.toml` — Added 4 Google API dependencies
- `CLAUDE.md` — Updated test count to 645, added Drive to file structure

### Next Session Should Start With
1. **End-to-end testing** with real API keys (Google Ads, Meta, BigQuery, Google Drive, Slack)
2. **Deploy to Railway** — production deployment

## Session 12 — Feb 13, 2026

### Summary
Built the complete Skill System — a YAML-based skill registry that lets Sidera learn 100+ specialized tasks with business-specific guidance. Includes semantic routing via Claude Haiku, prompt composition, Inngest workflows for execution + scheduling, and Slack `/sidera` slash command. 125 new tests (770 total), all passing, ruff lint clean.

### What Was Done

#### Skill System Architecture
- **SkillDefinition** frozen dataclass with ~20 fields (id, name, category, platforms, tags, tools_required, model, system_supplement, prompt_template, output_format, business_guidance, schedule, chain_after, requires_approval)
- **YAML on disk** — Skills are version-controlled in `src/skills/library/*.yaml`, loaded at startup
- **Prompt composition** — BASE_SYSTEM_PROMPT (~1,500 tokens) + skill.system_supplement + skill.output_format + skill.business_guidance = ~2,000-2,500 tokens per skill
- **Haiku router** — Semantic routing via Claude Haiku (~$0.001/call), compact routing index (~50 tokens/skill), confidence threshold 0.5
- **Generic Inngest workflow** — One `skill_runner_workflow` handles all skills, with approval flow and skill chaining (max depth 5)

#### Files Created (14)
- `src/skills/__init__.py` — Package init
- `src/skills/schema.py` — SkillDefinition, load_skill_from_yaml, validate_skill, SkillLoadError, SkillValidationError
- `src/skills/registry.py` — SkillRegistry (load_all, get, list_all, list_by_category, list_by_platform, list_scheduled, build_routing_index, search, reload)
- `src/skills/router.py` — SkillRouter (Haiku-based semantic routing), SkillMatch dataclass
- `src/skills/executor.py` — SkillExecutor, SkillResult, SkillNotFoundError
- `src/skills/library/creative_analysis.yaml` — Meta creative performance analysis skill
- `src/skills/library/budget_reallocation.yaml` — Cross-platform budget optimization skill
- `src/skills/library/weekly_report.yaml` — Auto-generated weekly performance report skill
- `alembic/versions/002_add_skill_columns.py` — Migration adding skill_id columns + indexes
- `tests/test_skills/__init__.py` — Test package init
- `tests/test_skills/test_schema.py` — 18 tests
- `tests/test_skills/test_registry.py` — 24 tests
- `tests/test_skills/test_executor.py` — 24 tests
- `tests/test_skills/test_router.py` — 24 tests
- `tests/test_workflows/test_skill_workflows.py` — 35 tests

#### Files Modified (7)
- `src/agent/prompts.py` — Split SYSTEM_PROMPT into BASE_SYSTEM_PROMPT + DAILY_BRIEFING_SUPPLEMENT (backward compat preserved)
- `src/agent/core.py` — Added `run_skill()` method and `_resolve_model()` helper
- `src/models/schema.py` — Added `skill_id` column to AnalysisResult and AuditLog + indexes
- `src/db/service.py` — Added 3 skill-aware methods: save_skill_result, get_skill_history, log_skill_event
- `src/workflows/daily_briefing.py` — Added skill_runner_workflow, skill_scheduler_workflow, _cron_matches_now, _MAX_CHAIN_DEPTH=5
- `src/api/routes/slack.py` — Added `/sidera` slash command (list, run, free-text routing)
- `pyproject.toml` — Added `pyyaml>=6.0.0`
- `tests/test_workflows/test_daily_briefing.py` — Updated test_all_workflows_list for 4 workflows

### Tests (125 new, 770 total — all passing)
- `tests/test_skills/test_schema.py` — 18 tests (YAML loading, validation, frozen dataclass)
- `tests/test_skills/test_registry.py` — 24 tests (load_all, lookup, filtering, search, routing index)
- `tests/test_skills/test_executor.py` — 24 tests (execute, chain_after, params, error handling)
- `tests/test_skills/test_router.py` — 24 tests (route, confidence thresholds, API errors, JSON parsing)
- `tests/test_workflows/test_skill_workflows.py` — 35 tests (cron matching, runner steps, approval flow, chain depth, scheduler dispatch)

### Next Session Should Start With
1. **Add more YAML skills** — Target 10-20 skills covering audience analysis, keyword optimization, A/B test analysis, etc.
2. **End-to-end testing** with real API keys (Google Ads, Meta, BigQuery, Google Drive, Slack)
3. **Deploy to Railway** — production deployment

## Session 13 — Feb 13, 2026

### Summary
Built a channel template system for adding new ad platform integrations, plus documentation for the process. Templates provide skeleton code so adding TikTok, LinkedIn, Pinterest, etc. follows a repeatable pattern.

### What Was Done

#### Channel Template System (`src/templates/`)
- `src/templates/connector_template.py` — Skeleton connector class with `__CHANNEL__` placeholders for 7 standard methods (get_accounts, get_account_info, get_campaigns, get_campaign_metrics, get_account_metrics, get_changes, get_recommendations)
- `src/templates/mcp_tools_template.py` — Skeleton MCP tools file with 5 standard tools (list accounts, get campaigns, get performance, get changes, get recommendations)
- `src/templates/oauth_routes_template.py` — Skeleton OAuth2 flow with 4 standard routes (authorize, callback, refresh, status)
- `src/templates/test_connector_template.py` — Skeleton test file with placeholder test classes
- `src/templates/test_mcp_tools_template.py` — Skeleton MCP tool tests
- `docs/adding-a-channel.md` — Step-by-step guide for adding a new channel, covering connector, MCP tools, OAuth, agent wiring, config, tests, and deployment

#### Configuration
- `pyproject.toml` — Added `src/templates/` to ruff exclude list (templates use placeholders that aren't valid Python)

### Files Created
- `src/templates/__init__.py`
- `src/templates/connector_template.py`
- `src/templates/mcp_tools_template.py`
- `src/templates/oauth_routes_template.py`
- `src/templates/test_connector_template.py`
- `src/templates/test_mcp_tools_template.py`
- `docs/adding-a-channel.md`

### Files Modified
- `pyproject.toml` — Added templates to ruff exclude
- `README.md` — Added "Adding a New Channel" section

### Next Session Should Start With
1. **LLM cost optimization** — Three-phase model routing, briefing deduplication, result caching

## Session 14 — Feb 13, 2026

### Summary
Implemented the full LLM cost optimization system — three-phase model routing (Haiku→Sonnet→Opus), briefing deduplication via DB check, and Redis result caching. Estimated 60-80% reduction in per-briefing LLM costs while upgrading strategic quality with Opus. 13 new tests (783 total), all passing, ruff lint clean.

### What Was Done

#### Three-Phase Model Routing (`src/agent/core.py`)
- New `run_daily_briefing_optimized()` method with three phases:
  - **Phase 1 (Haiku, ~$0.02):** Data collection via MCP tools — pulls all campaign data, formats into structured text blocks. Uses `settings.model_fast`, full tool access, `max_turns=settings.max_tool_calls_per_cycle`
  - **Phase 2 (Sonnet, ~$0.15):** Tactical analysis — produces Executive Summary, Key Metrics, Anomalies, Recommendations. Uses `settings.model_standard`, no tools (`mcp_servers={}`, `allowed_tools=[]`), `max_turns=1`
  - **Phase 3 (Opus, ~$0.35):** Strategic layer — cross-platform portfolio strategy, leading indicators, competitive dynamics. Uses `settings.model_reasoning`, no tools, `max_turns=1`. Skipped if response contains "No additional strategic insights"
- Redis cache integration: checks `sidera:briefing:{user_id}:{date}` before running, caches results with 2h TTL
- `force_refresh` parameter bypasses both DB dedup and Redis cache
- Cost tracking returns per-phase breakdown in `cost["phases"]` dict

#### Three-Phase Prompts (`src/agent/prompts.py`)
- `DATA_COLLECTION_SYSTEM` — Haiku system prompt: "collect and format, do NOT analyze"
- `DATA_COLLECTION_PROMPT` + `build_data_collection_prompt()` — instructs data pull for each account
- `ANALYSIS_ONLY_PROMPT` + `build_analysis_only_prompt()` — Sonnet analyzes pre-collected data
- `STRATEGIC_ANALYSIS_SYSTEM` — Opus system prompt: cross-platform strategy, leading indicators, risks
- `STRATEGIC_ANALYSIS_PROMPT` + `build_strategic_prompt()` — reviews tactical briefing for higher-level insights
- `_build_accounts_block()` — shared helper for formatting account info across prompts

#### Briefing Deduplication (`src/workflows/daily_briefing.py`)
- New `check-existing-briefing` Inngest step before `run-analysis`
- Queries `get_analyses_for_period()` for today's existing analysis
- If found and `force_refresh=False`, skips agent entirely and uses cached result
- Graceful degradation on DB errors (falls through to fresh analysis)
- `save_to_db` step returns `skipped=True` for deduplicated results
- `force_refresh` extracted from `ctx.event.data.get("force_refresh", False)`
- Switched from `run_daily_briefing()` to `run_daily_briefing_optimized()`
- Multi-model cost recording: uses `"multi-model"` label when `cost.get("phases")` present

#### Cache TTL Constant (`src/cache/service.py`)
- Added `CACHE_TTL_BRIEFING_RESULT = 7200` (2 hours)

### Cost Breakdown Per Briefing (Estimated)

| Phase | Model | Est. tokens | Est. cost |
|-------|-------|-------------|-----------|
| Data collection | Haiku | ~15K in + ~5K out | ~$0.02 |
| Tactical analysis | Sonnet | ~8K in + ~3K out | ~$0.15 |
| Strategic layer | Opus | ~5K in + ~1K out | ~$0.35 |
| **Total** | | | **~$0.52** |

Previous cost (Sonnet for everything): ~$1.50-3.00 per briefing.

### Tests (13 new, 783 total — all passing)

#### Workflow Deduplication Tests (`tests/test_workflows/test_daily_briefing.py` — 5 new)
- `test_daily_briefing_deduplication_skips_agent` — agent not called when DB has existing briefing
- `test_daily_briefing_deduplication_force_refresh` — agent called with force_refresh=True
- `test_daily_briefing_deduplication_db_error_falls_through` — graceful degradation on DB error
- `test_daily_briefing_force_refresh_default_false` — verifies default False
- `test_daily_briefing_dedup_skips_save` — save_to_db returns skipped=True for cached results

#### Agent Core Tests (`tests/test_agent/test_core.py` — 8 new)
- `test_three_phase_prompt_templates_exist` — verifies all prompt templates present
- `TestRunDailyBriefingOptimized` class (7 tests):
  - `test_returns_briefing_result_with_three_phases` — full 3-phase flow, cost sum ~$0.52
  - `test_cache_hit_returns_immediately` — Redis hit, no query() calls
  - `test_force_refresh_bypasses_cache` — cache_get not called
  - `test_cache_miss_runs_full_analysis_and_caches` — cache_set called with correct key/TTL
  - `test_opus_skipped_when_no_strategic_insights` — "No additional strategic insights" not appended
  - `test_phase1_uses_fast_model` — verifies model_fast/model_standard/model_reasoning per phase
  - `test_phase2_and_3_have_no_tools` — verifies no MCP servers and max_turns=1 for phases 2-3

#### Updated Existing Tests
- All `run_daily_briefing` references → `run_daily_briefing_optimized` across workflow and E2E tests
- Added `"check-existing-briefing"` to step ID assertions
- Added `force_refresh=False` to call assertions

### Files Modified
- `src/agent/core.py` — Added `run_daily_briefing_optimized()` three-phase method with Redis cache
- `src/agent/prompts.py` — Added 6 new prompt templates + 3 builder functions + shared helper
- `src/workflows/daily_briefing.py` — Deduplication step, optimized method call, multi-model cost recording
- `src/cache/service.py` — Added `CACHE_TTL_BRIEFING_RESULT = 7200`
- `tests/test_workflows/test_daily_briefing.py` — 5 new tests + updated existing tests
- `tests/test_agent/test_core.py` — 8 new tests
- `tests/test_integration/test_daily_briefing_e2e.py` — Updated for optimized method

### Next Session Should Start With
1. **Add more YAML skills** — Target 10-20 skills ✅ Done in Session 16
2. **End-to-end testing** with real API keys (Google Ads, Meta, BigQuery, Google Drive, Slack)
3. **Deploy to Railway** — production deployment

---

## Session 15 — Feb 13, 2026

### Summary
Production hardening complete — 7 major features added (retry, Sentry, encryption, DLQ, degradation, tiered analysis, token refresh). 171 new tests (783 → 954 total), all passing, lint clean.

### What Was Done

#### 1. Connector Retry with Exponential Backoff
- Created `src/connectors/retry.py` — `@retry_with_backoff` decorator with jitter, `is_transient_error()` classifier
- Applied to all 5 connectors: Google Ads (`_execute_query`), Meta (7 public methods), BigQuery (`_execute_query` + `discover_tables`), Google Drive (13 public methods), Slack (5 public methods)
- Distinguishes transient errors (429, 500, timeout) from permanent errors (auth failures)
- 86 new tests in `tests/test_connectors/test_retry.py`

#### 2. Sentry Error Capture Wiring
- Replaced 7 bare `except Exception: pass` blocks in `daily_briefing.py` with `capture_exception(exc)` calls
- Added `capture_exception(exc)` to all 5 connector error handlers
- 15 new tests in `tests/test_workflows/test_sentry_wiring.py`

#### 3. Application-Level Token Encryption
- Created `src/utils/encryption.py` — Fernet symmetric encryption with `enc:` prefix convention
- Added `token_encryption_key` setting to `src/config.py`
- Wired `encrypt_token()` into 3 OAuth callback routes (Google Ads, Meta, Google Drive)
- Wired `decrypt_token()` into 3 connectors (Google Ads, Meta, Google Drive)
- Backward compatible: plaintext tokens pass through unchanged
- 23 new tests in `tests/test_utils/test_encryption.py`

#### 4. Dead Letter Queue
- Created Alembic migration `003_add_failed_runs.py` with `failed_runs` table
- Added `FailedRun` model to `src/models/schema.py`
- Added 3 DB methods: `record_failed_run()`, `get_unresolved_failed_runs()`, `resolve_failed_run()`
- Wrapped all 4 workflows with top-level try/except DLQ recording
- 18 new tests in `tests/test_workflows/test_dead_letter_queue.py`

#### 5. Graceful Degradation on Phase 1 Failure
- Added `degradation_status` field to `BriefingResult` dataclass
- Added `_get_last_known_analysis()` method — falls back to Redis cache then DB (last 7 days)
- Phase 1 failure with fallback data returns stale result instead of crashing
- Stale data warning banner prepended to Slack briefing messages
- 9 new tests in `tests/test_agent/test_graceful_degradation.py` (Phase 1 fallback + stale banner)

#### 6. Tiered Analysis Depth (Volatility-Based Opus Skip)
- Added `_compute_volatility_score()` static method — regex-parses WoW percentage changes
- After Phase 2, if max deviation < 10%, Phase 3 (Opus) is skipped
- `force_refresh=True` always runs Phase 3
- 6 new tests in `tests/test_agent/test_graceful_degradation.py` (volatility scoring + gate)

#### 7. Proactive Token Refresh Workflow
- Added `token_refresh_workflow` — Inngest cron at 5 AM daily, retries=2
- Added `_refresh_oauth_token()` helper — handles Google + Meta token refresh via httpx
- Added 2 DB methods: `get_accounts_expiring_soon()`, `update_account_tokens()`
- Sends Slack alert on refresh failures
- Encrypts new tokens before saving
- 14 new tests in `tests/test_workflows/test_token_refresh.py`

#### Updated Existing Tests
- `test_all_workflows_list` updated from 4 → 5 workflows
- Phase-3-dependent tests updated to include % patterns in Phase 1 mock data (triggers volatility gate)

### Files Created
| File | Description |
|------|-------------|
| `src/connectors/retry.py` | `@retry_with_backoff` decorator + `is_transient_error()` |
| `src/utils/__init__.py` | Empty package init |
| `src/utils/encryption.py` | Fernet encrypt/decrypt with `enc:` prefix |
| `alembic/versions/003_add_failed_runs.py` | DLQ table migration |
| `tests/test_connectors/test_retry.py` | 86 retry tests |
| `tests/test_utils/__init__.py` | Empty package init |
| `tests/test_utils/test_encryption.py` | 23 encryption tests |
| `tests/test_workflows/test_sentry_wiring.py` | 15 Sentry wiring tests |
| `tests/test_workflows/test_dead_letter_queue.py` | 18 DLQ tests |
| `tests/test_workflows/test_token_refresh.py` | 14 token refresh tests |
| `tests/test_agent/test_graceful_degradation.py` | 15 degradation + volatility tests |

### Files Modified
- `src/config.py` — Added `token_encryption_key` setting
- `src/models/schema.py` — Added `FailedRun` model
- `src/db/service.py` — Added 5 new methods (DLQ + token refresh)
- `src/workflows/daily_briefing.py` — DLQ wrappers, Sentry capture, token refresh workflow, stale data warning, updated exports
- `src/agent/core.py` — `degradation_status`, Phase 1 fallback, `_get_last_known_analysis()`, `_compute_volatility_score()`, volatility gate
- `src/connectors/google_ads.py` — Retry decorator + Sentry + decrypt tokens
- `src/connectors/meta.py` — Retry decorator + Sentry + decrypt tokens
- `src/connectors/bigquery.py` — Retry decorator + Sentry
- `src/connectors/google_drive.py` — Retry decorator + Sentry + decrypt tokens
- `src/connectors/slack.py` — Retry decorator + Sentry
- `src/api/routes/google_ads_oauth.py` — Encrypt tokens before saving
- `src/api/routes/meta_oauth.py` — Encrypt tokens before saving
- `src/api/routes/google_drive_oauth.py` — Encrypt tokens before saving
- `tests/test_agent/test_core.py` — Updated Phase 3 tests for volatility gate
- `tests/test_workflows/test_daily_briefing.py` — Updated workflow count test

### Test Summary
- **Before:** 783 tests
- **After:** 954 tests (+171 new)
- **All passing**, ruff lint clean

### Next Session Should Start With
1. **Add more YAML skills** — Target 10-20 skills ✅ Done in Session 16
2. **End-to-end testing** with real API keys (Google Ads, Meta, BigQuery, Google Drive, Slack)
3. **Deploy to Railway** — production deployment

---

## Session 16 — Feb 13, 2026

### Summary
YAML skills expansion — 12 new skills added (3 → 15 total), covering analysis, optimization, monitoring, and reporting categories. 38 new tests (954 → 992 total), all passing, lint clean.

### What Was Done

#### 12 New YAML Skills
All skills written as YAML in `src/skills/library/` following established format with rich system_supplement, prompt_template, output_format, and business_guidance sections.

**Analysis Skills (4 new):**
1. `anomaly_detector` — Identifies metric anomalies (CPA spikes, ROAS drops), statistical deviation analysis (mean ± 2σ), root cause investigation across Google Ads + Meta + BigQuery
2. `search_term_audit` — Google Ads search term audit, negative keyword recommendations by theme, high-converting term expansion, wasted spend quantification
3. `audience_overlap` — Cross-platform audience overlap detection, attribution conflict analysis, cannibalization cost estimation, de-duplication strategies
4. `landing_page_analysis` — Landing page performance across platforms, URL normalization, page-level attribution inflation detection, optimization priorities

**Optimization Skills (3 new):**
5. `bid_strategy_review` — Evaluates Google Ads bid strategies (tCPA, tROAS, Maximize) against backend performance, identifies inflation-driven target mismatches
6. `dayparting_analysis` — Hour-of-day and day-of-week performance patterns, statistical significance checks, conversion delay accounting, schedule recommendations
7. `geo_performance` — Geographic performance breakdown by state/DMA, top/bottom geo identification, geo bid adjustment recommendations

**Monitoring Skills (3 new) — all use Haiku model, no approval required, max 5 turns:**
8. `creative_fatigue_check` — Quick ad fatigue scan (frequency + CTR trends), chains after creative_analysis
9. `budget_pacing_check` — Monthly budget pacing diagnostic, scheduled daily at noon (`0 12 * * *`)
10. `platform_health_check` — Service connectivity + data freshness check across all platforms

**Reporting Skills (2 new):**
11. `competitor_benchmark` — Industry vertical benchmark comparison from BigQuery data, percentile ranking
12. `monthly_report` — Comprehensive month-end report with Doc + Sheets + Slides deliverables, scheduled 1st of month (`0 9 1 * *`)

#### Skill Library Validation Tests
Created `tests/test_skills/test_skill_library.py` with 38 tests covering:
- All 15 skills load and validate (no errors)
- Unique IDs, valid tools/categories/platforms/models
- Monitoring skills use haiku model, no approval, low max_turns
- Scheduled skills have valid cron expressions
- chain_after references exist in registry
- Router builds 15-line index
- 15 parametrized router matching tests (one per skill)
- Category distribution and platform coverage

### Files Created
| File | Description |
|------|-------------|
| `src/skills/library/anomaly_detector.yaml` | Metric anomaly detection |
| `src/skills/library/search_term_audit.yaml` | Google Ads search term audit |
| `src/skills/library/audience_overlap.yaml` | Cross-platform audience overlap |
| `src/skills/library/landing_page_analysis.yaml` | Landing page performance |
| `src/skills/library/bid_strategy_review.yaml` | Bid strategy evaluation |
| `src/skills/library/dayparting_analysis.yaml` | Time-of-day optimization |
| `src/skills/library/geo_performance.yaml` | Geographic performance |
| `src/skills/library/creative_fatigue_check.yaml` | Quick creative fatigue scan |
| `src/skills/library/budget_pacing_check.yaml` | Budget pacing monitor |
| `src/skills/library/platform_health_check.yaml` | System health diagnostic |
| `src/skills/library/competitor_benchmark.yaml` | Industry benchmarking |
| `src/skills/library/monthly_report.yaml` | Month-end comprehensive report |
| `tests/test_skills/test_skill_library.py` | 38 skill library tests |

### Files Modified
- `CLAUDE.md` — Updated skill count (3 → 15), test count (954 → 992)
- `README.md` — Updated test count (954 → 992)
- `memory/MEMORY.md` — Updated skill count (3 → 15), test count (954 → 992)

### Test Summary
- **Before:** 954 tests
- **After:** 992 tests (+38 new)
- **All passing**, ruff lint clean

### Design Decisions
- Monitoring skills use `model: haiku` (lightweight), `requires_approval: false` (read-only), `max_turns: 5` (quick checks)
- `budget_pacing_check` auto-scheduled at noon daily
- `monthly_report` auto-scheduled on 1st of each month at 9 AM
- `creative_fatigue_check` chains after `creative_analysis` for natural drill-down
- All analysis/optimization skills require human approval before actions
- Reporting skills don't require approval (they only create deliverables, not modify campaigns)
- All skills have 8-12 tags for robust router matching

### Next Session Should Start With
1. **End-to-end testing** with real API keys (Google Ads, Meta, BigQuery, Google Drive, Slack)
2. **Deploy to Railway** — production deployment
3. **Add more YAML skills** — scale toward 50-100+ skills

## Session 17 — Feb 13, 2026

### Summary
Write operations complete. Added 12 connector write methods (6 Google Ads + 6 Meta), 4 write MCP tools, a write safety module, workflow execution steps, schema/config updates, and 126 new tests. The approval pipeline is now fully functional end-to-end: agent recommends → human approves via Slack → system executes the approved change on the ad platform.

### What Was Done

**Phase 1: Google Ads Write Methods** (`src/connectors/google_ads.py`)
- Added `GoogleAdsWriteError` exception class
- Added `_execute_mutate()` helper for all mutate operations
- 6 write methods: `update_campaign_budget`, `update_campaign_status`, `update_bid_strategy_target`, `add_negative_keywords`, `update_ad_schedule`, `update_geo_bid_modifier`
- All methods: fetch current state first (for rollback), 50% budget cap enforced, `@retry_with_backoff`, structured logging, no `@cached`

**Phase 2: Meta Write Methods** (`src/connectors/meta.py`)
- Added `MetaWriteError` exception class
- Added new imports: `Ad`, `AdSet` from `facebook_business.adobjects`
- 6 write methods: `update_campaign_status`, `update_campaign_budget`, `update_adset_status`, `update_adset_budget`, `update_ad_status`, `update_adset_bid`
- Same safety pattern: previous values stored, budget cap, structured logging

**Phase 3: Write MCP Tools + Safety Module**
- NEW: `src/mcp_servers/write_safety.py` — shared approval verification (`verify_and_load_approval`, `log_execution_start`, `record_execution_outcome`)
- Google Ads: 2 new tools (`update_google_ads_campaign`, `update_google_ads_keywords`) — compound action pattern
- Meta: 2 new tools (`update_meta_campaign`, `update_meta_ad`) — compound action + entity_type pattern
- Updated `src/agent/prompts.py` — tool lists 26 → 30

**Phase 4: Workflow Execution Step** (`src/workflows/daily_briefing.py`)
- Added `_execute_action()` routing function — routes approved actions to correct connector
- Added `execute-approved-actions` step to `daily_briefing_workflow`
- Added `execute-approved-actions` step to `skill_runner_workflow`
- Added `notify-execution` Slack notification step

**Phase 5a: Schema + Config**
- Extended `ActionType` enum with 6 new values
- Added `max_budget_change_ratio: float = 1.5` to config
- Added `record_execution_result()` to DB service
- New migration: `alembic/versions/004_add_action_types.py`

**Phase 5b: Tests** (126 new tests)
- `tests/test_connectors/test_google_ads_writes.py` — 43 tests
- `tests/test_connectors/test_meta_writes.py` — 36 tests
- `tests/test_mcp_servers/test_write_safety.py` — 9 tests
- `tests/test_mcp_servers/test_google_ads_write_mcp.py` — 22 tests (validation, actions, audit)
- `tests/test_mcp_servers/test_meta_write_mcp.py` — 30 tests (validation, entities, audit)
- `tests/test_workflows/test_execution_step.py` — 26 tests (routing, aliases, errors)
- Fixed 2 source bugs: duplicate `campaign_id` kwargs in logging for `add_negative_keywords` and `update_ad_schedule`

**Skill YAML Updates**
- 9 skills updated with write tools in `tools_required`: budget_reallocation, bid_strategy_review, search_term_audit, creative_analysis, geo_performance, dayparting_analysis, anomaly_detector, audience_overlap, landing_page_analysis

### Files Created
| File | Description |
|------|-------------|
| `src/mcp_servers/write_safety.py` | Shared approval verification for write tools |
| `alembic/versions/004_add_action_types.py` | Migration for new ActionType enum values |
| `tests/test_connectors/test_google_ads_writes.py` | 43 Google Ads write method tests |
| `tests/test_connectors/test_meta_writes.py` | 36 Meta write method tests |
| `tests/test_mcp_servers/test_write_safety.py` | 9 write safety module tests |
| `tests/test_mcp_servers/test_google_ads_write_mcp.py` | 22 Google Ads write MCP tool tests |
| `tests/test_mcp_servers/test_meta_write_mcp.py` | 30 Meta write MCP tool tests |
| `tests/test_workflows/test_execution_step.py` | 26 workflow execution step tests |

### Files Modified
| File | Changes |
|------|---------|
| `src/connectors/google_ads.py` | +6 write methods, `_execute_mutate`, `GoogleAdsWriteError` |
| `src/connectors/meta.py` | +6 write methods, `MetaWriteError`, `Ad`/`AdSet` imports |
| `src/mcp_servers/google_ads.py` | +2 write tools (5 → 7 total) |
| `src/mcp_servers/meta.py` | +2 write tools (5 → 7 total) |
| `src/agent/prompts.py` | Tool lists updated (26 → 30 tools) |
| `src/models/schema.py` | +6 ActionType enum values |
| `src/config.py` | +`max_budget_change_ratio` setting |
| `src/db/service.py` | +`record_execution_result()` method |
| `src/workflows/daily_briefing.py` | +`_execute_action()`, execution steps, Slack notification |
| 9 skill YAML files | +write tools in `tools_required` |
| `CLAUDE.md` | Updated tool/test counts, write ops |
| `README.md` | Updated tool/test counts |
| `memory/MEMORY.md` | Updated tool/test counts, write ops patterns |

### Test Summary
- **Before:** 992 tests
- **After:** 1118 tests (+126 new)
- **All passing**, ruff lint clean

### Safety Design
1. **50% budget cap** — enforced at connector level via `settings.max_budget_change_ratio`
2. **Previous values stored** — every write captures current state before mutating
3. **Double approval verification** — MCP tool checks approval, workflow step re-checks from DB
4. **No caching on writes** — `@cached` only on read methods
5. **`executed_at` prevents double-execution** — once set, the action won't re-execute
6. **Audit trail** — pre-execution log, post-execution log, result stored on ApprovalQueueItem

### Next Session Should Start With
1. **End-to-end testing** with real API keys (Google Ads, Meta, BigQuery, Google Drive, Slack)
2. **Deploy to Railway** — production deployment
3. **Add more YAML skills** — scale toward 50-100+ skills

---

## Session 18 — Feb 13, 2026

### Summary
Added folder-based skill support. Skills can now be directories containing `skill.yaml` plus `context/`, `examples/`, and `guidelines/` subdirectories. Context files (markdown) are loaded at runtime and injected into the system prompt, enabling deep domain expertise — examples, scoring rubrics, benchmarks, and decision frameworks that teach the model how to perform like an expert.

### What Was Done
1. **Schema** (`src/skills/schema.py`): Added `context_files` (tuple of glob patterns) and `source_dir` (path to skill's directory) fields to `SkillDefinition`. Added `resolve_context_files()` to resolve globs to file paths. Added `load_context_text()` to read files and compose section-headed text for prompt injection. Updated validation to warn on unresolvable patterns.
2. **Registry** (`src/skills/registry.py`): Updated `load_all()` to scan both flat `*.yaml` files AND subdirectories containing `skill.yaml`/`skill.yml`. Extracted `_load_single()` helper. Flat files and folder skills coexist; flat files load first (alphabetical), folder skills load after.
3. **Agent** (`src/agent/core.py`): Updated `run_skill()` to inject context file text between `system_supplement` and `output_format` when a skill has `context_files`. Logs `skill.context_injected` with character count.
4. **Converted `creative_analysis`** from flat `creative_analysis.yaml` to folder-based `creative_analysis/skill.yaml` with 5 context files:
   - `examples/good_analysis_ecommerce.md` — worked example of DTC creative analysis
   - `examples/good_analysis_lead_gen.md` — worked example of B2B lead gen analysis
   - `context/scoring_rubric.md` — performance tier definitions, fatigue rules, concentration risk
   - `context/platform_benchmarks.md` — Meta attribution inflation ranges, vertical benchmarks
   - `guidelines/decision_framework.md` — three-question framework, common mistakes, priority order

### Files Created (7)
- `src/skills/library/creative_analysis/skill.yaml` (moved from `creative_analysis.yaml`)
- `src/skills/library/creative_analysis/examples/good_analysis_ecommerce.md`
- `src/skills/library/creative_analysis/examples/good_analysis_lead_gen.md`
- `src/skills/library/creative_analysis/context/scoring_rubric.md`
- `src/skills/library/creative_analysis/context/platform_benchmarks.md`
- `src/skills/library/creative_analysis/guidelines/decision_framework.md`
- `tests/test_skills/test_folder_skills.py` (28 tests)

### Files Modified (6)
- `src/skills/schema.py` — context_files field, resolve_context_files(), load_context_text()
- `src/skills/registry.py` — folder skill discovery, _load_single() extraction
- `src/agent/core.py` — context injection in run_skill()
- `CLAUDE.md` — skill system docs, test count 1118→1146
- `README.md` — test count 1118→1146
- `memory/MEMORY.md` — folder skill patterns, test count

### Test Summary
- **Before:** 1118 tests
- **After:** 1146 tests (+28 new)
- **All passing**, ruff lint clean

### Architecture Note
Context files are read at execution time (not at registry load time) to keep the registry lightweight. The system prompt composition order is now:
```
BASE_SYSTEM_PROMPT
+ skill.system_supplement
+ [context files — examples, rubrics, benchmarks, guidelines]
+ # Output Format
+ # Business Guidance
```

### Next Session Should Start With
1. **Convert more skills to folder-based** — budget_reallocation, anomaly_detector are good candidates
2. **E2E testing** with real API keys
3. **Deploy to Railway**
4. **Add more YAML skills** — scale toward 50-100+
