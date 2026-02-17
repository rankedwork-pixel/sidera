"""Create all database tables directly (for dev/test -- production uses Alembic)."""

import asyncio

from src.db.session import init_db


async def main():
    await init_db()
    print("Tables created successfully.")


if __name__ == "__main__":
    asyncio.run(main())
