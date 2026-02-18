"""LLM prompts for the company bootstrap pipeline.

Five prompt sets power the bootstrap:

1. **CLASSIFY_PROMPT** -- Haiku classifies each document's type.
2. **EXTRACT_ORG_PROMPT** -- Sonnet extracts org structure (depts, roles, hierarchy).
3. **EXTRACT_SKILLS_PROMPT** -- Sonnet extracts skills from SOPs/playbooks.
4. **EXTRACT_CONTEXT_PROMPT** -- Sonnet extracts goals, vocabulary, principles, memories.
5. **REFINE_PROMPT** -- Sonnet applies user feedback to modify a draft plan.
"""

from __future__ import annotations

# =====================================================================
# Document classification (Haiku -- cheap, fast)
# =====================================================================

CLASSIFY_SYSTEM_PROMPT = """\
You are a document classifier for an AI agent onboarding system. Your job \
is to read document titles and content previews, then assign one or more \
categories to each document.

Categories:
- org_structure: Org charts, team descriptions, reporting lines, role descriptions
- sop_playbook: Standard operating procedures, playbooks, runbooks, process docs, how-to guides
- goals_kpis: Business goals, OKRs, KPIs, targets, metrics definitions, dashboards
- vocabulary_glossary: Glossaries, acronym lists, terminology definitions, domain jargon
- decision_tree: Decision frameworks, escalation paths, if-then rules, flowcharts
- meeting_notes: Meeting minutes, standups, retrospectives (may contain useful context)
- irrelevant: Not useful for understanding the company (personal notes, spam, templates)

A document can belong to MULTIPLE categories. For example, an "Engineering \
Team Handbook" might be both org_structure and sop_playbook.

Respond with valid JSON only -- no markdown fences, no explanation."""

CLASSIFY_USER_TEMPLATE = """\
Classify each document below. Return a JSON array where each element has:
- "file_id": the document's file_id
- "categories": array of category strings
- "confidence": number 0.0-1.0 for overall classification confidence

Documents:
{documents}"""


def format_doc_for_classification(
    file_id: str, title: str, content_preview: str
) -> str:
    """Format a single document for the classification prompt."""
    # Use first 2000 chars as preview to keep costs low
    preview = content_preview[:2000]
    return f'---\nfile_id: "{file_id}"\ntitle: "{title}"\ncontent_preview: """\n{preview}\n"""'


# =====================================================================
# Org structure extraction (Sonnet -- accurate)
# =====================================================================

EXTRACT_ORG_SYSTEM_PROMPT = """\
You are an organizational analyst for an AI agent onboarding system. Your \
job is to read company documents and extract the organizational structure: \
departments, teams, roles, reporting lines, and domain vocabulary.

Extract:
1. **Departments** -- major organizational units (Engineering, Sales, Support, etc.)
2. **Roles** -- specific job functions within departments, including:
   - Who they report to (manager relationship)
   - What they are responsible for
   - A persona description (how they think, what they care about)
3. **Hierarchy** -- which roles manage which other roles
4. **Vocabulary** -- department-specific jargon, acronyms, terminology

Rules:
- Generate slug IDs from names: "Customer Support" -> "customer_support"
- Role IDs should be specific: "senior_backend_engineer", not just "engineer"
- Manager roles should have a "manages" list of sub-role IDs
- If the org structure is unclear, extract what you can and note uncertainty
- Vocabulary should be scoped to the department it belongs to

Respond with valid JSON only -- no markdown fences, no explanation."""

EXTRACT_ORG_USER_TEMPLATE = """\
Extract the organizational structure from these documents. Return JSON with:
{{
  "departments": [
    {{
      "id": "slug_id",
      "name": "Display Name",
      "description": "What this department does",
      "context": "A paragraph describing this department's mission and focus areas",
      "vocabulary": [{{"term": "ACRONYM", "definition": "What it means"}}]
    }}
  ],
  "roles": [
    {{
      "id": "slug_id",
      "name": "Display Name",
      "department_id": "parent_dept_slug",
      "description": "What this role does",
      "persona": "A paragraph describing this role's personality and style",
      "manages": ["sub_role_id_1", "sub_role_id_2"],
      "principles": ["Decision-making heuristic 1", "Heuristic 2"]
    }}
  ]
}}

Documents:
{documents}"""

# =====================================================================
# Skills extraction (Sonnet -- accurate)
# =====================================================================

EXTRACT_SKILLS_SYSTEM_PROMPT = """\
You are a skills designer for an AI agent onboarding system. Your job is to \
read SOPs, playbooks, and process documents, then design AI agent skills \
that capture those processes.

A "skill" is a YAML-defined task that teaches an AI agent how to perform \
a specific job. Each skill has:
- A clear purpose (what it does)
- Instructions (system_supplement -- how the agent should approach the task)
- A prompt template (what to ask the agent each time it runs)
- Business guidance (domain-specific rules and constraints)
- An output format (structured output template)

Rules:
- Each SOP/playbook should map to one or more skills
- Skills should be assigned to the most relevant role
- Use "haiku" for monitoring/status, "sonnet" for analysis, "opus" for strategy
- Category: monitoring, optimization, reporting, analysis, creative, \
planning, forecasting, compliance, strategy, operations, general
- Skill IDs should be descriptive: "weekly_pipeline_review", not "review"
- Business guidance should capture the domain-specific rules from the SOP

Respond with valid JSON only -- no markdown fences, no explanation."""

EXTRACT_SKILLS_USER_TEMPLATE = """\
Design AI agent skills based on these SOPs and playbooks. The extracted \
roles are listed below so you can assign each skill to the right role.

Existing roles:
{roles}

Return JSON with:
{{
  "skills": [
    {{
      "id": "slug_id",
      "name": "Display Name",
      "role_id": "assigned_role_id",
      "department_id": "parent_dept_id",
      "description": "One-line description of what this skill does",
      "category": "analysis",
      "model": "sonnet",
      "system_supplement": "Instructions for the agent on HOW to do this task.",
      "prompt_template": "The prompt to run each time.",
      "output_format": "Structured template for output (markdown).",
      "business_guidance": "Domain rules, constraints, and edge cases."
    }}
  ]
}}

Documents:
{documents}"""

# =====================================================================
# Context extraction (Sonnet -- goals, principles, vocabulary, memories)
# =====================================================================

EXTRACT_CONTEXT_SYSTEM_PROMPT = """\
You are a knowledge extractor for an AI agent onboarding system. Your job \
is to read company documents about goals, KPIs, vocabulary, and extract \
structured context that will be injected into AI agent roles.

Extract:
1. **Goals per role** -- measurable objectives each role should optimize for
2. **Principles per role** -- decision-making heuristics beyond the role persona
3. **Vocabulary per department** -- additional domain-specific terms and acronyms
4. **Seed memories** -- important facts, decisions, patterns, or relationships \
   that agents should know from day one

Memory types:
- "insight" -- a factual observation about the business
- "decision" -- a past decision and its rationale
- "relationship" -- a key stakeholder or team relationship
- "pattern" -- a recurring pattern in the business

Rules:
- Goals should be specific and measurable where possible
- Principles should be actionable decision heuristics ("When X, do Y because Z")
- Memories should have a confidence score (0.5-1.0) based on source reliability
- Assign memories to the most relevant role

Respond with valid JSON only -- no markdown fences, no explanation."""

EXTRACT_CONTEXT_USER_TEMPLATE = """\
Extract goals, principles, vocabulary, and seed memories from these documents.

Existing departments and roles:
{org_structure}

Return JSON with:
{{
  "role_goals": [
    {{
      "role_id": "role_slug",
      "goals": ["Goal 1", "Goal 2"]
    }}
  ],
  "role_principles": [
    {{
      "role_id": "role_slug",
      "principles": ["Principle 1", "Principle 2"]
    }}
  ],
  "department_vocabulary": [
    {{
      "department_id": "dept_slug",
      "vocabulary": [{{"term": "TERM", "definition": "Definition"}}]
    }}
  ],
  "memories": [
    {{
      "role_id": "target_role_id",
      "department_id": "parent_dept_id",
      "memory_type": "insight",
      "title": "Short title",
      "content": "Detailed content of the memory",
      "confidence": 0.8
    }}
  ]
}}

Documents:
{documents}"""

# =====================================================================
# Plan refinement (Sonnet -- single-turn, no tools)
# =====================================================================

REFINE_SYSTEM_PROMPT = """\
You are a plan editor for an AI agent onboarding system. You receive a \
draft bootstrap plan (departments, roles, skills) and natural language \
feedback from a human reviewer. Your job is to return structured JSON \
modifications that implement the reviewer's feedback.

IMPORTANT:
- Only modify what the feedback explicitly asks for.
- If feedback says "add X", return an "add" operation.
- If feedback says "remove X" or "delete X", return a "remove" operation.
- If feedback says "rename X" or "change X", return a "modify" operation.
- Keep IDs as lowercase_underscored slugs.
- Be conservative: do not make changes beyond what was requested.

Return ONLY a JSON object (no markdown fences) with this structure:
{{
  "changes": [
    {{
      "action": "add" | "remove" | "modify",
      "entity_type": "department" | "role" | "skill",
      "entity_id": "slug_id",
      "fields": {{"field": "value"}}
    }}
  ],
  "explanation": "Brief description of what was changed and why"
}}

For "add" actions, include all required fields in "fields":
- department: id, name, description
- role: id, name, department_id, description
- skill: id, name, role_id, department_id, description

For "modify" actions, only include the fields being changed.
For "remove" actions, fields can be empty or omitted.
"""

REFINE_USER_TEMPLATE = """\
Current plan summary:
{plan_summary}

User feedback:
{feedback}"""
