"""Enable Row Level Security on all tables.

Supabase Security Advisor flags all public tables without RLS as errors.
This migration enables RLS on every application table and adds a permissive
policy for the authenticated/service_role so the SQLAlchemy connection
(which uses the service_role or direct connection) continues to work.

The anon key will be blocked from reading any table by default.

Revision ID: rls_001
Revises: working_group_001
Create Date: 2026-02-18
"""

revision = "rls_001"
down_revision = "working_group_001"

from alembic import op


# All application tables that need RLS enabled.
TABLES = [
    "accounts",
    "analysis_results",
    "approval_queue",
    "audit_log",
    "campaigns",
    "claude_code_tasks",
    "conversation_threads",
    "cost_tracking",
    "daily_metrics",
    "failed_runs",
    "meeting_sessions",
    "org_departments",
    "org_roles",
    "org_skills",
    "role_memory",
    "role_messages",
    "users",
    "webhook_events",
    "working_group_sessions",
]


def upgrade() -> None:
    for table in TABLES:
        # Enable RLS on the table
        op.execute(
            f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"
        )
        # Allow the service_role (used by SQLAlchemy direct connection)
        # full unrestricted access.  The postgres role (superuser /
        # connection owner) bypasses RLS automatically so this policy
        # covers the Supabase service_role specifically.
        op.execute(
            f'CREATE POLICY "service_role_full_access" ON public.{table} '
            f"FOR ALL "
            f"TO service_role "
            f"USING (true) WITH CHECK (true)"
        )
        # Also allow the postgres role explicitly in case it doesn't
        # bypass RLS in all Supabase configurations.
        op.execute(
            f'CREATE POLICY "postgres_full_access" ON public.{table} '
            f"FOR ALL "
            f"TO postgres "
            f"USING (true) WITH CHECK (true)"
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(
            f'DROP POLICY IF EXISTS "postgres_full_access" ON public.{table}'
        )
        op.execute(
            f'DROP POLICY IF EXISTS "service_role_full_access" ON public.{table}'
        )
        op.execute(
            f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY"
        )
