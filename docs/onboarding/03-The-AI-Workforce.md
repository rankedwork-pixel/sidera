# The AI Workforce: Every Role, Skill, and Department

## Organizational Chart

```
                        ┌─────────┐
                        │   CEO   │ (Opus, hourly heartbeat)
                        │ manages │
                        └────┬────┘
                  ┌──────────┴──────────┐
                  ▼                     ▼
         ┌────────────────┐    ┌──────────────┐
         │ Head of Mktg   │    │  Head of IT  │
         │ manages: 3     │    │  manages: 1  │
         └───────┬────────┘    └──────┬───────┘
       ┌─────────┼─────────┐         │
       ▼         ▼         ▼         ▼
  ┌──────────┐ ┌────────┐ ┌───────────┐ ┌──────────────┐
  │ Media    │ │Analyst │ │Strategist │ │Skill Creator │
  │ Buyer    │ │        │ │           │ │  (wizard)    │
  └──────────┘ └────────┘ └───────────┘ └──────────────┘
```

**3 departments, 7 roles, 11 skills.** These are examples — add your own departments, roles, and skills for any domain.

---

## Executive Department

### CEO (Manager Role)
- **Model:** Opus
- **Schedule:** 8 AM weekdays
- **Heartbeat:** Every hour, 7 AM - 8 PM weekdays
- **Manages:** Head of Marketing, Head of IT
- **Purpose:** Cross-department oversight. Catches issues that span departments (e.g., Google Ads disconnect = IT problem + marketing blind spot). Ensures approvals are flowing, costs are in check, all platforms are connected.
- **Skills:** org_health_check

**Key Principles:**
- "Systems thinking first — every issue has upstream causes and downstream effects"
- "Stale approvals are a leadership failure"
- "Silence from a department is not the same as health"

**org_health_check skill:** 7 mandatory checks (system health, failed runs, approval queue, cost summary, audit events, webhook events, inbox). Severity rules with hard thresholds. Healthy = 3-4 lines; issues need severity + component + delegation target. Priority: revenue-impacting > operational > cost.

---

## Marketing Department

**Vocabulary injected into all marketing roles:** ROAS, MER, CPA, CAC, AOV, CTR, CPM, LTV, inflation ratio, backend-attributed, marginal ROAS, creative fatigue, frequency cap (14 terms)

### Head of Marketing (Manager Role)
- **Model:** Sonnet
- **Schedule:** 9 AM weekdays
- **Heartbeat:** Every 30 min, 7 AM - 6 PM weekdays
- **Manages:** Media Buyer, Analyst, Strategist
- **Connectors:** Google Ads, Meta, BigQuery
- **Purpose:** Portfolio-level thinking. Reviews sub-role outputs, identifies cross-channel insights, coordinates budget allocation. Challenges reports that are too optimistic about their own channel.
- **Skills:** executive_summary

**Key Principles:**
- "Portfolio thinking — the whole is more than the sum of its parts"
- "Budget follows proven ROI — shift money from underperformers to winners"
- "When team recommendations conflict, favor the one backed by backend data"

**Manager Pipeline (4 phases):**
1. Runs own executive_summary skill
2. Decides which sub-roles to activate (quiet day = maybe just Media Buyer)
3. Activated sub-roles execute with full persona + tools
4. Synthesizes unified output with cross-channel insights

**executive_summary skill:** Mandatory data pulls from all platforms. 200-400 word limit. 5 required sections (total spend, blended CPA/ROAS, budget pacing, anomalies, goal attainment). No campaign deep-dives — that's the Media Buyer's job.

---

### Performance Media Buyer
- **Model:** Sonnet
- **Schedule:** 7 AM weekdays
- **Connectors:** Google Ads, Meta, BigQuery
- **Clearance:** Internal
- **Purpose:** Daily performance monitoring. Detects anomalies, analyzes creative performance, manages budgets. Backend-attributed ROAS is the north star, not platform-reported numbers.
- **Learns from:** Reporting Analyst, Strategist (via learning channels)
- **Reacts to webhooks:** budget_depleted, spend_spike, campaign_paused, conversion_drop, policy_violation (9 event types)

**Skills:**

| Skill | Model | Max Turns | What It Does |
|-------|-------|-----------|-------------|
| **anomaly_detector** | Sonnet | 15 | 7 mandatory steps: baseline collection (30 days, min 14), KPI computation (8 KPIs), statistical bounds (1.5σ/2σ/3σ), anomaly flagging, root cause investigation (6 categories), backend cross-reference, financial impact ranking. Hard rules: $50/day minimum spend, DoW comparison required, no single-day pause recommendations. |
| **creative_analysis** | Sonnet | 10 | 7 mandatory steps: creative-level data collection, backend cross-reference (non-negotiable), segmentation (format/copy/audience/objective), metric calculation (5 metrics), classification (top 20% Scale, middle 60% Maintain, bottom 20% Cut by backend ROAS), fatigue detection (frequency >3.0 AND declining CTR 7+ days), test hypotheses. |
| **fb_creative_cuts** | Sonnet | 10 | Code-backed skill with calibrated Python analysis. Agent MUST NOT re-analyze — trust the code's thresholds (CUT=50% above avg CPL, WATCH=20%, CPL Shield for top 20%, 14-day minimum, max 3+3 per ad set). Run code → read output → summarize → present. |

**Auto-Execute Rules (_rules.yaml):**

| Rule | Trigger | Guard Rails |
|------|---------|-------------|
| pause_low_roas_ads | ROAS < 0.5x AND spend > $100/day | Max 5/day, 60 min cooldown |
| pause_high_cpa_ads | CPA > 3x target AND spend > $50/day | Max 3/day, 120 min cooldown |
| add_obvious_negatives | 0 conversions AND $200+ spend | Max 10/day, 30 min cooldown |
| small_budget_increase | ROAS > 3x AND increase ≤ 15% | Max 2/day, 240 min cooldown (**disabled by default**) |

---

### Reporting Analyst
- **Model:** Sonnet
- **Connectors:** Google Ads, Meta, BigQuery
- **Clearance:** Internal
- **Purpose:** Produces weekly performance reports. Bridges raw data and strategic decisions. Always includes both platform-reported and backend-attributed metrics.
- **Learns from:** Media Buyer

**Skills:**

| Skill | What It Does |
|-------|-------------|
| **weekly_report** | 6 mandatory steps: data collection from all platforms, backend cross-reference, WoW + 4-week calculations for all metrics, exactly 3 wins and 3 concerns, goal attainment classification, deliverable creation in Google Drive. Writing rules: insights-first, context for every number, tables for data/prose for insights. |

**Key Principles:**
- "Highlight statistically significant changes, not just any movement"
- "Use concrete numbers, not vague qualifiers"
- "When two metrics conflict, present both and explain the discrepancy"

---

### Marketing Strategist
- **Model:** Opus (for complex reasoning)
- **Connectors:** Google Ads, Meta, BigQuery
- **Clearance:** Confidential
- **Purpose:** Looks beyond day-to-day optimization. Competitive intelligence, audience dynamics, strategic portfolio shifts. The long-view thinker.
- **Learns from:** Media Buyer, Reporting Analyst

**Skills:**

| Skill | What It Does |
|-------|-------------|
| **competitor_benchmark** | 7 mandatory steps including non-negotiable campaign type segmentation (NEVER compare search against display). Hard classification thresholds (>10% = advantage/gap). Benchmark data treated as directional, not targets. Attribution caveats required on all third-party data. |

**Key Principles:**
- "Consider the competitive context — what are competitors doing differently?"
- "When recommending a strategic shift, quantify the expected impact and the cost of inaction"

---

## IT Department

### Head of IT (Manager Role)
- **Model:** Sonnet
- **Schedule:** 6 AM daily (runs before marketing roles)
- **Heartbeat:** Every 15 minutes, 24/7
- **Manages:** Skill Creator
- **Connectors:** None (uses system introspection tools)
- **Clearance:** Internal
- **Purpose:** Platform reliability. Monitors DB, Redis, workflows, costs, DLQ, approval queue. Diagnoses failures, resolves transient errors, escalates persistent issues.
- **Learns from:** Media Buyer, Head of Marketing
- **Reacts to webhooks:** system_alert

**Skills:**

| Skill | Model | Max Turns | What It Does |
|-------|-------|-----------|-------------|
| **system_health_check** | Sonnet | 8 | 5-step mandatory check sequence (get_system_health → get_failed_runs → get_approval_queue_status → get_conversation_status → get_cost_summary). Severity classification: Database=CRITICAL, Redis=WARNING, etc. Resolves transient DLQ entries. Sends Slack alerts for WARNING/CRITICAL. |
| **error_diagnosis** | Sonnet | 10 | 5-step mandatory diagnostic sequence (symptom → health → audit → DLQ → cross-reference). Pattern matching for known issues. Evidence-based resolution only. Escalates recurring failures (3+ times) to steward. |
| **cost_monitoring** | Haiku | 5 | 4-step analysis with cost benchmark table (8 operations with expected costs). Alert thresholds: $10/day=WARNING, $25/day=CRITICAL, >$2 single op=runaway. Optimization signals with specific recommendations. |

**Key Principles:**
- "Check the simplest explanation first — misconfig before bug"
- "Never resolve a DLQ entry without understanding the root cause"
- "Prefer least-invasive fixes — restart before rebuild"

---

### Skill Creator (Wizard Role)
- **Model:** Sonnet
- **Schedule:** None (conversational only, plus heartbeat)
- **Heartbeat:** Every 4 hours
- **Purpose:** Guides users through creating new skills via conversation. Asks structured questions, generates valid YAML, validates tool names and categories, proposes via standard approval pipeline.

**Skills:**

| Skill | What It Does |
|-------|-------------|
| **create_skill_wizard** | 5-question mandatory interview (purpose, data sources, output format, business rules, ownership). Enforces safe defaults (requires_approval: true, model: sonnet). Validates categories and tool names. Generated system_supplements MUST use behavioral enforcement language (MUST/NEVER/BEFORE). |

On heartbeat, checks inbox for skill suggestions from other roles (e.g., gap detection pipeline sends suggestions when capability gaps are detected).

---

## Cross-Role Communication

### Peer Messaging
Any role can send async messages to any other role. Messages are delivered on the next run. Anti-loop protection: max 3 messages per run, max 5 chain depth.

### Learning Channels
Explicit whitelist of who can push structured learnings to whom:
```
Media Buyer    ← learns from → Analyst, Strategist
Analyst        ← learns from → Media Buyer
Strategist     ← learns from → Media Buyer, Analyst
Head of IT     ← learns from → Media Buyer, Head of Marketing
CEO            ← learns from → Head of Marketing, Head of IT
```

### Working Groups
Managers can form ad hoc cross-functional groups: "I need the Media Buyer, Analyst, and Strategist to investigate this together." Max 10 members. Manager plans tasks, members execute in parallel, manager synthesizes.

### Real Delegation in Conversations
When chatting with a manager role, it can delegate to sub-roles mid-conversation. The manager calls `delegate_to_role`, which runs a complete inner agent loop as the sub-role (full persona, context, memory, tools), then the manager synthesizes the result. Max 3 delegations per turn, no recursion.

---

## Memory Types

Every role accumulates persistent memory across 9 types:

| Type | What It Captures | Example |
|------|-----------------|---------|
| **Decision** | Approval outcomes | "Paused Campaign X because CPA exceeded $45" |
| **Anomaly** | Detected performance spikes/drops | "Meta spend spiked 40% on Tuesday" |
| **Pattern** | Recurring trends | "Performance dips every Monday morning" |
| **Insight** | Strategic learnings | "Brand search converts 3x better post-Meta prospecting" |
| **Lesson** | "I tried X, it failed because Y" | "Budget increase on Campaign Z backfired due to creative fatigue" |
| **Commitment** | Conversational promises | "I'll investigate the CTR drop tomorrow" |
| **Relationship** | Inter-role context | "The Analyst prefers data in table format" |
| **Steward Note** | Human-injected guidance (highest priority, agent can't override) | "Focus on ROAS over CPA this quarter" |
| **Cross-Role Insight** | Learnings from peer roles | "Media Buyer reports CPM spikes correlate with Meta policy updates" |

Hot memories (< 90 days) are auto-injected into every run, sorted by confidence, capped at 2000 tokens. When >20 hot memories exist, a compact index is injected instead — agents load specific memories on demand via `load_memory_detail` MCP tool.

Cold memories are archived but searchable via Slack. Weekly consolidation merges duplicates, detects contradictions (flagged with low confidence for human review). Memories are never deleted.
