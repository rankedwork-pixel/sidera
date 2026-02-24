# Getting Started: Deployment & Next Steps

> For a step-by-step walkthrough, see the **[QUICKSTART.md](../../QUICKSTART.md)** guide in the project root.

## What's Needed to Deploy

### API Keys & Accounts
| Service | What You Need | Purpose |
|---------|--------------|---------|
| **Anthropic** | API key | Claude models (Haiku/Sonnet/Opus) |
| **Google Ads** | OAuth client ID/secret + developer token | Campaign management |
| **Meta** | App ID/secret | Facebook/Instagram ad management |
| **BigQuery** | Service account JSON | Backend data warehouse |
| **Slack** | Bot token + signing secret | Notifications, approvals, conversations |
| **Supabase** | Database URL | PostgreSQL database |
| **Upstash** | Redis URL + token | Caching layer |
| **Railway** | Account | Hosting |
| **Recall.ai** (optional) | API key | Meeting transcript capture |

Only Anthropic and a database are truly required. Everything else is optional — Sidera degrades gracefully.

### Infrastructure
- **Railway** for hosting (FastAPI + Inngest worker)
- **Supabase** for PostgreSQL (free tier works for testing)
- **Upstash** for Redis (free tier works for testing)
- **Inngest** for workflow orchestration (free tier: 25K events/month)
- Or use **Docker Compose** for everything locally

---

## Deployment Steps

### 1. Clone & Configure
```bash
git clone https://github.com/mzola/sidera.git
cd sidera
cp .env.example .env
# Fill in API keys — see .env.example for inline docs
```

### 2. Start Infrastructure
```bash
# Option A: Docker Compose (recommended)
docker compose up -d

# Option B: Local development
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,dashboard]"
make dev                          # API server (terminal 1)
make dashboard                    # Streamlit UI (terminal 2)
npx inngest-cli@latest dev        # Workflow engine (terminal 3)
```

### 3. Database Setup
```bash
# Run migrations (29 revisions)
alembic upgrade head

# Seed initial data (optional)
python -m scripts.seed_test_data
```

### 4. Configure Slack
1. Create Slack app at [api.slack.com/apps](https://api.slack.com/apps)
2. Add bot scopes: `chat:write`, `chat:write.public`, `reactions:write`, `reactions:read`, `users:read`, `conversations:read`, `conversations:history`, `commands`, `app_mentions:read`
3. Enable Event Subscriptions → point to `https://your-domain/slack/events`
4. Subscribe to bot events: `app_mention`, `message.channels`, `message.groups`, `message.im`
5. Enable Interactivity → same URL
6. Add slash command `/sidera` → same URL
7. Install app to workspace
8. Invite bot to relevant channels

For local development, use ngrok: `ngrok http 8000`

### 5. Connect Platforms
- **Google Ads:** Navigate to `/oauth/google-ads/authorize` → complete OAuth flow
- **Meta:** Navigate to `/oauth/meta/authorize` → complete OAuth flow
- **Google Drive:** Navigate to `/oauth/google-drive/authorize` → complete OAuth flow
- **BigQuery:** Set `BIGQUERY_CREDENTIALS_JSON` in `.env` with service account JSON

### 6. Verify
```bash
# Health check
curl http://localhost:8000/health

# Test Slack
/sidera list

# Test a role
/sidera run role:head_of_it
```

---

## Onboarding a New Company

Sidera has an automated bootstrap pipeline for new company onboarding:

1. **Point at a Google Drive folder** containing company documents (strategy docs, brand guidelines, org charts, performance reports)
2. **Crawler** discovers and classifies documents with Haiku (~$0.01/100 docs)
3. **Extractor** pulls org structure, skills, goals, vocabulary with Sonnet (3 passes, ~$0.25-0.55 total)
4. **Generator** assembles a `BootstrapPlan` for human review
5. **On approval**, writes departments, roles, skills, and seed memories to DB

Total bootstrap cost: **~$0.30-0.60 per company**

---

## Adding New Skills

Skills are YAML files. To add a new skill:

```yaml
# src/skills/library/marketing/performance_media_buyer/new_skill.yaml
name: "New Skill Name"
version: "1.0"
description: "What this skill does"
category: analysis
platforms: [google_ads, meta]
tags: [relevant, tags]
tools_required:
  - get_google_ads_performance
  - get_meta_performance
model: sonnet
max_turns: 10
system_supplement: |
  MANDATORY ANALYSIS SEQUENCE:
  1. FIRST, pull data from [source] using [tool]
  2. THEN compute [metrics]
  3. Cross-reference against backend data — NEVER skip this step

  HARD RULES:
  - MUST show actual numbers, not vague descriptions
  - MUST flag anything deviating more than 20%
  - NEVER recommend actions without supporting data
prompt_template: |
  What to do this run. Date: {analysis_date}
  Accounts: {accounts_block}
output_format: |
  How to structure the output...
business_guidance: |
  HARD RULES:
  - Backend data overrides platform-reported metrics
  - Always provide week-over-week context
requires_approval: true
```

Then add it to the role's `briefing_skills` list in `_role.yaml`.

No code changes required. The registry auto-discovers new YAML files.

Or use the Skill Creator wizard: `/sidera chat skill_creator I need a new skill for budget pacing`

---

## Adding New Departments

For a completely new domain (e.g., Finance, Customer Success):

### 1. Create department YAML
```yaml
# src/skills/library/finance/_department.yaml
name: "Finance Department"
description: "Manages financial reporting, forecasting, and compliance"
context: |
  You are part of the Finance department...
vocabulary:
  - term: "ARR"
    definition: "Annual recurring revenue"
  - term: "Burn rate"
    definition: "Monthly cash expenditure"
```

### 2. Create role YAML
```yaml
# src/skills/library/finance/controller/_role.yaml
name: "Financial Controller"
description: "Monitors cash flow, expenses, and financial compliance"
persona: |
  You are a financial controller...
principles:
  - "Conservative estimates over optimistic projections"
  - "Flag variances exceeding 10% immediately"
goals:
  - "Maintain cash runway above 6 months"
  - "Keep expense-to-revenue ratio below 70%"
briefing_skills:
  - cash_flow_report
```

### 3. Add connectors (if needed)
New data sources need a connector in `src/connectors/`. Use `src/templates/connector_template.py` as a starting point. Each connector needs:
- Read methods (pull data)
- Write methods (take actions, approval-gated)
- Retry decorator
- Token encryption

### 4. Add MCP tools
Each connector needs corresponding MCP tools in `src/mcp_servers/`. Use `src/templates/mcp_server_template.py`.

---

## Slack Commands Reference

| Command | What It Does |
|---------|-------------|
| `/sidera list` | Show all skills |
| `/sidera list departments` | Show all departments |
| `/sidera list roles` | Show all roles |
| `/sidera list roles marketing` | Show roles in marketing dept |
| `/sidera run role:media_buyer` | Run media buyer's skills |
| `/sidera run manager:head_of_marketing` | Run full manager pipeline |
| `/sidera run dept:marketing` | Run entire department |
| `/sidera run skill_id` | Run a specific skill |
| `/sidera chat media_buyer` | Start conversation with media buyer |
| `/sidera chat skill_creator` | Start skill creation wizard |
| `/sidera meeting join <url>` | Join a meeting (listen-only) |
| `/sidera meeting status` | Check active meetings |
| `/sidera org list` | Show dynamic org chart |
| `/sidera org add-role ...` | Add a role via DB |
| `/sidera steward list` | Show stewardship assignments |
| `/sidera steward assign <role> @user` | Assign a steward |
| `/sidera steward note <role> <text>` | Inject steward guidance into role memory |
| `@Sidera talk to the media buyer` | Start conversational thread |
| `@Sidera hey head of IT, something broke` | Direct role conversation |

---

## Development Workflow

After making changes, always run the cleanup pipeline:

```bash
make lint          # Lint with ruff
make test          # Run 4221+ tests
make sync-docs     # Verify doc counts match codebase
make cleanup       # All of the above
```

Pre-commit hooks: `make pre-commit`

---

## What's Next

### Immediate
- [ ] Complete E2E testing (Meta live, full approval → execution flow)
- [ ] Deploy to Railway with production Slack app
- [ ] Run complete daily briefing cycle end-to-end

### Short-Term
- [ ] Build out skill library to 50+ skills
- [ ] Onboard first real company via bootstrap pipeline
- [ ] Add connectors for additional platforms (Stripe, Salesforce, HubSpot)

### Medium-Term
- [ ] Build new departments (Finance, Customer Success, Operations)
- [ ] Scale to 100+ skills across 10+ departments
- [ ] Skill marketplace for cross-company sharing
- [ ] Visual workflow builder (no-YAML skill creation)
