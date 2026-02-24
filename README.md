<p align="center">
  <h1 align="center">Sidera</h1>
  <p align="center"><strong>The open-source framework for building AI employees</strong></p>
  <p align="center">
    Connect APIs &middot; Teach skills via YAML &middot; Approve actions in Slack &middot; Automate any role
  </p>
  <p align="center">
    <a href="#quickstart">Quickstart</a> &middot;
    <a href="docs/onboarding/01-Executive-Overview.md">Docs</a> &middot;
    <a href="#architecture">Architecture</a> &middot;
    <a href="QUICKSTART.md">Full Setup Guide</a>
  </p>
</p>

---

Sidera lets you build **autonomous AI agents** that connect to your company's tools, analyze data on a schedule, recommend actions via Slack, and execute with human approval. Think of it as the operating system for an AI workforce.

The core pattern is domain-agnostic: connect any data source, teach skills via YAML, get structured Slack briefings with approve/reject buttons, and log every action. Swap the connectors and skills, and Sidera becomes a different employee.

## Why Sidera

Enterprise AI agent platforms charge six figures and lock you into their ecosystem. Sidera gives you the same capabilities for **~$0.50/day per role** in API costs:

| | Sidera | Enterprise Platforms |
|---|---|---|
| **Cost** | ~$0.50/day per role (API costs only) | $100K+/year SaaS contracts |
| **Models** | Any model (Claude, GPT, Llama, Gemini) | Locked to vendor |
| **Customization** | Full source code, YAML skills | Config UI, limited |
| **Data** | Self-hosted, your infrastructure | Vendor cloud |
| **Skills** | Write YAML, drop in a folder | Vendor-defined templates |
| **Approval flow** | Built-in Slack buttons | Varies |

**You own everything.** The code, the data, the skills, the deployment.

## What It Does

```
┌─────────────────────────────────────────────────────────────────┐
│                        YOUR COMPANY                              │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │ Google   │  │  Meta    │  │ BigQuery │  │  Slack   │        │
│  │   Ads    │  │   Ads    │  │ (backend)│  │          │        │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘        │
│       │              │              │              │              │
│       └──────────────┴──────────────┴──────┐       │              │
│                                            │       │              │
│                    ┌───────────────────┐    │       │              │
│                    │                   │◄───┘       │              │
│                    │   Sidera Agent    │            │              │
│                    │    (Claude)       │───────────►│              │
│                    │                   │  Briefings │              │
│                    └────────┬──────────┘  + Approve │              │
│                             │             / Reject  │              │
│                    ┌────────▼──────────┐  Buttons   │              │
│                    │   PostgreSQL +    │            │              │
│                    │     Redis         │            │              │
│                    └──────────────────┘            │              │
│                      Audit log, memory,            │              │
│                      approvals, skills             │              │
└─────────────────────────────────────────────────────────────────┘
```

**Every morning, your AI employees:**
1. Pull data from connected APIs
2. Analyze performance against your goals
3. Post a briefing to Slack with recommendations
4. Wait for you to approve or reject each action
5. Execute approved actions and log everything

**Throughout the day, you can:**
- `@Sidera talk to the media buyer` — chat with any role in a Slack thread
- `/sidera run anomaly_detector` — trigger any skill on demand
- Approve actions from your phone — Slack buttons work everywhere

## Core Features

### Skill System — YAML-Defined Intelligence

Skills are how you teach agents. No code required — just YAML:

```yaml
# src/skills/library/marketing/performance_media_buyer/anomaly_detector.yaml
name: Anomaly Detector
category: analysis
model: sonnet
schedule: "0 7 * * 1-5"  # 7 AM weekdays

system_supplement: |
  MANDATORY ANALYSIS SEQUENCE:
  1. Pull 30 days of data from Google Ads AND Meta
  2. Compute baselines for all KPIs (CPA, ROAS, CTR, CPC, CPM, CVR, spend, conversions)
  3. Flag anything beyond 2 standard deviations
  4. Cross-reference against backend BigQuery data
  5. Rank anomalies by financial impact

business_guidance: |
  HARD RULES:
  - Backend data ALWAYS overrides platform-reported metrics
  - Never recommend pausing a campaign based on a single day
  - Always check day-of-week patterns before flagging
```

Three-level hierarchy with context inheritance:

```
Department (Marketing)
  └── vocabulary, shared context
       └── Role (Performance Media Buyer)
            └── persona, principles, goals, memory
                 └── Skill (Anomaly Detector)
                      └── specific instructions, schedule, model
```

Context flows down. A skill inherits everything above it.

### Human-in-the-Loop — Three Trust Tiers

| Tier | How It Works | Example |
|------|-------------|---------|
| **Tier 1: Read-Only** | Agent reads data, reports findings. No write actions. | Daily briefings, anomaly reports |
| **Tier 2: Auto-Execute** | Pre-approved rules for low-risk, repetitive actions. Caps, cooldowns, kill switch. | Pause campaigns with CPA > 3x target |
| **Tier 3: Manual Approval** | Every action gets an Approve/Reject button in Slack. | Budget changes, bid strategy updates |

Auto-execute is **off by default**. When enabled, every rule has daily caps, cooldown periods, and a global kill switch. All actions are logged to an immutable audit trail.

### Manager Roles — AI That Delegates

Manager roles run their own analysis, then decide which sub-roles to activate:

```
Head of Marketing (Manager)
  ├── runs executive_summary skill
  ├── LLM decides: "activate media buyer + analyst today"
  ├── Media Buyer runs anomaly_detector, creative_analysis
  ├── Reporting Analyst runs weekly_report
  └── Manager synthesizes everything into one unified briefing
```

Recursive managers supported (CEO manages department heads who manage individual contributors).

### Persistent Memory — Agents That Learn

Eight memory types, tiered hot/cold architecture:

| Type | What It Stores | Example |
|------|---------------|---------|
| **Decision** | Past approval outcomes | "Budget increase for Campaign X was approved and improved ROAS by 15%" |
| **Anomaly** | Detected spikes/drops | "Meta CPM spike on Black Friday — seasonal, not a problem" |
| **Lesson** | Mistakes and learnings | "Don't recommend pausing branded campaigns during sales events" |
| **Commitment** | Promises made in conversation | "I said I'd investigate the CTR drop tomorrow" |
| **Steward Note** | Human-injected guidance | "Always prioritize Campaign X — it's the CEO's pet project" |

Hot memories (last 90 days) are auto-injected into every agent run. Cold memories are searchable. Agents learn from every execution.

### Conversational Mode — Talk to Any Role

Every role is both autonomous (scheduled) and conversational (Slack threads):

```
You:     @Sidera talk to the media buyer
Sidera:  Hey! I'm the Performance Media Buyer. I've been monitoring your
         campaigns — what would you like to dig into?

You:     What's going on with the Meta CPM spike this week?
Sidera:  [pulls data, analyzes, responds in character with findings]

You:     Can you pause the underperforming ad sets?
Sidera:  I'd recommend pausing these 3 ad sets:
         [Approve] [Reject]  ← buttons appear in-thread
```

Write operations work in conversations — the agent proposes actions, you approve or reject inline.

## Built-In Connectors

| Connector | Read Methods | Write Methods | What It Does |
|-----------|-------------|---------------|-------------|
| **Google Ads** | 7 | 6 | Campaigns, keywords, budgets, bids, schedules |
| **Meta Ads** | 7 | 6 | Campaigns, ad sets, ads, budgets, targeting |
| **BigQuery** | 7 | — | Backend source of truth (revenue, orders, attribution) |
| **Google Drive** | 13 | — | Docs, Sheets, Slides creation and management |
| **Slack** | 19 | — | Briefings, approvals, conversations, reactions |
| **Recall.ai** | 5 | — | Meeting transcript capture (Google Meet, Zoom) |
| **SSH** | 7 | — | Remote server execution with safety filter |
| **Computer Use** | 3 | — | Desktop automation via Anthropic Computer Use |

Adding a new connector: copy the template from `src/templates/`, implement your methods, register MCP tools. The agent loop, approval flow, and audit trail stay identical.

## What You Can Build

| Domain | Connectors You'd Add | Example Skills |
|--------|---------------------|---------------|
| **Customer Support** | Zendesk, Stripe, product DB | Ticket triage, churn risk, refund recommendations |
| **Engineering** | GitHub, Jira, PagerDuty, Datadog | Sprint health, incident postmortem, tech debt prioritization |
| **E-Commerce** | Shopify, inventory, shipping API | Reorder alerts, return analysis, demand forecasting |
| **Finance** | QuickBooks, Stripe, bank API | Cash flow monitor, invoice follow-up, anomaly detection |
| **HR / Recruiting** | Greenhouse, Lever, HRIS | Pipeline health, interview scheduling, offer analysis |

Each domain = new connectors + new YAML skills. Everything else stays the same.

## Architecture

```
src/
  agent/        Core AI agent loop, prompts, three-phase model routing
  skills/       YAML skill definitions, registry, router, executor, auto-execute rules
  connectors/   API clients (8 connectors + retry utility)
  mcp_servers/  74 MCP tools the agent can use
  workflows/    18 Inngest durable workflows (cron, approval, execution)
  db/           Async SQLAlchemy + 115-method CRUD service
  api/          FastAPI app, OAuth routes, webhooks, Slack commands
  cache/        Redis caching with @cached decorator
  middleware/   Sentry, rate limiting, auth, RBAC
  mcp_stdio/    MCP server for Claude Code integration
  meetings/     Listen-only meeting participation
  templates/    Templates for adding new connectors and tools
dashboard/      Streamlit admin UI (6 pages)
tests/          4221+ unit and integration tests
alembic/        29 database migrations
```

**Stack:** Python 3.13 &middot; FastAPI &middot; Anthropic API &middot; Inngest &middot; PostgreSQL &middot; Redis &middot; Slack Bolt &middot; SQLAlchemy

**Model Routing:** Haiku ($0.02) collects data &rarr; Sonnet ($0.15) analyzes &rarr; Opus ($0.35) adds strategy. Total ~$0.50/day per role. Opus auto-skips on stable days.

### The Agent Loop

```
Cron Trigger (configurable)
    │
    ▼
Phase 1: Data Collection (Haiku — fast, cheap)
    │   Pull from Google Ads, Meta, BigQuery via MCP tools
    │
    ▼
Phase 2: Analysis (Sonnet — accurate, efficient)
    │   Cross-reference platforms against backend truth
    │   Flag anomalies, compute trends, rank by impact
    │
    ▼
Phase 3: Strategy (Opus — deep reasoning, optional)
    │   Strategic recommendations, goal attainment, forecasting
    │   Skipped automatically on stable/low-volatility days
    │
    ▼
Slack Briefing
    │   Formatted report + [Approve] / [Reject] per recommendation
    │
    ▼
Human Decision
    │
    ├── Approve → Execute via connector → Audit log
    └── Reject  → Log reason → Agent learns for next time
```

### Safety

- **50% budget cap** — no single write operation can change a budget by more than 50%
- **Double-execution prevention** — approved actions can only execute once
- **Circuit breakers** — 20 tool calls per cycle, $10/day LLM cost cap per account
- **Clearance levels** — PUBLIC/INTERNAL/CONFIDENTIAL/RESTRICTED on users and skills
- **Immutable audit trail** — every agent action logged with timestamps, costs, and steward attribution
- **Stewardship** — every AI role has a designated human accountable for its behavior

## Quickstart

### Option A: Docker Compose (recommended)

```bash
git clone https://github.com/mzola/sidera.git
cd sidera

# Configure
cp .env.example .env
# Edit .env — minimum: ANTHROPIC_API_KEY

# Start everything
docker compose up -d
```

This starts: API server (`:8000`), PostgreSQL, Redis, Streamlit dashboard (`:8501`), Inngest dev server (`:8288`).

### Option B: Local Development

```bash
git clone https://github.com/mzola/sidera.git
cd sidera

python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,dashboard]"

cp .env.example .env
# Edit .env with your API keys

# Run database migrations
alembic upgrade head

# Start (3 terminals)
make dev                          # API server
make dashboard                    # Streamlit UI
npx inngest-cli@latest dev        # Workflow engine
```

See **[QUICKSTART.md](QUICKSTART.md)** for the complete setup guide including Slack app creation, OAuth configuration, and your first briefing.

## Configuration

All configuration via environment variables. See [`.env.example`](.env.example) for the full list with inline documentation.

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SLACK_BOT_TOKEN` | For Slack | Slack app bot token |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | For Google Ads | API access token |
| `META_APP_ID` | For Meta | Marketing API access |
| `BIGQUERY_PROJECT_ID` | For BigQuery | Backend data source |
| `REDIS_URL` | Recommended | Caching (gracefully degrades without) |

## Development

```bash
make lint          # Lint with ruff
make test          # Run 4200+ tests
make sync-docs     # Verify doc counts match codebase
make cleanup       # All of the above
```

Pre-commit hooks available: `make pre-commit`

## By the Numbers

| Metric | Count |
|--------|-------|
| Tests | 4221+ |
| MCP tools | 74 |
| DB service methods | 115 |
| Inngest workflows | 18 |
| Database migrations | 29 |
| Connectors | 8 |
| YAML skills | 11 (examples — add your own) |
| Departments | 3 |
| Agent roles | 7 |

## Contributing

Contributions welcome. The fastest ways to contribute:

1. **Add a connector** — Copy `src/templates/connector_template.py`, implement read/write methods for a new API
2. **Add skills** — Write YAML skill definitions for new domains
3. **Improve existing skills** — The 11 example skills are starting points, not finished products
4. **Report bugs** — Open an issue with reproduction steps

See [Adding a Channel Guide](docs/adding-a-channel.md) for a detailed walkthrough of adding new connectors.

## License

MIT — do whatever you want with it.
