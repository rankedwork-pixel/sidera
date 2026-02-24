# Architecture Decision Records

This document captures the key architectural decisions made during Sidera's development, the alternatives considered, and why we chose what we chose. If you're inheriting this codebase, start here.

---

## ADR-001: Inngest for Workflow Orchestration

**Decision:** Use Inngest durable functions for all workflow orchestration (cron jobs, async processing, approval flows).

**Alternatives considered:**
- **Celery + Redis/RabbitMQ** — industry standard, massive ecosystem. Rejected because: requires a persistent worker process, broker management, and complex retry/checkpoint logic. Celery tasks aren't natively durable — if a worker dies mid-task, you lose state.
- **Temporal** — excellent durability guarantees. Rejected because: heavy infrastructure (requires its own server cluster), steep learning curve, overkill for our workflow complexity.
- **Simple cron + FastAPI background tasks** — minimal. Rejected because: no checkpointing, no built-in retry, no visibility into running/failed jobs, no durable human-in-the-loop approval flow.

**Why Inngest:**
- Each workflow step is independently checkpointed — if step 3 of 7 fails, it retries from step 3, not step 1
- Built-in cron triggers with no persistent worker needed
- `step.wait_for_event()` enables async human approval (agent posts to Slack, workflow pauses, resumes when human clicks Approve)
- Dev server (`npx inngest-cli@latest dev`) gives full visibility into function runs, step states, and failures
- Hosted version available for production (or self-host the dev server)
- Lightweight: just `pip install inngest` and register functions with FastAPI

**Trade-offs:**
- Less ecosystem/community than Celery
- Vendor dependency if using hosted Inngest (mitigated: dev server is open source)
- Step-based model requires thinking in checkpoints rather than linear code

---

## ADR-002: Stateless Agent, Stateful Database

**Decision:** Every agent run is a fresh Claude API conversation. All context (memory, org chart, thread history) is loaded from PostgreSQL at the start of each run. No persistent LLM sessions.

**Alternatives considered:**
- **Persistent conversation sessions** — keep the Claude conversation alive across runs. Rejected because: expensive (paying for full context every API call), fragile (session loss = total memory loss), and doesn't scale across roles.
- **File-based context** — store agent state in local files. Rejected because: doesn't work in containerized/serverless deployment, no concurrent access safety.

**Why stateless:**
- Each run is reproducible — same DB state = same agent behavior
- No risk of conversation drift or hallucination compounding across sessions
- Easy to debug — inspect the DB to see exactly what context the agent had
- Scales horizontally — multiple agent runs can execute concurrently without session conflicts
- Role memory system provides continuity without persistent sessions

**Trade-offs:**
- Context assembly adds ~500ms per run (DB queries for memory, messages, org chart)
- Long conversation threads require fetching full history from Slack API each turn
- Agent can't reference "what I said earlier today" without explicit memory extraction

---

## ADR-003: YAML Skills on Disk, DB as Override Layer

**Decision:** Skill definitions live as YAML files in `src/skills/library/` organized by department/role. The database stores overrides and runtime modifications. `load_registry_with_db()` loads disk first, then overlays DB entries (DB wins on ID conflicts).

**Alternatives considered:**
- **DB-first** — all skills in the database, managed via UI/API. Rejected because: skills are code-like artifacts that benefit from version control, code review, and diff visibility. Putting them in a DB makes them invisible to git.
- **YAML-only** — no DB layer. Rejected because: agents need to propose skill modifications at runtime (skill evolution), and the dynamic org chart needs runtime CRUD without redeploying.
- **Git-backed with webhooks** — skills in a git repo, webhooks trigger reloads. Rejected because: adds deployment complexity and doesn't support runtime modifications.

**Why hybrid:**
- YAML files are the "seed data" — version-controlled, reviewable, portable
- DB overlay enables runtime modifications (agent proposes skill change → human approves → written to DB)
- DB unavailable → graceful fallback to disk-only (the system never fully breaks)
- New skills can be added by dropping a YAML file — no DB migration needed
- Existing skills can be modified at runtime without redeployment

**Trade-offs:**
- Two sources of truth — must understand the merge semantics (DB replaces disk on same ID, not field-level merge)
- Disk skills don't reflect DB modifications until exported
- `_sources` dict on SkillRegistry tracks provenance ("disk" vs "db") for debugging

---

## ADR-004: Three-Phase Model Routing

**Decision:** Daily briefings use three-phase routing: Haiku (data collection) → Sonnet (analysis) → Opus (strategy). Each phase is a separate Claude API call with a distinct system prompt.

**Alternatives considered:**
- **Single model for everything** — just use Sonnet. Rejected because: data collection is 80% of tokens and doesn't need Sonnet's reasoning. Opus adds strategy but is 5x the cost of Sonnet and unnecessary on stable days.
- **Two-phase (Haiku + Sonnet)** — skip Opus. This is actually what happens on most days. Opus is only invoked when volatility > 10%.

**Why three-phase:**
- Phase 1 (Haiku, ~$0.02): Pulls data via tools. Haiku is great at following tool-calling instructions and costs 12x less than Sonnet.
- Phase 2 (Sonnet, ~$0.15): Analyzes the data Haiku collected. No tool access — pure reasoning over structured data. This is where the real analysis happens.
- Phase 3 (Opus, ~$0.35): Strategic layer. Only runs on volatile days (>10% metric swings). Adds cross-channel insights, competitive context, and strategic recommendations.
- Total: ~$0.52/briefing vs ~$1.50-3.00 with a single Sonnet/Opus call doing everything.

**Trade-offs:**
- Three API calls instead of one — slightly higher latency (~30s total vs ~20s)
- Data must be serialized between phases (Phase 1 output becomes Phase 2 input)
- Phase 1 failures require fallback logic (stale data from cache/DB with warning banner)

---

## ADR-005: Slack as the Primary Interface

**Decision:** Slack is the primary human interface — briefings, approvals, conversations, and administration all happen in Slack.

**Alternatives considered:**
- **Custom web UI** — full control over UX. Rejected for MVP because: massive development effort for something that needs to be always-accessible. Slack is already open on everyone's computer.
- **Email** — lowest friction. Rejected because: no interactive buttons, no threading, no real-time conversation.
- **Discord** — similar to Slack. Could work but Slack is standard in business environments.

**Why Slack:**
- Already in the workflow — no new app to install or check
- Interactive buttons (Block Kit) enable approve/reject inline
- Threading maps naturally to agent conversations
- Slash commands for administration (`/sidera run`, `/sidera org`, `/sidera steward`)
- @mentions for natural-language role routing
- Mobile app means approvals from anywhere

**Trade-offs:**
- Slack API rate limits (1 message/second per channel)
- Slack Bolt SDK adds a dependency
- Rich formatting is limited to Block Kit (no charts, no tables beyond markdown)
- Streamlit dashboard exists as a supplement for data-heavy views

---

## ADR-006: Department → Role → Skill Hierarchy

**Decision:** Three-level organizational hierarchy with context inheritance flowing downward.

**Alternatives considered:**
- **Flat skill list** — no hierarchy, just skills with tags. This was the original design. Rejected because: no way to share context across related skills, no persona consistency, no manager delegation.
- **Two-level (Role → Skill)** — skip departments. Rejected because: department-level context (vocabulary, shared rules) is genuinely useful and departments provide organizational grouping.
- **Deep hierarchy (Org → Division → Department → Team → Role → Skill)** — too many levels. Rejected because: adds complexity without clear benefit. Three levels cover all real use cases.

**Why three levels:**
- **Department** provides shared vocabulary and context (e.g., "ROAS means return on ad spend" for all marketing roles)
- **Role** provides persona, principles, goals, and memory — the "who" of the agent
- **Skill** provides task-specific instructions — the "what" to do right now
- Context inheritance means you write things once at the right level
- Manager roles (with `manages` field) enable delegation within and across departments

**Trade-offs:**
- Directory structure mirrors hierarchy (`library/dept/role/skill.yaml`) — rigid but clear
- Adding a skill requires knowing which department and role it belongs to
- Cross-department skills don't have a natural home (we use loose skills as a fallback)

---

## ADR-007: Human-in-the-Loop Approval with Graduated Trust

**Decision:** Three trust tiers: Tier 1 (read-only, no approval needed), Tier 2 (auto-execute with rules, caps, and kill switch), Tier 3 (manual approval via Slack buttons for every action).

**Alternatives considered:**
- **Always require approval** — safest. Rejected because: pausing a $200/day campaign with 0 conversions at 3 AM shouldn't wait for a human who's asleep.
- **No approval needed** — let the agent act freely. Rejected because: absolutely not. One bad budget change could waste thousands.
- **Confidence-based** — agent self-reports confidence, high confidence auto-executes. Rejected because: LLMs are notoriously poorly calibrated on confidence. We can't trust the agent's self-assessment.

**Why graduated trust with rules:**
- Auto-execute rules are explicit YAML definitions with hard thresholds, not LLM judgment
- Each rule has daily caps, cooldown periods, and platform restrictions
- Global kill switch (`AUTO_EXECUTE_ENABLED=false` by default) — one toggle disables all auto-execution
- `AUTO_APPROVED` status in DB distinguishes auto-executed from human-approved actions
- Pre-action lesson check: if the agent's own memory contains a high-confidence lesson contradicting the action, auto-execute is blocked
- All auto-executed actions still get Slack notifications so humans can review after the fact

**Trade-offs:**
- Rule definitions are YAML — powerful but requires understanding the condition/constraint schema
- More rules = more testing needed to ensure they don't conflict
- 50% budget cap is a blunt safety instrument — may be too conservative for some use cases

---

## ADR-008: Persistent Role Memory (Hot/Cold Tiered)

**Decision:** Agents accumulate persistent memory across runs via the `role_memory` table. 9 memory types. Hot memories (< 90 days) are auto-injected into context. Cold memories are archived and searchable.

**Alternatives considered:**
- **No memory** — fresh context every run. Rejected because: agents would repeat mistakes, lose context about past decisions, and provide worse recommendations over time.
- **Full conversation history** — store and replay all past conversations. Rejected because: token explosion. A month of daily runs would be millions of tokens.
- **RAG / vector search** — embed memories and retrieve relevant ones. Rejected for v1 because: adds embedding pipeline complexity. Simple keyword search + confidence sorting works well enough.

**Why tiered memory:**
- Hot memories provide continuity without token explosion (capped at 2000 tokens, ~15-20 memories)
- Memory index pattern: when >20 hot memories exist, inject a compact title+ID index instead of full text. Agent loads specific memories on demand.
- Cold archive means nothing is lost — searchable via `search_role_memory_archive` MCP tool
- Weekly consolidation (Haiku) merges duplicates, boosts confidence for corroborated memories, and flags contradictions
- Steward notes (human-injected) have highest priority and can't be overridden by the agent

**Trade-offs:**
- Memory extraction adds ~$0.005-0.01 per run (Haiku)
- Memory quality depends on extraction accuracy — some memories may be noise
- 90-day hot window is arbitrary (configurable via `ttl_days` on memory type)

---

## ADR-009: MCP-Style Tool Registry (Not Actual MCP Protocol)

**Decision:** Use a custom `ToolRegistry` that mimics the MCP tool interface (name, description, JSON schema, async handler) but communicates in-process rather than over stdio/HTTP.

**Alternatives considered:**
- **Actual MCP servers** — each connector runs as a separate MCP server process. Rejected because: 8 connectors × separate processes = complex orchestration, and the agent loop runs in a single process anyway.
- **Direct function calls** — no tool abstraction, agent calls connector methods directly. Rejected because: loses the tool description/schema layer that helps Claude understand what tools are available and how to call them.

**Why custom registry:**
- Single-process, no IPC overhead
- Same interface as MCP (name, schema, handler) so tools are portable
- `ToolRegistry` dynamically composes tool sets based on role's connectors
- Write safety module (`write_safety.py`) wraps all write tools with approval verification
- Also have a real MCP stdio server (`src/mcp_stdio/`) for Claude Code integration — bridges the internal registry to actual MCP protocol

**Trade-offs:**
- Not a standard MCP server — tools can't be used by arbitrary MCP clients without the stdio bridge
- Tool count (74) is high — may benefit from sub-categorization in the future

---

## ADR-010: Inngest Events for Async Communication

**Decision:** Use Inngest events for all async communication between components: Slack handlers emit events, workflows process them.

**Pattern:**
```
Slack button click → emit "sidera/approval.decided" → approval workflow resumes
Slack @mention → emit "sidera/conversation.turn" → conversation_turn_workflow runs
Webhook POST → emit "sidera/webhook.received" → event_reactor_workflow runs
```

**Why events (not direct calls):**
- Decouples Slack handlers from business logic — handlers stay fast (< 3s Slack timeout)
- Inngest handles retry, checkpointing, and failure recovery
- Events are logged and inspectable in the Inngest dashboard
- Same event can trigger multiple workflows (fan-out)

**Exception:** Dev-mode inline runner (`_run_conversation_turn_inline`) bypasses Inngest for local testing — runs the agent loop directly in the Slack handler. Only active when `INNGEST_DEV=1`.

---

## ADR-011: Extended Thinking for Deep Reasoning

**Decision:** Enable Anthropic's extended thinking (`thinking_budget=10000` tokens) on all Sonnet and Opus calls. Interleaved thinking allows reasoning between tool calls.

**Why:**
- Agents make better decisions when they can reason internally before acting
- Interleaved thinking is especially valuable in multi-step tool orchestration — the agent reasons about what tool to call next
- ~10-30% cost increase is justified by quality improvement

**Where it's NOT used:**
- Haiku calls (not supported by the model)
- Single-turn classification calls (`call_claude_api`) — minimal benefit
- Delegation/synthesis decisions — simple enough to not need deep reasoning

---

## ADR-012: Behavioral Enforcement in Skills

**Decision:** Skill YAML uses mandatory behavioral language (MUST/NEVER/BEFORE) instead of suggestive instructions ("consider", "you might want to").

**Why:**
- LLMs follow explicit instructions more reliably than suggestions
- "NEVER compare search metrics against display benchmarks" prevents a common mistake every time
- Hard numeric thresholds (>2σ = WARNING, >3σ = CRITICAL) eliminate ambiguity
- MANDATORY SEQUENCES with numbered steps ensure consistent execution order

**Inspired by:** Superpowers-style prompt engineering — treat skills as behavioral checklists, not knowledge documents.
