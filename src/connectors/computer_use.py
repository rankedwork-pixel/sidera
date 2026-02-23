"""Anthropic Computer Use connector for Sidera.

Provides a client that orchestrates Claude's Computer Use capability —
full desktop automation via screenshots and mouse/keyboard control inside
a sandboxed Docker container.

Architecture:
    1. Docker container runs a Linux desktop (Xvfb + lightweight WM)
    2. This connector manages the container lifecycle and action execution
    3. Actions are sent to the container via HTTP API or docker exec
    4. Screenshots are captured and returned as base64 images
    5. The agent loop in ``run_computer_use_task()`` handles the full
       screenshot→action→screenshot cycle until the task is complete

The connector does NOT call the Anthropic API directly — it provides the
execution environment and action handlers.  The agent loop (see
``run_computer_use_task()``) handles the API interaction.

Security:
    - Each task gets its own isolated container (or reuses a warm one)
    - Containers have no access to host filesystem or network by default
    - Domain allowlisting via container-level firewall rules
    - Timeout enforcement prevents runaway tasks
    - All actions are logged for audit trail

Usage:
    from src.connectors.computer_use import ComputerUseConnector

    connector = ComputerUseConnector()
    session = await connector.create_session()
    screenshot = await connector.take_screenshot(session["session_id"])
    await connector.execute_action(session["session_id"], {
        "action": "left_click", "coordinate": [500, 300]
    })
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from src.config import settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ComputerUseError(Exception):
    """Base exception for Computer Use connector errors."""

    pass


class ComputerUseSessionError(ComputerUseError):
    """Session lifecycle error (create, connect, destroy)."""

    pass


class ComputerUseActionError(ComputerUseError):
    """Action execution error (click, type, screenshot)."""

    pass


class ComputerUseTimeoutError(ComputerUseError):
    """Task or action exceeded timeout."""

    pass


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

# Default display dimensions
_DEFAULT_WIDTH = 1024
_DEFAULT_HEIGHT = 768

# Max task duration (seconds)
_MAX_TASK_DURATION = 600  # 10 minutes

# Max actions per task (prevent infinite loops)
_MAX_ACTIONS_PER_TASK = 100

# Tool version for the latest models
_TOOL_VERSION_LATEST = "computer_20251124"
_BETA_FLAG_LATEST = "computer-use-2025-11-24"

# Tool version for older models
_TOOL_VERSION_LEGACY = "computer_20250124"
_BETA_FLAG_LEGACY = "computer-use-2025-01-24"

# Models that support the latest tool version
_LATEST_TOOL_MODELS = frozenset({
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-opus-4-5",
    "claude-opus-4-20250514",
    "claude-sonnet-4-20250514",
})


@dataclass
class ComputerUseSession:
    """Tracks an active computer use session."""

    session_id: str
    container_id: str | None = None
    container_url: str = ""  # HTTP endpoint for the container's action API
    display_width: int = _DEFAULT_WIDTH
    display_height: int = _DEFAULT_HEIGHT
    created_at: float = field(default_factory=time.time)
    action_count: int = 0
    total_cost_usd: float = 0.0
    is_active: bool = True


def get_tool_version(model: str) -> tuple[str, str]:
    """Get the appropriate tool version and beta flag for a model.

    Args:
        model: The Claude model identifier.

    Returns:
        Tuple of (tool_version, beta_flag).
    """
    _latest = ("opus-4-6", "sonnet-4-6", "opus-4-5", "opus-4.6", "sonnet-4.6", "opus-4.5")
    if any(m in model for m in _latest):
        return _TOOL_VERSION_LATEST, _BETA_FLAG_LATEST
    return _TOOL_VERSION_LEGACY, _BETA_FLAG_LEGACY


def build_computer_use_tools(
    model: str,
    display_width: int = _DEFAULT_WIDTH,
    display_height: int = _DEFAULT_HEIGHT,
    enable_zoom: bool = True,
) -> list[dict[str, Any]]:
    """Build the tool definitions for the Anthropic API.

    Returns the computer, text_editor, and bash tools in the format
    expected by ``client.beta.messages.create()``.

    Args:
        model: The Claude model to use (determines tool version).
        display_width: Display width in pixels.
        display_height: Display height in pixels.
        enable_zoom: Enable the zoom action (latest models only).

    Returns:
        List of tool definition dicts for the Anthropic API.
    """
    tool_version, _ = get_tool_version(model)

    computer_tool: dict[str, Any] = {
        "type": tool_version,
        "name": "computer",
        "display_width_px": display_width,
        "display_height_px": display_height,
    }

    # Enable zoom for latest tool version
    if tool_version == _TOOL_VERSION_LATEST and enable_zoom:
        computer_tool["enable_zoom"] = True

    # Text editor version follows computer tool version
    if tool_version == _TOOL_VERSION_LATEST:
        text_editor_type = "text_editor_20250728"
    else:
        text_editor_type = "text_editor_20250124"

    # Bash version
    bash_type = "bash_20250124"

    return [
        computer_tool,
        {"type": text_editor_type, "name": "str_replace_based_edit_tool"},
        {"type": bash_type, "name": "bash"},
    ]


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class ComputerUseConnector:
    """Manages computer use sessions for desktop automation.

    Handles container lifecycle, action execution, and screenshot capture
    for Anthropic's Computer Use capability.

    The connector supports two modes:
    1. **Docker mode** (production): Spins up isolated Docker containers
       with a Linux desktop environment.
    2. **HTTP mode** (development): Connects to an already-running
       computer use environment via HTTP.

    Args:
        credentials: Optional dict with ``container_url`` (for HTTP mode)
            or ``docker_image`` (for Docker mode). If omitted, values are
            read from the ``settings`` singleton.
    """

    def __init__(self, credentials: dict[str, Any] | None = None) -> None:
        creds = credentials or self._credentials_from_settings()
        self._container_url = creds.get("container_url", "")
        self._docker_image = creds.get(
            "docker_image",
            "ghcr.io/anthropics/anthropic-quickstarts:computer-use-demo-latest",
        )
        self._sessions: dict[str, ComputerUseSession] = {}
        self._http_client: httpx.AsyncClient | None = None

    @staticmethod
    def _credentials_from_settings() -> dict[str, str]:
        """Extract Computer Use config from settings."""
        return {
            "container_url": getattr(settings, "computer_use_container_url", ""),
            "docker_image": getattr(settings, "computer_use_docker_image", ""),
        }

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client for container communication."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    # -- Session lifecycle --------------------------------------------------

    async def create_session(
        self,
        display_width: int = _DEFAULT_WIDTH,
        display_height: int = _DEFAULT_HEIGHT,
    ) -> ComputerUseSession:
        """Create a new computer use session.

        In HTTP mode, connects to an existing environment.
        In Docker mode, would spin up a new container (placeholder for
        full Docker integration).

        Args:
            display_width: Display width in pixels.
            display_height: Display height in pixels.

        Returns:
            A ComputerUseSession tracking the session state.
        """
        session_id = str(uuid.uuid4())[:8]

        if self._container_url:
            # HTTP mode — connect to existing environment
            session = ComputerUseSession(
                session_id=session_id,
                container_url=self._container_url,
                display_width=display_width,
                display_height=display_height,
            )
        else:
            # Docker mode — spin up a container
            container_id = await self._start_container(
                session_id, display_width, display_height
            )
            session = ComputerUseSession(
                session_id=session_id,
                container_id=container_id,
                container_url="http://localhost:8080",  # Container port
                display_width=display_width,
                display_height=display_height,
            )

        self._sessions[session_id] = session
        logger.info(
            "computer_use.session_created",
            session_id=session_id,
            display=f"{display_width}x{display_height}",
            mode="http" if self._container_url else "docker",
        )
        return session

    async def _start_container(
        self,
        session_id: str,
        width: int,
        height: int,
    ) -> str:
        """Start a Docker container for the session.

        This is a placeholder that documents the Docker integration pattern.
        In production, this would use the Docker SDK or docker CLI.

        Args:
            session_id: Unique session identifier.
            width: Display width.
            height: Display height.

        Returns:
            Container ID string.
        """
        # Production implementation would:
        # 1. docker run -d --name sidera-cu-{session_id} \
        #    -p {port}:8080 -e WIDTH={width} -e HEIGHT={height} \
        #    --memory=2g --cpus=1 \
        #    {self._docker_image}
        # 2. Wait for container health check
        # 3. Return container ID

        logger.info(
            "computer_use.container_start_placeholder",
            session_id=session_id,
            image=self._docker_image,
            display=f"{width}x{height}",
        )

        # For now, return a placeholder — Docker integration requires
        # the Docker SDK or CLI access on the host
        return f"placeholder-{session_id}"

    async def destroy_session(self, session_id: str) -> None:
        """Destroy a computer use session and clean up resources.

        Args:
            session_id: The session to destroy.
        """
        session = self._sessions.pop(session_id, None)
        if session is None:
            return

        session.is_active = False

        if session.container_id and not session.container_id.startswith("placeholder"):
            # Production: docker rm -f {container_id}
            logger.info(
                "computer_use.container_destroyed",
                session_id=session_id,
                container_id=session.container_id,
            )
        else:
            logger.info("computer_use.session_destroyed", session_id=session_id)

    def get_session(self, session_id: str) -> ComputerUseSession | None:
        """Get an active session by ID."""
        return self._sessions.get(session_id)

    # -- Action execution ---------------------------------------------------

    async def take_screenshot(self, session_id: str) -> dict[str, Any]:
        """Capture a screenshot from the session's display.

        Args:
            session_id: The session to screenshot.

        Returns:
            Dict with 'image_base64' (PNG), 'width', 'height'.
        """
        session = self._sessions.get(session_id)
        if not session or not session.is_active:
            raise ComputerUseSessionError(f"No active session: {session_id}")

        try:
            client = await self._get_http_client()
            resp = await client.post(
                f"{session.container_url}/screenshot",
                json={
                    "display_width": session.display_width,
                    "display_height": session.display_height,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            return {
                "image_base64": data.get("image", ""),
                "width": session.display_width,
                "height": session.display_height,
            }

        except httpx.HTTPError as exc:
            raise ComputerUseActionError(f"Screenshot failed: {exc}") from exc

    async def execute_action(
        self,
        session_id: str,
        action: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a computer use action in the session.

        Args:
            session_id: The session to act in.
            action: The action dict from Claude's tool_use response.
                Must include 'action' key (screenshot, left_click, type, etc.)

        Returns:
            Dict with the action result.  For screenshot actions, includes
            'image_base64'.  For other actions, includes 'success' flag.
        """
        session = self._sessions.get(session_id)
        if not session or not session.is_active:
            raise ComputerUseSessionError(f"No active session: {session_id}")

        if session.action_count >= _MAX_ACTIONS_PER_TASK:
            raise ComputerUseTimeoutError(
                f"Action limit exceeded ({_MAX_ACTIONS_PER_TASK})"
            )

        action_type = action.get("action", "")

        try:
            client = await self._get_http_client()
            resp = await client.post(
                f"{session.container_url}/action",
                json=action,
                timeout=30.0,
            )
            resp.raise_for_status()
            result = resp.json()

            session.action_count += 1

            logger.info(
                "computer_use.action_executed",
                session_id=session_id,
                action=action_type,
                action_count=session.action_count,
            )

            return result

        except httpx.HTTPError as exc:
            raise ComputerUseActionError(
                f"Action '{action_type}' failed: {exc}"
            ) from exc

    # -- High-level agent loop ----------------------------------------------

    async def run_task(
        self,
        task_prompt: str,
        model: str | None = None,
        max_iterations: int = 50,
        timeout: int = 300,
        thinking_budget: int = 10000,
        custom_tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Run a complete computer use task via the Anthropic API.

        This is the main entry point for executing a computer use task.
        It creates a session, runs the agent loop (screenshot→Claude→action),
        and returns the result.

        Args:
            task_prompt: What the agent should do.
            model: Claude model to use (default: settings.model_standard).
            max_iterations: Max screenshot→action cycles (default 50).
            timeout: Total task timeout in seconds (default 300).
            thinking_budget: Token budget for extended thinking.
            custom_tools: Additional MCP tools to provide alongside
                computer use tools (e.g., domain-specific tools).

        Returns:
            Dict with keys: output, action_count, cost_usd, session_id,
            screenshots (list of base64 images for key moments).
        """
        try:
            import anthropic
        except ImportError:
            raise ComputerUseError(
                "anthropic SDK is required. Install with: pip install anthropic"
            )

        model = model or settings.model_standard
        tool_version, beta_flag = get_tool_version(model)

        # Create session
        session = await self.create_session()

        try:
            # Build tools
            tools = build_computer_use_tools(
                model=model,
                display_width=session.display_width,
                display_height=session.display_height,
            )
            if custom_tools:
                tools.extend(custom_tools)

            # Initialize conversation
            messages: list[dict[str, Any]] = [
                {"role": "user", "content": task_prompt},
            ]

            client = anthropic.AsyncAnthropic()
            key_screenshots: list[str] = []
            output_text = ""
            total_input_tokens = 0
            total_output_tokens = 0
            start_time = time.time()

            for iteration in range(max_iterations):
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    output_text += f"\n[Task timed out after {timeout}s]"
                    break

                # Call Claude
                api_kwargs: dict[str, Any] = {
                    "model": model,
                    "max_tokens": 4096,
                    "tools": tools,
                    "messages": messages,
                    "betas": [beta_flag],
                }

                # Add thinking if budget > 0
                if thinking_budget > 0:
                    api_kwargs["thinking"] = {
                        "type": "enabled",
                        "budget_tokens": thinking_budget,
                    }
                    api_kwargs["max_tokens"] = max(4096, thinking_budget + 4096)

                response = await client.beta.messages.create(**api_kwargs)

                # Track costs
                total_input_tokens += response.usage.input_tokens
                total_output_tokens += response.usage.output_tokens

                # Process response
                response_content = response.content
                messages.append({"role": "assistant", "content": response_content})

                # Extract tool calls and text
                tool_results: list[dict[str, Any]] = []

                for block in response_content:
                    if hasattr(block, "type"):
                        if block.type == "text":
                            output_text += block.text
                        elif block.type == "tool_use":
                            # Execute the tool action
                            result = await self._handle_tool_call(
                                session, block.name, block.input
                            )

                            # Capture key screenshots
                            if (
                                block.input.get("action") == "screenshot"
                                and isinstance(result, dict)
                                and result.get("type") == "image"
                            ):
                                img = result.get("data", "")
                                if img and len(key_screenshots) < 5:
                                    key_screenshots.append(img)

                            # Format result for Claude
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result if isinstance(result, list) else [result],
                            })

                # If no tool calls, task is done
                if not tool_results:
                    break

                # Continue conversation with tool results
                messages.append({"role": "user", "content": tool_results})

            # Estimate cost
            cost_usd = (total_input_tokens * 3.0 / 1_000_000) + (
                total_output_tokens * 15.0 / 1_000_000
            )

            return {
                "output": output_text.strip(),
                "action_count": session.action_count,
                "iterations": iteration + 1 if "iteration" in dir() else 0,
                "cost_usd": round(cost_usd, 4),
                "session_id": session.session_id,
                "screenshots": key_screenshots,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
            }

        finally:
            await self.destroy_session(session.session_id)

    async def _handle_tool_call(
        self,
        session: ComputerUseSession,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> Any:
        """Handle a tool call from Claude during the agent loop.

        Routes to the appropriate handler based on tool name:
        - 'computer' → execute_action / take_screenshot
        - 'bash' → execute bash command in container
        - 'str_replace_based_edit_tool' → execute text editor in container

        Args:
            session: The active session.
            tool_name: The tool being called.
            tool_input: The tool input from Claude.

        Returns:
            Tool result in the format expected by the Anthropic API.
        """
        try:
            if tool_name == "computer":
                action_type = tool_input.get("action", "")

                if action_type == "screenshot":
                    screenshot = await self.take_screenshot(session.session_id)
                    return {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": screenshot["image_base64"],
                        },
                        "data": screenshot["image_base64"],
                    }
                else:
                    await self.execute_action(session.session_id, tool_input)

                    # After most actions, auto-capture a screenshot
                    if action_type not in ("zoom",):
                        await asyncio.sleep(0.5)  # Let UI settle
                        screenshot = await self.take_screenshot(session.session_id)
                        return [
                            {"type": "text", "text": f"Action '{action_type}' executed."},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": screenshot["image_base64"],
                                },
                            },
                        ]
                    return {"type": "text", "text": f"Action '{action_type}' executed."}

            elif tool_name == "bash":
                # Execute bash command in container
                command = tool_input.get("command", "")
                client = await self._get_http_client()
                resp = await client.post(
                    f"{session.container_url}/bash",
                    json={"command": command},
                    timeout=30.0,
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "type": "text",
                    "text": data.get("output", ""),
                }

            elif tool_name == "str_replace_based_edit_tool":
                # Execute text editor command in container
                client = await self._get_http_client()
                resp = await client.post(
                    f"{session.container_url}/edit",
                    json=tool_input,
                    timeout=30.0,
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "type": "text",
                    "text": data.get("output", "Command executed."),
                }

            else:
                return {
                    "type": "text",
                    "text": f"Unknown tool: {tool_name}",
                }

        except Exception as exc:
            logger.error(
                "computer_use.tool_call_error",
                session_id=session.session_id,
                tool=tool_name,
                error=str(exc),
            )
            return {
                "type": "text",
                "text": f"Error: {exc}",
            }

    async def close(self) -> None:
        """Clean up all sessions and close HTTP client."""
        session_ids = list(self._sessions.keys())
        for sid in session_ids:
            await self.destroy_session(sid)

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
