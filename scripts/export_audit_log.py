"""Export audit log entries to CSV.

Usage:
  python -m scripts.export_audit_log                          # Last 7 days
  python -m scripts.export_audit_log --days 30                # Last 30 days
  python -m scripts.export_audit_log --user-id "user123"      # Specific user
  python -m scripts.export_audit_log --output audit_export.csv
"""

import argparse
import asyncio
import csv
import json
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.db.session import get_db_session
from src.models.schema import AuditLog

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Sidera audit log entries to CSV.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to export (default: 7)",
    )
    parser.add_argument(
        "--user-id",
        default=None,
        help="Filter to a specific user ID (default: all users)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV file path (default: stdout)",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


async def export_audit_log(
    days: int,
    user_id: str | None,
    output_path: str | None,
) -> None:
    """Query audit log and write to CSV."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    async with get_db_session() as session:
        stmt = select(AuditLog).where(AuditLog.created_at >= cutoff)

        if user_id:
            stmt = stmt.where(AuditLog.user_id == user_id)

        stmt = stmt.order_by(AuditLog.created_at.asc())
        result = await session.execute(stmt)
        entries = list(result.scalars().all())

    if not entries:
        print(f"No audit log entries found for the last {days} day(s).", file=sys.stderr)
        return

    # Determine output target
    if output_path:
        out_file = open(output_path, "w", newline="")  # noqa: SIM115
    else:
        out_file = sys.stdout

    try:
        fieldnames = ["timestamp", "user_id", "event_type", "source", "data"]
        writer = csv.DictWriter(out_file, fieldnames=fieldnames)
        writer.writeheader()

        for entry in entries:
            writer.writerow({
                "timestamp": entry.created_at.isoformat() if entry.created_at else "",
                "user_id": entry.user_id or "",
                "event_type": entry.event_type or "",
                "source": entry.source or "",
                "data": json.dumps(entry.event_data, default=str) if entry.event_data else "",
            })
    finally:
        if output_path and out_file is not sys.stdout:
            out_file.close()

    # Print summary to stderr so it doesn't pollute stdout CSV
    date_min = entries[0].created_at
    date_max = entries[-1].created_at
    summary_target = sys.stderr if output_path is None else sys.stdout
    print(
        f"\nExported {len(entries)} audit log entries "
        f"({date_min.strftime('%Y-%m-%d')} to {date_max.strftime('%Y-%m-%d')})",
        file=summary_target,
    )
    if output_path:
        print(f"Written to: {output_path}", file=summary_target)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    args = parse_args()
    await export_audit_log(
        days=args.days,
        user_id=args.user_id,
        output_path=args.output,
    )


if __name__ == "__main__":
    asyncio.run(main())
