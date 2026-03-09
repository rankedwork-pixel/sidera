"""Plugin loader — reads Claude Code / Cowork plugin directories and wires
them into Sidera's tool and skill registries.

A plugin directory follows the standard Claude Code layout::

    my-plugin/
      .claude-plugin/plugin.json   # manifest (optional)
      .mcp.json                     # MCP server definitions
      skills/                       # SKILL.md skill directories
        my-skill/SKILL.md

:func:`load_plugin` is the main entry point.  It discovers the plugin's
components, spawns MCP server processes, registers proxy tools in the
global :class:`ToolRegistry`, and imports skills via the existing
Anthropic compat layer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from src.agent.tool_registry import ToolDefinition, get_global_registry
from src.plugins.mcp_client import (
    MCPServerConfig,
    MCPServerConnection,
    connect_mcp_server,
    disconnect_mcp_server,
    parse_mcp_json,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PluginManifest:
    """Parsed representation of a Claude Code / Cowork plugin."""

    name: str
    version: str = ""
    description: str = ""
    author: str = ""
    source_dir: str = ""
    mcp_servers: tuple[MCPServerConfig, ...] = ()
    skill_dirs: tuple[str, ...] = ()


@dataclass
class LoadedPlugin:
    """Runtime state for a loaded plugin."""

    manifest: PluginManifest
    connections: list[MCPServerConnection] = field(default_factory=list)
    registered_tool_names: list[str] = field(default_factory=list)
    imported_skill_ids: list[str] = field(default_factory=list)
    is_loaded: bool = False


# ---------------------------------------------------------------------------
# Module-level plugin registry
# ---------------------------------------------------------------------------

_loaded_plugins: dict[str, LoadedPlugin] = {}


# ---------------------------------------------------------------------------
# Plugin discovery
# ---------------------------------------------------------------------------


def _read_plugin_json(plugin_dir: str) -> dict[str, Any]:
    """Read ``.claude-plugin/plugin.json`` if present."""
    path = Path(plugin_dir) / ".claude-plugin" / "plugin.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_mcp_json(plugin_dir: str) -> dict[str, Any]:
    """Read ``.mcp.json`` if present."""
    path = Path(plugin_dir) / ".mcp.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _derive_plugin_name(plugin_dir: str, plugin_json: dict[str, Any]) -> str:
    """Derive a safe snake_case name for tool namespacing."""
    raw = plugin_json.get("name", "") or Path(plugin_dir).name
    safe = re.sub(
        r"[^a-z0-9_]",
        "",
        raw.lower().replace("-", "_").replace(" ", "_"),
    )
    return safe or "unnamed_plugin"


def _discover_skill_dirs(plugin_dir: str) -> list[str]:
    """Find ``skills/<name>/SKILL.md`` directories inside a plugin."""
    skills_dir = Path(plugin_dir) / "skills"
    if not skills_dir.is_dir():
        return []
    return sorted(
        str(child)
        for child in skills_dir.iterdir()
        if child.is_dir() and (child / "SKILL.md").exists()
    )


def discover_plugin(plugin_dir: str) -> PluginManifest:
    """Read a plugin directory and build its manifest.

    Pure discovery — no side effects, no server spawning.
    """
    plugin_dir = str(Path(plugin_dir).resolve())
    plugin_json = _read_plugin_json(plugin_dir)
    mcp_json = _read_mcp_json(plugin_dir)

    name = _derive_plugin_name(plugin_dir, plugin_json)
    mcp_configs = parse_mcp_json(mcp_json, plugin_dir) if mcp_json else []
    skill_dirs = _discover_skill_dirs(plugin_dir)

    author_raw = plugin_json.get("author", "")
    if isinstance(author_raw, dict):
        author_raw = author_raw.get("name", "")

    return PluginManifest(
        name=name,
        version=plugin_json.get("version", ""),
        description=plugin_json.get("description", ""),
        author=str(author_raw),
        source_dir=plugin_dir,
        mcp_servers=tuple(mcp_configs),
        skill_dirs=tuple(skill_dirs),
    )


# ---------------------------------------------------------------------------
# Proxy tool handlers
# ---------------------------------------------------------------------------


def _build_proxy_handler(
    conn: MCPServerConnection,
    original_tool_name: str,
) -> Any:
    """Build an async handler that proxies calls to the MCP session.

    The returned callable matches the ToolRegistry handler signature::

        async def handler(args: dict[str, Any]) -> dict[str, Any]
    """

    async def proxy_handler(args: dict[str, Any]) -> dict[str, Any]:
        if not conn.is_connected or conn.session is None:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Error: Plugin server '{conn.config.name}' is not "
                            f"connected. The server may have crashed or been "
                            f"shut down."
                        ),
                    }
                ],
                "is_error": True,
            }

        try:
            result = await conn.session.call_tool(original_tool_name, args)

            # Convert MCP SDK CallToolResult to Sidera's dict format.
            content_blocks: list[dict[str, str]] = []
            for item in result.content or []:
                if hasattr(item, "text"):
                    content_blocks.append({"type": "text", "text": item.text})
                elif hasattr(item, "data"):
                    mime = getattr(item, "mimeType", "unknown")
                    content_blocks.append({"type": "text", "text": f"[Binary content: {mime}]"})
                else:
                    content_blocks.append({"type": "text", "text": str(item)})

            if not content_blocks:
                content_blocks = [{"type": "text", "text": "(empty response)"}]

            return {
                "content": content_blocks,
                "is_error": bool(getattr(result, "isError", False)),
            }

        except Exception as exc:
            logger.error(
                "plugin.tool_proxy_error",
                tool=original_tool_name,
                server=conn.config.name,
                error=str(exc),
            )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Error: Plugin tool '{original_tool_name}' failed: {exc}",
                    }
                ],
                "is_error": True,
            }

    return proxy_handler


def register_plugin_tools(
    conn: MCPServerConnection,
    plugin_name: str,
) -> list[str]:
    """Register all tools from an MCP connection into the ToolRegistry.

    Tool names are prefixed: ``{plugin_name}__{original_name}``.
    Returns the list of registered namespaced tool names.
    """
    registry = get_global_registry()
    registered: list[str] = []

    for tool_info in conn.tools:
        original_name = tool_info["name"]
        namespaced = f"{plugin_name}__{original_name}"

        if namespaced in registry:
            logger.warning(
                "plugin.tool_name_collision",
                tool=namespaced,
                plugin=plugin_name,
            )
            continue

        handler = _build_proxy_handler(conn, original_name)
        tool_def = ToolDefinition(
            name=namespaced,
            description=f"[Plugin: {plugin_name}] {tool_info.get('description', '')}",
            input_schema=tool_info.get(
                "inputSchema",
                {"type": "object", "properties": {}, "required": []},
            ),
            handler=handler,
        )
        registry.register(tool_def)
        registered.append(namespaced)

    logger.info(
        "plugin.tools_registered",
        plugin=plugin_name,
        count=len(registered),
    )
    return registered


# ---------------------------------------------------------------------------
# Skill import
# ---------------------------------------------------------------------------


def import_plugin_skills(
    skill_dirs: list[str],
    plugin_name: str,
    target_department_id: str = "",
    target_role_id: str = "",
) -> list[str]:
    """Import SKILL.md skills from a plugin into Sidera format.

    Uses the existing :func:`import_anthropic_skill` converter.
    Returns the list of imported skill IDs.
    """
    from src.skills.anthropic_compat import import_anthropic_skill

    imported: list[str] = []
    for skill_path in skill_dirs:
        try:
            result = import_anthropic_skill(
                source=skill_path,
                target_department_id=target_department_id,
                target_role_id=target_role_id,
                new_author=f"plugin:{plugin_name}",
            )
            if result.success:
                imported.append(result.skill_id)
                logger.info(
                    "plugin.skill_imported",
                    plugin=plugin_name,
                    skill_id=result.skill_id,
                )
            else:
                logger.warning(
                    "plugin.skill_import_failed",
                    plugin=plugin_name,
                    path=skill_path,
                    errors=result.errors,
                )
        except Exception as exc:
            logger.error(
                "plugin.skill_import_error",
                plugin=plugin_name,
                path=skill_path,
                error=str(exc),
            )
    return imported


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------


async def load_plugin(
    plugin_dir: str,
    target_department_id: str = "",
    target_role_id: str = "",
) -> LoadedPlugin:
    """Load a Claude Code / Cowork plugin into Sidera.

    1. Reads the plugin manifest
    2. Connects to all MCP servers defined in ``.mcp.json``
    3. Registers discovered tools in the global ToolRegistry (namespaced)
    4. Imports skills from ``skills/`` via the Anthropic compat layer

    Args:
        plugin_dir: Path to the plugin directory.
        target_department_id: Department to assign imported skills to.
        target_role_id: Role to assign imported skills to.

    Returns:
        :class:`LoadedPlugin` with connection state and registered tools/skills.

    Raises:
        ValueError: If *plugin_dir* does not exist.
    """
    plugin_dir_path = Path(plugin_dir)
    if not plugin_dir_path.is_dir():
        raise ValueError(f"Plugin directory does not exist: {plugin_dir}")

    manifest = discover_plugin(plugin_dir)

    if manifest.name in _loaded_plugins:
        logger.warning("plugin.already_loaded", plugin=manifest.name)
        return _loaded_plugins[manifest.name]

    loaded = LoadedPlugin(manifest=manifest)

    # Connect MCP servers and register proxy tools
    for server_config in manifest.mcp_servers:
        conn = await connect_mcp_server(server_config, manifest.name)
        loaded.connections.append(conn)

        if conn.is_connected:
            tool_names = register_plugin_tools(conn, manifest.name)
            loaded.registered_tool_names.extend(tool_names)

    # Import skills
    if manifest.skill_dirs:
        skill_ids = import_plugin_skills(
            list(manifest.skill_dirs),
            manifest.name,
            target_department_id=target_department_id,
            target_role_id=target_role_id,
        )
        loaded.imported_skill_ids.extend(skill_ids)

    loaded.is_loaded = True
    _loaded_plugins[manifest.name] = loaded

    logger.info(
        "plugin.loaded",
        plugin=manifest.name,
        version=manifest.version,
        mcp_servers=len(manifest.mcp_servers),
        connected=sum(1 for c in loaded.connections if c.is_connected),
        tools=len(loaded.registered_tool_names),
        skills=len(loaded.imported_skill_ids),
    )
    return loaded


async def unload_plugin(plugin_name: str) -> None:
    """Unload a plugin: disconnect MCP servers and remove tools."""
    loaded = _loaded_plugins.pop(plugin_name, None)
    if loaded is None:
        logger.warning("plugin.not_found", plugin=plugin_name)
        return

    for conn in loaded.connections:
        await disconnect_mcp_server(conn)

    registry = get_global_registry()
    for tool_name in loaded.registered_tool_names:
        registry.remove(tool_name)

    loaded.is_loaded = False
    logger.info(
        "plugin.unloaded",
        plugin=plugin_name,
        tools_removed=len(loaded.registered_tool_names),
    )


async def unload_all_plugins() -> None:
    """Unload all loaded plugins. Called during shutdown."""
    for name in list(_loaded_plugins):
        await unload_plugin(name)


def list_plugins() -> list[LoadedPlugin]:
    """List all currently loaded plugins."""
    return list(_loaded_plugins.values())


def get_plugin(plugin_name: str) -> LoadedPlugin | None:
    """Get a loaded plugin by name."""
    return _loaded_plugins.get(plugin_name)
