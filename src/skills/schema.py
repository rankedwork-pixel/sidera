"""Skill, role, and department schema and YAML loaders for Sidera.

Defines a three-level hierarchy:

- ``DepartmentDefinition`` — top-level grouping (e.g., "marketing")
- ``RoleDefinition`` — an AI employee within a department
- ``SkillDefinition`` — a specific task the employee can perform

Skills can be organized in a hierarchy on disk::

    library/
      marketing/                    ← Department
        _department.yaml
        performance_media_buyer/    ← Role
          _role.yaml
          creative_analysis/        ← Skill (folder-based)
            skill.yaml
          budget_reallocation.yaml  ← Skill (flat)

Or as standalone files (backward compatible)::

    library/
      creative_analysis.yaml        ← Skill with no dept/role

Folder-based skills support ``context_files`` — glob patterns that
resolve to markdown files injected into the system prompt.

Each YAML file must contain all required fields. Optional fields default
to sensible values.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from src.agent.prompts import ALL_TOOLS

# =============================================================================
# Exceptions
# =============================================================================


class SkillLoadError(Exception):
    """Raised when a skill YAML file cannot be loaded or parsed."""

    pass


class SkillValidationError(Exception):
    """Raised when a loaded skill fails validation."""

    pass


# =============================================================================
# Valid values
# =============================================================================

VALID_MODELS = frozenset({"haiku", "sonnet", "opus"})
VALID_CATEGORIES = frozenset(
    {
        "analysis",
        "optimization",
        "reporting",
        "monitoring",
        "creative",
        "audience",
        "bidding",
        "budget",
        "forecasting",
        "attribution",
        "operations",
    }
)
VALID_PLATFORMS = frozenset({"google_ads", "meta", "bigquery", "google_drive"})


# =============================================================================
# Skill definition dataclass
# =============================================================================


@dataclass(frozen=True)
class SkillDefinition:
    """Immutable definition of a Sidera skill loaded from YAML.

    Each field maps directly to a top-level key in the YAML file.
    The dataclass is frozen so that loaded skills cannot be accidentally
    mutated at runtime.

    A skill can be defined as either a flat YAML file or a folder
    containing ``skill.yaml`` plus context subdirectories.  When
    ``context_files`` patterns are specified, the executor reads matching
    files from ``source_dir`` and injects their contents into the system
    prompt as additional context sections.
    """

    # --- Identity ---
    id: str
    name: str
    version: str
    description: str

    # --- Classification ---
    category: str
    platforms: tuple[str, ...]
    tags: tuple[str, ...]

    # --- Execution ---
    tools_required: tuple[str, ...]
    model: str
    max_turns: int

    # --- Prompt composition ---
    system_supplement: str
    prompt_template: str
    output_format: str

    # --- Business guidance ---
    business_guidance: str

    # --- Context files (folder-based skills) ---
    context_files: tuple[str, ...] = ()
    context_file_descriptions: tuple[tuple[str, str], ...] = ()  # (pattern, description) pairs
    source_dir: str = ""

    # --- Pre-rendered context (DB-defined skills) ---
    context_text: str = ""

    # --- Code-backed skills ---
    skill_type: str = "llm"  # "llm" (default) or "code_backed"
    code_entrypoint: str = ""  # relative path, e.g. "code/run.py"
    code_timeout_seconds: int = 300  # subprocess timeout (5 min default)
    code_output_patterns: tuple[str, ...] = ()  # e.g. ("output/*.csv",)

    # --- Scheduling ---
    schedule: str | None = None

    # --- Chaining ---
    chain_after: str | None = None
    requires_approval: bool = True

    # --- Information clearance ---
    min_clearance: str = "public"  # Minimum clearance to run/view this skill's output

    # --- Cross-skill references (skill graphs) ---
    references: tuple[tuple[str, str, str], ...] = ()  # (skill_id, relationship, reason)

    # --- Hierarchy (set by registry based on disk location, not in YAML) ---
    department_id: str = ""
    role_id: str = ""

    # --- Metadata ---
    author: str = "sidera"
    created_at: str = ""
    updated_at: str = ""


# =============================================================================
# Department definition dataclass
# =============================================================================


@dataclass(frozen=True)
class DepartmentDefinition:
    """Immutable definition of a department loaded from ``_department.yaml``.

    Departments are the top-level grouping in the skill hierarchy.
    Their ``context`` and ``context_files`` are injected into the system
    prompt for every role and skill within the department.
    """

    id: str
    name: str
    description: str
    context: str = ""
    context_files: tuple[str, ...] = ()
    source_dir: str = ""
    context_text: str = ""

    # --- Domain vocabulary (injected into every role context) ---
    vocabulary: tuple[tuple[str, str], ...] = ()  # (term, definition) pairs

    # --- Routing keywords (for data-driven role router) ---
    routing_keywords: tuple[str, ...] = ()  # words/phrases that route to this dept's head

    # --- Stewardship ---
    steward: str = ""  # Slack user ID of the department steward

    # --- Department-scoped infrastructure ---
    slack_channel_id: str = ""  # Dedicated Slack channel for this department
    credentials_scope: str = ""  # "department" or "" (global fallback)


# =============================================================================
# Role definition dataclass
# =============================================================================


@dataclass(frozen=True)
class RoleDefinition:
    """Immutable definition of a role loaded from ``_role.yaml``.

    A role represents an AI employee within a department. It declares
    a ``persona`` (injected into the system prompt), the ``connectors``
    it needs, and an ordered list of ``briefing_skills`` that compose
    its daily briefing when "run as a role."
    """

    id: str
    name: str
    department_id: str
    description: str
    persona: str = ""
    connectors: tuple[str, ...] = ()
    briefing_skills: tuple[str, ...] = ()
    schedule: str | None = None
    context_files: tuple[str, ...] = ()
    source_dir: str = ""
    context_text: str = ""

    # --- Decision-making principles ---
    principles: tuple[str, ...] = ()  # decision heuristics beyond persona

    # --- Active goals (always-present decision filters) ---
    goals: tuple[str, ...] = ()  # what the role is trying to achieve

    # --- Manager fields ---
    manages: tuple[str, ...] = ()  # role IDs this manager directs
    delegation_model: str = "standard"  # "standard" (Sonnet) or "fast" (Haiku)
    synthesis_prompt: str = ""  # custom synthesis instructions

    # --- Information clearance ---
    clearance_level: str = "internal"  # This role's own clearance for agent-to-agent sharing

    # --- Routing keywords (for data-driven role router) ---
    routing_keywords: tuple[str, ...] = ()  # words/phrases that route to this role

    # --- Proactive heartbeat fields ---
    heartbeat_schedule: str | None = None  # cron expression for proactive check-ins
    heartbeat_model: str = ""  # model override (defaults to model_fast/Haiku)

    # --- Stewardship ---
    steward: str = ""  # Slack user ID of the human steward

    # --- Agent-to-agent learning ---
    learning_channels: tuple[str, ...] = ()  # role IDs that can push learnings to this role

    # --- Document sync (living documents) ---
    document_sync: tuple[tuple[str, str], ...] = ()  # (output_type, doc_id) pairs

    # --- Event subscriptions (always-on monitoring) ---
    event_subscriptions: tuple[str, ...] = ()  # event types this role handles


# =============================================================================
# Required fields
# =============================================================================

_REQUIRED_FIELDS = frozenset(
    {
        "id",
        "name",
        "version",
        "description",
        "category",
        "platforms",
        "tags",
        "tools_required",
        "model",
        "system_supplement",
        "prompt_template",
        "output_format",
        "business_guidance",
    }
)


# =============================================================================
# YAML loader
# =============================================================================


def load_skill_from_yaml(path: Path) -> SkillDefinition:
    """Load a ``SkillDefinition`` from a YAML file.

    Args:
        path: Path to the ``.yaml`` file.

    Returns:
        A validated ``SkillDefinition`` instance.

    Raises:
        SkillLoadError: If the file cannot be read, parsed, or is missing
            required fields.
    """
    if not path.exists():
        raise SkillLoadError(f"Skill file not found: {path}")

    if path.suffix not in (".yaml", ".yml"):
        raise SkillLoadError(f"Expected .yaml or .yml file, got: {path.suffix}")

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillLoadError(f"Cannot read skill file {path}: {exc}") from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise SkillLoadError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise SkillLoadError(f"Expected a YAML mapping in {path}, got {type(data).__name__}")

    # Check required fields
    missing = _REQUIRED_FIELDS - set(data.keys())
    if missing:
        raise SkillLoadError(f"Missing required fields in {path}: {', '.join(sorted(missing))}")

    # Build the SkillDefinition
    try:
        skill = SkillDefinition(
            id=str(data["id"]),
            name=str(data["name"]),
            version=str(data["version"]),
            description=str(data["description"]),
            category=str(data["category"]),
            platforms=tuple(str(p) for p in data.get("platforms", [])),
            tags=tuple(str(t) for t in data.get("tags", [])),
            tools_required=tuple(str(t) for t in data.get("tools_required", [])),
            model=str(data["model"]),
            max_turns=int(data.get("max_turns", 20)),
            system_supplement=str(data["system_supplement"]),
            prompt_template=str(data["prompt_template"]),
            output_format=str(data["output_format"]),
            business_guidance=str(data["business_guidance"]),
            context_files=tuple(str(cf) for cf in data.get("context_files", [])),
            context_file_descriptions=tuple(
                (str(d.get("pattern", "")), str(d.get("description", "")))
                for d in data.get("context_file_descriptions", [])
                if isinstance(d, dict) and d.get("pattern")
            ),
            source_dir=str(path.parent),
            schedule=data.get("schedule"),
            chain_after=data.get("chain_after"),
            requires_approval=bool(data.get("requires_approval", True)),
            min_clearance=str(data.get("min_clearance", "public")),
            skill_type=str(data.get("skill_type", "llm")),
            code_entrypoint=str(data.get("code_entrypoint", "")),
            code_timeout_seconds=int(data.get("code_timeout_seconds", 300)),
            code_output_patterns=tuple(str(p) for p in data.get("code_output_patterns", [])),
            references=tuple(
                (
                    str(r.get("skill_id", "")),
                    str(r.get("relationship", "")),
                    str(r.get("reason", "")),
                )
                for r in data.get("references", [])
                if isinstance(r, dict) and r.get("skill_id")
            ),
            author=str(data.get("author", "sidera")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise SkillLoadError(f"Error constructing SkillDefinition from {path}: {exc}") from exc

    return skill


# =============================================================================
# Validation
# =============================================================================


def validate_skill(skill: SkillDefinition) -> list[str]:
    """Validate a ``SkillDefinition`` and return a list of error messages.

    Checks include:
    - ID is non-empty and contains only valid characters
    - Model is one of the allowed values
    - Category is one of the allowed values
    - All platforms are recognized
    - All tools_required are in ALL_TOOLS
    - max_turns is within a reasonable range
    - Prompt template and system supplement are non-empty
    - chain_after is not self-referencing

    Args:
        skill: The skill definition to validate.

    Returns:
        List of error strings. Empty list means the skill is valid.
    """
    errors: list[str] = []

    # --- ID validation ---
    if not skill.id:
        errors.append("Skill ID is empty")
    elif not skill.id.replace("_", "").replace("-", "").isalnum():
        errors.append(
            f"Skill ID '{skill.id}' contains invalid characters "
            "(only alphanumeric, underscore, hyphen allowed)"
        )

    # --- Name validation ---
    if not skill.name:
        errors.append("Skill name is empty")

    # --- Version validation ---
    if not skill.version:
        errors.append("Skill version is empty")

    # --- Description validation ---
    if not skill.description:
        errors.append("Skill description is empty")

    # --- Model validation ---
    if skill.model not in VALID_MODELS:
        errors.append(
            f"Invalid model '{skill.model}'. Must be one of: {', '.join(sorted(VALID_MODELS))}"
        )

    # --- Category validation ---
    if skill.category not in VALID_CATEGORIES:
        errors.append(
            f"Invalid category '{skill.category}'. "
            f"Must be one of: {', '.join(sorted(VALID_CATEGORIES))}"
        )

    # --- Platform validation ---
    for platform in skill.platforms:
        if platform not in VALID_PLATFORMS:
            errors.append(
                f"Unknown platform '{platform}'. "
                f"Must be one of: {', '.join(sorted(VALID_PLATFORMS))}"
            )

    # --- Tools validation ---
    known_tools = set(ALL_TOOLS)
    for tool in skill.tools_required:
        if tool not in known_tools:
            errors.append(f"Unknown tool '{tool}' in tools_required")

    # --- max_turns validation ---
    if skill.max_turns < 1:
        errors.append(f"max_turns must be >= 1, got {skill.max_turns}")
    elif skill.max_turns > 50:
        errors.append(f"max_turns must be <= 50, got {skill.max_turns}")

    # --- Prompt validation ---
    if not skill.system_supplement.strip():
        errors.append("system_supplement is empty")

    if not skill.prompt_template.strip():
        errors.append("prompt_template is empty")

    if not skill.output_format.strip():
        errors.append("output_format is empty")

    if not skill.business_guidance.strip():
        errors.append("business_guidance is empty")

    # --- Context files validation ---
    if skill.context_files:
        if not skill.source_dir:
            errors.append(
                "context_files specified but source_dir is empty (cannot resolve file paths)"
            )
        else:
            source = Path(skill.source_dir)
            if source.exists():
                resolved = resolve_context_files(skill)
                if not resolved:
                    errors.append(
                        f"context_files patterns {list(skill.context_files)} "
                        f"matched no files in {skill.source_dir}"
                    )

    # --- Clearance validation ---
    valid_clearance_levels = ("public", "internal", "confidential", "restricted")
    if skill.min_clearance not in valid_clearance_levels:
        errors.append(
            f"Invalid min_clearance '{skill.min_clearance}'. "
            f"Must be one of: {', '.join(valid_clearance_levels)}"
        )

    # --- Chain validation ---
    if skill.chain_after and skill.chain_after == skill.id:
        errors.append(
            f"Skill '{skill.id}' has chain_after pointing to itself (would cause infinite loop)"
        )

    # --- References validation ---
    if skill.references:
        seen_refs: set[str] = set()
        for ref_tuple in skill.references:
            ref_skill_id = ref_tuple[0] if ref_tuple else ""
            if not ref_skill_id:
                errors.append("Reference skill_id cannot be empty")
            elif ref_skill_id == skill.id:
                errors.append(f"Skill '{skill.id}' references itself (self-reference not allowed)")
            elif ref_skill_id in seen_refs:
                errors.append(f"Duplicate reference to skill '{ref_skill_id}'")
            if ref_skill_id:
                seen_refs.add(ref_skill_id)

    # --- Code-backed skill validation ---
    valid_skill_types = ("llm", "code_backed")
    if skill.skill_type not in valid_skill_types:
        errors.append(
            f"Invalid skill_type '{skill.skill_type}'. "
            f"Must be one of: {', '.join(valid_skill_types)}"
        )

    if skill.skill_type == "code_backed":
        if not skill.code_entrypoint:
            errors.append("code_backed skill must specify code_entrypoint")
        elif skill.source_dir:
            entrypoint_path = Path(skill.source_dir) / skill.code_entrypoint
            if Path(skill.source_dir).exists() and not entrypoint_path.exists():
                errors.append(
                    f"code_entrypoint '{skill.code_entrypoint}' does not exist at {entrypoint_path}"
                )

        if not (1 <= skill.code_timeout_seconds <= 3600):
            errors.append(f"code_timeout_seconds must be 1-3600, got {skill.code_timeout_seconds}")

        if "run_skill_code" not in skill.tools_required:
            errors.append("code_backed skill must include 'run_skill_code' in tools_required")

    return errors


# =============================================================================
# Context file resolution
# =============================================================================


def resolve_context_files(skill: SkillDefinition) -> list[Path]:
    """Resolve ``context_files`` glob patterns to actual file paths.

    Scans ``skill.source_dir`` for files matching each glob pattern
    in ``skill.context_files``.  Returns a deduplicated, sorted list
    of paths.

    Args:
        skill: The skill definition with ``context_files`` and
            ``source_dir`` set.

    Returns:
        Sorted list of resolved file paths.  Empty if no patterns
        are configured or no files match.
    """
    if not skill.context_files or not skill.source_dir:
        return []

    source = Path(skill.source_dir)
    if not source.exists():
        return []

    seen: set[Path] = set()
    resolved: list[Path] = []

    for pattern in skill.context_files:
        for match in sorted(source.glob(pattern)):
            if match.is_file() and match not in seen:
                seen.add(match)
                resolved.append(match)

    return resolved


def load_context_text(skill: SkillDefinition, lazy: bool = False) -> str:
    """Read all context files and return combined text for prompt injection.

    If ``skill.context_text`` is non-empty (DB-defined skills), returns
    it directly without resolving filesystem paths.  Otherwise, resolves
    ``context_files`` glob patterns against ``source_dir``.

    When ``lazy=True`` (for multi-turn skills), returns a lightweight
    manifest of available files instead of the full text.  The agent
    can then use the ``load_skill_context`` MCP tool to fetch specific
    files on demand, saving tokens when the context isn't needed.

    Each resolved file is wrapped in a section header derived from its
    relative path (e.g. ``examples/good_analysis_01.md`` becomes
    ``# Context: examples/good_analysis_01.md``).

    Args:
        skill: The skill definition to load context for.
        lazy: If True, return a manifest instead of full text
            (for multi-turn skills where the agent can load on demand).

    Returns:
        Combined context text ready for injection into the system prompt.
        Empty string if no context files are configured or found.
    """
    # DB-defined skills carry pre-rendered context (always eager)
    if skill.context_text:
        return skill.context_text

    files = resolve_context_files(skill)
    if not files and not skill.references:
        return ""

    # Lazy mode: return manifest instead of full content
    if lazy:
        from src.mcp_servers.context import build_context_manifest

        # Build descriptions dict from (pattern, description) pairs
        descriptions: dict[str, str] | None = None
        if skill.context_file_descriptions:
            descriptions = {pattern: desc for pattern, desc in skill.context_file_descriptions}

        manifest = build_context_manifest(
            skill_id=skill.id,
            source_dir=skill.source_dir,
            context_files=skill.context_files,
            descriptions=descriptions,
            references=skill.references,
        )
        return manifest

    source = Path(skill.source_dir)
    sections: list[str] = []

    for fpath in files:
        try:
            content = fpath.read_text(encoding="utf-8").strip()
            if not content:
                continue
            relative = fpath.relative_to(source)
            sections.append(f"# Context: {relative}\n\n{content}")
        except OSError:
            continue  # Skip unreadable files silently

    return "\n\n".join(sections)


# =============================================================================
# Department YAML loader
# =============================================================================

_REQUIRED_DEPARTMENT_FIELDS = frozenset({"id", "name", "description"})


def load_department_from_yaml(path: Path) -> DepartmentDefinition:
    """Load a ``DepartmentDefinition`` from a ``_department.yaml`` file.

    Args:
        path: Path to the ``.yaml`` file.

    Returns:
        A ``DepartmentDefinition`` instance.

    Raises:
        SkillLoadError: If the file cannot be read, parsed, or is missing
            required fields.
    """
    if not path.exists():
        raise SkillLoadError(f"Department file not found: {path}")

    if path.suffix not in (".yaml", ".yml"):
        raise SkillLoadError(f"Expected .yaml or .yml file, got: {path.suffix}")

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillLoadError(f"Cannot read department file {path}: {exc}") from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise SkillLoadError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise SkillLoadError(f"Expected a YAML mapping in {path}, got {type(data).__name__}")

    missing = _REQUIRED_DEPARTMENT_FIELDS - set(data.keys())
    if missing:
        raise SkillLoadError(f"Missing required fields in {path}: {', '.join(sorted(missing))}")

    try:
        return DepartmentDefinition(
            id=str(data["id"]),
            name=str(data["name"]),
            description=str(data["description"]),
            context=str(data.get("context", "")),
            context_files=tuple(str(cf) for cf in data.get("context_files", [])),
            source_dir=str(path.parent),
            vocabulary=tuple(
                (str(v.get("term", "")), str(v.get("definition", "")))
                for v in data.get("vocabulary", [])
                if isinstance(v, dict) and v.get("term")
            ),
            routing_keywords=tuple(str(k) for k in data.get("routing_keywords", [])),
            steward=str(data.get("steward", "")),
            slack_channel_id=str(data.get("slack_channel_id", "")),
            credentials_scope=str(data.get("credentials_scope", "")),
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise SkillLoadError(f"Error constructing DepartmentDefinition from {path}: {exc}") from exc


# =============================================================================
# Role YAML loader
# =============================================================================

_REQUIRED_ROLE_FIELDS = frozenset(
    {
        "id",
        "name",
        "department_id",
        "description",
    }
)


def load_role_from_yaml(path: Path) -> RoleDefinition:
    """Load a ``RoleDefinition`` from a ``_role.yaml`` file.

    Args:
        path: Path to the ``.yaml`` file.

    Returns:
        A ``RoleDefinition`` instance.

    Raises:
        SkillLoadError: If the file cannot be read, parsed, or is missing
            required fields.
    """
    if not path.exists():
        raise SkillLoadError(f"Role file not found: {path}")

    if path.suffix not in (".yaml", ".yml"):
        raise SkillLoadError(f"Expected .yaml or .yml file, got: {path.suffix}")

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillLoadError(f"Cannot read role file {path}: {exc}") from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise SkillLoadError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise SkillLoadError(f"Expected a YAML mapping in {path}, got {type(data).__name__}")

    missing = _REQUIRED_ROLE_FIELDS - set(data.keys())
    if missing:
        raise SkillLoadError(f"Missing required fields in {path}: {', '.join(sorted(missing))}")

    try:
        return RoleDefinition(
            id=str(data["id"]),
            name=str(data["name"]),
            department_id=str(data["department_id"]),
            description=str(data["description"]),
            persona=str(data.get("persona", "")),
            connectors=tuple(str(c) for c in data.get("connectors", [])),
            briefing_skills=tuple(str(s) for s in data.get("briefing_skills", [])),
            schedule=data.get("schedule"),
            context_files=tuple(str(cf) for cf in data.get("context_files", [])),
            source_dir=str(path.parent),
            principles=tuple(str(p) for p in data.get("principles", [])),
            goals=tuple(str(g) for g in data.get("goals", [])),
            manages=tuple(str(m) for m in data.get("manages", [])),
            delegation_model=str(data.get("delegation_model", "standard")),
            synthesis_prompt=str(data.get("synthesis_prompt", "")),
            clearance_level=str(data.get("clearance_level", "internal")),
            routing_keywords=tuple(str(k) for k in data.get("routing_keywords", [])),
            heartbeat_schedule=data.get("heartbeat_schedule"),
            heartbeat_model=str(data.get("heartbeat_model", "")),
            steward=str(data.get("steward", "")),
            learning_channels=tuple(str(lc) for lc in data.get("learning_channels", [])),
            document_sync=tuple(
                (str(d.get("type", "")), str(d.get("doc_id", "")))
                for d in data.get("document_sync", [])
                if isinstance(d, dict) and d.get("type") and d.get("doc_id")
            ),
            event_subscriptions=tuple(str(e) for e in data.get("event_subscriptions", [])),
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise SkillLoadError(f"Error constructing RoleDefinition from {path}: {exc}") from exc


# =============================================================================
# Department / Role validation
# =============================================================================


def validate_department(dept: DepartmentDefinition) -> list[str]:
    """Validate a ``DepartmentDefinition`` and return error messages.

    Args:
        dept: The department definition to validate.

    Returns:
        List of error strings. Empty list means valid.
    """
    errors: list[str] = []

    if not dept.id:
        errors.append("Department ID is empty")
    elif not dept.id.replace("_", "").replace("-", "").isalnum():
        errors.append(
            f"Department ID '{dept.id}' contains invalid characters "
            "(only alphanumeric, underscore, hyphen allowed)"
        )

    if not dept.name:
        errors.append("Department name is empty")

    if not dept.description:
        errors.append("Department description is empty")

    if dept.context_files and not dept.source_dir:
        errors.append("context_files specified but source_dir is empty")

    return errors


def validate_role(role: RoleDefinition) -> list[str]:
    """Validate a ``RoleDefinition`` and return error messages.

    Args:
        role: The role definition to validate.

    Returns:
        List of error strings. Empty list means valid.
    """
    errors: list[str] = []

    if not role.id:
        errors.append("Role ID is empty")
    elif not role.id.replace("_", "").replace("-", "").isalnum():
        errors.append(
            f"Role ID '{role.id}' contains invalid characters "
            "(only alphanumeric, underscore, hyphen allowed)"
        )

    if not role.name:
        errors.append("Role name is empty")

    if not role.department_id:
        errors.append("Role department_id is empty")

    if not role.description:
        errors.append("Role description is empty")

    if not role.briefing_skills and not role.manages:
        errors.append("Role has no briefing_skills or manages defined")

    if role.context_files and not role.source_dir:
        errors.append("context_files specified but source_dir is empty")

    # --- Clearance validation ---
    valid_clearance_levels = ("public", "internal", "confidential", "restricted")
    if role.clearance_level not in valid_clearance_levels:
        errors.append(
            f"Invalid clearance_level '{role.clearance_level}'. "
            f"Must be one of: {', '.join(valid_clearance_levels)}"
        )

    # --- Manager field validation ---
    valid_delegation_models = ("standard", "fast")
    if role.delegation_model not in valid_delegation_models:
        errors.append(
            f"Invalid delegation_model '{role.delegation_model}'. "
            f"Must be one of: {', '.join(valid_delegation_models)}"
        )

    for managed_id in role.manages:
        if not managed_id.replace("_", "").replace("-", "").isalnum():
            errors.append(
                f"Invalid managed role ID '{managed_id}' — "
                "only alphanumeric, underscore, hyphen allowed"
            )

    return errors


# =============================================================================
# Generic context resolution for dept / role
# =============================================================================


def resolve_hierarchy_context_files(
    context_files: tuple[str, ...],
    source_dir: str,
) -> list[Path]:
    """Resolve context file glob patterns for a dept or role.

    Same logic as ``resolve_context_files`` but works with raw
    tuples instead of requiring a ``SkillDefinition``.

    Args:
        context_files: Glob patterns to match.
        source_dir: Base directory to resolve patterns against.

    Returns:
        Sorted, deduplicated list of resolved file paths.
    """
    if not context_files or not source_dir:
        return []

    source = Path(source_dir)
    if not source.exists():
        return []

    seen: set[Path] = set()
    resolved: list[Path] = []

    for pattern in context_files:
        for match in sorted(source.glob(pattern)):
            if match.is_file() and match not in seen:
                seen.add(match)
                resolved.append(match)

    return resolved


def load_hierarchy_context_text(
    context_files: tuple[str, ...],
    source_dir: str,
    context_text: str = "",
) -> str:
    """Read context files for a department or role.

    If ``context_text`` is non-empty (DB-defined entries), returns it
    directly without resolving filesystem paths.

    Args:
        context_files: Glob patterns to match.
        source_dir: Base directory to resolve patterns against.
        context_text: Pre-rendered context text (from DB). If non-empty,
            returned directly without filesystem resolution.

    Returns:
        Combined context text, or empty string if none found.
    """
    # DB-defined entries carry pre-rendered context
    if context_text:
        return context_text

    files = resolve_hierarchy_context_files(context_files, source_dir)
    if not files:
        return ""

    source = Path(source_dir)
    sections: list[str] = []

    for fpath in files:
        try:
            content = fpath.read_text(encoding="utf-8").strip()
            if not content:
                continue
            relative = fpath.relative_to(source)
            sections.append(f"# Context: {relative}\n\n{content}")
        except OSError:
            continue

    return "\n\n".join(sections)
