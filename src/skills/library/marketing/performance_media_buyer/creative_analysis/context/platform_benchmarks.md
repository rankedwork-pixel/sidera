# Platform Benchmarks & Attribution Notes

## Meta Attribution Inflation

Platform-reported metrics are typically inflated relative to backend
(BigQuery) attributed conversions. Use these ranges as a sanity check:

| Metric | Typical Inflation | Notes |
|--------|------------------|-------|
| Conversions (DTC, 7-day click) | 1.2-1.5x | View-through inflates most |
| Conversions (DTC, 1-day click) | 1.1-1.3x | More conservative window |
| Conversions (B2B lead gen) | 1.3-1.8x | Many leads never qualify |
| ROAS (DTC) | 1.2-1.5x | Mirrors conversion inflation |
| ROAS (B2B) | 1.4-2.0x | Revenue attribution is loose |

**Always use backend data as ground truth.** Platform data is useful
for relative comparisons between creatives (same bias applies to all),
but absolute performance must come from BigQuery.

## Typical Performance Ranges by Vertical

### DTC E-Commerce
- CTR: 1.0-3.0% (above 2% is good)
- Backend CPA: varies wildly by AOV. $15-40 for low-AOV, $50-150 for high-AOV
- Backend ROAS: 2.0-6.0x target range (below 2.0x = losing money after COGS)
- Frequency sweet spot: 1.5-2.5. Above 3.0 = fatigue risk.
- Video thumb-stop rate: 25-40% is healthy

### B2B SaaS / Lead Gen
- CTR: 0.5-1.5% (lower than DTC, audiences are smaller)
- Cost per lead: $20-80 (but lead-to-qualified rate matters more)
- Backend CPA (qualified): $60-200 depending on deal size
- Frequency sweet spot: 1.0-2.0. B2B audiences are smaller so frequency
  climbs faster. Above 2.5 = fatigue risk.

### App Install
- CTR: 1.5-4.0%
- Cost per install: $1-5 (casual), $5-20 (utility), $20-80 (fintech)
- Day-7 retention rate matters more than install volume
- Creative fatigue happens fastest in app install — refresh every 2 weeks

## Cross-Creative Comparison Rules

When comparing creatives against each other:
1. Compare within the same campaign/ad set (same audience, same bid)
2. Normalize by impressions, not spend (Meta auto-allocates budget
   toward winners, which means winners get more spend by default)
3. Use backend CPA as the primary sort metric, not CTR or ROAS alone
4. Account for launch date — a 3-day-old creative vs a 21-day creative
   is NOT a fair comparison. Segment by cohort.
