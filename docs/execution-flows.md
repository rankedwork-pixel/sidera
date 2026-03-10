# Execution Flows Reference

This document traces the complete code path for every trigger type in Sidera — from user action or cron tick through Slack handlers, Inngest workflows, agent execution, and final output. If something isn't working, start here to understand where in the pipeline it breaks.

**Key files referenced throughout:**
- `src/api/routes/slack.py` — Slack handlers, slash commands, interactive buttons
- `src/workflows/daily_briefing.py` — All 13 Inngest workflow functions
- `src/agent/core.py` — SideraAgent (the brain)
- `src/skills/executor.py` — RoleExecutor, DepartmentExecutor
- `src/skills/manager.py` — ManagerExecutor (delegation + synthesis)

---

## Table of Contents

1. [Slash Command: `/sidera run role:X`](#1-slash-command-run-rolex)
2. [@Mention Conversation](#2-mention-conversation)
3. [Daily Cron → Role/Manager Execution](#3-daily-cron--rolemanager-execution)
4. [Approval Flow (Button Click → Execution)](#4-approval-flow-button-click--execution)
5. [Heartbeat (Proactive Investigation)](#5-heartbeat-proactive-investigation)
6. [Working Group Formation](#6-working-group-formation)
7. [Cross-Cutting Patterns](#7-cross-cutting-patterns)
8. [Quick Reference Table](#8-quick-reference-table)

---

## 1. Slash Command: `/sidera run role:X`

**Entry point:** `handle_sidera_command()` in `src/api/routes/slack.py`

```
User types: /sidera run role:ceo
         │
         ▼
handle_sidera_command()
  ├── Parse text: extract "role:ceo"
  ├── RBAC check: user must be approver+
  ├── Load registry: verify role exists
  ├── Check if role is a manager → redirect to manager workflow
  └── Emit Inngest event
         │
         ▼
┌─────────────────────────────────────────┐
│ inngest.Event("sidera/role.run")        │  ← or "sidera/manager.run"
│   data: { role_id, user_id, channel_id }│
└─────────────────────────────────────────┘
         │
         ▼
role_runner_workflow()              ← see Flow #3 for full steps
  └── Posts output to Slack channel
```

**Inngest event:** `sidera/role.run` (worker roles) or `sidera/manager.run` (managers)
**Output:** Slack message with briefing + Approve/Reject buttons for any recommendations

---

## 2. @Mention Conversation

**Entry point:** `handle_app_mention()` in `src/api/routes/slack.py`

```
User types: @Sidera talk to the CEO about system health
         │
         ▼
handle_app_mention()
  ├── Strip bot mention from text
  ├── RBAC check: user must have "chat" permission
  │
  ├── RoleRouter.identify_role() ← Two-tier matching
  │     ├── Tier 1: Regex patterns ("talk to the CEO" → ceo)
  │     └── Tier 2: Haiku semantic fallback (if Tier 1 misses)
  │
  ├── Create/get ConversationThread in DB
  │     └── Maps thread_ts → role_id (one role per thread)
  │
  └── Emit Inngest event
         │
         ▼
┌──────────────────────────────────────────────┐
│ inngest.Event("sidera/conversation.turn")     │
│   data: {                                     │
│     channel_id, thread_ts, role_id, user_id,  │
│     message_text, source_user_name            │
│   }                                           │
└──────────────────────────────────────────────┘
         │
         ▼
conversation_turn_workflow()
  ├── Step 1: load-thread → Fetch ConversationThread from DB
  ├── Step 2: check-limits → 20 turns, 24h timeout, $5 cost cap
  ├── Step 3: build-context → compose_role_context() with memory
  ├── Step 4: get-history → Fetch thread history from Slack API
  │            └── Old bot messages compressed via observation masking
  ├── Step 5: run-turn → SideraAgent.run_conversation_turn()
  │            ├── System: BASE_SYSTEM_PROMPT + role context + CONVERSATION_SUPPLEMENT
  │            ├── User: formatted thread history + new message
  │            ├── Model: Sonnet (default)
  │            └── Full MCP tool access (including delegation for managers)
  ├── Step 6: extract-recommendations → Parse JSON blocks from response
  ├── Step 7: post-reply → Slack thread reply
  │            └── If recommendations found: post Approve/Reject buttons in-thread
  ├── Step 8: auto-extract-memories → Haiku analyzes exchange (max 3 per turn)
  └── Step 9: update-thread → Increment turn_count, update cost
```

**Thread replies** from the same user trigger `handle_thread_message()` → same workflow.

**Write operations in conversations:** Agent generates JSON `{"recommendations": [...]}` blocks → system extracts, creates DB approvals, posts Approve/Reject buttons in-thread → on approval, executes via connector and posts result to thread.

**Dev-mode shortcut:** When `INNGEST_DEV=1`, `_run_conversation_turn_inline()` bypasses Inngest and runs the agent loop directly in the Slack handler (for local testing).

---

## 3. Daily Cron → Role/Manager Execution

Three levels of execution, from top-level cron to individual skill runs.

### Level 1: Daily Briefing (Legacy)

**Trigger:** Cron schedule (e.g., 7 AM weekdays)
**Workflow:** `daily_briefing_workflow()` in `src/workflows/daily_briefing.py`

```
Cron fires at scheduled time
         │
         ▼
daily_briefing_workflow()
  ├── Check briefing deduplication (skip if already ran today)
  ├── Load user accounts from DB
  └── SideraAgent.run_daily_briefing_optimized()
         │
         ▼
Three-phase model routing:
  Phase 1 (Haiku, ~$0.02): Data collection via MCP tools
  Phase 1.5 (optional): Haiku compression if Phase 1 > 8000 chars
  Phase 2 (Sonnet, ~$0.15): Tactical analysis (no tools, max_turns=1)
  Phase 3 (Opus, ~$0.35): Strategic layer (skipped if volatility < 10%)
         │
         ▼
  ├── Extract recommendations from briefing text
  ├── Post briefing to Slack
  ├── For each recommendation → create approval_queue entry + Slack buttons
  └── Wait for approval decisions (step.wait_for_event)
```

### Level 2: Role Runner

**Trigger:** Scheduler cron or `/sidera run role:X`
**Workflow:** `role_runner_workflow()` in `src/workflows/daily_briefing.py`

```
inngest.Event("sidera/role.run")
         │
         ▼
role_runner_workflow() — 13 durable steps:

  ┌─ SETUP ─────────────────────────────────────────────┐
  │ 1. load-accounts       Load accounts                │
  │ 2. load-registry       Load skill registry          │
  │ 3. load-role-memory    Get hot memories for role     │
  │ 4. load-messages       Check peer message inbox      │
  └─────────────────────────────────────────────────────┘
         │
         ▼
  ┌─ EXECUTION ─────────────────────────────────────────┐
  │ 5. compose-context     Build full role context       │
  │    └── dept.context + vocabulary + role.persona +    │
  │        principles + goals + context_files +          │
  │        memory + pending messages                     │
  │                                                      │
  │ 6. execute-role        RoleExecutor.execute()        │
  │    └── For each briefing_skill:                      │
  │        SideraAgent.run_skill(skill_definition)       │
  │    └── Merge all skill outputs                       │
  └─────────────────────────────────────────────────────┘
         │
         ▼
  ┌─ OUTPUT & LEARNING ─────────────────────────────────┐
  │ 7.  save-analysis      Store in analysis_results     │
  │ 8.  post-to-slack      Send to Slack channel         │
  │ 9.  process-recs       Create approval_queue entries │
  │ 10. extract-memories   Haiku extracts lessons        │
  │ 11. post-run-reflection Haiku: "What was hard?"      │
  │ 12. scan-lesson-friction  Detect skill friction      │
  │     └── 3+ lessons about same skill → propose change │
  │ 13. scan-gaps          Detect capability gaps        │
  │     └── 3+ gap observations → propose new role       │
  │ 14. suggest-skills     Skill-scoped gaps → message   │
  │     └── to relevant role via messaging                │
  │ 15. push-learnings     Share insights with peers     │
  │ 16. log-audit          Record in audit_log           │
  └─────────────────────────────────────────────────────┘
```

### Level 3: Manager Runner

**Trigger:** Scheduler cron or `/sidera run manager:X`
**Workflow:** `manager_runner_workflow()` in `src/workflows/daily_briefing.py`
**Executor:** `ManagerExecutor` in `src/skills/manager.py`

```
inngest.Event("sidera/manager.run")
         │
         ▼
manager_runner_workflow() — Four-phase pipeline:

  Phase 1: Own Skills
  ├── Run manager's own briefing_skills
  └── (e.g., CEO runs its own strategic overview skill)
         │
         ▼
  Phase 2: Delegation Decision
  ├── Single LLM call (Sonnet, no tools, max_turns=1)
  ├── Input: "Which sub-roles should I activate today?"
  ├── Output: List of sub-role IDs to run
  └── On LLM failure → activate ALL sub-roles (safe fallback)
         │
         ▼
  Phase 3: Sub-Role Execution (sequential, checkpointed)
  ├── For each activated sub-role:
  │   └── RoleExecutor.execute(sub_role_id) — inline in workflow
  └── Results collected for synthesis
         │
         ▼
  Phase 4: Synthesis
  ├── Single LLM call (Sonnet, no tools, max_turns=1)
  ├── Input: Manager's output + all sub-role outputs
  └── Output: Unified briefing with cross-functional insights
         │
         ▼
  Post-execution: same as role runner (memory, reflection, Slack, audit)
```

**Key detail:** Sub-roles run inline in the manager workflow (not separate Inngest events), so results stay in scope for synthesis. Recursive managers supported with depth limit (max 3).

---

## 4. Approval Flow (Button Click → Execution)

**Entry point:** `handle_approve()` / `handle_reject()` in `src/api/routes/slack.py`

```
User clicks [Approve] button on Slack message
         │
         ▼
handle_approve()
  ├── ack() — Must respond within 3 seconds (Slack timeout)
  ├── Extract: approval_id, user_id, channel_id, message_ts
  ├── RBAC check: user must have "approve" permission
  │
  ├── Update Slack message: replace buttons with "Approved by @user"
  │
  ├── Save to DB:
  │   approval_queue.status = "APPROVED"
  │   approval_queue.decided_by = user_id
  │   approval_queue.decided_at = now()
  │
  └── Emit Inngest event
         │
         ▼
┌──────────────────────────────────────────────┐
│ inngest.Event("sidera/approval.decided")      │
│   data: { approval_id, status, decided_by }   │
└──────────────────────────────────────────────┘
         │
         ▼
Calling workflow resumes (step.wait_for_event unblocks):

  Pre-execution checks:
  ├── _capture_evidence_snapshot()  Capture current state
  ├── _check_lessons_before_action() Check role memory for warnings
  └── verify_and_load_approval()   Confirm still valid, not already executed
         │
         ▼
  Route to connector:
  ├── action_params["platform"] determines which connector
  ├── action_type determines which method
  │   └── Each connector exposes read + write methods;
  │       the action_type maps to the appropriate write method
  │
  ├── Safety: connector-level caps enforce change limits
  ├── Safety: executed_at field prevents double-execution
  └── Safety: write_safety module logs start/outcome
         │
         ▼
  ├── Record execution result in approval_queue
  ├── Post result to Slack (channel or thread)
  └── Log to audit_log
```

### Auto-Execute Path (Tier 2)

For actions matching pre-approved rules in `_rules.yaml`:

```
Agent generates recommendation
         │
         ▼
process_recommendations()
  ├── For each recommendation:
  │   ├── Create approval_queue entry
  │   ├── should_auto_execute() evaluates rules:
  │   │   ├── Condition match (AND logic, 10 operators)
  │   │   ├── Constraint check (daily limits, cooldowns, platform)
  │   │   ├── Skill proposals NEVER auto-execute (hard block)
  │   │   ├── Pre-action lesson check (high-confidence contradictions block)
  │   │   └── Global kill switch: AUTO_EXECUTE_ENABLED (default OFF)
  │   │
  │   ├── If matched → status = "AUTO_APPROVED"
  │   │   ├── Execute immediately via connector
  │   │   └── Post Slack notification (so humans can review after the fact)
  │   │
  │   └── If no match → status = "pending" + Slack Approve/Reject buttons
```

---

## 5. Heartbeat (Proactive Investigation)

**Trigger:** Role's `heartbeat_schedule` cron field (e.g., `*/15 * * * *`)
**Workflow:** `heartbeat_runner_workflow()` in `src/workflows/daily_briefing.py`

```
Scheduler detects heartbeat_schedule matches current time
         │
         ▼
┌──────────────────────────────────────────────┐
│ inngest.Event("sidera/heartbeat.run")         │
│   data: { role_id, user_id, channel_id }      │
└──────────────────────────────────────────────┘
         │
         ▼
heartbeat_runner_workflow()
  ├── Step 1: check-cooldown — Skip if ran too recently
  ├── Step 2: load-registry
  ├── Step 3: compose-context — Full role context with memory
  ├── Step 4: load-messages — Pending peer messages
  │
  ├── Step 5: run-heartbeat-turn
  │   └── SideraAgent.run_heartbeat_turn()
  │       ├── System: BASE_SYSTEM_PROMPT + role context + HEARTBEAT_SUPPLEMENT
  │       ├── User: Open-ended investigative prompt
  │       │   └── "Check your domain. Investigate anything that seems off."
  │       ├── Model: heartbeat_model (usually Haiku) or settings.model_fast
  │       ├── Max 5 agent turns, 15 tool calls, $0.50 cost cap
  │       └── Full MCP tool access (read + write, write still approval-gated)
  │
  ├── Step 6: check-findings — Did the agent find anything?
  │   ├── No findings → silent (no Slack post)
  │   └── Has findings → continue
  │
  ├── Step 7: post-findings → Slack message to role's channel
  ├── Step 8: extract-memories
  └── Step 9: log-audit
```

**Example:** Head of IT heartbeats every 15 min 24/7. Checks system health, DLQ, approval queue, costs. Only posts to Slack when something is wrong.

**Cost:** ~$0.02-0.10 per heartbeat (Haiku data collection, most are "all clear")

---

## 6. Working Group Formation

**Entry point:** `form_working_group` MCP tool in `src/mcp_servers/working_group.py`

```
Manager agent calls form_working_group() during execution:
  objective: "Analyze system performance across all services"
  coordinator_role_id: "ceo"
  member_role_ids: ["analyst", "engineer"]
         │
         ▼
form_working_group() MCP tool:
  ├── Validation:
  │   ├── Coordinator must be a manager role
  │   ├── Max 10 members
  │   ├── No duplicates, no self-inclusion
  │   └── Cost cap validation
  ├── Generate group_id (UUID)
  ├── Create WorkingGroupSession in DB (status = "forming")
  └── Emit Inngest event
         │
         ▼
┌──────────────────────────────────────────────┐
│ inngest.Event("sidera/working_group.run")     │
│   data: {                                     │
│     group_id, objective, coordinator_role_id, │
│     member_role_ids, cost_cap_usd             │
│   }                                           │
└──────────────────────────────────────────────┘
         │
         ▼
working_group_workflow() — 6 phases:

  Phase 1: Setup
  ├── Load coordinator and member role definitions
  └── DB status → "planning"
         │
         ▼
  Phase 2: Planning (Coordinator LLM call)
  ├── Sonnet: "Divide this objective among your team members"
  ├── Output: WorkingGroupPlan with per-member task assignments
  └── DB status → "executing"
         │
         ▼
  Phase 3-N: Member Execution (sequential, checkpointed)
  ├── For each member:
  │   └── RoleExecutor.execute(member_role_id)
  │       └── With injected context: group objective + individual task
  ├── Cost tracking per member
  ├── Stop if total cost exceeds cap
  └── Results stored in working_group_sessions.member_results_json
         │
         ▼
  Phase N+1: Synthesis (Coordinator LLM call)
  ├── Sonnet: Combine all member outputs into group conclusion
  └── DB status → "synthesizing" → "completed"
         │
         ▼
  Phase N+2: Post Results
  ├── Post to Slack thread or channel
  ├── Include each member's output and final synthesis
  └── Create approval_queue entries for recommendations
```

---

## 7. Cross-Cutting Patterns

Every execution flow shares these common steps:

### Context Assembly
```
compose_role_context() in src/skills/executor.py:
  ┌─ STABLE (KV-cache friendly, goes first) ─────────────┐
  │  Department context + vocabulary                       │
  │  Role persona                                          │
  │  Principles ("Decision-Making Principles")             │
  │  Goals ("Active Goals — filter every decision")        │
  │  Context files (from YAML globs)                       │
  │  Team awareness (who reports to this role)              │
  └────────────────────────────────────────────────────────┘
  ┌─ DYNAMIC (attention edge, goes last) ─────────────────┐
  │  Memory context (hot memories, sorted by confidence)   │
  │  Pending messages (from peer roles)                    │
  └────────────────────────────────────────────────────────┘
```

### Memory Lifecycle
```
Before execution:
  load hot memories → inject into context (capped at 2000 tokens)
  if >20 memories → inject compact index instead (title + ID only)

After execution:
  1. extract-memories    Haiku: identify lessons/insights from output
  2. post-run-reflection Haiku: "What was hard? What data was missing?"
  3. scan-friction       3+ lessons about same skill → propose changes
  4. scan-gaps           3+ gap observations → propose new role
  5. push-learnings      Share flagged insights with peer roles
```

### Write Operation Safety Chain
```
Agent generates recommendation
  └── process_recommendations()
      ├── Create approval_queue entry
      ├── Check auto-execute rules (_rules.yaml)
      │   ├── Condition evaluation (AND logic, 10 operators)
      │   ├── Constraint checking (daily limits, cooldowns)
      │   ├── Lesson contradiction check (blocks if high-confidence warning)
      │   └── Global kill switch (AUTO_EXECUTE_ENABLED, default OFF)
      │
      ├── Auto-approve path:
      │   ├── status = "AUTO_APPROVED"
      │   ├── Execute immediately
      │   └── Post Slack notification
      │
      └── Manual path:
          ├── Post Approve/Reject buttons to Slack
          ├── step.wait_for_event("sidera/approval.decided")
          ├── On approve: execute via connector
          │   ├── Evidence snapshot (capture pre-action state)
          │   ├── Connector-level safety caps
          │   ├── Double-execution prevention (executed_at field)
          │   └── write_safety module logging
          └── On reject: log rejection reason
```

### Inngest Event Reference
```
sidera/briefing.run         → daily_briefing_workflow
sidera/role.run             → role_runner_workflow
sidera/manager.run          → manager_runner_workflow
sidera/department.run       → department_runner_workflow
sidera/conversation.turn    → conversation_turn_workflow
sidera/heartbeat.run        → heartbeat_runner_workflow
sidera/approval.decided     → (resumes waiting workflow)
sidera/working_group.run    → working_group_workflow
sidera/skill.run            → skill_runner_workflow
```

---

## 8. Quick Reference Table

| Flow | Trigger | Slack Handler | Inngest Event | Workflow Function | Agent Method | Output |
|------|---------|---------------|---------------|-------------------|--------------|--------|
| Run role | `/sidera run role:X` | `handle_sidera_command()` | `sidera/role.run` | `role_runner_workflow()` | `RoleExecutor.execute()` | Slack + approvals |
| Conversation | `@Sidera ...` | `handle_app_mention()` | `sidera/conversation.turn` | `conversation_turn_workflow()` | `run_conversation_turn()` | Thread reply + approvals |
| Daily cron | Scheduler | — | `sidera/role.run` | `role_runner_workflow()` | `run_skill()` | Slack briefing |
| Manager run | Scheduler or command | `handle_sidera_command()` | `sidera/manager.run` | `manager_runner_workflow()` | `ManagerExecutor` | Slack + approvals |
| Approval | Button click | `handle_approve()` | `sidera/approval.decided` | (resumes caller) | Connector write | Execution result |
| Heartbeat | Role cron | — | `sidera/heartbeat.run` | `heartbeat_runner_workflow()` | `run_heartbeat_turn()` | Slack (if findings) |
| Working group | MCP tool | — | `sidera/working_group.run` | `working_group_workflow()` | `RoleExecutor` per member | Slack thread |
