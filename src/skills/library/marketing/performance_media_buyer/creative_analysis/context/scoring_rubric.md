# Creative Scoring Rubric

## Performance Tiers

Use backend-attributed ROAS (not platform-reported) for all tier
assignments. Platform ROAS is typically inflated 1.2-1.8x depending
on attribution window and conversion type.

### Scale Tier (Top 20%)
- Backend ROAS >= 2x the account's break-even ROAS
- Frequency < 3.0 (still has headroom)
- CTR stable or increasing over last 7 days
- Minimum 14 days of data (7 days for DTC, 14 days for B2B/high-ticket)
- **Action:** Increase budget 10-20%. Never more than 20% in a single change.

### Maintain Tier (Middle 60%)
- Backend ROAS between break-even and 2x break-even
- No clear fatigue signals
- **Action:** Keep running, review in 7 days.

### Cut Tier (Bottom 20%)
- Backend ROAS below break-even after sufficient data window
- OR: frequency > 4.0 AND CTR declining for 5+ consecutive days
- OR: $0 backend conversions after 2,000+ impressions (7+ days)
- **Action:** Pause. Calculate weekly savings in the report.

### Probation (Insufficient Data)
- Less than 1,000 impressions (DTC) or 5,000 impressions (B2B)
- Less than 7 days since launch
- **Action:** Do NOT cut. Reduce budget 50% if concerning early signals.
  Re-evaluate in 7 days.

## Format-Specific Adjustments

### Video Creatives
- Evaluate thumb-stop rate: 3-second views / impressions. Benchmark: >25%
- Evaluate hook rate: views past 25% / 3-second views. Benchmark: >30%
- Low thumb-stop (<15%) = weak opening frame. Recommend new hook.
- Good thumb-stop but low hook = creative loses viewer mid-way. Trim.
- Extend evaluation window to 14 days (viewers may convert later).

### Carousel Creatives
- Evaluate both the carousel aggregate AND individual card performance
- If card 1 has high CTR but card 3+ has near-zero, the creative
  structure is failing. Recommend reordering or trimming cards.
- Carousel with >5 cards: check if engagement drops off sharply. If
  cards 4+ get <10% of card 1 impressions, shorten the carousel.

### Static Image Creatives
- Fastest to evaluate — 7 days is sufficient for most DTC accounts
- Primary metric: CTR + backend CPA. ROAS is secondary for statics
  since they often drive assist conversions rather than last-click.
- Compare headline variants separately from image variants to isolate
  which element is driving performance differences.

## Fatigue Detection Rules

A creative is fatigued when ALL of these are true:
1. Frequency > 3.0 (the same user is seeing it >3 times)
2. CTR has declined for 5+ consecutive days
3. The decline is meaningful (>0.1 percentage points total)

Early fatigue warning (take action within 3 days):
1. Frequency > 2.5 AND CTR down 3 of last 5 days
2. OR: Frequency growth rate > 0.3/day (will hit 3.0 within a week)

## Concentration Risk

Flag when any single creative accounts for:
- >40% of total account spend (WARNING)
- >60% of total account spend (CRITICAL — diversify immediately)

The fix is NOT to cut the winning creative, but to scale up
alternatives or launch new tests to rebalance the portfolio.
