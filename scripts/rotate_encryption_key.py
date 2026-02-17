#!/usr/bin/env python3
"""Re-encrypt all stored tokens with the current encryption key.

Key Rotation Workflow
---------------------
1. Generate a new Fernet key:
   ``python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"``

2. In your ``.env`` file:
   - Move the current ``TOKEN_ENCRYPTION_KEY`` value to ``TOKEN_ENCRYPTION_KEY_PREVIOUS``
   - Set ``TOKEN_ENCRYPTION_KEY`` to the newly generated key

3. Run this script:
   ``python -m scripts.rotate_encryption_key``

4. Once all tokens are re-encrypted, you can remove ``TOKEN_ENCRYPTION_KEY_PREVIOUS``
   from ``.env`` (optional — keeping it is harmless).

The script reads every ``enc:``-prefixed token from the ``accounts`` table
(where OAuth tokens are stored), decrypts it with either key via
``MultiFernet``, and re-encrypts it with the current key.
"""

from __future__ import annotations

import asyncio
import sys

import structlog

# Ensure the project root is on sys.path when run as ``python -m scripts.rotate_encryption_key``
sys.path.insert(0, ".")

from src.config import settings  # noqa: E402
from src.utils.encryption import rotate_token  # noqa: E402

logger = structlog.get_logger("rotate_encryption_key")


async def main() -> None:
    """Re-encrypt all stored tokens in the accounts table."""
    if not settings.token_encryption_key:
        print("ERROR: TOKEN_ENCRYPTION_KEY is not set. Cannot rotate.")
        sys.exit(1)

    if not settings.database_url:
        print("ERROR: DATABASE_URL is not set. Cannot connect to database.")
        sys.exit(1)

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(settings.database_url, echo=False)

    # Columns that may contain encrypted tokens
    # Format: (table, column)
    token_columns = [
        ("accounts", "access_token"),
        ("accounts", "refresh_token"),
    ]

    total_rotated = 0
    total_skipped = 0
    total_errors = 0

    async with engine.begin() as conn:
        for table, column in token_columns:
            print(f"\n--- Rotating {table}.{column} ---")

            # Check if table/column exists
            try:
                result = await conn.execute(
                    text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL")  # noqa: S608
                )
                rows = result.fetchall()
            except Exception as exc:
                print(f"  SKIP: Could not read {table}.{column} — {exc}")
                continue

            for row in rows:
                row_id, stored_value = row[0], row[1]

                if not stored_value:
                    total_skipped += 1
                    continue

                if not stored_value.startswith("enc:"):
                    # Plaintext token — encrypt it for the first time
                    print(f"  Row {row_id}: encrypting plaintext token")

                try:
                    rotated = rotate_token(stored_value)

                    if rotated != stored_value:
                        await conn.execute(
                            text(
                                f"UPDATE {table} SET {column} = :val WHERE id = :id"  # noqa: S608
                            ),
                            {"val": rotated, "id": row_id},
                        )
                        total_rotated += 1
                        print(f"  Row {row_id}: rotated successfully")
                    else:
                        total_skipped += 1
                except Exception as exc:
                    total_errors += 1
                    print(f"  Row {row_id}: ERROR — {exc}")
                    logger.error(
                        "rotate.row_failed",
                        table=table,
                        column=column,
                        row_id=row_id,
                        error=str(exc),
                    )

    await engine.dispose()

    print("\n=== Rotation Complete ===")
    print(f"  Rotated: {total_rotated}")
    print(f"  Skipped: {total_skipped}")
    print(f"  Errors:  {total_errors}")

    if total_errors > 0:
        print("\nWARNING: Some tokens failed to rotate. Check logs above.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
