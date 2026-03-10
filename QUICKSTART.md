# Quickstart Guide

This guide takes you from `git clone` to a working Sidera instance with your first AI briefing.

**Time estimate:** ~30 minutes for Docker setup, ~45 minutes with Slack.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Clone and Configure](#2-clone-and-configure)
3. [Start the Infrastructure](#3-start-the-infrastructure)
4. [Set Up the Database](#4-set-up-the-database)
5. [Create a Slack App](#5-create-a-slack-app)
6. [Run Your First Briefing](#7-run-your-first-briefing)
7. [Talk to a Role](#8-talk-to-a-role)
8. [Add Your Own Skills](#9-add-your-own-skills)
9. [Next Steps](#10-next-steps)

---

## 1. Prerequisites

You need:

- **Python 3.11+** (3.13 recommended)
- **Docker and Docker Compose** (for PostgreSQL + Redis, or use managed services)
- **An Anthropic API key** — get one at [console.anthropic.com](https://console.anthropic.com/settings/keys)
- **A Slack workspace** where you can create apps (optional but recommended)

That's it. Connectors for your APIs are added later — Sidera works out of the box with just Claude and a database.

## 2. Clone and Configure

```bash
git clone https://github.com/rankedwork-pixel/sidera.git
cd sidera
```

### Create your environment file

```bash
cp .env.example .env
```

Open `.env` and set the required values:

```bash
# REQUIRED — everything else is optional
ANTHROPIC_API_KEY=sk-ant-your-key-here

# If using Docker Compose, these are pre-configured:
DATABASE_URL=postgresql+asyncpg://sidera:sidera_dev@localhost:5432/sidera
REDIS_URL=redis://localhost:6379/0

# If using managed services (Supabase, Upstash), use their connection strings instead
```

The `.env.example` file has inline comments explaining every setting. Most have sensible defaults.

## 3. Start the Infrastructure

### Option A: Docker Compose (recommended)

```bash
docker compose up -d
```

This starts five services:

| Service | Port | Purpose |
|---------|------|---------|
| **app** | 8000 | FastAPI server (API + Slack webhook handler) |
| **postgres** | 5432 | PostgreSQL database |
| **redis** | 6379 | Redis cache |
| **dashboard** | 8501 | Streamlit admin UI |
| **inngest** | 8288 | Workflow engine (cron jobs, approval flow) |

Verify everything is running:

```bash
# Check service health
curl http://localhost:8000/health

# Open the dashboard
open http://localhost:8501

# Open the Inngest UI
open http://localhost:8288
```

### Option B: Local Development (no Docker)

You'll need PostgreSQL and Redis running separately (install via Homebrew, apt, or use cloud services).

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install all dependencies
pip install -e ".[dev,dashboard]"

# Update .env with your PostgreSQL and Redis connection strings
# DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/sidera
# REDIS_URL=redis://localhost:6379/0
```

Start three processes (each in a separate terminal):

```bash
# Terminal 1: API server
make dev

# Terminal 2: Streamlit dashboard
make dashboard

# Terminal 3: Inngest dev server
npx inngest-cli@latest dev
```

## 4. Set Up the Database

Run the database migrations to create all tables:

```bash
# If using Docker Compose, exec into the app container:
docker compose exec app alembic upgrade head

# If running locally:
alembic upgrade head
```

Optionally seed sample data for testing:

```bash
# Docker:
docker compose exec app python -m scripts.seed_test_data

# Local:
python -m scripts.seed_test_data
```

Verify the database is ready:

```bash
curl http://localhost:8000/health
# Should return: {"status": "healthy", "database": "connected", ...}
```

## 5. Create a Slack App

This is optional but gives you the full experience — briefings, approvals, conversations.

### Step 1: Create the app

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App** → **From Scratch**
3. Name it `Sidera` (or whatever you want), select your workspace

### Step 2: Set bot permissions

Go to **OAuth & Permissions** → **Scopes** → **Bot Token Scopes** and add:

| Scope | Why |
|-------|-----|
| `chat:write` | Post briefings and replies |
| `chat:write.public` | Post to public channels |
| `reactions:write` | Add typing indicators |
| `reactions:read` | Read reactions |
| `users:read` | Look up user info |
| `conversations:read` | Access channel info |
| `conversations:history` | Fetch thread history for conversations |
| `commands` | Accept slash commands |
| `app_mentions:read` | Listen for @mentions |

### Step 3: Install to workspace

Click **Install to Workspace** at the top of **OAuth & Permissions**. Copy the **Bot User OAuth Token** (starts with `xoxb-`).

### Step 4: Get signing secret

Go to **Basic Information** → **App Credentials** → copy **Signing Secret**.

### Step 5: Update your .env

```bash
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_SIGNING_SECRET=your-signing-secret
SLACK_CHANNEL_ID=C0123456789  # Right-click channel → View Details → copy ID
```

### Step 6: Set up the webhook URL

For local development, use [ngrok](https://ngrok.com/) to expose your local server:

```bash
ngrok http 8000
```

Copy the HTTPS URL (e.g., `https://abc123.ngrok.io`).

In your Slack app settings:

1. **Interactivity & Shortcuts** → Toggle ON → Request URL: `https://abc123.ngrok.io/slack/events`
2. **Slash Commands** → Create: `/sidera` with Request URL: `https://abc123.ngrok.io/slack/events`
3. **Event Subscriptions** → Toggle ON → Request URL: `https://abc123.ngrok.io/slack/events`
   - Subscribe to bot events: `app_mention`, `message.channels`, `message.groups`, `message.im`

### Step 7: Invite the bot

In Slack, invite Sidera to your channel:

```
/invite @Sidera
```

### Step 8: Test the connection

```
/sidera list
```

You should see a list of departments, roles, and available skills.

## 6. Run Your First Briefing

### Via Slack

```
/sidera run role:ceo
```

The CEO will check org health across all departments and post a briefing to your channel.

Or run a specific skill:

```
/sidera run org_health_check
```

### Via API

```bash
curl -X POST http://localhost:8000/api/run \
  -H "Content-Type: application/json" \
  -d '{"type": "role", "id": "ceo"}'
```

### Via Inngest Dashboard

Open `http://localhost:8288`, find the `sidera/role.run` function, and trigger it manually with:

```json
{"data": {"role_id": "ceo"}}
```

### What to expect

The agent will:
1. Run all skills assigned to the role
2. Post a formatted briefing to your Slack channel
3. If it finds actionable recommendations, each gets an **Approve** / **Reject** button
4. You click a button, the action executes (or gets logged as rejected)

## 7. Talk to a Role

In Slack, mention Sidera to start a conversation:

```
@Sidera talk to the CEO
```

Or use the slash command:

```
/sidera chat ceo What's the system health looking like?
```

The agent responds in character with full tool access. It can pull live data, analyze it, and propose actions — all within a Slack thread.

**End a conversation** by simply stopping replies. Threads auto-expire after 24 hours or 20 turns.

## 8. Add Your Own Skills

Create a new YAML file in the skill library:

```bash
# Create a skill in a department under a role
mkdir -p src/skills/library/engineering/on_call_engineer
touch src/skills/library/engineering/on_call_engineer/incident_triage/skill.yaml
```

```yaml
name: Incident Triage
description: Triage incoming incidents and prioritize response
category: monitoring
model: sonnet
schedule: "*/15 * * * *"  # Every 15 minutes (optional)

system_supplement: |
  MANDATORY ANALYSIS SEQUENCE:
  1. First, ALWAYS check system health using get_system_health
  2. Then review recent failed runs using get_failed_runs
  3. Cross-reference against recent audit events
  4. NEVER skip step 3 — without cross-referencing, your analysis is unreliable

  HARD RULES:
  - MUST show actual numbers, not vague descriptions
  - MUST flag anything deviating from normal baselines
  - NEVER recommend actions without supporting data

output_format: |
  ## Incident Triage Report
  **Date:** {date}
  **Summary:** 2-3 sentence overview
  **Key Findings:** Bulleted list
  **Recommendations:** Numbered, actionable items

business_guidance: |
  HARD RULES:
  - Check the simplest explanation first — misconfig before bug
  - Flag items only when actionable — don't create noise
  - Escalate anything affecting production immediately
```

The skill is immediately available — no restart needed. Test it:

```
/sidera run incident_triage
```

### Add a new department and role

Create the directory structure:

```bash
mkdir -p src/skills/library/my_department/my_role
```

Create `src/skills/library/my_department/_department.yaml`:

```yaml
name: My Department
description: What this department handles
```

Create `src/skills/library/my_department/my_role/_role.yaml`:

```yaml
name: My Role Name
description: What this role does
persona: |
  You are the [Role Name]. You specialize in [domain].
  You're analytical, precise, and always back your recommendations with data.
principles:
  - Always cross-reference multiple data sources before concluding
  - Prefer conservative recommendations over aggressive ones
  - When uncertain, ask for clarification rather than guessing
goals:
  - Maintain [KPI] above [target]
  - Reduce [metric] by [amount] this quarter
briefing_skills:
  - my_first_skill
  - my_second_skill
```

Then add skill YAML files in that role's directory.

## 9. Next Steps

### Deploy to production

Sidera is designed for Railway deployment:

```bash
# railway.toml and Procfile are included
railway up
```

Or use Docker anywhere:

```bash
docker compose -f docker-compose.yml up -d
```

Set `APP_ENV=production` and configure your production database/Redis URLs.

### Enable scheduled briefings

In the Inngest dashboard (`http://localhost:8288`), the scheduler workflow runs on a configurable cron. By default, it checks all role schedules and dispatches runs automatically.

### Enable auto-execute (graduated trust)

For low-risk, repetitive actions, create auto-execute rules:

```yaml
# src/skills/library/<department>/<role>/_rules.yaml
rules:
  - id: auto_resolve_transient
    name: Auto-resolve transient errors
    action_types: [custom_action]
    conditions:
      - field: error_type
        operator: equals
        value: transient
    constraints:
      max_per_day: 5
      cooldown_minutes: 30
```

Auto-execute is **off by default**. Enable globally with `AUTO_EXECUTE_ENABLED=true`.

### Add a connector for your APIs

```bash
cp src/templates/connector_template.py src/connectors/my_service.py
cp src/templates/mcp_server_template.py src/mcp_servers/my_service.py
```

Implement your read/write methods. The agent loop, approval flow, memory, and audit trail all work automatically.

### Run the test suite

```bash
make test          # Full test suite
make lint          # Lint check
make cleanup       # Everything
```

---

## Troubleshooting

### "Database connection failed"

Check that PostgreSQL is running and `DATABASE_URL` is correct. For Docker Compose, ensure the postgres service is healthy:

```bash
docker compose ps
docker compose logs postgres
```

### "Slack command not responding"

1. Verify ngrok is running and the URL matches your Slack app's Request URL
2. Check that `SLACK_BOT_TOKEN` and `SLACK_SIGNING_SECRET` are set in `.env`
3. Check logs: `docker compose logs app` or check terminal output

### "Inngest functions not running"

1. Open `http://localhost:8288` — the Inngest dev server UI shows registered functions and their status
2. Ensure the app can reach Inngest: check `INNGEST_DEV=1` is set in development

### Reset everything

```bash
docker compose down -v   # Removes volumes (database data)
docker compose up -d     # Fresh start
docker compose exec app alembic upgrade head  # Recreate tables
```

---

## Getting Help

- **Issues:** [github.com/rankedwork-pixel/sidera/issues](https://github.com/rankedwork-pixel/sidera/issues)
- **Docs:** See `docs/onboarding/` for architecture deep-dives
- **Skills reference:** Every YAML file in `src/skills/library/` is a working example
