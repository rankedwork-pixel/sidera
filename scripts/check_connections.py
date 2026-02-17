"""Check connectivity to all external services.

Usage: python -m scripts.check_connections

Tests: Database, Redis, Slack, Google Ads (auth only), Meta (auth only), Inngest.
Exits 0 if all critical services pass, 1 if any critical service fails.
"""

import asyncio
import sys

import httpx
from sqlalchemy import text

from src.config import settings

# ---------------------------------------------------------------------------
# Check functions — each returns (ok: bool, detail: str)
# ---------------------------------------------------------------------------


async def check_database() -> tuple[bool, str]:
    """Verify database connectivity by running SELECT 1."""
    if not settings.database_url:
        return False, "DATABASE_URL not configured"

    try:
        from src.db.session import get_db_session

        async with get_db_session() as session:
            await session.execute(text("SELECT 1"))
        return True, "Connected"
    except Exception as exc:
        return False, str(exc)[:120]


async def check_redis() -> tuple[bool, str]:
    """Verify Redis connectivity via PING."""
    if not settings.redis_url:
        return False, "REDIS_URL not configured"

    try:
        from src.cache.redis_client import get_redis_client

        client = get_redis_client()
        if client is None:
            return False, "Client not initialized"
        pong = await client.ping()
        return pong is True, "Connected" if pong else "PING failed"
    except Exception as exc:
        return False, str(exc)[:120]


async def check_slack() -> tuple[bool, str]:
    """Verify Slack bot token via auth.test()."""
    if not settings.slack_bot_token:
        return False, "SLACK_BOT_TOKEN not configured"

    try:
        from src.connectors.slack import SlackConnector

        connector = SlackConnector()
        result = connector.test_connection()
        team = result.get("team", "?")
        user = result.get("user", "?")
        return True, f"Team: {team}, Bot: {user}"
    except Exception as exc:
        return False, str(exc)[:120]


async def check_google_ads() -> tuple[bool, str]:
    """Verify Google Ads credentials are configured (does not call the API)."""
    missing = []
    if not settings.google_ads_developer_token:
        missing.append("GOOGLE_ADS_DEVELOPER_TOKEN")
    if not settings.google_ads_client_id:
        missing.append("GOOGLE_ADS_CLIENT_ID")
    if not settings.google_ads_client_secret:
        missing.append("GOOGLE_ADS_CLIENT_SECRET")

    if missing:
        return False, f"Missing: {', '.join(missing)}"
    return True, "Credentials configured"


async def check_meta() -> tuple[bool, str]:
    """Verify Meta API credentials are configured (does not call the API)."""
    missing = []
    if not settings.meta_app_id:
        missing.append("META_APP_ID")
    if not settings.meta_app_secret:
        missing.append("META_APP_SECRET")

    if missing:
        return False, f"Missing: {', '.join(missing)}"
    return True, "Credentials configured"


async def check_inngest() -> tuple[bool, str]:
    """Verify Inngest dev server is reachable."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get("http://127.0.0.1:8288/")
            if response.status_code < 500:
                return True, f"Dev server responding (HTTP {response.status_code})"
            return False, f"Dev server returned HTTP {response.status_code}"
    except httpx.ConnectError:
        return False, "Dev server not reachable at localhost:8288"
    except Exception as exc:
        return False, str(exc)[:120]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

# (name, check_fn, is_critical)
CHECKS = [
    ("Database (PostgreSQL)", check_database, True),
    ("Redis (Upstash)", check_redis, False),
    ("Slack", check_slack, False),
    ("Google Ads (creds)", check_google_ads, False),
    ("Meta (creds)", check_meta, False),
    ("Inngest (dev)", check_inngest, False),
]


async def main():
    print("Sidera Connection Check")
    print("=" * 60)

    results: list[tuple[str, bool, str, bool]] = []

    for name, check_fn, is_critical in CHECKS:
        ok, detail = await check_fn()
        results.append((name, ok, detail, is_critical))

    # Print results table
    max_name_len = max(len(name) for name, *_ in results)
    for name, ok, detail, is_critical in results:
        icon = "[PASS]" if ok else "[FAIL]"
        crit_label = " (critical)" if is_critical and not ok else ""
        print(f"  {icon} {name:<{max_name_len}}  {detail}{crit_label}")

    print("=" * 60)

    # Summary
    passed = sum(1 for _, ok, _, _ in results if ok)
    total = len(results)
    critical_failures = [name for name, ok, _, is_critical in results if is_critical and not ok]

    print(f"  {passed}/{total} services connected")

    if critical_failures:
        print(f"\n  Critical failures: {', '.join(critical_failures)}")
        print("  Fix critical services before running the application.")
        sys.exit(1)
    else:
        print("\n  All critical services operational.")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
