"""Anthropic Agent Skills format compatibility for Sidera.

Provides bidirectional conversion between Anthropic's ``SKILL.md`` format
(YAML frontmatter + markdown body) and Sidera's ``skill.yaml`` format.

Anthropic format::

    skill-name/
        SKILL.md          # YAML frontmatter + markdown instructions
        scripts/          # Executable code
        references/       # Documentation
        assets/           # Templates, fonts, icons

Sidera format::

    skill_name/
        skill.yaml        # All config + prompts
        context/          # Context files
        examples/         # Examples
        guidelines/       # Guidelines
        code/             # Code-backed skill scripts

Usage::

    from src.skills.anthropic_compat import (
        parse_skill_md,
        anthropic_to_sidera,
        sidera_to_anthropic,
        import_anthropic_skill,
        export_to_anthropic_dir,
    )

    # Import an Anthropic skill
    bundle = parse_skill_md(Path("my-skill/SKILL.md"))
    sidera_dict = anthropic_to_sidera(bundle)

    # Export a Sidera skill
    anthropic_bundle = sidera_to_anthropic(skill_definition)
    write_skill_md(anthropic_bundle, Path("/tmp/export"))
"""

from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.skills.portability import ImportResult

# =============================================================================
# Constants
# =============================================================================

# Tool name mapping: Anthropic (Claude Code / Claude.ai) → Sidera MCP tools.
# The two ecosystems are fundamentally different — this mapping is intentionally
# sparse.  Unmapped tools are preserved in metadata for round-trip fidelity.
_ANTHROPIC_TO_SIDERA_TOOLS: dict[str, str | None] = {
    "Bash": None,
    "Read": None,
    "Write": None,
    "Edit": None,
    "Glob": None,
    "Grep": None,
    "WebFetch": "fetch_web_page",
    "WebSearch": "web_search",
    "TodoWrite": None,
    "NotebookEdit": None,
}

_SIDERA_TO_ANTHROPIC_TOOLS: dict[str, str] = {
    v: k for k, v in _ANTHROPIC_TO_SIDERA_TOOLS.items() if v is not None
}

# Subdirectories in an Anthropic skill bundle
_ANTHROPIC_SUBDIRS = ("scripts", "references", "assets")

# Kebab-case validation
_KEBAB_CASE_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


# =============================================================================
# Data structure
# =============================================================================


@dataclass
class AnthropicSkillBundle:
    """Parsed representation of an Anthropic SKILL.md bundle."""

    name: str = ""
    description: str = ""
    license: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    compatibility: str = ""
    body_markdown: str = ""
    scripts: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    source_dir: str = ""


# =============================================================================
# Parsing: SKILL.md → AnthropicSkillBundle
# =============================================================================


def parse_skill_md(path: str | Path) -> AnthropicSkillBundle:
    """Parse a SKILL.md file (or directory containing one) into a bundle.

    Args:
        path: Path to SKILL.md file or to a directory containing SKILL.md.

    Returns:
        Parsed :class:`AnthropicSkillBundle`.

    Raises:
        FileNotFoundError: If SKILL.md does not exist.
        ValueError: If YAML frontmatter is malformed.
    """
    path = Path(path)
    if path.is_dir():
        skill_md = path / "SKILL.md"
    else:
        skill_md = path
        path = path.parent

    if not skill_md.exists():
        raise FileNotFoundError(f"SKILL.md not found at {skill_md}")

    raw = skill_md.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(raw)

    if frontmatter is None:
        raise ValueError(f"No YAML frontmatter found in {skill_md}")

    data = yaml.safe_load(frontmatter) or {}

    # Parse allowed-tools (can be string or list)
    allowed_tools_raw = data.get("allowed-tools", [])
    if isinstance(allowed_tools_raw, str):
        allowed_tools = allowed_tools_raw.split()
    elif isinstance(allowed_tools_raw, list):
        allowed_tools = [str(t) for t in allowed_tools_raw]
    else:
        allowed_tools = []

    # Scan sibling directories
    scripts = _scan_subdir(path, "scripts")
    references = _scan_subdir(path, "references")
    assets = _scan_subdir(path, "assets")

    return AnthropicSkillBundle(
        name=data.get("name", ""),
        description=data.get("description", ""),
        license=data.get("license", ""),
        allowed_tools=allowed_tools,
        metadata=data.get("metadata", {}),
        compatibility=str(data.get("compatibility", "")),
        body_markdown=body.strip(),
        scripts=scripts,
        references=references,
        assets=assets,
        source_dir=str(path),
    )


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    """Split YAML frontmatter from markdown body.

    Returns (frontmatter_yaml, body_markdown).  If no frontmatter is
    found, returns (None, original_text).
    """
    # Frontmatter must start at the very beginning of the file
    if not text.startswith("---"):
        return None, text

    # Find the closing ---
    end = text.find("\n---", 3)
    if end == -1:
        return None, text

    frontmatter = text[3:end].strip()
    body = text[end + 4 :]  # skip past \n---
    return frontmatter, body


def _scan_subdir(base: Path, subdir: str) -> list[str]:
    """List all files in a subdirectory, relative to *base*."""
    d = base / subdir
    if not d.is_dir():
        return []
    return sorted(str(f.relative_to(base)) for f in d.rglob("*") if f.is_file())


# =============================================================================
# Writing: AnthropicSkillBundle → SKILL.md
# =============================================================================


def write_skill_md(bundle: AnthropicSkillBundle, output_dir: str | Path) -> Path:
    """Write an AnthropicSkillBundle to disk as a SKILL.md directory.

    Creates the directory structure expected by Anthropic's skill format.

    Args:
        bundle: The skill bundle to write.
        output_dir: Target directory (will be created).

    Returns:
        Path to the created directory.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build frontmatter dict
    fm: dict[str, Any] = {"name": bundle.name, "description": bundle.description}
    if bundle.license:
        fm["license"] = bundle.license
    if bundle.allowed_tools:
        fm["allowed-tools"] = bundle.allowed_tools
    if bundle.compatibility:
        fm["compatibility"] = bundle.compatibility
    if bundle.metadata:
        fm["metadata"] = bundle.metadata

    # Write SKILL.md
    frontmatter_yaml = yaml.dump(fm, default_flow_style=False, sort_keys=False)
    content = f"---\n{frontmatter_yaml}---\n\n{bundle.body_markdown}\n"
    (output_dir / "SKILL.md").write_text(content, encoding="utf-8")

    # Copy supporting files from source_dir if available
    if bundle.source_dir:
        src = Path(bundle.source_dir)
        for subdir in _ANTHROPIC_SUBDIRS:
            src_sub = src / subdir
            if src_sub.is_dir():
                dst_sub = output_dir / subdir
                if src_sub != dst_sub:
                    shutil.copytree(src_sub, dst_sub, dirs_exist_ok=True)

    return output_dir


# =============================================================================
# Conversion: Anthropic → Sidera
# =============================================================================


def anthropic_to_sidera(
    bundle: AnthropicSkillBundle,
) -> tuple[dict[str, Any], list[str]]:
    """Convert an Anthropic skill bundle to a Sidera skill definition dict.

    Args:
        bundle: Parsed Anthropic skill bundle.

    Returns:
        Tuple of (sidera_dict, warnings).  The dict can be passed to
        ``SkillDefinition(**sidera_dict)`` after tuple conversion.
    """
    warnings: list[str] = []

    # Name conversion: kebab-case → underscore
    skill_id = bundle.name.replace("-", "_")
    skill_name = bundle.name.replace("-", " ").title()

    # Tool mapping
    tools_required: list[str] = []
    unmapped_tools: list[str] = []
    for tool in bundle.allowed_tools:
        # Strip any parenthetical qualifiers like "Bash(python:*)"
        base_tool = tool.split("(")[0]
        sidera_tool = _ANTHROPIC_TO_SIDERA_TOOLS.get(base_tool)
        if sidera_tool is not None:
            tools_required.append(sidera_tool)
        else:
            unmapped_tools.append(tool)

    if unmapped_tools:
        warnings.append(
            f"These Anthropic tools have no Sidera MCP equivalent and were "
            f"preserved in metadata: {', '.join(unmapped_tools)}"
        )

    # Extract metadata fields
    meta = bundle.metadata or {}
    author = meta.get("author", "imported")
    version = meta.get("version", "1.0")
    category = meta.get("category", "operations")
    tags = meta.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]

    # Build Sidera dict with sensible defaults
    result: dict[str, Any] = {
        "id": skill_id,
        "name": skill_name,
        "version": str(version),
        "description": bundle.description,
        "category": category,
        "tags": tags,
        "tools_required": tools_required,
        "model": "sonnet",
        "max_turns": 20,
        "system_supplement": bundle.body_markdown or "Follow the skill instructions.",
        "prompt_template": f"Execute the {skill_name} skill. {{analysis_date}}",
        "output_format": "Produce a clear, structured report.",
        "business_guidance": "Follow the instructions in the system supplement.",
        "requires_approval": True,
        "author": str(author),
    }

    # Preserve Anthropic-specific fields for round-trip
    if bundle.license:
        result.setdefault("_anthropic_metadata", {})["license"] = bundle.license
    if unmapped_tools:
        result.setdefault("_anthropic_metadata", {})["original_allowed_tools"] = unmapped_tools
    if bundle.compatibility:
        result.setdefault("_anthropic_metadata", {})["compatibility"] = bundle.compatibility

    # Round-trip: restore Sidera-specific fields from metadata.sidera
    sidera_meta = meta.get("sidera", {})
    if sidera_meta and isinstance(sidera_meta, dict):
        round_trip_fields = {
            "model",
            "max_turns",
            "schedule",
            "chain_after",
            "requires_approval",
            "min_clearance",
            "platforms",
            "skill_type",
            "code_entrypoint",
            "code_timeout_seconds",
            "references",
        }
        for key, value in sidera_meta.items():
            if key in round_trip_fields:
                result[key] = value

    return result, warnings


# =============================================================================
# Conversion: Sidera → Anthropic
# =============================================================================


def sidera_to_anthropic(skill_dict: dict[str, Any]) -> AnthropicSkillBundle:
    """Convert a Sidera skill definition to an Anthropic skill bundle.

    Args:
        skill_dict: Dict representation of a SkillDefinition (from
            ``dataclasses.asdict()`` or manual construction).

    Returns:
        An :class:`AnthropicSkillBundle` ready to be written to disk.
    """
    skill_id = skill_dict.get("id", "unnamed")

    # Name: underscores → hyphens for kebab-case
    name = skill_id.replace("_", "-")

    # Tool mapping: Sidera MCP → Anthropic where possible
    allowed_tools: list[str] = []
    for tool in skill_dict.get("tools_required", ()) or ():
        anthropic_name = _SIDERA_TO_ANTHROPIC_TOOLS.get(str(tool))
        if anthropic_name:
            allowed_tools.append(anthropic_name)

    # Build structured markdown body from Sidera fields
    sections: list[str] = []

    supplement = skill_dict.get("system_supplement", "")
    if supplement:
        sections.append(f"## Instructions\n\n{supplement}")

    guidance = skill_dict.get("business_guidance", "")
    if guidance:
        sections.append(f"## Business Guidance\n\n{guidance}")

    output_fmt = skill_dict.get("output_format", "")
    if output_fmt:
        sections.append(f"## Expected Output\n\n{output_fmt}")

    template = skill_dict.get("prompt_template", "")
    if template:
        sections.append(f"## Usage\n\n{template}")

    body_markdown = "\n\n".join(sections)

    # Metadata: standard fields + sidera block for round-trip
    metadata: dict[str, Any] = {}
    if skill_dict.get("author"):
        metadata["author"] = skill_dict["author"]
    if skill_dict.get("version"):
        metadata["version"] = skill_dict["version"]
    if skill_dict.get("category"):
        metadata["category"] = skill_dict["category"]
    tags = skill_dict.get("tags", ())
    if tags:
        metadata["tags"] = list(tags) if not isinstance(tags, list) else tags

    # Store Sidera-only fields for round-trip preservation
    sidera_fields: dict[str, Any] = {}
    sidera_only_keys = (
        "model",
        "max_turns",
        "schedule",
        "chain_after",
        "requires_approval",
        "min_clearance",
        "platforms",
        "skill_type",
        "code_entrypoint",
        "code_timeout_seconds",
        "code_output_patterns",
        "references",
        "context_file_descriptions",
    )
    for key in sidera_only_keys:
        val = skill_dict.get(key)
        if val is not None and val != "" and val != ():
            # Convert tuples to lists for YAML
            if isinstance(val, tuple):
                val = list(val)
            sidera_fields[key] = val

    # Also preserve tools_required (full list, not just mapped ones)
    tools_req = skill_dict.get("tools_required", ())
    if tools_req:
        sidera_fields["tools_required"] = (
            list(tools_req) if not isinstance(tools_req, list) else tools_req
        )

    if sidera_fields:
        metadata["sidera"] = sidera_fields

    return AnthropicSkillBundle(
        name=name,
        description=skill_dict.get("description", ""),
        allowed_tools=allowed_tools,
        metadata=metadata,
        body_markdown=body_markdown,
        source_dir=skill_dict.get("source_dir", ""),
    )


# =============================================================================
# Validation
# =============================================================================


def validate_anthropic_bundle(
    path: str | Path,
) -> tuple[bool, list[str], list[str]]:
    """Validate an Anthropic skill bundle.

    Args:
        path: Path to a directory containing SKILL.md.

    Returns:
        Tuple of (is_valid, errors, warnings).
    """
    path = Path(path)
    errors: list[str] = []
    warnings: list[str] = []

    # Must be a directory
    if not path.is_dir():
        errors.append(f"Expected a directory, got: {path}")
        return False, errors, warnings

    # Must contain SKILL.md
    skill_md = path / "SKILL.md"
    if not skill_md.exists():
        errors.append("SKILL.md not found in bundle directory")
        return False, errors, warnings

    # Parse and validate
    try:
        bundle = parse_skill_md(path)
    except (ValueError, yaml.YAMLError) as exc:
        errors.append(f"Failed to parse SKILL.md: {exc}")
        return False, errors, warnings

    if not bundle.name:
        errors.append("Missing required field: name")
    elif not _KEBAB_CASE_RE.match(bundle.name):
        warnings.append(f"Name '{bundle.name}' is not kebab-case (expected format: my-skill-name)")

    if not bundle.description:
        errors.append("Missing required field: description")

    # Check if name matches directory name
    if bundle.name and bundle.name != path.name:
        warnings.append(f"Skill name '{bundle.name}' does not match directory name '{path.name}'")

    return len(errors) == 0, errors, warnings


def is_anthropic_bundle(path: str | Path) -> bool:
    """Check if a path looks like an Anthropic skill bundle.

    Returns True if the path is a directory containing SKILL.md but
    no skill.yaml or manifest.yaml (which would indicate a Sidera bundle).
    Also handles ZIP files.
    """
    path = Path(path)

    if path.is_dir():
        has_skill_md = (path / "SKILL.md").exists()
        has_sidera = (path / "skill.yaml").exists() or (path / "manifest.yaml").exists()
        return has_skill_md and not has_sidera

    if path.suffix == ".zip" and path.is_file():
        try:
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
                has_skill_md = any(n.endswith("SKILL.md") for n in names)
                has_sidera = any(
                    n.endswith("skill.yaml") or n.endswith("manifest.yaml") for n in names
                )
                return has_skill_md and not has_sidera
        except (zipfile.BadZipFile, OSError):
            return False

    return False


# =============================================================================
# Import: Anthropic → Sidera (file operations)
# =============================================================================


def import_anthropic_skill(
    source: str | Path,
    target_dir: str | Path | None = None,
    target_department_id: str = "",
    target_role_id: str = "",
    new_skill_id: str = "",
    new_author: str = "",
) -> ImportResult:
    """Import an Anthropic skill bundle into Sidera format.

    Parses the SKILL.md, converts to Sidera format, and optionally
    installs to disk.

    Args:
        source: Path to Anthropic skill directory or ZIP.
        target_dir: If provided, install the converted skill here.
        target_department_id: Department to assign the skill to.
        target_role_id: Role to assign the skill to.
        new_skill_id: Override the skill ID (for forking).
        new_author: Override the author.

    Returns:
        :class:`ImportResult` with success status and details.
    """
    source = Path(source)
    result = ImportResult()

    # Handle ZIP extraction
    temp_dir: Path | None = None
    if source.suffix == ".zip" and source.is_file():
        import tempfile

        temp_dir = Path(tempfile.mkdtemp(prefix="sidera_anthropic_"))
        try:
            with zipfile.ZipFile(source) as zf:
                zf.extractall(temp_dir)
            # Find the SKILL.md — could be in root or a subdirectory
            skill_mds = list(temp_dir.rglob("SKILL.md"))
            if not skill_mds:
                result.errors.append("No SKILL.md found in ZIP archive")
                return result
            source = skill_mds[0].parent
        except zipfile.BadZipFile:
            result.errors.append("Invalid ZIP file")
            return result

    # Validate
    is_valid, errors, warnings = validate_anthropic_bundle(source)
    result.warnings.extend(warnings)
    if not is_valid:
        result.errors.extend(errors)
        _cleanup_temp(temp_dir)
        return result

    # Parse and convert
    try:
        bundle = parse_skill_md(source)
        sidera_dict, conv_warnings = anthropic_to_sidera(bundle)
        result.warnings.extend(conv_warnings)
    except Exception as exc:
        result.errors.append(f"Conversion failed: {exc}")
        _cleanup_temp(temp_dir)
        return result

    # Apply overrides
    if new_skill_id:
        sidera_dict["id"] = new_skill_id
    if new_author:
        sidera_dict["author"] = new_author
    if target_department_id:
        sidera_dict["department_id"] = target_department_id
    if target_role_id:
        sidera_dict["role_id"] = target_role_id

    result.skill_id = sidera_dict["id"]
    result.skill_name = sidera_dict.get("name", "")
    result.target_department_id = target_department_id
    result.target_role_id = target_role_id

    # Install to disk if target_dir provided
    if target_dir:
        target_dir = Path(target_dir)
        skill_dir = target_dir / sidera_dict["id"]
        skill_dir.mkdir(parents=True, exist_ok=True)

        # Write skill.yaml
        clean_dict = {
            k: v
            for k, v in sidera_dict.items()
            if not k.startswith("_")
            and v is not None
            and k not in ("department_id", "role_id", "source_dir")
        }
        # Convert lists back to lists (some may be tuples)
        for key in ("tags", "tools_required", "platforms"):
            if key in clean_dict and isinstance(clean_dict[key], tuple):
                clean_dict[key] = list(clean_dict[key])

        yaml_path = skill_dir / "skill.yaml"
        yaml_path.write_text(
            yaml.dump(clean_dict, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

        # Copy reference/asset files → context/
        context_count = 0
        for subdir in ("references", "assets"):
            src_sub = Path(bundle.source_dir) / subdir if bundle.source_dir else source / subdir
            if src_sub.is_dir():
                dst = skill_dir / "context" / subdir
                shutil.copytree(src_sub, dst, dirs_exist_ok=True)
                context_count += sum(1 for _ in dst.rglob("*") if _.is_file())

        # Copy scripts → code/
        if bundle.source_dir:
            scripts_src = Path(bundle.source_dir) / "scripts"
        else:
            scripts_src = source / "scripts"
        if scripts_src.is_dir():
            dst = skill_dir / "code"
            shutil.copytree(scripts_src, dst, dirs_exist_ok=True)
            context_count += sum(1 for _ in dst.rglob("*") if _.is_file())

        result.context_files_count = context_count

    result.success = True
    _cleanup_temp(temp_dir)
    return result


def _cleanup_temp(temp_dir: Path | None) -> None:
    """Remove a temporary directory if it exists."""
    if temp_dir and temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)


# =============================================================================
# Export: Sidera → Anthropic (file operations)
# =============================================================================


def export_to_anthropic_dir(
    skill_dict: dict[str, Any],
    output_dir: str | Path,
    source_skill_dir: str | Path | None = None,
) -> Path:
    """Export a Sidera skill to Anthropic's SKILL.md format on disk.

    Args:
        skill_dict: Dict representation of a SkillDefinition.
        output_dir: Parent directory for the output.
        source_skill_dir: Original skill directory (for copying context files).

    Returns:
        Path to the created Anthropic skill directory.
    """
    bundle = sidera_to_anthropic(skill_dict)

    # Create the output directory using the kebab-case name
    skill_dir = Path(output_dir) / bundle.name
    skill_dir.mkdir(parents=True, exist_ok=True)

    # Write SKILL.md
    write_skill_md(bundle, skill_dir)

    # Copy context files → references/
    if source_skill_dir:
        source = Path(source_skill_dir)
        for subdir in ("context", "examples", "guidelines"):
            src = source / subdir
            if src.is_dir():
                dst = skill_dir / "references" / subdir
                shutil.copytree(src, dst, dirs_exist_ok=True)

        # Copy code → scripts/
        code_dir = source / "code"
        if code_dir.is_dir():
            dst = skill_dir / "scripts"
            shutil.copytree(code_dir, dst, dirs_exist_ok=True)

    return skill_dir


def export_to_anthropic_zip(
    skill_dict: dict[str, Any],
    output_path: str | Path,
    source_skill_dir: str | Path | None = None,
) -> Path:
    """Export a Sidera skill to an Anthropic-format ZIP archive.

    Args:
        skill_dict: Dict representation of a SkillDefinition.
        output_path: Path for the output ZIP file.
        source_skill_dir: Original skill directory (for copying context files).

    Returns:
        Path to the created ZIP file.
    """
    import tempfile

    output_path = Path(output_path)

    with tempfile.TemporaryDirectory(prefix="sidera_export_") as tmp:
        skill_dir = export_to_anthropic_dir(skill_dict, tmp, source_skill_dir)

        # Create ZIP
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in skill_dir.rglob("*"):
                if file.is_file():
                    arcname = file.relative_to(Path(tmp))
                    zf.write(file, arcname)

    return output_path


# =============================================================================
# Discovery
# =============================================================================


def list_anthropic_skills(directory: str | Path) -> list[AnthropicSkillBundle]:
    """Scan a directory for Anthropic-format skill bundles.

    Looks for subdirectories containing SKILL.md.

    Args:
        directory: Directory to scan.

    Returns:
        List of parsed :class:`AnthropicSkillBundle` objects.
    """
    directory = Path(directory)
    bundles: list[AnthropicSkillBundle] = []

    if not directory.is_dir():
        return bundles

    for child in sorted(directory.iterdir()):
        if child.is_dir() and (child / "SKILL.md").exists():
            try:
                bundles.append(parse_skill_md(child))
            except (ValueError, FileNotFoundError, yaml.YAMLError):
                pass  # Skip unparseable skills

    return bundles
