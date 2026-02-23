# Getting Started: Deployment & Next Steps

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

### Infrastructure
- **Railway** for hosting (FastAPI + Inngest worker)
- **Supabase** for PostgreSQL (free tier works for testing)
- **Upstash** for Redis (free tier works for testing)
- **Inngest** for workflow orchestration (free tier: 25K events/month)

---

## Deployment Steps

### 1. Clone & Configure
```bash
git clone <repo>
cp .env.example .env
# Fill in API keys
```

### 2. Database Setup
```bash
# Run migrations
alembic upgrade head

# Seed initial data (optional)
python scripts/seed_data.py
```

### 3. Deploy to Railway
```bash
# Railway will use the existing Dockerfile + railway.toml
railway up
```

### 4. Configure Slack
1. Create Slack app at api.slack.com/apps
2. Enable Event Subscriptions → point to `https://your-domain/slack/events`
3. Enable Interactivity → same URL
4. Add slash command `/sidera` → same URL
5. Install app to workspace
6. Invite bot to relevant channels

### 5. Connect Platforms
- **Google Ads:** Navigate to `/api/oauth/google-ads/authorize` → complete OAuth flow
- **Meta:** Navigate to `/api/oauth/meta/authorize` → complete OAuth flow
- **Google Drive:** Navigate to `/api/oauth/google-drive/authorize` → complete OAuth flow
- **BigQuery:** Upload service account JSON via config

### 6. Verify
```bash
# Health check
curl https://your-domain/health

# Test Slack
/sidera list roles

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
id: new_skill
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
  Detailed instructions for the agent...
prompt_template: |
  What to do this run. Date: {analysis_date}
  Accounts: {accounts_block}
output_format: |
  How to structure the output...
business_guidance: |
  Domain-specific rules and guardrails...
requires_approval: true
```

Then add it to the role's `briefing_skills` list in `_role.yaml`.

No code changes required. The registry auto-discovers new YAML files.

---

## Adding New Departments

For a completely new domain (e.g., Finance, Customer Success):

### 1. Create department YAML
```yaml
# src/skills/library/finance/_department.yaml
id: finance
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
id: controller
name: "Financial Controller"
department_id: finance
description: "Monitors cash flow, expenses, and financial compliance"
persona: |
  You are a financial controller...
principles:
  - "Conservative estimates over optimistic projections"
connectors:
  - bigquery  # or new connectors: stripe, quickbooks
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
Each connector needs corresponding MCP tools in `src/mcp_servers/`. Use `src/templates/mcp_template.py`.

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
| `/sidera chat media_buyer` | Start conversation with media buyer |
| `/sidera meeting join <url>` | Join a meeting (listen-only) |
| `/sidera meeting status` | Check active meetings |
| `/sidera org list` | Show dynamic org chart |
| `/sidera org add-role ...` | Add a role via DB |
| `/sidera steward list` | Show stewardship assignments |
| `/sidera steward assign <role> @user` | Assign a steward |
| `@Sidera talk to the media buyer` | Start conversational thread |
| `@Sidera hey head of IT, something broke` | Direct role conversation |

---

## What's Next

### Immediate (E2E Testing)
- [ ] Verify Meta connector with live API keys
- [ ] Test full approval → execution flow (approve in Slack → action taken)
- [ ] Run complete daily briefing cycle end-to-end
- [ ] Test manager delegation (Head of Marketing → sub-roles → synthesis)
- [ ] Verify webhook event reactor with simulated alerts

### Short-Term (Production Readiness)
- [ ] Deploy to Railway
- [ ] Configure production Slack app (no ngrok)
- [ ] Set up monitoring dashboards (Sentry, cost tracking)
- [ ] Onboard first real company via bootstrap pipeline
- [ ] Build out skill library to 50+ skills

### Medium-Term (Scale)
- [ ] Add connectors for additional platforms (Stripe, Salesforce, HubSpot)
- [ ] Build new departments (Finance, Customer Success, Operations)
- [ ] Scale to 100+ skills across 10+ departments
- [ ] Enable hybrid model routing for cost optimization
- [ ] Skill marketplace for cross-company sharing

### Long-Term (Vision)
- [ ] Self-improving skill library (agents propose and refine their own skills)
- [ ] Cross-company benchmarking (anonymized performance comparisons)
- [ ] Industry-specific skill packs (DTC e-commerce, SaaS, fintech)
- [ ] Visual workflow builder (no-YAML skill creation)
