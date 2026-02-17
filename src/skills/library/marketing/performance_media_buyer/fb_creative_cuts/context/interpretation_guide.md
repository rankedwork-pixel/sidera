# FB Creative Cuts — Interpretation Guide

## Understanding the Output

The analysis produces two files:

### 1. CSV Scorecard (`output/creative_cuts_YYYYMMDD.csv`)

Columns:
- **ad_set**: The targeting combination (State + Gender + Language)
- **creative_name**: The ad creative identifier
- **recommendation**: One of CUT, WATCH, N (no action), C (already paused)
- **on_platform_cpbc**: Cost per backend conversion (primary metric)
- **cpl**: Cost per lead (secondary/fallback metric)
- **spend**: Total spend for this creative in this ad set
- **days_in_market**: How long the creative has been running
- **waste_score**: Calculated waste (spend above efficient frontier)
- **cross_ad_set_flag**: True if this creative is bad in 3+ ad sets

### 2. Word Document (`output/creative_cuts_YYYYMMDD.docx`)

Formatted to match the daily standup notes style:
- Lists CUT and WATCH recommendations by ad set
- Uses informal language matching team communication style
- Max 3 CUT + 2 WATCH names shown per ad set

## Key Metrics

- **CPBC** (Cost Per Backend Conversion): The primary metric. "Backend" means
  the conversion data comes from the advertiser's own system, not Meta's
  attribution. This is the source of truth.
- **CPL** (Cost Per Lead): Secondary metric. Used as fallback when CPBC data
  is insufficient.
- **CPL Shield**: Ads in the top 20% of CPL performance in their ad set are
  protected from CPBC-only cuts. Rationale: they're generating leads even if
  backend conversion tracking shows poor CPBC.

## What Each Recommendation Means

- **CUT**: Pause immediately. This creative's CPBC is 50%+ above its ad set
  average AND it's not protected by strong CPL performance.
- **WATCH**: Monitor closely. CPBC is 20-50% above ad set average. May
  need to be cut next week if it doesn't improve.
- **N**: No action needed. Creative is performing at or above ad set average.
- **C**: Already paused (classified as inactive in the data).

## Cross-Ad-Set Patterns

When a creative appears in the CUT list for 3+ different ad sets, it's
flagged as a "global bad creative." This is high-signal — the creative is
underperforming regardless of targeting. These should be the first cuts.
