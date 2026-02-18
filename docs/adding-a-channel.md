# Adding a New Connector to Sidera

This guide walks through adding a new API connector (e.g., Zendesk, GitHub, Shopify, TikTok Ads) to Sidera. Follow each step in order — by the end you'll have a fully wired connector with API client, MCP tools, OAuth, tests, and caching.

**Time estimate:** 2-4 hours for an experienced developer familiar with the platform's API.

**Template files:** Copy the skeletons from `src/templates/` and replace the placeholders:
- `__CHANNEL__` → lowercase name (e.g., `tiktok`)
- `__Channel__` → PascalCase name (e.g., `TikTok`)
- `__CHANNEL_UPPER__` → UPPER_CASE name (e.g., `TIKTOK`)

---

## Step 1: Create the Connector

**File:** `src/connectors/__CHANNEL__.py`
**Template:** `src/templates/connector_template.py`

The connector wraps the platform's SDK/API and exposes clean, dict-based read-only methods.

### Pattern to follow

Look at `src/connectors/meta.py` as the cleanest reference.

### Checklist

- [ ] Custom exception classes: `__Channel__ConnectorError`, `__Channel__AuthError`
- [ ] Class with `__init__(self, credentials=None)`
- [ ] `_credentials_from_settings()` — reads from `settings` singleton
- [ ] `_build_client()` or `_init_api()` — creates the SDK client
- [ ] structlog logger with `connector="__CHANNEL__"` binding
- [ ] 5-7 public read-only methods returning `list[dict]` or `dict | None`:
  - `get_accounts()` or `get_ad_accounts()`
  - `get_account_info(account_id)`
  - `get_campaigns(account_id)`
  - `get_campaign_metrics(account_id, campaign_id, start_date, end_date)`
  - `get_account_metrics(account_id, start_date, end_date)`
  - Platform-specific methods as needed (e.g., `get_campaign_insights`, `get_account_activity`)
- [ ] `@cached` decorators on read methods (import from `src.cache.decorators`)
- [ ] Error handling: catch platform-specific exceptions, raise auth errors as `__Channel__AuthError`, log and return empty results for transient failures
- [ ] All monetary values normalized to standard units (dollars, not micros/cents)

### Key conventions

- Google Ads returns monetary values in **micros** (÷ 1,000,000)
- Meta returns spend as **string decimal** and budgets as **string cents** (÷ 100)
- Your platform will have its own quirks — document the conversion in comments

---

## Step 2: Create the MCP Server

**File:** `src/mcp_servers/__CHANNEL__.py`
**Template:** `src/templates/mcp_server_template.py`

MCP tools are the interface between the Claude agent and your connector. Each tool has a JSON schema describing its inputs and returns formatted text the agent can reason about.

### Checklist

- [ ] Import shared helpers from `src.mcp_servers.helpers` (`text_response`, `error_response`, `format_currency`, `format_number`, `format_percentage`)
- [ ] `_get_connector()` factory function
- [ ] 5 tool functions with `@tool` decorator:
  1. `list___CHANNEL___accounts` — List connected accounts
  2. `get___CHANNEL___campaigns` — Get campaigns for an account
  3. `get___CHANNEL___performance` — Get performance metrics for a date range
  4. `get___CHANNEL___insights` — Get platform-specific breakdown/insights
  5. `get___CHANNEL___account_activity` — Get recent account changes
- [ ] Each tool has:
  - `name` — snake_case tool name
  - `description` — 2-3 sentences explaining what the tool does and when to use it
  - `input_schema` — JSON Schema dict with required/optional properties
  - Async handler function that validates input, calls connector, formats text output
- [ ] `create___CHANNEL___tools()` — returns `list[SdkMcpTool]`
- [ ] `create___CHANNEL___mcp_server()` — returns `McpSdkServerConfig`

### Description writing tips

Tool descriptions are what the agent sees to decide which tool to call. Make them:
- Start with what the tool returns ("Gets all campaigns...")
- Mention when to use it ("Use this to understand account structure before...")
- Include example input formats ("e.g. 'act_123456789'")

---

## Step 3: Update Config

**File:** `src/config.py`

Add credential fields to the `Settings` class:

```python
# __Channel__ API
__CHANNEL___api_key: str = ""
__CHANNEL___client_id: str = ""
__CHANNEL___client_secret: str = ""
__CHANNEL___access_token: str = ""
```

All fields should default to empty string so the app starts even without this channel configured.

---

## Step 4: Wire Into the Agent

### 4a. MCP server registration

**File:** `src/agent/core.py`

Add your MCP server to `_build_mcp_servers()`:

```python
from src.mcp_servers.__CHANNEL__ import create___CHANNEL___mcp_server

# In _build_mcp_servers():
servers["__CHANNEL__"] = create___CHANNEL___mcp_server()
```

### 4b. Tool list registration

**File:** `src/agent/prompts.py`

Add your tool names and include them in `ALL_TOOLS`:

```python
__CHANNEL_UPPER___TOOLS = [
    "list___CHANNEL___accounts",
    "get___CHANNEL___campaigns",
    "get___CHANNEL___performance",
    "get___CHANNEL___insights",
    "get___CHANNEL___account_activity",
]

ALL_TOOLS = (
    GOOGLE_ADS_TOOLS + META_TOOLS + SLACK_TOOLS + BIGQUERY_TOOLS
    + GOOGLE_DRIVE_TOOLS + __CHANNEL_UPPER___TOOLS
)
```

---

## Step 5: Add OAuth Route (if needed)

**File:** `src/api/routes/__CHANNEL___oauth.py`
**Template:** `src/templates/oauth_route_template.py`

Most APIs use OAuth2. Create a router with 4 endpoints:

- `GET /authorize` — Redirect user to platform login
- `GET /callback` — Exchange auth code for tokens
- `POST /refresh` — Refresh an expired token
- `GET /status` — Check connection status

### Mount the router

**File:** `src/api/app.py`

```python
from src.api.routes.__CHANNEL___oauth import router as __CHANNEL___oauth_router

app.include_router(__CHANNEL___oauth_router)
```

### Key patterns

- Use `secrets.token_urlsafe(32)` for CSRF state tokens
- Store state in Redis with in-memory fallback (see `_save_oauth_state` / `_get_oauth_state` in meta_oauth.py)
- State tokens expire after 10 minutes
- Always validate state on callback to prevent CSRF attacks

---

## Step 6: Update .env.example

**File:** `.env.example`

Add credential placeholders with documentation URLs:

```bash
# --- __Channel__ Ads API ---
# Create at <platform developer portal URL>
__CHANNEL_UPPER___CLIENT_ID=your-client-id
__CHANNEL_UPPER___CLIENT_SECRET=your-client-secret
# Generated via OAuth flow — leave blank, Sidera's OAuth route handles this
__CHANNEL_UPPER___ACCESS_TOKEN=
```

---

## Step 7: Write Tests

### Connector tests

**File:** `tests/test_connectors/test___CHANNEL__.py`
**Template:** `src/templates/test_connector_template.py`

Cover:
- Construction with explicit credentials
- Construction from settings fallback
- Every public method: happy path, empty result, API error
- Auth error detection and raising
- Private helper methods (e.g., ID formatting, monetary conversion)

### MCP tool tests

**File:** `tests/test_mcp_servers/test___CHANNEL___mcp.py`
**Template:** `src/templates/test_mcp_server_template.py`

Cover:
- Each tool: happy path, empty result, error from connector
- Input validation (missing required fields)
- Use `mock_connector` fixture that patches `_get_connector`
- Call tools via `.handler({...})` — they're async functions

### Test patterns

```python
# Connector fixture pattern
@pytest.fixture()
def connector(mock_sdk):
    with patch("src.connectors.__CHANNEL__.SdkClient", return_value=mock_sdk):
        from src.connectors.__CHANNEL__ import __Channel__Connector
        conn = __Channel__Connector(credentials=_FAKE_CREDENTIALS)
    return conn

# MCP tool fixture pattern
PATCH_TARGET = "src.mcp_servers.__CHANNEL__._get_connector"

@pytest.fixture()
def mock_connector():
    with patch(PATCH_TARGET) as mock_get:
        connector = MagicMock()
        mock_get.return_value = connector
        yield connector
```

---

## Step 8: Add Cache TTLs

**File:** `src/cache/service.py`

Add platform-specific TTL constants:

```python
CACHE_TTL___CHANNEL_UPPER___CAMPAIGNS = 3600    # 1 hour
CACHE_TTL___CHANNEL_UPPER___METRICS = 300        # 5 minutes
CACHE_TTL___CHANNEL_UPPER___ACCOUNT_INFO = 7200  # 2 hours
```

Then import and use them in your connector's `@cached` decorators.

---

## Step 9: Add Normalization (if needed)

**File:** `src/models/normalized.py`

If the platform reports metrics in non-standard formats (like Meta's string decimals or Google's micros), add:

1. A mapping dict for platform-specific enum values (e.g., campaign objective names)
2. A `normalize___CHANNEL___metrics()` function that converts raw API data to `NormalizedMetrics`

---

## Step 10: Update System Prompt (optional)

**File:** `src/agent/prompts.py`

If the new channel has unique analysis considerations, add a note to `BASE_SYSTEM_PROMPT` explaining:
- What kind of data this platform provides
- Key differences from Google/Meta (e.g., attribution window, metric definitions)
- How the agent should reason about this platform's data

---

## Verification Checklist

After completing all steps, verify:

- [ ] `ruff check src/ tests/` — no lint errors
- [ ] `python -c "from src.connectors.__CHANNEL__ import __Channel__Connector"` — imports OK
- [ ] `python -c "from src.mcp_servers.__CHANNEL__ import create___CHANNEL___mcp_server"` — imports OK
- [ ] `pytest tests/test_connectors/test___CHANNEL__.py -v` — all tests pass
- [ ] `pytest tests/test_mcp_servers/test___CHANNEL___mcp.py -v` — all tests pass
- [ ] `pytest tests/ -v` — full suite still passes (no regressions)
- [ ] `.env.example` has all new variables documented

---

## Reference Files

| What | File |
|------|------|
| Cleanest connector example | `src/connectors/meta.py` |
| Cleanest MCP server example | `src/mcp_servers/meta.py` |
| Shared MCP helpers | `src/mcp_servers/helpers.py` |
| Cleanest OAuth route | `src/api/routes/meta_oauth.py` |
| Config pattern | `src/config.py` |
| Agent MCP wiring | `src/agent/core.py:_build_mcp_servers()` |
| Tool list registration | `src/agent/prompts.py:ALL_TOOLS` |
| Cache TTL constants | `src/cache/service.py` |
| Normalization layer | `src/models/normalized.py` |
| Connector test pattern | `tests/test_connectors/test_meta.py` |
| MCP tool test pattern | `tests/test_mcp_servers/test_meta_mcp.py` |
