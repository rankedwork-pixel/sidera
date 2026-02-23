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
│  │ Google Ads  │  │    Meta      │  │   BigQuery   │           │
│  │ Connector   │  │  Connector   │  │  Connector   │           │
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
- **Webhook:** External system pushes an event (spend spike, campaign paused)
- **Heartbeat:** Proactive check-in on a configurable schedule

### 2. Context Assembly
The system assembles the agent's full context before execution:
```
Department context (vocabulary, shared rules)
  + Role persona (who am I, what do I care about)
    + Role principles (decision-making heuristics)
      + Role goals (what am I trying to achieve)
        + Hot memories (last 90 days of learnings)
          + Pending peer messages (from other roles)
            + Skill system_supplement (task-specific instructions)
              + Skill prompt_template (what to do this run)
```

### 3. Agent Execution
The agent runs as a Claude conversation with MCP tools:
- **Haiku** ($0.02/run) — data collection, routing, simple classification
- **Sonnet** ($0.15/run) — analysis, pattern recognition, most skills
- **Opus** ($0.35/run) — complex strategy, only on volatile days

Each agent gets access to specific MCP tools based on its role's `connectors` field. A Media Buyer gets Google Ads + Meta + BigQuery tools. The Head of IT gets system health + DLQ + audit tools.

### 4. Output Processing
After the agent completes:
- **Briefing text** → posted to Slack
- **Recommendations** (JSON blocks) → parsed, each becomes an approval queue item
- **Approval buttons** → Approve / Reject posted in Slack
- **Memory extraction** → decisions, anomalies, lessons saved to DB
- **Post-run reflection** → cheap Haiku call captures "what was hard, what would I do differently"

### 5. Approval & Execution
```
Agent recommends "Pause Campaign X"
  → Approval queue item created (PENDING)
  → Auto-execute rules checked:
      ✓ Matches rule? → AUTO_APPROVED, execute immediately, notify Slack
      ✗ No match? → Post Approve/Reject buttons to Slack
        → Human clicks Approve → Execute via connector → Log result
        → Human clicks Reject → Log decision, agent learns from it
```

## Key Technical Components

### Connectors (src/connectors/)
API clients that read from and write to external platforms:
| Connector | Methods | Purpose |
|-----------|---------|---------|
| Google Ads | 13 (7 read, 6 write) | Campaign management, performance data |
| Meta | 13 (7 read, 6 write) | Facebook/Instagram campaign management |
| BigQuery | 7 (read only) | Backend source of truth for business metrics |
| Google Drive | 13 | Docs, Sheets, Slides, file management |
| Slack | 16 | Messaging, approval buttons, thread management |
| Recall.ai | 5 | Meeting transcript capture |

All connectors have:
- Retry with exponential backoff (3 retries, 1-30s delay)
- Fernet token encryption for stored credentials
- 50% budget cap on write operations (safety)

### Workflows (src/workflows/)
Inngest durable functions — each step is checkpointed and auto-retried:

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| daily_briefing | Cron 7 AM | Legacy three-phase briefing |
| skill_runner | Event | Execute a single skill |
| role_runner | Event | Execute all skills for a role |
| manager_runner | Event | Four-phase manager delegation pipeline |
| conversation_turn | Event | Handle one Slack conversation reply |
| heartbeat_runner | Event | Proactive open-ended investigation |
| event_reactor | Webhook event | Classify severity, alert, investigate |
| meeting_join/end | Event | Recall.ai bot lifecycle |
| memory_consolidation | Cron Sunday 4 AM | Merge duplicate memories |
| data_retention | Cron 3 AM | Purge expired data |
| token_refresh | Cron 5 AM | Refresh expiring OAuth tokens |
| cost_monitor | Cron every 30 min | Check LLM spend vs limits |

### Database (PostgreSQL via Supabase)
27 Alembic migrations, key tables:
- `approval_queue` — pending/approved/rejected actions with steward routing
- `audit_log` — every agent action logged with steward attribution
- `role_memory` — 8 memory types, hot/cold tiered, never deleted
- `conversation_threads` — Slack thread → role mapping
- `org_departments/roles/skills` — dynamic org chart (DB overrides YAML)
- `failed_runs` — dead letter queue for workflow failures
- `webhook_events` — inbound monitoring events from external systems

### MCP Tools (src/mcp_servers/)
62 tools organized by domain:
- **Google Ads** (7): campaigns, performance, changes, recommendations, writes
- **Meta** (7): campaigns, performance, activity, audience insights, writes
- **BigQuery** (5): goals, pacing, performance, attribution, table discovery
- **Google Drive** (8): search, read/write docs, sheets, slides, folders
- **Slack** (6): alerts, briefings, thread replies, reactions, memory search
- **System** (8): health, DLQ, audit, approvals, conversations, costs, webhooks
- **Evolution** (2): propose skill changes, propose role changes
- **Meeting** (3): transcript, participants, end session
- **Memory** (2): save memory, load memory detail
- **Messaging** (3): send, check inbox, reply
- **Working Groups** (2): form group, get status
- **Other** (9): context loading, delegation, orchestration, code execution

## Data Flow Example: Daily Media Buyer Run

```
7:00 AM — Scheduler triggers sidera/role.run for performance_media_buyer

Step 1: Load role memory (decisions, lessons from past runs)
Step 2: Load pending peer messages (from reporting_analyst, strategist)
Step 3: Execute skill: anomaly_detector
  → Agent calls get_google_ads_performance (last 30 days)
  → Agent calls get_meta_performance (last 30 days)
  → Agent calls get_backend_performance (cross-reference)
  → Agent analyzes: finds CPA spike on Campaign X (2.3σ deviation)
  → Agent calls get_google_ads_changes (what changed?)
  → Agent finds: bid strategy changed 3 days ago
  → Agent recommends: "Revert bid strategy on Campaign X"
Step 4: Process recommendation
  → Check auto-execute rules: no match (bid changes require approval)
  → Create approval queue item (PENDING)
  → Post to Slack with Approve/Reject buttons
  → Steward @mentioned in message
Step 5: Extract memories
  → Anomaly: "CPA spike on Campaign X traced to bid strategy change"
  → Saved to role_memory table
Step 6: Post-run reflection (Haiku, ~$0.01)
  → "The bid strategy change was obvious in hindsight. Lesson: always
     check change history first before running full statistical analysis."
  → Saved as LESSON memory
Step 7: Check for recurring friction → scan lesson memories
  → Not enough lessons yet to trigger skill evolution proposal
```
