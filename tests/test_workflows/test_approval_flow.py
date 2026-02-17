"""Tests for the shared approval flow in src/workflows/approval_flow.py.

Covers:
- Empty recommendations -> zero-count summary
- Single and multiple recommendations through manual path
- Auto-execute path when rules match
- Auto-execute failure and error recording
- Manual path: Slack approval request, approve, reject, timeout
- Mixed auto-execute and manual recommendations
- Exception handling in DB create_approval
- Exception handling when loading auto-execute rules
- Summary dict key completeness
- ctx.step.run and ctx.step.wait_for_event call patterns
- Double-execution prevention (executed_at already set)
- Execution failure in manual path
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.workflows.approval_flow import process_recommendations
from tests.test_workflows.conftest import _make_mock_context

# =====================================================================
# Helpers
# =====================================================================

SUMMARY_KEYS = {
    "auto_executed",
    "sent_for_approval",
    "approved",
    "rejected",
    "expired",
    "executed",
    "failed",
    "errors",
}


def _make_recommendation(
    action: str = "Increase budget by 10%",
    reasoning: str = "Strong ROAS",
    action_type: str = "budget_change",
    risk_level: str = "low",
    projected_impact: str = "+$500/week",
    action_params: dict | None = None,
) -> dict:
    return {
        "action": action,
        "reasoning": reasoning,
        "action_type": action_type,
        "risk_level": risk_level,
        "projected_impact": projected_impact,
        "action_params": action_params or {"platform": "google_ads"},
    }


def _mock_approval_item(
    item_id: int = 1,
    action_type_value: str = "budget_change",
    action_params: dict | None = None,
    executed_at=None,
):
    """Build a mock ApprovalQueueItem."""
    item = MagicMock()
    item.id = item_id
    item.action_type = MagicMock()
    item.action_type.value = action_type_value
    item.action_params = action_params or {"platform": "google_ads"}
    item.executed_at = executed_at
    item.auto_execute_rule_id = None
    return item


def _mock_auto_decision(should: bool, rule_id: str = "rule-1"):
    """Build a mock AutoExecuteDecision."""
    d = MagicMock()
    d.should_auto_execute = should
    d.matched_rule_id = rule_id
    d.reasons = ("budget under threshold",)
    return d


def _mock_db_session_ctx(mock_session):
    """Return an async context manager that yields mock_session."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _ctx_with_wait(wait_return=None):
    """Build a mock context with configurable wait_for_event return."""
    ctx = _make_mock_context(
        event_data={},
        run_id="run-abc",
    )
    ctx.step.wait_for_event = AsyncMock(return_value=wait_return)
    return ctx


# --- Patch target constants ---

_P_DB_SESSION = "src.db.session.get_db_session"
_P_CREATE_APPROVAL = "src.db.service.create_approval"
_P_UPDATE_STATUS = "src.db.service.update_approval_status"
_P_LOG_EVENT = "src.db.service.log_event"
_P_RECORD_EXEC = "src.db.service.record_execution_result"
_P_GET_APPROVAL = "src.db.service.get_approval_by_id"
_P_SHOULD_AUTO = "src.skills.auto_execute.should_auto_execute"
_P_REGISTRY = "src.skills.db_loader.load_registry_with_db"
_P_SETTINGS = "src.config.settings"
_P_EXEC_ACTION = "src.workflows.daily_briefing._execute_action"
_P_SLACK = "src.connectors.slack.SlackConnector"


# =====================================================================
# Empty recommendations
# =====================================================================


@pytest.mark.asyncio
async def test_empty_recommendations_returns_zero_summary():
    """Empty list returns summary with all counts at zero."""
    ctx = _make_mock_context()
    result = await process_recommendations(
        ctx=ctx,
        recommendations=[],
        user_id="u1",
        channel_id="C1",
        run_id="run-1",
        source="test",
    )
    assert result["auto_executed"] == 0
    assert result["sent_for_approval"] == 0
    assert result["approved"] == 0
    assert result["rejected"] == 0
    assert result["expired"] == 0
    assert result["executed"] == 0
    assert result["failed"] == 0
    assert result["errors"] == []


@pytest.mark.asyncio
async def test_empty_recommendations_has_all_keys():
    """Summary dict contains every expected key."""
    ctx = _make_mock_context()
    result = await process_recommendations(
        ctx=ctx,
        recommendations=[],
        user_id="u1",
        channel_id="C1",
        run_id="r",
        source="s",
    )
    assert set(result.keys()) == SUMMARY_KEYS


# =====================================================================
# Single recommendation -- manual path
# =====================================================================


@pytest.mark.asyncio
async def test_single_rec_manual_path_sends_approval():
    """One recommendation without auto-execute goes to manual path."""
    ctx = _ctx_with_wait(wait_return=None)  # timeout
    mock_session = MagicMock()
    mock_item = _mock_approval_item(item_id=10)

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_SLACK) as mock_slack_cls,
        patch(_P_SETTINGS) as mock_settings,
    ):
        mock_settings.auto_execute_enabled = False
        slack = mock_slack_cls.return_value
        slack.send_approval_request.return_value = {"ok": True}

        result = await process_recommendations(
            ctx=ctx,
            recommendations=[_make_recommendation()],
            user_id="u1",
            channel_id="C1",
            run_id="run-1",
            source="daily_briefing",
        )

    assert result["sent_for_approval"] == 1
    assert result["auto_executed"] == 0
    slack.send_approval_request.assert_called_once()


# =====================================================================
# Multiple recommendations -- all manual
# =====================================================================


@pytest.mark.asyncio
async def test_multiple_recs_all_manual():
    """Three recs all go to manual path when no role_id."""
    ctx = _ctx_with_wait(wait_return=None)
    mock_session = MagicMock()

    items = [_mock_approval_item(item_id=i) for i in [10, 20, 30]]

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            side_effect=items,
        ),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_SLACK) as mock_slack_cls,
    ):
        slack = mock_slack_cls.return_value
        slack.send_approval_request.return_value = {"ok": True}

        recs = [_make_recommendation(action=f"Action {i}") for i in range(3)]
        result = await process_recommendations(
            ctx=ctx,
            recommendations=recs,
            user_id="u1",
            channel_id="C1",
            run_id="run-1",
            source="test",
        )

    assert result["sent_for_approval"] == 3
    assert result["auto_executed"] == 0
    assert result["expired"] == 3  # all timed out


# =====================================================================
# Auto-execute path
# =====================================================================


def _auto_exec_patches(
    mock_session,
    mock_item,
    auto_decision,
    exec_return=None,
    exec_side_effect=None,
):
    """Return a tuple of context managers for auto-execute tests."""
    exec_kwargs = {"new_callable": AsyncMock}
    if exec_side_effect is not None:
        exec_kwargs["side_effect"] = exec_side_effect
    else:
        exec_kwargs["return_value"] = exec_return or {"ok": True}

    return (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(_P_UPDATE_STATUS, new_callable=AsyncMock),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_RECORD_EXEC, new_callable=AsyncMock),
        patch(
            _P_SHOULD_AUTO,
            new_callable=AsyncMock,
            return_value=auto_decision,
        ),
        patch(_P_REGISTRY, new_callable=AsyncMock),
        patch(_P_SETTINGS),
        patch(_P_EXEC_ACTION, **exec_kwargs),
        patch(_P_SLACK),
    )


def _setup_auto_mocks(patches):
    """Configure mock objects after entering auto-exec patches."""
    # patches is a tuple of patchers; we get mock objects by name
    # But since we use 'with' blocks, we handle it in each test.
    pass


@pytest.mark.asyncio
async def test_auto_execute_when_rules_match():
    """Rec auto-executes when should_auto_execute returns True."""
    ctx = _make_mock_context(run_id="run-auto")
    mock_session = MagicMock()
    mock_item = _mock_approval_item(item_id=5)
    decision = _mock_auto_decision(True)

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(_P_UPDATE_STATUS, new_callable=AsyncMock),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_RECORD_EXEC, new_callable=AsyncMock),
        patch(
            _P_SHOULD_AUTO,
            new_callable=AsyncMock,
            return_value=decision,
        ),
        patch(_P_REGISTRY, new_callable=AsyncMock) as mock_reg_cls,
        patch(_P_SETTINGS) as mock_settings,
        patch(
            _P_EXEC_ACTION,
            new_callable=AsyncMock,
            return_value={"success": True},
        ),
        patch(_P_SLACK) as mock_slack_cls,
    ):
        mock_settings.auto_execute_enabled = True
        mock_reg_cls.return_value.get_rules.return_value = MagicMock()
        mock_slack_cls.return_value.send_alert.return_value = {"ok": True}

        result = await process_recommendations(
            ctx=ctx,
            recommendations=[_make_recommendation()],
            user_id="u1",
            channel_id="C1",
            run_id="run-auto",
            source="skill",
            role_id="media_buyer",
        )

    assert result["auto_executed"] == 1
    assert result["sent_for_approval"] == 0


@pytest.mark.asyncio
async def test_auto_execute_calls_execute_action():
    """Auto-execute path calls _execute_action with correct params."""
    ctx = _make_mock_context(run_id="run-exec")
    mock_session = MagicMock()
    mock_item = _mock_approval_item(item_id=7)

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(_P_UPDATE_STATUS, new_callable=AsyncMock),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_RECORD_EXEC, new_callable=AsyncMock),
        patch(
            _P_SHOULD_AUTO,
            new_callable=AsyncMock,
            return_value=_mock_auto_decision(True),
        ),
        patch(_P_REGISTRY, new_callable=AsyncMock) as mock_reg_cls,
        patch(_P_SETTINGS) as mock_settings,
        patch(
            _P_EXEC_ACTION,
            new_callable=AsyncMock,
            return_value={"done": True},
        ) as mock_exec,
        patch(_P_SLACK) as mock_slack_cls,
    ):
        mock_settings.auto_execute_enabled = True
        mock_reg_cls.return_value.get_rules.return_value = MagicMock()
        mock_slack_cls.return_value.send_alert.return_value = {"ok": True}

        rec = _make_recommendation(action_type="pause_campaign")
        await process_recommendations(
            ctx=ctx,
            recommendations=[rec],
            user_id="u1",
            channel_id="C1",
            run_id="run-exec",
            source="s",
            role_id="buyer",
        )

    mock_exec.assert_called_once_with(
        "pause_campaign",
        rec["action_params"],
        is_auto_approved=True,
    )


@pytest.mark.asyncio
async def test_auto_execute_notifies_slack():
    """Auto-execute path sends Slack notification after execution."""
    ctx = _make_mock_context(run_id="run-notify")
    mock_session = MagicMock()
    mock_item = _mock_approval_item(item_id=8)

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(_P_UPDATE_STATUS, new_callable=AsyncMock),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_RECORD_EXEC, new_callable=AsyncMock),
        patch(
            _P_SHOULD_AUTO,
            new_callable=AsyncMock,
            return_value=_mock_auto_decision(True),
        ),
        patch(_P_REGISTRY, new_callable=AsyncMock) as mock_reg_cls,
        patch(_P_SETTINGS) as mock_settings,
        patch(
            _P_EXEC_ACTION,
            new_callable=AsyncMock,
            return_value={"ok": True},
        ),
        patch(_P_SLACK) as mock_slack_cls,
    ):
        mock_settings.auto_execute_enabled = True
        mock_reg_cls.return_value.get_rules.return_value = MagicMock()
        mock_slack_cls.return_value.send_alert.return_value = {"ok": True}

        await process_recommendations(
            ctx=ctx,
            recommendations=[_make_recommendation()],
            user_id="u1",
            channel_id="C1",
            run_id="run-notify",
            source="s",
            role_id="buyer",
        )

    mock_slack_cls.return_value.send_alert.assert_called_once()


@pytest.mark.asyncio
async def test_auto_execute_failure_records_error():
    """Auto-execute failure records the error in the DB."""
    ctx = _make_mock_context(run_id="run-fail")
    mock_session = MagicMock()
    mock_item = _mock_approval_item(item_id=9)

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(_P_UPDATE_STATUS, new_callable=AsyncMock),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_RECORD_EXEC, new_callable=AsyncMock) as mock_record,
        patch(
            _P_SHOULD_AUTO,
            new_callable=AsyncMock,
            return_value=_mock_auto_decision(True),
        ),
        patch(_P_REGISTRY, new_callable=AsyncMock) as mock_reg_cls,
        patch(_P_SETTINGS) as mock_settings,
        patch(
            _P_EXEC_ACTION,
            new_callable=AsyncMock,
            side_effect=RuntimeError("API down"),
        ),
        patch(_P_SLACK) as mock_slack_cls,
    ):
        mock_settings.auto_execute_enabled = True
        mock_reg_cls.return_value.get_rules.return_value = MagicMock()
        mock_slack_cls.return_value.send_alert.return_value = {"ok": True}

        result = await process_recommendations(
            ctx=ctx,
            recommendations=[_make_recommendation()],
            user_id="u1",
            channel_id="C1",
            run_id="run-fail",
            source="s",
            role_id="buyer",
        )

    # Auto-executed count incremented even on failure (attempted)
    assert result["auto_executed"] == 1
    # record_execution_result called with error
    mock_record.assert_called_with(
        mock_session,
        9,
        execution_error="API down",
    )


# =====================================================================
# Manual path -- approval received
# =====================================================================


@pytest.mark.asyncio
async def test_manual_approval_executes_action():
    """Approved manual recommendation executes the action."""
    approval_event = MagicMock()
    approval_event.data = {
        "status": "approved",
        "decided_by": "user-42",
        "approval_id": "",
    }

    ctx = _ctx_with_wait(wait_return=approval_event)
    mock_session = MagicMock()
    mock_item = _mock_approval_item(item_id=15)

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(
            _P_GET_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(_P_RECORD_EXEC, new_callable=AsyncMock),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_SLACK) as mock_slack_cls,
        patch(
            _P_EXEC_ACTION,
            new_callable=AsyncMock,
            return_value={"success": True},
        ) as mock_exec,
    ):
        slack = mock_slack_cls.return_value
        slack.send_approval_request.return_value = {"ok": True}

        result = await process_recommendations(
            ctx=ctx,
            recommendations=[_make_recommendation()],
            user_id="u1",
            channel_id="C1",
            run_id="run-1",
            source="test",
        )

    assert result["approved"] == 1
    assert result["executed"] == 1
    assert result["rejected"] == 0
    mock_exec.assert_called_once()


# =====================================================================
# Manual path -- rejection
# =====================================================================


@pytest.mark.asyncio
async def test_manual_rejection_no_execution():
    """Rejected recommendation does not execute."""
    rejection_event = MagicMock()
    rejection_event.data = {
        "status": "rejected",
        "decided_by": "user-42",
    }

    ctx = _ctx_with_wait(wait_return=rejection_event)
    mock_session = MagicMock()
    mock_item = _mock_approval_item(item_id=20)

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_SLACK) as mock_slack_cls,
        patch(
            _P_EXEC_ACTION,
            new_callable=AsyncMock,
        ) as mock_exec,
    ):
        slack = mock_slack_cls.return_value
        slack.send_approval_request.return_value = {"ok": True}

        result = await process_recommendations(
            ctx=ctx,
            recommendations=[_make_recommendation()],
            user_id="u1",
            channel_id="C1",
            run_id="run-1",
            source="test",
        )

    assert result["rejected"] == 1
    assert result["executed"] == 0
    mock_exec.assert_not_called()


# =====================================================================
# Manual path -- timeout (None event)
# =====================================================================


@pytest.mark.asyncio
async def test_manual_timeout_counted_as_expired():
    """Timeout (None event) is counted as expired."""
    ctx = _ctx_with_wait(wait_return=None)
    mock_session = MagicMock()
    mock_item = _mock_approval_item(item_id=25)

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_SLACK) as mock_slack_cls,
    ):
        slack = mock_slack_cls.return_value
        slack.send_approval_request.return_value = {"ok": True}

        result = await process_recommendations(
            ctx=ctx,
            recommendations=[_make_recommendation()],
            user_id="u1",
            channel_id="C1",
            run_id="run-1",
            source="test",
        )

    assert result["expired"] == 1
    assert result["approved"] == 0
    assert result["executed"] == 0


# =====================================================================
# Mixed: auto-execute + manual
# =====================================================================


@pytest.mark.asyncio
async def test_mixed_auto_and_manual():
    """Two recs: first auto-executes, second goes manual / times out."""
    ctx = _ctx_with_wait(wait_return=None)
    mock_session = MagicMock()
    item1 = _mock_approval_item(item_id=100)
    item2 = _mock_approval_item(item_id=200)

    # should_auto_execute: True first, False second
    auto_decisions = [
        _mock_auto_decision(True, rule_id="r1"),
        _mock_auto_decision(False),
    ]

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            side_effect=[item1, item2],
        ),
        patch(_P_UPDATE_STATUS, new_callable=AsyncMock),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_RECORD_EXEC, new_callable=AsyncMock),
        patch(
            _P_SHOULD_AUTO,
            new_callable=AsyncMock,
            side_effect=auto_decisions,
        ),
        patch(_P_REGISTRY, new_callable=AsyncMock) as mock_reg_cls,
        patch(_P_SETTINGS) as mock_settings,
        patch(
            _P_EXEC_ACTION,
            new_callable=AsyncMock,
            return_value={"ok": True},
        ),
        patch(_P_SLACK) as mock_slack_cls,
    ):
        mock_settings.auto_execute_enabled = True
        mock_reg_cls.return_value.get_rules.return_value = MagicMock()
        slack = mock_slack_cls.return_value
        slack.send_alert.return_value = {"ok": True}
        slack.send_approval_request.return_value = {"ok": True}

        recs = [
            _make_recommendation(action="Auto action"),
            _make_recommendation(action="Manual action"),
        ]
        result = await process_recommendations(
            ctx=ctx,
            recommendations=recs,
            user_id="u1",
            channel_id="C1",
            run_id="run-mix",
            source="s",
            role_id="buyer",
        )

    assert result["auto_executed"] == 1
    assert result["sent_for_approval"] == 1
    assert result["expired"] == 1


# =====================================================================
# Error handling
# =====================================================================


@pytest.mark.asyncio
async def test_create_approval_exception_handled_gracefully():
    """Exception in create_approval does not crash the flow."""
    ctx = _ctx_with_wait(wait_return=None)
    mock_session = MagicMock()

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB offline"),
        ),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_SLACK) as mock_slack_cls,
    ):
        slack = mock_slack_cls.return_value
        slack.send_approval_request.return_value = {"ok": True}

        result = await process_recommendations(
            ctx=ctx,
            recommendations=[_make_recommendation()],
            user_id="u1",
            channel_id="C1",
            run_id="run-err",
            source="test",
        )

    # Flow continues: sent for manual approval with db_id=0
    assert result["sent_for_approval"] == 1


@pytest.mark.asyncio
async def test_rules_load_exception_falls_back_to_manual():
    """Exception loading auto-execute rules falls back to manual."""
    ctx = _ctx_with_wait(wait_return=None)
    mock_session = MagicMock()
    mock_item = _mock_approval_item(item_id=30)

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(
            _P_REGISTRY,
            new_callable=AsyncMock,
            side_effect=ImportError("missing module"),
        ),
        patch(_P_SETTINGS) as mock_settings,
        patch(_P_SLACK) as mock_slack_cls,
    ):
        mock_settings.auto_execute_enabled = True
        slack = mock_slack_cls.return_value
        slack.send_approval_request.return_value = {"ok": True}

        result = await process_recommendations(
            ctx=ctx,
            recommendations=[_make_recommendation()],
            user_id="u1",
            channel_id="C1",
            run_id="run-fallback",
            source="test",
            role_id="buyer",
        )

    # Falls back to manual since rules could not be loaded
    assert result["sent_for_approval"] == 1
    assert result["auto_executed"] == 0


# =====================================================================
# Summary keys
# =====================================================================


@pytest.mark.asyncio
async def test_summary_has_all_expected_keys_with_data():
    """Non-empty run also has all expected summary keys."""
    ctx = _ctx_with_wait(wait_return=None)
    mock_session = MagicMock()
    mock_item = _mock_approval_item(item_id=50)

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_SLACK) as mock_slack_cls,
    ):
        slack = mock_slack_cls.return_value
        slack.send_approval_request.return_value = {"ok": True}

        result = await process_recommendations(
            ctx=ctx,
            recommendations=[_make_recommendation()],
            user_id="u1",
            channel_id="C1",
            run_id="run-keys",
            source="test",
        )

    assert set(result.keys()) == SUMMARY_KEYS


# =====================================================================
# ctx.step.run call patterns
# =====================================================================


@pytest.mark.asyncio
async def test_step_run_called_for_each_recommendation():
    """ctx.step.run is called for each recommendation create+decide."""
    ctx = _ctx_with_wait(wait_return=None)
    mock_session = MagicMock()
    items = [_mock_approval_item(item_id=i) for i in [60, 61]]

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            side_effect=items,
        ),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_SLACK) as mock_slack_cls,
    ):
        slack = mock_slack_cls.return_value
        slack.send_approval_request.return_value = {"ok": True}

        recs = [
            _make_recommendation(),
            _make_recommendation(action="Second"),
        ]
        await process_recommendations(
            ctx=ctx,
            recommendations=recs,
            user_id="u1",
            channel_id="C1",
            run_id="run-steps",
            source="test",
        )

    # 2 create-approval + 2 send-approval = 4 step.run calls
    names = [c.args[0] for c in ctx.step.run.call_args_list]
    assert "create-approval-0" in names
    assert "create-approval-1" in names
    assert "send-approval-0" in names
    assert "send-approval-1" in names


@pytest.mark.asyncio
async def test_wait_for_event_only_called_for_manual():
    """wait_for_event is called only for manual approvals."""
    ctx = _make_mock_context(run_id="run-wait")
    ctx.step.wait_for_event = AsyncMock(return_value=None)
    mock_session = MagicMock()
    mock_item = _mock_approval_item(item_id=70)

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(_P_UPDATE_STATUS, new_callable=AsyncMock),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_RECORD_EXEC, new_callable=AsyncMock),
        patch(
            _P_SHOULD_AUTO,
            new_callable=AsyncMock,
            return_value=_mock_auto_decision(True),
        ),
        patch(_P_REGISTRY, new_callable=AsyncMock) as mock_reg_cls,
        patch(_P_SETTINGS) as mock_settings,
        patch(
            _P_EXEC_ACTION,
            new_callable=AsyncMock,
            return_value={"ok": True},
        ),
        patch(_P_SLACK) as mock_slack_cls,
    ):
        mock_settings.auto_execute_enabled = True
        mock_reg_cls.return_value.get_rules.return_value = MagicMock()
        mock_slack_cls.return_value.send_alert.return_value = {"ok": True}

        # Auto-execute path only -- no wait_for_event needed
        await process_recommendations(
            ctx=ctx,
            recommendations=[_make_recommendation()],
            user_id="u1",
            channel_id="C1",
            run_id="run-wait",
            source="s",
            role_id="buyer",
        )

    ctx.step.wait_for_event.assert_not_called()


# =====================================================================
# Double-execution prevention
# =====================================================================


@pytest.mark.asyncio
async def test_already_executed_item_is_skipped():
    """Item with executed_at already set is skipped."""
    from datetime import datetime

    approval_event = MagicMock()
    approval_event.data = {
        "status": "approved",
        "decided_by": "user-42",
    }

    ctx = _ctx_with_wait(wait_return=approval_event)
    mock_session = MagicMock()
    mock_item = _mock_approval_item(item_id=80)
    executed_item = _mock_approval_item(
        item_id=80,
        executed_at=datetime.now(),
    )

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(
            _P_GET_APPROVAL,
            new_callable=AsyncMock,
            return_value=executed_item,
        ),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_SLACK) as mock_slack_cls,
        patch(
            _P_EXEC_ACTION,
            new_callable=AsyncMock,
        ) as mock_exec,
    ):
        slack = mock_slack_cls.return_value
        slack.send_approval_request.return_value = {"ok": True}

        result = await process_recommendations(
            ctx=ctx,
            recommendations=[_make_recommendation()],
            user_id="u1",
            channel_id="C1",
            run_id="run-dup",
            source="test",
        )

    assert result["approved"] == 1
    # Skipped due to executed_at, so executed count stays 0
    assert result["executed"] == 0
    mock_exec.assert_not_called()


# =====================================================================
# Execution failure in manual path
# =====================================================================


@pytest.mark.asyncio
async def test_manual_execution_failure():
    """Execution failure in manual path records error in summary."""
    approval_event = MagicMock()
    approval_event.data = {
        "status": "approved",
        "decided_by": "user-42",
    }

    ctx = _ctx_with_wait(wait_return=approval_event)
    mock_session = MagicMock()
    mock_item = _mock_approval_item(item_id=90)

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(
            _P_GET_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(_P_RECORD_EXEC, new_callable=AsyncMock),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_SLACK) as mock_slack_cls,
        patch(
            _P_EXEC_ACTION,
            new_callable=AsyncMock,
            side_effect=RuntimeError("Connector failed"),
        ),
    ):
        slack = mock_slack_cls.return_value
        slack.send_approval_request.return_value = {"ok": True}

        result = await process_recommendations(
            ctx=ctx,
            recommendations=[_make_recommendation()],
            user_id="u1",
            channel_id="C1",
            run_id="run-mfail",
            source="test",
        )

    assert result["approved"] == 1
    assert result["failed"] == 1
    assert result["executed"] == 0
    assert "Connector failed" in result["errors"]


# =====================================================================
# Item not found in DB during execution
# =====================================================================


@pytest.mark.asyncio
async def test_approval_item_not_found_is_skipped():
    """If get_approval_by_id returns None, execution is skipped."""
    approval_event = MagicMock()
    approval_event.data = {
        "status": "approved",
        "decided_by": "user-42",
    }

    ctx = _ctx_with_wait(wait_return=approval_event)
    mock_session = MagicMock()
    mock_item = _mock_approval_item(item_id=95)

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(
            _P_GET_APPROVAL,
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_SLACK) as mock_slack_cls,
        patch(
            _P_EXEC_ACTION,
            new_callable=AsyncMock,
        ) as mock_exec,
    ):
        slack = mock_slack_cls.return_value
        slack.send_approval_request.return_value = {"ok": True}

        result = await process_recommendations(
            ctx=ctx,
            recommendations=[_make_recommendation()],
            user_id="u1",
            channel_id="C1",
            run_id="run-nf",
            source="test",
        )

    assert result["approved"] == 1
    assert result["executed"] == 0
    mock_exec.assert_not_called()


# =====================================================================
# wait_for_event -- approval_id in filter expression
# =====================================================================


@pytest.mark.asyncio
async def test_wait_for_event_uses_correct_approval_id():
    """wait_for_event uses the correct approval_id filter."""
    ctx = _ctx_with_wait(wait_return=None)
    mock_session = MagicMock()
    mock_item = _mock_approval_item(item_id=55)

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_SLACK) as mock_slack_cls,
    ):
        slack = mock_slack_cls.return_value
        slack.send_approval_request.return_value = {"ok": True}

        await process_recommendations(
            ctx=ctx,
            recommendations=[_make_recommendation()],
            user_id="u1",
            channel_id="C1",
            run_id="run-wid",
            source="daily_briefing",
        )

    # Check the wait_for_event call contains the right id
    call_kwargs = ctx.step.wait_for_event.call_args
    expected_id = "daily_briefing-run-wid-55"
    if_exp = call_kwargs.kwargs.get(
        "if_exp",
        call_kwargs[1].get("if_exp", ""),
    )
    assert expected_id in if_exp


# =====================================================================
# No role_id means no auto-execute evaluation
# =====================================================================


@pytest.mark.asyncio
async def test_no_role_id_skips_auto_execute_evaluation():
    """Without role_id, auto-execute rules are not loaded."""
    ctx = _ctx_with_wait(wait_return=None)
    mock_session = MagicMock()
    mock_item = _mock_approval_item(item_id=110)

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_REGISTRY, new_callable=AsyncMock) as mock_reg_cls,
        patch(_P_SLACK) as mock_slack_cls,
    ):
        slack = mock_slack_cls.return_value
        slack.send_approval_request.return_value = {"ok": True}

        result = await process_recommendations(
            ctx=ctx,
            recommendations=[_make_recommendation()],
            user_id="u1",
            channel_id="C1",
            run_id="run-norole",
            source="test",
            # No role_id
        )

    assert result["sent_for_approval"] == 1
    # SkillRegistry not instantiated without role_id
    mock_reg_cls.assert_not_called()


# =====================================================================
# DB_id=0 means execution is skipped
# =====================================================================


@pytest.mark.asyncio
async def test_approved_with_db_id_zero_skips_execution():
    """Approved item with db_id=0 (from failed create) skips exec."""
    approval_event = MagicMock()
    approval_event.data = {
        "status": "approved",
        "decided_by": "user-42",
    }

    ctx = _ctx_with_wait(wait_return=approval_event)
    mock_session = MagicMock()

    # create_approval raises so db_id stays 0
    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB error"),
        ),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_SLACK) as mock_slack_cls,
        patch(
            _P_EXEC_ACTION,
            new_callable=AsyncMock,
        ) as mock_exec,
    ):
        slack = mock_slack_cls.return_value
        slack.send_approval_request.return_value = {"ok": True}

        result = await process_recommendations(
            ctx=ctx,
            recommendations=[_make_recommendation()],
            user_id="u1",
            channel_id="C1",
            run_id="run-zero",
            source="test",
        )

    # Approved but db_id=0 so execution is skipped
    assert result["approved"] == 1
    assert result["executed"] == 0
    mock_exec.assert_not_called()


# =====================================================================
# auto_execute_enabled=False with role_id still goes manual
# =====================================================================


@pytest.mark.asyncio
async def test_role_id_with_auto_execute_disabled():
    """role_id present but auto_execute_enabled=False -> manual."""
    ctx = _ctx_with_wait(wait_return=None)
    mock_session = MagicMock()
    mock_item = _mock_approval_item(item_id=120)

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_REGISTRY, new_callable=AsyncMock) as mock_reg_cls,
        patch(_P_SETTINGS) as mock_settings,
        patch(_P_SLACK) as mock_slack_cls,
    ):
        mock_settings.auto_execute_enabled = False
        mock_reg_cls.return_value.get_rules.return_value = MagicMock()
        slack = mock_slack_cls.return_value
        slack.send_approval_request.return_value = {"ok": True}

        result = await process_recommendations(
            ctx=ctx,
            recommendations=[_make_recommendation()],
            user_id="u1",
            channel_id="C1",
            run_id="run-disabled",
            source="test",
            role_id="buyer",
        )

    assert result["sent_for_approval"] == 1
    assert result["auto_executed"] == 0


# =====================================================================
# Auto-execute: DB status update to AUTO_APPROVED
# =====================================================================


@pytest.mark.asyncio
async def test_auto_execute_updates_status_to_auto_approved():
    """Auto-execute updates the approval status to AUTO_APPROVED."""
    ctx = _make_mock_context(run_id="run-status")
    mock_session = MagicMock()
    mock_item = _mock_approval_item(item_id=130)

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(
            _P_UPDATE_STATUS,
            new_callable=AsyncMock,
        ) as mock_update,
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_RECORD_EXEC, new_callable=AsyncMock),
        patch(
            _P_SHOULD_AUTO,
            new_callable=AsyncMock,
            return_value=_mock_auto_decision(True),
        ),
        patch(_P_REGISTRY, new_callable=AsyncMock) as mock_reg_cls,
        patch(_P_SETTINGS) as mock_settings,
        patch(
            _P_EXEC_ACTION,
            new_callable=AsyncMock,
            return_value={"ok": True},
        ),
        patch(_P_SLACK) as mock_slack_cls,
    ):
        mock_settings.auto_execute_enabled = True
        mock_reg_cls.return_value.get_rules.return_value = MagicMock()
        mock_slack_cls.return_value.send_alert.return_value = {"ok": True}

        await process_recommendations(
            ctx=ctx,
            recommendations=[_make_recommendation()],
            user_id="u1",
            channel_id="C1",
            run_id="run-status",
            source="s",
            role_id="buyer",
        )

    from src.models.schema import ApprovalStatus

    mock_update.assert_called_once_with(
        mock_session,
        130,
        status=ApprovalStatus.AUTO_APPROVED,
        decided_by="auto_execute",
    )


# =====================================================================
# Errors list is capped at 10
# =====================================================================


@pytest.mark.asyncio
async def test_errors_capped_at_10():
    """Errors list in the summary is capped at 10 entries."""
    approval_event = MagicMock()
    approval_event.data = {"status": "approved", "decided_by": "u"}

    ctx = _ctx_with_wait(wait_return=approval_event)
    mock_session = MagicMock()
    items = [_mock_approval_item(item_id=i) for i in range(1, 13)]

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            side_effect=items,
        ),
        patch(
            _P_GET_APPROVAL,
            new_callable=AsyncMock,
            side_effect=items,
        ),
        patch(_P_RECORD_EXEC, new_callable=AsyncMock),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_SLACK) as mock_slack_cls,
        patch(
            _P_EXEC_ACTION,
            new_callable=AsyncMock,
            side_effect=RuntimeError("fail"),
        ),
    ):
        slack = mock_slack_cls.return_value
        slack.send_approval_request.return_value = {"ok": True}

        recs = [_make_recommendation(action=f"Action {i}") for i in range(12)]
        result = await process_recommendations(
            ctx=ctx,
            recommendations=recs,
            user_id="u1",
            channel_id="C1",
            run_id="run-cap",
            source="test",
        )

    assert result["failed"] == 12
    assert len(result["errors"]) == 10  # capped


# =====================================================================
# Recommendation with missing fields uses defaults
# =====================================================================


@pytest.mark.asyncio
async def test_recommendation_with_missing_fields():
    """Rec without action_type or action_params uses defaults."""
    ctx = _ctx_with_wait(wait_return=None)
    mock_session = MagicMock()
    mock_item = _mock_approval_item(item_id=140)

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ) as mock_create,
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_SLACK) as mock_slack_cls,
    ):
        slack = mock_slack_cls.return_value
        slack.send_approval_request.return_value = {"ok": True}

        # Minimal recommendation -- only description
        rec = {"description": "Do something"}
        await process_recommendations(
            ctx=ctx,
            recommendations=[rec],
            user_id="u1",
            channel_id="C1",
            run_id="run-min",
            source="test",
        )

    # create_approval should be called with defaults
    call_kw = mock_create.call_args
    assert call_kw.kwargs["action_type"] == "recommendation_accept"
    assert call_kw.kwargs["action_params"] == {}
    assert call_kw.kwargs["description"] == "Do something"


# =====================================================================
# source and run_id are embedded in approval_id
# =====================================================================


@pytest.mark.asyncio
async def test_approval_id_format():
    """Approval ID follows the format: source-run_id-db_id."""
    ctx = _ctx_with_wait(wait_return=None)
    mock_session = MagicMock()
    mock_item = _mock_approval_item(item_id=77)

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ),
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_SLACK) as mock_slack_cls,
    ):
        slack = mock_slack_cls.return_value
        slack.send_approval_request.return_value = {"ok": True}

        await process_recommendations(
            ctx=ctx,
            recommendations=[_make_recommendation()],
            user_id="u1",
            channel_id="C1",
            run_id="run-fmt",
            source="my_source",
        )

    # Verify send_approval_request was called with the right id
    call_kw = slack.send_approval_request.call_args
    aid = call_kw.kwargs.get(
        "approval_id",
        call_kw[1].get("approval_id", ""),
    )
    if not aid:
        # positional
        aid = call_kw[0][1] if len(call_kw[0]) > 1 else ""
    assert aid == "my_source-run-fmt-77"


# =====================================================================
# account_id and analysis_id passed through
# =====================================================================


@pytest.mark.asyncio
async def test_account_id_and_analysis_id_passed_through():
    """account_id and analysis_id are forwarded to create_approval."""
    ctx = _ctx_with_wait(wait_return=None)
    mock_session = MagicMock()
    mock_item = _mock_approval_item(item_id=150)

    with (
        patch(
            _P_DB_SESSION,
            return_value=_mock_db_session_ctx(mock_session),
        ),
        patch(
            _P_CREATE_APPROVAL,
            new_callable=AsyncMock,
            return_value=mock_item,
        ) as mock_create,
        patch(_P_LOG_EVENT, new_callable=AsyncMock),
        patch(_P_SLACK) as mock_slack_cls,
    ):
        slack = mock_slack_cls.return_value
        slack.send_approval_request.return_value = {"ok": True}

        await process_recommendations(
            ctx=ctx,
            recommendations=[_make_recommendation()],
            user_id="u1",
            channel_id="C1",
            run_id="run-ids",
            source="test",
            account_id=42,
            analysis_id=99,
        )

    call_kw = mock_create.call_args
    assert call_kw.kwargs["account_id"] == 42
    assert call_kw.kwargs["analysis_id"] == 99
