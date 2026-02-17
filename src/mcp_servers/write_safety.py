"""Shared approval verification for write MCP tools.

Every write tool in Sidera must prove that a human approved the action
before touching any ad account.  This module provides the common safety
layer used by all write tool handlers:

1. **verify_and_load_approval** — loads the approval queue item from the DB,
   checks that it is APPROVED and not already executed.
2. **log_execution_start** — writes an ``action_execution_started`` event to
   the audit log *before* the connector write call.
3. **record_execution_result** — writes success/failure to the approval item
   and logs an ``action_execution_completed`` event.

By centralising these checks, we guarantee that no write tool can skip
the approval verification step — they all call the same entrypoint.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.db import service as db
from src.db.session import get_db_session
from src.models.schema import ApprovalQueueItem, ApprovalStatus

logger = structlog.get_logger(__name__)


# =========================================================================
# 1. Approval verification
# =========================================================================


async def verify_and_load_approval(
    approval_id: int,
) -> tuple[ApprovalQueueItem | None, str]:
    """Load and verify an approval queue item.

    Checks:
    - Item exists in the database.
    - Status is ``APPROVED``.
    - Not already executed (``executed_at`` is ``None``).

    Args:
        approval_id: Primary key of the approval queue item.

    Returns:
        ``(item, "")`` on success, or ``(None, error_message)`` on failure.
    """
    async with get_db_session() as session:
        item = await db.get_approval_by_id(session, approval_id)

        if item is None:
            msg = f"Approval #{approval_id} not found."
            logger.warning("write_safety.not_found", approval_id=approval_id)
            return None, msg

        if item.status not in (
            ApprovalStatus.APPROVED,
            ApprovalStatus.AUTO_APPROVED,
        ):
            msg = (
                f"Approval #{approval_id} has status '{item.status.value}', "
                f"expected 'approved' or 'auto_approved'."
            )
            logger.warning(
                "write_safety.wrong_status",
                approval_id=approval_id,
                status=item.status.value,
            )
            return None, msg

        if item.executed_at is not None:
            msg = f"Approval #{approval_id} was already executed at {item.executed_at.isoformat()}."
            logger.warning(
                "write_safety.already_executed",
                approval_id=approval_id,
                executed_at=item.executed_at.isoformat(),
            )
            return None, msg

    return item, ""


# =========================================================================
# 2. Pre-execution audit log
# =========================================================================


async def log_execution_start(
    approval_id: int,
    user_id: str,
    action_type: str,
    action_params: dict[str, Any],
) -> None:
    """Write an ``action_execution_started`` event to the audit log.

    Called immediately *before* the connector write method so there is
    always a record that the system attempted execution.

    Args:
        approval_id: Approval queue item ID.
        user_id: Owner of the approval.
        action_type: The ``ActionType`` value (e.g. ``budget_change``).
        action_params: Full action parameters from the approval item.
    """
    async with get_db_session() as session:
        await db.log_event(
            session,
            user_id=user_id,
            event_type="action_execution_started",
            event_data={
                "approval_id": approval_id,
                "action_type": action_type,
                "action_params": action_params,
            },
            source="approval_workflow",
            required_approval=True,
            approval_status="approved",
        )

    logger.info(
        "write_safety.execution_started",
        approval_id=approval_id,
        action_type=action_type,
    )


# =========================================================================
# 3. Post-execution result recording
# =========================================================================


async def record_execution_outcome(
    approval_id: int,
    user_id: str,
    action_type: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Record execution success or failure on the approval item and audit log.

    On success, stores ``execution_result`` JSON on the approval item.
    On failure, stores ``execution_error`` text.  Both paths write to the
    audit log so the timeline is complete.

    Args:
        approval_id: Approval queue item ID.
        user_id: Owner of the approval.
        action_type: The ``ActionType`` value.
        result: Connector result dict on success, ``None`` on failure.
        error: Error message on failure, ``None`` on success.
    """
    async with get_db_session() as session:
        # Update the approval queue item
        await db.record_execution_result(
            session,
            approval_id=approval_id,
            execution_result=result,
            execution_error=error,
        )

        # Write audit log
        event_data: dict[str, Any] = {
            "approval_id": approval_id,
            "action_type": action_type,
            "success": error is None,
        }
        if result is not None:
            event_data["result"] = result
        if error is not None:
            event_data["error"] = error

        await db.log_event(
            session,
            user_id=user_id,
            event_type="action_execution_completed",
            event_data=event_data,
            source="approval_workflow",
            required_approval=True,
            approval_status="approved",
        )

    logger.info(
        "write_safety.execution_completed",
        approval_id=approval_id,
        action_type=action_type,
        success=error is None,
    )
