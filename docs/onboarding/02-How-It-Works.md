# How Sidera Works: Technical Architecture

## System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                         SIDERA PLATFORM                          │
│                                                                  │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────────────┐  │
│  │  Skill YAML │───▶│ Agent Loop   │───▶│  Slack (Output)    │  │
│  │  (Config)   │    │ (Claude API) │    │  Approve / Reject  │  │
│  └─────────────┘    └──────┬───────┘    └────────────────────┘  │
│                            │                                     │
│         ┌──────────────────┼──────────────────┐                 │
│         ▼                  ▼                  ▼                  │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │  Your       │  │  Your        │  │  Your        │           │
│  │ Connector A │  │ Connector B  │  │ Connector C  │           │
│  │ (e.g. CRM)  │  │(e.g. Billing)│  │(e.g. Infra)  │           │
│  └─────────────┘  └──────────────┘  └──────────────┘           │
│         │                  │                  │                  │
│         ▼                  ▼                  ▼                  │
│  ┌─────────────────────────────────────────────────┐            │
│  │              PostgreSQL (Supabase)               │            │
│  │  Accounts, Approvals, Audit Log, Memory, Org    │            │
│  └─────────────────────────────────────────────────┘            │
│  ┌─────────────────────────────────────────────────┐            │
│  │              Redis (Upstash)                      │            │
│  │  API Response Cache, Session State, 2h TTL       │            │
│  └─────────────────────────────────────────────────┘            │
└──────────────────────────────────────────────────────────────────┘
```

## The Execution Cycle

Every agent run follows the same pattern:

### 1. Trigger
- **Scheduled:** Cron-based (e.g., "0 7 * * 1-5" = 7 AM weekdays)
- **Conversational:** User @mentions the bot in Slack
- **Heartbeat:** Proactive check-in on a configurable schedule
- **Manual:** `/sidera run <skill_id>` or API call

### 2. Context Assembly
The system assembles the agent's full context before execution. Stable identity sections go first (for KV-cache reuse), dynamic per-run sections go last (at the attention edge):
```
Stable (cached across runs):
  Department context (vocabulary, shared rules)
    + Role persona (who am I, what do I care about)
      + Role principles (decision-making heuristics)
        + Role goals (what am I trying to achieve)
          + Context files (examples, guidelines)
            + Team awareness (who else is in the org)

Dynamic (per-run, at attention edge):
  Hot memories (last 90 days of learnings, decisions, lessons)
    + Pending peer messages (from other roles)
      + Skill system_supplement (task-specific instructions)
        + Skill prompt_template (what to do this run)
```

### 3. Agent Execution
The agent runs as a Claude conversation with MCP tools. Three-phase model routing optimizes cost:
- **Haiku** (~$0.02/run) — data collection, routing, simple classification
- **Sonnet** (~$0.15/run) — analysis, pattern recognition, most skills
- **Opus** (~$0.35/run) — complex strategy, only on volatile days (auto-skipped otherwise)

Extended thinking enabled for Sonnet and Opus calls — agents reason deeply between tool calls.

Each agent gets access to specific MCP tools based on its role configuration. The tools available depend on which connectors you build and register for each role.

### 4. Output Processing
After the agent completes:
- **Briefing text** → posted to Slack
- **Recommendations** (JSON blocks) → parsed, each becomes an approval queue item
- **Approval buttons** → Approve / Reject posted in Slack (steward @mentioned)
- **Memory extraction** → decisions, anomalies, lessons, commitments saved to DB
- **Post-run reflection** → Haiku call captures "what was hard, what would I do differently"
- **Cross-role learning** → observations flagged for sharing pushed to peer roles

### 5. Approval & Execution
```
Agent recommends "Scale up Widget-X provisioning"
  → Approval queue item created (PENDING)
  → Auto-execute rules checked:
      ✓ Matches rule? → AUTO_APPROVED, execute immediately, notify Slack
      ✗ No match? → Post Approve/Reject buttons to Slack
        → Human clicks Approve → Execute via connector → Log result
        → Human clicks Reject → Log decision, agent learns from it
```

Safety: Double-execution prevention. Pre-action lesson check (blocks auto-execute if contradicting lessons exist). Write operations are gated through configurable safety rules per connector.

## Key Technical Components

### Connectors (src/connectors/)
API clients that read from and write to external platforms. Slack is the only built-in connector. You add your own domain connectors using the template system in `src/templates/`.

| Connector | Status | Purpose |
|-----------|--------|---------|
| Slack | Built-in (19 methods) | Messaging, approval buttons, thread management |
| Your connectors | Use `src/templates/` | Add any API: CRM, billing, infra, databases, etc. |

The template system provides skeleton code for new connectors, MCP tool wrappers, and OAuth routes. Each template includes retry logic, error handling, and Sentry integration out of the box.

All connectors have:
- Retry with exponential backoff (3 retries, 1-30s delay, jitter)
- Fernet token encryption for stored credentials
- Sentry error capture

### Workflows (src/workflows/)
13 Inngest durable functions — each step is checkpointed and auto-retried:

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| daily_briefing | Cron 7 AM | Legacy three-phase briefing |
| skill_runner | Event | Execute a single skill |
| skill_scheduler | Cron | Dispatch scheduled skills and heartbeats |
| role_runner | Event | Execute all skills for a role + reflection |
| manager_runner | Event | Four-phase manager delegation pipeline |
| department_runner | Event | Execute all roles in a department |
| conversation_turn | Event | Handle one Slack conversation reply |
| heartbeat_runner | Event | Proactive open-ended investigation |
| working_group | Event | Multi-agent collaborative task execution |
| memory_consolidation | Cron Sunday 4 AM | Merge duplicate memories, detect contradictions |
| data_retention | Cron 3 AM | Purge expired data, expire stale messages |
| cost_monitor | Cron every 30 min | Check LLM spend vs limits |

### Database (PostgreSQL via Supabase)
29 Alembic migrations, 115 CRUD methods. Key tables:
- `approval_queue` — pending/approved/rejected actions with steward routing
- `audit_log` — every agent action logged with steward attribution
- `role_memory` — 9 memory types, hot/cold tiered, never deleted
- `conversation_threads` — Slack thread → role mapping
- `org_departments/roles/skills` — dynamic org chart (DB overrides YAML)
- `failed_runs` — dead letter queue for workflow failures
- `working_group_sessions` — multi-agent collaborative sessions
- `role_messages` — peer-to-peer async messaging between roles
- `users` — RBAC, clearance levels, stewardship

### MCP Tools (src/mcp_servers/)
~29 built-in tools organized by function. You add domain-specific tools when you build connectors.

**Built-in tools:**
- **Slack** (6): alerts, briefings, thread replies, reactions, memory search
- **System** (8): health, DLQ, audit, approvals, conversations, costs
- **Evolution** (2): propose skill changes, propose role changes
- **Memory** (2): save memory, load memory detail
- **Context** (2): load skill context, load referenced skill context
- **Messaging** (4): send, check inbox, reply, push learning
- **Working Groups** (2): form group, get status
- **Delegation** (1): delegate to sub-role
- **Orchestration** (1): orchestrate multi-step task with quality evaluation
- **Code Execution** (1): run skill-backed Python code

Plus 10 MCP meta-tools for Claude Code integration (talk_to_role, run_role, list_roles, review_pending_approvals, decide_approval, run_claude_code_task, orchestrate, load_plugin, unload_plugin, list_loaded_plugins).

## Data Flow Example: On-Call Engineer Incident Triage

```
7:00 AM — Scheduler triggers sidera/role.run for on_call_engineer

Step 1: Load role memory (decisions, lessons from past runs)
Step 2: Load pending peer messages (from other engineering roles)
Step 3: Execute skill: system_health_check
  → Agent calls your infrastructure connector (e.g., check dashboards)
  → Agent calls your logging connector (e.g., pull error rates)
  → Agent analyzes: finds elevated error rate on Service Y (2.3σ deviation)
  → Agent calls your change tracker (e.g., recent deployments)
  → Agent finds: config change deployed 3 hours ago
  → Agent recommends: "Roll back config change on Service Y"
Step 4: Process recommendation
  → Check auto-execute rules: no match (rollbacks require approval)
  → Create approval queue item (PENDING)
  → Post to Slack with Approve/Reject buttons
  → Steward @mentioned in message
Step 5: Extract memories
  → Anomaly: "Error spike on Service Y traced to config change"
  → Saved to role_memory table
Step 6: Post-run reflection (Haiku, ~$0.01)
  → "The config change was obvious in hindsight. Lesson: always
     check recent deployments first before deep-diving into logs."
  → Saved as LESSON memory, linked to relevant principle
Step 7: Check for recurring friction → scan lesson memories
  → 3+ lessons about same skill? → propose skill modification
Step 8: Push cross-role learnings
  → Flag observation for Platform Engineer: "Config changes without
     canary deploys cause error spikes — worth adding a gate"
```
