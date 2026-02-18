# Sidera — AI Agent Framework

> Connect APIs. Teach skills. Approve actions. Automate any role.

Sidera is a framework for building **AI employees** — autonomous agents that connect to external systems, analyze data on a schedule, recommend actions, and execute with human approval via Slack.

The pattern is domain-agnostic: connect data sources, teach the agent via YAML skills (with examples, context, and guidelines), get structured briefings in Slack with approve/reject buttons, and log every action. Swap the connectors and skills, and Sidera becomes a different employee.

## Built-In Connectors

Sidera ships with connectors ready to use out of the box:

- Google Ads (7 read + 6 write methods)
- Meta Marketing API (7 read + 6 write methods)
- BigQuery for backend business data
- Google Drive (Docs, Sheets, Slides)
- Slack (notifications, approvals, conversations)
- Recall.ai (meeting transcript capture)

23 example skills across 3 departments demonstrate the skill system. Add your own connectors and skills for any domain — customer support, engineering management, e-commerce, finance, or anything with APIs and decisions.

## Framework Capabilities

### Core Agent Loop
- **Multi-source data analysis** — Connect any API, pull data on a schedule or on-demand, cross-reference across sources
- **Scheduled Slack briefings** — Automated reports with actionable insights and interactive approve/reject buttons
- **Three-phase model routing** — Haiku collects data (~$0.02), Sonnet analyzes (~$0.15), Opus adds strategy (~$0.35). ~$0.50/day per account
- **Full audit trail** — Every agent action logged for compliance and debugging
- **Cost controls** — Circuit breakers ($10/day LLM cap, 20 tool calls/cycle)

### Conversational Mode
- **Talk to any role** in a Slack thread (`@Sidera talk to the analyst`). The agent stays in character, uses tools, and can propose write operations with in-thread Approve/Reject buttons
- **Write operations in conversations** — Agent generates action proposals → Approve/Reject buttons appear in-thread → approved actions execute and post results back
- **Slash command** — `/sidera run <skill_id>`, `/sidera run manager:<role_id>`, `/sidera chat <role_id>`, or describe what you need in natural language

### Skill System
- **YAML-defined skills** (flat files or rich folders with examples/context/guidelines) with Haiku-powered semantic routing
- **Three-level hierarchy** — Department → Role → Skill. Context flows down: department context → role persona → skill instructions
- **Manager roles** — Hierarchical delegation: a manager role runs its own analysis, decides which sub-roles to activate via LLM, runs them, and synthesizes everything into a unified report
- **Skill evolution** — Agents propose improvements to their own skill definitions. Changes go through human approval with diff view in Slack
- **Dynamic org chart** — Add, update, or remove departments, roles, and skills at runtime via Slack commands or REST API. No code changes or restarts needed

### Claude Code Integration
- **Approval-gated Claude Code tasks** — Agents propose Claude Code task execution for complex, multi-step work (file editing, bash commands, multi-turn investigation). Proposals show in Slack with task preview (skill, prompt, budget, permission mode) and Approve/Reject buttons
- **Graduated trust** — Auto-execute rules for trusted Claude Code patterns (e.g., health checks under $5 with file-edit permissions). Hard safety blocks: `bypassPermissions` always requires human approval, budget over $10 always requires human approval
- **Skill-based execution** — Claude Code tasks map to skills in the registry, inheriting their system prompts, context, and constraints

### Safety & Trust
- **Human-in-the-loop** — Every write action requires explicit human approval. Read everything, change nothing without permission
- **Graduated trust** — Define auto-execute rules per role for low-risk, repetitive actions. Global kill switch, daily caps, cooldowns
- **Persistent role memory** — Agents remember past decisions, anomalies, and patterns. Hot memories auto-injected into context; cold archive searchable via Slack
- **Post-run reflection** — Haiku call after each role run captures insights and lessons as memories. Agents learn from every execution
- **Information security** — Clearance levels (PUBLIC/INTERNAL/CONFIDENTIAL/RESTRICTED) on users and skills, role-scoped agent-to-agent filtering

### Listen-Only Meetings
- **Meeting participation** — Manager roles join live video calls (Google Meet, Zoom) as listen-only participants via Recall.ai bot
- **Transcript capture** — Real-time transcript via webhooks. Post-call: transcript summary → action item extraction → manager delegation

### Agent Collaboration
- **Peer-to-peer messaging** — Roles send asynchronous messages to each other without manager mediation. Anti-loop protection (max 3 per run, max 5 chain depth)
- **Proactive heartbeats** — Roles wake up on configurable cron schedules and freely investigate their domain
- **Google Drive integration** — Auto-generate reports in Docs, Sheets, and Slides

## What You Can Build

| Domain | Connectors | Example Skills |
|--------|-----------|---------------|
| **Customer Support Ops** | Zendesk, Stripe, product DB | Ticket triage, churn risk detection, refund recommendations |
| **Engineering Management** | GitHub, Jira, PagerDuty, Datadog | Sprint health, incident postmortem, tech debt prioritization |
| **E-Commerce Ops** | Shopify, inventory system, shipping API | Reorder alerts, return analysis, demand forecasting |
| **Finance / Accounting** | QuickBooks, Stripe, bank API | Cash flow monitor, invoice follow-up, anomaly detection |
| **Digital Marketing** | Google Ads, Meta, BigQuery | Creative analysis, budget reallocation, pacing monitor |

Each new domain = new connectors + new skills. The agent loop, Slack interaction, approval queue, audit trail, cron scheduling, cost controls, encryption, and error handling stay identical.

## Architecture

```
                          ┌──────────────┐
┌─────────────┐           │              │           ┌─────────────┐
│ Data Source  │──────────▶│   Sidera     │──────────▶│    Slack     │
│   APIs       │           │   Agent      │           │  Briefing +  │
│ (any domain) │           │  (Claude)    │           │  Approvals   │
└─────────────┘           │              │           └─────────────┘
                          └──────┬───────┘
┌─────────────┐                 │              ┌─────────────┐
│  Backend DB  │──────────▶     │         ────▶│ Google Drive │
│ (source of   │          ┌──────▼───────┐     │ Docs/Sheets  │
│   truth)     │          │  PostgreSQL   │     └─────────────┘
└─────────────┘           │  + Redis      │
                          └──────────────┘
```

**Built-in connectors:** Google Ads, Meta, BigQuery, Google Drive, Slack, Recall.ai (6 total)
**Adding new connectors:** Copy templates from `src/templates/`, implement read/write methods, register MCP tools.

**Stack:** Python 3.13 · FastAPI · Anthropic API · Claude Agent SDK · Inngest · SQLAlchemy · Slack Bolt · Streamlit

## Quick Start

### Prerequisites
- Python 3.12+
- PostgreSQL (or use Docker Compose)
- Redis (or use Docker Compose)

### Setup

```bash
# Clone and enter project
git clone https://github.com/your-org/sidera.git
cd sidera

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e ".[dev,dashboard]"

# Copy environment config
cp .env.example .env
# Edit .env with your API keys

# Set up database
python -m scripts.create_tables

# (Optional) Seed sample data
python -m scripts.seed_test_data
```

### Run with Docker Compose

```bash
docker compose up -d
```

This starts: API server (port 8000), PostgreSQL, Redis, Streamlit dashboard (port 8501), and Inngest dev server (port 8288).

### Run Locally

```bash
# Start the API server
uvicorn src.api.app:app --reload --port 8000

# Start the Streamlit dashboard (separate terminal)
streamlit run dashboard/app.py

# Start Inngest dev server (separate terminal)
npx inngest-cli@latest dev
```

## Running Tests

```bash
# Full test suite
make test

# Lint + format
make lint

# Sync doc counts with codebase
make sync-docs

# All of the above
make cleanup
```

## Project Structure

```
src/
  agent/        — Core AI agent loop, prompts, three-phase model routing
  skills/       — YAML skill definitions, registry, router, executor, manager executor, auto-execute rules
  api/          — FastAPI app, OAuth routes, webhooks, /sidera command, org chart REST API
  cache/        — Redis caching layer with @cached decorator
  claude_code/  — Claude Code headless executor, task manager, concurrency control
  connectors/   — API clients (6 connectors + retry utility — extensible)
  db/           — Async SQLAlchemy session + 115-method CRUD service
  meetings/     — Listen-only meeting session manager, transcript capture
  middleware/   — Sentry, rate limiting, structured request logging, API auth, RBAC
  mcp_servers/  — MCP tools for the Claude agent (62 tools — extensible)
  mcp_stdio/    — MCP stdio server for Claude Code (bridge, meta-tools)
  models/       — Database schema, cross-platform metric normalization
  workflows/    — Inngest durable functions (15 workflows)
  templates/    — Templates for adding new connectors, MCP tools, and OAuth routes
dashboard/      — Streamlit MVP (6 pages)
scripts/        — Operational scripts (seed, health check, cache, audit, doc sync)
tests/          — 3613+ unit and integration tests
alembic/        — Database migrations (18 revisions)
```

## Configuration

All configuration is via environment variables. See [`.env.example`](.env.example) for the full list.

Key settings:
| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SLACK_BOT_TOKEN` | Yes | Slack app bot token |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | For Google Ads | Google Ads API access |
| `META_APP_ID` | For Meta | Meta Marketing API access |
| `BIGQUERY_PROJECT_ID` | For backend data | BigQuery source of truth |
| `REDIS_URL` | Recommended | Upstash Redis for caching |
| `CLAUDE_CODE_ENABLED` | For Claude Code | Enable Claude Code task execution |

## How It Works

1. **Scheduled Cron (configurable):** Inngest triggers a workflow (e.g., daily briefing at 7 AM weekdays)
2. **Data Pull:** Agent fetches data from connected sources via MCP tools
3. **Analysis:** Claude analyzes data against configured goals and thresholds
4. **Briefing:** Formatted report sent to your Slack channel (optionally saved to Google Drive)
5. **Approval Flow:** Each recommendation gets an interactive approve/reject button
6. **Execution:** Approved actions are executed against the source APIs and logged

### Skill System

Skills are the core extensibility mechanism. Each skill is a YAML file (or folder with examples/context/guidelines) that teaches the agent how to perform a specific task:

```
/sidera analyze my Meta creatives     → routes to creative_analysis skill
/sidera run budget_reallocation       → runs budget optimization directly
/sidera list                          → shows all available skills
```

Add new skills without writing code — just drop YAML in `src/skills/library/`.

### Claude Code Tasks

For complex multi-step work, agents can propose Claude Code task execution:

```
User: "Use claude code to run a system health check"
  → Agent calls propose_claude_code_task(skill_id, prompt, budget, ...)
  → Slack shows Approve/Reject with task preview
  → On Approve: Claude Code executes the task
  → Result posted back to the Slack thread
```

Safety: hard blocks on `bypassPermissions` and budgets over $10. Auto-execute rules available for trusted patterns.

### Adding a New Domain

1. **Create connectors** — Copy `src/templates/connector_template.py`, implement read/write methods for your APIs
2. **Create MCP tools** — Copy `src/templates/mcp_server_template.py`, expose connector methods as agent tools
3. **Create skills** — Write YAML skill definitions for the domain's tasks
4. **Configure** — Add environment variables for API credentials
5. **Deploy** — Same infrastructure, new capabilities

See the [Adding a Channel Guide](docs/adding-a-channel.md) for a detailed walkthrough.

## By the Numbers

| Metric | Count |
|--------|-------|
| Unit + integration tests | 2916+ |
| MCP tools | 54 |
| DB service methods | 98 |
| Inngest workflows | 15 |
| Alembic migrations | 18 |
| API connectors | 8 |
| YAML skills | 19 |
| Departments | 2 |
| Agent roles | 5 |

## License

MIT
