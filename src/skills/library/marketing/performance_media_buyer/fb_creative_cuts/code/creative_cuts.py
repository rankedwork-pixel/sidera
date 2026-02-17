#!/usr/bin/env python3
"""
Meta Creative Cuts Analysis — v2 (Calibrated)
Identifies underperforming Meta ads per the PRD logic, calibrated against
real human cut decisions from the 1/19 daily standup.

Ad Set = State + Gender + Language
Primary metric: On-Platform CPBC
Secondary metric: CPL (fallback)
Tertiary metric: Internal CPBC (validation only)

Calibration changes from v1:
  - Minimum spend threshold for zero-signal cuts ($50)
  - Cross-ad-set creative flagging (bad everywhere = global flag)
  - "WATCH" tier alongside "CUT" recommendations
  - Higher tolerance for decent on-platform CPBC even if CPL/internal are high
  - Skip tiny ad sets with insufficient signal (<$500 total spend)
"""

import csv
import glob as globmod
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

# ─── Configuration ───────────────────────────────────────────────────────────
MAX_CUTS_PER_AD_SET = 3
MAX_WATCH_PER_AD_SET = 3
MIN_SPEND_ZERO_SIGNAL = 50  # Ignore zero-signal ads below this spend
MIN_AD_SET_SPEND = 500  # Skip ad sets with less than this total spend
CPBC_CUT_THRESHOLD_PCT = 50  # % above ad set avg to recommend CUT
CPBC_WATCH_THRESHOLD_PCT = 20  # % above ad set avg to recommend WATCH
CROSS_AD_SET_BAD_THRESHOLD = 3  # Flag creative if bad in N+ ad sets
MIN_DAYS_IN_MARKET = 14  # PRD: ads must be 14+ days old to evaluate
CPL_SHIELD_PERCENTILE = 20  # Top N% CPL in ad set shields from CPBC-only cut

# Code lives in code/, data and output are at the skill root (parent dir)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

# Default input — will be overridden by CLI arg or find_latest_csv()
INPUT_FILE = os.path.join(DATA_DIR, "Creative - Sheet (2)_data.csv")


def find_latest_csv():
    """Find the most recently modified CSV in the data/ folder."""
    csvs = globmod.glob(os.path.join(DATA_DIR, "*.csv"))
    if not csvs:
        return INPUT_FILE  # fallback
    return max(csvs, key=os.path.getmtime)


def resolve_input_file(cli_arg=None):
    """Determine which CSV to use: CLI arg > latest in data/ > default."""
    if cli_arg and os.path.isfile(cli_arg):
        return cli_arg
    return find_latest_csv()


def dated_output_path(prefix, ext, records):
    """Build an output filename like 'prefix_2026-01-19_to_2026-02-16.ext'
    using the min/max reporting dates found in the data."""
    starts = [r["reporting_starts"] for r in records if r.get("reporting_starts")]
    ends = [r["reporting_ends"] for r in records if r.get("reporting_ends")]
    if starts and ends:
        d1 = min(starts).strftime("%Y-%m-%d")
        d2 = max(ends).strftime("%Y-%m-%d")
        fname = f"{prefix}_{d1}_to_{d2}{ext}"
    else:
        fname = f"{prefix}_{datetime.now().strftime('%Y-%m-%d')}{ext}"
    return os.path.join(OUTPUT_DIR, fname)


# ─── Load & Pivot ────────────────────────────────────────────────────────────


def load_data(filepath):
    """Load UTF-16 tab-delimited CSV and pivot to wide format.
    Captures Reporting Starts/Ends to compute days in market."""
    ads = defaultdict(dict)
    dates = {}  # key -> (reporting_starts, reporting_ends)

    with open(filepath, encoding="utf-16") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            key = (row["Special State"], row["Gender"], row["Copy Language"], row["Creative (new)"])
            measure = row["Measure Names"]
            value = float(row["Measure Values"]) if row["Measure Values"] else 0.0
            ads[key][measure] = value

            # Capture dates (same for all measures of same ad)
            if key not in dates:
                try:
                    starts = datetime.strptime(row["Reporting Starts"], "%m/%d/%Y")
                    ends = datetime.strptime(row["Reporting Ends"], "%m/%d/%Y")
                    dates[key] = (starts, ends)
                except (ValueError, KeyError):
                    pass

    # Determine the latest reporting_ends in the dataset — ads ending before
    # this date were already paused/cut and shouldn't get new recommendations.
    all_end_dates = [d[1] for d in dates.values() if d[1] is not None]
    max_end_date = max(all_end_dates) if all_end_dates else None

    records = []
    for (state, gender, lang, creative), metrics in ads.items():
        starts, ends = dates.get((state, gender, lang, creative), (None, None))
        days_in_market = (ends - starts).days if starts and ends else 0
        already_paused = ends is not None and max_end_date is not None and ends < max_end_date

        records.append(
            {
                "state": state,
                "gender": gender,
                "language": lang,
                "creative": creative,
                "spend": metrics.get("Internal FB Spend", 0),
                "on_platform_cpbc": metrics.get("On Platform CPBC", 0),
                "on_platform_consults": metrics.get("New On Platform Booked Consults", 0),
                "internal_cpbc": metrics.get("Internal CPBC", 0),
                "internal_consults": metrics.get("Internal Booked Consults", 0),
                "cpl": metrics.get("Cost per Lead (Aggregate)", 0),
                "leads": metrics.get("Leads (Internal)", 0),
                "days_in_market": days_in_market,
                "reporting_starts": starts,
                "reporting_ends": ends,
                "already_paused": already_paused,
            }
        )
    return records


# ─── Eligibility ─────────────────────────────────────────────────────────────


def classify_ad(ad):
    """
    Classify an ad:
      'already_paused' — Reporting ends before the latest date in the dataset
                         (ad was already cut/paused, no action needed)
      'zero_signal'    — Spend > 0, zero leads, zero on-platform consults
      'performance'    — Spend > 0, has CPBC or CPL data
      'too_new'        — Less than 14 days in market, not enough data
      'ineligible'     — doesn't meet any gate
    """
    # Already paused ads: reporting_ends < max date means it was already cut
    if ad.get("already_paused"):
        return "already_paused"

    spend = ad["spend"]
    leads = ad["leads"]
    consults = ad["on_platform_consults"]
    cpbc = ad["on_platform_cpbc"]
    cpl = ad["cpl"]
    days = ad.get("days_in_market", 0)

    if spend <= 0:
        return "ineligible"

    # PRD: ads must be 14+ days old to evaluate
    if days < MIN_DAYS_IN_MARKET:
        return "too_new"

    if leads == 0 and consults == 0:
        return "zero_signal"

    if cpbc > 0 or cpl > 0:
        return "performance"

    return "ineligible"


# ─── Cross-Ad-Set Creative Analysis ─────────────────────────────────────────


def analyze_creatives_globally(records):
    """
    Identify creatives that are consistently bad across multiple ad sets.
    Returns dict of creative -> list of ad sets where it's underperforming.
    """
    # Group by creative
    by_creative = defaultdict(list)
    for ad in records:
        by_creative[ad["creative"]].append(ad)

    global_flags = {}
    for creative, ads in by_creative.items():
        bad_in = []
        for ad in ads:
            cls = classify_ad(ad)
            if cls == "already_paused":
                continue  # Don't count already-paused ads toward global flags
            if cls == "zero_signal" and ad["spend"] >= MIN_SPEND_ZERO_SIGNAL:
                bad_in.append(f"{ad['state']}/{ad['gender']}/{ad['language']}")
            elif cls == "performance" and ad["on_platform_cpbc"] == 0 and ad["spend"] > 100:
                # Has leads but no consults, meaningful spend
                bad_in.append(f"{ad['state']}/{ad['gender']}/{ad['language']}")

        if len(bad_in) >= CROSS_AD_SET_BAD_THRESHOLD:
            global_flags[creative] = bad_in

    return global_flags


# ─── Waste Score ─────────────────────────────────────────────────────────────


def _waste_score(ad, avg_cpbc, avg_cpl, global_flags):
    """
    Unified waste score that interleaves zero-signal and poor-performers
    by actual dollar impact rather than category.

    Score components:
      - Base: spend (higher spend = more waste)
      - Multiplier for how bad it is:
          * Zero-signal: 2x (spending with literally nothing to show)
          * No consults but has leads: 1.5x
          * High CPBC: 1.0 + (distance_pct / 100) so 50% above avg = 1.5x
      - Global flag bonus: +50%
    """
    spend = ad["spend"]
    cls = ad.get("classification", "")

    if cls == "zero_signal":
        multiplier = 2.0
    elif ad["on_platform_consults"] == 0 and ad["leads"] > 0:
        # Has leads but zero consults — bad funnel
        multiplier = 1.5
    elif ad.get("distance_pct") is not None and ad["distance_pct"] > 0:
        # Above average CPBC — scale by how far above
        multiplier = 1.0 + (ad["distance_pct"] / 100)
    else:
        multiplier = 0.5  # Below average, not really waste

    score = spend * multiplier

    # Global flag boost
    if ad["creative"] in global_flags:
        score *= 1.5

    return score


# ─── Ranking (Calibrated v3) ────────────────────────────────────────────────


def rank_ad_set(ads, global_flags):
    """
    Rank ads within an ad set using unified waste scoring.
    High-spend poor performers rank alongside (and can outrank) zero-signal ads.
    Returns:
      - cuts: list of ads to CUT (max 3)
      - watches: list of ads to WATCH (max 3)
      - avg_cpbc, avg_cpl
    """
    zero_signal = []
    performance = []

    for ad in ads:
        classification = classify_ad(ad)
        ad["classification"] = classification
        if classification == "zero_signal":
            zero_signal.append(ad)
        elif classification == "performance":
            performance.append(ad)

    # Averages for performance ads
    cpbc_values = [a["on_platform_cpbc"] for a in ads if a["on_platform_cpbc"] > 0]
    avg_cpbc = sum(cpbc_values) / len(cpbc_values) if cpbc_values else 0

    cpl_values = [a["cpl"] for a in ads if a["cpl"] > 0]
    avg_cpl = sum(cpl_values) / len(cpl_values) if cpl_values else 0

    # Compute distance for performance ads
    for ad in performance:
        if ad["on_platform_cpbc"] > 0 and avg_cpbc > 0:
            ad["distance_pct"] = ((ad["on_platform_cpbc"] - avg_cpbc) / avg_cpbc) * 100
            ad["distance_metric"] = "CPBC"
        elif ad["cpl"] > 0 and avg_cpl > 0:
            ad["distance_pct"] = ((ad["cpl"] - avg_cpl) / avg_cpl) * 100
            ad["distance_metric"] = "CPL"
        else:
            ad["distance_pct"] = 0
            ad["distance_metric"] = "N/A"

    # For zero-signal, set distance fields
    for ad in zero_signal:
        ad["distance_pct"] = None
        ad["distance_metric"] = "N/A"

    # ─── Build unified candidate pool ────────────────────────────────────────
    # CUT candidates: zero-signal with meaningful spend OR performance above threshold
    cut_candidates = []

    for ad in zero_signal:
        if ad["spend"] >= MIN_SPEND_ZERO_SIGNAL:
            ad["reason"] = "ZERO_SIGNAL"
            if ad["creative"] in global_flags:
                ad["reason"] = "ZERO_SIGNAL (GLOBAL)"
            cut_candidates.append(ad)

    for ad in performance:
        has_consults = ad["on_platform_consults"] > 0

        if not has_consults and ad["spend"] > 100:
            ad["reason"] = "NO_CONSULTS"
            if ad["creative"] in global_flags:
                ad["reason"] = "NO_CONSULTS (GLOBAL)"
            cut_candidates.append(ad)
        elif ad["on_platform_cpbc"] > 0 and ad["distance_pct"] >= CPBC_CUT_THRESHOLD_PCT:
            ad["reason"] = "HIGH_CPBC"
            if ad["creative"] in global_flags:
                ad["reason"] = "HIGH_CPBC (GLOBAL)"
            cut_candidates.append(ad)

    # Sort by waste score (highest waste first) — this interleaves all types
    cut_candidates.sort(key=lambda a: -_waste_score(a, avg_cpbc, avg_cpl, global_flags))

    # ─── CPL Shield ────────────────────────────────────────────────────────
    # If an ad's CPBC is high but its CPL is in the top N% of the ad set,
    # the strong lead generation offsets the CPBC concern.
    # Downgrade from CUT → WATCH with reasoning.
    cpl_shield_cutoff = None
    if cpl_values:
        sorted_cpls = sorted(cpl_values)
        idx = max(0, int(len(sorted_cpls) * CPL_SHIELD_PERCENTILE / 100) - 1)
        cpl_shield_cutoff = sorted_cpls[idx]

    shielded = []
    remaining_cuts = []
    for ad in cut_candidates:
        if (
            ad.get("reason", "").startswith("HIGH_CPBC")
            and cpl_shield_cutoff is not None
            and ad["cpl"] > 0
            and ad["cpl"] <= cpl_shield_cutoff
        ):
            ad["reason"] = "CPL_SHIELDED"
            ad["cpl_shield_note"] = (
                f"CPBC is elevated but CPL ${ad['cpl']:,.0f} is top {CPL_SHIELD_PERCENTILE}% "
                f"in ad set (cutoff ${cpl_shield_cutoff:,.0f}) — worth keeping?"
            )
            shielded.append(ad)
        else:
            remaining_cuts.append(ad)

    # Take top 3 from non-shielded, then shielded go to watch
    cuts = remaining_cuts[:MAX_CUTS_PER_AD_SET]

    # ─── Build WATCH list ────────────────────────────────────────────────────
    watch_candidates = []

    # Shielded ads go to watch first (these are close calls)
    for ad in shielded:
        watch_candidates.append(ad)

    # Low-spend zero-signal
    for ad in zero_signal:
        if ad["spend"] < MIN_SPEND_ZERO_SIGNAL and ad["spend"] > 0:
            ad["reason"] = "ZERO_SIGNAL_LOW_SPEND"
            watch_candidates.append(ad)

    # Performance ads that are elevated but not cut-worthy
    for ad in performance:
        if ad in cuts or ad in watch_candidates:
            continue
        has_consults = ad["on_platform_consults"] > 0

        if ad["on_platform_cpbc"] > 0 and ad["distance_pct"] >= CPBC_WATCH_THRESHOLD_PCT:
            ad["reason"] = "ELEVATED_CPBC"
            watch_candidates.append(ad)
        elif not has_consults and ad["spend"] > 0 and ad not in cut_candidates:
            ad["reason"] = "NO_CONSULTS_LOW_SPEND"
            watch_candidates.append(ad)

    # Overflow cut candidates that didn't make top 3
    for ad in cut_candidates:
        if ad not in cuts and ad not in watch_candidates:
            ad["reason"] = "OVERFLOW_CUT"
            watch_candidates.append(ad)

    # Sort watches by waste score too
    watch_candidates.sort(key=lambda a: -_waste_score(a, avg_cpbc, avg_cpl, global_flags))
    watches = watch_candidates[:MAX_WATCH_PER_AD_SET]

    return cuts, watches, avg_cpbc, avg_cpl


# ─── Main ────────────────────────────────────────────────────────────────────


def main():
    # ─── Resolve input file ───────────────────────────────────────────────────
    cli_arg = sys.argv[1] if len(sys.argv) > 1 else None
    input_file = resolve_input_file(cli_arg)

    print("=" * 110)
    print("META CREATIVE CUTS v2 — CALIBRATED ANALYSIS")
    print("=" * 110)
    print()
    print(f"Input:  {input_file}")
    print(
        f"Config: min_spend_zero_signal=${MIN_SPEND_ZERO_SIGNAL} | "
        f"min_ad_set_spend=${MIN_AD_SET_SPEND} | "
        f"cut_threshold={CPBC_CUT_THRESHOLD_PCT}% | "
        f"watch_threshold={CPBC_WATCH_THRESHOLD_PCT}%"
    )
    print()

    records = load_data(input_file)
    print(f"Loaded {len(records)} ad instances")

    # Cross-ad-set analysis
    global_flags = analyze_creatives_globally(records)
    if global_flags:
        print(f"\nGLOBALLY FLAGGED CREATIVES (bad in {CROSS_AD_SET_BAD_THRESHOLD}+ ad sets):")
        for creative, ad_sets in sorted(global_flags.items(), key=lambda x: -len(x[1])):
            print(f"  {creative} — flagged in {len(ad_sets)} ad sets: {', '.join(ad_sets)}")
        print()

    # Group by ad set
    ad_set_groups = defaultdict(list)
    for ad in records:
        ad_set_groups[(ad["state"], ad["gender"], ad["language"])].append(ad)

    print(f"Identified {len(ad_set_groups)} ad sets (State / Gender / Language)")
    print()

    all_cuts = []
    all_watches = []
    skipped_ad_sets = []
    total_zero_signal = 0
    total_already_paused = 0

    for ad_set_key in sorted(ad_set_groups.keys()):
        ads = ad_set_groups[ad_set_key]
        state, gender, lang = ad_set_key
        total_spend = sum(a["spend"] for a in ads)

        # Skip tiny ad sets
        if total_spend < MIN_AD_SET_SPEND:
            skipped_ad_sets.append((ad_set_key, total_spend, len(ads)))
            continue

        cuts, watches, avg_cpbc, avg_cpl = rank_ad_set(ads, global_flags)

        n_eligible = sum(1 for a in ads if classify_ad(a) in ("zero_signal", "performance"))
        n_zero = sum(1 for a in ads if classify_ad(a) == "zero_signal")
        n_too_new = sum(1 for a in ads if classify_ad(a) == "too_new")
        n_paused = sum(1 for a in ads if classify_ad(a) == "already_paused")
        total_zero_signal += n_zero
        total_already_paused += n_paused

        print("-" * 110)
        print(f"AD SET: {state} / {gender} / {lang}")
        print(
            f"  Ads: {len(ads)} total | {n_eligible} eligible | {n_zero} zero-signal"
            f" | {n_too_new} too new (<{MIN_DAYS_IN_MARKET}d) | {n_paused} already paused"
        )
        print(
            f"  Total spend: ${total_spend:,.2f} | Avg On-Platform CPBC: ${avg_cpbc:,.2f}"
            f" | Avg CPL: ${avg_cpl:,.2f}"
        )

        if cuts:
            print(f"\n  CUT ({len(cuts)}):")
            for i, c in enumerate(cuts, 1):
                _print_ad(i, c)
                c["ad_set"] = f"{state}/{gender}/{lang}"
                c["avg_cpbc"] = avg_cpbc
                c["avg_cpl"] = avg_cpl
                c["tier"] = "CUT"
                all_cuts.append(c)

        if watches:
            print(f"\n  WATCH ({len(watches)}):")
            for i, c in enumerate(watches, 1):
                _print_ad(i, c)
                c["ad_set"] = f"{state}/{gender}/{lang}"
                c["avg_cpbc"] = avg_cpbc
                c["avg_cpl"] = avg_cpl
                c["tier"] = "WATCH"
                all_watches.append(c)

        if not cuts and not watches:
            print("\n  N/C (no changes recommended)")

        print()

    # ─── Skipped Ad Sets ─────────────────────────────────────────────────────
    if skipped_ad_sets:
        print("-" * 110)
        print(f"SKIPPED AD SETS (total spend < ${MIN_AD_SET_SPEND}):")
        for (state, gender, lang), spend, count in skipped_ad_sets:
            print(f"  {state}/{gender}/{lang} — {count} ads, ${spend:,.2f} total spend")
        print()

    # ─── Summary ─────────────────────────────────────────────────────────────
    print("=" * 110)
    print("SUMMARY")
    print("=" * 110)
    print(f"  Total ad instances analyzed:     {len(records)}")
    print(f"  Total already paused (excluded): {total_already_paused}")
    print(f"  Total ad sets:                   {len(ad_set_groups)}")
    print(f"  Ad sets analyzed:                {len(ad_set_groups) - len(skipped_ad_sets)}")
    print(f"  Ad sets skipped (low spend):     {len(skipped_ad_sets)}")
    print(f"  Total zero-signal ads:           {total_zero_signal}")
    print()
    print(f"  Ads recommended for CUT:         {len(all_cuts)}")
    print(f"  Spend on CUT candidates:         ${sum(c['spend'] for c in all_cuts):,.2f}")
    print(f"  Ads recommended for WATCH:       {len(all_watches)}")
    print(f"  Spend on WATCH candidates:       ${sum(c['spend'] for c in all_watches):,.2f}")
    print()

    # Breakdown by reason
    print("  CUT breakdown:")
    for reason, count in Counter(c["reason"] for c in all_cuts).most_common():
        spend = sum(c["spend"] for c in all_cuts if c["reason"] == reason)
        print(f"    {reason}: {count} ads (${spend:,.2f})")

    print("\n  WATCH breakdown:")
    for reason, count in Counter(c["reason"] for c in all_watches).most_common():
        spend = sum(c["spend"] for c in all_watches if c["reason"] == reason)
        print(f"    {reason}: {count} ads (${spend:,.2f})")
    print()

    # ─── Export CSV ──────────────────────────────────────────────────────────
    output_csv = dated_output_path("cut_recommendations", ".csv", records)
    all_recs = all_cuts + all_watches
    with open(output_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "Tier",
                "Ad Set",
                "Creative",
                "Reason",
                "Spend",
                "On-Platform CPBC",
                "CPL",
                "Internal CPBC",
                "On-Platform Consults",
                "Leads",
                "Days in Market",
                "Distance from Avg (%)",
                "Ad Set Avg CPBC",
                "Ad Set Avg CPL",
                "Globally Flagged",
            ]
        )
        for c in all_recs:
            writer.writerow(
                [
                    c.get("tier", ""),
                    c.get("ad_set", ""),
                    c["creative"],
                    c["reason"],
                    f"{c['spend']:.2f}",
                    f"{c['on_platform_cpbc']:.2f}" if c["on_platform_cpbc"] > 0 else "",
                    f"{c['cpl']:.2f}" if c["cpl"] > 0 else "",
                    f"{c['internal_cpbc']:.2f}" if c["internal_cpbc"] > 0 else "",
                    f"{c['on_platform_consults']:.0f}",
                    f"{c['leads']:.0f}",
                    f"{c.get('days_in_market', '')}",
                    f"{c['distance_pct']:.1f}" if c.get("distance_pct") is not None else "",
                    f"{c.get('avg_cpbc', 0):.2f}",
                    f"{c.get('avg_cpl', 0):.2f}",
                    "YES" if c["creative"] in global_flags else "",
                ]
            )

    print(f"  Exported {len(all_recs)} recommendations to: {output_csv}")
    print()


def _print_ad(i, c):
    """Print a single ad recommendation line."""
    dist_str = f"{c['distance_pct']:+.1f}%" if c.get("distance_pct") is not None else "—"
    cpbc_str = f"${c['on_platform_cpbc']:,.2f}" if c["on_platform_cpbc"] > 0 else "—"
    cpl_str = f"${c['cpl']:,.2f}" if c["cpl"] > 0 else "—"
    int_cpbc_str = f"${c['internal_cpbc']:,.2f}" if c["internal_cpbc"] > 0 else "—"
    global_tag = " ***GLOBAL***" if c.get("reason", "").endswith("(GLOBAL)") else ""

    print(f"     {i}. {c['creative']}{global_tag}")
    print(
        f"        {c['reason']}  |  Spend: ${c['spend']:,.2f}  |  "
        f"CPBC: {cpbc_str}  |  CPL: {cpl_str}  |  Int.CPBC: {int_cpbc_str}"
    )
    print(
        f"        Consults: {c['on_platform_consults']:.0f}  |  "
        f"Leads: {c['leads']:.0f}  |  Dist: {dist_str}"
    )


if __name__ == "__main__":
    main()
