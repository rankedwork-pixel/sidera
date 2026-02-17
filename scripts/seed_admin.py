#!/usr/bin/env python3
"""Seed the first admin user in the database.

Usage:
    python scripts/seed_admin.py <slack_user_id> [display_name]

Example:
    python scripts/seed_admin.py U0123ABCDEF "Jane Smith"
"""

import asyncio
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/seed_admin.py <slack_user_id> [display_name]")
        print("Example: python scripts/seed_admin.py U0123ABCDEF 'Jane Smith'")
        sys.exit(1)

    user_id = sys.argv[1].strip().upper()
    display_name = sys.argv[2] if len(sys.argv) > 2 else "Admin"

    from src.db import service as db_service
    from src.db.session import get_db_session

    async with get_db_session() as session:
        existing = await db_service.get_user(session, user_id)
        if existing:
            role = existing.role.value if hasattr(existing.role, "value") else str(existing.role)
            print(f"User {user_id} already exists with role: {role}")
            if role != "admin":
                print(f"Upgrading {user_id} to admin...")
                await db_service.update_user_role(
                    session, user_id, "admin", changed_by="seed_script",
                )
                await session.commit()
                print(f"Done. {user_id} is now admin.")
            return

        await db_service.create_user(
            session,
            user_id,
            display_name=display_name,
            role="admin",
            created_by="seed_script",
        )
        await session.commit()
        print(f"Created admin user: {user_id} ({display_name})")


if __name__ == "__main__":
    asyncio.run(main())
