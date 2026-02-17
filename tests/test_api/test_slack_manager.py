"""Tests for manager-related /sidera Slack commands.

Covers:
- ``/sidera run manager:<id>`` — explicit manager workflow dispatch
- ``/sidera run role:<id>`` — auto-redirect to manager workflow when role is a manager
- ``/sidera list`` — managers section in output
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


def _make_mock_role(
    role_id: str = "media_buyer",
    name: str = "Media Buyer",
    manages: tuple[str, ...] = (),
):
    """Build a mock RoleDefinition-like object."""
    role = MagicMock()
    role.id = role_id
    role.name = name
    role.manages = manages
    role.department_id = "marketing"
    role.description = f"{name} role"
    role.briefing_skills = ("skill_a",)
    role.schedule = None
    return role


def _make_mock_skill(skill_id: str = "budget_check"):
    """Build a mock SkillDefinition-like object."""
    skill = MagicMock()
    skill.id = skill_id
    skill.name = "Budget Check"
    skill.description = "Check budgets"
    skill.schedule = None
    skill.role_id = ""
    return skill


# =====================================================================
# /sidera run manager:<id> — sends correct Inngest event
# =====================================================================


@pytest.mark.asyncio
async def test_run_manager_sends_manager_event():
    """``/sidera run manager:<id>`` dispatches sidera/manager.run event."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    body = _make_slash_body("run manager:marketing_lead")

    mock_registry = MagicMock()
    mock_registry.get_role.return_value = _make_mock_role(
        role_id="marketing_lead",
        name="Marketing Lead",
        manages=("media_buyer", "analyst"),
    )
    mock_registry.is_manager.return_value = True

    mock_send = AsyncMock()

    with (
        patch(
            _PATCH_REGISTRY,
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch(
            "src.workflows.inngest_client.inngest_client.send",
            mock_send,
        ),
    ):
        await handle_sidera_command(ack=ack, body=body, client=client)

    ack.assert_awaited_once()
    mock_send.assert_awaited_once()
    sent_event = mock_send.call_args.args[0]
    assert sent_event.name == "sidera/manager.run"
    assert sent_event.data["role_id"] == "marketing_lead"
    assert sent_event.data["user_id"] == "U123"
    assert sent_event.data["channel_id"] == "C456"

    msg = client.chat_postMessage.call_args.kwargs["text"]
    assert "marketing_lead" in msg
    assert "dispatched" in msg


# =====================================================================
# /sidera run role:<id> — auto-redirects manager to manager workflow
# =====================================================================


@pytest.mark.asyncio
async def test_run_role_auto_redirects_manager():
    """``/sidera run role:<id>`` sends manager.run when role is a manager."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    body = _make_slash_body("run role:marketing_lead")

    mock_registry = MagicMock()
    mock_registry.is_manager.return_value = True

    mock_send = AsyncMock()

    with (
        patch(
            _PATCH_REGISTRY,
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch(
            "src.workflows.inngest_client.inngest_client.send",
            mock_send,
        ),
    ):
        await handle_sidera_command(ack=ack, body=body, client=client)

    mock_send.assert_awaited_once()
    sent_event = mock_send.call_args.args[0]
    assert sent_event.name == "sidera/manager.run"
    assert sent_event.data["role_id"] == "marketing_lead"

    msg = client.chat_postMessage.call_args.kwargs["text"]
    assert "Manager" in msg


@pytest.mark.asyncio
async def test_run_role_non_manager_sends_role_event():
    """``/sidera run role:<id>`` sends role.run for non-manager roles."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    body = _make_slash_body("run role:media_buyer")

    mock_registry = MagicMock()
    mock_registry.is_manager.return_value = False

    mock_send = AsyncMock()

    with (
        patch(
            _PATCH_REGISTRY,
            new_callable=AsyncMock,
            return_value=mock_registry,
        ),
        patch(
            "src.workflows.inngest_client.inngest_client.send",
            mock_send,
        ),
    ):
        await handle_sidera_command(ack=ack, body=body, client=client)

    mock_send.assert_awaited_once()
    sent_event = mock_send.call_args.args[0]
    assert sent_event.name == "sidera/role.run"
    assert sent_event.data["role_id"] == "media_buyer"

    msg = client.chat_postMessage.call_args.kwargs["text"]
    assert "Role" in msg


# =====================================================================
# /sidera run manager:<id> — rejects non-manager roles
# =====================================================================


@pytest.mark.asyncio
async def test_run_manager_rejects_non_manager():
    """``/sidera run manager:<id>`` rejects a role that is not a manager."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    body = _make_slash_body("run manager:media_buyer")

    mock_registry = MagicMock()
    mock_registry.get_role.return_value = _make_mock_role(
        role_id="media_buyer",
        manages=(),
    )
    mock_registry.is_manager.return_value = False

    with patch(
        _PATCH_REGISTRY,
        new_callable=AsyncMock,
        return_value=mock_registry,
    ):
        await handle_sidera_command(ack=ack, body=body, client=client)

    msg = client.chat_postMessage.call_args.kwargs["text"]
    assert "not a manager" in msg
    assert "media_buyer" in msg


# =====================================================================
# /sidera run manager:<id> — rejects unknown role IDs
# =====================================================================


@pytest.mark.asyncio
async def test_run_manager_rejects_unknown_role():
    """``/sidera run manager:<id>`` shows warning for unknown role ID."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    body = _make_slash_body("run manager:nonexistent_role")

    mock_registry = MagicMock()
    mock_registry.get_role.return_value = None

    with patch(
        _PATCH_REGISTRY,
        new_callable=AsyncMock,
        return_value=mock_registry,
    ):
        await handle_sidera_command(ack=ack, body=body, client=client)

    msg = client.chat_postMessage.call_args.kwargs["text"]
    assert "not found" in msg
    assert "nonexistent_role" in msg


# =====================================================================
# /sidera list — includes managers section when managers exist
# =====================================================================


@pytest.mark.asyncio
async def test_list_includes_managers_section():
    """``/sidera list`` output includes Managers section."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    body = _make_slash_body("list")

    mock_skill = _make_mock_skill()
    mock_manager = _make_mock_role(
        role_id="marketing_lead",
        name="Marketing Lead",
        manages=("media_buyer", "analyst"),
    )

    mock_registry = MagicMock()
    mock_registry.list_all.return_value = [mock_skill]
    mock_registry.list_managers.return_value = [mock_manager]

    with patch(
        _PATCH_REGISTRY,
        new_callable=AsyncMock,
        return_value=mock_registry,
    ):
        await handle_sidera_command(ack=ack, body=body, client=client)

    msg = client.chat_postMessage.call_args.kwargs["text"]
    assert "Managers" in msg
    assert "Marketing Lead" in msg
    assert "media_buyer" in msg
    assert "analyst" in msg


# =====================================================================
# /sidera list — no managers section when no managers exist
# =====================================================================


@pytest.mark.asyncio
async def test_list_no_managers_section_when_none():
    """``/sidera list`` has no Managers section when no managers exist."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    body = _make_slash_body("list")

    mock_skill = _make_mock_skill()

    mock_registry = MagicMock()
    mock_registry.list_all.return_value = [mock_skill]
    mock_registry.list_managers.return_value = []

    with patch(
        _PATCH_REGISTRY,
        new_callable=AsyncMock,
        return_value=mock_registry,
    ):
        await handle_sidera_command(ack=ack, body=body, client=client)

    msg = client.chat_postMessage.call_args.kwargs["text"]
    assert "Managers" not in msg
    # Skills should still be listed
    assert "budget_check" in msg


# =====================================================================
# /sidera run manager: — empty id shows usage
# =====================================================================


@pytest.mark.asyncio
async def test_run_manager_empty_id_shows_usage():
    """``/sidera run manager:`` with empty id shows usage hint."""
    ack = AsyncMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    body = _make_slash_body("run manager:")

    await handle_sidera_command(ack=ack, body=body, client=client)

    msg = client.chat_postMessage.call_args.kwargs["text"]
    assert "Usage" in msg
