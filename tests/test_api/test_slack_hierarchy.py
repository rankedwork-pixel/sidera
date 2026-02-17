"""Tests for hierarchy-related /sidera Slack commands.

Covers: list departments, list roles, run role:<id>, run dept:<id>.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.slack import handle_sidera_command

# =====================================================================
# Helpers
# =====================================================================

_PATCH_REGISTRY = "src.skills.db_loader.load_registry_with_db"


def _make_slash_body(
    text: str,
    user_id: str = "U123",
    channel_id: str = "C456",
) -> dict:
    """Build a minimal /sidera slash command body."""
    return {
        "text": text,
        "user_id": user_id,
        "channel_id": channel_id,
    }


def _make_mock_department(dept_id: str = "marketing", name: str = "Marketing"):
    dept = MagicMock()
    dept.id = dept_id
    dept.name = name
    dept.description = f"{name} department"
    return dept


def _make_mock_role(
    role_id: str = "media_buyer",
    name: str = "Media Buyer",
    department_id: str = "marketing",
    schedule: str | None = None,
):
    role = MagicMock()
    role.id = role_id
    role.name = name
    role.department_id = department_id
    role.description = f"{name} role"
    role.briefing_skills = ("skill_a", "skill_b")
    role.schedule = schedule
    return role


# =====================================================================
# /sidera (empty) — help text includes new commands
# =====================================================================


@pytest.mark.asyncio
async def test_empty_command_shows_hierarchy_help():
    """Empty /sidera shows help text including hierarchy commands."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    body = _make_slash_body("")

    await handle_sidera_command(ack=ack, body=body, client=client)

    ack.assert_awaited_once()
    msg = client.chat_postMessage.call_args.kwargs["text"]
    assert "list departments" in msg
    assert "list roles" in msg
    assert "run role:" in msg
    assert "run dept:" in msg


# =====================================================================
# /sidera list departments
# =====================================================================


@pytest.mark.asyncio
async def test_list_departments_success():
    """Lists departments with role counts."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    body = _make_slash_body("list departments")

    mock_registry = MagicMock()
    dept = _make_mock_department()
    mock_registry.list_departments.return_value = [dept]
    mock_registry.list_roles.return_value = [
        _make_mock_role(),
        _make_mock_role(role_id="analyst"),
    ]

    with patch(_PATCH_REGISTRY, new_callable=AsyncMock, return_value=mock_registry):
        await handle_sidera_command(ack=ack, body=body, client=client)

    msg = client.chat_postMessage.call_args.kwargs["text"]
    assert "marketing" in msg
    assert "Marketing" in msg
    assert "2 roles" in msg


@pytest.mark.asyncio
async def test_list_departments_empty():
    """Shows warning when no departments found."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    body = _make_slash_body("list departments")

    mock_registry = MagicMock()
    mock_registry.list_departments.return_value = []

    with patch(_PATCH_REGISTRY, new_callable=AsyncMock, return_value=mock_registry):
        await handle_sidera_command(ack=ack, body=body, client=client)

    msg = client.chat_postMessage.call_args.kwargs["text"]
    assert "No departments" in msg


# =====================================================================
# /sidera list roles
# =====================================================================


@pytest.mark.asyncio
async def test_list_roles_success():
    """Lists roles with skill counts."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    body = _make_slash_body("list roles")

    mock_registry = MagicMock()
    role = _make_mock_role()
    mock_registry.list_roles.return_value = [role]

    with patch(_PATCH_REGISTRY, new_callable=AsyncMock, return_value=mock_registry):
        await handle_sidera_command(ack=ack, body=body, client=client)

    msg = client.chat_postMessage.call_args.kwargs["text"]
    assert "media_buyer" in msg
    assert "Media Buyer" in msg
    assert "2 skills" in msg


@pytest.mark.asyncio
async def test_list_roles_filtered_by_department():
    """Lists roles for a specific department."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    body = _make_slash_body("list roles marketing")

    mock_registry = MagicMock()
    role = _make_mock_role()
    mock_registry.list_roles.return_value = [role]

    with patch(_PATCH_REGISTRY, new_callable=AsyncMock, return_value=mock_registry):
        await handle_sidera_command(ack=ack, body=body, client=client)

    # Verify list_roles was called with the department filter
    mock_registry.list_roles.assert_called_with("marketing")
    msg = client.chat_postMessage.call_args.kwargs["text"]
    assert "marketing" in msg


@pytest.mark.asyncio
async def test_list_roles_empty():
    """Shows warning when no roles found."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    body = _make_slash_body("list roles nonexistent")

    mock_registry = MagicMock()
    mock_registry.list_roles.return_value = []

    with patch(_PATCH_REGISTRY, new_callable=AsyncMock, return_value=mock_registry):
        await handle_sidera_command(ack=ack, body=body, client=client)

    msg = client.chat_postMessage.call_args.kwargs["text"]
    assert "No roles" in msg


# =====================================================================
# /sidera list skills — backward compat
# =====================================================================


@pytest.mark.asyncio
async def test_list_skills_backward_compat():
    """/sidera list still works (same as list skills)."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    body = _make_slash_body("list")

    mock_skill = MagicMock()
    mock_skill.id = "creative_analysis"
    mock_skill.name = "Creative Analysis"
    mock_skill.description = "Analyze creatives"
    mock_skill.schedule = None
    mock_skill.role_id = ""

    mock_registry = MagicMock()
    mock_registry.list_all.return_value = [mock_skill]

    with patch(_PATCH_REGISTRY, new_callable=AsyncMock, return_value=mock_registry):
        await handle_sidera_command(ack=ack, body=body, client=client)

    msg = client.chat_postMessage.call_args.kwargs["text"]
    assert "creative_analysis" in msg


@pytest.mark.asyncio
async def test_list_skills_shows_role_tag():
    """/sidera list shows role tag when skill belongs to a role."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    body = _make_slash_body("list skills")

    mock_skill = MagicMock()
    mock_skill.id = "budget_check"
    mock_skill.name = "Budget Check"
    mock_skill.description = "Check budgets"
    mock_skill.schedule = None
    mock_skill.role_id = "media_buyer"

    mock_registry = MagicMock()
    mock_registry.list_all.return_value = [mock_skill]

    with patch(_PATCH_REGISTRY, new_callable=AsyncMock, return_value=mock_registry):
        await handle_sidera_command(ack=ack, body=body, client=client)

    msg = client.chat_postMessage.call_args.kwargs["text"]
    assert "media_buyer" in msg


# =====================================================================
# /sidera run role:<role_id>
# =====================================================================


@pytest.mark.asyncio
async def test_run_role_dispatches_event():
    """Dispatches sidera/role.run event via Inngest."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    body = _make_slash_body("run role:media_buyer")

    mock_send = AsyncMock()

    with patch(
        "src.workflows.inngest_client.inngest_client.send",
        mock_send,
    ):
        await handle_sidera_command(ack=ack, body=body, client=client)

    mock_send.assert_awaited_once()
    sent_event = mock_send.call_args.args[0]
    assert sent_event.name == "sidera/role.run"
    assert sent_event.data["role_id"] == "media_buyer"
    assert sent_event.data["user_id"] == "U123"
    assert sent_event.data["channel_id"] == "C456"

    msg = client.chat_postMessage.call_args.kwargs["text"]
    assert "media_buyer" in msg
    assert "dispatched" in msg


@pytest.mark.asyncio
async def test_run_role_empty_id():
    """Shows usage when role_id is empty."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    body = _make_slash_body("run role:")

    await handle_sidera_command(ack=ack, body=body, client=client)

    msg = client.chat_postMessage.call_args.kwargs["text"]
    assert "Usage" in msg


# =====================================================================
# /sidera run dept:<department_id>
# =====================================================================


@pytest.mark.asyncio
async def test_run_dept_dispatches_event():
    """Dispatches sidera/department.run event via Inngest."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    body = _make_slash_body("run dept:marketing")

    mock_send = AsyncMock()

    with patch(
        "src.workflows.inngest_client.inngest_client.send",
        mock_send,
    ):
        await handle_sidera_command(ack=ack, body=body, client=client)

    mock_send.assert_awaited_once()
    sent_event = mock_send.call_args.args[0]
    assert sent_event.name == "sidera/department.run"
    assert sent_event.data["department_id"] == "marketing"
    assert sent_event.data["user_id"] == "U123"

    msg = client.chat_postMessage.call_args.kwargs["text"]
    assert "marketing" in msg
    assert "dispatched" in msg


@pytest.mark.asyncio
async def test_run_dept_empty_id():
    """Shows usage when department_id is empty."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    body = _make_slash_body("run dept:")

    await handle_sidera_command(ack=ack, body=body, client=client)

    msg = client.chat_postMessage.call_args.kwargs["text"]
    assert "Usage" in msg


# =====================================================================
# /sidera run <skill_id> — backward compat
# =====================================================================


@pytest.mark.asyncio
async def test_run_skill_still_works():
    """Plain /sidera run <skill_id> still dispatches skill event."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    body = _make_slash_body("run creative_analysis")

    mock_send = AsyncMock()

    with patch(
        "src.workflows.inngest_client.inngest_client.send",
        mock_send,
    ):
        await handle_sidera_command(ack=ack, body=body, client=client)

    mock_send.assert_awaited_once()
    sent_event = mock_send.call_args.args[0]
    assert sent_event.name == "sidera/skill.run"
    assert sent_event.data["skill_id"] == "creative_analysis"
