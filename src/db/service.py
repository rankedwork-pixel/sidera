"""Database service layer for Sidera.

Provides async CRUD operations for all Sidera database tables.
Each method accepts an AsyncSession and performs a single operation.
Methods are organized by domain (accounts, analysis, approvals, audit, costs).
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.schema import (
    Account,
    AnalysisResult,
    ApprovalQueueItem,
    ApprovalStatus,
    AuditLog,
    Campaign,
    ClaudeCodeTaskRecord,
    ConversationThread,
    CostTracking,
    DailyMetric,
    FailedRun,
    MeetingSession,
    OrgDepartment,
    OrgRole,
    OrgSkill,
    Platform,
    RoleMemory,
    RoleMessage,
    User,
    UserRole,
    WebhookEvent,
    WorkingGroupSession,
)

logger = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-naive datetime.

    PostgreSQL ``TIMESTAMP WITHOUT TIME ZONE`` columns reject
    timezone-aware Python datetimes. This helper ensures every
    timestamp we write is naive-UTC, matching the DB column type.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ============================================================
# Accounts
# ============================================================


async def get_accounts_for_user(session: AsyncSession, user_id: str) -> list[Account]:
    """Get all active accounts for a user."""
    stmt = select(Account).where(
        Account.user_id == user_id,
        Account.is_active.is_(True),
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_account_by_platform_id(
    session: AsyncSession,
    user_id: str,
    platform: Platform,
    platform_account_id: str,
) -> Account | None:
    """Get a specific account by user, platform, and platform account ID."""
    stmt = select(Account).where(
        Account.user_id == user_id,
        Account.platform == platform,
        Account.platform_account_id == platform_account_id,
    )
    result = await session.execute(stmt)
    return result.scalars().first()


async def upsert_account(
    session: AsyncSession,
    user_id: str,
    platform: Platform,
    platform_account_id: str,
    **kwargs,
) -> Account:
    """Create or update an account. Extra kwargs are set on the Account model."""
    existing = await get_account_by_platform_id(session, user_id, platform, platform_account_id)

    if existing is not None:
        for key, value in kwargs.items():
            if hasattr(existing, key):
                setattr(existing, key, value)
        existing.updated_at = _utcnow()
        logger.info(
            "account_updated",
            user_id=user_id,
            platform=platform.value,
            platform_account_id=platform_account_id,
        )
        return existing

    account = Account(
        user_id=user_id,
        platform=platform,
        platform_account_id=platform_account_id,
        **kwargs,
    )
    session.add(account)
    await session.flush()
    logger.info(
        "account_created",
        user_id=user_id,
        platform=platform.value,
        platform_account_id=platform_account_id,
    )
    return account


# ============================================================
# Analysis Results
# ============================================================


async def save_analysis_result(
    session: AsyncSession,
    user_id: str,
    run_date: date,
    briefing_content: str,
    recommendations: list | None = None,
    cost_info: dict | None = None,
    accounts_analyzed: list | None = None,
    **kwargs,
) -> AnalysisResult:
    """Save an analysis result for a daily run."""
    analysis = AnalysisResult(
        user_id=user_id,
        run_date=run_date,
        briefing_content=briefing_content,
        recommendations=recommendations,
        accounts_analyzed=accounts_analyzed,
        **kwargs,
    )

    # Apply cost info fields if provided
    if cost_info:
        for key, value in cost_info.items():
            if hasattr(analysis, key):
                setattr(analysis, key, value)

    session.add(analysis)
    await session.flush()
    logger.info("analysis_result_saved", user_id=user_id, run_date=str(run_date))
    return analysis


async def get_latest_analysis(session: AsyncSession, user_id: str) -> AnalysisResult | None:
    """Get the most recent analysis result for a user."""
    stmt = (
        select(AnalysisResult)
        .where(AnalysisResult.user_id == user_id)
        .order_by(AnalysisResult.run_date.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalars().first()


async def get_analyses_for_period(
    session: AsyncSession,
    user_id: str,
    start_date: date,
    end_date: date,
) -> list[AnalysisResult]:
    """Get all analysis results for a user within a date range."""
    stmt = (
        select(AnalysisResult)
        .where(
            AnalysisResult.user_id == user_id,
            AnalysisResult.run_date >= start_date,
            AnalysisResult.run_date <= end_date,
        )
        .order_by(AnalysisResult.run_date.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ============================================================
# Approval Queue
# ============================================================


async def create_approval(
    session: AsyncSession,
    analysis_id: int,
    user_id: str,
    action_type: str,
    account_id: int,
    description: str,
    reasoning: str,
    action_params: dict,
    projected_impact: str | None = None,
    risk_assessment: str | None = None,
    **kwargs,
) -> ApprovalQueueItem:
    """Create a new approval queue item for human review."""
    item = ApprovalQueueItem(
        analysis_id=analysis_id,
        user_id=user_id,
        action_type=action_type,
        account_id=account_id,
        description=description,
        reasoning=reasoning,
        action_params=action_params,
        projected_impact=projected_impact,
        risk_assessment=risk_assessment,
        **kwargs,
    )
    session.add(item)
    await session.flush()
    logger.info(
        "approval_created",
        user_id=user_id,
        action_type=action_type,
        approval_id=item.id,
    )
    return item


async def update_approval_status(
    session: AsyncSession,
    approval_id: int,
    status: ApprovalStatus,
    decided_by: str,
    rejection_reason: str | None = None,
) -> ApprovalQueueItem | None:
    """Update the status of an approval queue item."""
    stmt = select(ApprovalQueueItem).where(ApprovalQueueItem.id == approval_id)
    result = await session.execute(stmt)
    item = result.scalars().first()

    if item is None:
        logger.warning("approval_not_found", approval_id=approval_id)
        return None

    item.status = status
    item.decided_by = decided_by
    item.decided_at = _utcnow()
    if rejection_reason is not None:
        item.rejection_reason = rejection_reason

    logger.info(
        "approval_status_updated",
        approval_id=approval_id,
        status=status.value,
        decided_by=decided_by,
    )
    return item


async def get_pending_approvals(session: AsyncSession, user_id: str) -> list[ApprovalQueueItem]:
    """Get all pending approval items for a user."""
    stmt = (
        select(ApprovalQueueItem)
        .where(
            ApprovalQueueItem.user_id == user_id,
            ApprovalQueueItem.status == ApprovalStatus.PENDING,
        )
        .order_by(ApprovalQueueItem.created_at.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_approval_by_id(session: AsyncSession, approval_id: int) -> ApprovalQueueItem | None:
    """Get a single approval queue item by ID."""
    stmt = select(ApprovalQueueItem).where(ApprovalQueueItem.id == approval_id)
    result = await session.execute(stmt)
    return result.scalars().first()


async def expire_old_approvals(session: AsyncSession, hours: int = 24) -> int:
    """Expire pending approvals older than N hours. Returns count of expired items."""
    cutoff = _utcnow() - timedelta(hours=hours)
    stmt = select(ApprovalQueueItem).where(
        ApprovalQueueItem.status == ApprovalStatus.PENDING,
        ApprovalQueueItem.created_at < cutoff,
    )
    result = await session.execute(stmt)
    items = list(result.scalars().all())

    count = 0
    for item in items:
        item.status = ApprovalStatus.EXPIRED
        item.decided_at = _utcnow()
        count += 1

    if count > 0:
        logger.info("approvals_expired", count=count, cutoff_hours=hours)

    return count


async def count_auto_executions_today(
    session: AsyncSession,
    user_id: str,
    rule_id: str = "",
) -> int:
    """Count auto-executed actions today, optionally filtered by rule.

    Args:
        session: Async database session.
        user_id: The user to query for.
        rule_id: If non-empty, count only executions for this rule.
            If empty, count all auto-executions.

    Returns:
        Number of auto-executed actions today.
    """
    from datetime import date as date_cls

    today = date_cls.today()
    conditions = [
        ApprovalQueueItem.user_id == user_id,
        ApprovalQueueItem.status == ApprovalStatus.AUTO_APPROVED,
        func.date(ApprovalQueueItem.created_at) == today,
    ]
    if rule_id:
        conditions.append(
            ApprovalQueueItem.auto_execute_rule_id == rule_id,
        )

    stmt = select(func.count()).select_from(select(ApprovalQueueItem).where(*conditions).subquery())
    result = await session.execute(stmt)
    return result.scalar() or 0


async def get_last_auto_execution_time(
    session: AsyncSession,
    user_id: str,
    rule_id: str,
) -> datetime | None:
    """Get the timestamp of the most recent auto-execution for a rule.

    Args:
        session: Async database session.
        user_id: The user to query for.
        rule_id: The rule to check cooldown for.

    Returns:
        The ``executed_at`` datetime of the most recent auto-execution,
        or ``None`` if no auto-executions exist.
    """
    stmt = (
        select(ApprovalQueueItem.executed_at)
        .where(
            ApprovalQueueItem.user_id == user_id,
            ApprovalQueueItem.status == ApprovalStatus.AUTO_APPROVED,
            ApprovalQueueItem.auto_execute_rule_id == rule_id,
            ApprovalQueueItem.executed_at.isnot(None),
        )
        .order_by(ApprovalQueueItem.executed_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar()


async def record_execution_result(
    session: AsyncSession,
    approval_id: int,
    execution_result: dict | None = None,
    execution_error: str | None = None,
) -> ApprovalQueueItem | None:
    """Record the outcome of executing an approved action.

    Sets ``executed_at`` and either ``execution_result`` (on success) or
    ``execution_error`` (on failure) on the approval queue item.

    Args:
        session: Database session.
        approval_id: ID of the approval to update.
        execution_result: JSON-serialisable result from the connector.
        execution_error: Error message if execution failed.

    Returns:
        Updated item, or ``None`` if not found.
    """
    stmt = select(ApprovalQueueItem).where(ApprovalQueueItem.id == approval_id)
    result = await session.execute(stmt)
    item = result.scalars().first()
    if item is None:
        return None

    item.executed_at = _utcnow()
    if execution_result is not None:
        item.execution_result = execution_result
    if execution_error is not None:
        item.execution_error = execution_error

    logger.info(
        "execution_result_recorded",
        approval_id=approval_id,
        success=execution_error is None,
    )
    return item


# ============================================================
# Audit Log
# ============================================================


async def log_event(
    session: AsyncSession,
    user_id: str,
    event_type: str,
    event_data: dict | None = None,
    source: str | None = None,
    agent_model: str | None = None,
    account_id: int | None = None,
    required_approval: bool = False,
    approval_status: str | None = None,
    approved_by: str | None = None,
    steward_user_id: str = "",
) -> AuditLog:
    """Write an entry to the audit log."""
    entry = AuditLog(
        user_id=user_id,
        event_type=event_type,
        event_data=event_data,
        source=source,
        agent_model=agent_model,
        account_id=account_id,
        required_approval=required_approval,
        approval_status=approval_status,
        approved_by=approved_by,
    )
    if steward_user_id:
        entry.steward_user_id = steward_user_id
    session.add(entry)
    await session.flush()
    logger.debug("audit_event_logged", user_id=user_id, event_type=event_type)
    return entry


async def get_audit_trail(
    session: AsyncSession,
    user_id: str,
    limit: int = 50,
    event_type: str | None = None,
) -> list[AuditLog]:
    """Get audit log entries for a user, optionally filtered by event type."""
    stmt = select(AuditLog).where(AuditLog.user_id == user_id)

    if event_type is not None:
        stmt = stmt.where(AuditLog.event_type == event_type)

    stmt = stmt.order_by(AuditLog.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ============================================================
# Cost Tracking
# ============================================================


async def record_cost(
    session: AsyncSession,
    user_id: str,
    run_date: date,
    model: str,
    cost_usd: Decimal,
    input_tokens: int = 0,
    output_tokens: int = 0,
    operation: str | None = None,
    account_id: int | None = None,
) -> CostTracking:
    """Record an LLM cost entry."""
    entry = CostTracking(
        user_id=user_id,
        run_date=run_date,
        model=model,
        cost_usd=cost_usd,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        operation=operation,
        account_id=account_id,
    )
    session.add(entry)
    await session.flush()
    logger.debug(
        "cost_recorded",
        user_id=user_id,
        model=model,
        cost_usd=str(cost_usd),
    )
    return entry


async def get_daily_cost(session: AsyncSession, user_id: str, run_date: date) -> Decimal:
    """Sum of all LLM costs for a user on a given date."""
    stmt = select(func.sum(CostTracking.cost_usd)).where(
        CostTracking.user_id == user_id,
        CostTracking.run_date == run_date,
    )
    result = await session.execute(stmt)
    total = result.scalar()
    return Decimal(str(total)) if total is not None else Decimal("0")


async def get_daily_cost_all_users(session: AsyncSession, run_date: date) -> Decimal:
    """Sum of all LLM costs across all users for a given date."""
    stmt = select(func.sum(CostTracking.cost_usd)).where(
        CostTracking.run_date == run_date,
    )
    result = await session.execute(stmt)
    total = result.scalar()
    return Decimal(str(total)) if total is not None else Decimal("0")


# ============================================================
# Skill Results
# ============================================================


async def save_skill_result(
    session: AsyncSession,
    user_id: str,
    skill_id: str,
    run_date: date,
    briefing_content: str,
    recommendations: list | None = None,
    cost_info: dict | None = None,
    accounts_analyzed: list | None = None,
    **kwargs,
) -> AnalysisResult:
    """Save an analysis result from a skill run.

    Works like ``save_analysis_result`` but always sets the ``skill_id``
    column to identify which skill produced this output.
    """
    analysis = AnalysisResult(
        user_id=user_id,
        run_date=run_date,
        briefing_content=briefing_content,
        recommendations=recommendations,
        accounts_analyzed=accounts_analyzed,
        skill_id=skill_id,
        **kwargs,
    )

    # Apply cost info fields if provided
    if cost_info:
        for key, value in cost_info.items():
            if hasattr(analysis, key):
                setattr(analysis, key, value)

    session.add(analysis)
    await session.flush()
    logger.info(
        "skill_result_saved",
        user_id=user_id,
        skill_id=skill_id,
        run_date=str(run_date),
    )
    return analysis


async def get_skill_history(
    session: AsyncSession,
    user_id: str,
    skill_id: str,
    limit: int = 10,
) -> list[AnalysisResult]:
    """Get recent analysis results for a specific skill.

    Args:
        session: Database session.
        user_id: The user's identifier.
        skill_id: The skill that produced the results.
        limit: Maximum number of results to return.

    Returns:
        List of ``AnalysisResult`` entries, newest first.
    """
    stmt = (
        select(AnalysisResult)
        .where(
            AnalysisResult.user_id == user_id,
            AnalysisResult.skill_id == skill_id,
        )
        .order_by(AnalysisResult.run_date.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def log_skill_event(
    session: AsyncSession,
    user_id: str,
    skill_id: str,
    event_type: str,
    event_data: dict | None = None,
    source: str = "skill_run",
    agent_model: str | None = None,
    account_id: int | None = None,
    required_approval: bool = False,
    approval_status: str | None = None,
    approved_by: str | None = None,
) -> AuditLog:
    """Write a skill-related entry to the audit log.

    Works like ``log_event`` but always sets the ``skill_id`` column.
    """
    entry = AuditLog(
        user_id=user_id,
        event_type=event_type,
        event_data=event_data,
        source=source,
        agent_model=agent_model,
        account_id=account_id,
        skill_id=skill_id,
        required_approval=required_approval,
        approval_status=approval_status,
        approved_by=approved_by,
    )
    session.add(entry)
    await session.flush()
    logger.debug(
        "skill_event_logged",
        user_id=user_id,
        skill_id=skill_id,
        event_type=event_type,
    )
    return entry


# ============================================================
# Role / Department Results
# ============================================================


async def save_role_result(
    session: AsyncSession,
    user_id: str,
    role_id: str,
    department_id: str,
    run_date: date,
    briefing_content: str = "",
    recommendations: list | None = None,
    cost_info: dict | None = None,
    accounts_analyzed: list | None = None,
) -> AnalysisResult:
    """Save a role execution result to the database.

    Stores the combined output from all skills in a role run.
    Sets ``skill_id`` to ``role:<role_id>`` by convention so role
    results can be distinguished from individual skill runs.
    """
    analysis = AnalysisResult(
        user_id=user_id,
        run_date=run_date,
        briefing_content=briefing_content,
        recommendations=recommendations or [],
        accounts_analyzed=accounts_analyzed or [],
        llm_cost_usd=cost_info.get("total_cost_usd", 0) if cost_info else 0,
        skill_id=f"role:{role_id}",
        role_id=role_id,
        department_id=department_id,
    )
    session.add(analysis)
    await session.flush()
    logger.debug(
        "role_result_saved",
        user_id=user_id,
        role_id=role_id,
        department_id=department_id,
        analysis_id=analysis.id,
    )
    return analysis


async def get_role_history(
    session: AsyncSession,
    user_id: str,
    role_id: str,
    limit: int = 10,
) -> list[AnalysisResult]:
    """Get recent role run results for a user.

    Args:
        session: Async database session.
        user_id: The user to query for.
        role_id: The role ID to filter by.
        limit: Maximum number of results (default 10).

    Returns:
        List of ``AnalysisResult`` records, newest first.
    """
    stmt = (
        select(AnalysisResult)
        .where(
            AnalysisResult.user_id == user_id,
            AnalysisResult.role_id == role_id,
        )
        .order_by(AnalysisResult.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def log_role_event(
    session: AsyncSession,
    user_id: str,
    role_id: str,
    department_id: str,
    event_type: str,
    event_data: dict | None = None,
    source: str = "role_runner",
) -> AuditLog:
    """Write a role-level entry to the audit log.

    Sets ``role_id`` and ``department_id`` on the audit entry.
    """
    entry = AuditLog(
        user_id=user_id,
        event_type=event_type,
        event_data=event_data,
        source=source,
        role_id=role_id,
        department_id=department_id,
    )
    session.add(entry)
    await session.flush()
    logger.debug(
        "role_event_logged",
        user_id=user_id,
        role_id=role_id,
        department_id=department_id,
        event_type=event_type,
    )
    return entry


# ============================================================
# Role Memory
# ============================================================


async def save_memory(
    session: AsyncSession,
    user_id: str,
    role_id: str,
    department_id: str,
    memory_type: str,
    title: str,
    content: str,
    *,
    confidence: float = 1.0,
    source_skill_id: str | None = None,
    source_run_date: date | None = None,
    evidence: dict | None = None,
    ttl_days: int = 90,
    source_role_id: str | None = None,
) -> RoleMemory:
    """Save a memory entry for a role.

    Args:
        session: Async database session.
        user_id: The user this memory belongs to.
        role_id: The role that produced this memory.
        department_id: The department the role belongs to.
        memory_type: One of decision, anomaly, pattern, insight, lesson, relationship.
        title: One-line summary of the memory.
        content: Full memory text for prompt injection.
        confidence: Confidence score 0.0–1.0 (default 1.0).
        source_skill_id: Which skill produced this memory.
        source_run_date: When the source analysis ran.
        evidence: Supporting data (metrics, IDs, etc.).
        ttl_days: Days in the "hot" tier (default 90). After this period
            the memory moves to the cold archive (is_archived=True) but is
            never deleted — it remains searchable via ``search_role_memories``.
            0 = always hot (never archived).
        source_role_id: For inter-agent memories — the role that was the source
            of this memory (e.g. the role that was messaged or delegated to).
            None = standard user-to-role memory.

    Returns:
        The created ``RoleMemory`` record.
    """
    # Steward notes are always hot — never archived
    if memory_type == "steward_note":
        ttl_days = 0

    expires_at = None
    if ttl_days > 0:
        expires_at = _utcnow() + timedelta(days=ttl_days)

    memory = RoleMemory(
        user_id=user_id,
        role_id=role_id,
        department_id=department_id,
        memory_type=memory_type,
        title=title,
        content=content,
        confidence=confidence,
        source_skill_id=source_skill_id,
        source_run_date=source_run_date,
        evidence=evidence,
        expires_at=expires_at,
        source_role_id=source_role_id,
    )
    session.add(memory)
    await session.flush()
    logger.debug(
        "memory_saved",
        user_id=user_id,
        role_id=role_id,
        memory_type=memory_type,
        memory_id=memory.id,
    )
    return memory


async def get_role_memories(
    session: AsyncSession,
    user_id: str,
    role_id: str,
    *,
    limit: int = 20,
    memory_type: str | None = None,
    min_confidence: float = 0.0,
    include_archived: bool = False,
) -> list[RoleMemory]:
    """Get active memories for a role.

    Args:
        session: Async database session.
        user_id: The user to query for.
        role_id: The role to query for.
        limit: Maximum number of results (default 20).
        memory_type: Optional filter by memory type.
        min_confidence: Minimum confidence score (default 0.0).
        include_archived: Include archived memories (default False).

    Returns:
        List of ``RoleMemory`` records, newest first.
    """
    conditions = [
        RoleMemory.user_id == user_id,
        RoleMemory.role_id == role_id,
        RoleMemory.confidence >= min_confidence,
        # Exclude originals that were folded into a consolidated memory
        RoleMemory.consolidated_into_id.is_(None),
    ]
    if not include_archived:
        conditions.append(RoleMemory.is_archived == False)  # noqa: E712
    if memory_type is not None:
        conditions.append(RoleMemory.memory_type == memory_type)

    stmt = (
        select(RoleMemory)
        .where(*conditions)
        .order_by(RoleMemory.confidence.desc(), RoleMemory.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def archive_expired_memories(session: AsyncSession) -> int:
    """Move expired memories from hot tier to cold archive.

    Sets ``is_archived = True`` on memories whose ``expires_at`` has passed.
    Archived memories are NOT deleted — they remain fully searchable via
    ``search_role_memories`` but are no longer auto-injected into prompts.

    Returns:
        Number of memories archived.
    """
    now = _utcnow()
    stmt = select(RoleMemory).where(
        RoleMemory.expires_at <= now,
        RoleMemory.is_archived == False,  # noqa: E712
        RoleMemory.expires_at.isnot(None),
    )
    result = await session.execute(stmt)
    memories = list(result.scalars().all())
    for mem in memories:
        mem.is_archived = True
    await session.flush()
    if memories:
        logger.info("memories_archived", count=len(memories))
    return len(memories)


async def update_memory_confidence(
    session: AsyncSession,
    memory_id: int,
    new_confidence: float,
) -> None:
    """Update the confidence score of a memory.

    Args:
        session: Async database session.
        memory_id: The memory to update.
        new_confidence: New confidence value (0.0–1.0).
    """
    stmt = select(RoleMemory).where(RoleMemory.id == memory_id)
    result = await session.execute(stmt)
    memory = result.scalars().first()
    if memory is not None:
        memory.confidence = max(0.0, min(1.0, new_confidence))
        await session.flush()


async def delete_memory(
    session: AsyncSession,
    memory_id: int,
) -> None:
    """Delete a memory entry permanently.

    Args:
        session: Async database session.
        memory_id: The memory to delete.
    """
    stmt = select(RoleMemory).where(RoleMemory.id == memory_id)
    result = await session.execute(stmt)
    memory = result.scalars().first()
    if memory is not None:
        await session.delete(memory)
        await session.flush()


async def get_memory_by_id(
    session: AsyncSession,
    memory_id: int,
) -> RoleMemory | None:
    """Get a single memory by its ID.

    Args:
        session: Async database session.
        memory_id: The memory ID to look up.

    Returns:
        The ``RoleMemory`` record or None.
    """
    stmt = select(RoleMemory).where(RoleMemory.id == memory_id)
    result = await session.execute(stmt)
    return result.scalars().first()


async def search_role_memories(
    session: AsyncSession,
    user_id: str,
    role_id: str,
    *,
    query: str = "",
    memory_type: str | None = None,
    limit: int = 10,
) -> list[RoleMemory]:
    """Search ALL memories for a role — including archived (cold) memories.

    Unlike ``get_role_memories`` which only returns active (hot) memories,
    this searches the full archive. Useful when the agent needs to recall
    something from older history (beyond the 90-day active window).

    Args:
        session: Async database session.
        user_id: The user to query for.
        role_id: The role to query for.
        query: Free-text search term (matched against title + content).
            Empty string returns most recent memories across all time.
        memory_type: Optional filter by type (decision/anomaly/pattern/insight).
        limit: Maximum results (default 10).

    Returns:
        List of ``RoleMemory`` records, newest first.
    """
    conditions = [
        RoleMemory.user_id == user_id,
        RoleMemory.role_id == role_id,
    ]
    if memory_type is not None:
        conditions.append(RoleMemory.memory_type == memory_type)
    if query:
        pattern = f"%{query}%"
        conditions.append((RoleMemory.title.ilike(pattern)) | (RoleMemory.content.ilike(pattern)))

    stmt = select(RoleMemory).where(*conditions).order_by(RoleMemory.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


# --- Memory consolidation ---


async def get_distinct_memory_role_pairs(
    session: AsyncSession,
) -> list[tuple[str, str]]:
    """Get all (user_id, role_id) pairs that have unconsolidated memories.

    Returns:
        List of (user_id, role_id) tuples.
    """
    stmt = (
        select(RoleMemory.user_id, RoleMemory.role_id)
        .where(
            RoleMemory.consolidated_into_id.is_(None),
            RoleMemory.is_archived == False,  # noqa: E712
        )
        .group_by(RoleMemory.user_id, RoleMemory.role_id)
    )
    result = await session.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]


async def get_unconsolidated_memories(
    session: AsyncSession,
    user_id: str,
    role_id: str,
    *,
    limit: int = 100,
    min_age_days: int = 7,
) -> list[RoleMemory]:
    """Get unconsolidated hot memories older than min_age_days.

    Used by the weekly consolidation workflow to identify memories
    eligible for merging.

    Args:
        session: Async database session.
        user_id: The user to query for.
        role_id: The role to query for.
        limit: Maximum number of results (default 100).
        min_age_days: Only include memories older than this many days.

    Returns:
        List of ``RoleMemory`` records, oldest first.
    """
    cutoff = _utcnow() - timedelta(days=min_age_days)
    stmt = (
        select(RoleMemory)
        .where(
            RoleMemory.user_id == user_id,
            RoleMemory.role_id == role_id,
            RoleMemory.consolidated_into_id.is_(None),
            RoleMemory.is_archived == False,  # noqa: E712
            RoleMemory.created_at <= cutoff,
        )
        .order_by(RoleMemory.created_at.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def save_consolidated_memory(
    session: AsyncSession,
    user_id: str,
    role_id: str,
    department_id: str,
    memory_type: str,
    title: str,
    content: str,
    source_ids: list[int],
    *,
    confidence: float = 1.0,
    supersedes_id: int | None = None,
) -> RoleMemory:
    """Save a consolidated memory and mark originals.

    Creates a new memory representing the merged/consolidated version,
    then bulk-updates the original memories to point at this new one
    via ``consolidated_into_id``.

    Args:
        session: Async database session.
        user_id: The user this memory belongs to.
        role_id: The role this memory belongs to.
        department_id: The department the role belongs to.
        memory_type: The type of the consolidated memory.
        title: Title for the consolidated memory.
        content: Content of the consolidated memory.
        source_ids: IDs of the original memories being consolidated.
        confidence: Confidence score (default 1.0).
        supersedes_id: Optional ID of a memory this one supersedes.

    Returns:
        The created consolidated ``RoleMemory`` record.
    """
    memory = RoleMemory(
        user_id=user_id,
        role_id=role_id,
        department_id=department_id,
        memory_type=memory_type,
        title=title,
        content=content,
        confidence=confidence,
        supersedes_id=supersedes_id,
        expires_at=None,  # Consolidated memories never auto-expire
        evidence={"source_ids": source_ids, "consolidation": True},
    )
    session.add(memory)
    await session.flush()

    # Mark originals as consolidated
    if source_ids:
        stmt = (
            update(RoleMemory)
            .where(RoleMemory.id.in_(source_ids))
            .values(consolidated_into_id=memory.id)
        )
        await session.execute(stmt)
        await session.flush()

    logger.info(
        "memory_consolidated",
        user_id=user_id,
        role_id=role_id,
        memory_id=memory.id,
        originals=len(source_ids),
    )
    return memory


async def get_superseded_memory_ids(
    session: AsyncSession,
    user_id: str,
    role_id: str,
) -> set[int]:
    """Get IDs of memories that have been superseded by newer versions.

    A memory is superseded if another memory's ``supersedes_id`` points
    to it. The superseded memory should be filtered out of prompt
    injection (only the latest version is shown).

    Args:
        session: Async database session.
        user_id: The user to query for.
        role_id: The role to query for.

    Returns:
        Set of memory IDs that are superseded.
    """
    # Find all supersedes_id values from active memories for this role
    stmt = select(RoleMemory.supersedes_id).where(
        RoleMemory.user_id == user_id,
        RoleMemory.role_id == role_id,
        RoleMemory.supersedes_id.isnot(None),
        RoleMemory.is_archived == False,  # noqa: E712
    )
    result = await session.execute(stmt)
    return {row[0] for row in result.all()}


# ============================================================
# Campaigns & Metrics
# ============================================================


async def upsert_campaign(
    session: AsyncSession,
    account_id: int,
    platform: Platform,
    platform_campaign_id: str,
    **kwargs,
) -> Campaign:
    """Create or update a campaign."""
    stmt = select(Campaign).where(
        Campaign.account_id == account_id,
        Campaign.platform == platform,
        Campaign.platform_campaign_id == platform_campaign_id,
    )
    result = await session.execute(stmt)
    existing = result.scalars().first()

    if existing is not None:
        for key, value in kwargs.items():
            if hasattr(existing, key):
                setattr(existing, key, value)
        existing.updated_at = _utcnow()
        logger.debug(
            "campaign_updated",
            account_id=account_id,
            platform_campaign_id=platform_campaign_id,
        )
        return existing

    campaign = Campaign(
        account_id=account_id,
        platform=platform,
        platform_campaign_id=platform_campaign_id,
        **kwargs,
    )
    session.add(campaign)
    await session.flush()
    logger.debug(
        "campaign_created",
        account_id=account_id,
        platform_campaign_id=platform_campaign_id,
    )
    return campaign


async def save_daily_metrics(
    session: AsyncSession,
    campaign_id: int,
    metric_date: date,
    metrics_dict: dict,
) -> DailyMetric:
    """Save daily metrics for a campaign. Overwrites if same campaign+date exists."""
    stmt = select(DailyMetric).where(
        DailyMetric.campaign_id == campaign_id,
        DailyMetric.date == metric_date,
    )
    result = await session.execute(stmt)
    existing = result.scalars().first()

    if existing is not None:
        for key, value in metrics_dict.items():
            if hasattr(existing, key):
                setattr(existing, key, value)
        existing.pulled_at = _utcnow()
        return existing

    metric = DailyMetric(
        campaign_id=campaign_id,
        date=metric_date,
        **metrics_dict,
    )
    session.add(metric)
    await session.flush()
    return metric


# ============================================================
# Dead Letter Queue (Failed Runs)
# ============================================================


async def record_failed_run(
    session: AsyncSession,
    workflow_name: str,
    event_name: str,
    event_data: dict | None = None,
    error_message: str = "",
    error_type: str = "",
    user_id: str = "",
    run_id: str = "",
) -> FailedRun:
    """Record a workflow failure to the dead letter queue.

    Args:
        session: Async database session.
        workflow_name: The Inngest workflow that failed.
        event_name: The event that triggered the workflow.
        event_data: Full event data dict (for replay).
        error_message: The exception message.
        error_type: The exception class name.
        user_id: The user ID from the event data.
        run_id: The Inngest run ID.

    Returns:
        The created ``FailedRun`` record.
    """
    failed = FailedRun(
        workflow_name=workflow_name,
        event_name=event_name,
        event_data=event_data,
        error_message=error_message,
        error_type=error_type,
        user_id=user_id,
        run_id=run_id,
    )
    session.add(failed)
    await session.flush()
    logger.info(
        "failed_run_recorded",
        workflow_name=workflow_name,
        error_type=error_type,
        user_id=user_id,
    )
    return failed


async def get_unresolved_failed_runs(
    session: AsyncSession,
    user_id: str | None = None,
) -> list[FailedRun]:
    """Get all unresolved failed runs, optionally filtered by user.

    Args:
        session: Async database session.
        user_id: Optional filter by user ID.

    Returns:
        List of ``FailedRun`` records with ``resolved_at IS NULL``.
    """
    stmt = select(FailedRun).where(FailedRun.resolved_at.is_(None))
    if user_id is not None:
        stmt = stmt.where(FailedRun.user_id == user_id)
    stmt = stmt.order_by(FailedRun.created_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def resolve_failed_run(
    session: AsyncSession,
    failed_run_id: int,
    resolved_by: str,
) -> FailedRun | None:
    """Mark a failed run as resolved.

    Args:
        session: Async database session.
        failed_run_id: The ID of the failed run to resolve.
        resolved_by: Who resolved it (user ID or operator name).

    Returns:
        The updated ``FailedRun`` record, or ``None`` if not found.
    """
    stmt = select(FailedRun).where(FailedRun.id == failed_run_id)
    result = await session.execute(stmt)
    failed = result.scalars().first()
    if failed is None:
        return None

    failed.resolved_at = _utcnow()
    failed.resolved_by = resolved_by
    logger.info(
        "failed_run_resolved",
        failed_run_id=failed_run_id,
        resolved_by=resolved_by,
    )
    return failed


# ============================================================
# Token Refresh
# ============================================================


async def get_accounts_expiring_soon(
    session: AsyncSession,
    within_days: int = 7,
) -> list[Account]:
    """Get active accounts with tokens expiring within the given window.

    Args:
        session: Async database session.
        within_days: Number of days ahead to check for expiring tokens.

    Returns:
        List of ``Account`` records with tokens expiring soon.
    """
    cutoff = _utcnow() + timedelta(days=within_days)
    stmt = select(Account).where(
        Account.is_active.is_(True),
        Account.token_expires_at.isnot(None),
        Account.token_expires_at < cutoff,
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def update_account_tokens(
    session: AsyncSession,
    account_id: int,
    access_token: str,
    refresh_token: str | None = None,
    expires_at: datetime | None = None,
) -> Account | None:
    """Update OAuth tokens for an account.

    Args:
        session: Async database session.
        account_id: The database ID of the account.
        access_token: The new access token (should be encrypted).
        refresh_token: The new refresh token (if rotated).
        expires_at: New token expiration datetime.

    Returns:
        The updated ``Account``, or ``None`` if not found.
    """
    stmt = select(Account).where(Account.id == account_id)
    result = await session.execute(stmt)
    account = result.scalars().first()
    if account is None:
        return None

    account.oauth_access_token = access_token
    if refresh_token is not None:
        account.oauth_refresh_token = refresh_token
    if expires_at is not None:
        account.token_expires_at = expires_at

    logger.info(
        "account_tokens_updated",
        account_id=account_id,
        platform=account.platform.value if hasattr(account.platform, "value") else account.platform,
    )
    return account


# ============================================================
# Conversation Threads
# ============================================================


async def create_conversation_thread(
    session: AsyncSession,
    thread_ts: str,
    channel_id: str,
    role_id: str,
    user_id: str,
) -> ConversationThread:
    """Create a new conversation thread mapping.

    Args:
        session: Async database session.
        thread_ts: Slack thread timestamp (parent message ts).
        channel_id: Slack channel ID.
        role_id: The role assigned to this thread.
        user_id: Slack user ID who started the conversation.

    Returns:
        The created ``ConversationThread`` record.
    """
    thread = ConversationThread(
        thread_ts=thread_ts,
        channel_id=channel_id,
        role_id=role_id,
        user_id=user_id,
    )
    session.add(thread)
    await session.flush()
    logger.info(
        "conversation_thread_created",
        thread_ts=thread_ts,
        channel_id=channel_id,
        role_id=role_id,
        user_id=user_id,
    )
    return thread


async def get_conversation_thread(
    session: AsyncSession,
    thread_ts: str,
) -> ConversationThread | None:
    """Look up a conversation thread by its Slack thread_ts.

    Args:
        session: Async database session.
        thread_ts: Slack thread timestamp to look up.

    Returns:
        The ``ConversationThread`` record, or ``None`` if not found.
    """
    stmt = select(ConversationThread).where(
        ConversationThread.thread_ts == thread_ts,
    )
    result = await session.execute(stmt)
    return result.scalars().first()


async def update_conversation_thread_activity(
    session: AsyncSession,
    thread_ts: str,
    cost_increment: float = 0.0,
) -> None:
    """Update a conversation thread's activity and increment counters.

    Bumps ``turn_count`` by 1, adds ``cost_increment`` to
    ``total_cost_usd``, and updates ``last_activity_at``.

    Args:
        session: Async database session.
        thread_ts: Slack thread timestamp.
        cost_increment: Cost to add to the running total (default 0).
    """
    stmt = select(ConversationThread).where(
        ConversationThread.thread_ts == thread_ts,
    )
    result = await session.execute(stmt)
    thread = result.scalars().first()
    if thread is None:
        return

    thread.turn_count = (thread.turn_count or 0) + 1
    thread.total_cost_usd = float(thread.total_cost_usd or 0) + cost_increment
    thread.last_activity_at = _utcnow()
    await session.flush()
    logger.debug(
        "conversation_thread_updated",
        thread_ts=thread_ts,
        turn_count=thread.turn_count,
        total_cost_usd=thread.total_cost_usd,
    )


async def deactivate_stale_threads(
    session: AsyncSession,
    hours: int = 24,
) -> int:
    """Mark conversation threads as inactive if idle for too long.

    Args:
        session: Async database session.
        hours: Number of hours of inactivity before deactivation.

    Returns:
        Number of threads deactivated.
    """
    cutoff = _utcnow() - timedelta(hours=hours)
    stmt = select(ConversationThread).where(
        ConversationThread.is_active.is_(True),
        ConversationThread.last_activity_at < cutoff,
    )
    result = await session.execute(stmt)
    threads = list(result.scalars().all())
    for t in threads:
        t.is_active = False
    await session.flush()
    if threads:
        logger.info("stale_threads_deactivated", count=len(threads))
    return len(threads)


# ============================================================
# Org Chart — Departments
# ============================================================


async def _log_org_chart_change(
    session: AsyncSession,
    operation: str,
    entity_type: str,
    entity_id: str,
    changes: dict | None = None,
    created_by: str = "",
) -> None:
    """Write an org_chart_change entry to the audit log."""
    await log_event(
        session,
        user_id=created_by or "system",
        event_type="org_chart_change",
        event_data={
            "operation": operation,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "changes": changes or {},
        },
        source="org_chart",
    )


async def create_org_department(
    session: AsyncSession,
    dept_id: str,
    name: str,
    description: str,
    context: str = "",
    context_text: str = "",
    created_by: str = "",
    steward_user_id: str = "",
) -> OrgDepartment:
    """Create a new dynamic department definition.

    Args:
        session: Async database session.
        dept_id: Unique department identifier.
        name: Human-readable department name.
        description: Department description.
        context: High-level context injected into all role prompts.
        context_text: Pre-rendered context text (replaces context_files for DB entries).
        created_by: Who created this entry.
        steward_user_id: Slack user ID of the department steward.

    Returns:
        The created ``OrgDepartment`` record.

    Raises:
        sqlalchemy.exc.IntegrityError: If dept_id already exists.
    """
    dept = OrgDepartment(
        dept_id=dept_id,
        name=name,
        description=description,
        context=context,
        context_text=context_text,
        created_by=created_by,
        steward_user_id=steward_user_id,
    )
    session.add(dept)
    await session.flush()
    await _log_org_chart_change(
        session,
        "create",
        "department",
        dept_id,
        changes={"name": name, "description": description},
        created_by=created_by,
    )
    logger.info("org_department_created", dept_id=dept_id, name=name)
    return dept


async def update_org_department(
    session: AsyncSession,
    dept_id: str,
    **kwargs,
) -> OrgDepartment | None:
    """Update fields on an existing dynamic department.

    Args:
        session: Async database session.
        dept_id: The department to update.
        **kwargs: Fields to update (name, description, context, context_text, is_active).

    Returns:
        Updated ``OrgDepartment`` or ``None`` if not found.
    """
    stmt = select(OrgDepartment).where(OrgDepartment.dept_id == dept_id)
    result = await session.execute(stmt)
    dept = result.scalars().first()
    if dept is None:
        return None

    changes = {}
    for key, value in kwargs.items():
        if hasattr(dept, key) and key not in ("id", "dept_id", "created_at"):
            old = getattr(dept, key)
            setattr(dept, key, value)
            changes[key] = {"old": old, "new": value}

    dept.updated_at = _utcnow()
    await session.flush()
    await _log_org_chart_change(
        session,
        "update",
        "department",
        dept_id,
        changes=changes,
    )
    logger.info("org_department_updated", dept_id=dept_id, fields=list(changes.keys()))
    return dept


async def delete_org_department(
    session: AsyncSession,
    dept_id: str,
) -> OrgDepartment | None:
    """Soft-delete a dynamic department (set is_active=False).

    Args:
        session: Async database session.
        dept_id: The department to deactivate.

    Returns:
        Updated ``OrgDepartment`` or ``None`` if not found.
    """
    stmt = select(OrgDepartment).where(OrgDepartment.dept_id == dept_id)
    result = await session.execute(stmt)
    dept = result.scalars().first()
    if dept is None:
        return None

    dept.is_active = False
    dept.updated_at = _utcnow()
    await session.flush()
    await _log_org_chart_change(
        session,
        "delete",
        "department",
        dept_id,
    )
    logger.info("org_department_deleted", dept_id=dept_id)
    return dept


async def get_org_department(
    session: AsyncSession,
    dept_id: str,
) -> OrgDepartment | None:
    """Get a single dynamic department by dept_id.

    Args:
        session: Async database session.
        dept_id: The department identifier.

    Returns:
        ``OrgDepartment`` or ``None``.
    """
    stmt = select(OrgDepartment).where(OrgDepartment.dept_id == dept_id)
    result = await session.execute(stmt)
    return result.scalars().first()


async def list_org_departments(
    session: AsyncSession,
    active_only: bool = True,
) -> list[OrgDepartment]:
    """List all dynamic departments.

    Args:
        session: Async database session.
        active_only: If True, only return active departments.

    Returns:
        List of ``OrgDepartment`` records sorted by dept_id.
    """
    stmt = select(OrgDepartment)
    if active_only:
        stmt = stmt.where(OrgDepartment.is_active.is_(True))
    stmt = stmt.order_by(OrgDepartment.dept_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ============================================================
# Org Chart — Roles
# ============================================================


async def create_org_role(
    session: AsyncSession,
    role_id: str,
    name: str,
    department_id: str,
    description: str,
    persona: str = "",
    connectors: list | None = None,
    briefing_skills: list | None = None,
    schedule: str | None = None,
    context_text: str = "",
    manages: list | None = None,
    delegation_model: str = "standard",
    synthesis_prompt: str = "",
    created_by: str = "",
    steward_user_id: str = "",
) -> OrgRole:
    """Create a new dynamic role definition.

    Args:
        session: Async database session.
        role_id: Unique role identifier.
        name: Human-readable role name.
        department_id: Department this role belongs to.
        description: Role description.
        persona: Persona text injected into the system prompt.
        connectors: List of connector IDs the role needs.
        briefing_skills: Ordered list of skill IDs for daily briefing.
        schedule: Optional cron expression.
        context_text: Pre-rendered context text.
        manages: List of role IDs this manager directs.
        delegation_model: "standard" (Sonnet) or "fast" (Haiku).
        synthesis_prompt: Custom synthesis instructions.
        created_by: Who created this entry.
        steward_user_id: Slack user ID of the role steward.

    Returns:
        The created ``OrgRole`` record.
    """
    role = OrgRole(
        role_id=role_id,
        name=name,
        department_id=department_id,
        description=description,
        persona=persona,
        connectors=connectors or [],
        briefing_skills=briefing_skills or [],
        schedule=schedule,
        context_text=context_text,
        manages=manages or [],
        delegation_model=delegation_model,
        synthesis_prompt=synthesis_prompt,
        created_by=created_by,
        steward_user_id=steward_user_id,
    )
    session.add(role)
    await session.flush()
    await _log_org_chart_change(
        session,
        "create",
        "role",
        role_id,
        changes={"name": name, "department_id": department_id},
        created_by=created_by,
    )
    logger.info("org_role_created", role_id=role_id, department_id=department_id)
    return role


async def update_org_role(
    session: AsyncSession,
    role_id: str,
    **kwargs,
) -> OrgRole | None:
    """Update fields on an existing dynamic role.

    Args:
        session: Async database session.
        role_id: The role to update.
        **kwargs: Fields to update.

    Returns:
        Updated ``OrgRole`` or ``None`` if not found.
    """
    stmt = select(OrgRole).where(OrgRole.role_id == role_id)
    result = await session.execute(stmt)
    role = result.scalars().first()
    if role is None:
        return None

    changes = {}
    for key, value in kwargs.items():
        if hasattr(role, key) and key not in ("id", "role_id", "created_at"):
            old = getattr(role, key)
            setattr(role, key, value)
            changes[key] = {"old": old, "new": value}

    role.updated_at = _utcnow()
    await session.flush()
    await _log_org_chart_change(
        session,
        "update",
        "role",
        role_id,
        changes=changes,
    )
    logger.info("org_role_updated", role_id=role_id, fields=list(changes.keys()))
    return role


async def delete_org_role(
    session: AsyncSession,
    role_id: str,
) -> OrgRole | None:
    """Soft-delete a dynamic role (set is_active=False).

    Args:
        session: Async database session.
        role_id: The role to deactivate.

    Returns:
        Updated ``OrgRole`` or ``None`` if not found.
    """
    stmt = select(OrgRole).where(OrgRole.role_id == role_id)
    result = await session.execute(stmt)
    role = result.scalars().first()
    if role is None:
        return None

    role.is_active = False
    role.updated_at = _utcnow()
    await session.flush()
    await _log_org_chart_change(
        session,
        "delete",
        "role",
        role_id,
    )
    logger.info("org_role_deleted", role_id=role_id)
    return role


async def get_org_role(
    session: AsyncSession,
    role_id: str,
) -> OrgRole | None:
    """Get a single dynamic role by role_id.

    Args:
        session: Async database session.
        role_id: The role identifier.

    Returns:
        ``OrgRole`` or ``None``.
    """
    stmt = select(OrgRole).where(OrgRole.role_id == role_id)
    result = await session.execute(stmt)
    return result.scalars().first()


async def list_org_roles(
    session: AsyncSession,
    department_id: str | None = None,
    active_only: bool = True,
) -> list[OrgRole]:
    """List all dynamic roles, optionally filtered by department.

    Args:
        session: Async database session.
        department_id: If provided, filter by department.
        active_only: If True, only return active roles.

    Returns:
        List of ``OrgRole`` records sorted by role_id.
    """
    stmt = select(OrgRole)
    if active_only:
        stmt = stmt.where(OrgRole.is_active.is_(True))
    if department_id is not None:
        stmt = stmt.where(OrgRole.department_id == department_id)
    stmt = stmt.order_by(OrgRole.role_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ============================================================
# Org Chart — Skills
# ============================================================


async def create_org_skill(
    session: AsyncSession,
    skill_id: str,
    name: str,
    description: str,
    category: str,
    system_supplement: str,
    prompt_template: str,
    output_format: str,
    business_guidance: str,
    platforms: list | None = None,
    tags: list | None = None,
    tools_required: list | None = None,
    model: str = "sonnet",
    max_turns: int = 20,
    context_text: str = "",
    schedule: str | None = None,
    chain_after: str | None = None,
    requires_approval: bool = True,
    department_id: str = "",
    role_id: str = "",
    author: str = "sidera",
    created_by: str = "",
) -> OrgSkill:
    """Create a new dynamic skill definition.

    Args:
        session: Async database session.
        skill_id: Unique skill identifier.
        name: Human-readable skill name.
        description: Skill description.
        category: Skill category (analysis, optimization, etc.).
        system_supplement: Additional system prompt text.
        prompt_template: The user prompt template.
        output_format: Expected output format instructions.
        business_guidance: Business context and guardrails.
        platforms: List of platform IDs.
        tags: List of searchable tags.
        tools_required: List of MCP tool names.
        model: LLM model to use (haiku/sonnet/opus).
        max_turns: Max agent turns.
        context_text: Pre-rendered context text.
        schedule: Optional cron expression.
        chain_after: Skill ID to chain after.
        requires_approval: Whether actions need approval.
        department_id: Department this skill belongs to.
        role_id: Role this skill belongs to.
        author: Author name.
        created_by: Who created this entry.

    Returns:
        The created ``OrgSkill`` record.
    """
    skill = OrgSkill(
        skill_id=skill_id,
        name=name,
        description=description,
        category=category,
        system_supplement=system_supplement,
        prompt_template=prompt_template,
        output_format=output_format,
        business_guidance=business_guidance,
        platforms=platforms or [],
        tags=tags or [],
        tools_required=tools_required or [],
        model=model,
        max_turns=max_turns,
        context_text=context_text,
        schedule=schedule,
        chain_after=chain_after,
        requires_approval=requires_approval,
        department_id=department_id,
        role_id=role_id,
        author=author,
        created_by=created_by,
    )
    session.add(skill)
    await session.flush()
    await _log_org_chart_change(
        session,
        "create",
        "skill",
        skill_id,
        changes={"name": name, "category": category},
        created_by=created_by,
    )
    logger.info("org_skill_created", skill_id=skill_id, category=category)
    return skill


async def update_org_skill(
    session: AsyncSession,
    skill_id: str,
    **kwargs,
) -> OrgSkill | None:
    """Update fields on an existing dynamic skill.

    Args:
        session: Async database session.
        skill_id: The skill to update.
        **kwargs: Fields to update.

    Returns:
        Updated ``OrgSkill`` or ``None`` if not found.
    """
    stmt = select(OrgSkill).where(OrgSkill.skill_id == skill_id)
    result = await session.execute(stmt)
    skill = result.scalars().first()
    if skill is None:
        return None

    changes = {}
    for key, value in kwargs.items():
        if hasattr(skill, key) and key not in ("id", "skill_id", "created_at"):
            old = getattr(skill, key)
            setattr(skill, key, value)
            changes[key] = {"old": old, "new": value}

    skill.updated_at = _utcnow()
    await session.flush()
    await _log_org_chart_change(
        session,
        "update",
        "skill",
        skill_id,
        changes=changes,
    )
    logger.info("org_skill_updated", skill_id=skill_id, fields=list(changes.keys()))
    return skill


async def delete_org_skill(
    session: AsyncSession,
    skill_id: str,
) -> OrgSkill | None:
    """Soft-delete a dynamic skill (set is_active=False).

    Args:
        session: Async database session.
        skill_id: The skill to deactivate.

    Returns:
        Updated ``OrgSkill`` or ``None`` if not found.
    """
    stmt = select(OrgSkill).where(OrgSkill.skill_id == skill_id)
    result = await session.execute(stmt)
    skill = result.scalars().first()
    if skill is None:
        return None

    skill.is_active = False
    skill.updated_at = _utcnow()
    await session.flush()
    await _log_org_chart_change(
        session,
        "delete",
        "skill",
        skill_id,
    )
    logger.info("org_skill_deleted", skill_id=skill_id)
    return skill


async def get_org_skill(
    session: AsyncSession,
    skill_id: str,
) -> OrgSkill | None:
    """Get a single dynamic skill by skill_id.

    Args:
        session: Async database session.
        skill_id: The skill identifier.

    Returns:
        ``OrgSkill`` or ``None``.
    """
    stmt = select(OrgSkill).where(OrgSkill.skill_id == skill_id)
    result = await session.execute(stmt)
    return result.scalars().first()


async def list_org_skills(
    session: AsyncSession,
    role_id: str | None = None,
    department_id: str | None = None,
    active_only: bool = True,
) -> list[OrgSkill]:
    """List all dynamic skills, optionally filtered.

    Args:
        session: Async database session.
        role_id: If provided, filter by role.
        department_id: If provided, filter by department.
        active_only: If True, only return active skills.

    Returns:
        List of ``OrgSkill`` records sorted by skill_id.
    """
    stmt = select(OrgSkill)
    if active_only:
        stmt = stmt.where(OrgSkill.is_active.is_(True))
    if role_id is not None:
        stmt = stmt.where(OrgSkill.role_id == role_id)
    if department_id is not None:
        stmt = stmt.where(OrgSkill.department_id == department_id)
    stmt = stmt.order_by(OrgSkill.skill_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ============================================================
# Users & RBAC
# ============================================================


async def get_user(session: AsyncSession, user_id: str) -> User | None:
    """Get a user by their Slack user ID."""
    stmt = select(User).where(User.user_id == user_id)
    result = await session.execute(stmt)
    return result.scalars().first()


async def get_user_role(session: AsyncSession, user_id: str) -> str | None:
    """Return the role string for a user, or None if not found.

    This is the fast-path used by RBAC middleware — single column select.
    """
    stmt = select(User.role).where(
        User.user_id == user_id,
        User.is_active.is_(True),
    )
    result = await session.execute(stmt)
    role = result.scalar_one_or_none()
    if role is None:
        return None
    # Handle both enum and string
    return role.value if hasattr(role, "value") else str(role)


async def create_user(
    session: AsyncSession,
    user_id: str,
    *,
    display_name: str = "",
    email: str = "",
    role: str = "approver",
    clearance_level: str = "public",
    created_by: str = "",
) -> User:
    """Create a new user."""
    user = User(
        user_id=user_id,
        display_name=display_name,
        email=email,
        role=UserRole(role),
        clearance_level=clearance_level,
        is_active=True,
        created_by=created_by,
    )
    session.add(user)
    await session.flush()
    logger.info(
        "user.created",
        user_id=user_id,
        role=role,
        clearance_level=clearance_level,
        created_by=created_by,
    )
    return user


async def update_user_role(
    session: AsyncSession,
    user_id: str,
    new_role: str,
    *,
    changed_by: str = "",
) -> User | None:
    """Update a user's RBAC role. Returns the updated user or None if not found."""
    user = await get_user(session, user_id)
    if user is None:
        return None
    old_role = user.role.value if hasattr(user.role, "value") else str(user.role)
    user.role = UserRole(new_role)
    user.updated_at = _utcnow()
    await session.flush()
    logger.info(
        "user.role_updated",
        user_id=user_id,
        old_role=old_role,
        new_role=new_role,
        changed_by=changed_by,
    )
    return user


async def deactivate_user(
    session: AsyncSession,
    user_id: str,
    *,
    deactivated_by: str = "",
) -> bool:
    """Deactivate a user (soft delete). Returns True if found and deactivated."""
    user = await get_user(session, user_id)
    if user is None:
        return False
    user.is_active = False
    user.updated_at = _utcnow()
    await session.flush()
    logger.info("user.deactivated", user_id=user_id, deactivated_by=deactivated_by)
    return True


async def list_users(
    session: AsyncSession,
    *,
    active_only: bool = True,
    role: str | None = None,
) -> list[User]:
    """List users, optionally filtered by active status and role."""
    stmt = select(User)
    if active_only:
        stmt = stmt.where(User.is_active.is_(True))
    if role is not None:
        stmt = stmt.where(User.role == UserRole(role))
    stmt = stmt.order_by(User.user_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ============================================================
# Clearance — User Information Access Level
# ============================================================


async def get_user_clearance(session: AsyncSession, user_id: str) -> str | None:
    """Return the clearance level string for a user, or None if not found.

    This is the fast-path used by RBAC clearance middleware — single column select.
    """
    stmt = select(User.clearance_level).where(
        User.user_id == user_id,
        User.is_active.is_(True),
    )
    result = await session.execute(stmt)
    clearance = result.scalar_one_or_none()
    if clearance is None:
        return None
    return clearance.value if hasattr(clearance, "value") else str(clearance)


async def update_user_clearance(
    session: AsyncSession,
    user_id: str,
    new_clearance: str,
    *,
    changed_by: str = "",
) -> User | None:
    """Update a user's information clearance level. Returns the updated user or None."""
    user = await get_user(session, user_id)
    if user is None:
        return None
    old_clearance = (
        user.clearance_level.value
        if hasattr(user.clearance_level, "value")
        else str(user.clearance_level)
    )
    user.clearance_level = new_clearance
    user.updated_at = _utcnow()
    await session.flush()
    logger.info(
        "user.clearance_updated",
        user_id=user_id,
        old_clearance=old_clearance,
        new_clearance=new_clearance,
        changed_by=changed_by,
    )
    return user


async def get_agent_relationship_memories(
    session: AsyncSession,
    role_id: str,
    *,
    limit: int = 5,
) -> list[RoleMemory]:
    """Get inter-agent relationship memories for a role.

    Returns memories where ``source_role_id IS NOT NULL`` — these are memories
    about this role's interactions with other roles (delegation, messaging).

    Args:
        session: Async database session.
        role_id: The role to get memories for.
        limit: Max number of memories to return (default 5).

    Returns:
        List of RoleMemory objects, newest first.
    """
    stmt = (
        select(RoleMemory)
        .where(
            RoleMemory.role_id == role_id,
            RoleMemory.source_role_id.isnot(None),
            RoleMemory.is_archived.is_(False),
        )
        .order_by(RoleMemory.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ============================================================
# Data Retention — Purge Methods
# ============================================================

_PURGE_BATCH_SIZE = 1000


async def purge_old_audit_logs(session: AsyncSession, cutoff: datetime) -> int:
    """Delete audit log entries older than *cutoff*. Returns count deleted."""
    from sqlalchemy import delete

    stmt = (
        delete(AuditLog)
        .where(AuditLog.created_at < cutoff)
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(stmt)
    await session.flush()
    count = result.rowcount or 0
    logger.info("retention.purge_audit_logs", deleted=count, cutoff=str(cutoff))
    return count


async def purge_old_analysis_results(session: AsyncSession, cutoff: datetime) -> int:
    """Delete analysis results older than *cutoff*. Returns count deleted."""
    from sqlalchemy import delete

    stmt = (
        delete(AnalysisResult)
        .where(AnalysisResult.created_at < cutoff)
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(stmt)
    await session.flush()
    count = result.rowcount or 0
    logger.info("retention.purge_analysis_results", deleted=count, cutoff=str(cutoff))
    return count


async def purge_old_cost_tracking(session: AsyncSession, cutoff: datetime) -> int:
    """Delete cost tracking entries older than *cutoff*. Returns count deleted."""
    from sqlalchemy import delete

    stmt = (
        delete(CostTracking)
        .where(CostTracking.created_at < cutoff)
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(stmt)
    await session.flush()
    count = result.rowcount or 0
    logger.info("retention.purge_cost_tracking", deleted=count, cutoff=str(cutoff))
    return count


async def purge_decided_approvals(session: AsyncSession, cutoff: datetime) -> int:
    """Delete decided (non-pending) approval queue items older than *cutoff*."""
    from sqlalchemy import delete

    stmt = (
        delete(ApprovalQueueItem)
        .where(
            ApprovalQueueItem.created_at < cutoff,
            ApprovalQueueItem.status != ApprovalStatus.PENDING,
        )
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(stmt)
    await session.flush()
    count = result.rowcount or 0
    logger.info("retention.purge_decided_approvals", deleted=count, cutoff=str(cutoff))
    return count


async def purge_resolved_failed_runs(session: AsyncSession, cutoff: datetime) -> int:
    """Delete resolved failed runs older than *cutoff*."""
    from sqlalchemy import delete

    stmt = (
        delete(FailedRun)
        .where(
            FailedRun.created_at < cutoff,
            FailedRun.resolved_at.isnot(None),
        )
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(stmt)
    await session.flush()
    count = result.rowcount or 0
    logger.info("retention.purge_resolved_failed_runs", deleted=count, cutoff=str(cutoff))
    return count


async def purge_old_daily_metrics(session: AsyncSession, cutoff_date: date) -> int:
    """Delete daily metrics older than *cutoff_date*. Returns count deleted."""
    from sqlalchemy import delete

    stmt = (
        delete(DailyMetric)
        .where(DailyMetric.date < cutoff_date)
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(stmt)
    await session.flush()
    count = result.rowcount or 0
    logger.info("retention.purge_daily_metrics", deleted=count, cutoff=str(cutoff_date))
    return count


async def purge_inactive_threads(session: AsyncSession, cutoff: datetime) -> int:
    """Delete inactive conversation threads older than *cutoff*."""
    from sqlalchemy import delete

    stmt = (
        delete(ConversationThread)
        .where(
            ConversationThread.is_active.is_(False),
            ConversationThread.started_at < cutoff,
        )
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(stmt)
    await session.flush()
    count = result.rowcount or 0
    logger.info("retention.purge_inactive_threads", deleted=count, cutoff=str(cutoff))
    return count


async def purge_archived_memories(session: AsyncSession, cutoff: datetime) -> int:
    """Delete cold (archived) memories older than *cutoff*. Returns count deleted.

    Only deletes memories outside the hot window (>90 days old by default).
    """
    from sqlalchemy import delete

    stmt = (
        delete(RoleMemory)
        .where(RoleMemory.created_at < cutoff)
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(stmt)
    await session.flush()
    count = result.rowcount or 0
    logger.info("retention.purge_archived_memories", deleted=count, cutoff=str(cutoff))
    return count


# ============================================================
# GDPR — User Data Export & Delete
# ============================================================


async def export_user_data(session: AsyncSession, user_id: str) -> dict:
    """Export all data associated with a user (GDPR Article 15).

    Returns a dict with keys for each table containing user data.
    """
    data: dict = {"user_id": user_id}

    # User record
    user = await get_user(session, user_id)
    if user:
        data["user"] = {
            "user_id": user.user_id,
            "display_name": user.display_name,
            "email": user.email,
            "role": user.role.value if user.role else None,
            "is_active": user.is_active,
            "created_at": str(user.created_at) if user.created_at else None,
        }

    # Accounts
    stmt = select(Account).where(Account.user_id == user_id)
    result = await session.execute(stmt)
    accounts = result.scalars().all()
    data["accounts"] = [
        {
            "id": a.id,
            "platform": a.platform.value if a.platform else None,
            "account_name": a.account_name,
            "is_active": a.is_active,
            "created_at": str(a.created_at) if a.created_at else None,
        }
        for a in accounts
    ]

    # Audit log entries
    stmt = (
        select(AuditLog)
        .where(AuditLog.user_id == user_id)
        .order_by(AuditLog.created_at.desc())
        .limit(1000)
    )
    result = await session.execute(stmt)
    logs = result.scalars().all()
    data["audit_log"] = [
        {
            "id": log_entry.id,
            "event_type": log_entry.event_type,
            "created_at": str(log_entry.created_at) if log_entry.created_at else None,
        }
        for log_entry in logs
    ]

    # Approval queue items
    stmt = (
        select(ApprovalQueueItem)
        .where(ApprovalQueueItem.user_id == user_id)
        .order_by(ApprovalQueueItem.created_at.desc())
        .limit(1000)
    )
    result = await session.execute(stmt)
    approvals = result.scalars().all()
    data["approvals"] = [
        {
            "id": a.id,
            "action_type": a.action_type.value if a.action_type else None,
            "status": a.status.value if a.status else None,
            "description": a.description,
            "created_at": str(a.created_at) if a.created_at else None,
        }
        for a in approvals
    ]

    # Conversation threads
    stmt = (
        select(ConversationThread)
        .where(ConversationThread.user_id == user_id)
        .order_by(ConversationThread.started_at.desc())
    )
    result = await session.execute(stmt)
    threads = result.scalars().all()
    data["conversation_threads"] = [
        {
            "id": t.id,
            "role_id": t.role_id,
            "turn_count": t.turn_count,
            "is_active": t.is_active,
            "started_at": str(t.started_at) if t.started_at else None,
        }
        for t in threads
    ]

    logger.info(
        "gdpr.export",
        user_id=user_id,
        tables=list(data.keys()),
    )
    return data


async def delete_user_data(session: AsyncSession, user_id: str) -> dict:
    """Hard-delete user data and anonymize audit trail (GDPR Article 17).

    Deletes: accounts, approvals, conversation threads, cost tracking.
    Anonymizes: audit log entries (sets user_id to 'deleted').
    Returns a summary of deleted/anonymized counts.
    """
    from sqlalchemy import delete, update

    counts: dict[str, int] = {}

    # Delete conversation threads
    stmt = delete(ConversationThread).where(ConversationThread.user_id == user_id)
    result = await session.execute(stmt)
    counts["conversation_threads"] = result.rowcount or 0

    # Delete approval queue items
    stmt = delete(ApprovalQueueItem).where(ApprovalQueueItem.user_id == user_id)
    result = await session.execute(stmt)
    counts["approvals"] = result.rowcount or 0

    # Delete accounts (cascades to campaigns, metrics, etc.)
    stmt = delete(Account).where(Account.user_id == user_id)
    result = await session.execute(stmt)
    counts["accounts"] = result.rowcount or 0

    # Anonymize audit log (keep for compliance, but remove PII)
    stmt = update(AuditLog).where(AuditLog.user_id == user_id).values(user_id="deleted")
    result = await session.execute(stmt)
    counts["audit_log_anonymized"] = result.rowcount or 0

    # Delete user record
    stmt = delete(User).where(User.user_id == user_id)
    result = await session.execute(stmt)
    counts["user"] = result.rowcount or 0

    await session.flush()

    logger.info(
        "gdpr.delete",
        user_id=user_id,
        counts=counts,
    )
    return counts


# =============================================================================
# Meeting Sessions
# =============================================================================


async def create_meeting_session(
    session: AsyncSession,
    *,
    meeting_url: str,
    role_id: str,
    user_id: str,
    bot_id: str = "",
    channel_id: str = "",
) -> MeetingSession:
    """Create a new meeting session record.

    Args:
        session: The async DB session.
        meeting_url: The video call URL.
        role_id: The Sidera role participating.
        user_id: The user who initiated the meeting join.
        bot_id: The Recall.ai bot UUID.
        channel_id: The Slack channel for notifications.

    Returns:
        The newly created ``MeetingSession`` record.
    """
    meeting = MeetingSession(
        meeting_url=meeting_url,
        role_id=role_id,
        user_id=user_id,
        bot_id=bot_id,
        channel_id=channel_id,
        status="joining",
        started_at=_utcnow(),
    )
    session.add(meeting)
    await session.flush()
    logger.info(
        "meeting.created",
        meeting_id=meeting.id,
        role_id=role_id,
        meeting_url=meeting_url,
    )
    return meeting


async def get_meeting_session(
    session: AsyncSession,
    meeting_id: int,
) -> MeetingSession | None:
    """Get a meeting session by its primary key.

    Args:
        session: The async DB session.
        meeting_id: The meeting session ID.

    Returns:
        The ``MeetingSession`` or ``None`` if not found.
    """
    stmt = select(MeetingSession).where(MeetingSession.id == meeting_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_meeting_session_by_bot_id(
    session: AsyncSession,
    bot_id: str,
) -> MeetingSession | None:
    """Get a meeting session by its Recall.ai bot UUID.

    Args:
        session: The async DB session.
        bot_id: The Recall.ai bot UUID.

    Returns:
        The ``MeetingSession`` or ``None`` if not found.
    """
    stmt = select(MeetingSession).where(MeetingSession.bot_id == bot_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def update_meeting_status(
    session: AsyncSession,
    meeting_id: int,
    status: str,
    **kwargs: Any,
) -> None:
    """Update the status and optional fields of a meeting session.

    Args:
        session: The async DB session.
        meeting_id: The meeting session ID.
        status: New status value.
        **kwargs: Additional columns to update (e.g., ``joined_at``,
            ``ended_at``, ``total_cost_usd``, ``agent_turns``, etc.).
    """
    values: dict[str, Any] = {"status": status, "updated_at": _utcnow()}
    values.update(kwargs)

    stmt = update(MeetingSession).where(MeetingSession.id == meeting_id).values(**values)
    await session.execute(stmt)
    await session.flush()
    logger.info("meeting.status_updated", meeting_id=meeting_id, status=status)


async def update_meeting_transcript(
    session: AsyncSession,
    meeting_id: int,
    transcript_json: list[dict[str, Any]],
    transcript_summary: str = "",
) -> None:
    """Update the transcript data for a meeting session.

    Args:
        session: The async DB session.
        meeting_id: The meeting session ID.
        transcript_json: The full transcript entries.
        transcript_summary: Optional LLM-generated summary.
    """
    values: dict[str, Any] = {
        "transcript_json": transcript_json,
        "updated_at": _utcnow(),
    }
    if transcript_summary:
        values["transcript_summary"] = transcript_summary

    stmt = update(MeetingSession).where(MeetingSession.id == meeting_id).values(**values)
    await session.execute(stmt)
    await session.flush()
    logger.info(
        "meeting.transcript_updated",
        meeting_id=meeting_id,
        entries=len(transcript_json),
    )


async def get_active_meetings(
    session: AsyncSession,
) -> list[MeetingSession]:
    """Get all active meeting sessions (not yet ended).

    Returns meetings with status in ('joining', 'in_call').
    """
    stmt = (
        select(MeetingSession)
        .where(MeetingSession.status.in_(["joining", "in_call"]))
        .order_by(MeetingSession.started_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


# =====================================================================
# Role messages — peer-to-peer async communication
# =====================================================================


async def create_role_message(
    session: AsyncSession,
    *,
    from_role_id: str,
    to_role_id: str,
    from_department_id: str = "",
    to_department_id: str = "",
    subject: str,
    content: str,
    reply_to_id: int | None = None,
    expires_in_days: int = 7,
    metadata: dict[str, Any] | None = None,
) -> int:
    """Create a new role-to-role message.

    Args:
        session: Async database session.
        from_role_id: Role ID of the sender.
        to_role_id: Role ID of the recipient.
        from_department_id: Sender's department ID.
        to_department_id: Recipient's department ID.
        subject: Short subject line (max 200 chars).
        content: Message body.
        reply_to_id: Optional parent message ID for threading.
        expires_in_days: Days until message expires (default 7).
        metadata: Optional extra context.

    Returns:
        The new message's ID.
    """
    now = _utcnow()
    msg = RoleMessage(
        from_role_id=from_role_id,
        to_role_id=to_role_id,
        from_department_id=from_department_id,
        to_department_id=to_department_id,
        subject=subject[:200],
        content=content,
        status="pending",
        reply_to_id=reply_to_id,
        created_at=now,
        expires_at=now + timedelta(days=expires_in_days),
        metadata_=metadata,
    )
    session.add(msg)
    await session.flush()
    logger.info(
        "role_message.created",
        from_role=from_role_id,
        to_role=to_role_id,
        subject=subject[:50],
        message_id=msg.id,
    )
    return msg.id


async def get_pending_messages(
    session: AsyncSession,
    role_id: str,
    limit: int = 10,
) -> list[RoleMessage]:
    """Get undelivered messages for a role, oldest first.

    Returns pending messages that haven't expired.
    """
    now = _utcnow()
    stmt = (
        select(RoleMessage)
        .where(
            RoleMessage.to_role_id == role_id,
            RoleMessage.status == "pending",
            (RoleMessage.expires_at > now) | (RoleMessage.expires_at.is_(None)),
        )
        .order_by(RoleMessage.created_at.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def mark_messages_delivered(
    session: AsyncSession,
    message_ids: list[int],
) -> int:
    """Mark messages as delivered (injected into agent context).

    Returns the number of messages updated.
    """
    if not message_ids:
        return 0
    now = _utcnow()
    stmt = (
        update(RoleMessage)
        .where(
            RoleMessage.id.in_(message_ids),
            RoleMessage.status == "pending",
        )
        .values(status="delivered", delivered_at=now)
    )
    result = await session.execute(stmt)
    return result.rowcount  # type: ignore[return-value]


async def mark_message_read(
    session: AsyncSession,
    message_id: int,
) -> None:
    """Mark a message as read (agent acknowledged it)."""
    now = _utcnow()
    stmt = (
        update(RoleMessage).where(RoleMessage.id == message_id).values(status="read", read_at=now)
    )
    await session.execute(stmt)


async def get_message_thread(
    session: AsyncSession,
    message_id: int,
) -> list[RoleMessage]:
    """Get a message and all its replies (thread view).

    Returns the original message and all replies, ordered by creation time.
    """
    # First get the starting message
    stmt = select(RoleMessage).where(RoleMessage.id == message_id)
    result = await session.execute(stmt)
    msg = result.scalar_one_or_none()
    if msg is None:
        return []

    # Walk up reply_to_id to find the true root of the thread
    root_id = msg.id
    current_id = msg.reply_to_id
    max_depth = 20  # Safety limit
    while current_id is not None and max_depth > 0:
        parent_stmt = select(RoleMessage).where(
            RoleMessage.id == current_id,
        )
        parent_result = await session.execute(parent_stmt)
        parent = parent_result.scalar_one_or_none()
        if parent is None:
            break
        root_id = parent.id
        current_id = parent.reply_to_id
        max_depth -= 1

    # Collect all thread IDs (root + all descendants)
    thread_ids = {root_id}
    changed = True
    while changed:
        changed = False
        child_stmt = select(RoleMessage.id).where(
            RoleMessage.reply_to_id.in_(thread_ids),
        )
        child_result = await session.execute(child_stmt)
        for (child_id,) in child_result:
            if child_id not in thread_ids:
                thread_ids.add(child_id)
                changed = True

    # Fetch all messages in the thread
    stmt = (
        select(RoleMessage)
        .where(RoleMessage.id.in_(thread_ids))
        .order_by(RoleMessage.created_at.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def expire_stale_messages(
    session: AsyncSession,
) -> int:
    """Mark pending messages past expires_at as expired.

    Called by data_retention_workflow. Returns count of expired messages.
    """
    now = _utcnow()
    stmt = (
        update(RoleMessage)
        .where(
            RoleMessage.status == "pending",
            RoleMessage.expires_at.is_not(None),
            RoleMessage.expires_at <= now,
        )
        .values(status="expired")
    )
    result = await session.execute(stmt)
    count = result.rowcount or 0
    if count:
        logger.info("role_messages.expired", count=count)
    return count


# ============================================================
# Claude Code Tasks
# ============================================================


async def create_claude_code_task(
    session: AsyncSession,
    task_id: str,
    skill_id: str,
    user_id: str,
    prompt: str,
    status: str = "pending",
    role_id: str = "",
    department_id: str = "",
    max_budget_usd: float = 5.0,
    permission_mode: str = "acceptEdits",
    inngest_run_id: str | None = None,
) -> ClaudeCodeTaskRecord:
    """Create a new Claude Code task record.

    Returns the created record with ``id`` set.
    """
    task = ClaudeCodeTaskRecord(
        task_id=task_id,
        skill_id=skill_id,
        user_id=user_id,
        prompt=prompt,
        status=status,
        role_id=role_id,
        department_id=department_id,
        max_budget_usd=max_budget_usd,
        permission_mode=permission_mode,
        inngest_run_id=inngest_run_id,
    )
    session.add(task)
    await session.flush()
    return task


async def update_claude_code_task(
    session: AsyncSession,
    task_id: str,
    *,
    status: str | None = None,
    error_message: str | None = None,
    result_text: str | None = None,
    structured_output: Any = None,
    cost_usd: float | None = None,
    num_turns: int | None = None,
    duration_ms: int | None = None,
    session_id: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> None:
    """Update fields on a Claude Code task record.

    Only non-None values are written — pass ``None`` to skip a field.
    """
    values: dict[str, Any] = {}
    if status is not None:
        values["status"] = status
    if error_message is not None:
        values["error_message"] = error_message
    if result_text is not None:
        values["result_text"] = result_text
    if structured_output is not None:
        values["structured_output"] = structured_output
    if cost_usd is not None:
        values["cost_usd"] = cost_usd
    if num_turns is not None:
        values["num_turns"] = num_turns
    if duration_ms is not None:
        values["duration_ms"] = duration_ms
    if session_id is not None:
        values["session_id"] = session_id
    if started_at is not None:
        values["started_at"] = started_at
    if completed_at is not None:
        values["completed_at"] = completed_at

    if not values:
        return

    stmt = (
        update(ClaudeCodeTaskRecord).where(ClaudeCodeTaskRecord.task_id == task_id).values(**values)
    )
    await session.execute(stmt)
    await session.flush()


async def get_claude_code_task(
    session: AsyncSession,
    task_id: str,
) -> ClaudeCodeTaskRecord | None:
    """Get a Claude Code task by ``task_id``."""
    stmt = select(ClaudeCodeTaskRecord).where(ClaudeCodeTaskRecord.task_id == task_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_claude_code_tasks(
    session: AsyncSession,
    user_id: str | None = None,
    skill_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[ClaudeCodeTaskRecord]:
    """List Claude Code tasks with optional filters.

    Returns tasks ordered by ``created_at`` descending (newest first).
    """
    stmt = (
        select(ClaudeCodeTaskRecord).order_by(ClaudeCodeTaskRecord.created_at.desc()).limit(limit)
    )
    if user_id:
        stmt = stmt.where(ClaudeCodeTaskRecord.user_id == user_id)
    if skill_id:
        stmt = stmt.where(ClaudeCodeTaskRecord.skill_id == skill_id)
    if status:
        stmt = stmt.where(ClaudeCodeTaskRecord.status == status)
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ============================================================
# Stewardship
# ============================================================


async def get_steward_for_role(
    session: AsyncSession,
    role_id: str,
) -> str | None:
    """Return the steward_user_id for a role, or None if not set."""
    stmt = select(OrgRole.steward_user_id).where(OrgRole.role_id == role_id)
    result = await session.execute(stmt)
    val = result.scalar_one_or_none()
    return val if val else None


async def get_steward_for_department(
    session: AsyncSession,
    dept_id: str,
) -> str | None:
    """Return the steward_user_id for a department, or None if not set."""
    stmt = select(OrgDepartment.steward_user_id).where(
        OrgDepartment.dept_id == dept_id,
    )
    result = await session.execute(stmt)
    val = result.scalar_one_or_none()
    return val if val else None


async def resolve_steward(
    session: AsyncSession,
    role_id: str,
    department_id: str = "",
) -> str | None:
    """Resolve the steward for a role with department fallback.

    Checks the role-level steward first.  If empty, falls back to the
    department-level steward.  Returns ``None`` when neither is set.
    """
    if role_id:
        steward = await get_steward_for_role(session, role_id)
        if steward:
            return steward
        # If no department_id passed, try to look it up from the role
        if not department_id:
            stmt = select(OrgRole.department_id).where(OrgRole.role_id == role_id)
            result = await session.execute(stmt)
            department_id = result.scalar_one_or_none() or ""
    if department_id:
        return await get_steward_for_department(session, department_id)
    return None


async def assign_steward(
    session: AsyncSession,
    scope_type: str,
    scope_id: str,
    user_id: str,
    assigned_by: str = "",
) -> bool:
    """Assign a steward to a role or department.

    Returns ``True`` if the entity was found and updated, ``False`` otherwise.
    """
    if scope_type == "role":
        stmt = select(OrgRole).where(OrgRole.role_id == scope_id)
        result = await session.execute(stmt)
        entity = result.scalars().first()
        if entity is None:
            return False
        old_steward = entity.steward_user_id or ""
        entity.steward_user_id = user_id
        entity.updated_at = _utcnow()
    elif scope_type == "department":
        stmt = select(OrgDepartment).where(OrgDepartment.dept_id == scope_id)
        result = await session.execute(stmt)
        entity = result.scalars().first()
        if entity is None:
            return False
        old_steward = entity.steward_user_id or ""
        entity.steward_user_id = user_id
        entity.updated_at = _utcnow()
    else:
        return False

    await session.flush()
    await _log_org_chart_change(
        session,
        "steward_assigned",
        scope_type,
        scope_id,
        changes={"steward_user_id": {"old": old_steward, "new": user_id}},
        created_by=assigned_by,
    )
    logger.info(
        "steward_assigned",
        scope_type=scope_type,
        scope_id=scope_id,
        steward=user_id,
        assigned_by=assigned_by,
    )
    return True


async def release_steward(
    session: AsyncSession,
    scope_type: str,
    scope_id: str,
    released_by: str = "",
) -> bool:
    """Remove the steward assignment from a role or department.

    Returns ``True`` if the entity was found and updated, ``False`` otherwise.
    """
    if scope_type == "role":
        stmt = select(OrgRole).where(OrgRole.role_id == scope_id)
        result = await session.execute(stmt)
        entity = result.scalars().first()
        if entity is None:
            return False
        old_steward = entity.steward_user_id or ""
        entity.steward_user_id = ""
        entity.updated_at = _utcnow()
    elif scope_type == "department":
        stmt = select(OrgDepartment).where(OrgDepartment.dept_id == scope_id)
        result = await session.execute(stmt)
        entity = result.scalars().first()
        if entity is None:
            return False
        old_steward = entity.steward_user_id or ""
        entity.steward_user_id = ""
        entity.updated_at = _utcnow()
    else:
        return False

    await session.flush()
    await _log_org_chart_change(
        session,
        "steward_released",
        scope_type,
        scope_id,
        changes={"steward_user_id": {"old": old_steward, "new": ""}},
        created_by=released_by,
    )
    logger.info(
        "steward_released",
        scope_type=scope_type,
        scope_id=scope_id,
        old_steward=old_steward,
        released_by=released_by,
    )
    return True


async def list_stewardships(
    session: AsyncSession,
) -> list[dict[str, str]]:
    """Return all active stewardship assignments (roles + departments)."""
    assignments: list[dict[str, str]] = []

    # Roles with stewards
    stmt = select(OrgRole).where(
        OrgRole.is_active == True,  # noqa: E712
        OrgRole.steward_user_id != "",
        OrgRole.steward_user_id.is_not(None),
    )
    result = await session.execute(stmt)
    for role in result.scalars().all():
        assignments.append(
            {
                "scope_type": "role",
                "scope_id": role.role_id,
                "steward_user_id": role.steward_user_id,
                "name": role.name,
            }
        )

    # Departments with stewards
    stmt = select(OrgDepartment).where(
        OrgDepartment.is_active == True,  # noqa: E712
        OrgDepartment.steward_user_id != "",
        OrgDepartment.steward_user_id.is_not(None),
    )
    result = await session.execute(stmt)
    for dept in result.scalars().all():
        assignments.append(
            {
                "scope_type": "department",
                "scope_id": dept.dept_id,
                "steward_user_id": dept.steward_user_id,
                "name": dept.name,
            }
        )

    return assignments


async def get_steward_roles(
    session: AsyncSession,
    user_id: str,
) -> list[dict[str, str]]:
    """Return all roles and departments a user is steward of."""
    assignments: list[dict[str, str]] = []

    stmt = select(OrgRole).where(
        OrgRole.is_active == True,  # noqa: E712
        OrgRole.steward_user_id == user_id,
    )
    result = await session.execute(stmt)
    for role in result.scalars().all():
        assignments.append(
            {
                "scope_type": "role",
                "scope_id": role.role_id,
                "name": role.name,
                "department_id": role.department_id,
            }
        )

    stmt = select(OrgDepartment).where(
        OrgDepartment.is_active == True,  # noqa: E712
        OrgDepartment.steward_user_id == user_id,
    )
    result = await session.execute(stmt)
    for dept in result.scalars().all():
        assignments.append(
            {
                "scope_type": "department",
                "scope_id": dept.dept_id,
                "name": dept.name,
            }
        )

    return assignments


# =====================================================================
# Webhook Events (always-on monitoring)
# =====================================================================


async def record_webhook_event(
    session: AsyncSession,
    *,
    source: str,
    event_type: str,
    severity: str,
    summary: str = "",
    raw_payload: dict | None = None,
    normalized_payload: dict | None = None,
    account_id: str = "",
    campaign_id: str = "",
    dedup_key: str = "",
    status: str = "received",
) -> WebhookEvent:
    """Insert a new webhook event record."""
    event = WebhookEvent(
        source=source,
        event_type=event_type,
        severity=severity,
        summary=summary,
        raw_payload=raw_payload or {},
        normalized_payload=normalized_payload or {},
        account_id=account_id or None,
        campaign_id=campaign_id or None,
        dedup_key=dedup_key or None,
        status=status,
    )
    session.add(event)
    await session.flush()
    return event


async def check_webhook_dedup(
    session: AsyncSession,
    dedup_key: str,
    window_hours: int = 1,
) -> bool:
    """Check if a webhook event with this dedup_key already exists recently.

    Returns True if a duplicate exists (event should be skipped).
    """
    if not dedup_key:
        return False

    cutoff = _utcnow() - timedelta(hours=window_hours)
    stmt = (
        select(func.count())
        .select_from(WebhookEvent)
        .where(
            WebhookEvent.dedup_key == dedup_key,
            WebhookEvent.created_at >= cutoff,
        )
    )
    result = await session.execute(stmt)
    return (result.scalar() or 0) > 0


async def update_webhook_event_status(
    session: AsyncSession,
    event_id: int,
    status: str,
    *,
    dispatched_event: str | None = None,
    role_id: str | None = None,
) -> None:
    """Update the status of a webhook event after processing."""
    values: dict[str, Any] = {"status": status}
    if dispatched_event is not None:
        values["dispatched_event"] = dispatched_event
    if role_id is not None:
        values["role_id"] = role_id
    stmt = update(WebhookEvent).where(WebhookEvent.id == event_id).values(**values)
    await session.execute(stmt)
    await session.flush()


async def get_recent_webhook_events(
    session: AsyncSession,
    *,
    source: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    hours: int = 24,
    limit: int = 20,
) -> list[WebhookEvent]:
    """Query recent webhook events with optional filters."""
    cutoff = _utcnow() - timedelta(hours=hours)
    stmt = (
        select(WebhookEvent)
        .where(WebhookEvent.created_at >= cutoff)
        .order_by(WebhookEvent.created_at.desc())
        .limit(limit)
    )
    if source:
        stmt = stmt.where(WebhookEvent.source == source)
    if event_type:
        stmt = stmt.where(WebhookEvent.event_type == event_type)
    if severity:
        stmt = stmt.where(WebhookEvent.severity == severity)
    if status:
        stmt = stmt.where(WebhookEvent.status == status)

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_webhook_event(
    session: AsyncSession,
    event_id: int,
) -> WebhookEvent | None:
    """Retrieve a single webhook event by ID."""
    stmt = select(WebhookEvent).where(WebhookEvent.id == event_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Working Group sessions
# ---------------------------------------------------------------------------


async def create_working_group(
    session: AsyncSession,
    *,
    group_id: str,
    objective: str,
    coordinator_role_id: str,
    member_role_ids: list[str],
    initiated_by: str = "",
    cost_cap_usd: float = 5.0,
    max_duration_minutes: int = 60,
    steward_user_id: str | None = None,
    slack_channel_id: str | None = None,
    slack_thread_ts: str | None = None,
) -> WorkingGroupSession:
    """Create a new working group session."""
    wg = WorkingGroupSession(
        group_id=group_id,
        objective=objective,
        coordinator_role_id=coordinator_role_id,
        member_role_ids=member_role_ids,
        initiated_by=initiated_by,
        status="forming",
        cost_cap_usd=cost_cap_usd,
        max_duration_minutes=max_duration_minutes,
        steward_user_id=steward_user_id,
        slack_channel_id=slack_channel_id,
        slack_thread_ts=slack_thread_ts,
    )
    session.add(wg)
    await session.flush()
    return wg


async def get_working_group(
    session: AsyncSession,
    group_id: str,
) -> WorkingGroupSession | None:
    """Retrieve a working group by its group_id."""
    stmt = select(WorkingGroupSession).where(
        WorkingGroupSession.group_id == group_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def update_working_group_status(
    session: AsyncSession,
    group_id: str,
    status: str,
    **kwargs: object,
) -> WorkingGroupSession | None:
    """Update a working group's status and optional fields."""
    wg = await get_working_group(session, group_id)
    if wg is None:
        return None
    wg.status = status
    for key, value in kwargs.items():
        if hasattr(wg, key):
            setattr(wg, key, value)
    await session.flush()
    return wg


async def save_member_result(
    session: AsyncSession,
    group_id: str,
    role_id: str,
    result_text: str,
    cost_usd: float = 0.0,
) -> WorkingGroupSession | None:
    """Save a member's execution result into the working group."""
    wg = await get_working_group(session, group_id)
    if wg is None:
        return None
    results = dict(wg.member_results_json or {})
    results[role_id] = {
        "output": result_text,
        "cost_usd": cost_usd,
    }
    wg.member_results_json = results
    wg.total_cost_usd = float(wg.total_cost_usd or 0) + cost_usd
    await session.flush()
    return wg


async def list_working_groups(
    session: AsyncSession,
    *,
    coordinator_role_id: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> list[WorkingGroupSession]:
    """List working groups with optional filters."""
    stmt = select(WorkingGroupSession)
    if coordinator_role_id:
        stmt = stmt.where(
            WorkingGroupSession.coordinator_role_id == coordinator_role_id,
        )
    if status:
        stmt = stmt.where(WorkingGroupSession.status == status)
    stmt = stmt.order_by(
        WorkingGroupSession.created_at.desc(),
    ).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())
