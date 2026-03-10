# The AI Workforce: Every Role, Skill, and Department

## Organizational Chart

```
                        ┌─────────┐
                        │   CEO   │ (Opus, hourly heartbeat)
                        │ manages │
                        └────┬────┘
                             │
                     (your departments here)
```

**1 department, 1 role, 1 skill.** These are starting points — the framework ships with a minimal Executive department so you have a working example. You build your own departments, roles, and skills for your domain.

---

## Executive Department

### CEO (Manager Role)
- **Model:** Opus
- **Schedule:** 8 AM weekdays
- **Heartbeat:** Every hour, 7 AM - 8 PM weekdays
- **Manages:** empty (add your department heads)
- **Purpose:** Cross-department oversight. Catches issues that span departments. Ensures approvals are flowing, costs are in check, all systems are connected. As you add department heads, the CEO delegates to and synthesizes across them.
- **Skills:** org_health_check

**Key Principles:**
- "Systems thinking first — every issue has upstream causes and downstream effects"
- "Stale approvals are a leadership failure"
- "Silence from a department is not the same as health"

**org_health_check skill:** 7 mandatory checks (system health, failed runs, approval queue, cost summary, audit events, webhook events, inbox). Severity rules with hard thresholds. Healthy = 3-4 lines; issues need severity + component + delegation target. Priority: revenue-impacting > operational > cost.

**Manager Pipeline (4 phases):**
1. Runs own skills (e.g., org_health_check)
2. Decides which sub-roles to activate (quiet day = maybe skip some)
3. Activated sub-roles execute with full persona + tools
4. Synthesizes unified output with cross-cutting insights

As you add department heads under the CEO, this pipeline delegates work down through the hierarchy automatically.

---

## Building Your Own Departments

The framework is domain-agnostic. To build out your workforce:

1. **Create a department** — add a `_department.yaml` under `src/skills/library/<your_dept>/` with a name, description, and optional vocabulary (domain-specific terms injected into all roles in that department).

2. **Create roles** — add `_role.yaml` files under `src/skills/library/<your_dept>/<your_role>/` defining the persona, model, schedule, principles, and goals. Roles can be individual contributors or managers (with a `manages` field listing sub-role IDs).

3. **Create skills** — add `skill.yaml` files (or skill directories with context files) defining what each role can do. Skills specify the prompt template, required tools, output format, and business rules.

4. **Wire up the hierarchy** — set the CEO's `manages` field to include your new department heads. Set each department head's `manages` field to include their reports.

See the skill creation tutorial and the Instructions file for detailed guidance on YAML schema and fields.

---

## Cross-Role Communication

### Peer Messaging
Any role can send async messages to any other role. Messages are delivered on the next run. Anti-loop protection: max 3 messages per run, max 5 chain depth.

### Learning Channels
Explicit whitelist of who can push structured learnings to whom. You configure `learning_channels` on each role's `_role.yaml` to control knowledge flow. For example:
```
Role A    ← learns from → Role B, Role C
Role B    ← learns from → Role A
```

Learning channels are admin-controlled — agents cannot modify who can push learnings to them.

### Working Groups
Managers can form ad hoc cross-functional groups: "I need Role A, Role B, and Role C to investigate this together." Max 10 members. The manager plans tasks, members execute in parallel, the manager synthesizes.

### Real Delegation in Conversations
When chatting with a manager role, it can delegate to sub-roles mid-conversation. The manager calls `delegate_to_role`, which runs a complete inner agent loop as the sub-role (full persona, context, memory, tools), then the manager synthesizes the result. Max 3 delegations per turn, no recursion.

---

## Memory Types

Every role accumulates persistent memory across 9 types:

| Type | What It Captures | Example |
|------|-----------------|---------|
| **Decision** | Approval outcomes | "Approved restarting the monitoring service" |
| **Anomaly** | Detected spikes/drops | "Error rate spiked 40% on Tuesday" |
| **Pattern** | Recurring trends | "Performance dips every Monday morning" |
| **Insight** | Strategic learnings | "Service restarts resolve 80% of transient errors" |
| **Lesson** | "I tried X, it failed because Y" | "Scaling up without load testing caused cascading failures" |
| **Commitment** | Conversational promises | "I'll investigate the latency spike tomorrow" |
| **Relationship** | Inter-role context | "The on-call engineer prefers detailed incident reports" |
| **Steward Note** | Human-injected guidance (highest priority, agent can't override) | "Focus on uptime over feature velocity this quarter" |
| **Cross-Role Insight** | Learnings from peer roles | "Security team reports patching correlates with brief latency spikes" |

Hot memories (< 90 days) are auto-injected into every run, sorted by confidence, capped at 2000 tokens. When >20 hot memories exist, a compact index is injected instead — agents load specific memories on demand via `load_memory_detail` MCP tool.

Cold memories are archived but searchable via Slack. Weekly consolidation merges duplicates, detects contradictions (flagged with low confidence for human review). Memories are never deleted.
