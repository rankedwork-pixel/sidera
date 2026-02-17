#!/usr/bin/env bash
# ============================================================================
# Sidera — Company Provisioning Script
#
# Sets up a new Sidera instance for a company from scratch.
# Run this after cloning the repo to get a deployment-ready environment.
#
# Usage:
#   ./scripts/provision_company.sh
#
# What it does:
#   1. Validates prerequisites (Python, Node, psql, Redis)
#   2. Creates a .env file from .env.example with generated secrets
#   3. Installs Python dependencies
#   4. Runs database migrations (Alembic)
#   5. Seeds the first admin user
#   6. Runs a health check
#   7. Prints next steps (OAuth setup, Slack app, deploy)
#
# Idempotent: safe to run multiple times. Skips steps already completed.
# ============================================================================

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info()  { echo -e "${BLUE}[INFO]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ============================================================================
# Step 0: Validate prerequisites
# ============================================================================

echo ""
echo "=========================================="
echo "  Sidera — Company Provisioning"
echo "=========================================="
echo ""

log_info "Checking prerequisites..."

MISSING=0

if ! command -v python3 &> /dev/null; then
    log_error "Python 3 is required. Install from https://python.org"
    MISSING=1
else
    PYTHON_VER=$(python3 --version 2>&1 | awk '{print $2}')
    log_ok "Python $PYTHON_VER"
fi

if ! command -v pip &> /dev/null && ! command -v pip3 &> /dev/null; then
    log_error "pip is required. Install with: python3 -m ensurepip"
    MISSING=1
else
    log_ok "pip available"
fi

if ! command -v psql &> /dev/null; then
    log_warn "psql not found — you won't be able to run migrations locally."
    log_warn "Install PostgreSQL client or use a managed DB (Supabase, Railway Postgres)."
else
    log_ok "psql available"
fi

if [ $MISSING -eq 1 ]; then
    log_error "Fix the missing prerequisites above and re-run."
    exit 1
fi

# ============================================================================
# Step 1: Create .env file
# ============================================================================

echo ""
log_info "Step 1: Environment configuration"

if [ -f ".env" ]; then
    log_ok ".env already exists — skipping creation."
else
    log_info "Creating .env from .env.example..."
    cp .env.example .env

    # Generate secrets
    API_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
    TOKEN_KEY=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' 2>/dev/null || echo "")

    # Write generated values
    if [ -n "$API_KEY" ]; then
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s/^API_KEY=$/API_KEY=$API_KEY/" .env
        else
            sed -i "s/^API_KEY=$/API_KEY=$API_KEY/" .env
        fi
        log_ok "Generated API_KEY"
    fi

    if [ -n "$TOKEN_KEY" ]; then
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s/^TOKEN_ENCRYPTION_KEY=$/TOKEN_ENCRYPTION_KEY=$TOKEN_KEY/" .env
        else
            sed -i "s/^TOKEN_ENCRYPTION_KEY=$/TOKEN_ENCRYPTION_KEY=$TOKEN_KEY/" .env
        fi
        log_ok "Generated TOKEN_ENCRYPTION_KEY"
    fi

    log_ok "Created .env — edit it to add your API keys (Anthropic, Slack, Google, etc.)"
fi

# ============================================================================
# Step 2: Install dependencies
# ============================================================================

echo ""
log_info "Step 2: Installing Python dependencies..."

if [ -f "pyproject.toml" ]; then
    pip3 install -e ".[dev]" --quiet 2>/dev/null || pip3 install -e . --quiet
    log_ok "Dependencies installed"
else
    log_error "pyproject.toml not found. Are you in the project root?"
    exit 1
fi

# ============================================================================
# Step 3: Database migrations
# ============================================================================

echo ""
log_info "Step 3: Database setup"

# Check if DATABASE_URL is configured
source .env 2>/dev/null || true
DB_URL="${DATABASE_URL:-}"

if [ -z "$DB_URL" ]; then
    log_warn "DATABASE_URL not set in .env — skipping migrations."
    log_warn "Set DATABASE_URL to a PostgreSQL connection string, then run:"
    log_warn "  alembic upgrade head"
else
    log_info "Running Alembic migrations..."
    alembic upgrade head
    log_ok "Database migrations complete"
fi

# ============================================================================
# Step 4: Seed admin user
# ============================================================================

echo ""
log_info "Step 4: Seed admin user"

if [ -z "$DB_URL" ]; then
    log_warn "DATABASE_URL not set — skipping admin user creation."
    log_warn "After setting up the database, create the first admin with:"
    log_warn "  python3 scripts/seed_admin.py <slack_user_id>"
else
    echo ""
    read -p "Enter the Slack User ID for the first admin (e.g. U0123ABCDEF): " ADMIN_ID
    if [ -n "$ADMIN_ID" ]; then
        python3 -c "
import asyncio
import sys
sys.path.insert(0, '.')
from src.db.session import get_db_session
from src.db import service as db_service

async def seed():
    async with get_db_session() as session:
        existing = await db_service.get_user(session, '$ADMIN_ID')
        if existing:
            print('Admin user already exists.')
            return
        await db_service.create_user(
            session, '$ADMIN_ID',
            display_name='Initial Admin',
            role='admin',
            created_by='provision_script',
        )
        await session.commit()
        print('Admin user created: $ADMIN_ID')

asyncio.run(seed())
"
        log_ok "Admin user seeded"
    else
        log_warn "Skipped admin user creation. Create later with:"
        log_warn "  python3 scripts/seed_admin.py <slack_user_id>"
    fi
fi

# ============================================================================
# Step 5: Verify
# ============================================================================

echo ""
log_info "Step 5: Quick verification"

# Run a quick import check
python3 -c "
from src.config import settings
from src.models.schema import User, UserRole
print(f'  App env: {settings.app_env}')
print(f'  API key configured: {bool(settings.api_key)}')
print(f'  Database configured: {bool(settings.database_url)}')
print(f'  Slack configured: {bool(settings.slack_bot_token)}')
print(f'  Anthropic configured: {bool(settings.anthropic_api_key)}')
print(f'  RBAC default role: {settings.rbac_default_role}')
" 2>/dev/null && log_ok "Import check passed" || log_error "Import check failed"

# ============================================================================
# Step 6: Next steps
# ============================================================================

echo ""
echo "=========================================="
echo "  Provisioning Complete"
echo "=========================================="
echo ""
echo "Next steps to get fully operational:"
echo ""
echo "  1. REQUIRED: Edit .env and add your API keys:"
echo "     - ANTHROPIC_API_KEY (get from console.anthropic.com)"
echo "     - DATABASE_URL (PostgreSQL connection string)"
echo "     - SLACK_BOT_TOKEN + SLACK_SIGNING_SECRET (from api.slack.com)"
echo "     - SLACK_CHANNEL_ID (where approvals are posted)"
echo ""
echo "  2. REQUIRED: Set up the Slack app:"
echo "     - Create app at api.slack.com/apps"
echo "     - Enable Event Subscriptions + Interactivity"
echo "     - Add slash command /sidera"
echo "     - Install to workspace"
echo ""
echo "  3. OPTIONAL: OAuth for connectors:"
echo "     - Google Ads: GOOGLE_ADS_* keys + OAuth flow"
echo "     - Meta: META_* keys + OAuth flow"
echo "     - BigQuery: BIGQUERY_* keys"
echo ""
echo "  4. Start the server:"
echo "     uvicorn src.api.app:app --reload --port 8000"
echo ""
echo "  5. For Slack in development, use ngrok:"
echo "     ngrok http 8000"
echo "     Set the ngrok URL in your Slack app config"
echo ""
echo "  6. Verify:"
echo "     curl http://localhost:8000/health"
echo "     /sidera users whoami  (in Slack)"
echo ""
