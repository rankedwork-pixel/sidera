"""Tests for the Computer Use connector (src/connectors/computer_use.py).

Covers:
    - get_tool_version()      — model→version mapping
    - build_computer_use_tools() — tool definition generation
    - ComputerUseSession      — dataclass defaults
    - ComputerUseConnector construction and credential loading
    - create_session()        — HTTP mode and Docker mode
    - destroy_session()       — cleanup and missing session
    - get_session()           — lookup by ID
    - take_screenshot()       — mock HTTP, error handling
    - execute_action()        — mock HTTP, action limit enforcement
    - run_task()              — full Anthropic API loop (mocked)
    - _handle_tool_call()     — routing for computer/bash/text_editor
    - close()                 — cleanup all sessions

All external calls (httpx, anthropic SDK) are mocked.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.connectors.computer_use import (
    _DEFAULT_HEIGHT,
    _DEFAULT_WIDTH,
    _MAX_ACTIONS_PER_TASK,
    _TOOL_VERSION_LATEST,
    _TOOL_VERSION_LEGACY,
    ComputerUseActionError,
    ComputerUseConnector,
    ComputerUseError,
    ComputerUseSession,
    ComputerUseSessionError,
    ComputerUseTimeoutError,
    build_computer_use_tools,
    get_tool_version,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def connector_http():
    """Create a ComputerUseConnector in HTTP mode."""
    return ComputerUseConnector(credentials={
        "container_url": "http://localhost:8080",
    })


@pytest.fixture
def connector_docker():
    """Create a ComputerUseConnector in Docker mode."""
    return ComputerUseConnector(credentials={
        "container_url": "",
        "docker_image": "test-image:latest",
    })


def _mock_httpx_response(status_code: int = 200, json_data: dict | None = None):
    """Build a fake httpx.Response."""
    import json as _json

    content = _json.dumps(json_data or {}).encode()
    return httpx.Response(
        status_code=status_code,
        content=content,
        headers={"content-type": "application/json"},
        request=httpx.Request("POST", "http://localhost:8080/test"),
    )


# ---------------------------------------------------------------------------
# get_tool_version
# ---------------------------------------------------------------------------


class TestGetToolVersion:
    """Tests for model-to-tool-version mapping."""

    def test_opus_4_6_gets_latest(self):
        """Opus 4.6 should use the latest tool version."""
        version, beta = get_tool_version("claude-opus-4-6")
        assert version == _TOOL_VERSION_LATEST

    def test_sonnet_4_6_gets_latest(self):
        """Sonnet 4.6 should use the latest tool version."""
        version, beta = get_tool_version("claude-sonnet-4-6")
        assert version == _TOOL_VERSION_LATEST

    def test_opus_4_5_gets_latest(self):
        """Opus 4.5 should use the latest tool version."""
        version, beta = get_tool_version("claude-opus-4-5")
        assert version == _TOOL_VERSION_LATEST

    def test_opus_4_dot_6_gets_latest(self):
        """Model with dot notation should also use latest."""
        version, _ = get_tool_version("claude-opus-4.6")
        assert version == _TOOL_VERSION_LATEST

    def test_sonnet_4_dot_6_gets_latest(self):
        """Sonnet with dot notation should also use latest."""
        version, _ = get_tool_version("claude-sonnet-4.6")
        assert version == _TOOL_VERSION_LATEST

    def test_sonnet_3_5_gets_legacy(self):
        """Sonnet 3.5 should use the legacy tool version."""
        version, beta = get_tool_version("claude-3-5-sonnet-20241022")
        assert version == _TOOL_VERSION_LEGACY

    def test_haiku_gets_legacy(self):
        """Haiku should use the legacy tool version."""
        version, beta = get_tool_version("claude-3-5-haiku-20241022")
        assert version == _TOOL_VERSION_LEGACY

    def test_unknown_model_gets_legacy(self):
        """Unknown models should default to legacy."""
        version, _ = get_tool_version("some-future-model")
        assert version == _TOOL_VERSION_LEGACY

    def test_returns_tuple(self):
        """Should return a (version, beta_flag) tuple."""
        result = get_tool_version("claude-opus-4-6")
        assert isinstance(result, tuple)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# build_computer_use_tools
# ---------------------------------------------------------------------------


class TestBuildComputerUseTools:
    """Tests for tool definition builder."""

    def test_returns_three_tools(self):
        """Should return exactly 3 tools: computer, text_editor, bash."""
        tools = build_computer_use_tools("claude-opus-4-6")
        assert len(tools) == 3

    def test_computer_tool_has_dimensions(self):
        """Computer tool should include display dimensions."""
        tools = build_computer_use_tools("claude-opus-4-6", 1920, 1080)
        computer = tools[0]
        assert computer["name"] == "computer"
        assert computer["display_width_px"] == 1920
        assert computer["display_height_px"] == 1080

    def test_latest_model_has_zoom(self):
        """Latest models should have enable_zoom on computer tool."""
        tools = build_computer_use_tools("claude-opus-4-6")
        computer = tools[0]
        assert computer.get("enable_zoom") is True

    def test_latest_model_zoom_disabled(self):
        """Zoom can be disabled via parameter."""
        tools = build_computer_use_tools("claude-opus-4-6", enable_zoom=False)
        computer = tools[0]
        assert "enable_zoom" not in computer

    def test_legacy_model_no_zoom(self):
        """Legacy models should not have enable_zoom."""
        tools = build_computer_use_tools("claude-3-5-sonnet-20241022")
        computer = tools[0]
        assert "enable_zoom" not in computer

    def test_latest_text_editor_version(self):
        """Latest models should use text_editor_20250728."""
        tools = build_computer_use_tools("claude-opus-4-6")
        editor = tools[1]
        assert editor["type"] == "text_editor_20250728"
        assert editor["name"] == "str_replace_based_edit_tool"

    def test_legacy_text_editor_version(self):
        """Legacy models should use text_editor_20250124."""
        tools = build_computer_use_tools("claude-3-5-sonnet-20241022")
        editor = tools[1]
        assert editor["type"] == "text_editor_20250124"

    def test_bash_tool(self):
        """Bash tool should always use bash_20250124."""
        tools = build_computer_use_tools("claude-opus-4-6")
        bash = tools[2]
        assert bash["type"] == "bash_20250124"
        assert bash["name"] == "bash"

    def test_default_dimensions(self):
        """Default dimensions should be used if not specified."""
        tools = build_computer_use_tools("claude-opus-4-6")
        computer = tools[0]
        assert computer["display_width_px"] == _DEFAULT_WIDTH
        assert computer["display_height_px"] == _DEFAULT_HEIGHT


# ---------------------------------------------------------------------------
# ComputerUseSession
# ---------------------------------------------------------------------------


class TestComputerUseSession:
    """Tests for the session dataclass."""

    def test_defaults(self):
        """Session should have sensible defaults."""
        session = ComputerUseSession(session_id="abc123")
        assert session.session_id == "abc123"
        assert session.container_id is None
        assert session.container_url == ""
        assert session.display_width == _DEFAULT_WIDTH
        assert session.display_height == _DEFAULT_HEIGHT
        assert session.action_count == 0
        assert session.total_cost_usd == 0.0
        assert session.is_active is True

    def test_created_at_is_recent(self):
        """created_at should be close to current time."""
        before = time.time()
        session = ComputerUseSession(session_id="test")
        after = time.time()
        assert before <= session.created_at <= after


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    """Tests for ComputerUseConnector initialization."""

    def test_http_mode_credentials(self, connector_http):
        """HTTP mode should store container_url."""
        assert connector_http._container_url == "http://localhost:8080"

    def test_docker_mode_credentials(self, connector_docker):
        """Docker mode should store docker_image."""
        assert connector_docker._docker_image == "test-image:latest"
        assert connector_docker._container_url == ""

    def test_credentials_from_settings(self):
        """Should read credentials from settings when none provided."""
        with patch("src.connectors.computer_use.settings") as mock_settings:
            mock_settings.computer_use_container_url = "http://settings:8080"
            mock_settings.computer_use_docker_image = "settings-image:v1"

            conn = ComputerUseConnector()
            assert conn._container_url == "http://settings:8080"

    def test_default_docker_image(self):
        """Default docker image should be the Anthropic quickstarts image."""
        conn = ComputerUseConnector(credentials={})
        assert "anthropic" in conn._docker_image.lower()

    def test_starts_with_no_sessions(self, connector_http):
        """Should start with empty sessions dict."""
        assert len(connector_http._sessions) == 0


# ---------------------------------------------------------------------------
# create_session / destroy_session / get_session
# ---------------------------------------------------------------------------


class TestSessionLifecycle:
    """Tests for session creation, retrieval, and destruction."""

    @pytest.mark.asyncio
    async def test_create_http_session(self, connector_http):
        """HTTP mode should create session with container_url."""
        session = await connector_http.create_session()

        assert session.session_id is not None
        assert session.container_url == "http://localhost:8080"
        assert session.container_id is None
        assert session.is_active is True
        assert session.session_id in connector_http._sessions

    @pytest.mark.asyncio
    async def test_create_docker_session(self, connector_docker):
        """Docker mode should create session with placeholder container_id."""
        session = await connector_docker.create_session()

        assert session.container_id is not None
        assert session.container_id.startswith("placeholder-")
        assert session.container_url == "http://localhost:8080"

    @pytest.mark.asyncio
    async def test_create_session_custom_dimensions(self, connector_http):
        """Custom display dimensions should be stored."""
        session = await connector_http.create_session(1920, 1080)

        assert session.display_width == 1920
        assert session.display_height == 1080

    @pytest.mark.asyncio
    async def test_get_session(self, connector_http):
        """get_session should return the session by ID."""
        session = await connector_http.create_session()
        retrieved = connector_http.get_session(session.session_id)
        assert retrieved is session

    @pytest.mark.asyncio
    async def test_get_nonexistent_session(self, connector_http):
        """get_session with unknown ID should return None."""
        assert connector_http.get_session("nonexistent") is None

    @pytest.mark.asyncio
    async def test_destroy_session(self, connector_http):
        """destroy_session should remove session and mark inactive."""
        session = await connector_http.create_session()
        sid = session.session_id

        await connector_http.destroy_session(sid)

        assert connector_http.get_session(sid) is None
        assert session.is_active is False

    @pytest.mark.asyncio
    async def test_destroy_nonexistent_session(self, connector_http):
        """Destroying a nonexistent session should be a no-op."""
        await connector_http.destroy_session("nonexistent")  # Should not raise

    @pytest.mark.asyncio
    async def test_multiple_sessions(self, connector_http):
        """Should support multiple concurrent sessions."""
        s1 = await connector_http.create_session()
        s2 = await connector_http.create_session()

        assert s1.session_id != s2.session_id
        assert len(connector_http._sessions) == 2


# ---------------------------------------------------------------------------
# take_screenshot
# ---------------------------------------------------------------------------


class TestTakeScreenshot:
    """Tests for screenshot capture."""

    @pytest.mark.asyncio
    async def test_successful_screenshot(self, connector_http):
        """Should return base64 image data."""
        session = await connector_http.create_session()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            return_value=_mock_httpx_response(200, {"image": "base64data=="})
        )
        connector_http._http_client = mock_client

        result = await connector_http.take_screenshot(session.session_id)

        assert result["image_base64"] == "base64data=="
        assert result["width"] == _DEFAULT_WIDTH
        assert result["height"] == _DEFAULT_HEIGHT

    @pytest.mark.asyncio
    async def test_screenshot_no_active_session(self, connector_http):
        """Should raise for nonexistent session."""
        with pytest.raises(ComputerUseSessionError, match="No active session"):
            await connector_http.take_screenshot("nonexistent")

    @pytest.mark.asyncio
    async def test_screenshot_inactive_session(self, connector_http):
        """Should raise for inactive session."""
        session = await connector_http.create_session()
        session.is_active = False

        with pytest.raises(ComputerUseSessionError):
            await connector_http.take_screenshot(session.session_id)

    @pytest.mark.asyncio
    async def test_screenshot_http_error(self, connector_http):
        """HTTP errors should raise ComputerUseActionError."""
        session = await connector_http.create_session()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        connector_http._http_client = mock_client

        with pytest.raises(ComputerUseActionError, match="Screenshot failed"):
            await connector_http.take_screenshot(session.session_id)


# ---------------------------------------------------------------------------
# execute_action
# ---------------------------------------------------------------------------


class TestExecuteAction:
    """Tests for action execution."""

    @pytest.mark.asyncio
    async def test_successful_action(self, connector_http):
        """Should execute action and increment counter."""
        session = await connector_http.create_session()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            return_value=_mock_httpx_response(200, {"success": True})
        )
        connector_http._http_client = mock_client

        result = await connector_http.execute_action(
            session.session_id, {"action": "left_click", "coordinate": [500, 300]}
        )

        assert result["success"] is True
        assert session.action_count == 1

    @pytest.mark.asyncio
    async def test_action_increments_counter(self, connector_http):
        """Each action should increment the counter."""
        session = await connector_http.create_session()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            return_value=_mock_httpx_response(200, {"success": True})
        )
        connector_http._http_client = mock_client

        await connector_http.execute_action(session.session_id, {"action": "left_click"})
        await connector_http.execute_action(session.session_id, {"action": "type"})

        assert session.action_count == 2

    @pytest.mark.asyncio
    async def test_action_limit_enforced(self, connector_http):
        """Should raise when action limit is exceeded."""
        session = await connector_http.create_session()
        session.action_count = _MAX_ACTIONS_PER_TASK

        with pytest.raises(ComputerUseTimeoutError, match="Action limit exceeded"):
            await connector_http.execute_action(
                session.session_id, {"action": "left_click"}
            )

    @pytest.mark.asyncio
    async def test_action_no_active_session(self, connector_http):
        """Should raise for nonexistent session."""
        with pytest.raises(ComputerUseSessionError):
            await connector_http.execute_action("nope", {"action": "left_click"})

    @pytest.mark.asyncio
    async def test_action_http_error(self, connector_http):
        """HTTP errors should raise ComputerUseActionError."""
        session = await connector_http.create_session()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        connector_http._http_client = mock_client

        with pytest.raises(ComputerUseActionError):
            await connector_http.execute_action(
                session.session_id, {"action": "left_click"}
            )


# ---------------------------------------------------------------------------
# _handle_tool_call
# ---------------------------------------------------------------------------


class TestHandleToolCall:
    """Tests for tool call routing."""

    @pytest.mark.asyncio
    async def test_computer_screenshot(self, connector_http):
        """computer tool with screenshot action should return image."""
        session = await connector_http.create_session()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            return_value=_mock_httpx_response(200, {"image": "screenshot_data"})
        )
        connector_http._http_client = mock_client

        result = await connector_http._handle_tool_call(
            session, "computer", {"action": "screenshot"}
        )

        assert result["type"] == "image"
        assert result["data"] == "screenshot_data"

    @pytest.mark.asyncio
    async def test_computer_click_returns_action_and_screenshot(self, connector_http):
        """computer tool with click action should return action text + screenshot."""
        session = await connector_http.create_session()

        mock_client = AsyncMock()
        # First call: execute_action, second call: take_screenshot
        mock_client.post = AsyncMock(
            side_effect=[
                _mock_httpx_response(200, {"success": True}),
                _mock_httpx_response(200, {"image": "after_click_screenshot"}),
            ]
        )
        connector_http._http_client = mock_client

        with patch("src.connectors.computer_use.asyncio.sleep", new_callable=AsyncMock):
            result = await connector_http._handle_tool_call(
                session, "computer", {"action": "left_click", "coordinate": [100, 200]}
            )

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["type"] == "text"
        assert "left_click" in result[0]["text"]
        assert result[1]["type"] == "image"

    @pytest.mark.asyncio
    async def test_computer_zoom_no_auto_screenshot(self, connector_http):
        """zoom action should not auto-capture a screenshot."""
        session = await connector_http.create_session()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            return_value=_mock_httpx_response(200, {"success": True})
        )
        connector_http._http_client = mock_client

        result = await connector_http._handle_tool_call(
            session, "computer", {"action": "zoom"}
        )

        # Should return text only, not a list
        assert isinstance(result, dict)
        assert result["type"] == "text"
        assert "zoom" in result["text"]

    @pytest.mark.asyncio
    async def test_bash_tool(self, connector_http):
        """bash tool should POST to /bash endpoint."""
        session = await connector_http.create_session()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            return_value=_mock_httpx_response(200, {"output": "hello world"})
        )
        connector_http._http_client = mock_client

        result = await connector_http._handle_tool_call(
            session, "bash", {"command": "echo hello world"}
        )

        assert result["type"] == "text"
        assert result["text"] == "hello world"

    @pytest.mark.asyncio
    async def test_text_editor_tool(self, connector_http):
        """str_replace_based_edit_tool should POST to /edit endpoint."""
        session = await connector_http.create_session()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            return_value=_mock_httpx_response(200, {"output": "File edited."})
        )
        connector_http._http_client = mock_client

        result = await connector_http._handle_tool_call(
            session, "str_replace_based_edit_tool", {"command": "view", "path": "/tmp/file.py"}
        )

        assert result["type"] == "text"
        assert "edited" in result["text"].lower() or "File edited" in result["text"]

    @pytest.mark.asyncio
    async def test_unknown_tool(self, connector_http):
        """Unknown tool should return error text."""
        session = await connector_http.create_session()

        result = await connector_http._handle_tool_call(
            session, "unknown_tool", {}
        )

        assert result["type"] == "text"
        assert "Unknown tool" in result["text"]

    @pytest.mark.asyncio
    async def test_tool_call_error_handled(self, connector_http):
        """Errors during tool call should return error text, not raise."""
        session = await connector_http.create_session()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("failed"))
        connector_http._http_client = mock_client

        result = await connector_http._handle_tool_call(
            session, "bash", {"command": "echo hi"}
        )

        assert result["type"] == "text"
        assert "Error" in result["text"]


# ---------------------------------------------------------------------------
# run_task
# ---------------------------------------------------------------------------


class TestRunTask:
    """Tests for the high-level agent loop."""

    @pytest.mark.asyncio
    async def test_anthropic_not_installed(self, connector_http):
        """Should raise ComputerUseError if anthropic is not importable."""
        with patch("builtins.__import__", side_effect=ImportError("no anthropic")):
            with pytest.raises(ComputerUseError, match="anthropic SDK is required"):
                await connector_http.run_task("do something")

    @pytest.mark.asyncio
    async def test_simple_text_response(self, connector_http):
        """Agent that responds with text only (no tool calls) should return output."""
        mock_response = MagicMock()
        mock_response.usage = SimpleNamespace(input_tokens=100, output_tokens=50)
        text_block = SimpleNamespace(type="text", text="Task completed successfully.")
        mock_response.content = [text_block]

        mock_client_instance = MagicMock()
        mock_client_instance.beta.messages.create = AsyncMock(return_value=mock_response)

        mock_anthropic = MagicMock()
        mock_anthropic.AsyncAnthropic.return_value = mock_client_instance

        # Also mock the http client for session creation
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(
            return_value=_mock_httpx_response(200, {"image": "data"})
        )
        connector_http._http_client = mock_http

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            result = await connector_http.run_task(
                "Click the button",
                model="claude-opus-4-6",
            )

        assert result["output"] == "Task completed successfully."
        assert result["cost_usd"] >= 0
        assert result["session_id"] is not None

    @pytest.mark.asyncio
    async def test_task_timeout(self, connector_http):
        """Task exceeding timeout should stop and include timeout message."""
        # Make time.time() jump forward to simulate timeout
        call_count = 0
        real_time = time.time

        def mock_time():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return real_time()
            return real_time() + 999  # Jump way past timeout

        text_block = SimpleNamespace(type="text", text="Working...")
        tool_block = SimpleNamespace(
            type="tool_use", name="computer",
            input={"action": "screenshot"}, id="tool_1"
        )

        mock_response = MagicMock()
        mock_response.usage = SimpleNamespace(input_tokens=100, output_tokens=50)
        mock_response.content = [text_block, tool_block]

        mock_client_instance = MagicMock()
        mock_client_instance.beta.messages.create = AsyncMock(return_value=mock_response)

        mock_anthropic = MagicMock()
        mock_anthropic.AsyncAnthropic.return_value = mock_client_instance

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(
            return_value=_mock_httpx_response(200, {"image": "data"})
        )
        connector_http._http_client = mock_http

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            with patch("src.connectors.computer_use.time.time", side_effect=mock_time):
                result = await connector_http.run_task(
                    "do something",
                    model="claude-opus-4-6",
                    timeout=10,
                )

        assert "timed out" in result["output"].lower()

    @pytest.mark.asyncio
    async def test_session_cleaned_up_on_error(self, connector_http):
        """Session should be destroyed even if an error occurs."""
        mock_anthropic = MagicMock()
        mock_client_instance = MagicMock()
        mock_client_instance.beta.messages.create = AsyncMock(
            side_effect=RuntimeError("API error")
        )
        mock_anthropic.AsyncAnthropic.return_value = mock_client_instance

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(
            return_value=_mock_httpx_response(200, {"image": "data"})
        )
        connector_http._http_client = mock_http

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            with pytest.raises(RuntimeError):
                await connector_http.run_task(
                    "do something", model="claude-opus-4-6",
                )

        # Session should have been cleaned up in finally block
        assert len(connector_http._sessions) == 0

    @pytest.mark.asyncio
    async def test_cost_calculation(self, connector_http):
        """Cost should be calculated from token usage."""
        mock_response = MagicMock()
        mock_response.usage = SimpleNamespace(input_tokens=1000, output_tokens=500)
        text_block = SimpleNamespace(type="text", text="Done.")
        mock_response.content = [text_block]

        mock_client_instance = MagicMock()
        mock_client_instance.beta.messages.create = AsyncMock(return_value=mock_response)

        mock_anthropic = MagicMock()
        mock_anthropic.AsyncAnthropic.return_value = mock_client_instance

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(
            return_value=_mock_httpx_response(200, {"image": "data"})
        )
        connector_http._http_client = mock_http

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            result = await connector_http.run_task(
                "do something", model="claude-opus-4-6",
            )

        # Cost = (1000 * 3.0 / 1M) + (500 * 15.0 / 1M) = 0.003 + 0.0075 = 0.0105
        assert result["cost_usd"] == pytest.approx(0.0105, abs=0.001)
        assert result["input_tokens"] == 1000
        assert result["output_tokens"] == 500


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


class TestClose:
    """Tests for connector cleanup."""

    @pytest.mark.asyncio
    async def test_close_destroys_all_sessions(self, connector_http):
        """close() should destroy all sessions."""
        await connector_http.create_session()
        await connector_http.create_session()
        assert len(connector_http._sessions) == 2

        await connector_http.close()

        assert len(connector_http._sessions) == 0

    @pytest.mark.asyncio
    async def test_close_closes_http_client(self, connector_http):
        """close() should close the HTTP client."""
        mock_client = AsyncMock()
        connector_http._http_client = mock_client

        await connector_http.close()

        mock_client.aclose.assert_called_once()
        assert connector_http._http_client is None

    @pytest.mark.asyncio
    async def test_close_no_client(self, connector_http):
        """close() with no HTTP client should not error."""
        await connector_http.close()  # Should not raise
