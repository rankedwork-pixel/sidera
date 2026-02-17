"""Tests for the MCP stdio server bridge (tool allowlist).

Verifies that:
- All DIRECT_TOOLS exist in the global ToolRegistry
- Write tools are excluded from DIRECT_TOOLS
- Context-dependent tools are excluded from DIRECT_TOOLS
- Meta-tool names don't collide with registry tools
- Counts are correct
"""

from __future__ import annotations

import src.mcp_servers.actions  # noqa: F401
import src.mcp_servers.bigquery  # noqa: F401
import src.mcp_servers.claude_code_actions  # noqa: F401
import src.mcp_servers.code_execution  # noqa: F401
import src.mcp_servers.context  # noqa: F401
import src.mcp_servers.delegation  # noqa: F401
import src.mcp_servers.evolution  # noqa: F401
import src.mcp_servers.google_ads  # noqa: F401
import src.mcp_servers.google_drive  # noqa: F401
import src.mcp_servers.meeting  # noqa: F401
import src.mcp_servers.memory  # noqa: F401
import src.mcp_servers.messaging  # noqa: F401
import src.mcp_servers.meta  # noqa: F401
import src.mcp_servers.slack  # noqa: F401
import src.mcp_servers.skill_runner  # noqa: F401
import src.mcp_servers.system  # noqa: F401
from src.agent.tool_registry import get_global_registry
from src.mcp_stdio.bridge import (
    CONTEXT_DEPENDENT_TOOLS,
    DIRECT_TOOLS,
    META_TOOL_NAMES,
    WRITE_TOOLS,
)


class TestDirectToolsAllowlist:
    """Verify DIRECT_TOOLS contains only valid, registered tools."""

    def test_all_direct_tools_exist_in_registry(self):
        """Every tool in DIRECT_TOOLS must be registered in the global registry."""
        registry = get_global_registry()
        for tool_name in DIRECT_TOOLS:
            assert tool_name in registry, (
                f"DIRECT_TOOLS contains '{tool_name}' but it's not registered"
            )

    def test_direct_tools_count(self):
        """DIRECT_TOOLS should have exactly 39 stateless tools."""
        assert len(DIRECT_TOOLS) == 39

    def test_meta_tools_count(self):
        """META_TOOL_NAMES should have exactly 7 meta-tools."""
        assert len(META_TOOL_NAMES) == 7

    def test_total_exposed_tools(self):
        """Total tools exposed to Claude Code = 39 direct + 7 meta = 46."""
        assert len(DIRECT_TOOLS) + len(META_TOOL_NAMES) == 46


class TestWriteToolsExcluded:
    """Verify write tools are NOT in DIRECT_TOOLS."""

    def test_write_tools_not_in_direct(self):
        """Write tools should not appear in DIRECT_TOOLS."""
        overlap = DIRECT_TOOLS & WRITE_TOOLS
        assert not overlap, f"Write tools leaked into DIRECT_TOOLS: {overlap}"

    def test_write_tools_count(self):
        """There should be exactly 4 excluded write tools."""
        assert len(WRITE_TOOLS) == 4

    def test_specific_write_tools_excluded(self):
        """Each specific write tool must not be in DIRECT_TOOLS."""
        for name in [
            "update_google_ads_campaign",
            "update_google_ads_keywords",
            "update_meta_campaign",
            "update_meta_ad",
        ]:
            assert name not in DIRECT_TOOLS, f"{name} should not be in DIRECT_TOOLS"


class TestContextDependentToolsExcluded:
    """Verify context-dependent tools are NOT in DIRECT_TOOLS."""

    def test_context_tools_not_in_direct(self):
        """Context-dependent tools should not appear in DIRECT_TOOLS."""
        overlap = DIRECT_TOOLS & CONTEXT_DEPENDENT_TOOLS
        assert not overlap, f"Context-dependent tools in DIRECT_TOOLS: {overlap}"

    def test_context_dependent_count(self):
        """There should be exactly 17 excluded context-dependent tools."""
        assert len(CONTEXT_DEPENDENT_TOOLS) == 17

    def test_propose_action_excluded(self):
        assert "propose_action" not in DIRECT_TOOLS

    def test_save_memory_excluded(self):
        assert "save_memory" not in DIRECT_TOOLS

    def test_delegate_to_role_excluded(self):
        assert "delegate_to_role" not in DIRECT_TOOLS

    def test_propose_claude_code_task_excluded(self):
        assert "propose_claude_code_task" not in DIRECT_TOOLS


class TestMetaToolsNoCollision:
    """Verify meta-tool names don't collide with ToolRegistry names."""

    def test_meta_tools_not_in_registry(self):
        """Meta-tool names should not exist in the global registry."""
        registry = get_global_registry()
        for name in META_TOOL_NAMES:
            assert name not in registry, f"Meta-tool '{name}' collides with a registered tool"

    def test_no_overlap_with_direct(self):
        """Meta-tool names should not overlap with DIRECT_TOOLS."""
        overlap = DIRECT_TOOLS & META_TOOL_NAMES
        assert not overlap, f"Meta-tools overlap with DIRECT_TOOLS: {overlap}"


class TestCategoryCompleteness:
    """Verify all registered tools are categorized."""

    def test_all_tools_categorized(self):
        """Every registered tool should be in exactly one category."""
        registry = get_global_registry()
        all_names = set(registry.get_tool_names())

        categorized = DIRECT_TOOLS | WRITE_TOOLS | CONTEXT_DEPENDENT_TOOLS
        uncategorized = all_names - categorized
        assert not uncategorized, (
            f"Tools not in any category: {uncategorized}. "
            f"Add them to DIRECT_TOOLS, WRITE_TOOLS, or CONTEXT_DEPENDENT_TOOLS."
        )

    def test_no_double_categorization(self):
        """No tool should appear in more than one category."""
        pairs = [
            ("DIRECT", DIRECT_TOOLS, "WRITE", WRITE_TOOLS),
            ("DIRECT", DIRECT_TOOLS, "CONTEXT", CONTEXT_DEPENDENT_TOOLS),
            ("WRITE", WRITE_TOOLS, "CONTEXT", CONTEXT_DEPENDENT_TOOLS),
        ]
        for name_a, set_a, name_b, set_b in pairs:
            overlap = set_a & set_b
            assert not overlap, f"Tools in both {name_a} and {name_b}: {overlap}"
