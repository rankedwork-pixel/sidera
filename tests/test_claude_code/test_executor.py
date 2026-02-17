"""Tests for the Claude Code executor.

Tests ``ClaudeCodeResult``, ``ClaudeCodeExecutor.execute``, model resolution,
tool resolution, and the private helper methods
(``_compose_system_prompt``, ``_render_prompt``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.claude_code.executor import ClaudeCodeExecutor, ClaudeCodeResult, _resolve_model

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill(
    skill_id: str = "test_skill",
    name: str = "Test Skill",
    model: str = "sonnet",
    max_turns: int = 5,
    system_supplement: str = "You are a helpful assistant.",
    prompt_template: str = "Analyze the data.",
    output_format: str = "",
    business_guidance: str = "",
    context_files: tuple = (),
    department_id: str = "test_dept",
    tools_required: tuple = (),
) -> MagicMock:
    """Create a mock SkillDefinition."""
    skill = MagicMock()
    skill.id = skill_id
    skill.name = name
    skill.model = model
    skill.max_turns = max_turns
    skill.system_supplement = system_supplement
    skill.prompt_template = prompt_template
    skill.output_format = output_format
    skill.business_guidance = business_guidance
    skill.context_files = context_files
    skill.department_id = department_id
    skill.tools_required = tools_required
    return skill


@dataclass
class FakeTurnResult:
    """Stand-in for ``TurnResult`` from ``src.agent.api_client``."""

    text: str = "Analysis complete."
    cost: dict[str, Any] = field(
        default_factory=lambda: {
            "total_cost_usd": 0.05,
            "input_tokens": 1000,
            "output_tokens": 200,
            "duration_ms": 5000,
        }
    )
    turn_count: int = 3
    session_id: str = "sess_123"
    is_error: bool = False


# ---------------------------------------------------------------------------
# ClaudeCodeResult tests
# ---------------------------------------------------------------------------


class TestClaudeCodeResult:
    """Tests for ClaudeCodeResult dataclass."""

    def test_basic_construction(self):
        result = ClaudeCodeResult(
            skill_id="test_skill",
            user_id="user1",
            output_text="Hello world",
        )
        assert result.skill_id == "test_skill"
        assert result.user_id == "user1"
        assert result.output_text == "Hello world"
        assert result.structured_output is None
        assert result.cost_usd == 0.0
        assert result.num_turns == 0
        assert result.duration_ms == 0
        assert result.is_error is False
        assert result.error_message == ""

    def test_error_result(self):
        result = ClaudeCodeResult(
            skill_id="test_skill",
            user_id="user1",
            output_text="",
            is_error=True,
            error_message="SDK crash",
        )
        assert result.is_error is True
        assert result.error_message == "SDK crash"

    def test_full_construction(self):
        result = ClaudeCodeResult(
            skill_id="campaign_analysis",
            user_id="claude_code",
            output_text="Analysis done",
            structured_output={"score": 85},
            cost_usd=0.12,
            num_turns=5,
            duration_ms=10000,
            session_id="sess_abc",
            usage={"input_tokens": 500},
        )
        assert result.structured_output == {"score": 85}
        assert result.cost_usd == 0.12
        assert result.num_turns == 5
        assert result.duration_ms == 10000
        assert result.session_id == "sess_abc"
        assert result.usage == {"input_tokens": 500}


# ---------------------------------------------------------------------------
# _compose_system_prompt tests
# ---------------------------------------------------------------------------


class TestComposeSystemPrompt:
    """Tests for the system prompt composition."""

    def test_all_sections_included(self):
        executor = ClaudeCodeExecutor(project_dir="/tmp/test")
        skill = _make_skill(
            system_supplement="Supplement here.",
            business_guidance="Focus on ROAS.",
            output_format="Return JSON.",
        )

        with patch(
            "src.claude_code.executor.load_context_text",
            return_value="Context file content",
        ):
            prompt = executor._compose_system_prompt(
                skill,
                role_context="You are the media buyer.",
                memory_context="Previous insight: ROAS improved.",
            )

        assert "You are the media buyer." in prompt
        assert "Previous insight: ROAS improved." in prompt
        assert "Supplement here." in prompt
        assert "Context file content" in prompt
        assert "Focus on ROAS." in prompt
        assert "Return JSON." in prompt

    def test_section_order(self):
        """Sections should follow the documented order."""
        executor = ClaudeCodeExecutor(project_dir="/tmp/test")
        skill = _make_skill(
            system_supplement="SUPPLEMENT",
            business_guidance="GUIDANCE",
            output_format="FORMAT",
        )

        with patch(
            "src.claude_code.executor.load_context_text",
            return_value="CONTEXT_FILES",
        ):
            prompt = executor._compose_system_prompt(
                skill,
                role_context="ROLE_CTX",
                memory_context="MEMORY_CTX",
            )

        # Verify order: role → memory → supplement → context files → guidance → format
        role_pos = prompt.index("ROLE_CTX")
        memory_pos = prompt.index("MEMORY_CTX")
        supplement_pos = prompt.index("SUPPLEMENT")
        context_pos = prompt.index("CONTEXT_FILES")
        guidance_pos = prompt.index("GUIDANCE")
        format_pos = prompt.index("FORMAT")

        assert role_pos < memory_pos < supplement_pos < context_pos < guidance_pos < format_pos

    def test_empty_sections_gets_base_prompt(self):
        """When no role_context is provided, BASE_SYSTEM_PROMPT is included."""
        executor = ClaudeCodeExecutor(project_dir="/tmp/test")
        skill = _make_skill(
            system_supplement="",
            business_guidance="",
            output_format="",
        )

        with patch(
            "src.claude_code.executor.load_context_text",
            return_value="",
        ):
            prompt = executor._compose_system_prompt(skill, "", "")

        # Should contain the base prompt (not empty)
        assert len(prompt) > 0
        assert "Sidera" in prompt


# ---------------------------------------------------------------------------
# _render_prompt tests
# ---------------------------------------------------------------------------


class TestRenderPrompt:
    """Tests for prompt rendering logic."""

    def test_explicit_prompt_overrides_template(self):
        executor = ClaudeCodeExecutor(project_dir="/tmp/test")
        skill = _make_skill(prompt_template="Default template.")
        result = executor._render_prompt(skill, "Custom prompt.", None)
        assert result == "Custom prompt."

    def test_template_used_when_prompt_empty(self):
        executor = ClaudeCodeExecutor(project_dir="/tmp/test")
        skill = _make_skill(prompt_template="Analyze {period} data.")
        result = executor._render_prompt(skill, "", {"period": "Q1"})
        assert result == "Analyze Q1 data."

    def test_template_with_no_params(self):
        executor = ClaudeCodeExecutor(project_dir="/tmp/test")
        skill = _make_skill(prompt_template="Run health check.")
        result = executor._render_prompt(skill, "", None)
        assert result == "Run health check."

    def test_template_with_missing_params_returns_raw(self):
        executor = ClaudeCodeExecutor(project_dir="/tmp/test")
        skill = _make_skill(prompt_template="Analyze {metric} for {period}.")
        result = executor._render_prompt(skill, "", {"metric": "CPA"})
        # KeyError on {period} → returns raw template
        assert result == "Analyze {metric} for {period}."


# ---------------------------------------------------------------------------
# _resolve_model tests
# ---------------------------------------------------------------------------


class TestResolveModel:
    """Tests for model name resolution."""

    def test_haiku_maps_to_model_fast(self):
        with patch("src.claude_code.executor.settings") as mock_settings:
            mock_settings.model_fast = "claude-3-5-haiku-20241022"
            assert _resolve_model("haiku") == "claude-3-5-haiku-20241022"

    def test_sonnet_maps_to_model_standard(self):
        with patch("src.claude_code.executor.settings") as mock_settings:
            mock_settings.model_standard = "claude-sonnet-4-20250514"
            assert _resolve_model("sonnet") == "claude-sonnet-4-20250514"

    def test_opus_maps_to_model_reasoning(self):
        with patch("src.claude_code.executor.settings") as mock_settings:
            mock_settings.model_reasoning = "claude-opus-4-20250514"
            assert _resolve_model("opus") == "claude-opus-4-20250514"

    def test_unknown_model_falls_back_to_standard(self):
        with patch("src.claude_code.executor.settings") as mock_settings:
            mock_settings.model_fast = "fast"
            mock_settings.model_standard = "standard"
            mock_settings.model_reasoning = "reasoning"
            assert _resolve_model("gpt-4") == "standard"

    def test_empty_string_falls_back_to_standard(self):
        with patch("src.claude_code.executor.settings") as mock_settings:
            mock_settings.model_fast = "fast"
            mock_settings.model_standard = "standard"
            mock_settings.model_reasoning = "reasoning"
            assert _resolve_model("") == "standard"


# ---------------------------------------------------------------------------
# _resolve_tools tests
# ---------------------------------------------------------------------------


class TestResolveTools:
    """Tests for tool resolution from ToolRegistry."""

    def test_default_returns_direct_tools(self):
        """Without tools_required, should return all DIRECT_TOOLS."""
        executor = ClaudeCodeExecutor()
        skill = _make_skill(tools_required=())

        mock_registry = MagicMock()
        mock_registry.get_filtered_definitions.return_value = [
            {"name": "get_google_ads_campaigns"},
            {"name": "get_meta_campaigns"},
        ]

        with (
            patch("src.agent.tool_registry.get_global_registry", return_value=mock_registry),
            patch.dict(
                "sys.modules",
                {
                    "src.mcp_servers.bigquery": MagicMock(),
                    "src.mcp_servers.google_ads": MagicMock(),
                    "src.mcp_servers.google_drive": MagicMock(),
                    "src.mcp_servers.meta": MagicMock(),
                    "src.mcp_servers.slack": MagicMock(),
                    "src.mcp_servers.system": MagicMock(),
                },
            ),
        ):
            result = executor._resolve_tools(skill)

        assert len(result) == 2
        # Should be called with all DIRECT_TOOLS
        mock_registry.get_filtered_definitions.assert_called_once()
        call_args = mock_registry.get_filtered_definitions.call_args[0][0]
        assert isinstance(call_args, list)
        assert len(call_args) > 0  # Non-empty set of tools

    def test_tools_required_filters_to_subset(self):
        """tools_required should intersect with DIRECT_TOOLS."""
        executor = ClaudeCodeExecutor()
        # Use a tool that IS in DIRECT_TOOLS
        skill = _make_skill(tools_required=("get_google_ads_campaigns", "get_meta_campaigns"))

        mock_registry = MagicMock()
        mock_registry.get_filtered_definitions.return_value = [
            {"name": "get_google_ads_campaigns"},
            {"name": "get_meta_campaigns"},
        ]

        with (
            patch("src.agent.tool_registry.get_global_registry", return_value=mock_registry),
            patch.dict(
                "sys.modules",
                {
                    "src.mcp_servers.bigquery": MagicMock(),
                    "src.mcp_servers.google_ads": MagicMock(),
                    "src.mcp_servers.google_drive": MagicMock(),
                    "src.mcp_servers.meta": MagicMock(),
                    "src.mcp_servers.slack": MagicMock(),
                    "src.mcp_servers.system": MagicMock(),
                },
            ),
        ):
            executor._resolve_tools(skill)

        mock_registry.get_filtered_definitions.assert_called_once()
        call_args = mock_registry.get_filtered_definitions.call_args[0][0]
        assert "get_google_ads_campaigns" in call_args
        assert "get_meta_campaigns" in call_args

    def test_context_dependent_tools_excluded(self):
        """Context-dependent tools should not be returned even if in tools_required."""
        executor = ClaudeCodeExecutor()
        # "save_memory" is in CONTEXT_DEPENDENT_TOOLS, not in DIRECT_TOOLS
        skill = _make_skill(tools_required=("save_memory", "delegate_to_role"))

        mock_registry = MagicMock()
        mock_registry.get_filtered_definitions.return_value = []

        with (
            patch("src.agent.tool_registry.get_global_registry", return_value=mock_registry),
            patch.dict(
                "sys.modules",
                {
                    "src.mcp_servers.bigquery": MagicMock(),
                    "src.mcp_servers.google_ads": MagicMock(),
                    "src.mcp_servers.google_drive": MagicMock(),
                    "src.mcp_servers.meta": MagicMock(),
                    "src.mcp_servers.slack": MagicMock(),
                    "src.mcp_servers.system": MagicMock(),
                },
            ),
        ):
            executor._resolve_tools(skill)

        # No tools match DIRECT_TOOLS, so falls back to full DIRECT_TOOLS set
        mock_registry.get_filtered_definitions.assert_called_once()

    @pytest.mark.asyncio
    async def test_include_sidera_tools_false_returns_empty(self):
        """When include_sidera_tools=False, execute passes empty tools."""
        executor = ClaudeCodeExecutor()
        skill = _make_skill()

        fake_turn = FakeTurnResult()

        with (
            patch(
                "src.claude_code.executor.load_context_text",
                return_value="",
            ),
            patch(
                "src.claude_code.executor.run_agent_loop",
                new_callable=AsyncMock,
                return_value=fake_turn,
            ) as mock_loop,
        ):
            await executor.execute(
                skill,
                "Test",
                "user1",
                include_sidera_tools=False,
            )

        # tools should be None (empty list converted)
        _, kwargs = mock_loop.call_args
        assert kwargs.get("tools") is None


# ---------------------------------------------------------------------------
# execute tests
# ---------------------------------------------------------------------------


class TestExecute:
    """Tests for the execute() method using run_agent_loop."""

    @pytest.mark.asyncio
    async def test_happy_path(self):
        """A successful execution should return a populated ClaudeCodeResult."""
        executor = ClaudeCodeExecutor()
        skill = _make_skill()

        fake_turn = FakeTurnResult()

        with (
            patch(
                "src.claude_code.executor.load_context_text",
                return_value="",
            ),
            patch(
                "src.claude_code.executor.run_agent_loop",
                new_callable=AsyncMock,
                return_value=fake_turn,
            ),
            patch.object(executor, "_resolve_tools", return_value=[{"name": "tool1"}]),
        ):
            result = await executor.execute(skill, "Test prompt", "user1")

        assert result.output_text == "Analysis complete."
        assert result.cost_usd == 0.05
        assert result.num_turns == 3
        assert result.duration_ms == 5000
        assert result.session_id == "sess_123"
        assert result.is_error is False
        assert result.error_message == ""
        assert result.skill_id == "test_skill"
        assert result.user_id == "user1"

    @pytest.mark.asyncio
    async def test_error_from_agent_loop(self):
        """Exceptions from run_agent_loop should be caught and returned as error results."""
        executor = ClaudeCodeExecutor()
        skill = _make_skill()

        with (
            patch(
                "src.claude_code.executor.load_context_text",
                return_value="",
            ),
            patch(
                "src.claude_code.executor.run_agent_loop",
                new_callable=AsyncMock,
                side_effect=RuntimeError("API call failed"),
            ),
            patch.object(executor, "_resolve_tools", return_value=[]),
        ):
            result = await executor.execute(skill, "Test prompt", "user1")

        assert result.is_error is True
        assert "API call failed" in result.error_message
        assert result.output_text == ""
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_is_error_in_turn_result(self):
        """If TurnResult.is_error is True, ClaudeCodeResult should reflect that."""
        executor = ClaudeCodeExecutor()
        skill = _make_skill()

        fake_turn = FakeTurnResult(
            text="Agent loop error",
            is_error=True,
            cost={
                "total_cost_usd": 0.01,
                "input_tokens": 100,
                "output_tokens": 10,
                "duration_ms": 1000,
            },
            turn_count=1,
        )

        with (
            patch(
                "src.claude_code.executor.load_context_text",
                return_value="",
            ),
            patch(
                "src.claude_code.executor.run_agent_loop",
                new_callable=AsyncMock,
                return_value=fake_turn,
            ),
            patch.object(executor, "_resolve_tools", return_value=[]),
        ):
            result = await executor.execute(skill, "Test prompt", "user1")

        assert result.is_error is True
        assert result.error_message == "Agent loop error"
        assert result.cost_usd == 0.01

    @pytest.mark.asyncio
    async def test_correct_args_passed_to_agent_loop(self):
        """Verify the right arguments are passed to run_agent_loop."""
        executor = ClaudeCodeExecutor()
        skill = _make_skill(model="haiku", max_turns=10)

        fake_turn = FakeTurnResult()
        fake_tools = [{"name": "tool1"}, {"name": "tool2"}]

        with (
            patch(
                "src.claude_code.executor.load_context_text",
                return_value="",
            ),
            patch(
                "src.claude_code.executor.run_agent_loop",
                new_callable=AsyncMock,
                return_value=fake_turn,
            ) as mock_loop,
            patch.object(executor, "_resolve_tools", return_value=fake_tools),
            patch("src.claude_code.executor.settings") as mock_settings,
        ):
            mock_settings.model_fast = "claude-3-5-haiku-20241022"
            mock_settings.model_standard = "claude-sonnet-4-20250514"
            mock_settings.model_reasoning = "claude-opus-4-20250514"
            await executor.execute(
                skill,
                "Custom prompt",
                "user1",
                role_context="Role ctx",
                memory_context="Memory ctx",
            )

        mock_loop.assert_called_once()
        _, kwargs = mock_loop.call_args
        assert kwargs["model"] == "claude-3-5-haiku-20241022"
        assert kwargs["max_turns"] == 10
        assert kwargs["tools"] == fake_tools
        assert kwargs["user_prompt"] == "Custom prompt"
        assert "Role ctx" in kwargs["system_prompt"]
        assert "Memory ctx" in kwargs["system_prompt"]

    @pytest.mark.asyncio
    async def test_empty_cost_dict_handled(self):
        """TurnResult with empty cost dict should not crash."""
        executor = ClaudeCodeExecutor()
        skill = _make_skill()

        fake_turn = FakeTurnResult(cost={})

        with (
            patch(
                "src.claude_code.executor.load_context_text",
                return_value="",
            ),
            patch(
                "src.claude_code.executor.run_agent_loop",
                new_callable=AsyncMock,
                return_value=fake_turn,
            ),
            patch.object(executor, "_resolve_tools", return_value=[]),
        ):
            result = await executor.execute(skill, "Test prompt", "user1")

        assert result.cost_usd == 0.0
        assert result.usage == {"input_tokens": 0, "output_tokens": 0}
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_none_text_handled(self):
        """TurnResult with None text should return empty string."""
        executor = ClaudeCodeExecutor()
        skill = _make_skill()

        fake_turn = FakeTurnResult(text=None, cost={"total_cost_usd": 0.0})

        with (
            patch(
                "src.claude_code.executor.load_context_text",
                return_value="",
            ),
            patch(
                "src.claude_code.executor.run_agent_loop",
                new_callable=AsyncMock,
                return_value=fake_turn,
            ),
            patch.object(executor, "_resolve_tools", return_value=[]),
        ):
            result = await executor.execute(skill, "Test prompt", "user1")

        assert result.output_text == ""

    @pytest.mark.asyncio
    async def test_deprecated_params_accepted(self):
        """Deprecated params (project_dir, etc.) are accepted but ignored."""
        executor = ClaudeCodeExecutor(
            project_dir="/tmp/test",
            sidera_mcp_config={"sidera": {"command": "python3"}},
        )
        skill = _make_skill()
        fake_turn = FakeTurnResult()

        with (
            patch(
                "src.claude_code.executor.load_context_text",
                return_value="",
            ),
            patch(
                "src.claude_code.executor.run_agent_loop",
                new_callable=AsyncMock,
                return_value=fake_turn,
            ),
            patch.object(executor, "_resolve_tools", return_value=[]),
        ):
            result = await executor.execute(
                skill,
                "Test",
                "user1",
                permission_mode="bypassPermissions",
                max_budget_usd=99.0,
            )

        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_max_budget_usd_passed_to_agent_loop(self):
        """max_budget_usd should be forwarded as max_cost_usd."""
        executor = ClaudeCodeExecutor()
        skill = _make_skill()
        fake_turn = FakeTurnResult()

        with (
            patch("src.claude_code.executor.load_context_text", return_value=""),
            patch(
                "src.claude_code.executor.run_agent_loop",
                new_callable=AsyncMock,
                return_value=fake_turn,
            ) as mock_loop,
            patch.object(executor, "_resolve_tools", return_value=[]),
        ):
            await executor.execute(skill, "Test", "user1", max_budget_usd=2.5)

        _, kwargs = mock_loop.call_args
        assert kwargs["max_cost_usd"] == 2.5

    @pytest.mark.asyncio
    async def test_max_budget_usd_none_by_default(self):
        """Without max_budget_usd, max_cost_usd should be None."""
        executor = ClaudeCodeExecutor()
        skill = _make_skill()
        fake_turn = FakeTurnResult()

        with (
            patch("src.claude_code.executor.load_context_text", return_value=""),
            patch(
                "src.claude_code.executor.run_agent_loop",
                new_callable=AsyncMock,
                return_value=fake_turn,
            ) as mock_loop,
            patch.object(executor, "_resolve_tools", return_value=[]),
        ):
            await executor.execute(skill, "Test", "user1")

        _, kwargs = mock_loop.call_args
        assert kwargs["max_cost_usd"] is None


# ---------------------------------------------------------------------------
# Structured output parsing tests
# ---------------------------------------------------------------------------


class TestStructuredOutput:
    """Tests for _try_parse_structured_output."""

    def test_parses_json_block(self):
        text = 'Some text\n```json\n{"score": 85}\n```\nMore text'
        skill = _make_skill(output_format="JSON object with score")
        result = ClaudeCodeExecutor._try_parse_structured_output(text, skill)
        assert result == {"score": 85}

    def test_parses_raw_json(self):
        text = '{"score": 85}'
        skill = _make_skill(output_format="JSON object with score")
        result = ClaudeCodeExecutor._try_parse_structured_output(text, skill)
        assert result == {"score": 85}

    def test_returns_none_for_non_json(self):
        text = "This is just plain text"
        skill = _make_skill(output_format="JSON object")
        result = ClaudeCodeExecutor._try_parse_structured_output(text, skill)
        assert result is None

    def test_returns_none_when_no_output_format(self):
        text = '{"score": 85}'
        skill = _make_skill(output_format="")
        result = ClaudeCodeExecutor._try_parse_structured_output(text, skill)
        assert result is None

    def test_returns_none_for_empty_text(self):
        skill = _make_skill(output_format="JSON")
        result = ClaudeCodeExecutor._try_parse_structured_output("", skill)
        assert result is None

    @pytest.mark.asyncio
    async def test_structured_output_populated_in_result(self):
        """Execute should populate structured_output from output."""
        executor = ClaudeCodeExecutor()
        skill = _make_skill(output_format="JSON object")
        fake_turn = FakeTurnResult(text='```json\n{"key": "value"}\n```')

        with (
            patch("src.claude_code.executor.load_context_text", return_value=""),
            patch(
                "src.claude_code.executor.run_agent_loop",
                new_callable=AsyncMock,
                return_value=fake_turn,
            ),
            patch.object(executor, "_resolve_tools", return_value=[]),
        ):
            result = await executor.execute(skill, "Test", "user1")

        assert result.structured_output == {"key": "value"}


# ---------------------------------------------------------------------------
# Base system prompt alignment tests
# ---------------------------------------------------------------------------


class TestBasePromptAlignment:
    """Tests for BASE_SYSTEM_PROMPT inclusion."""

    def test_no_role_context_includes_base_prompt(self):
        executor = ClaudeCodeExecutor()
        skill = _make_skill(system_supplement="Supplement")

        with patch("src.claude_code.executor.load_context_text", return_value=""):
            prompt = executor._compose_system_prompt(skill, role_context="", memory_context="")

        # Should contain BASE_SYSTEM_PROMPT text and supplement
        assert len(prompt) > 0
        assert "Supplement" in prompt

    def test_with_role_context_no_base_prompt_duplication(self):
        executor = ClaudeCodeExecutor()
        skill = _make_skill(system_supplement="Supplement")

        with patch("src.claude_code.executor.load_context_text", return_value=""):
            prompt = executor._compose_system_prompt(
                skill,
                role_context="You are the media buyer.",
                memory_context="",
            )

        assert "You are the media buyer." in prompt
        assert "Supplement" in prompt

    def test_lazy_context_for_multi_turn_skills(self):
        executor = ClaudeCodeExecutor()
        skill = _make_skill(max_turns=5)

        with patch("src.claude_code.executor.load_context_text", return_value="") as mock_load:
            executor._compose_system_prompt(skill, role_context="ctx", memory_context="")

        mock_load.assert_called_once_with(skill, lazy=True)

    def test_full_context_for_single_turn_skills(self):
        executor = ClaudeCodeExecutor()
        skill = _make_skill(max_turns=1)

        with patch("src.claude_code.executor.load_context_text", return_value="") as mock_load:
            executor._compose_system_prompt(skill, role_context="ctx", memory_context="")

        mock_load.assert_called_once_with(skill, lazy=False)


# ---------------------------------------------------------------------------
# Context-dependent tools tests
# ---------------------------------------------------------------------------


class TestContextTools:
    """Tests for include_context_tools parameter."""

    def test_resolve_tools_with_context_tools_expands_set(self):
        """When include_context_tools=True, allowed tools should expand."""
        from src.mcp_stdio.bridge import HEADLESS_CONTEXT_TOOLS

        executor = ClaudeCodeExecutor()
        skill = _make_skill(tools_required=())

        mock_registry = MagicMock()
        mock_registry.get_filtered_definitions.return_value = []

        with (
            patch("src.agent.tool_registry.get_global_registry", return_value=mock_registry),
            patch.dict(
                "sys.modules",
                {
                    "src.mcp_servers.bigquery": MagicMock(),
                    "src.mcp_servers.google_ads": MagicMock(),
                    "src.mcp_servers.google_drive": MagicMock(),
                    "src.mcp_servers.meta": MagicMock(),
                    "src.mcp_servers.slack": MagicMock(),
                    "src.mcp_servers.system": MagicMock(),
                    "src.mcp_servers.actions": MagicMock(),
                    "src.mcp_servers.context": MagicMock(),
                    "src.mcp_servers.evolution": MagicMock(),
                    "src.mcp_servers.memory": MagicMock(),
                    "src.mcp_servers.messaging": MagicMock(),
                },
            ),
        ):
            executor._resolve_tools(skill, include_context_tools=True)

        call_args = mock_registry.get_filtered_definitions.call_args[0][0]
        for tool_name in HEADLESS_CONTEXT_TOOLS:
            assert tool_name in call_args

    @pytest.mark.asyncio
    async def test_contextvars_setup_and_teardown(self):
        """Contextvars should be set up before and torn down after execution."""
        executor = ClaudeCodeExecutor()
        skill = _make_skill()
        fake_turn = FakeTurnResult()

        with (
            patch("src.claude_code.executor.load_context_text", return_value=""),
            patch(
                "src.claude_code.executor.run_agent_loop",
                new_callable=AsyncMock,
                return_value=fake_turn,
            ),
            patch.object(executor, "_resolve_tools", return_value=[]),
            patch.object(executor, "_setup_contextvars") as mock_setup,
            patch.object(executor, "_teardown_contextvars") as mock_teardown,
        ):
            await executor.execute(
                skill,
                "Test",
                "user1",
                include_context_tools=True,
                role_id="performance_media_buyer",
                department_id="marketing",
            )

        mock_setup.assert_called_once_with("performance_media_buyer", "marketing", "user1")
        mock_teardown.assert_called_once()

    @pytest.mark.asyncio
    async def test_contextvars_not_setup_without_flag(self):
        """Without include_context_tools, contextvars should not be touched."""
        executor = ClaudeCodeExecutor()
        skill = _make_skill()
        fake_turn = FakeTurnResult()

        with (
            patch("src.claude_code.executor.load_context_text", return_value=""),
            patch(
                "src.claude_code.executor.run_agent_loop",
                new_callable=AsyncMock,
                return_value=fake_turn,
            ),
            patch.object(executor, "_resolve_tools", return_value=[]),
            patch.object(executor, "_setup_contextvars") as mock_setup,
        ):
            await executor.execute(skill, "Test", "user1")

        mock_setup.assert_not_called()

    @pytest.mark.asyncio
    async def test_contextvars_cleaned_up_on_error(self):
        """Contextvars should be cleaned up even if agent loop raises."""
        executor = ClaudeCodeExecutor()
        skill = _make_skill()

        with (
            patch("src.claude_code.executor.load_context_text", return_value=""),
            patch(
                "src.claude_code.executor.run_agent_loop",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
            patch.object(executor, "_resolve_tools", return_value=[]),
            patch.object(executor, "_setup_contextvars"),
            patch.object(executor, "_teardown_contextvars") as mock_teardown,
        ):
            result = await executor.execute(
                skill,
                "Test",
                "user1",
                include_context_tools=True,
                role_id="some_role",
            )

        assert result.is_error is True
        mock_teardown.assert_called_once()


# ---------------------------------------------------------------------------
# Cost cap enforcement in agent loop
# ---------------------------------------------------------------------------


class TestCostCapEnforcement:
    """Tests for max_cost_usd in run_agent_loop."""

    def test_estimate_cost_function(self):
        from src.agent.api_client import _estimate_cost

        cost = _estimate_cost("claude-sonnet-4-20250514", 1_000_000, 100_000)
        # input: 1M * $3/1M = $3, output: 100K * $15/1M = $1.5
        assert abs(cost - 4.5) < 0.01

    def test_estimate_cost_unknown_model(self):
        from src.agent.api_client import _estimate_cost

        cost = _estimate_cost("unknown-model", 1_000_000, 100_000)
        assert abs(cost - 4.5) < 0.01

    def test_run_agent_loop_signature_has_max_cost_usd(self):
        import inspect

        from src.agent.api_client import run_agent_loop

        sig = inspect.signature(run_agent_loop)
        assert "max_cost_usd" in sig.parameters


# ---------------------------------------------------------------------------
# HEADLESS_CONTEXT_TOOLS constant
# ---------------------------------------------------------------------------


class TestHeadlessContextTools:
    """Verify HEADLESS_CONTEXT_TOOLS is well-defined."""

    def test_is_subset_of_context_dependent(self):
        from src.mcp_stdio.bridge import CONTEXT_DEPENDENT_TOOLS, HEADLESS_CONTEXT_TOOLS

        for tool in HEADLESS_CONTEXT_TOOLS:
            assert tool in CONTEXT_DEPENDENT_TOOLS, f"{tool} not in CONTEXT_DEPENDENT_TOOLS"

    def test_excludes_dangerous_tools(self):
        from src.mcp_stdio.bridge import HEADLESS_CONTEXT_TOOLS

        assert "delegate_to_role" not in HEADLESS_CONTEXT_TOOLS
        assert "consult_peer" not in HEADLESS_CONTEXT_TOOLS
        assert "get_meeting_transcript" not in HEADLESS_CONTEXT_TOOLS
        assert "end_meeting_participation" not in HEADLESS_CONTEXT_TOOLS

    def test_includes_safe_tools(self):
        from src.mcp_stdio.bridge import HEADLESS_CONTEXT_TOOLS

        assert "save_memory" in HEADLESS_CONTEXT_TOOLS
        assert "check_inbox" in HEADLESS_CONTEXT_TOOLS
        assert "propose_skill_change" in HEADLESS_CONTEXT_TOOLS
        assert "propose_action" in HEADLESS_CONTEXT_TOOLS


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


class TestRunClaudeCodeSkill:
    """Tests for the run_claude_code_skill convenience function."""

    @pytest.mark.asyncio
    async def test_skill_not_found_returns_error(self):
        from src.claude_code import run_claude_code_skill

        mock_registry = MagicMock()
        mock_registry.get_skill.return_value = None

        with patch(
            "src.skills.db_loader.load_registry_with_db",
            new_callable=AsyncMock,
            return_value=mock_registry,
        ):
            result = await run_claude_code_skill(skill_id="nonexistent")

        assert result["is_error"] is True
        assert "not found" in result["error_message"]

    @pytest.mark.asyncio
    async def test_happy_path_returns_dict(self):
        from src.claude_code import run_claude_code_skill

        mock_skill = _make_skill()
        mock_registry = MagicMock()
        mock_registry.get_skill.return_value = mock_skill

        fake_turn = FakeTurnResult()

        with (
            patch(
                "src.skills.db_loader.load_registry_with_db",
                new_callable=AsyncMock,
                return_value=mock_registry,
            ),
            patch("src.claude_code.executor.load_context_text", return_value=""),
            patch(
                "src.claude_code.executor.run_agent_loop",
                new_callable=AsyncMock,
                return_value=fake_turn,
            ),
        ):
            result = await run_claude_code_skill(skill_id="test_skill")

        assert result["is_error"] is False
        assert result["output_text"] == "Analysis complete."
        assert result["cost_usd"] == 0.05
        assert "structured_output" in result
