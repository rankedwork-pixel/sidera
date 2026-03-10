# Sidera

**Build AI employees that actually do work.**

Sidera is an open-source framework for creating autonomous AI agents that connect to your APIs, run on schedules, analyze data, recommend actions, and execute them — with human approval at every step.

Think of it like hiring a team of AI workers: each one has a job title, a set of skills, memory of past work, and a Slack channel where they report to you. You approve or reject their recommendations with a button click. They learn from every interaction.

---

### The Problem

You want AI agents that do real work in your business — not chatbots, not copilots, not prompt chains. Actual employees that wake up every morning, check your data, find problems, recommend solutions, and execute approved changes.

But building that from scratch means solving: tool orchestration, human approval flows, persistent memory, cost controls, audit logging, multi-agent coordination, skill definitions, scheduled execution, and safety guardrails. That's months of infrastructure work before your agent does anything useful.

### The Solution

Sidera gives you all of that out of the box. You just define **what your agents should do** (in simple YAML files) and **connect your data sources** (with a Python connector template).

```
connect data sources → define skills in YAML → get Slack briefings → approve or reject → execute → repeat
```

---

## Quick Start

```bash
git clone https://github.com/rankedwork-pixel/sidera.git && cd sidera
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

export ANTHROPIC_API_KEY="your-key-here"
make demo
```

The demo runs a full agent briefing cycle against sample data — no database, no Slack, just Python and an API key.

---

## How It Works

### 1. Define Skills in YAML

Skills tell agents what to do. No code required for most skills — just describe the task, pick a model, and set a schedule.

```yaml
name: Daily Health Check
category: monitoring
model: sonnet
schedule: "0 7 * * 1-5"   # 7 AM, weekdays

system_supplement: |
  1. Check system health across all connected services
  2. Review error rates and latency trends
  3. Compare against 7-day baselines
  4. Flag anything unusual and recommend fixes

business_guidance: |
  - Don't alert on single transient errors
  - Always check if scheduled maintenance explains anomalies
  - Rank issues by business impact, not technical severity
```

### 2. Organize Into Teams

Skills are grouped into a three-level hierarchy: **Department → Role → Skill**.

Each role has a persona, principles, goals, and memory. Context flows down — a skill inherits everything from its role and department. Manager roles delegate to sub-roles and synthesize results.

```
Executive/
  CEO (manager)
    → org_health_check
    → delegates to department heads

Engineering/
  On-Call Engineer
    → incident_triage
    → runbook_executor
```

### 3. Approve or Reject in Slack

Every recommendation shows up in Slack with Approve/Reject buttons. You stay in control.

Agents can also hold conversations — `@Sidera talk to the on-call engineer` starts a threaded back-and-forth with full tool access.

### 4. Agents Learn and Evolve

Agents persist memories across runs — decisions made, anomalies found, lessons learned, commitments tracked. They reflect after every run and propose improvements to their own skills through the same approval pipeline.

---

## Key Features

| Feature | What It Means |
|---------|--------------|
| **YAML-based skills** | Define agent behavior without code. Schedule, model, tools, and instructions in one file. |
| **Human-in-the-loop** | Every write action goes through Slack approval. Auto-execute rules available (off by default). |
| **Persistent memory** | Agents remember across runs. Hot/cold tiers, weekly consolidation, contradiction detection. |
| **Manager roles** | Hierarchical delegation — managers decide which sub-roles to activate and synthesize results. |
| **Skill evolution** | Agents propose changes to their own skills. Forbidden fields prevent safety bypass. |
| **Multi-agent working groups** | Form ad hoc cross-functional teams around a shared objective. |
| **Pluggable connectors** | Copy a template, implement your methods. Framework handles the rest. |
| **Plugin import** | Absorb Claude Code / Cowork plugins — agents can use external MCP tools. |
| **Cost controls** | Per-run budgets, daily caps, three-phase model routing (Haiku → Sonnet → Opus). |
| **Full audit trail** | Every action, recommendation, approval, and cost tracked in PostgreSQL. |
| **Stewardship** | Every AI role has a designated human accountable for its behavior. |

---

## Adding a Connector

Connectors let agents talk to your APIs. Copy the template, implement your read/write methods, register MCP tools:

```bash
cp src/templates/connector_template.py src/connectors/my_service.py
```

The agent loop, approval flow, memory, and audit trail all work automatically with any connector.

---

## Architecture

```
src/
  agent/        Core agent loop, prompts, model routing
  skills/       YAML definitions, registry, router, executor, evolution
  connectors/   API clients (Slack built-in, add your own)
  mcp_servers/  MCP tools for agent capabilities
  plugins/      Claude Code / Cowork plugin import
  workflows/    13 Inngest durable functions (scheduling, orchestration)
  db/           Async SQLAlchemy + 115-method CRUD service
  api/          FastAPI, Slack commands, org chart API
  mcp_stdio/    MCP server for Claude Code integration
  llm/          Hybrid model routing (Claude + external providers)
  cache/        Redis caching layer
  middleware/   Auth, RBAC, rate limiting, Sentry
  templates/    Starter templates for new connectors and tools
```

**Stack:** Python, FastAPI, Anthropic Claude API, Inngest, PostgreSQL, Redis, Slack Bolt, SQLAlchemy

---

## Setup

### Docker (fastest)

```bash
cp .env.example .env  # add your API keys
docker compose up -d
```

### Local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,dashboard]"
cp .env.example .env  # add your API keys
alembic upgrade head

make dev                     # API server
make dashboard               # Streamlit admin UI
npx inngest-cli@latest dev   # Workflow engine
```

### Development

```bash
make demo       # Zero-config demo (no DB needed)
make lint       # Ruff linter
make test       # Full test suite
make cleanup    # Format + lint + test + sync-docs
```

---

## Docs

- [QUICKSTART.md](QUICKSTART.md) — full setup walkthrough
- [CONTRIBUTING.md](CONTRIBUTING.md) — how to contribute
- [docs/onboarding/](docs/onboarding/) — architecture deep-dives
- [docs/skill-creation-guide.md](docs/skill-creation-guide.md) — writing custom skills

---

## License

MIT
