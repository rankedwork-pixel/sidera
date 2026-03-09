"""Meta-tools for the Sidera MCP stdio server.

These ten tools provide high-level operations that are not simple
pass-through calls to the internal ``ToolRegistry``.  They set up
proper ``contextvars``, orchestrate agent turns, manage the approval
queue, spawn headless Claude Code instances, and load external
plugins — bridging the gap between Claude Code and Sidera's internal
agent infrastructure.

Meta-tools:
    1. talk_to_role  — Run a single conversation turn as a Sidera role
    2. run_role      — Execute a full role briefing (all skills)
    3. list_roles    — List departments → roles → skills hierarchy
    4. review_pending_approvals — Show pending approval queue items
    5. decide_approval — Approve or reject a pending action
    6. run_claude_code_task — Execute a skill as a headless Claude Code instance
    7. orchestrate — Multi-step orchestration with evaluation and re-tasking
    8. load_plugin — Load a Claude Code / Cowork plugin
    9. unload_plugin — Unload a plugin
   10. list_loaded_plugins — List all loaded plugins
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from mcp.types import TextContent, Tool

logger = structlog.get_logger(__name__)


# =============================================================================
# Meta-tool JSON schemas
# =============================================================================

META_TOOL_DEFINITIONS: list[Tool] = [
    Tool(
        name="talk_to_role",
        description=(
            "Send a message to a specific Sidera role (e.g. 'performance_media_buyer', "
            "'head_of_it', 'head_of_marketing') and get a response. The role responds "
            "in-character with full access to its tools (Google Ads, Meta, BigQuery, etc). "
            "Each call is stateless — Claude Code's own context provides continuity."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "role_id": {
                    "type": "string",
                    "description": (
                        "The role identifier, e.g. 'performance_media_buyer', "
                        "'reporting_analyst', 'strategist', 'head_of_marketing', "
                        "'head_of_it'"
                    ),
                },
                "message": {
                    "type": "string",
                    "description": "The message or question to send to the role.",
                },
            },
            "required": ["role_id", "message"],
        },
    ),
    Tool(
        name="run_role",
        description=(
            "Trigger a full role execution — runs all of the role's briefing skills "
            "sequentially and returns the combined output. Equivalent to "
            "'/sidera run role:<role_id>'. For manager roles, this includes "
            "delegation to sub-roles and synthesis."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "role_id": {
                    "type": "string",
                    "description": "The role to execute (e.g. 'head_of_it').",
                },
            },
            "required": ["role_id"],
        },
    ),
    Tool(
        name="list_roles",
        description=(
            "List all departments, roles, and skills in the Sidera org chart. "
            "Optionally filter by department."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "department_id": {
                    "type": "string",
                    "description": (
                        "Optional department ID to filter by (e.g. 'marketing', 'it'). "
                        "Omit to list all departments."
                    ),
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="review_pending_approvals",
        description=(
            "List pending items in the approval queue. Shows action type, "
            "description, reasoning, risk assessment, and proposed parameters "
            "for each item awaiting human decision."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": (
                        "Filter by status. Default 'pending'. Options: "
                        "'pending', 'approved', 'rejected', 'expired', 'auto_approved'"
                    ),
                    "default": "pending",
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="decide_approval",
        description=(
            "Approve or reject a pending action in the approval queue. "
            "On approval, the action is executed immediately via the "
            "appropriate connector (Google Ads, Meta, etc)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "approval_id": {
                    "type": "integer",
                    "description": "The ID of the approval queue item.",
                },
                "decision": {
                    "type": "string",
                    "enum": ["approve", "reject"],
                    "description": "Whether to approve or reject the action.",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional reason for the decision.",
                },
            },
            "required": ["approval_id", "decision"],
        },
    ),
    Tool(
        name="run_claude_code_task",
        description=(
            "Execute a Sidera skill as a headless Claude Code instance. "
            "The skill runs in a separate Claude Code process with full "
            "agentic capabilities (file editing, bash, multi-turn). "
            "Returns the output text, structured output (if applicable), "
            "and cost. Use for complex tasks that need Claude Code's "
            "full capabilities beyond what a standard agent turn provides."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "skill_id": {
                    "type": "string",
                    "description": (
                        "The skill to execute (must exist in the registry). "
                        "The skill's system_supplement, context, tools, and "
                        "output_format configure the Claude Code instance."
                    ),
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "Optional custom prompt. If omitted, the skill's prompt_template is used."
                    ),
                },
                "role_id": {
                    "type": "string",
                    "description": (
                        "Optional role context. If provided, the role's "
                        "persona, memory, and principles are injected."
                    ),
                },
                "max_budget_usd": {
                    "type": "number",
                    "description": "Cost cap for this task in USD (default: 5.0).",
                },
                "permission_mode": {
                    "type": "string",
                    "enum": [
                        "default",
                        "acceptEdits",
                        "plan",
                        "bypassPermissions",
                    ],
                    "description": (
                        "What the Claude Code instance is allowed to do (default: acceptEdits)."
                    ),
                },
                "params": {
                    "type": "object",
                    "description": (
                        "Optional key-value parameters to inject into the skill's prompt template."
                    ),
                },
            },
            "required": ["skill_id"],
        },
    ),
    Tool(
        name="orchestrate",
        description=(
            "Run a multi-step orchestration: dispatch a task to a Sidera role, "
            "evaluate the output against success criteria, and automatically "
            "refine or delegate to another role if the output is insufficient. "
            "Iterates until success or budget/iteration limits are reached. "
            "Use this for complex objectives that may require multiple attempts "
            "or cross-role coordination."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "objective": {
                    "type": "string",
                    "description": (
                        "What to accomplish. This is the initial prompt sent to the role."
                    ),
                },
                "primary_role_id": {
                    "type": "string",
                    "description": (
                        "The role to start with (e.g. 'head_of_marketing', "
                        "'head_of_it'). If not specified, uses 'head_of_marketing'."
                    ),
                },
                "success_criteria": {
                    "type": "string",
                    "description": (
                        "What makes the output acceptable. Be specific: "
                        "'Must include ROAS analysis and $ recommendations' "
                        "is better than 'good analysis'."
                    ),
                },
                "max_iterations": {
                    "type": "integer",
                    "description": (
                        "Max retry attempts (default 3, max 5). Each iteration "
                        "dispatches to a role and evaluates the result."
                    ),
                },
                "max_cost_usd": {
                    "type": "number",
                    "description": (
                        "Total cost budget in USD (default 10.0). Includes "
                        "role dispatch + evaluation costs."
                    ),
                },
            },
            "required": ["objective"],
        },
    ),
    Tool(
        name="load_plugin",
        description=(
            "Load a Claude Code / Cowork plugin into Sidera. Connects to the "
            "plugin's MCP servers, registers its tools (namespaced as "
            "'pluginname__toolname'), and imports its SKILL.md skills. "
            "After loading, all plugin tools are available to Sidera agents."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "plugin_dir": {
                    "type": "string",
                    "description": "Absolute path to the plugin directory.",
                },
                "target_department_id": {
                    "type": "string",
                    "description": (
                        "Optional department to assign imported skills to."
                    ),
                },
                "target_role_id": {
                    "type": "string",
                    "description": (
                        "Optional role to assign imported skills to."
                    ),
                },
            },
            "required": ["plugin_dir"],
        },
    ),
    Tool(
        name="unload_plugin",
        description=(
            "Unload a previously loaded plugin. Disconnects its MCP servers "
            "and removes its tools from the registry."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "plugin_name": {
                    "type": "string",
                    "description": "The plugin name to unload.",
                },
            },
            "required": ["plugin_name"],
        },
    ),
    Tool(
        name="list_loaded_plugins",
        description=(
            "List all currently loaded plugins with their tools and skills."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
]


# Build lookup dict: name → handler coroutine
META_TOOL_HANDLERS: dict[str, Any] = {}


def _register_handler(name: str):
    """Decorator to register a meta-tool handler function."""

    def decorator(func):
        META_TOOL_HANDLERS[name] = func
        return func

    return decorator


# =============================================================================
# Handler: talk_to_role
# =============================================================================


@_register_handler("talk_to_role")
async def _handle_talk_to_role(arguments: dict[str, Any]) -> list[TextContent]:
    """Run a single conversation turn as a specific Sidera role.

    Replicates the pattern from ``_run_conversation_turn_inline()`` in
    ``src/api/routes/slack.py`` but without Slack I/O.
    """
    role_id = arguments.get("role_id", "")
    message = arguments.get("message", "")

    if not role_id or not message:
        return [TextContent(type="text", text="Error: role_id and message are required.")]

    try:
        from src.agent.core import SideraAgent
        from src.db import service as db_service
        from src.db.session import get_db_session
        from src.mcp_servers.actions import clear_pending_actions, get_pending_actions
        from src.mcp_servers.delegation import (
            clear_delegation_context,
            set_delegation_context,
        )
        from src.mcp_servers.evolution import (
            clear_pending_proposals,
            clear_proposer_context,
            get_pending_proposals,
            set_proposer_context,
        )
        from src.mcp_servers.memory import clear_memory_context, set_memory_context
        from src.mcp_servers.messaging import (
            clear_messaging_context,
            compose_message_context,
            set_messaging_context,
        )
        from src.skills.db_loader import load_registry_with_db
        from src.skills.executor import compose_role_context
        from src.skills.memory import compose_memory_context, filter_superseded_memories

        # Clear stale contextvars
        clear_pending_actions()
        clear_pending_proposals()
        clear_delegation_context()
        clear_memory_context()
        clear_messaging_context()
        clear_proposer_context()

        # Load registry
        registry = await load_registry_with_db()
        role = registry.get_role(role_id)
        if role is None:
            return [TextContent(type="text", text=f"Error: Role '{role_id}' not found.")]

        dept = registry.get_department(role.department_id)

        # Load memory context (best-effort)
        memory_ctx = ""
        user_id = "claude_code"
        try:
            async with get_db_session() as session:
                memories = await db_service.get_role_memories(session, user_id, role_id, limit=10)
                superseded = await db_service.get_superseded_memory_ids(session, user_id, role_id)
                memories = filter_superseded_memories(memories, superseded)
                agent_memories = await db_service.get_agent_relationship_memories(
                    session, role_id, limit=5
                )
                all_memories = list(memories) + list(agent_memories)
                if all_memories:
                    memory_ctx = compose_memory_context(all_memories)
        except Exception:
            pass

        # Load pending peer messages (best-effort)
        message_ctx = ""
        try:
            async with get_db_session() as session:
                pending_msgs = await db_service.get_pending_messages(session, role_id, limit=10)
                message_ctx = compose_message_context(pending_msgs)
                if pending_msgs:
                    msg_ids = [m.id for m in pending_msgs]
                    await db_service.mark_messages_delivered(session, msg_ids)
        except Exception:
            pass

        # Compose role context
        role_context = compose_role_context(
            department=dept,
            role=role,
            memory_context=memory_ctx,
            registry=registry,
            pending_messages=message_ctx,
        )

        # Set contextvars for the agent turn
        is_manager = bool(getattr(role, "manages", ()))
        dept_id = dept.id if dept else ""
        if is_manager:
            set_delegation_context(role_id, registry)
        set_memory_context(role_id, dept_id, user_id)
        set_messaging_context(role_id, dept_id, registry)
        set_proposer_context(role_id, dept_id)

        # Run agent turn
        agent = SideraAgent()
        try:
            result = await agent.run_conversation_turn(
                role_id=role_id,
                role_context=role_context,
                thread_history=[],
                current_message=message,
                user_id=user_id,
                bot_user_id="",
                turn_number=1,
                is_manager=is_manager,
                user_clearance="restricted",  # Claude Code user = CEO = max clearance
            )
        finally:
            clear_memory_context()
            clear_messaging_context()
            clear_proposer_context()
            if is_manager:
                clear_delegation_context()

        # Collect pending actions + skill proposals
        pending_actions = get_pending_actions()
        skill_proposals = []
        try:
            skill_proposals = get_pending_proposals()
        except Exception:
            pass

        # Format response
        sections = [result.response_text]

        all_proposals = pending_actions + skill_proposals
        if all_proposals:
            sections.append("\n\n---\n**Proposed Actions (require approval):**")
            for i, prop in enumerate(all_proposals, 1):
                desc = prop.get("description", prop.get("action_type", "Unknown"))
                reasoning = prop.get("reasoning", "")
                sections.append(f"\n{i}. **{desc}**")
                if reasoning:
                    sections.append(f"   Reasoning: {reasoning}")

        cost = result.cost if isinstance(result.cost, dict) else {}
        cost_usd = cost.get("total_cost_usd", 0.0)
        if cost_usd > 0:
            sections.append(f"\n\n_Cost: ${cost_usd:.4f}_")

        return [TextContent(type="text", text="".join(sections))]

    except Exception as exc:
        logger.exception("talk_to_role.error", role_id=role_id)
        return [TextContent(type="text", text=f"Error talking to {role_id}: {exc}")]


# =============================================================================
# Handler: run_role
# =============================================================================


@_register_handler("run_role")
async def _handle_run_role(arguments: dict[str, Any]) -> list[TextContent]:
    """Execute a full role briefing (all skills)."""
    role_id = arguments.get("role_id", "")

    if not role_id:
        return [TextContent(type="text", text="Error: role_id is required.")]

    try:
        from src.agent.core import SideraAgent
        from src.db import service as db_service
        from src.db.session import get_db_session
        from src.mcp_servers.actions import clear_pending_actions
        from src.mcp_servers.delegation import clear_delegation_context
        from src.mcp_servers.evolution import (
            clear_pending_proposals,
            clear_proposer_context,
            set_proposer_context,
        )
        from src.mcp_servers.memory import clear_memory_context, set_memory_context
        from src.mcp_servers.messaging import (
            clear_messaging_context,
            compose_message_context,
            set_messaging_context,
        )
        from src.skills.db_loader import load_registry_with_db
        from src.skills.executor import RoleExecutor, SkillExecutor
        from src.skills.memory import compose_memory_context, filter_superseded_memories

        clear_pending_actions()
        clear_pending_proposals()
        clear_delegation_context()
        clear_memory_context()
        clear_messaging_context()
        clear_proposer_context()

        registry = await load_registry_with_db()
        role = registry.get_role(role_id)
        if role is None:
            return [TextContent(type="text", text=f"Error: Role '{role_id}' not found.")]

        dept = registry.get_department(role.department_id)
        user_id = "claude_code"
        dept_id = dept.id if dept else ""

        # Load memory (best-effort)
        memory_ctx = ""
        try:
            async with get_db_session() as session:
                memories = await db_service.get_role_memories(session, user_id, role_id, limit=10)
                superseded = await db_service.get_superseded_memory_ids(session, user_id, role_id)
                memories = filter_superseded_memories(memories, superseded)
                agent_memories = await db_service.get_agent_relationship_memories(
                    session, role_id, limit=5
                )
                all_memories = list(memories) + list(agent_memories)
                if all_memories:
                    memory_ctx = compose_memory_context(all_memories)
        except Exception:
            pass

        # Load messages (best-effort)
        message_ctx = ""
        try:
            async with get_db_session() as session:
                pending_msgs = await db_service.get_pending_messages(session, role_id, limit=10)
                message_ctx = compose_message_context(pending_msgs)
                if pending_msgs:
                    msg_ids = [m.id for m in pending_msgs]
                    await db_service.mark_messages_delivered(session, msg_ids)
        except Exception:
            pass

        # Set contextvars
        set_memory_context(role_id, dept_id, user_id)
        set_messaging_context(role_id, dept_id, registry)
        set_proposer_context(role_id, dept_id)

        is_manager = bool(getattr(role, "manages", ()))

        try:
            agent = SideraAgent()
            skill_executor = SkillExecutor(agent=agent, registry=registry)
            role_executor = RoleExecutor(skill_executor=skill_executor, registry=registry)

            if is_manager:
                from src.skills.manager import ManagerExecutor

                manager_executor = ManagerExecutor(
                    skill_executor=skill_executor,
                    role_executor=role_executor,
                    registry=registry,
                )
                mgr_result = await manager_executor.execute_manager(
                    role_id=role_id,
                    user_id=user_id,
                    accounts=[],
                    memory_context=memory_ctx,
                )
                output = mgr_result.synthesis or mgr_result.own_result.combined_output
                cost = mgr_result.total_cost
            else:
                role_result = await role_executor.execute_role(
                    role_id=role_id,
                    user_id=user_id,
                    accounts=[],
                    memory_context=memory_ctx,
                    pending_messages=message_ctx,
                )
                output = role_result.combined_output
                cost = role_result.total_cost

        finally:
            clear_memory_context()
            clear_messaging_context()
            clear_proposer_context()

        sections = [output]
        if isinstance(cost, dict):
            cost_usd = cost.get("total_cost_usd", 0.0)
        elif isinstance(cost, (int, float)):
            cost_usd = float(cost)
        else:
            cost_usd = 0.0
        if cost_usd > 0:
            sections.append(f"\n\n_Cost: ${cost_usd:.4f}_")

        return [TextContent(type="text", text="".join(sections))]

    except Exception as exc:
        logger.exception("run_role.error", role_id=role_id)
        return [TextContent(type="text", text=f"Error running role {role_id}: {exc}")]


# =============================================================================
# Handler: list_roles
# =============================================================================


@_register_handler("list_roles")
async def _handle_list_roles(arguments: dict[str, Any]) -> list[TextContent]:
    """List departments → roles → skills hierarchy."""
    try:
        from src.skills.db_loader import load_registry_with_db

        registry = await load_registry_with_db()
        filter_dept = arguments.get("department_id", "")

        lines: list[str] = []
        departments = registry.get_departments()

        for dept in departments:
            if filter_dept and dept.id != filter_dept:
                continue

            lines.append(f"## Department: {dept.name} ({dept.id})")
            if dept.context:
                lines.append(f"  Context: {dept.context[:100]}...")
            lines.append("")

            roles = registry.get_roles_for_department(dept.id)
            for role in roles:
                manages_info = ""
                if getattr(role, "manages", ()):
                    manages_info = f" [Manager → {', '.join(role.manages)}]"
                lines.append(f"  ### Role: {role.name} ({role.id}){manages_info}")
                if role.persona:
                    lines.append(f"    Persona: {role.persona[:100]}...")
                # List briefing skills
                if role.briefing_skills:
                    lines.append(f"    Skills: {', '.join(role.briefing_skills)}")
                lines.append("")

        if not lines:
            if filter_dept:
                return [
                    TextContent(
                        type="text",
                        text=f"No department found with ID '{filter_dept}'.",
                    )
                ]
            return [TextContent(type="text", text="No departments/roles found.")]

        return [TextContent(type="text", text="\n".join(lines))]

    except Exception as exc:
        logger.exception("list_roles.error")
        return [TextContent(type="text", text=f"Error listing roles: {exc}")]


# =============================================================================
# Handler: review_pending_approvals
# =============================================================================


@_register_handler("review_pending_approvals")
async def _handle_review_pending_approvals(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """List pending approval queue items."""
    status_filter = arguments.get("status", "pending")

    try:
        from sqlalchemy import select

        from src.db.session import get_db_session
        from src.models.schema import ApprovalQueueItem, ApprovalStatus

        # Map string to enum
        status_map = {s.value: s for s in ApprovalStatus}
        status_enum = status_map.get(status_filter, ApprovalStatus.PENDING)

        async with get_db_session() as session:
            stmt = (
                select(ApprovalQueueItem)
                .where(ApprovalQueueItem.status == status_enum)
                .order_by(ApprovalQueueItem.created_at.asc())
                .limit(50)
            )
            result = await session.execute(stmt)
            items = list(result.scalars().all())

        if not items:
            return [
                TextContent(
                    type="text",
                    text=f"No {status_filter} approvals found.",
                )
            ]

        lines: list[str] = [f"**{len(items)} {status_filter} approval(s):**\n"]
        for item in items:
            lines.append("---")
            lines.append(f"**ID:** {item.id}")
            lines.append(f"**Type:** {item.action_type}")
            lines.append(f"**Description:** {item.description}")
            if item.reasoning:
                lines.append(f"**Reasoning:** {item.reasoning}")
            if item.risk_assessment:
                lines.append(f"**Risk:** {item.risk_assessment}")
            if item.projected_impact:
                lines.append(f"**Projected Impact:** {item.projected_impact}")
            if item.action_params:
                params = item.action_params
                if isinstance(params, str):
                    try:
                        params = json.loads(params)
                    except Exception:
                        pass
                # Show a compact summary of params
                if isinstance(params, dict):
                    platform = params.get("platform", "")
                    if platform:
                        lines.append(f"**Platform:** {platform}")
                    # Show key params without dumping entire JSON
                    param_summary = {
                        k: v for k, v in params.items() if k not in ("platform", "customer_id")
                    }
                    if param_summary:
                        lines.append(f"**Params:** {json.dumps(param_summary, indent=2)}")
            lines.append(f"**Created:** {item.created_at}")
            lines.append("")

        return [TextContent(type="text", text="\n".join(lines))]

    except Exception as exc:
        logger.exception("review_pending_approvals.error")
        return [TextContent(type="text", text=f"Error reviewing approvals: {exc}")]


# =============================================================================
# Handler: decide_approval
# =============================================================================


@_register_handler("decide_approval")
async def _handle_decide_approval(arguments: dict[str, Any]) -> list[TextContent]:
    """Approve or reject a pending action."""
    approval_id = arguments.get("approval_id")
    decision = arguments.get("decision", "")
    reason = arguments.get("reason", "")

    if approval_id is None or decision not in ("approve", "reject"):
        return [
            TextContent(
                type="text",
                text="Error: approval_id (int) and decision ('approve'|'reject') required.",
            )
        ]

    try:
        from src.db import service as db_service
        from src.db.session import get_db_session
        from src.models.schema import ApprovalStatus

        async with get_db_session() as session:
            item = await db_service.get_approval_by_id(session, int(approval_id))
            if item is None:
                return [
                    TextContent(
                        type="text",
                        text=f"Error: Approval #{approval_id} not found.",
                    )
                ]

            if item.status != ApprovalStatus.PENDING:
                return [
                    TextContent(
                        type="text",
                        text=(
                            f"Approval #{approval_id} is already "
                            f"{item.status.value}, cannot change."
                        ),
                    )
                ]

            if decision == "reject":
                item.status = ApprovalStatus.REJECTED
                item.decided_by = "claude_code"
                item.decision_reason = reason or "Rejected via Claude Code"
                from datetime import datetime, timezone

                item.decided_at = datetime.now(timezone.utc)
                await session.commit()
                return [
                    TextContent(
                        type="text",
                        text=f"✗ Rejected approval #{approval_id}: {item.description}",
                    )
                ]

            # Approve + execute
            item.status = ApprovalStatus.APPROVED
            item.decided_by = "claude_code"
            item.decision_reason = reason or "Approved via Claude Code"
            from datetime import datetime, timezone

            item.decided_at = datetime.now(timezone.utc)
            await session.commit()

        # Execute the approved action
        try:
            from src.workflows.daily_briefing import _execute_action

            action_params = item.action_params
            if isinstance(action_params, str):
                action_params = json.loads(action_params)

            at = item.action_type
            action_type_str = str(at.value if hasattr(at, "value") else at)
            exec_result = await _execute_action(
                action_type=action_type_str,
                action_params=action_params,
                is_auto_approved=False,
            )
            return [
                TextContent(
                    type="text",
                    text=(
                        f"✓ Approved and executed #{approval_id}: "
                        f"{item.description}\n\n"
                        f"Result: {json.dumps(exec_result, indent=2, default=str)}"
                    ),
                )
            ]

        except Exception as exec_exc:
            logger.exception("decide_approval.execution_error", approval_id=approval_id)
            return [
                TextContent(
                    type="text",
                    text=(
                        f"✓ Approved #{approval_id} but execution failed: "
                        f"{exec_exc}\n\nThe approval status has been set to "
                        f"'approved'. You may need to retry manually."
                    ),
                )
            ]

    except Exception as exc:
        logger.exception("decide_approval.error", approval_id=approval_id)
        return [TextContent(type="text", text=f"Error deciding approval: {exc}")]


# =============================================================================
# Handler: run_claude_code_task
# =============================================================================


@_register_handler("run_claude_code_task")
async def _handle_run_claude_code_task(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Execute a skill as a headless Claude Code instance."""
    skill_id = arguments.get("skill_id", "")
    prompt = arguments.get("prompt", "")
    role_id = arguments.get("role_id", "")
    max_budget = arguments.get("max_budget_usd")
    permission_mode = arguments.get("permission_mode", "")
    params = arguments.get("params", {})

    if not skill_id:
        return [TextContent(type="text", text="Error: skill_id is required.")]

    try:
        from src.config import settings as _settings

        if not _settings.claude_code_enabled:
            return [
                TextContent(
                    type="text",
                    text=(
                        "Error: Claude Code task execution is disabled (claude_code_enabled=False)."
                    ),
                )
            ]

        from src.skills.db_loader import load_registry_with_db

        registry = await load_registry_with_db()
        skill = registry.get(skill_id)
        if skill is None:
            return [
                TextContent(
                    type="text",
                    text=f"Error: Skill '{skill_id}' not found.",
                )
            ]

        # Build role context if role_id provided
        role_context = ""
        memory_context = ""
        if role_id:
            role = registry.get_role(role_id)
            if role:
                dept = registry.get_department(role.department_id)
                from src.skills.executor import compose_role_context

                role_context = compose_role_context(
                    department=dept,
                    role=role,
                    registry=registry,
                )

                # Load memory (best-effort)
                try:
                    from src.db import service as db_service
                    from src.db.session import get_db_session
                    from src.skills.memory import (
                        compose_memory_context,
                        filter_superseded_memories,
                    )

                    async with get_db_session() as session:
                        memories = await db_service.get_role_memories(
                            session, "claude_code", role_id, limit=10
                        )
                        superseded = await db_service.get_superseded_memory_ids(
                            session, "claude_code", role_id
                        )
                        memories = filter_superseded_memories(memories, superseded)
                        if memories:
                            memory_context = compose_memory_context(list(memories))
                except Exception:
                    pass

        # Enforce budget cap
        budget = max_budget or _settings.claude_code_default_budget_usd
        budget = min(budget, _settings.claude_code_max_budget_usd)

        perm = permission_mode or _settings.claude_code_default_permission_mode

        # Execute
        from src.claude_code.task_manager import ClaudeCodeTaskManager

        manager = ClaudeCodeTaskManager(
            max_concurrent=_settings.claude_code_max_concurrent,
        )
        result = await manager.run_task_sync(
            skill=skill,
            prompt=prompt,
            user_id="claude_code",
            role_context=role_context,
            memory_context=memory_context,
            role_id=role_id,
            department_id=skill.department_id,
            max_budget_usd=budget,
            permission_mode=perm,
            params=params,
        )

        # Format response
        sections: list[str] = []
        if result.is_error:
            sections.append(f"**Error:** {result.error_message}")
        else:
            sections.append(result.output_text)
            if result.structured_output:
                sections.append(
                    "\n\n**Structured Output:**\n```json\n"
                    f"{json.dumps(result.structured_output, indent=2)}"
                    "\n```"
                )

        sections.append(
            f"\n\n_Cost: ${result.cost_usd:.4f} | "
            f"Turns: {result.num_turns} | "
            f"Duration: {result.duration_ms}ms_"
        )

        return [TextContent(type="text", text="".join(sections))]

    except Exception as exc:
        logger.exception("run_claude_code_task.error", skill_id=skill_id)
        return [
            TextContent(
                type="text",
                text=f"Error executing Claude Code task: {exc}",
            )
        ]


# =============================================================================
# Handler: orchestrate
# =============================================================================


@_register_handler("orchestrate")
async def _handle_orchestrate(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Run a multi-step orchestration with evaluation and re-tasking."""
    objective = arguments.get("objective", "")
    if not objective:
        return [
            TextContent(
                type="text",
                text="Error: objective is required.",
            )
        ]

    primary_role_id = arguments.get(
        "primary_role_id",
        "head_of_marketing",
    )
    success_criteria = arguments.get("success_criteria", "")
    max_iterations = arguments.get("max_iterations", 3)
    max_cost_usd = arguments.get("max_cost_usd", 10.0)

    try:
        from src.claude_code.orchestrator import Orchestrator

        orchestrator = Orchestrator()
        result = await orchestrator.run(
            objective=objective,
            primary_role_id=primary_role_id,
            success_criteria=success_criteria,
            max_iterations=max_iterations,
            max_cost_usd=max_cost_usd,
            user_id="claude_code",
        )

        # Format response
        sections: list[str] = []

        if result.success:
            sections.append("**Orchestration succeeded.**\n")
        else:
            sections.append("**Orchestration completed (not fully satisfied).**\n")
            if result.abort_reason:
                sections.append(f"Reason: {result.abort_reason}\n")

        sections.append(result.final_output)

        # Summary
        sections.append(
            f"\n\n---\n_Orchestration: {result.iterations} iteration(s) | "
            f"Cost: ${result.total_cost_usd:.4f} | "
            f"Duration: {result.total_duration_ms}ms_"
        )

        # Step details
        if len(result.steps) > 1:
            sections.append("\n\n**Step History:**")
            for step in result.steps:
                ev = step.evaluation
                verdict = ev.get("verdict", "?")
                score = ev.get("score", 0)
                dec = step.decision.get("action", "?")
                sections.append(
                    f"\n  Step {step.step_number} "
                    f"(role={step.role_id}): "
                    f"{verdict} (score={score:.1f}) → {dec}"
                )

        return [TextContent(type="text", text="".join(sections))]

    except Exception as exc:
        logger.exception("orchestrate.error")
        return [
            TextContent(
                type="text",
                text=f"Error in orchestration: {exc}",
            )
        ]


# =============================================================================
# 8. load_plugin
# =============================================================================


@_register_handler("load_plugin")
async def _handle_load_plugin(arguments: dict[str, Any]) -> list[TextContent]:
    """Load a Claude Code / Cowork plugin into Sidera."""
    from src.plugins.loader import load_plugin

    plugin_dir = arguments.get("plugin_dir", "")
    if not plugin_dir:
        return [TextContent(type="text", text="Error: plugin_dir is required.")]

    try:
        loaded = await load_plugin(
            plugin_dir=plugin_dir,
            target_department_id=arguments.get("target_department_id", ""),
            target_role_id=arguments.get("target_role_id", ""),
        )

        sections = [f"Plugin **{loaded.manifest.name}** loaded successfully."]
        if loaded.manifest.version:
            sections[0] += f" (v{loaded.manifest.version})"

        if loaded.registered_tool_names:
            sections.append(
                f"\n\n**Tools registered ({len(loaded.registered_tool_names)}):**"
            )
            for name in loaded.registered_tool_names:
                sections.append(f"\n  - `{name}`")

        if loaded.imported_skill_ids:
            sections.append(
                f"\n\n**Skills imported ({len(loaded.imported_skill_ids)}):**"
            )
            for sid in loaded.imported_skill_ids:
                sections.append(f"\n  - `{sid}`")

        failed = sum(
            1 for c in loaded.connections if not c.is_connected
        )
        if failed:
            sections.append(
                f"\n\n**Warning:** {failed} MCP server(s) failed to connect."
            )

        return [TextContent(type="text", text="".join(sections))]

    except Exception as exc:
        logger.exception("load_plugin.error")
        return [
            TextContent(type="text", text=f"Error loading plugin: {exc}")
        ]


# =============================================================================
# 9. unload_plugin
# =============================================================================


@_register_handler("unload_plugin")
async def _handle_unload_plugin(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Unload a previously loaded plugin."""
    from src.plugins.loader import get_plugin, unload_plugin

    plugin_name = arguments.get("plugin_name", "")
    if not plugin_name:
        return [
            TextContent(type="text", text="Error: plugin_name is required.")
        ]

    plugin = get_plugin(plugin_name)
    if plugin is None:
        return [
            TextContent(
                type="text",
                text=f"Plugin '{plugin_name}' is not loaded.",
            )
        ]

    tool_count = len(plugin.registered_tool_names)
    await unload_plugin(plugin_name)
    return [
        TextContent(
            type="text",
            text=(
                f"Plugin '{plugin_name}' unloaded. "
                f"Removed {tool_count} tool(s)."
            ),
        )
    ]


# =============================================================================
# 10. list_loaded_plugins
# =============================================================================


@_register_handler("list_loaded_plugins")
async def _handle_list_loaded_plugins(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """List all currently loaded plugins."""
    from src.plugins.loader import list_plugins

    plugins = list_plugins()
    if not plugins:
        return [TextContent(type="text", text="No plugins loaded.")]

    sections = [f"**{len(plugins)} plugin(s) loaded:**"]
    for p in plugins:
        m = p.manifest
        connected = sum(1 for c in p.connections if c.is_connected)
        total_servers = len(p.connections)
        sections.append(
            f"\n\n**{m.name}**"
            f"{f' v{m.version}' if m.version else ''}"
            f"\n  Source: `{m.source_dir}`"
            f"\n  MCP servers: {connected}/{total_servers} connected"
            f"\n  Tools: {len(p.registered_tool_names)}"
            f"\n  Skills: {len(p.imported_skill_ids)}"
        )
        if p.registered_tool_names:
            sections.append("\n  Tool list: " + ", ".join(
                f"`{n}`" for n in p.registered_tool_names[:10]
            ))
            if len(p.registered_tool_names) > 10:
                sections.append(
                    f" ... and {len(p.registered_tool_names) - 10} more"
                )

    return [TextContent(type="text", text="".join(sections))]
