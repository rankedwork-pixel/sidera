"""Inngest durable functions for Sidera's daily analysis cycle.

Contains seventeen workflows:

1. **daily_briefing_workflow** — Runs at 7 AM weekdays. Pulls account
   data, runs the SideraAgent analysis, sends the briefing to Slack,
   posts interactive approval requests for each recommendation, waits
   up to 24 hours for human decisions, and logs results.

2. **cost_monitor_workflow** — Runs every 30 minutes. Checks cumulative
   LLM costs for the day and sends a Slack alert if usage exceeds 80 %
   of the daily limit.

3. **skill_runner_workflow** — Triggered by ``sidera/skill.run`` event.
   Loads a skill from the registry, executes it via SkillExecutor,
   saves results, sends output to Slack, and optionally sends approval
   requests and chains to a follow-up skill.

4. **skill_scheduler_workflow** — Runs every minute. Checks for skills
   and roles with cron schedules that should fire and emits events.

5. **token_refresh_workflow** — Runs at 5 AM daily. Proactively refreshes
   OAuth tokens expiring within 7 days and alerts on failures.

6. **role_runner_workflow** — Triggered by ``sidera/role.run`` event.
   Runs all briefing_skills for a role via RoleExecutor and sends the
   combined output to Slack.

7. **department_runner_workflow** — Triggered by ``sidera/department.run``
   event. Runs all roles in a department via DepartmentExecutor.

8. **manager_runner_workflow** — Triggered by ``sidera/manager.run``
   event. Runs a manager role: own skills, delegation decision,
   sub-role execution, synthesis, and approval flow.

9. **conversation_turn_workflow** — Triggered by ``sidera/conversation.turn``
   event. Handles a single conversation turn: loads thread context,
   checks limits, builds role context, runs the agent, posts reply.

10. **meeting_join_workflow** — Triggered by ``sidera/meeting.join``
    event. Validates the role has voice capability, joins the meeting
    via MeetingSessionManager, and starts the real-time audio pipeline.

11. **meeting_end_workflow** — Triggered by ``sidera/meeting.ended``
    event. Summarizes the transcript, extracts action items, posts a
    meeting summary to Slack, and optionally triggers manager delegation.

12. **data_retention_workflow** — Runs at 3 AM daily. Purges expired
    data based on configurable retention periods.

13. **heartbeat_runner_workflow** — Triggered by ``sidera/heartbeat.run``
    event. Runs a proactive heartbeat check-in for a role: checks
    cooldown, loads context + pending messages, runs the agent with an
    open-ended investigative prompt, posts findings to Slack, and logs
    the result.

14. **memory_consolidation_workflow** — Runs at 4 AM every Sunday.
    Iterates over all (user_id, role_id) pairs with unconsolidated
    memories, batches them, runs a Haiku call to identify duplicates
    and overlaps, and merges them into consolidated memory entries.

15. **claude_code_task_workflow** — Triggered by ``sidera/claude_code.task``
    event. Executes a Sidera skill as a headless Claude Code instance
    with full agentic capabilities. Loads context, runs the task,
    saves results to DB, records cost, and optionally notifies Slack.

16. **event_reactor_workflow** — Triggered by ``sidera/webhook.received``
    event. Processes inbound webhook events from external monitoring
    sources (Google Ads Scripts, Meta, BigQuery, custom). Classifies
    severity, sends Slack alerts, and optionally triggers an agent
    investigation (heartbeat-style) for high/critical events.

17. **working_group_workflow** — Triggered by ``sidera/working_group.run``
    event. Runs a multi-agent working group: creates DB session, runs
    coordinator planning LLM call, executes member tasks sequentially
    with checkpointing, synthesizes results, and posts to Slack.

All functions use Inngest's step primitives (``step.run``,
``step.wait_for_event``) so that each stage is memoized and
automatically retried on transient failures.
"""

from __future__ import annotations

from typing import Any

import inngest
import structlog

from src.workflows.inngest_client import inngest_client

_workflow_logger = structlog.get_logger("sidera.workflows")

# =====================================================================
# Claude Code task execution helper
# =====================================================================


async def _execute_claude_code_task(
    action_params: dict[str, Any],
) -> dict[str, Any]:
    """Execute an approved Claude Code task.

    Reads ``skill_id``, ``prompt``, ``max_budget_usd``, and
    ``permission_mode`` from *action_params* and delegates to
    :class:`ClaudeCodeTaskManager`.

    Returns:
        Result dict with ``success``, ``output_text``, ``cost_usd``, etc.
    """
    from src.config import settings as cfg

    if not cfg.claude_code_enabled:
        return {"error": "Claude Code execution is disabled", "success": False}

    skill_id = action_params.get("skill_id", "")
    prompt = action_params.get("prompt", "")
    max_budget = float(action_params.get("max_budget_usd", cfg.claude_code_default_budget_usd))
    permission_mode = action_params.get("permission_mode", cfg.claude_code_default_permission_mode)
    role_id = action_params.get("role_id", "")
    user_id = action_params.get("user_id", "claude_code")

    if not skill_id:
        return {"error": "Missing skill_id", "success": False}

    # Enforce hard ceiling
    max_budget = min(max_budget, cfg.claude_code_max_budget_usd)

    from src.skills.db_loader import load_registry_with_db

    registry = await load_registry_with_db()
    skill = registry.get_skill(skill_id)
    if not skill:
        return {"error": f"Skill '{skill_id}' not found", "success": False}

    # Build role context if available
    role_context = ""
    memory_context = ""
    if role_id:
        role = registry.get_role(role_id)
        if role:
            dept = registry.get_department(role.department_id)
            from src.skills.executor import compose_role_context

            role_context = compose_role_context(department=dept, role=role, registry=registry)

    from src.claude_code.task_manager import ClaudeCodeTaskManager

    manager = ClaudeCodeTaskManager(
        max_concurrent=cfg.claude_code_max_concurrent,
    )
    result = await manager.run_task_sync(
        skill=skill,
        prompt=prompt,
        user_id=user_id,
        role_context=role_context,
        memory_context=memory_context,
        role_id=role_id,
        department_id=skill.department_id,
        max_budget_usd=max_budget,
        permission_mode=permission_mode,
    )

    return {
        "success": not result.is_error,
        "output_text": result.output_text[:20000],
        "cost_usd": result.cost_usd,
        "num_turns": result.num_turns,
        "duration_ms": result.duration_ms,
        "is_error": result.is_error,
        "error_message": result.error_message,
    }


# =====================================================================
# Shared execution helper — routes approved actions to connectors
# =====================================================================


async def _execute_action(
    action_type: str,
    action_params: dict[str, Any],
    is_auto_approved: bool = False,
) -> dict[str, Any]:
    """Route an approved action to the correct connector write method.

    Reads ``action_params["platform"]`` to decide whether to call the
    Google Ads or Meta connector.  Returns the connector result dict.

    Budget safety cap (``max_budget_change_ratio``) is only enforced for
    auto-approved actions. Human-approved actions skip the cap because
    the human has already reviewed and explicitly approved the change.

    Args:
        action_type: The ``ActionType`` value (e.g. ``"budget_change"``).
        action_params: Full parameters from the approval queue item.
        is_auto_approved: If True, enforce budget cap. If False (human
            approved), skip the cap.

    Returns:
        Result dict from the connector write method.

    Raises:
        ValueError: If the platform or action_type is not recognized.
    """
    # Skill evolution proposals — no platform, routes to DB write
    if action_type == "skill_proposal":
        from src.skills.evolution import execute_skill_proposal

        return await execute_skill_proposal(action_params)

    # Role evolution proposals — no platform, routes to DB write
    if action_type == "role_proposal":
        from src.skills.role_evolution import execute_role_proposal

        return await execute_role_proposal(action_params)

    # Claude Code task execution — no platform, routes to task manager
    if action_type == "claude_code_task":
        return await _execute_claude_code_task(action_params)

    platform = action_params.get("platform", "")

    if platform == "google_ads":
        from src.connectors.google_ads import GoogleAdsConnector

        connector = GoogleAdsConnector()
        customer_id = action_params["customer_id"]
        campaign_id = action_params.get("campaign_id", "")

        if action_type in ("budget_change", "update_budget"):
            return connector.update_campaign_budget(
                customer_id,
                campaign_id,
                int(action_params["new_budget_micros"]),
                validate_cap=is_auto_approved,
            )
        elif action_type in ("pause_campaign",):
            return connector.update_campaign_status(
                customer_id,
                campaign_id,
                "PAUSED",
            )
        elif action_type in ("enable_campaign",):
            return connector.update_campaign_status(
                customer_id,
                campaign_id,
                "ENABLED",
            )
        elif action_type in ("bid_change", "update_bid_target"):
            return connector.update_bid_strategy_target(
                customer_id,
                campaign_id,
                target_cpa_micros=action_params.get("target_cpa_micros"),
                target_roas=action_params.get("target_roas"),
            )
        elif action_type == "add_negative_keywords":
            return connector.add_negative_keywords(
                customer_id,
                campaign_id,
                action_params["keywords"],
            )
        elif action_type == "update_ad_schedule":
            return connector.update_ad_schedule(
                customer_id,
                campaign_id,
                action_params["schedule"],
            )
        elif action_type == "update_geo_bid_modifier":
            return connector.update_geo_bid_modifier(
                customer_id,
                campaign_id,
                int(action_params["geo_target_id"]),
                float(action_params["bid_modifier"]),
            )
        elif action_type == "create_campaign":
            return connector.create_campaign(
                customer_id,
                name=action_params["name"],
                channel_type=action_params.get("channel_type", "SEARCH"),
                daily_budget_micros=int(action_params["daily_budget_micros"]),
                status=action_params.get("status", "PAUSED"),
                bidding_strategy=action_params.get(
                    "bidding_strategy",
                    "MAXIMIZE_CLICKS",
                ),
            )
        else:
            raise ValueError(f"Unsupported Google Ads action: {action_type}")

    elif platform == "meta":
        from src.connectors.meta import MetaConnector

        connector = MetaConnector()
        account_id = action_params["account_id"]

        if action_type in ("budget_change", "update_budget"):
            return connector.update_campaign_budget(
                account_id,
                action_params["campaign_id"],
                int(action_params["new_budget_cents"]),
                action_params.get("budget_type", "daily"),
                validate_cap=is_auto_approved,
            )
        elif action_type in ("pause_campaign",):
            return connector.update_campaign_status(
                account_id,
                action_params["campaign_id"],
                "PAUSED",
            )
        elif action_type in ("enable_campaign",):
            return connector.update_campaign_status(
                account_id,
                action_params["campaign_id"],
                "ACTIVE",
            )
        elif action_type == "pause_ad_set":
            return connector.update_adset_status(
                account_id,
                action_params["adset_id"],
                "PAUSED",
            )
        elif action_type == "update_ad_status":
            return connector.update_ad_status(
                account_id,
                action_params["ad_id"],
                action_params.get("status", "PAUSED"),
            )
        elif action_type == "update_adset_budget":
            return connector.update_adset_budget(
                account_id,
                action_params["adset_id"],
                int(action_params["new_budget_cents"]),
                action_params.get("budget_type", "daily"),
                validate_cap=is_auto_approved,
            )
        elif action_type == "update_adset_bid":
            return connector.update_adset_bid(
                account_id,
                action_params["adset_id"],
                int(action_params["bid_amount_cents"]),
            )
        else:
            raise ValueError(f"Unsupported Meta action: {action_type}")

    else:
        raise ValueError(f"Unknown platform: {platform}")


# =====================================================================
# Evidence snapshot — capture state before executing actions
# =====================================================================


def _capture_evidence_snapshot(
    action_type: str,
    action_params: dict[str, Any],
) -> dict[str, Any]:
    """Capture current-state metrics before executing an approved action.

    The snapshot is stored alongside the execution result in the audit
    trail, making each audit entry self-contained (you can see what the
    state was *before* the action, not just after).

    Non-fatal: returns ``{"capture_error": ...}`` on any failure.

    Args:
        action_type: The action being executed (e.g. ``"budget_change"``).
        action_params: Full parameters from the approval queue item.

    Returns:
        Snapshot dict with captured metrics and a ``captured_at`` timestamp.
    """
    from datetime import datetime, timezone

    try:
        snapshot: dict[str, Any] = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "action_type": action_type,
        }

        if action_type in ("budget_change", "update_budget", "update_adset_budget"):
            snapshot["current_budget"] = action_params.get(
                "current_budget_micros",
                action_params.get("current_budget", action_params.get("current_budget_cents")),
            )
            snapshot["proposed_budget"] = action_params.get(
                "new_budget_micros",
                action_params.get("new_budget", action_params.get("new_budget_cents")),
            )

        elif action_type in ("pause_campaign", "enable_campaign"):
            snapshot["current_status"] = action_params.get("current_status", "unknown")
            snapshot["campaign_id"] = action_params.get("campaign_id", "")

        elif action_type in ("bid_change", "update_bid_target"):
            snapshot["current_cpa_micros"] = action_params.get("current_cpa_micros")
            snapshot["current_roas"] = action_params.get("current_roas")

        snapshot["platform"] = action_params.get("platform", "")
        return snapshot

    except Exception as exc:
        return {
            "capture_error": str(exc),
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }


# =====================================================================
# Pre-action lesson check — query role lessons for warnings
# =====================================================================


async def _check_lessons_before_action(
    role_id: str,
    user_id: str,
    action_type: str,
    action_params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Query role's lessons for warnings relevant to the pending action.

    Searches for lesson memories that mention the action type or platform,
    returning up to 5 relevant lessons. Non-fatal — returns empty on error.

    Args:
        role_id: The role that produced the recommendation.
        user_id: The advertiser user ID.
        action_type: The action being executed.
        action_params: Full parameters from the approval queue.

    Returns:
        List of relevant lesson dicts (title, content snippet, confidence).
    """
    try:
        from src.db import service as db_service
        from src.db.session import get_db_session

        platform = action_params.get("platform", "")
        search_terms = [action_type]
        if platform:
            search_terms.append(platform)

        warnings: list[dict[str, Any]] = []

        async with get_db_session() as session:
            for term in search_terms:
                lessons = await db_service.search_role_memories(
                    session,
                    user_id=user_id,
                    role_id=role_id,
                    memory_type="lesson",
                    keyword=term,
                    limit=5,
                )
                for lesson in lessons:
                    content = getattr(lesson, "content", "") or ""
                    title = getattr(lesson, "title", "") or ""
                    confidence = getattr(lesson, "confidence", 0.0) or 0.0

                    if not any(w.get("title") == title for w in warnings):
                        warnings.append(
                            {
                                "title": title,
                                "content": content[:300],
                                "confidence": confidence,
                            }
                        )

        return warnings[:5]

    except Exception as exc:
        _workflow_logger.warning(
            "lesson_check.error",
            role_id=role_id,
            error=str(exc),
        )
        return []


# =====================================================================
# Daily briefing workflow
# =====================================================================


@inngest_client.create_function(
    fn_id="sidera-daily-briefing",
    name="Sidera Daily Briefing",
    trigger=inngest.TriggerCron(cron="0 7 * * MON-FRI"),
    retries=2,
)
async def daily_briefing_workflow(ctx: inngest.Context) -> dict:
    """Daily performance analysis -> Slack briefing -> approval flow."""
    try:
        user_id = ctx.event.data.get("user_id", "default")
        channel_id = ctx.event.data.get("channel_id", "")
        force_refresh = ctx.event.data.get("force_refresh", False)

        # Step 1: Load account config — try DB first, fall back to event data
        async def load_accounts() -> dict:
            try:
                from src.db import service as db_service
                from src.db.session import get_db_session

                async with get_db_session() as session:
                    db_accounts = await db_service.get_accounts_for_user(session, user_id)
                    if db_accounts:
                        return {
                            "accounts": [
                                {
                                    "platform": (
                                        a.platform.value
                                        if hasattr(a.platform, "value")
                                        else a.platform
                                    ),
                                    "account_id": a.platform_account_id,
                                    "account_name": a.account_name or "",
                                    "target_roas": (
                                        float(a.target_roas) if a.target_roas else None
                                    ),
                                    "target_cpa": (float(a.target_cpa) if a.target_cpa else None),
                                    "monthly_budget_cap": (
                                        float(a.monthly_budget_cap)
                                        if a.monthly_budget_cap
                                        else None
                                    ),
                                }
                                for a in db_accounts
                            ],
                            "source": "database",
                        }
            except Exception as exc:
                from src.middleware.sentry_setup import capture_exception

                capture_exception(exc)
                # Fall through to event data
            # Fall back to event data
            return {
                "accounts": ctx.event.data.get("accounts", []),
                "source": "event",
            }

        account_data = await ctx.step.run("load-accounts", load_accounts)
        accounts = account_data["accounts"]

        if not accounts:
            raise inngest.NonRetriableError("No accounts configured for analysis")

        # Step 1b: Check if today's briefing already exists (deduplication)
        async def check_existing_briefing() -> dict:
            if force_refresh:
                return {"exists": False}
            try:
                from datetime import date

                from src.db import service as db_service
                from src.db.session import get_db_session

                async with get_db_session() as session:
                    existing = await db_service.get_analyses_for_period(
                        session, user_id, date.today(), date.today()
                    )
                    if existing:
                        latest = existing[-1]
                        return {
                            "exists": True,
                            "briefing_text": latest.briefing_content or "",
                            "recommendations": latest.recommendations or [],
                            "cost": {},
                            "session_id": "",
                        }
            except Exception as exc:
                from src.middleware.sentry_setup import capture_exception

                capture_exception(exc)
                # Fall through to fresh analysis on DB errors
            return {"exists": False}

        cached_result = await ctx.step.run("check-existing-briefing", check_existing_briefing)

        # Step 2: Run agent analysis (skip if today's briefing already exists)
        if cached_result["exists"]:
            analysis = cached_result
        else:

            async def run_analysis() -> dict:
                from src.agent.core import SideraAgent

                agent = SideraAgent()
                result = await agent.run_daily_briefing_optimized(
                    user_id=user_id,
                    account_ids=accounts,
                    force_refresh=force_refresh,
                )
                return {
                    "briefing_text": result.briefing_text,
                    "recommendations": result.recommendations,
                    "cost": result.cost,
                    "session_id": result.session_id,
                    "degradation_status": result.degradation_status,
                }

            analysis = await ctx.step.run("run-analysis", run_analysis)

        # Step 2b: Save analysis result to database (skip if deduplicated)
        async def save_to_db() -> dict:
            if cached_result.get("exists"):
                return {"analysis_id": None, "saved": False, "skipped": True}
            try:
                from datetime import date

                from src.db import service as db_service
                from src.db.session import get_db_session

                async with get_db_session() as session:
                    result = await db_service.save_analysis_result(
                        session=session,
                        user_id=user_id,
                        run_date=date.today(),
                        briefing_content=analysis["briefing_text"],
                        recommendations=analysis.get("recommendations", []),
                        cost_info=analysis.get("cost", {}),
                        accounts_analyzed=[a.get("account_id", "") for a in accounts],
                    )
                    # Record LLM cost
                    cost = analysis.get("cost", {})
                    if cost.get("total_cost_usd"):
                        # Determine model label — optimized runs use
                        # multiple models, so record as "multi-model"
                        model_label = (
                            "multi-model"
                            if cost.get("phases")
                            else cost.get("model", "claude-sonnet-4-5-20250929")
                        )
                        await db_service.record_cost(
                            session=session,
                            user_id=user_id,
                            run_date=date.today(),
                            model=model_label,
                            cost_usd=cost["total_cost_usd"],
                            operation="daily_analysis",
                        )
                    # Log audit event
                    await db_service.log_event(
                        session=session,
                        user_id=user_id,
                        event_type="analysis_run",
                        event_data={
                            "session_id": analysis.get("session_id", ""),
                            "cost": cost,
                        },
                        source="daily_briefing",
                    )
                    return {"analysis_id": result.id, "saved": True}
            except Exception as exc:
                from src.middleware.sentry_setup import capture_exception

                capture_exception(exc)
                return {"analysis_id": None, "saved": False, "error": str(exc)}

        await ctx.step.run("save-to-db", save_to_db)

        # Step 3: Send briefing to Slack
        async def send_briefing() -> dict:
            from src.connectors.slack import SlackConnector

            slack = SlackConnector()

            briefing_text = analysis["briefing_text"]
            # Prepend stale data warning if degradation occurred
            if analysis.get("degradation_status") == "stale":
                from datetime import date as _date

                briefing_text = (
                    ":warning: *STALE DATA* — Phase 1 data collection failed. "
                    f"Showing most recent analysis from {_date.today().isoformat()}.\n\n"
                    + briefing_text
                )

            return slack.send_briefing(
                channel_id=channel_id or None,
                briefing_text=briefing_text,
                recommendations=analysis.get("recommendations", []),
            )

        briefing_result = await ctx.step.run("send-briefing", send_briefing)

        # Step 4: Send approval requests for each recommendation
        approval_ids: list[str] = []
        for i, rec in enumerate(analysis.get("recommendations", [])):

            async def send_approval(rec: dict = rec, idx: int = i) -> dict:
                from src.connectors.slack import SlackConnector

                slack = SlackConnector()
                approval_id = f"approval-{ctx.run_id}-{idx}"
                result = slack.send_approval_request(
                    channel_id=channel_id or None,
                    approval_id=approval_id,
                    action_type=rec.get("action", "unknown"),
                    description=rec.get("action", ""),
                    reasoning=rec.get("reasoning", ""),
                    projected_impact=rec.get("projected_impact", ""),
                    risk_level=rec.get("risk_level", "unknown"),
                )
                return {"approval_id": approval_id, "slack_result": result}

            approval_result = await ctx.step.run(f"send-approval-{i}", send_approval)
            approval_ids.append(approval_result["approval_id"])

        # Step 5: Wait for approvals (24-hour timeout)
        decisions: dict[str, dict] = {}
        for approval_id in approval_ids:
            event = await ctx.step.wait_for_event(
                f"wait-approval-{approval_id}",
                event="sidera/approval.decided",
                if_exp=f"event.data.approval_id == '{approval_id}'",
                timeout=86_400_000,  # 24 hours in ms
            )
            if event is not None:
                decisions[approval_id] = {
                    "status": event.data.get("status", "unknown"),
                    "decided_by": event.data.get("decided_by", ""),
                }
            else:
                decisions[approval_id] = {"status": "expired"}

        # Step 6: Execute approved actions
        async def execute_approved_actions() -> dict:
            executed = 0
            skipped = 0
            failed = 0
            errors: list[str] = []

            for approval_id_str, decision in decisions.items():
                if decision.get("status") != "approved":
                    skipped += 1
                    continue

                # Extract DB approval ID from the string
                parts = approval_id_str.split("-")
                db_approval_id = int(parts[-1]) if parts[-1].isdigit() else 0
                if db_approval_id == 0:
                    skipped += 1
                    continue

                try:
                    from src.db import service as db_service
                    from src.db.session import get_db_session

                    async with get_db_session() as session:
                        item = await db_service.get_approval_by_id(
                            session,
                            db_approval_id,
                        )
                        if item is None or item.executed_at is not None:
                            skipped += 1
                            continue

                        action_type = item.action_type.value if item.action_type else "unknown"
                        action_params = item.action_params or {}

                        # Log start
                        await db_service.log_event(
                            session,
                            user_id=user_id,
                            event_type="action_execution_started",
                            event_data={
                                "approval_id": db_approval_id,
                                "action_type": action_type,
                            },
                            source="daily_briefing",
                        )

                    # Capture pre-action evidence snapshot
                    evidence_snapshot = _capture_evidence_snapshot(action_type, action_params)

                    # Execute outside the session
                    result = await _execute_action(
                        action_type,
                        action_params,
                    )

                    # Attach evidence snapshot to result
                    if isinstance(result, dict):
                        result["evidence_snapshot"] = evidence_snapshot

                    # Record success
                    async with get_db_session() as session:
                        await db_service.record_execution_result(
                            session,
                            db_approval_id,
                            execution_result=result,
                        )
                        await db_service.log_event(
                            session,
                            user_id=user_id,
                            event_type="action_execution_completed",
                            event_data={
                                "approval_id": db_approval_id,
                                "action_type": action_type,
                                "success": True,
                                "result": result,
                            },
                            source="daily_briefing",
                        )
                    executed += 1

                except Exception as exc:
                    _workflow_logger.error(
                        "execute_action.failed",
                        approval_id=db_approval_id,
                        error=str(exc),
                    )
                    failed += 1
                    errors.append(str(exc))
                    try:
                        from src.db import service as db_service
                        from src.db.session import get_db_session

                        async with get_db_session() as session:
                            await db_service.record_execution_result(
                                session,
                                db_approval_id,
                                execution_error=str(exc),
                            )
                    except Exception:
                        pass  # Don't fail recording

            return {
                "executed": executed,
                "skipped": skipped,
                "failed": failed,
                "errors": errors[:10],
            }

        execution_summary = await ctx.step.run(
            "execute-approved-actions",
            execute_approved_actions,
        )

        # Step 6b: Notify execution results on Slack
        if execution_summary["executed"] > 0 or execution_summary["failed"] > 0:

            async def notify_execution() -> dict:
                from src.connectors.slack import SlackConnector

                slack = SlackConnector()
                msg = (
                    f"Action execution summary: "
                    f"{execution_summary['executed']} executed, "
                    f"{execution_summary['skipped']} skipped, "
                    f"{execution_summary['failed']} failed"
                )
                return slack.send_alert(
                    channel_id=channel_id or None,
                    alert_type="execution_summary",
                    message=msg,
                    details=execution_summary,
                )

            await ctx.step.run("notify-execution", notify_execution)

        # Step 7: Log results and persist approval decisions to DB
        async def log_results() -> dict:
            try:
                from src.db import service as db_service
                from src.db.session import get_db_session
                from src.models.schema import ApprovalStatus

                async with get_db_session() as session:
                    for approval_id, decision in decisions.items():
                        if decision.get("status") in ("approved", "rejected"):
                            status = (
                                ApprovalStatus.APPROVED
                                if decision["status"] == "approved"
                                else ApprovalStatus.REJECTED
                            )
                            parts = approval_id.split("-")
                            db_approval_id = int(parts[-1]) if parts[-1].isdigit() else 0
                            await db_service.update_approval_status(
                                session=session,
                                approval_id=db_approval_id,
                                status=status,
                                decided_by=decision.get("decided_by", ""),
                            )
            except Exception as exc:
                from src.middleware.sentry_setup import capture_exception

                capture_exception(exc)
                # Don't fail the workflow for DB errors
            return {
                "user_id": user_id,
                "briefing_sent": briefing_result.get("ok", False),
                "approvals_sent": len(approval_ids),
                "decisions": decisions,
                "execution": execution_summary,
                "cost": analysis.get("cost", {}),
            }

        result = await ctx.step.run("log-results", log_results)
        return result
    except inngest.NonRetriableError:
        raise  # Let Inngest handle these
    except Exception as dlq_exc:
        from src.middleware.sentry_setup import capture_exception

        capture_exception(dlq_exc)
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.record_failed_run(
                    session=session,
                    workflow_name="daily_briefing",
                    event_name=ctx.event.name,
                    event_data=ctx.event.data,
                    error_message=str(dlq_exc),
                    error_type=type(dlq_exc).__name__,
                    user_id=ctx.event.data.get("user_id", ""),
                    run_id=ctx.run_id,
                )
        except Exception:
            pass  # Don't fail the DLQ recording itself
        raise  # Re-raise so Inngest retries


# =====================================================================
# Cost monitor workflow
# =====================================================================


@inngest_client.create_function(
    fn_id="sidera-cost-monitor",
    name="Sidera Cost Monitor",
    trigger=inngest.TriggerCron(cron="*/30 * * * *"),
    retries=1,
)
async def cost_monitor_workflow(ctx: inngest.Context) -> dict:
    """Monitor LLM costs and send alerts if approaching limits."""
    try:

        async def check_costs() -> dict:
            try:
                from datetime import date

                from src.config import settings
                from src.db import service as db_service
                from src.db.session import get_db_session

                async with get_db_session() as session:
                    total = await db_service.get_daily_cost_all_users(session, date.today())
                    return {
                        "total_cost_today": float(total),
                        "limit": float(settings.max_llm_cost_per_account_per_day),
                        "accounts_checked": 1,
                        "source": "database",
                    }
            except Exception:
                return {
                    "total_cost_today": 0.0,
                    "limit": 10.0,
                    "accounts_checked": 0,
                    "source": "fallback",
                }

        costs = await ctx.step.run("check-costs", check_costs)

        # Alert if over 80 % of daily limit
        if costs["total_cost_today"] > costs["limit"] * 0.8:

            async def send_cost_alert() -> dict:
                from src.connectors.slack import SlackConnector

                slack = SlackConnector()
                pct = costs["total_cost_today"] / costs["limit"] * 100
                return slack.send_alert(
                    channel_id=None,
                    alert_type="cost_warning",
                    message=(
                        f"LLM cost alert: ${costs['total_cost_today']:.2f} "
                        f"of ${costs['limit']:.2f} daily limit used "
                        f"({pct:.0f}%)"
                    ),
                    details=costs,
                )

            await ctx.step.run("send-cost-alert", send_cost_alert)

        return costs
    except inngest.NonRetriableError:
        raise  # Let Inngest handle these
    except Exception as dlq_exc:
        from src.middleware.sentry_setup import capture_exception

        capture_exception(dlq_exc)
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.record_failed_run(
                    session=session,
                    workflow_name="cost_monitor",
                    event_name=ctx.event.name,
                    event_data=ctx.event.data,
                    error_message=str(dlq_exc),
                    error_type=type(dlq_exc).__name__,
                    user_id=ctx.event.data.get("user_id", ""),
                    run_id=ctx.run_id,
                )
        except Exception:
            pass  # Don't fail the DLQ recording itself
        raise  # Re-raise so Inngest retries


# =====================================================================
# Skill runner workflow
# =====================================================================

# Maximum chain depth to prevent runaway recursive skill triggers
_MAX_CHAIN_DEPTH = 5


@inngest_client.create_function(
    fn_id="sidera-skill-runner",
    name="Sidera Skill Runner",
    trigger=inngest.TriggerEvent(event="sidera/skill.run"),
    retries=2,
)
async def skill_runner_workflow(ctx: inngest.Context) -> dict:
    """Generic skill execution → Slack delivery → approval → chaining.

    Expected event data:
    - skill_id (str, required): Which skill to run
    - user_id (str, required): Advertiser user ID
    - channel_id (str, optional): Slack channel for output
    - params (dict, optional): Extra runtime parameters for the skill
    - chain_depth (int, optional): Current depth in a chain (default 0)
    """
    try:
        skill_id = ctx.event.data.get("skill_id", "")
        user_id = ctx.event.data.get("user_id", "default")
        channel_id = ctx.event.data.get("channel_id", "")
        params = ctx.event.data.get("params", {})
        chain_depth = ctx.event.data.get("chain_depth", 0)

        if not skill_id:
            raise inngest.NonRetriableError("skill_id is required in event data")

        if chain_depth >= _MAX_CHAIN_DEPTH:
            raise inngest.NonRetriableError(
                f"Skill chain depth limit reached ({_MAX_CHAIN_DEPTH}). "
                f"Stopping at skill '{skill_id}'."
            )

        # Step 1: Load accounts (same pattern as daily briefing)
        async def load_accounts() -> dict:
            try:
                from src.db import service as db_service
                from src.db.session import get_db_session

                async with get_db_session() as session:
                    db_accounts = await db_service.get_accounts_for_user(session, user_id)
                    if db_accounts:
                        return {
                            "accounts": [
                                {
                                    "platform": (
                                        a.platform.value
                                        if hasattr(a.platform, "value")
                                        else a.platform
                                    ),
                                    "account_id": a.platform_account_id,
                                    "account_name": a.account_name or "",
                                    "target_roas": (
                                        float(a.target_roas) if a.target_roas else None
                                    ),
                                    "target_cpa": (float(a.target_cpa) if a.target_cpa else None),
                                    "monthly_budget_cap": (
                                        float(a.monthly_budget_cap)
                                        if a.monthly_budget_cap
                                        else None
                                    ),
                                }
                                for a in db_accounts
                            ],
                            "source": "database",
                        }
            except Exception as exc:
                from src.middleware.sentry_setup import capture_exception

                capture_exception(exc)
            return {
                "accounts": ctx.event.data.get("accounts", []),
                "source": "event",
            }

        account_data = await ctx.step.run("load-accounts", load_accounts)
        accounts = account_data["accounts"]

        if not accounts:
            raise inngest.NonRetriableError("No accounts configured for skill execution")

        # Step 2: Execute skill
        async def execute_skill() -> dict:
            from src.agent.core import SideraAgent
            from src.skills.db_loader import load_registry_with_db
            from src.skills.executor import SkillExecutor

            registry = await load_registry_with_db()
            agent = SideraAgent()
            executor = SkillExecutor(agent=agent, registry=registry)

            result = await executor.execute(
                skill_id=skill_id,
                user_id=user_id,
                accounts=accounts,
                params=params,
            )
            return {
                "skill_id": result.skill_id,
                "output_text": result.output_text,
                "recommendations": result.recommendations,
                "cost": result.cost,
                "session_id": result.session_id,
                "chain_next": result.chain_next,
                "requires_approval": registry.get(skill_id).requires_approval
                if registry.get(skill_id)
                else True,
            }

        execution = await ctx.step.run("execute-skill", execute_skill)

        # Step 3: Save results to DB
        async def save_to_db() -> dict:
            try:
                from datetime import date

                from src.db import service as db_service
                from src.db.session import get_db_session

                async with get_db_session() as session:
                    result = await db_service.save_skill_result(
                        session=session,
                        user_id=user_id,
                        skill_id=skill_id,
                        run_date=date.today(),
                        briefing_content=execution["output_text"],
                        recommendations=execution.get("recommendations", []),
                        cost_info=execution.get("cost", {}),
                        accounts_analyzed=[a.get("account_id", "") for a in accounts],
                    )
                    # Record LLM cost
                    cost = execution.get("cost", {})
                    if cost.get("total_cost_usd"):
                        await db_service.record_cost(
                            session=session,
                            user_id=user_id,
                            run_date=date.today(),
                            model=cost.get("model", "claude-sonnet-4-5-20250929"),
                            cost_usd=cost["total_cost_usd"],
                            operation=f"skill_{skill_id}",
                        )
                    # Log audit event
                    await db_service.log_skill_event(
                        session=session,
                        user_id=user_id,
                        skill_id=skill_id,
                        event_type="skill_run",
                        event_data={
                            "session_id": execution.get("session_id", ""),
                            "cost": cost,
                            "chain_depth": chain_depth,
                        },
                    )
                    return {"analysis_id": result.id, "saved": True}
            except Exception as exc:
                from src.middleware.sentry_setup import capture_exception

                capture_exception(exc)
                return {"analysis_id": None, "saved": False, "error": str(exc)}

        await ctx.step.run("save-to-db", save_to_db)

        # Step 4: Send output to Slack
        async def send_output() -> dict:
            from src.connectors.slack import SlackConnector

            slack = SlackConnector()
            return slack.send_briefing(
                channel_id=channel_id or None,
                briefing_text=execution["output_text"],
                recommendations=execution.get("recommendations", []),
            )

        slack_result = await ctx.step.run("send-output", send_output)

        # Step 5: Approval flow (only if skill requires approval)
        decisions: dict[str, dict] = {}
        if execution.get("requires_approval"):
            approval_ids: list[str] = []
            for i, rec in enumerate(execution.get("recommendations", [])):

                async def send_approval(rec: dict = rec, idx: int = i) -> dict:
                    from src.connectors.slack import SlackConnector

                    slack = SlackConnector()
                    approval_id = f"skill-{skill_id}-{ctx.run_id}-{idx}"
                    result = slack.send_approval_request(
                        channel_id=channel_id or None,
                        approval_id=approval_id,
                        action_type=rec.get("action", "unknown"),
                        description=rec.get("action", ""),
                        reasoning=rec.get("reasoning", ""),
                        projected_impact=rec.get("projected_impact", ""),
                        risk_level=rec.get("risk_level", "unknown"),
                    )
                    return {"approval_id": approval_id, "slack_result": result}

                approval_result = await ctx.step.run(f"send-approval-{i}", send_approval)
                approval_ids.append(approval_result["approval_id"])

            # Wait for approvals (24-hour timeout)
            for approval_id in approval_ids:
                event = await ctx.step.wait_for_event(
                    f"wait-approval-{approval_id}",
                    event="sidera/approval.decided",
                    if_exp=f"event.data.approval_id == '{approval_id}'",
                    timeout=86_400_000,  # 24 hours in ms
                )
                if event is not None:
                    decisions[approval_id] = {
                        "status": event.data.get("status", "unknown"),
                        "decided_by": event.data.get("decided_by", ""),
                    }
                else:
                    decisions[approval_id] = {"status": "expired"}

        # Step 5b: Execute approved actions
        skill_execution_summary: dict[str, Any] = {
            "executed": 0,
            "skipped": 0,
            "failed": 0,
            "errors": [],
        }
        if decisions:

            async def execute_skill_actions() -> dict:
                executed = 0
                skipped = 0
                failed = 0
                errors: list[str] = []

                for aid_str, decision in decisions.items():
                    if decision.get("status") != "approved":
                        skipped += 1
                        continue

                    parts = aid_str.split("-")
                    db_id = int(parts[-1]) if parts[-1].isdigit() else 0
                    if db_id == 0:
                        skipped += 1
                        continue

                    try:
                        from src.db import service as db_service
                        from src.db.session import get_db_session

                        async with get_db_session() as session:
                            item = await db_service.get_approval_by_id(
                                session,
                                db_id,
                            )
                            if item is None or item.executed_at is not None:
                                skipped += 1
                                continue

                            at = item.action_type.value if item.action_type else "unknown"
                            ap = item.action_params or {}

                        evidence_snapshot = _capture_evidence_snapshot(at, ap)
                        result = await _execute_action(at, ap)
                        if isinstance(result, dict):
                            result["evidence_snapshot"] = evidence_snapshot

                        async with get_db_session() as session:
                            await db_service.record_execution_result(
                                session,
                                db_id,
                                execution_result=result,
                            )
                        executed += 1

                    except Exception as exc:
                        _workflow_logger.error(
                            "skill_execute_action.failed",
                            approval_id=db_id,
                            error=str(exc),
                        )
                        failed += 1
                        errors.append(str(exc))
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

                return {
                    "executed": executed,
                    "skipped": skipped,
                    "failed": failed,
                    "errors": errors[:10],
                }

            skill_execution_summary = await ctx.step.run(
                "execute-approved-actions",
                execute_skill_actions,
            )

        # Step 6: Chain to next skill (if approved and chain_after is set)
        chain_next = execution.get("chain_next")
        chained = False
        if chain_next:
            # Chain only if all approvals were approved (or no approvals needed)
            all_approved = not decisions or all(
                d.get("status") == "approved" for d in decisions.values()
            )
            if all_approved:

                async def trigger_chain() -> dict:
                    # Merge previous output into params for pipeline
                    chain_params = dict(params) if params else {}
                    output_text = execution.get("output_text", "")
                    if output_text:
                        chain_params["previous_output"] = output_text[:4000]

                    await inngest_client.send(
                        inngest.Event(
                            name="sidera/skill.run",
                            data={
                                "skill_id": chain_next,
                                "user_id": user_id,
                                "channel_id": channel_id,
                                "chain_depth": chain_depth + 1,
                                "params": chain_params,
                            },
                        )
                    )
                    return {
                        "chained_to": chain_next,
                        "depth": chain_depth + 1,
                    }

                await ctx.step.run("trigger-chain", trigger_chain)
                chained = True

        return {
            "skill_id": skill_id,
            "user_id": user_id,
            "output_sent": slack_result.get("ok", False),
            "approvals_sent": len(decisions),
            "decisions": decisions,
            "execution": skill_execution_summary,
            "chained": chained,
            "chain_next": chain_next if chained else None,
            "cost": execution.get("cost", {}),
        }
    except inngest.NonRetriableError:
        raise  # Let Inngest handle these
    except Exception as dlq_exc:
        from src.middleware.sentry_setup import capture_exception

        capture_exception(dlq_exc)
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.record_failed_run(
                    session=session,
                    workflow_name="skill_runner",
                    event_name=ctx.event.name,
                    event_data=ctx.event.data,
                    error_message=str(dlq_exc),
                    error_type=type(dlq_exc).__name__,
                    user_id=ctx.event.data.get("user_id", ""),
                    run_id=ctx.run_id,
                )
        except Exception:
            pass  # Don't fail the DLQ recording itself
        raise  # Re-raise so Inngest retries


# =====================================================================
# Skill scheduler workflow
# =====================================================================


@inngest_client.create_function(
    fn_id="sidera-skill-scheduler",
    name="Sidera Skill Scheduler",
    trigger=inngest.TriggerCron(cron="* * * * *"),
    retries=1,
)
async def skill_scheduler_workflow(ctx: inngest.Context) -> dict:
    """Check for scheduled skills that should run and dispatch them.

    Runs every minute. Loads the skill registry, finds skills with cron
    schedules, checks if they should run now, and emits
    ``sidera/skill.run`` events for each.

    Note: In production, schedule matching should use a more sophisticated
    approach (e.g., croniter library). This implementation uses a simple
    minute-level check.
    """
    try:

        async def check_and_dispatch() -> dict:
            from src.config import settings
            from src.skills.db_loader import load_registry_with_db

            registry = await load_registry_with_db()
            count = len(registry)
            scheduled = registry.list_scheduled()

            dispatched = 0
            try:
                from datetime import datetime, timezone

                now = datetime.now(timezone.utc)

                # Dispatch scheduled skills
                for skill in scheduled:
                    if _cron_matches_now(skill.schedule, now):
                        await inngest_client.send(
                            inngest.Event(
                                name="sidera/skill.run",
                                data={
                                    "skill_id": skill.id,
                                    "user_id": ctx.event.data.get("user_id", "default"),
                                    "channel_id": ctx.event.data.get("channel_id", ""),
                                    "params": {},
                                    "chain_depth": 0,
                                },
                            )
                        )
                        dispatched += 1

                # Dispatch scheduled roles
                scheduled_roles = [r for r in registry.list_roles() if r.schedule is not None]
                for role in scheduled_roles:
                    if _cron_matches_now(role.schedule, now):
                        # Resolve department-scoped Slack channel
                        role_channel = ctx.event.data.get("channel_id", "")
                        if role.department_id:
                            dept = registry.get_department(role.department_id)
                            if dept and dept.slack_channel_id:
                                role_channel = dept.slack_channel_id
                        await inngest_client.send(
                            inngest.Event(
                                name="sidera/role.run",
                                data={
                                    "role_id": role.id,
                                    "user_id": ctx.event.data.get("user_id", "default"),
                                    "channel_id": role_channel,
                                },
                            )
                        )
                        dispatched += 1

                # Dispatch heartbeat runs (proactive check-ins)
                if settings.heartbeat_enabled:
                    heartbeat_roles = [
                        r
                        for r in registry.list_roles()
                        if getattr(r, "heartbeat_schedule", None) is not None
                    ]
                    for role in heartbeat_roles:
                        if _cron_matches_now(role.heartbeat_schedule, now):
                            # Resolve department-scoped Slack channel
                            hb_channel = ctx.event.data.get("channel_id", "")
                            if role.department_id:
                                dept = registry.get_department(role.department_id)
                                if dept and dept.slack_channel_id:
                                    hb_channel = dept.slack_channel_id
                            await inngest_client.send(
                                inngest.Event(
                                    name="sidera/heartbeat.run",
                                    data={
                                        "role_id": role.id,
                                        "user_id": "heartbeat",
                                        "channel_id": hb_channel,
                                    },
                                )
                            )
                            dispatched += 1

            except Exception as exc:
                from src.middleware.sentry_setup import capture_exception

                capture_exception(exc)

            return {
                "loaded": count,
                "scheduled_skills": len(scheduled),
                "scheduled_roles": len([r for r in registry.list_roles() if r.schedule is not None])
                if registry.role_count
                else 0,
                "dispatched": dispatched,
            }

        result = await ctx.step.run("check-and-dispatch", check_and_dispatch)
        return result
    except inngest.NonRetriableError:
        raise  # Let Inngest handle these
    except Exception as dlq_exc:
        from src.middleware.sentry_setup import capture_exception

        capture_exception(dlq_exc)
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.record_failed_run(
                    session=session,
                    workflow_name="skill_scheduler",
                    event_name=ctx.event.name,
                    event_data=ctx.event.data,
                    error_message=str(dlq_exc),
                    error_type=type(dlq_exc).__name__,
                    user_id=ctx.event.data.get("user_id", ""),
                    run_id=ctx.run_id,
                )
        except Exception:
            pass  # Don't fail the DLQ recording itself
        raise  # Re-raise so Inngest retries


# =====================================================================
# Token refresh workflow
# =====================================================================


@inngest_client.create_function(
    fn_id="sidera-token-refresh",
    name="Sidera Token Refresh",
    trigger=inngest.TriggerCron(cron="0 5 * * *"),
    retries=2,
)
async def token_refresh_workflow(ctx: inngest.Context) -> dict:
    """Proactively refresh OAuth tokens expiring within 7 days.

    Runs at 5 AM daily (2 hours before the briefing). Queries the
    accounts table for tokens expiring within 7 days, refreshes each
    one via the appropriate platform token endpoint, and saves the
    new tokens (encrypted) back to the database.

    If any refreshes fail, sends a Slack alert so the human can
    intervene before the 7 AM briefing.
    """

    async def check_and_refresh() -> dict:
        from src.db import service as db_service
        from src.db.session import get_db_session

        refreshed = 0
        failed = 0
        errors: list[str] = []

        async with get_db_session() as session:
            accounts = await db_service.get_accounts_expiring_soon(session, within_days=7)

            for account in accounts:
                try:
                    new_tokens = await _refresh_oauth_token(account)
                    if new_tokens:
                        await db_service.update_account_tokens(
                            session,
                            account.id,
                            access_token=new_tokens["access_token"],
                            refresh_token=new_tokens.get(
                                "refresh_token",
                                account.oauth_refresh_token or "",
                            ),
                            expires_at=new_tokens.get("expires_at"),
                        )
                        refreshed += 1
                except Exception as exc:
                    from src.middleware.sentry_setup import capture_exception

                    capture_exception(exc)
                    failed += 1
                    errors.append(f"{account.platform}:{account.platform_account_id}: {exc}")

        return {
            "refreshed": refreshed,
            "failed": failed,
            "errors": errors[:10],  # Cap error detail list
        }

    result = await ctx.step.run("check-and-refresh", check_and_refresh)

    # Alert on failures
    if result["failed"] > 0:

        async def send_alert() -> dict:
            from src.connectors.slack import SlackConnector

            slack = SlackConnector()
            return slack.send_alert(
                channel_id=None,
                alert_type="token_refresh_failed",
                message=(f"Token refresh: {result['refreshed']} OK, {result['failed']} failed"),
                details={
                    "refreshed": result["refreshed"],
                    "failed": result["failed"],
                    "errors": result["errors"],
                },
            )

        await ctx.step.run("alert-on-failure", send_alert)

    return result


async def _refresh_oauth_token(account) -> dict | None:
    """Refresh an OAuth token for the given account.

    Calls the appropriate token endpoint based on the account's platform:
    - Google (google_ads, google_drive): POST to ``googleapis.com/token``
    - Meta: GET ``graph.facebook.com/v18.0/oauth/access_token``

    Args:
        account: An Account model instance with platform, oauth_refresh_token,
            and related fields.

    Returns:
        Dict with ``access_token``, optional ``refresh_token``, and optional
        ``expires_at`` (as a datetime), or None if the platform is not
        supported for refresh.
    """
    from datetime import datetime, timezone
    from datetime import timedelta as _td

    import httpx

    from src.config import settings
    from src.utils.encryption import decrypt_token, encrypt_token

    platform = (
        account.platform.value if hasattr(account.platform, "value") else str(account.platform)
    )

    if platform in ("google_ads", "google_drive"):
        refresh_token = decrypt_token(account.oauth_refresh_token or "")
        if not refresh_token:
            return None

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": settings.google_ads_client_id,
                    "client_secret": settings.google_ads_client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        expires_in = data.get("expires_in", 3600)
        return {
            "access_token": encrypt_token(data["access_token"]),
            "refresh_token": encrypt_token(data.get("refresh_token", refresh_token)),
            "expires_at": datetime.now(timezone.utc) + _td(seconds=expires_in),
        }

    if platform == "meta":
        access_token = decrypt_token(account.oauth_access_token or "")
        if not access_token:
            return None

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://graph.facebook.com/v18.0/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": settings.meta_app_id,
                    "client_secret": settings.meta_app_secret,
                    "fb_exchange_token": access_token,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        expires_in = data.get("expires_in", 5_184_000)  # 60 days default
        return {
            "access_token": encrypt_token(data["access_token"]),
            "expires_at": datetime.now(timezone.utc) + _td(seconds=expires_in),
        }

    return None  # Unsupported platform


def _cron_matches_now(cron_expr: str | None, now) -> bool:
    """Cron expression matcher for minute-level scheduling.

    Supports standard 5-field cron: minute hour day month weekday.
    Handles: ``*``, exact values, steps (``*/15``), ranges (``1-5``),
    comma-separated lists (``0,15,30,45``), and combinations (``1-5/2``).
    Day names (``MON``-``SUN``) are supported for the weekday field.
    Weekday uses 0=Monday through 6=Sunday.

    Args:
        cron_expr: Cron expression string (e.g., ``"*/15 7-18 * * 1-5"``).
        now: datetime object for the current time.

    Returns:
        True if the cron expression matches the current minute.
    """
    if not cron_expr:
        return False

    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return False

    minute, hour, day, month, weekday = parts

    # Day name mapping
    day_names = {
        "MON": 0,
        "TUE": 1,
        "WED": 2,
        "THU": 3,
        "FRI": 4,
        "SAT": 5,
        "SUN": 6,
    }

    def _resolve_name(token: str, names: dict | None) -> int | None:
        """Convert a day name to its numeric value, or parse as int."""
        if names and token.upper() in names:
            return names[token.upper()]
        try:
            return int(token)
        except ValueError:
            return None

    def _expand_field(field: str, names: dict | None = None) -> set[int] | None:
        """Expand a cron field into a set of matching values.

        Returns None for ``*`` (matches everything) or a set of ints.
        """
        # Handle comma-separated lists first
        if "," in field:
            result: set[int] = set()
            for part in field.split(","):
                expanded = _expand_field(part.strip(), names)
                if expanded is None:
                    return None  # One part is *, matches everything
                result |= expanded
            return result

        # Handle */N (step from start)
        if field.startswith("*/"):
            try:
                step = int(field[2:])
                if step <= 0:
                    return set()
                return {v for v in range(60) if v % step == 0}
            except ValueError:
                return set()

        # Handle range: A-B or A-B/N
        if "-" in field:
            step = 1
            range_part = field
            if "/" in field:
                range_part, step_str = field.split("/", 1)
                try:
                    step = int(step_str)
                except ValueError:
                    return set()

            range_parts = range_part.split("-", 1)
            if len(range_parts) != 2:
                return set()

            start = _resolve_name(range_parts[0], names)
            end = _resolve_name(range_parts[1], names)
            if start is None or end is None:
                return set()

            if start <= end:
                return set(range(start, end + 1, step))
            else:
                # Wrap-around (e.g., 5-1 for weekdays)
                return set(range(start, 7, step)) | set(range(0, end + 1, step))

        # Plain * (matches everything)
        if field == "*":
            return None

        # Single value
        val = _resolve_name(field, names)
        if val is not None:
            return {val}
        return set()

    def _matches(field: str, value: int, names: dict | None = None) -> bool:
        allowed = _expand_field(field, names)
        if allowed is None:
            return True  # * matches everything
        return value in allowed

    return (
        _matches(minute, now.minute)
        and _matches(hour, now.hour)
        and _matches(day, now.day)
        and _matches(month, now.month)
        and _matches(weekday, now.weekday(), day_names)
    )


# =====================================================================
# Role runner workflow
# =====================================================================


@inngest_client.create_function(
    fn_id="sidera-role-runner",
    name="Sidera Role Runner",
    trigger=inngest.TriggerEvent(event="sidera/role.run"),
    retries=2,
)
async def role_runner_workflow(ctx: inngest.Context) -> dict:
    """Run all briefing_skills for a role via RoleExecutor.

    Expected event data:
    - role_id (str, required): Which role to run
    - user_id (str, required): Advertiser user ID
    - channel_id (str, optional): Slack channel for output
    """
    try:
        role_id = ctx.event.data.get("role_id", "")
        user_id = ctx.event.data.get("user_id", "default")
        channel_id = ctx.event.data.get("channel_id", "")

        if not role_id:
            raise inngest.NonRetriableError("role_id is required in event data")

        # Step 1: Load accounts
        async def load_accounts() -> dict:
            try:
                from src.db import service as db_service
                from src.db.session import get_db_session

                async with get_db_session() as session:
                    db_accounts = await db_service.get_accounts_for_user(session, user_id)
                    if db_accounts:
                        return {
                            "accounts": [
                                {
                                    "platform": (
                                        a.platform.value
                                        if hasattr(a.platform, "value")
                                        else a.platform
                                    ),
                                    "account_id": a.platform_account_id,
                                    "account_name": a.account_name or "",
                                    "target_roas": (
                                        float(a.target_roas) if a.target_roas else None
                                    ),
                                    "target_cpa": (float(a.target_cpa) if a.target_cpa else None),
                                    "monthly_budget_cap": (
                                        float(a.monthly_budget_cap)
                                        if a.monthly_budget_cap
                                        else None
                                    ),
                                }
                                for a in db_accounts
                            ],
                            "source": "database",
                        }
            except Exception as exc:
                from src.middleware.sentry_setup import capture_exception

                capture_exception(exc)
            return {
                "accounts": ctx.event.data.get("accounts", []),
                "source": "event",
            }

        account_data = await ctx.step.run("load-accounts", load_accounts)
        accounts = account_data["accounts"]

        if not accounts:
            raise inngest.NonRetriableError("No accounts configured for role execution")

        # Step 2: Load role memory + pending messages
        async def load_role_memory() -> dict:
            try:
                from src.db import service as db_service
                from src.db.session import get_db_session
                from src.mcp_servers.messaging import compose_message_context
                from src.skills.memory import (
                    compose_memory_context,
                    filter_superseded_memories,
                )

                async with get_db_session() as session:
                    memories = await db_service.get_role_memories(
                        session,
                        user_id,
                        role_id,
                        min_confidence=0.5,
                        limit=20,
                    )
                    superseded = await db_service.get_superseded_memory_ids(
                        session,
                        user_id,
                        role_id,
                    )
                    memories = filter_superseded_memories(memories, superseded)

                    # Load inter-agent relationship memories
                    agent_memories = await db_service.get_agent_relationship_memories(
                        session, role_id, limit=5
                    )
                    all_memories = list(memories) + list(agent_memories)

                    composed = compose_memory_context(all_memories)

                    # Load pending peer messages
                    pending_msgs = await db_service.get_pending_messages(
                        session,
                        role_id,
                        limit=10,
                    )
                    message_context = compose_message_context(pending_msgs)

                    # Mark as delivered
                    if pending_msgs:
                        msg_ids = [m.id for m in pending_msgs]
                        await db_service.mark_messages_delivered(
                            session,
                            msg_ids,
                        )

                    return {
                        "memory_context": composed,
                        "memory_count": len(all_memories),
                        "message_context": message_context,
                        "message_count": len(pending_msgs),
                    }
            except Exception as exc:
                _workflow_logger.warning(
                    "role_runner.memory_load_failed",
                    error=str(exc),
                )
                return {
                    "memory_context": "",
                    "memory_count": 0,
                    "message_context": "",
                    "message_count": 0,
                }

        memory_data = await ctx.step.run(
            "load-role-memory",
            load_role_memory,
        )

        # Step 3: Execute role
        async def execute_role() -> dict:
            from src.agent.core import SideraAgent
            from src.mcp_servers.evolution import (
                clear_proposer_context,
                set_proposer_context,
            )
            from src.mcp_servers.messaging import (
                clear_messaging_context,
                set_messaging_context,
            )
            from src.skills.db_loader import load_registry_with_db
            from src.skills.executor import RoleExecutor, SkillExecutor

            registry = await load_registry_with_db()
            agent = SideraAgent()
            skill_executor = SkillExecutor(agent=agent, registry=registry)
            role_executor = RoleExecutor(
                skill_executor=skill_executor,
                registry=registry,
            )

            # Resolve department_id for messaging context.
            # get_role() is sync on SkillRegistry so we call it directly.
            _dept_id = ""
            try:
                _role_def = registry.get_role(role_id)
                if _role_def and hasattr(_role_def, "department_id"):
                    _dept_id = _role_def.department_id or ""
            except Exception:
                pass

            set_messaging_context(role_id, str(_dept_id), registry)
            set_proposer_context(role_id, str(_dept_id))
            try:
                result = await role_executor.execute_role(
                    role_id=role_id,
                    user_id=user_id,
                    accounts=accounts,
                    memory_context=memory_data.get("memory_context", ""),
                    pending_messages=memory_data.get("message_context", ""),
                )
            finally:
                clear_messaging_context()
                clear_proposer_context()
            # Derive role name from the combined output header or role_id.
            # The combined output starts with "# <Role Name> — Briefing".
            role_name = role_id
            combined = result.combined_output or ""
            if combined.startswith("# ") and " — " in combined:
                role_name = combined.split(" — ", 1)[0][2:].strip()

            # Pass role principles for reflection linking
            _principles: tuple[str, ...] = ()
            if _role_def and hasattr(_role_def, "principles"):
                _principles = _role_def.principles or ()

            # Aggregate tool errors from all skill runs
            all_tool_errors = [err for sr in result.skill_results for err in sr.tool_errors]

            return {
                "role_id": result.role_id,
                "department_id": result.department_id,
                "role_name": role_name,
                "combined_output": result.combined_output,
                "total_cost": result.total_cost,
                "session_id": result.session_id,
                "skills_run": len(result.skill_results),
                "skill_ids": [sr.skill_id for sr in result.skill_results],
                "principles": list(_principles),
                "recommendations": [
                    rec for sr in result.skill_results for rec in sr.recommendations
                ],
                "tool_errors": all_tool_errors,
            }

        execution = await ctx.step.run("execute-role", execute_role)

        # Step 3: Save to DB
        async def save_to_db() -> dict:
            try:
                from datetime import date

                from src.db import service as db_service
                from src.db.session import get_db_session

                async with get_db_session() as session:
                    result = await db_service.save_analysis_result(
                        session=session,
                        user_id=user_id,
                        run_date=date.today(),
                        briefing_content=execution["combined_output"],
                        recommendations=execution.get("recommendations", []),
                        cost_info=execution.get("total_cost", {}),
                        accounts_analyzed=[a.get("account_id", "") for a in accounts],
                        skill_id=f"role:{role_id}",
                    )
                    cost = execution.get("total_cost", {})
                    if cost.get("total_cost_usd"):
                        await db_service.record_cost(
                            session=session,
                            user_id=user_id,
                            run_date=date.today(),
                            model="multi-model",
                            cost_usd=cost["total_cost_usd"],
                            operation=f"role_{role_id}",
                        )
                    await db_service.log_event(
                        session=session,
                        user_id=user_id,
                        event_type="role_run",
                        event_data={
                            "role_id": role_id,
                            "department_id": execution.get("department_id", ""),
                            "skills_run": execution.get("skills_run", 0),
                            "cost": cost,
                        },
                        source="role_runner",
                    )
                    return {"analysis_id": result.id, "saved": True}
            except Exception as exc:
                from src.middleware.sentry_setup import capture_exception

                capture_exception(exc)
                return {"analysis_id": None, "saved": False, "error": str(exc)}

        await ctx.step.run("save-to-db", save_to_db)

        # Step: Sync output to Google Drive (living documents)
        async def sync_to_drive() -> dict:
            try:
                from src.skills.document_sync import sync_role_output_to_drive

                cost_info = execution.get("total_cost", {})
                return await sync_role_output_to_drive(
                    role_id=role_id,
                    output_type="briefings",
                    content=execution.get("combined_output", ""),
                    metadata={
                        "cost_usd": cost_info.get("total_cost_usd", 0),
                        "skills_run": execution.get("skills_run", 0),
                    },
                    role_name=execution.get("role_name"),
                )
            except Exception as exc:
                _workflow_logger.warning(
                    "role_runner.drive_sync_failed",
                    error=str(exc),
                )
                return {"synced": False, "error": str(exc)}

        await ctx.step.run("sync-to-drive", sync_to_drive)

        # Step: Extract and save memories from this run
        async def extract_and_save_memories() -> dict:
            try:
                from datetime import date as date_cls
                from types import SimpleNamespace

                from src.db import service as db_service
                from src.db.session import get_db_session
                from src.skills.memory import extract_memories_from_results

                skill_proxies = [
                    SimpleNamespace(
                        skill_id=f"role:{role_id}",
                        output_text=execution.get(
                            "combined_output",
                            "",
                        ),
                        recommendations=execution.get(
                            "recommendations",
                            [],
                        ),
                    ),
                ]

                entries = extract_memories_from_results(
                    role_id=role_id,
                    department_id=execution.get(
                        "department_id",
                        "",
                    ),
                    skill_results=skill_proxies,
                    run_date=date_cls.today(),
                )

                saved = 0
                if entries:
                    async with get_db_session() as session:
                        for entry in entries:
                            await db_service.save_memory(
                                session=session,
                                user_id=user_id,
                                **entry,
                            )
                            saved += 1
                return {
                    "memories_extracted": len(entries),
                    "saved": saved,
                }
            except Exception as exc:
                _workflow_logger.warning(
                    "role_runner.memory_extract_failed",
                    error=str(exc),
                )
                return {
                    "memories_extracted": 0,
                    "saved": 0,
                    "error": str(exc),
                }

        await ctx.step.run(
            "extract-and-save-memories",
            extract_and_save_memories,
        )

        # Step: Post-run reflection — cheap Haiku call to capture insights
        async def run_post_reflection() -> dict:
            try:
                from src.agent.core import SideraAgent
                from src.db import service as db_service
                from src.db.session import get_db_session

                agent = SideraAgent()

                # Find peer roles that accept learnings from this role
                peer_role_ids: list[str] = []
                try:
                    from src.skills.db_loader import load_registry_with_db

                    reg = await load_registry_with_db()
                    for rid, rdef in reg._roles.items():
                        lc = getattr(rdef, "learning_channels", ())
                        if role_id in lc:
                            peer_role_ids.append(rid)
                except Exception:
                    pass  # Non-critical: reflection still works without peers

                reflection_memories = await agent.run_reflection(
                    role_id=role_id,
                    role_name=execution.get("role_name", role_id),
                    output_text=execution.get("combined_output", ""),
                    skill_ids=execution.get("skill_ids", []),
                    principles=tuple(execution.get("principles", [])),
                    peer_role_ids=tuple(peer_role_ids),
                    tool_errors=execution.get("tool_errors", []),
                )

                # Fill in department_id from execution context
                dept_id = execution.get("department_id", "")
                for mem in reflection_memories:
                    mem["department_id"] = dept_id

                saved = 0
                if reflection_memories:
                    async with get_db_session() as session:
                        for entry in reflection_memories:
                            await db_service.save_memory(
                                session=session,
                                user_id=user_id,
                                **entry,
                            )
                            saved += 1

                return {
                    "reflections_generated": len(reflection_memories),
                    "saved": saved,
                    "reflection_memories": reflection_memories,
                    "peer_role_ids": peer_role_ids,
                }
            except Exception as exc:
                _workflow_logger.warning(
                    "role_runner.reflection_failed",
                    error=str(exc),
                )
                return {
                    "reflections_generated": 0,
                    "saved": 0,
                    "error": str(exc),
                }

        reflection_result = await ctx.step.run("post-run-reflection", run_post_reflection)

        # Step: Push cross-role learnings from reflection
        async def push_cross_role_learnings() -> dict:
            try:
                from src.db import service as db_service
                from src.db.session import get_db_session

                memories = reflection_result.get("reflection_memories", [])
                if not memories:
                    return {"pushed": 0}

                dept_id = execution.get("department_id", "")
                from_role_name = execution.get("role_name", role_id)
                pushed = 0
                max_pushes = 5  # Hard cap per run

                for mem in memories:
                    evidence = mem.get("evidence", {})
                    share_with = evidence.get("share_with", [])
                    if not share_with:
                        continue

                    for target_role_id in share_with:
                        if pushed >= max_pushes:
                            break

                        try:
                            # Resolve target department
                            from src.skills.db_loader import load_registry_with_db

                            reg = await load_registry_with_db()
                            target_role = reg.get_role(target_role_id)
                            target_dept = (
                                getattr(target_role, "department_id", "") if target_role else ""
                            )

                            async with get_db_session() as session:
                                await db_service.save_memory(
                                    session=session,
                                    user_id="__system__",
                                    role_id=target_role_id,
                                    department_id=target_dept,
                                    memory_type="cross_role_insight",
                                    title=mem.get("title", ""),
                                    content=(
                                        f"[From {from_role_name} "
                                        f"({role_id})]: "
                                        f"{mem.get('content', '')}"
                                    ),
                                    confidence=mem.get("confidence", 0.7),
                                    source_skill_id=f"learning:{role_id}",
                                    source_role_id=role_id,
                                    evidence={
                                        "source": "reflection_auto_push",
                                        "from_role_id": role_id,
                                        "from_department_id": dept_id,
                                    },
                                )
                            pushed += 1
                        except Exception:
                            pass  # Individual push failures are non-critical

                if pushed:
                    _workflow_logger.info(
                        "role_runner.learnings_pushed",
                        role_id=role_id,
                        pushed=pushed,
                    )

                return {"pushed": pushed}
            except Exception as exc:
                _workflow_logger.warning(
                    "role_runner.learning_push_failed",
                    error=str(exc),
                )
                return {"pushed": 0, "error": str(exc)}

        await ctx.step.run("push-cross-role-learnings", push_cross_role_learnings)

        # Step: Scan lessons for recurring friction → auto-propose skill changes
        async def scan_lessons_for_evolution() -> dict:
            try:
                from src.skills.reflection_evolution import scan_lessons_for_proposals

                proposals = await scan_lessons_for_proposals(
                    role_id=role_id,
                    department_id=execution.get("department_id", ""),
                    user_id=user_id,
                )

                if proposals:
                    _workflow_logger.info(
                        "role_runner.lesson_proposals_generated",
                        role_id=role_id,
                        count=len(proposals),
                    )

                return {
                    "proposals": proposals,
                    "count": len(proposals),
                }
            except Exception as exc:
                _workflow_logger.warning(
                    "role_runner.lesson_scan_failed",
                    error=str(exc),
                )
                return {"proposals": [], "count": 0, "error": str(exc)}

        lesson_evolution_result = await ctx.step.run(
            "scan-lessons-for-evolution",
            scan_lessons_for_evolution,
        )

        # Step: Scan gap observations for role proposals (managers only)
        async def scan_gaps_for_roles() -> dict:
            try:
                from src.skills.reflection_evolution import (
                    scan_gaps_for_role_proposals,
                )

                proposals = await scan_gaps_for_role_proposals(
                    role_id=role_id,
                    department_id=execution.get("department_id", ""),
                    user_id=user_id,
                )

                if proposals:
                    _workflow_logger.info(
                        "role_runner.gap_proposals_generated",
                        role_id=role_id,
                        count=len(proposals),
                    )

                return {
                    "proposals": proposals,
                    "count": len(proposals),
                }
            except Exception as exc:
                _workflow_logger.warning(
                    "role_runner.gap_scan_failed",
                    error=str(exc),
                )
                return {"proposals": [], "count": 0, "error": str(exc)}

        gap_detection_result = await ctx.step.run(
            "scan-gaps-for-role-proposals",
            scan_gaps_for_roles,
        )

        # Step: Suggest skills from gaps → message skill_creator
        async def suggest_skills_from_gaps() -> dict:
            try:
                from src.skills.reflection_evolution import (
                    scan_gaps_for_skill_suggestions,
                )

                suggestions = await scan_gaps_for_skill_suggestions(
                    role_id=role_id,
                    department_id=execution.get("department_id", ""),
                    user_id=user_id,
                )

                if not suggestions:
                    return {"suggestions": [], "sent": 0}

                from src.db import service as db_service
                from src.db.session import get_db_session

                sent = 0
                for suggestion in suggestions:
                    message_content = (
                        f"Skill Creation Suggestion from {role_id}:\n\n"
                        f"Domain: {suggestion['domain']}\n"
                        f"Suggested name: {suggestion['suggested_skill_name']}\n"
                        f"Display name: {suggestion.get('display_name', '')}\n"
                        f"Description: {suggestion['suggested_description']}\n"
                        f"Category: {suggestion.get('suggested_category', 'analysis')}\n"
                        f"Target role: {suggestion.get('suggested_role_id', role_id)}\n"
                        f"Reasoning: {suggestion['reasoning']}\n\n"
                        f"Gap evidence ({suggestion.get('gap_count', 0)} observations):\n"
                        f"{suggestion.get('gap_summary', 'No details available')}"
                    )
                    async with get_db_session() as session:
                        await db_service.create_role_message(
                            session,
                            from_role_id=role_id,
                            to_role_id="skill_creator",
                            from_department_id=execution.get("department_id", ""),
                            to_department_id="it",
                            subject=f"Skill Suggestion: {suggestion['domain']}"[:100],
                            content=message_content[:2000],
                        )
                    sent += 1

                return {"suggestions": len(suggestions), "sent": sent}
            except Exception as exc:
                _workflow_logger.warning(
                    "role_runner.skill_suggestion_failed",
                    error=str(exc),
                )
                return {"suggestions": 0, "sent": 0, "error": str(exc)}

        await ctx.step.run("suggest-skills-from-gaps", suggest_skills_from_gaps)

        # Step: Send output to Slack
        async def send_output() -> dict:
            from src.connectors.slack import SlackConnector

            slack = SlackConnector()
            return slack.send_briefing(
                channel_id=channel_id or None,
                briefing_text=execution["combined_output"],
                recommendations=execution.get("recommendations", []),
            )

        slack_result = await ctx.step.run("send-output", send_output)

        # Collect any skill evolution proposals from MCP tool calls
        all_role_recs = list(execution.get("recommendations", []))
        try:
            from src.mcp_servers.evolution import get_pending_proposals

            skill_proposals = get_pending_proposals()
            if skill_proposals:
                all_role_recs.extend(skill_proposals)
                _workflow_logger.info(
                    "role_runner.skill_proposals_collected",
                    role_id=role_id,
                    count=len(skill_proposals),
                )
        except Exception:
            pass  # Non-fatal — evolution tools may not be available

        # Also merge lesson-based evolution proposals
        try:
            lesson_proposals = lesson_evolution_result.get("proposals", [])
            if lesson_proposals:
                from src.skills.evolution import format_proposal_as_recommendation

                for prop in lesson_proposals:
                    rec = format_proposal_as_recommendation(prop)
                    if rec:
                        all_role_recs.append(rec)
                _workflow_logger.info(
                    "role_runner.lesson_proposals_merged",
                    role_id=role_id,
                    count=len(lesson_proposals),
                )
        except Exception:
            pass  # Non-fatal

        # Also merge gap-detection-based role proposals
        try:
            gap_proposals = gap_detection_result.get("proposals", [])
            if gap_proposals:
                from src.skills.role_evolution import (
                    format_role_proposal_as_recommendation,
                    generate_role_diff,
                )

                for prop in gap_proposals:
                    diff = generate_role_diff(
                        existing=None,
                        proposed_changes=prop.get("proposed_changes", {}),
                    )
                    rec = format_role_proposal_as_recommendation(
                        proposal=prop,
                        rationale=prop.get("reasoning", ""),
                        diff=diff,
                        proposer_role_id=prop.get("proposer_role_id", role_id),
                        department_id=prop.get("department_id", ""),
                    )
                    if rec:
                        all_role_recs.append(rec)
                _workflow_logger.info(
                    "role_runner.gap_proposals_merged",
                    role_id=role_id,
                    count=len(gap_proposals),
                )
        except Exception:
            pass  # Non-fatal

        # Step: Approval flow for collected recommendations
        decisions: dict[str, dict] = {}
        approval_ids: list[str] = []
        for i, rec in enumerate(all_role_recs):

            async def send_approval(rec: dict = rec, idx: int = i) -> dict:
                from src.connectors.slack import SlackConnector

                slack = SlackConnector()
                approval_id = f"role-{role_id}-{ctx.run_id}-{idx}"

                # Include diff text for skill proposals
                diff_text = ""
                if rec.get("action_type") == "skill_proposal":
                    diff_text = rec.get("action_params", {}).get("diff", "")

                result = slack.send_approval_request(
                    channel_id=channel_id or None,
                    approval_id=approval_id,
                    action_type=rec.get("action_type", rec.get("action", "unknown")),
                    description=rec.get("action", rec.get("description", "")),
                    reasoning=rec.get("reasoning", ""),
                    projected_impact=rec.get("projected_impact", ""),
                    risk_level=rec.get("risk_level", "unknown"),
                    diff_text=diff_text,
                )
                return {"approval_id": approval_id, "slack_result": result}

            approval_result = await ctx.step.run(f"send-approval-{i}", send_approval)
            approval_ids.append(approval_result["approval_id"])

        for approval_id in approval_ids:
            event = await ctx.step.wait_for_event(
                f"wait-approval-{approval_id}",
                event="sidera/approval.decided",
                if_exp=f"event.data.approval_id == '{approval_id}'",
                timeout=86_400_000,
            )
            if event is not None:
                decisions[approval_id] = {
                    "status": event.data.get("status", "unknown"),
                    "decided_by": event.data.get("decided_by", ""),
                }
            else:
                decisions[approval_id] = {"status": "expired"}

        # Step 6: Execute approved actions
        role_execution_summary: dict[str, Any] = {
            "executed": 0,
            "skipped": 0,
            "failed": 0,
            "errors": [],
        }
        if decisions:

            async def execute_role_actions() -> dict:
                executed = 0
                skipped = 0
                failed = 0
                errors: list[str] = []

                for aid_str, decision in decisions.items():
                    if decision.get("status") != "approved":
                        skipped += 1
                        continue

                    parts = aid_str.split("-")
                    db_id = int(parts[-1]) if parts[-1].isdigit() else 0
                    if db_id == 0:
                        skipped += 1
                        continue

                    try:
                        from src.db import service as db_service
                        from src.db.session import get_db_session

                        async with get_db_session() as session:
                            item = await db_service.get_approval_by_id(
                                session,
                                db_id,
                            )
                            if item is None or item.executed_at is not None:
                                skipped += 1
                                continue
                            at = item.action_type.value if item.action_type else "unknown"
                            ap = item.action_params or {}

                        evidence_snapshot = _capture_evidence_snapshot(at, ap)
                        result = await _execute_action(at, ap)
                        if isinstance(result, dict):
                            result["evidence_snapshot"] = evidence_snapshot

                        async with get_db_session() as session:
                            await db_service.record_execution_result(
                                session,
                                db_id,
                                execution_result=result,
                            )
                        executed += 1

                    except Exception as exc:
                        _workflow_logger.error(
                            "role_execute_action.failed",
                            approval_id=db_id,
                            error=str(exc),
                        )
                        failed += 1
                        errors.append(str(exc))
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

                return {
                    "executed": executed,
                    "skipped": skipped,
                    "failed": failed,
                    "errors": errors[:10],
                }

            role_execution_summary = await ctx.step.run(
                "execute-approved-actions",
                execute_role_actions,
            )

        return {
            "role_id": role_id,
            "user_id": user_id,
            "skills_run": execution.get("skills_run", 0),
            "output_sent": slack_result.get("ok", False),
            "approvals_sent": len(approval_ids),
            "decisions": decisions,
            "execution": role_execution_summary,
            "cost": execution.get("total_cost", {}),
        }
    except inngest.NonRetriableError:
        raise
    except Exception as dlq_exc:
        from src.middleware.sentry_setup import capture_exception

        capture_exception(dlq_exc)
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.record_failed_run(
                    session=session,
                    workflow_name="role_runner",
                    event_name=ctx.event.name,
                    event_data=ctx.event.data,
                    error_message=str(dlq_exc),
                    error_type=type(dlq_exc).__name__,
                    user_id=ctx.event.data.get("user_id", ""),
                    run_id=ctx.run_id,
                )
        except Exception:
            pass
        raise


# =====================================================================
# Department runner workflow
# =====================================================================


@inngest_client.create_function(
    fn_id="sidera-department-runner",
    name="Sidera Department Runner",
    trigger=inngest.TriggerEvent(event="sidera/department.run"),
    retries=2,
)
async def department_runner_workflow(ctx: inngest.Context) -> dict:
    """Run all roles in a department via DepartmentExecutor.

    Expected event data:
    - department_id (str, required): Which department to run
    - user_id (str, required): Advertiser user ID
    - channel_id (str, optional): Slack channel for output
    """
    try:
        department_id = ctx.event.data.get("department_id", "")
        user_id = ctx.event.data.get("user_id", "default")
        channel_id = ctx.event.data.get("channel_id", "")

        if not department_id:
            raise inngest.NonRetriableError("department_id is required in event data")

        # Step 1: Load accounts
        async def load_accounts() -> dict:
            try:
                from src.db import service as db_service
                from src.db.session import get_db_session

                async with get_db_session() as session:
                    db_accounts = await db_service.get_accounts_for_user(session, user_id)
                    if db_accounts:
                        return {
                            "accounts": [
                                {
                                    "platform": (
                                        a.platform.value
                                        if hasattr(a.platform, "value")
                                        else a.platform
                                    ),
                                    "account_id": a.platform_account_id,
                                    "account_name": a.account_name or "",
                                    "target_roas": (
                                        float(a.target_roas) if a.target_roas else None
                                    ),
                                    "target_cpa": (float(a.target_cpa) if a.target_cpa else None),
                                    "monthly_budget_cap": (
                                        float(a.monthly_budget_cap)
                                        if a.monthly_budget_cap
                                        else None
                                    ),
                                }
                                for a in db_accounts
                            ],
                            "source": "database",
                        }
            except Exception as exc:
                from src.middleware.sentry_setup import capture_exception

                capture_exception(exc)
            return {
                "accounts": ctx.event.data.get("accounts", []),
                "source": "event",
            }

        account_data = await ctx.step.run("load-accounts", load_accounts)
        accounts = account_data["accounts"]

        if not accounts:
            raise inngest.NonRetriableError("No accounts configured for department execution")

        # Step 2: Execute department
        async def execute_department() -> dict:
            from src.agent.core import SideraAgent
            from src.skills.db_loader import load_registry_with_db
            from src.skills.executor import (
                DepartmentExecutor,
                RoleExecutor,
                SkillExecutor,
            )

            registry = await load_registry_with_db()
            agent = SideraAgent()
            skill_executor = SkillExecutor(agent=agent, registry=registry)
            role_executor = RoleExecutor(
                skill_executor=skill_executor,
                registry=registry,
            )
            dept_executor = DepartmentExecutor(
                role_executor=role_executor,
                registry=registry,
            )

            result = await dept_executor.execute_department(
                department_id=department_id,
                user_id=user_id,
                accounts=accounts,
            )
            return {
                "department_id": result.department_id,
                "combined_output": result.combined_output,
                "total_cost": result.total_cost,
                "roles_run": len(result.role_results),
                "recommendations": [
                    rec
                    for rr in result.role_results
                    for sr in rr.skill_results
                    for rec in sr.recommendations
                ],
            }

        execution = await ctx.step.run("execute-department", execute_department)

        # Step 3: Save to DB
        async def save_to_db() -> dict:
            try:
                from datetime import date

                from src.db import service as db_service
                from src.db.session import get_db_session

                async with get_db_session() as session:
                    result = await db_service.save_analysis_result(
                        session=session,
                        user_id=user_id,
                        run_date=date.today(),
                        briefing_content=execution["combined_output"],
                        recommendations=execution.get("recommendations", []),
                        cost_info=execution.get("total_cost", {}),
                        accounts_analyzed=[a.get("account_id", "") for a in accounts],
                        skill_id=f"dept:{department_id}",
                    )
                    cost = execution.get("total_cost", {})
                    if cost.get("total_cost_usd"):
                        await db_service.record_cost(
                            session=session,
                            user_id=user_id,
                            run_date=date.today(),
                            model="multi-model",
                            cost_usd=cost["total_cost_usd"],
                            operation=f"dept_{department_id}",
                        )
                    await db_service.log_event(
                        session=session,
                        user_id=user_id,
                        event_type="department_run",
                        event_data={
                            "department_id": department_id,
                            "roles_run": execution.get("roles_run", 0),
                            "cost": cost,
                        },
                        source="department_runner",
                    )
                    return {"analysis_id": result.id, "saved": True}
            except Exception as exc:
                from src.middleware.sentry_setup import capture_exception

                capture_exception(exc)
                return {"analysis_id": None, "saved": False, "error": str(exc)}

        await ctx.step.run("save-to-db", save_to_db)

        # Step 4: Send output to Slack
        async def send_output() -> dict:
            from src.connectors.slack import SlackConnector

            slack = SlackConnector()
            return slack.send_briefing(
                channel_id=channel_id or None,
                briefing_text=execution["combined_output"],
                recommendations=execution.get("recommendations", []),
            )

        slack_result = await ctx.step.run("send-output", send_output)

        return {
            "department_id": department_id,
            "user_id": user_id,
            "roles_run": execution.get("roles_run", 0),
            "output_sent": slack_result.get("ok", False),
            "cost": execution.get("total_cost", {}),
        }
    except inngest.NonRetriableError:
        raise
    except Exception as dlq_exc:
        from src.middleware.sentry_setup import capture_exception

        capture_exception(dlq_exc)
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.record_failed_run(
                    session=session,
                    workflow_name="department_runner",
                    event_name=ctx.event.name,
                    event_data=ctx.event.data,
                    error_message=str(dlq_exc),
                    error_type=type(dlq_exc).__name__,
                    user_id=ctx.event.data.get("user_id", ""),
                    run_id=ctx.run_id,
                )
        except Exception:
            pass
        raise


# =====================================================================
# Manager runner workflow
# =====================================================================


@inngest_client.create_function(
    fn_id="manager-runner",
    trigger=inngest.TriggerEvent(event="sidera/manager.run"),
    retries=2,
)
async def manager_runner_workflow(ctx: inngest.Context) -> dict:
    """Run a manager role: own skills -> delegation -> sub-roles -> synthesis -> approval.

    A manager role has a ``manages`` field listing sub-role IDs. This
    workflow runs the manager's own ``briefing_skills`` first, then asks
    the LLM which sub-roles to activate, runs those sub-roles inline
    (each in its own durable step), synthesizes all results, stores
    the analysis, sends a unified Slack briefing, and processes any
    recommendations through the approval pipeline.

    Expected event data:
    - user_id (str, required): Advertiser user ID
    - role_id (str, required): The manager role ID
    - channel_id (str, optional): Slack channel for output
    - account_id (int, optional): Account ID for approval items
    - force_refresh (bool, optional): Bypass caches
    - meeting_context (dict, optional): Injected by meeting_end_workflow
      with keys: meeting_id, summary, action_items, transcript_length,
      duration_seconds, participants. When present, the meeting summary
      is prepended to the delegation decision context.
    """
    try:
        user_id = ctx.event.data.get("user_id", "default")
        role_id = ctx.event.data.get("role_id", "")
        channel_id = ctx.event.data.get("channel_id", "")
        account_id = ctx.event.data.get("account_id", 0)
        meeting_context = ctx.event.data.get("meeting_context")

        if not role_id:
            raise inngest.NonRetriableError("role_id is required in event data")

        # Step 1: Load manager role from registry and validate
        async def load_manager() -> dict:
            from src.skills.db_loader import load_registry_with_db

            registry = await load_registry_with_db()

            role = registry.get_role(role_id)
            if role is None:
                raise inngest.NonRetriableError(f"Role '{role_id}' not found in registry")

            if not role.manages:
                raise inngest.NonRetriableError(
                    f"Role '{role_id}' is not a manager (no manages field)"
                )

            # Load memory context
            memory_context = ""
            try:
                from src.db import service as db_service
                from src.db.session import get_db_session
                from src.skills.memory import (
                    compose_memory_context,
                    filter_superseded_memories,
                )

                async with get_db_session() as session:
                    memories = await db_service.get_role_memories(
                        session,
                        user_id,
                        role_id,
                        min_confidence=0.5,
                        limit=20,
                    )
                    superseded = await db_service.get_superseded_memory_ids(
                        session,
                        user_id,
                        role_id,
                    )
                    memories = filter_superseded_memories(memories, superseded)

                    # Load inter-agent relationship memories
                    agent_memories = await db_service.get_agent_relationship_memories(
                        session, role_id, limit=5
                    )
                    all_memories = list(memories) + list(agent_memories)

                    memory_context = compose_memory_context(all_memories)
            except Exception as exc:
                _workflow_logger.warning(
                    "manager_runner.memory_load_failed",
                    error=str(exc),
                )

            # Resolve managed roles
            managed_roles = registry.get_managed_roles(role_id)
            managed_info = [
                {
                    "role_id": mr.id,
                    "name": mr.name,
                    "description": mr.description,
                    "department_id": mr.department_id,
                    "briefing_skills": list(mr.briefing_skills),
                }
                for mr in managed_roles
            ]

            return {
                "role_name": role.name,
                "role_persona": role.persona,
                "department_id": role.department_id,
                "briefing_skills": list(role.briefing_skills),
                "manages": list(role.manages),
                "managed_roles": managed_info,
                "delegation_model": role.delegation_model,
                "synthesis_prompt": role.synthesis_prompt,
                "memory_context": memory_context,
            }

        manager_data = await ctx.step.run("load-manager", load_manager)

        # Step 1b: Load accounts
        async def load_accounts() -> dict:
            try:
                from src.db import service as db_service
                from src.db.session import get_db_session

                async with get_db_session() as session:
                    db_accounts = await db_service.get_accounts_for_user(
                        session,
                        user_id,
                    )
                    if db_accounts:
                        return {
                            "accounts": [
                                {
                                    "platform": (
                                        a.platform.value
                                        if hasattr(a.platform, "value")
                                        else a.platform
                                    ),
                                    "account_id": a.platform_account_id,
                                    "account_name": a.account_name or "",
                                    "target_roas": (
                                        float(a.target_roas) if a.target_roas else None
                                    ),
                                    "target_cpa": (float(a.target_cpa) if a.target_cpa else None),
                                    "monthly_budget_cap": (
                                        float(a.monthly_budget_cap)
                                        if a.monthly_budget_cap
                                        else None
                                    ),
                                }
                                for a in db_accounts
                            ],
                            "source": "database",
                        }
            except Exception as exc:
                from src.middleware.sentry_setup import capture_exception

                capture_exception(exc)
            return {
                "accounts": ctx.event.data.get("accounts", []),
                "source": "event",
            }

        account_data = await ctx.step.run("load-accounts", load_accounts)
        accounts = account_data["accounts"]

        if not accounts:
            raise inngest.NonRetriableError("No accounts configured for manager execution")

        # Step 2: Run manager's own briefing_skills (skip if none)
        own_results_text = ""
        own_recommendations: list[dict] = []

        if manager_data["briefing_skills"]:

            async def run_own_skills() -> dict:
                from src.agent.core import SideraAgent
                from src.skills.db_loader import load_registry_with_db
                from src.skills.executor import RoleExecutor, SkillExecutor

                registry = await load_registry_with_db()
                agent = SideraAgent()
                skill_executor = SkillExecutor(
                    agent=agent,
                    registry=registry,
                )
                role_executor = RoleExecutor(
                    skill_executor=skill_executor,
                    registry=registry,
                )

                result = await role_executor.execute_role(
                    role_id=role_id,
                    user_id=user_id,
                    accounts=accounts,
                    memory_context=manager_data.get("memory_context", ""),
                )
                return {
                    "combined_output": result.combined_output,
                    "total_cost": result.total_cost,
                    "skills_run": len(result.skill_results),
                    "recommendations": [
                        rec for sr in result.skill_results for rec in sr.recommendations
                    ],
                }

            own_skill_data = await ctx.step.run(
                "run-own-skills",
                run_own_skills,
            )
            own_results_text = own_skill_data.get("combined_output", "")
            own_recommendations = own_skill_data.get("recommendations", [])

        # Step 3: Delegation decision — which sub-roles to activate
        # If triggered by meeting_end_workflow, prepend meeting context
        delegation_context = own_results_text
        if meeting_context:
            meeting_summary = meeting_context.get("summary", "")
            action_items = meeting_context.get("action_items", [])
            action_text = (
                "\n".join(f"- {item.get('item', '')}" for item in action_items)
                if action_items
                else "None identified."
            )
            meeting_preamble = (
                f"## Post-Meeting Context\n\n"
                f"This delegation was triggered after a live meeting "
                f"({meeting_context.get('duration_seconds', 0) // 60} minutes, "
                f"{meeting_context.get('transcript_length', 0)} transcript entries).\n\n"
                f"### Meeting Summary\n{meeting_summary[:1500]}\n\n"
                f"### Action Items from Meeting\n{action_text}\n\n"
                f"---\n\n"
            )
            delegation_context = meeting_preamble + own_results_text

        async def delegation_decision() -> dict:
            from src.agent.core import SideraAgent

            agent = SideraAgent()
            try:
                decision = await agent.run_delegation_decision(
                    manager_name=manager_data["role_name"],
                    manager_persona=manager_data.get("role_persona", ""),
                    own_results_summary=delegation_context[:4000],
                    available_roles=manager_data["managed_roles"],
                    model=(None if manager_data.get("delegation_model") != "fast" else "haiku"),
                )
                return decision
            except Exception as exc:
                _workflow_logger.warning(
                    "manager_runner.delegation_failed",
                    error=str(exc),
                )
                # Fallback: activate all sub-roles
                return {
                    "activate": [
                        {
                            "role_id": r["role_id"],
                            "reason": "Fallback: delegation failed",
                            "priority": idx + 1,
                        }
                        for idx, r in enumerate(manager_data["managed_roles"])
                    ],
                    "skip": [],
                }

        delegation = await ctx.step.run(
            "delegation-decision",
            delegation_decision,
        )

        # Determine which sub-role IDs to run
        activated_ids = [
            entry.get("role_id", "")
            for entry in delegation.get("activate", [])
            if entry.get("role_id")
        ]

        # Step 4: Run each activated sub-role inline (own durable step)
        sub_role_results: dict[str, dict] = {}
        sub_role_errors: dict[str, str] = {}

        for sub_role_id in activated_ids:

            async def run_sub_role(
                sr_id: str = sub_role_id,
            ) -> dict:
                from src.agent.core import SideraAgent
                from src.skills.db_loader import load_registry_with_db
                from src.skills.executor import RoleExecutor, SkillExecutor

                registry = await load_registry_with_db()
                agent = SideraAgent()
                skill_executor = SkillExecutor(
                    agent=agent,
                    registry=registry,
                )
                role_executor = RoleExecutor(
                    skill_executor=skill_executor,
                    registry=registry,
                )

                try:
                    result = await role_executor.execute_role(
                        role_id=sr_id,
                        user_id=user_id,
                        accounts=accounts,
                    )
                    return {
                        "success": True,
                        "role_id": result.role_id,
                        "combined_output": result.combined_output,
                        "total_cost": result.total_cost,
                        "skills_run": len(result.skill_results),
                        "recommendations": [
                            rec for sr in result.skill_results for rec in sr.recommendations
                        ],
                    }
                except Exception as exc:
                    _workflow_logger.error(
                        "manager_runner.sub_role_failed",
                        sub_role_id=sr_id,
                        error=str(exc),
                    )
                    return {
                        "success": False,
                        "role_id": sr_id,
                        "error": str(exc),
                    }

            sub_result = await ctx.step.run(
                f"run-sub-role-{sub_role_id}",
                run_sub_role,
            )

            if sub_result.get("success"):
                sub_role_results[sub_role_id] = sub_result
            else:
                sub_role_errors[sub_role_id] = sub_result.get("error", "Unknown error")

        # Step 5: Synthesis — combine all results
        async def synthesis() -> dict:
            from src.agent.core import SideraAgent

            agent = SideraAgent()

            # Format sub-role results for synthesis
            sub_results_parts: list[str] = []
            for sr_id, sr_data in sub_role_results.items():
                sub_results_parts.append(f"## {sr_id}\n\n{sr_data.get('combined_output', '')}")
            for sr_id, error in sub_role_errors.items():
                sub_results_parts.append(f"## {sr_id}\n\n[ERROR: {error}]")

            sub_results_text = "\n\n".join(sub_results_parts)

            synthesis_text = await agent.run_synthesis(
                manager_name=manager_data["role_name"],
                manager_persona=manager_data.get("role_persona", ""),
                own_results=own_results_text,
                sub_role_results=sub_results_text,
                synthesis_prompt=manager_data.get("synthesis_prompt", ""),
            )
            return {"synthesis_text": synthesis_text}

        synthesis_data = await ctx.step.run("synthesis", synthesis)
        synthesis_text = synthesis_data["synthesis_text"]

        # Collect all recommendations
        all_recommendations = list(own_recommendations)
        for sr_data in sub_role_results.values():
            all_recommendations.extend(sr_data.get("recommendations", []))

        # Collect any skill evolution proposals from MCP tool calls
        try:
            from src.mcp_servers.evolution import get_pending_proposals

            skill_proposals = get_pending_proposals()
            if skill_proposals:
                all_recommendations.extend(skill_proposals)
                _workflow_logger.info(
                    "manager_runner.skill_proposals_collected",
                    role_id=role_id,
                    count=len(skill_proposals),
                )
        except Exception:
            pass  # Non-fatal — evolution tools may not be available

        # Dispatch any pending working groups
        try:
            from src.mcp_servers.working_group import (
                get_pending_working_groups,
            )

            pending_wgs = get_pending_working_groups()
            for wg_proposal in pending_wgs:
                wg_proposal["user_id"] = user_id
                wg_proposal["channel_id"] = channel_id
                await inngest_client.send(
                    inngest.Event(
                        name="sidera/working_group.run",
                        data=wg_proposal,
                    ),
                )
                _workflow_logger.info(
                    "manager_runner.working_group_dispatched",
                    group_id=wg_proposal.get("group_id"),
                    role_id=role_id,
                )
        except Exception:
            pass  # Non-fatal

        # Step 6: Store results in DB
        async def store_results() -> dict:
            try:
                from datetime import date

                from src.db import service as db_service
                from src.db.session import get_db_session

                async with get_db_session() as session:
                    result = await db_service.save_analysis_result(
                        session=session,
                        user_id=user_id,
                        run_date=date.today(),
                        briefing_content=synthesis_text,
                        recommendations=all_recommendations,
                        cost_info={},
                        accounts_analyzed=[a.get("account_id", "") for a in accounts],
                        skill_id=f"manager:{role_id}",
                    )
                    await db_service.log_event(
                        session=session,
                        user_id=user_id,
                        event_type="manager_run",
                        event_data={
                            "role_id": role_id,
                            "sub_roles_activated": activated_ids,
                            "sub_roles_succeeded": list(sub_role_results.keys()),
                            "sub_roles_failed": list(sub_role_errors.keys()),
                            "own_skills_run": len(manager_data.get("briefing_skills", [])),
                        },
                        source="manager_runner",
                    )
                    return {"analysis_id": result.id, "saved": True}
            except Exception as exc:
                from src.middleware.sentry_setup import capture_exception

                capture_exception(exc)
                return {
                    "analysis_id": None,
                    "saved": False,
                    "error": str(exc),
                }

        store_data = await ctx.step.run("store-results", store_results)

        # Step: Sync synthesis to Google Drive (living documents)
        async def sync_manager_to_drive() -> dict:
            try:
                from src.skills.document_sync import sync_role_output_to_drive

                return await sync_role_output_to_drive(
                    role_id=role_id,
                    output_type="briefings",
                    content=synthesis_text,
                    metadata={
                        "skills_run": len(manager_data.get("briefing_skills", [])),
                    },
                    role_name=manager_data.get("role_name"),
                )
            except Exception as exc:
                _workflow_logger.warning(
                    "manager_runner.drive_sync_failed",
                    error=str(exc),
                )
                return {"synced": False, "error": str(exc)}

        await ctx.step.run("sync-to-drive", sync_manager_to_drive)

        # Step 7: Send unified briefing to Slack
        async def send_briefing() -> dict:
            from src.connectors.slack import SlackConnector

            slack = SlackConnector()
            return slack.send_briefing(
                channel_id=channel_id or None,
                briefing_text=synthesis_text,
                recommendations=all_recommendations,
            )

        slack_result = await ctx.step.run("send-briefing", send_briefing)

        # Step 8: Process recommendations through approval pipeline
        approval_result: dict[str, Any] = {}
        if all_recommendations:

            async def run_approval_flow() -> dict:
                from src.workflows.approval_flow import (
                    process_recommendations,
                )

                return await process_recommendations(
                    ctx=ctx,
                    recommendations=all_recommendations,
                    user_id=user_id,
                    channel_id=channel_id,
                    run_id=ctx.run_id,
                    source="manager_runner",
                    role_id=role_id,
                    analysis_id=store_data.get("analysis_id"),
                    account_id=account_id,
                )

            approval_result = await ctx.step.run(
                "process-recommendations",
                run_approval_flow,
            )

        # Step 9: Save manager-level memory
        async def save_memory() -> dict:
            try:
                from datetime import date as date_cls
                from types import SimpleNamespace

                from src.db import service as db_service
                from src.db.session import get_db_session
                from src.skills.memory import extract_memories_from_results

                skill_proxies = [
                    SimpleNamespace(
                        skill_id=f"manager:{role_id}",
                        output_text=synthesis_text,
                        recommendations=all_recommendations,
                    ),
                ]

                entries = extract_memories_from_results(
                    role_id=role_id,
                    department_id=manager_data.get("department_id", ""),
                    skill_results=skill_proxies,
                    run_date=date_cls.today(),
                )

                saved = 0
                if entries:
                    async with get_db_session() as session:
                        for entry in entries:
                            await db_service.save_memory(
                                session=session,
                                user_id=user_id,
                                **entry,
                            )
                            saved += 1
                return {
                    "memories_extracted": len(entries),
                    "saved": saved,
                }
            except Exception as exc:
                _workflow_logger.warning(
                    "manager_runner.memory_save_failed",
                    error=str(exc),
                )
                return {
                    "memories_extracted": 0,
                    "saved": 0,
                    "error": str(exc),
                }

        await ctx.step.run("save-memory", save_memory)

        return {
            "role_id": role_id,
            "user_id": user_id,
            "own_skills_run": len(manager_data.get("briefing_skills", [])),
            "sub_roles_activated": activated_ids,
            "sub_roles_succeeded": list(sub_role_results.keys()),
            "sub_roles_failed": list(sub_role_errors.keys()),
            "synthesis_length": len(synthesis_text),
            "recommendations_count": len(all_recommendations),
            "output_sent": slack_result.get("ok", False),
            "approval": approval_result,
            "analysis_id": store_data.get("analysis_id"),
        }
    except inngest.NonRetriableError:
        raise
    except Exception as dlq_exc:
        from src.middleware.sentry_setup import capture_exception

        capture_exception(dlq_exc)
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.record_failed_run(
                    session=session,
                    workflow_name="manager_runner",
                    event_name=ctx.event.name,
                    event_data=ctx.event.data,
                    error_message=str(dlq_exc),
                    error_type=type(dlq_exc).__name__,
                    user_id=ctx.event.data.get("user_id", ""),
                    run_id=ctx.run_id,
                )
        except Exception:
            pass  # Don't fail the DLQ recording itself
        raise  # Re-raise so Inngest retries


# =====================================================================
# 9. Conversation Turn Workflow
# =====================================================================


@inngest_client.create_function(
    fn_id="sidera-conversation-turn",
    trigger=inngest.TriggerEvent(event="sidera/conversation.turn"),
    retries=1,
)
async def conversation_turn_workflow(
    ctx: inngest.Context,
    step: inngest.Step,
) -> dict[str, Any]:
    """Handle a single conversation turn in a Slack thread.

    Triggered by ``sidera/conversation.turn`` events emitted by the Slack
    ``app_mention`` and ``message`` handlers.

    Steps:
    1. Load/create conversation thread record in DB
    2. Check thread limits (turns, timeout, cost)
    3. Build role context (department + role + memory)
    4. Get thread history from Slack
    5. Run agent conversation turn
    6. Post reply to Slack thread
    7. Update thread activity in DB
    """
    role_id = ctx.event.data.get("role_id", "")
    channel_id = ctx.event.data.get("channel_id", "")
    thread_ts = ctx.event.data.get("thread_ts", "")
    user_id = ctx.event.data.get("user_id", "")
    message_text = ctx.event.data.get("message_text", "")
    image_content: list[dict[str, Any]] | None = ctx.event.data.get("image_content")

    # Resolve user clearance (from event data if present, else DB lookup)
    user_clearance = ctx.event.data.get("user_clearance", "")
    if not user_clearance:
        try:
            from src.middleware.rbac import resolve_user_clearance

            user_clearance = await resolve_user_clearance(user_id)
        except Exception:
            user_clearance = "public"

    _workflow_logger.info(
        "conversation_turn.start",
        role_id=role_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        user_id=user_id,
    )

    try:
        # -- Step 1: Load or create thread record --
        async def load_thread():
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                thread = await db_service.get_conversation_thread(
                    session,
                    thread_ts,
                )
                if thread is None:
                    thread = await db_service.create_conversation_thread(
                        session=session,
                        thread_ts=thread_ts,
                        channel_id=channel_id,
                        role_id=role_id,
                        user_id=user_id,
                    )
                return {
                    "thread_ts": thread.thread_ts,
                    "role_id": thread.role_id,
                    "turn_count": thread.turn_count,
                    "is_active": thread.is_active,
                    "total_cost_usd": float(thread.total_cost_usd or 0),
                    "last_activity_at": (
                        thread.last_activity_at.isoformat() if thread.last_activity_at else None
                    ),
                }

        thread_data = await ctx.step.run("load-thread", load_thread)

        # Use the role from the thread record (may differ from event if
        # this is a reply in an existing thread)
        effective_role_id = thread_data.get("role_id", role_id)

        # -- Step 2: Check thread limits --
        async def check_limits():
            from datetime import datetime, timedelta, timezone
            from decimal import Decimal

            from src.config import settings as s

            # Turn limit
            if thread_data["turn_count"] >= s.conversation_max_turns_per_thread:
                return {
                    "allowed": False,
                    "reason": (
                        f"Thread limit reached ({s.conversation_max_turns_per_thread} turns). "
                        "Please start a new thread."
                    ),
                }

            # Cost limit
            if Decimal(str(thread_data["total_cost_usd"])) >= s.conversation_max_cost_per_thread:
                return {
                    "allowed": False,
                    "reason": (
                        f"Thread cost limit reached (${s.conversation_max_cost_per_thread}). "
                        "Please start a new thread."
                    ),
                }

            # Timeout
            if thread_data.get("last_activity_at"):
                last_activity = datetime.fromisoformat(thread_data["last_activity_at"])
                timeout = timedelta(hours=s.conversation_thread_timeout_hours)
                if datetime.now(timezone.utc) - last_activity > timeout:
                    return {
                        "allowed": False,
                        "reason": (
                            f"Thread timed out ({s.conversation_thread_timeout_hours}h inactive). "
                            "Please start a new thread."
                        ),
                    }

            # Active check
            if not thread_data.get("is_active", True):
                return {
                    "allowed": False,
                    "reason": "Thread is no longer active. Please start a new thread.",
                }

            return {"allowed": True, "reason": ""}

        limits = await ctx.step.run("check-limits", check_limits)

        if not limits["allowed"]:
            # Post limit message to thread and exit
            async def post_limit_message():
                from src.connectors.slack import SlackConnector

                connector = SlackConnector()
                return connector.send_thread_reply(
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    text=f":warning: {limits['reason']}",
                )

            await ctx.step.run("post-limit-message", post_limit_message)
            return {"status": "limited", "reason": limits["reason"]}

        # -- Step 3: Build role context --
        async def build_context():
            from src.mcp_servers.messaging import compose_message_context
            from src.skills.db_loader import load_registry_with_db
            from src.skills.executor import compose_role_context
            from src.skills.memory import (
                compose_memory_context,
                filter_superseded_memories,
            )

            registry = await load_registry_with_db()

            role = registry.get_role(effective_role_id)
            if role is None:
                return {"error": f"Role '{effective_role_id}' not found"}

            dept = registry.get_department(role.department_id)

            # Load memory context
            memory_ctx = ""
            try:
                from src.db import service as db_service
                from src.db.session import get_db_session

                async with get_db_session() as session:
                    memories = await db_service.get_role_memories(
                        session,
                        user_id,
                        effective_role_id,
                        limit=10,
                    )
                    superseded = await db_service.get_superseded_memory_ids(
                        session,
                        user_id,
                        effective_role_id,
                    )
                    memories = filter_superseded_memories(memories, superseded)
                    # Also load inter-agent relationship memories
                    agent_memories = await db_service.get_agent_relationship_memories(
                        session, effective_role_id, limit=5
                    )
                    all_memories = list(memories) + list(agent_memories)
                    if all_memories:
                        memory_ctx = compose_memory_context(all_memories)
            except Exception:
                pass  # Memory is best-effort

            # Load pending peer messages
            message_ctx = ""
            try:
                from src.db import service as db_service
                from src.db.session import get_db_session

                async with get_db_session() as session:
                    pending_msgs = await db_service.get_pending_messages(
                        session,
                        effective_role_id,
                        limit=10,
                    )
                    message_ctx = compose_message_context(pending_msgs)
                    if pending_msgs:
                        msg_ids = [m.id for m in pending_msgs]
                        await db_service.mark_messages_delivered(
                            session,
                            msg_ids,
                        )
            except Exception:
                pass  # Messages are best-effort

            role_context = compose_role_context(
                department=dept,
                role=role,
                memory_context=memory_ctx,
                registry=registry,
                pending_messages=message_ctx,
            )

            return {
                "role_context": role_context,
                "role_name": role.name,
                "department_id": role.department_id,
                "is_manager": bool(getattr(role, "manages", ())),
            }

        context_data = await ctx.step.run("build-context", build_context)

        if "error" in context_data:

            async def post_error():
                from src.connectors.slack import SlackConnector

                connector = SlackConnector()
                return connector.send_thread_reply(
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    text=f":x: {context_data['error']}",
                )

            await ctx.step.run("post-error", post_error)
            return {"status": "error", "error": context_data["error"]}

        # -- Step 4: Get thread history from Slack --
        async def get_history():
            from src.connectors.slack import SlackConnector

            connector = SlackConnector()
            history = connector.get_thread_history(
                channel_id=channel_id,
                thread_ts=thread_ts,
                limit=50,
            )
            # Exclude the current message (last in thread)
            # to avoid duplication since we pass it separately
            if history and history[-1].get("text") == message_text:
                history = history[:-1]
            return history

        thread_history = await ctx.step.run("get-history", get_history)

        # -- Step 5: Run agent conversation turn --
        async def run_turn():
            from src.agent.core import SideraAgent
            from src.mcp_servers.delegation import (
                clear_delegation_context,
                get_delegation_results,
                set_delegation_context,
            )
            from src.mcp_servers.evolution import (
                clear_proposer_context,
                set_proposer_context,
            )
            from src.mcp_servers.memory import (
                clear_memory_context,
                set_memory_context,
            )
            from src.mcp_servers.messaging import (
                clear_messaging_context,
                set_messaging_context,
            )
            from src.mcp_servers.skill_runner import (
                clear_skill_runner_context,
                set_skill_runner_context,
            )
            from src.mcp_servers.working_group import (
                clear_working_group_context,
                get_pending_working_groups,
                set_working_group_context,
            )
            from src.skills.db_loader import load_registry_with_db

            is_manager = context_data.get("is_manager", False)
            delegation_results: list[dict] = []

            registry = await load_registry_with_db()

            # Set delegation + working group context for managers
            if is_manager:
                set_delegation_context(effective_role_id, registry)
                set_working_group_context(effective_role_id, registry)

            # Set memory context so save_memory tool works
            source_user_name = ctx.event.data.get("source_user_name", "")
            dept_id_for_ctx = context_data.get("department_id", "")
            set_memory_context(
                effective_role_id,
                dept_id_for_ctx,
                user_id,
                source_user_name,
            )

            # Set messaging context so messaging tools work
            set_messaging_context(
                effective_role_id,
                dept_id_for_ctx,
                registry,
            )

            # Set proposer context so propose_role_change tool works
            set_proposer_context(effective_role_id, dept_id_for_ctx)

            # Set skill runner context so run_skill tool works
            set_skill_runner_context(
                effective_role_id,
                registry,
                user_id,
                context_data.get("role_context", ""),
            )

            agent = SideraAgent()
            try:
                result = await agent.run_conversation_turn(
                    role_id=effective_role_id,
                    role_context=context_data["role_context"],
                    thread_history=thread_history,
                    current_message=message_text,
                    user_id=user_id,
                    bot_user_id="",
                    turn_number=thread_data["turn_count"] + 1,
                    is_manager=is_manager,
                    channel_id=channel_id,
                    message_ts=ctx.event.data.get("message_ts", ""),
                    image_content=image_content,
                    user_clearance=user_clearance,
                )
            finally:
                clear_skill_runner_context()
                clear_memory_context()
                clear_messaging_context()
                clear_proposer_context()
                if is_manager:
                    delegation_results = get_delegation_results()
                    clear_delegation_context()
                    # Dispatch any working groups proposed during turn
                    for wg_proposal in get_pending_working_groups():
                        wg_proposal["user_id"] = user_id
                        wg_proposal["channel_id"] = channel_id
                        wg_proposal["thread_ts"] = ctx.event.data.get("thread_ts", "")
                        await inngest_client.send(
                            inngest.Event(
                                name="sidera/working_group.run",
                                data=wg_proposal,
                            ),
                        )
                    clear_working_group_context()

            # Compute total cost including delegations
            total_cost = result.cost
            if delegation_results:
                delegation_cost = sum(
                    dr.get("cost", {}).get("total_cost_usd", 0.0)
                    for dr in delegation_results
                    if isinstance(dr.get("cost"), dict)
                )
                if isinstance(total_cost, dict):
                    total_cost = {
                        **total_cost,
                        "delegation_cost_usd": delegation_cost,
                        "total_cost_usd": (total_cost.get("total_cost_usd", 0.0) + delegation_cost),
                    }

            return {
                "response_text": result.response_text,
                "cost": total_cost,
                "session_id": result.session_id,
                "turn_number": result.turn_number,
                "delegation_results": delegation_results,
            }

        turn_result = await ctx.step.run("run-turn", run_turn)

        # -- Step 6: Post reply to Slack thread --
        async def post_reply():
            from src.connectors.slack import SlackConnector

            connector = SlackConnector()
            role_label = context_data.get("role_name", effective_role_id)
            response_text = turn_result["response_text"]
            # Strip existing prefix to prevent duplication
            prefixes = [f"*{role_label}:*\n", f"{role_label}:\n"]
            for prefix_variant in prefixes:
                if response_text.startswith(prefix_variant):
                    response_text = response_text[len(prefix_variant) :]
                    break

            # Redirect long responses to Google Drive (summary + link)
            from src.api.routes.slack import _maybe_redirect_to_drive

            response_text = _maybe_redirect_to_drive(
                response_text,
                role_label,
            )

            prefixed_text = f"*{role_label}:*\n{response_text}"
            return connector.send_thread_reply(
                channel_id=channel_id,
                thread_ts=thread_ts,
                text=prefixed_text,
            )

        slack_result = await ctx.step.run("post-reply", post_reply)

        # -- Step 7: Update thread activity --
        async def update_thread():
            from src.db import service as db_service
            from src.db.session import get_db_session

            cost_increment = turn_result.get("cost", {}).get("total_cost_usd", 0.0)
            async with get_db_session() as session:
                await db_service.update_conversation_thread_activity(
                    session=session,
                    thread_ts=thread_ts,
                    cost_increment=cost_increment,
                )
            return {"updated": True}

        await ctx.step.run("update-thread", update_thread)

        # -- Log to audit --
        async def log_audit():
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.log_event(
                    session=session,
                    user_id=user_id,
                    event_type="conversation_turn",
                    event_data={
                        "role_id": effective_role_id,
                        "thread_ts": thread_ts,
                        "turn_number": turn_result["turn_number"],
                        "response_length": len(turn_result["response_text"]),
                        "cost": turn_result.get("cost", {}),
                    },
                    source="conversation_workflow",
                )
            return {"logged": True}

        await ctx.step.run("log-audit", log_audit)

        # -- Step 8: Extract and save memories (LLM-powered) --
        async def extract_conversation_memories():
            try:
                import structlog

                from src.db import service as db_service
                from src.db.session import get_db_session
                from src.mcp_servers.memory import (
                    extract_conversation_memories_llm,
                )
                from src.skills.db_loader import load_registry_with_db

                _logger = structlog.get_logger(__name__)
                registry = await load_registry_with_db()
                _role = registry.get_role(effective_role_id)
                dept_id = _role.department_id if _role else ""
                role_name = _role.name if _role else effective_role_id

                entries = await extract_conversation_memories_llm(
                    role_id=effective_role_id,
                    role_name=role_name,
                    department_id=dept_id,
                    user_message=message_text,
                    agent_response=turn_result["response_text"],
                    user_id=user_id,
                    thread_history=thread_history,
                    source_user_name=ctx.event.data.get("source_user_name", ""),
                )
                if entries:
                    async with get_db_session() as session:
                        for entry in entries:
                            await db_service.save_memory(
                                session=session,
                                user_id=user_id,
                                **entry,
                            )
                return {"memories_saved": len(entries)}
            except Exception as exc:
                import structlog

                _logger = structlog.get_logger(__name__)
                _logger.warning(
                    "conversation.memory_skip",
                    error=str(exc),
                )
                return {"memories_saved": 0}

        await ctx.step.run(
            "extract-conversation-memories",
            extract_conversation_memories,
        )

        return {
            "status": "success",
            "role_id": effective_role_id,
            "turn_number": turn_result["turn_number"],
            "response_length": len(turn_result["response_text"]),
            "cost": turn_result.get("cost", {}),
            "slack_ok": slack_result.get("ok", False),
        }

    except inngest.NonRetriableError:
        raise
    except Exception as dlq_exc:
        from src.middleware.sentry_setup import capture_exception

        capture_exception(dlq_exc)

        # Post error to thread
        try:
            from src.connectors.slack import SlackConnector

            connector = SlackConnector()
            connector.send_thread_reply(
                channel_id=channel_id,
                thread_ts=thread_ts,
                text=(
                    ":x: Sorry, I encountered an error processing your message. Please try again."
                ),
            )
        except Exception:
            pass

        # Record to DLQ
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.record_failed_run(
                    session=session,
                    workflow_name="conversation_turn",
                    event_name=ctx.event.name,
                    event_data=ctx.event.data,
                    error_message=str(dlq_exc),
                    error_type=type(dlq_exc).__name__,
                    user_id=user_id,
                    run_id=ctx.run_id,
                )
        except Exception:
            pass
        raise


# =====================================================================
# 10. Meeting Join Workflow
# =====================================================================


@inngest_client.create_function(
    fn_id="meeting-join",
    trigger=inngest.TriggerEvent(event="sidera/meeting.join"),
    retries=1,
)
async def meeting_join_workflow(ctx: inngest.Context) -> dict:
    """Join a live meeting as a voice participant.

    Triggered by ``sidera/meeting.join`` events emitted by the Slack
    ``/sidera meeting join`` command.

    Steps:
    1. Validate role exists
    2. Join the meeting via MeetingSessionManager (listen-only)
    3. Wait for meeting to end (or timeout at max_duration)

    Expected event data:
    - meeting_url (str, required): Google Meet / Zoom URL
    - role_id (str, required): The role that will join (listen-only)
    - user_id (str, required): Who initiated the join
    - channel_id (str, optional): Slack channel for notifications
    """
    meeting_url = ctx.event.data.get("meeting_url", "")
    role_id = ctx.event.data.get("role_id", "")
    user_id = ctx.event.data.get("user_id", "default")
    channel_id = ctx.event.data.get("channel_id", "")

    if not meeting_url:
        raise inngest.NonRetriableError("meeting_url is required")
    if not role_id:
        raise inngest.NonRetriableError("role_id is required")

    try:
        # Step 1: Validate role exists
        async def validate_role() -> dict:
            from src.skills.db_loader import load_registry_with_db

            registry = await load_registry_with_db()
            role = registry.get_role(role_id)
            if role is None:
                raise inngest.NonRetriableError(f"Role '{role_id}' not found in registry")
            return {
                "role_name": role.name,
                "is_manager": bool(role.manages),
            }

        await ctx.step.run("validate-role", validate_role)

        # Step 2: Join the meeting
        async def join_meeting() -> dict:
            from src.meetings.session import get_meeting_manager

            manager = get_meeting_manager()
            meeting_ctx = await manager.join(
                meeting_url=meeting_url,
                role_id=role_id,
                user_id=user_id,
                channel_id=channel_id,
            )
            return {
                "bot_id": meeting_ctx.bot_id,
                "meeting_id": meeting_ctx.meeting_id,
                "role_name": meeting_ctx.role_name,
            }

        join_data = await ctx.step.run("join-meeting", join_meeting)

        _workflow_logger.info(
            "meeting_join.success",
            meeting_url=meeting_url,
            role_id=role_id,
            bot_id=join_data["bot_id"],
            role_name=join_data["role_name"],
        )

        return {
            "status": "joined",
            "meeting_url": meeting_url,
            "role_id": role_id,
            "role_name": join_data["role_name"],
            "bot_id": join_data["bot_id"],
            "meeting_id": join_data["meeting_id"],
        }

    except inngest.NonRetriableError:
        raise
    except Exception as dlq_exc:
        from src.middleware.sentry_setup import capture_exception

        capture_exception(dlq_exc)

        # Notify Slack of failure
        try:
            from src.connectors.slack import SlackConnector

            connector = SlackConnector()
            connector.send_alert(
                channel_id=channel_id or None,
                text=(
                    f":x: Failed to join meeting as *{role_id}*: {dlq_exc}\n"
                    f"Meeting URL: {meeting_url}"
                ),
            )
        except Exception:
            pass

        # Record to DLQ
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.record_failed_run(
                    session=session,
                    workflow_name="meeting_join",
                    event_name=ctx.event.name,
                    event_data=ctx.event.data,
                    error_message=str(dlq_exc),
                    error_type=type(dlq_exc).__name__,
                    user_id=user_id,
                    run_id=ctx.run_id,
                )
        except Exception:
            pass
        raise


# =====================================================================
# 11. Meeting End Workflow
# =====================================================================


@inngest_client.create_function(
    fn_id="meeting-end",
    trigger=inngest.TriggerEvent(event="sidera/meeting.ended"),
    retries=2,
)
async def meeting_end_workflow(ctx: inngest.Context) -> dict:
    """Post-call processing after a meeting ends.

    Triggered by ``sidera/meeting.ended`` events emitted by
    ``MeetingSessionManager.leave()``.

    Steps:
    1. Load meeting session from DB (full transcript)
    2. Summarize transcript + extract action items via LLM
    3. Save summary + action items to DB
    4. Emit ``sidera/manager.run`` with meeting_context if role is a manager
    5. Post meeting summary to Slack

    Expected event data:
    - bot_id (str, required): Recall.ai bot UUID
    - meeting_id (int, required): DB meeting_session ID
    - role_id (str, required): Which role was in the meeting
    - user_id (str, required): Who initiated the meeting
    - channel_id (str, optional): Slack channel for output
    - agent_turns (int): Number of times agent spoke
    - transcript_entries (int): Number of transcript lines
    - total_cost_usd (float): Total LLM cost during meeting
    """
    bot_id = ctx.event.data.get("bot_id", "")
    meeting_id = ctx.event.data.get("meeting_id", 0)
    role_id = ctx.event.data.get("role_id", "")
    user_id = ctx.event.data.get("user_id", "default")
    channel_id = ctx.event.data.get("channel_id", "")

    if not bot_id or not meeting_id:
        raise inngest.NonRetriableError("bot_id and meeting_id are required")

    try:
        # Step 1: Load meeting session and transcript from DB
        async def load_meeting() -> dict:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                meeting = await db_service.get_meeting_session(
                    session,
                    meeting_id,
                )
            if meeting is None:
                raise inngest.NonRetriableError(f"Meeting session {meeting_id} not found")

            transcript_json = meeting.transcript_json or []
            return {
                "meeting_id": meeting.id,
                "meeting_url": meeting.meeting_url or "",
                "role_id": meeting.role_id or role_id,
                "transcript": transcript_json,
                "transcript_length": len(transcript_json),
                "duration_seconds": meeting.duration_seconds or 0,
                "agent_turns": meeting.agent_turns or 0,
                "total_cost_usd": float(meeting.total_cost_usd or 0),
                "participants_json": meeting.participants_json or [],
            }

        meeting_data = await ctx.step.run("load-meeting", load_meeting)

        # Step 2: Summarize transcript + extract action items via LLM
        async def summarize_meeting() -> dict:
            from src.agent.core import SideraAgent
            from src.skills.db_loader import load_registry_with_db

            # Build transcript text
            transcript = meeting_data.get("transcript", [])
            if not transcript:
                return {
                    "summary": "No transcript was captured during this meeting.",
                    "action_items": [],
                }

            transcript_text = "\n".join(
                f"{entry.get('speaker', 'Unknown')}: {entry.get('text', '')}"
                for entry in transcript
            )

            # Get role context
            registry = await load_registry_with_db()
            role = registry.get_role(meeting_data.get("role_id", ""))
            role_name = role.name if role else meeting_data.get("role_id", "Agent")

            # Use Sonnet for summarization
            agent = SideraAgent()
            participants = ", ".join(
                p.get("name", "Unknown") for p in meeting_data.get("participants_json", [])
            )
            prompt = (
                f"You are {role_name}. You just attended a meeting. "
                f"Duration: {meeting_data.get('duration_seconds', 0) // 60} minutes. "
                f"Participants: {participants}.\n\n"
                f"## Meeting Transcript\n\n{transcript_text[:8000]}\n\n"
                "## Instructions\n\n"
                "1. Write a concise meeting summary "
                "(3-5 bullet points of key decisions "
                "and discussion topics).\n"
                f"2. Extract specific action items with assignees where identifiable.\n"
                f"3. Note any items that need your department's attention or follow-up.\n\n"
                f"Format your response as:\n"
                f"## Summary\n[bullet points]\n\n"
                f"## Action Items\n[numbered list with assignees]\n\n"
                f"## Department Follow-ups\n[items needing your attention]"
            )

            result = await agent.run_conversation_turn(
                role_id=meeting_data.get("role_id", ""),
                role_context=role.persona if role else "",
                thread_history=[],
                current_message=prompt,
                user_id=user_id,
                bot_user_id="",
                turn_number=1,
            )

            # Extract action items as structured data
            response_text = result.response_text
            action_items = []
            if "## Action Items" in response_text:
                ai_section = response_text.split("## Action Items")[1]
                if "##" in ai_section:
                    ai_section = ai_section.split("##")[0]
                # Parse numbered items
                import re

                items = re.findall(
                    r"\d+\.\s*(.+?)(?=\n\d+\.|\n##|\Z)",
                    ai_section,
                    re.DOTALL,
                )
                action_items = [
                    {"item": item.strip(), "status": "pending"} for item in items if item.strip()
                ]

            return {
                "summary": response_text,
                "action_items": action_items,
                "cost": result.cost,
            }

        summary_data = await ctx.step.run(
            "summarize-meeting",
            summarize_meeting,
        )

        # Step 3: Save summary + action items to DB
        async def save_summary() -> dict:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.update_meeting_transcript(
                    session=session,
                    meeting_id=meeting_id,
                    transcript_json=meeting_data.get("transcript", []),
                    transcript_summary=summary_data.get("summary", ""),
                )
                # Update meeting status to ended and store action items
                from sqlalchemy import update as sql_update

                from src.models.schema import MeetingSession

                await session.execute(
                    sql_update(MeetingSession)
                    .where(MeetingSession.id == meeting_id)
                    .values(
                        action_items_json=summary_data.get("action_items", []),
                    )
                )
                await session.commit()
            return {"saved": True}

        await ctx.step.run("save-summary", save_summary)

        # Step: Sync meeting summary to Google Drive (living documents)
        async def sync_meeting_to_drive() -> dict:
            try:
                from src.skills.document_sync import sync_role_output_to_drive

                summary_text = summary_data.get("summary", "")
                action_items = summary_data.get("action_items", [])
                return await sync_role_output_to_drive(
                    role_id=role_id,
                    output_type="meetings",
                    content=summary_text,
                    metadata={
                        "duration_seconds": meeting_data.get("duration_seconds", 0),
                        "action_items_count": len(action_items),
                        "cost_usd": meeting_data.get("total_cost_usd", 0),
                    },
                )
            except Exception as exc:
                _workflow_logger.warning(
                    "meeting_end.drive_sync_failed",
                    error=str(exc),
                )
                return {"synced": False, "error": str(exc)}

        await ctx.step.run("sync-meeting-to-drive", sync_meeting_to_drive)

        # Step 4: Post summary to Slack
        async def post_summary() -> dict:
            from src.connectors.slack import SlackConnector

            connector = SlackConnector()
            duration_min = meeting_data.get("duration_seconds", 0) // 60
            agent_turns = meeting_data.get("agent_turns", 0)
            action_count = len(summary_data.get("action_items", []))
            cost = meeting_data.get("total_cost_usd", 0)
            summary_cost = summary_data.get("cost", {}).get("total_cost_usd", 0)
            total_cost = cost + summary_cost

            header = (
                f":microphone: *Meeting Summary* — {meeting_data.get('role_id', 'agent')}\n"
                f"Duration: {duration_min} min · Agent spoke {agent_turns} times · "
                f"{action_count} action items · Cost: ${total_cost:.2f}\n"
                f"Meeting: {meeting_data.get('meeting_url', 'N/A')}\n\n"
            )

            full_text = header + summary_data.get("summary", "No summary available.")
            return connector.send_alert(
                channel_id=channel_id or None,
                text=full_text,
            )

        slack_result = await ctx.step.run("post-summary", post_summary)

        # Step 5: If role is a manager, emit sidera/manager.run with meeting context
        async def trigger_delegation() -> dict:
            from src.skills.db_loader import load_registry_with_db

            registry = await load_registry_with_db()
            role = registry.get_role(meeting_data.get("role_id", ""))

            if role is None or not role.manages:
                return {"delegated": False, "reason": "not_a_manager"}

            # Emit manager.run event with meeting_context injected
            event_data = {
                "user_id": user_id,
                "role_id": meeting_data.get("role_id", ""),
                "channel_id": channel_id,
                "meeting_context": {
                    "meeting_id": meeting_id,
                    "summary": summary_data.get("summary", ""),
                    "action_items": summary_data.get("action_items", []),
                    "transcript_length": meeting_data.get("transcript_length", 0),
                    "duration_seconds": meeting_data.get("duration_seconds", 0),
                    "participants": meeting_data.get("participants_json", []),
                },
            }

            await ctx.step.send_event(
                "trigger-delegation",
                inngest.Event(
                    name="sidera/manager.run",
                    data=event_data,
                ),
            )

            return {"delegated": True, "role_id": meeting_data.get("role_id", "")}

        delegation_result = await ctx.step.run(
            "trigger-delegation",
            trigger_delegation,
        )

        # Step 6: Log audit event
        async def log_audit() -> dict:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.log_event(
                    session=session,
                    user_id=user_id,
                    event_type="meeting_ended",
                    event_data={
                        "meeting_id": meeting_id,
                        "bot_id": bot_id,
                        "role_id": meeting_data.get("role_id", ""),
                        "duration_seconds": meeting_data.get("duration_seconds", 0),
                        "agent_turns": meeting_data.get("agent_turns", 0),
                        "transcript_length": meeting_data.get("transcript_length", 0),
                        "action_items_count": len(summary_data.get("action_items", [])),
                        "delegated": delegation_result.get("delegated", False),
                        "total_cost_usd": meeting_data.get("total_cost_usd", 0),
                    },
                    source="meeting_end_workflow",
                )
            return {"logged": True}

        await ctx.step.run("log-audit", log_audit)

        return {
            "status": "completed",
            "meeting_id": meeting_id,
            "role_id": meeting_data.get("role_id", ""),
            "duration_seconds": meeting_data.get("duration_seconds", 0),
            "transcript_length": meeting_data.get("transcript_length", 0),
            "action_items": len(summary_data.get("action_items", [])),
            "delegated": delegation_result.get("delegated", False),
            "slack_ok": slack_result.get("ok", False),
        }

    except inngest.NonRetriableError:
        raise
    except Exception as dlq_exc:
        from src.middleware.sentry_setup import capture_exception

        capture_exception(dlq_exc)
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.record_failed_run(
                    session=session,
                    workflow_name="meeting_end",
                    event_name=ctx.event.name,
                    event_data=ctx.event.data,
                    error_message=str(dlq_exc),
                    error_type=type(dlq_exc).__name__,
                    user_id=user_id,
                    run_id=ctx.run_id,
                )
        except Exception:
            pass
        raise


# =====================================================================
# 12. Data Retention Workflow
# =====================================================================


@inngest_client.create_function(
    fn_id="sidera-data-retention",
    trigger=inngest.TriggerCron(cron="0 3 * * *"),  # 3 AM daily
)
async def data_retention_workflow(
    ctx: inngest.Context,
    step: inngest.Step,
) -> dict:
    """Purge expired data based on retention settings.

    Runs at 3 AM daily. Each table has a configurable retention period
    (in days, 0 = keep forever). Uses batch deletion to avoid long locks.
    """
    from datetime import datetime, timedelta, timezone

    import structlog

    from src.config import settings
    from src.db import service as db_service
    from src.db.session import get_db_session

    logger = structlog.get_logger("sidera.workflows.retention")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    total_deleted: dict[str, int] = {}

    # Table → (setting_name, purge_function, uses_date_not_datetime)
    retention_map: list[tuple[str, int, str, bool]] = [
        ("audit_log", settings.retention_audit_log_days, "purge_old_audit_logs", False),
        (
            "analysis_results",
            settings.retention_analysis_results_days,
            "purge_old_analysis_results",
            False,
        ),
        ("cost_tracking", settings.retention_cost_tracking_days, "purge_old_cost_tracking", False),
        (
            "decided_approvals",
            settings.retention_decided_approvals_days,
            "purge_decided_approvals",
            False,
        ),
        (
            "resolved_failed_runs",
            settings.retention_resolved_failed_runs_days,
            "purge_resolved_failed_runs",
            False,
        ),
        ("daily_metrics", settings.retention_daily_metrics_days, "purge_old_daily_metrics", True),
        (
            "inactive_threads",
            settings.retention_inactive_threads_days,
            "purge_inactive_threads",
            False,
        ),
        (
            "archived_memories",
            settings.retention_cold_memories_days,
            "purge_archived_memories",
            False,
        ),
    ]

    for table_name, retention_days, func_name, uses_date in retention_map:
        if retention_days <= 0:
            total_deleted[table_name] = 0
            continue

        async def _purge(
            _tbl=table_name,
            _days=retention_days,
            _fn=func_name,
            _uses_date=uses_date,
        ) -> int:
            cutoff = now - timedelta(days=_days)
            purge_func = getattr(db_service, _fn)
            try:
                async with get_db_session() as session:
                    if _uses_date:
                        count = await purge_func(session, cutoff.date())
                    else:
                        count = await purge_func(session, cutoff)
                    await session.commit()
                return count
            except Exception as exc:
                logger.error(
                    "retention.purge_failed",
                    table=_tbl,
                    error=str(exc),
                )
                return 0

        count = await step.run(f"purge-{table_name}", _purge)
        total_deleted[table_name] = count

    # Expire stale peer-to-peer messages
    async def _expire_messages() -> int:
        try:
            async with get_db_session() as session:
                count = await db_service.expire_stale_messages(session)
                await session.commit()
            return count
        except Exception as exc:
            logger.error(
                "retention.expire_messages_failed",
                error=str(exc),
            )
            return 0

    expired_msgs = await step.run("expire-stale-messages", _expire_messages)
    total_deleted["stale_messages"] = expired_msgs

    logger.info("retention.complete", deleted=total_deleted)
    return total_deleted


# =====================================================================
# Heartbeat runner workflow (proactive check-ins)
# =====================================================================


@inngest_client.create_function(
    fn_id="sidera-heartbeat-runner",
    name="Sidera Heartbeat Runner",
    trigger=inngest.TriggerEvent(event="sidera/heartbeat.run"),
    retries=1,
)
async def heartbeat_runner_workflow(ctx: inngest.Context) -> dict:
    """Run a proactive heartbeat check-in for a role.

    The agent gets an open-ended investigative prompt and freely uses
    tools to check its domain. Unlike briefing runs (which execute
    specific skills), heartbeats let the agent decide what to investigate.

    Expected event data:
    - role_id (str, required): Which role to run the heartbeat for
    - user_id (str, optional): Defaults to "heartbeat"
    - channel_id (str, optional): Slack channel for findings
    """
    try:
        role_id = ctx.event.data.get("role_id", "")
        user_id = ctx.event.data.get("user_id", "heartbeat")
        channel_id = ctx.event.data.get("channel_id", "")

        if not role_id:
            raise inngest.NonRetriableError("role_id is required in event data")

        # Step 1: Check cooldown — skip if ran recently
        async def check_cooldown() -> dict:
            from datetime import datetime, timedelta, timezone

            from src.config import settings
            from src.db import service as db_service
            from src.db.session import get_db_session

            cooldown_minutes = settings.heartbeat_cooldown_minutes
            cutoff = datetime.now(timezone.utc) - timedelta(
                minutes=cooldown_minutes,
            )

            try:
                async with get_db_session() as session:
                    # Check audit_log for recent heartbeat runs
                    recent = await db_service.get_recent_audit_events(
                        session,
                        user_id=user_id,
                        event_type="heartbeat",
                        limit=1,
                    )
                    if recent:
                        last_run = recent[0]
                        last_ts = getattr(last_run, "created_at", None)
                        if last_ts and last_ts > cutoff:
                            # Check it was for the same role
                            event_data = (
                                getattr(
                                    last_run,
                                    "event_data",
                                    {},
                                )
                                or {}
                            )
                            if event_data.get("role_id") == role_id:
                                return {
                                    "should_run": False,
                                    "reason": "cooldown",
                                }
            except Exception:
                pass  # On error, proceed with heartbeat

            return {"should_run": True, "reason": ""}

        cooldown = await ctx.step.run("check-cooldown", check_cooldown)
        if not cooldown.get("should_run", True):
            return {
                "status": "skipped",
                "reason": cooldown.get("reason", "cooldown"),
                "role_id": role_id,
            }

        # Step 2: Load context (role definition, department, memory, messages)
        async def load_context() -> dict:
            from src.db import service as db_service
            from src.db.session import get_db_session
            from src.mcp_servers.messaging import compose_message_context
            from src.skills.db_loader import load_registry_with_db
            from src.skills.executor import compose_role_context
            from src.skills.memory import compose_memory_context

            registry = await load_registry_with_db()
            role = registry.get_role(role_id)
            if role is None:
                return {"error": f"Role '{role_id}' not found"}

            dept = registry.get_department(role.department_id)
            is_manager = bool(getattr(role, "manages", ()))
            heartbeat_model = getattr(role, "heartbeat_model", "")

            # Load role memories
            memory_context = ""
            try:
                from src.skills.memory import filter_superseded_memories

                async with get_db_session() as session:
                    memories = await db_service.get_role_memories(
                        session,
                        user_id,
                        role_id,
                        min_confidence=0.5,
                        limit=20,
                    )
                    superseded = await db_service.get_superseded_memory_ids(
                        session,
                        user_id,
                        role_id,
                    )
                    memories = filter_superseded_memories(memories, superseded)

                    # Load inter-agent relationship memories
                    agent_memories = await db_service.get_agent_relationship_memories(
                        session, role_id, limit=5
                    )
                    all_memories = list(memories) + list(agent_memories)

                    memory_context = compose_memory_context(all_memories)
            except Exception:
                pass

            # Load pending peer messages
            message_context = ""
            pending_message_summary = ""
            try:
                async with get_db_session() as session:
                    pending_msgs = await db_service.get_pending_messages(
                        session,
                        role_id,
                        limit=10,
                    )
                    message_context = compose_message_context(pending_msgs)
                    if pending_msgs:
                        pending_message_summary = (
                            f"You have {len(pending_msgs)} unread message(s) from other roles."
                        )
                        # Mark as delivered
                        msg_ids = [m.id for m in pending_msgs]
                        await db_service.mark_messages_delivered(
                            session,
                            msg_ids,
                        )
            except Exception:
                pass

            role_context = compose_role_context(
                department=dept,
                role=role,
                memory_context=memory_context,
                registry=registry,
                pending_messages=message_context,
            )

            return {
                "role_context": role_context,
                "role_name": role.name,
                "department_id": role.department_id,
                "is_manager": is_manager,
                "heartbeat_model": heartbeat_model,
                "pending_message_summary": pending_message_summary,
            }

        context_data = await ctx.step.run("load-context", load_context)

        if context_data.get("error"):
            raise inngest.NonRetriableError(context_data["error"])

        # Step 3: Run heartbeat
        async def run_heartbeat() -> dict:
            from src.agent.core import SideraAgent
            from src.mcp_servers.evolution import (
                clear_proposer_context,
                set_proposer_context,
            )
            from src.mcp_servers.messaging import (
                clear_messaging_context,
                set_messaging_context,
            )
            from src.skills.db_loader import load_registry_with_db

            registry = await load_registry_with_db()
            dept_id = context_data.get("department_id", "")
            set_messaging_context(role_id, dept_id, registry)
            set_proposer_context(role_id, dept_id)

            agent = SideraAgent()
            try:
                result = await agent.run_heartbeat_turn(
                    role_id=role_id,
                    role_context=context_data.get("role_context", ""),
                    user_id=user_id,
                    is_manager=context_data.get("is_manager", False),
                    heartbeat_model=context_data.get(
                        "heartbeat_model",
                        "",
                    ),
                    pending_messages_summary=context_data.get(
                        "pending_message_summary",
                        "",
                    ),
                )
            finally:
                clear_messaging_context()
                clear_proposer_context()

            return {
                "output_text": result.output_text,
                "cost": result.cost,
                "tool_calls_used": result.tool_calls_used,
                "has_findings": result.has_findings,
            }

        heartbeat_result = await ctx.step.run(
            "run-heartbeat",
            run_heartbeat,
        )

        # Step 4: Post findings to Slack (only if noteworthy)
        async def post_findings() -> dict:
            if not heartbeat_result.get("has_findings"):
                return {"posted": False}

            try:
                from src.config import settings
                from src.connectors.slack import SlackConnector

                target_channel = channel_id or settings.slack_channel_id
                if not target_channel:
                    return {"posted": False, "reason": "no_channel"}

                slack = SlackConnector()
                role_name = context_data.get("role_name", role_id)
                output = heartbeat_result.get("output_text", "")

                message = f"🔍 *{role_name} — Proactive Check-In*\n\n{output}"
                await slack.send_alert(
                    channel=target_channel,
                    text=message,
                )
                return {"posted": True}
            except Exception as exc:
                _workflow_logger.warning(
                    "heartbeat.slack_post_failed",
                    error=str(exc),
                )
                return {"posted": False, "reason": str(exc)}

        await ctx.step.run("post-findings", post_findings)

        # Step 5: Save audit log + extract memories
        async def save_audit() -> dict:
            from datetime import date

            from src.db import service as db_service
            from src.db.session import get_db_session

            try:
                async with get_db_session() as session:
                    # Audit log entry
                    await db_service.log_event(
                        session=session,
                        user_id=user_id,
                        event_type="heartbeat",
                        event_data={
                            "role_id": role_id,
                            "department_id": context_data.get("department_id", ""),
                            "has_findings": heartbeat_result.get("has_findings", False),
                            "tool_calls_used": heartbeat_result.get("tool_calls_used", 0),
                            "cost": heartbeat_result.get("cost", {}),
                        },
                        role_id=role_id,
                        department_id=context_data.get("department_id", ""),
                    )

                    # Record cost
                    cost = heartbeat_result.get("cost", {})
                    if cost.get("total_cost_usd"):
                        await db_service.record_cost(
                            session=session,
                            user_id=user_id,
                            run_date=date.today(),
                            model=cost.get("model", "unknown"),
                            cost_usd=cost["total_cost_usd"],
                            operation=f"heartbeat_{role_id}",
                        )

                    await session.commit()

                return {
                    "audit_saved": True,
                }
            except Exception as exc:
                _workflow_logger.warning(
                    "heartbeat.audit_save_failed",
                    error=str(exc),
                )
                return {"audit_saved": False}

        await ctx.step.run("save-audit", save_audit)

        return {
            "status": "completed",
            "role_id": role_id,
            "has_findings": heartbeat_result.get("has_findings", False),
            "tool_calls_used": heartbeat_result.get("tool_calls_used", 0),
            "cost": heartbeat_result.get("cost", {}),
        }

    except inngest.NonRetriableError:
        raise
    except Exception as dlq_exc:
        from src.middleware.sentry_setup import capture_exception

        capture_exception(dlq_exc)
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.record_failed_run(
                    session=session,
                    workflow_name="heartbeat_runner",
                    event_name=ctx.event.name,
                    event_data=ctx.event.data,
                    error_message=str(dlq_exc),
                    error_type=type(dlq_exc).__name__,
                    user_id=ctx.event.data.get("user_id", ""),
                    run_id=ctx.run_id,
                )
        except Exception:
            pass
        raise


# =====================================================================
# Workflow 14: Memory Consolidation (Sunday 4 AM)
# =====================================================================


@inngest_client.create_function(
    fn_id="memory-consolidation",
    trigger=inngest.TriggerCron(cron="0 4 * * 0"),  # Sunday 4 AM
    retries=1,
)
async def memory_consolidation_workflow(ctx: inngest.Context) -> dict:
    """Weekly memory consolidation — merges duplicate/overlapping memories.

    Iterates over all (user_id, role_id) pairs with unconsolidated
    memories, batches them into groups of 50, and runs Haiku to
    identify and merge duplicates.
    """
    try:
        # Step 1: Get all role pairs with unconsolidated memories
        async def get_role_pairs() -> list[list[str]]:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                pairs = await db_service.get_distinct_memory_role_pairs(session)
            # Return as list of lists (JSON-serializable)
            return [[uid, rid] for uid, rid in pairs]

        pairs = await ctx.step.run("get-role-pairs", get_role_pairs)

        total_stats: dict[str, Any] = {
            "roles_processed": 0,
            "consolidated_count": 0,
            "originals_marked": 0,
            "total_cost_usd": 0.0,
            "errors": [],
        }

        # Step 2: Consolidate each pair
        for pair in pairs:
            uid, rid = pair[0], pair[1]
            step_id = f"consolidate-{uid}-{rid}"

            async def consolidate_pair(
                _uid: str = uid,
                _rid: str = rid,
            ) -> dict:
                from src.db import service as db_service
                from src.db.session import get_db_session
                from src.skills.consolidation import consolidate_role_memories

                async with get_db_session() as session:
                    memories = await db_service.get_unconsolidated_memories(
                        session,
                        _uid,
                        _rid,
                        limit=100,
                        min_age_days=7,
                    )

                if len(memories) < 3:
                    return {"skipped": True, "reason": "too_few", "count": len(memories)}

                # Get department_id from first memory
                dept_id = getattr(memories[0], "department_id", "") or ""

                # Batch into groups of 50
                batch_stats: dict[str, Any] = {
                    "consolidated_count": 0,
                    "originals_marked": 0,
                    "cost_usd": 0.0,
                    "errors": [],
                }

                for i in range(0, len(memories), 50):
                    batch = memories[i : i + 50]
                    result = await consolidate_role_memories(
                        _uid,
                        _rid,
                        dept_id,
                        batch,
                    )
                    batch_stats["consolidated_count"] += result["consolidated_count"]
                    batch_stats["originals_marked"] += result["originals_marked"]
                    batch_stats["cost_usd"] += result["cost_usd"]
                    batch_stats["errors"].extend(result["errors"])

                return batch_stats

            try:
                pair_result = await ctx.step.run(step_id, consolidate_pair)
                if not pair_result.get("skipped"):
                    total_stats["roles_processed"] += 1
                    total_stats["consolidated_count"] += pair_result.get("consolidated_count", 0)
                    total_stats["originals_marked"] += pair_result.get("originals_marked", 0)
                    total_stats["total_cost_usd"] += pair_result.get("cost_usd", 0.0)
                    errors = pair_result.get("errors", [])
                    if errors:
                        total_stats["errors"].extend(errors)
            except Exception as exc:
                total_stats["errors"].append(f"{uid}/{rid}: {exc}")

        _workflow_logger.info(
            "memory_consolidation.completed",
            **{k: v for k, v in total_stats.items() if k != "errors"},
            error_count=len(total_stats["errors"]),
        )
        return total_stats

    except Exception as exc:
        _workflow_logger.exception(
            "memory_consolidation.failed",
            error=str(exc),
        )
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.record_failed_run(
                    session,
                    workflow_name="memory_consolidation",
                    event_name="cron",
                    error_message=str(exc),
                    error_type=type(exc).__name__,
                    event_data={},
                    run_id=ctx.run_id,
                )
        except Exception:
            pass
        raise


# =====================================================================
# Workflow 15: Claude Code Task Execution
# =====================================================================


@inngest_client.create_function(
    fn_id="claude-code-task",
    trigger=inngest.TriggerEvent(event="sidera/claude_code.task"),
    retries=1,
)
async def claude_code_task_workflow(ctx: inngest.Context) -> dict:
    """Execute a Sidera skill as a headless Claude Code instance.

    Spawns a Claude Code subprocess with full agentic capabilities,
    configured from the skill definition + role/memory context.

    Event data:
        skill_id (str): The skill to execute (required).
        user_id (str): Who triggered execution (default: "claude_code").
        prompt (str): Override prompt (uses skill template if empty).
        role_id (str): Role for persona/memory injection.
        department_id (str): Department context.
        max_budget_usd (float): Per-task cost cap.
        permission_mode (str): Claude Code permission level.
        params (dict): Template parameters for prompt rendering.
        channel_id (str): Slack channel for result notification.
    """
    data = ctx.event.data or {}
    skill_id = data.get("skill_id", "")
    user_id = data.get("user_id", "claude_code")
    prompt = data.get("prompt", "")
    role_id = data.get("role_id", "")
    department_id = data.get("department_id", "")
    max_budget_usd = data.get("max_budget_usd")
    permission_mode = data.get("permission_mode", "")
    params = data.get("params", {})
    channel_id = data.get("channel_id", "")

    if not skill_id:
        raise inngest.NonRetriableError("Missing required field: skill_id")

    try:
        # Step 1: Load context (skill, role, memory)
        async def load_context() -> dict:
            from src.config import settings as cfg

            if not cfg.claude_code_enabled:
                return {"error": "Claude Code execution is disabled"}

            from src.skills.db_loader import load_registry_with_db

            registry = await load_registry_with_db()
            skill = registry.get_skill(skill_id)
            if not skill:
                return {"error": f"Skill not found: {skill_id}"}

            result: dict[str, Any] = {
                "skill_name": skill.name,
                "skill_model": skill.model,
            }

            # Build role context if role_id provided
            role_context = ""
            memory_context = ""
            resolved_dept_id = department_id

            if role_id:
                role = registry.get_role(role_id)
                if role:
                    resolved_dept_id = resolved_dept_id or role.department_id
                    dept = (
                        registry.get_department(role.department_id) if role.department_id else None
                    )

                    from src.skills.executor import compose_role_context

                    role_context = compose_role_context(
                        department=dept,
                        role=role,
                        memory_context="",
                        registry=registry,
                    )

                    # Load hot memories
                    try:
                        from src.db import service as db_service
                        from src.db.session import get_db_session
                        from src.skills.memory import compose_memory_context

                        async with get_db_session() as session:
                            memories = await db_service.get_hot_memories(session, user_id, role_id)
                        memory_context = compose_memory_context(memories)
                    except Exception:
                        pass

            result["role_context"] = role_context
            result["memory_context"] = memory_context
            result["department_id"] = resolved_dept_id

            # Resolve budget cap
            budget = float(max_budget_usd) if max_budget_usd else cfg.claude_code_default_budget_usd
            budget = min(budget, cfg.claude_code_max_budget_usd)
            result["budget_usd"] = budget

            return result

        context_data = await ctx.step.run("load-context", load_context)

        if context_data.get("error"):
            raise inngest.NonRetriableError(context_data["error"])

        # Step 2: Execute Claude Code
        async def execute_claude_code() -> dict:
            from src.claude_code.task_manager import ClaudeCodeTaskManager
            from src.skills.db_loader import load_registry_with_db

            registry = await load_registry_with_db()
            skill = registry.get_skill(skill_id)
            if not skill:
                return {"error": f"Skill not found: {skill_id}"}

            manager = ClaudeCodeTaskManager()
            result = await manager.run_task_sync(
                skill=skill,
                prompt=prompt,
                user_id=user_id,
                role_context=context_data.get("role_context", ""),
                memory_context=context_data.get("memory_context", ""),
                role_id=role_id,
                department_id=context_data.get("department_id", ""),
                max_budget_usd=context_data.get("budget_usd", 5.0),
                permission_mode=permission_mode,
                params=params,
            )

            return {
                "output_text": result.output_text,
                "structured_output": result.structured_output,
                "cost_usd": result.cost_usd,
                "num_turns": result.num_turns,
                "duration_ms": result.duration_ms,
                "session_id": result.session_id,
                "is_error": result.is_error,
                "error_message": result.error_message,
            }

        exec_result = await ctx.step.run(
            "execute-claude-code",
            execute_claude_code,
        )

        if exec_result.get("error"):
            raise inngest.NonRetriableError(exec_result["error"])

        # Step 3: Save results + audit log
        async def save_results() -> dict:
            from datetime import date as date_cls

            from src.db import service as db_service
            from src.db.session import get_db_session

            try:
                async with get_db_session() as session:
                    # Save to analysis_results for compatibility
                    output = exec_result.get("output_text", "")
                    await db_service.save_skill_result(
                        session=session,
                        user_id=user_id,
                        skill_id=f"cc:{skill_id}",
                        run_date=date_cls.today(),
                        briefing_content=output[:4000] if output else "",
                        cost_info={
                            "cost_usd": exec_result.get("cost_usd", 0),
                        },
                        role_id=role_id,
                        department_id=context_data.get("department_id", ""),
                    )

                    # Audit log
                    await db_service.log_event(
                        session=session,
                        user_id=user_id,
                        event_type="claude_code_task",
                        event_data={
                            "skill_id": skill_id,
                            "role_id": role_id,
                            "department_id": context_data.get("department_id", ""),
                            "cost_usd": exec_result.get("cost_usd", 0),
                            "num_turns": exec_result.get("num_turns", 0),
                            "duration_ms": exec_result.get("duration_ms", 0),
                            "is_error": exec_result.get("is_error", False),
                            "inngest_run_id": ctx.run_id,
                        },
                        role_id=role_id,
                        department_id=context_data.get("department_id", ""),
                    )

                    await session.commit()

                return {"saved": True}
            except Exception as exc:
                _workflow_logger.warning("claude_code_task.save_failed", error=str(exc))
                return {"saved": False, "reason": str(exc)}

        await ctx.step.run("save-results", save_results)

        # Step 4: Notify Slack (optional)
        if channel_id:

            async def notify_slack() -> dict:
                try:
                    from src.connectors.slack import SlackConnector

                    slack = SlackConnector()
                    skill_name = context_data.get("skill_name", skill_id)
                    cost = exec_result.get("cost_usd", 0)
                    turns = exec_result.get("num_turns", 0)
                    duration_s = (exec_result.get("duration_ms", 0) or 0) / 1000
                    is_err = exec_result.get("is_error", False)

                    status_emoji = "❌" if is_err else "✅"
                    output = exec_result.get("output_text", "")

                    # Redirect long output to Google Drive
                    from src.api.routes.slack import _maybe_redirect_to_drive

                    output = _maybe_redirect_to_drive(
                        output,
                        skill_name,
                        doc_title_prefix=f"Task: {skill_name}",
                    )

                    message = (
                        f"{status_emoji} *Claude Code Task: {skill_name}*\n\n"
                        f"{output}\n\n"
                        f"_Cost: ${cost:.4f} · {turns} turns · {duration_s:.1f}s_"
                    )

                    await slack.send_alert(
                        channel=channel_id,
                        text=message,
                    )
                    return {"notified": True}
                except Exception as exc:
                    _workflow_logger.warning(
                        "claude_code_task.slack_failed",
                        error=str(exc),
                    )
                    return {"notified": False, "reason": str(exc)}

            await ctx.step.run("notify-slack", notify_slack)

        return {
            "status": "completed" if not exec_result.get("is_error") else "failed",
            "skill_id": skill_id,
            "cost_usd": exec_result.get("cost_usd", 0),
            "num_turns": exec_result.get("num_turns", 0),
            "duration_ms": exec_result.get("duration_ms", 0),
            "is_error": exec_result.get("is_error", False),
        }

    except inngest.NonRetriableError:
        raise
    except Exception as dlq_exc:
        from src.middleware.sentry_setup import capture_exception

        capture_exception(dlq_exc)
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.record_failed_run(
                    session=session,
                    workflow_name="claude_code_task",
                    event_name=ctx.event.name,
                    event_data=ctx.event.data,
                    error_message=str(dlq_exc),
                    error_type=type(dlq_exc).__name__,
                    user_id=user_id,
                    run_id=ctx.run_id,
                )
        except Exception:
            pass
        raise


# =====================================================================
# Event reactor workflow (always-on monitoring)
# =====================================================================


@inngest_client.create_function(
    fn_id="sidera-event-reactor",
    name="Sidera Event Reactor",
    trigger=inngest.TriggerEvent(event="sidera/webhook.received"),
    retries=1,
)
async def event_reactor_workflow(ctx: inngest.Context) -> dict:
    """Process an inbound webhook event from an external monitoring source.

    Classifies severity, sends Slack alerts for medium+ events, and
    triggers an agent investigation (heartbeat-style) for high/critical.

    Expected event data:
    - webhook_event_id (int, optional): DB record ID
    - source (str): "google_ads", "meta", "bigquery", "custom:X"
    - event_type (str): normalized event type
    - severity (str): "low", "medium", "high", "critical"
    - account_id (str, optional): platform account ID
    - campaign_id (str, optional): platform campaign ID
    - campaign_name (str, optional): campaign name
    - summary (str): human-readable event summary
    - details (dict, optional): structured event details
    """
    try:
        data = ctx.event.data
        event_id = data.get("webhook_event_id")
        source = data.get("source", "unknown")
        event_type = data.get("event_type", "custom")
        severity = data.get("severity", "medium")
        summary = data.get("summary", "Webhook event received")
        details = data.get("details", {})

        _workflow_logger.info(
            "event_reactor.start",
            event_id=event_id,
            source=source,
            event_type=event_type,
            severity=severity,
        )

        # Step 1: Classify response level
        async def classify_response() -> dict:
            from src.config import settings

            severity_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
            min_investigate = severity_order.get(settings.webhook_auto_investigate_severity, 2)
            event_level = severity_order.get(severity, 1)

            should_alert = event_level >= 1  # medium+
            should_investigate = event_level >= min_investigate

            # Rate-limit investigations
            if should_investigate:
                try:
                    from src.db import service as db_service
                    from src.db.session import get_db_session

                    async with get_db_session() as session:
                        recent = await db_service.get_recent_audit_events(
                            session,
                            event_type="webhook_investigation",
                            limit=settings.webhook_max_investigations_per_hour,
                        )
                        if len(recent) >= settings.webhook_max_investigations_per_hour:
                            should_investigate = False
                except Exception:
                    pass

            return {
                "should_alert": should_alert,
                "should_investigate": should_investigate,
                "severity": severity,
            }

        classification = await ctx.step.run("classify-response", classify_response)

        # Step 2: Resolve which role should handle this event
        async def resolve_role() -> dict:
            # Check role event_subscriptions from registry
            try:
                from src.skills.db_loader import load_registry_with_db

                registry = await load_registry_with_db()
                for rid, role in registry.list_roles():
                    subs = getattr(role, "event_subscriptions", ())
                    if event_type in subs:
                        return {"role_id": rid}
            except Exception:
                pass  # Fall through to defaults

            # Fallback: static routing by source
            role_map = {
                "google_ads": "performance_media_buyer",
                "meta": "performance_media_buyer",
                "bigquery": "performance_media_buyer",
            }
            role_id = role_map.get(source, "")

            if event_type == "system_alert":
                role_id = "head_of_it"

            if not role_id:
                role_id = "head_of_marketing"

            return {"role_id": role_id}

        role_data = await ctx.step.run("resolve-role", resolve_role)
        target_role_id = role_data.get("role_id", "head_of_marketing")

        # Step 3: Update DB status
        if event_id:

            async def update_status() -> None:
                from src.db import service as db_service
                from src.db.session import get_db_session

                status = "dispatched" if classification.get("should_alert") else "ignored"
                try:
                    async with get_db_session() as session:
                        await db_service.update_webhook_event_status(
                            session,
                            event_id,
                            status,
                            dispatched_event="sidera/webhook.received",
                            role_id=target_role_id,
                        )
                except Exception as exc:
                    _workflow_logger.warning(
                        "event_reactor.status_update_failed",
                        error=str(exc),
                    )

            await ctx.step.run("update-status", update_status)

        # Step 4: Send Slack alert for medium+ severity
        if classification.get("should_alert"):

            async def send_alert() -> dict:
                from src.config import settings
                from src.connectors.slack import SlackConnector

                severity_emoji = {
                    "low": ":information_source:",
                    "medium": ":warning:",
                    "high": ":rotating_light:",
                    "critical": ":fire:",
                }

                emoji = severity_emoji.get(severity, ":bell:")
                alert_text = (
                    f"{emoji} *Webhook Alert — {severity.upper()}*\n"
                    f"*Source:* {source} | *Type:* {event_type}\n"
                    f"*Summary:* {summary}\n"
                )

                campaign_name = data.get("campaign_name", "")
                account_id = data.get("account_id", "")
                if campaign_name:
                    alert_text += f"*Campaign:* {campaign_name}\n"
                if account_id:
                    alert_text += f"*Account:* {account_id}\n"

                if classification.get("should_investigate"):
                    alert_text += f"\n_Investigating via {target_role_id}..._"

                channel = settings.slack_channel_id
                try:
                    connector = SlackConnector()
                    connector.post_message(channel, alert_text)
                    return {"posted": True}
                except Exception as exc:
                    _workflow_logger.warning(
                        "event_reactor.slack_failed",
                        error=str(exc),
                    )
                    return {"posted": False, "error": str(exc)}

            await ctx.step.run("send-alert", send_alert)

        # Step 5: Trigger agent investigation for high/critical
        if classification.get("should_investigate"):

            async def run_investigation() -> dict:
                from src.agent.core import SideraAgent
                from src.agent.prompts import (
                    WEBHOOK_REACTION_SUPPLEMENT,
                    build_webhook_reaction_prompt,
                )
                from src.mcp_servers.evolution import (
                    clear_proposer_context,
                    set_proposer_context,
                )
                from src.mcp_servers.messaging import (
                    clear_messaging_context,
                    set_messaging_context,
                )
                from src.skills.db_loader import load_registry_with_db
                from src.skills.executor import compose_role_context

                registry = await load_registry_with_db()
                role = registry.get_role(target_role_id)
                if role is None:
                    return {
                        "status": "skipped",
                        "reason": f"Role '{target_role_id}' not found",
                    }

                dept = registry.get_department(role.department_id)
                set_messaging_context(target_role_id, role.department_id, registry)
                set_proposer_context(target_role_id, role.department_id)

                # Build role context (no memory loading needed for reactive)
                role_context = compose_role_context(
                    department=dept,
                    role=role,
                    memory_context="",
                    registry=registry,
                )

                # Build system prompt with webhook reaction supplement
                system_prompt = role_context + "\n\n" + WEBHOOK_REACTION_SUPPLEMENT

                # Build the user prompt with event context
                user_prompt = build_webhook_reaction_prompt(
                    role_name=role.name,
                    event_type=event_type,
                    severity=severity,
                    source=source,
                    summary=summary,
                    details=details,
                    campaign_name=data.get("campaign_name", ""),
                    account_id=data.get("account_id", ""),
                )

                agent = SideraAgent()
                try:
                    # Use Sonnet for event investigations (higher quality than Haiku)
                    from src.config import settings

                    result = await agent.run_heartbeat_turn(
                        role_id=target_role_id,
                        role_context=system_prompt,
                        user_id="webhook_reactor",
                        is_manager=bool(getattr(role, "manages", ())),
                        heartbeat_model=settings.model_standard,
                        pending_messages_summary="",
                        user_prompt_override=user_prompt,
                    )
                    return {
                        "status": "completed",
                        "output": result.get("output", "")[:2000],
                        "cost": result.get("cost", {}),
                    }
                except Exception as exc:
                    return {"status": "error", "error": str(exc)}
                finally:
                    clear_messaging_context()
                    clear_proposer_context()

            investigation = await ctx.step.run("run-investigation", run_investigation)

            # Step 6: Post investigation results
            if investigation.get("status") == "completed" and investigation.get("output"):

                async def post_results() -> None:
                    from src.config import settings
                    from src.connectors.slack import SlackConnector

                    output = investigation.get("output", "")
                    text = (
                        f":mag: *Investigation Results — {target_role_id}*\n"
                        f"Re: {event_type} from {source}\n\n"
                        f"{output}"
                    )
                    try:
                        connector = SlackConnector()
                        connector.post_message(settings.slack_channel_id, text)
                    except Exception as exc:
                        _workflow_logger.warning(
                            "event_reactor.results_post_failed",
                            error=str(exc),
                        )

                await ctx.step.run("post-results", post_results)

        # Step 7: Audit log
        async def log_audit() -> None:
            from src.db import service as db_service
            from src.db.session import get_db_session

            event_audit_type = (
                "webhook_investigation"
                if classification.get("should_investigate")
                else "webhook_alert"
            )

            try:
                async with get_db_session() as session:
                    await db_service.log_event(
                        session,
                        user_id="webhook_reactor",
                        event_type=event_audit_type,
                        event_data={
                            "webhook_event_id": event_id,
                            "source": source,
                            "event_type": event_type,
                            "severity": severity,
                            "summary": summary,
                            "role_id": target_role_id,
                            "investigated": classification.get("should_investigate", False),
                        },
                        source="event_reactor",
                    )
            except Exception:
                pass

        await ctx.step.run("log-audit", log_audit)

        return {
            "status": "completed",
            "source": source,
            "event_type": event_type,
            "severity": severity,
            "alerted": classification.get("should_alert", False),
            "investigated": classification.get("should_investigate", False),
            "role_id": target_role_id,
        }

    except Exception as dlq_exc:
        _workflow_logger.error(
            "event_reactor.failed",
            error=str(dlq_exc),
        )
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.record_failed_run(
                    session=session,
                    workflow_name="event_reactor",
                    event_name=ctx.event.name,
                    event_data=ctx.event.data,
                    error_message=str(dlq_exc),
                    error_type=type(dlq_exc).__name__,
                    user_id="webhook_reactor",
                    run_id=ctx.run_id,
                )
        except Exception:
            pass
        raise


# =====================================================================
# Working Group workflow
# =====================================================================


@inngest_client.create_function(
    fn_id="working-group-workflow",
    trigger=inngest.TriggerEvent(event="sidera/working_group.run"),
    retries=1,
)
async def working_group_workflow(ctx: inngest.Context) -> dict:
    """Run a multi-agent working group: plan -> execute members -> synthesize.

    Expected event data:
    - group_id (str, required): Unique working group ID
    - objective (str, required): The group's objective
    - coordinator_role_id (str, required): Manager role coordinating
    - member_role_ids (list[str], required): Roles to execute tasks
    - cost_cap_usd (float, optional): Cost cap for the group
    - max_duration_minutes (int, optional): Max duration
    - shared_context (str, optional): Shared context for all members
    - user_id (str, optional): User who initiated
    - channel_id (str, optional): Slack channel for output
    - thread_ts (str, optional): Slack thread for output
    """
    try:
        group_id = ctx.event.data.get("group_id", "")
        objective = ctx.event.data.get("objective", "")
        coordinator_role_id = ctx.event.data.get("coordinator_role_id", "")
        member_role_ids = ctx.event.data.get("member_role_ids", [])
        cost_cap = float(ctx.event.data.get("cost_cap_usd", 5.0))
        shared_context = ctx.event.data.get("shared_context", "")
        user_id = ctx.event.data.get("user_id", "default")
        channel_id = ctx.event.data.get("channel_id", "")
        thread_ts = ctx.event.data.get("thread_ts", "")

        if not group_id:
            raise inngest.NonRetriableError("group_id is required")
        if not coordinator_role_id:
            raise inngest.NonRetriableError("coordinator_role_id is required")
        if not member_role_ids:
            raise inngest.NonRetriableError("member_role_ids is required")

        # Step 1: Create DB session and validate roles
        async def create_session() -> dict:
            from src.db import service as db_service
            from src.db.session import get_db_session
            from src.skills.db_loader import load_registry_with_db
            from src.skills.working_group import (
                build_member_descriptions,
            )

            registry = await load_registry_with_db()

            coordinator = registry.get_role(coordinator_role_id)
            if coordinator is None:
                raise inngest.NonRetriableError(
                    f"Coordinator role '{coordinator_role_id}' not found"
                )

            member_roles = []
            for rid in member_role_ids:
                role = registry.get_role(rid)
                if role is None:
                    raise inngest.NonRetriableError(f"Member role '{rid}' not found")
                member_roles.append(role)

            # Create DB session
            async with get_db_session() as session:
                await db_service.create_working_group(
                    session,
                    group_id=group_id,
                    objective=objective,
                    coordinator_role_id=coordinator_role_id,
                    member_role_ids=member_role_ids,
                    initiated_by=user_id,
                    cost_cap_usd=cost_cap,
                    slack_channel_id=channel_id or None,
                    slack_thread_ts=thread_ts or None,
                )
                await session.commit()

            member_desc = build_member_descriptions(member_roles)
            return {
                "member_descriptions": member_desc,
                "coordinator_name": coordinator.name,
                "coordinator_persona": coordinator.persona,
                "coordinator_dept": coordinator.department_id,
            }

        session_data = await ctx.step.run(
            "create-session",
            create_session,
        )

        # Step 2: Planning — coordinator LLM call
        async def run_planning() -> dict:
            from src.db import service as db_service
            from src.db.session import get_db_session
            from src.llm import TaskType
            from src.llm.router import complete_with_fallback
            from src.skills.working_group import (
                PLANNING_PROMPT,
                parse_plan,
            )

            planning_prompt = PLANNING_PROMPT.format(
                objective=objective,
                member_descriptions=session_data["member_descriptions"],
                shared_context=shared_context or "(none)",
            )

            system_prompt = (
                f"You are {session_data['coordinator_name']}, "
                f"a manager coordinating a working group.\n"
                f"{session_data['coordinator_persona']}"
            )

            result = await complete_with_fallback(
                task_type=TaskType.DELEGATION,
                system_prompt=system_prompt,
                user_message=planning_prompt,
                max_tokens=2000,
            )

            plan = parse_plan(result.text)
            plan_dict = {
                "plan_summary": plan.plan_summary,
                "assignments": plan.assignments,
            }

            # Update DB
            from datetime import datetime as _dt
            from datetime import timezone as _tz

            async with get_db_session() as session:
                await db_service.update_working_group_status(
                    session,
                    group_id,
                    "executing",
                    plan_json=plan_dict,
                    started_at=_dt.now(_tz.utc),
                )
                await session.commit()

            return plan_dict

        plan = await ctx.step.run("run-planning", run_planning)
        assignments = plan.get("assignments", [])

        # Ensure all members have assignments (fallback: generic task)
        assigned_ids = {a["role_id"] for a in assignments}
        for rid in member_role_ids:
            if rid not in assigned_ids:
                assignments.append(
                    {
                        "role_id": rid,
                        "task": (f"Contribute your expertise to the objective: {objective}"),
                        "priority": "medium",
                    }
                )

        # Step 3: Execute member tasks (sequential with checkpointing)
        total_cost = 0.0
        for assignment in assignments:
            member_rid = assignment.get("role_id", "")
            member_task = assignment.get("task", "")

            if member_rid not in member_role_ids:
                continue  # Skip invalid assignments

            async def run_member(
                rid: str = member_rid,
                task: str = member_task,
            ) -> dict:
                from src.db import service as db_service
                from src.db.session import get_db_session
                from src.llm import TaskType
                from src.llm.router import complete_with_fallback
                from src.skills.db_loader import load_registry_with_db
                from src.skills.executor import compose_role_context

                registry = await load_registry_with_db()
                role = registry.get_role(rid)
                if role is None:
                    return {
                        "role_id": rid,
                        "output": f"Role {rid} not found",
                        "cost_usd": 0,
                        "success": False,
                    }

                dept = registry.get_department(role.department_id)
                sys_prompt = compose_role_context(
                    department=dept,
                    role=role,
                    memory_context="",
                    registry=registry,
                )

                # Build a working group task prompt
                wg_prompt = (
                    f"# Working Group Task\n\n"
                    f"You are part of a working group with "
                    f"objective: {objective}\n\n"
                    f"Your assigned task: {task}\n\n"
                    f"Shared context: "
                    f"{shared_context or '(none)'}\n\n"
                    f"Provide your analysis and findings. "
                    f"Be thorough but concise."
                )

                try:
                    result = await complete_with_fallback(
                        task_type=TaskType.ANALYSIS,
                        system_prompt=sys_prompt,
                        user_message=wg_prompt,
                        max_tokens=3000,
                    )
                    output = result.text
                    cost = result.cost_usd
                except Exception as exc:
                    output = f"Error: {exc}"
                    cost = 0.0
                    _workflow_logger.error(
                        "working_group.member_error",
                        group_id=group_id,
                        role_id=rid,
                        error=str(exc),
                    )

                # Save to DB
                async with get_db_session() as session:
                    await db_service.save_member_result(
                        session,
                        group_id,
                        rid,
                        output,
                        cost,
                    )
                    await session.commit()

                return {
                    "role_id": rid,
                    "output": output,
                    "cost_usd": cost,
                    "success": "Error:" not in output,
                }

            member_result = await ctx.step.run(
                f"run-member-{member_rid}",
                run_member,
            )
            total_cost += member_result.get("cost_usd", 0)

            # Check cost cap
            if total_cost >= cost_cap:
                _workflow_logger.warning(
                    "working_group.cost_cap_reached",
                    group_id=group_id,
                    total_cost=total_cost,
                    cap=cost_cap,
                )
                break

        # Step 4: Synthesis — coordinator synthesizes all outputs
        async def run_synthesis() -> dict:
            from src.db import service as db_service
            from src.db.session import get_db_session
            from src.llm import TaskType
            from src.llm.router import complete_with_fallback
            from src.skills.working_group import (
                SYNTHESIS_PROMPT,
                MemberTaskResult,
                format_member_outputs,
            )

            # Load member results from DB
            async with get_db_session() as session:
                wg = await db_service.get_working_group(
                    session,
                    group_id,
                )
                if wg is None:
                    return {"synthesis": "Working group not found"}

                results_json = wg.member_results_json or {}

            # Build member task results
            task_map = {a["role_id"]: a.get("task", "") for a in assignments}
            member_results = []
            for rid, data in results_json.items():
                member_results.append(
                    MemberTaskResult(
                        role_id=rid,
                        task=task_map.get(rid, ""),
                        output=data.get("output", ""),
                        cost_usd=data.get("cost_usd", 0),
                        success="Error:" not in data.get("output", ""),
                    )
                )

            member_outputs = format_member_outputs(member_results)
            synthesis_prompt = SYNTHESIS_PROMPT.format(
                objective=objective,
                plan_summary=plan.get("plan_summary", ""),
                member_outputs=member_outputs,
            )

            system_prompt = (
                f"You are {session_data['coordinator_name']}.\n"
                f"{session_data['coordinator_persona']}"
            )

            result = await complete_with_fallback(
                task_type=TaskType.SYNTHESIS,
                system_prompt=system_prompt,
                user_message=synthesis_prompt,
                max_tokens=3000,
            )

            synthesis_text = result.text
            synthesis_cost = result.cost_usd

            # Update DB
            from datetime import datetime, timezone

            async with get_db_session() as session:
                await db_service.update_working_group_status(
                    session,
                    group_id,
                    "completed",
                    synthesis=synthesis_text,
                    total_cost_usd=total_cost + synthesis_cost,
                    completed_at=datetime.now(timezone.utc),
                )
                await session.commit()

            return {
                "synthesis": synthesis_text,
                "cost_usd": synthesis_cost,
            }

        synthesis_result = await ctx.step.run(
            "run-synthesis",
            run_synthesis,
        )

        # Step 5: Post to Slack
        async def post_to_slack() -> dict:
            if not channel_id:
                return {"posted": False, "reason": "no channel_id"}

            try:
                from src.connectors.slack import SlackConnector

                slack = SlackConnector()

                members_str = ", ".join(member_role_ids)
                text = (
                    f":busts_in_silhouette: *Working Group Complete: "
                    f"{group_id}*\n\n"
                    f"*Objective:* {objective}\n"
                    f"*Coordinator:* {coordinator_role_id}\n"
                    f"*Members:* {members_str}\n"
                    f"*Cost:* ${total_cost:.4f}\n\n"
                    f"*Synthesis:*\n"
                    f"{synthesis_result.get('synthesis', '')}"
                )

                kwargs: dict[str, Any] = {"channel": channel_id}
                if thread_ts:
                    kwargs["thread_ts"] = thread_ts

                await slack.post_message(text=text, **kwargs)
                return {"posted": True}
            except Exception as exc:
                _workflow_logger.error(
                    "working_group.slack_error",
                    group_id=group_id,
                    error=str(exc),
                )
                return {"posted": False, "error": str(exc)}

        await ctx.step.run("post-to-slack", post_to_slack)

        # Step 6: Audit log
        async def log_audit() -> dict:
            try:
                from src.db import service as db_service
                from src.db.session import get_db_session

                async with get_db_session() as session:
                    await db_service.log_event(
                        session,
                        event_type="working_group_completed",
                        event_data={
                            "group_id": group_id,
                            "objective": objective[:200],
                            "coordinator": coordinator_role_id,
                            "members": member_role_ids,
                            "total_cost_usd": total_cost,
                            "member_count": len(member_role_ids),
                        },
                        role_id=coordinator_role_id,
                        department_id=session_data.get(
                            "coordinator_dept",
                            "",
                        ),
                        user_id=user_id,
                    )
                    await session.commit()
            except Exception:
                pass
            return {"logged": True}

        await ctx.step.run("log-audit", log_audit)

        return {
            "group_id": group_id,
            "status": "completed",
            "members": len(member_role_ids),
            "total_cost_usd": total_cost,
        }

    except Exception as exc:
        _workflow_logger.error(
            "working_group.workflow_error",
            group_id=ctx.event.data.get("group_id", ""),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        # Update DB status to failed
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            gid = ctx.event.data.get("group_id", "")
            if gid:
                async with get_db_session() as session:
                    await db_service.update_working_group_status(
                        session,
                        gid,
                        "failed",
                    )
                    await session.commit()
        except Exception:
            pass
        # DLQ
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.record_failed_run(
                    session,
                    workflow_name="working_group_workflow",
                    event_data=dict(ctx.event.data),
                    error_message=str(exc),
                    error_type=type(exc).__name__,
                    user_id=ctx.event.data.get("user_id", ""),
                    run_id=ctx.run_id,
                )
                await session.commit()
        except Exception:
            pass
        raise


# =====================================================================
# 18. Bootstrap workflow -- ingest company docs and configure agents
# =====================================================================


@inngest_client.create_function(
    fn_id="bootstrap-workflow",
    trigger=inngest.TriggerEvent(event="sidera/bootstrap.run"),
    retries=1,
)
async def bootstrap_workflow(ctx: inngest.Context) -> dict:
    """Run the company bootstrap pipeline.

    Expected event data:
    - folder_id (str, required): Google Drive folder ID
    - user_id (str, optional): User who initiated
    - max_docs (int, optional): Max documents to crawl (default 100)
    - channel_id (str, optional): Slack channel for results
    """
    try:
        folder_id = ctx.event.data.get("folder_id", "")
        user_id = ctx.event.data.get("user_id", "bootstrap")
        max_docs = int(ctx.event.data.get("max_docs", 100))
        channel_id = ctx.event.data.get("channel_id", "")

        if not folder_id:
            raise inngest.NonRetriableError("folder_id is required")

        # Step 1: Run the full bootstrap pipeline (crawl -> classify -> extract -> generate)
        async def run_pipeline() -> dict:
            from src.bootstrap import run_bootstrap

            plan = await run_bootstrap(folder_id, user_id=user_id, max_docs=max_docs)
            return plan.to_dict()

        plan_data = await ctx.step.run("run-bootstrap-pipeline", run_pipeline)

        # Step 2: Post plan summary to Slack for review
        async def post_plan_for_review() -> dict:
            plan_id = plan_data.get("id", "unknown")
            n_depts = len(plan_data.get("departments", []))
            n_roles = len(plan_data.get("roles", []))
            n_skills = len(plan_data.get("skills", []))
            n_memories = len(plan_data.get("memories", []))
            cost = plan_data.get("estimated_cost", 0)
            n_docs = plan_data.get("documents_crawled", 0)
            errors = plan_data.get("errors", [])

            summary_lines = [
                "*Bootstrap Plan Ready for Review*",
                f"Plan ID: `{plan_id}`",
                f"Documents crawled: {n_docs}",
                "",
                "*Generated configuration:*",
                f"  Departments: {n_depts}",
                f"  Roles: {n_roles}",
                f"  Skills: {n_skills}",
                f"  Seed memories: {n_memories}",
                f"  Estimated cost: ${cost:.2f}",
            ]

            if errors:
                summary_lines.append(f"\n:warning: {len(errors)} warning(s):")
                for err in errors[:5]:
                    summary_lines.append(f"  - {err}")

            summary_lines.append(f"\nReview via API: `GET /api/bootstrap/{plan_id}`")
            summary_lines.append(f"Approve: `POST /api/bootstrap/{plan_id}/approve`")

            text = "\n".join(summary_lines)

            # Post to Slack if channel configured
            if channel_id:
                try:
                    from src.connectors.slack import SlackConnector

                    slack = SlackConnector()
                    slack.send_message(channel=channel_id, text=text)
                except Exception as slack_exc:
                    _workflow_logger.warning("bootstrap.slack_post_error", error=str(slack_exc))

            return {"plan_id": plan_id, "posted": bool(channel_id)}

        await ctx.step.run("post-plan-for-review", post_plan_for_review)

        return {
            "status": "plan_ready",
            "plan_id": plan_data.get("id", ""),
            "departments": len(plan_data.get("departments", [])),
            "roles": len(plan_data.get("roles", [])),
            "skills": len(plan_data.get("skills", [])),
        }

    except inngest.NonRetriableError:
        raise
    except Exception as exc:
        _workflow_logger.error("bootstrap.workflow_error", error=str(exc))
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                await db_service.record_failed_run(
                    session,
                    workflow_name="bootstrap_workflow",
                    event_data=dict(ctx.event.data),
                    error_message=str(exc),
                    error_type=type(exc).__name__,
                    user_id=ctx.event.data.get("user_id", ""),
                    run_id=ctx.run_id,
                )
                await session.commit()
        except Exception:
            pass
        raise


# =====================================================================
# Exports
# =====================================================================

all_workflows = [
    daily_briefing_workflow,
    cost_monitor_workflow,
    skill_runner_workflow,
    skill_scheduler_workflow,
    token_refresh_workflow,
    role_runner_workflow,
    department_runner_workflow,
    manager_runner_workflow,
    conversation_turn_workflow,
    meeting_join_workflow,
    meeting_end_workflow,
    data_retention_workflow,
    heartbeat_runner_workflow,
    memory_consolidation_workflow,
    claude_code_task_workflow,
    event_reactor_workflow,
    working_group_workflow,
    bootstrap_workflow,
]
