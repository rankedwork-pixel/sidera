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

Skills are the atomic unit of agent capability. A role (like "Performance Media
Buyer") is composed of multiple skills. A department (like "Marketing") is
composed of multiple roles. Skills are designed to be:

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
  marketing/                          <-- Department
    _department.yaml                  <-- Department config
    performance_media_buyer/          <-- Role
      _role.yaml                      <-- Role config
      _rules.yaml                     <-- Auto-execute rules (optional)
      anomaly_detector.yaml           <-- Flat skill (single file)
      creative_analysis/              <-- Folder-based skill (directory)
        skill.yaml                    <-- Skill config
        context/                      <-- Context files
          scoring_rubric.md
          platform_benchmarks.md
        examples/                     <-- Example outputs
          good_analysis_ecommerce.md
        guidelines/                   <-- Decision frameworks
          decision_framework.md
      fb_creative_cuts/               <-- Code-backed skill (directory)
        skill.yaml
        code/
          run.py                      <-- Python entrypoint
        context/
          calibration_notes.md
  it/                                 <-- Another department
    _department.yaml
    head_of_it/
      _role.yaml
      system_health_check.yaml
```

**Naming conventions:**

| File | Purpose |
|------|---------|
| `_department.yaml` | Department definition (underscore prefix = config, not a skill) |
| `_role.yaml` | Role definition |
| `_rules.yaml` | Auto-execute rules for the role |
| `anomaly_detector.yaml` | Flat skill (single YAML file, no context files) |
| `creative_analysis/skill.yaml` | Folder-based skill (skill.yaml inside a directory) |

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

**Example:** The `fb_creative_cuts` skill uses Python code calibrated against
real human cut decisions. The code computes CPBC thresholds. The agent's job is
to run the code, read the CSV output, and present findings.

This guide focuses on LLM skills first. Code-backed skills are covered in
[Section 15](#15-code-backed-skills).

---

## 4. Step 2: Create the YAML File

Decide: flat skill or folder-based?

**Flat skill** — a single `.yaml` file. Use when the skill needs no context
files (examples, rubrics, guidelines).

```bash
# Create a flat skill
touch src/skills/library/marketing/performance_media_buyer/budget_optimizer.yaml
```

**Folder-based skill** — a directory containing `skill.yaml` plus context
subdirectories. Use when you want to inject examples, scoring rubrics,
decision frameworks, or other reference material into the agent's context.

```bash
# Create a folder-based skill
mkdir -p src/skills/library/marketing/performance_media_buyer/budget_optimizer
touch src/skills/library/marketing/performance_media_buyer/budget_optimizer/skill.yaml
mkdir -p src/skills/library/marketing/performance_media_buyer/budget_optimizer/context
mkdir -p src/skills/library/marketing/performance_media_buyer/budget_optimizer/examples
mkdir -p src/skills/library/marketing/performance_media_buyer/budget_optimizer/guidelines
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
id: budget_optimizer                    # Unique ID (alphanumeric + underscore + hyphen)
name: "Budget Optimizer"                # Human-readable name
version: "1.0"                          # Semantic version
description: >-                         # One-line description (used by the SkillRouter
  Analyze campaign budgets and          #   for semantic matching — make it specific)
  recommend reallocations based on
  backend-attributed ROAS

# --- Classification ---
category: budget                        # One of: analysis, optimization, reporting,
                                        #   monitoring, creative, audience, bidding,
                                        #   budget, forecasting, attribution, operations
platforms: [google_ads, meta, bigquery] # Which connectors this skill needs
tags:                                   # Keywords for routing and search
  - budget
  - allocation
  - roas
  - spend
  - efficiency

# --- Execution ---
tools_required:                         # MCP tools the agent can call
  - get_google_ads_performance
  - get_google_ads_campaigns
  - get_meta_performance
  - get_meta_campaigns
  - get_backend_performance
  - get_budget_pacing
  - get_business_goals
  - update_google_ads_campaign          # Write tool (requires approval)
  - update_meta_campaign                # Write tool (requires approval)
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

- Good: `anomaly_detector`, `creative_analysis`, `system_health_check`
- Bad: `my skill`, `budget.optimizer`, `skill #3`

**`name`** (required) — Human-readable name shown in Slack messages, dashboard,
and logs.

**`version`** (required) — Semantic version string. Increment when you make
meaningful changes. The system tracks this for skill evolution history.

**`description`** (required) — This is critically important. The `SkillRouter`
(which uses Claude Haiku) reads this description to decide whether to route a
user's query to this skill. Be specific about what the skill does and what
questions it answers. Vague descriptions cause misrouting.

- Good: "Identify significant metric anomalies across Google Ads and Meta campaigns, find root causes for sudden CPA spikes, ROAS drops, or spend surges"
- Bad: "Analyzes data" or "Does marketing stuff"

**`category`** (required) — Must be one of: `analysis`, `optimization`,
`reporting`, `monitoring`, `creative`, `audience`, `bidding`, `budget`,
`forecasting`, `attribution`, `operations`. Used for filtering and organization.

**`platforms`** (required) — Which platform connectors this skill needs data
from. Valid values: `google_ads`, `meta`, `bigquery`, `google_drive`. A skill
with `platforms: [meta, bigquery]` needs access to both Meta and BigQuery.

**`tags`** (required) — Keywords used by the SkillRouter for semantic matching.
Include synonyms. If your skill handles "CPA spikes," include both `cpa` and
`cost-per-acquisition`. The router builds a compact index from these:
`skill_id | description | tag1, tag2, tag3`.

**`tools_required`** (required) — The MCP tools this skill can call. The agent
will only have access to tools listed here. If you list a write tool (like
`update_google_ads_campaign`), the agent can propose changes but they go through
the approval pipeline.

The full list of available tools is defined in `src/agent/prompts.py` in the
`ALL_TOOLS` list (74 tools total). Common ones:

| Tool | Purpose |
|------|---------|
| `get_google_ads_performance` | Pull Google Ads metrics by date range |
| `get_google_ads_campaigns` | List campaigns with status/budget/strategy |
| `get_google_ads_changes` | Recent account changes (last N days) |
| `get_meta_performance` | Pull Meta metrics by date range |
| `get_meta_campaigns` | List Meta campaigns with objectives/budgets |
| `get_meta_audience_insights` | Breakdown by age/gender/platform/device |
| `get_backend_performance` | Backend revenue, orders, AOV from BigQuery |
| `get_campaign_attribution` | Backend campaign-level attribution |
| `get_budget_pacing` | Budget vs actual spend pacing |
| `get_business_goals` | Revenue/CPA/ROAS targets |
| `update_google_ads_campaign` | Modify Google Ads campaign (write, needs approval) |
| `update_meta_campaign` | Modify Meta campaign (write, needs approval) |
| `send_slack_alert` | Send alert to Slack channel |
| `create_google_doc` | Create a Google Doc |
| `manage_google_sheets` | Create/read/write Google Sheets |
| `get_system_health` | System infrastructure health |
| `get_failed_runs` | Dead letter queue entries |

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

Here is the pattern from the real `anomaly_detector` skill:

```yaml
system_supplement: |
  You detect and diagnose metric anomalies across all connected advertising
  platforms. Your job is to find significant deviations from normal
  performance, determine root causes with evidence, and recommend
  corrective actions before small problems become expensive ones.

  ## MANDATORY ANALYSIS SEQUENCE — execute every step, no shortcuts

  BEFORE producing any output, you MUST complete ALL 7 steps below.
  If you skip a step, your anomaly detection is incomplete and unreliable.
  If a tool call fails, report the failure — NEVER silently omit data.

  **STEP 1: BASELINE DATA COLLECTION**
  You MUST pull the last 30 days of daily performance data for EVERY active
  campaign across Google Ads and Meta. This is your baseline window.
  NEVER use fewer than 14 days. ...

  **STEP 2: KPI COMPUTATION**
  For each campaign, you MUST compute rolling baselines for ALL of these KPIs:
  - CPA (cost per acquisition — backend-attributed, NOT platform-reported)
  - ROAS (return on ad spend — backend-attributed, NOT platform-reported)
  ...

  **STEP 3: STATISTICAL BOUNDS**
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
You MUST pull data for EVERY active campaign.
NEVER use fewer than 14 days of baseline data.
If a tool call fails, report the failure — NEVER silently omit data.
BEFORE investigating complex causes, you MUST check simple ones first.
You MUST NOT recommend pausing a campaign based on a single-day anomaly.
```

**Patterns that do NOT work:**

```
Try to pull data for all campaigns.          (too soft — agent will skip some)
It would be good to use 14+ days.            (agent treats as optional)
Consider checking for tool call failures.    (agent will not prioritize this)
You might want to look at backend data.      (agent may or may not do it)
```

### What to Include in system_supplement

1. **One-paragraph role description** — What this skill does, in the agent's voice
2. **Mandatory analysis sequence** — Numbered steps, explicit tool calls, failure handling
3. **Severity/classification rules** — Exact thresholds (not "significant" — say ">2 sigma")
4. **Action rules** — What the agent MUST do vs MUST NOT do
5. **Cross-reference requirements** — e.g., "You MUST cross-reference platform data with BigQuery backend"

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
| `{accounts_block}` | Formatted list of connected accounts | `Google Ads: 123-456-7890\nMeta: act_123456789` |
| `{lookback_days}` | Configurable lookback window | `30` |
| `{previous_output}` | Output from the previous skill in the pipeline | (text from prior skill) |

### Template Structure

```yaml
prompt_template: |
  Run a budget optimization analysis for all connected accounts.
  Analysis date: {analysis_date}

  Connected Accounts:
  {accounts_block}

  Pull the last {lookback_days} days of performance and pacing data.
  Cross-reference with backend attribution data from BigQuery.
  Compare current allocation against business goals and targets.

  Focus especially on:
  - Campaigns significantly over or under budget pace
  - Campaigns where ROAS justifies budget increase
  - Campaigns where budget is being wasted (high spend, low ROAS)
  - Cross-channel reallocation opportunities
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
  2-3 sentences: Overall budget health. Are we on track, overspending,
  or leaving money on the table?

  ## Budget Pacing Table
  Table with columns: Campaign | Platform | Monthly Budget | Spent |
  Remaining | Pace Status | Projected End-of-Month

  ## Reallocation Recommendations
  For each recommended change:
  - Campaign name and ID
  - Current daily budget → Recommended daily budget
  - Rationale (with specific metrics)
  - Expected impact on backend ROAS
  - Risk assessment

  ## Efficiency Opportunities
  Campaigns where budget is being wasted:
  - Campaign name
  - Current spend and ROAS
  - Recommended action (reduce/pause/restructure)
  - Estimated weekly savings

  ## Cross-Channel Summary
  How is budget split across Google Ads vs Meta? Is the current split
  optimal based on backend-attributed performance?
```

**Tips:**

- Start with an Executive Summary — this is what the human reads first
- Use tables for data-heavy sections
- For each recommendation, require: what, why (with metrics), and expected impact
- Include a "savings" or "impact" number wherever possible — dollar values drive action

---

## 9. Step 7: Write Business Guidance

The `business_guidance` is the guardrail layer. It contains hard rules that
the agent must follow regardless of what the data suggests. Think of it as the
"even if the numbers say X, you must still Y" safety net.

### Structure: Hard Rules Block

```yaml
business_guidance: |
  ## HARD RULES — violations make your analysis unreliable

  - You MUST NOT recommend budget changes exceeding 20% of current budget
    in a single move. Large jumps destabilize platform learning algorithms.
    If ROAS justifies a 50% increase, recommend two sequential 20% increases
    over 2 weeks instead.
  - You MUST require a minimum 7-day performance window before recommending
    ANY budget change. Decisions on <7 days of data are noise, not signal.
  - Backend-attributed ROAS ALWAYS takes precedence over platform-reported
    ROAS. If they disagree, follow backend. Period.
  - You MUST NOT reallocate budget FROM a campaign that is currently in
    learning phase (indicated by "Learning" status or <50 conversions in
    the optimization window). Learning campaigns need stability.
  - You MUST factor in day-of-week effects: compare Tuesday to previous
    Tuesdays, not to trailing daily average.
  - When total account spend is within 5% of monthly budget, you MUST NOT
    recommend increases — only redistribution within current total.
  - You MUST flag campaigns spending >30% of total account budget as
    concentration risk, even if performance is good.
```

### What Makes Good Business Guidance

1. **Specific thresholds** — "20% max budget change" not "don't change too much"
2. **Data source precedence** — explicitly state which data source wins
3. **Minimum data requirements** — how much data before taking action
4. **Exception handling** — what to do when rules conflict
5. **Conservative defaults** — when in doubt, the agent should do less

### Difference Between system_supplement and business_guidance

| system_supplement | business_guidance |
|------------------|-------------------|
| How to analyze | What rules to follow |
| Step-by-step procedure | Constraint boundaries |
| "Pull 30 days of data" | "Never use <14 days baseline" |
| "Compute sigma thresholds" | "Only flag >2 sigma as Warning" |
| Process-oriented | Outcome-oriented |

Both are injected into the system prompt. The system_supplement comes first
(how to work), then business_guidance (what limits to respect). At runtime, the
agent sees both as part of the same instruction set.

---

## 10. Step 8: Add Context Files (Folder-Based Skills)

Context files are markdown documents that get injected into the agent's system
prompt at runtime. They provide reference material the agent can consult while
working: scoring rubrics, example outputs, decision frameworks, platform
benchmarks.

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
- Scoring rubrics (tier definitions, thresholds)
- Platform benchmarks (industry averages, expected ranges)
- Account-specific context (target CPA, budget caps, business constraints)

**`examples/`** — Example outputs showing "what good looks like":
- Full example analyses with real (or realistic) numbers
- Annotations explaining why each decision is correct
- Both good and bad examples if helpful

**`guidelines/`** — Decision frameworks and process guides:
- How to prioritize when multiple issues exist
- Common mistakes to avoid
- Escalation criteria

### Example: Context File Content

Here is a real context file from the `creative_analysis` skill
(`context/scoring_rubric.md`):

```markdown
# Creative Scoring Rubric

## Performance Tiers

Use backend-attributed ROAS (not platform-reported) for all tier
assignments. Platform ROAS is typically inflated 1.2-1.8x.

### Scale Tier (Top 20%)
- Backend ROAS >= 2x the account's break-even ROAS
- Frequency < 3.0 (still has headroom)
- CTR stable or increasing over last 7 days
- Action: Increase budget 10-20%. Never more than 20%.

### Cut Tier (Bottom 20%)
- Backend ROAS below break-even after sufficient data window
- OR: frequency > 4.0 AND CTR declining for 5+ consecutive days
- Action: Pause. Calculate weekly savings in the report.
```

### How Context Files Are Injected

At runtime, the executor calls `load_context_text()` from
`src/skills/schema.py`. This function:

1. Resolves each glob pattern against the skill's `source_dir`
2. Reads each matching file
3. Wraps each file in a section header: `# Context: context/scoring_rubric.md`
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
- context/scoring_rubric.md — Creative performance tier definitions and thresholds
- examples/good_analysis_ecommerce.md — Example analysis for DTC brand
```

The agent then uses the `load_skill_context` MCP tool to load specific files
on demand. This saves tokens when the context is not needed for every run.

To provide descriptions in the manifest, add `context_file_descriptions`:

```yaml
context_file_descriptions:
  - pattern: "context/*.md"
    description: "Scoring rubrics and platform benchmarks"
  - pattern: "examples/*.md"
    description: "Example analyses showing what good output looks like"
  - pattern: "guidelines/*.md"
    description: "Decision frameworks for cut/scale/test recommendations"
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
  - anomaly_detector         # Runs first
  - budget_optimizer         # Runs second (gets anomaly_detector's output)
  - creative_analysis        # Runs third
```

**Order matters.** Skills run sequentially, and each skill can access the
previous skill's output via the `{previous_output}` variable in its prompt
template. This creates a pipeline:

```
anomaly_detector → output → budget_optimizer → output → creative_analysis
```

If `budget_optimizer` needs to know about anomalies, it can reference them
because `anomaly_detector` ran first and its output is available as
`{previous_output}`.

### Verify the Role Can Access Required Tools

The role's `connectors` field determines which platform connectors are
available. If your skill needs `get_meta_performance`, the role must have
`meta` in its connectors:

```yaml
# In _role.yaml
connectors:
  - google_ads
  - meta
  - bigquery
```

### Test a Single Skill

You can run a single skill without running the entire role via Slack:

```
/sidera run budget_optimizer
```

Or run the full role:

```
/sidera run role:performance_media_buyer
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
role_id: performance_media_buyer

rules:
  - id: pause_low_roas_ads
    description: "Auto-pause ads with ROAS < 0.5x and daily spend > $100"
    enabled: true
    action_types:
      - pause_campaign
      - update_ad_status
    conditions:
      - field: "action_params.metrics.roas"
        operator: "lt"
        value: 0.5
      - field: "action_params.metrics.daily_spend"
        operator: "gt"
        value: 100.0
    constraints:
      max_daily_auto_executions: 5
      cooldown_minutes: 60
      platforms:
        - google_ads
        - meta
```

### Rule Anatomy

**`action_types`** — Which action types this rule can auto-execute. Must match
the `ActionType` enum values:
- `budget_change`
- `pause_campaign`
- `enable_campaign`
- `bid_change`
- `add_negative_keywords`
- `update_ad_schedule`
- `update_geo_bid_modifier`
- `update_ad_status`
- `update_adset_budget`
- `update_adset_bid`

**`conditions`** — ALL must be true (AND logic). Each condition has:
- `field` — Dot-path into the action payload (e.g., `action_params.metrics.roas`)
- `operator` — One of: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `not_in`, `contains`, `regex`
- `value` — The comparison value

**`constraints`** — Safety limits:
- `max_daily_auto_executions` — Maximum auto-executions per day for this rule
- `cooldown_minutes` — Minimum time between auto-executions
- `platforms` — Which platforms this rule applies to

### Safety Guarantees

1. The **global kill switch** `auto_execute_enabled` defaults to `False`. No auto-execution happens until you explicitly enable it.
2. **Budget changes** are further capped by `max_budget_change_ratio` (default 1.5 = 50%). Even if a rule allows a budget change, it cannot exceed this cap.
3. **Skill proposals** (agent modifying its own skills) can NEVER auto-execute — hard-coded block in `should_auto_execute()`.
4. **Role proposals** (agent proposing new roles) can NEVER auto-execute.
5. **Lesson contradiction check** — before auto-executing, the system checks if the agent has any high-confidence lessons (>=0.8) that contradict the action. If found, auto-execution is blocked.

---

## 13. Step 11: Add Cross-Skill References

Skills can reference other skills to create a knowledge graph. When skill A
references skill B, the agent can load skill B's context on demand.

### Add References to skill.yaml

```yaml
references:
  - skill_id: anomaly_detector
    relationship: "depends_on"
    reason: "Budget decisions should account for active anomalies"
  - skill_id: creative_analysis
    relationship: "informs"
    reason: "Creative performance affects budget allocation efficiency"
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
/sidera run budget_optimizer
```

Test the entire role:

```
/sidera run role:performance_media_buyer
```

Start a conversation to test interactively:

```
/sidera chat performance_media_buyer How are our budgets looking?
```

### Testing via Code

```python
from src.skills.registry import SkillRegistry
from src.skills.schema import validate_skill

registry = SkillRegistry()
registry.load_all()

skill = registry.get("budget_optimizer")
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
  - "output/*.docx"

tools_required:
  - run_skill_code                   # REQUIRED for code-backed skills
  - send_slack_alert                 # Optional — agent can push results
  - create_google_doc                # Optional — agent can create docs
```

### Directory Structure

```
fb_creative_cuts/
  skill.yaml
  code/
    run.py                  # Entrypoint — the agent executes this
    creative_cuts.py        # Business logic
    generate_cuts_doc.py    # Output generation
  data/                     # Input data (CSV exports, etc.)
  output/                   # Where code writes results
  context/
    calibration_notes.md
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
  2. SECOND: Read the output CSV/files.
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
interprets the output, and can push results to any connector (Google Drive,
Slack, BigQuery).

---

## 16. Behavioral Enforcement Patterns

These patterns have been tested across all 19 Sidera skills and produce
reliable, consistent agent behavior.

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
NEVER use fewer than 14 days of baseline data.
ALWAYS cross-reference platform data with BigQuery backend.
NEVER recommend changes exceeding 50% of current budget.
ALWAYS state the exact sigma value when claiming "significant deviation."
```

### Pattern 3: Conditional Requirements

```
If backend data is unavailable for a creative, you MUST label it
"Platform-only — backend data unavailable" and flag this prominently.

If a tool call fails, report the failure — NEVER silently omit data.

When 3+ campaigns show the same anomaly simultaneously, you MUST look
for account-level causes BEFORE investigating campaign-specific factors.
```

### Pattern 4: Specific Thresholds Over Vague Language

```
# BAD — vague, agent will interpret inconsistently
Flag campaigns that are significantly over budget.

# GOOD — specific, reproducible
Flag campaigns where actual spend exceeds budget pace by >10%.
Classify: WARNING if 10-20% over pace, CRITICAL if >20% over pace.
```

### Pattern 5: Data Source Precedence

```
Backend-attributed data ALWAYS takes precedence over platform data.
If backend shows stable CPA but the platform shows a spike, you MUST
classify it as "attribution artifact — not a real performance issue."
NEVER recommend budget changes based on platform-only anomalies.
```

### Pattern 6: Conservative Defaults

```
When in doubt about cutting, you MUST recommend reduced-budget test
BEFORE recommending full pause. Cutting a potential winner is more
expensive than running it at low budget for another week.
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
┌─────────────────────────────────────────────────────┐
│  BASE_SYSTEM_PROMPT (from src/agent/prompts.py)     │ ← Identity, safety rules
├─────────────────────────────────────────────────────┤
│  STABLE IDENTITY LAYER (cached across runs)         │
│  ├── Department context + vocabulary                │ ← From _department.yaml
│  ├── Role persona                                   │ ← From _role.yaml
│  ├── Decision-making principles                     │ ← From _role.yaml
│  ├── Active goals                                   │ ← From _role.yaml
│  ├── Role context files                             │ ← From _role.yaml context_files
│  └── Team awareness (manager roles only)            │ ← From manages list
├─────────────────────────────────────────────────────┤
│  DYNAMIC PER-RUN LAYER (attention edge)             │
│  ├── Memory context (hot memories, <=2000 tokens)   │ ← From role_memory table
│  └── Pending messages (peer inbox)                  │ ← From role_messages table
├─────────────────────────────────────────────────────┤
│  SKILL-SPECIFIC CONTEXT                             │
│  ├── system_supplement                              │ ← Your instructions
│  ├── Context files (or lazy manifest)               │ ← Your context/*.md files
│  ├── output_format                                  │ ← Your output structure
│  └── business_guidance                              │ ← Your hard rules
└─────────────────────────────────────────────────────┘
```

### User Prompt (what kicks off each run)

```
┌─────────────────────────────────────────────────────┐
│  prompt_template (with variables substituted)       │ ← Your run prompt
└─────────────────────────────────────────────────────┘
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

Here is the complete execution flow when `/sidera run role:performance_media_buyer` is triggered:

```
1. Slack slash command → src/api/routes/slack.py
   └── Dispatches Inngest event: sidera/role.run

2. Inngest picks up event → src/workflows/daily_briefing.py
   └── role_runner_workflow (17 steps)

3. Step: load-registry
   └── SkillRegistry.load_all() + merge_db_definitions()

4. Step: load-role-memory
   └── db_service.get_hot_memories(role_id) → compose_memory_context()

5. Step: check-inbox
   └── db_service.get_pending_messages(role_id) → compose_message_context()

6. Step: execute-role
   └── For each skill in briefing_skills:
       a. SkillExecutor.execute(skill_id, ...)
       b. SideraAgent.run_skill(skill, role_context, ...)
          └── Compose full system prompt (see Section 17)
          └── Call Anthropic API with tools
          └── Agent loop: think → call tool → get result → think → ...
          └── Return BriefingResult

7. Step: extract-and-save-memories
   └── Scan output for decisions, anomalies → save to role_memory

8. Step: post-run-reflection
   └── Haiku call: "What was hard? What would you do differently?"
   └── Save lesson/insight memories

9. Step: scan-lessons-for-skill-proposals
   └── Check if 3+ lessons about same skill → propose skill change

10. Step: process-recommendations
    └── Extract recommendations → create approval queue entries
    └── Or auto-execute if matching rules

11. Step: post-to-slack
    └── Format output as Slack briefing → send via Slack connector
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
   needing to check landing page data but my instructions don't mention it")
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
    skill=registry.get("creative_analysis"),
    registry=registry,
    output_path="./exports/creative_analysis.zip",
    exported_by="michael",
)
```

### Bundle Structure

```
creative_analysis.zip
  manifest.yaml          # Provenance, SHA-256 hash, compatibility
  skill.yaml             # Sanitized (org-specific fields stripped)
  context/
    scoring_rubric.md
    platform_benchmarks.md
  examples/
    good_analysis_ecommerce.md
  guidelines/
    decision_framework.md
```

### Import

```python
from src.skills.portability import import_skill_from_bundle

result = import_skill_from_bundle(
    bundle_path="./exports/creative_analysis.zip",
    target_dept_id="marketing",
    target_role_id="my_media_buyer",
    new_skill_id="imported_creative_analysis",  # Fork with new ID
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
- [ ] Dollar impact or savings estimates are required where applicable
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
