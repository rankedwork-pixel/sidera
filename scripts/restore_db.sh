#!/bin/bash
# ==============================================================================
# Sidera Database Restore Script
#
# Restores a PostgreSQL database from a pg_dump backup file.
#
# Usage:
#   ./scripts/restore_db.sh <backup_file> --confirm
#
# Requirements:
#   - DATABASE_URL environment variable must be set
#   - pg_restore must be installed (PostgreSQL client tools)
#   - --confirm flag is REQUIRED (safety check)
#
# WARNING: This will OVERWRITE data in the target database!
# ==============================================================================

set -euo pipefail

# --- Parse arguments ---
BACKUP_FILE="${1:-}"
CONFIRM="${2:-}"

if [ -z "${BACKUP_FILE}" ]; then
    echo "ERROR: No backup file specified."
    echo "Usage: ./scripts/restore_db.sh <backup_file> --confirm"
    exit 1
fi

if [ ! -f "${BACKUP_FILE}" ]; then
    echo "ERROR: Backup file not found: ${BACKUP_FILE}"
    exit 1
fi

if [ "${CONFIRM}" != "--confirm" ]; then
    echo "ERROR: Safety check failed. You must pass --confirm to proceed."
    echo ""
    echo "WARNING: This will overwrite data in the target database!"
    echo "Make sure DATABASE_URL points to the correct database."
    echo ""
    echo "Usage: ./scripts/restore_db.sh ${BACKUP_FILE} --confirm"
    exit 1
fi

# --- Validation ---
if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL environment variable is not set."
    exit 1
fi

if ! command -v pg_restore &> /dev/null; then
    echo "ERROR: pg_restore is not installed."
    echo "Install PostgreSQL client tools: brew install postgresql (macOS)"
    exit 1
fi

# --- Restore ---
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
echo "=== Sidera Database Restore ==="
echo "Timestamp:   ${TIMESTAMP}"
echo "Backup file: ${BACKUP_FILE}"
echo "Target DB:   ${DATABASE_URL:0:40}..."
echo ""

FILE_SIZE=$(du -h "${BACKUP_FILE}" | cut -f1)
echo "Backup size: ${FILE_SIZE}"
echo ""

echo "Starting pg_restore..."
pg_restore \
    --dbname="${DATABASE_URL}" \
    --verbose \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges \
    "${BACKUP_FILE}" \
    2>&1 | while IFS= read -r line; do echo "  ${line}"; done

echo ""
echo "Restore complete."
echo ""
echo "Post-restore steps:"
echo "  1. Verify the application starts correctly"
echo "  2. Run: alembic current  (check migration state)"
echo "  3. Run: alembic upgrade head  (if migrations are behind)"
echo "  4. Test critical functionality"
echo ""
echo "=== Done ==="
