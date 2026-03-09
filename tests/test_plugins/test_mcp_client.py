"""Tests for src.plugins.mcp_client — MCP client wrapper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.plugins.mcp_client import (
    MCPServerConfig,
    MCPServerConnection,
    _expand_env,
    connect_mcp_server,
    disconnect_mcp_server,
    parse_mcp_json,
)

# ---------------------------------------------------------------------------
# Environment variable expansion
# ---------------------------------------------------------------------------


class TestExpandEnv:
    def test_basic_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_VAR", "hello")
        assert _expand_env("${MY_VAR}", "/plugin") == "hello"

    def test_plugin_root(self) -> None:
        assert _expand_env("${CLAUDE_PLUGIN_ROOT}", "/my/plugin") == "/my/plugin"

    def test_missing_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("UNDEFINED_VAR_XYZ", raising=False)
        assert _expand_env("${UNDEFINED_VAR_XYZ}", "/p") == ""

    def test_multiple_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("A", "1")
        monkeypatch.setenv("B", "2")
        result = _expand_env("${A}-${B}-${CLAUDE_PLUGIN_ROOT}", "/p")
        assert result == "1-2-/p"

    def test_no_vars(self) -> None:
        assert _expand_env("plain text", "/p") == "plain text"

    def test_partial_match(self) -> None:
        assert _expand_env("before ${CLAUDE_PLUGIN_ROOT} after", "/x") == "before /x after"


# ---------------------------------------------------------------------------
# parse_mcp_json
# ---------------------------------------------------------------------------


class TestParseMcpJson:
    def test_single_server(self) -> None:
        mcp_json = {
            "mcpServers": {
                "my-server": {
                    "command": "node",
                    "args": ["server.js"],
                    "env": {"PORT": "3000"},
                }
            }
        }
        configs = parse_mcp_json(mcp_json, "/plugin")
        assert len(configs) == 1
        assert configs[0].name == "my-server"
        assert configs[0].command == "node"
        assert configs[0].args == ("server.js",)
        assert configs[0].env == {"PORT": "3000"}

    def test_multiple_servers(self) -> None:
        mcp_json = {
            "mcpServers": {
                "a": {"command": "cmd_a"},
                "b": {"command": "cmd_b"},
            }
        }
        configs = parse_mcp_json(mcp_json, "/p")
        assert len(configs) == 2
        names = {c.name for c in configs}
        assert names == {"a", "b"}

    def test_empty_mcp_servers(self) -> None:
        assert parse_mcp_json({}, "/p") == []
        assert parse_mcp_json({"mcpServers": {}}, "/p") == []

    def test_env_expansion_in_args(self) -> None:
        mcp_json = {
            "mcpServers": {
                "s": {
                    "command": "node",
                    "args": ["${CLAUDE_PLUGIN_ROOT}/index.js"],
                }
            }
        }
        configs = parse_mcp_json(mcp_json, "/my/plugin")
        assert configs[0].args == ("/my/plugin/index.js",)

    def test_cwd_expansion(self) -> None:
        mcp_json = {
            "mcpServers": {
                "s": {
                    "command": "node",
                    "cwd": "${CLAUDE_PLUGIN_ROOT}/server",
                }
            }
        }
        configs = parse_mcp_json(mcp_json, "/root")
        assert configs[0].cwd == "/root/server"

    def test_default_cwd_is_plugin_dir(self) -> None:
        mcp_json = {"mcpServers": {"s": {"command": "node"}}}
        configs = parse_mcp_json(mcp_json, "/my/dir")
        assert configs[0].cwd == "/my/dir"


# ---------------------------------------------------------------------------
# connect_mcp_server
# ---------------------------------------------------------------------------


class TestConnectMcpServer:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        """Successful connection returns connected MCPServerConnection."""
        mock_tool = MagicMock()
        mock_tool.name = "search"
        mock_tool.description = "Search docs"
        mock_tool.inputSchema = {"type": "object", "properties": {}}

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_list_result = MagicMock()
        mock_list_result.tools = [mock_tool]
        mock_session.list_tools = AsyncMock(return_value=mock_list_result)

        # Mock the context managers
        mock_transport = (MagicMock(), MagicMock())

        with (
            patch("src.plugins.mcp_client.stdio_client") as mock_stdio,
            patch("src.plugins.mcp_client.ClientSession") as mock_cs_cls,
        ):
            # stdio_client is an async context manager returning transport
            mock_stdio_ctx = AsyncMock()
            mock_stdio_ctx.__aenter__ = AsyncMock(return_value=mock_transport)
            mock_stdio_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_stdio.return_value = mock_stdio_ctx

            # ClientSession is an async context manager returning session
            mock_cs_ctx = AsyncMock()
            mock_cs_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_cs_cls.return_value = mock_cs_ctx

            config = MCPServerConfig(name="test", command="node", args=("s.js",))
            conn = await connect_mcp_server(config, "myplugin")

            assert conn.is_connected
            assert len(conn.tools) == 1
            assert conn.tools[0]["name"] == "search"
            assert conn.plugin_name == "myplugin"

    @pytest.mark.asyncio
    async def test_connection_failure(self) -> None:
        """Failed connection returns MCPServerConnection with is_connected=False."""
        with patch("src.plugins.mcp_client.stdio_client") as mock_stdio:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(side_effect=OSError("spawn failed"))
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_stdio.return_value = mock_ctx

            config = MCPServerConfig(name="bad", command="nonexistent")
            conn = await connect_mcp_server(config, "plugin")

            assert not conn.is_connected
            assert conn.tools == []
            assert conn.session is None


# ---------------------------------------------------------------------------
# disconnect_mcp_server
# ---------------------------------------------------------------------------


class TestDisconnectMcpServer:
    @pytest.mark.asyncio
    async def test_disconnect(self) -> None:
        mock_stack = AsyncMock()
        mock_stack.aclose = AsyncMock()
        conn = MCPServerConnection(
            config=MCPServerConfig(name="s", command="x"),
            is_connected=True,
            session=MagicMock(),
        )
        conn._exit_stack = mock_stack

        await disconnect_mcp_server(conn)

        assert not conn.is_connected
        assert conn.session is None
        assert conn._exit_stack is None
        mock_stack.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_already_disconnected(self) -> None:
        conn = MCPServerConnection(
            config=MCPServerConfig(name="s", command="x"),
        )
        # Should not raise
        await disconnect_mcp_server(conn)
        assert not conn.is_connected

    @pytest.mark.asyncio
    async def test_disconnect_aclose_error(self) -> None:
        """aclose raises but disconnect still marks as disconnected."""
        mock_stack = AsyncMock()
        mock_stack.aclose = AsyncMock(side_effect=RuntimeError("oops"))
        conn = MCPServerConnection(
            config=MCPServerConfig(name="s", command="x"),
            is_connected=True,
        )
        conn._exit_stack = mock_stack

        await disconnect_mcp_server(conn)
        assert not conn.is_connected
        assert conn._exit_stack is None
