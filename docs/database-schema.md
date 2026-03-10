# Database Schema Reference

This document covers every table, column, index, enum, and relationship in Sidera's PostgreSQL database. If you're writing a migration, debugging a query, or trying to understand where data lives — start here.

**Database:** PostgreSQL via Supabase
**ORM:** SQLAlchemy (async, via `src/db/session.py`)
**Migrations:** Alembic (29 revisions in `alembic/versions/`)
**Models:** `src/models/schema.py`
**CRUD:** `src/db/service.py` (115 methods)
**Row Level Security:** Enabled on all 19 tables (migration 027)

---

## Table of Contents

1. [Enums](#enums)
2. [Core Tables](#core-tables) — users, accounts, campaigns, daily_metrics
3. [Agent Operations](#agent-operations) — analysis_results, approval_queue, audit_log, cost_tracking
4. [Failure Recovery](#failure-recovery) — failed_runs
5. [Role Memory](#role-memory) — role_memory
6. [Conversations](#conversations) — conversation_threads
7. [Dynamic Org Chart](#dynamic-org-chart) — org_departments, org_roles, org_skills
8. [Meetings](#meetings) — meeting_sessions
9. [Messaging](#messaging) — role_messages
10. [Claude Code Tasks](#claude-code-tasks) — claude_code_tasks
11. [Webhooks](#webhooks) — webhook_events
12. [Working Groups](#working-groups) — working_group_sessions
13. [Migration History](#migration-history)
14. [Entity Relationship Summary](#entity-relationship-summary)

---

## Enums

### ActionType
Classifies what an approved action does. Used in `approval_queue.action_type`.

| Value | Meaning |
|-------|---------|
| `recommendation_accept` | Accept an agent's recommendation |
| `recommendation_reject` | Reject an agent's recommendation |
| `skill_proposal` | Agent proposes a skill definition change |
| `role_proposal` | Agent proposes a new role or role modification |
| `claude_code_task` | Claude Code headless task execution |
| `custom_action` | Domain-specific action defined by connectors |

### ApprovalStatus
Lifecycle state of an item in the approval queue.

| Value | Meaning |
|-------|---------|
| `pending` | Awaiting human decision |
| `approved` | Human clicked Approve |
| `rejected` | Human clicked Reject |
| `expired` | Timed out without a decision |
| `auto_approved` | Matched an auto-execute rule (Tier 2) |

### MemoryType
Classifies persistent role memories. Used as string values in `role_memory.memory_type`.

| Value | Purpose | Example |
|-------|---------|---------|
| `decision` | Approval outcomes | "Approved restarting the monitoring service" |
| `anomaly` | Performance spikes/drops | "Error rate spiked 40% on Tuesday" |
| `pattern` | Recurring trends | "Performance dips every Monday morning" |
| `insight` | Strategic learnings | "Service restarts resolve 80% of transient errors" |
| `lesson` | "I tried X, it failed because Y" | "Scaling up without load testing caused cascading failures" |
| `commitment` | Conversational promises | "I'll investigate the latency spike tomorrow" |
| `relationship` | Inter-role context | "The on-call engineer prefers detailed incident reports" |
| `steward_note` | Human-injected guidance (highest priority, agent can't create) | "Focus on uptime over feature velocity this quarter" |
| `cross_role_insight` | Learnings from peer roles | "Security team reports patching correlates with brief latency spikes" |

### ClearanceLevel
Information security classification. Used in `users.clearance_level` and `org_roles.clearance_level`.

| Value | Access |
|-------|--------|
| `public` | No restrictions |
| `internal` | Standard agent/employee access |
| `confidential` | Sensitive business data |
| `restricted` | Highest security (PII, financials) |

### WorkingGroupStatus
Lifecycle state for multi-agent working group sessions.

| Value | Phase |
|-------|-------|
| `forming` | Group created, not yet planned |
| `planning` | Coordinator assigning tasks |
| `executing` | Members running tasks in parallel |
| `synthesizing` | Coordinator combining outputs |
| `completed` | Successfully finished |
| `failed` | Error during execution |

---

## Core Tables

### `users`
Registered Sidera users (Slack users who interact with the system).

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | INTEGER | NOT NULL | auto PK | Primary key |
| `slack_user_id` | VARCHAR(255) | NOT NULL | — | UNIQUE; Slack user ID (e.g., `U123ABC`) |
| `email` | VARCHAR(255) | YES | — | |
| `name` | VARCHAR(255) | YES | — | Display name |
| `role` | VARCHAR(50) | YES | `"viewer"` | RBAC role: `viewer`, `approver`, `admin` |
| `clearance_level` | VARCHAR(20) | NOT NULL | `"internal"` | ClearanceLevel enum value |
| `is_active` | BOOLEAN | YES | `True` | Soft delete |
| `created_at` | TIMESTAMP | YES | `now()` | |
| `updated_at` | TIMESTAMP | YES | `now()` | Auto-updates |

**Indexes:** `ix_users_slack_user_id` (UNIQUE on `slack_user_id`)

---

### `accounts`
Connected external service accounts.

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | INTEGER | NOT NULL | auto PK | Primary key |
| `user_id` | VARCHAR(255) | NOT NULL | — | Indexed |
| `platform` | VARCHAR(50) | NOT NULL | — | Platform string (e.g., `custom` or any connector name) |
| `account_id` | VARCHAR(255) | NOT NULL | — | Platform-native account ID |
| `account_name` | VARCHAR(255) | YES | — | |
| `credentials_encrypted` | TEXT | YES | — | Fernet-encrypted OAuth tokens |
| `is_active` | BOOLEAN | YES | `True` | |
| `created_at` | TIMESTAMP | YES | `now()` | |
| `updated_at` | TIMESTAMP | YES | `now()` | Auto-updates |

**Indexes:** `ix_accounts_user_id` (on `user_id`), `ix_accounts_user_platform` (composite on `user_id, platform`)

---

### `campaigns`
Unified campaign records across all platforms.

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | INTEGER | NOT NULL | auto PK | Primary key |
| `account_id` | INTEGER | NOT NULL | — | FK → `accounts.id` |
| `platform_campaign_id` | VARCHAR(255) | NOT NULL | — | Native platform ID |
| `name` | VARCHAR(500) | YES | — | |
| `campaign_type` | VARCHAR(100) | YES | — | search, display, shopping, pmax, etc. |
| `status` | VARCHAR(50) | YES | — | enabled, paused, removed |
| `daily_budget` | NUMERIC(12,2) | YES | — | In account currency |
| `monthly_budget` | NUMERIC(12,2) | YES | — | |
| `platform_data` | JSONB | YES | — | Platform-specific fields |
| `created_at` | TIMESTAMP | YES | `now()` | |
| `updated_at` | TIMESTAMP | YES | `now()` | Auto-updates |

**Foreign Keys:** `account_id` → `accounts.id`
**Indexes:** `ix_campaigns_account_id` (on `account_id`)

---

### `daily_metrics`
Daily performance metrics per campaign. Stores both platform-reported and normalized values.

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | INTEGER | NOT NULL | auto PK | Primary key |
| `campaign_id` | INTEGER | NOT NULL | — | FK → `campaigns.id` |
| `date` | DATE | NOT NULL | — | |
| `impressions` | INTEGER | YES | `0` | |
| `clicks` | INTEGER | YES | `0` | |
| `conversions` | NUMERIC(10,2) | YES | `0` | |
| `conversion_value` | NUMERIC(12,2) | YES | `0` | Revenue |
| `cost` | NUMERIC(12,2) | YES | `0` | Spend |
| `ctr` | NUMERIC(8,4) | YES | `0` | Click-through rate |
| `cpc` | NUMERIC(8,2) | YES | `0` | Cost per click |
| `cpa` | NUMERIC(10,2) | YES | `0` | Cost per acquisition |
| `roas` | NUMERIC(8,4) | YES | `0` | Return on ad spend |
| `platform_data` | JSONB | YES | — | Raw platform-specific metrics |
| `created_at` | TIMESTAMP | YES | `now()` | |

**Foreign Keys:** `campaign_id` → `campaigns.id`
**Indexes:** `ix_metrics_campaign_id` (on `campaign_id`), `ix_metrics_campaign_date` (composite on `campaign_id, date`)

---

## Agent Operations

### `analysis_results`
Stores the output of every agent run (briefings, skill executions, conversations).

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | INTEGER | NOT NULL | auto PK | Primary key |
| `user_id` | VARCHAR(255) | NOT NULL | — | Indexed |
| `run_date` | DATE | NOT NULL | — | |
| `analysis_type` | VARCHAR(100) | NOT NULL | — | daily_analysis, budget_optimization, etc. |
| `model_used` | VARCHAR(100) | YES | — | Claude model ID |
| `input_tokens` | INTEGER | YES | `0` | |
| `output_tokens` | INTEGER | YES | `0` | |
| `cost_usd` | NUMERIC(8,4) | YES | `0` | |
| `summary` | TEXT | YES | — | Agent's analysis output |
| `recommendations` | JSONB | YES | — | Structured recommendations |
| `raw_response` | TEXT | YES | — | Full LLM response |
| `skill_id` | VARCHAR(100) | YES | NULL | NULL for legacy non-skill runs |
| `department_id` | VARCHAR(100) | YES | NULL | |
| `role_id` | VARCHAR(100) | YES | NULL | |
| `context_text` | TEXT | YES | — | System prompt context used |
| `created_at` | TIMESTAMP | YES | `now()` | |

**Indexes:** `ix_analysis_user_id`, `ix_analysis_user_date` (composite), `ix_analysis_skill`, `ix_analysis_role`, `ix_analysis_department`

---

### `approval_queue`
Human approval queue for agent-recommended actions. Actions flow: pending → approved/rejected/expired/auto_approved → executed.

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | INTEGER | NOT NULL | auto PK | Primary key |
| `analysis_id` | INTEGER | YES | NULL | FK → `analysis_results.id`; nullable for skill proposals |
| `user_id` | VARCHAR(255) | NOT NULL | — | Indexed |
| `action_type` | ENUM(ActionType) | NOT NULL | — | What the action does |
| `account_id` | INTEGER | YES | NULL | FK → `accounts.id`; nullable for skill proposals |
| `campaign_id` | INTEGER | YES | — | FK → `campaigns.id` |
| `description` | TEXT | NOT NULL | — | Human-readable description |
| `reasoning` | TEXT | YES | — | Agent's reasoning |
| `action_params` | JSONB | NOT NULL | — | Parameters for execution |
| `projected_impact` | TEXT | YES | — | Expected outcome |
| `risk_assessment` | TEXT | YES | — | Potential downsides |
| `status` | ENUM(ApprovalStatus) | YES | `pending` | Current state |
| `decided_at` | TIMESTAMP | YES | — | When approved/rejected |
| `decided_by` | VARCHAR(255) | YES | — | Slack user ID |
| `rejection_reason` | TEXT | YES | — | |
| `executed_at` | TIMESTAMP | YES | — | When action was executed (prevents double-execution) |
| `execution_result` | JSONB | YES | — | Execution outcome |
| `execution_error` | TEXT | YES | — | Error if execution failed |
| `auto_execute_rule_id` | VARCHAR(100) | YES | NULL | Rule ID if auto-approved |
| `slack_message_ts` | VARCHAR(100) | YES | — | Slack message timestamp |
| `slack_channel_id` | VARCHAR(100) | YES | — | |
| `steward_user_id` | VARCHAR(255) | YES | NULL | Steward at time of creation |
| `created_at` | TIMESTAMP | YES | `now()` | |
| `expires_at` | TIMESTAMP | YES | — | Auto-expire pending items |

**Foreign Keys:** `analysis_id` → `analysis_results.id`, `account_id` → `accounts.id`, `campaign_id` → `campaigns.id`
**Indexes:** `ix_approval_user_id`, `ix_approval_user_status` (composite)

---

### `audit_log`
Complete audit trail of every agent action. Immutable once written.

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | INTEGER | NOT NULL | auto PK | Primary key |
| `user_id` | VARCHAR(255) | NOT NULL | — | Indexed |
| `account_id` | INTEGER | YES | — | FK → `accounts.id` |
| `event_type` | VARCHAR(100) | NOT NULL | — | analysis_run, recommendation, action_executed, etc. |
| `event_data` | JSONB | YES | — | Full event payload |
| `source` | VARCHAR(100) | YES | — | daily_briefing, manual_query, skill_run, etc. |
| `agent_model` | VARCHAR(100) | YES | — | Claude model used |
| `skill_id` | VARCHAR(100) | YES | NULL | |
| `department_id` | VARCHAR(100) | YES | NULL | |
| `role_id` | VARCHAR(100) | YES | NULL | |
| `steward_user_id` | VARCHAR(255) | YES | NULL | Immutable snapshot of steward at event time |
| `required_approval` | BOOLEAN | YES | `False` | |
| `approval_status` | VARCHAR(50) | YES | — | |
| `approved_by` | VARCHAR(255) | YES | — | |
| `created_at` | TIMESTAMP | YES | `now()` | |

**Foreign Keys:** `account_id` → `accounts.id`
**Indexes:** `ix_audit_user_id`, `ix_audit_user_created` (composite), `ix_audit_event_type`, `ix_audit_skill`, `ix_audit_role`, `ix_audit_department`

---

### `cost_tracking`
Per-run LLM cost tracking for circuit breakers and billing.

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | INTEGER | NOT NULL | auto PK | Primary key |
| `user_id` | VARCHAR(255) | NOT NULL | — | |
| `account_id` | INTEGER | YES | — | FK → `accounts.id` |
| `run_date` | DATE | NOT NULL | — | |
| `model` | VARCHAR(100) | NOT NULL | — | Claude model identifier |
| `input_tokens` | INTEGER | YES | `0` | |
| `output_tokens` | INTEGER | YES | `0` | |
| `cost_usd` | NUMERIC(8,4) | YES | `0` | |
| `operation` | VARCHAR(100) | YES | — | daily_analysis, budget_optimization, etc. |
| `created_at` | TIMESTAMP | YES | `now()` | |

**Foreign Keys:** `account_id` → `accounts.id`
**Indexes:** `ix_cost_user_date` (composite), `ix_cost_account_date` (composite)

---

## Failure Recovery

### `failed_runs`
Dead letter queue (DLQ) for workflow failures. Stores enough context to replay the event.

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | INTEGER | NOT NULL | auto PK | Primary key |
| `workflow_name` | VARCHAR(255) | NOT NULL | — | e.g., `daily_briefing_workflow` |
| `event_name` | VARCHAR(255) | NOT NULL | — | Inngest event name |
| `event_data` | JSONB | YES | — | Full event data for replay |
| `error_message` | TEXT | YES | — | |
| `error_type` | VARCHAR(255) | YES | — | Exception class name |
| `user_id` | VARCHAR(255) | YES | — | |
| `run_id` | VARCHAR(255) | YES | — | Inngest run ID |
| `retry_count` | INTEGER | YES | `0` | |
| `created_at` | TIMESTAMP | YES | `now()` | |
| `resolved_at` | TIMESTAMP | YES | — | Set when investigated |
| `resolved_by` | VARCHAR(255) | YES | — | |

**Indexes:** `ix_failed_runs_user`, `ix_failed_runs_workflow`

---

## Role Memory

### `role_memory`
Persistent memory for AI roles. Accumulates across runs. Tiered: hot (≤90 days, auto-injected) and cold (archived, searchable on demand).

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | INTEGER | NOT NULL | auto PK | Primary key |
| `user_id` | VARCHAR(255) | NOT NULL | — | |
| `role_id` | VARCHAR(100) | NOT NULL | — | |
| `department_id` | VARCHAR(100) | YES | — | |
| `memory_type` | VARCHAR(50) | NOT NULL | — | See MemoryType enum |
| `title` | VARCHAR(500) | NOT NULL | — | |
| `content` | TEXT | NOT NULL | — | |
| `confidence` | FLOAT | YES | `1.0` | 0.0–1.0; steward_notes fixed at 1.0 |
| `source_skill_id` | VARCHAR(200) | YES | — | Which skill produced this memory |
| `source_run_date` | DATE | YES | — | |
| `evidence` | JSONB | YES | — | Structured supporting data |
| `expires_at` | TIMESTAMP | YES | — | NULL = never (e.g., steward_notes) |
| `is_archived` | BOOLEAN | YES | `False` | True = cold archive |
| `source_role_id` | VARCHAR(100) | YES | NULL | For cross-role learnings |
| `supersedes_id` | INTEGER | YES | NULL | FK → self; memory versioning chain |
| `consolidated_into_id` | INTEGER | YES | NULL | FK → self; set by weekly consolidation |
| `created_at` | TIMESTAMP | YES | `now()` | |
| `updated_at` | TIMESTAMP | YES | `now()` | Auto-updates |

**Self-Referential FKs:** `supersedes_id` → `role_memory.id` (ON DELETE SET NULL), `consolidated_into_id` → `role_memory.id` (ON DELETE SET NULL)
**Indexes:** `ix_role_memory_lookup` (composite: `user_id, role_id, is_archived`), `ix_role_memory_type`, `ix_role_memory_expiry`, `ix_role_memory_supersedes_id`, `ix_role_memory_consolidated_into_id`, `ix_role_memory_source_role` (composite: `source_role_id, role_id`), `ix_role_memory_role_active_time` (composite: `role_id, is_archived, created_at`), `ix_role_memory_unconsolidated` (PARTIAL: `role_id, confidence DESC WHERE consolidated_into_id IS NULL`)

---

## Conversations

### `conversation_threads`
Maps Slack threads to Sidera roles for conversation mode. One record per thread.

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | INTEGER | NOT NULL | auto PK | Primary key |
| `thread_ts` | VARCHAR(100) | NOT NULL | — | UNIQUE; Slack thread timestamp |
| `channel_id` | VARCHAR(100) | NOT NULL | — | |
| `role_id` | VARCHAR(100) | NOT NULL | — | Which role owns this thread |
| `user_id` | VARCHAR(255) | NOT NULL | — | Who started the conversation |
| `started_at` | TIMESTAMP | YES | `now()` | |
| `last_activity_at` | TIMESTAMP | YES | `now()` | Auto-updates on each turn |
| `turn_count` | INTEGER | YES | `0` | Incremented each reply |
| `is_active` | BOOLEAN | YES | `True` | False = timed out or closed |
| `total_cost_usd` | NUMERIC(8,4) | YES | `0` | Accumulated per-turn cost |

**Indexes:** `ix_conversation_threads_thread_ts` (UNIQUE), `ix_conv_thread_channel` (composite: `channel_id, thread_ts`), `ix_conv_thread_role`, `ix_conv_thread_active` (composite: `is_active, last_activity_at`)

---

## Dynamic Org Chart

These three tables form the DB override layer for the YAML-based skill system. YAML on disk is the seed data; DB entries with the same ID **replace** disk entries entirely.

### `org_departments`
Dynamic department definitions.

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | INTEGER | NOT NULL | auto PK | Primary key |
| `dept_id` | VARCHAR(100) | NOT NULL | — | UNIQUE; business key |
| `name` | VARCHAR(255) | NOT NULL | — | |
| `description` | TEXT | NOT NULL | `""` | |
| `context` | TEXT | YES | `""` | Shared context for all roles in this dept |
| `context_text` | TEXT | YES | `""` | Pre-rendered context for DB-defined depts |
| `steward_user_id` | VARCHAR(255) | YES | `""` | |
| `slack_channel_id` | VARCHAR(100) | YES | `""` | Dept-scoped Slack channel |
| `credentials_scope` | VARCHAR(50) | YES | `""` | Env var prefix for dept-scoped creds |
| `vocabulary` | JSONB | YES | NULL | Domain terminology: `[{term, definition}]` |
| `is_active` | BOOLEAN | YES | `True` | Soft delete |
| `created_by` | VARCHAR(255) | YES | `""` | |
| `created_at` | TIMESTAMP | YES | `now()` | |
| `updated_at` | TIMESTAMP | YES | `now()` | Auto-updates |

**Indexes:** `ix_org_departments_dept_id` (UNIQUE), `ix_org_dept_active`

---

### `org_roles`
Dynamic role definitions.

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | INTEGER | NOT NULL | auto PK | Primary key |
| `role_id` | VARCHAR(100) | NOT NULL | — | UNIQUE; business key |
| `name` | VARCHAR(255) | NOT NULL | — | |
| `department_id` | VARCHAR(100) | NOT NULL | — | Indexed |
| `description` | TEXT | NOT NULL | `""` | |
| `persona` | TEXT | YES | `""` | Injected into system prompt |
| `connectors` | JSONB | YES | `[]` | Connector names |
| `briefing_skills` | JSONB | YES | `[]` | Ordered skill IDs |
| `schedule` | VARCHAR(100) | YES | NULL | Cron expression |
| `context_text` | TEXT | YES | `""` | Pre-rendered context |
| `principles` | JSONB | YES | `[]` | Decision-making heuristics |
| `manages` | JSONB | YES | `[]` | Sub-role IDs (makes this a manager) |
| `delegation_model` | VARCHAR(50) | YES | `"standard"` | `standard` (Sonnet) or `fast` (Haiku) |
| `synthesis_prompt` | TEXT | YES | `""` | Manager synthesis instructions |
| `clearance_level` | VARCHAR(20) | NOT NULL | `"internal"` | |
| `steward_user_id` | VARCHAR(255) | YES | `""` | |
| `learning_channels` | JSONB | YES | `[]` | Role IDs allowed to push learnings |
| `document_sync` | JSONB | YES | NULL | Maps output types → Google Doc IDs |
| `goals` | JSONB | YES | NULL | Role-level goals |
| `is_active` | BOOLEAN | YES | `True` | Soft delete |
| `created_by` | VARCHAR(255) | YES | `""` | |
| `created_at` | TIMESTAMP | YES | `now()` | |
| `updated_at` | TIMESTAMP | YES | `now()` | Auto-updates |

**Indexes:** `ix_org_roles_role_id` (UNIQUE), `ix_org_roles_department_id`, `ix_org_role_active`

---

### `org_skills`
Dynamic skill definitions.

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | INTEGER | NOT NULL | auto PK | Primary key |
| `skill_id` | VARCHAR(100) | NOT NULL | — | UNIQUE; business key |
| `name` | VARCHAR(255) | NOT NULL | — | |
| `version` | VARCHAR(20) | YES | `"1.0"` | |
| `description` | TEXT | NOT NULL | `""` | |
| `category` | VARCHAR(50) | NOT NULL | — | analysis, monitoring, reporting, etc. |
| `platforms` | JSONB | YES | `[]` | |
| `tags` | JSONB | YES | `[]` | |
| `tools_required` | JSONB | YES | `[]` | MCP tool names |
| `model` | VARCHAR(20) | YES | `"sonnet"` | haiku, sonnet, or opus |
| `max_turns` | INTEGER | YES | `20` | 1–50 |
| `system_supplement` | TEXT | NOT NULL | `""` | Appended to system prompt |
| `prompt_template` | TEXT | NOT NULL | `""` | User-turn prompt |
| `output_format` | TEXT | NOT NULL | `""` | Output structure instructions |
| `business_guidance` | TEXT | NOT NULL | `""` | Business context |
| `context_text` | TEXT | YES | `""` | Pre-rendered context files |
| `schedule` | VARCHAR(100) | YES | NULL | Cron expression |
| `chain_after` | VARCHAR(100) | YES | NULL | Skill ID to run after this one |
| `requires_approval` | BOOLEAN | YES | `True` | FORBIDDEN_FIELD — agents cannot modify |
| `min_clearance` | VARCHAR(20) | NOT NULL | `"public"` | Minimum clearance to run |
| `references` | JSONB | YES | `[]` | Cross-skill reference graph |
| `department_id` | VARCHAR(100) | YES | `""` | Indexed |
| `role_id` | VARCHAR(100) | YES | `""` | Indexed |
| `author` | VARCHAR(100) | YES | `"sidera"` | |
| `is_active` | BOOLEAN | YES | `True` | Soft delete |
| `created_by` | VARCHAR(255) | YES | `""` | |
| `created_at` | TIMESTAMP | YES | `now()` | |
| `updated_at` | TIMESTAMP | YES | `now()` | Auto-updates |

**Indexes:** `ix_org_skills_skill_id` (UNIQUE), `ix_org_skills_department_id`, `ix_org_skills_role_id`, `ix_org_skill_active`

---

## Meetings

### `meeting_sessions`
Listen-only meeting sessions via Recall.ai bot.

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | INTEGER | NOT NULL | auto PK | Primary key |
| `meeting_url` | VARCHAR(500) | NOT NULL | — | Google Meet, Zoom, Teams, etc. |
| `role_id` | VARCHAR(100) | NOT NULL | — | Participating role |
| `user_id` | VARCHAR(255) | NOT NULL | — | Who initiated |
| `bot_id` | VARCHAR(255) | YES | NULL | Recall.ai bot UUID |
| `status` | VARCHAR(50) | YES | `"joining"` | joining → in_call → ended → delegating → completed |
| `started_at` | TIMESTAMP | YES | `now()` | |
| `joined_at` | TIMESTAMP | YES | NULL | |
| `ended_at` | TIMESTAMP | YES | NULL | |
| `transcript_json` | JSONB | YES | `[]` | Accumulated transcript |
| `transcript_summary` | TEXT | YES | `""` | LLM-generated summary |
| `action_items_json` | JSONB | YES | `[]` | Extracted action items |
| `delegation_result_id` | INTEGER | YES | NULL | Manager delegation result reference |
| `delegation_status` | VARCHAR(50) | YES | NULL | |
| `total_cost_usd` | NUMERIC(8,4) | YES | `0` | |
| `agent_turns` | INTEGER | YES | `0` | |
| `duration_seconds` | INTEGER | YES | `0` | |
| `participants_json` | JSONB | YES | `[]` | Meeting participants |
| `slack_notification_ts` | VARCHAR(100) | YES | NULL | |
| `channel_id` | VARCHAR(100) | YES | NULL | |
| `created_at` | TIMESTAMP | YES | `now()` | |
| `updated_at` | TIMESTAMP | YES | `now()` | Auto-updates |

**Indexes:** `ix_meeting_role`, `ix_meeting_status`, `ix_meeting_user`

---

## Messaging

### `role_messages`
Async peer-to-peer messages between roles. Delivered on recipient's next run.

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | INTEGER | NOT NULL | auto PK | Primary key |
| `from_role_id` | VARCHAR(200) | NOT NULL | — | Sender |
| `to_role_id` | VARCHAR(200) | NOT NULL | — | Recipient |
| `from_department_id` | VARCHAR(200) | NOT NULL | `""` | |
| `to_department_id` | VARCHAR(200) | NOT NULL | `""` | |
| `subject` | VARCHAR(200) | NOT NULL | — | |
| `content` | TEXT | NOT NULL | — | |
| `status` | VARCHAR(20) | NOT NULL | `"pending"` | pending → delivered → read → expired |
| `reply_to_id` | INTEGER | YES | NULL | FK → self (threading) |
| `created_at` | TIMESTAMP | YES | `now()` | |
| `delivered_at` | TIMESTAMP | YES | NULL | |
| `read_at` | TIMESTAMP | YES | NULL | |
| `expires_at` | TIMESTAMP | YES | NULL | |
| `metadata` | JSONB | YES | NULL | Column name `"metadata"` (mapped from `metadata_`) |

**Self-Referential FK:** `reply_to_id` → `role_messages.id`
**Indexes:** `ix_role_msg_to_status` (composite: `to_role_id, status`), `ix_role_msg_from_created` (composite: `from_role_id, created_at`)

---

## Claude Code Tasks

### `claude_code_tasks`
Tracks headless Claude Code task executions.

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | INTEGER | NOT NULL | auto PK | Primary key |
| `task_id` | VARCHAR(100) | NOT NULL | — | UNIQUE; business key |
| `skill_id` | VARCHAR(100) | NOT NULL | — | |
| `user_id` | VARCHAR(255) | NOT NULL | — | |
| `role_id` | VARCHAR(100) | YES | `""` | |
| `department_id` | VARCHAR(100) | YES | `""` | |
| `prompt` | TEXT | NOT NULL | — | |
| `permission_mode` | VARCHAR(50) | YES | `"acceptEdits"` | |
| `max_budget_usd` | NUMERIC(8,4) | YES | `5.0` | |
| `status` | VARCHAR(50) | YES | `"pending"` | pending → running → completed/failed/cancelled |
| `error_message` | TEXT | YES | `""` | |
| `result_text` | TEXT | YES | `""` | |
| `structured_output` | JSONB | YES | NULL | Parsed JSON from fenced blocks |
| `cost_usd` | NUMERIC(8,4) | YES | `0` | |
| `num_turns` | INTEGER | YES | `0` | |
| `duration_ms` | INTEGER | YES | `0` | |
| `session_id` | VARCHAR(255) | YES | `""` | |
| `usage_json` | JSONB | YES | NULL | Token usage breakdown |
| `created_at` | TIMESTAMP | YES | `now()` | |
| `started_at` | TIMESTAMP | YES | NULL | |
| `completed_at` | TIMESTAMP | YES | NULL | |
| `inngest_run_id` | VARCHAR(255) | YES | NULL | |

**Indexes:** `ix_cc_task_task_id` (UNIQUE), `ix_cc_task_user`, `ix_cc_task_skill`, `ix_cc_task_status`, `ix_cc_task_role`, `ix_cc_task_created`

---

## Webhooks

### `webhook_events`
Inbound webhook events from external monitoring sources.

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | INTEGER | NOT NULL | auto PK | Primary key |
| `source` | VARCHAR(50) | NOT NULL | — | custom:X or any registered source |
| `event_type` | VARCHAR(100) | NOT NULL | — | budget_depleted, spend_spike, etc. |
| `severity` | VARCHAR(20) | NOT NULL | — | low, medium, high, critical |
| `summary` | TEXT | NOT NULL | `""` | |
| `raw_payload` | JSONB | YES | `{}` | Original webhook body |
| `normalized_payload` | JSONB | YES | `{}` | Normalized fields |
| `account_id` | VARCHAR(100) | YES | NULL | Platform account ID (string, not FK) |
| `campaign_id` | VARCHAR(100) | YES | NULL | Platform campaign ID (string) |
| `dedup_key` | VARCHAR(255) | YES | NULL | SHA-256 dedup hash |
| `status` | VARCHAR(20) | NOT NULL | `"received"` | received → processing → dispatched/ignored/error |
| `dispatched_event` | VARCHAR(100) | YES | NULL | Inngest event name dispatched |
| `role_id` | VARCHAR(100) | YES | NULL | Assigned investigating role |
| `created_at` | TIMESTAMP | YES | `now()` | |

**Indexes:** `ix_webhook_source_type` (composite: `source, event_type`), `ix_webhook_status`, `ix_webhook_created`, `ix_webhook_dedup`

---

## Working Groups

### `working_group_sessions`
Ad hoc multi-agent working group sessions.

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | INTEGER | NOT NULL | auto PK | Primary key |
| `group_id` | VARCHAR(100) | NOT NULL | — | UNIQUE; business key |
| `objective` | TEXT | NOT NULL | — | Shared goal |
| `coordinator_role_id` | VARCHAR(100) | NOT NULL | — | Manager role |
| `member_role_ids` | JSONB | NOT NULL | `[]` | Participating roles |
| `initiated_by` | VARCHAR(255) | NOT NULL | `""` | Slack user or role ID |
| `status` | VARCHAR(50) | NOT NULL | `"forming"` | See WorkingGroupStatus enum |
| `plan_json` | JSONB | YES | NULL | Coordinator's task assignments |
| `member_results_json` | JSONB | YES | `{}` | Per-member execution results |
| `synthesis` | TEXT | YES | `""` | Combined output |
| `shared_context_json` | JSONB | YES | `{}` | Context shared with all members |
| `cost_cap_usd` | NUMERIC(8,4) | YES | `5.0` | |
| `max_duration_minutes` | INTEGER | YES | `60` | |
| `deadline` | TIMESTAMP | YES | NULL | |
| `total_cost_usd` | NUMERIC(8,4) | YES | `0` | |
| `steward_user_id` | VARCHAR(255) | YES | NULL | |
| `approved_at` | TIMESTAMP | YES | NULL | |
| `approved_by` | VARCHAR(255) | YES | NULL | |
| `created_at` | TIMESTAMP | YES | `now()` | |
| `started_at` | TIMESTAMP | YES | NULL | |
| `completed_at` | TIMESTAMP | YES | NULL | |
| `slack_channel_id` | VARCHAR(100) | YES | NULL | |
| `slack_thread_ts` | VARCHAR(50) | YES | NULL | |

**Indexes:** `ix_wg_group_id` (UNIQUE), `ix_wg_coordinator`, `ix_wg_status`, `ix_wg_created`

---

## Migration History

29 Alembic revisions, applied in sequence:

| # | Revision | Key Changes |
|---|----------|-------------|
| 001 | Initial | Core tables: users, accounts, campaigns, daily_metrics, analysis_results, approval_queue, audit_log, cost_tracking |
| 002 | skill_id | Added `skill_id` to analysis_results + audit_log |
| 003 | failed_runs | Created `failed_runs` DLQ table |
| 004 | action_types | Extended ActionType enum values |
| 005 | hierarchy | Added `department_id`, `role_id` to analysis_results + audit_log |
| 006 | role_memory | Created `role_memory` table |
| 007 | auto_execute | Added `auto_execute_rule_id` to approval_queue; `AUTO_APPROVED` enum value |
| 008 | conversation_threads | Created `conversation_threads` table |
| 009 | org_chart | Created `org_departments`, `org_roles`, `org_skills` tables |
| 010 | skill_evolution | Added `SKILL_PROPOSAL` ActionType; nullable `account_id`/`analysis_id` on approval_queue |
| 011 | users_rbac | Created `users` table; added RBAC fields |
| 012 | meeting_sessions | Created `meeting_sessions` table |
| 013 | lesson_memory | Added `lesson` memory type support; added `principles` JSON on org_roles |
| 014 | role_messages | Created `role_messages` table |
| 015 | consolidation | Added `supersedes_id`, `consolidated_into_id` self-referential FKs on role_memory |
| 016 | clearance | Added `clearance_level` on users + org_roles; `source_role_id` on role_memory; `min_clearance` on org_skills |
| 017 | claude_code | Created `claude_code_tasks` table |
| 018 | role_proposal | Added `ROLE_PROPOSAL` ActionType |
| 019 | role_proposal_fix | Fixed role proposal action type registration |
| 020 | stewardship | Added `steward_user_id` on org_roles, org_departments, approval_queue, audit_log |
| 021 | vocabulary_goals | Added `vocabulary` JSON on org_departments; `goals` JSON on org_roles |
| 022 | dept_channels | Added `slack_channel_id`, `credentials_scope` on org_departments |
| 023 | document_sync | Added `document_sync` JSON on org_roles |
| 024 | webhook_events | Created `webhook_events` table |
| 025 | learning_channels | Added `learning_channels` JSON on org_roles |
| 026 | working_groups | Created `working_group_sessions` table |
| 027 | rls | Row Level Security enabled on all 19 tables |
| 028 | skill_references | Added `references` JSON on org_skills |
| 029 | memory_indexes | Added composite + partial indexes on role_memory for performance |

---

## Entity Relationship Summary

```
users ──┐
        │ user_id
        ├──→ accounts ──→ campaigns ──→ daily_metrics
        │        │
        │        ├──→ analysis_results
        │        │         │
        │        │         └──→ approval_queue ──→ (execution)
        │        │
        │        ├──→ audit_log
        │        └──→ cost_tracking
        │
        ├──→ conversation_threads
        ├──→ meeting_sessions
        └──→ failed_runs

org_departments ──→ org_roles ──→ org_skills

role_memory (self-referential: supersedes_id, consolidated_into_id)
role_messages (self-referential: reply_to_id)

claude_code_tasks (standalone)
webhook_events (standalone)
working_group_sessions (standalone)
```

**Key relationships:**
- `accounts` → `campaigns` → `daily_metrics` (platform data hierarchy)
- `analysis_results` → `approval_queue` (agent output → human decision)
- `role_memory` → self (versioning chains via `supersedes_id`, consolidation via `consolidated_into_id`)
- `role_messages` → self (threading via `reply_to_id`)
- `org_departments` / `org_roles` / `org_skills` are logically linked by string IDs but not enforced via FK constraints (because YAML disk definitions don't exist in the DB)

**Row Level Security:** All 19 tables have RLS enabled (migration 027) with `service_role` and `postgres` full-access policies.
