# Sidera — Performance Marketing Agent

## What This Is
An AI agent that connects to Google Ads + Meta ad accounts, runs first-principles analysis of campaign performance, recommends cross-platform budget reallocations, and delivers daily briefings via Slack — with human approval on all actions.

**Core thesis:** Google/Meta AI optimizes for platform revenue. Sidera optimizes for the advertiser's business outcomes. The agent's loyalty is to the advertiser's P&L.

**Target customer:** Performance marketers managing $50K-$500K+/month across Google + Meta.

## Current Status
**Phase:** Week 1, Day 1 — Project Foundation
**What's done:**
- Full project plan finalized (see `.claude/plans/valiant-sparking-abelson.md`)
- Directory structure created
- Git repo initialized

**What's next:**
- Finish foundation files (pyproject.toml, config, env, schemas)
- Begin Google Ads API connector (Days 3-5)

## Architecture Decisions
- **Agent Framework:** Claude Agent SDK (Python) with MCP servers for tool integration
- **Models:** Haiku (parsing) → Sonnet (analysis) → Opus (complex strategy)
- **Orchestration:** Inngest durable functions — scheduled cron trigger, checkpointed steps, async human approval
- **Database:** PostgreSQL via Supabase (accounts, campaigns, metrics, audit_log, approval_queue)
- **Cache:** Redis via Upstash (API response caching, session state — never sole source of truth)
- **Notifications:** Slack Bolt SDK (interactive approve/reject buttons)
- **Deploy:** Railway (cron + background workers)
- **Frontend:** Streamlit (MVP) → Next.js later

## Key Design Principles
1. **Read-first, write-gated:** Agent never modifies ad accounts without human approval
2. **Stateless agent, stateful database:** Each run is a fresh Claude conversation loading context from PostgreSQL
3. **Verify every claim:** All metrics cross-checked against raw API data before presenting
4. **Circuit breakers:** Max 20 tool calls per cycle, max $10 LLM cost per account per day
5. **First-principles analysis:** Start from advertiser data + goals, not from platform recommendations

## File Structure Overview
```
src/
  agent/        — Core agent loop, analysis engine, prompts
  mcp_servers/  — Google Ads, Meta, Slack MCP tools
  workflows/    — Inngest durable functions (daily briefing, approval, cost monitor)
  models/       — Database schema, cross-platform metric normalization
  connectors/   — API clients for Google Ads, Meta, Slack
  api/          — FastAPI app, OAuth routes, webhooks, dashboard endpoints
inngest/        — Inngest function definitions
dashboard/      — Streamlit MVP UI
tests/          — Unit + integration tests with mock fixtures
scripts/        — DB setup, manual triggers
docs/           — Build progress log
```

## Build Progress
See `docs/progress.md` for detailed session-by-session history.

## Known Issues / Blockers
None yet — just started.
