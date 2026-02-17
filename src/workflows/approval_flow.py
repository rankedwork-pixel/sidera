"""Shared approval flow for all Sidera workflows.

Replaces the triplicated approval logic across daily_briefing,
skill_runner, and role_runner workflows.  For each recommendation:

1. Create a DB approval item (status=PENDING)
2. Check auto-execute rules (graduated trust)
3. If auto-execute: update status → AUTO_APPROVED, execute, notify Slack
4. If manual: send Slack approval request, wait for Inngest event, execute

Usage::

    from src.workflows.approval_flow import process_recommendations

    result = await process_recommendations(
        ctx=ctx,
        recommendations=recs,
        user_id="u1",
        channel_id="C123",
        run_id=ctx.run_id,
        source="daily_briefing",
        role_id="media_buyer",      # optional — for auto-execute rules
        analysis_id=42,             # DB analysis ID for FK
    )
"""

from __future__ import annotations

from typing import Any

import inngest
import structlog

logger = structlog.get_logger("sidera.approval_flow")


async def process_recommendations(
    ctx: inngest.Context,
    recommendations: list[dict],
    user_id: str,
    channel_id: str,
    run_id: str,
    source: str,
    *,
    role_id: str = "",
    analysis_id: int | None = None,
    account_id: int = 0,
) -> dict[str, Any]:
    """Process a list of recommendations through the approval pipeline.

    For each recommendation:
    1. Creates a DB approval queue item (PENDING)
    2. Evaluates auto-execute rules (if role has rules and feature enabled)
    3. Auto-approved → execute immediately, notify Slack after
    4. Manual → send approval request to Slack, wait 24h, execute if approved

    Args:
        ctx: Inngest context for step functions.
        recommendations: List of recommendation dicts from agent output.
        user_id: The user who owns the accounts.
        channel_id: Slack channel for notifications.
        run_id: The Inngest run ID.
        source: Workflow source label (e.g. ``"daily_briefing"``).
        role_id: Optional role ID for auto-execute rule lookup.
        analysis_id: Optional DB analysis result ID for FK.
        account_id: Default account ID for approval items.

    Returns:
        Summary dict with counts and details.
    """
    if not recommendations:
        return {
            "auto_executed": 0,
            "sent_for_approval": 0,
            "approved": 0,
            "rejected": 0,
            "expired": 0,
            "executed": 0,
            "failed": 0,
            "errors": [],
        }

    # Load auto-execute rules if available
    ruleset = None
    if role_id:
        try:
            from src.skills.db_loader import load_registry_with_db

            registry = await load_registry_with_db()
            ruleset = registry.get_rules(role_id)
        except Exception as exc:
            logger.warning(
                "approval_flow.rules_load_failed",
                role_id=role_id,
                error=str(exc),
            )

    auto_executed = 0
    sent_for_approval = 0
    approval_ids: list[str] = []
    approval_db_ids: dict[str, int] = {}

    for i, rec in enumerate(recommendations):
        # Step: Create DB approval + decide auto vs manual
        async def create_and_decide(
            rec: dict = rec,
            idx: int = i,
        ) -> dict:
            from src.config import settings as _settings

            db_id = 0
            auto = False

            try:
                from src.db import service as db_service
                from src.db.session import get_db_session

                async with get_db_session() as session:
                    # Resolve steward for this role (if any)
                    steward_id = ""
                    if role_id:
                        try:
                            steward_id = await db_service.resolve_steward(session, role_id) or ""
                        except Exception:
                            pass

                    item = await db_service.create_approval(
                        session=session,
                        analysis_id=analysis_id or 0,
                        user_id=user_id,
                        action_type=rec.get("action_type", "recommendation_accept"),
                        account_id=account_id,
                        description=rec.get("action", rec.get("description", "")),
                        reasoning=rec.get("reasoning", ""),
                        action_params=rec.get("action_params", {}),
                        projected_impact=rec.get("projected_impact"),
                        risk_assessment=rec.get("risk_level"),
                        steward_user_id=steward_id,
                    )
                    db_id = item.id

                    # Evaluate auto-execute rules
                    if ruleset and getattr(
                        _settings,
                        "auto_execute_enabled",
                        False,
                    ):
                        from src.skills.auto_execute import should_auto_execute

                        # Merge recommendation data for condition evaluation
                        eval_data = {**rec, "user_id": user_id}
                        decision = await should_auto_execute(
                            eval_data,
                            role_id,
                            ruleset,
                            _settings,
                            session,
                        )
                        if decision.should_auto_execute:
                            auto = True
                            # Update status to AUTO_APPROVED
                            from src.models.schema import ApprovalStatus

                            await db_service.update_approval_status(
                                session,
                                db_id,
                                status=ApprovalStatus.AUTO_APPROVED,
                                decided_by="auto_execute",
                            )
                            # Set the rule ID on the item
                            item.auto_execute_rule_id = decision.matched_rule_id
                            await session.flush()

                            # Audit log
                            await db_service.log_event(
                                session,
                                user_id=user_id,
                                event_type="auto_execute_approved",
                                event_data={
                                    "approval_id": db_id,
                                    "rule_id": decision.matched_rule_id,
                                    "reasons": list(decision.reasons),
                                    "action_type": rec.get("action_type", ""),
                                },
                                source=source,
                            )

            except Exception as exc:
                logger.warning(
                    "approval_flow.create_failed",
                    error=str(exc),
                    idx=idx,
                )

            approval_id = f"{source}-{run_id}-{db_id or idx}"
            return {
                "db_id": db_id,
                "approval_id": approval_id,
                "auto_execute": auto,
                "steward_id": steward_id,
            }

        decision_result = await ctx.step.run(
            f"create-approval-{i}",
            create_and_decide,
        )

        db_id = decision_result["db_id"]
        approval_id = decision_result["approval_id"]
        approval_db_ids[approval_id] = db_id

        if decision_result["auto_execute"]:
            # Auto-execute path: execute immediately
            async def auto_exec(
                rec: dict = rec,
                db_id: int = db_id,
                approval_id: str = approval_id,
            ) -> dict:
                try:
                    from src.workflows.daily_briefing import _execute_action

                    action_type = rec.get("action_type", "")
                    action_params = rec.get("action_params", {})

                    result = await _execute_action(
                        action_type,
                        action_params,
                        is_auto_approved=True,
                    )

                    # Record execution result
                    from src.db import service as db_service
                    from src.db.session import get_db_session

                    async with get_db_session() as session:
                        await db_service.record_execution_result(
                            session,
                            db_id,
                            execution_result=result,
                        )
                        await db_service.log_event(
                            session,
                            user_id=user_id,
                            event_type="auto_execute_completed",
                            event_data={
                                "approval_id": db_id,
                                "action_type": action_type,
                                "success": True,
                            },
                            source=source,
                        )

                    return {"executed": True, "result": result}

                except Exception as exc:
                    logger.error(
                        "approval_flow.auto_exec_failed",
                        approval_id=db_id,
                        error=str(exc),
                    )
                    try:
                        from src.db import service as db_service
                        from src.db.session import get_db_session

                        async with get_db_session() as session:
                            await db_service.record_execution_result(
                                session,
                                db_id,
                                execution_error=str(exc),
                            )
                    except Exception:
                        pass
                    return {"executed": False, "error": str(exc)}

            await ctx.step.run(f"auto-execute-{i}", auto_exec)

            # Notify Slack about auto-execution (post-execution)
            async def notify_auto_exec(
                rec: dict = rec,
                db_id: int = db_id,
            ) -> dict:
                try:
                    from src.connectors.slack import SlackConnector

                    slack = SlackConnector()
                    return slack.send_alert(
                        channel_id=channel_id or None,
                        alert_type="auto_executed",
                        message=(
                            f":robot_face: Auto-Executed: "
                            f"{rec.get('action_type', 'action')}\n"
                            f"_{rec.get('action', rec.get('description', ''))}_"
                        ),
                        details={
                            "reasoning": rec.get("reasoning", ""),
                            "projected_impact": rec.get("projected_impact", ""),
                            "approval_id": db_id,
                        },
                    )
                except Exception as exc:
                    logger.warning(
                        "approval_flow.auto_notify_failed",
                        error=str(exc),
                    )
                    return {"ok": False}

            await ctx.step.run(f"notify-auto-exec-{i}", notify_auto_exec)
            auto_executed += 1

        else:
            # Manual approval path: send Slack request
            _steward_id = decision_result.get("steward_id", "")

            async def send_approval(
                rec: dict = rec,
                approval_id: str = approval_id,
                steward_id: str = _steward_id,
            ) -> dict:
                from src.connectors.slack import SlackConnector

                slack = SlackConnector()

                # Include diff text for skill/role proposals
                diff_text = ""
                if rec.get("action_type") in ("skill_proposal", "role_proposal"):
                    diff_text = rec.get("action_params", {}).get("diff", "")

                # Include task preview for Claude Code tasks
                task_preview = ""
                if rec.get("action_type") == "claude_code_task":
                    params = rec.get("action_params", {})
                    task_preview = (
                        f"Skill: {params.get('skill_name', params.get('skill_id', ''))}\n"
                        f"Prompt: {params.get('prompt') or '(skill default)'}\n"
                        f"Budget: ${params.get('max_budget_usd', 5.0):.2f}\n"
                        f"Permission: {params.get('permission_mode', 'acceptEdits')}"
                    )

                # Build steward @mention for the approval message
                steward_mention = f"<@{steward_id}>" if steward_id else ""

                result = slack.send_approval_request(
                    channel_id=channel_id or None,
                    approval_id=approval_id,
                    action_type=rec.get("action_type", rec.get("action", "unknown")),
                    description=rec.get("action", rec.get("description", "")),
                    reasoning=rec.get("reasoning", ""),
                    projected_impact=rec.get("projected_impact", ""),
                    risk_level=rec.get("risk_level", "unknown"),
                    diff_text=diff_text,
                    task_preview=task_preview,
                    steward_mention=steward_mention,
                )
                return {"approval_id": approval_id, "slack_result": result}

            await ctx.step.run(f"send-approval-{i}", send_approval)
            approval_ids.append(approval_id)
            sent_for_approval += 1

    # Wait for manual approvals
    decisions: dict[str, dict] = {}
    for approval_id in approval_ids:
        event = await ctx.step.wait_for_event(
            f"wait-approval-{approval_id}",
            event="sidera/approval.decided",
            if_exp=f"event.data.approval_id == '{approval_id}'",
            timeout=86_400_000,  # 24 hours
        )
        if event is not None:
            decisions[approval_id] = {
                "status": event.data.get("status", "unknown"),
                "decided_by": event.data.get("decided_by", ""),
            }
        else:
            decisions[approval_id] = {"status": "expired"}

    # Execute approved actions
    approved = 0
    rejected = 0
    expired = 0
    executed = 0
    failed = 0
    errors: list[str] = []

    for approval_id, decision in decisions.items():
        status = decision.get("status", "")
        if status == "approved":
            approved += 1
        elif status == "rejected":
            rejected += 1
        else:
            expired += 1
            continue

        if status != "approved":
            continue

        db_id = approval_db_ids.get(approval_id, 0)
        if db_id == 0:
            continue

        async def execute_action(
            db_id: int = db_id,
            aid: str = approval_id,
        ) -> dict:
            try:
                from src.db import service as db_service
                from src.db.session import get_db_session

                async with get_db_session() as session:
                    item = await db_service.get_approval_by_id(session, db_id)
                    if item is None or item.executed_at is not None:
                        return {"skipped": True}

                    at = item.action_type.value if item.action_type else "unknown"
                    ap = item.action_params or {}

                    await db_service.log_event(
                        session,
                        user_id=user_id,
                        event_type="action_execution_started",
                        event_data={
                            "approval_id": db_id,
                            "action_type": at,
                        },
                        source=source,
                    )

                from src.workflows.daily_briefing import _execute_action

                result = await _execute_action(at, ap)

                async with get_db_session() as session:
                    await db_service.record_execution_result(
                        session,
                        db_id,
                        execution_result=result,
                    )
                    await db_service.log_event(
                        session,
                        user_id=user_id,
                        event_type="action_execution_completed",
                        event_data={
                            "approval_id": db_id,
                            "action_type": at,
                            "success": True,
                        },
                        source=source,
                    )
                return {"executed": True}

            except Exception as exc:
                logger.error(
                    "approval_flow.execute_failed",
                    approval_id=db_id,
                    error=str(exc),
                )
                try:
                    from src.db import service as db_service
                    from src.db.session import get_db_session

                    async with get_db_session() as session:
                        await db_service.record_execution_result(
                            session,
                            db_id,
                            execution_error=str(exc),
                        )
                except Exception:
                    pass
                return {"executed": False, "error": str(exc)}

        exec_result = await ctx.step.run(
            f"execute-action-{approval_id}",
            execute_action,
        )
        if exec_result.get("skipped"):
            continue
        if exec_result.get("executed"):
            executed += 1
        else:
            failed += 1
            if exec_result.get("error"):
                errors.append(exec_result["error"])

    return {
        "auto_executed": auto_executed,
        "sent_for_approval": sent_for_approval,
        "approved": approved,
        "rejected": rejected,
        "expired": expired,
        "executed": executed,
        "failed": failed,
        "errors": errors[:10],
    }
