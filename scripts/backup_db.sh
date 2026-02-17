#!/bin/bash
# ==============================================================================
# Sidera Database Backup Script
#
# Creates a compressed PostgreSQL backup using pg_dump.
#
# Usage:
#   ./scripts/backup_db.sh [output_directory]
#
# Requirements:
#   - DATABASE_URL environment variable must be set
#   - pg_dump must be installed (PostgreSQL client tools)
#
# Output:
#   Creates: sidera_backup_YYYYMMDD_HHMMSS.dump
#
# Optional S3 upload:
#   Set BACKUP_S3_BUCKET to upload to S3 after creating the backup.
#   Example: BACKUP_S3_BUCKET=s3://my-bucket/sidera-backups
# ==============================================================================

set -euo pipefail

# --- Configuration ---
OUTPUT_DIR="${1:-.}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
FILENAME="sidera_backup_${TIMESTAMP}.dump"
FILEPATH="${OUTPUT_DIR}/${FILENAME}"

# --- Validation ---
if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL environment variable is not set."
    echo "Set it in your .env file or export it before running this script."
    exit 1
fi

if ! command -v pg_dump &> /dev/null; then
    echo "ERROR: pg_dump is not installed."
    echo "Install PostgreSQL client tools: brew install postgresql (macOS)"
    exit 1
fi

# --- Create output directory if needed ---
mkdir -p "${OUTPUT_DIR}"

# --- Run backup ---
echo "=== Sidera Database Backup ==="
echo "Timestamp: ${TIMESTAMP}"
echo "Output:    ${FILEPATH}"
echo ""

echo "Starting pg_dump..."
pg_dump \
    "${DATABASE_URL}" \
    --format=custom \
    --verbose \
    --no-owner \
    --no-privileges \
    --file="${FILEPATH}" \
    2>&1 | while IFS= read -r line; do echo "  ${line}"; done

# --- Verify ---
if [ ! -f "${FILEPATH}" ]; then
    echo "ERROR: Backup file was not created."
    exit 1
fi

FILE_SIZE=$(du -h "${FILEPATH}" | cut -f1)
echo ""
echo "Backup complete: ${FILEPATH} (${FILE_SIZE})"

# --- Optional S3 upload ---
if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
    if command -v aws &> /dev/null; then
        echo ""
        echo "Uploading to S3: ${BACKUP_S3_BUCKET}/${FILENAME}"
        aws s3 cp "${FILEPATH}" "${BACKUP_S3_BUCKET}/${FILENAME}"
        echo "S3 upload complete."
    else
        echo "WARNING: BACKUP_S3_BUCKET is set but AWS CLI is not installed."
        echo "Install it with: pip install awscli"
    fi
fi

echo ""
echo "=== Done ==="
