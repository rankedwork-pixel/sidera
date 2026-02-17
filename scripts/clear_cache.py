"""Clear Redis cache entries.

Usage:
  python -m scripts.clear_cache                    # Clear all Sidera cache
  python -m scripts.clear_cache --pattern "google*" # Clear Google Ads cache only
  python -m scripts.clear_cache --pattern "meta*"   # Clear Meta cache only
  python -m scripts.clear_cache --pattern "oauth*"  # Clear OAuth states
  python -m scripts.clear_cache --force             # Skip confirmation prompt
"""

import argparse
import asyncio
import sys

from src.cache.service import cache_delete_pattern

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

SIDERA_PREFIX = "sidera:"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clear Sidera Redis cache entries.",
    )
    parser.add_argument(
        "--pattern",
        default=None,
        help=(
            "Glob pattern to match cache keys (e.g. 'google*', 'meta*', 'oauth*'). "
            "Defaults to all Sidera keys."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    args = parse_args()

    # Build the full Redis key pattern
    if args.pattern:
        pattern = f"{SIDERA_PREFIX}*{args.pattern}*"
    else:
        pattern = f"{SIDERA_PREFIX}*"

    print(f"Cache pattern: {pattern}")

    # Confirm unless --force
    if not args.force:
        confirm = input("Delete matching cache keys? [y/N] ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Cancelled.")
            sys.exit(0)

    deleted = await cache_delete_pattern(pattern)
    print(f"Deleted {deleted} cache key(s).")


if __name__ == "__main__":
    asyncio.run(main())
