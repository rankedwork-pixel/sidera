# Sidera: Executive Overview

## What Is This?

Sidera is an open-source framework for building **AI employees** — autonomous agents that connect to your company's existing tools (Google Ads, Meta, BigQuery, Slack, Google Drive, and more), analyze data on a schedule, recommend actions, and execute them with human approval.

Think of it as the operating system for an AI workforce — one you own completely.

## The Problem It Solves

Today, companies using AI for operational tasks hit the same wall:

1. **One-off agents** — each agent is a standalone chatbot. No coordination, no shared context, no organizational structure.
2. **No memory** — every interaction starts from scratch. The agent that analyzed your campaigns yesterday has no idea what it found.
3. **No trust model** — agents either do everything (dangerous) or nothing (useless). There's no middle ground.
4. **No accountability** — when an AI agent makes a mistake, who's responsible? Nobody owns it.

Enterprise platforms (like OpenAI Frontier) solve some of these problems, but at six-figure price tags and with vendor lock-in. Sidera gives you the same capabilities for ~$0.50/day per role in API costs, running on your infrastructure, with full source code access.

## The Core Idea

**Departments → Roles → Skills**

| Layer | What It Is | Example |
|-------|-----------|---------|
| **Department** | Top-level grouping with shared context and vocabulary | Marketing, IT, Executive |
| **Role** | An AI employee with a persona, goals, principles, and tools | Performance Media Buyer, Head of IT |
| **Skill** | A specific task the role can perform | Anomaly Detection, Creative Analysis |

Context flows downward. The Marketing department defines vocabulary ("ROAS means return on ad spend"). The Media Buyer role inherits that vocabulary plus its own persona and principles. Each skill inherits everything above it.

You teach agents by writing YAML files. No code required for new skills.

## Key Capabilities

- **Scheduled analysis** — roles run on cron schedules (e.g., Media Buyer at 7 AM weekdays)
- **Slack integration** — briefings posted to Slack, approve/reject buttons on recommendations
- **Conversational mode** — @mention a role in Slack to chat with it directly, including in-thread write operations
- **Persistent memory** — 9 memory types, agents learn from every run and consolidate knowledge weekly
- **Graduated trust** — 3 tiers from fully gated to auto-execute with guardrails and kill switches
- **Manager delegation** — manager roles decide which sub-roles to activate and synthesize their output
- **Human stewardship** — every AI role has a designated human accountable for its behavior
- **Cost control** — three-phase model routing (Haiku/Sonnet/Opus) keeps daily costs under $1/role
- **Webhook monitoring** — external systems push alerts, agents investigate automatically
- **Skill evolution** — agents propose improvements to their own skills, subject to human approval
- **Peer communication** — roles message each other and share structured learnings
- **Infrastructure control** — SSH into servers, automate desktops via Computer Use
- **Meeting participation** — join calls listen-only via Recall.ai, capture transcripts, delegate action items

## What It's Not

- It's **not a chatbot**. Agents run autonomously on schedules.
- It's **not a single-purpose tool**. The framework is domain-agnostic. Swap the connectors for any domain.
- It's **not uncontrolled**. Every write action requires human approval (or matching auto-execute rules with guardrails).
- It's **not vendor-locked**. MIT license. Run it anywhere. Use any model via hybrid routing.

## By the Numbers

| Metric | Count |
|--------|-------|
| Connectors | 8 (Google Ads, Meta, BigQuery, Drive, Slack, Recall.ai, SSH, Computer Use) |
| MCP tools | 74 |
| Inngest workflows | 18 |
| Database migrations | 29 |
| DB service methods | 115 |
| YAML skills | 11 (examples — add your own) |
| Departments | 3 (Marketing, IT, Executive) |
| Roles | 7 |
| Tests | 4221+ |

## Who Is This For?

**Developers and operators** who want to deploy AI agents at scale with proper organizational structure, accountability, and human oversight. The first use case is performance marketing, but the architecture works for any domain — finance, operations, HR, customer success, engineering management, e-commerce.

Build a new department by writing YAML and (optionally) a connector. The agent loop, Slack interaction, approval queue, audit trail, memory system, and cost controls stay identical.
