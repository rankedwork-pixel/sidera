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
         │ manages: 3     │    │  manages: 0  │
         └───────┬────────┘    └──────────────┘
       ┌─────────┼─────────┐
       ▼         ▼         ▼
  ┌──────────┐ ┌────────┐ ┌───────────┐
  │ Media    │ │Analyst │ │Strategist │
  │ Buyer    │ │        │ │           │
  └──────────┘ └────────┘ └───────────┘
```

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
| **anomaly_detector** | Sonnet | 15 | Statistical anomaly detection across all campaigns. Pulls 30 days of data, computes rolling baselines, flags 1.5σ/2σ/3σ deviations on CPA, ROAS, CTR, CPC, CPM, spend, conversion volume. Drills into root cause (bid changes, creative fatigue, competitive pressure, landing page issues). Cross-references platform vs backend data. |
| **creative_analysis** | Sonnet | 10 | Analyzes ad creative performance across platforms. Identifies fatigue, winning/losing creatives, recommended creative refreshes. |
| **fb_creative_cuts** | Sonnet | 10 | Meta-specific creative performance breakdown. Audience-level cuts, placement analysis, creative-audience fit. |

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
| **weekly_report** | Weekly performance summary with period-over-period comparisons, trend analysis, statistical significance |

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
| **competitor_benchmark** | Competitive landscape analysis, audience overlap detection, strategic opportunity identification |

**Key Principles:**
- "Consider the competitive context — what are competitors doing differently?"
- "When recommending a strategic shift, quantify the expected impact and the cost of inaction"

---

## IT Department

### Head of IT
- **Model:** Sonnet
- **Schedule:** 6 AM daily (runs before marketing roles)
- **Heartbeat:** Every 15 minutes, 24/7
- **Connectors:** None (uses system introspection tools)
- **Clearance:** Internal
- **Purpose:** Platform reliability. Monitors DB, Redis, workflows, costs, DLQ, approval queue. Diagnoses failures, resolves transient errors, escalates persistent issues.
- **Learns from:** Media Buyer, Head of Marketing
- **Reacts to webhooks:** system_alert

**Skills:**

| Skill | Model | Max Turns | What It Does |
|-------|-------|-----------|-------------|
| **system_health_check** | Sonnet | 8 | Full infrastructure health check — DB, Redis, config, DLQ, approvals, conversations, costs. Resolves transient DLQ entries. Sends Slack alerts for WARNING/CRITICAL. |
| **error_diagnosis** | Sonnet | 10 | Deep-dive into specific failures. Reads DLQ entries, audit logs, traces root cause. Recommends fixes. |
| **cost_monitoring** | Haiku | 5 | Checks LLM spend vs daily budget. Flags spikes. Analyzes which models/operations drive costs. |

**Key Principles:**
- "Check the simplest explanation first — misconfig before bug"
- "Never resolve a DLQ entry without understanding the root cause"
- "Prefer least-invasive fixes — restart before rebuild"

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
Managers can form ad hoc cross-functional groups: "I need the Media Buyer, Analyst, and Strategist to investigate this together." Max 10 members. Manager plans tasks, members execute, manager synthesizes.

---

## Memory Types

Every role accumulates persistent memory:

| Type | What It Captures | Example |
|------|-----------------|---------|
| **Decision** | Approval outcomes | "Paused Campaign X because CPA exceeded $45" |
| **Anomaly** | Detected performance spikes/drops | "Meta spend spiked 40% on Tuesday" |
| **Pattern** | Recurring trends | "Performance dips every Monday morning" |
| **Insight** | Strategic learnings | "Brand search converts 3x better post-Meta prospecting" |
| **Lesson** | "I tried X, it failed because Y" | "Budget increase on Campaign Z backfired due to creative fatigue" |
| **Commitment** | Conversational promises | "I'll investigate the CTR drop tomorrow" |
| **Relationship** | Inter-role context | "The Analyst prefers data in table format" |
| **Steward Note** | Human-injected guidance | "Focus on ROAS over CPA this quarter" (highest priority, agent can't override) |

Hot memories (< 90 days) are auto-injected into every run. Cold memories are archived but searchable. Weekly consolidation merges duplicates. Memories are never deleted.
