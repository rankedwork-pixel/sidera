"""Tests for the Claude Code orchestrator (multi-step supervision)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.claude_code.orchestrator import (
    _MAX_ITERATIONS,
    OrchestrationResult,
    OrchestrationStep,
    Orchestrator,
)

# =============================================================================
# Helpers
# =============================================================================


def _make_dispatch_result(
    output: str = "Some analysis output.",
    cost: float = 0.05,
    error: str = "",
) -> dict:
    result = {
        "output": output,
        "cost_usd": cost,
        "duration_ms": 100,
    }
    if error:
        result["error"] = error
    return result


def _make_evaluation(
    verdict: str = "success",
    score: float = 0.9,
    reasoning: str = "Good output.",
) -> dict:
    return {
        "verdict": verdict,
        "score": score,
        "reasoning": reasoning,
        "missing": [],
        "refinement_hint": "",
        "_cost_usd": 0.005,
    }


def _make_decision(
    action: str = "done",
    reasoning: str = "Output is acceptable.",
    refined_prompt: str = "",
    delegate_to: str = "",
    delegate_prompt: str = "",
) -> dict:
    d: dict = {
        "action": action,
        "reasoning": reasoning,
        "_cost_usd": 0.005,
    }
    if refined_prompt:
        d["refined_prompt"] = refined_prompt
    if delegate_to:
        d["delegate_to"] = delegate_to
    if delegate_prompt:
        d["delegate_prompt"] = delegate_prompt
    return d


# =============================================================================
# OrchestrationResult tests
# =============================================================================


class TestOrchestrationResult:
    def test_to_dict(self):
        result = OrchestrationResult(
            objective="Test objective",
            final_output="Final output.",
            success=True,
            total_cost_usd=0.10,
            total_duration_ms=500,
            iterations=1,
            steps=[
                OrchestrationStep(
                    step_number=1,
                    role_id="test_role",
                    prompt="Test prompt",
                    output="Test output",
                    cost_usd=0.05,
                    evaluation={"verdict": "success"},
                    decision={"action": "done"},
                )
            ],
        )
        d = result.to_dict()
        assert d["objective"] == "Test objective"
        assert d["success"] is True
        assert d["iterations"] == 1
        assert len(d["steps"]) == 1
        assert d["steps"][0]["role_id"] == "test_role"

    def test_to_dict_truncates(self):
        result = OrchestrationResult(
            objective="Test",
            final_output="Final",
            success=True,
            steps=[
                OrchestrationStep(
                    step_number=1,
                    role_id="r",
                    prompt="p" * 300,
                    output="o" * 600,
                )
            ],
        )
        d = result.to_dict()
        assert len(d["steps"][0]["prompt_preview"]) == 200
        assert len(d["steps"][0]["output_preview"]) == 500


# =============================================================================
# Orchestrator.run tests
# =============================================================================


class TestOrchestratorRun:
    """Test the main orchestration loop."""

    @pytest.mark.asyncio
    async def test_single_iteration_success(self):
        """If first dispatch is evaluated as success, loop ends."""
        orchestrator = Orchestrator()

        with (
            patch.object(
                orchestrator,
                "_dispatch",
                new_callable=AsyncMock,
                return_value=_make_dispatch_result(),
            ),
            patch.object(
                orchestrator,
                "_evaluate",
                new_callable=AsyncMock,
                return_value=_make_evaluation(),
            ),
        ):
            result = await orchestrator.run(
                objective="Analyze Q1",
                primary_role_id="test_role",
            )

        assert result.success is True
        assert result.iterations == 1
        assert len(result.steps) == 1
        assert result.final_output == "Some analysis output."

    @pytest.mark.asyncio
    async def test_refine_on_insufficient(self):
        """Insufficient verdict triggers refine decision."""
        orchestrator = Orchestrator()

        dispatch_results = [
            _make_dispatch_result("Partial output"),
            _make_dispatch_result("Complete output with ROAS"),
        ]
        eval_results = [
            _make_evaluation("insufficient", 0.4, "Missing ROAS"),
            _make_evaluation("success", 0.9, "Good"),
        ]
        decision_results = [
            _make_decision(
                "refine",
                "Need ROAS data",
                refined_prompt="Include ROAS analysis",
            ),
        ]

        with (
            patch.object(
                orchestrator,
                "_dispatch",
                new_callable=AsyncMock,
                side_effect=dispatch_results,
            ),
            patch.object(
                orchestrator,
                "_evaluate",
                new_callable=AsyncMock,
                side_effect=eval_results,
            ),
            patch.object(
                orchestrator,
                "_decide",
                new_callable=AsyncMock,
                side_effect=decision_results,
            ),
        ):
            result = await orchestrator.run(
                objective="Analyze Q1",
                primary_role_id="test_role",
                success_criteria="Must include ROAS",
            )

        assert result.success is True
        assert result.iterations == 2
        assert len(result.steps) == 2

    @pytest.mark.asyncio
    async def test_delegate_to_different_role(self):
        """Delegate action switches to a different role."""
        orchestrator = Orchestrator()

        dispatch_results = [
            _make_dispatch_result("Marketing view"),
            _make_dispatch_result("IT diagnostics complete"),
        ]
        eval_results = [
            _make_evaluation("off_topic", 0.2, "Wrong domain"),
            _make_evaluation("success", 0.85, "Good"),
        ]
        decision_results = [
            _make_decision(
                "delegate",
                "Need IT role",
                delegate_to="head_of_it",
                delegate_prompt="Run system diagnostics",
            ),
        ]

        with (
            patch.object(
                orchestrator,
                "_dispatch",
                new_callable=AsyncMock,
                side_effect=dispatch_results,
            ),
            patch.object(
                orchestrator,
                "_evaluate",
                new_callable=AsyncMock,
                side_effect=eval_results,
            ),
            patch.object(
                orchestrator,
                "_decide",
                new_callable=AsyncMock,
                side_effect=decision_results,
            ),
        ):
            result = await orchestrator.run(
                objective="Check system health",
                primary_role_id="head_of_marketing",
            )

        assert result.success is True
        assert result.iterations == 2
        assert result.steps[1].role_id == "head_of_it"

    @pytest.mark.asyncio
    async def test_abort_decision(self):
        """Abort action stops the loop."""
        orchestrator = Orchestrator()

        with (
            patch.object(
                orchestrator,
                "_dispatch",
                new_callable=AsyncMock,
                return_value=_make_dispatch_result("Bad output"),
            ),
            patch.object(
                orchestrator,
                "_evaluate",
                new_callable=AsyncMock,
                return_value=_make_evaluation(
                    "error",
                    0.1,
                    "Cannot proceed",
                ),
            ),
            patch.object(
                orchestrator,
                "_decide",
                new_callable=AsyncMock,
                return_value=_make_decision(
                    "abort",
                    "Objective not achievable",
                ),
            ),
        ):
            result = await orchestrator.run(
                objective="Impossible task",
                primary_role_id="test_role",
            )

        assert result.success is False
        assert result.abort_reason == "Objective not achievable"
        assert result.iterations == 1

    @pytest.mark.asyncio
    async def test_max_iterations_cap(self):
        """Loop stops at max_iterations even if not successful."""
        orchestrator = Orchestrator()

        with (
            patch.object(
                orchestrator,
                "_dispatch",
                new_callable=AsyncMock,
                return_value=_make_dispatch_result("Partial"),
            ),
            patch.object(
                orchestrator,
                "_evaluate",
                new_callable=AsyncMock,
                return_value=_make_evaluation(
                    "insufficient",
                    0.4,
                    "Still missing",
                ),
            ),
            patch.object(
                orchestrator,
                "_decide",
                new_callable=AsyncMock,
                return_value=_make_decision(
                    "refine",
                    "Try again",
                    refined_prompt="Better prompt",
                ),
            ),
        ):
            result = await orchestrator.run(
                objective="Hard task",
                primary_role_id="test_role",
                max_iterations=2,
            )

        assert result.iterations == 2
        assert result.final_output == "Partial"
        # Score 0.4 < 0.6, so not success
        assert result.success is False

    @pytest.mark.asyncio
    async def test_hard_iteration_cap(self):
        """max_iterations is capped at _MAX_ITERATIONS (5)."""
        orchestrator = Orchestrator()

        call_count = 0

        async def counting_dispatch(**kwargs):
            nonlocal call_count
            call_count += 1
            return _make_dispatch_result(f"Attempt {call_count}")

        with (
            patch.object(
                orchestrator,
                "_dispatch",
                new_callable=AsyncMock,
                side_effect=counting_dispatch,
            ),
            patch.object(
                orchestrator,
                "_evaluate",
                new_callable=AsyncMock,
                return_value=_make_evaluation(
                    "insufficient",
                    0.3,
                    "Keep trying",
                ),
            ),
            patch.object(
                orchestrator,
                "_decide",
                new_callable=AsyncMock,
                return_value=_make_decision(
                    "refine",
                    "Try again",
                    refined_prompt="Better",
                ),
            ),
        ):
            result = await orchestrator.run(
                objective="Hard task",
                primary_role_id="test_role",
                max_iterations=100,  # Should be capped to 5
            )

        assert result.iterations == _MAX_ITERATIONS
        assert call_count == _MAX_ITERATIONS

    @pytest.mark.asyncio
    async def test_budget_exhaustion_stops_loop(self):
        """When budget is exhausted, loop stops and accepts output."""
        orchestrator = Orchestrator()

        with (
            patch.object(
                orchestrator,
                "_dispatch",
                new_callable=AsyncMock,
                return_value=_make_dispatch_result(
                    "Expensive output",
                    cost=5.0,
                ),
            ),
        ):
            result = await orchestrator.run(
                objective="Budget test",
                primary_role_id="test_role",
                max_cost_usd=5.0,
            )

        assert result.success is True
        assert result.iterations == 1
        assert result.total_cost_usd >= 5.0

    @pytest.mark.asyncio
    async def test_dispatch_error_retries(self):
        """Dispatch error triggers retry on next iteration."""
        orchestrator = Orchestrator()

        dispatch_results = [
            _make_dispatch_result(error="Connection failed"),
            _make_dispatch_result("Success after retry"),
        ]

        with (
            patch.object(
                orchestrator,
                "_dispatch",
                new_callable=AsyncMock,
                side_effect=dispatch_results,
            ),
            patch.object(
                orchestrator,
                "_evaluate",
                new_callable=AsyncMock,
                return_value=_make_evaluation(),
            ),
        ):
            result = await orchestrator.run(
                objective="Retry test",
                primary_role_id="test_role",
            )

        assert result.success is True
        assert result.iterations == 2

    @pytest.mark.asyncio
    async def test_dispatch_error_on_last_iteration(self):
        """Dispatch error on final iteration aborts."""
        orchestrator = Orchestrator()

        with patch.object(
            orchestrator,
            "_dispatch",
            new_callable=AsyncMock,
            return_value=_make_dispatch_result(error="Fatal error"),
        ):
            result = await orchestrator.run(
                objective="Error test",
                primary_role_id="test_role",
                max_iterations=1,
            )

        assert result.success is False
        assert "Fatal error" in result.abort_reason

    @pytest.mark.asyncio
    async def test_default_success_criteria(self):
        """When no criteria provided, uses default."""
        orchestrator = Orchestrator()

        with (
            patch.object(
                orchestrator,
                "_dispatch",
                new_callable=AsyncMock,
                return_value=_make_dispatch_result(),
            ),
            patch.object(
                orchestrator,
                "_evaluate",
                new_callable=AsyncMock,
                return_value=_make_evaluation(),
            ) as mock_eval,
        ):
            await orchestrator.run(
                objective="Test",
                primary_role_id="test_role",
            )

        # Verify default criteria was passed
        call_kwargs = mock_eval.call_args
        criteria = call_kwargs.kwargs.get(
            "criteria",
            call_kwargs[1].get("criteria", ""),
        )
        assert "actionable" in criteria.lower()

    @pytest.mark.asyncio
    async def test_cost_tracking_across_iterations(self):
        """Cost is accumulated across all steps."""
        orchestrator = Orchestrator()

        dispatch_results = [
            _make_dispatch_result("First", cost=0.10),
            _make_dispatch_result("Second", cost=0.15),
        ]
        eval_results = [
            _make_evaluation("insufficient", 0.4, "Incomplete"),
            _make_evaluation("success", 0.9, "Good"),
        ]
        decision_results = [
            _make_decision(
                "refine",
                "Need more",
                refined_prompt="More detail",
            ),
        ]

        with (
            patch.object(
                orchestrator,
                "_dispatch",
                new_callable=AsyncMock,
                side_effect=dispatch_results,
            ),
            patch.object(
                orchestrator,
                "_evaluate",
                new_callable=AsyncMock,
                side_effect=eval_results,
            ),
            patch.object(
                orchestrator,
                "_decide",
                new_callable=AsyncMock,
                side_effect=decision_results,
            ),
        ):
            result = await orchestrator.run(
                objective="Cost tracking test",
                primary_role_id="test_role",
            )

        # 0.10 + eval(0.005) + decide(0.005) + 0.15 + eval(0.005)
        assert result.total_cost_usd == pytest.approx(
            0.265,
            abs=0.01,
        )


# =============================================================================
# Orchestrator._evaluate tests
# =============================================================================


class TestOrchestratorEvaluate:
    """Test the output evaluation method."""

    @pytest.mark.asyncio
    async def test_successful_evaluation(self):
        orchestrator = Orchestrator()

        mock_response = {
            "text": '{"verdict": "success", "score": 0.9, "reasoning": "Good", "missing": []}',
            "cost": {"total_cost_usd": 0.005},
        }

        with patch(
            "src.agent.api_client.call_claude_api",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await orchestrator._evaluate(
                output="Test output",
                objective="Test objective",
                criteria="Must be good",
            )

        assert result["verdict"] == "success"
        assert result["score"] == 0.9
        assert result["_cost_usd"] == 0.005

    @pytest.mark.asyncio
    async def test_evaluation_error_defaults_to_success(self):
        """On error, evaluation defaults to accepting the output."""
        orchestrator = Orchestrator()

        with patch(
            "src.agent.api_client.call_claude_api",
            new_callable=AsyncMock,
            side_effect=Exception("API down"),
        ):
            result = await orchestrator._evaluate(
                output="Test output",
                objective="Test",
                criteria="Test",
            )

        assert result["verdict"] == "success"
        assert result["score"] == 0.5

    @pytest.mark.asyncio
    async def test_evaluation_truncates_long_output(self):
        """Very long outputs are truncated before sending to eval."""
        orchestrator = Orchestrator()

        mock_response = {
            "text": '{"verdict": "success", "score": 0.8}',
            "cost": {"total_cost_usd": 0.005},
        }

        with patch(
            "src.agent.api_client.call_claude_api",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_call:
            await orchestrator._evaluate(
                output="x" * 20000,
                objective="Test",
                criteria="Test",
            )

        # Verify the output was truncated in the prompt
        call_args = mock_call.call_args
        user_msg = call_args.kwargs.get(
            "user_message",
            call_args[1].get("user_message", ""),
        )
        # 8000 chars of output + template text
        assert len(user_msg) < 15000


# =============================================================================
# Orchestrator._decide tests
# =============================================================================


class TestOrchestratorDecide:
    """Test the next-step decision method."""

    @pytest.mark.asyncio
    async def test_decide_refine(self):
        orchestrator = Orchestrator()

        mock_response = {
            "text": '{"action": "refine", "reasoning": "Need more detail", '
            '"refined_prompt": "Add specifics"}',
            "cost": {"total_cost_usd": 0.005},
        }

        with patch(
            "src.agent.api_client.call_claude_api",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await orchestrator._decide(
                objective="Test",
                iteration=1,
                max_iterations=3,
                cost_so_far=0.10,
                max_cost=5.0,
                evaluation={"verdict": "insufficient", "score": 0.4},
                steps=[],
            )

        assert result["action"] == "refine"
        assert result["refined_prompt"] == "Add specifics"

    @pytest.mark.asyncio
    async def test_decide_error_defaults_to_done(self):
        """On error, decision defaults to accepting output."""
        orchestrator = Orchestrator()

        with patch(
            "src.agent.api_client.call_claude_api",
            new_callable=AsyncMock,
            side_effect=Exception("API failure"),
        ):
            result = await orchestrator._decide(
                objective="Test",
                iteration=1,
                max_iterations=3,
                cost_so_far=0.10,
                max_cost=5.0,
                evaluation={"verdict": "insufficient"},
                steps=[],
            )

        assert result["action"] == "done"


# =============================================================================
# Orchestrator._parse_json tests
# =============================================================================


class TestParseJson:
    def test_raw_json(self):
        result = Orchestrator._parse_json('{"verdict": "success"}')
        assert result["verdict"] == "success"

    def test_markdown_fenced_json(self):
        text = '```json\n{"verdict": "success"}\n```'
        result = Orchestrator._parse_json(text)
        assert result["verdict"] == "success"

    def test_json_in_text(self):
        text = 'Here is my analysis: {"verdict": "success"} end.'
        result = Orchestrator._parse_json(text)
        assert result["verdict"] == "success"

    def test_unparseable_returns_error(self):
        result = Orchestrator._parse_json("not json at all")
        assert result["verdict"] == "error"

    def test_markdown_fence_without_json_label(self):
        text = '```\n{"action": "done"}\n```'
        result = Orchestrator._parse_json(text)
        assert result["action"] == "done"


# =============================================================================
# Meta-tool handler tests
# =============================================================================


class TestOrchestrateMetaTool:
    """Test the MCP meta-tool handler for orchestration."""

    @pytest.mark.asyncio
    async def test_missing_objective(self):
        from src.mcp_stdio.meta_tools import META_TOOL_HANDLERS

        handler = META_TOOL_HANDLERS.get("orchestrate")
        assert handler is not None

        result = await handler({})
        assert len(result) == 1
        assert "required" in result[0].text.lower()

    @pytest.mark.asyncio
    async def test_successful_orchestration(self):
        from src.mcp_stdio.meta_tools import META_TOOL_HANDLERS

        handler = META_TOOL_HANDLERS.get("orchestrate")

        mock_result = OrchestrationResult(
            objective="Test",
            final_output="Analysis complete.",
            success=True,
            total_cost_usd=0.15,
            total_duration_ms=500,
            iterations=1,
            steps=[
                OrchestrationStep(
                    step_number=1,
                    role_id="test_role",
                    prompt="Test",
                    output="Analysis complete.",
                    evaluation={"verdict": "success", "score": 0.9},
                    decision={"action": "done"},
                )
            ],
        )

        with patch(
            "src.claude_code.orchestrator.Orchestrator",
        ) as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.run.return_value = mock_result
            mock_cls.return_value = mock_instance

            result = await handler(
                {"objective": "Test", "primary_role_id": "test_role"},
            )

        assert len(result) == 1
        assert "succeeded" in result[0].text
        assert "Analysis complete." in result[0].text

    @pytest.mark.asyncio
    async def test_failed_orchestration(self):
        from src.mcp_stdio.meta_tools import META_TOOL_HANDLERS

        handler = META_TOOL_HANDLERS.get("orchestrate")

        mock_result = OrchestrationResult(
            objective="Test",
            final_output="Partial result.",
            success=False,
            abort_reason="Could not achieve objective.",
            iterations=3,
        )

        with patch(
            "src.claude_code.orchestrator.Orchestrator",
        ) as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.run.return_value = mock_result
            mock_cls.return_value = mock_instance

            result = await handler(
                {"objective": "Hard task"},
            )

        assert len(result) == 1
        assert "not fully satisfied" in result[0].text

    @pytest.mark.asyncio
    async def test_orchestration_error(self):
        from src.mcp_stdio.meta_tools import META_TOOL_HANDLERS

        handler = META_TOOL_HANDLERS.get("orchestrate")

        with patch(
            "src.claude_code.orchestrator.Orchestrator",
        ) as mock_cls:
            mock_cls.side_effect = Exception("Import failed")

            result = await handler(
                {"objective": "Test"},
            )

        assert len(result) == 1
        assert "error" in result[0].text.lower()

    @pytest.mark.asyncio
    async def test_multi_step_history_shown(self):
        from src.mcp_stdio.meta_tools import META_TOOL_HANDLERS

        handler = META_TOOL_HANDLERS.get("orchestrate")

        mock_result = OrchestrationResult(
            objective="Test",
            final_output="Final.",
            success=True,
            iterations=2,
            steps=[
                OrchestrationStep(
                    step_number=1,
                    role_id="role_a",
                    prompt="First",
                    output="Partial",
                    evaluation={
                        "verdict": "insufficient",
                        "score": 0.4,
                    },
                    decision={"action": "refine"},
                ),
                OrchestrationStep(
                    step_number=2,
                    role_id="role_a",
                    prompt="Refined",
                    output="Final.",
                    evaluation={
                        "verdict": "success",
                        "score": 0.9,
                    },
                    decision={"action": "done"},
                ),
            ],
        )

        with patch(
            "src.claude_code.orchestrator.Orchestrator",
        ) as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.run.return_value = mock_result
            mock_cls.return_value = mock_instance

            result = await handler({"objective": "Test"})

        text = result[0].text
        assert "Step History" in text
        assert "Step 1" in text
        assert "Step 2" in text
