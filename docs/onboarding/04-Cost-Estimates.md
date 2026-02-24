# Cost Estimates: Annual Projections

## Model Pricing (Anthropic, as of 2025)

| Model | Input (per 1M tokens) | Output (per 1M tokens) | Typical Use |
|-------|----------------------|----------------------|-------------|
| Haiku | $0.25 | $1.25 | Data collection, routing, memory extraction, reflection |
| Sonnet | $3.00 | $15.00 | Analysis, conversation, most skills |
| Opus | $15.00 | $75.00 | Complex strategy, CEO role, volatile-day analysis |

Extended thinking adds ~10-30% to Sonnet/Opus costs when enabled ($0.52 → $0.57-0.67/briefing).

Hybrid routing (optional) sends structured tasks to cheaper OpenAI-compatible providers, reducing Haiku-tier costs by 55-60%.

---

## Per-Role Daily Cost Estimates

### Performance Media Buyer
| Activity | Model | Frequency | Est. Cost/Run | Daily Cost |
|----------|-------|-----------|---------------|------------|
| anomaly_detector | Sonnet | 1x/weekday | $0.15 | $0.15 |
| creative_analysis | Sonnet | 1x/weekday | $0.12 | $0.12 |
| fb_creative_cuts | Sonnet | 1x/weekday | $0.12 | $0.12 |
| Post-run reflection | Haiku | 1x/weekday | $0.01 | $0.01 |
| Memory extraction | Haiku | 1x/weekday | $0.005 | $0.005 |
| **Subtotal** | | | | **$0.42/day** |

### Reporting Analyst
| Activity | Model | Frequency | Est. Cost/Run | Daily Cost |
|----------|-------|-----------|---------------|------------|
| weekly_report | Sonnet | 1x/week | $0.20 | $0.04* |
| Reflection + memory | Haiku | 1x/week | $0.015 | $0.003 |
| **Subtotal** | | | | **$0.04/day** |

*Amortized: $0.20/week ÷ 5 weekdays

### Marketing Strategist
| Activity | Model | Frequency | Est. Cost/Run | Daily Cost |
|----------|-------|-----------|---------------|------------|
| competitor_benchmark | Opus | 1x/week | $0.40 | $0.08* |
| Reflection + memory | Haiku | 1x/week | $0.015 | $0.003 |
| **Subtotal** | | | | **$0.08/day** |

### Head of Marketing (Manager)
| Activity | Model | Frequency | Est. Cost/Run | Daily Cost |
|----------|-------|-----------|---------------|------------|
| executive_summary | Sonnet | 1x/weekday | $0.12 | $0.12 |
| Delegation decision | Sonnet | 1x/weekday | $0.05 | $0.05 |
| Synthesis | Sonnet | 1x/weekday | $0.08 | $0.08 |
| Heartbeat (30 min, 7-6) | Sonnet | ~22x/weekday | $0.03 | $0.66 |
| Reflection + memory | Haiku | 1x/weekday | $0.01 | $0.01 |
| **Subtotal** | | | | **$0.92/day** |

Note: Sub-role costs (Media Buyer, Analyst, Strategist) are counted separately above. The manager's cost is just its own overhead.

### Head of IT (Manager)
| Activity | Model | Frequency | Est. Cost/Run | Daily Cost |
|----------|-------|-----------|---------------|------------|
| system_health_check | Sonnet | 1x/day | $0.10 | $0.10 |
| error_diagnosis | Sonnet | 1x/day | $0.10 | $0.10 |
| cost_monitoring | Haiku | 1x/day | $0.02 | $0.02 |
| Heartbeat (15 min, 24/7) | Sonnet | ~96x/day | $0.03 | $2.88 |
| Reflection + memory | Haiku | 1x/day | $0.01 | $0.01 |
| **Subtotal** | | | | **$3.11/day** |

Note: Head of IT heartbeat runs every 15 min 24/7. This is aggressive — most deployments would reduce to business hours only ($0.66/day instead of $2.88/day), bringing the total to ~$0.89/day.

### Skill Creator
| Activity | Model | Frequency | Est. Cost/Run | Daily Cost |
|----------|-------|-----------|---------------|------------|
| Heartbeat (every 4 hours) | Sonnet | ~6x/day | $0.03 | $0.18 |
| Conversation turns | Sonnet | On demand | $0.08/turn | — |
| **Subtotal** | | | | **$0.18/day** (+ conversation) |

### CEO (Manager)
| Activity | Model | Frequency | Est. Cost/Run | Daily Cost |
|----------|-------|-----------|---------------|------------|
| org_health_check | Opus | 1x/weekday | $0.40 | $0.40 |
| Delegation decision | Sonnet | 1x/weekday | $0.05 | $0.05 |
| Synthesis | Sonnet | 1x/weekday | $0.08 | $0.08 |
| Heartbeat (hourly, 7-8 PM) | Opus | ~13x/weekday | $0.10 | $1.30 |
| Reflection + memory | Haiku | 1x/weekday | $0.01 | $0.01 |
| **Subtotal** | | | | **$1.84/day** |

---

## Conversational Mode Costs

When someone @mentions a role in Slack:

| Per-turn cost | Model | Estimate |
|--------------|-------|----------|
| Conversation turn | Sonnet | $0.03 - $0.15 |
| Auto memory extraction | Haiku | $0.005 - $0.01 |
| **Per turn** | | **$0.04 - $0.16** |

Limits: 20 turns/thread, $5/thread cap, 24h timeout.

Estimated conversational usage: ~10 conversations/day x 5 turns avg x $0.08/turn = **$4.00/day**

---

## Infrastructure Costs

| Service | Provider | Est. Monthly Cost |
|---------|----------|------------------|
| PostgreSQL | Supabase (Pro) | $25 |
| Redis | Upstash (Pay-as-go) | $10 |
| Hosting | Railway (Pro) | $20 |
| Inngest | Free tier (25K events) | $0 |
| Ngrok (dev only) | Free tier | $0 |
| **Total Infrastructure** | | **$55/month** |

Self-hosted alternative: PostgreSQL + Redis on a $20/month VPS + Railway/Render for the app = similar cost.

---

## Annual Cost Summary

### Scenario A: Marketing Department Only (Current Example Setup)
Weekdays only (260 days/year), IT runs daily (365 days/year).

| Role | Daily Cost | Annual Cost |
|------|-----------|-------------|
| Media Buyer | $0.42 | $109 |
| Reporting Analyst | $0.04 | $10 |
| Strategist | $0.08 | $21 |
| Head of Marketing | $0.92 | $239 |
| Head of IT (business hours) | $0.89 | $325 |
| Skill Creator | $0.18 | $66 |
| CEO | $1.84 | $478 |
| Conversations (est.) | $4.00 | $1,040 |
| Weekly memory consolidation | $0.05/week | $3 |
| Infrastructure | — | $660 |
| **Total** | | **~$2,950/year** |

### Scenario B: Reduced Heartbeat Frequency
Same as A but with less aggressive heartbeats:
- Head of Marketing: hourly instead of every 30 min (→ $0.59/day)
- Head of IT: business hours only, every 30 min (→ $0.55/day)
- CEO: every 2 hours instead of hourly (→ $1.19/day)

| Annual Total | **~$2,050/year** |
|---|---|

### Scenario C: Scale to 5 Departments, 15 Roles
Estimated based on similar per-role costs:

| Component | Annual Cost |
|-----------|-------------|
| 15 worker roles (avg $0.20/day) | $780 |
| 5 manager roles (avg $0.60/day) | $780 |
| 1 CEO role | $478 |
| Head of IT | $325 |
| Conversations (est. 20/day) | $2,080 |
| Memory consolidation | $13 |
| Infrastructure (scaled) | $1,200 |
| **Total** | **~$5,700/year** |

### Scenario D: Hybrid Model Routing (External LLM for Structured Tasks)
Using a cheaper OpenAI-compatible provider (Groq, Together AI) for routing, extraction, and reflection tasks:

| Scenario | Without Hybrid | With Hybrid | Savings |
|----------|---------------|-------------|---------|
| A (Current) | ~$2,950 | ~$2,700 | ~$250 (8%) |
| C (15 Roles) | ~$5,700 | ~$4,850 | ~$850 (15%) |

Hybrid routing saves more at scale because structured tasks (routing, memory extraction, reflection) grow linearly with role count.

---

## Cost Comparison: AI Workforce vs Human Workforce

| | AI Workforce (Scenario A) | Human Equivalent |
|---|---|---|
| **Annual cost** | ~$2,950 | ~$350,000+ (media buyer + analyst + strategist salaries) |
| **Hours/day** | 24/7 monitoring | 8 hours |
| **Response time** | Minutes (webhook → investigation) | Hours to days |
| **Consistency** | Same quality every run | Varies by day, mood, workload |
| **Memory** | Perfect recall across all runs | Notes, tribal knowledge, turnover risk |

**Important caveat:** AI agents don't replace human judgment for strategic decisions, client relationships, or creative work. They replace the repetitive analytical grunt work (pull data, check for anomalies, generate reports, monitor budgets) so humans can focus on strategy and relationships.

---

## Cost Optimization Levers

1. **Heartbeat frequency** — Biggest cost driver. Reduce from every 15 min to every hour and costs drop significantly.
2. **Opus skip** — Strategic analysis (Opus) only runs on volatile days (>10% metric swings). Most days it's skipped.
3. **Hybrid routing** — Route structured tasks to cheaper providers. Kill switch defaults to OFF.
4. **Conversation limits** — $5/thread cap and 20-turn limit prevent runaway costs.
5. **Phase compression** — For large data sets, a Haiku compression step between data collection and analysis saves Sonnet input tokens.
6. **Skill model selection** — Not every skill needs Sonnet. Cost monitoring runs on Haiku ($0.02 vs $0.15).
7. **Extended thinking budget** — Default 10K tokens. Reduce for simpler skills, increase for complex strategy.
