"""Tests for skill-proposal execution pipeline wiring.

Covers:
- ``_execute_action()`` routing ``skill_proposal`` to
  ``execute_skill_proposal`` (bypassing platform connectors)
- ``execute_skill_proposal()`` integration with ``create_org_skill``
  and ``update_org_skill`` DB service methods
- Fallback from modify to create when skill not found

All DB sessions and service methods are mocked.  Because
``execute_skill_proposal`` uses *deferred* imports (``from src.db import
service as db_service``), we patch at the source module paths
(``src.db.session.get_db_session``, ``src.db.service.create_org_skill``,
``src.db.service.update_org_skill``).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.skills.evolution import execute_skill_proposal
from src.workflows.daily_briefing import _execute_action

# =====================================================================
# Helpers
# =====================================================================


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _mock_db_session():
    """Return a mock ``get_db_session`` async context manager and its session.

    The returned ``mock_get`` can be used as the replacement for
    ``src.db.session.get_db_session``.  Each call returns a fresh
    async CM that yields the same ``session`` mock.
    """
    session = AsyncMock()

    def _make_cm():
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    mock_get = MagicMock(side_effect=lambda: _make_cm())
    return mock_get, session


# =====================================================================
# _execute_action routing — skill_proposal
# =====================================================================


class TestExecuteActionSkillProposalRouting:
    """Verify _execute_action routes skill_proposal to execute_skill_proposal."""

    def test_create_proposal_routes_to_execute_skill_proposal(self):
        """action_type='skill_proposal' with create params calls execute_skill_proposal."""
        action_params = {
            "proposal_type": "create",
            "skill_fields": {
                "name": "Weekly Summary",
                "description": "Summarize weekly data",
                "category": "reporting",
                "system_supplement": "You are a reporting agent.",
                "prompt_template": "Summarize this week.",
                "output_format": "markdown",
                "business_guidance": "Focus on KPIs.",
            },
        }

        with patch(
            "src.skills.evolution.execute_skill_proposal",
            new_callable=AsyncMock,
            return_value={"ok": True, "proposal_type": "create", "skill_id": "weekly_summary"},
        ) as mock_exec:
            result = _run(_execute_action("skill_proposal", action_params))

        mock_exec.assert_called_once_with(action_params)
        assert result["ok"] is True
        assert result["proposal_type"] == "create"

    def test_modify_proposal_routes_to_execute_skill_proposal(self):
        """action_type='skill_proposal' with modify params calls execute_skill_proposal."""
        action_params = {
            "proposal_type": "modify",
            "skill_id": "daily_spend_check",
            "changes": {"description": "Updated description"},
        }

        with patch(
            "src.skills.evolution.execute_skill_proposal",
            new_callable=AsyncMock,
            return_value={
                "ok": True,
                "proposal_type": "modify",
                "skill_id": "daily_spend_check",
                "changed_fields": ["description"],
            },
        ) as mock_exec:
            result = _run(_execute_action("skill_proposal", action_params))

        mock_exec.assert_called_once_with(action_params)
        assert result["proposal_type"] == "modify"

    def test_skill_proposal_does_not_check_platform(self):
        """skill_proposal routing does NOT read action_params['platform']."""
        action_params = {
            "proposal_type": "create",
            "skill_fields": {
                "name": "Test",
                "description": "d",
                "category": "analysis",
                "system_supplement": "s",
                "prompt_template": "p",
                "output_format": "o",
                "business_guidance": "b",
            },
            # Deliberately include no 'platform' key — must not raise
        }

        with patch(
            "src.skills.evolution.execute_skill_proposal",
            new_callable=AsyncMock,
            return_value={"ok": True, "proposal_type": "create", "skill_id": "test"},
        ):
            # Should succeed without KeyError on 'platform'
            result = _run(_execute_action("skill_proposal", action_params))

        assert result["ok"] is True

    def test_regular_platform_action_still_routes_to_connector(self):
        """Non-skill_proposal actions (e.g. budget_change) still route to platform connectors."""
        mock_connector = MagicMock()
        mock_connector.update_campaign_budget.return_value = {"success": True}

        with patch(
            "src.connectors.google_ads.GoogleAdsConnector",
            return_value=mock_connector,
        ):
            result = _run(
                _execute_action(
                    "budget_change",
                    {
                        "platform": "google_ads",
                        "customer_id": "123",
                        "campaign_id": "456",
                        "new_budget_micros": "5000000",
                    },
                )
            )

        assert result == {"success": True}
        mock_connector.update_campaign_budget.assert_called_once()


# =====================================================================
# execute_skill_proposal integration
# =====================================================================


class TestExecuteSkillProposalIntegration:
    """Verify execute_skill_proposal delegates to DB service correctly."""

    def test_create_calls_create_org_skill(self):
        """Create proposal calls create_org_skill with correct args."""
        mock_get, session = _mock_db_session()
        mock_create = AsyncMock(return_value=MagicMock(skill_id="weekly_summary"))

        action_params = {
            "proposal_type": "create",
            "skill_fields": {
                "name": "Weekly Summary",
                "description": "Summarize weekly data",
                "category": "reporting",
                "system_supplement": "You are a reporting agent.",
                "prompt_template": "Summarize this week.",
                "output_format": "markdown",
                "business_guidance": "Focus on KPIs.",
            },
        }

        with (
            patch("src.db.session.get_db_session", mock_get),
            patch("src.db.service.create_org_skill", mock_create),
        ):
            result = _run(execute_skill_proposal(action_params))

        assert result["ok"] is True
        assert result["proposal_type"] == "create"
        assert result["skill_id"] == "weekly_summary"

        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args
        # session is first positional arg
        assert call_kwargs[0][0] is session
        assert call_kwargs[1]["skill_id"] == "weekly_summary"
        assert call_kwargs[1]["created_by"] == "skill_evolution"

    def test_modify_calls_update_org_skill(self):
        """Modify proposal calls update_org_skill with correct args."""
        mock_get, session = _mock_db_session()
        mock_update = AsyncMock(return_value=MagicMock(skill_id="daily_spend_check"))

        action_params = {
            "proposal_type": "modify",
            "skill_id": "daily_spend_check",
            "changes": {"description": "Better description"},
        }

        with (
            patch("src.db.session.get_db_session", mock_get),
            patch("src.db.service.update_org_skill", mock_update),
        ):
            result = _run(execute_skill_proposal(action_params))

        assert result["ok"] is True
        assert result["proposal_type"] == "modify"
        assert result["skill_id"] == "daily_spend_check"
        assert "description" in result["changed_fields"]

        mock_update.assert_called_once()
        call_args = mock_update.call_args
        # Positional: session, skill_id
        assert call_args[0][0] is session
        assert call_args[0][1] == "daily_spend_check"
        assert call_args[1]["description"] == "Better description"

    def test_modify_falls_back_to_create_when_skill_not_found(self):
        """When update_org_skill returns None, modify falls back to create_org_skill."""
        mock_get, session = _mock_db_session()
        mock_update = AsyncMock(return_value=None)
        mock_create = AsyncMock(return_value=MagicMock(skill_id="nonexistent_skill"))

        action_params = {
            "proposal_type": "modify",
            "skill_id": "nonexistent_skill",
            "changes": {"description": "A description"},
        }

        with (
            patch("src.db.session.get_db_session", mock_get),
            patch("src.db.service.update_org_skill", mock_update),
            patch("src.db.service.create_org_skill", mock_create),
        ):
            result = _run(execute_skill_proposal(action_params))

        assert result["ok"] is True
        assert result["proposal_type"] == "modify"

        # update was tried first
        mock_update.assert_called_once()
        # then create fallback was invoked
        mock_create.assert_called_once()
        create_kwargs = mock_create.call_args[1]
        assert create_kwargs["skill_id"] == "nonexistent_skill"
        assert create_kwargs["created_by"] == "skill_evolution"

    def test_unknown_proposal_type_raises_value_error(self):
        """Unknown proposal_type raises ValueError."""
        action_params = {
            "proposal_type": "delete",
        }

        with pytest.raises(ValueError, match="Unknown proposal_type: delete"):
            _run(execute_skill_proposal(action_params))
