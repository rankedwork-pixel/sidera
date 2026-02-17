"""Manually trigger a daily briefing workflow.

Usage: python -m scripts.trigger_briefing [--user-id USER] [--channel-id CHANNEL]

Sends an Inngest event to trigger the daily briefing workflow immediately.
Requires INNGEST_EVENT_KEY to be set (or INNGEST_DEV for local dev server).
"""

import argparse
import asyncio
import os
import sys

import httpx

from src.config import settings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INNGEST_CLOUD_URL = "https://inn.gs/e/{event_key}"
INNGEST_DEV_URL = "http://127.0.0.1:8288/e/{event_key}"
EVENT_NAME = "sidera/daily-briefing.trigger"


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


async def send_event(user_id: str, channel_id: str | None) -> dict:
    """Send the briefing trigger event to Inngest.

    Args:
        user_id: The user ID to run the briefing for.
        channel_id: Optional Slack channel to deliver the briefing to.

    Returns:
        Dict with the Inngest response.

    Raises:
        RuntimeError: If event key is missing or the request fails.
    """
    event_key = settings.inngest_event_key
    if not event_key:
        raise RuntimeError(
            "INNGEST_EVENT_KEY is not set. "
            "Add it to your .env file or set the environment variable."
        )

    # Use local dev server when INNGEST_DEV is set
    is_dev = os.environ.get("INNGEST_DEV", "").lower() in ("1", "true", "yes")
    base_url = INNGEST_DEV_URL if is_dev else INNGEST_CLOUD_URL
    url = base_url.format(event_key=event_key)

    event_data: dict = {"user_id": user_id}
    if channel_id:
        event_data["channel_id"] = channel_id

    payload = {
        "name": EVENT_NAME,
        "data": event_data,
    }

    print(f"Sending event to {'dev server' if is_dev else 'Inngest Cloud'}...")
    print(f"  Event: {EVENT_NAME}")
    print(f"  User:  {user_id}")
    if channel_id:
        print(f"  Channel: {channel_id}")

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, json=payload)

        if response.status_code >= 400:
            print(f"\nFailed! Status {response.status_code}: {response.text}", file=sys.stderr)
            sys.exit(1)

        result = response.json()
        print("\nSuccess! Event sent.")
        if isinstance(result, dict):
            event_ids = result.get("ids", [])
            if event_ids:
                print(f"  Event IDs: {', '.join(event_ids)}")
            status = result.get("status")
            if status:
                print(f"  Status: {status}")

        return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manually trigger a Sidera daily briefing workflow.",
    )
    parser.add_argument(
        "--user-id",
        default="default",
        help="User ID to run the briefing for (default: 'default')",
    )
    parser.add_argument(
        "--channel-id",
        default=None,
        help="Slack channel ID to deliver the briefing to (optional)",
    )
    return parser.parse_args()


async def main():
    args = parse_args()
    await send_event(user_id=args.user_id, channel_id=args.channel_id)


if __name__ == "__main__":
    asyncio.run(main())
