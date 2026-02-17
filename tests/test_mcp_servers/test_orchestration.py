"""Tests for src.mcp_servers.orchestration -- orchestrate_task tool.

Covers:
- orchestrate_task without objective → error
- orchestrate_task without role_id → error
- orchestrate_task at depth >= 1 → recursion blocked
- parameter capping (max_iterations, max_cost_usd)
- successful orchestration returns formatted result
- orchestration error returns error_response
- depth is restored after completion (success and error)
- depth is restored after exception in Orchestrator
- context set/clear lifecycle
- default parameter values
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

from src.mcp_servers.orchestration import (
    _DEFAULT_BUDGET,
    _DEFAULT_ITERATIONS,
    _MAX_ORCHESTRATION_BUDGET,
    _MAX_ORCHESTRATION_ITERATIONS,
    _orchestration_depth_var,
    clear_orchestration_context,
    orchestrate_task,
    set_orchestration_context,
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
# Fake OrchestrationResult for mocking
# ---------------------------------------------------------------------------


@dataclass
class FakeOrchestrationStep:
    step_number: int = 1
    role_id: str = "test_role"
    prompt: str = "test prompt"
    output: str = "test output"
    cost_usd: float = 0.05
    duration_ms: int = 1000
    evaluation: dict = field(default_factory=lambda: {"verdict": "success", "score": 0.9})
    decision: dict = field(default_factory=lambda: {"action": "done"})


@dataclass
class FakeOrchestrationResult:
    objective: str = "test objective"
    final_output: str = "Here is the analysis result."
    success: bool = True
    total_cost_usd: float = 0.15
    total_duration_ms: int = 3000
    iterations: int = 2
    steps: list = field(default_factory=lambda: [FakeOrchestrationStep()])
    abort_reason: str = ""


# ---------------------------------------------------------------------------
# Tests: Validation
# ---------------------------------------------------------------------------


class TestOrchestrationValidation:
    """Test input validation for orchestrate_task."""

    def test_missing_objective(self):
        result = _run(orchestrate_task({"role_id": "test_role"}))
        assert _is_error(result)
        assert "objective" in _text(result).lower()

    def test_empty_objective(self):
        result = _run(orchestrate_task({"objective": "", "role_id": "test_role"}))
        assert _is_error(result)
        assert "objective" in _text(result).lower()

    def test_missing_role_id(self):
        result = _run(orchestrate_task({"objective": "test"}))
        assert _is_error(result)
        assert "role_id" in _text(result).lower()

    def test_empty_role_id(self):
        result = _run(orchestrate_task({"objective": "test", "role_id": ""}))
        assert _is_error(result)
        assert "role_id" in _text(result).lower()


# ---------------------------------------------------------------------------
# Tests: Recursion Guard
# ---------------------------------------------------------------------------


class TestOrchestrationRecursion:
    """Test that nested orchestrations are blocked."""

    def test_blocked_at_depth_1(self):
        """orchestrate_task should fail when depth >= 1."""
        _orchestration_depth_var.set(1)
        try:
            result = _run(
                orchestrate_task(
                    {
                        "objective": "test task",
                        "role_id": "test_role",
                    }
                )
            )
            assert _is_error(result)
            assert "nest" in _text(result).lower() or "recursion" in _text(result).lower()
        finally:
            _orchestration_depth_var.set(0)

    def test_blocked_at_depth_2(self):
        """orchestrate_task should fail at any depth > 0."""
        _orchestration_depth_var.set(2)
        try:
            result = _run(
                orchestrate_task(
                    {
                        "objective": "test task",
                        "role_id": "test_role",
                    }
                )
            )
            assert _is_error(result)
        finally:
            _orchestration_depth_var.set(0)


# ---------------------------------------------------------------------------
# Tests: Parameter Capping
# ---------------------------------------------------------------------------


class TestOrchestrationParameters:
    """Test that parameters are capped at safe limits."""

    @patch("src.claude_code.orchestrator.Orchestrator")
    def test_iterations_capped(self, mock_orch_cls):
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock(return_value=FakeOrchestrationResult())
        mock_orch_cls.return_value = mock_orch

        _run(
            orchestrate_task(
                {
                    "objective": "test",
                    "role_id": "test_role",
                    "max_iterations": 100,  # Way over limit
                }
            )
        )

        call_kwargs = mock_orch.run.call_args[1]
        assert call_kwargs["max_iterations"] == _MAX_ORCHESTRATION_ITERATIONS

    @patch("src.claude_code.orchestrator.Orchestrator")
    def test_budget_capped(self, mock_orch_cls):
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock(return_value=FakeOrchestrationResult())
        mock_orch_cls.return_value = mock_orch

        _run(
            orchestrate_task(
                {
                    "objective": "test",
                    "role_id": "test_role",
                    "max_cost_usd": 999.0,  # Way over limit
                }
            )
        )

        call_kwargs = mock_orch.run.call_args[1]
        assert call_kwargs["max_cost_usd"] == _MAX_ORCHESTRATION_BUDGET

    @patch("src.claude_code.orchestrator.Orchestrator")
    def test_default_parameters(self, mock_orch_cls):
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock(return_value=FakeOrchestrationResult())
        mock_orch_cls.return_value = mock_orch

        _run(
            orchestrate_task(
                {
                    "objective": "test",
                    "role_id": "test_role",
                }
            )
        )

        call_kwargs = mock_orch.run.call_args[1]
        assert call_kwargs["max_iterations"] == _DEFAULT_ITERATIONS
        assert call_kwargs["max_cost_usd"] == _DEFAULT_BUDGET

    @patch("src.claude_code.orchestrator.Orchestrator")
    def test_invalid_iterations_type_uses_default(self, mock_orch_cls):
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock(return_value=FakeOrchestrationResult())
        mock_orch_cls.return_value = mock_orch

        _run(
            orchestrate_task(
                {
                    "objective": "test",
                    "role_id": "test_role",
                    "max_iterations": "not_a_number",
                }
            )
        )

        call_kwargs = mock_orch.run.call_args[1]
        assert call_kwargs["max_iterations"] == _DEFAULT_ITERATIONS

    @patch("src.claude_code.orchestrator.Orchestrator")
    def test_invalid_budget_type_uses_default(self, mock_orch_cls):
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock(return_value=FakeOrchestrationResult())
        mock_orch_cls.return_value = mock_orch

        _run(
            orchestrate_task(
                {
                    "objective": "test",
                    "role_id": "test_role",
                    "max_cost_usd": "not_a_number",
                }
            )
        )

        call_kwargs = mock_orch.run.call_args[1]
        assert call_kwargs["max_cost_usd"] == _DEFAULT_BUDGET


# ---------------------------------------------------------------------------
# Tests: Successful Orchestration
# ---------------------------------------------------------------------------


class TestOrchestrationSuccess:
    """Test successful orchestration calls."""

    @patch("src.claude_code.orchestrator.Orchestrator")
    def test_success_returns_formatted_result(self, mock_orch_cls):
        fake_result = FakeOrchestrationResult(
            success=True,
            final_output="CPA spiked because of competitor entry.",
            total_cost_usd=0.25,
            iterations=2,
        )
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock(return_value=fake_result)
        mock_orch_cls.return_value = mock_orch

        result = _run(
            orchestrate_task(
                {
                    "objective": "investigate CPA spike",
                    "role_id": "performance_media_buyer",
                    "success_criteria": "root cause with data",
                }
            )
        )

        assert not _is_error(result)
        text = _text(result)
        assert "completed successfully" in text
        assert "CPA spiked because of competitor entry" in text
        assert "$0.25" in text

    @patch("src.claude_code.orchestrator.Orchestrator")
    def test_aborted_orchestration(self, mock_orch_cls):
        fake_result = FakeOrchestrationResult(
            success=False,
            final_output="Could not determine root cause.",
            abort_reason="Role not found.",
            total_cost_usd=0.05,
        )
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock(return_value=fake_result)
        mock_orch_cls.return_value = mock_orch

        result = _run(
            orchestrate_task(
                {
                    "objective": "test",
                    "role_id": "nonexistent_role",
                }
            )
        )

        assert not _is_error(result)
        text = _text(result)
        assert "aborted" in text
        assert "Role not found" in text

    @patch("src.claude_code.orchestrator.Orchestrator")
    def test_limited_success(self, mock_orch_cls):
        fake_result = FakeOrchestrationResult(
            success=False,
            final_output="Partial analysis done.",
            total_cost_usd=0.10,
            abort_reason="",
        )
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock(return_value=fake_result)
        mock_orch_cls.return_value = mock_orch

        result = _run(
            orchestrate_task(
                {
                    "objective": "test",
                    "role_id": "test_role",
                }
            )
        )

        assert not _is_error(result)
        text = _text(result)
        assert "completed with limitations" in text

    @patch("src.claude_code.orchestrator.Orchestrator")
    def test_step_summaries_included(self, mock_orch_cls):
        steps = [
            FakeOrchestrationStep(
                step_number=1,
                role_id="media_buyer",
                evaluation={"verdict": "insufficient", "score": 0.4},
            ),
            FakeOrchestrationStep(
                step_number=2,
                role_id="media_buyer",
                evaluation={"verdict": "success", "score": 0.9},
            ),
        ]
        fake_result = FakeOrchestrationResult(
            success=True,
            final_output="Done.",
            steps=steps,
            iterations=2,
        )
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock(return_value=fake_result)
        mock_orch_cls.return_value = mock_orch

        result = _run(
            orchestrate_task(
                {
                    "objective": "test",
                    "role_id": "media_buyer",
                }
            )
        )

        text = _text(result)
        assert "Step 1" in text
        assert "Step 2" in text
        assert "insufficient" in text
        assert "success" in text

    @patch("src.claude_code.orchestrator.Orchestrator")
    def test_orchestrator_called_with_correct_args(self, mock_orch_cls):
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock(return_value=FakeOrchestrationResult())
        mock_orch_cls.return_value = mock_orch

        _run(
            orchestrate_task(
                {
                    "objective": "investigate CPA spike",
                    "role_id": "performance_media_buyer",
                    "success_criteria": "root cause identified",
                    "max_iterations": 4,
                    "max_cost_usd": 3.0,
                }
            )
        )

        mock_orch.run.assert_called_once_with(
            objective="investigate CPA spike",
            primary_role_id="performance_media_buyer",
            success_criteria="root cause identified",
            max_iterations=4,
            max_cost_usd=3.0,
            user_id="__orchestration__",
        )


# ---------------------------------------------------------------------------
# Tests: Error Handling
# ---------------------------------------------------------------------------


class TestOrchestrationErrors:
    """Test error handling in orchestrate_task."""

    @patch("src.claude_code.orchestrator.Orchestrator")
    def test_orchestrator_exception_returns_error(self, mock_orch_cls):
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock(side_effect=RuntimeError("Connection failed"))
        mock_orch_cls.return_value = mock_orch

        result = _run(
            orchestrate_task(
                {
                    "objective": "test",
                    "role_id": "test_role",
                }
            )
        )

        assert _is_error(result)
        assert "Connection failed" in _text(result)


# ---------------------------------------------------------------------------
# Tests: Depth Management
# ---------------------------------------------------------------------------


class TestOrchestrationDepth:
    """Test that depth is correctly managed across all paths."""

    @patch("src.claude_code.orchestrator.Orchestrator")
    def test_depth_restored_after_success(self, mock_orch_cls):
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock(return_value=FakeOrchestrationResult())
        mock_orch_cls.return_value = mock_orch

        assert _orchestration_depth_var.get(0) == 0

        _run(
            orchestrate_task(
                {
                    "objective": "test",
                    "role_id": "test_role",
                }
            )
        )

        assert _orchestration_depth_var.get(0) == 0

    @patch("src.claude_code.orchestrator.Orchestrator")
    def test_depth_restored_after_error(self, mock_orch_cls):
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock(side_effect=RuntimeError("boom"))
        mock_orch_cls.return_value = mock_orch

        assert _orchestration_depth_var.get(0) == 0

        result = _run(
            orchestrate_task(
                {
                    "objective": "test",
                    "role_id": "test_role",
                }
            )
        )

        # Should be error response
        assert _is_error(result)
        # But depth must be restored
        assert _orchestration_depth_var.get(0) == 0

    @patch("src.claude_code.orchestrator.Orchestrator")
    def test_depth_incremented_during_execution(self, mock_orch_cls):
        """Verify depth is 1 while orchestrator.run() executes."""
        captured_depth = None

        async def capture_depth(**kwargs):
            nonlocal captured_depth
            captured_depth = _orchestration_depth_var.get(0)
            return FakeOrchestrationResult()

        mock_orch = MagicMock()
        mock_orch.run = AsyncMock(side_effect=capture_depth)
        mock_orch_cls.return_value = mock_orch

        _run(
            orchestrate_task(
                {
                    "objective": "test",
                    "role_id": "test_role",
                }
            )
        )

        assert captured_depth == 1

    def test_clear_orchestration_context_resets_depth(self):
        _orchestration_depth_var.set(3)
        clear_orchestration_context()
        assert _orchestration_depth_var.get(0) == 0


# ---------------------------------------------------------------------------
# Tests: Context Lifecycle
# ---------------------------------------------------------------------------


class TestOrchestrationContext:
    """Test context management helpers."""

    def test_set_orchestration_context(self):
        """set_orchestration_context should not raise."""
        set_orchestration_context()  # No-op currently, but should not error

    def test_clear_orchestration_context(self):
        """clear_orchestration_context should reset depth."""
        _orchestration_depth_var.set(5)
        clear_orchestration_context()
        assert _orchestration_depth_var.get(0) == 0


# ---------------------------------------------------------------------------
# Tests: Delegation Tool Exclusion
# ---------------------------------------------------------------------------


class TestDelegationExclusion:
    """Verify orchestrate_task is excluded from delegation sub-agent tools."""

    def test_orchestrate_task_in_delegation_no_recurse(self):
        """delegate_to_role should exclude orchestrate_task from sub-agent tools."""
        # This is a structural test — verifying the code pattern in delegation.py
        import inspect

        from src.mcp_servers import delegation

        source = inspect.getsource(delegation.delegate_to_role)
        assert "orchestrate_task" in source, (
            "orchestrate_task should be in the _no_recurse set in delegate_to_role"
        )

    def test_orchestrate_task_in_consult_peer_no_recurse(self):
        """consult_peer should exclude orchestrate_task from peer tools."""
        import inspect

        from src.mcp_servers import delegation

        source = inspect.getsource(delegation.consult_peer)
        assert "orchestrate_task" in source, (
            "orchestrate_task should be in the _no_recurse set in consult_peer"
        )
