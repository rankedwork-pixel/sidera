# Sidera: Executive Overview

## What Is This?

Sidera is an open-source framework for building **AI employees** — autonomous agents that connect to your company's existing tools and APIs, analyze data on a schedule, recommend actions, and execute them with human approval.

Think of it as the operating system for an AI workforce — one you own completely.

## The Problem It Solves

Today, companies using AI for operational tasks hit the same wall:

1. **One-off agents** — each agent is a standalone chatbot. No coordination, no shared context, no organizational structure.
2. **No memory** — every interaction starts from scratch. The agent that analyzed your data yesterday has no idea what it found.
3. **No trust model** — agents either do everything (dangerous) or nothing (useless). There's no middle ground.
4. **No accountability** — when an AI agent makes a mistake, who's responsible? Nobody owns it.

Enterprise platforms solve some of these problems, but at six-figure price tags and with vendor lock-in. Sidera gives you the same capabilities for ~$0.50/day per role in API costs, running on your infrastructure, with full source code access.

## The Core Idea

**Departments → Roles → Skills**

| Layer | What It Is | Example |
|-------|-----------|---------|
| **Department** | Top-level grouping with shared context and vocabulary | Engineering, Sales, Executive |
| **Role** | An AI employee with a persona, goals, principles, and tools | On-Call Engineer, Pipeline Analyst |
| **Skill** | A specific task the role can perform | Incident Triage, Deal Health Check |

Context flows downward. A department defines shared vocabulary and context. Each role inherits that plus its own persona and principles. Each skill inherits everything above it.

You teach agents by writing YAML files. No code required for new skills.

## Key Capabilities

- **Scheduled analysis** — roles run on cron schedules (e.g., On-Call Engineer every 15 minutes)
- **Slack integration** — briefings posted to Slack, approve/reject buttons on recommendations
- **Conversational mode** — @mention a role in Slack to chat with it directly, including in-thread write operations
- **Persistent memory** — 9 memory types, agents learn from every run and consolidate knowledge weekly
- **Graduated trust** — 3 tiers from fully gated to auto-execute with guardrails and kill switches
- **Manager delegation** — manager roles decide which sub-roles to activate and synthesize their output
- **Human stewardship** — every AI role has a designated human accountable for its behavior
- **Cost control** — three-phase model routing (Haiku/Sonnet/Opus) keeps daily costs under $1/role
- **Skill evolution** — agents propose improvements to their own skills, subject to human approval
- **Peer communication** — roles message each other and share structured learnings
- **Pluggable connectors** — add any API with a Python template; the framework handles the rest

## What It's Not

- It's **not a chatbot**. Agents run autonomously on schedules.
- It's **not a single-purpose tool**. The framework is domain-agnostic. Swap the connectors for any domain.
- It's **not uncontrolled**. Every write action requires human approval (or matching auto-execute rules with guardrails).
- It's **not vendor-locked**. MIT license. Run it anywhere. Use any model via hybrid routing.

## By the Numbers

| Metric | Count |
|--------|-------|
| Built-in connector | 1 (Slack) — add your own with templates |
| MCP tools | ~29 framework tools + 10 meta-tools |
| Inngest workflows | 13 |
| Database migrations | 29 |
| DB service methods | 115 |
| Starting skills | 1 (CEO org_health_check) — add your own |
| Starting departments | 1 (Executive) — add your own |

## Who Is This For?

**Developers and operators** who want to deploy AI agents at scale with proper organizational structure, accountability, and human oversight. The framework ships with a CEO role as a starting point — you build departments, roles, skills, and connectors for your domain: engineering, finance, operations, HR, customer success, sales, e-commerce, or anything else.

Build a new department by writing YAML and (optionally) a connector. The agent loop, Slack interaction, approval queue, audit trail, memory system, and cost controls stay identical.
