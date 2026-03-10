# Sidera

Open-source framework for building autonomous AI agents that connect to APIs, run on schedules, and execute actions with human approval.

```bash
git clone https://github.com/rankedwork-pixel/sidera.git && cd sidera
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

export ANTHROPIC_API_KEY="your-key-here"
make demo
```

The demo runs a full agent briefing cycle against sample data — no database, no external services, just Python and an API key.

## What It Does

Sidera agents pull data from APIs, analyze it with Claude, and post structured recommendations to Slack with approve/reject buttons. Approved actions execute through the same connectors. Everything is logged.

The core loop: **connect data sources → define skills in YAML → get Slack briefings → approve or reject → execute → repeat.**

Skills, connectors, and roles are all swappable. The framework handles the agent loop, approval pipeline, memory, scheduling, and audit trail.

## Skills

Skills are YAML files that tell agents what to do:

```yaml
name: Anomaly Detector
category: analysis
model: sonnet
schedule: "0 7 * * 1-5"

system_supplement: |
  1. Pull 30 days of data from Google Ads AND Meta
  2. Compute baselines for all KPIs
  3. Flag anything beyond 2 standard deviations
  4. Cross-reference against backend BigQuery data

business_guidance: |
  - Backend data ALWAYS overrides platform-reported metrics
  - Never recommend pausing a campaign based on a single day
```

Skills are organized into a three-level hierarchy: **Department → Role → Skill**. Context flows down — a skill inherits its role's persona, principles, and goals, plus its department's vocabulary and shared context.

## Approval Tiers

| Tier | Behavior |
|------|----------|
| Read-only | Agent reads data, reports findings. No writes. |
| Auto-execute | Pre-approved rules with caps, cooldowns, kill switch. Off by default. |
| Manual approval | Approve/Reject buttons in Slack for every action. |

## Memory

Agents persist memories across runs — decisions, anomalies, lessons, commitments, steward notes, cross-role insights. Hot memories (90 days) are auto-injected. Cold memories are searchable. Weekly consolidation detects contradictions.

## Conversations

Every role works in Slack threads. `@Sidera talk to the media buyer` starts a conversation pinned to that role. Write operations work inline — the agent proposes, you approve or reject in-thread.

## Manager Roles

A manager is a role with a `manages` field. It runs its own skills, decides which sub-roles to activate (via LLM), runs them, and synthesizes the results. Recursive (managers of managers) supported.

## Skill Evolution

Agents propose changes to their own skills through the approval pipeline. Post-run reflection detects recurring friction and generates modification proposals. Agents cannot modify their own safety controls (`requires_approval`, `manages`, `is_active` are forbidden fields).

## Connectors

Ships with 8 reference connectors: Google Ads, Meta Ads, BigQuery, Google Drive, Slack, Recall.ai (meetings), SSH, Computer Use. Adding a connector: copy `src/templates/connector_template.py`, implement methods, register MCP tools.

## Plugin Import

Sidera can absorb Claude Code / Cowork plugins — importing skills and connecting to MCP servers so agents can use external tools. `load_plugin` / `unload_plugin` meta-tools for interactive management.

## Project Structure

```
src/
  agent/        Agent loop, prompts, three-phase model routing
  skills/       YAML definitions, registry, router, executor, evolution
  connectors/   API clients (8 connectors + retry)
  mcp_servers/  74 MCP tools
  plugins/      Claude Code / Cowork plugin import
  workflows/    18 Inngest durable functions
  db/           Async SQLAlchemy + 115-method CRUD service
  api/          FastAPI, OAuth, webhooks, Slack commands
  mcp_stdio/    MCP server for Claude Code integration
  llm/          Hybrid model routing (Claude + external providers)
  cache/        Redis caching
  middleware/   Sentry, rate limiting, auth, RBAC
  meetings/     Listen-only meeting participation
  templates/    Templates for new connectors/tools
dashboard/      Streamlit admin UI
tests/          4200+ tests
alembic/        29 migrations
```

**Stack:** Python 3.13, FastAPI, Anthropic API, Inngest, PostgreSQL, Redis, Slack Bolt, SQLAlchemy

## Setup

### Docker Compose

```bash
cp .env.example .env  # edit with your API keys
docker compose up -d
```

### Local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,dashboard]"
cp .env.example .env  # edit with your API keys
alembic upgrade head

make dev              # API server
make dashboard        # Streamlit UI
npx inngest-cli@latest dev  # Workflow engine
```

See [QUICKSTART.md](QUICKSTART.md) for the full setup guide.

## Development

```bash
make demo          # Zero-config demo
make lint          # Ruff
make test          # 4200+ tests
make cleanup       # Format + lint + test + sync-docs
```

## Configuration

All via environment variables. See [`.env.example`](.env.example) for the full list.

Key variables: `ANTHROPIC_API_KEY`, `DATABASE_URL`, `SLACK_BOT_TOKEN`, `REDIS_URL`.

## Docs

- [QUICKSTART.md](QUICKSTART.md) — full setup guide
- [CONTRIBUTING.md](CONTRIBUTING.md) — how to contribute
- [docs/onboarding/](docs/onboarding/) — architecture deep-dives
- [docs/skill-creation-guide.md](docs/skill-creation-guide.md) — writing skills

## License

MIT
