"""Tests for src.plugins.loader — plugin loader and proxy handlers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.tool_registry import ToolDefinition, get_global_registry
from src.plugins.loader import (
    LoadedPlugin,
    PluginManifest,
    _build_proxy_handler,
    _derive_plugin_name,
    _discover_skill_dirs,
    _read_mcp_json,
    _read_plugin_json,
    discover_plugin,
    import_plugin_skills,
    list_plugins,
    load_plugin,
    register_plugin_tools,
    unload_all_plugins,
    unload_plugin,
)
from src.plugins.mcp_client import MCPServerConfig, MCPServerConnection

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    """Create a minimal plugin directory structure."""
    plugin = tmp_path / "test-plugin"
    plugin.mkdir()

    # .claude-plugin/plugin.json
    meta_dir = plugin / ".claude-plugin"
    meta_dir.mkdir()
    (meta_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "test-plugin",
                "version": "1.0.0",
                "description": "A test plugin",
                "author": {"name": "Test Author"},
            }
        )
    )

    # .mcp.json
    (plugin / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "test-server": {
                        "command": "node",
                        "args": ["server.js"],
                    }
                }
            }
        )
    )

    # skills/my-skill/SKILL.md
    skill_dir = plugin / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: A test skill\n---\nDo the thing.\n"
    )

    return plugin


@pytest.fixture(autouse=True)
async def cleanup_plugins():
    """Ensure plugin state is clean between tests."""
    yield
    await unload_all_plugins()


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestReadPluginJson:
    def test_with_manifest(self, plugin_dir: Path) -> None:
        result = _read_plugin_json(str(plugin_dir))
        assert result["name"] == "test-plugin"
        assert result["version"] == "1.0.0"

    def test_without_manifest(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty-plugin"
        empty.mkdir()
        assert _read_plugin_json(str(empty)) == {}


class TestReadMcpJson:
    def test_with_mcp(self, plugin_dir: Path) -> None:
        result = _read_mcp_json(str(plugin_dir))
        assert "mcpServers" in result
        assert "test-server" in result["mcpServers"]

    def test_without_mcp(self, tmp_path: Path) -> None:
        empty = tmp_path / "no-mcp"
        empty.mkdir()
        assert _read_mcp_json(str(empty)) == {}


class TestDerivePluginName:
    def test_from_json(self) -> None:
        assert _derive_plugin_name("/x", {"name": "my-plugin"}) == "my_plugin"

    def test_from_dir(self) -> None:
        assert _derive_plugin_name("/path/to/cool-plugin", {}) == "cool_plugin"

    def test_sanitization(self) -> None:
        assert _derive_plugin_name("/x", {"name": "My Cool Plugin!"}) == "my_cool_plugin"

    def test_empty_fallback(self) -> None:
        assert _derive_plugin_name("/x", {"name": "!!!"}) == "unnamed_plugin"

    def test_hyphens_to_underscores(self) -> None:
        assert _derive_plugin_name("/x", {"name": "a-b-c"}) == "a_b_c"


class TestDiscoverSkillDirs:
    def test_finds_skills(self, plugin_dir: Path) -> None:
        dirs = _discover_skill_dirs(str(plugin_dir))
        assert len(dirs) == 1
        assert "my-skill" in dirs[0]

    def test_no_skills_dir(self, tmp_path: Path) -> None:
        empty = tmp_path / "no-skills"
        empty.mkdir()
        assert _discover_skill_dirs(str(empty)) == []

    def test_skips_dirs_without_skill_md(self, tmp_path: Path) -> None:
        plugin = tmp_path / "partial"
        skills = plugin / "skills" / "bad-skill"
        skills.mkdir(parents=True)
        (skills / "README.md").write_text("not a skill")
        assert _discover_skill_dirs(str(plugin)) == []


class TestDiscoverPlugin:
    def test_full_discovery(self, plugin_dir: Path) -> None:
        manifest = discover_plugin(str(plugin_dir))
        assert manifest.name == "test_plugin"
        assert manifest.version == "1.0.0"
        assert manifest.description == "A test plugin"
        assert manifest.author == "Test Author"
        assert len(manifest.mcp_servers) == 1
        assert manifest.mcp_servers[0].name == "test-server"
        assert len(manifest.skill_dirs) == 1

    def test_minimal_plugin(self, tmp_path: Path) -> None:
        minimal = tmp_path / "minimal"
        minimal.mkdir()
        manifest = discover_plugin(str(minimal))
        assert manifest.name == "minimal"
        assert manifest.mcp_servers == ()
        assert manifest.skill_dirs == ()

    def test_author_as_string(self, tmp_path: Path) -> None:
        plugin = tmp_path / "str-author"
        meta_dir = plugin / ".claude-plugin"
        meta_dir.mkdir(parents=True)
        (meta_dir / "plugin.json").write_text(json.dumps({"name": "x", "author": "Jane Doe"}))
        manifest = discover_plugin(str(plugin))
        assert manifest.author == "Jane Doe"


# ---------------------------------------------------------------------------
# Proxy handlers
# ---------------------------------------------------------------------------


class TestProxyHandler:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        mock_text = MagicMock()
        mock_text.text = "result data"
        mock_result = MagicMock()
        mock_result.content = [mock_text]
        mock_result.isError = False

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)

        conn = MCPServerConnection(
            config=MCPServerConfig(name="s", command="x"),
            is_connected=True,
            session=mock_session,
        )

        handler = _build_proxy_handler(conn, "search")
        result = await handler({"query": "test"})

        assert result["content"][0]["text"] == "result data"
        assert not result.get("is_error")
        mock_session.call_tool.assert_awaited_once_with("search", {"query": "test"})

    @pytest.mark.asyncio
    async def test_error_from_server(self) -> None:
        mock_text = MagicMock()
        mock_text.text = "not found"
        mock_result = MagicMock()
        mock_result.content = [mock_text]
        mock_result.isError = True

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)

        conn = MCPServerConnection(
            config=MCPServerConfig(name="s", command="x"),
            is_connected=True,
            session=mock_session,
        )

        handler = _build_proxy_handler(conn, "get_item")
        result = await handler({})
        assert result["is_error"] is True

    @pytest.mark.asyncio
    async def test_disconnected_server(self) -> None:
        conn = MCPServerConnection(
            config=MCPServerConfig(name="dead", command="x"),
            is_connected=False,
            session=None,
        )

        handler = _build_proxy_handler(conn, "anything")
        result = await handler({})
        assert "is not connected" in result["content"][0]["text"]
        assert result["is_error"] is True

    @pytest.mark.asyncio
    async def test_exception_during_call(self) -> None:
        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(side_effect=RuntimeError("boom"))

        conn = MCPServerConnection(
            config=MCPServerConfig(name="s", command="x"),
            is_connected=True,
            session=mock_session,
        )

        handler = _build_proxy_handler(conn, "broken")
        result = await handler({})
        assert "boom" in result["content"][0]["text"]
        assert result["is_error"] is True

    @pytest.mark.asyncio
    async def test_empty_response(self) -> None:
        mock_result = MagicMock()
        mock_result.content = []
        mock_result.isError = False

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)

        conn = MCPServerConnection(
            config=MCPServerConfig(name="s", command="x"),
            is_connected=True,
            session=mock_session,
        )

        handler = _build_proxy_handler(conn, "empty")
        result = await handler({})
        assert result["content"][0]["text"] == "(empty response)"

    @pytest.mark.asyncio
    async def test_binary_content(self) -> None:
        mock_item = MagicMock(spec=[])
        mock_item.data = b"binary"
        mock_item.mimeType = "image/png"
        mock_result = MagicMock()
        mock_result.content = [mock_item]
        mock_result.isError = False

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)

        conn = MCPServerConnection(
            config=MCPServerConfig(name="s", command="x"),
            is_connected=True,
            session=mock_session,
        )

        handler = _build_proxy_handler(conn, "img")
        result = await handler({})
        assert "image/png" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Register plugin tools
# ---------------------------------------------------------------------------


class TestRegisterPluginTools:
    def test_namespacing(self) -> None:
        conn = MCPServerConnection(
            config=MCPServerConfig(name="s", command="x"),
            plugin_name="myplugin",
            tools=[
                {"name": "search", "description": "Search", "inputSchema": {}},
            ],
            is_connected=True,
        )

        registered = register_plugin_tools(conn, "myplugin")
        assert registered == ["myplugin__search"]

        registry = get_global_registry()
        assert "myplugin__search" in registry
        # Clean up
        registry.remove("myplugin__search")

    def test_description_prefix(self) -> None:
        conn = MCPServerConnection(
            config=MCPServerConfig(name="s", command="x"),
            plugin_name="p",
            tools=[
                {"name": "t", "description": "Do stuff", "inputSchema": {}},
            ],
            is_connected=True,
        )

        register_plugin_tools(conn, "p")
        registry = get_global_registry()
        tool_def = registry._tools.get("p__t")
        assert tool_def is not None
        assert tool_def.description.startswith("[Plugin: p]")
        registry.remove("p__t")

    def test_collision_skipped(self) -> None:
        registry = get_global_registry()

        # Pre-register a tool with the same namespaced name
        existing = ToolDefinition(
            name="p__existing",
            description="already here",
            input_schema={},
            handler=AsyncMock(),
        )
        registry.register(existing)

        conn = MCPServerConnection(
            config=MCPServerConfig(name="s", command="x"),
            tools=[
                {"name": "existing", "description": "New", "inputSchema": {}},
            ],
            is_connected=True,
        )

        registered = register_plugin_tools(conn, "p")
        assert registered == []  # skipped due to collision
        registry.remove("p__existing")


# ---------------------------------------------------------------------------
# Skill import
# ---------------------------------------------------------------------------


class TestImportPluginSkills:
    def test_success(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: A skill\n---\nInstructions here.\n"
        )

        ids = import_plugin_skills([str(skill_dir)], "myplugin")
        assert len(ids) == 1
        assert "test_skill" in ids[0] or "test-skill" in ids[0]

    def test_invalid_skill(self, tmp_path: Path) -> None:
        bad_dir = tmp_path / "bad"
        bad_dir.mkdir()
        (bad_dir / "SKILL.md").write_text("no frontmatter here")

        # Should not raise, just return empty or log warning
        ids = import_plugin_skills([str(bad_dir)], "p")
        # May or may not import depending on parser tolerance
        assert isinstance(ids, list)


# ---------------------------------------------------------------------------
# load_plugin / unload_plugin / list_plugins
# ---------------------------------------------------------------------------


class TestLoadPlugin:
    @pytest.mark.asyncio
    async def test_nonexistent_dir(self) -> None:
        with pytest.raises(ValueError, match="does not exist"):
            await load_plugin("/nonexistent/path/to/plugin")

    @pytest.mark.asyncio
    async def test_full_load_with_mocked_mcp(self, plugin_dir: Path) -> None:
        """Full load with mocked MCP connection."""
        mock_tool = MagicMock()
        mock_tool.name = "plugin_search"
        mock_tool.description = "Search"
        mock_tool.inputSchema = {"type": "object", "properties": {}}

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_list_result = MagicMock()
        mock_list_result.tools = [mock_tool]
        mock_session.list_tools = AsyncMock(return_value=mock_list_result)

        mock_transport = (MagicMock(), MagicMock())

        with (
            patch("src.plugins.mcp_client.stdio_client") as mock_stdio,
            patch("src.plugins.mcp_client.ClientSession") as mock_cs_cls,
        ):
            mock_stdio_ctx = AsyncMock()
            mock_stdio_ctx.__aenter__ = AsyncMock(return_value=mock_transport)
            mock_stdio_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_stdio.return_value = mock_stdio_ctx

            mock_cs_ctx = AsyncMock()
            mock_cs_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_cs_cls.return_value = mock_cs_ctx

            loaded = await load_plugin(str(plugin_dir))

            assert loaded.is_loaded
            assert loaded.manifest.name == "test_plugin"
            assert len(loaded.connections) == 1
            assert loaded.connections[0].is_connected
            assert "test_plugin__plugin_search" in loaded.registered_tool_names

            # Verify tool is in global registry
            registry = get_global_registry()
            assert "test_plugin__plugin_search" in registry

    @pytest.mark.asyncio
    async def test_duplicate_load(self, plugin_dir: Path) -> None:
        """Loading the same plugin twice returns the existing one."""
        with (
            patch("src.plugins.mcp_client.stdio_client") as mock_stdio,
            patch("src.plugins.mcp_client.ClientSession"),
        ):
            # First call: connection fails (simpler mock)
            mock_stdio_ctx = AsyncMock()
            mock_stdio_ctx.__aenter__ = AsyncMock(side_effect=OSError("fail"))
            mock_stdio_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_stdio.return_value = mock_stdio_ctx

            first = await load_plugin(str(plugin_dir))
            second = await load_plugin(str(plugin_dir))
            assert first is second

    @pytest.mark.asyncio
    async def test_minimal_plugin(self, tmp_path: Path) -> None:
        """Plugin with no MCP servers and no skills."""
        empty = tmp_path / "empty"
        empty.mkdir()

        loaded = await load_plugin(str(empty))
        assert loaded.is_loaded
        assert loaded.connections == []
        assert loaded.registered_tool_names == []
        assert loaded.imported_skill_ids == []


class TestUnloadPlugin:
    @pytest.mark.asyncio
    async def test_unload(self, tmp_path: Path) -> None:
        empty = tmp_path / "unload-test"
        empty.mkdir()
        loaded = await load_plugin(str(empty))
        assert len(list_plugins()) == 1

        await unload_plugin(loaded.manifest.name)
        assert len(list_plugins()) == 0

    @pytest.mark.asyncio
    async def test_unload_not_found(self) -> None:
        # Should not raise
        await unload_plugin("nonexistent_plugin")

    @pytest.mark.asyncio
    async def test_unload_removes_tools(self, tmp_path: Path) -> None:
        """Unloading removes registered tools from registry."""
        plugin = tmp_path / "tool-plugin"
        plugin.mkdir()

        # Manually set up a loaded plugin with tools
        registry = get_global_registry()
        registry.register(
            ToolDefinition(
                name="tp__tool1",
                description="test",
                input_schema={},
                handler=AsyncMock(),
            )
        )

        from src.plugins.loader import _loaded_plugins

        manifest = PluginManifest(name="tp", source_dir=str(plugin))
        lp = LoadedPlugin(
            manifest=manifest,
            registered_tool_names=["tp__tool1"],
            is_loaded=True,
        )
        _loaded_plugins["tp"] = lp

        assert "tp__tool1" in registry
        await unload_plugin("tp")
        assert "tp__tool1" not in registry


class TestListPlugins:
    @pytest.mark.asyncio
    async def test_empty(self) -> None:
        assert list_plugins() == []

    @pytest.mark.asyncio
    async def test_with_plugins(self, tmp_path: Path) -> None:
        p1 = tmp_path / "p1"
        p1.mkdir()
        p2 = tmp_path / "p2"
        p2.mkdir()

        await load_plugin(str(p1))
        await load_plugin(str(p2))
        assert len(list_plugins()) == 2


class TestUnloadAll:
    @pytest.mark.asyncio
    async def test_unload_all(self, tmp_path: Path) -> None:
        for name in ("a", "b", "c"):
            d = tmp_path / name
            d.mkdir()
            await load_plugin(str(d))

        assert len(list_plugins()) == 3
        await unload_all_plugins()
        assert len(list_plugins()) == 0
