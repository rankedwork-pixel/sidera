# Sidera: Executive Overview

## What Is This?

Sidera is a framework for building **AI employees** — autonomous agents that connect to your company's existing tools (Google Ads, Meta, BigQuery, Slack, Google Drive), analyze data on a schedule, recommend actions, and execute them with human approval.

Think of it as the operating system for an AI workforce.

## The Problem It Solves

Today, companies using AI for operational tasks hit the same wall:

1. **One-off agents** — each agent is a standalone chatbot. No coordination, no shared context, no organizational structure.
2. **No memory** — every interaction starts from scratch. The agent that analyzed your campaigns yesterday has no idea what it found.
3. **No trust model** — agents either do everything (dangerous) or nothing (useless). There's no middle ground.
4. **No accountability** — when an AI agent makes a mistake, who's responsible? Nobody owns it.

Sidera solves all four by organizing agents the same way companies organize people.

## The Core Idea

**Departments → Roles → Skills**

| Layer | What It Is | Example |
|-------|-----------|---------|
| **Department** | Top-level grouping with shared context | Marketing, IT, Executive |
| **Role** | An AI employee with a persona, goals, and tools | Performance Media Buyer, Head of IT |
| **Skill** | A specific task the role can perform | Anomaly Detection, Budget Pacing |

Context flows downward. The Marketing department defines vocabulary ("ROAS means return on ad spend"). The Media Buyer role inherits that vocabulary plus its own persona and principles. Each skill inherits everything above it.

You teach agents by writing YAML files. No code required for new skills.

## Key Capabilities

- **Scheduled analysis** — roles run on cron schedules (e.g., Media Buyer at 7 AM weekdays)
- **Slack integration** — briefings posted to Slack, approve/reject buttons on recommendations
- **Conversational mode** — @mention a role in Slack to chat with it directly
- **Persistent memory** — 8 memory types, agents learn from every run
- **Graduated trust** — 3 tiers from fully gated to auto-execute with guardrails
- **Manager delegation** — manager roles decide which sub-roles to activate and synthesize their output
- **Human stewardship** — every AI role has a designated human accountable for its behavior
- **Cost control** — model routing (Haiku/Sonnet/Opus) keeps daily costs under $1/role
- **Webhook monitoring** — external systems push alerts, agents investigate automatically

## What It's Not

- It's **not a chatbot**. Agents run autonomously on schedules.
- It's **not a single-purpose tool**. The framework is domain-agnostic. Swap the connectors for any domain.
- It's **not uncontrolled**. Every write action requires human approval (or matching auto-execute rules with guardrails).

## Current State

- **Phase:** E2E testing with real API keys
- **Built:** Full framework — 6 connectors, 62 MCP tools, 18 workflows, 20 skills across 3 departments, 6 roles, 3,685+ tests
- **Verified:** Google Ads live, Slack bot connected, conversation mode working
- **Next:** Meta live testing, Railway deployment, scale to 100+ skills

## Who Is This For?

Any company that wants to deploy AI agents at scale with proper organizational structure, accountability, and human oversight. The first use case is performance marketing, but the architecture works for any domain — finance, operations, HR, customer success.
