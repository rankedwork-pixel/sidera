"""Tests for src.mcp_servers.claude_code_actions -- propose_claude_code_task MCP tool.

Covers input validation (missing required fields), skill-not-found handling,
successful proposals with default and explicit parameters, recommendation
format, and the pending-tasks lifecycle (accumulate, get-and-clear, clear).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_servers.claude_code_actions import (
    clear_pending_cc_tasks,
    get_pending_cc_tasks,
    propose_claude_code_task,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine synchronously for testing."""
    return asyncio.run(coro)


def _mock_skill():
    """Create a mock skill object with standard test attributes."""
    skill = MagicMock()
    skill.name = "Test Skill"
    skill.department_id = "test_dept"
    return skill


def _mock_registry(skill=None):
    """Create a mock registry that returns the given skill on get_skill()."""
    registry = MagicMock()
    registry.get_skill.return_value = skill
    return registry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_pending_tasks():
    """Ensure pending CC tasks are empty before and after each test."""
    clear_pending_cc_tasks()
    yield
    clear_pending_cc_tasks()


# ===========================================================================
# 1. Valid task proposal
# ===========================================================================


class TestProposeValidTask:
    """propose_claude_code_task with valid arguments and a known skill."""

    def test_propose_valid_task(self):
        """A valid proposal should return success with 'queued' text."""
        skill = _mock_skill()
        registry = _mock_registry(skill)
        mock_loader = AsyncMock(return_value=registry)

        with patch("src.skills.db_loader.load_registry_with_db", mock_loader):
            result = _run(
                propose_claude_code_task.handler(
                    {
                        "skill_id": "daily_spend_analysis",
                        "description": "Run daily spend analysis via Claude Code",
                        "reasoning": "Need deeper analysis with file access.",
                    }
                )
            )

        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "queued" in text.lower()

    def test_proposal_in_pending_list(self):
        """After a valid proposal, get_pending_cc_tasks() should return it."""
        skill = _mock_skill()
        registry = _mock_registry(skill)
        mock_loader = AsyncMock(return_value=registry)

        with patch("src.skills.db_loader.load_registry_with_db", mock_loader):
            _run(
                propose_claude_code_task.handler(
                    {
                        "skill_id": "daily_spend_analysis",
                        "description": "Run spend analysis",
                        "reasoning": "Complex task needs Claude Code.",
                    }
                )
            )

        tasks = get_pending_cc_tasks()
        assert len(tasks) == 1
        assert tasks[0]["action_type"] == "claude_code_task"
        assert tasks[0]["action_params"]["skill_id"] == "daily_spend_analysis"


# ===========================================================================
# 2. Missing required fields
# ===========================================================================


class TestMissingFields:
    """Validation: required fields must be present and non-empty."""

    def test_missing_skill_id(self):
        """Omitting skill_id should return an error."""
        result = _run(
            propose_claude_code_task.handler(
                {
                    "description": "Some description",
                    "reasoning": "Some reasoning",
                }
            )
        )

        assert result["is_error"] is True
        assert "skill_id" in result["content"][0]["text"].lower()

    def test_missing_description(self):
        """Omitting description should return an error."""
        result = _run(
            propose_claude_code_task.handler(
                {
                    "skill_id": "daily_spend_analysis",
                    "reasoning": "Some reasoning",
                }
            )
        )

        assert result["is_error"] is True
        assert "description" in result["content"][0]["text"].lower()

    def test_missing_reasoning(self):
        """Omitting reasoning should return an error."""
        result = _run(
            propose_claude_code_task.handler(
                {
                    "skill_id": "daily_spend_analysis",
                    "description": "Some description",
                }
            )
        )

        assert result["is_error"] is True
        assert "reasoning" in result["content"][0]["text"].lower()


# ===========================================================================
# 3. Skill not found
# ===========================================================================


class TestSkillNotFound:
    """Validation: skill must exist in registry."""

    def test_skill_not_found(self):
        """If registry.get_skill() returns None, an error is returned."""
        registry = _mock_registry(skill=None)
        mock_loader = AsyncMock(return_value=registry)

        with patch("src.skills.db_loader.load_registry_with_db", mock_loader):
            result = _run(
                propose_claude_code_task.handler(
                    {
                        "skill_id": "nonexistent_skill",
                        "description": "Trying a missing skill",
                        "reasoning": "Should fail validation.",
                    }
                )
            )

        assert result["is_error"] is True
        assert "not found" in result["content"][0]["text"].lower()


# ===========================================================================
# 4. Default values
# ===========================================================================


class TestDefaults:
    """Optional fields should use sensible defaults."""

    def test_default_values(self):
        """Proposal with only required fields should get default budget and permission mode."""
        skill = _mock_skill()
        registry = _mock_registry(skill)
        mock_loader = AsyncMock(return_value=registry)

        with patch("src.skills.db_loader.load_registry_with_db", mock_loader):
            _run(
                propose_claude_code_task.handler(
                    {
                        "skill_id": "daily_spend_analysis",
                        "description": "Run analysis",
                        "reasoning": "Test defaults.",
                    }
                )
            )

        tasks = get_pending_cc_tasks()
        assert len(tasks) == 1
        params = tasks[0]["action_params"]
        assert params["max_budget_usd"] == 5.0
        assert params["permission_mode"] == "acceptEdits"


# ===========================================================================
# 5. Pending lifecycle — get returns and clears
# ===========================================================================


class TestPendingLifecycle:
    """get_pending_cc_tasks returns proposals and clears; clear_pending_cc_tasks discards."""

    def test_get_pending_returns_and_clears(self):
        """After calling get_pending_cc_tasks, the list should be empty."""
        skill = _mock_skill()
        registry = _mock_registry(skill)
        mock_loader = AsyncMock(return_value=registry)

        with patch("src.skills.db_loader.load_registry_with_db", mock_loader):
            _run(
                propose_claude_code_task.handler(
                    {
                        "skill_id": "daily_spend_analysis",
                        "description": "Run analysis",
                        "reasoning": "Test lifecycle.",
                    }
                )
            )

        tasks = get_pending_cc_tasks()
        assert len(tasks) == 1

        # Second call should return empty
        tasks_again = get_pending_cc_tasks()
        assert len(tasks_again) == 0

    def test_clear_pending(self):
        """clear_pending_cc_tasks should discard all tasks."""
        skill = _mock_skill()
        registry = _mock_registry(skill)
        mock_loader = AsyncMock(return_value=registry)

        with patch("src.skills.db_loader.load_registry_with_db", mock_loader):
            _run(
                propose_claude_code_task.handler(
                    {
                        "skill_id": "daily_spend_analysis",
                        "description": "Run analysis",
                        "reasoning": "Test clear.",
                    }
                )
            )

        clear_pending_cc_tasks()

        tasks = get_pending_cc_tasks()
        assert len(tasks) == 0


# ===========================================================================
# 6. Recommendation format
# ===========================================================================


class TestRecommendationFormat:
    """Verify the recommendation dict has the expected keys and structure."""

    def test_recommendation_format(self):
        """The queued recommendation should have all required keys."""
        skill = _mock_skill()
        registry = _mock_registry(skill)
        mock_loader = AsyncMock(return_value=registry)

        with patch("src.skills.db_loader.load_registry_with_db", mock_loader):
            _run(
                propose_claude_code_task.handler(
                    {
                        "skill_id": "daily_spend_analysis",
                        "description": "Detailed analysis task",
                        "reasoning": "Need file access for this.",
                        "prompt": "Analyze the spend data in detail.",
                        "max_budget_usd": 8.0,
                        "permission_mode": "plan",
                        "risk_level": "high",
                    }
                )
            )

        tasks = get_pending_cc_tasks()
        assert len(tasks) == 1
        rec = tasks[0]

        # Top-level keys
        assert rec["action_type"] == "claude_code_task"
        assert rec["description"] == "Detailed analysis task"
        assert rec["reasoning"] == "Need file access for this."
        assert "projected_impact" in rec
        assert rec["risk_level"] == "high"

        # action_params sub-keys
        params = rec["action_params"]
        assert params["skill_id"] == "daily_spend_analysis"
        assert params["skill_name"] == "Test Skill"
        assert params["prompt"] == "Analyze the spend data in detail."
        assert params["max_budget_usd"] == 8.0
        assert params["permission_mode"] == "plan"
