"""Claude Code / Cowork plugin import system.

Load any Claude Code or Cowork plugin into Sidera — connects to the
plugin's MCP servers, registers its tools in the global ToolRegistry
(namespaced as ``plugin_name__tool_name``), and imports its SKILL.md
skills via the existing Anthropic compat layer.

Usage::

    from src.plugins import load_plugin, unload_plugin, list_plugins

    # Load a plugin directory
    loaded = await load_plugin("/path/to/plugin")

    # All plugin tools are now available to agents as "pluginname__toolname"
    # All plugin skills are imported into the SkillRegistry

    # Unload when done
    await unload_plugin("pluginname")
"""

from src.plugins.loader import (
    LoadedPlugin,
    PluginManifest,
    discover_plugin,
    get_plugin,
    list_plugins,
    load_plugin,
    unload_all_plugins,
    unload_plugin,
)
from src.plugins.mcp_client import MCPServerConfig, MCPServerConnection

__all__ = [
    "LoadedPlugin",
    "MCPServerConfig",
    "MCPServerConnection",
    "PluginManifest",
    "discover_plugin",
    "get_plugin",
    "list_plugins",
    "load_plugin",
    "unload_all_plugins",
    "unload_plugin",
]
