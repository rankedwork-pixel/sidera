"""Plan executor for the bootstrap pipeline.

Writes an approved ``BootstrapPlan`` to the database using existing
org chart CRUD methods.  Execution order respects foreign keys:
departments -> roles -> skills -> memories.

Idempotent: checks if each entity already exists before creating.
"""

from __future__ import annotations

import structlog

from src.bootstrap.models import (
    BootstrapPlan,
    BootstrapStatus,
    ExecutionResult,
    ExtractedDepartment,
    ExtractedMemory,
    ExtractedRole,
    ExtractedSkill,
)
from src.db import service as db_service
from src.db.session import get_db_session

logger = structlog.get_logger(__name__)


async def execute_plan(
    plan: BootstrapPlan,
    *,
    user_id: str = "bootstrap",
) -> ExecutionResult:
    """Write an approved BootstrapPlan to the database.

    Parameters
    ----------
    plan:
        The approved plan to execute.
    user_id:
        The user performing the bootstrap (for audit trail).

    Returns
    -------
    ExecutionResult
        Summary of what was created, skipped, and any errors.
    """
    if plan.status != BootstrapStatus.APPROVED.value:
        return ExecutionResult(
            plan_id=plan.id,
            errors=[f"Plan status is '{plan.status}', expected 'approved'"],
        )

    result = ExecutionResult(plan_id=plan.id)

    try:
        async with get_db_session() as session:
            # Phase 1: Departments
            for dept in plan.departments:
                await _create_department(session, dept, user_id, result)

            # Phase 2: Roles (depends on departments)
            for role in plan.roles:
                skills_for_role = [
                    s.id for s in plan.skills if s.role_id == role.id
                ]
                await _create_role(
                    session, role, skills_for_role, user_id, result
                )

            # Phase 3: Skills (depends on roles)
            for skill in plan.skills:
                await _create_skill(session, skill, user_id, result)

            # Phase 4: Seed memories
            for memory in plan.memories:
                await _seed_memory(session, memory, user_id, result)

            await session.commit()

    except Exception as exc:
        result.errors.append(f"Execution failed: {exc}")
        logger.error("bootstrap.execute_error", error=str(exc), plan_id=plan.id)

    plan.status = (
        BootstrapStatus.EXECUTED.value
        if result.success
        else BootstrapStatus.FAILED.value
    )

    summary = result.summary()
    summary.pop("plan_id", None)  # avoid duplicate kwarg
    logger.info(
        "bootstrap.execute_complete",
        plan_id=plan.id,
        **summary,
    )

    return result


# =====================================================================
# Entity creation helpers
# =====================================================================


async def _create_department(
    session,
    dept: ExtractedDepartment,
    user_id: str,
    result: ExecutionResult,
) -> None:
    """Create a department, skipping if it already exists."""
    try:
        existing = await db_service.get_org_department(session, dept.id)
        if existing:
            logger.debug(
                "bootstrap.dept_exists", dept_id=dept.id, action="skip"
            )
            result.departments_skipped += 1
            return

        # Serialize vocabulary for the DB (JSON column)
        vocab_json = (
            [{"term": v["term"], "definition": v["definition"]} for v in dept.vocabulary]
            if dept.vocabulary
            else None
        )

        await db_service.create_org_department(
            session,
            dept_id=dept.id,
            name=dept.name,
            description=dept.description,
            context=dept.context,
            context_text=dept.context,
            created_by=user_id,
        )
        result.departments_created += 1
        logger.info("bootstrap.dept_created", dept_id=dept.id, name=dept.name)

        # Set vocabulary if the DB model supports it
        if vocab_json:
            try:
                await db_service.update_org_department(
                    session, dept.id, vocabulary=vocab_json
                )
            except Exception:
                # Vocabulary column may not exist in older schemas
                pass

    except Exception as exc:
        result.errors.append(f"Failed to create dept '{dept.id}': {exc}")
        logger.warning("bootstrap.dept_create_error", dept_id=dept.id, error=str(exc))


async def _create_role(
    session,
    role: ExtractedRole,
    briefing_skill_ids: list[str],
    user_id: str,
    result: ExecutionResult,
) -> None:
    """Create a role, skipping if it already exists."""
    try:
        existing = await db_service.get_org_role(session, role.id)
        if existing:
            logger.debug("bootstrap.role_exists", role_id=role.id, action="skip")
            result.roles_skipped += 1
            return

        await db_service.create_org_role(
            session,
            role_id=role.id,
            name=role.name,
            department_id=role.department_id,
            description=role.description,
            persona=role.persona,
            connectors=role.connectors or None,
            briefing_skills=briefing_skill_ids or None,
            manages=role.manages or None,
            created_by=user_id,
        )
        result.roles_created += 1
        logger.info(
            "bootstrap.role_created",
            role_id=role.id,
            name=role.name,
            dept=role.department_id,
        )

        # Set goals and principles if available
        update_kwargs = {}
        if role.goals:
            update_kwargs["goals"] = role.goals
        if role.principles:
            update_kwargs["principles"] = role.principles
        if update_kwargs:
            try:
                await db_service.update_org_role(
                    session, role.id, **update_kwargs
                )
            except Exception:
                # Goals/principles columns may not exist in older schemas
                pass

    except Exception as exc:
        result.errors.append(f"Failed to create role '{role.id}': {exc}")
        logger.warning(
            "bootstrap.role_create_error", role_id=role.id, error=str(exc)
        )


async def _create_skill(
    session,
    skill: ExtractedSkill,
    user_id: str,
    result: ExecutionResult,
) -> None:
    """Create a skill, skipping if it already exists."""
    try:
        existing = await db_service.get_org_skill(session, skill.id)
        if existing:
            logger.debug(
                "bootstrap.skill_exists", skill_id=skill.id, action="skip"
            )
            result.skills_skipped += 1
            return

        await db_service.create_org_skill(
            session,
            skill_id=skill.id,
            name=skill.name,
            description=skill.description,
            category=skill.category,
            system_supplement=skill.system_supplement,
            prompt_template=skill.prompt_template,
            output_format=skill.output_format,
            business_guidance=skill.business_guidance,
            tools_required=skill.tools_required or None,
            model=skill.model,
            department_id=skill.department_id,
            role_id=skill.role_id,
            author="bootstrap",
            created_by=user_id,
        )
        result.skills_created += 1
        logger.info(
            "bootstrap.skill_created",
            skill_id=skill.id,
            name=skill.name,
            role=skill.role_id,
        )

    except Exception as exc:
        result.errors.append(f"Failed to create skill '{skill.id}': {exc}")
        logger.warning(
            "bootstrap.skill_create_error",
            skill_id=skill.id,
            error=str(exc),
        )


async def _seed_memory(
    session,
    memory: ExtractedMemory,
    user_id: str,
    result: ExecutionResult,
) -> None:
    """Seed a memory into the role's memory system."""
    try:
        await db_service.save_memory(
            session,
            user_id=user_id,
            role_id=memory.role_id,
            department_id=memory.department_id,
            memory_type=memory.memory_type,
            title=f"[Bootstrap] {memory.title}",
            content=memory.content,
            confidence=memory.confidence,
            evidence={"source": "bootstrap", "source_doc": memory.source_doc},
            ttl_days=0,  # never expire bootstrap memories
        )
        result.memories_seeded += 1

    except Exception as exc:
        result.errors.append(
            f"Failed to seed memory '{memory.title}': {exc}"
        )
        logger.warning(
            "bootstrap.memory_seed_error",
            title=memory.title,
            error=str(exc),
        )
