# Cross-Department Analysis Guidelines

## Department Overview

### Marketing Department
- **Head:** Head of Marketing (manages: media buyer, reporting analyst, strategist)
- **Primary connectors:** Google Ads, Meta, BigQuery
- **Key metrics:** ROAS, CPA, MER, daily spend, campaign status
- **Schedule:** Daily briefing at 9 AM, heartbeat every 30 min during business hours
- **Common issues:** Platform disconnections, attribution discrepancies, budget pacing

### IT & Operations Department
- **Head:** Head of IT (no sub-roles currently)
- **Primary tools:** System health, failed runs, cost monitoring
- **Key metrics:** Uptime, DLQ size, LLM cost, error rate
- **Schedule:** Daily briefing at 6 AM, heartbeat every 15 min 24/7
- **Common issues:** Redis connectivity, DB connection pools, API rate limits

## Cross-Department Interaction Patterns

### IT issues affecting Marketing
- Redis down → cached API responses unavailable → slower tool calls → higher costs
- DB connection pool exhausted → approval queue inaccessible → marketing actions blocked
- Token refresh failure → platform connector dies → marketing data goes stale

### Marketing issues affecting IT
- Large data pulls → BigQuery cost spikes → appears as IT cost anomaly
- Too many concurrent conversations → DB connection pool pressure
- Aggressive heartbeat schedules → high LLM cost → triggers IT cost alerts

## Synthesis Framework

When combining department head reports, ask:

1. **Consistency check:** Do both departments agree on system health?
   - If IT says "all clear" but Marketing reports data issues → connector problem
   - If Marketing says "campaigns healthy" but IT sees errors → surface-level vs deep health

2. **Causal chain:** Can an IT issue explain a Marketing anomaly (or vice versa)?
   - Always trace the dependency chain before treating issues as independent

3. **Priority ranking:** When multiple issues compete for attention:
   - Revenue impact > operational efficiency > cost optimization
   - Active data loss > potential data loss > no data impact
   - Cross-department impact > single-department impact

4. **Resource allocation:** Should the same human fix both, or do they need different stewards?
