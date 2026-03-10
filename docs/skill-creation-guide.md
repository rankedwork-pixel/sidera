# Skill Creation Guide — From Zero to Production

A step-by-step technical tutorial for building Sidera skills. This document
assumes you understand what Sidera is (an AI agent framework) but have never
created a skill before. By the end, you will understand the complete skill
lifecycle: definition, context enrichment, behavioral enforcement, wiring into
roles, auto-execute rules, testing, and evolution.

---

## Table of Contents

1. [What Is a Skill?](#1-what-is-a-skill)
2. [The Three-Level Hierarchy](#2-the-three-level-hierarchy)
3. [Step 1: Choose a Skill Type](#3-step-1-choose-a-skill-type)
4. [Step 2: Create the YAML File](#4-step-2-create-the-yaml-file)
5. [Step 3: Fill In Every Required Field](#5-step-3-fill-in-every-required-field)
6. [Step 4: Write the System Supplement](#6-step-4-write-the-system-supplement)
7. [Step 5: Write the Prompt Template](#7-step-5-write-the-prompt-template)
8. [Step 6: Define the Output Format](#8-step-6-define-the-output-format)
9. [Step 7: Write Business Guidance](#9-step-7-write-business-guidance)
10. [Step 8: Add Context Files (Folder-Based Skills)](#10-step-8-add-context-files)
11. [Step 9: Wire the Skill into a Role](#11-step-9-wire-the-skill-into-a-role)
12. [Step 10: Configure Auto-Execute Rules](#12-step-10-configure-auto-execute-rules)
13. [Step 11: Add Cross-Skill References](#13-step-11-add-cross-skill-references)
14. [Step 12: Validate and Test](#14-step-12-validate-and-test)
15. [Code-Backed Skills](#15-code-backed-skills)
16. [Behavioral Enforcement Patterns](#16-behavioral-enforcement-patterns)
17. [The Prompt Composition Pipeline](#17-the-prompt-composition-pipeline)
18. [How Skills Execute at Runtime](#18-how-skills-execute-at-runtime)
19. [Skill Evolution (Agent Self-Modification)](#19-skill-evolution)
20. [Skill Portability (Export/Import)](#20-skill-portability)
21. [Complete Reference: All YAML Fields](#21-complete-reference-all-yaml-fields)
22. [Checklist: Before You Ship](#22-checklist-before-you-ship)

---

## 1. What Is a Skill?

A skill is a **single, well-defined task** that an AI agent can perform. It is
defined entirely in YAML and tells the agent:

- **What to do** (system_supplement — the agent's instructions)
- **How to start** (prompt_template — the user-facing prompt with variables)
- **What output to produce** (output_format — the expected deliverable structure)
- **What rules to follow** (business_guidance — hard constraints and guardrails)
- **What tools to use** (tools_required — which MCP tools are available)
- **Which model to use** (model — haiku, sonnet, or opus)

Skills are the atomic unit of agent capability. A role (like "CEO" or
"Head of IT") is composed of multiple skills. A department (like "Executive")
is composed of multiple roles. Skills are designed to be:

- **Composable** — multiple skills run in sequence to form a role's briefing
- **Portable** — skills can be exported and imported across organizations
- **Evolvable** — agents can propose modifications to their own skills
- **Testable** — each skill can be validated independently

**Key insight:** The skill YAML is not just configuration. It is the
accumulated expertise of your domain. The quality of the system_supplement,
business_guidance, and context files is what makes the difference between a
mediocre agent and an expert one.

---

## 2. The Three-Level Hierarchy

Skills exist inside a three-level hierarchy on disk:

```
src/skills/library/
  executive/                            <-- Department
    _department.yaml                    <-- Department config
    ceo/                                <-- Role
      _role.yaml                        <-- Role config
      _rules.yaml                       <-- Auto-execute rules (optional)
      system_monitor.yaml               <-- Flat skill (single file)
      incident_triage/                  <-- Folder-based skill (directory)
        skill.yaml                      <-- Skill config
        context/                        <-- Context files
          severity_rubric.md
          escalation_policy.md
        examples/                       <-- Example outputs
          good_triage_report.md
        guidelines/                     <-- Decision frameworks
          decision_framework.md
      cost_report/                      <-- Code-backed skill (directory)
        skill.yaml
        code/
          run.py                        <-- Python entrypoint
        context/
          threshold_notes.md
  engineering/                           <-- Another department
    _department.yaml
    on_call_engineer/
      _role.yaml
      incident_triage.yaml
```

**Naming conventions:**

| File | Purpose |
|------|---------|
| `_department.yaml` | Department definition (underscore prefix = config, not a skill) |
| `_role.yaml` | Role definition |
| `_rules.yaml` | Auto-execute rules for the role |
| `system_monitor.yaml` | Flat skill (single YAML file, no context files) |
| `incident_triage/skill.yaml` | Folder-based skill (skill.yaml inside a directory) |

The underscore prefix on `_department.yaml`, `_role.yaml`, and `_rules.yaml`
tells the registry "this is configuration, not a skill." The registry skips
files starting with `_` during skill discovery.

**How discovery works** (in `src/skills/registry.py`):

1. Scan each subdirectory of `library/` for `_department.yaml` → load as department
2. Inside each department dir, scan subdirs for `_role.yaml` → load as role
3. Inside each role dir, load `.yaml` files (flat skills) and `skill.yaml` inside subdirs (folder-based skills)
4. Any `.yaml` file at the top level of `library/` is loaded as a "loose skill" (backward compatibility, no department or role)

The registry automatically sets `department_id` and `role_id` on each skill
based on where it lives on disk. You never put these in the skill YAML.

---

## 3. Step 1: Choose a Skill Type

There are two skill types:

### LLM Skills (default, `skill_type: "llm"`)

The agent uses Claude to perform the task. It calls MCP tools, reasons about
the data, and produces a text output. This is what you want 95% of the time.

**Use when:** The task requires judgment, analysis, synthesis, or
natural-language output.

### Code-Backed Skills (`skill_type: "code_backed"`)

The agent runs a Python script via the `run_skill_code` MCP tool. The code does
the computation; the agent interprets and presents the results.

**Use when:** The task requires precise numerical computation, calibrated
thresholds, statistical analysis, or deterministic output that an LLM should
not be trusted to compute.

**Example:** A `cost_report` skill uses Python code to compute cost breakdowns
across agent runs. The code aggregates billing data from logs. The agent's job
is to run the code, read the output, and present findings.

This guide focuses on LLM skills first. Code-backed skills are covered in
[Section 15](#15-code-backed-skills).

---

## 4. Step 2: Create the YAML File

Decide: flat skill or folder-based?

**Flat skill** — a single `.yaml` file. Use when the skill needs no context
files (examples, rubrics, guidelines).

```bash
# Create a flat skill
touch src/skills/library/<department>/<role>/system_monitor.yaml
```

**Folder-based skill** — a directory containing `skill.yaml` plus context
subdirectories. Use when you want to inject examples, scoring rubrics,
decision frameworks, or other reference material into the agent's context.

```bash
# Create a folder-based skill
mkdir -p src/skills/library/<department>/<role>/incident_triage
touch src/skills/library/<department>/<role>/incident_triage/skill.yaml
mkdir -p src/skills/library/<department>/<role>/incident_triage/context
mkdir -p src/skills/library/<department>/<role>/incident_triage/examples
mkdir -p src/skills/library/<department>/<role>/incident_triage/guidelines
```

**When to use which:**

| Flat skill | Folder-based skill |
|------------|-------------------|
| Simple analysis task | Complex multi-step analysis |
| No reference material needed | Needs examples, rubrics, or frameworks |
| Self-contained instructions | Instructions reference external context |
| Quick to create | More setup, but much higher quality output |

---

## 5. Step 3: Fill In Every Required Field

Here is the complete skeleton with every required field. Copy this and fill it
in:

```yaml
# --- Identity ---
id: system_monitor                      # Unique ID (alphanumeric + underscore + hyphen)
name: "System Monitor"                  # Human-readable name
version: "1.0"                          # Semantic version
description: >-                         # One-line description (used by the SkillRouter
  Monitor system health, detect failed  #   for semantic matching — make it specific)
  runs, and recommend corrective
  actions based on error patterns

# --- Classification ---
category: monitoring                    # One of: analysis, optimization, reporting,
                                        #   monitoring, creative, audience, bidding,
                                        #   budget, forecasting, attribution, operations
platforms: [custom]                     # Which connectors this skill needs
tags:                                   # Keywords for routing and search
  - health
  - monitoring
  - errors
  - failed-runs
  - diagnostics

# --- Execution ---
tools_required:                         # MCP tools the agent can call
  - get_system_health
  - get_failed_runs
  - get_recent_audit_events
  - get_cost_summary
  - get_approval_queue_status
  - send_slack_alert
model: sonnet                           # haiku | sonnet | opus
max_turns: 15                           # Max API round-trips (1-50)

# --- Prompt composition ---
system_supplement: |                    # Instructions for the agent (see Step 4)
  [Your detailed instructions here]

prompt_template: |                      # The user-facing prompt (see Step 5)
  [Your prompt template here]

output_format: |                        # Expected output structure (see Step 6)
  [Your output format here]

# --- Business guidance ---
business_guidance: |                    # Hard rules and guardrails (see Step 7)
  [Your business rules here]

# --- Optional fields ---
schedule: null                          # Cron expression for scheduled execution
chain_after: null                       # Skill ID to run after this one completes
requires_approval: true                 # Whether write actions need human approval
min_clearance: internal                 # Minimum clearance: public|internal|confidential|restricted
author: your_name
created_at: "2025-01-01"
updated_at: "2025-01-01"
```

### Field-by-Field Explanation

**`id`** (required) — The unique identifier. Must be alphanumeric plus
underscores and hyphens. This is how the system refers to the skill everywhere:
in `briefing_skills` lists, in `chain_after` references, in the routing index,
in the database.

- Good: `system_monitor`, `incident_triage`, `system_health_check`
- Bad: `my skill`, `health.check`, `skill #3`

**`name`** (required) — Human-readable name shown in Slack messages, dashboard,
and logs.

**`version`** (required) — Semantic version string. Increment when you make
meaningful changes. The system tracks this for skill evolution history.

**`description`** (required) — This is critically important. The `SkillRouter`
(which uses Claude Haiku) reads this description to decide whether to route a
user's query to this skill. Be specific about what the skill does and what
questions it answers. Vague descriptions cause misrouting.

- Good: "Identify failed agent runs, diagnose root causes for DLQ entries, cost spikes, or system errors, and recommend corrective actions"
- Bad: "Analyzes data" or "Does monitoring stuff"

**`category`** (required) — Must be one of: `analysis`, `optimization`,
`reporting`, `monitoring`, `creative`, `audience`, `bidding`, `budget`,
`forecasting`, `attribution`, `operations`. Used for filtering and organization.

**`platforms`** (required) — Which platform connectors this skill needs data
from. Valid values: `custom`. A skill with `platforms: [custom]` uses the
framework's built-in tools.

**`tags`** (required) — Keywords used by the SkillRouter for semantic matching.
Include synonyms. If your skill handles "system errors," include both `errors`
and `failures`. The router builds a compact index from these:
`skill_id | description | tag1, tag2, tag3`.

**`tools_required`** (required) — The MCP tools this skill can call. The agent
will only have access to tools listed here. If you list a write tool, the agent
can propose changes but they go through the approval pipeline.

The full list of available tools is defined in `src/agent/prompts.py` in the
`ALL_TOOLS` list. Common ones:

| Tool | Purpose |
|------|---------|
| `get_system_health` | System infrastructure health check |
| `get_failed_runs` | Dead letter queue entries |
| `get_recent_audit_events` | Recent audit log entries |
| `get_cost_summary` | LLM cost tracking summary |
| `get_approval_queue_status` | Pending approval queue status |
| `get_conversation_status` | Active conversation thread status |
| `get_webhook_events` | Recent webhook events |
| `resolve_failed_run` | Mark a DLQ entry as resolved |
| `send_slack_alert` | Send alert to Slack channel |
| `send_slack_thread_reply` | Reply in a Slack thread |
| `react_to_slack_message` | Add emoji reaction to a message |
| `search_role_memory_archive` | Search cold memory archive |
| `check_slack_connection` | Verify Slack connectivity |
| `preview_slack_briefing` | Preview a briefing before posting |
| `propose_skill_change` | Propose a modification to a skill |
| `propose_role_change` | Propose a new role or role modification |
| `save_memory` | Save a memory for the current role |
| `load_memory_detail` | Load full content of a memory by ID |
| `load_skill_context` | Load a context file on demand |
| `load_referenced_skill_context` | Load a referenced skill's context |
| `send_message_to_role` | Send async message to another role |
| `check_inbox` | Check for pending messages |
| `reply_to_message` | Reply to a received message |
| `request_peer_consultation` | Ask another role for input |
| `form_working_group` | Form an ad hoc cross-functional group |
| `get_working_group_status` | Check working group progress |
| `delegate_to_role` | Delegate a task to a sub-role |
| `orchestrate_task` | Run a supervised multi-step task |
| `run_skill_code` | Execute a code-backed skill's Python script |

**`model`** (required) — Which Claude model to use:

| Model | Cost | Best for |
|-------|------|----------|
| `haiku` | ~$0.02/run | Data collection, routing, simple parsing |
| `sonnet` | ~$0.15/run | Analysis, recommendations, most skills |
| `opus` | ~$0.35/run | Complex strategy, nuanced judgment |

Most skills use `sonnet`. Use `haiku` for simple data-pulling skills. Use
`opus` only when the task genuinely requires strategic reasoning.

**`max_turns`** (required, default 20) — Maximum number of API round-trips the
agent can make. Each "turn" is: the agent thinks → calls a tool → gets the
result → thinks again. A skill that needs to call 5 tools and reason about the
results needs at least 8-10 turns. Set this to be comfortably above what the
skill actually needs, but not so high that a runaway agent burns money.

- Data collection skill: 5-8 turns
- Analysis skill: 10-15 turns
- Complex multi-step skill: 15-20 turns

---

## 6. Step 4: Write the System Supplement

The `system_supplement` is the most important field. It is injected into the
agent's system prompt and tells the agent exactly how to perform this skill.

### Structure: Mandatory Analysis Sequence

The single most effective pattern for high-quality agent output is the
**Mandatory Analysis Sequence**. This is a numbered list of steps the agent
MUST execute in order. Each step specifies what to do, what tools to call,
and what conditions trigger special handling.

Here is the pattern from a `system_monitor` skill:

```yaml
system_supplement: |
  You detect and diagnose system health issues across the Sidera agent
  framework. Your job is to find failed runs, cost anomalies, and
  infrastructure problems, determine root causes with evidence, and
  recommend corrective actions before small problems escalate.

  ## MANDATORY ANALYSIS SEQUENCE — execute every step, no shortcuts

  BEFORE producing any output, you MUST complete ALL 7 steps below.
  If you skip a step, your diagnosis is incomplete and unreliable.
  If a tool call fails, report the failure — NEVER silently omit data.

  **STEP 1: SYSTEM HEALTH CHECK**
  You MUST call get_system_health to get the current infrastructure status.
  Check for database connectivity, Redis availability, and Inngest worker
  status. NEVER skip this step.

  **STEP 2: FAILED RUN INSPECTION**
  You MUST call get_failed_runs to retrieve all dead letter queue entries.
  Classify each failure: transient (retry-safe) vs permanent (needs human).
  NEVER resolve entries you are unsure about.

  **STEP 3: COST ANALYSIS**
  You MUST call get_cost_summary to check LLM spend against budgets.
  Flag any role or skill where cost exceeds 120% of its expected run cost.
  ...

  **STEP 4: AUDIT LOG REVIEW**
  ...
```

### Behavioral Enforcement Language

The key to reliable agent behavior is using imperative, unambiguous language.
The agent is an LLM — it will follow suggestions loosely but will follow
commands strictly. Use this language hierarchy:

| Strength | Language | When to use |
|----------|----------|-------------|
| Strongest | `You MUST`, `NEVER`, `ALWAYS` | Non-negotiable requirements |
| Strong | `You MUST NOT`, `BEFORE doing X, you MUST do Y` | Ordering constraints |
| Medium | `If X, you MUST Y` | Conditional requirements |
| Weakest | `Consider`, `You may want to` | Suggestions (avoid in system_supplement) |

**Specific patterns that work:**

```
You MUST check system health for EVERY connected service.
NEVER resolve a failed run without diagnosing the root cause.
If a tool call fails, report the failure — NEVER silently omit data.
BEFORE recommending a fix, you MUST verify the error is reproducible.
You MUST NOT auto-resolve failures caused by auth errors or schema bugs.
```

**Patterns that do NOT work:**

```
Try to check all services.                  (too soft — agent will skip some)
It would be good to check the DLQ.          (agent treats as optional)
Consider checking for tool call failures.   (agent will not prioritize this)
You might want to look at cost data.        (agent may or may not do it)
```

### What to Include in system_supplement

1. **One-paragraph role description** — What this skill does, in the agent's voice
2. **Mandatory analysis sequence** — Numbered steps, explicit tool calls, failure handling
3. **Severity/classification rules** — Exact thresholds (not "significant" — say ">3 consecutive failures")
4. **Action rules** — What the agent MUST do vs MUST NOT do
5. **Cross-reference requirements** — e.g., "You MUST cross-reference failed runs with recent audit events"

### What NOT to Include

- Output formatting (that goes in `output_format`)
- Business rules and guardrails (that goes in `business_guidance`)
- Role persona (that comes from `_role.yaml`)
- Department context (that comes from `_department.yaml`)

---

## 7. Step 5: Write the Prompt Template

The `prompt_template` is the user-facing prompt that kicks off each run. It
supports variable substitution using Python's `str.format()` syntax.

### Available Variables

| Variable | Source | Example |
|----------|--------|---------|
| `{analysis_date}` | The date of analysis | `2025-02-24` |
| `{accounts_block}` | Formatted list of connected accounts | `System: Sidera Framework v1.0` |
| `{lookback_days}` | Configurable lookback window | `30` |
| `{previous_output}` | Output from the previous skill in the pipeline | (text from prior skill) |

### Template Structure

```yaml
prompt_template: |
  Run a system health and diagnostics analysis.
  Analysis date: {analysis_date}

  Connected Systems:
  {accounts_block}

  Check the last {lookback_days} days of failed runs, cost data, and
  audit events. Cross-reference error patterns with recent system changes.

  Focus especially on:
  - Roles with repeated failures (3+ in the lookback window)
  - Cost spikes above 120% of expected run cost
  - Unresolved DLQ entries older than 24 hours
  - Any approval queue bottlenecks
```

**Tips:**

- Be specific about what to analyze — don't just say "analyze everything"
- Reference the tools implicitly (the agent knows what tools it has)
- Include focus areas to guide the agent's attention
- The prompt template is less about instructions (that's system_supplement) and more about framing the specific run

---

## 8. Step 6: Define the Output Format

The `output_format` tells the agent exactly what structure its output should
follow. The agent will match this structure closely.

### Pattern: Section-Based Output

```yaml
output_format: |
  ## Executive Summary
  2-3 sentences: Overall system health. Are all services operational,
  are there unresolved failures, or is cost trending above budget?

  ## System Health Status
  Table with columns: Service | Status | Last Check | Notes

  ## Failed Run Analysis
  For each failed run or cluster of failures:
  - Role and skill that failed
  - Error type (transient vs permanent)
  - Root cause diagnosis
  - Recommended action (auto-resolve, escalate, or investigate)
  - Time in DLQ

  ## Cost Analysis
  Roles or skills exceeding expected cost:
  - Role/skill name
  - Expected cost vs actual cost
  - Trend (increasing/stable/decreasing)
  - Recommended action

  ## Approval Queue Status
  Summary of pending approvals:
  - Count of pending items by role
  - Oldest pending item age
  - Any bottlenecks or stuck approvals
```

**Tips:**

- Start with an Executive Summary — this is what the human reads first
- Use tables for data-heavy sections
- For each recommendation, require: what, why (with metrics), and expected impact
- Include a "time" or "urgency" indicator wherever possible — response time drives action

---

## 9. Step 7: Write Business Guidance

The `business_guidance` is the guardrail layer. It contains hard rules that
the agent must follow regardless of what the data suggests. Think of it as the
"even if the numbers say X, you must still Y" safety net.

### Structure: Hard Rules Block

```yaml
business_guidance: |
  ## HARD RULES — violations make your analysis unreliable

  - You MUST NOT auto-resolve DLQ entries caused by authentication
    failures, schema mismatches, or code bugs. These require human
    intervention. Only transient network errors are safe to auto-resolve.
  - You MUST require a minimum of 3 occurrences of the same error
    pattern before classifying it as a systemic issue. Single failures
    are noise, not signal.
  - Audit log data ALWAYS takes precedence over inferred state.
    If the audit log shows a successful run but the DLQ has an entry,
    investigate the discrepancy — do not assume either is wrong.
  - You MUST NOT recommend restarting services or clearing caches
    as a first response. Diagnose the root cause first.
  - You MUST factor in scheduled maintenance windows: failures during
    known maintenance are expected, not anomalies.
  - When total daily LLM cost exceeds 90% of the daily budget, you
    MUST flag it as a cost warning — even if all runs succeeded.
  - You MUST flag any single role consuming >40% of total daily cost
    as a concentration risk, even if the role is performing well.
```

### What Makes Good Business Guidance

1. **Specific thresholds** — "3+ occurrences" not "multiple failures"
2. **Data source precedence** — explicitly state which data source wins
3. **Minimum data requirements** — how much data before taking action
4. **Exception handling** — what to do when rules conflict
5. **Conservative defaults** — when in doubt, the agent should do less

### Difference Between system_supplement and business_guidance

| system_supplement | business_guidance |
|------------------|-------------------|
| How to analyze | What rules to follow |
| Step-by-step procedure | Constraint boundaries |
| "Check failed runs for the last 30 days" | "Never auto-resolve auth failures" |
| "Compute cost ratios per role" | "Only flag >120% of expected cost" |
| Process-oriented | Outcome-oriented |

Both are injected into the system prompt. The system_supplement comes first
(how to work), then business_guidance (what limits to respect). At runtime, the
agent sees both as part of the same instruction set.

---

## 10. Step 8: Add Context Files (Folder-Based Skills)

Context files are markdown documents that get injected into the agent's system
prompt at runtime. They provide reference material the agent can consult while
working: severity rubrics, example outputs, decision frameworks, escalation
policies.

### Setting Up Context Files

1. Make your skill a folder-based skill (directory with `skill.yaml`)
2. Create subdirectories: `context/`, `examples/`, `guidelines/`
3. Write markdown files in these directories
4. Add `context_files` glob patterns to `skill.yaml`

```yaml
# In skill.yaml
context_files:
  - "context/*.md"
  - "examples/*.md"
  - "guidelines/*.md"
```

### Glob Pattern Resolution

Patterns are resolved relative to the skill's directory (where `skill.yaml`
lives). The system uses Python's `pathlib.glob()`:

| Pattern | Matches |
|---------|---------|
| `"context/*.md"` | All `.md` files directly in `context/` |
| `"examples/**/*.md"` | All `.md` files in `examples/` and subdirs |
| `"*.md"` | All `.md` files in the skill root |

### What Goes Where

**`context/`** — Reference data the agent needs during analysis:
- Severity rubrics (tier definitions, thresholds)
- Escalation policies (who to notify, when to page)
- System-specific context (expected error rates, known issues, SLA targets)

**`examples/`** — Example outputs showing "what good looks like":
- Full example analyses with real (or realistic) data
- Annotations explaining why each decision is correct
- Both good and bad examples if helpful

**`guidelines/`** — Decision frameworks and process guides:
- How to prioritize when multiple issues exist
- Common mistakes to avoid
- Escalation criteria

### Example: Context File Content

Here is an example context file for an `incident_triage` skill
(`context/severity_rubric.md`):

```markdown
# Incident Severity Rubric

## Severity Tiers

Use the actual error count and impact scope for all tier
assignments. Single occurrences do not warrant escalation.

### Critical (P0)
- All agent roles failing simultaneously
- Database connectivity lost
- Cost runaway: daily spend >200% of budget
- Action: Immediate Slack alert to steward. Pause all scheduled runs.

### Warning (P1)
- Single role failing repeatedly (3+ consecutive failures)
- Cost for a role >150% of expected
- Approval queue backed up >48 hours
- Action: Alert steward. Investigate root cause within 4 hours.

### Info (P2)
- Transient errors that self-resolved
- Single DLQ entry with known cause
- Action: Log for pattern detection. No immediate action needed.
```

### How Context Files Are Injected

At runtime, the executor calls `load_context_text()` from
`src/skills/schema.py`. This function:

1. Resolves each glob pattern against the skill's `source_dir`
2. Reads each matching file
3. Wraps each file in a section header: `# Context: context/severity_rubric.md`
4. Concatenates all sections

The combined text is injected into the system prompt after the skill's
`system_supplement`.

### Lazy Loading for Multi-Turn Skills

For skills with `max_turns > 1`, context files are loaded **lazily** by
default. Instead of injecting the full text, the system injects a lightweight
manifest:

```
## Available Context Files
You can load any of these files using the load_skill_context tool:
- context/severity_rubric.md — Incident severity tier definitions and thresholds
- examples/good_triage_report.md — Example triage report for a multi-role outage
```

The agent then uses the `load_skill_context` MCP tool to load specific files
on demand. This saves tokens when the context is not needed for every run.

To provide descriptions in the manifest, add `context_file_descriptions`:

```yaml
context_file_descriptions:
  - pattern: "context/*.md"
    description: "Severity rubrics and escalation policies"
  - pattern: "examples/*.md"
    description: "Example triage reports showing what good output looks like"
  - pattern: "guidelines/*.md"
    description: "Decision frameworks for prioritization and escalation"
```

---

## 11. Step 9: Wire the Skill into a Role

A skill does nothing until it is listed in a role's `briefing_skills`. This is
how the system knows to run the skill during daily briefings and scheduled
executions.

### Edit the Role's `_role.yaml`

```yaml
# In _role.yaml
briefing_skills:
  - system_monitor            # Runs first
  - incident_triage           # Runs second (gets system_monitor's output)
  - cost_analysis             # Runs third
```

**Order matters.** Skills run sequentially, and each skill can access the
previous skill's output via the `{previous_output}` variable in its prompt
template. This creates a pipeline:

```
system_monitor → output → incident_triage → output → cost_analysis
```

If `incident_triage` needs to know about system health issues, it can reference
them because `system_monitor` ran first and its output is available as
`{previous_output}`.

### Verify the Role Can Access Required Tools

The role's `connectors` field determines which platform connectors are
available. If your skill needs a specific connector's tools, the role must
list it:

```yaml
# In _role.yaml
connectors:
  - custom
```

### Test a Single Skill

You can run a single skill without running the entire role via Slack:

```
/sidera run system_monitor
```

Or run the full role:

```
/sidera run role:ceo
```

---

## 12. Step 10: Configure Auto-Execute Rules

By default, every action the agent recommends requires human approval via
Slack buttons. Auto-execute rules let you pre-approve specific action patterns
so the agent can act immediately.

### Create `_rules.yaml`

Auto-execute rules live in `_rules.yaml` inside the role directory:

```yaml
# _rules.yaml
role_id: ceo

rules:
  - id: resolve_transient_errors
    description: "Auto-resolve DLQ entries caused by transient network errors"
    enabled: true
    action_types:
      - resolve_error
    conditions:
      - field: "action_params.error_type"
        operator: "eq"
        value: "transient"
      - field: "action_params.retry_count"
        operator: "gte"
        value: 3
    constraints:
      max_daily_auto_executions: 10
      cooldown_minutes: 30
      platforms:
        - custom
```

### Rule Anatomy

**`action_types`** — Which action types this rule can auto-execute. Must match
the `ActionType` enum values:
- `resolve_error`
- `restart_service`
- `clear_cache`
- `send_alert`
- `update_config`

**`conditions`** — ALL must be true (AND logic). Each condition has:
- `field` — Dot-path into the action payload (e.g., `action_params.error_type`)
- `operator` — One of: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `not_in`, `contains`, `regex`
- `value` — The comparison value

**`constraints`** — Safety limits:
- `max_daily_auto_executions` — Maximum auto-executions per day for this rule
- `cooldown_minutes` — Minimum time between auto-executions
- `platforms` — Which platforms this rule applies to

### Safety Guarantees

1. The **global kill switch** `auto_execute_enabled` defaults to `False`. No auto-execution happens until you explicitly enable it.
2. **Skill proposals** (agent modifying its own skills) can NEVER auto-execute — hard-coded block in `should_auto_execute()`.
3. **Role proposals** (agent proposing new roles) can NEVER auto-execute.
4. **Lesson contradiction check** — before auto-executing, the system checks if the agent has any high-confidence lessons (>=0.8) that contradict the action. If found, auto-execution is blocked.

---

## 13. Step 11: Add Cross-Skill References

Skills can reference other skills to create a knowledge graph. When skill A
references skill B, the agent can load skill B's context on demand.

### Add References to skill.yaml

```yaml
references:
  - skill_id: system_health_check
    relationship: "depends_on"
    reason: "Incident triage should account for current system health status"
  - skill_id: cost_monitoring
    relationship: "informs"
    reason: "Cost patterns may indicate underlying system issues"
```

### How References Work at Runtime

1. During prompt composition, the agent sees a "Related Skills" section in its
   context manifest listing available references
2. The agent can call `load_referenced_skill_context` to load a referenced
   skill's system_supplement, business_guidance, and context files
3. A traversal budget of 3 loads per agent turn prevents runaway context loading
4. Each load is capped: system_supplement 2000 chars, business_guidance 2000
   chars, context files 4000 chars

### Valid Relationships

- `depends_on` — This skill needs data/output from the referenced skill
- `informs` — This skill's output is useful to the referenced skill
- `related_to` — General topical relationship
- `contradicts` — These skills may produce conflicting recommendations

---

## 14. Step 12: Validate and Test

### Automatic Validation

When the registry loads your skill, it runs `validate_skill()` from
`src/skills/schema.py`. This checks:

- ID is non-empty, alphanumeric (plus `_` and `-`)
- Model is one of `haiku`, `sonnet`, `opus`
- Category is in the allowed list
- All platforms are recognized
- All `tools_required` exist in `ALL_TOOLS`
- `max_turns` is 1-50
- `system_supplement`, `prompt_template`, `output_format`, `business_guidance` are non-empty
- Context file patterns match at least one file (if specified)
- `chain_after` doesn't reference self
- References don't self-reference or duplicate
- Code-backed skills have `code_entrypoint` and `run_skill_code` in tools

If validation fails, the skill is logged as a warning and skipped. Check your
logs for `skill.validation_failed` events.

### Manual Validation

Run the full test suite to ensure nothing is broken:

```bash
make test
```

Run lint to ensure your YAML is well-formed:

```bash
make lint
```

Sync docs to verify the skill count is updated:

```bash
make sync-docs
```

### Testing via Slack

Test a single skill directly:

```
/sidera run system_monitor
```

Test the entire role:

```
/sidera run role:ceo
```

Start a conversation to test interactively:

```
/sidera chat ceo How is the system health looking?
```

### Testing via Code

```python
from src.skills.registry import SkillRegistry
from src.skills.schema import validate_skill

registry = SkillRegistry()
registry.load_all()

skill = registry.get("system_monitor")
assert skill is not None, "Skill not found in registry"

errors = validate_skill(skill)
assert not errors, f"Validation errors: {errors}"

print(f"Skill loaded: {skill.name}")
print(f"Tools: {skill.tools_required}")
print(f"Category: {skill.category}")
```

---

## 15. Code-Backed Skills

Code-backed skills run Python code instead of (or alongside) LLM reasoning.
The agent calls `run_skill_code` to execute the Python script, then reads and
presents the output.

### YAML Configuration

```yaml
skill_type: code_backed
code_entrypoint: code/run.py         # Relative path from skill directory
code_timeout_seconds: 120            # Subprocess timeout (max 3600)
code_output_patterns:                # Glob patterns for output files
  - "output/*.csv"
  - "output/*.json"

tools_required:
  - run_skill_code                   # REQUIRED for code-backed skills
  - send_slack_alert                 # Optional — agent can push results
```

### Directory Structure

```
cost_report/
  skill.yaml
  code/
    run.py                  # Entrypoint — the agent executes this
    cost_analysis.py        # Business logic
    generate_report.py      # Output generation
  data/                     # Input data (CSV exports, etc.)
  output/                   # Where code writes results
  context/
    threshold_notes.md
```

### The system_supplement Pattern for Code-Backed Skills

The key difference: tell the agent to RUN the code, not RE-DO the analysis:

```yaml
system_supplement: |
  You are executing a **code-backed skill**. The Python code does the
  analysis — your job is to run it, read the output, and present the
  findings. You MUST NOT re-analyze the data yourself.

  ## MANDATORY WORKFLOW — follow this exact sequence

  1. FIRST: Call run_skill_code to execute the analysis.
  2. SECOND: Read the output CSV/JSON files.
  3. THIRD: Summarize findings.
  4. FOURTH: Present in Slack-ready format.

  ## HARD RULES
  - You MUST NOT second-guess the code's recommendations.
  - You MUST show actual numbers from the output, not your own calculations.
  - If the code fails, report the error. NEVER fabricate results.
```

### ClaudeCodeExecutor

When a code-backed skill executes, the `SkillExecutor` delegates to
`ClaudeCodeExecutor` instead of the standard `SideraAgent.run_skill()`. The
executor spins up a full agent instance with access to all connectors plus the
`run_skill_code` tool. The agent runs the Python code in a subprocess,
interprets the output, and can push results to Slack or other connected
services.

---

## 16. Behavioral Enforcement Patterns

These patterns have been tested across Sidera skills and produce reliable,
consistent agent behavior.

### Pattern 1: Mandatory Step Sequences

Force the agent to execute steps in order by numbering them and adding a
preamble:

```
## MANDATORY ANALYSIS SEQUENCE — execute every step, no shortcuts

BEFORE producing any output, you MUST complete ALL N steps below.
If you skip a step, your analysis is incomplete and unreliable.
If a tool call fails, report the failure — NEVER silently omit data.

**STEP 1: ...**
**STEP 2: ...**
```

### Pattern 2: NEVER/ALWAYS Absolute Rules

For rules that must never be violated:

```
NEVER resolve a DLQ entry without diagnosing the root cause first.
ALWAYS cross-reference failed runs with recent audit events.
NEVER recommend service restarts without exhausting diagnostic steps.
ALWAYS state the exact error count when claiming "repeated failures."
```

### Pattern 3: Conditional Requirements

```
If a service health check returns degraded status, you MUST escalate
to the steward and flag it prominently in the report.

If a tool call fails, report the failure — NEVER silently omit data.

When 3+ roles show the same failure pattern simultaneously, you MUST
look for infrastructure-level causes BEFORE investigating role-specific factors.
```

### Pattern 4: Specific Thresholds Over Vague Language

```
# BAD — vague, agent will interpret inconsistently
Flag roles that are having too many failures.

# GOOD — specific, reproducible
Flag roles with 3+ consecutive failed runs in the last 24 hours.
Classify: WARNING if 3-5 failures, CRITICAL if >5 failures.
```

### Pattern 5: Data Source Precedence

```
Audit log data ALWAYS takes precedence over inferred system state.
If the audit log shows a successful completion but the DLQ has an
entry, you MUST classify it as "state inconsistency — investigate."
NEVER auto-resolve entries based on inferred state alone.
```

### Pattern 6: Conservative Defaults

```
When in doubt about resolving a DLQ entry, you MUST recommend manual
review BEFORE auto-resolving. Incorrectly resolving a real failure
is more costly than leaving it in the queue for human inspection.
```

### Pattern 7: What NOT to Do (Explicit Anti-Patterns)

```
You MUST NOT send a Slack alert if all findings are HEALTHY or INFO.
Unnecessary alerts erode trust.

You MUST NOT resolve DLQ entries caused by auth failures, schema
issues, or code bugs. Those require human intervention.
```

---

## 17. The Prompt Composition Pipeline

Understanding how the final prompt is assembled helps you write better skills.
Here is the exact composition order (from `compose_role_context()` in
`src/skills/executor.py` and `run_skill()` in `src/agent/core.py`):

### System Prompt (what the agent sees as instructions)

```
+-----------------------------------------------------+
|  BASE_SYSTEM_PROMPT (from src/agent/prompts.py)     | <- Identity, safety rules
+-----------------------------------------------------+
|  STABLE IDENTITY LAYER (cached across runs)         |
|  |-- Department context + vocabulary                | <- From _department.yaml
|  |-- Role persona                                   | <- From _role.yaml
|  |-- Decision-making principles                     | <- From _role.yaml
|  |-- Active goals                                   | <- From _role.yaml
|  |-- Role context files                             | <- From _role.yaml context_files
|  +-- Team awareness (manager roles only)            | <- From manages list
+-----------------------------------------------------+
|  DYNAMIC PER-RUN LAYER (attention edge)             |
|  |-- Memory context (hot memories, <=2000 tokens)   | <- From role_memory table
|  +-- Pending messages (peer inbox)                  | <- From role_messages table
+-----------------------------------------------------+
|  SKILL-SPECIFIC CONTEXT                             |
|  |-- system_supplement                              | <- Your instructions
|  |-- Context files (or lazy manifest)               | <- Your context/*.md files
|  |-- output_format                                  | <- Your output structure
|  +-- business_guidance                              | <- Your hard rules
+-----------------------------------------------------+
```

### User Prompt (what kicks off each run)

```
+-----------------------------------------------------+
|  prompt_template (with variables substituted)       | <- Your run prompt
+-----------------------------------------------------+
```

### Why the Ordering Matters

The ordering follows the **attention-edge principle**: the beginning and end of
context receive the strongest attention from the LLM (U-shaped attention curve).

- **Stable sections first** (department, persona, principles, goals) — these
  benefit from KV-cache prefix reuse across requests, making subsequent calls
  faster and cheaper
- **Dynamic sections last** (memories, messages) — these change every run and
  sit at the attention edge where recall is strongest
- **Skill-specific context** bridges the gap — it's specific to this skill
  but stable across runs of the same skill

---

## 18. How Skills Execute at Runtime

Here is the complete execution flow when `/sidera run role:ceo` is triggered:

```
1. Slack slash command -> src/api/routes/slack.py
   +-- Dispatches Inngest event: sidera/role.run

2. Inngest picks up event -> src/workflows/daily_briefing.py
   +-- role_runner_workflow (17 steps)

3. Step: load-registry
   +-- SkillRegistry.load_all() + merge_db_definitions()

4. Step: load-role-memory
   +-- db_service.get_hot_memories(role_id) -> compose_memory_context()

5. Step: check-inbox
   +-- db_service.get_pending_messages(role_id) -> compose_message_context()

6. Step: execute-role
   +-- For each skill in briefing_skills:
       a. SkillExecutor.execute(skill_id, ...)
       b. SideraAgent.run_skill(skill, role_context, ...)
          +-- Compose full system prompt (see Section 17)
          +-- Call Anthropic API with tools
          +-- Agent loop: think -> call tool -> get result -> think -> ...
          +-- Return BriefingResult

7. Step: extract-and-save-memories
   +-- Scan output for decisions, anomalies -> save to role_memory

8. Step: post-run-reflection
   +-- Haiku call: "What was hard? What would you do differently?"
   +-- Save lesson/insight memories

9. Step: scan-lessons-for-skill-proposals
   +-- Check if 3+ lessons about same skill -> propose skill change

10. Step: process-recommendations
    +-- Extract recommendations -> create approval queue entries
    +-- Or auto-execute if matching rules

11. Step: post-to-slack
    +-- Format output as Slack briefing -> send via Slack connector
```

### Key Points

- Each skill gets its own agent loop (fresh conversation with Claude)
- Skills in `briefing_skills` run sequentially — output from skill N is available to skill N+1
- Memory and messages are loaded ONCE before all skills run, not per-skill
- Reflection happens AFTER all skills complete, not per-skill
- The role's persona, principles, and goals are in every skill's context

---

## 19. Skill Evolution (Agent Self-Modification)

Agents can propose changes to their own skills via the `propose_skill_change`
MCP tool. This is how skills improve over time without human editing.

### How It Works

1. During reflection, the agent identifies recurring friction (e.g., "I keep
   needing to check cost summaries but my instructions don't mention it")
2. The `scan_lessons_for_skill_proposals` workflow step checks: are there 3+
   lessons about the same skill?
3. If yes, it uses Haiku to determine if a skill modification would help
4. If yes, it generates a structured proposal and routes it through the
   approval pipeline
5. The human sees a diff in Slack and clicks Approve or Reject
6. On approval, the change is written to the database

### What Agents Can Modify

Graduated evidence thresholds control what agents can propose:

| Evidence Level | Allowed Fields |
|----------------|---------------|
| 3+ lessons | `business_guidance` only |
| 5+ lessons | + `system_supplement` |
| 7+ lessons | + `prompt_template`, `output_format`, `model`, `max_turns` |

### What Agents Can NEVER Modify

These fields are hard-coded in `FORBIDDEN_FIELDS`:
- `requires_approval` — agent cannot turn off its own approval requirement
- `manages` — agent cannot change who it manages
- `is_active` — agent cannot deactivate itself or others
- `created_by` — agent cannot change attribution

---

## 20. Skill Portability (Export/Import)

Skills can be exported as portable bundles (ZIP files) and imported into other
organizations.

### Export

```python
from src.skills.portability import export_skill_to_zip

path = export_skill_to_zip(
    skill=registry.get("incident_triage"),
    registry=registry,
    output_path="./exports/incident_triage.zip",
    exported_by="michael",
)
```

### Bundle Structure

```
incident_triage.zip
  manifest.yaml          # Provenance, SHA-256 hash, compatibility
  skill.yaml             # Sanitized (org-specific fields stripped)
  context/
    severity_rubric.md
    escalation_policy.md
  examples/
    good_triage_report.md
  guidelines/
    decision_framework.md
```

### Import

```python
from src.skills.portability import import_skill_from_bundle

result = import_skill_from_bundle(
    bundle_path="./exports/incident_triage.zip",
    target_dept_id="executive",
    target_role_id="ceo",
    new_skill_id="imported_incident_triage",  # Fork with new ID
    new_author="new_org",
)
```

### What Gets Sanitized on Export

These org-specific fields are stripped: `source_dir`, `context_text`,
`department_id`, `role_id`. The skill becomes a clean, portable unit that can
be dropped into any organization's skill library.

---

## 21. Complete Reference: All YAML Fields

### Skill Fields

| Field | Required | Type | Default | Description |
|-------|----------|------|---------|-------------|
| `id` | Yes | string | — | Unique identifier |
| `name` | Yes | string | — | Human-readable name |
| `version` | Yes | string | — | Semantic version |
| `description` | Yes | string | — | Description (used by router) |
| `category` | Yes | string | — | One of 10 allowed categories |
| `platforms` | Yes | list[str] | — | Required platform connectors |
| `tags` | Yes | list[str] | — | Keywords for routing/search |
| `tools_required` | Yes | list[str] | — | Available MCP tools |
| `model` | Yes | string | — | `haiku`, `sonnet`, or `opus` |
| `max_turns` | No | int | 20 | Max API round-trips (1-50) |
| `system_supplement` | Yes | string | — | Agent instructions |
| `prompt_template` | Yes | string | — | Run prompt with variables |
| `output_format` | Yes | string | — | Expected output structure |
| `business_guidance` | Yes | string | — | Hard rules and guardrails |
| `context_files` | No | list[str] | [] | Glob patterns for context files |
| `context_file_descriptions` | No | list[dict] | [] | Descriptions for manifest |
| `schedule` | No | string | null | Cron expression |
| `chain_after` | No | string | null | Skill ID to chain after |
| `requires_approval` | No | bool | true | Whether writes need approval |
| `min_clearance` | No | string | "public" | Minimum access level |
| `skill_type` | No | string | "llm" | `"llm"` or `"code_backed"` |
| `code_entrypoint` | No | string | "" | Python script path (code-backed) |
| `code_timeout_seconds` | No | int | 300 | Subprocess timeout (code-backed) |
| `code_output_patterns` | No | list[str] | [] | Output file patterns (code-backed) |
| `references` | No | list[dict] | [] | Cross-skill references |
| `author` | No | string | "sidera" | Creator attribution |
| `created_at` | No | string | "" | Creation date |
| `updated_at` | No | string | "" | Last modification date |

### Role Fields

| Field | Required | Type | Default | Description |
|-------|----------|------|---------|-------------|
| `id` | Yes | string | — | Unique identifier |
| `name` | Yes | string | — | Human-readable name |
| `department_id` | Yes | string | — | Parent department ID |
| `description` | Yes | string | — | Role description |
| `persona` | No | string | "" | Agent personality/voice |
| `principles` | No | list[str] | [] | Decision-making heuristics |
| `goals` | No | list[str] | [] | Active objectives |
| `connectors` | No | list[str] | [] | Platform connectors needed |
| `briefing_skills` | No | list[str] | [] | Skills to run (in order) |
| `schedule` | No | string | null | Cron schedule for daily runs |
| `manages` | No | list[str] | [] | Sub-role IDs (manager roles) |
| `delegation_model` | No | string | "standard" | "standard" (Sonnet) or "fast" (Haiku) |
| `heartbeat_schedule` | No | string | null | Cron for proactive check-ins |
| `learning_channels` | No | list[str] | [] | Roles that can push learnings |
| `event_subscriptions` | No | list[str] | [] | Webhook event types to handle |
| `steward` | No | string | "" | Human steward's Slack user ID |
| `clearance_level` | No | string | "internal" | Role's own clearance level |

### Department Fields

| Field | Required | Type | Default | Description |
|-------|----------|------|---------|-------------|
| `id` | Yes | string | — | Unique identifier |
| `name` | Yes | string | — | Human-readable name |
| `description` | Yes | string | — | Department description |
| `context` | No | string | "" | Department-wide context (injected into all roles) |
| `vocabulary` | No | list[dict] | [] | Domain terminology (term + definition pairs) |
| `routing_keywords` | No | list[str] | [] | Keywords for routing queries |
| `steward` | No | string | "" | Department steward's Slack user ID |
| `slack_channel_id` | No | string | "" | Dedicated Slack channel |
| `credentials_scope` | No | string | "" | "department" for scoped creds |

---

## 22. Checklist: Before You Ship

Use this checklist before considering a skill production-ready:

### YAML Structure
- [ ] All 13 required fields are present and non-empty
- [ ] `id` is unique across the entire registry
- [ ] `id` uses only alphanumeric characters, underscores, and hyphens
- [ ] `model` is one of: `haiku`, `sonnet`, `opus`
- [ ] `category` is one of the 10 allowed values
- [ ] All `platforms` are recognized
- [ ] All `tools_required` exist in `ALL_TOOLS`
- [ ] `max_turns` is appropriate (not too low, not wastefully high)

### Instructions Quality
- [ ] `system_supplement` has a Mandatory Analysis Sequence with numbered steps
- [ ] Each step specifies which tools to call
- [ ] Failure handling is explicit ("if tool fails, report — NEVER omit")
- [ ] Uses MUST/NEVER/ALWAYS language (not "consider" or "try to")
- [ ] Specific thresholds replace vague language

### Output Quality
- [ ] `output_format` starts with Executive Summary
- [ ] Each section has clear column definitions or field requirements
- [ ] Impact or urgency indicators are required where applicable
- [ ] Recommendations include: what, why (metrics), expected impact

### Business Guidance
- [ ] Hard rules use specific thresholds
- [ ] Data source precedence is stated explicitly
- [ ] Minimum data requirements are defined
- [ ] Conservative defaults are specified for ambiguous situations
- [ ] Known anti-patterns are called out with "MUST NOT"

### Integration
- [ ] Skill is listed in the parent role's `briefing_skills`
- [ ] Role has the required `connectors` for the skill's `platforms`
- [ ] Context files (if any) exist and match the glob patterns
- [ ] Auto-execute rules (if any) have appropriate constraints
- [ ] Cross-skill references (if any) point to existing skill IDs

### Validation
- [ ] `make test` passes
- [ ] `make lint` passes
- [ ] `make sync-docs` passes (or `make update-docs` to fix counts)
- [ ] Skill loads in registry without validation warnings
- [ ] Manual test via `/sidera run <skill_id>` produces expected output
