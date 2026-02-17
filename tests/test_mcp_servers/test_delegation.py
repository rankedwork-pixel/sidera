"""Tests for src.mcp_servers.delegation -- delegate_to_role + consult_peer.

Covers:
- delegation without context set → error
- delegation to unmanaged role → error
- delegation to nonexistent role → error
- max delegations enforced
- valid delegation calls run_agent_loop with sub-role context
- delegation cost tracked
- sub-role doesn't receive delegate_to_role tool (no recursion)
- context set/clear lifecycle
- peer consultation between managers
- peer consultation to non-manager → error
- peer consultation to self → error
- shared delegation count between delegate and consult
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.mcp_servers.delegation import (
    _MAX_DELEGATIONS_PER_TURN,
    clear_delegation_context,
    consult_peer,
    delegate_to_role,
    get_delegation_results,
    set_delegation_context,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine synchronously for testing."""
    return asyncio.run(coro)


def _is_error(result: dict) -> bool:
    """Check if an MCP response is an error."""
    return result.get("is_error", False)


def _text(result: dict) -> str:
    """Extract text from an MCP response."""
    return result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Fake types for testing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeRole:
    id: str = "performance_media_buyer"
    name: str = "Performance Media Buyer"
    department_id: str = "marketing"
    description: str = "Buys media"
    persona: str = "I buy media."
    manages: tuple[str, ...] = ()
    briefing_skills: tuple[str, ...] = ("daily_spend_analysis",)
    connectors: tuple[str, ...] = ()
    context_files: tuple[str, ...] = ()
    source_dir: str = ""
    context_text: str = ""
    principles: tuple[str, ...] = ()
    delegation_model: str = "standard"
    synthesis_prompt: str = ""
    schedule: str | None = None


@dataclass(frozen=True)
class FakeDepartment:
    id: str = "marketing"
    name: str = "Marketing"
    description: str = "Marketing dept"
    context: str = ""
    context_files: tuple[str, ...] = ()
    source_dir: str = ""
    context_text: str = ""


@dataclass
class FakeTurnResult:
    text: str = "Sub-role completed the task."
    cost: dict[str, Any] = field(
        default_factory=lambda: {
            "total_cost_usd": 0.05,
            "input_tokens": 500,
            "output_tokens": 200,
        }
    )
    turn_count: int = 2
    session_id: str = ""
    is_error: bool = False


class FakeRegistry:
    """Minimal registry mock with get_role, get_department, list_roles."""

    def __init__(
        self,
        roles: dict[str, FakeRole] | None = None,
        departments: dict[str, FakeDepartment] | None = None,
    ):
        self._roles = roles or {}
        self._departments = departments or {}

    def get_role(self, role_id: str) -> FakeRole | None:
        return self._roles.get(role_id)

    def get_department(self, dept_id: str) -> FakeDepartment | None:
        return self._departments.get(dept_id)

    def list_roles(
        self,
        department_id: str | None = None,
    ) -> list[FakeRole]:
        roles = list(self._roles.values())
        if department_id:
            roles = [r for r in roles if r.department_id == department_id]
        return sorted(roles, key=lambda r: r.id)

    def list_skills_for_role(self, role_id: str) -> list:
        return []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MANAGER_ROLE = FakeRole(
    id="head_of_marketing",
    name="Head of Marketing",
    department_id="marketing",
    description="Manages the marketing team",
    persona="I lead the marketing department.",
    manages=("performance_media_buyer", "reporting_analyst"),
)

_SUB_ROLE = FakeRole(
    id="performance_media_buyer",
    name="Performance Media Buyer",
    department_id="marketing",
    description="Buys media",
    persona="I buy media.",
    briefing_skills=("daily_spend_analysis",),
)

_ANALYST_ROLE = FakeRole(
    id="reporting_analyst",
    name="Reporting Analyst",
    department_id="marketing",
    description="Analyzes reports",
    persona="I analyze data.",
)

_IT_MANAGER_ROLE = FakeRole(
    id="head_of_it",
    name="Head of IT",
    department_id="it",
    description="Manages the IT department",
    persona="I oversee all systems and infrastructure.",
    manages=("it_engineer",),
)

_IT_ENGINEER_ROLE = FakeRole(
    id="it_engineer",
    name="IT Engineer",
    department_id="it",
    description="Manages systems",
    persona="I keep the lights on.",
)

_DEPT = FakeDepartment()

_IT_DEPT = FakeDepartment(
    id="it",
    name="IT",
    description="IT department",
)


def _make_registry() -> FakeRegistry:
    return FakeRegistry(
        roles={
            "head_of_marketing": _MANAGER_ROLE,
            "performance_media_buyer": _SUB_ROLE,
            "reporting_analyst": _ANALYST_ROLE,
            "head_of_it": _IT_MANAGER_ROLE,
            "it_engineer": _IT_ENGINEER_ROLE,
        },
        departments={"marketing": _DEPT, "it": _IT_DEPT},
    )


@pytest.fixture(autouse=True)
def _clean_delegation_state():
    """Ensure delegation context is clear before and after each test."""
    clear_delegation_context()
    yield
    clear_delegation_context()


# ===========================================================================
# 1. No context → error
# ===========================================================================


class TestDelegateWithoutContext:
    """delegate_to_role called without set_delegation_context → error."""

    def test_no_context_returns_error(self):
        result = _run(
            delegate_to_role.handler(
                {
                    "role_id": "performance_media_buyer",
                    "task": "Run search term audit",
                }
            )
        )
        assert _is_error(result)
        assert "not available" in _text(result).lower()


# ===========================================================================
# 2. Missing args → error
# ===========================================================================


class TestDelegateMissingArgs:
    def test_missing_role_id(self):
        set_delegation_context("head_of_marketing", _make_registry())
        result = _run(
            delegate_to_role.handler(
                {
                    "task": "Run search term audit",
                }
            )
        )
        assert _is_error(result)
        assert "required" in _text(result).lower()

    def test_missing_task(self):
        set_delegation_context("head_of_marketing", _make_registry())
        result = _run(
            delegate_to_role.handler(
                {
                    "role_id": "performance_media_buyer",
                }
            )
        )
        assert _is_error(result)
        assert "required" in _text(result).lower()


# ===========================================================================
# 3. Unmanaged role → error
# ===========================================================================


class TestDelegateToUnmanagedRole:
    def test_unmanaged_role_returns_error(self):
        set_delegation_context("head_of_marketing", _make_registry())
        result = _run(
            delegate_to_role.handler(
                {
                    "role_id": "it_engineer",
                    "task": "Check system health",
                }
            )
        )
        assert _is_error(result)
        assert "cannot delegate" in _text(result).lower()
        assert "performance_media_buyer" in _text(result)


# ===========================================================================
# 4. Nonexistent sub-role → error
# ===========================================================================


class TestDelegateToNonexistentRole:
    def test_nonexistent_role_returns_error(self):
        registry = FakeRegistry(
            roles={"head_of_marketing": _MANAGER_ROLE},
            departments={"marketing": _DEPT},
        )
        set_delegation_context("head_of_marketing", registry)
        result = _run(
            delegate_to_role.handler(
                {
                    "role_id": "performance_media_buyer",
                    "task": "Run search term audit",
                }
            )
        )
        assert _is_error(result)
        assert "not found" in _text(result).lower()


# ===========================================================================
# 5. Manager role not found → error
# ===========================================================================


class TestDelegateManagerNotFound:
    def test_manager_not_in_registry(self):
        registry = FakeRegistry(
            roles={"performance_media_buyer": _SUB_ROLE},
            departments={"marketing": _DEPT},
        )
        set_delegation_context("nonexistent_manager", registry)
        result = _run(
            delegate_to_role.handler(
                {
                    "role_id": "performance_media_buyer",
                    "task": "Run search term audit",
                }
            )
        )
        assert _is_error(result)
        assert "not found" in _text(result).lower()


# ===========================================================================
# 6. Max delegations enforced
# ===========================================================================


class TestMaxDelegationsEnforced:
    def test_exceeds_max_delegations(self):
        set_delegation_context("head_of_marketing", _make_registry())
        from src.mcp_servers.delegation import _delegation_count_var

        _delegation_count_var.set(_MAX_DELEGATIONS_PER_TURN)

        result = _run(
            delegate_to_role.handler(
                {
                    "role_id": "performance_media_buyer",
                    "task": "Run search term audit",
                }
            )
        )
        assert _is_error(result)
        assert "maximum" in _text(result).lower()


# ===========================================================================
# 7. Successful delegation
# ===========================================================================


class TestSuccessfulDelegation:
    """Valid delegation runs inner agent and returns result."""

    @patch("src.agent.api_client.run_agent_loop", new_callable=AsyncMock)
    @patch("src.skills.executor.compose_role_context")
    def test_valid_delegation_returns_result(
        self,
        mock_compose,
        mock_run_loop,
    ):
        mock_compose.return_value = "Sub-role context here"
        mock_run_loop.return_value = FakeTurnResult(
            text="Found 12 wasted search terms.",
        )

        set_delegation_context("head_of_marketing", _make_registry())
        result = _run(
            delegate_to_role.handler(
                {
                    "role_id": "performance_media_buyer",
                    "task": "Run search term audit on account 12345",
                }
            )
        )

        assert not _is_error(result)
        text = _text(result)
        assert "Performance Media Buyer" in text
        assert "12 wasted search terms" in text

    @patch("src.agent.api_client.run_agent_loop", new_callable=AsyncMock)
    @patch("src.skills.executor.compose_role_context")
    def test_delegation_increments_count(
        self,
        mock_compose,
        mock_run_loop,
    ):
        mock_compose.return_value = "ctx"
        mock_run_loop.return_value = FakeTurnResult()

        set_delegation_context("head_of_marketing", _make_registry())

        _run(
            delegate_to_role.handler(
                {
                    "role_id": "performance_media_buyer",
                    "task": "Task 1",
                }
            )
        )

        results = get_delegation_results()
        assert len(results) == 1
        assert results[0]["role_id"] == "performance_media_buyer"
        assert results[0]["success"] is True
        expected_cost = {
            "total_cost_usd": 0.05,
            "input_tokens": 500,
            "output_tokens": 200,
        }
        assert results[0]["cost"] == expected_cost

    @patch("src.agent.api_client.run_agent_loop", new_callable=AsyncMock)
    @patch("src.skills.executor.compose_role_context")
    def test_sub_role_excludes_delegate_tool(
        self,
        mock_compose,
        mock_run_loop,
    ):
        """Sub-role should NOT get delegate_to_role tool."""
        mock_compose.return_value = "ctx"
        mock_run_loop.return_value = FakeTurnResult()

        set_delegation_context("head_of_marketing", _make_registry())

        _run(
            delegate_to_role.handler(
                {
                    "role_id": "performance_media_buyer",
                    "task": "Task",
                }
            )
        )

        # Check the tools passed to run_agent_loop
        call_args = mock_run_loop.call_args
        tools_passed = call_args.kwargs.get("tools") or call_args[1].get("tools", [])
        tool_names = [t["name"] for t in tools_passed]
        assert "delegate_to_role" not in tool_names

    @patch("src.agent.api_client.run_agent_loop", new_callable=AsyncMock)
    @patch("src.skills.executor.compose_role_context")
    def test_delegation_error_tracked(
        self,
        mock_compose,
        mock_run_loop,
    ):
        """If sub-role returns an error, it's reflected in results."""
        mock_compose.return_value = "ctx"
        mock_run_loop.return_value = FakeTurnResult(
            text="Something went wrong.",
            is_error=True,
        )

        set_delegation_context("head_of_marketing", _make_registry())
        result = _run(
            delegate_to_role.handler(
                {
                    "role_id": "performance_media_buyer",
                    "task": "Task",
                }
            )
        )

        text = _text(result)
        assert "error" in text.lower() or "encountered" in text.lower()

    @patch("src.agent.api_client.run_agent_loop", new_callable=AsyncMock)
    @patch("src.skills.executor.compose_role_context")
    def test_multiple_delegations(
        self,
        mock_compose,
        mock_run_loop,
    ):
        """Can delegate to different sub-roles up to max."""
        mock_compose.return_value = "ctx"
        mock_run_loop.return_value = FakeTurnResult()

        set_delegation_context("head_of_marketing", _make_registry())

        _run(
            delegate_to_role.handler(
                {
                    "role_id": "performance_media_buyer",
                    "task": "Task 1",
                }
            )
        )
        _run(
            delegate_to_role.handler(
                {
                    "role_id": "reporting_analyst",
                    "task": "Task 2",
                }
            )
        )

        results = get_delegation_results()
        assert len(results) == 2
        assert results[0]["role_id"] == "performance_media_buyer"
        assert results[1]["role_id"] == "reporting_analyst"


# ===========================================================================
# 8. Context lifecycle
# ===========================================================================


class TestContextLifecycle:
    """set/clear/get delegation context behaves correctly."""

    def test_set_then_clear(self):
        set_delegation_context("head_of_marketing", _make_registry())
        clear_delegation_context()

        result = _run(
            delegate_to_role.handler(
                {
                    "role_id": "performance_media_buyer",
                    "task": "Task",
                }
            )
        )
        assert _is_error(result)

    def test_get_results_clears_list(self):
        """get_delegation_results returns and clears accumulated."""
        from src.mcp_servers.delegation import _delegation_results_var

        _delegation_results_var.set(
            [
                {"role_id": "test", "cost": {}, "success": True},
            ]
        )

        results = get_delegation_results()
        assert len(results) == 1

        results2 = get_delegation_results()
        assert len(results2) == 0


# ===========================================================================
# 9. Exception handling in delegation
# ===========================================================================


class TestDelegationExceptionHandling:
    """If run_agent_loop raises, delegation records failure."""

    @patch("src.agent.api_client.run_agent_loop", new_callable=AsyncMock)
    @patch("src.skills.executor.compose_role_context")
    def test_exception_returns_error_and_tracks(
        self,
        mock_compose,
        mock_run_loop,
    ):
        mock_compose.return_value = "ctx"
        mock_run_loop.side_effect = RuntimeError("API timeout")

        set_delegation_context("head_of_marketing", _make_registry())
        result = _run(
            delegate_to_role.handler(
                {
                    "role_id": "performance_media_buyer",
                    "task": "Task",
                }
            )
        )

        assert _is_error(result)
        assert "failed" in _text(result).lower()

        results = get_delegation_results()
        assert len(results) == 1
        assert results[0]["success"] is False


# ===========================================================================
# 10. Peer consultation — no context
# ===========================================================================


class TestConsultPeerWithoutContext:
    """consult_peer called without set_delegation_context → error."""

    def test_no_context_returns_error(self):
        result = _run(
            consult_peer.handler(
                {
                    "role_id": "head_of_it",
                    "question": "Any system issues?",
                }
            )
        )
        assert _is_error(result)
        assert "not available" in _text(result).lower()


# ===========================================================================
# 11. Peer consultation — missing args
# ===========================================================================


class TestConsultPeerMissingArgs:
    def test_missing_role_id(self):
        set_delegation_context("head_of_marketing", _make_registry())
        result = _run(
            consult_peer.handler(
                {
                    "question": "Any system issues?",
                }
            )
        )
        assert _is_error(result)
        assert "required" in _text(result).lower()

    def test_missing_question(self):
        set_delegation_context("head_of_marketing", _make_registry())
        result = _run(
            consult_peer.handler(
                {
                    "role_id": "head_of_it",
                }
            )
        )
        assert _is_error(result)
        assert "required" in _text(result).lower()


# ===========================================================================
# 12. Peer consultation — consult self
# ===========================================================================


class TestConsultPeerSelf:
    def test_cannot_consult_self(self):
        set_delegation_context("head_of_marketing", _make_registry())
        result = _run(
            consult_peer.handler(
                {
                    "role_id": "head_of_marketing",
                    "question": "What should I do?",
                }
            )
        )
        assert _is_error(result)
        assert "yourself" in _text(result).lower()


# ===========================================================================
# 13. Peer consultation — target not a manager
# ===========================================================================


class TestConsultPeerNotManager:
    def test_non_manager_target_returns_error(self):
        set_delegation_context("head_of_marketing", _make_registry())
        result = _run(
            consult_peer.handler(
                {
                    "role_id": "performance_media_buyer",
                    "question": "What's your view?",
                }
            )
        )
        assert _is_error(result)
        assert "not a department head" in _text(result).lower()
        # Should suggest available peers
        assert "head_of_it" in _text(result)


# ===========================================================================
# 14. Peer consultation — nonexistent role
# ===========================================================================


class TestConsultPeerNonexistent:
    def test_nonexistent_role_returns_error(self):
        set_delegation_context("head_of_marketing", _make_registry())
        result = _run(
            consult_peer.handler(
                {
                    "role_id": "head_of_finance",
                    "question": "Budget question",
                }
            )
        )
        assert _is_error(result)
        assert "not found" in _text(result).lower()


# ===========================================================================
# 15. Successful peer consultation
# ===========================================================================


class TestSuccessfulPeerConsultation:
    """Valid peer consultation runs inner agent and returns result."""

    @patch("src.agent.api_client.run_agent_loop", new_callable=AsyncMock)
    @patch("src.skills.executor.compose_role_context")
    def test_valid_consultation_returns_result(
        self,
        mock_compose,
        mock_run_loop,
    ):
        mock_compose.return_value = "IT head context"
        mock_run_loop.return_value = FakeTurnResult(
            text="All systems nominal. Redis latency is 2ms.",
        )

        set_delegation_context("head_of_marketing", _make_registry())
        result = _run(
            consult_peer.handler(
                {
                    "role_id": "head_of_it",
                    "question": "Are there any system issues affecting tracking?",
                }
            )
        )

        assert not _is_error(result)
        text = _text(result)
        assert "Head of IT" in text
        assert "Redis latency" in text

    @patch("src.agent.api_client.run_agent_loop", new_callable=AsyncMock)
    @patch("src.skills.executor.compose_role_context")
    def test_consultation_tracked_with_type(
        self,
        mock_compose,
        mock_run_loop,
    ):
        """Consultations should have type='consultation' in results."""
        mock_compose.return_value = "ctx"
        mock_run_loop.return_value = FakeTurnResult()

        set_delegation_context("head_of_marketing", _make_registry())

        _run(
            consult_peer.handler(
                {
                    "role_id": "head_of_it",
                    "question": "Quick question",
                }
            )
        )

        results = get_delegation_results()
        assert len(results) == 1
        assert results[0]["role_id"] == "head_of_it"
        assert results[0]["type"] == "consultation"
        assert results[0]["success"] is True

    @patch("src.agent.api_client.run_agent_loop", new_callable=AsyncMock)
    @patch("src.skills.executor.compose_role_context")
    def test_consultation_excludes_delegation_tools(
        self,
        mock_compose,
        mock_run_loop,
    ):
        """Peer should NOT get delegate_to_role or consult_peer."""
        mock_compose.return_value = "ctx"
        mock_run_loop.return_value = FakeTurnResult()

        set_delegation_context("head_of_marketing", _make_registry())

        _run(
            consult_peer.handler(
                {
                    "role_id": "head_of_it",
                    "question": "Check systems",
                }
            )
        )

        call_args = mock_run_loop.call_args
        tools_passed = call_args.kwargs.get("tools") or call_args[1].get("tools", [])
        tool_names = [t["name"] for t in tools_passed]
        assert "delegate_to_role" not in tool_names
        assert "consult_peer" not in tool_names

    @patch("src.agent.api_client.run_agent_loop", new_callable=AsyncMock)
    @patch("src.skills.executor.compose_role_context")
    def test_consultation_prompt_includes_caller_name(
        self,
        mock_compose,
        mock_run_loop,
    ):
        """The peer should see who is consulting them."""
        mock_compose.return_value = "ctx"
        mock_run_loop.return_value = FakeTurnResult()

        set_delegation_context("head_of_marketing", _make_registry())

        _run(
            consult_peer.handler(
                {
                    "role_id": "head_of_it",
                    "question": "Are tracking pixels firing?",
                }
            )
        )

        call_args = mock_run_loop.call_args
        user_prompt = call_args.kwargs.get("user_prompt") or call_args[1].get("user_prompt", "")
        assert "Head of Marketing" in user_prompt


# ===========================================================================
# 16. Shared count between delegation and consultation
# ===========================================================================


class TestSharedDelegationCount:
    """Delegations and consultations share the same per-turn counter."""

    @patch("src.agent.api_client.run_agent_loop", new_callable=AsyncMock)
    @patch("src.skills.executor.compose_role_context")
    def test_mixed_delegation_and_consultation(
        self,
        mock_compose,
        mock_run_loop,
    ):
        mock_compose.return_value = "ctx"
        mock_run_loop.return_value = FakeTurnResult()

        set_delegation_context("head_of_marketing", _make_registry())

        # 1 delegation
        _run(
            delegate_to_role.handler(
                {
                    "role_id": "performance_media_buyer",
                    "task": "Task 1",
                }
            )
        )

        # 1 consultation
        _run(
            consult_peer.handler(
                {
                    "role_id": "head_of_it",
                    "question": "Question 1",
                }
            )
        )

        results = get_delegation_results()
        assert len(results) == 2
        # First is delegation (no type field)
        assert results[0]["role_id"] == "performance_media_buyer"
        # Second is consultation
        assert results[1]["role_id"] == "head_of_it"
        assert results[1]["type"] == "consultation"

    @patch("src.agent.api_client.run_agent_loop", new_callable=AsyncMock)
    @patch("src.skills.executor.compose_role_context")
    def test_max_count_shared(
        self,
        mock_compose,
        mock_run_loop,
    ):
        """After max delegations, consultation also blocked."""
        mock_compose.return_value = "ctx"
        mock_run_loop.return_value = FakeTurnResult()

        set_delegation_context("head_of_marketing", _make_registry())

        from src.mcp_servers.delegation import _delegation_count_var

        _delegation_count_var.set(_MAX_DELEGATIONS_PER_TURN)

        result = _run(
            consult_peer.handler(
                {
                    "role_id": "head_of_it",
                    "question": "Question",
                }
            )
        )
        assert _is_error(result)
        assert "maximum" in _text(result).lower()


# ===========================================================================
# 17. Peer consultation exception handling
# ===========================================================================


class TestConsultPeerExceptionHandling:
    """If run_agent_loop raises during consultation, failure recorded."""

    @patch("src.agent.api_client.run_agent_loop", new_callable=AsyncMock)
    @patch("src.skills.executor.compose_role_context")
    def test_exception_returns_error_and_tracks(
        self,
        mock_compose,
        mock_run_loop,
    ):
        mock_compose.return_value = "ctx"
        mock_run_loop.side_effect = RuntimeError("API timeout")

        set_delegation_context("head_of_marketing", _make_registry())
        result = _run(
            consult_peer.handler(
                {
                    "role_id": "head_of_it",
                    "question": "Are systems OK?",
                }
            )
        )

        assert _is_error(result)
        assert "failed" in _text(result).lower()

        results = get_delegation_results()
        assert len(results) == 1
        assert results[0]["success"] is False
        assert results[0]["type"] == "consultation"
