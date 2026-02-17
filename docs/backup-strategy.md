# Sidera Database Backup Strategy

## Overview

Sidera uses PostgreSQL via Supabase. This document covers automated and manual backup procedures, restore workflows, and pre-migration checklists.

## Automated Backups (Supabase)

Supabase provides automated backups depending on your plan:

| Plan | Backup Frequency | Retention | Point-in-Time Recovery |
|------|-----------------|-----------|----------------------|
| Free | None | — | No |
| Pro | Daily | 7 days | Yes (last 7 days) |
| Team | Daily | 14 days | Yes (last 14 days) |
| Enterprise | Daily | 30 days | Yes (last 30 days) |

### Accessing Automated Backups

1. Go to your Supabase dashboard
2. Navigate to **Database > Backups**
3. Select a backup point to restore from
4. For PITR: choose a specific timestamp within the retention window

### Point-in-Time Recovery (PITR)

PITR allows restoring to any second within the retention window. Use this for:
- Accidental data deletion
- Recovering from a bad migration
- Rolling back to a known good state

## Manual Backups

For additional safety, use the provided backup script before risky operations.

### Prerequisites

- `pg_dump` and `pg_restore` installed (PostgreSQL client tools)
- `DATABASE_URL` environment variable set
- (Optional) AWS CLI for S3 upload

### Creating a Backup

```bash
# Basic backup
./scripts/backup_db.sh

# Specify output directory
./scripts/backup_db.sh /path/to/backups

# The script creates: sidera_backup_YYYYMMDD_HHMMSS.dump
```

### Restoring from Backup

```bash
# Restore (requires --confirm flag for safety)
./scripts/restore_db.sh /path/to/sidera_backup_20240115_143022.dump --confirm
```

**WARNING:** Restore overwrites the target database. Always verify you're targeting the correct database.

## Pre-Migration Backup Checklist

Before running any Alembic migration:

1. Create a manual backup: `./scripts/backup_db.sh`
2. Verify the backup file size is reasonable (not 0 bytes)
3. Test the migration on a staging database first (if available)
4. Run the migration: `alembic upgrade head`
5. Verify the migration: `alembic current`
6. Keep the backup for at least 7 days after migration

## Data Retention

Sidera automatically purges expired data via the `data_retention_workflow` (runs at 3 AM daily). See `src/config.py` for retention settings:

| Table | Default Retention | Setting |
|-------|------------------|---------|
| Audit Log | 365 days | `RETENTION_AUDIT_LOG_DAYS` |
| Analysis Results | 180 days | `RETENTION_ANALYSIS_RESULTS_DAYS` |
| Cost Tracking | 180 days | `RETENTION_COST_TRACKING_DAYS` |
| Decided Approvals | 90 days | `RETENTION_DECIDED_APPROVALS_DAYS` |
| Resolved Failed Runs | 30 days | `RETENTION_RESOLVED_FAILED_RUNS_DAYS` |
| Inactive Threads | 30 days | `RETENTION_INACTIVE_THREADS_DAYS` |
| Daily Metrics | 365 days | `RETENTION_DAILY_METRICS_DAYS` |
| Archived Memories | Forever (0) | `RETENTION_COLD_MEMORIES_DAYS` |

Set any value to `0` to keep data forever.

## Disaster Recovery

In case of total database loss:

1. **Supabase PITR** (fastest): Restore from Supabase dashboard to the latest good timestamp
2. **Manual backup**: Restore from the most recent `backup_db.sh` dump file
3. **Rebuild**: If no backup exists, recreate the schema with `alembic upgrade head` and re-seed with `scripts/seed_test_data.py`

## Monitoring

- The `cost_monitor_workflow` runs every 30 minutes and alerts on anomalies
- The `data_retention_workflow` logs purge counts to the audit log
- Check Sentry for any backup/restore errors
