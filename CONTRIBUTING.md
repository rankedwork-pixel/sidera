# Contributing to Sidera

Thanks for your interest in contributing! Sidera is a framework for building AI employees — the most valuable contributions are new skills, connectors, and domains.

## Getting Started

```bash
git clone https://github.com/rankedwork-pixel/sidera.git
cd sidera

python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

make test          # Run tests (should all pass)
make demo          # Run the zero-config demo
```

## How to Contribute

### 1. Add Skills (Easiest, Highest Impact)

Skills are YAML files that teach agents how to do a job. No Python required.

**Create a new skill:**

```yaml
# src/skills/library/<department>/<role>/<skill_name>/skill.yaml
id: my_new_skill
name: "My New Skill"
version: "1.0"
description: "What this skill does in one line"
category: analysis          # analysis, optimization, reporting, monitoring, etc.
platforms: [custom]          # which connectors it uses
tags: [relevant, keywords]
model: sonnet               # haiku, sonnet, or opus
max_turns: 15
tools_required:
  - get_system_health
  - get_cost_summary

system_supplement: |
  Clear instructions for the agent. Use behavioral enforcement:
  - "You MUST..." not "You should..."
  - "NEVER..." not "Try to avoid..."
  - Define mandatory analysis sequences

prompt_template: |
  Analyze data for {analysis_date}.
  Connected accounts: {accounts_block}

output_format: |
  ## Summary
  ## Findings
  ## Recommendations

business_guidance: |
  Hard rules and thresholds the agent must follow.
```

**Add context files** for richer skills:

```
my_new_skill/
  skill.yaml
  context/
    scoring_rubric.md       # Decision frameworks
    benchmarks.md           # Reference data
  examples/
    good_output.md          # Example of what good output looks like
  guidelines/
    common_mistakes.md      # What to avoid
```

Context files are automatically injected into the agent's system prompt.

See the [Skill Creation Guide](docs/skill-creation-guide.md) for a detailed walkthrough with examples.

### 2. Add Connectors

Connectors are Python classes that interface with external APIs.

```bash
# Start from the template
cp src/templates/connector_template.py src/connectors/my_api.py
cp src/templates/mcp_server_template.py src/mcp_servers/my_api.py
cp src/templates/test_connector_template.py tests/test_connectors/test_my_api.py
cp src/templates/test_mcp_server_template.py tests/test_mcp_servers/test_my_api.py
```

Implement your read/write methods following the connector template. The agent loop, approval flow, and audit trail stay identical.

### 3. Add Departments and Roles

Create a new domain by adding YAML config files:

```
src/skills/library/my_department/
  _department.yaml          # Department definition (name, context, vocabulary)
  my_role/
    _role.yaml              # Role definition (persona, principles, goals)
    my_skill/
      skill.yaml            # Skills for this role
```

See the `executive` department for a working example of the hierarchy.

### 4. Fix Bugs and Improve Existing Code

- Check open issues for bugs or feature requests
- The CEO role's skills are starting points — improve their instructions, add context files, refine thresholds
- All write operations need test coverage

## Development Workflow

**Before every PR, run:**

```bash
make cleanup
```

This runs: `ruff format` + `ruff check` + `pytest` + `doc_sync`. All four must pass.

**Individual commands:**

```bash
make format        # Auto-format code
make lint          # Lint check
make test          # Full test suite
make test-fast     # Parallel, no coverage
make sync-docs     # Verify doc counts match codebase
make update-docs   # Auto-fix stale doc counts
```

## Code Style

- **Python 3.11+** — use modern syntax (type unions with `|`, f-strings, etc.)
- **Ruff** — 100-char line length, rules: E, F, I, N, W
- **Async** — all I/O operations use `async`/`await`
- **Type hints** — on function signatures (not required on local variables)
- **No unnecessary abstractions** — three similar lines is better than a premature helper function

## Testing

- Tests go in `tests/` mirroring the `src/` structure
- Use `pytest` with `asyncio_mode = "auto"`
- Mock external services — use `fakeredis` for Redis, `sqlite+aiosqlite:///:memory:` for DB
- All new features need tests. New connectors and MCP tools need both unit and integration tests.

## PR Guidelines

- One feature per PR
- Tests required for new functionality
- Run `make cleanup` before submitting
- If you add DB methods, migrations, MCP tools, or workflows, run `make update-docs` to keep counts in sync

## Architecture Quick Reference

```
Department → Role → Skill          # Hierarchy (context flows down)
Connector → MCP Tool → Agent       # Data flow
Agent → Recommendation → Approval  # Action flow
Approval → Execution → Audit Log   # Safety flow
```

- **Skills** define *what* the agent does (YAML)
- **Connectors** define *how* to talk to APIs (Python)
- **MCP Tools** bridge connectors to the agent (registered via `@tool` decorator)
- **Workflows** handle scheduling and orchestration (Inngest durable functions)

## Questions?

Open an issue or start a discussion. We're happy to help you get started.
