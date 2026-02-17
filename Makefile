# ──────────────────────────────────────────────────────────────────────────────
# Sidera — Development Makefile
# ──────────────────────────────────────────────────────────────────────────────
#
# Usage:
#   make lint          Lint source + tests with ruff
#   make format        Auto-format with ruff
#   make test          Run full test suite
#   make test-fast     Run tests (no slow markers)
#   make sync-docs     Check doc counts match codebase
#   make update-docs   Auto-fix doc counts
#   make cleanup       Full cleanup: lint + test + sync-docs
#   make pre-commit    Install pre-commit hooks
#   make check         CI-equivalent check (lint + test + sync-docs)
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: lint format test test-fast sync-docs update-docs cleanup pre-commit check help

PYTHON ?= python3
PYTEST ?= $(PYTHON) -m pytest
RUFF ?= $(PYTHON) -m ruff

# Default target
help:
	@echo "Sidera Development Commands"
	@echo "════════════════════════════"
	@echo ""
	@echo "  make lint          Lint source + tests with ruff"
	@echo "  make format        Auto-format with ruff"
	@echo "  make test          Run full test suite"
	@echo "  make test-fast     Run tests in parallel (no coverage)"
	@echo "  make sync-docs     Check doc counts match codebase"
	@echo "  make update-docs   Auto-fix stale doc counts"
	@echo "  make cleanup       Full cleanup: format + lint + test + sync-docs"
	@echo "  make pre-commit    Install pre-commit hooks"
	@echo "  make check         CI-equivalent check (lint + test + sync-docs)"
	@echo ""

# ── Lint ─────────────────────────────────────────────────────────────────────

lint:
	$(RUFF) check src/ tests/ dashboard/

format:
	$(RUFF) format src/ tests/ dashboard/
	$(RUFF) check --fix src/ tests/ dashboard/

# ── Test ─────────────────────────────────────────────────────────────────────

test:
	$(PYTEST) tests/ -v --tb=short -q

test-fast:
	$(PYTEST) tests/ -x -q --no-header

test-cov:
	$(PYTEST) tests/ -v --tb=short --cov=src --cov-report=html --cov-report=term

# ── Doc Sync ─────────────────────────────────────────────────────────────────

sync-docs:
	$(PYTHON) scripts/doc_sync.py

update-docs:
	$(PYTHON) scripts/doc_sync.py --update

sync-docs-fast:
	$(PYTHON) scripts/doc_sync.py --skip-pytest

# ── Full Cleanup ─────────────────────────────────────────────────────────────

cleanup: format lint test sync-docs
	@echo ""
	@echo "✓ Full cleanup complete: format + lint + test + doc sync"

# ── Pre-commit ───────────────────────────────────────────────────────────────

pre-commit:
	$(PYTHON) -m pre_commit install
	@echo "Pre-commit hooks installed."

# ── CI Check (no formatting, strict) ────────────────────────────────────────

check: lint test sync-docs
	@echo ""
	@echo "✓ All CI checks passed."

# ── Alembic ──────────────────────────────────────────────────────────────────

migrate:
	alembic upgrade head

migration:
	@read -p "Migration message: " msg; \
	alembic revision --autogenerate -m "$$msg"

# ── Server ───────────────────────────────────────────────────────────────────

dev:
	uvicorn src.api.app:app --reload --port 8000

dashboard:
	streamlit run dashboard/app.py
