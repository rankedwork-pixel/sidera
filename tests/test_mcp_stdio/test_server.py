"""Tests for the MCP stdio server module.

Verifies the server's list_tools and call_tool handlers work correctly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from mcp.types import TextContent

from src.mcp_stdio.bridge import DIRECT_TOOLS, META_TOOL_NAMES


class TestListTools:
    """Verify the server's list_tools handler."""

    @pytest.mark.asyncio
    async def test_list_tools_returns_all(self):
        """list_tools should return direct + meta tools."""
        from src.mcp_stdio.server import list_tools

        tools = await list_tools()

        # Should have direct + meta tools
        expected_count = len(DIRECT_TOOLS) + len(META_TOOL_NAMES)
        assert len(tools) == expected_count, f"Expected {expected_count} tools, got {len(tools)}"

    @pytest.mark.asyncio
    async def test_list_tools_includes_direct_tools(self):
        """All DIRECT_TOOLS should be in the returned list."""
        from src.mcp_stdio.server import list_tools

        tools = await list_tools()
        tool_names = {t.name for t in tools}

        for name in DIRECT_TOOLS:
            assert name in tool_names, f"Direct tool '{name}' missing from list_tools"

    @pytest.mark.asyncio
    async def test_list_tools_includes_meta_tools(self):
        """All meta-tools should be in the returned list."""
        from src.mcp_stdio.server import list_tools

        tools = await list_tools()
        tool_names = {t.name for t in tools}

        for name in META_TOOL_NAMES:
            assert name in tool_names, f"Meta-tool '{name}' missing from list_tools"

    @pytest.mark.asyncio
    async def test_tools_have_schemas(self):
        """Every tool should have a name, description, and inputSchema."""
        from src.mcp_stdio.server import list_tools

        tools = await list_tools()
        for tool in tools:
            assert tool.name, "Tool missing name"
            assert tool.description, f"Tool '{tool.name}' missing description"
            assert tool.inputSchema, f"Tool '{tool.name}' missing inputSchema"


class TestCallTool:
    """Verify the server's call_tool handler."""

    @pytest.mark.asyncio
    async def test_dispatch_direct_tool(self):
        """Direct tools should be dispatched to ToolRegistry."""
        from src.mcp_stdio.server import call_tool

        with patch(
            "src.mcp_stdio.server.registry.dispatch",
            new_callable=AsyncMock,
            return_value='{"accounts": []}',
        ) as mock_dispatch:
            result = await call_tool("list_google_ads_accounts", {})

        mock_dispatch.assert_called_once_with("list_google_ads_accounts", {})
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert "accounts" in result[0].text

    @pytest.mark.asyncio
    async def test_dispatch_meta_tool(self):
        """Meta-tools should be dispatched to their handlers."""
        from src.mcp_stdio.server import call_tool

        mock_handler = AsyncMock(return_value=[TextContent(type="text", text="roles listed")])

        with patch(
            "src.mcp_stdio.server.META_TOOL_HANDLERS",
            {"list_roles": mock_handler},
        ):
            result = await call_tool("list_roles", {})

        mock_handler.assert_called_once_with({})
        assert "roles listed" in result[0].text

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        """Unknown tools should return an error message."""
        from src.mcp_stdio.server import call_tool

        result = await call_tool("totally_fake_tool", {})
        assert "Unknown tool" in result[0].text
        assert "totally_fake_tool" in result[0].text

    @pytest.mark.asyncio
    async def test_direct_tool_passes_arguments(self):
        """Arguments should be forwarded to registry.dispatch."""
        from src.mcp_stdio.server import call_tool

        args = {"customer_id": "123", "date_range": "LAST_7_DAYS"}
        with patch(
            "src.mcp_stdio.server.registry.dispatch",
            new_callable=AsyncMock,
            return_value="ok",
        ) as mock_dispatch:
            await call_tool("get_google_ads_performance", args)

        mock_dispatch.assert_called_once_with("get_google_ads_performance", args)
