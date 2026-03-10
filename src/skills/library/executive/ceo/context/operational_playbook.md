# CEO Operational Playbook

## Escalation Decision Tree

```
Issue detected
  ├── Infrastructure (DB, Redis, API) → Route to relevant department head
  ├── Cost anomaly → Check all departments
  ├── Approval queue stale → Alert steward directly
  └── Cross-department → Investigate yourself via orchestrate_task
```

## When to Use orchestrate_task vs delegate_to_role

- **delegate_to_role**: Quick, single-shot question to a department head
- **orchestrate_task**: Complex investigation needing iterative refinement

## Common Patterns to Watch For

### The Silent Department
A department head that reports "all clear" for 3+ days straight may be:
- Not checking deeply enough
- Missing data due to connector issues
- Running successfully but with stale data

**Action:** Directly query system health and recent audit events for that department's roles.

### The Approval Queue Pile-up
Approvals stacking up usually means:
- Steward is unavailable
- Auto-execute rules need tuning
- Agent is proposing too many low-value changes

**Action:** Anything >24h → alert steward. Anything >48h → escalate with urgency.

### The Cost Creep
LLM costs tend to creep up as agents get more tools and longer conversations.

**Warning signs:**
- Any single role >$1/day (unless doing deep analysis)
- Total org cost >$10/day without a known reason
- Heartbeat costs >$0.50/run (should be ~$0.05-0.15)

## Memory Guidelines

Save to memory when you discover:
- A pattern that took investigation to understand
- A resolution that worked (or didn't work) for a recurring issue
- A relationship between systems
- A human preference

Don't save:
- Routine "all clear" results
- One-off transient errors that resolved themselves
- Raw data dumps (save the insight, not the data)
