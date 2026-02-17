#!/usr/bin/env python3
"""
Creative Cuts — One-command runner.

Usage:
    python3 run.py                          # auto-picks latest CSV in data/
    python3 run.py path/to/new_export.csv   # uses a specific CSV

Workflow:
    1. Drop your new Tableau/Meta CSV export into the data/ folder
    2. Run: python3 run.py
    3. Outputs land in output/ with date-stamped filenames
"""

import os
import sys

from creative_cuts import main as run_analysis
from generate_cuts_doc import main as run_doc

# Ensure we're running from the project directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Pass through any CLI arg (csv path)
csv_arg = sys.argv[1] if len(sys.argv) > 1 else None


def main():
    """Run the full creative cuts pipeline."""
    print("=" * 60)
    print("  CREATIVE CUTS PIPELINE")
    print("=" * 60)
    print()

    # ─── Step 1: Run analysis & export CSV ──────────────────────────────
    print("STEP 1: Running analysis...\n")

    # Inject CLI arg into sys.argv so creative_cuts.main() picks it up
    original_argv = sys.argv[:]
    if csv_arg:
        sys.argv = ["creative_cuts.py", csv_arg]
    else:
        sys.argv = ["creative_cuts.py"]

    run_analysis()
    sys.argv = original_argv

    # ─── Step 2: Generate Word doc ──────────────────────────────────────
    print("\n" + "=" * 60)
    print("STEP 2: Generating Word doc...\n")

    # Same CLI passthrough
    if csv_arg:
        sys.argv = ["generate_cuts_doc.py", csv_arg]
    else:
        sys.argv = ["generate_cuts_doc.py"]

    run_doc()
    sys.argv = original_argv

    print("\n" + "=" * 60)
    print("  DONE — check the output/ folder")
    print("=" * 60)


if __name__ == "__main__":
    main()
