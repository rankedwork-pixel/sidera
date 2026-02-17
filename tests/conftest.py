"""Root conftest — shared fixtures for the entire test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _rbac_allow_all_in_tests(monkeypatch):
    """Set RBAC default role to 'approver' for all tests.

    Production default is ``"none"`` (block unregistered users).
    Tests need ``"approver"`` so test user IDs aren't rejected.
    ``monkeypatch`` ensures the change is scoped to each test.
    """
    from src.config import settings

    monkeypatch.setattr(settings, "rbac_default_role", "approver")
